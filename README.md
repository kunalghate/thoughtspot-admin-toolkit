# ThoughtSpot Admin Toolkit

A web application for ThoughtSpot administrators. Provides workflows and tools
for managing ThoughtSpot instances that go beyond what's available out of the box —
user management, content governance, bulk operations, and more.

Built as a modern alternative to the [CS Tools](https://thoughtspot.github.io/cs_tools/)
CLI, with a full web UI that any admin can use without needing Python or terminal knowledge.

---

## Features

| Feature | Description |
|---|---|
| **User Management** | Search, filter, bulk update groups, transfer content ownership, deactivate users |
| **Content Archiver** | Find stale content, preview impact, tag and archive with dry-run protection |
| **Bulk Sharing** | Share content to users/groups in bulk, with impact preview before executing |
| **Metadata Explorer** | Searchable, filterable view of all content with owner, tags, last accessed |
| **Relationship Visualizer** | Graph view of content dependencies and user-group membership |
| **Multi-cluster** | Manage multiple ThoughtSpot instances from one app |

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
ts-admin serve
```

Opens the app at `http://localhost:8080`.

On first launch, you will see a setup screen to connect your ThoughtSpot instance.
All configuration (URL, credentials, profiles) is done through the web UI — no
CLI configuration required.

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

You are taken to the dashboard immediately after setup.

---

## Managing multiple clusters

To add another ThoughtSpot instance, go to **Settings → Connections → Add cluster**
and follow the same setup flow.

Switch between clusters at any time using the cluster picker in the top navigation bar.
Each cluster has its own local cache — switching clusters shows that cluster's data.

---

## Developer mode

If you have Node.js installed and want hot-reload for UI development:

```bash
ts-admin serve --dev
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
the local cache is only used for browsing and building operations.

For more detail: [How sync works](docs/user/SYNC.md) · [Architecture](docs/dev/ARCHITECTURE.md)
