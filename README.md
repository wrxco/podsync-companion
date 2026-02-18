# Podsync Companion

`podsync-companion` adds channel indexing, explicit episode selection/downloads, and feed generation on top of Podsync.

## Quick Start

```bash
docker compose up --build -d
```

1. Open `http://localhost:8080`
2. Sync channels from Podsync or add a channel URL
3. Index channels
4. Queue explicit episodes as needed

## Minimal Compose Example

```yaml
services:
  podsync-companion:
    image: ghcr.io/wrxco/podsync-companion:latest
    restart: unless-stopped
    environment:
      COMPANION_DB_URL: sqlite:////data/companion.db
      COMPANION_PUBLIC_BASE_URL: https://podsync-companion.example.com
      COMPANION_BASIC_AUTH_USERNAME: change-me
      COMPANION_BASIC_AUTH_PASSWORD: change-me-strong-password
    volumes:
      - ./podsync-companion:/data
      - ./podsync/config.toml:/podsync/config.toml:ro
      - ./podsync/data:/podsync/data:ro
```

## Docs

- Full configuration reference (all options, defaults, behavior): `docs/configuration.md`
- Full API reference (routes, request/response, auth/CSRF/rate-limit behavior): `docs/api.md`
- Expanded compose example: `docker-compose.example.yml`
