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

    # Per-channel merged feeds live at /feeds/merged/<channel_id>.xml
    merged_feed_path_prefix: str = "/feeds/merged"
    merged_feed_dir: str = "/data/merged"
    merged_feed_title_suffix: str = "Merged Feed"
    merged_feed_description: str = "Podsync feed items plus companion manual items"

    download_audio: bool = True

    # Read-only mount of Podsync config for automatic channel import.
    podsync_config_path: str = "/podsync/config.toml"
    podsync_data_dir: str = "/podsync/data"
    podsync_feed_sync_interval_seconds: int = 300


settings = Settings()
