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

You need Python 3.10 or later. To check:

```bash
python --version
```

If you don't have Python, download it from [python.org](https://python.org).

Install the toolkit:

```bash
pip install ts-admin-toolkit
```

---

## Step 2: Start the app

```bash
ts-admin-toolkit serve
```

This opens `http://localhost:8080` in your browser. Keep the terminal window open
while you use the app — closing it stops the server.

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

## Managing multiple ThoughtSpot instances

To connect to a second cluster (e.g. a staging environment):

1. Go to **Settings → Connections**
2. Click **Add cluster**
3. Follow the same setup flow

Switch between clusters using the **cluster picker** in the top navigation bar.
Each cluster has its own local cache.

---

## Upgrading

```bash
pip install --upgrade ts-admin-toolkit
```

Your local data and settings are preserved when upgrading.

---

## Troubleshooting

**"Can't reach ThoughtSpot"**
Check that the URL is correct and that your network can reach it.
If you use a VPN to access ThoughtSpot, make sure it is connected.

**"Invalid credentials"**
Go to **Settings → Connections**, select your cluster, and update your credentials.

**"Data looks out of date"**
Click the **Refresh** button on any page to fetch the latest data from ThoughtSpot.

**App won't start**
Make sure Python 3.10+ is installed and the `ts-admin-toolkit` package is installed.
Try running `pip install --upgrade ts-admin-toolkit`.
