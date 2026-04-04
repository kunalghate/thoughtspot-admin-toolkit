# Architecture

## Overview

ThoughtSpot Admin Toolkit is a locally-installed web application. A Python process
serves both the API (FastAPI) and the pre-built frontend (Next.js static export)
on localhost. All ThoughtSpot data is cached locally in SQLite. The app never
exposes any port to the internet.

```
Browser → http://localhost:8080
              │
              ▼
        FastAPI (Python)
        ├── /api/v1/*     ← REST API routes
        └── /static/*     ← serves pre-built Next.js SPA
              │                          │
              │ HTTPS                    │ read/write
              ▼                          ▼
    ThoughtSpot REST API v2       SQLite DB
    (customer's cluster)          (~/.ts-admin/db.sqlite)
```

---

## Tech stack

| Layer | Technology | Reason |
|---|---|---|
| Backend | Python + FastAPI | Standard Python web framework, async, auto-generated API docs |
| Frontend | Next.js (TypeScript) | Best ecosystem for data-heavy admin UIs |
| Data grid | AG Grid | Industry standard for filterable/sortable grids with virtual scroll |
| Graph viz | React Flow | Best library for interactive node/edge relationship graphs |
| UI components | shadcn/ui | Accessible, unstyled-by-default components |
| Local DB | SQLite (via SQLModel) | Zero-config, ships with Python, no server required |
| DB migrations | Alembic | Schema versioning from day one |
| HTTP client | httpx | Async-native, clean API |
| Credentials | OS keychain via `keyring` | Secure credential storage, never plaintext |
| Packaging | pyproject.toml + pip | Standard Python packaging |

---

## Project structure

```
thoughtspot-admin-toolkit/
├── ts_admin/                    # Python package (pip-installable)
│   ├── cli.py                   # `ts-admin-toolkit serve` entrypoint
│   ├── main.py                  # FastAPI app factory
│   ├── config.py                # Config loading (TOML + keyring + env vars)
│   ├── database.py              # SQLite setup, session factory
│   ├── ts_client/               # ThoughtSpot REST API layer
│   │   ├── client.py            # ThoughtSpotClient (thin HTTP wrapper)
│   │   ├── auth.py              # Auth strategies (Basic, Trusted, Bearer)
│   │   ├── models.py            # Pydantic response models for TS API
│   │   ├── exceptions.py        # Named exception hierarchy
│   │   └── retry.py             # Exponential backoff, rate limit handling
│   ├── api/                     # FastAPI routers (one file per feature)
│   │   ├── users.py
│   │   ├── groups.py
│   │   ├── metadata.py
│   │   ├── sharing.py
│   │   ├── archiver.py
│   │   ├── tml.py
│   │   ├── jobs.py
│   │   └── sync.py
│   ├── services/                # Business logic (orchestrates client + DB)
│   │   ├── user_service.py
│   │   ├── archiver_service.py
│   │   ├── sharing_service.py
│   │   ├── metadata_service.py
│   │   └── job_service.py
│   ├── models/                  # SQLModel table definitions
│   │   ├── cluster.py           # Cluster profiles
│   │   ├── sync_log.py          # Per-entity sync history
│   │   ├── audit_log.py         # Admin actions taken via this app
│   │   ├── job.py               # Background job queue + history
│   │   └── cache/               # Cached TS entities
│   │       ├── ts_user.py
│   │       ├── ts_group.py
│   │       ├── ts_metadata.py
│   │       ├── ts_tag.py
│   │       └── ts_dependency.py
│   └── static/                  # Pre-built Next.js (updated at release)
│       └── ...                  # Never edit manually
├── frontend/                    # Next.js source (developers only)
│   ├── pages/
│   ├── components/
│   │   ├── DataGrid/            # AG Grid wrapper with standard config
│   │   ├── BulkActionBar/       # Appears on row selection across all grids
│   │   ├── ConfirmDialog/       # Dry-run preview + confirmation
│   │   └── JobStatus/           # Live progress panel
│   ├── lib/
│   │   ├── api.ts               # Typed fetch wrapper for FastAPI
│   │   └── types.ts             # Shared TypeScript types
│   └── next.config.js           # output: 'export' for static build
├── tests/
│   ├── unit/                    # ThoughtSpotClient + Services (mock httpx)
│   ├── integration/             # API routes (FastAPI TestClient)
│   └── e2e/                     # Playwright (full user workflows)
├── alembic/                     # DB migrations
├── Makefile                     # make dev | build | test | release
└── pyproject.toml
```

---

## Cluster connection management

### Three-store pattern

A cluster's data is split across three stores. Each store has a specific role:

```
Store 1: ~/.ts-admin/config.toml       — non-sensitive connection config
  active_cluster = "production"

  [clusters.production]
  name     = "Production"
  url      = "https://company.thoughtspot.cloud"
  username = "admin@company.com"
  auth_type = "trusted"

Store 2: OS keychain (via `keyring`)   — credentials only, never plaintext
  service  = "ts-admin-toolkit"
  username = "production:secret_key"
  password = "79573ef6-..."            ← the actual secret key

Store 3: SQLite clusters table         — FK integrity for all cache tables
  id = "production", name = "Production",
  url = "...", username = "...", auth_type = "trusted"
```

**Why three stores?**
- TOML is human-readable, easy to inspect, version-controllable (without credentials)
- OS keychain is the secure store for secrets — never written to disk as plaintext
- SQLite is needed so every cache table (`ts_metadata`, `ts_users`, etc.) can have a valid FK to `clusters.id`

### Cluster lifecycle

```
CREATE (save_cluster)
  ├── Write to config.toml       (name, url, username, auth_type)
  ├── Write to OS keychain       (password | secret_key | token)
  ├── Write to SQLite clusters   (same non-sensitive fields)
  └── If first cluster: auto-set as active_cluster in config.toml

UPDATE (update_cluster)
  ├── Update config.toml         (any field)
  ├── If new_secret provided:
  │   ├── If auth_type changed: delete old keychain entry first
  │   └── Write new credential to keychain under new field name
  ├── Update SQLite clusters     (name, url, username, auth_type)
  └── If new_secret is None: keychain entry is left untouched

DELETE (delete_cluster)
  ├── Remove from config.toml
  ├── Delete from OS keychain    (all credential fields for this cluster)
  ├── Delete from SQLite clusters
  └── If was active_cluster: auto-promote next remaining cluster (or clear)
```

### Auth type → keychain field mapping

| Auth type | Keychain field | Value stored |
|---|---|---|
| `basic` | `password` | User's ThoughtSpot password |
| `trusted` | `secret_key` | Trusted auth secret key from Developer settings |
| `bearer` | `token` | Pre-obtained bearer token |

When auth type changes during an edit, the old keychain field is deleted and the new one is written. This prevents stale credentials from accumulating in the keychain.

### Orgs

Orgs are fetched live from the ThoughtSpot API — they are not cached in SQLite. The org list is fetched when the active cluster changes (on app load and on cluster switch). This keeps org data always fresh without needing a separate sync step.

---

## Data architecture

### Local cache (SQLite)

The app maintains a local copy of ThoughtSpot data. All browsing and filtering
reads from this cache. Write operations always go to ThoughtSpot live.

```
~/.ts-admin/db.sqlite

clusters           ← named connection profiles (prod, staging, ...)
  id, name, url, username, created_at

ts_users           ← cached ThoughtSpot users
ts_groups          ← cached ThoughtSpot groups
ts_metadata        ← cached content (liveboards, answers, worksheets, tables)
ts_tags            ← cached tags
ts_dependencies    ← content dependency graph (powers relationship visualizer)

sync_log           ← per-entity sync history (one row per entity type per cluster)
  cluster_id, entity_type, synced_at, record_count, duration_ms, status, error

audit_log          ← every admin action executed via this app
  cluster_id, action_type, parameters, items_affected, status, executed_at

jobs               ← background job queue and history
  id, cluster_id, job_type, status, progress, total, error, created_at, completed_at
```

Every cached row has a `cluster_id` foreign key. Switching clusters shows only
that cluster's cached data. Data from different clusters is never mixed.

### Per-entity sync

Each entity type (users, groups, metadata, tags, dependencies, orgs) syncs
independently with its own timestamp and status. Admins working only with users
and groups never need to trigger a metadata sync.

```
Sync is triggered by:
  1. First navigation to a section (auto-prompt if never synced)
  2. Admin clicks "Refresh" on any page
  3. Admin clicks "Refresh all" from Settings → Sync
```

Syncing runs as a background job — the UI remains responsive and shows live progress.

---

## Multi-cluster

The app supports multiple ThoughtSpot cluster profiles. All configuration is
done through the web UI (Settings → Connections). There is no CLI config command.

On first launch (no config), the app shows a setup/onboarding screen.
On subsequent launches, it goes straight to the dashboard.

Cluster switching uses the cluster picker in the top navigation bar.

---

## Authentication to ThoughtSpot

Three auth strategies are supported, selected per cluster profile:

| Strategy | How it works |
|---|---|
| Basic Auth | Username + password |
| Trusted Auth | Username + ThoughtSpot secret key (allows login as any user) |
| Bearer Token | Pre-obtained bearer token with configurable expiry |

Auth is implemented as a Strategy pattern (`auth.py`). Switching strategies
requires only a config change — no code change.

Token refresh (for Bearer) and session management are handled transparently
by `ThoughtSpotClient`.

---

## Background jobs

Bulk operations (sharing 500 objects, archiving 200 pieces of content) run as
background jobs so the UI stays responsive.

```
POST /api/v1/archiver/execute
  → Creates job record in SQLite (status: QUEUED)
  → Starts background task
  → Returns job_id immediately

GET /api/v1/jobs/{job_id}
  → Returns current status, progress (n/total), errors

Frontend polls every 2 seconds while job is RUNNING
  → Updates progress bar
  → On COMPLETE: shows summary (n succeeded, m failed)
  → On FAILED: shows error details
```

Every job is persisted in SQLite. Job history is visible at `/jobs` and survives
app restarts.

---

## Dry-run pattern

Every destructive operation (archive, delete, bulk share to restrict) supports
a dry-run mode. The flow is always:

```
1. User selects items and clicks action
2. App calls live TS API for dry-run impact preview
3. User sees: "This will affect N objects / M users" — with names
4. User confirms
5. App executes — calls TS API live, verifies objects still exist first
6. Result shown: N succeeded, M failed (with details)
7. Audit log entry written
```

Dry-run is not optional for destructive operations — the confirm dialog always
shows the impact preview. This is a non-negotiable UX pattern.

---

## Deployment modes

### Standard (default — for end users)
```
ts-admin-toolkit serve [--port 8080] [--profile production]
```
- FastAPI serves both the API and the pre-built Next.js static files
- No Node.js required
- Single process, single port

### Dev mode (for developers / advanced users with Node.js)
```
ts-admin-toolkit serve --dev
```
- FastAPI on `:8000` with hot reload
- Next.js dev server on `:3000` with hot reload
- Next.js proxies `/api/*` to FastAPI
- Both processes started and managed by the Python CLI launcher

### Security
- Binds to `127.0.0.1` only — never accessible from outside the machine
- CORS restricted to the local port only (never `*`)
- TS URL validated to be HTTPS and not a private/loopback address
- Credentials stored in OS keychain (never in plaintext files)

---

## Error handling principles

- Every exception class is named specifically — never `except Exception`
- Every caught exception logs: method name, arguments, cluster/user context, timestamp
- Every caught exception either: retries with backoff, degrades gracefully with a
  user-visible message, or re-raises with added context
- ThoughtSpot API rate limits (429) are handled transparently with exponential backoff
- Partial success on bulk operations returns HTTP 207 with per-item results

See the full error/rescue registry in [DECISIONS.md](DECISIONS.md).
