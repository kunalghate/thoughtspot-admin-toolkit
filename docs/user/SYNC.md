# How Data Sync Works

## The short version

The app keeps a local copy of your ThoughtSpot data in a database on your machine.
This makes every page load instant — instead of waiting 30-60 seconds for ThoughtSpot
to respond to every query, you see your data in under a second.

You control when data is refreshed. Each section syncs independently.

---

## Why a local cache?

ThoughtSpot instances can have tens of thousands of objects — users, Liveboards,
Answers, Worksheets, Tables. Fetching all of that on every page load would take
30-60 seconds every time you opened the app.

Instead, the app fetches your data once and stores it locally. Browsing, filtering,
and searching all run against the local copy. Only write operations (archiving,
sharing, deleting) go to ThoughtSpot in real time.

---

## What gets cached

Each of these entity types is cached separately and can be refreshed independently:

| Entity | What it includes | Typical size |
|---|---|---|
| **Users** | All user accounts, email, groups, status, last login | Hundreds to thousands |
| **Groups** | All user groups, privileges, members | Dozens to hundreds |
| **Metadata** | All Liveboards, Answers, Worksheets, Tables — with owner, tags, last accessed | Thousands to tens of thousands |
| **Tags** | All tags defined on the instance | Small |
| **Dependencies** | Which content depends on which data source | Large — synced on demand |
| **Orgs** | All orgs (for multi-org ThoughtSpot instances) | Small |

---

## Syncing each section

Each section of the app has its own **last synced** indicator and **Refresh** button.

- **Users section:** shows "Synced 2h ago" with a [Refresh users] button
- **Metadata section:** shows "Synced 8h ago ⚠" if data is getting stale
- **Settings → Sync:** overview of all entity types, when each was last synced, and a button to refresh each one individually

You can also refresh everything at once from **Settings → Sync → Refresh all**.

### What "stale" means

| Time since last sync | Indicator |
|---|---|
| Less than 1 hour | Gray — fresh |
| 1–6 hours | Yellow — consider refreshing |
| More than 6 hours | Orange ⚠ — refresh recommended |
| Never synced | Red — sync required before using this section |

These are advisory — the app will still work with older cached data. Use your
judgment about how fresh the data needs to be for the operation you're doing.

---

## First visit to a section

If you navigate to a section that has never been synced, the app will prompt you
to sync it before showing any data. This only happens once — after that, you see
your cached data immediately.

---

## Multi-cluster: each cluster has its own cache

If you have multiple ThoughtSpot clusters configured (e.g. Production and Staging),
each cluster has a completely separate local cache.

Switching clusters in the top navigation bar switches both the live connection
and the local cache for that cluster. Data from one cluster is never mixed with another.

---

## Write operations always go to ThoughtSpot directly

The local cache is read-only from the app's perspective. When you:
- Archive content
- Share a Liveboard
- Deactivate a user
- Delete objects

...the app always calls the ThoughtSpot API directly in real time. It does not
write to the local cache — it verifies the objects still exist first, executes
the operation, and then updates the relevant cache entries.

This means you can safely trust that any operation you execute reflects the
actual current state of ThoughtSpot.

---

## Where is my data stored?

Your local cache is stored in a SQLite database on your machine:

- **Mac/Linux:** `~/.ts-admin/db.sqlite`
- **Windows:** `C:\Users\<you>\.ts-admin\db.sqlite`

Your credentials are stored in your operating system's keychain (not in this file).

To fully reset the app (clear all cached data and settings):

```bash
ts-admin reset
```

This deletes the local cache and settings. It does not affect your ThoughtSpot
instance in any way.
