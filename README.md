# Podsync Companion

`podsync-companion` is a companion service for Podsync that adds:

- full channel history indexing (metadata only),
- a web UI to select back-catalog episodes,
- manual download queue,
- automatic `manual.xml` regeneration after each completed manual download,
- automatic channel import from Podsync's `config.toml`,
- per-channel merged feeds (Podsync feed + companion manual downloads for that channel).

Podsync remains the owner of your main Podsync feed XML.

## What it serves

- Web UI: `http://localhost:8080`
- Manual RSS feed: `http://localhost:8080/feeds/manual.xml`
- Per-channel merged RSS feed: `http://localhost:8080/feeds/merged/<channel_id>.xml`
- Manual media files: `http://localhost:8080/media/<filename>`

## Run

```bash
docker compose up --build -d
```

Then:

1. Open `http://localhost:8080`
2. Channels from Podsync config are auto-imported at startup
3. Click `Index channel`
4. Select episodes and click `Queue selected`

Each successful manual download updates `manual.xml` and the merged feed for that channel.

## Security defaults

- Auth fails closed by default. You must configure credentials or requests are rejected (`503`):
  - `COMPANION_AUTH_REQUIRED=true` (default)
  - `COMPANION_BASIC_AUTH_USERNAME`
  - `COMPANION_BASIC_AUTH_PASSWORD`
- Anti-CSRF protection is enabled by default for mutating API routes (`POST/PUT/PATCH/DELETE` under `/api/*`).
  Clients must send the configured header:
  - `COMPANION_CSRF_PROTECTION_ENABLED=true` (default)
  - `COMPANION_CSRF_HEADER_NAME=x-companion-csrf`
  - `COMPANION_CSRF_HEADER_VALUE=1`
- Channel/video URL ingestion is restricted to YouTube hosts.
- Mutating API routes are rate-limited by default:
  - `COMPANION_API_MUTATION_RATE_LIMIT_PER_MINUTE=60`

### TLS and exposure

- Do not expose Basic Auth over plain HTTP.
- Recommended deployment: reverse proxy with HTTPS and local bind for companion (`127.0.0.1:8080:8080`).
- Set `COMPANION_PUBLIC_BASE_URL` to your public `https://...` URL.

### Operational roadblocks to expect

- If auth credentials are not configured while `COMPANION_AUTH_REQUIRED=true`, all requests fail with `503`.
- If a non-UI client calls mutating `/api/*` routes without the CSRF header, requests fail with `403`.
- If a client exceeds the mutation rate limit, requests fail with `429`.
- To disable these protections for trusted local development only:
  - `COMPANION_AUTH_REQUIRED=false`
  - `COMPANION_CSRF_PROTECTION_ENABLED=false`
  - `COMPANION_API_MUTATION_RATE_LIMIT_PER_MINUTE=0`

## Compose notes

- Service name: `podsync-companion`
- Environment prefix: `COMPANION_`
- Data volume: `./companion-data:/data`
- Mount Podsync config read-only into companion:
  `./podsync/config.toml:/podsync/config.toml:ro`
- Mount Podsync data read-only into companion for merged feed import:
  `./podsync/data:/podsync/data:ro`

## Important paths inside companion container

- SQLite DB: `/data/companion.db`
- Download temp/source: `/data/source`
- Final manual media: `/data/media`
- Generated manual feed file: `/data/manual.xml`
- Generated per-channel merged feed files: `/data/merged/<channel_id>.xml`
- Podsync config input: `/podsync/config.toml`
- Podsync XML input directory: `/podsync/data`

## Key sync settings

- `COMPANION_PODSYNC_CONFIG_PATH=/podsync/config.toml`
- `COMPANION_PODSYNC_DATA_DIR=/podsync/data`
- `COMPANION_PODSYNC_FEED_SYNC_INTERVAL_SECONDS=300`
- `COMPANION_CHANNEL_SCAN_LIMIT=200` (recommended; set `0` for unlimited)
- `COMPANION_INDEX_COMMAND_TIMEOUT_SECONDS=1800`
- `COMPANION_AUTH_REQUIRED=true`
- `COMPANION_BASIC_AUTH_USERNAME=...`
- `COMPANION_BASIC_AUTH_PASSWORD=...`
- `COMPANION_CSRF_PROTECTION_ENABLED=true`
- `COMPANION_CSRF_HEADER_NAME=x-companion-csrf`
- `COMPANION_CSRF_HEADER_VALUE=1`
- `COMPANION_API_MUTATION_RATE_LIMIT_PER_MINUTE=60`

## Merged feed settings

- `COMPANION_MERGED_FEED_PATH_PREFIX=/feeds/merged`
- `COMPANION_MERGED_FEED_DIR=/data/merged`
- `COMPANION_MERGED_FEED_TITLE_SUFFIX=Merged Feed`

## API summary

- `POST /api/channels` add channel `{ url, name? }`
- `POST /api/channels/sync_from_podsync` import feeds from Podsync config now
- `POST /api/channels/{id}/index` queue indexing
- `GET /api/videos?limit=300` list indexed videos
- `POST /api/downloads/enqueue` queue manual downloads `{ video_ids: [...] }`
- `GET /api/downloads` list manual download statuses
- `POST /api/feed/regenerate` regenerate manual + per-channel merged feeds
- `GET /api/feed` feed URL metadata (manual URL + merged URL template)
