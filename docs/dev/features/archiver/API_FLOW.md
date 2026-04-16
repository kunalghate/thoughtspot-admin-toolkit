# Content Archiver — API Flow

User-journey map of every call the Archiver makes. Endpoints all under
`/api/v1/archiver/*`; job polling uses the shared `GET /api/v1/jobs/{job_id}`
(2-second interval).

## Endpoint cheatsheet

| Endpoint | Purpose |
|---|---|
| `GET /preview` | Count stale objects for badge (SQLite, instant) |
| `GET /results` | Paginated stale-object grid rows |
| `GET /tags` | Tag names on stale objects (filter dropdown) |
| `POST /execute` | Async: tag / untag / delete. Returns `{job_id}` |
| `POST /dryrun` | Async: permissions + dependency impact. Returns `{job_id}` |
| `GET /dryrun/{job_id}/objects` | Paginated objects in a dry-run |
| `POST /restore` | Async: re-import deleted objects from TML |
| `GET /records` | Paginated history of deleted objects |
| `GET /history` / `/history/{job_id}` | Archive sessions, session detail |
| `GET /download/{record_id}` | Download TML backup (direct link) |

## Journeys

### 1. Land on `/archiver`

Fires on mount, in parallel:

1. `GET /tags` — populate tag filter dropdown.
2. `GET /preview` — count badge.
3. `GET /results` — first page of grid (AG Grid infinite datasource).

### 2. Change stale criteria (slider / AND·OR toggle)

Debounced 300 ms, then:

1. `GET /tags` — refresh available tags for new criteria.
2. `GET /preview` — refresh count badge.
3. `GET /results` — grid datasource purged and reloaded.

### 3. Apply column filter (type / tag / name search)

Filter state is local; no call until the grid asks for rows.

1. `GET /results` — datasource purged, reloads with filter params merged in.

### 4. Tag selected rows

1. `POST /execute` — `{action: "tag", object_ids, tag_name, create_tag_if_missing: true}` → `{job_id}`.
2. Poll `GET /jobs/{job_id}` every 2 s until `COMPLETE / PARTIAL / FAILED`.
3. `GET /results` — reload grid.

Untag is the same with `action: "untag"`.

### 5. Delete selected rows (via Dry-run modal — the default path)

1. `POST /dryrun` — `{object_ids}` → `{job_id}`.
2. Poll `GET /jobs/{job_id}` until dry-run complete (returns impact summary).
3. `GET /dryrun/{job_id}/objects` — populate modal grid.
4. User types `DELETE` to confirm → `POST /execute` `{action: "delete", object_ids}` → `{job_id}`.
5. Poll `GET /jobs/{job_id}` until delete complete.
6. `GET /results` — reload grid.

Delete backend order: TML export → metadata delete → audit. Objects that fail
TML export are not deleted.

### 6. Switch to History tab

1. `GET /records` — paginated list of deleted objects.

(Optional: `GET /history` for session view, `GET /history/{job_id}` for one session.)

### 7. Restore an archived record ( Hidden/Roadmap )

1. `POST /restore` — `{archive_record_ids}` → `{job_id}`.
2. Poll `GET /jobs/{job_id}` until complete. Restored objects get **new GUIDs**.
3. `GET /records` — reload history grid.

### 8. Download TML

1. `GET /download/{record_id}?cluster_id=…` — direct browser download, no polling.

## Notes

- **Debounce:** criteria changes debounced 300 ms before any refetch.
- **Polling:** all async jobs (execute, dryrun, restore) polled at 2 s.
- **Grid reloads:** any mutation (tag / delete / restore) purges the AG Grid infinite datasource so the next scroll refetches `/results` or `/records`.
- **Source of truth:** `/preview`, `/results`, `/tags`, `/records` all read SQLite cache; writes go to ThoughtSpot live and mirror back to cache.
