# Bulk Deleter — How it works

## What it is

The Bulk Deleter lets admins delete *targeted sets* of ThoughtSpot
content. It's a sibling to the Content Archiver: same destruction
pipeline, different intake.

| Feature        | Selection style       | Mental model                              |
|----------------|-----------------------|-------------------------------------------|
| Archiver       | discovery-driven      | "find rotten content automatically"       |
| Bulk Deleter   | targeting-driven      | "delete this exact set, I already know"   |

Both write to the same `archive_records` table; the History tab and
Restore flow are shared.

## Three intake modes

1. **Downstream** — pick a root (Worksheet / Table / Model / View),
   delete every dependent. The root itself is never included.
2. **From Tag** — pick a tag, delete every object that carries it.
   System-User-owned content is filtered. Has a sub-mode:
   *Delete tag only* (the label is removed; objects stay).
3. **From List** — paste GUIDs (newline or comma) or upload a CSV.
   Cache hits are deleted; misses are reported as "unrecognized".

## Backend flow

```
                    ┌─────────────────────────────────┐
   /resolve/X    ──▶│  deleter_service.resolve_X()    │──▶ list of GUIDs
                    └─────────────────────────────────┘
                                                       │
   /dryrun       ──▶ create Job(bulk_delete_dryrun) ──▶│
                                                       ▼
                    ┌─────────────────────────────────┐
                    │  deletion_service.dryrun()      │ permissions + dependents
                    └─────────────────────────────────┘
                                                       │
                                                       ▼ Job.result {summary}
   modal polls GET /jobs/{id} every 2s ─────────────  ┘

   user types "DELETE", clicks Confirm
   /execute      ──▶ create Job(bulk_delete) ─────────┐
                                                       ▼
                    ┌─────────────────────────────────┐
                    │ deletion_service._execute_delete│
                    │   A. TML export (chunks of 50)  │
                    │   B. delete_metadata (cancel-aware)│
                    │   C. AuditLog + Job.complete    │
                    └─────────────────────────────────┘
                                                       │
                                                       ▼ archive_records rows
   History tab pulls from /archiver/records ───────  ┘ (shared with Archiver)
```

## Endpoints

| Method | Path                              | Purpose                                    |
|--------|-----------------------------------|--------------------------------------------|
| POST   | /api/v1/deleter/resolve/downstream | Root → dependents (TS API call)           |
| POST   | /api/v1/deleter/resolve/tag        | Tag name → cached rows                    |
| POST   | /api/v1/deleter/resolve/list       | GUID list → cached rows + unrecognized    |
| GET    | /api/v1/deleter/tags               | Distinct cached tag names (picker)        |
| GET    | /api/v1/deleter/roots/search       | Autocomplete for Downstream picker        |
| POST   | /api/v1/deleter/dryrun             | Start impact-check job (bulk_delete_dryrun)|
| GET    | /api/v1/deleter/dryrun/{id}/objects | Paginated objects for the modal grid     |
| POST   | /api/v1/deleter/execute            | Start TML-backup-then-delete (bulk_delete)|
| POST   | /api/v1/deleter/delete-tag-only    | Delete the tag itself; objects untouched  |

## Files of interest

**Backend:**
- [ts_admin/api/deleter.py](../../../../ts_admin/api/deleter.py) — router, schemas
- [ts_admin/services/deleter_service.py](../../../../ts_admin/services/deleter_service.py) — three resolve modes + tag-only
- [ts_admin/services/deletion_service.py](../../../../ts_admin/services/deletion_service.py) — shared dryrun + `_execute_delete` (also used by Archiver)
- [ts_admin/ts_client/client.py](../../../../ts_admin/ts_client/client.py) — `delete_metadata`, `delete_tag`, `fetch_dependents`

**Frontend:**
- [frontend/pages/deleter.tsx](../../../../frontend/pages/deleter.tsx) — page with mode tabs and Delete | History
- [frontend/components/DeleterIntake/](../../../../frontend/components/DeleterIntake/) — DownstreamPicker, TagPicker, ListPaste
- [frontend/components/Deleter/DryRunModal.tsx](../../../../frontend/components/Deleter/DryRunModal.tsx) — shared with Archiver
- [frontend/components/Deleter/HistoryTab.tsx](../../../../frontend/components/Deleter/HistoryTab.tsx) — shared deletion history
- [frontend/components/Deleter/columns.ts](../../../../frontend/components/Deleter/columns.ts) — shared metadata-grid columns

## Safety guarantees

- **Dry-run is mandatory** before delete. Run as a background job;
  reports per-object dependents and shared-access principals.
- **TML export before delete** (Phase A in `_execute_delete`).
  Objects whose TML export fails are *never* deleted — they stay in
  TS, and the corresponding `archive_records` row is marked FAILED.
- **Typed `DELETE` confirmation** in the modal — the red Delete
  button stays disabled until the user types it.
- **System User exclusion** at every resolve point — built-in content
  can never be picked.
- **Audit log** per execute (`action_type = "bulk_delete"`) and per
  tag-only delete (`action_type = "bulk_delete_tag"`).
- **Restore from History** works for bulk-deleted items just like
  archived items — same `archive_records` table, same TML files.

## What's deferred

- A `pre-flight backup ZIP download` from inside the dry-run modal
  (Plan Option B). v1 keeps Archiver's pattern: TML during execute,
  per-object download from History.
- Deleting the tag *and* the objects in one click. Today the user
  picks one or the other in the From Tag mode.
- Multi-cluster fan-out. Each delete is scoped to the active cluster
  + org (matches Archiver).
