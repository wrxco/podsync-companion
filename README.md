# Podsync Companion

`podsync-companion` is a companion service for Podsync that adds:

- full channel history indexing (metadata only),
- a web UI to select back-catalog episodes,
- manual download queue,
- automatic `manual.xml` regeneration after each completed manual download,
- automatic channel import from Podsync's `config.toml`.
- video browser defaults to oldest-to-newest, paged, and hides private/deleted entries.

Podsync remains the owner of your main Podsync feed XML. Companion publishes a second feed for manual picks.

## What it serves

- Web UI: `http://localhost:8080`
- Manual RSS feed: `http://localhost:8080/feeds/manual.xml`
- Merged RSS feed: `http://localhost:8080/feeds/merged.xml`
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

Each successful manual download updates `manual.xml`.

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
- Generated feed file: `/data/manual.xml`
- Generated merged feed file: `/data/merged.xml`
- Podsync config input: `/podsync/config.toml`
- Podsync XML input directory: `/podsync/data`

## Key sync settings

- `COMPANION_PODSYNC_CONFIG_PATH=/podsync/config.toml`
- `COMPANION_PODSYNC_DATA_DIR=/podsync/data`
- `COMPANION_PODSYNC_FEED_SYNC_INTERVAL_SECONDS=300`

## API summary

- `POST /api/channels` add channel `{ url, name? }`
- `POST /api/channels/sync_from_podsync` import feeds from Podsync config now
- `POST /api/channels/{id}/index` queue indexing
- `GET /api/videos?limit=300` list indexed videos
- `POST /api/downloads/enqueue` queue manual downloads `{ video_ids: [...] }`
- `GET /api/downloads` list manual download statuses
- `POST /api/feed/regenerate` regenerate `manual.xml`
- `POST /api/feed/regenerate` regenerate `manual.xml` and `merged.xml`
- `GET /api/feed` feed URL metadata
