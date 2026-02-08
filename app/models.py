from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    videos: Mapped[list["Video"]] = relationship(back_populates="channel")


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (UniqueConstraint("channel_id", "video_id", name="uq_channel_video"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(1024), default="")
    description: Mapped[str] = mapped_column(String(4096), default="")
    webpage_url: Mapped[str] = mapped_column(String(1024), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thumbnail_url: Mapped[str] = mapped_column(String(1024), default="")
    uploader: Mapped[str] = mapped_column(String(255), default="")
    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    channel: Mapped[Channel] = relationship(back_populates="videos")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Download(Base):
    __tablename__ = "downloads"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    source_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    media_path: Mapped[str | None] = mapped_column("library_path", String(2048), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
