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
    from ts_admin.api import clusters, sync, jobs, health, metadata

    app.include_router(health.router,    prefix="/api/v1")
    app.include_router(clusters.router,  prefix="/api/v1")
    app.include_router(sync.router,      prefix="/api/v1")
    app.include_router(jobs.router,      prefix="/api/v1")
    app.include_router(metadata.router,  prefix="/api/v1")


# Module-level app instance for uvicorn: uvicorn ts_admin.main:app
# Must be after _register_routers is defined.
app = create_app()
