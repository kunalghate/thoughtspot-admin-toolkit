# ThoughtSpot Admin Toolkit

> **⚠️ Community tool — not a ThoughtSpot product.**
> This is **not** part of the ThoughtSpot product and is **not supported by the ThoughtSpot product team**. Built by CS/PS team and It is
> provided as-is, with no warranty and no SLA. Use at your own risk — and test
> against a non-production instance first.

A web application for ThoughtSpot administrators. Provides workflows and tools
for managing ThoughtSpot instances that go beyond what's available out of the box —
content governance, bulk operations, metadata exploration, and more.

Built as a modern alternative to the [CS Tools](https://thoughtspot.github.io/cs_tools/)
CLI, with a full web UI that any admin can use without needing Python or terminal knowledge.

---

## Support & expectations

- **Not a ThoughtSpot product.** No ThoughtSpot product, support, or engineering
  team owns, endorses, or maintains this toolkit. Do not open ThoughtSpot Support
  tickets for it.
- **Community-maintained.** Bugs, questions, and feature requests belong in this
  repository's GitHub issues. Fixes happen on a best-effort basis.
- **Built for advanced admins.** It performs bulk and destructive operations
  (delete, transfer ownership, bulk share) against a live instance. Every
  destructive action has a dry-run preview and TML backup, but you are
  responsible for what you run.
- **Uses only the public [ThoughtSpot REST API v2](https://developers.thoughtspot.com/docs/rest-api-getstarted).**
  It relies on no private or internal endpoints, but ThoughtSpot may change its
  APIs at any time without notice, which can break this tool.

---

## Features

| Feature | Status | Description |
|---|---|---|
| **Dashboard** | ✅ Available | Landing view — what needs attention, content totals, recent jobs and admin activity, and a cache-freshness strip showing when users, groups, metadata, and lineage each last synced. |
| **Metadata Explorer** | ✅ Available | Searchable, filterable grid of all content — owner, tags, last accessed, views. Every column funnel sends real server-side filters (name / owner / tag substring, date ranges, numeric ranges). Analyst Studio datasets are identified as their own type instead of being lumped in with tables. |
| **Content Archiver** | ✅ Available | Find stale content, tag or delete with mandatory TML backup, dry-run impact check, restore from History. Archive + History grids share the same full-column filter model. |
| **User Management** | ✅ Available | Search and filter users, transfer ownership, transfer sharing, bulk delete — every destructive action behind a dry-run preview. Click any row for a read-only audit drawer: group membership, effective privileges ("can do"), and a live on-demand permissions fetch ("can see"). |
| **Group Management** | ✅ Available | Read-only group browser — privileges, member users, and **who created each group** (which ThoughtSpot's own UI never shows), instance- and org-scoped. Writes stay in the ThoughtSpot UI for now. |
| **Bulk Sharing** | ✅ Available | Share content to users/groups in bulk, with an impact preview before anything is applied and a History tab of past runs. |
| **Lineage** | ✅ Available | Graph view of content dependencies plus a column-level lineage explorer — trace a model column back to its physical DB table/column and forward to everything that consumes it. Browse from a connection, a logical table, an answer, or a liveboard. |
| **Content Deleter** | ✅ Available | Targeted deletion by GUID or search, with dependency resolution and the same dry-run + TML-backup guarantees as the Archiver. |
| **Jobs** | ✅ Available | Every sync and bulk operation runs as a tracked background job with live progress and failure detail. |
| **Diagnostics** | ✅ Available | Tail the application log in-app and download a support bundle (logs + recent failed jobs + app info, no credentials) to send to support. |
| **Multi-instance** | ✅ Available | Manage multiple ThoughtSpot instances from one app, each with its own local cache. |
| **Update notifications** | ✅ Available | The app checks for new releases and shows an **Update available** pill in the top bar. Updating is a single command — see [Updating](#updating). It never updates itself silently. |

All data is cached locally — the app is fast, no waiting on ThoughtSpot API calls
on every page load. See [How sync works](docs/user/SYNC.md).

---

## Requirements

- A ThoughtSpot instance (Cloud or Software)
- Admin-level access (`ADMINISTRATION` privilege) on that instance

You do **not** need Python or Node.js. The installer below brings its own Python,
and the web UI is pre-built and bundled with the package.

---

## Install

**macOS / Linux** — paste this into Terminal:

```bash
curl -LsSf https://raw.githubusercontent.com/kunalghate/thoughtspot-admin-toolkit/main/install.sh | sh
```

**Windows** — paste this into PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/kunalghate/thoughtspot-admin-toolkit/main/install.ps1 | iex"
```

That is the whole install. It sets up an isolated environment, downloads a Python
for the toolkit if your machine doesn't have a suitable one, and installs
everything. It changes nothing else on your system.

To update later, see [Updating](#updating) — the app tells you when a new
version is available.

<details>
<summary>Already a Python user? Install it your own way</summary>

Releases are published as a wheel attached to each
[GitHub Release](https://github.com/kunalghate/thoughtspot-admin-toolkit/releases)
rather than to PyPI, so install from the release URL:

```bash
uv tool install https://github.com/kunalghate/thoughtspot-admin-toolkit/releases/latest/download/<wheel>
```

</details>

## Run

```bash
ts-admin-toolkit serve
```

Opens the app at `http://localhost:8080`.

On first launch, you will see a setup screen to connect your ThoughtSpot instance.
All configuration (URL, credentials, org) is done through the web UI — no CLI configuration required.

---

## Connecting to ThoughtSpot

On first launch, the app walks you through connecting to your ThoughtSpot instance:

1. Enter your ThoughtSpot URL (e.g. `https://company.thoughtspot.cloud`)
2. Enter your username
3. Choose your auth method: **Basic** (password), **Trusted Auth** (secret key), or **Bearer token**
4. Enter your credentials
5. Click **Test connection** — the app verifies live access before saving
6. Give this connection a name (e.g. `Production`, `Staging`)
7. Click **Save** — credentials are stored securely in your OS keychain

---

## Dashboard

The landing page, read entirely from the local cache — it renders instantly even
when the instance is unreachable.

- **Needs attention** — failed jobs, orphaned content, inactive users, empty
  groups, stale content. Each row links to the screen that fixes it. When there
  is nothing to act on, it says so.
- **Counts** — users, groups, content objects, archivable content. An entity that
  has never synced shows **—** and a Sync action rather than a misleading `0`.
- **Cache freshness** — when users, groups, metadata, and lineage each last
  synced. Syncs are lazy and independent, so every entity keeps its own clock: a
  green dot means synced in the last 24 hours, amber means older, grey means
  never. Hover a cell for the exact timestamp and a **Sync now** action. Only a
  *successful* sync moves the clock — a failed attempt leaves the cache as old
  as it was.
- **Recent jobs** and **Recent admin activity** — what the instance has been
  doing, with the failure reason on any job that failed.

---

## Content Archiver

The Content Archiver helps admins identify, tag, and safely delete stale Liveboards and Answers.

**Workflow:**
1. **Set criteria** — a single **Stale: 90d AND 90d** pill opens a compact editor where you pick both `Last Accessed ≥` and `Last Modified ≥` thresholds plus the AND/OR operator. Scope further with type chips (Liveboard / Answer) and per-tag include/exclude.
2. **Review** — browse stale objects in a grid; every column funnel applies real backend filters (name · type · owner · tag substring, numeric ranges on Views / Days Unused, date ranges on Last Accessed / Modified / Created). System-owned objects are hidden automatically.
3. **Tag** — bulk-tag selected rows (e.g. `Stale`) with one click; choose from existing instance tags or create new ones. Tag lookup is case-insensitive and scoped to the selected org (including the Primary org, `org_id=0`); if the name already exists elsewhere on the instance the Archiver reuses it instead of failing. Tags currently on the selection appear as red pill chips in the toolbar — click one to remove that tag from every selected row.
4. **Delete (safe)** — click **Delete selected** → dry-run checks permissions and dependencies → type `DELETE` to confirm → every object gets a TML backup before deletion.
5. **History** — browse all past archive sessions; download individual TML backups for any deleted object. Same column-filter model as the Archive tab.

The TML backup means every deletion is reversible — backups are stored locally at `~/.ts-admin/tml-exports/`.

---

## User & Group Management

**Users** — search and filter the user grid, then act on a selection:

- **Transfer ownership** — move every object a leaving user owns to another user.
- **Transfer sharing** — re-share everything the leaving user could see with a
  replacement, at the same access level.
- **Delete users** — bulk removal with a snapshot of what each user owned.

Every one of these previews its full impact before you confirm, and each run is
recorded in the History tab.

Click any row (outside the checkbox) to open the read-only **audit drawer**:
group membership, effective privileges — the union of every group's privileges,
which is how ThoughtSpot actually grants them — and an on-demand **Load access
from ThoughtSpot** button that fetches, live, every object the user can see
(directly or inherited through a group).

**Groups** — a read-only browser for the groups themselves: privileges,
member users, and the user who created each group, scoped to the selected
instance and org. Creator is resolved to a display name from the cached users,
so run a **users** sync alongside **groups** to see names rather than GUIDs.
Group writes stay in the ThoughtSpot UI in this version.

Both screens read from the local cache — run a **users** and a **groups** sync
to populate them. Group membership is written by the *groups* sync, so effective
privileges and the admin badge only appear once groups have been synced.

---

## Lineage

Two views over the same dependency cache:

- **Graph** — how content connects: liveboards and answers to the models they
  read, models to their tables, tables to their connections. Start from any of
  the four: **Connections**, **Logical Tables**, **Answers**, or **Liveboards**.
- **Columns** — column-level lineage for a model: each column traced back to its
  physical DB table and column, and forward to every liveboard/answer that uses
  it. Computed columns are labelled **ƒ Formula** rather than shown as a missing
  chain, since they are defined by a formula and have no physical source.

Run a **Lineage** sync (the `dependencies` entity in the API and job log) to
build it. The build is incremental — unchanged liveboards are skipped on
re-runs.

---

## Troubleshooting

**Settings → Diagnostics** has the two things support will ask for:

- **Recent logs** — tail the application log without leaving the app.
- **Download support bundle** — a small zip with the log tail, the most recent
  failed jobs (with tracebacks), and version/OS info. It contains **no**
  passwords, API tokens, or ThoughtSpot data — open it before sending if you
  want to verify. If support asks for more, use the **Download full logs** link
  beside it.

When a sync fails, the failure toast links straight here.

---

## Managing multiple instances

To add another ThoughtSpot instance, go to **Settings → Connections → Add instance**
and follow the same setup flow.

Switch between instances at any time using the instance picker in the sidebar.
Each instance has its own local cache — switching shows that instance's data, and
syncs always run against the instance you are looking at.
The selected org is remembered per instance across page refreshes.

---

## Developer mode

If you have Node.js installed and want hot-reload for UI development:

```bash
ts-admin-toolkit serve --dev
```

This runs FastAPI on `:8000` and the Next.js dev server on `:3000` with full
hot-reload. See [CONTRIBUTING.md](docs/dev/CONTRIBUTING.md) for the full developer setup.

---

## Updating

The app tells you when a new version is out: an **Update available** pill appears
in the top bar, with the steps below built in. You can also check any time with
`ts-admin-toolkit update --check`.

> **Installed the toolkit before v0.2.0?** The `update` command and the in-app
> update pill did not exist yet, so `ts-admin-toolkit update` will answer
> `Error: No such command 'update'`. Re-run the install command from
> [Install](#install) once — that brings you up to date, and every update after
> it works with the three steps below.

To update:

**1. Stop the toolkit.** Press `Ctrl+C` in the terminal window that is running it.

**2. Run the update:**

```bash
ts-admin-toolkit update
```

It checks for the newest release, downloads it, and replaces your current
install. If you are already on the latest version it says so and does nothing.

**3. Start it again:**

```bash
ts-admin-toolkit serve
```

That is the whole update — there is nothing to set up again afterwards.

Updating replaces the program only. Your ThoughtSpot instances, your saved
sign-ins, and everything you have synced (users, groups, metadata, lineage, job
history, audit log) live in your home folder and your computer's keychain, not
inside the app — so they are never touched. You will not have to reconnect or
re-sync.

<details>
<summary>Other ways to update</summary>

**Re-run the installer.** Re-running the install one-liner from the top of this
README does exactly the same thing — `ts-admin-toolkit update` just saves you
finding the URL again.

**If `ts-admin-toolkit update` cannot find `uv`** (the package manager the
installer uses), re-run the install one-liner instead.

**Installed it yourself with uv or pip?** Then update it the same way you
installed it, pointing at the newest wheel on the
[Releases page](https://github.com/kunalghate/thoughtspot-admin-toolkit/releases).

**Check which version you are on:**

```bash
ts-admin-toolkit --version
```

</details>

### Does it update itself automatically?

No, by design. The toolkit performs bulk deletes, archives and permission
changes, and silently swapping the code out from under an admin mid-operation is
the wrong trade. It notifies you; you choose when.

---

## How it works

The toolkit connects to your ThoughtSpot instance using the
[ThoughtSpot REST API v2](https://developers.thoughtspot.com/docs/rest-api-getstarted).
It caches data locally in a SQLite database so the UI is always fast.
All write operations (archive, share, delete) go directly to ThoughtSpot in real time —
the local cache is only used for browsing and building bulk operations.

For more detail: [How sync works](docs/user/SYNC.md) · [Architecture](docs/dev/ARCHITECTURE.md)
