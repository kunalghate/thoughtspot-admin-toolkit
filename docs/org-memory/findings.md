# Findings ledger

**Findings that are real but not yet committed to.** A discovery run may file
only as many `BACKLOG.md` rows as the org has closed since the previous
discovery run (the Balance rules in
[.claude/skills/improve-cycle/SKILL.md](../../.claude/skills/improve-cycle/SKILL.md)).
Surplus CONFIRMED findings land here instead of in the queue.

Why the split: "we found this" and "we committed to fixing this" were the same
list, so the list could only grow — 5 hunter lenses file in parallel while a fix
cycle closes one row. This file absorbs the difference without losing anything.

## Rules

- **Nothing is lost.** A finding here is as real as a backlog row; it is just
  not queued.
- **Only a human promotes.** A cycle may append here and may mark an entry
  `promoted` / `stale`, but only a human moves an entry into `BACKLOG.md`.
  Promotion copies the entry verbatim and gives it an ID.
- **Same evidence bar as a row.** Every entry carries a failure scenario, the
  file:line evidence, and drafted acceptance criteria written against `main` —
  so promotion is a copy, never a re-investigation.
- **Re-verify before promoting.** Findings go stale; the code moves. State the
  commit the finding was measured against.
- **Duplicates die here, not in the queue.** Dedupe a new finding against this
  file *and* `BACKLOG.md` before appending.

## Entry format

```
### <short title>
`CONFIRMED` · measured against `<commit>` on <date> · lens: <correctness|security|…>
**Failure scenario:** …
**Evidence:** file:line …
**Drafted acceptance criteria:** …
**Status:** unpromoted | promoted (→ S99) | stale (<why>)
```

## Findings

### A UI-side safety gate on a destructive path has zero CI enforcement
`CONFIRMED` · measured against `177d913` on 2026-09-08 · lens: security

**Failure scenario:** S31's delete-button gate lives entirely in a React
`disabled` expression. A future agent refactors `DeleteUsersModal`, drops or
inverts the acknowledgement clause, and every gate CI actually runs stays green
— the PR merges and the Delete button re-enables on a typed `DELETE` alone
against a truncated cache, silently restoring the exact S31 fail-open.

**Evidence:** Mutation run in a worktree: deleting the
`(!cacheAuthoritative && !confirmStaleCache)` clause from the `disabled`
expression in `frontend/components/Users/DeleteUsersModal.tsx` turns `vitest`
red but leaves `npx tsc --noEmit` at **exit 0** and `next build` green.
`.github/workflows/ci.yml`'s frontend job runs exactly `npm ci` →
`npx tsc --noEmit` → `npm run build`; `grep -c "vitest\|npm test"` on it returns
**0**. `frontend/tsconfig.json` sets no `noUnusedLocals`, so the orphaned
`confirmStaleCache` state does not even warn. This is M12/S1 landing on a
destructive path for the first time.

**Drafted acceptance criteria:** Either CI runs `npm test` (closing M12/S1, which
touches the protected `.github/workflows/*` and so needs `human-approved`), or
any UI-side gate on a destructive path is additionally expressed as a
type-level construct or a backend assertion that a CI-run gate can see; a
mutation of the gate is demonstrated red under a check CI actually executes.

**Status:** unpromoted

### preview_delete is O(users x org_objects) on the event loop, with no owner index
`CONFIRMED` · measured against `177d913` on 2026-09-08 · lens: performance

**Failure scenario:** An admin dry-runs a 500-user delete on a cluster with a
16,000-object org. `preview_delete` issues one `count(distinct)` per user, each
scanning every row in the org, and the whole loop runs synchronously on the
FastAPI event loop via the inline-awaited background task (S26). All HTTP
handling freezes for ~3.4 s — including the job-status polling this very modal
uses to render its own result, and `/health`.

**Evidence:** Measured on a seeded scratch DB (`ts_metadata` 47,840 rows; 16,000
in org 0): **2.02 ms per user** at 16k org rows, **3,420 ms** for
`preview_delete(500 users)`. `ix_ts_metadata_cluster_org_guid` is
`(cluster_id, org_id, ts_guid)` — `owner_guid` is not in it, so each per-user
count scans the org. Both the N-query loop and the missing index predate S31;
S31 only makes the path more visible. Note the S6 fact that `create_all` will
not add an index to an existing table.

**Drafted acceptance criteria:** `preview_delete` resolves all selected users'
owned-object counts in a single grouped query (`GROUP BY owner_guid` with
`owner_guid IN (...)`) rather than N counts; a composite
`(cluster_id, org_id, owner_guid)` index exists and is added via a
`_BACKFILL_INDEXES` entry so existing installs get it; the blocking section runs
off the event loop or the row is explicitly deferred to S26; an `EXPLAIN QUERY
PLAN` on a realistically-sized DB is recorded showing the index is used.

**Status:** unpromoted

### /users/delete/preview ships metadata_cache_authoritative permanently false
`CONFIRMED` · measured against `56dd37a` on 2026-09-08 · lens: correctness

**Failure scenario:** `DeletePreviewRequest` carries no `org_id`, so
`delete_preview` calls the service with `org_id=None`; the service correctly
refuses to certify a cluster-wide count from one org's marker and hardcodes
`False`. The endpoint's `metadata_cache_authoritative` is therefore a
compile-time constant `False` for every caller, forever. Any future consumer that
treats it as a real signal gets a permanent false alarm — or "fixes" the guard
and gets a fail-open the day a NULL-org/cluster-wide `sync_log` writer exists.

**Evidence:** `ts_admin/api/users.py` `DeletePreviewRequest` (no `org_id` field)
and `delete_preview` (never passes one); the guard at
`ts_admin/services/user_management_service.py`. `usersApi.deletePreview`
(`frontend/lib/api.ts:619`) has **no call sites**, so the endpoint is UI-dead
today and nothing consumes the constant. `sync_log.org_id` is `NOT NULL`
(`ts_admin/models/sync_log.py:16`), so the NULL-org row the guard defends against
cannot currently be seeded at all.

**Drafted acceptance criteria:** Either `DeletePreviewRequest` gains
`org_id: int = 0` (mirroring `DeleteDryRunRequest`) so the endpoint's flag is a
real signal and the count is org-scoped rather than cluster-wide, or the field is
dropped from `DeletePreviewResponse` and the endpoint documented as
non-certifying; a test asserts the chosen contract and fails if the flag silently
becomes constant again.

**Status:** unpromoted

### get_user_detail's owned_object_count has the same uncertified-cache hole
`CONFIRMED` · measured against `177d913` on 2026-09-08 · lens: research/correctness

**Failure scenario:** Identical absence-as-evidence shape to S31, one endpoint
over. `get_user_detail` counts `CachedMetadata` rows by `owner_guid` with no
completeness check and returns `owned_object_count`, rendered as a bare
`<Stat label="Owned objects">` in the user detail drawer. On a truncated or
never-synced metadata cache an admin reads "Owned objects: 0" for a user who owns
40 worksheets, and makes an offboarding decision on it. It is a read path, so the
posture is flag (not refuse), exactly as S31 concluded.

**Evidence:** `ts_admin/services/user_management_service.py` `get_user_detail`
(count block, emitted as `owned_object_count`);
`frontend/components/Users/UserDetailDrawer.tsx:221`. Deliberately left out of
the S31 diff to keep a destructive-path change tightly scoped.

**Drafted acceptance criteria:** `get_user_detail` returns the same
`metadata_cache_authoritative` signal S31 established, and the drawer presents
the owned-objects stat as uncertified (not as a bare number) when it is false; a
test reads the same user twice over one seed — certified giving a non-zero count,
then truncated giving 0 with the flag false — so the fixture is proven
non-vacuous.

**Status:** unpromoted

### preview_delete's read-only-session invariant is enforced by comment only
`CONFIRMED` · measured against `bbf3422` on 2026-09-08 · lens: correctness

**Failure scenario:** S31's completeness flag compares a sync marker's identity
before and after the count loop, in one session. Its soundness rests on the
session staying **read-only**: pysqlite opens no transaction for SELECT-only
work, which is exactly why the after-read observes another connection's commit.
The moment any future edit adds DML before the after-read, a real transaction
opens, the snapshot freezes, and `marker_after` silently degrades into a re-read
of `marker_before` — restoring the fail-open S31 exists to close. **No test goes
red**, and the unit fixture is structurally incapable of catching it: it uses
`poolclass=StaticPool`, a single shared DBAPI connection, so it cannot exercise
cross-connection visibility at all.

**Evidence:** the invariant comment at
`ts_admin/services/user_management_service.py:1053-1061`; the StaticPool fixture
in `tests/unit/test_stale_cache_guard.py`. Same class as the sibling surviving
mutant: deleting `session.expire_all()` (`:971`) kills no test either, because
the marker row is only weakly referenced and GC makes the re-read fresh by
timing. Both mechanisms that make the guard sound are ones no test can go red on.
Measured by the review lens on a file-backed two-connection repro:
`hold_strong=True, expire=False -> before=2020 after=2020 certified=True`
(degrades to a presence check).

**Drafted acceptance criteria:** The read-only invariant is enforced
mechanically rather than by comment — either `marker_after` is read on a fresh
short-lived `Session` (removing the dependency entirely), or a
`@event.listens_for(session, "after_flush")` hook raises if the preview session
ever flushes DML; a test demonstrates the enforcement red-then-green by adding a
write to that session. If neither is adopted, at minimum a non-StaticPool
(file-backed, two-connection) fixture covers the before/after comparison so the
class of error becomes observable.

**Status:** unpromoted
### The share activity entry reports ATTEMPTED objects, not REQUESTED ones
`CONFIRMED` · measured against `364f671` on 2026-09-09 · lens: correctness

**Failure scenario:** An admin selects 100 objects to share with a group. 40 of
the GUIDs are skipped before any share call is made, so the job is marked
PARTIAL and **no `ShareRecord` row exists for them at all**. The dashboard feed
groups `ShareRecord` rows and renders "Updated sharing on 60 objects for 1
principal" — a denominator the admin never asked for, with nothing on screen
saying 40 were dropped. The delete feed does not have this problem:
`ArchiveRecord` gets one row per REQUESTED GUID, so its counts are a true
denominator. Even with the M14 round-4 terminal-status fix (which correctly
degrades that entry to PARTIAL), the *count* remains silently narrower than the
request — the entry says PARTIAL about 60 objects while the request was 100.

**Evidence:** one `ArchiveRecord` per requested GUID at
`ts_admin/services/deletion_service.py:352-374`; skipped GUIDs mark the job
PARTIAL with no share row written at
`ts_admin/services/bulk_sharing_service.py:915`; the feed's counts are
`COUNT(DISTINCT ShareRecord.object_guid)` /
`COUNT(DISTINCT ShareRecord.principal_guid)` in
`ts_admin/services/dashboard_service.py` (share aggregate).

**Drafted acceptance criteria:** The share feed entry distinguishes requested
from attempted — either it names the skipped count ("Updated sharing on 60 of
100 objects; 40 skipped") or a row is written for every requested pair with a
`SKIPPED` status so the denominator is recoverable from the table; a test seeds
a session with skipped GUIDs and asserts the rendered label is not narrower than
the request.

**Status:** unpromoted

### The two new dashboard aggregates bind ix_*_cluster_id only and post-filter org_id
`CONFIRMED` · measured against `364f671` on 2026-09-09 · lens: performance

**Failure scenario:** The dashboard's archive and share activity aggregates are
the S25 pathology on `archive_records` / `share_records`: only single-column
indexes exist, so `EXPLAIN QUERY PLAN` binds `cluster_id=?` and post-filters
`org_id`, then builds temp b-trees for the GROUP BY, the `COUNT(DISTINCT …)`
pair and the ORDER BY. Measured on a 50k+50k-row synthetic DB: **148 ms and
76 ms median** — on **every dashboard poll**, and on the **event loop** (nothing
in this path uses `to_thread`), so the whole app stalls for ~0.2 s per poll.
`.limit(50)` bounds only what Python renders, not what SQLite reads, so the cost
grows with total table size rather than with the 30-day window the feature
advertises.

**Evidence:** the archive and share aggregates in
`ts_admin/services/dashboard_service.py` (`_ACTIVITY_SCAN_SESSIONS` limit, the
`HAVING max(executed_at) >= cutoff` clause); indexes on
`ts_admin/models/archive_record.py` and `ts_admin/models/share_record.py`
(`cluster_id` / `org_id` single-column only). The 30-day cutoff must STAY in
`HAVING` — moving it to `WHERE` would filter rows before grouping and break the
exact-count property those aggregates were rewritten to provide — so a
`(cluster_id, org_id)` composite index is the safe lever, not a predicate move.
Note `create_all` does NOT add an index to an already-existing table (2026-08-14
S6 fact), so this needs explicit `CREATE INDEX IF NOT EXISTS` DDL in `init_db()`
or existing user DBs keep the old plan.

**Drafted acceptance criteria:** A `(cluster_id, org_id)` composite index exists
on both tables and is created for pre-existing databases, the query plan for
both aggregates binds it instead of `ix_*_cluster_id`, and the measured cost of
the dashboard aggregate on a 100k-row DB is bounded (single-digit ms) rather
than scaling with table size.

**Status:** unpromoted
