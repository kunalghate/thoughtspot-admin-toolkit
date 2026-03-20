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
    yield
    logger.info("Shutting down.")


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

    return app


def _register_routers(app: FastAPI) -> None:
    from ts_admin.api import clusters, sync, jobs, health

    app.include_router(health.router,   prefix="/api/v1")
    app.include_router(clusters.router, prefix="/api/v1")
    app.include_router(sync.router,     prefix="/api/v1")
    app.include_router(jobs.router,     prefix="/api/v1")
