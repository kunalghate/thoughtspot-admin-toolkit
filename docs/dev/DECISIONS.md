# Architecture Decision Records

Decisions made during planning, with the rationale for each.
Update this file when significant decisions are revisited or changed.

---

## ADR-001: FastAPI as the backend framework

**Decision:** Use FastAPI (Python).

**Rationale:**
- Python was a firm requirement — the codebase is shared with customers and must
  be readable to any Python developer
- FastAPI is the current standard for Python APIs — async-native, auto-generated
  OpenAPI docs, excellent type safety via Pydantic
- Familiar to the broadest range of Python developers

**Alternatives considered:**
- Flask: simpler but no async, no built-in validation
- Django: too heavy, adds ORM + admin + auth we don't need

---

## ADR-002: Next.js as the frontend framework

**Decision:** Use Next.js (TypeScript/React), built as a static export, bundled
into the Python package.

**Rationale:**
- The app requires data grids with filtering/sorting/virtual scrolling for
  potentially 50k+ rows, and a relationship graph visualizer. These requirements
  demand a JavaScript-based frontend.
- Next.js with `output: 'export'` produces static files that FastAPI serves.
  End users get a single `pip install` — no Node.js required.
- Developers need Node.js to modify the UI, but this is acceptable.

**Alternatives considered:**
- HTMX: limited for complex client-side data grids and graph visualization
- NiceGUI: Python-only but lower UI ceiling and smaller ecosystem
- Streamlit: not suited for multi-step admin workflows

**Packaging approach:**
- At release time: `npm run build` → `frontend/out/` → copied to `ts_admin/static/`
- Static files are committed to the repo under a `release` branch/tag
- In dev mode (`--dev` flag): Next.js dev server runs alongside FastAPI with hot reload

---

## ADR-003: No dependency on CS Tools library

**Decision:** Build the ThoughtSpot REST API layer (`ts_client/`) from scratch
using httpx. Do not import `cs_tools` as a library.

**Rationale:**
- CS Tools is designed as a CLI tool, not a library. Its programmatic API is
  undocumented and not stable.
- Taking a direct dependency on it would expose our users to CS Tools' entire
  dependency tree and release cycle.
- The ThoughtSpot REST API v2 is well-documented. Building a clean, thin wrapper
  is a manageable one-time effort and gives us full control.

**Trade-off:** More initial build effort for the `ts_client/` layer.

---

## ADR-004: SQLite as the local database

**Decision:** Use SQLite (via SQLModel + Alembic) for all local storage.

**Rationale:**
- Zero-config: ships with Python, no server, no install
- Single-file database in `~/.ts-admin/db.sqlite` — easy to inspect, backup, reset
- Sufficient for single-admin local use case
- Schema is versioned with Alembic from day one, making a future migration to
  Postgres (for multi-user deployment) a config change rather than a rewrite

**All tables:** clusters, ts_users, ts_groups, ts_metadata, ts_tags, ts_dependencies,
sync_log, audit_log, jobs.

---

## ADR-005: Per-entity lazy sync model

**Decision:** Each entity type (users, groups, metadata, tags, dependencies, orgs)
syncs independently with its own timestamp and can be refreshed without affecting others.

**Rationale:**
- A ThoughtSpot instance with 50k+ objects takes 30-60 seconds to fully enumerate.
  A full sync on every app launch is unusable.
- Admins typically work on one domain at a time (e.g., user management only).
  Forcing a full sync wastes time and TS API quota.
- Per-entity sync gives admins precise control: refresh only what they need.

**Stale thresholds (advisory only — app still works with stale data):**
- < 1 hour: fresh (gray indicator)
- 1–6 hours: consider refreshing (yellow indicator)
- > 6 hours: refresh recommended (orange indicator)

**Dependencies entity:** Never auto-synced. Only synced when the admin navigates
to the relationship visualizer, with an explicit warning about duration.

---

## ADR-006: Multi-cluster support in v1

**Decision:** Build multi-cluster support (multiple named ThoughtSpot profiles)
in v1, not deferred.

**Rationale:**
- Multi-cluster affects the config schema and database schema. Retrofitting it
  after v1 would require a data migration and schema redesign.
- Many admins manage dev/staging/prod environments or work as consultants.
- The implementation cost is low if done from the start (one `cluster_id` FK on
  every cached table).

**Cluster switching:** via the cluster picker in the top navigation bar.
Each cluster has its own independent local cache in SQLite.

---

## ADR-007: All configuration via web UI (no CLI config commands)

**Decision:** Connection setup, cluster management, and all configuration is done
through the web UI. The CLI does one thing: `ts-admin-toolkit serve`.

**Rationale:**
- This is a web application. The configuration experience should be web-native.
- A CLI config flow is inconsistent with the "no terminal knowledge required" goal.
- On first launch with no config, the app shows a setup/onboarding screen.
  On subsequent launches, it goes straight to the dashboard.

---

## ADR-008: Credentials stored in OS keychain

**Decision:** Passwords and secret keys are stored in the OS keychain via the
`keyring` Python library. URLs and usernames are stored in a TOML config file.
Nothing sensitive is ever written to disk in plaintext.

**Rationale:**
- Plaintext credentials in a config file are a security risk on shared machines
  and in version-controlled directories.
- The OS keychain (macOS Keychain, Windows Credential Manager, Linux Secret Service)
  is the standard secure credential store for desktop applications.

**Fallback:** If the OS keychain is unavailable, the app reads credentials from
environment variables (`TS_ADMIN_PASSWORD`, `TS_ADMIN_SECRET_KEY`). It never
falls back to plaintext file storage.

---

## ADR-009: Dry-run required for all destructive operations

**Decision:** Every destructive operation (archive, delete, bulk share with restrict)
must show an impact preview and require explicit confirmation. Dry-run is not optional.

**Rationale:**
- Bulk operations on production ThoughtSpot instances are high-risk.
  Mistakes can affect hundreds of users.
- Admins need to see real numbers (not estimates) before confirming: exactly
  which objects will be affected, how many users, which groups.
- The "dry run → preview → confirm → execute" flow is non-negotiable UX.

---

## ADR-010: Single-admin, localhost-only in v1

**Decision:** v1 binds to `127.0.0.1` only. No web authentication is required.
The assumption is one admin per installation on their own machine.

**Rationale:**
- Multi-user deployment requires a full auth layer (login, sessions, user table),
  which significantly increases complexity and delays the MVP.
- The majority of target users are individual admins running this locally.

**Future path:** The auth layer is designed as pluggable middleware from the start
so multi-user support can be added in v2 without a rewrite.

**Known limitation:** Anyone with access to the machine can access the app on localhost.
A PIN/passphrase option is planned for v1.1 for shared machines.

---

## ADR-011: Relationship visualizer in v1

**Decision:** Build the React Flow relationship visualizer in v1 (not deferred).

**Rationale:**
- Understanding content dependencies before making changes is one of the most
  common admin pain points.
- The visualizer depends on the metadata + dependencies sync which is also in v1.
- React Flow integrates cleanly into the Next.js frontend.

**Scope:** Object dependency graph (which Liveboards depend on which Worksheets/Tables)
and user-group membership graph. Built on top of the `ts_dependencies` SQLite table.

---

## Security decisions

| Decision | Rationale |
|---|---|
| Bind to 127.0.0.1 only | Never expose admin access beyond the local machine |
| CORS restricted to local port | Prevent malicious pages from calling the local API |
| Validate TS URL is HTTPS and not localhost/private range | Prevent SSRF attacks |
| No `dangerouslySetInnerHTML` in React | Prevent XSS from malicious object names in TS |
| Audit log for all write operations | Accountability for every change made via the app |

---

## Deferred decisions (revisit in future phases)

| Decision | Why deferred | Target phase |
|---|---|---|
| Multi-user auth (login screen, sessions) | Significant complexity, MVP is single-admin | v2 |
| Scheduled/cron workflows | Requires APScheduler + notification system | v2 |
| Slack/email notifications | Needs external config, not blocking | v2 |
| Optional PIN/passphrase for localhost | Useful for shared machines, not blocking | v1.1 |
| Auto-sync background thread | Manual refresh sufficient for MVP | v1.1 |
| Postgres support | SQLite sufficient for single-admin | v2 (multi-user) |
