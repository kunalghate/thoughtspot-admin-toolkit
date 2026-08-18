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
# it — the next lineage sync repopulates it.
#
# {table_name: (sentinel column, sync_log entity_type that rebuilds it)}. The
# entity_type matters: dropping the table without clearing its sync_log rows
# leaves the UI reporting a recent, successful sync over an empty table, so the
# admin sees a blank grid with nothing telling them to re-sync.
#
# NOTE: any new column on a rebuildable cache table must bump its sentinel here,
# or create_all silently skips it and queries fail with "no such column".
_REBUILDABLE_SENTINELS: dict[str, tuple[str, str]] = {
    "ts_column_lineage": ("is_formula", "dependencies"),
    # `author_guid` was added after ts_groups shipped. The table is a pure
    # cache of groups/search, so drop-and-re-sync is cheaper and safer than a
    # migration — the next Groups sync repopulates it with the creator GUID.
    "ts_groups": ("author_guid", "groups"),
}


def _drop_outdated_rebuildable_tables() -> None:
    """Drop rebuildable cache tables whose schema predates their sentinel column."""
    import logging

    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table, (sentinel, entity_type) in _REBUILDABLE_SENTINELS.items():
        if table not in existing_tables:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if sentinel in columns:
            continue
        with engine.begin() as conn:
            conn.execute(text(f'DROP TABLE "{table}"'))
            # Invalidate the freshness record in the same transaction, so the
            # entity reads as "never synced" instead of stale-but-green.
            if "sync_log" in existing_tables:
                conn.execute(
                    text("DELETE FROM sync_log WHERE entity_type = :entity_type"),
                    {"entity_type": entity_type},
                )
        logging.getLogger(__name__).info(
            "Dropped outdated cache table %r (missing %r); re-run the %s sync to rebuild it",
            table,
            sentinel,
            entity_type,
        )


# Indexes that must exist on tables which may predate them. `create_all` only
# builds indexes for tables it creates, so an index added to an existing model
# never lands on an already-installed DB. These are additive and idempotent —
# unlike the rebuildable-cache sentinels above, no data is dropped.
#
# {index_name: (table, [columns])}
_BACKFILL_INDEXES: dict[str, tuple[str, list[str]]] = {
    "ix_ts_metadata_cluster_org_guid": ("ts_metadata", ["cluster_id", "org_id", "ts_guid"]),
}


def _create_missing_indexes() -> None:
    """Add indexes declared on models that `create_all` skipped on existing tables."""
    from sqlalchemy import inspect, text

    engine = get_engine()
    existing_tables = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        for index_name, (table, columns) in _BACKFILL_INDEXES.items():
            if table not in existing_tables:
                continue  # create_all will build it with the table
            cols = ", ".join(f'"{c}"' for c in columns)
            conn.execute(text(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table}" ({cols})'))


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
    _create_missing_indexes()


def get_session() -> Session:
    """Return a new database session. Use as a context manager."""
    return Session(get_engine())
