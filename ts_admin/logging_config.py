"""
Logging setup for ts-admin-toolkit.

Writes to both stderr (human view during `serve`) and a rotating file
(persists across restarts so we can ship logs in a support bundle).

Honors:
  LOG_LEVEL — DEBUG | INFO | WARNING | ERROR (default INFO)
  LOG_DIR   — directory for log files (default ~/.ts-admin-toolkit/logs)
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FILE_NAME = "app.log"
_MAX_BYTES = 5_000_000  # ~5 MB per file
_BACKUP_COUNT = 5  # keep last 5 rotations → ~25 MB total cap
_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_already_configured = False


def get_log_dir() -> Path:
    """Resolve the log directory; create it if missing."""
    env = os.environ.get("LOG_DIR")
    if env:
        path = Path(env).expanduser()
    else:
        path = Path.home() / ".ts-admin-toolkit" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_log_file() -> Path:
    return get_log_dir() / _LOG_FILE_NAME


def setup_logging() -> None:
    """Install console + rotating file handlers on the root logger.

    Idempotent — calling twice will not duplicate handlers.
    """
    global _already_configured
    if _already_configured:
        return

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    # Rotating file handler
    try:
        file_handler = RotatingFileHandler(
            get_log_file(),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        # If we can't open the log file (read-only fs, permissions), keep
        # the console handler and warn on stderr — never crash the server
        # over logging setup.
        logging.getLogger(__name__).warning(
            "Could not open log file at %s: %s — continuing with console logging only",
            get_log_file(),
            exc,
        )

    _already_configured = True
