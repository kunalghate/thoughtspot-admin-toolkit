"""
CLI entrypoint — the only command customers need to know.

Usage:
  ts-admin-toolkit serve             # static mode (no Node.js required)
  ts-admin-toolkit serve --dev       # dev mode (FastAPI + Next.js hot reload)
  ts-admin-toolkit serve --port 9000 # custom port
"""

import logging
import signal
import subprocess
import sys
import webbrowser
from pathlib import Path

import click
import uvicorn

from ts_admin.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@click.group()
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
    from ts_admin import __version__

    mode = "dev mode" if dev else "static mode"
    click.echo(f"\n  ThoughtSpot Admin Toolkit v{__version__}  ({mode})\n")
