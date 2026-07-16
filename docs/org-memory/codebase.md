# Codebase facts

Verified facts about this code. Read before any task. One dated bullet per fact
with `file:line` evidence and the originating cycle/PR. Prune to ~120 lines.

## Stack & commands

- 2026-07-15 (BOOT): Verification bar = `ruff check` + `ruff format --check`,
  `pytest tests/unit` + `pytest tests/integration`, `cd frontend && npx tsc
  --noEmit`, `cd frontend && npm run build`. Source: `Makefile`,
  `.github/workflows/ci.yml`.
- 2026-07-15 (BOOT): `mypy ts_admin/` (Makefile `typecheck`) and frontend `vitest`
  exist but are NOT in CI; Playwright e2e is not in CI either. Tracked as BACKLOG
  S1 / S2 / W1 — do not treat as passing gates.
- 2026-07-15 (S1): Frontend `vitest` is now WIRED locally (still not a CI gate —
  the ci.yml step is a deferred protected change). `cd frontend && npm test` =
  `vitest run` (non-watch, CI-safe); `npm run test:watch` for watch mode.
- 2026-07-15 (S1): KNOWN RED on `main` — `ruff format --check ts_admin/ tests/`
  fails on 5 PR-#10 files (`ts_admin/services/lineage_service.py`,
  `tests/unit/test_lineage_{columns,models,service}.py`,
  `tests/integration/test_relationships_api.py`) under local ruff `0.15.10`. Cause:
  unbounded `ruff>=0.4.0` pin (`pyproject.toml`) lets a newer formatter reformat
  them. Tracked as BACKLOG **W2**. Any agent running the full bar sees this red —
  it is NOT their change; do not reformat these files inside an unrelated PR.

## Security invariants

- 2026-07-15 (BOOT): CORS is localhost-only, configured in `ts_admin/main.py:170`
  (`allow_origins` list of `localhost`/`127.0.0.1` ports). Never `["*"]`.
- 2026-07-15 (BOOT): SSRF guard `validate_cluster_url()` at
  `ts_admin/services/cluster_service.py:11` — HTTPS-only, rejects
  localhost/private/loopback/link-local IPs. Asserted by `tests/unit/test_cluster_service.py`.
- 2026-07-15 (BOOT): Server binds `127.0.0.1` in `ts_admin/cli.py:64`. No web auth
  in v1 — fail-closed by local binding only.
- 2026-07-15 (BOOT): Credentials are stored in the OS keychain via `keyring` in
  `ts_admin/config.py`. No secrets in the repo or a committed `.env`.

## Data model

- 2026-07-15 (BOOT): Every SQLModel table carries a `cluster_id` FK (multi-cluster
  is v1). Tables in `ts_admin/models/`.
- 2026-07-15 (BOOT): SQLite init via `database.init_db()` uses `create_all`
  (additive-only). `alembic/` exists but is NOT wired into the runtime path.

## Test gates (the four guards)

- 2026-07-15 (BOOT): Dry-run safety — `tests/integration/test_dryrun_safety.py`,
  registry `DRYRUN_ENDPOINTS`. Every new destructive endpoint must append a row.
- 2026-07-15 (BOOT): Cluster isolation — `tests/integration/test_cluster_isolation.py`,
  registry `READ_ENDPOINTS`. Every new list/read endpoint taking `cluster_id` must
  append a row.
- 2026-07-15 (BOOT): SSRF — `tests/unit/test_cluster_service.py`. Audit log —
  `tests/integration/test_audit_log_writes.py` (one row per destructive execute).

## Architecture

- 2026-07-15 (BOOT): Layering — `ts_client/` thin HTTP → `services/` business logic
  → `api/` FastAPI routers → `models/` SQLModel. `ThoughtSpotClient` must stay a
  thin wrapper (no business logic).
- 2026-07-15 (BOOT): 11 routers in `ts_admin/api/`, 12 services in
  `ts_admin/services/`. Writes go live to ThoughtSpot; reads come from the local
  SQLite cache. Per-entity lazy sync — never force a full sync.
- 2026-07-15 (BOOT): Convention — never `except Exception`; name the specific
  exception class. ruff line-length 120, select `E,F,I,UP`, Python ≥3.10.

## Frontend testing (vitest)

- 2026-07-15 (S1): Config `frontend/vitest.config.mts` (`.mts` so app `tsc` does
  NOT typecheck it): jsdom env, `globals`, `resolve.alias { "@": <frontend root> }`
  mirroring tsconfig `"@/*": ["./*"]`, `include: **/*.test.{ts,tsx}`,
  `exclude` adds `tests/e2e/**` + `out/**`. Setup `frontend/vitest.setup.ts`
  registers `@testing-library/jest-dom/vitest`. Tests are co-located `*.test.tsx`.
- 2026-07-15 (S1): `frontend/tsconfig.json` include globs (`**/*.ts`, `**/*.tsx`;
  only `node_modules` excluded) mean `*.test.tsx` AND `vitest.setup.ts` ARE
  typechecked by `npx tsc --noEmit` and by `next build` (no
  `typescript.ignoreBuildErrors` in `next.config.js`). So a type error in a test
  file breaks the frontend typecheck/build gates even though vitest isn't in CI.
  `.mts` files escape the glob. jest-dom matchers (`.toBeInTheDocument()`) resolve
  program-wide via the setup import being part of the tsc program.
- 2026-07-15 (S1): NO ESLint config exists in `frontend/` (no `.eslintrc*` /
  `eslint.config*` / `eslintConfig` key) → `next build` skips lint and CI never
  runs `next lint`. Lint is not a frontend gate today.
- 2026-07-15 (S1): `@/lib/theme` barrel (`frontend/lib/theme/index.ts`) has NO
  import-time browser-API access — `window`/`matchMedia`/`localStorage` are only
  touched inside `ThemeProvider` functions/effects — so theme-consuming components
  render in jsdom without a `ThemeProvider` wrapper.
- 2026-07-15 (S1): `frontend/package-lock.json` is lockfileVersion 3; CI uses
  `npm ci` (`ci.yml:57`). Any `devDependencies` change requires regenerating the
  lockfile via `npm install` or `npm ci` fails on manifest/lock mismatch.
- 2026-07-15 (S1): `frontend/components/Relationships/nodeStyles.ts:24,34` exports
  `NODE_STYLES` + `NODE_STYLE_ORDER` (6 lineage node types: CONNECTION, DB_TABLE,
  LOGICAL_TABLE, MODEL, ANSWER, LIVEBOARD) — single source of truth for graph,
  legend, list. "Inaccessible" is literal JSX in `Legend.tsx:32`, not in NODE_STYLES.
- 2026-07-15 (S1): `frontend/tests/e2e/archiver-dry-run.spec.ts` is a Playwright
  spec (`.spec.ts`, imports `@playwright/test`); vitest must use `.test.` include +
  exclude `tests/e2e/**` so it never loads it. `ts_admin/static/` is gitignored, so
  `npm run build` produces no tracked git churn.
