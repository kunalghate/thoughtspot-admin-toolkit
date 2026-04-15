# ThoughtSpot Admin Toolkit

A web application for ThoughtSpot administrators. Provides workflows and tools
for managing ThoughtSpot instances that go beyond what's available out of the box —
content governance, bulk operations, metadata exploration, and more.

Built as a modern alternative to the [CS Tools](https://thoughtspot.github.io/cs_tools/)
CLI, with a full web UI that any admin can use without needing Python or terminal knowledge.

---

## Features

| Feature | Status | Description |
|---|---|---|
| **Metadata Explorer** | ✅ Available | Searchable, filterable grid of all content — owner, tags, last accessed, views. Every column funnel sends real server-side filters (name / owner / tag substring, date ranges, numeric ranges). |
| **Content Archiver** | ✅ Available | Find stale content, tag or delete with mandatory TML backup, dry-run impact check, restore from History. Archive + History grids share the same full-column filter model. |
| **User Management** | 🔜 Coming soon | Search, filter, bulk group updates, ownership transfer, deactivation |
| **Bulk Sharing** | 🔜 Coming soon | Share content to users/groups in bulk with impact preview |
| **Relationship Visualizer** | 🔜 Coming soon | Graph view of content dependencies and user-group membership |
| **Multi-cluster** | ✅ Available | Manage multiple ThoughtSpot instances from one app |

All data is cached locally — the app is fast, no waiting on ThoughtSpot API calls
on every page load. See [How sync works](docs/user/SYNC.md).

---

## Requirements

- Python 3.10 or later
- A ThoughtSpot instance (Cloud or Software)
- Admin-level access (`ADMINISTRATION` privilege) on that instance

No Node.js required for end users. The web UI is pre-built and bundled with the package.

---

## Install

```bash
pip install ts-admin-toolkit
```

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

## Content Archiver

The Content Archiver helps admins identify, tag, and safely delete stale Liveboards and Answers.

**Workflow:**
1. **Set criteria** — a single **Stale: 90d AND 90d** pill opens a compact editor where you pick both `Last Accessed ≥` and `Last Modified ≥` thresholds plus the AND/OR operator. Scope further with type chips (Liveboard / Answer) and per-tag include/exclude.
2. **Review** — browse stale objects in a grid; every column funnel applies real backend filters (name · type · owner · tag substring, numeric ranges on Views / Days Unused, date ranges on Last Accessed / Modified / Created). System-owned objects are hidden automatically.
3. **Tag** — bulk-tag selected rows (e.g. `Stale`) with one click; choose from existing cluster tags or create new ones. Tags currently on the selection appear as red pill chips in the toolbar — click one to remove that tag from every selected row.
4. **Delete (safe)** — click **Delete selected** → dry-run checks permissions and dependencies → type `DELETE` to confirm → every object gets a TML backup before deletion.
5. **History** — browse all past archive sessions; download individual TML backups for any deleted object. Same column-filter model as the Archive tab.

The TML backup means every deletion is reversible — backups are stored locally at `~/.ts-admin/tml-exports/`.

---

## Managing multiple clusters

To add another ThoughtSpot instance, go to **Settings → Connections → Add cluster**
and follow the same setup flow.

Switch between clusters at any time using the cluster picker in the top navigation bar.
Each cluster has its own local cache — switching clusters shows that cluster's data.
The selected org is remembered per cluster across page refreshes.

---

## Developer mode

If you have Node.js installed and want hot-reload for UI development:

```bash
ts-admin-toolkit serve --dev
```

This runs FastAPI on `:8000` and the Next.js dev server on `:3000` with full
hot-reload. See [CONTRIBUTING.md](docs/dev/CONTRIBUTING.md) for the full developer setup.

---

## Upgrading

```bash
pip install --upgrade ts-admin-toolkit
```

---

## How it works

The toolkit connects to your ThoughtSpot instance using the
[ThoughtSpot REST API v2](https://developers.thoughtspot.com/docs/rest-api-getstarted).
It caches data locally in a SQLite database so the UI is always fast.
All write operations (archive, share, delete) go directly to ThoughtSpot in real time —
the local cache is only used for browsing and building bulk operations.

For more detail: [How sync works](docs/user/SYNC.md) · [Architecture](docs/dev/ARCHITECTURE.md)
