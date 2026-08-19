# Getting Started

## What is this?

ThoughtSpot Admin Toolkit is a web application that gives ThoughtSpot administrators
a powerful UI for managing their ThoughtSpot instance. It covers workflows that
require multiple API calls, bulk operations, and governance tasks that are tedious
or impossible to do through the standard ThoughtSpot UI.

You don't need to know Python, use a terminal, or understand APIs. Once it's running,
everything is done through your browser.

---

## Step 1: Install

You don't need Python, and you don't need to install anything beforehand. You
will use a terminal twice — once to install, once to start the app — and
everything after that happens in your browser.

**On a Mac or Linux machine**

Open **Terminal** (on a Mac: press `Cmd + Space`, type `Terminal`, press Enter).
Copy the line below, paste it in, and press Enter:

```bash
curl -LsSf https://raw.githubusercontent.com/kunalghate/thoughtspot-admin-toolkit/main/install.sh | sh
```

**On Windows**

Open **PowerShell** (press the Windows key, type `PowerShell`, press Enter).
Copy the line below, paste it in, and press Enter:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/kunalghate/thoughtspot-admin-toolkit/main/install.ps1 | iex"
```

It takes a minute or two. When it finishes it prints `Done. The toolkit is
installed.` and tells you what to type next.

The installer puts the toolkit in its own isolated folder and brings its own
copy of Python if your machine doesn't already have a suitable one. It does not
change any other software on your computer.

---

## Step 2: Start the app

In the same window, type:

```bash
ts-admin-toolkit serve
```

This opens `http://localhost:8080` in your browser. Keep the terminal window open
while you use the app — closing it stops the server.

> If you see `command not found`, the terminal hasn't picked up the new install
> yet. Close the window, open a new one, and try again.

---

## Step 3: Connect to ThoughtSpot

On first launch you will see a **Connect to ThoughtSpot** setup screen.

**You will need:**
- Your ThoughtSpot instance URL (e.g. `https://company.thoughtspot.cloud`)
- Your admin username and password
  — OR your ThoughtSpot secret key (Trusted Auth)
  — OR a bearer token

**Steps:**
1. Enter your ThoughtSpot URL
2. Enter your username
3. Select your auth method and enter your credentials
4. Click **Test connection** — the app checks that your credentials work
5. Give this connection a name (e.g. `Production`)
6. Click **Save**

You will be taken to the dashboard. Your credentials are stored securely in your
operating system's keychain — never in a plain text file.

---

## Step 4: Sync your data

The app works from a local cache of your ThoughtSpot data. On first use,
you need to sync the data you want to work with.

You will see a **"Not yet synced"** message on each section until you sync it.

To sync:
- Go to the section you want to use (e.g. **Users**)
- Click **Sync users** — the app fetches all users from ThoughtSpot
- The grid fills with your data

You only need to sync each section once. After that, your data is cached locally
and the app loads instantly. Refresh whenever you want up-to-date data.

See [How sync works](SYNC.md) for more detail.

---

## Stopping the app

Press `Ctrl+C` in the terminal window where `ts-admin-toolkit serve` is running.

Your data (local cache, settings, job history) is preserved between sessions.

---

## Managing connections

All connection management is done from **Settings → Connections**.

### Adding a cluster

1. Click **Add cluster**
2. Enter a display name (e.g. `Production`, `Staging`)
3. Enter your ThoughtSpot URL (e.g. `https://company.thoughtspot.cloud`)
4. Enter your username
5. Choose your auth method:
   - **Basic** — username + password
   - **Trusted Auth** — username + secret key (found in ThoughtSpot under **Develop → Security Settings → Trusted authentication**)
   - **Bearer token** — a pre-obtained API token
6. Click **Test connection** to verify before saving
7. Click **Save**

The first cluster you add is automatically set as active. Additional clusters must be manually activated.

Your credentials are stored securely in your operating system's keychain — never in a plain text file.

---

### Switching the active cluster

Only one cluster is active at a time. The active cluster is shown with a purple indicator on the connections page.

To switch:
1. Go to **Settings → Connections**
2. Click **Set active** on the cluster you want to switch to

All pages will immediately reflect data from the newly active cluster.

---

### Editing a cluster

Use this when your credentials rotate, your auth method changes, or your ThoughtSpot URL changes.

1. Go to **Settings → Connections**
2. Click the **pencil icon** on the cluster you want to edit
3. Update any fields
4. To rotate your credential (password, secret key, or token), enter the new value in the credential field. **Leave it blank to keep your existing credential unchanged.**
5. Click **Save changes**

> If you change the auth method (e.g. from Basic to Trusted Auth), you must enter a new credential — the old one is automatically removed from the keychain.

---

### Removing a cluster

1. Go to **Settings → Connections**
2. Click the **trash icon** on the cluster you want to remove
3. Confirm the deletion

Removing a cluster deletes its configuration and credential from the keychain. It does **not** delete the local cached data (users, metadata, etc.) associated with that cluster. To clear cached data, use the sync management page.

---

## Updating

You do not have to watch for new versions. When one is published, an
**Update available** pill appears in the top bar of the app, and clicking it
shows these same steps.

> **If you installed before v0.2.0**, your copy predates the `update` command —
> it will say `No such command 'update'`. Run the install command from
> [Step 1](#step-1-install) once to catch up. After that, the steps below work.

**1. Stop the toolkit.** Press `Ctrl+C` in the terminal window running it.

**2. Update it:**

```bash
ts-admin-toolkit update
```

If you are already on the newest version, it tells you so and changes nothing.

**3. Start it again:**

```bash
ts-admin-toolkit serve
```

Nothing to set up again afterwards. Updating swaps out the program itself; your
ThoughtSpot instances, your saved sign-ins, and all your synced data (users,
groups, metadata, lineage, job history) are stored separately — in your home
folder and your computer's keychain — so an update never touches them.

To see what you are running now: `ts-admin-toolkit --version`. To check for a
new version without installing it: `ts-admin-toolkit update --check`.

If `ts-admin-toolkit update` reports that it cannot find `uv`, run the install
command from [Step 1](#step-1-install) again instead — that upgrades in place
too.

The toolkit never updates itself in the background. It performs bulk deletes and
permission changes, so it tells you an update exists and leaves the timing to you.

---

## Troubleshooting

**"Can't reach ThoughtSpot"**
Check that the URL is correct and that your network can reach it.
If you use a VPN to access ThoughtSpot, make sure it is connected.

**"Invalid credentials"**
Go to **Settings → Connections**, select your cluster, and update your credentials.

**"Data looks out of date"**
Click the **Refresh** button on any page to fetch the latest data from ThoughtSpot.

**`command not found: ts-admin-toolkit`**
The terminal window you're in was opened before the install finished, so it
hasn't picked up the new command. Close it, open a new one, and try again.

**App won't start**
Re-run the install command from [Step 1](#step-1-install) — it repairs and
upgrades an existing install.
