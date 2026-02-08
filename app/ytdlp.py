import json
import subprocess
from datetime import datetime
from pathlib import Path

from dateutil.parser import isoparse


def run_command(cmd: list[str]) -> str:
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{completed.stderr.strip()}")
    return completed.stdout


def is_unavailable_item(item: dict) -> bool:
    title = str(item.get("title") or "").strip().lower()
    availability = str(item.get("availability") or "").strip().lower()
    if title in {"[private video]", "[deleted video]"}:
        return True
    if availability in {"private", "subscriber_only", "premium_only", "needs_auth"}:
        return True
    return False


def index_channel(url: str, limit: int = 0) -> list[dict]:
    cmd = ["yt-dlp", "--flat-playlist", "--dump-json", "--ignore-errors", url]
    output = run_command(cmd)
    videos: list[dict] = []

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        if is_unavailable_item(item):
            continue

        raw_date = item.get("upload_date")
        published_at = None
        if isinstance(raw_date, str) and len(raw_date) == 8 and raw_date.isdigit():
            published_at = datetime.strptime(raw_date, "%Y%m%d")
        elif item.get("release_timestamp"):
            published_at = datetime.utcfromtimestamp(item["release_timestamp"])
        elif item.get("timestamp"):
            published_at = datetime.utcfromtimestamp(item["timestamp"])
        elif item.get("upload_date"):
            try:
                published_at = isoparse(item["upload_date"])
            except Exception:
                published_at = None

        video_id = item.get("id")
        if not video_id:
            continue

        raw_url = item.get("webpage_url") or item.get("url") or ""
        if isinstance(raw_url, str) and raw_url.startswith("http"):
            webpage_url = raw_url
        else:
            webpage_url = f"https://www.youtube.com/watch?v={video_id}"

        videos.append(
            {
                "video_id": video_id,
                "title": item.get("title") or "",
                "description": item.get("description") or "",
                "webpage_url": webpage_url,
                "published_at": published_at,
                "duration_seconds": item.get("duration") if isinstance(item.get("duration"), int) else None,
                "thumbnail_url": item.get("thumbnail") or "",
                "uploader": item.get("channel") or item.get("uploader") or "",
            }
        )

        if videos and videos[-1]["published_at"] is None:
            try:
                metadata = get_video_metadata(webpage_url)
                videos[-1]["published_at"] = metadata.get("published_at")
                if metadata.get("description"):
                    videos[-1]["description"] = metadata["description"]
                if metadata.get("duration_seconds") is not None:
                    videos[-1]["duration_seconds"] = metadata["duration_seconds"]
                if metadata.get("thumbnail_url"):
                    videos[-1]["thumbnail_url"] = metadata["thumbnail_url"]
                if metadata.get("uploader"):
                    videos[-1]["uploader"] = metadata["uploader"]
                if metadata.get("title"):
                    title = str(metadata["title"]).strip().lower()
                    if title not in {"[private video]", "[deleted video]"}:
                        videos[-1]["title"] = metadata["title"]
                    else:
                        videos.pop()
                        continue
            except Exception:
                pass

        if limit and len(videos) >= limit:
            break

    return videos


def get_video_metadata(video_url: str) -> dict:
    cmd = ["yt-dlp", "--dump-single-json", "--no-warnings", "--no-playlist", video_url]
    output = run_command(cmd)
    item = json.loads(output)

    raw_date = item.get("upload_date")
    published_at = None
    if isinstance(raw_date, str) and len(raw_date) == 8 and raw_date.isdigit():
        published_at = datetime.strptime(raw_date, "%Y%m%d")
    elif item.get("release_timestamp"):
        published_at = datetime.utcfromtimestamp(item["release_timestamp"])
    elif item.get("timestamp"):
        published_at = datetime.utcfromtimestamp(item["timestamp"])

    return {
        "title": item.get("title") or "",
        "description": item.get("description") or "",
        "published_at": published_at,
        "duration_seconds": item.get("duration") if isinstance(item.get("duration"), int) else None,
        "thumbnail_url": item.get("thumbnail") or "",
        "uploader": item.get("uploader") or item.get("channel") or "",
    }


def download_video(video_url: str, source_dir: str, video_id: str, audio_only: bool = True) -> str:
    Path(source_dir).mkdir(parents=True, exist_ok=True)
    template = str(Path(source_dir) / f"{video_id}.%(ext)s")

    cmd = ["yt-dlp", "--no-progress", "--no-playlist"]
    if audio_only:
        cmd.extend(["-f", "bestaudio", "-x", "--audio-format", "mp3"])
    else:
        cmd.extend(["-f", "bv*+ba/best", "--merge-output-format", "mp4"])
    cmd.extend(["-o", template, video_url])
    run_command(cmd)

    matches = sorted(Path(source_dir).glob(f"{video_id}.*"))
    if not matches:
        raise RuntimeError(f"No downloaded file found for {video_id}")
    return str(matches[0])
