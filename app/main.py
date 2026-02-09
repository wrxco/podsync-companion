import threading
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, SessionLocal, engine
from .models import Channel, Download, Job, Video
from .schemas import ChannelCreate, ChannelOut, DownloadOut, EnqueueDownloadIn, VideoOut
from .worker import regenerate_all_feeds, sync_channels_from_podsync_config, worker_loop
from .ytdlp import get_video_metadata

app = FastAPI(title="podsync-companion")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount(settings.media_url_path, StaticFiles(directory=settings.media_dir, check_dir=False), name="media")

stop_event = threading.Event()
worker_thread: threading.Thread | None = None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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


@app.get(settings.merged_feed_path)
def merged_feed():
    feed_file = Path(settings.merged_feed_file)
    if not feed_file.exists():
        raise HTTPException(status_code=404, detail="merged feed has not been generated yet")
    return FileResponse(feed_file, media_type="application/rss+xml")


@app.get("/api/channels", response_model=list[ChannelOut])
def list_channels(db: Session = Depends(get_db)):
    return db.execute(select(Channel).order_by(Channel.id.desc())).scalars().all()


@app.post("/api/channels", response_model=ChannelOut)
def create_channel(payload: ChannelCreate, db: Session = Depends(get_db)):
    existing = db.execute(select(Channel).where(Channel.url == payload.url)).scalar_one_or_none()
    if existing:
        return existing

    channel = Channel(url=payload.url, name=payload.name or "")
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


@app.post("/api/channels/sync_from_podsync")
def sync_channels_from_podsync():
    added = sync_channels_from_podsync_config()
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
    sort: str = "asc",
    include_unavailable: bool = False,
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    query = select(Video)
    if channel_id is not None:
        query = query.where(Video.channel_id == channel_id)

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
    count = 0
    for video_id in payload.video_ids:
        existing = db.execute(select(Download).where(Download.video_id == video_id)).scalar_one_or_none()
        if existing and existing.status in {"queued", "running", "done"}:
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
    return {"ok": True, "queued": count}


@app.get("/api/downloads", response_model=list[DownloadOut])
def list_downloads(db: Session = Depends(get_db)):
    return db.execute(select(Download).order_by(Download.id.desc()).limit(200)).scalars().all()


@app.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.execute(select(Job).order_by(Job.id.desc()).limit(200)).scalars().all()
    return [
        {
            "id": j.id,
            "job_type": j.job_type,
            "status": j.status,
            "payload": j.payload,
            "error": j.error,
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
    return {
        "manual_feed_url": settings.public_base_url.rstrip("/") + settings.manual_feed_path,
        "merged_feed_url": settings.public_base_url.rstrip("/") + settings.merged_feed_path,
        "manual_feed_path": settings.manual_feed_path,
        "merged_feed_path": settings.merged_feed_path,
        "media_url_path": settings.media_url_path,
    }
