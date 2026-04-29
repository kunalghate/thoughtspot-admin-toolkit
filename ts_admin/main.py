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

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


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


def _recover_stuck_jobs() -> None:
    """
    Mark any RUNNING or QUEUED jobs as FAILED on startup.

    For archive delete jobs where TML export succeeded before the crash,
    conservatively remove those CachedMetadata rows (assume the delete went
    through — they can be restored from ArchiveRecord if not).
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
            # For archive delete jobs: remove CachedMetadata rows that were
            # successfully exported (conservative assumption: they were deleted)
            if job.job_type == "archive" and job.parameters:
                params = job.get_parameters()
                if params.get("action") == "delete":
                    exported_guids = session.exec(
                        select(ArchiveRecord.ts_guid).where(
                            ArchiveRecord.job_id == job.id,
                            ArchiveRecord.tml_export_status == "SUCCESS",
                        )
                    ).all()
                    if exported_guids:
                        session.exec(sql_delete(CachedMetadata).where(col(CachedMetadata.ts_guid).in_(exported_guids)))
                        logger.warning(
                            "Startup recovery: removed %d CachedMetadata rows "
                            "for stuck archive job %s (TML export succeeded — "
                            "assumed deleted)",
                            len(exported_guids),
                            job.id,
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
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers
    _register_routers(app)

    # Serve pre-built Next.js SPA from /static
    # In --dev mode, Next.js runs on its own port — static files not used
    _static_files = [f for f in STATIC_DIR.iterdir() if f.name != ".gitkeep"] if STATIC_DIR.exists() else []
    if _static_files:
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
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
    from ts_admin.api import archiver, clusters, health, jobs, metadata, sync

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(clusters.router, prefix="/api/v1")
    app.include_router(sync.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(metadata.router, prefix="/api/v1")
    app.include_router(archiver.router, prefix="/api/v1")


# Module-level app instance for uvicorn: uvicorn ts_admin.main:app
# Must be after _register_routers is defined.
app = create_app()
