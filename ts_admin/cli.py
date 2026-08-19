"""
CLI entrypoint — the only command customers need to know.

Usage:
  ts-admin-toolkit serve             # static mode (no Node.js required)
  ts-admin-toolkit serve --dev       # dev mode (FastAPI + Next.js hot reload)
  ts-admin-toolkit serve --port 9000 # custom port
  ts-admin-toolkit update            # upgrade to the latest release
  ts-admin-toolkit --version         # print the installed version
"""

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
import webbrowser
from pathlib import Path

import click
import uvicorn

from ts_admin import __version__
from ts_admin.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@click.group()
@click.version_option(__version__, "-v", "--version", prog_name="ts-admin-toolkit")
def main() -> None:
    """ThoughtSpot Admin Toolkit — admin control plane for ThoughtSpot."""


@main.command()
@click.option("--port", default=8080, show_default=True, help="Port to run on.")
@click.option("--dev", is_flag=True, default=False, help="Dev mode: hot reload for both API and UI.")
@click.option("--no-browser", is_flag=True, default=False, help="Don't open browser automatically.")
def serve(port: int, dev: bool, no_browser: bool) -> None:
    """Start the ThoughtSpot Admin Toolkit server."""

    if dev:
        _serve_dev(api_port=8000, ui_port=3000, no_browser=no_browser)
    else:
        _serve_static(port=port, no_browser=no_browser)


# ── Update ─────────────────────────────────────────────────────────────────────

# uv puts its own binary and the tool shims it creates in this directory. The
# same path install.sh/install.ps1 use.
UV_BIN_DIR = Path(os.environ.get("XDG_BIN_HOME") or (Path.home() / ".local" / "bin"))

# Matches install.sh — uv fetches a managed interpreter when the system has none
# that satisfies requires-python, which is the common case on stock macOS.
UV_PYTHON_VERSION = "3.12"


@main.command()
@click.option("--check", is_flag=True, default=False, help="Only report whether an update exists.")
def update(check: bool) -> None:
    """Upgrade the toolkit to the latest release."""
    from ts_admin.services.update_service import RELEASES_URL, check_for_update

    # httpx logs every request at INFO. Useful inside `serve`, but here it puts
    # a stack-trace-looking line in the middle of a four-line user interaction.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    click.echo("\n  Checking for updates...")

    # force=True: the 6-hour cache exists to keep the in-app banner cheap. An
    # explicit `update` must never install a stale cached wheel.
    result = asyncio.run(check_for_update(force=True))

    if not result.checked:
        click.echo(f"\n  ✗ {result.error or 'Could not check for updates.'}", err=True)
        click.echo(f"    Check your network connection, or see {RELEASES_URL}\n", err=True)
        sys.exit(1)

    if not result.update_available:
        click.echo(f"\n  ✓ You are on the latest version (v{result.current}).\n")
        return

    click.echo(f"\n  Update available: v{result.current} → v{result.latest}")

    if check:
        click.echo("\n  To install it, run:  ts-admin-toolkit update\n")
        return

    uv = _resolve_uv()
    if uv is None:
        click.echo(
            "\n  ✗ Could not find uv, the package manager the toolkit was installed with.\n"
            "    Re-run the install command from the README to upgrade instead.\n",
            err=True,
        )
        sys.exit(1)

    click.echo("  Installing...")

    # --force replaces the existing install rather than no-opping, exactly as
    # install.sh does. The local database and keychain credentials live outside
    # the tool environment, so they are untouched by this.
    completed = subprocess.run(
        [uv, "tool", "install", "--force", "--python", UV_PYTHON_VERSION, result.wheel_url],
        capture_output=True,
        text=True,
    )

    if completed.returncode != 0:
        click.echo(f"\n  ✗ Update failed.\n{completed.stderr}", err=True)
        click.echo(f"    You can install manually from {RELEASES_URL}\n", err=True)
        sys.exit(1)

    click.echo(f"\n  ✓ Updated to v{result.latest}.")
    click.echo("    Nothing to set up again — your instances, sign-ins and synced data all stay.")
    click.echo("\n  Start it again with:  ts-admin-toolkit serve\n")


def _resolve_uv() -> str | None:
    """
    Find uv on PATH, falling back to the directory the installers put it in —
    a shell that has not been restarted since install will not have it on PATH.
    """
    found = shutil.which("uv")
    if found:
        return found

    for name in ("uv", "uv.exe"):
        candidate = UV_BIN_DIR / name
        if candidate.exists():
            return str(candidate)

    return None


# ── Static mode (default) ──────────────────────────────────────────────────────


def _serve_static(port: int, no_browser: bool) -> None:
    """
    Run FastAPI on a single port. FastAPI serves both the API and
    the pre-built Next.js static files.
    """
    from ts_admin.main import create_app

    _print_banner(port=port, dev=False)

    if not no_browser:
        import threading

        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    app = create_app(port=port)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


# ── Dev mode ───────────────────────────────────────────────────────────────────


def _serve_dev(api_port: int, ui_port: int, no_browser: bool) -> None:
    """
    Start FastAPI (with reload) and Next.js dev server as subprocesses.
    Ctrl+C shuts both down cleanly.
    """
    if not FRONTEND_DIR.exists():
        click.echo(
            "✗ frontend/ directory not found.\n  Clone the full repository to use --dev mode.",
            err=True,
        )
        sys.exit(1)

    _check_node()

    _print_banner(port=ui_port, dev=True)

    processes = []

    try:
        # FastAPI with hot reload
        api = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "ts_admin.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--reload",
                "--log-level",
                "warning",
            ]
        )
        processes.append(api)
        click.echo(f"  ✓ API running on http://localhost:{api_port}")

        # Next.js dev server
        ui = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=FRONTEND_DIR,
        )
        processes.append(ui)
        click.echo(f"  ✓ UI running on  http://localhost:{ui_port}")
        click.echo(f"\n  → Open http://localhost:{ui_port}\n")

        if not no_browser:
            import threading

            threading.Timer(3.0, lambda: webbrowser.open(f"http://localhost:{ui_port}")).start()

        # Forward Ctrl+C to both processes
        def _shutdown(signum, frame):
            click.echo("\nShutting down...")
            for p in processes:
                p.terminate()
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        for p in processes:
            p.wait()

    except FileNotFoundError as exc:
        click.echo(f"\n✗ Missing dependency: {exc}", err=True)
        click.echo("  Make sure Node.js is installed: https://nodejs.org", err=True)
        for p in processes:
            p.terminate()
        sys.exit(1)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _check_node() -> None:
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True)
        if result.returncode != 0:
            raise FileNotFoundError
    except FileNotFoundError:
        click.echo(
            "✗ Node.js is required for --dev mode but was not found.\n"
            "  Install it from https://nodejs.org then run: cd frontend && npm install",
            err=True,
        )
        sys.exit(1)


def _print_banner(port: int, dev: bool) -> None:
    mode = "dev mode" if dev else "static mode"
    click.echo(f"\n  ThoughtSpot Admin Toolkit v{__version__}  ({mode})\n")
