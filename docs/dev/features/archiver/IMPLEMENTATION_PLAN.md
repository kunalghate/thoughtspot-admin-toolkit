# Content Archiver — Implementation Plan

## Core Safety Principles

1. **No cap on dry-run** — every selected object gets a full permission + dependency check,
   run as a background job so the HTTP connection never blocks
2. **TML export is mandatory** — objects whose export fails are never deleted
3. **`DELETE` typed confirmation** — prevents accidental click-through on large batches
4. **`ArchiveRecord` per deleted object** — full snapshot stored for restore, including
   the new GUID assigned by ThoughtSpot after restore
5. **Restore always available** — Archive History + TML import endpoint

---

## Staleness Filter

```sql
WHERE (last_accessed_at < :cutoff_activity OR last_accessed_at IS NULL)
  AND (modified_at      < :cutoff_modified  OR modified_at IS NULL)
```

UI exposes two independent sliders (both default 90 days):
- **"Unused for at least N days"** → `stale_activity_days`
- **"Unedited for at least M days"** → `stale_modified_days`

---

## Files to Create

| File | Purpose |
|---|---|
| `ts_admin/models/archive_record.py` | SQLModel table — one row per deleted object |
| `ts_admin/services/archiver_service.py` | Business logic: SQLite queries + TS API orchestration |
| `ts_admin/api/archiver.py` | FastAPI router — 9 endpoints |
| `frontend/pages/archiver.tsx` | Archiver page — wizard + archive history |

## Files to Modify

| File | Change |
|---|---|
| `ts_admin/ts_client/client.py` | Add `tml_export()`, `create_tag()`, `fetch_dependents()`, `import_tml()` |
| `ts_admin/database.py` | Import `archive_record` in `init_db()` so table is created at startup |
| `alembic/env.py` | Import `archive_record` model so autogenerate detects the table |
| `ts_admin/main.py` | Register archiver router |
| `frontend/lib/types.ts` | Add archiver types |
| `frontend/lib/api.ts` | Add `archiverApi` object |

Sidebar nav (`/archiver`) already wired in `Sidebar.tsx` — no change needed.

---

## Phase 1 — New SQLite Model (`ts_admin/models/archive_record.py`)

```python
from datetime import datetime, UTC
from uuid import uuid4
from sqlmodel import SQLModel, Field


class ArchiveRecord(SQLModel, table=True):
    __tablename__ = "archive_records"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    job_id: str = Field(index=True)           # FK to jobs.id — the archive session

    # Snapshot of the object at deletion time
    ts_guid: str = Field(index=True)
    name: str
    object_type: str                           # LIVEBOARD | ANSWER
    owner_guid: str
    owner_name: str
    org_id: int = Field(default=0)
    last_accessed_at: datetime | None = None
    days_unused: int = 0
    tags: str = Field(default="[]")           # JSON array of tag names at deletion time

    # TML backup
    tml_path: str | None = None               # absolute path to .tml file on disk
    tml_export_status: str = "PENDING"        # PENDING | SUCCESS | FAILED
    tml_export_error: str | None = None

    # Lifecycle
    archived_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    restored_at: datetime | None = None
    restored_as_guid: str | None = None       # NEW GUID assigned by TS after TML import
    restored_by_job_id: str | None = None     # job_id of the restore job
```

**No `@property is_restorable` on the model.** It is computed in the API layer when
building `ArchiveRecordResponse` to avoid SQLModel serialization issues:

```python
# In api/archiver.py, when building the response:
is_restorable = (
    record.tml_path is not None
    and record.tml_export_status == "SUCCESS"
    and record.restored_at is None
)
```

**Why `restored_as_guid`?** ThoughtSpot assigns a brand-new GUID when importing a TML.
The original `ts_guid` is permanently gone. After restore, the new GUID is stored here
so the toolkit can cache and track the recreated object correctly.

### Required companion edits

**`ts_admin/database.py` — add import in `init_db()`:**
```python
def init_db() -> None:
    import ts_admin.models.archive_record      # noqa: F401  ← add this
    # ... existing imports
    SQLModel.metadata.create_all(get_engine())
```

**`alembic/env.py` — add import so autogenerate detects the table:**
```python
import ts_admin.models.archive_record          # noqa: F401  ← add this
# ... existing imports
```

**Generate and apply the migration:**
```bash
alembic revision --autogenerate -m "add archive_records table"
alembic upgrade head
```

---

## Phase 2 — ts_client Extensions (`ts_admin/ts_client/client.py`)

### `tml_export(object_ids) → list[dict]`

```python
async def tml_export(self, *, object_ids: list[str]) -> list[dict]:
    """
    POST /api/rest/2.0/metadata/tml/export

    Returns list of dicts. A successful item has an "edoc" key containing
    the YAML TML string. A failed item has no "edoc" key (or an empty one).
    HTTP-level failures raise TSServerError via _request().
    """
    data = await self._request(
        "POST",
        "/api/rest/2.0/metadata/tml/export",
        json={
            "metadata": [{"identifier": oid} for oid in object_ids],
            "export_associated_objects": "NONE",
            "export_fqn": True,
        },
        context="tml_export",
    )
    return data if isinstance(data, list) else data.get("object", [])
```

**Success check (fix #10):** use `"edoc" in result and result["edoc"]`, NOT `status_code == "OK"`.
The v2 export API does not include a `status_code` field on success — it just includes `edoc`.

### `create_tag(name, color="") → TSTag`

```python
async def create_tag(self, *, name: str, color: str = "") -> TSTag:
    """POST /api/rest/2.0/tags"""
    data = await self._request(
        "POST", "/api/rest/2.0/tags",
        json={"name": name, "color": color or None},
        context="create_tag",
    )
    return TSTag.model_validate(data)
```

### `fetch_dependents(objects) → dict[str, list[dict]]`

```python
async def fetch_dependents(
    self,
    *,
    objects: list[dict],    # [{ "identifier": guid, "type": "LIVEBOARD" }, ...]
) -> dict[str, list[dict]]:
    """
    POST /api/rest/2.0/dependency/listdependents

    ⚠ VERIFY RESPONSE SHAPE before implementing the parser.
    Run: curl -X POST {ts_url}/api/rest/2.0/dependency/listdependents \
         -H "Authorization: Bearer {token}" \
         -d '{"metadata": [{"identifier": "<guid>", "type": "LIVEBOARD"}]}'
    Adjust the key names below to match the actual response.
    Wrap in try/except TSResponseParseError as a defensive fallback.
    """
    data = await self._request(
        "POST",
        "/api/rest/2.0/dependency/listdependents",
        json={"metadata": objects},
        context="fetch_dependents",
    )
    result: dict[str, list[dict]] = {}
    try:
        for item in data if isinstance(data, list) else []:
            guid = item.get("id") or item.get("identifier", "")
            result[guid] = item.get("dependents", [])
    except (KeyError, TypeError, AttributeError) as exc:
        raise TSResponseParseError(
            f"Unexpected shape from dependency/listdependents: {exc}"
        ) from exc
    return result
```

### `import_tml(tml_strings, import_policy="PARTIAL") → list[dict]`

```python
async def import_tml(
    self,
    *,
    tml_strings: list[str],
    import_policy: str = "PARTIAL",
) -> list[dict]:
    """
    POST /api/rest/2.0/metadata/tml/import

    import_policy="PARTIAL": import what we can, surface per-item errors
    rather than failing the entire batch.

    Each returned item has: object_id (new GUID), name, type,
    status { status_code: "OK"|"ERROR", error_message: str|None }
    """
    data = await self._request(
        "POST",
        "/api/rest/2.0/metadata/tml/import",
        json={"metadata_tmls": tml_strings, "import_policy": import_policy},
        context="import_tml",
    )
    return data if isinstance(data, list) else data.get("object", [])
```

---

## Phase 3 — ArchiverService (`ts_admin/services/archiver_service.py`)

All methods are `@staticmethod`. SQLite-only methods are sync; TS-calling methods are async.

### `preview()` — sync, SQLite only

```python
preview(
    cluster_id: str,
    org_id: int,
    stale_activity_days: int,
    stale_modified_days: int,
    types: list[str] | None,
    exclude_tags: list[str] | None,
    owner_guid: str | None,
    exclude_owner_guids: list[str] | None,
) -> dict   # { total: int, by_type: dict[str,int], criteria_summary: str }
```

Pure SQLite `COUNT(*)`. Filters beyond `MetadataService`:
- `exclude_tags`: `~CachedMetadata.tag_names.contains(f'"{tag}"')` per tag
- `exclude_owner_guids`: `CachedMetadata.owner_guid.notin_(list)`
- Stale AND filter:
  `(last_accessed_at < cutoff_a OR IS NULL) AND (modified_at < cutoff_m OR IS NULL)`

### `search()` — sync, SQLite only

Same query, paginated. Computes `days_unused` per row:
`(now_utc - last_accessed_at).days` → fallback `(now_utc - created_at).days` → fallback `0`.

**Sorting:** `days_unused` is computed in Python — it is not a DB column and cannot be used in
`ORDER BY`. Sort by `last_accessed_at` as a proxy using the same pattern as `MetadataService.search()`:

```python
from sqlalchemy import asc, nulls_last
order_expr = nulls_last(asc(CachedMetadata.last_accessed_at))
# NULL last_accessed_at → sorted first (never accessed = most stale)
```

Then compute and attach `days_unused` after fetching the page.

### `list_tags()` — sync, SQLite only

Reads `CachedTag` rows. Used for tag autocomplete.

### `dryrun()` — async, background task body, NO CAP

```python
async def dryrun(
    job_id: str,
    cluster_id: str,
    org_id: int,
    object_ids: list[str],
) -> None
```

Called via `BackgroundTasks.add_task()` — same pattern as `execute()`. Stores result
in `Job.result` JSON. Frontend polls `GET /api/v1/jobs/{job_id}`.

```
1. mark_running(job_id, total=len(object_ids))

2. Look up { ts_guid, object_type, name, owner_name } from SQLite for all object_ids
   (single SELECT WHERE ts_guid IN (...) — one query, not N queries)

3. Permission check — asyncio.gather with Semaphore(10):
   sem = asyncio.Semaphore(10)
   async def check_one(guid, obj_type):
       async with sem:
           return await client.fetch_permissions(ts_guid=guid, object_type=obj_type)
   all_perms = await asyncio.gather(
       *[check_one(g, t) for g, t in guid_type_pairs],
       return_exceptions=True,   # ← don't let one failure abort all others
   )
   # Handle TSObjectNotFoundError per-item (object may have been deleted already)

4. Dependency check — single batch call:
   dep_map = await client.fetch_dependents(objects=[
       {"identifier": guid, "type": obj_type}
       for guid, obj_type in guid_type_pairs
   ])

5. Aggregate:
   by_type: { "LIVEBOARD": N, "ANSWER": M }
   shared_count: count of objects where permissions list is non-empty
   affected_principals: Counter by principal_name → { name, type, object_count }, desc
   dependency_warnings: [{ ts_guid, name, object_type, dependents[] }]
                         for objects where dep_map[guid] is non-empty

6. mark_complete({
       "total": len(object_ids),
       "by_type": by_type,
       "shared_count": shared_count,
       "affected_principals": [...],
       "dependency_warnings": [...],
   })
   # all_objects is NOT stored here — paginated separately via /dryrun/{job_id}/objects
```

**Performance:** 300 objects / semaphore(10) × ~150ms ≈ 4-5s total. Runs in background —
no HTTP timeout risk. Frontend shows live progress via job polling.

### `dryrun_objects()` — sync, SQLite only

```python
dryrun_objects(
    job_id: str,
    cluster_id: str,
    record_offset: int,
    page_size: int,
) -> tuple[list[dict], int]
```

Reads `object_ids` from `Job.parameters` JSON, then runs a paginated
`SELECT FROM CachedMetadata WHERE ts_guid IN (...)` ordered by `last_accessed_at ASC NULLS FIRST`
(see note in `search()` — `days_unused` is computed post-fetch, not sortable in SQL).
Used by `GET /dryrun/{job_id}/objects` to power the modal's object list grid.

### `execute()` — async, background task

```python
async def execute(
    job_id: str,
    cluster_id: str,
    org_id: int,
    object_ids: list[str],
    action: str,                  # "tag" | "untag" | "delete"
    tag_name: str | None,
    create_tag_if_missing: bool,
) -> None
```

**action="delete" flow:**

```
Phase A — TML Export (mandatory, cannot be skipped):
  mark_running(job_id, total=len(object_ids))

  # Create ArchiveRecord rows (PENDING) in one batch INSERT
  with get_session() as session:
      for obj in objects_from_sqlite:
          session.add(ArchiveRecord(
              cluster_id=cluster_id, job_id=job_id,
              ts_guid=obj.ts_guid, name=obj.name,
              object_type=obj.object_type,
              owner_guid=obj.owner_guid, owner_name=obj.owner_name,
              org_id=obj.org_id,
              last_accessed_at=obj.last_accessed_at,
              days_unused=compute_days_unused(obj),
              tags=obj.tag_names,
              tml_export_status="PENDING",
          ))
      session.commit()

  delete_batch: list[str] = []
  for chunk in chunks(object_ids, 50):
      results = await client.tml_export(object_ids=chunk)
      for result in results:
          guid = result.get("info", {}).get("id", "")
          edoc = result.get("edoc", "")          # ← correct field (fix #10)
          if edoc:                                # success: edoc is non-empty
              path = TML_DIR / job_id / f"{guid}.tml"
              path.parent.mkdir(parents=True, exist_ok=True)
              path.write_text(edoc, encoding="utf-8")
              update ArchiveRecord: tml_path=str(path), tml_export_status="SUCCESS"
              delete_batch.append(guid)
          else:
              error_msg = result.get("info", {}).get("error_message", "TML export failed")
              update ArchiveRecord: tml_export_status="FAILED", tml_export_error=error_msg
              # This guid is NOT added to delete_batch — it will NOT be deleted

Phase B — Delete (only objects with successful TML export):
  # Group by object_type, then chunk — delete API requires homogeneous type per call
  # Map string type to MetadataType enum (fix #8):
  #   MetadataType(obj_type_str) e.g. MetadataType("LIVEBOARD") → MetadataType.LIVEBOARD
  succeeded = []
  failed_delete = []
  by_type = group_by(delete_batch, key=lambda g: sqlite_lookup_type(g))
  for obj_type_str, guids in by_type.items():
      enum_type = MetadataType(obj_type_str)      # ← fix #8: string → enum
      for chunk in chunks(guids, 50):
          try:
              await client.delete_metadata(object_ids=chunk, object_type=enum_type)
              succeeded.extend(chunk)
              # Remove from SQLite cache
              with get_session() as session:
                  session.exec(
                      delete(CachedMetadata).where(CachedMetadata.ts_guid.in_(chunk))
                  )
                  session.commit()
          except TSAdminError as exc:
              failed_delete.extend(chunk)
              log(f"Delete failed for chunk: {exc}")
          update_progress(job_id, progress=len(succeeded) + len(failed_delete))

Phase C — Audit:
  Write AuditLog entry:
    action_type = "delete"
    entity_type = "metadata"
    items_affected = len(succeeded)
    parameters = {
        "job_id": job_id,
        "succeeded": [{ "ts_guid", "name", "object_type", "owner_name" } for each],
        "failed_tml_export": [{ "ts_guid", "name", "error" } for each],
        "failed_delete": [{ "ts_guid", "name", "error" } for each],
    }
    status = "COMPLETE" | "PARTIAL" | "FAILED"

  mark_complete({
      "succeeded": len(succeeded),
      "failed_tml_export": count,
      "failed_delete": count,
      "tml_export_path": str(TML_DIR / job_id),
  })
```

**action="tag" flow:**
```
  mark_running(job_id, total)
  if create_tag_if_missing:
      tags = await client.search_tags()
      tag = next((t for t in tags if t.name == tag_name), None)
      if tag is None:
          tag = await client.create_tag(name=tag_name)
  for chunk in chunks(object_ids, 50):
      await client.assign_tag(object_ids=chunk, tag_id=tag.id)
      # Update tag_names in SQLite cache (append tag if not present)
      update_progress(job_id, ...)
  Write AuditLog entry (action_type="tag")
  mark_complete()
```

**action="untag" flow:** same but `client.unassign_tag()` + remove tag from `tag_names`.

### `restore()` — async, background task

```python
async def restore(
    job_id: str,
    cluster_id: str,
    org_id: int,
    archive_record_ids: list[str],
) -> None
```

```
  mark_running(job_id, total=len(archive_record_ids))
  restored = 0
  failed = []

  # Batch into groups of 10 TML strings per import call (fix #9)
  for batch in chunks(archive_record_ids, 10):
      records = SELECT FROM archive_records WHERE id IN batch
      importable = [r for r in records
                    if r.tml_path and r.tml_export_status == "SUCCESS"
                       and r.restored_at is None]
      skipped = [r for r in records if r not in importable]
      for r in skipped:
          failed.append({ "id": r.id, "name": r.name, "reason": "TML not available" })

      tml_strings = [Path(r.tml_path).read_text(encoding="utf-8") for r in importable]
      results = await client.import_tml(tml_strings=tml_strings, import_policy="PARTIAL")

      # Map result index back to record (results and importable are same-order)
      for i, result in enumerate(results):
          record = importable[i]
          status = result.get("status", {}).get("status_code", "ERROR")
          if status == "OK":
              new_guid = result.get("object_id") or result.get("id", "")
              # Update ArchiveRecord with new GUID (fix #2)
              UPDATE archive_records SET
                  restored_at = now(),
                  restored_as_guid = new_guid,    # ← CRITICAL: new GUID, not old one
                  restored_by_job_id = job_id
              WHERE id = record.id

              # Insert into CachedMetadata using NEW guid (not old ts_guid) (fix #2)
              with get_session() as session:
                  session.add(CachedMetadata(
                      cluster_id=cluster_id,
                      org_id=org_id,
                      ts_guid=new_guid,            # ← new GUID
                      name=record.name,
                      object_type=record.object_type,
                      owner_guid=record.owner_guid,
                      owner_name=record.owner_name,
                      tag_names=record.tags,
                      synced_at=datetime.now(UTC),
                  ))
                  session.commit()
              restored += 1
          else:
              error = result.get("status", {}).get("error_message", "Import failed")
              failed.append({ "id": record.id, "name": record.name, "reason": error })

      update_progress(job_id, progress=restored + len(failed))

  Write AuditLog entry (action_type="restore")
  mark_complete({ "restored": restored, "failed": failed })
```

---

## Phase 4 — Archiver API (`ts_admin/api/archiver.py`)

Router prefix `/archiver` → all routes at `/api/v1/archiver/...`

### Endpoints

```
GET  /api/v1/archiver/preview
  Query: cluster_id, org_id, stale_activity_days=90, stale_modified_days=90,
         types[], exclude_tags[], owner_guid, exclude_owner_guids[]
  → ArchiverPreviewResponse { total, by_type, criteria_summary }
  Sync. SQLite only.

GET  /api/v1/archiver/results
  Same criteria + record_offset=0, page_size=200
  → ArchiverResultsResponse { items[], total, page, page_size }
  Sync. SQLite only.

GET  /api/v1/archiver/tags
  Query: cluster_id, org_id
  → list[ArchiverTagItem]
  Sync. SQLite only.

POST /api/v1/archiver/dryrun                            ← now a background job
  Body: DryRunRequest { cluster_id, org_id, object_ids[] }
  → ExecuteResponse { job_id, action="dryrun", total }
  Creates Job (job_type="archive_dryrun", parameters={ object_ids }) →
  fires BackgroundTasks.add_task(ArchiverService.dryrun, ...) →
  returns job_id immediately.
  Frontend polls GET /api/v1/jobs/{job_id}. When COMPLETE, Job.result has the summary.

GET  /api/v1/archiver/dryrun/{job_id}/objects           ← new endpoint (fix #5)
  Query: cluster_id, record_offset=0, page_size=100
  → ArchiverResultsResponse
  Reads object_ids from Job.parameters, paginates from CachedMetadata.
  Sync. SQLite only. Used to power the modal's object list grid.

POST /api/v1/archiver/execute
  Body: ExecuteRequest { cluster_id, org_id, object_ids[], action, tag_name,
                          create_tag_if_missing=true }
  → ExecuteResponse { job_id, action, total }
  Creates Job → fires BackgroundTask → returns immediately.

GET  /api/v1/archiver/history
  Query: cluster_id, org_id, record_offset=0, page_size=20
  → ArchiveHistoryResponse { sessions[], total }
  Sync. SQLite only.

GET  /api/v1/archiver/history/{job_id}
  Query: cluster_id, record_offset=0, page_size=100
  → ArchiveSessionResponse { job, records[], total }
  Sync. SQLite only.

POST /api/v1/archiver/restore
  Body: RestoreRequest { cluster_id, org_id, archive_record_ids[] }
  → ExecuteResponse { job_id, action="restore", total }
  Creates Job → fires BackgroundTask → returns immediately.
```

### Pydantic Models

```python
# Requests
DryRunRequest:    cluster_id: str, org_id: int, object_ids: list[str]
ExecuteRequest:   cluster_id: str, org_id: int, object_ids: list[str],
                  action: Literal["tag","untag","delete"],
                  tag_name: str | None = None,
                  create_tag_if_missing: bool = True
RestoreRequest:   cluster_id: str, org_id: int, archive_record_ids: list[str]

# Responses — results / preview
ArchiverResultItem:      ts_guid, name, object_type, owner_name, owner_guid,
                         last_accessed_at: str|None, days_unused: int, tags: list[str]
ArchiverResultsResponse: items, total, page, page_size
ArchiverPreviewResponse: total: int, by_type: dict[str,int], criteria_summary: str

# Responses — dry-run (stored in Job.result, returned via job polling)
AffectedPrincipal:       principal_name, principal_type, object_count
DependencyWarning:       ts_guid, name, object_type,
                         dependents: list[{ name: str, type: str, owner_name: str }]
DryRunSummary:           total: int,
                         by_type: dict[str,int],
                         shared_count: int,
                         affected_principals: list[AffectedPrincipal],
                         dependency_warnings: list[DependencyWarning]
                         # NOTE: all_objects is NOT here — paginated via separate endpoint

# Responses — execute / restore
ExecuteResponse:         job_id: str, action: str, total: int

# Responses — history
ArchiveRecordResponse:   id, ts_guid, name, object_type, owner_name, owner_guid,
                         archived_at, days_unused, tags: list[str],
                         tml_export_status: Literal["PENDING","SUCCESS","FAILED"],
                         tml_path: str|None, restored_at: str|None,
                         restored_as_guid: str|None,
                         is_restorable: bool           # computed in API layer, not @property
ArchiveSessionSummary:   job_id, archived_at, total, succeeded, failed, status: str
ArchiveHistoryResponse:  sessions: list[ArchiveSessionSummary], total: int
ArchiveSessionResponse:  job: ArchiveSessionSummary, records: list[ArchiveRecordResponse],
                         total: int
ArchiverTagItem:         ts_guid, name, color
```

### Register in `main.py`

```python
from ts_admin.api import ..., archiver
app.include_router(archiver.router, prefix="/api/v1")
```

---

## Phase 5 — Frontend Types (`frontend/lib/types.ts`)

```typescript
export interface ArchiverCriteria {
  stale_activity_days: number;
  stale_modified_days: number;
  types: string[];
  exclude_tags?: string[];
  owner_guid?: string;
  exclude_owner_guids?: string[];
}

export interface ArchiverResultItem {
  ts_guid: string;
  name: string;
  object_type: string;
  owner_name: string;
  owner_guid: string;
  last_accessed_at: string | null;
  days_unused: number;
  tags: string[];
}

export interface ArchiverPreviewResponse {
  total: number;
  by_type: Record<string, number>;
  criteria_summary: string;
}

export interface ArchiverResultsResponse {
  items: ArchiverResultItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface AffectedPrincipal {
  principal_name: string;
  principal_type: string;
  object_count: number;
}

export interface DependencyWarning {
  ts_guid: string;
  name: string;
  object_type: string;
  dependents: Array<{ name: string; type: string; owner_name: string }>;
}

// Returned from Job.result after job_type="archive_dryrun" completes
export interface DryRunSummary {
  total: number;
  by_type: Record<string, number>;
  shared_count: number;
  affected_principals: AffectedPrincipal[];
  dependency_warnings: DependencyWarning[];
  // all_objects NOT here — loaded lazily via GET /dryrun/{job_id}/objects
}

export interface ExecuteResponse {
  job_id: string;
  action: string;
  total: number;
}

export interface ArchiverTagItem {
  ts_guid: string;
  name: string;
  color: string;
}

export interface ArchiveRecordResponse {
  id: string;
  ts_guid: string;
  name: string;
  object_type: string;
  owner_name: string;
  archived_at: string;
  days_unused: number;
  tags: string[];
  tml_export_status: "PENDING" | "SUCCESS" | "FAILED";
  tml_path: string | null;
  restored_at: string | null;
  restored_as_guid: string | null;
  is_restorable: boolean;
}

export interface ArchiveSessionSummary {
  job_id: string;
  archived_at: string;
  total: number;
  succeeded: number;
  failed: number;
  status: string;
}

export interface ArchiveHistoryResponse {
  sessions: ArchiveSessionSummary[];
  total: number;
}

export interface ArchiveSessionResponse {
  job: ArchiveSessionSummary;
  records: ArchiveRecordResponse[];
  total: number;
}
```

---

## Phase 6 — Frontend API Client (`frontend/lib/api.ts`)

```typescript
export const archiverApi = {
  preview: (params: ArchiverCriteria & { cluster_id: string; org_id: number }) => {
    const q = buildArchiverParams(params);
    return request<ArchiverPreviewResponse>(`/archiver/preview?${q}`);
  },

  results: (params: ArchiverCriteria & {
    cluster_id: string; org_id: number;
    record_offset?: number; page_size?: number;
  }) => {
    const q = buildArchiverParams(params);
    return request<ArchiverResultsResponse>(`/archiver/results?${q}`);
  },

  tags: (clusterId: string, orgId: number) =>
    request<ArchiverTagItem[]>(`/archiver/tags?cluster_id=${clusterId}&org_id=${orgId}`),

  // Returns job_id immediately — frontend polls GET /api/v1/jobs/{job_id}
  dryrun: (body: { cluster_id: string; org_id: number; object_ids: string[] }) =>
    request<ExecuteResponse>(`/archiver/dryrun`, { method: "POST", body: JSON.stringify(body) }),

  // Paginated object list for the dry-run modal grid
  dryrunObjects: (jobId: string, clusterId: string, offset = 0, pageSize = 100) =>
    request<ArchiverResultsResponse>(
      `/archiver/dryrun/${jobId}/objects?cluster_id=${clusterId}&record_offset=${offset}&page_size=${pageSize}`
    ),

  execute: (body: {
    cluster_id: string; org_id: number; object_ids: string[];
    action: "tag" | "untag" | "delete"; tag_name?: string;
    create_tag_if_missing?: boolean;
  }) =>
    request<ExecuteResponse>(`/archiver/execute`, { method: "POST", body: JSON.stringify(body) }),

  history: (clusterId: string, orgId: number, offset = 0) =>
    request<ArchiveHistoryResponse>(
      `/archiver/history?cluster_id=${clusterId}&org_id=${orgId}&record_offset=${offset}`
    ),

  historySession: (jobId: string, clusterId: string, offset = 0) =>
    request<ArchiveSessionResponse>(
      `/archiver/history/${jobId}?cluster_id=${clusterId}&record_offset=${offset}`
    ),

  restore: (body: { cluster_id: string; org_id: number; archive_record_ids: string[] }) =>
    request<ExecuteResponse>(`/archiver/restore`, { method: "POST", body: JSON.stringify(body) }),
};
```

---

## Phase 7 — Frontend Page (`frontend/pages/archiver.tsx`)

Single file. `AppShell` wrapper. Pages router (not App Router). Two tabs: **Archive** and **History**.

### Archive Tab Layout

```
Two-column: CriteriaPanel (280px) | ResultsPanel (flex-1)
  CriteriaPanel:
    - staleActivityDays input (default 90) — "Unused for at least N days"
    - staleModifiedDays input (default 90) — "Unedited for at least M days"
    - Content type checkboxes (LIVEBOARD, ANSWER)
    - Exclude tags input (comma-separated)
    - Owner filter / Exclude owners inputs
    - LiveCountBadge: "342 stale objects match"
    - "Search" + "Reset" buttons

  ResultsPanel:
    - Header: "{total} stale objects" + "Export CSV"
    - SelectionBar (selectedGuids.size > 0):
        "{N} selected" | "Tag as INACTIVE" | "Delete selected" | "Clear"
    - AgGridReact:
        Columns: checkbox, Name, Type, Owner, Last accessed, Days unused, Tags, Actions
        Actions col: per-row "Untag" button

DryRunModal (shown when dryRunState !== "idle"):

  "polling" state — dry-run job is running:
    Spinner + "Checking {N} objects for permissions and dependencies..."
    (polls GET /api/v1/jobs/{dryRunJobId} every 2s)

  "ready" state — dry-run job complete, summary loaded:
    - Impact cards: Total / Liveboards / Answers
    - Dependency warning box (if dependency_warnings.length > 0):
        "⚠ {M} objects have active dependents — deleting them may break other content"
        Collapsible list of { object name → dependent names }
    - Shared access warning (if shared_count > 0):
        "{K} objects are shared — these users will lose access: [principal names]"
    - Object list: AgGridReact loading from GET /dryrun/{jobId}/objects (lazy, paginated)
    - Confirmation input: "Type DELETE to confirm"
    - Footer: Cancel | "Delete {N} objects" (red, disabled until input === "DELETE")

  "running" state — execute job is running:
    Progress bar + "{done}/{total} objects processed"

  "complete" state:
    Summary: "{succeeded} deleted · {failed_tml} skipped (TML export failed) ·
              {failed_delete} failed"
    Link: "View in History →" (switches to History tab)
```

### History Tab Layout

```
ArchiveHistoryGrid:
  Columns: Date, Objects deleted, Succeeded, Failed, Status, Actions ("View")
  Click "View" → ArchiveSessionDrawer

ArchiveSessionDrawer (slide-over):
  Header: "Archive session — {date}"
  AgGridReact of ArchiveRecord rows:
    Columns: Name, Type, Owner, Archived at, Days unused, TML export, Restored?, Actions
    Actions: "Restore" button (disabled if !is_restorable, tooltip explains why)
  Footer: "Restore selected" bulk button
```

### Key State

```typescript
activeTab: "archive" | "history"

// Criteria
staleActivityDays: number = 90
staleModifiedDays: number = 90
types: string[] = ["LIVEBOARD", "ANSWER"]
excludeTags: string = ""
ownerFilter: string = ""
excludeOwners: string = ""
debouncedCriteria: ArchiverCriteria    // 300ms debounced

// Preview
previewCount: number | null

// Selection — external Set, persists across AG Grid infinite scroll pages
selectedGuids: Set<string>

// Dry-run state machine
dryRunState: "idle" | "polling" | "ready" | "running" | "complete"
dryRunJobId: string | null             // job_id from POST /dryrun
dryRunSummary: DryRunSummary | null    // loaded from Job.result when COMPLETE
deleteConfirmText: string              // must equal "DELETE" to enable confirm

// Execute job
activeJobId: string | null
jobProgress: { progress: number; total: number } | null
pollRef: MutableRefObject<ReturnType<typeof setInterval> | null>

// History
historyData: ArchiveHistoryResponse | null
activeSession: ArchiveSessionResponse | null
sessionDrawerOpen: boolean
```

### Confirmation Gate

```typescript
const canConfirm =
  dryRunState === "ready" &&
  deleteConfirmText === "DELETE" &&
  dryRunSummary !== null;
```

### Job Reconnection on Page Mount (fix #11)

```typescript
// On mount: check for any in-progress archive jobs and resume polling
useEffect(() => {
  if (!activeCluster?.id) return;
  jobsApi.list({ cluster_id: activeCluster.id })
    .then((jobs) => {
      const running = jobs.find(
        (j) => j.job_type === "archive" && j.status === "RUNNING"
      );
      if (running) {
        setActiveJobId(running.id);
        setJobProgress({ progress: running.progress, total: running.total });
        // dryRunState stays "idle" — this is an execute job, not a dry-run
      }
    })
    .catch(() => {});
}, [activeCluster?.id]);
```

### Debounce Pattern

```typescript
// Criteria change → debounce → update debouncedCriteria → fetch preview + reset grid
useEffect(() => {
  const t = setTimeout(
    () => setDebouncedCriteria({ staleActivityDays, staleModifiedDays, types,
                                  excludeTags, ownerFilter, excludeOwners }),
    300
  );
  return () => clearTimeout(t);
}, [staleActivityDays, staleModifiedDays, types, excludeTags, ownerFilter, excludeOwners]);

useEffect(() => {
  if (!activeCluster?.id) return;
  archiverApi.preview({ cluster_id: activeCluster.id, org_id: activeOrg?.org_id ?? 0,
                         ...debouncedCriteria })
    .then((r) => setPreviewCount(r.total))
    .catch(() => {});
  gridRef.current?.api?.setDatasource(buildDatasource(debouncedCriteria));
}, [debouncedCriteria, activeCluster?.id]);
```

---

## Implementation Order

| # | Step | Gate / Test |
|---|---|---|
| 1 | `models/archive_record.py` | File created |
| 2 | Update `database.py` `init_db()` + `alembic/env.py` | Both import the model |
| 3 | Generate + apply Alembic migration | `archive_records` table in SQLite |
| 4 | `client.py` — `tml_export()`, `create_tag()`, `fetch_dependents()`, `import_tml()` | Unit test with mock HTTP. Verify `fetch_dependents` shape against real cluster |
| 5 | `archiver_service.py` — `preview()`, `search()`, `list_tags()` | SQLite queries return data |
| 6 | `api/archiver.py` — GET endpoints + register in `main.py` | `GET /preview` returns 200 |
| 7 | `archiver_service.py` — `dryrun()` as background task | POST → job_id → poll → COMPLETE |
| 8 | `api/archiver.py` — POST `/dryrun` + GET `/dryrun/{job_id}/objects` | Dependency warnings appear |
| 9 | `archiver_service.py` — `execute()` tag + untag flows | Tag real GUIDs, verify in TS UI |
| 10 | `api/archiver.py` — POST `/execute` | Tag job runs, poll to COMPLETE |
| 11 | `execute()` delete flow — TML export → ArchiveRecord → delete | TML files written; failed exports skipped, not deleted |
| 12 | `archiver_service.py` — `restore()` (batched, new GUID) | Restore object appears in TS with new GUID |
| 13 | `api/archiver.py` — GET `/history`, GET `/history/{job_id}`, POST `/restore` | History lists sessions, restore fires |
| 14 | `frontend/lib/types.ts` + `api.ts` | TypeScript compiles |
| 15 | Frontend Archive tab — CriteriaPanel + count badge + results grid | Badge updates, grid paginating |
| 16 | DryRunModal — polling → ready states, warnings, typed confirmation | "DELETE" gates confirm; dep warnings shown |
| 17 | Execute + job polling + complete state + reconnection on mount | Progress bar; refresh page mid-job and polling resumes |
| 18 | Frontend History tab + ArchiveSessionDrawer + restore | Restore fires, `restored_as_guid` visible |

---

## Design Decisions

**Why is dry-run a background job?**
A synchronous HTTP endpoint calling fetch_permissions() for 500+ objects (even with semaphore(10))
can block for 15–30+ seconds, creating an unresponsive UI. Running it as a background job
(same pattern as execute) lets the frontend poll with a progress spinner, matches the
existing job infrastructure, and eliminates any HTTP timeout risk.

**Why is `all_objects` removed from DryRunSummary?**
Embedding 2,000 objects in `Job.result` JSON bloats the jobs table and forces the full
list into memory in both the backend and frontend. The modal grid loads it lazily via
`GET /dryrun/{job_id}/objects` which reads from SQLite — fast, paginated, no bloat.

**Why `restored_as_guid` instead of overwriting `ts_guid`?**
ThoughtSpot assigns a new GUID on TML import. Overwriting `ts_guid` would destroy the
audit trail (we'd lose the link between the original object and its archive record).
Keeping both allows the history panel to show "was X, now lives as Y".

**Why `is_restorable` computed in the API layer?**
SQLModel `@property` decorators are not included in Pydantic serialization — they're
invisible to `model_validate` and `model_dump`. Computing it in the API layer when
building `ArchiveRecordResponse` is the correct pattern.

**Why `MetadataType(obj_type_str)` conversion?**
`delete_metadata()` takes `object_type: MetadataType` (the enum). `CachedMetadata.object_type`
stores a raw string. Without the conversion, Pydantic raises a validation error at runtime.

**Why `return_exceptions=True` in `asyncio.gather` for permissions?**
One `TSObjectNotFoundError` (object already deleted) or network glitch per-object should
not abort the entire dry-run for 300 other objects. Each exception is caught per-item,
logged, and the object is flagged in the summary rather than crashing the job.

**Why `import_policy="PARTIAL"` for restore?**
If an object being restored references a Worksheet or Table that was renamed or deleted,
`PARTIAL` imports the rest of the TML and surfaces per-field errors rather than failing
the entire restore. The admin is shown what couldn't be resolved.

**Why batch restore in groups of 10 TML strings?**
The TS TML import API accepts multiple TML strings per call. One string per call = N
round-trips. Batching at 10 keeps the payload size reasonable while reducing round-trips
by 10×. Each import result maps back to its archive record by index position.

**TML directory location:**
`~/.ts-admin/tml-exports/{job_id}/{guid}.tml`
Constant `TML_DIR = Path.home() / ".ts-admin" / "tml-exports"` — consistent with the
existing `DB_DIR = Path.home() / ".ts-admin"` pattern in `database.py`.

---

## Enterprise Hardening

These requirements must be implemented alongside the core feature. They are not optional
polish — several are correctness issues that cause data inconsistency or silent failures
in production.

---

### H1 — Crash Recovery: Mark Stuck Jobs FAILED on Startup *(High)*

**Problem:** If the Python process crashes (OOM, SIGKILL, reboot) mid-delete, jobs are
left in `RUNNING` state forever. The admin has no way to know whether deletion completed,
and `ArchiveRecord` rows are left in an ambiguous state.

**Fix — `ts_admin/main.py` startup event:**

```python
@app.on_event("startup")
async def recover_stuck_jobs() -> None:
    with get_session() as session:
        stuck = session.exec(
            select(Job).where(Job.status == "RUNNING")
        ).all()
        for job in stuck:
            mark_failed(job.id, error=(
                "Process restarted while this job was running. "
                "Check Archive History to reconcile what was actually deleted."
            ))
            # For archive delete jobs: conservatively remove CachedMetadata rows
            # for any ArchiveRecord with tml_export_status="SUCCESS" in this job,
            # since those objects were likely deleted from TS before the crash.
            if job.job_type == "archive":
                params = job.get_parameters()
                if params.get("action") == "delete":
                    guids = session.exec(
                        select(ArchiveRecord.ts_guid)
                        .where(ArchiveRecord.job_id == job.id)
                        .where(ArchiveRecord.tml_export_status == "SUCCESS")
                    ).all()
                    if guids:
                        session.exec(
                            delete(CachedMetadata)
                            .where(CachedMetadata.ts_guid.in_(guids))
                        )
                        session.commit()
```

This prevents ghost objects in the Metadata Explorer after a crash, and gives the admin
a clear FAILED status with an explanatory message to check History.

---

### H2 — Surface Permission-Check Errors in Dry-Run Summary *(High)*

**Problem:** `asyncio.gather(return_exceptions=True)` silently swallows per-object errors
(e.g. `TSObjectNotFoundError` — object already deleted by someone else between search and
dry-run). These objects are neither in `shared_count` nor flagged — they're invisible.

**Fix — add `errors` field to `DryRunSummary`:**

```python
# In DryRunSummary (Pydantic model and TypeScript interface)
errors: list[{ ts_guid: str, name: str, reason: str }]
```

In `dryrun()` background task, after the `asyncio.gather`:

```python
for i, result in enumerate(all_perms):
    guid, obj_type = guid_type_pairs[i]
    if isinstance(result, Exception):
        errors.append({
            "ts_guid": guid,
            "name": sqlite_objects[guid]["name"],
            "reason": str(result),
        })
    else:
        # normal aggregation
```

In the dry-run modal, show an "Unable to check" warning section if `errors.length > 0`:
> "⚠ {N} objects could not be checked (may have been deleted already). Review before confirming."

Add `errors: Array<{ ts_guid: string; name: string; reason: string }>` to
`DryRunSummary` in `frontend/lib/types.ts`.

---

### H3 — Chunk SQLite `IN (...)` Queries at 500 *(High)*

**Problem:** SQLite has a hard limit of 999 bound parameters per query. Any `WHERE
ts_guid IN (...)` with more than 999 GUIDs throws `OperationalError: too many SQL
variables`. This affects `preview()`, `search()`, `dryrun()` lookup, `dryrun_objects()`,
and any restore lookup.

**Fix — add a `chunked_in_query` helper in `ts_admin/services/archiver_service.py`:**

```python
def _fetch_objects_by_guids(
    session: Session, guids: list[str], cluster_id: str, org_id: int
) -> list[CachedMetadata]:
    """Fetches CachedMetadata rows for a list of GUIDs, chunked to avoid
    SQLite's 999-parameter limit."""
    results = []
    for chunk in _chunks(guids, 500):
        rows = session.exec(
            select(CachedMetadata).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                CachedMetadata.ts_guid.in_(chunk),
            )
        ).all()
        results.extend(rows)
    return results

def _chunks(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]
```

Apply `_fetch_objects_by_guids()` everywhere a GUID list is looked up from SQLite. Apply
`_chunks()` to the `CachedMetadata` DELETE in the execute flow as well.

---

### H4 — Use `json.loads` / `json.dumps` for `tag_names` Mutations *(High)*

**Problem:** `CachedMetadata.tag_names` is stored as a JSON string (e.g. `'["INACTIVE",
"Q3"]'`). The plan references `.contains(f'"{tag}"')` for filtering and string
manipulation for updates. Both break for tag names containing double-quotes, backslashes,
or Unicode. ThoughtSpot allows arbitrary tag names.

**Fix — parse before every read, serialize after every write:**

```python
# Reading (filter)
import json

def _has_tag(tag_names_json: str, tag: str) -> bool:
    try:
        return tag in json.loads(tag_names_json or "[]")
    except json.JSONDecodeError:
        return False

def _add_tag(tag_names_json: str, tag: str) -> str:
    tags = json.loads(tag_names_json or "[]")
    if tag not in tags:
        tags.append(tag)
    return json.dumps(tags)

def _remove_tag(tag_names_json: str, tag: str) -> str:
    tags = json.loads(tag_names_json or "[]")
    return json.dumps([t for t in tags if t != tag])
```

Replace all raw `.contains(f'"{tag}"')` SQLAlchemy filters with a Python-side filter
applied after fetching rows (acceptable since exclude_tags is a small list), or use a
JSON function if SQLite supports it (`json_each`). Replace all string-concat tag updates
with `_add_tag` / `_remove_tag`.

---

### H5 — Job Cancellation *(Significant)*

**Problem:** Once a delete job starts, there is no way to stop it. An admin who confirms
the wrong batch must wait for all deletions to complete, then restore from History.

**Fix — add cancel support to the Jobs API (`ts_admin/api/jobs.py`):**

```python
@router.delete("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(job_id: str) -> JobResponse:
    job = get_job(job_id)
    if job.status not in ("QUEUED", "RUNNING"):
        raise HTTPException(status_code=409, detail="Job is already complete")
    cancel_job(job_id)          # sets job.is_cancelled = True in DB
    return JobResponse.from_job(job)
```

Add `is_cancelled: bool = False` field to the `Job` SQLModel (`ts_admin/models/job.py`).
⚠ Not yet in the model — confirmed against current codebase. Requires an Alembic migration
(covered by the Phase 1 migration that also creates `archive_records`). In `execute()`, check at
the start of each chunk:

```python
for chunk in chunks(delete_batch, 50):
    job = get_job(job_id)
    if job.is_cancelled:
        mark_failed(job_id, error="Cancelled by admin")
        return
    # ... proceed with delete
```

**Frontend:** Add a "Cancel" button to the DryRunModal running state (visible alongside
the progress bar). On click, call `DELETE /api/v1/jobs/{activeJobId}/cancel` and set
`dryRunState = "idle"`. Objects deleted before cancel are still in ArchiveRecord and
restorable from History.

**Note:** `is_cancelled` requires an Alembic migration since it adds a column to the
`jobs` table. Generate it alongside or after the `archive_records` migration.

---

### H6 — TML Export Directory Retention Cleanup *(Significant)*

**Problem:** Every delete job writes TML files to `~/.ts-admin/tml-exports/{job_id}/`.
With no cleanup, this grows unbounded over months of use.

**Fix — add `_cleanup_old_tml_exports()` called from the startup event:**

```python
TML_RETENTION_DAYS = 90   # configurable via settings in v1.1

def _cleanup_old_tml_exports() -> None:
    export_root = TML_DIR
    if not export_root.exists():
        return
    cutoff = datetime.now(UTC) - timedelta(days=TML_RETENTION_DAYS)
    for job_dir in export_root.iterdir():
        if not job_dir.is_dir():
            continue
        # Only clean up if all records in this job are fully restored
        # or the directory is older than retention threshold
        try:
            mtime = datetime.fromtimestamp(job_dir.stat().st_mtime, tz=UTC)
            if mtime < cutoff:
                # Check: are all ArchiveRecords for this job_id restored?
                with get_session() as session:
                    unrestored = session.exec(
                        select(func.count(ArchiveRecord.id))
                        .where(ArchiveRecord.job_id == job_dir.name)
                        .where(ArchiveRecord.restored_at.is_(None))
                    ).one()
                if unrestored == 0:
                    shutil.rmtree(job_dir)
        except OSError:
            pass   # skip directories we can't stat
```

Add `import shutil` and call `_cleanup_old_tml_exports()` from the startup event after
`recover_stuck_jobs()`.

---

### H7 — Exclude `parameters` Column from History List Query *(Minor)*

**Problem:** `Job.parameters` stores the full `object_ids` JSON list (up to ~180KB for
5,000 GUIDs). Loading it for every row in the archive history list wastes memory and
makes the query slower than necessary.

**Fix — in `GET /api/v1/archiver/history`, select only summary columns:**

```python
# In archiver_service.py history() method
jobs = session.exec(
    select(
        Job.id, Job.status, Job.progress, Job.total,
        Job.created_at, Job.started_at, Job.completed_at, Job.result
        # NOTE: exclude Job.parameters
    ).where(
        Job.cluster_id == cluster_id,
        Job.job_type == "archive",
    ).order_by(Job.created_at.desc())
    .offset(record_offset).limit(page_size)
).all()
```

Use `with_only_columns()` or a named tuple select rather than `select(Job)`.

---

### H8 — Color-Coded Job Completion Toast *(Minor)*

**Problem:** A job that deleted 0 objects (all TML exports failed) reports as "COMPLETE"
with the same neutral toast as a fully successful run. The admin may not notice.

**Fix — frontend toast logic in `archiver.tsx`:**

```typescript
function buildToast(job: Job): { message: string; variant: "success" | "warning" | "error" } {
  const result = job.result as { succeeded: number; failed_tml_export: number; failed_delete: number };
  if (job.status === "FAILED") {
    return { message: "Archive job failed — no objects were deleted.", variant: "error" };
  }
  if (result.succeeded === 0) {
    return {
      message: `Archive completed but 0 objects were deleted — ${result.failed_tml_export} TML exports failed.`,
      variant: "error",
    };
  }
  if (result.failed_tml_export > 0 || result.failed_delete > 0) {
    return {
      message: `${result.succeeded} deleted · ${result.failed_tml_export + result.failed_delete} skipped. View History for details.`,
      variant: "warning",
    };
  }
  return { message: `${result.succeeded} objects archived successfully.`, variant: "success" };
}
```

---

### H9 — Structured Logging After Every AuditLog Write *(Minor)*

**Problem:** `AuditLog` entries live only in SQLite. A corrupted or deleted database
loses the entire audit trail.

**Fix — emit a structured log line after every `AuditLog` write in `execute()` and
`restore()`:**

```python
import logging
logger = logging.getLogger("ts_admin.archiver")

# After session.commit() for AuditLog:
logger.info(
    "audit action=%(action)s cluster=%(cluster)s items=%(items)d status=%(status)s job=%(job)s",
    {
        "action": entry.action_type,
        "cluster": cluster_id,
        "items": entry.items_affected,
        "status": entry.status,
        "job": job_id,
    },
)
```

This uses the existing Python `logging` infrastructure. Log output goes wherever the
app's root logger is configured (stdout / file), providing a secondary audit trail that
survives database issues.

---

### Updated Implementation Order (with hardening)

| # | Step | Gate / Test |
|---|---|---|
| 1 | `models/archive_record.py` | File created |
| 2 | Update `database.py` `init_db()` + `alembic/env.py` | Both import the model |
| 3 | Add `is_cancelled` to `Job` model | Alembic migration covers both new columns |
| 4 | Generate + apply Alembic migrations | `archive_records` table + `jobs.is_cancelled` in SQLite |
| 5 | `client.py` — 4 new methods + `fetch_dependents` shape verified | Unit test with mock HTTP |
| 6 | `archiver_service.py` — `_chunks()`, `_fetch_objects_by_guids()`, `_add_tag()`, `_remove_tag()` helpers | Unit tests |
| 7 | `archiver_service.py` — `preview()`, `search()`, `list_tags()` | SQLite queries return data |
| 8 | `api/archiver.py` — GET endpoints + register in `main.py` | `GET /preview` returns 200 |
| 9 | `main.py` — startup event: `recover_stuck_jobs()` + `_cleanup_old_tml_exports()` | Startup logs recovery |
| 10 | `jobs.py` — `DELETE /{job_id}/cancel` endpoint | Cancel sets `is_cancelled=True` |
| 11 | `archiver_service.py` — `dryrun()` background task (with `errors[]`) | POST → poll → COMPLETE with warnings |
| 12 | `api/archiver.py` — POST `/dryrun` + GET `/dryrun/{job_id}/objects` | Dep warnings + error section appear |
| 13 | `archiver_service.py` — `execute()` tag + untag flows | Tag real GUIDs, verify in TS UI |
| 14 | `api/archiver.py` — POST `/execute` | Tag job polls to COMPLETE |
| 15 | `execute()` delete flow — TML export → ArchiveRecord → chunked delete + cancel check | TML written; failed exports skipped; cancel mid-job works |
| 16 | `archiver_service.py` — `restore()` (batched 10, new GUID) | Restored object in TS with new GUID |
| 17 | `api/archiver.py` — GET `/history` (no parameters col) + GET `/history/{job_id}` + POST `/restore` | History lists sessions |
| 18 | `frontend/lib/types.ts` + `api.ts` (add `errors[]` to DryRunSummary) | TypeScript compiles |
| 19 | Frontend Archive tab — CriteriaPanel + badge + grid + reconnection on mount | Badge updates; mid-job refresh resumes polling |
| 20 | DryRunModal — polling → ready + error section + dep warnings + typed confirmation | "DELETE" gates confirm |
| 21 | Execute + progress + cancel button + color-coded toast | Cancel stops mid-job; toast shows correct variant |
| 22 | Frontend History tab + ArchiveSessionDrawer + restore | `restored_as_guid` visible |
