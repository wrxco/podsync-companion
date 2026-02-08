# Podsync Companion

`podsync-companion` is a companion service for Podsync that adds:

- full channel history indexing (metadata only),
- a web UI to select back-catalog episodes,
- manual download queue,
- automatic `manual.xml` regeneration after each completed manual download,
- automatic channel import from Podsync's `config.toml`.

Podsync remains the owner of your main Podsync feed XML. Companion publishes a second feed for manual picks.

## What it serves

- Web UI: `http://localhost:8080`
- Manual RSS feed: `http://localhost:8080/feeds/manual.xml`
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

## Important paths inside companion container

- SQLite DB: `/data/companion.db`
- Download temp/source: `/data/source`
- Final manual media: `/data/media`
- Generated feed file: `/data/manual.xml`
- Podsync config input: `/podsync/config.toml`

## Key sync settings

- `COMPANION_PODSYNC_CONFIG_PATH=/podsync/config.toml`
- `COMPANION_PODSYNC_FEED_SYNC_INTERVAL_SECONDS=300`

## API summary

- `POST /api/channels` add channel `{ url, name? }`
- `POST /api/channels/sync_from_podsync` import feeds from Podsync config now
- `POST /api/channels/{id}/index` queue indexing
- `GET /api/videos?limit=300` list indexed videos
- `POST /api/downloads/enqueue` queue manual downloads `{ video_ids: [...] }`
- `GET /api/downloads` list manual download statuses
- `POST /api/feed/regenerate` regenerate `manual.xml`
- `GET /api/feed` feed URL metadata
