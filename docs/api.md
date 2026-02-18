# API Reference

Base path: same host as UI.

Authentication, CSRF, and rate-limiting behavior apply to API routes as configured.

## Security Model

- Basic Auth:
  - Controlled by `COMPANION_AUTH_REQUIRED`, `COMPANION_BASIC_AUTH_USERNAME`, `COMPANION_BASIC_AUTH_PASSWORD`.
  - By default, feed routes bypass auth (`COMPANION_AUTH_BYPASS_FEEDS=true`), API routes do not.
- CSRF:
  - Mutating API methods (`POST`, `PUT`, `PATCH`, `DELETE`) under `/api/*` require header:
    - name: `COMPANION_CSRF_HEADER_NAME` (default `x-companion-csrf`)
    - value: `COMPANION_CSRF_HEADER_VALUE` (default `1`)
- Rate limit:
  - Mutating `/api/*` requests are rate limited by `COMPANION_API_MUTATION_RATE_LIMIT_PER_MINUTE`.

## Common Error Statuses

- `400`: validation/business rule failure (for example unsupported channel host)
- `401`: auth required/invalid credentials
- `403`: missing/invalid CSRF header
- `404`: missing resource (for example unknown channel)
- `429`: mutation rate limit exceeded
- `503`: auth required but credentials not configured

## Routes

### `GET /api/channels`

Returns all channels ordered by newest first.

Response item:
- `id` (int)
- `url` (string)
- `name` (string)
- `last_indexed_at` (datetime or null)

### `POST /api/channels`

Create or return existing channel.

Request JSON:
- `url` (string, required, YouTube host only)
- `name` (string or null, optional)

Response:
- Channel object (same shape as `GET /api/channels` item)

Notes:
- If channel URL already exists, existing record is returned.
- Triggers feed regeneration.

### `POST /api/channels/sync_from_podsync`

Import channels from Podsync config file.

Response JSON:
- `ok` (bool)
- `added` (int) number of new channels added

### `POST /api/channels/{channel_id}/index`

Queue index job for a channel.

Response JSON:
- `ok` (bool)

Errors:
- `404` if channel not found

### `GET /api/videos`

List indexed videos with filtering, search, sorting, and pagination.

Query params:
- `channel_id` (int, optional)
- `limit` (int, default `100`, clamped to `1..500`)
- `offset` (int, default `0`, min `0`)
- `sort` (`desc` or `asc`, default `desc`, by `published_at`)
- `q` (string, optional; case-insensitive search across title/video_id/description/uploader)
- `include_unavailable` (bool, default `false`; excludes private/deleted markers when false)

Response item:
- `id` (int)
- `channel_id` (int)
- `video_id` (string)
- `title` (string)
- `description` (string)
- `webpage_url` (string)
- `published_at` (datetime or null)
- `duration_seconds` (int or null)
- `thumbnail_url` (string)
- `uploader` (string)

### `POST /api/downloads/enqueue`

Queue explicit downloads for indexed video IDs.

Request JSON:
- `video_ids` (array of strings)

Response JSON:
- `ok` (bool)
- `queued` (int) number queued now
- `skipped_existing` (int) skipped because already queued/running/done or already present via Podsync

### `GET /api/downloads`

List recent explicit download statuses and Podsync-detected statuses.

Response item:
- `video_id` (string)
- `status` (string, one of `queued`, `running`, `done`, `failed`, `podsync`, or empty legacy value)
- `filename` (string or null)
- `error` (string or null; redacted for API safety)

### `GET /api/jobs`

List recent jobs.

Response item:
- `id` (int)
- `job_type` (string; for example `index_channel`, `download_video`, `regenerate_manual_feed`, `sync_podsync_feeds`)
- `status` (string; `pending`, `running`, `done`, `failed`)
- `payload` (object)
- `error` (string or null; redacted)
- `created_at` (datetime)
- `updated_at` (datetime)

### `POST /api/feed/regenerate`

Queue regeneration of explicit and merged feed XML outputs.

Response JSON:
- `ok` (bool)

### `GET /api/feed`

Return feed URL metadata for UI/clients.

Response JSON:
- `manual_feed_url` (string)
- `manual_feed_path` (string)
- `merged_feed_path_prefix` (string)
- `merged_feed_url_template` (string containing `{channel_id}`)
- `media_url_path` (string)

### `GET /api/feed/merged`

List currently generated merged feeds.

Response item:
- `channel_id` (int)
- `channel_name` (string)
- `url` (string)

## Non-API Content Routes

- `GET /` web UI
- `GET {COMPANION_MANUAL_FEED_PATH}` explicit feed XML
- `GET {COMPANION_MERGED_FEED_PATH_PREFIX}/{channel_id}.xml` merged feed XML

