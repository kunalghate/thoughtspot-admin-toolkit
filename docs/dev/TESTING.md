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
- If the endpoint returns 202 and dispatches work with
  `background_tasks.add_task`, read [Refusals on a 202 endpoint](#refusals-on-a-202-endpoint-s23--m10)
  below and append your `(module, function)` row to `BACKGROUND_DISPATCH_SITES`
  in [tests/unit/test_background_dispatch_sites.py](../../tests/unit/test_background_dispatch_sites.py).

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

1. **Mutate by LINE, never by string replace.** The `_changed` predicates in
   `build_column_map` (`lineage_service.py:709`) and `build_answer_index`
   (`:1252`) **share the disjunct substring**
   `last_built is None or modified_at is None or modified_at > last_built`. A
   replace-all hits both and you learn nothing about either.
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
| H1b | 897 (now `:1252`) | H1 applied to the twin in `build_answer_index` | SURVIVED (expected) | **equivalent mutant** — same as S30's B1; see the S30 kill table |
| K3 | 766 | scoped usage delete loses `consumer_type == "LIVEBOARD"` | SURVIVED | not killable with realistic data — see gaps |
| K4 | 758 | scoped edge delete loses `source_type == "LIVEBOARD"` | SURVIVED | not killable with realistic data — see gaps |
| EQ1 | 752 | `if lb_guids:` → `if True:` | SURVIVED (expected) | equivalent mutant — see gaps |

Line numbers in this table are as measured on 2026-08-15; `lineage_service.py`
has moved since (the `build_column_map` `_changed` predicate is now `:709`).
Locate a predicate by its text, not by these numbers, before re-running a row.

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
| M10 | the `not incremental` disjunct at `:709` (`build_column_map`; was `:559`) | Dead code — no production caller passes `incremental=False`. Killing it would require a test-only entry point. |
| A1 | the `not incremental` disjunct at `:1250` (`build_answer_index`) | Same dead code as M10: `api/relationships.py` → `run_deep_index` → `build_answer_index(incremental=True)` is the only caller. Recorded, not removed (S30 scope). |
| P08 / P26 | purge predicates that only differ on physically-impossible rows | Only killable by seeding rows the writers cannot produce. |
| K3 / K4 | `consumer_type`/`source_type` on the **scoped** (`in_(lb_guids)`) deletes | Same family as P08/P26: those deletes are already keyed on a liveboard GUID, and no ANSWER usage row or object-tier edge can carry a liveboard GUID in that position (`_edges_from_dependents` skips liveboard dependents by design). |
| EQ1 / P17b | `if lb_guids:` → `if True:` | **Equivalent mutant.** `in_([])` is always false, so the guarded deletes are no-ops when `lb_guids` is empty. Confirmed by measurement (survived, as expected). Do not try to kill it. |
| H1b | `last_built is None` in `build_answer_index`'s `_changed` (`:1252`, was `:897`) | **Equivalent mutant** (reclassified by S30, where it is B1). S27 recorded it as a genuine gap because the answer predicate then had no pinning test; S30 proved it unkillable — see the B1 proof under the S30 kill table. |
| O1 | removing the delete-phase `commit()` at `:770` | Only observable via crash injection between the delete and the insert. Deferred — owned by BACKLOG S29. |

### Answer-index kill table (S30)

Measured 2026-10-02 on `improve/S30-answer-index-mutation-coverage` against
`build_answer_index` (`lineage_service.py:1205`), full `tests/unit` +
`tests/integration` run per mutant, by-line edit with the `1	1` numstat check
(harness above). "Before" = the suite as of `main@536b2bc` (897 tests); "after" =
plus the four S30 tests in `tests/unit/test_answer_index.py` (901 tests). Unmutated
run green both times. Result: **13 → 24 killed of 31**; all 7 survivors are
recorded below with a reason.

| ID | Line | Mutation | Before | After | Killed by (S30 test, where one is the target) |
|---|---|---|---|---|---|
| Q2 | 1223 | answer universe: drop `cluster_id` | SURVIVED | KILLED | `test_deep_index_is_scoped_to_one_cluster_and_org` |
| Q1 | 1224 | answer universe: drop `org_id` | SURVIVED | KILLED | same |
| Q3 | 1225 | answer universe: drop `object_type == "ANSWER"` | KILLED | KILLED | five pre-existing tests + all four S30 tests |
| W2 | 1232 | watermark: drop `cluster_id` | SURVIVED | KILLED | `test_deep_index_is_scoped_to_one_cluster_and_org` |
| W1 | 1233 | watermark: drop `org_id` | SURVIVED | KILLED | same |
| W3 | 1234 | watermark: drop `consumer_type == "ANSWER"` | SURVIVED | KILLED | `test_column_map_stamps_do_not_move_the_answer_watermark` |
| W4 | 1235 | watermark: drop `synced_at IS NOT NULL` | SURVIVED | SURVIVED | **equivalent** — proof below |
| C2 | 1241 | certified set: drop `cluster_id` | SURVIVED | KILLED | `test_deep_index_is_scoped_to_one_cluster_and_org` |
| C1 | 1242 | certified set: drop `org_id` | SURVIVED | KILLED | same |
| C3 | 1243 | certified set: drop `consumer_type == "ANSWER"` | SURVIVED | SURVIVED | **impossible row** — proof below |
| C4 | 1244 | certified set: drop `synced_at IS NOT NULL` | SURVIVED | KILLED | `test_lazily_opened_late_answer_is_still_deep_crawled` |
| A1 | 1250 | drop `not incremental or` | SURVIVED | SURVIVED | dead code — see A1 under Known non-kills |
| A2 | 1250 | drop `or guid not in certified` | KILLED | KILLED | `test_deep_index_heals_a_watermark_poisoned_before_the_fix` (+2 S30) |
| A3 | 1250 | `guid not in certified` → `guid in certified` | KILLED | KILLED | heal + skip + recrawl tests (+4 S30) |
| B1 | 1252 | drop `last_built is None or` (was H1b) | SURVIVED | SURVIVED | **equivalent** — proof below |
| B2 | 1252 | drop `or modified_at is None` | SURVIVED | KILLED (`TypeError`) | `test_answer_with_null_modified_at_is_recrawled_not_crashed` |
| B3 | 1252 | drop `or modified_at > last_built` | KILLED | KILLED | `test_a_modified_answer_is_recrawled_after_a_full_pass` (+2 S30) |
| B4 | 1252 | `>` → `>=` | SURVIVED | SURVIVED | out of scope — only differs on an exact `modified_at == last_built` tie |
| B5 | 1252 | `>` → `<` | KILLED | KILLED | heal + skip + recrawl tests (+4 S30) |
| B6 | 1252 | whole return → `return True` | KILLED | KILLED | same |
| B7 | 1252 | whole return → `return False` | KILLED | KILLED | recrawl test (+3 S30) |
| B8 | 1251 | uncertified branch `return True` → `return False` | KILLED | KILLED | 4 pre-existing (+4 S30) |
| E1 | 1256 | empty-set early return → `if False:` | KILLED | KILLED | skip + no-answers-cached tests |
| E2 | 1256 | empty-set early return → `if True:` | KILLED | KILLED | 4 pre-existing (+4 S30) |
| D2 | 1306 | delete: drop `cluster_id` | SURVIVED | KILLED | `test_deep_index_is_scoped_to_one_cluster_and_org` |
| D1 | 1307 | delete: drop `org_id` | SURVIVED | KILLED | same |
| D3 | 1308 | delete: drop `consumer_type == "ANSWER"` | SURVIVED | SURVIVED | **impossible row** — proof below |
| D4 | 1309 | delete `in_(rebuilt_guids)` → `in_(answer_guids)` (failed-export preservation) | SURVIVED | SURVIVED | out of scope — ledgered separately |
| D5 | 1309 | delete GUID filter → `True` | KILLED | KILLED | heal test |
| S1 | 1314 | certification stamp `row.synced_at = now` → `None` | KILLED | KILLED | 3 pre-existing (+3 S30) |
| S2 | 1302 | `g not in failed_set` → `g in failed_set` | KILLED | KILLED | 2 pre-existing (+2 S30) |

Why each survivor is not chased:

- **B1 (`last_built is None`) is equivalent.** `_changed` only reaches the
  return when `guid in certified`. `certified` (`:1240-1244`) and `last_built`
  (`:1231-1235`) read the SAME rows under the SAME `WHERE` — so a GUID in
  `certified` proves at least one stamped row exists, and `MAX(synced_at)` over
  a non-empty set of non-NULL values is non-NULL. `last_built is None` is
  therefore always false where it is evaluated. (It becomes live only if C4 is
  also mutated, which C4's own test kills.)
- **W4 (watermark `synced_at IS NOT NULL`) is equivalent.** SQL `MAX` ignores
  NULLs, so dropping the filter cannot change the aggregate.
- **C3 / D3 (`consumer_type == "ANSWER"` in certified / delete) need an
  impossible row.** Both are keyed on an answer GUID (`guid in certified` for an
  answer in the universe; `consumer_guid in rebuilt_guids`). The only writer of
  LIVEBOARD usage (`_persist_column_map`, built at `:833`) sets `consumer_guid`
  to a GUID whose TML classified as a liveboard, so no LIVEBOARD usage row can
  carry an answer GUID. Same family as P08/P26 and K3/K4.
- **B4, D4, A1** — see the rows; B4 needs an exact timestamp tie, D4
  (failed-export preservation) is tracked outside S30, A1 is dead code.

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

## Refusals on a 202 endpoint (S23 / M10)

An endpoint that returns 202 and dispatches `background_tasks.add_task` has two
places a refusal can live, and only one of them can reach the caller.

**In the router, before `create_job`** — this is where a refusal that must reach
the caller belongs. The caller gets the real status code and no `Job` row exists.
Refusing *after* `create_job` still returns the right status (a router's response
has not started yet, and the background task never runs), but it leaves an
orphaned `QUEUED` `Job` row the UI polls forever.

**In the background target, a refusal that `raise`s is fail-SILENT.** The target
runs after the 202 is on the wire, so `error_handlers._STATUS_BY_TYPE` cannot
apply (Starlette: "Caught handled exception, but response already started") and
the `Job` row strands at `QUEUED` / `error=None` until the next restart. A
refusal that `mark_failed`s in the target is correct and visible — the FAILED
`Job` row is the operator's signal.

The rule is **both, not either.** Add the check to the router *in addition*; the
service-layer copy stays as defence in depth and must
`mark_failed(job_id, exc); return` — never `raise`
(`bulk_sharing_service.py:747-757`). **Do not "move" a refusal out of a
background target.** Four of them are only reachable there and are load-bearing
where they are — a transfer target who is a cluster admin
(`user_management_service.py:954`), `_delete_refusal` (`:1455`), 0 of N objects
resolved (`bulk_sharing_service.py:809`), tag not found
(`archiver_service.py:580`). Each correctly `mark_failed`s, and that FAILED
`Job` row is the only thing the operator ever sees; deleting it would also break
the S36 kill table below.

**A service-level unit test is not coverage for a refusal that must reach the
caller**: it calls the coroutine directly and so observes a `raise` production
can never surface — all 241 unit tests passed on the broken S23 guard (full
autopsy in the docstring of
[tests/integration/test_stale_cache_endpoints.py](../../tests/integration/test_stale_cache_endpoints.py)).
The covering test is a **TestClient** test asserting the status code **and** that
no `Job` row was created. Anchor it with the mirror-image case that *does* 202
and *does* create exactly one row, or the "no rows" assertion also passes on a
broken fixture (helper `_jobs` at `:137`, anti-vacuity anchor at `:198`). To
drive a 202 endpoint without doing the work, `monkeypatch` the target to a no-op
coroutine — Starlette's TestClient runs background tasks **inline**
(`test_dryrun_safety.py:178-196`).

[tests/unit/test_background_dispatch_sites.py](../../tests/unit/test_background_dispatch_sites.py)
locks the inventory of dispatch sites so a new one cannot land without this
being read. It is a tripwire, not a proof: it cannot tell whether a given
endpoint *needs* a refusal, and its ordering check passes trivially on the S23
shape.

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

## Mutation testing the delete-confirmation guard (S36)

`ArchiveRecord.deleted_confirmed_at` is the single source of truth for "this
object really left ThoughtSpot". Three separate call sites read it — startup
crash-recovery, the restore partition, and `is_restorable` — so a regression in
any one of them silently re-opens a data-loss or duplicate-object bug that the
admin only discovers in production. The suite was therefore measured, not
assumed, using the harness described above.

### Kill table (measured 2026-08-24)

Every row was observed red-then-restored-green, with the mutated file's content
asserted changed before pytest ran and asserted restored afterwards.

| ID | File | Mutation | Result | Killed by |
|---|---|---|---|---|
| S1 | `main.py` | recovery reads `tml_export_status == "SUCCESS"` instead of `deleted_confirmed_at` (**the original S36 bug**) | KILLED | `test_exported_but_unconfirmed_objects_keep_their_cache_rows` |
| S2 | `main.py` | cache purge drops the `cluster_id` predicate | KILLED | `test_purge_does_not_cross_cluster_boundaries` |
| S3 | `main.py` | cache purge drops the `org_id` predicate | KILLED | `test_purge_does_not_cross_org_boundaries` |
| S4 | `main.py` | `_is_delete_job` returns False for `bulk_delete` | KILLED | `test_exported_but_unconfirmed_objects_keep_their_cache_rows[bulk_delete]` |
| S5 | `main.py` | confirmed-record query drops `job_id` scoping | KILLED | `test_purge_is_scoped_to_the_stuck_job` |
| S6 | `main.py` | `_is_delete_job` returns True for every `archive` job | KILLED | `test_non_delete_archive_job_purges_nothing` |
| S7 | `deletion_service.py` | delete no longer stamps `deleted_confirmed_at` | KILLED | `test_confirmed_delete_stamps_the_record` |
| S8 | `deletion_service.py` | the stamp is scoped to the job, not the confirmed chunk | KILLED | `test_a_failed_delete_chunk_leaves_its_siblings_confirmation_alone` |
| S9 | `archiver_service.py` | restore no longer requires a confirmed delete | KILLED | `test_record_whose_delete_never_completed_is_skipped` |
| S10 | `database.py` | the one-shot legacy backfill is dropped from the ALTER | KILLED | `test_migration_backfills_legacy_rows_as_confirmed` |

10 of 10 killed; no surviving mutants.

### Two facts worth keeping

- **The stamp must be scoped to the chunk, not the job.** `_execute_delete`
  groups by object type, so one job issues several `delete_metadata` calls. S8
  is the mutation that catches a job-wide stamp confirming objects whose own
  delete call raised — and it is only killable with a fixture whose two objects
  have *different* object types, because same-type objects share one call.
- **The legacy backfill must run exactly once, inside the ALTER transaction.**
  Re-running it on every startup would re-confirm precisely the rows that
  crash-recovery deliberately left unconfirmed, resurrecting the duplicate-
  restore bug on the next boot. `test_migration_does_not_reconfirm_on_every_startup`
  pins that; S10 pins the other direction (dropping the backfill makes every
  pre-upgrade archive unrestorable).
