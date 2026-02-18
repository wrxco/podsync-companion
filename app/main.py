import threading
from base64 import b64decode
from collections import defaultdict, deque
from hmac import compare_digest
from datetime import datetime
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, SessionLocal, engine
from .models import Channel, Download, Job, Video
from .schemas import ChannelCreate, ChannelOut, DownloadOut, EnqueueDownloadIn, VideoOut
from .worker import regenerate_all_feeds, sync_channels_from_podsync_config, worker_loop
from .video_id import extract_video_id
from .ytdlp import get_video_metadata

app = FastAPI(title="podsync-companion")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount(settings.media_url_path, StaticFiles(directory=settings.media_dir, check_dir=False), name="media")

stop_event = threading.Event()
worker_thread: threading.Thread | None = None
rate_limit_lock = threading.Lock()
mutation_request_times: dict[str, deque[float]] = defaultdict(deque)
podsync_download_cache_lock = threading.Lock()
podsync_download_cache: dict[str, dict] = {}
podsync_download_cache_expires_at: float = 0.0
podsync_filename_video_id_hints: dict[str, str] = {}


ALLOWED_CHANNEL_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _is_allowed_channel_url(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.netloc or "").split("@")[-1].split(":")[0].strip().lower()
    return host in ALLOWED_CHANNEL_HOSTS


def _unauthorized():
    return JSONResponse(
        status_code=401,
        content={"detail": "authentication required"},
        headers={"WWW-Authenticate": "Basic"},
    )


def _misconfigured_auth():
    return JSONResponse(
        status_code=503,
        content={"detail": "service auth is required but credentials are not configured"},
    )


def _is_feed_request(path: str) -> bool:
    if path == settings.manual_feed_path:
        return True
    merged_prefix = settings.merged_feed_path_prefix.rstrip("/")
    return path.startswith(f"{merged_prefix}/")


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    if settings.auth_bypass_feeds and _is_feed_request(request.url.path):
        return await call_next(request)

    username = settings.basic_auth_username.strip()
    password = settings.basic_auth_password.strip()
    if settings.auth_required:
        if not username or not password:
            return _misconfigured_auth()
    elif not username and not password:
        return await call_next(request)
    elif not username or not password:
        return _unauthorized()

    auth = request.headers.get("authorization", "")
    if not auth.startswith("Basic "):
        return _unauthorized()
    encoded = auth[6:].strip()
    try:
        decoded = b64decode(encoded).decode("utf-8")
    except Exception:
        return _unauthorized()
    if ":" not in decoded:
        return _unauthorized()
    user, pwd = decoded.split(":", 1)
    if not (compare_digest(user, username) and compare_digest(pwd, password)):
        return _unauthorized()

    return await call_next(request)


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    if not settings.csrf_protection_enabled:
        return await call_next(request)

    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
        header_name = settings.csrf_header_name.strip().lower()
        expected = settings.csrf_header_value
        if not header_name:
            return JSONResponse(status_code=500, content={"detail": "invalid csrf header configuration"})

        actual = request.headers.get(header_name)
        if not actual or not compare_digest(actual, expected):
            return JSONResponse(status_code=403, content={"detail": "missing or invalid csrf header"})

    return await call_next(request)


@app.middleware("http")
async def mutation_rate_limit_middleware(request: Request, call_next):
    limit = settings.api_mutation_rate_limit_per_minute
    if limit <= 0:
        return await call_next(request)

    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
        client_host = (request.client.host if request.client else "") or "unknown"
        key = f"{client_host}:{request.url.path}"
        now = monotonic()
        window_start = now - 60.0

        with rate_limit_lock:
            bucket = mutation_request_times[key]
            while bucket and bucket[0] < window_start:
                bucket.popleft()
            if len(bucket) >= limit:
                return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
            bucket.append(now)

    return await call_next(request)


def _redact_error_text(error: str | None) -> str | None:
    if not error:
        return None
    return "Operation failed. Check server logs for details."


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_child_text(item: ET.Element, local_name: str) -> str:
    for child in item:
        if _local_name(child.tag) == local_name:
            return child.text or ""
    return ""


def _find_enclosure_url(item: ET.Element) -> str:
    for child in item:
        if _local_name(child.tag) == "enclosure":
            return (child.attrib.get("url") or "").strip()
    return ""


def _extract_video_id(value: str) -> str:
    return extract_video_id(value)


def _looks_like_video_ref(value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    return "youtu" in text or "youtube.com" in text or "v=" in text or "://" in text


def _iter_item_id_candidates(item: ET.Element) -> list[str]:
    candidates: list[str] = []
    for child in item:
        child_text = (child.text or "").strip()
        if child_text and _looks_like_video_ref(child_text):
            candidates.append(child_text)
        for attr_name in ("url", "href", "src"):
            attr_value = (child.attrib.get(attr_name) or "").strip()
            if attr_value:
                candidates.append(attr_value)
    return candidates


def _collect_podsync_downloads() -> dict[str, dict]:
    global podsync_filename_video_id_hints
    data_root = Path(settings.podsync_data_dir)
    if not data_root.exists():
        return {}

    found: dict[str, dict] = {}
    filename_hits: dict[str, set[str]] = defaultdict(set)
    for xml_file in data_root.rglob("*.xml"):
        try:
            root = ET.parse(xml_file).getroot()
        except Exception:
            continue

        for item in root.iter():
            if _local_name(item.tag) != "item":
                continue

            enclosure_url = _find_enclosure_url(item)
            filename = Path(enclosure_url.split("?", 1)[0]).name if enclosure_url else ""
            video_id = ""

            for candidate in (
                _find_child_text(item, "guid"),
                _find_child_text(item, "link"),
                enclosure_url,
            ):
                video_id = _extract_video_id(candidate)
                if video_id:
                    break

            if not video_id:
                for candidate in _iter_item_id_candidates(item):
                    video_id = _extract_video_id(candidate)
                    if video_id:
                        break

            if not video_id:
                video_id = _extract_video_id(ET.tostring(item, encoding="unicode"))

            if filename and video_id:
                filename_hits[filename].add(video_id)
            if not video_id or video_id in found:
                continue

            found[video_id] = {
                "video_id": video_id,
                "status": "podsync",
                "filename": filename or None,
                "error": None,
            }

    for filename, ids in filename_hits.items():
        if len(ids) == 1:
            podsync_filename_video_id_hints[filename] = next(iter(ids))
        else:
            podsync_filename_video_id_hints.pop(filename, None)

    # Also detect already-downloaded Podsync media files even if they are no longer in feed XML.
    media_exts = {
        ".mp3",
        ".m4a",
        ".mp4",
        ".mkv",
        ".webm",
        ".opus",
        ".aac",
        ".flac",
        ".ogg",
        ".wav",
    }
    for media_file in data_root.rglob("*"):
        if not media_file.is_file():
            continue
        if media_file.suffix.lower() not in media_exts:
            continue
        video_id = _extract_video_id(media_file.name)
        if not video_id:
            video_id = podsync_filename_video_id_hints.get(media_file.name, "")
        if not video_id or video_id in found:
            continue
        found[video_id] = {
            "video_id": video_id,
            "status": "podsync",
            "filename": media_file.name,
            "error": None,
        }

    return found


def _get_podsync_downloads_cached() -> dict[str, dict]:
    global podsync_download_cache, podsync_download_cache_expires_at
    now = monotonic()
    with podsync_download_cache_lock:
        if now < podsync_download_cache_expires_at:
            return dict(podsync_download_cache)
        podsync_download_cache = _collect_podsync_downloads()
        podsync_download_cache_expires_at = now + 30.0
        return dict(podsync_download_cache)


@app.on_event("startup")
def on_startup() -> None:
    global worker_thread
    Base.metadata.create_all(bind=engine)
    Path(settings.source_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.media_dir).mkdir(parents=True, exist_ok=True)

    # Import channels declared in Podsync config before serving requests.
    sync_channels_from_podsync_config()
    regenerate_all_feeds()

    stop_event.clear()
    worker_thread = threading.Thread(target=worker_loop, args=(stop_event,), daemon=True)
    worker_thread.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    stop_event.set()


@app.get("/")
def index():
    return FileResponse("app/static/index.html")


@app.get(settings.manual_feed_path)
def manual_feed():
    feed_file = Path(settings.manual_feed_file)
    if not feed_file.exists():
        raise HTTPException(status_code=404, detail="manual feed has not been generated yet")
    return FileResponse(feed_file, media_type="application/rss+xml")


@app.get(f"{settings.merged_feed_path_prefix}/{{channel_id}}.xml")
def merged_feed_for_channel(channel_id: int):
    feed_file = Path(settings.merged_feed_dir) / f"{channel_id}.xml"
    if not feed_file.exists():
        raise HTTPException(status_code=404, detail="merged feed for this channel has not been generated yet")
    return FileResponse(feed_file, media_type="application/rss+xml")


@app.get("/api/channels", response_model=list[ChannelOut])
def list_channels(db: Session = Depends(get_db)):
    return db.execute(select(Channel).order_by(Channel.id.desc())).scalars().all()


@app.post("/api/channels", response_model=ChannelOut)
def create_channel(payload: ChannelCreate, db: Session = Depends(get_db)):
    if not _is_allowed_channel_url(payload.url):
        raise HTTPException(status_code=400, detail="Only YouTube channel/playlist URLs are allowed")

    existing = db.execute(select(Channel).where(Channel.url == payload.url)).scalar_one_or_none()
    if existing:
        return existing

    channel = Channel(url=payload.url, name=payload.name or "")
    db.add(channel)
    db.commit()
    db.refresh(channel)
    regenerate_all_feeds()
    return channel


@app.post("/api/channels/sync_from_podsync")
def sync_channels_from_podsync():
    added = sync_channels_from_podsync_config()
    regenerate_all_feeds()
    return {"ok": True, "added": added}


@app.post("/api/channels/{channel_id}/index")
def enqueue_index(channel_id: int, db: Session = Depends(get_db)):
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    db.add(Job(job_type="index_channel", status="pending", payload={"channel_id": channel_id}))
    db.commit()
    return {"ok": True}


@app.get("/api/videos", response_model=list[VideoOut])
def list_videos(
    channel_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    sort: str = "desc",
    q: str | None = None,
    include_unavailable: bool = False,
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    query = select(Video)
    if channel_id is not None:
        query = query.where(Video.channel_id == channel_id)

    if q is not None and q.strip():
        needle = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(Video.title).like(needle),
                func.lower(Video.video_id).like(needle),
                func.lower(Video.description).like(needle),
                func.lower(Video.uploader).like(needle),
            )
        )

    if not include_unavailable:
        lowered = func.lower(Video.title)
        query = query.where(
            lowered != "[private video]",
            lowered != "[deleted video]",
        )

    null_rank = (Video.published_at.is_(None)).asc()
    if sort.lower() == "desc":
        query = query.order_by(null_rank, desc(Video.published_at), desc(Video.id))
    else:
        query = query.order_by(null_rank, asc(Video.published_at), asc(Video.id))

    query = query.limit(limit).offset(offset)
    videos = db.execute(query).scalars().all()
    updated = False
    hydrate_budget = 20
    for video in videos:
        if hydrate_budget <= 0:
            break
        if video.published_at is not None:
            continue
        try:
            metadata = get_video_metadata(video.webpage_url)
        except Exception:
            continue

        if metadata.get("published_at") is not None:
            video.published_at = metadata["published_at"]
            updated = True
        if metadata.get("duration_seconds") is not None and video.duration_seconds is None:
            video.duration_seconds = metadata["duration_seconds"]
            updated = True
        if metadata.get("description") and not video.description:
            video.description = metadata["description"]
            updated = True
        if metadata.get("thumbnail_url") and not video.thumbnail_url:
            video.thumbnail_url = metadata["thumbnail_url"]
            updated = True
        if metadata.get("uploader") and not video.uploader:
            video.uploader = metadata["uploader"]
            updated = True
        hydrate_budget -= 1

    if updated:
        db.commit()

    return videos


@app.post("/api/downloads/enqueue")
def enqueue_downloads(payload: EnqueueDownloadIn, db: Session = Depends(get_db)):
    podsync_downloads = _get_podsync_downloads_cached()
    count = 0
    skipped_existing = 0
    for video_id in payload.video_ids:
        if video_id in podsync_downloads:
            skipped_existing += 1
            continue

        existing = db.execute(select(Download).where(Download.video_id == video_id)).scalar_one_or_none()
        if existing and existing.status in {"queued", "running", "done"}:
            skipped_existing += 1
            continue

        if not existing:
            db.add(Download(video_id=video_id, status="queued", created_at=datetime.utcnow(), updated_at=datetime.utcnow()))
        else:
            existing.status = "queued"
            existing.error = None
            existing.updated_at = datetime.utcnow()

        db.add(Job(job_type="download_video", status="pending", payload={"video_id": video_id}))
        count += 1

    db.commit()
    return {"ok": True, "queued": count, "skipped_existing": skipped_existing}


@app.get("/api/downloads", response_model=list[DownloadOut])
def list_downloads(db: Session = Depends(get_db)):
    rows = db.execute(select(Download).order_by(Download.id.desc()).limit(200)).scalars().all()
    out = [
        {
            "video_id": d.video_id,
            "status": d.status,
            "filename": d.filename,
            "error": _redact_error_text(d.error),
        }
        for d in rows
    ]
    by_video_id = {str(item["video_id"]): item for item in out}
    for pod_item in _get_podsync_downloads_cached().values():
        video_id = str(pod_item["video_id"])
        existing = by_video_id.get(video_id)
        if existing is None:
            out.append(pod_item)
            by_video_id[video_id] = pod_item
            continue

        # Preserve explicit companion states, but backfill blank/unknown status from Podsync presence.
        existing_status = str(existing.get("status") or "").strip().lower()
        if not existing_status:
            existing["status"] = "podsync"
        if not existing.get("filename"):
            existing["filename"] = pod_item.get("filename")
    return out


@app.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.execute(select(Job).order_by(Job.id.desc()).limit(200)).scalars().all()
    return [
        {
            "id": j.id,
            "job_type": j.job_type,
            "status": j.status,
            "payload": j.payload,
            "error": _redact_error_text(j.error),
            "created_at": j.created_at,
            "updated_at": j.updated_at,
        }
        for j in jobs
    ]


@app.post("/api/feed/regenerate")
def regenerate_feed(db: Session = Depends(get_db)):
    db.add(Job(job_type="regenerate_manual_feed", status="pending", payload={}))
    db.commit()
    return {"ok": True}


@app.get("/api/feed")
def feed_info():
    base = settings.public_base_url.rstrip("/")
    return {
        "manual_feed_url": base + settings.manual_feed_path,
        "manual_feed_path": settings.manual_feed_path,
        "merged_feed_path_prefix": settings.merged_feed_path_prefix,
        "merged_feed_url_template": base + f"{settings.merged_feed_path_prefix}/{{channel_id}}.xml",
        "media_url_path": settings.media_url_path,
    }


@app.get("/api/feed/merged")
def merged_feeds(db: Session = Depends(get_db)):
    base = settings.public_base_url.rstrip("/")
    merged_dir = Path(settings.merged_feed_dir)
    if not merged_dir.exists():
        return []

    available_ids: set[int] = set()
    for xml_file in merged_dir.glob("*.xml"):
        try:
            available_ids.add(int(xml_file.stem))
        except ValueError:
            continue

    if not available_ids:
        return []

    channels = db.execute(select(Channel).where(Channel.id.in_(available_ids)).order_by(Channel.id.asc())).scalars().all()
    return [
        {
            "channel_id": ch.id,
            "channel_name": ch.name or ch.url,
            "url": f"{base}{settings.merged_feed_path_prefix}/{ch.id}.xml",
        }
        for ch in channels
    ]
