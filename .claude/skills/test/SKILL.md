---
name: test
description: Runs the right test slice for the current branch AND flags missing coverage (untested service/router files, destructive endpoints not registered in the parametrized safety tests, new SQLModel tables missing cluster_id). Use before commit and before declaring a task done.
---

# /test — Test guard for ts-admin-toolkit

Goal: prevent shipping code without the matching tests. Runs in two stages — guard, then run — and reports a single block with results and concrete next actions.

## Stage A — Coverage guard (read-only)

1. Run `git diff --name-only HEAD` and `git status --short`. Combine into a list of changed files.
2. For each changed source file under `ts_admin/`, check whether its matching test was also changed (or exists at all):
   - `ts_admin/services/<name>.py` → `tests/unit/test_<name>.py` (note: some files use `_service` suffix in the test name, e.g. `test_metadata_service.py`; accept either)
   - `ts_admin/api/<name>.py` → `tests/integration/test_<name>_api.py`
   - `ts_admin/models/<name>.py` → if a new SQLModel `table=True` was added, confirm it has a `cluster_id` field with `foreign_key="clusters.id"`. CLAUDE.md rule: every table must have it.
3. For new POST/PUT/DELETE endpoints in `ts_admin/api/`:
   - If the endpoint mutates state (writes to TS or to local tables), grep `tests/integration/test_dryrun_safety.py` for the route. If absent → flag it and tell the user to add it to `DRYRUN_ENDPOINTS`.
4. For new GET/list endpoints in `ts_admin/api/`:
   - Grep `tests/integration/test_cluster_isolation.py` for the route. If absent → flag it and tell the user to add it to `READ_ENDPOINTS`.
5. For changed `.tsx` under `frontend/components/` or `frontend/pages/`:
   - Note that `frontend/__tests__/` does not exist yet (vitest is installed but unwired). Flag this once if any frontend code changed; don't repeat.
6. For changes to `ts_admin/services/cluster_service.py::validate_cluster_url`:
   - Confirm `tests/unit/test_cluster_service.py` was also touched. SSRF is a CLAUDE.md non-negotiable.
7. For changes to `ts_admin/services/deletion_service.py::_execute_delete`:
   - Confirm `tests/integration/test_audit_log_writes.py` was also touched (audit log shape).

## Stage B — Run the right slice

8. Map changed files to test files using the rules above. If anything destructive was touched (any `_execute_delete`, archiver `execute`/`restore`, or new POST/PUT/DELETE endpoint), always also include:
   - `tests/integration/test_dryrun_safety.py`
   - `tests/integration/test_cluster_isolation.py`
   - `tests/integration/test_audit_log_writes.py`
9. Run the selected pytest files via `pytest -x -v <files>`. Fail-fast keeps output short.
10. If any frontend `.tsx` changed, run `cd frontend && npm test -- --run` (vitest in run-once mode, when wired up). Skip with a one-line note if vitest isn't configured yet.
11. If any frontend page or critical component changed AND `@playwright/test` is installed (check `frontend/node_modules/@playwright/test`), run `cd frontend && npx playwright test`. Specs live at `frontend/tests/e2e/`. Skip with a note if Playwright isn't installed.
12. If no changed source files map to any test file, fall back to `make test`.

## Stage C — Report

Output one block with three sections:

```
✅ Tests run
   - tests/unit/test_cluster_service.py   26 passed
   - tests/integration/test_dryrun_safety.py   6 passed
   ...

⚠️  Coverage gaps detected (or "None — looks clean")
   - ts_admin/api/sharing.py changed but tests/integration/test_sharing_api.py not found.
   - New endpoint POST /api/v1/sharing/execute not registered in DRYRUN_ENDPOINTS
     (tests/integration/test_dryrun_safety.py:32). Add it before merging.

💡 Suggested next action
   - Create tests/integration/test_sharing_api.py mirroring test_deleter_api.py.
   - Append a pytest.param entry for the new endpoint to DRYRUN_ENDPOINTS.
```

## Hard rules

- Never silently skip a destructive endpoint. If you find a write endpoint that's not in the registry, report it loudly.
- If pytest fails, summarize the first failure with file:line and the assertion message, then stop. Don't try to fix unless asked.
- Do not run `make test` if a focused slice exists — running everything is slow and dilutes the signal of "what your change broke."
- Read-only guard. This skill never edits files. Always recommend the fix; let the user (or the next prompt) make the change.

## When NOT to use

- After purely cosmetic changes (rename, reformat, comment-only). Mention `git diff --stat` would be enough.
- During the planning phase. This is a pre-commit skill.
