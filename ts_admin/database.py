"""
Database setup and session management.

Uses SQLite via SQLModel (SQLAlchemy under the hood).
All tables include a cluster_id FK for multi-cluster isolation.

DB location: ~/.ts-admin/ts_admin_toolkit_db.sqlite
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

DB_DIR = Path.home() / ".ts-admin"
DB_PATH = DB_DIR / "ts_admin_toolkit_db.sqlite"

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        # Migrate from old filename if it exists and new one doesn't
        old_path = DB_DIR / "db.sqlite"
        if old_path.exists() and not DB_PATH.exists():
            old_path.rename(DB_PATH)
        _engine = create_engine(
            f"sqlite:///{DB_PATH}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
    return _engine


# Rebuildable cache tables use drop-and-rebuild via re-sync instead of Alembic
# (see the model docstrings). create_all never alters existing tables, so when a
# model gains a column, drop the outdated table here and let create_all recreate
# it — the next lineage sync repopulates it. {table_name: sentinel column}.
_REBUILDABLE_SENTINELS: dict[str, str] = {
    "ts_column_lineage": "is_formula",
}


def _drop_outdated_rebuildable_tables() -> None:
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table, sentinel in _REBUILDABLE_SENTINELS.items():
        if table not in existing_tables:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if sentinel not in columns:
            with engine.begin() as conn:
                conn.execute(text(f'DROP TABLE "{table}"'))


def init_db() -> None:
    """Create all tables (recreating outdated rebuildable caches). Called once at app startup."""
    # Import all models so SQLModel picks them up
    import ts_admin.models.archive_record  # noqa: F401
    import ts_admin.models.audit_log  # noqa: F401
    import ts_admin.models.cache.content_permissions  # noqa: F401
    import ts_admin.models.cache.ts_column_lineage  # noqa: F401
    import ts_admin.models.cache.ts_column_usage  # noqa: F401
    import ts_admin.models.cache.ts_dependency  # noqa: F401
    import ts_admin.models.cache.ts_group  # noqa: F401
    import ts_admin.models.cache.ts_metadata  # noqa: F401
    import ts_admin.models.cache.ts_org  # noqa: F401
    import ts_admin.models.cache.ts_tag  # noqa: F401
    import ts_admin.models.cache.ts_user  # noqa: F401  (registers CachedUser + UserOrgMembership)
    import ts_admin.models.cluster  # noqa: F401
    import ts_admin.models.job  # noqa: F401
    import ts_admin.models.share_record  # noqa: F401
    import ts_admin.models.sync_log  # noqa: F401
    import ts_admin.models.user_action_record  # noqa: F401

    _drop_outdated_rebuildable_tables()
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Session:
    """Return a new database session. Use as a context manager."""
    return Session(get_engine())
