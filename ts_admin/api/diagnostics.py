"""
Diagnostics endpoints — let an admin grab logs and a support bundle without
opening a terminal.

GET /api/v1/diagnostics/logs?lines=N      — tail of app.log
GET /api/v1/diagnostics/bundle?job_id=X   — zip with logs + recent failed
                                            jobs + app info, for emailing
                                            to support.
GET /api/v1/diagnostics/bundle?full=true  — same, but every rotated log in
                                            full (big; only when support asks).

The default bundle is deliberately small — the log tail only, and the last
few failed jobs — so it can be emailed or pasted into a support thread
without hitting an attachment/upload limit.

The bundle never contains keychain values, passwords, bearer tokens, or
the SQLite DB itself. Cluster names and URLs are included (the admin
already knows them) so support can correlate against their environment.
"""

from __future__ import annotations

import io
import json
import logging
import platform
import sys
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])

_README = """\
ThoughtSpot Admin Toolkit — support bundle
==========================================

This zip contains diagnostic information collected at the time it was
downloaded. Email it to support along with a short description of what
you were doing when the issue occurred.

What's in here:
  - app.log — the tail of the application log (the most recent {log_lines}
    lines), which is where the failure that sent you here will be. Add
    `?full=true` to the download URL to get every rotated log in full —
    only needed if support asks for it.
  - app_info.json — version, Python version, OS, cluster summary.
  - failed_jobs.json — the most recent {job_limit} failed jobs (id, type,
    error message, exception type, traceback, timing).
  - job_<id>.json (when downloaded from a specific job) — that job's full
    record + any related archive records.

What's NOT in here:
  - Passwords, API tokens, or anything from the OS keychain.
  - The SQLite database itself.
  - Any ThoughtSpot object content (TML, query data, etc.).

If you'd rather not send the bundle: open it first, look it over, and
remove anything you don't want to share. It's a normal zip file.
"""


@router.get("/logs", response_class=PlainTextResponse)
async def tail_logs(lines: int = Query(default=500, ge=1, le=5000)) -> str:
    """Return the last N lines of app.log as plain text."""
    from ts_admin.logging_config import get_log_file

    log_file = get_log_file()
    if not log_file.exists():
        return "(no log file yet)"

    try:
        # Small file — read whole and slice. With our 5 MB rotation cap this
        # is bounded.
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read log file: {exc}")

    tail = text.splitlines()[-lines:]
    return "\n".join(tail)


# Defaults sized so the bundle stays small enough to email or upload: the log
# tail carries the failure and its traceback, the older rotations almost never
# matter. `?full=true` restores the everything-in-full behaviour.
_TAIL_LINES = 2000
_FAILED_JOB_LIMIT = 10


@router.get("/bundle")
async def download_bundle(
    job_id: str | None = Query(default=None),
    full: bool = Query(default=False, description="Include every rotated log in full (large)."),
) -> StreamingResponse:
    """Build a zip with logs, recent failed jobs, and (optionally) one
    specific job's full record. Streams the zip back as a download."""
    job_limit = 50 if full else _FAILED_JOB_LIMIT
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", _README.format(log_lines="all" if full else _TAIL_LINES, job_limit=job_limit))
        zf.writestr("app_info.json", json.dumps(_app_info(), indent=2))
        zf.writestr(
            "failed_jobs.json", json.dumps(_recent_failed_jobs(limit=job_limit), indent=2, default=_json_default)
        )
        _add_log_files(zf, full=full)

        if job_id:
            try:
                detail = _job_detail(job_id)
            except KeyError:
                raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
            zf.writestr(f"job_{job_id}.json", json.dumps(detail, indent=2, default=_json_default))

    buf.seek(0)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"ts-admin-toolkit-bundle-{timestamp}.zip"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Helpers ────────────────────────────────────────────────────────────────────


def _json_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def _app_info() -> dict:
    """Version + environment metadata. No credentials."""
    from ts_admin import __version__

    info: dict = {
        "version": __version__,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "clusters": [],
        "active_cluster_id": None,
    }
    try:
        from ts_admin.config import load_config

        cfg = load_config()
        info["active_cluster_id"] = cfg.active_cluster_id
        info["clusters"] = [
            {"id": c.id, "name": c.name, "url": c.url, "auth_type": str(c.auth_type)} for c in cfg.clusters.values()
        ]
    except Exception as exc:  # noqa: BLE001 — diagnostics must never crash on env state
        info["clusters_error"] = f"{type(exc).__name__}: {exc}"

    return info


def _recent_failed_jobs(*, limit: int) -> list[dict]:
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.job import Job

    with get_session() as session:
        rows = session.exec(
            select(Job).where(Job.status == "FAILED").order_by(Job.created_at.desc()).limit(limit)
        ).all()

    return [_job_to_dict(j) for j in rows]


def _job_detail(job_id: str) -> dict:
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.archive_record import ArchiveRecord
    from ts_admin.models.job import Job

    with get_session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise KeyError(job_id)
        archive_records = session.exec(select(ArchiveRecord).where(ArchiveRecord.job_id == job_id)).all()

    return {
        "job": _job_to_dict(job),
        "archive_records": [_archive_record_to_dict(r) for r in archive_records],
    }


def _job_to_dict(job) -> dict:
    return {
        "id": job.id,
        "cluster_id": job.cluster_id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "total": job.total,
        "parameters": _safe_json_loads(job.parameters),
        "result": _safe_json_loads(job.result),
        "error": job.error,
        "error_type": job.error_type,
        "error_traceback": job.error_traceback,
        "is_cancelled": job.is_cancelled,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _archive_record_to_dict(rec) -> dict:
    return {
        "id": rec.id,
        "ts_guid": rec.ts_guid,
        "name": rec.name,
        "object_type": rec.object_type,
        "tml_export_status": rec.tml_export_status,
        "tml_export_error": rec.tml_export_error,
        "archived_at": rec.archived_at.isoformat() if rec.archived_at else None,
        "restored_at": rec.restored_at.isoformat() if rec.restored_at else None,
    }


def _safe_json_loads(value: str | None) -> dict | list | str | None:
    if not value:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def _add_log_files(zf: zipfile.ZipFile, *, full: bool) -> None:
    """Add the log tail — or, with full=True, app.log plus every rotated copy."""
    from ts_admin.logging_config import get_log_dir, get_log_file

    log_file = get_log_file()
    log_dir = get_log_dir()

    if full:
        if log_file.exists():
            zf.write(log_file, arcname="app.log")
        for rotated in sorted(log_dir.glob("app.log.*")):
            zf.write(rotated, arcname=rotated.name)
        return

    if not log_file.exists():
        return
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        zf.writestr("app.log", f"(could not read log file: {exc})")
        return

    lines = text.splitlines()
    tail = lines[-_TAIL_LINES:]
    if len(lines) > len(tail):
        tail.insert(0, f"(truncated — showing the last {len(tail)} of {len(lines)} lines; use ?full=true for all)")
    zf.writestr("app.log", "\n".join(tail))
