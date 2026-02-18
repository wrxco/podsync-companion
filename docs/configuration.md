# Configuration Reference

All environment variables are read with the `COMPANION_` prefix.

Example: `db_url` setting is configured with `COMPANION_DB_URL`.

## Defaults And Behavior

| Variable | Default | Description | Typical override |
| --- | --- | --- | --- |
| `COMPANION_DB_URL` | `sqlite:////data/companion.db` | SQLAlchemy DB URL for companion state. | Point to persistent path/DB service. |
| `COMPANION_POLL_INTERVAL_SECONDS` | `3` | Worker loop polling interval. | Increase to reduce DB churn. |
| `COMPANION_SOURCE_DIR` | `/data/source` | Temporary download staging directory. | Rarely overridden. |
| `COMPANION_MEDIA_DIR` | `/data/media` | Final explicit media directory served by companion. | Match volume layout. |
| `COMPANION_CHANNEL_SCAN_LIMIT` | `0` | Max videos scanned per index job (`0` = unlimited). | Set to `100-500` for faster, bounded indexing. |
| `COMPANION_INDEX_COMMAND_TIMEOUT_SECONDS` | `1800` | Timeout for index command execution. | Increase for large channels. |
| `COMPANION_PUBLIC_BASE_URL` | `http://localhost:8080` | Public base URL used in feed links. | Set to your real HTTPS URL. |
| `COMPANION_MEDIA_URL_PATH` | `/media` | URL path that serves explicit media files. | Keep default unless routing requires changes. |
| `COMPANION_MANUAL_FEED_PATH` | `/feeds/manual.xml` | Explicit feed URL path. | Rarely overridden. |
| `COMPANION_MANUAL_FEED_FILE` | `/data/manual.xml` | Explicit feed output file path. | Match storage mount. |
| `COMPANION_MANUAL_FEED_TITLE` | `Podsync Companion Manual Feed` | Explicit feed channel title. | Customize branding. |
| `COMPANION_MANUAL_FEED_DESCRIPTION` | `Manually selected back-catalog episodes` | Explicit feed channel description. | Customize text. |
| `COMPANION_MANUAL_FEED_IMAGE_URL` | *(empty)* | Optional explicit feed artwork override URL. If empty, artwork is derived from Podsync feeds when possible. | Set explicit artwork URL for stable cover art. |
| `COMPANION_MERGED_FEED_PATH_PREFIX` | `/feeds/merged` | URL prefix for per-channel merged feeds. | Rarely overridden. |
| `COMPANION_MERGED_FEED_DIR` | `/data/merged` | Output directory for merged XML files. | Match storage mount. |
| `COMPANION_MERGED_FEED_TITLE_SUFFIX` | `Merged Feed` | Suffix used when generating merged feed titles without Podsync title metadata. | Branding only. |
| `COMPANION_MERGED_FEED_DESCRIPTION` | `Podsync feed items plus companion manual items` | Fallback merged feed description. | Branding only. |
| `COMPANION_DOWNLOAD_AUDIO` | `true` | If `true`, explicit downloads are audio-only (`mp3`). If `false`, video format path is used. | Set `false` for video downloads. |
| `COMPANION_PODSYNC_CONFIG_PATH` | `/podsync/config.toml` | Path to Podsync config for channel auto-import. | Match mounted Podsync config path. |
| `COMPANION_PODSYNC_DATA_DIR` | `/podsync/data` | Podsync data directory used for merged feed import and Podsync download detection. | Must match mounted Podsync data path. |
| `COMPANION_PODSYNC_FEED_SYNC_INTERVAL_SECONDS` | `300` | Interval for periodic Podsync config/feed synchronization. | Increase for lower background activity. |
| `COMPANION_AUTH_REQUIRED` | `true` | Require Basic Auth globally. If true and credentials missing, requests fail with `503`. | Set `false` only in trusted local setups. |
| `COMPANION_BASIC_AUTH_USERNAME` | *(empty)* | Basic Auth username. | Required when auth is enabled. |
| `COMPANION_BASIC_AUTH_PASSWORD` | *(empty)* | Basic Auth password. | Required when auth is enabled. |
| `COMPANION_AUTH_BYPASS_FEEDS` | `true` | If true, feed routes bypass Basic Auth: `/feeds/manual.xml` and `/feeds/merged/*`. | Set `false` to protect feed URLs too. |
| `COMPANION_CSRF_PROTECTION_ENABLED` | `true` | Enforce CSRF header on mutating `/api/*` routes. | Set `false` only for trusted local clients. |
| `COMPANION_CSRF_HEADER_NAME` | `x-companion-csrf` | Required header name for CSRF check. | Change only if custom clients need different header name. |
| `COMPANION_CSRF_HEADER_VALUE` | `1` | Required header value for CSRF check. | Change only if coordinating custom clients. |
| `COMPANION_API_MUTATION_RATE_LIMIT_PER_MINUTE` | `60` | Per-client+path limit for mutating `/api/*` requests. `<=0` disables. | Raise for automation-heavy usage. |

## Security Roadblocks (Fail-Closed Paths)

- If `COMPANION_AUTH_REQUIRED=true` and username/password are not both set, all requests fail with `503`.
- If CSRF protection is enabled and mutating `/api/*` requests omit the required header, requests fail with `403`.
- If mutation rate limit is exceeded, requests fail with `429`.

## URL And Path Summary

- UI: `/`
- Explicit feed: `COMPANION_MANUAL_FEED_PATH` (default `/feeds/manual.xml`)
- Merged feeds: `COMPANION_MERGED_FEED_PATH_PREFIX/{channel_id}.xml` (default `/feeds/merged/{channel_id}.xml`)
- Media serving: `COMPANION_MEDIA_URL_PATH/{filename}` (default `/media/{filename}`)

## Recommended Production Baseline

- Keep auth enabled and set strong credentials.
- Keep CSRF enabled.
- Keep rate limit enabled.
- Set `COMPANION_PUBLIC_BASE_URL` to public HTTPS origin.
- Mount persistent storage for `/data`.
- Mount Podsync config/data read-only into companion.

