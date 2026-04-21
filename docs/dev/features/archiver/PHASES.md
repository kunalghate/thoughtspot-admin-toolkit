# Content Archiver — Phased Implementation

Each phase ends with a vertical slice that is fully testable end-to-end.
No phase leaves the app in a broken state.

---

## Phase 1 — Foundation

**Goal:** All infrastructure is in place. Nothing the admin can use yet, but
all subsequent phases build on this without revisiting it.

### What to build

**Models & DB**
- `ts_admin/models/archive_record.py` — `ArchiveRecord` SQLModel table
- Add `is_cancelled: bool = False` to `Job` model (`ts_admin/models/job.py`)
  ⚠ Not yet in the model — this is a real Phase 1 task. Phase 5's cancel-aware delete loop depends on it.
- Import `archive_record` in `ts_admin/database.py` `init_db()`
- Import `archive_record` in `alembic/env.py`
- Generate + apply Alembic migrations (covers both `archive_records` table + `jobs.is_cancelled`)

**ts_client extensions** (`ts_admin/ts_client/client.py`)
- `tml_export(object_ids)` → POST `/api/rest/2.0/metadata/tml/export`
  Success check: `"edoc" in result and result["edoc"]` (not `status_code`)
- `create_tag(name, color)` → POST `/api/rest/2.0/tags`
- `fetch_dependents(objects)` → POST `/api/rest/2.0/dependency/listdependents`
  ⚠ Verify actual response shape against real cluster before writing parser.
  Wrap in `try/except TSResponseParseError` as a defensive fallback.
- `import_tml(tml_strings, import_policy="PARTIAL")` → POST `/api/rest/2.0/metadata/tml/import`

**Service helpers** (`ts_admin/services/archiver_service.py`)
- `_chunks(lst, size)` — generic chunker
- `_fetch_objects_by_guids(session, guids, cluster_id, org_id)` — chunked at 500
  to avoid SQLite's 999-parameter `IN (...)` limit
- `_add_tag(tag_names_json, tag)` — `json.loads` → append → `json.dumps`
- `_remove_tag(tag_names_json, tag)` — `json.loads` → filter → `json.dumps`
- `_has_tag(tag_names_json, tag)` — `json.loads` → `in` check

**Startup hardening** (`ts_admin/main.py`)
- `recover_stuck_jobs()` — marks any `RUNNING` jobs as `FAILED` on startup;
  for `job_type="archive"` + `action="delete"` jobs, removes `CachedMetadata`
  rows for GUIDs with `tml_export_status="SUCCESS"` (conservative: assume deleted)
- `_cleanup_old_tml_exports()` — removes TML directories older than 90 days
  where all `ArchiveRecord` rows for that `job_id` have `restored_at IS NOT NULL`

### Ready to use & test
- `archive_records` table exists in SQLite (verify with DB browser or `sqlite3`)
- `jobs` table has `is_cancelled` column
- App starts cleanly with startup event logged
- `ts_client` methods callable via unit tests with mocked HTTP

---

## Phase 2 — Identify Stale Content (Read-Only Backend)

**Goal:** Admin can query stale objects via the API. No writes to ThoughtSpot yet.
Fully usable via `/api/docs` for validation.

### What to build

**Service** (`ts_admin/services/archiver_service.py`)
- `preview(cluster_id, org_id, stale_activity_days, stale_modified_days, types,
          exclude_tags, owner_guid, exclude_owner_guids)`
  → `{ total, by_type, criteria_summary }`
  SQLite `COUNT(*)` with AND stale filter + `exclude_tags` (via `_has_tag`) +
  `exclude_owner_guids` (via `notin_`). Uses `_chunks` for any IN queries.
- `search(same params + record_offset, page_size)`
  → `(list[dict], int)` — paginated rows with `days_unused` computed
- `list_tags(cluster_id, org_id)` → reads `CachedTag` rows

**API** (`ts_admin/api/archiver.py` — create file + register in `main.py`)
- `GET /api/v1/archiver/preview` → `ArchiverPreviewResponse`
- `GET /api/v1/archiver/results` → `ArchiverResultsResponse`
- `GET /api/v1/archiver/tags` → `list[ArchiverTagItem]`

**Pydantic models** (in `archiver.py`):
`ArchiverPreviewResponse`, `ArchiverResultItem`, `ArchiverResultsResponse`, `ArchiverTagItem`

### Ready to use & test
- `GET /api/v1/archiver/preview?cluster_id=X&stale_activity_days=90&stale_modified_days=90`
  returns `{ total, by_type, criteria_summary }`
- `GET /api/v1/archiver/results` returns paginated stale object list with `days_unused`
- Filters work: content types, exclude_tags, both stale thresholds
- `GET /api/v1/archiver/tags` returns available tags for autocomplete

---

## Phase 3 — Tag & Untag (First Safe Writes)

**Goal:** Admin can tag objects as INACTIVE and untag them via the API.
These are reversible — a good checkpoint before implementing delete.

### What to build

**Service** (`ts_admin/services/archiver_service.py`)
- `execute(job_id, cluster_id, org_id, object_ids, action, tag_name, create_tag_if_missing)`
  — tag and untag flows only (delete added in Phase 5)

  `action="tag"`:
  1. `mark_running(job_id, total)`
  2. `client.search_tags()` → `client.create_tag()` if missing
  3. `client.assign_tag(chunk, tag.id)` in chunks of 50
  4. Update `CachedMetadata.tag_names` via `_add_tag()`
  5. Write `AuditLog` entry + `logger.info` structured log line
  6. `mark_complete()`

  `action="untag"`: same but `client.unassign_tag()` + `_remove_tag()`

**Jobs API** (`ts_admin/api/jobs.py`)
- `DELETE /api/v1/jobs/{job_id}/cancel`
  Sets `job.is_cancelled = True`. Returns 409 if job already done.

**API** (`ts_admin/api/archiver.py`)
- `POST /api/v1/archiver/execute`
  Creates Job → `BackgroundTasks.add_task(ArchiverService.execute, ...)` → returns `{ job_id, action, total }`

**New Pydantic models**: `ExecuteRequest`, `ExecuteResponse`

### Ready to use & test
- POST `/execute` with `action="tag"`, real GUIDs, `tag_name="TOOLKIT_TEST"` →
  poll `GET /api/v1/jobs/{job_id}` → COMPLETE → tag appears in ThoughtSpot UI
- POST `/execute` with `action="untag"` → tag removed from TS UI
- `DELETE /jobs/{job_id}/cancel` on running tag job → status becomes FAILED
- `AuditLog` row written (verify in SQLite), structured log line in server output

---

## Phase 4 — Dry-Run Impact Check (Background Job)

**Goal:** Full pre-delete impact assessment for every selected object —
permissions + dependencies — with no HTTP timeout risk. Admin can see exactly
what will happen before any deletion.

### What to build

**Service** (`ts_admin/services/archiver_service.py`)
- `dryrun(job_id, cluster_id, org_id, object_ids)` — background task body

  1. `mark_running(job_id, total=len(object_ids))`
  2. `_fetch_objects_by_guids()` — single chunked SQLite lookup
     ⚠ Call this **before** firing async permission checks. `_sync_metadata` does a full DELETE +
     re-insert (not upsert), so a concurrent metadata sync could wipe `CachedMetadata` rows mid-job.
     Fetching up front makes the dryrun resilient to that race.
  3. Permission check: `asyncio.gather(*[check_one(g, t) for g,t in pairs],
                                       return_exceptions=True)` with `Semaphore(10)`
     — exceptions collected into `errors[]`
  4. Dependency check: single `client.fetch_dependents()` batch call
  5. Aggregate: `by_type`, `shared_count`, `affected_principals`,
     `dependency_warnings`, `errors`
  6. `mark_complete({ total, by_type, shared_count, affected_principals,
                      dependency_warnings, errors })`
     — `all_objects` NOT stored here

- `dryrun_objects(job_id, cluster_id, record_offset, page_size)`
  Reads `object_ids` from `Job.parameters` → paginated SQLite lookup →
  ordered by `last_accessed_at ASC NULLS FIRST` (proxy for staleness — NULL = never accessed)
  ⚠ `days_unused` is computed in Python from `last_accessed_at`, not a DB column — cannot sort by it in SQL.
  Use `nulls_last(asc(CachedMetadata.last_accessed_at))` (same pattern as `MetadataService.search()`).

**API** (`ts_admin/api/archiver.py`)
- `POST /api/v1/archiver/dryrun`
  Creates Job (`job_type="archive_dryrun"`), stores `object_ids` in `Job.parameters`,
  fires BackgroundTask, returns `{ job_id }`
- `GET /api/v1/archiver/dryrun/{job_id}/objects`
  Paginated object list for the modal grid

**New Pydantic models**: `DryRunRequest`, `DryRunSummary`, `AffectedPrincipal`, `DependencyWarning`

### Ready to use & test
- POST `/dryrun` with 20+ real GUIDs → poll → COMPLETE in ~5s (no timeout)
- `Job.result` contains `shared_count`, `dependency_warnings`, `errors[]`
- `GET /dryrun/{job_id}/objects` returns paginated list sorted by staleness
- Shared objects → appear in `affected_principals`
- Already-deleted objects → appear in `errors[]`, not as failures
- 300+ object selection completes without HTTP timeout

---

## Phase 5 — Delete with Mandatory Safety Net

**Goal:** Admin can permanently delete stale objects via the API. Every object
gets a TML backup first. Objects whose export fails are skipped — never deleted.
Jobs can be cancelled mid-run. Full audit trail written.

### What to build

**Service** (`ts_admin/services/archiver_service.py`)
- `execute()` — add `action="delete"` flow:

  Phase A — TML Export (mandatory):
  - `mark_running(job_id, total)`
  - Batch INSERT all `ArchiveRecord` rows (`tml_export_status="PENDING"`)
  - For each chunk of 50: `client.tml_export()` → check `"edoc" in result`
    - Success → write `~/.ts-admin/tml-exports/{job_id}/{guid}.tml`
      → update `ArchiveRecord`: `tml_export_status="SUCCESS"`, `tml_path`
      → add to `delete_batch`
    - Failure → update `ArchiveRecord`: `tml_export_status="FAILED"`, `tml_export_error`
      → do NOT add to `delete_batch`

  Phase B — Delete (only exported objects, cancel-aware):
  - Group `delete_batch` by `object_type`
  - Convert string → `MetadataType` enum per group
  - For each chunk of 50:
    - Check `job.is_cancelled` → exit cleanly if set
    - `client.delete_metadata(chunk, enum_type)`
    - DELETE from `CachedMetadata` WHERE `ts_guid IN chunk`
    - `update_progress()`

  Phase C — Audit:
  - Write `AuditLog` (full succeeded/failed lists in `parameters`)
  - `logger.info` structured log line
  - `mark_complete({ succeeded, failed_tml_export, failed_delete, tml_export_path })`

### Ready to use & test
- POST `/execute` with `action="delete"`, 5 test GUIDs →
  TML files written to `~/.ts-admin/tml-exports/{job_id}/` →
  objects removed from ThoughtSpot UI →
  `ArchiveRecord` rows with `tml_export_status="SUCCESS"` →
  `CachedMetadata` rows deleted
- Object with failed TML export → NOT deleted, `tml_export_status="FAILED"`
- Cancel during delete → job stops cleanly at next chunk boundary
- `AuditLog` entry written with full object lists

---

## Phase 6 — Restore & History

**Goal:** Admin can browse every past archive session and restore any object
from its TML backup. Restored objects get a new GUID — stored as `restored_as_guid`.

### What to build

**Service** (`ts_admin/services/archiver_service.py`)
- `restore(job_id, cluster_id, org_id, archive_record_ids)` — background task body
  - Batches of 10 records per `client.import_tml()` call
  - Skips records with missing TML or `tml_export_status != "SUCCESS"`
  - Maps result by index → updates `ArchiveRecord`:
    `restored_at`, `restored_as_guid` (new GUID from TS), `restored_by_job_id`
  - Re-inserts into `CachedMetadata` using the NEW guid
  - Write `AuditLog` entry + `logger.info`

- `history(cluster_id, org_id, record_offset, page_size)`
  — SELECT summary columns only, exclude `Job.parameters`

- `history_session(job_id, cluster_id, record_offset, page_size)`
  — paginated `ArchiveRecord` rows; `is_restorable` computed in API layer

**API** (`ts_admin/api/archiver.py`)
- `GET /api/v1/archiver/history` → `ArchiveHistoryResponse`
- `GET /api/v1/archiver/history/{job_id}` → `ArchiveSessionResponse`
- `POST /api/v1/archiver/restore` → creates Job, fires BackgroundTask

**New Pydantic models**: `RestoreRequest`, `ArchiveRecordResponse`, `ArchiveSessionSummary`,
`ArchiveHistoryResponse`, `ArchiveSessionResponse`

### Ready to use & test
- `GET /history` lists archive sessions with succeeded/failed counts
- `GET /history/{job_id}` shows `ArchiveRecord` rows with `is_restorable` flag
- POST `/restore` with 1–2 `archive_record_ids` → poll → COMPLETE →
  object reappears in ThoughtSpot with new GUID →
  `ArchiveRecord.restored_as_guid` populated →
  `CachedMetadata` row inserted with new GUID
- Record with missing TML → `is_restorable=false`, skipped gracefully

---

## Phase 7 — Frontend: Browse & Identify

**Goal:** Admin can open `/archiver`, adjust criteria, see the live count update,
browse stale objects in a grid, and export CSV. No write actions yet.

### What to build

**Types & API client**
- `frontend/lib/types.ts` — all archiver types (including `errors[]` in `DryRunSummary`)
- `frontend/lib/api.ts` — `archiverApi` object (all 9 methods)

**Page** (`frontend/pages/archiver.tsx`)
- Two-tab layout: **Archive** tab + **History** tab (History stubbed)
- `AppShell` wrapper, Pages router

  Archive tab — `CriteriaPanel` (280px):
  - staleActivityDays, staleModifiedDays inputs (default 90)
  - Content type checkboxes (LIVEBOARD, ANSWER)
  - Exclude tags, owner filter, exclude owners inputs
  - `LiveCountBadge` ("342 stale objects match")
  - Search / Reset buttons

  Archive tab — `ResultsPanel` (flex-1):
  - Header: "{total} stale objects" + "Export CSV" button
  - `AgGridReact` (infinite row model, `IDatasource` → `archiverApi.results()`)
  - Columns: Name, Type, Owner, Last accessed, Days unused, Tags

  Shared behaviour:
  - 300ms debounce → `debouncedCriteria` → preview fetch + datasource reset
  - Page-mount check: poll for RUNNING archive job → restore `activeJobId`

### Ready to use & test
- Navigate to `http://localhost:3000/archiver` — page loads
- Adjust stale thresholds → live count badge updates within 300ms
- Results grid paginates, sorted by `days_unused` descending
- "Export CSV" downloads `stale-content.csv`
- Switch cluster → grid resets and re-queries
- Refresh page mid-delete → polling for running job resumes automatically

---

## Phase 8 — Frontend: Tag, Untag & Dry-Run

**Goal:** Admin can select objects, tag/untag them, and run the full dry-run
impact check before committing to deletion. No actual deletion yet.

### What to build

**Results grid additions**
- Checkbox column + external `selectedGuids: Set<string>` state
- `SelectionBar` (when `selectedGuids.size > 0`):
  `"{N} selected" | "Tag as INACTIVE" | "Delete selected" | "Clear"`
- Actions column: per-row "Untag" button (calls `archiverApi.execute` directly)

**`DryRunModal`** (state machine: `idle → polling → ready`)
- `polling` (POST `/dryrun` → `dryRunJobId`):
  Spinner — "Checking {N} objects for permissions and dependencies..."
  Polls `GET /api/v1/jobs/{dryRunJobId}` every 2s
- `ready` (job COMPLETE, summary in `Job.result`):
  - Impact cards: Total / Liveboards / Answers
  - Dependency warning box (if `dependency_warnings.length > 0`)
  - Shared access warning (if `shared_count > 0`)
  - Errors section: "⚠ {N} objects could not be checked" (if `errors.length > 0`)
  - Object list grid: lazy-loaded from `GET /dryrun/{jobId}/objects`
  - Confirmation input: "Type DELETE to confirm"
  - Footer: Cancel | "Delete {N} objects" (red, disabled until input === `"DELETE"`)

**Tag/Untag job flow**
- Tag/Untag buttons → `archiverApi.execute()` → poll → color-coded toast

### Ready to use & test
- Select 10 objects → "Tag as INACTIVE" → poll → COMPLETE → tag in TS UI
- Per-row Untag → tag removed from single object
- "Delete selected" → modal polling state (spinner)
- After ~5s → modal ready: shared count, dependency warnings, errors section visible
- Object list grid paginates through all selected objects
- Typing "DELETE" enables confirm; anything else keeps it disabled

---

## Phase 9 — Frontend: Execute, Cancel & Polish

**Goal:** Admin can complete the full delete flow from the UI. Progress is
visible, the job can be cancelled mid-run, and the toast clearly shows the outcome.

### What to build

**`DryRunModal` additions** (state machine: `ready → running → complete`)
- `running` (confirm → POST `/execute` → `activeJobId`):
  Progress bar: "{done}/{total} objects processed"
  Cancel button → `DELETE /api/v1/jobs/{activeJobId}/cancel`
- `complete`:
  Color-coded summary (counts-based):
  - Green: all succeeded
  - Warning: partial (some TML exports/deletes failed)
  - Red: 0 succeeded or job FAILED
  "View in History →" link — switches to History tab

**Toast helper** (`buildToast(job)`)
- Green: "{N} objects archived successfully."
- Warning: "{N} deleted · {M} skipped. View History for details."
- Red: "Archive completed but 0 objects deleted — {M} TML exports failed."

### Ready to use & test
- Full flow: criteria → select → dry-run → type DELETE → confirm
  → progress bar counts up → color-coded toast
- Cancel mid-delete → job stops at next chunk → partial toast
- Refresh mid-delete → polling resumes, progress bar catches up
- Objects confirmed removed from ThoughtSpot UI

---

## Phase 10 — Frontend: History & Restore

**Goal:** Admin can browse all past archive sessions and restore any object
with a valid TML backup. Completes the full feature lifecycle.

### What to build

**History tab**
- `ArchiveHistoryGrid`:
  Columns: Date, Total, Succeeded, Failed, Status, "View" button
  Calls `archiverApi.history()`, paginated

- `ArchiveSessionDrawer` (slide-over on "View"):
  `AgGridReact` of `ArchiveRecord` rows
  Columns: Name, Type, Owner, Archived at, Days unused, TML export, Restored?, Actions
  Actions: "Restore" button (disabled if `!is_restorable`, tooltip explains why)
  Footer: "Restore selected" bulk button

**Restore flow**
- Select rows → "Restore selected" → `archiverApi.restore()` → poll → toast
- On COMPLETE: grid refreshes — `restored_at` + `restored_as_guid` visible

### Ready to use & test
- History tab lists all past sessions with counts
- "View" → drawer shows individual deleted objects with TML status
- Records with `tml_export_status="FAILED"` → Restore button disabled with tooltip
- Select 2 restorable objects → "Restore selected" → poll → COMPLETE →
  objects reappear in TS with new GUIDs →
  `restored_as_guid` visible in drawer
- Already restored objects → Restore button disabled ("Already restored")

---

## Feature Readiness Summary

| Phase | Scope | Features ready to use |
|---|---|---|
| 1 | Backend — infrastructure | Tables exist, crash recovery runs, ts_client methods available |
| 2 | Backend — read | Query stale objects via `/api/docs`, filter by criteria |
| 3 | Backend — tag/untag | Tag/untag objects, cancel jobs via `/api/docs` |
| 4 | Backend — dry-run | Full impact check (permissions + deps) via `/api/docs`, no timeout |
| 5 | Backend — delete | Safe delete with TML backup, cancel, audit trail via `/api/docs` |
| 6 | Backend — restore | Browse history, restore deleted objects via `/api/docs` |
| 7 | Frontend — browse | `/archiver` page: live count, results grid, CSV export |
| 8 | Frontend — tag & review | Select objects, tag/untag from UI, preview full impact |
| 9 | Frontend — execute | Full delete from UI, cancel, color-coded toast |
| 10 | Frontend — history | Browse sessions, restore from UI, see new GUIDs |
