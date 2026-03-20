# ThoughtSpot Admin Toolkit — Project Context

## What is this?

A locally-installed web application for ThoughtSpot administrators. Replaces
the CS Tools CLI with a web UI. Admins install it with `pip install`, run
`ts-admin serve`, and get a browser-based admin control plane.

## Stack

- **Backend:** Python + FastAPI
- **Frontend:** Next.js (TypeScript), static export, bundled into pip package
- **UI:** AG Grid (data tables), React Flow (graph viz), shadcn/ui (components)
- **DB:** SQLite via SQLModel + Alembic migrations
- **HTTP client:** httpx (async)
- **Credentials:** OS keychain via `keyring`

## Key architectural decisions

Read [docs/dev/DECISIONS.md](docs/dev/DECISIONS.md) for full rationale. Short version:

- **No cs_tools dependency.** We call ThoughtSpot REST API v2 directly via `ts_client/`.
- **Static export.** Next.js builds to `ts_admin/static/` at release time. End users
  need only Python. Developers need Node.js.
- **SQLite local cache.** All TS data is synced locally. Browsing reads from SQLite.
  Write operations always go to TS live.
- **Per-entity lazy sync.** Users, groups, metadata, tags, dependencies each sync
  independently. Never force a full sync.
- **Multi-cluster in v1.** Every SQLite table has a `cluster_id` FK.
- **No web auth in v1.** Binds to 127.0.0.1. Single-admin local use.
- **All config via web UI.** `ts-admin serve` is the only CLI command. Setup,
  connection management, and cluster switching are all in-app.
- **Dry-run required** for all destructive operations. Non-negotiable UX pattern.

## Developer workflow

```bash
make dev      # FastAPI (:8000) + Next.js dev (:3000) with hot reload
make test     # pytest + vitest + Playwright
make build    # next build → copy to ts_admin/static/
make release  # bump version + commit static/ + tag + publish
```

## Project structure highlights

```
ts_admin/ts_client/    ← ThoughtSpot REST API layer (thin HTTP, no business logic)
ts_admin/services/     ← Business logic (orchestrates client + DB calls)
ts_admin/api/          ← FastAPI routers (one per feature)
ts_admin/models/       ← SQLModel table definitions (always include cluster_id)
frontend/              ← Next.js source (developers only, never shipped directly)
ts_admin/static/       ← Pre-built frontend (updated by `make build` at release)
docs/dev/              ← Architecture, decisions, TODOs (for developers)
docs/user/             ← Getting started, sync guide (for ThoughtSpot admins)
```

## MVP features (v1)

User Management · Content Archiver · Bulk Sharing · Metadata Explorer ·
Relationship Visualizer · Multi-cluster · CSV export · Background jobs · Audit log

## What's deferred

See [docs/dev/TODOS.md](docs/dev/TODOS.md). Key deferred items:
- Multi-user auth (v2)
- Scheduled/cron workflows (v2)
- Auto-sync background thread (v1.1)
- PIN/passphrase for localhost (v1.1)

## Critical rules

- `ThoughtSpotClient` is a thin HTTP wrapper only. No business logic in it.
- Every SQLite table must have a `cluster_id` FK — multi-cluster is v1.
- Never `except Exception` — name the specific exception class.
- Every write operation: verify live before executing, dry-run first, audit log after.
- CORS must be restricted to `http://localhost:{port}` — never `*`.
- TS URL must be validated as HTTPS and not a private/loopback address (SSRF prevention).
