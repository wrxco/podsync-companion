import re
from pathlib import Path

VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{6,20}")
YOUTUBE_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
FILENAME_VIDEO_ID_RE = re.compile(r"_([A-Za-z0-9_-]{6,20})\.[^.]+$")
URL_VIDEO_ID_PATTERNS = (
    re.compile(r"[?&]v=([A-Za-z0-9_-]{6,20})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{6,20})"),
    re.compile(r"youtube\.com/(?:shorts|live|embed)/([A-Za-z0-9_-]{6,20})"),
)


def extract_video_id(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""

    for pattern in URL_VIDEO_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)

    file_part = Path(text.split("?", 1)[0]).name
    stem = Path(file_part).stem

    if len(stem) >= 11:
        tail = stem[-11:]
        if YOUTUBE_VIDEO_ID_RE.fullmatch(tail):
            return tail

    filename_match = FILENAME_VIDEO_ID_RE.search(file_part)
    if filename_match:
        return filename_match.group(1)

    stem_parts = [part for part in re.split(r"[_\-. ]+", stem) if part]
    for part in reversed(stem_parts):
        if VIDEO_ID_RE.fullmatch(part):
            return part

    match = VIDEO_ID_RE.fullmatch(text)
    if match:
        return text

    return ""
