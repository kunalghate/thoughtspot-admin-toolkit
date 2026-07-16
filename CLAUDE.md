# ThoughtSpot Admin Toolkit — Project Context

## What is this?

A locally-installed web application for ThoughtSpot administrators. Replaces
the CS Tools CLI with a web UI. Admins install it with `pip install`, run
`ts-admin-toolkit serve`, and get a browser-based admin control plane.

## Stack

- **Backend:** Python + FastAPI
- **Frontend:** Next.js (TypeScript), static export, bundled into pip package
- **UI:** AG Grid (data tables), React Flow (graph viz), shadcn/ui (components)
- **DB:** SQLite via SQLModel. (Note: `alembic/` exists but is **not** wired into
  the runtime path — `database.init_db()` uses `create_all` (additive-only). Rebuildable
  cache tables like the lineage tables use drop-and-rebuild-via-re-sync, not migrations.)
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
- **All config via web UI.** `ts-admin-toolkit serve` is the only CLI command. Setup,
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
- **Tests follow code.** When you modify `ts_admin/services/*` or `ts_admin/api/*`, update or create the matching test file in the same change. New destructive endpoints must be registered in `DRYRUN_ENDPOINTS` in `tests/integration/test_dryrun_safety.py`; new list/read endpoints in `READ_ENDPOINTS` in `tests/integration/test_cluster_isolation.py`. See [docs/dev/TESTING.md](docs/dev/TESTING.md). Run `/test` before declaring a task done.

---

# The Org (self-improving agent operating model)

This repo is worked by a small "organization" of agents coordinated by a CEO
orchestrator. The sections below are the durable contract every agent reads
first. See [.claude/skills/improve-cycle/SKILL.md](.claude/skills/improve-cycle/SKILL.md)
for the cycle, [.claude/agents/](.claude/agents/) for the departments, and
[docs/org-memory/README.md](docs/org-memory/README.md) for the memory model.

## Verification bar (for ANY change)

Every change an agent hands back must pass these, in order. **Never weaken a
gate to make it pass** — a red gate means "fix it or hand to a human," not
"relax the gate."

1. **Lint + format** — `ruff check ts_admin/ tests/` && `ruff format --check ts_admin/ tests/`
2. **Backend tests** — `pytest tests/unit/ -v` && `pytest tests/integration/ -v`
3. **Frontend typecheck** — `cd frontend && npx tsc --noEmit`
4. **Build** — `cd frontend && npm run build` (static export → `ts_admin/static/`)
5. **Feature check** — exercise the backlog row's acceptance criteria (drive the
   endpoint / confirm a guard test covers it). Use `/test` for the coverage-aware
   slice, and `/code-review` + `/security-review` on the diff.

These are exactly the gates that run green in CI today. Known gaps are tracked as
backlog rows, **never faked as passing**: `mypy ts_admin/` runs locally but not in
CI (W-row); frontend `vitest` is installed but not wired (S-row); Playwright e2e
and a `/health` smoke check are not in CI (S-row).

## Protected paths (no autonomous change without a human)

An agent must NOT modify these without a human adding the `human-approved` label
on the PR. The CI `guard` job enforces this and agents never add the label,
never `gh pr merge --admin`, and never weaken the guard.

- `CLAUDE.md` — this constitution (so the rules cannot self-amend)
- `.github/workflows/*` — the gates themselves
- `.env*` and any secret material (real credentials live in the OS keychain via `ts_admin/config.py`)
- **Security layer:** `ts_admin/main.py` (CORS localhost-only), `ts_admin/services/cluster_service.py` (`validate_cluster_url` SSRF check), `ts_admin/cli.py` (127.0.0.1 binding), `ts_admin/config.py` (keyring)
- **Guard tests + registries:** `tests/integration/test_dryrun_safety.py` (`DRYRUN_ENDPOINTS`), `tests/integration/test_cluster_isolation.py` (`READ_ENDPOINTS`), `tests/integration/test_audit_log_writes.py`, `tests/unit/test_cluster_service.py`

Appending a row to `DRYRUN_ENDPOINTS`/`READ_ENDPOINTS` for a genuinely new
endpoint is required and expected (tests-follow-code) — but because it touches a
protected file, that PR needs the `human-approved` label. Never relax an existing
guard assertion.

## How the org works (operating model)

Three standing goals, in **precedence order**:

1. **Improve the app** — stability, features, refactors (correctness · efficiency · performance).
2. **Keep it current** — track ThoughtSpot REST API v2 drift and Python/Node dependency drift.
3. **Improve the org itself** — sharpen agents/skills/gates; file a process (M) row
   whenever a cycle exposes a gate that missed something or an ambiguous rule.

- The **CEO** is the orchestrating session ONLY (the `/improve-cycle` skill). It
  dispatches departments and does not do department work itself. Independent
  agents run in **parallel** (one message, multiple spawns); dependent stages
  (research → design → build → review) stay **sequential**.
- **Departments** = the agents in [.claude/agents/](.claude/agents/). Every change
  is a branch + PR with a verification-evidence section. **Nothing auto-merges** —
  every PR stops for human review.
- **Three memory stores** (see [docs/org-memory/README.md](docs/org-memory/README.md)):
  `CLAUDE.md` = **Rules** (human-approved PRs only), `BACKLOG.md` = **Tasks**
  (cycles change Status/notes/append rows ONLY), `docs/org-memory/` = **Facts**
  (written at the Records step, read before every task).
- **Protected-path safety:** the CI `guard` job fails any PR touching a protected
  path unless a human adds the `human-approved` label. A red guard means "hand to
  human."
- **Gate serialization:** pytest integration uses an in-memory TestClient, but
  `make dev`/Playwright bind fixed ports (8000/3000). Only ONE agent runs
  server-bound gates at a time; parallel worktree implementers run the cheap
  lint/typecheck gate only.

**Autonomy line — this org is human-gated.** A PR is ready for a human to merge
only when: required checks are green ∧ review found no CONFIRMED correctness bug
∧ the `guard` job is green. Agents never merge to `main`, never add the
`human-approved` label, never use `--admin`.
