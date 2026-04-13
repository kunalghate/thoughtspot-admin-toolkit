# Content Archiver — How It Works

## What Is the Archiver?

The Content Archiver helps ThoughtSpot administrators identify and remove stale Answers and
Liveboards. It is a web-based replacement for the `cs_tools archiver` CLI tool. It preserves
the same core workflow but adds a guided UI, real-time feedback, dependency awareness, and a
full restore path — things the CLI lacks entirely.

**The guiding principle:** make accidental deletion impossible, and make recovery trivial if it
happens anyway.

---

## Staleness Definition

An object is considered **stale** when both of the following are true simultaneously:

```
last_accessed_at < (now - stale_activity_days)   ← no views in N days
AND
modified_at      < (now - stale_modified_days)    ← no edits in M days
```

- A `NULL` value on either field counts as exceeding the threshold (never viewed / never edited)
- Both thresholds default to **90 days**
- Both are independently configurable in the UI

**Why both conditions?** An object someone recently edited — even if never viewed — is
probably actively maintained. Requiring both thresholds avoids flagging content a developer
is actively working on but hasn't published yet.

---

## Data Sources

| Data | Source | When updated |
|---|---|---|
| Object name, type, owner | `CachedMetadata` SQLite table | Background sync |
| `last_accessed_at` | `CachedMetadata.last_accessed_at` | Background sync |
| `modified_at` | `CachedMetadata.modified_at` | Background sync |
| `tag_names` | `CachedMetadata.tag_names` (JSON) | Sync + after tag ops |
| Who has access | ThoughtSpot REST API — live | Dry-run only |
| What depends on objects | ThoughtSpot REST API — live | Dry-run only |
| Deleted object snapshots | `ArchiveRecord` SQLite table | Written at deletion time |
| TML backup files | `~/.ts-admin/tml-exports/{job_id}/` | Written at deletion time |

All filtering and counting reads from the local SQLite cache (fast, no TS calls).
Live TS API calls happen only during dry-run and execution.

---

## The Workflow

### Step 1 — Set Criteria

Admin sets thresholds and optional filters in the left panel:

| Filter | Description |
|---|---|
| Unused for at least N days | `last_accessed_at` older than N days (or NULL) |
| Unedited for at least M days | `modified_at` older than M days (or NULL) |
| Content type | LIVEBOARD, ANSWER, or both |
| Exclude tagged | Skip objects that already have specific tags (e.g. `Keep`) |
| Owner filter | Restrict to objects owned by a specific user |
| Exclude owners | Skip objects owned by these users |

A **live count badge** updates as criteria change (debounced 300ms, SQLite only — instant).

---

### Step 2 — Review Results

The results grid shows all matching stale objects:

| Column | Notes |
|---|---|
| Name | Object display name |
| Type | LIVEBOARD or ANSWER |
| Owner | Author display name |
| Last accessed | `last_accessed_at` date, or "Never" |
| Days unused | `(today - last_accessed_at).days`, fallback to days since creation |
| Tags | Existing tags on the object |
| Actions | Per-row "Untag" button (removes stale tag if already applied) |

Admin selects the objects they want to act on (checkbox per row, or select all).

**Export CSV** downloads the full result set for sharing with team leads or content owners
before taking action.

---

### Step 3 — Dry-Run (Full Impact Check — No Cap, Runs as Background Job)

Before any destructive action, admin clicks **"Delete selected"**. The system fires a
background job that runs a **complete impact assessment** on every single selected object
— no sampling, no cap. The HTTP call returns a `job_id` immediately; the frontend polls
every 2 seconds, showing a spinner while the job runs.

#### 3a. Permission check (who has access?)
- Calls `POST /api/rest/2.0/security/metadata/fetch-permissions` for every object
- Uses `asyncio.gather` with a concurrency semaphore of 10 parallel requests and
  `return_exceptions=True` — one failed object does not abort the rest
  (e.g. 300 objects / 10 concurrent ≈ 4-5s total)
- Aggregates: `shared_count` (objects shared with anyone), `affected_principals`
  (deduplicated list of users/groups who will lose access)

#### 3b. Dependency check (what depends on these objects?)
- Calls `POST /api/rest/2.0/dependency/listdependents` for the full batch
- Surfaces objects that have active dependents elsewhere in the cluster
- Example: an Answer pinned to a Liveboard NOT in the delete list
- Objects with live dependents are flagged as **HIGH RISK** in the modal

When the job completes, the modal transitions to the **ready** state and shows:
- **By-type breakdown** — total count, Liveboards vs Answers
- **Shared content warning** — N objects will revoke access for listed users/groups
- **Dependency warning** — M objects have active dependents that will break (listed by name)
- **Object list** — paginated grid loaded lazily from the job's stored object list
- **Confirmation gate** — admin must type `DELETE` to unlock the confirm button

No changes are made during this step. If dry-run has not completed, the confirm button
is disabled.

---

### Step 4 — Execute

Admin types `DELETE` and confirms. The operation runs as a **background job**:

**Pre-delete (mandatory, cannot be skipped):**
1. Export TML for every selected object via `POST /api/rest/2.0/metadata/tml/export`
2. Save each TML file to `~/.ts-admin/tml-exports/{job_id}/{guid}.tml`
3. If TML export fails for any object, that object is **removed from the delete batch**
   and logged as a failure — the job continues for the rest
4. Write a `ArchiveRecord` row per object with full metadata snapshot + TML path

**Deletion:**
5. Group objects by type (delete API requires homogeneous type per call)
6. `DELETE /api/rest/2.0/metadata/delete` in chunks of 50
7. Update progress after each chunk
8. Remove deleted objects from `CachedMetadata` SQLite table
9. Write `AuditLog` entry (action_type="delete", full object list in parameters)
10. `mark_complete({ succeeded, failed, tml_export_path, archive_session_id })`

Frontend polls `GET /api/v1/jobs/{job_id}` every 2 seconds. On completion, a toast
shows the result and links to the Archive History panel for restore access.

---

## Archive History & Restore

Every delete operation creates a permanent, queryable record. Admins can:

1. Browse the **Archive History** tab — a paginated list of past archive sessions
2. Drill into a session to see every object that was deleted (name, type, owner,
   deleted_at, TML file path)
3. Select individual objects and click **"Restore"**

**Restore flow:**
1. Reads the `.tml` file from `~/.ts-admin/tml-exports/{job_id}/{guid}.tml`
2. Calls `POST /api/rest/2.0/metadata/tml/import` with `import_policy: "PARTIAL"`
   (batched in groups of 10 TML strings per call)
3. On success: ThoughtSpot creates the object with a **new GUID** — the original GUID
   is permanently gone. The new GUID is stored as `ArchiveRecord.restored_as_guid`
   and re-inserted into the local SQLite cache so the toolkit tracks the restored object.
   The history panel shows both the original GUID and the new one.
4. On failure: surfaces the TML import error to the admin

If the TML file is missing (e.g. disk was cleared), the restore UI shows a warning and
provides the admin with the object metadata (name, type, owner) so they can recreate it
manually.

---

## Actions

### Tag as Stale

Applies a tag (default: `INACTIVE`) to selected objects in ThoughtSpot. Recommended
first step before deletion — gives content owners visibility that their content is
flagged.

- Uses `POST /api/rest/2.0/tags/assign`
- Auto-creates the tag if it doesn't exist (`POST /api/rest/2.0/tags`)
- Updates `tag_names` in SQLite cache after completion
- Writes `AuditLog` entry

**Recommended workflow:** Tag → notify owners → wait 2 weeks → delete what remains tagged.

### Untag

Removes a stale tag from selected objects. Allows admins to honor user requests to
keep their content.

- Uses `POST /api/rest/2.0/tags/unassign`
- Available as bulk action (selection bar) or per-row button
- Updates SQLite cache
- Writes `AuditLog` entry

### Delete (Irreversible — with mandatory safety net)

Permanently deletes selected objects from ThoughtSpot.

- **TML export is always performed first** — not optional, cannot be skipped
- **Dry-run must complete** on all objects before confirm button unlocks
- **`DELETE` must be typed** in the confirmation modal
- Objects for which TML export fails are skipped (not deleted), logged separately
- All deleted objects are recorded in `ArchiveRecord` table
- TML files kept at `~/.ts-admin/tml-exports/{job_id}/`
- Restoring is available from Archive History at any time

---

## ThoughtSpot REST API Calls

| Operation | Endpoint | When |
|---|---|---|
| Identify stale objects | SQLite only — no TS call | Criteria/results step |
| Permission check | `POST /api/rest/2.0/security/metadata/fetch-permissions` | Dry-run — ALL objects |
| Dependency check | `POST /api/rest/2.0/dependency/listdependents` | Dry-run — ALL objects |
| Apply tag | `POST /api/rest/2.0/tags/assign` | Tag action (chunks of 50) |
| Remove tag | `POST /api/rest/2.0/tags/unassign` | Untag action (chunks of 50) |
| Create tag | `POST /api/rest/2.0/tags` | Tag action — if tag absent |
| List tags | `POST /api/rest/2.0/tags/search` | Tag autocomplete |
| Export TML | `POST /api/rest/2.0/metadata/tml/export` | Mandatory pre-delete |
| Delete objects | `DELETE /api/rest/2.0/metadata/delete` | Execute — after TML export |
| Restore TML | `POST /api/rest/2.0/metadata/tml/import` | Restore action |

---

## Safety Rules

- **No cap on dry-run.** Every object in the selection gets a full permission + dependency
  check. If this is slow, the UI shows a progress spinner — it does not skip objects.
- **TML export before every delete.** Objects for which export fails are not deleted.
- **Dry-run required.** The confirm button is disabled until dry-run completes.
- **`DELETE` typed confirmation.** Prevents accidental click-through on large batches.
- **Audit log on every write.** tag, untag, and delete each produce an `AuditLog` row.
- **`ArchiveRecord` per deleted object.** Full snapshot stored — name, type, owner, timestamps.
- **Restore always available.** Archive History lets admins recover any deleted object as
  long as the TML file exists on disk.
- **System-owned objects excluded.** `tsadmin`, `system` accounts never appear in results.

---

## How This Differs from cs_tools Archiver

| cs_tools | This toolkit |
|---|---|
| 3 separate CLI commands | Single guided web workflow |
| Same threshold for view + edit | Two independent sliders |
| `--dry-run` prints to terminal | Full modal: permissions + dependency check on all objects |
| Dry-run capped / sampled | No cap — every selected object is checked |
| No dependency awareness | Dependency check flags objects with active dependents |
| CSV via `--syncer` plugin | Built-in CSV export button |
| Blocks terminal during deletion | Background job with progress bar |
| TML export optional (`--directory`) | TML export mandatory — objects not exported are not deleted |
| No restore UI | Archive History tab with per-object restore |
| No audit trail | `AuditLog` + `ArchiveRecord` per deleted object |
| No typed confirmation | Must type `DELETE` to confirm bulk deletion |
| Single cluster | Multi-cluster via `cluster_id` FK |
