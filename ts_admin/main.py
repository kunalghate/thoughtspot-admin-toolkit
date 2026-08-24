"""
FastAPI application factory.

Request flow:
  Browser
    │
    ▼
  FastAPI
    ├── /api/v1/*  ← API routes (JSON)
    └── /*         ← static files (pre-built Next.js SPA)

CORS is restricted to the local port only. Never use allow_origins=["*"].
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ts_admin.database import init_db
from ts_admin.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class CacheControlStaticFiles(StaticFiles):
    """
    StaticFiles with an explicit cache policy.

    Next.js content-hashes everything under /_next/static/, so those files are
    safe to cache forever. Everything else — index.html above all — must
    revalidate on every load (cheap 304 via ETag), or browsers heuristically
    serve a stale UI after a pip upgrade until the user hard-refreshes.
    """

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        if scope["path"].startswith("/_next/static/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    logger.info("Starting ThoughtSpot Admin Toolkit...")
    init_db()
    logger.info("Database ready.")
    _recover_stuck_jobs()
    _cleanup_old_tml_exports()
    yield
    logger.info("Shutting down.")


def _is_delete_job(job) -> bool:
    """True for jobs that permanently delete ThoughtSpot objects.

    Two callers reach `deletion_service._execute_delete`: the Archiver
    (`job_type="archive"` with `action="delete"` — the same type also covers
    non-destructive archive actions) and the Bulk Deleter
    (`job_type="bulk_delete"`, which carries no `action` key at all).
    """
    if job.job_type == "bulk_delete":
        return True
    if job.job_type != "archive" or not job.parameters:
        return False
    return job.get_parameters().get("action") == "delete"


def _recover_stuck_jobs() -> None:
    """
    Mark any RUNNING or QUEUED jobs as FAILED on startup, and reconcile the
    metadata cache for delete jobs the crash interrupted.

    A delete job is only reconciled against CONFIRMED deletes —
    `ArchiveRecord.deleted_confirmed_at`, written after `delete_metadata`
    returns. It is never inferred from `tml_export_status`: `_execute_delete`
    exports EVERY object in Phase A before Phase B deletes any, so the widest
    crash window is exactly the one where a SUCCESS export means nothing was
    deleted. Inferring there purged the cache for objects still live in
    ThoughtSpot — they vanished from Metadata Explorer and the Archiver while
    the admin could still see them in TS, and their ArchiveRecords still read
    `is_restorable`, so "restoring" them created duplicates.

    Records left unconfirmed stay unconfirmed, which is what makes them
    non-restorable (see `ArchiveRecord.deleted_confirmed_at`).
    """
    from datetime import datetime, timezone

    from sqlmodel import col, select
    from sqlmodel import delete as sql_delete

    from ts_admin.database import get_session
    from ts_admin.models.archive_record import ArchiveRecord
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.models.job import Job

    with get_session() as session:
        stuck = session.exec(select(Job).where(col(Job.status).in_(["RUNNING", "QUEUED"]))).all()

        if not stuck:
            return

        for job in stuck:
            if _is_delete_job(job):
                # Only GUIDs ThoughtSpot confirmed deleting. Phase B purges the
                # cache in the same transaction that stamps the confirmation, so
                # this is normally a no-op; it still matters for jobs stranded by
                # a build that predates that guarantee.
                confirmed = session.exec(
                    select(ArchiveRecord.ts_guid, ArchiveRecord.org_id).where(
                        ArchiveRecord.job_id == job.id,
                        ArchiveRecord.cluster_id == job.cluster_id,
                        col(ArchiveRecord.deleted_confirmed_at).is_not(None),
                    )
                ).all()

                # Group by org: a GUID can exist in more than one org on the
                # same cluster, and the purge must not reach past the org the
                # object was deleted from.
                by_org: dict[int, list[str]] = {}
                for ts_guid, org_id in confirmed:
                    by_org.setdefault(org_id, []).append(ts_guid)

                purged = 0
                for org_id, guids in by_org.items():
                    result = session.exec(
                        sql_delete(CachedMetadata).where(
                            CachedMetadata.cluster_id == job.cluster_id,
                            CachedMetadata.org_id == org_id,
                            col(CachedMetadata.ts_guid).in_(guids),
                        )
                    )
                    purged += result.rowcount or 0

                unconfirmed = session.exec(
                    select(ArchiveRecord.id).where(
                        ArchiveRecord.job_id == job.id,
                        col(ArchiveRecord.deleted_confirmed_at).is_(None),
                    )
                ).all()
                if purged or unconfirmed:
                    logger.warning(
                        "Startup recovery: %s job %s was interrupted — purged %d "
                        "CachedMetadata row(s) for confirmed deletes; %d object(s) "
                        "were never confirmed deleted and remain in the cache and "
                        "non-restorable",
                        job.job_type,
                        job.id,
                        purged,
                        len(unconfirmed),
                    )

            job.status = "FAILED"
            job.error = "Server restarted while job was running"
            job.completed_at = datetime.now(timezone.utc)
            session.add(job)

        session.commit()
        logger.info("Startup recovery: marked %d stuck job(s) as FAILED", len(stuck))


def _cleanup_old_tml_exports() -> None:
    """
    Remove TML export directories older than 90 days where all ArchiveRecords
    for that job_id have been restored (restored_at IS NOT NULL).

    Safe to skip on error — this is housekeeping only.
    """
    import shutil
    from datetime import datetime, timedelta, timezone

    from sqlmodel import func, select

    from ts_admin.database import get_session
    from ts_admin.models.archive_record import ArchiveRecord
    from ts_admin.services.deletion_service import TML_EXPORT_DIR

    if not TML_EXPORT_DIR.exists():
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=90)

    try:
        for job_dir in TML_EXPORT_DIR.iterdir():
            if not job_dir.is_dir():
                continue

            # Check mtime — skip recent directories
            mtime = datetime.fromtimestamp(job_dir.stat().st_mtime, tz=timezone.utc)
            if mtime > cutoff:
                continue

            job_id = job_dir.name
            with get_session() as session:
                total = session.exec(
                    select(func.count()).select_from(ArchiveRecord).where(ArchiveRecord.job_id == job_id)
                ).one()
                restored = session.exec(
                    select(func.count())
                    .select_from(ArchiveRecord)
                    .where(
                        ArchiveRecord.job_id == job_id,
                        ArchiveRecord.restored_at.isnot(None),
                    )
                ).one()

            if total > 0 and total == restored:
                shutil.rmtree(job_dir, ignore_errors=True)
                logger.info("Cleaned up TML export dir for fully-restored job %s", job_id)
    except Exception as exc:
        logger.warning("TML export cleanup skipped due to error: %s", exc)


def create_app(port: int = 8080) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        port: The port the server is running on, used to configure CORS.
    """
    app = FastAPI(
        title="ThoughtSpot Admin Toolkit",
        description="Admin control plane for ThoughtSpot administrators.",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )

    # CORS: localhost only, never wildcard
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://localhost:{port}",
            f"http://127.0.0.1:{port}",
            # Next.js dev server (only active in --dev mode)
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Translate uncaught domain errors into consistent JSON responses
    from ts_admin.api.error_handlers import register_exception_handlers

    register_exception_handlers(app)

    # Register API routers
    _register_routers(app)

    # Serve pre-built Next.js SPA from /static
    # In --dev mode, Next.js runs on its own port — static files not used
    _static_files = [f for f in STATIC_DIR.iterdir() if f.name != ".gitkeep"] if STATIC_DIR.exists() else []
    if _static_files:
        app.mount("/", CacheControlStaticFiles(directory=STATIC_DIR, html=True), name="static")
        logger.info("Serving frontend from %s", STATIC_DIR)
    else:
        logger.warning(
            "No static files found at %s. "
            "Run 'make build' to bundle the frontend, or use 'ts-admin-toolkit serve --dev'.",
            STATIC_DIR,
        )

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def dev_index():
            return """
            <!doctype html>
            <html>
            <head>
              <title>TS Admin Toolkit</title>
              <style>
                body { font-family: Geist, system-ui, sans-serif; background: #F2EDE3;
                       display: flex; align-items: center; justify-content: center;
                       height: 100vh; margin: 0; }
                .box { background: #FAF8F4; border: 1px solid #E8E1D5; border-radius: 10px;
                       padding: 40px 48px; max-width: 480px; text-align: center; }
                .logo { width: 40px; height: 40px; border-radius: 10px; margin: 0 auto 20px;
                        background: linear-gradient(135deg, #8B5CF6, #6D28D9);
                        display: flex; align-items: center; justify-content: center;
                        color: white; font-weight: 700; font-size: 16px; }
                h1 { font-size: 18px; color: #1A1714; margin: 0 0 8px; }
                p  { font-size: 13px; color: #7A7068; margin: 0 0 24px; line-height: 1.6; }
                a  { display: inline-block; padding: 8px 18px; background: #8B5CF6;
                     color: white; border-radius: 6px; text-decoration: none;
                     font-size: 13px; font-weight: 500; }
              </style>
            </head>
            <body>
              <div class="box">
                <div class="logo">TS</div>
                <h1>ThoughtSpot Admin Toolkit</h1>
                <p>The frontend hasn't been built yet.<br>
                   Run <code>make build</code> to bundle the UI,
                   or use <code>ts-admin-toolkit serve --dev</code> for hot-reload development.</p>
                <a href="/api/docs">Open API docs →</a>
              </div>
            </body>
            </html>
            """

    return app


def _register_routers(app: FastAPI) -> None:
    from ts_admin.api import (
        archiver,
        clusters,
        dashboard,
        deleter,
        diagnostics,
        groups,
        health,
        jobs,
        metadata,
        relationships,
        sharing,
        sync,
        update,
        users,
    )

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(clusters.router, prefix="/api/v1")
    app.include_router(sync.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(metadata.router, prefix="/api/v1")
    app.include_router(archiver.router, prefix="/api/v1")
    app.include_router(deleter.router, prefix="/api/v1")
    app.include_router(diagnostics.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(groups.router, prefix="/api/v1")
    app.include_router(sharing.router, prefix="/api/v1")
    app.include_router(relationships.router, prefix="/api/v1")
    app.include_router(dashboard.router, prefix="/api/v1")
    app.include_router(update.router, prefix="/api/v1")


# Module-level app instance for uvicorn: uvicorn ts_admin.main:app
# Must be after _register_routers is defined.
app = create_app()
