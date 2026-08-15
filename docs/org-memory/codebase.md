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
- 2026-08-14 (S6): **RESOLVED — the 2026-07-15 (S1) "KNOWN RED `ruff format
  --check`" bullet is deleted.** W2 shipped in PR #11 (ruff pinned
  `>=0.15.0,<0.16.0`, the 5 PR-#10 files reformatted). Verified on `main` @
  `ef0172a` under ruff 0.15.10: "90 files already formatted", exit 0. Two agents
  in this cycle still acted on the stale bullet — if the format gate is red for
  you now, it IS your change.

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
- 2026-08-14 (S3/S4/M2): **`col(X).not_in(<empty collection>)` deletes every row.**
  Verified on the installed SQLAlchemy 2.0.x — it compiles to
  `X NOT IN (NULL) OR (1 = 1)`, i.e. unconditionally true. Any purge built from a
  "seen this sweep" collection needs an explicit empty guard, because an empty
  upstream response is indistinguishable from "everything was deleted upstream."
  Guarded in `sync_service._sync_groups` (an empty `groups/search` page would
  otherwise wipe the org's whole group + membership cache while reporting
  SUCCESS). Deliberately NOT guarded in `lineage_service._persist_column_map`,
  where an empty `all_lb_guids` genuinely means "no liveboards exist, so every
  cached liveboard edge is orphaned" — `build_column_map` early-returns before
  that line when metadata is simply unsynced. The distinction is which empty set
  is ambiguous, not the construct itself.
- 2026-08-14 (S3): `ts_admin/database.py::_REBUILDABLE_SENTINELS` maps
  `{table: (sentinel column, sync_log entity_type)}` and is the drop-and-rebuild
  mechanism for cache tables (`create_all` is additive-only and never ALTERs).
  **Any new column on a rebuildable cache table must bump its sentinel**, or
  `create_all` silently skips it and queries fail with "no such column". The drop
  now also deletes the paired `sync_log` rows (table is `sync_log`, singular) —
  without that the UI reported a recent successful sync over an empty table.
- 2026-08-14 (S3): `UserGroupMembership` had **no writer at all** before this
  change, so `user_management_service._is_admin()` silently returned `False` for
  everyone and the admin-target guards in `preview_transfer_sharing` /
  `execute_transfer_sharing` were dead code. Now that `_sync_groups` populates it,
  those guards fire for the first time — a transfer targeting an admin that used
  to succeed now returns 422. Behaviour flips on the first *groups sync*, not on
  an explicit action. `_is_admin` still joins on `cluster_id` only (no `org_id`).
- 2026-08-14 (S3): `ThoughtSpotClient.principal_permissions` and
  `fetch_permissions` have **no wire-shape tests** — every caller is stubbed.
  Both parsers degrade to `[]` on a key mismatch rather than raising, and
  `execute_transfer_sharing` treats an empty list as SUCCESS with an audit-log
  row. The v2 shape is `principal_permission_details[].metadata_permission_info[]
  .metadata_permissions[].permission`, and the endpoint is `principals/`
  (plural) — the pre-2026-08 code had all three wrong and returned `[]` silently.
- 2026-08-14 (S3): Lineage delete scoping is split across two phases:
  `build_object_graph` owns `USES` where `source_type != "LIVEBOARD"`;
  `_persist_column_map` owns `CONNECTS` + `LIVEBOARD`-sourced `USES`. There is no
  purge keyed on **target** GUID, so an edge whose target is deleted while its
  source liveboard is unchanged outlives it (ghost node in the graph) — filed as S6.

## Lineage cache — why a target-keyed purge is unsound (S6, rejected)

- 2026-08-14 (S6): **Do not add a target-keyed delete to the lineage cache.** A
  full implementation passed the entire verification bar and was still rejected
  at review; it lives at `improve/S6-lineage-orphan-purge` @ `b8a86de` as a
  worked example of what not to ship. The rule "target absent from
  `CachedMetadata` ⇒ deleted in TS" is unsound for four independent reasons below.
- 2026-08-14 (S6): **The builder deliberately writes edges to targets absent from
  the metadata cache.** `lineage_service.py:656` —
  `meta_by_guid.get(model_guid, ("", ""))` emits the edge with `target_name=""`.
  So a purge on that predicate deletes rows the same code path creates: a model
  created in TS after the last metadata sync makes two bit-identical
  `build_column_map` runs produce different databases. Any purge here must not
  contradict `:656`.
- 2026-08-14 (S6): **Deleting a lineage row is effectively permanent.**
  `build_column_map` only re-exports *changed* liveboards, and the only self-heal
  (`has_lb_edges`, `:540-547`) fires solely when **every** liveboard edge is gone —
  one surviving edge suppresses it forever. `build_answer_index` has the same
  shape: its watermark is `max(synced_at)` over the *surviving* ANSWER rows, so a
  **partial** deletion is permanent while a total one self-heals. Partial loss is
  worse than total loss in both writers. There is no non-incremental entry point
  (`api/relationships.py:190` → `run_deep_index` → `incremental=True`).
- 2026-08-14 (S6): **`_sync_metadata` is delete-all-then-repage-in-spec-order**
  (`sync_service.py:308-345`): it commits a DELETE-all, then re-inserts page by
  page committing per page, in `search_metadata`'s spec order — LIVEBOARD, ANSWER,
  *then* the five logical-table subtypes (`ts_client/client.py:337-345`). An
  interrupted sync therefore leaves a **non-empty but truncated** cache in the
  worst possible shape: liveboards + answers present, every model and table
  missing. **A "cache is non-empty" check is NOT a freshness or completeness
  guard.** Filed as S23. (`_write_sync_log` does flip the single
  `(cluster, org, entity_type)` row to `FAILED` on a caught exception — that row,
  not the row count, is the completeness signal.)
- 2026-08-14 (S6): `ts_dependencies` **deliberately supports edge endpoints with
  no `ts_metadata` row** — the model docstring (`models/cache/ts_dependency.py:6-9`)
  calls them "connections and inaccessible stubs" carrying a denormalized name.
  Deleting them contradicts the table's design.
- 2026-08-14 (S6): The `accessible` flag is **plumbed end-to-end but never set
  `False`** — `_node_from_edge_endpoint(..., accessible: bool = True)`
  (`lineage_service.py:1169`) is the only producer and no call site passes it;
  the frontend already renders it (dashed border + 0.6 opacity,
  `LineageFlow.tsx:56-57`) with a tested "Inaccessible" Legend key. **Nameless
  ghost nodes are a read-path gap, not a cache-integrity problem** — filed as S22.
- 2026-08-14 (S6): `CachedMetadata` never contains CONNECTION objects —
  `search_metadata` syncs exactly 7 specs (`ts_client/client.py:337-345`) while
  `CONNECTS` edges carry `target_type="CONNECTION"` and often a synthetic
  `conn::<name>` target (`lineage_service.py:611-614`). Any metadata-existence
  purge would eat every CONNECTS edge.
- 2026-08-14 (S6): `api/sync.py:85-138` creates sync jobs **unconditionally — no
  concurrency guard**, so a metadata sync and a dependencies build interleave with
  the cache mid-repopulation. Filed as S24.
- 2026-08-14 (S6, performance): **`CachedMetadata` has NO composite index** — only
  single-column `cluster_id`, `org_id`, `ts_guid` (`models/cache/ts_metadata.py:22-25`).
  A correlated subquery or join keyed on `(cluster_id, org_id, ts_guid)` therefore
  binds `cluster_id=?` ONLY and post-filters the rest: `EXPLAIN QUERY PLAN` shows
  `SEARCH ts_metadata USING INDEX ix_ts_metadata_cluster_id (cluster_id=?)`. In a
  single-cluster install that selects every row — O(outer × metadata). Measured on
  a 32k-row scratch DB: **50,589 ms vs 72.6 ms** with a `(cluster_id, org_id,
  ts_guid)` composite (≈697×), plan flipping to a covering index. The app never
  runs `ANALYZE` and sets no pragmas (`database.py:30-34`), so SQLite's no-stats
  heuristic picks the least selective index. **Add the composite index before any
  new `NOT EXISTS`/join against `ts_metadata`** — filed as S25 (blocks S22).
- 2026-08-14 (S6, performance): **`metadata.create_all` does NOT add a new index to
  an already-existing table** (verified: a second `create_all` with an added
  `Index` produced zero new indexes). This is DISTINCT from the known "new column
  needs a `_REBUILDABLE_SENTINELS` bump" gotcha. Adding an index to a
  non-rebuildable cache model requires explicit `CREATE INDEX IF NOT EXISTS` DDL
  in `init_db()`, or existing user DBs silently keep the old plan.
- 2026-08-14 (S6, performance): **Sync background tasks run ON the event loop, not
  a threadpool.** `api/sync.py:113` is `background_tasks.add_task(run_sync, ...)`
  with an `async def` target, which Starlette awaits inline; `_persist_column_map`
  (`lineage_service.py:701`) is a plain `def` called synchronously. Nothing uses
  `to_thread`/`run_in_executor`. Any slow blocking DB work in a sync freezes ALL
  HTTP handling — including the job-status polling the UI uses to render sync
  progress, and `/health`. The engine also sets no `journal_mode` (rollback
  journal, not WAL), so a long write transaction holds the write lock. Filed as S26.
- 2026-08-14 (S6, process): The unit suite is **structurally incapable of catching
  a quadratic query plan** — the lineage tests run a handful of rows against an
  in-memory DB and pass in milliseconds. A green bar says nothing about plan
  quality; use `EXPLAIN QUERY PLAN` on a realistically-sized scratch DB when
  adding any correlated subquery or join.
- 2026-08-14 (S6): A `NOT IN`-style purge **cannot be chunked** with `_chunks`
  (`lineage_service.py:129-131`) — chunk A deletes every row whose key lives in
  chunk B. Correlated `NOT EXISTS` is the only correct formulation, and it does
  compile correctly correlated on SQLite. (`IN`-style purges chunk fine; the
  asymmetry is the point.) Pairs with the `not_in(<empty>)` hazard above.
- 2026-08-14 (S6): Lineage graph nodes are keyed **`guid`**, not `id` —
  `_node_from_edge_endpoint` returns `{guid, name, node_type, layer, owner_name,
  accessible}`. In `tests/unit/test_lineage_columns.py`, `_seed(..., lb_modified=now)`
  does NOT make a liveboard "changed" on a second build (`_changed` compares
  against the first build's `synced_at`, which is later) — use a **future**
  timestamp to force re-export.
