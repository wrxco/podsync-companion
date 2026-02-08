from datetime import datetime

from pydantic import BaseModel


class ChannelCreate(BaseModel):
    url: str
    name: str | None = None


class ChannelOut(BaseModel):
    id: int
    url: str
    name: str
    last_indexed_at: datetime | None

    class Config:
        from_attributes = True


class VideoOut(BaseModel):
    id: int
    channel_id: int
    video_id: str
    title: str
    description: str
    webpage_url: str
    published_at: datetime | None
    duration_seconds: int | None
    thumbnail_url: str
    uploader: str

    class Config:
        from_attributes = True


class EnqueueDownloadIn(BaseModel):
    video_ids: list[str]


class DownloadOut(BaseModel):
    video_id: str
    status: str
    source_path: str | None
    media_path: str | None
    filename: str | None
    error: str | None

    class Config:
        from_attributes = True
