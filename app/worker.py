import mimetypes
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


@dataclass
class FeedItem:
    guid: str
    pub_date: datetime
    xml: str
    description_len: int


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


def _find_child_text(item: ET.Element, local_name: str) -> str:
    for child in item:
        if _local_name(child.tag) == local_name:
            return child.text or ""
    return ""


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

            url = str(feed_cfg.get("url") or "").strip()
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
    now = datetime.utcnow()
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

    rows.sort(key=lambda row: row[1].published_at or row[0].updated_at or now, reverse=True)
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
        items.append(FeedItem(guid=video.video_id, pub_date=pub_dt, xml=xml, description_len=len(description_raw)))

    return items


def _podsync_feed_items() -> list[FeedItem]:
    data_root = Path(settings.podsync_data_dir)
    if not data_root.exists():
        return []

    items: list[FeedItem] = []
    for xml_file in sorted(data_root.rglob("*.xml")):
        try:
            root = ET.parse(xml_file).getroot()
        except Exception:
            continue

        for item in root.iter():
            if _local_name(item.tag) != "item":
                continue

            guid = _find_child_text(item, "guid").strip() or _find_child_text(item, "link").strip()
            if not guid:
                continue

            pub_text = _find_child_text(item, "pubDate")
            pub_dt = _parse_pub_date(pub_text) or datetime.utcnow()
            desc_text = _find_child_text(item, "description")

            xml = ET.tostring(item, encoding="unicode")
            # Normalize indentation a bit for readability
            xml = "    " + xml.replace("\n", "\n    ")
            items.append(FeedItem(guid=guid, pub_date=pub_dt, xml=xml, description_len=len(desc_text)))

    return items


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


def regenerate_merged_feed() -> None:
    manual_items = _manual_feed_items(_load_manual_rows())
    podsync_items = _podsync_feed_items()

    by_guid: dict[str, FeedItem] = {}
    for item in podsync_items + manual_items:
        existing = by_guid.get(item.guid)
        if existing is None:
            by_guid[item.guid] = item
            continue
        # Prefer richer metadata, then newer publish date.
        if item.description_len > existing.description_len:
            by_guid[item.guid] = item
        elif item.description_len == existing.description_len and item.pub_date > existing.pub_date:
            by_guid[item.guid] = item

    merged = sorted(by_guid.values(), key=lambda it: it.pub_date, reverse=True)
    _write_feed_file(
        settings.merged_feed_file,
        settings.merged_feed_title,
        settings.merged_feed_description,
        settings.public_base_url.rstrip("/") + settings.merged_feed_path,
        merged,
    )


def regenerate_all_feeds() -> None:
    regenerate_manual_feed()
    regenerate_merged_feed()


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
        process_next_job()

        if settings.podsync_feed_sync_interval_seconds > 0 and datetime.utcnow() >= next_feed_sync_at:
            try:
                sync_channels_from_podsync_config()
                regenerate_merged_feed()
            except Exception:
                pass
            next_feed_sync_at = datetime.utcnow() + timedelta(seconds=settings.podsync_feed_sync_interval_seconds)

        stop_event.wait(settings.poll_interval_seconds)
