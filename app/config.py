from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COMPANION_", extra="ignore")

    db_url: str = "sqlite:////data/companion.db"
    poll_interval_seconds: int = 3
    source_dir: str = "/data/source"
    media_dir: str = "/data/media"
    channel_scan_limit: int = 0  # 0 means unlimited
    public_base_url: str = "http://localhost:8080"
    media_url_path: str = "/media"
    manual_feed_path: str = "/feeds/manual.xml"
    manual_feed_file: str = "/data/manual.xml"
    manual_feed_title: str = "Podsync Companion Manual Feed"
    manual_feed_description: str = "Manually selected back-catalog episodes"
    download_audio: bool = True


settings = Settings()
