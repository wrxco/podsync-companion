import mimetypes
import logging
import os
import re
import threading
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .models import Channel, Download, Job, Video
from .ytdlp import download_video, get_video_metadata, index_channel

logger = logging.getLogger(__name__)


@dataclass
class FeedItem:
    guid: str
    dedupe_key: str
    pub_date: datetime
    episode_number: int | None
    xml: str
    description_len: int


@dataclass
class PodsyncFeed:
    feed_id: str
    source_url: str
    title: str
    description: str
    items: list[FeedItem]


def slugify_title(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "", value).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:150] or "untitled"


def build_filename(published_at: datetime | None, title: str, ext: str, video_id: str) -> str:
    date_part = (published_at or datetime.utcnow()).strftime("%Y-%m-%d")
    return f"{date_part}_{slugify_title(title)}_{video_id}.{ext}"


def resolve_dest_path(base_dest_path: Path) -> Path:
    if not base_dest_path.exists() and not os.path.lexists(base_dest_path):
        return base_dest_path

    stem = base_dest_path.stem
    suffix = base_dest_path.suffix
    parent = base_dest_path.parent
    for i in range(2, 1000):
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists() and not os.path.lexists(candidate):
            return candidate
    return parent / f"{stem}_{int(datetime.utcnow().timestamp())}{suffix}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _find_child_text(item: ET.Element, local_name: str) -> str:
    for child in item:
        if _local_name(child.tag) == local_name:
            return child.text or ""
    return ""


def _extract_video_id(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""

    match = re.search(r"[?&]v=([A-Za-z0-9_-]{6,20})", text)
    if match:
        return match.group(1)

    match = re.search(r"youtu\.be/([A-Za-z0-9_-]{6,20})", text)
    if match:
        return match.group(1)

    match = re.fullmatch(r"[A-Za-z0-9_-]{6,20}", text)
    if match:
        return text

    return ""


def _extract_episode_number(value: str) -> int | None:
    text = (value or "").strip()
    if not text:
        return None

    match = re.search(r"(?:episode|edition)\s*#?\s*(\d{1,5})\b", text, flags=re.IGNORECASE)
    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


def _parse_pub_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _format_pub_date(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return format_datetime(dt)


def _write_feed_file(path: str, title: str, description: str, link: str, items: list[FeedItem]) -> None:
    now = datetime.utcnow()
    xml = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0">',
            "  <channel>",
            f"    <title>{escape(title)}</title>",
            f"    <description>{escape(description)}</description>",
            f"    <link>{escape(link)}</link>",
            f"    <lastBuildDate>{_format_pub_date(now)}</lastBuildDate>",
            *[item.xml for item in items],
            "  </channel>",
            "</rss>",
        ]
    )

    feed_file = Path(path)
    feed_file.parent.mkdir(parents=True, exist_ok=True)
    feed_file.write_text(xml + "\n", encoding="utf-8")


def _load_podsync_feeds() -> list[PodsyncFeed]:
    data_root = Path(settings.podsync_data_dir)
    if not data_root.exists():
        return []

    feeds: list[PodsyncFeed] = []
    for xml_file in sorted(data_root.rglob("*.xml")):
        try:
            root = ET.parse(xml_file).getroot()
        except Exception:
            continue

        channel = None
        for node in root.iter():
            if _local_name(node.tag) == "channel":
                channel = node
                break
        if channel is None:
            continue

        feed_id = xml_file.stem
        source_url = _normalize_url(_find_child_text(channel, "link"))
        title = _find_child_text(channel, "title") or feed_id
        description = _find_child_text(channel, "description")

        items: list[FeedItem] = []
        for item in channel:
            if _local_name(item.tag) != "item":
                continue

            guid = _find_child_text(item, "guid").strip() or _find_child_text(item, "link").strip()
            if not guid:
                continue

            pub_dt = _parse_pub_date(_find_child_text(item, "pubDate")) or datetime.utcnow()
            desc_text = _find_child_text(item, "description")
            xml = "    " + ET.tostring(item, encoding="unicode").replace("\n", "\n    ")
            dedupe_key = _extract_video_id(guid) or _extract_video_id(_find_child_text(item, "link")) or guid
            items.append(
                FeedItem(
                    guid=guid,
                    dedupe_key=dedupe_key,
                    pub_date=pub_dt,
                    episode_number=_extract_episode_number(_find_child_text(item, "title")),
                    xml=xml,
                    description_len=len(desc_text),
                )
            )

        feeds.append(PodsyncFeed(feed_id=feed_id, source_url=source_url, title=title, description=description, items=items))

    return feeds


def sync_channels_from_podsync_config() -> int:
    config_path = Path(settings.podsync_config_path)
    if not config_path.exists():
        return 0

    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return 0

    feeds = raw.get("feeds")
    if not isinstance(feeds, dict):
        return 0

    added = 0
    with SessionLocal() as session:
        for feed_id, feed_cfg in feeds.items():
            if not isinstance(feed_cfg, dict):
                continue

            url = _normalize_url(str(feed_cfg.get("url") or ""))
            if not url:
                continue

            existing = session.execute(select(Channel).where(Channel.url == url)).scalar_one_or_none()
            if existing is None:
                session.add(Channel(url=url, name=str(feed_id)))
                added += 1
            elif not existing.name:
                existing.name = str(feed_id)

        session.commit()

    return added


def ensure_video_metadata(video: Video) -> None:
    metadata = get_video_metadata(video.webpage_url)
    if metadata["title"]:
        video.title = metadata["title"]
    if metadata["description"]:
        video.description = metadata["description"]
    if metadata["published_at"] is not None:
        video.published_at = metadata["published_at"]
    if metadata["duration_seconds"] is not None:
        video.duration_seconds = metadata["duration_seconds"]
    if metadata["thumbnail_url"]:
        video.thumbnail_url = metadata["thumbnail_url"]
    if metadata["uploader"]:
        video.uploader = metadata["uploader"]


def _load_manual_rows() -> list[tuple[Download, Video]]:
    with SessionLocal() as session:
        rows = session.execute(
            select(Download, Video)
            .join(Video, Video.video_id == Download.video_id)
            .where(Download.status == "done", Download.filename.is_not(None))
        ).all()

        changed = False
        for _download, video in rows:
            if video.published_at is not None:
                continue
            try:
                metadata = get_video_metadata(video.webpage_url)
            except Exception:
                continue
            if metadata.get("published_at") is not None:
                video.published_at = metadata["published_at"]
                changed = True
            if metadata.get("duration_seconds") is not None and video.duration_seconds is None:
                video.duration_seconds = metadata["duration_seconds"]
                changed = True
            if metadata.get("description") and not video.description:
                video.description = metadata["description"]
                changed = True
            if metadata.get("thumbnail_url") and not video.thumbnail_url:
                video.thumbnail_url = metadata["thumbnail_url"]
                changed = True
            if metadata.get("uploader") and not video.uploader:
                video.uploader = metadata["uploader"]
                changed = True
        if changed:
            session.commit()

    rows.sort(key=lambda row: row[1].published_at or row[0].updated_at or datetime.utcnow(), reverse=True)
    return rows


def _manual_feed_items(rows: list[tuple[Download, Video]]) -> list[FeedItem]:
    base_url = settings.public_base_url.rstrip("/")
    media_path_prefix = settings.media_url_path.strip("/")
    now = datetime.utcnow()
    items: list[FeedItem] = []

    for download, video in rows:
        pub_dt = video.published_at or download.updated_at or now
        pub = _format_pub_date(pub_dt)
        title = escape(video.title or download.filename or video.video_id)
        description_raw = video.description or video.webpage_url or ""
        description = escape(description_raw)
        guid = escape(video.video_id)
        enclosure_url = f"{base_url}/{media_path_prefix}/{quote(download.filename)}"
        mime_type = mimetypes.guess_type(download.filename or "")[0] or "application/octet-stream"
        size = 0
        if download.media_path:
            try:
                size = Path(download.media_path).stat().st_size
            except OSError:
                size = 0

        xml = "\n".join(
            [
                "    <item>",
                f"      <title>{title}</title>",
                f"      <description>{description}</description>",
                f"      <guid isPermaLink=\"false\">{guid}</guid>",
                f"      <pubDate>{pub}</pubDate>",
                f"      <link>{escape(video.webpage_url or '')}</link>",
                f"      <enclosure url=\"{escape(enclosure_url)}\" length=\"{size}\" type=\"{escape(mime_type)}\" />",
                "    </item>",
            ]
        )
        items.append(
            FeedItem(
                guid=video.video_id,
                dedupe_key=video.video_id,
                pub_date=pub_dt,
                episode_number=_extract_episode_number(video.title),
                xml=xml,
                description_len=len(description_raw),
            )
        )

    return items


def regenerate_manual_feed() -> None:
    rows = _load_manual_rows()
    items = _manual_feed_items(rows)
    _write_feed_file(
        settings.manual_feed_file,
        settings.manual_feed_title,
        settings.manual_feed_description,
        settings.public_base_url.rstrip("/") + settings.manual_feed_path,
        items,
    )


def regenerate_merged_feeds() -> None:
    podsync_feeds = _load_podsync_feeds()
    by_source_url = {_normalize_url(feed.source_url): feed for feed in podsync_feeds if feed.source_url}
    by_feed_id = {feed.feed_id: feed for feed in podsync_feeds}

    manual_rows = _load_manual_rows()
    manual_by_channel: dict[int, list[tuple[Download, Video]]] = {}
    for row in manual_rows:
        manual_by_channel.setdefault(row[1].channel_id, []).append(row)

    with SessionLocal() as session:
        channels = session.execute(select(Channel).order_by(Channel.id.asc())).scalars().all()

    merged_dir = Path(settings.merged_feed_dir)
    merged_dir.mkdir(parents=True, exist_ok=True)
    generated_channel_ids: set[int] = set()

    for channel in channels:
        podsync_feed = by_source_url.get(_normalize_url(channel.url))
        if podsync_feed is None and channel.name:
            podsync_feed = by_feed_id.get(channel.name)

        podsync_items = podsync_feed.items if podsync_feed else []
        manual_items = _manual_feed_items(manual_by_channel.get(channel.id, []))
        if not podsync_items and not manual_items:
            continue

        by_guid: dict[str, FeedItem] = {}
        for item in podsync_items + manual_items:
            existing = by_guid.get(item.dedupe_key)
            if existing is None:
                by_guid[item.dedupe_key] = item
                continue
            if item.description_len > existing.description_len:
                by_guid[item.dedupe_key] = item
            elif item.description_len == existing.description_len and item.pub_date > existing.pub_date:
                by_guid[item.dedupe_key] = item

        def _sort_key(item: FeedItem) -> tuple[datetime, int, int, str]:
            # Keep chronological correctness for podcast clients (pubDate is primary),
            # then use episode number as a stable tie-breaker when dates match.
            has_episode = 1 if item.episode_number is not None else 0
            episode_num = item.episode_number or -1
            return (item.pub_date, has_episode, episode_num, item.guid)

        merged_items = sorted(by_guid.values(), key=_sort_key, reverse=True)
        link = settings.public_base_url.rstrip("/") + f"{settings.merged_feed_path_prefix}/{channel.id}.xml"
        if podsync_feed:
            title = podsync_feed.title
            description = podsync_feed.description or settings.merged_feed_description
        else:
            channel_name = channel.name or channel.url
            title = f"{channel_name} {settings.merged_feed_title_suffix}"
            description = settings.merged_feed_description

        _write_feed_file(
            str(merged_dir / f"{channel.id}.xml"),
            title,
            description,
            link,
            merged_items,
        )
        generated_channel_ids.add(channel.id)

    for old_file in merged_dir.glob("*.xml"):
        try:
            channel_id = int(old_file.stem)
        except ValueError:
            continue
        if channel_id not in generated_channel_ids:
            old_file.unlink(missing_ok=True)


def regenerate_all_feeds() -> None:
    regenerate_manual_feed()
    regenerate_merged_feeds()


def _handle_index(job: Job) -> None:
    channel_id = int(job.payload["channel_id"])
    with SessionLocal() as session:
        channel = session.get(Channel, channel_id)
        if channel is None:
            raise RuntimeError(f"channel {channel_id} not found")
        entries = index_channel(channel.url, settings.channel_scan_limit)

        for entry in entries:
            existing = session.execute(
                select(Video).where(Video.channel_id == channel.id, Video.video_id == entry["video_id"])
            ).scalar_one_or_none()

            if existing:
                existing.title = entry["title"]
                existing.description = entry["description"]
                existing.webpage_url = entry["webpage_url"]
                existing.published_at = entry["published_at"]
                existing.duration_seconds = entry["duration_seconds"]
                existing.thumbnail_url = entry["thumbnail_url"]
                existing.uploader = entry["uploader"]
                existing.indexed_at = datetime.utcnow()
            else:
                session.add(
                    Video(
                        channel_id=channel.id,
                        video_id=entry["video_id"],
                        title=entry["title"],
                        description=entry["description"],
                        webpage_url=entry["webpage_url"],
                        published_at=entry["published_at"],
                        duration_seconds=entry["duration_seconds"],
                        thumbnail_url=entry["thumbnail_url"],
                        uploader=entry["uploader"],
                    )
                )

        channel.last_indexed_at = datetime.utcnow()
        session.commit()


def _handle_download(job: Job) -> None:
    video_id = job.payload["video_id"]

    with SessionLocal() as session:
        video = session.execute(select(Video).where(Video.video_id == video_id)).scalar_one_or_none()
        if video is None:
            raise RuntimeError(f"video {video_id} not found in index")

        ensure_video_metadata(video)

        record = session.execute(select(Download).where(Download.video_id == video_id)).scalar_one_or_none()
        if record is None:
            record = Download(video_id=video_id, status="running")
            session.add(record)
        else:
            record.status = "running"
            record.error = None
        record.updated_at = datetime.utcnow()
        session.commit()

    source_path = download_video(video.webpage_url, settings.source_dir, video_id, audio_only=settings.download_audio)
    ext = Path(source_path).suffix.lstrip(".") or "mp4"
    filename = build_filename(video.published_at, video.title, ext, video_id)

    Path(settings.media_dir).mkdir(parents=True, exist_ok=True)
    media_path = str(resolve_dest_path(Path(settings.media_dir) / filename))
    os.replace(source_path, media_path)

    with SessionLocal() as session:
        record = session.execute(select(Download).where(Download.video_id == video_id)).scalar_one()
        record.status = "done"
        record.source_path = None
        record.media_path = media_path
        record.filename = Path(media_path).name
        record.updated_at = datetime.utcnow()
        session.commit()

    regenerate_all_feeds()


def process_next_job() -> None:
    with SessionLocal() as session:
        job = session.execute(
            select(Job).where(Job.status == "pending").order_by(Job.id.asc()).limit(1)
        ).scalar_one_or_none()
        if job is None:
            return
        job.status = "running"
        job.updated_at = datetime.utcnow()
        session.commit()
        job_id = job.id

    try:
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            if job.job_type == "index_channel":
                _handle_index(job)
            elif job.job_type == "download_video":
                _handle_download(job)
            elif job.job_type == "regenerate_manual_feed":
                regenerate_all_feeds()
            elif job.job_type == "sync_podsync_feeds":
                sync_channels_from_podsync_config()
                regenerate_merged_feeds()
            else:
                raise RuntimeError(f"unknown job type {job.job_type}")

        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job:
                job.status = "done"
                job.error = None
                job.updated_at = datetime.utcnow()
                session.commit()
    except Exception as exc:
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error = str(exc)
                job.updated_at = datetime.utcnow()
                session.commit()

        if job and job.job_type == "download_video":
            with SessionLocal() as session:
                record = session.execute(
                    select(Download).where(Download.video_id == job.payload.get("video_id"))
                ).scalar_one_or_none()
                if record:
                    record.status = "failed"
                    record.error = str(exc)
                    record.updated_at = datetime.utcnow()
                    session.commit()


def worker_loop(stop_event: threading.Event) -> None:
    next_feed_sync_at = datetime.utcnow()

    while not stop_event.is_set():
        try:
            process_next_job()
        except Exception:
            # Keep worker alive on unexpected polling/DB exceptions.
            logger.exception("worker loop failed while processing next job")

        if settings.podsync_feed_sync_interval_seconds > 0 and datetime.utcnow() >= next_feed_sync_at:
            try:
                sync_channels_from_podsync_config()
                regenerate_merged_feeds()
            except Exception:
                logger.exception("worker loop failed during periodic podsync feed sync")
            next_feed_sync_at = datetime.utcnow() + timedelta(seconds=settings.podsync_feed_sync_interval_seconds)

        stop_event.wait(settings.poll_interval_seconds)
