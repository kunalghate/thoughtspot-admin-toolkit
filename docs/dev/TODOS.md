# TODOS — Deferred Work

Items explicitly considered and deferred from v1. Each has a priority, effort estimate,
and the context needed to pick it up later.

---

## P2 — High value, next phase

### Cascade-delete cached data when an org is removed

**What:** When `GET /clusters/{id}/orgs` fetches a fresh org list and detects that
one or more org IDs are no longer present, delete the corresponding rows from
`ts_metadata`, `ts_groups`, `ts_tags`, and `ts_users` (org-scoped memberships) for
those org IDs.

**Why:** The current delete-before-insert for `ts_orgs` leaves orphaned rows in all
org-scoped cache tables when an org is deleted in ThoughtSpot. The rows are not
surfaced in any UI (the org no longer appears in the dropdown), but they consume
disk space indefinitely.

**How to implement:**
- In `list_cluster_orgs` (or a helper), diff the incoming org IDs against the
  current `ts_orgs` rows for the cluster before replacing them
- For each removed `ts_org_id`, issue DELETE WHERE `cluster_id = ? AND org_id = ?`
  on `ts_metadata`, `ts_groups`, `ts_tags`, and `user_org_memberships`

**Effort:** S | **Priority:** P2 | **Depends on:** Nothing

---


### PIN/passphrase protection for localhost

**What:** An optional PIN or passphrase required to access the app at localhost.

**Why:** Currently, anyone with access to the machine can reach `http://localhost:8080`
without any authentication. On shared machines or servers with SSH access, this
means any local user has full ThoughtSpot admin access through the toolkit.

**How to implement:**
- Add an optional `pin` field to the cluster/app config
- If set, the FastAPI app serves a PIN entry page before the main app
- PIN stored as a bcrypt hash in the config (never plaintext)
- Session cookie set after successful PIN entry, expires after 8 hours of inactivity

**Pros:** Closes a real security gap for shared machines.
**Cons:** Friction for solo admins on their own laptops (can be disabled in config).
**Effort:** S | **Priority:** P2 | **Depends on:** Basic app working in v1

---

### Scheduled / cron workflows

**What:** Allow admins to schedule any operation to run automatically on a cron schedule.
Example: "Run the archiver every Sunday at midnight and email me a summary."

**Why:** Transforms the app from a manual tool into an autonomous governance platform.
Admins shouldn't have to remember to clean up stale content — the app should do it.

**How to implement:**
- Add APScheduler as a dependency
- Schedule configuration UI in Settings → Schedules
- Each schedule specifies: operation type, parameters, cron expression, notification target
- Scheduled job results appear in the Job History page
- Notifications: see Slack/email TODO below

**Pros:** 10x value for governance use cases. Differentiates from CS Tools significantly.
**Cons:** Adds APScheduler dependency. Requires notification system. Significant UI work.
**Effort:** L | **Priority:** P2 | **Depends on:** All MVP features stable, notification system

---

## P3 — Future phase

### Slack / email notifications on job completion

**What:** Send a Slack message or email when a background job completes (or fails).

**Why:** Admin triggers a long-running job (bulk share 500 objects), closes the app,
and wants to know when it finishes without having to reopen and check.

**How to implement:**
- Settings → Notifications: configure Slack webhook URL and/or SMTP
- Each job has a "notify on complete" toggle
- Notification sent on job completion with: operation type, duration, success/fail count
- Scheduled jobs always notify (see scheduling TODO above)

**Pros:** Makes the app feel like a production system.
**Cons:** Requires external service configuration. Adds SMTP/Slack complexity.
**Effort:** M | **Priority:** P3 | **Depends on:** Background job system (v1)

---

## Future phases (v2+)

### Multi-user deployment (auth layer)

**What:** Support a shared team deployment where multiple admins log in with
individual accounts. Requires: login screen, session management, per-user audit log,
potentially per-user TS credentials or a shared service account.

**Why:** Teams of admins want to use one deployed instance rather than each installing
their own.

**Architecture note:** The auth layer is designed as pluggable middleware in v1.
The v1 implementation is "allow all" (localhost = trusted). Multi-user adds a real
auth implementation without changing any other code.

**Effort:** XL | **Priority:** P2 (for teams) | **Depends on:** v1 stable

---

### Auto-sync background thread

**What:** Automatically refresh the local cache in the background on a configurable
interval (default: 30 minutes), rather than requiring manual refresh.

**Why:** Admins don't want to think about data freshness. The UI should always show
reasonably current data without manual intervention.

**Architecture note:** Implemented as a background thread that wakes up every N minutes,
checks `sync_log` for stale entity types, and syncs them. Must handle: sync fails,
TS unreachable, concurrent sync + user operation.

**Effort:** M | **Priority:** P2 | **Depends on:** Per-entity sync system (v1)

---

### Postgres support

**What:** Support Postgres as an alternative to SQLite, for multi-user shared deployments.

**Why:** SQLite has a single-writer limitation. For a shared team deployment with
concurrent users, Postgres provides proper concurrent access.

**Architecture note:** SQLModel (SQLAlchemy) is used in v1. Switching to Postgres
is a connection string config change + testing effort. No code changes required.

**Effort:** M | **Priority:** P3 | **Depends on:** Multi-user auth layer

---

### Workflow builder

**What:** A UI for chaining multiple operations into a sequence.
Example: "Offboard user → transfer their content → remove from all groups → deactivate account"
as a single named workflow that can be saved and reused.

**Why:** Many admin tasks are multi-step sequences. Doing them manually through
separate pages introduces errors and takes time.

**Effort:** XL | **Priority:** P3 | **Depends on:** All individual operations stable
