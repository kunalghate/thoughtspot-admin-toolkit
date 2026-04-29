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
