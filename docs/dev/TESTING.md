# Testing — what to write, where it lives, when to run it

This is the recipe for keeping the test suite in sync with the code as new
features land. Follow it for every PR and the safety net stays intact.

## What we test (and why)

Four CLAUDE.md rules are non-negotiable. Each has a guard test that fails the
build if the rule is broken:

| Rule | Guard test | What it proves |
|---|---|---|
| Dry-run before every write (incl. UI) | [tests/integration/test_dryrun_safety.py](../../tests/integration/test_dryrun_safety.py) + [frontend/tests/e2e/archiver-dry-run.spec.ts](../../frontend/tests/e2e/archiver-dry-run.spec.ts) | Dry-run endpoints return 202, create a `*_dryrun` job, never mutate `ts_metadata`/`archive_records`/`audit_log`; archiver UI loads |
| Multi-cluster isolation via `cluster_id` | [tests/integration/test_cluster_isolation.py](../../tests/integration/test_cluster_isolation.py) | Read endpoints scoped to cluster A never return rows from cluster B |
| SSRF-validated TS URLs | [tests/unit/test_cluster_service.py](../../tests/unit/test_cluster_service.py) | localhost / private IPv4 / private IPv6 / IPv4-mapped IPv6 / non-HTTPS all rejected |
| Audit log written after every destructive action | [tests/integration/test_audit_log_writes.py](../../tests/integration/test_audit_log_writes.py) | Exactly one `audit_log` row per execute, with the right `action_type`, `entity_type`, `items_affected`, `parameters`, `status` |

Plus the existing per-feature unit and integration tests under `tests/unit/`
and `tests/integration/`.

## Recipe per change type

### New service (`ts_admin/services/foo.py`)

- Add `tests/unit/test_foo.py` (or `test_foo_service.py`) following
  [tests/unit/test_metadata_service.py](../../tests/unit/test_metadata_service.py) — uses the
  StaticPool in-memory engine + `monkeypatch get_engine` fixture.
- If it calls the ThoughtSpot API, mock the client following
  [tests/unit/test_deletion_service.py](../../tests/unit/test_deletion_service.py)'s `_FakeClient` pattern,
  or use `respx.mock` ([tests/unit/test_auth.py](../../tests/unit/test_auth.py)).
- A green bar on a delete-before-insert / incremental-watermark service means
  very little on its own — measure it. See
  [Mutation testing the lineage builder](#mutation-testing-the-lineage-builder)
  for the harness and the four test shapes that actually kill those mutations.

### New router (`ts_admin/api/foo.py`)

- Add `tests/integration/test_foo_api.py` following
  [tests/integration/test_deleter_api.py](../../tests/integration/test_deleter_api.py).
- If the router has a destructive endpoint (POST/PUT/DELETE that writes to TS
  or to local tables), **register it** in
  [tests/integration/test_dryrun_safety.py](../../tests/integration/test_dryrun_safety.py)
  by appending a `pytest.param(...)` entry to `DRYRUN_ENDPOINTS`. The
  parametrized safety check then covers it automatically.
- If the router has a list/read endpoint that takes `cluster_id` as a query
  param, register it in `READ_ENDPOINTS` in
  [tests/integration/test_cluster_isolation.py](../../tests/integration/test_cluster_isolation.py).

### New SQLModel table (`ts_admin/models/foo.py`)

- The table **must** have `cluster_id: str = Field(foreign_key="clusters.id")`.
  CLAUDE.md rule. If you can't justify having one, you're probably modeling
  something wrong.
- No dedicated test file for the model alone — coverage comes through the
  service/router that uses it.

### New page or component (`frontend/...`)

- Vitest is installed but not yet wired. When you wire it up, add component
  tests to `frontend/__tests__/`.
- For pages with a destructive action, add a Playwright spec under
  `frontend/tests/e2e/` modeled on
  [frontend/tests/e2e/archiver-dry-run.spec.ts](../../frontend/tests/e2e/archiver-dry-run.spec.ts).
  Use `data-testid` attributes on the elements you need to click.

## Mutation testing the lineage builder

`tests/unit/test_lineage_columns.py` was measured **mutation-vacuous** in the S7
cycle: six of seven mutations to `build_column_map` left every test green. S27
added [tests/unit/test_lineage_incremental.py](../../tests/unit/test_lineage_incremental.py)
and re-measured. Everything below is a run that was actually executed on
2026-08-15 against `improve/S27-lineage-mutation-coverage`; baseline for the
three-file lineage set (`test_lineage_columns.py`, `test_lineage_service.py`,
`test_lineage_incremental.py`) is **37 passed**.

### The harness (four rules, all learned the hard way)

1. **Mutate by LINE, never by string replace.** The `_changed` predicate
   `return not incremental or last_built is None or modified_at is None or modified_at > last_built`
   is **byte-identical** at `lineage_service.py:559` (`build_column_map`) and
   `:897` (`build_answer_index`). A replace-all hits both and you learn nothing
   about either.
2. **Prove the edit landed before you run pytest.** After every mutation:

   ```bash
   git diff --numstat -- ts_admin/services/lineage_service.py   # MUST print "1<TAB>1"
   ```

   A failed/duplicated replace silently produces a green "mutation survived"
   false result. Verified: the same edit applied as a string replace-all prints
   `2	2` — abort on anything that is not `1	1`.
3. **Restore with `git checkout -- <file>` and re-confirm green** before the next
   mutation.
4. **Do the experiments in a throwaway `git worktree`** (`git worktree add
   --detach <scratch-path> HEAD`), never in the working checkout — a mutation
   left behind reddens another agent's gate run (BACKLOG M8). Note the editable
   install points at the main checkout, so run the worktree copy explicitly:
   `PYTHONPATH=<worktree> python3 -m pytest ...`, and sanity-check
   `python3 -c "import ts_admin; print(ts_admin.__file__)"` resolves inside the
   worktree first. Remove the worktree when done.

### Kill table (measured 2026-08-15)

Every KILLED row below was observed red-then-restored-green. "Killed only by the
new file" means the other 36 tests stayed green under that mutation — i.e. the
pre-S27 suite was blind to it.

| ID | Line | Mutation | Result | Killed by |
|---|---|---|---|---|
| X1 | 532 | metadata read: `org_id == org_id` (drop org scoping) | KILLED (only by new file) | `test_build_column_map_is_scoped_to_one_cluster_and_org` |
| X2 | 531 | metadata read: drop `cluster_id` scoping | KILLED (only by new file) | same |
| X3 | 538 | `last_built` watermark: drop `org_id` scoping | KILLED (only by new file) | same |
| X4 | 537 | `last_built` watermark: drop `cluster_id` scoping | KILLED (only by new file) | same |
| X5 | 545 | `has_lb_edges` probe: drop `org_id` scoping | KILLED (only by new file) | `test_self_heal_probe_ignores_other_scopes` |
| X6 | 544 | `has_lb_edges` probe: drop `cluster_id` scoping | KILLED (only by new file) | same |
| X7 | 712 | lineage full-rebuild delete: drop `org_id` scoping | KILLED (only by new file) | `test_build_column_map_is_scoped_to_one_cluster_and_org` |
| X8 | 711 | lineage full-rebuild delete: drop `cluster_id` scoping | KILLED (only by new file) | same |
| X9 | 737 | orphan LB-edge purge: drop `org_id` scoping | KILLED (only by new file) | same |
| X10 | 746 | orphan LB-usage purge: drop `org_id` scoping | KILLED (only by new file) | same |
| X11 | 736 | orphan LB-edge purge: drop `cluster_id` scoping | KILLED (only by new file) | same |
| X12 | 745 | orphan LB-usage purge: drop `cluster_id` scoping | KILLED (only by new file) | same |
| I1 | 740 | orphan LB-edge purge `not_in` → `in_` | KILLED | `test_second_build_with_nothing_changed_is_idempotent` + pre-existing `test_column_map_purges_deleted_liveboards` |
| I2 | 748 | orphan LB-usage purge `not_in` → `in_` | KILLED | same pair |
| I3 | 720 | CONNECTS full-rebuild delete neutered (`relation == "CONNECTS_NEVER"`) | KILLED (only by new file) | idempotence + cross-scope tests |
| I4 | 711 | lineage full-rebuild delete neutered (`cluster_id == "no-such-cluster"`) | KILLED (only by new file) | idempotence + cross-scope tests |
| K1 | 747 | orphan usage purge loses `consumer_type == "LIVEBOARD"` (eats ANSWER usage) | KILLED (only by new file) | `test_column_build_preserves_answer_usage_and_object_edges` |
| K2 | 739 | orphan edge purge loses `source_type == "LIVEBOARD"` (eats object-tier edges) | KILLED (only by new file) | same |
| H1 | 559 | drop the `last_built is None` disjunct | KILLED (only by new file, **via `TypeError`**) | `test_null_watermark_alone_forces_a_full_liveboard_recrawl` |
| H2 | 559 | drop the `modified_at is None` disjunct | KILLED (only by new file, **via `TypeError`**) | `test_liveboard_with_null_modified_at_is_always_recrawled` |
| H3 | 569 | remove the empty-universe early return (`if False:`) | KILLED (only by new file) | `test_empty_metadata_universe_returns_early_and_touches_nothing` |
| H4 | 564 | remove the `has_lb_edges` self-heal (`if False:`) | KILLED | `test_self_heal_probe_ignores_other_scopes` + pre-existing `test_column_map_self_heals_missing_liveboard_edges` |
| H1b | **897** | H1 applied to the byte-identical twin in `build_answer_index` | **SURVIVED** | nothing — see gaps below |
| K3 | 766 | scoped usage delete loses `consumer_type == "LIVEBOARD"` | SURVIVED | not killable with realistic data — see gaps |
| K4 | 758 | scoped edge delete loses `source_type == "LIVEBOARD"` | SURVIVED | not killable with realistic data — see gaps |
| EQ1 | 752 | `if lb_guids:` → `if True:` | SURVIVED (expected) | equivalent mutant — see gaps |

Two fixture facts fell out of the measurement and are load-bearing; don't
"simplify" them away:

- **Two shadow scopes are required, not one.** A single `(c2, org 1)` shadow is
  excluded by *either* predicate alone, so X1 and X2 both survived against it.
  The fixture seeds `(c1, org 1)` *and* `(c2, org 0)`.
- **Each shadow needs a scope-unique liveboard GUID.** With only shared GUIDs,
  X9–X12 survived: the shadow's rows are inside `all_lb_guids` and therefore
  protected by the very `not_in` predicate under test.

### Known non-kills (recorded, deliberately not chased)

| ID | What | Why it is not killed |
|---|---|---|
| M10 | the `not incremental` disjunct at `:559` | Dead code — no production caller passes `incremental=False` (`api/relationships.py` → `run_deep_index` → `incremental=True`). Killing it would require a test-only entry point. |
| P08 / P26 | purge predicates that only differ on physically-impossible rows | Only killable by seeding rows the writers cannot produce. |
| K3 / K4 | `consumer_type`/`source_type` on the **scoped** (`in_(lb_guids)`) deletes | Same family as P08/P26: those deletes are already keyed on a liveboard GUID, and no ANSWER usage row or object-tier edge can carry a liveboard GUID in that position (`_edges_from_dependents` skips liveboard dependents by design). |
| EQ1 / P17b | `if lb_guids:` → `if True:` | **Equivalent mutant.** `in_([])` is always false, so the guarded deletes are no-ops when `lb_guids` is empty. Confirmed by measurement (survived, as expected). Do not try to kill it. |
| H1b | the `_changed` twin in `build_answer_index` (`:897`) | **Genuine gap, not equivalent.** `build_answer_index`'s incremental predicate has no pinning test at all. S27 scoped only `build_column_map`; file a backlog row before touching the answer index. |
| O1 | removing the delete-phase `commit()` at `:770` | Only observable via crash injection between the delete and the insert. Deferred — owned by BACKLOG S29. |

## How to run

```bash
make test               # everything
make test-unit          # fast: unit only
make test-integration   # in-memory SQLite + TestClient
cd frontend && npm test         # vitest (when wired)
cd frontend && npm run test:e2e # Playwright; one-time `npm run test:e2e:install` first
```

In Claude sessions: `/test` runs the right slice for `git diff` and flags any
missing coverage before commit.

## When you change a guard test

If you intentionally relax one of the four guard tests, leave a comment on
the changed line citing the reason and the issue/PR. Reviewers should treat a
weakened guard the same as a security review.

## A green test is not evidence until you know it can go red

Four ways a test in this repo has been vacuous in production. Check all four
before you count a test as coverage.

**1. It never asserts the predicate it guards.** A "spares X" test placed on a
code path that delete-alls and re-inserts X passes with its entire predicate
deleted. Falsify every guard test by deleting the thing it guards and watching
it fail. (This is backlog row M4.)

**2. Its fixture is a state no writer can produce.** A dashboard test asserted a
`record_count` trend by hand-inserting two `sync_log` rows. No writer in the
codebase can create that shape — `_write_sync_log` upserts a single row per
`(cluster_id, org_id, entity_type)` — so the field it "covered" was
structurally `0` in production for the life of the feature, behind a green
test. **Drive the real writer** and assert on what it leaves behind; do not
hand-seed its output.

**3. It runs in an environment that disarms it.** CI runs `TZ=UTC`. Under UTC a
naive-UTC timestamp parses to the same instant with or without a `Z`, so every
timezone assertion passes with the bug present. `frontend/vitest.config.mts`
pins `TZ=America/New_York` at config-module scope for exactly this reason — it
must be set before the worker pool forks. A test whose subject is a timestamp
must pin its own timezone.

**4. It patches a name the code no longer reads.** A module-level dispatch dict
binds function objects at import time, so `monkeypatch.setattr(module,
"_handler", ...)` swaps the module attribute while the dict still points at the
original — the test then silently exercises the real handler. Measured: hoisting
`run_sync`'s handler dict to a constant broke 4 tests this way. Dispatch tables
that tests patch must be built per call (`sync_service.sync_handlers()`).

## Timestamps

Backend datetimes reach the browser **naive-UTC**: the models set
`datetime.now(timezone.utc)`, SQLite drops the tzinfo on read, and FastAPI
serializes with no `Z` and no offset. ECMAScript parses a date-time with no
offset as **local**, so `new Date(iso)` is wrong by the viewer's UTC offset —
west of Greenwich that puts recent timestamps in the future. Always parse
through `parseUtc` in `frontend/lib/utils.ts`, and format through `formatDate` /
`formatRelative` / `formatAbsolute` / `formatDay`, which are built on it.

On the Python side, assert timestamps read back from SQLite against naive UTC
(`datetime.now(tz=timezone.utc).replace(tzinfo=None)`), never against
`datetime.now()` — the latter passes in the Americas and fails east of
Greenwich.
