#!/bin/sh
# ThoughtSpot Admin Toolkit — one-line installer for macOS and Linux.
#
#   curl -LsSf https://raw.githubusercontent.com/kunalghate/thoughtspot-admin-toolkit/main/install.sh | sh
#
# Installs uv (a self-contained package manager) if it is not already present,
# then installs the toolkit into its own isolated environment. uv downloads a
# managed Python if the machine does not already have a suitable one, so there
# is no prerequisite beyond a shell and curl.
#
# Re-running this script upgrades an existing install.

set -eu

PACKAGE="ts-admin-toolkit"
PYTHON_VERSION="3.12"
UV_INSTALLER="https://astral.sh/uv/install.sh"

say() { printf '%s\n' "$1"; }
die() { printf 'Error: %s\n' "$1" >&2; exit 1; }

# uv installs its own binaries here, and puts tool shims here too.
UV_BIN_DIR="${XDG_BIN_HOME:-${HOME}/.local/bin}"

say ""
say "Installing ThoughtSpot Admin Toolkit..."
say ""

# ── Step 1: make sure uv is available ──────────────────────────────────────────

# A previous run may have installed uv without the shell having been restarted,
# so look in uv's install directory as well as on PATH.
if command -v uv >/dev/null 2>&1; then
    UV="uv"
elif [ -x "${UV_BIN_DIR}/uv" ]; then
    UV="${UV_BIN_DIR}/uv"
else
    say "Installing uv (the package manager the toolkit installs through)..."
    command -v curl >/dev/null 2>&1 || die "curl is required but was not found."
    curl -LsSf "$UV_INSTALLER" | sh >/dev/null 2>&1 \
        || die "could not install uv. See https://docs.astral.sh/uv/getting-started/installation/"

    if command -v uv >/dev/null 2>&1; then
        UV="uv"
    elif [ -x "${UV_BIN_DIR}/uv" ]; then
        UV="${UV_BIN_DIR}/uv"
    else
        die "uv installed but could not be found. Open a new terminal and run this script again."
    fi
fi

# ── Step 2: install the toolkit ────────────────────────────────────────────────

# --python makes uv fetch a managed interpreter when the system has none that
# satisfies requires-python, which is the common case on stock macOS.
# --upgrade makes a re-run behave as an upgrade rather than a no-op.
say "Installing the toolkit..."
"$UV" tool install --upgrade --python "$PYTHON_VERSION" "$PACKAGE" >/dev/null \
    || die "could not install ${PACKAGE}. Run without the installer for details: uv tool install ${PACKAGE}"

# Adds uv's shim directory to the shell profile if it is not already there.
# Failure is not fatal — we fall back to telling the user the full path.
"$UV" tool update-shell >/dev/null 2>&1 || true

# ── Step 3: tell the user exactly what to type next ────────────────────────────

say ""
say "Done. The toolkit is installed."
say ""

if command -v ts-admin-toolkit >/dev/null 2>&1; then
    say "To start it, run:"
    say ""
    say "    ts-admin-toolkit serve"
else
    # The shim directory was added to the profile but this shell has not picked
    # it up yet, so give a command that works right now in this same window.
    say "To start it, open a NEW terminal window and run:"
    say ""
    say "    ts-admin-toolkit serve"
    say ""
    say "Or, to start it in this window without opening a new one:"
    say ""
    say "    ${UV_BIN_DIR}/ts-admin-toolkit serve"
fi

say ""
say "That opens the app in your browser. Keep the terminal window open while you use it."
say ""
