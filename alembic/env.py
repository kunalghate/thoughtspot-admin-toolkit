"""
Alembic migration environment.

Imports all SQLModel table definitions so that autogenerate can detect
schema changes. Uses the same DB path as the application.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# ── Import all models so Alembic sees their metadata ──────────────────────────
import ts_admin.models.cluster        # noqa: F401
import ts_admin.models.sync_log       # noqa: F401
import ts_admin.models.audit_log      # noqa: F401
import ts_admin.models.job            # noqa: F401
import ts_admin.models.cache.ts_user  # noqa: F401  (includes UserOrgMembership + UserGroupMembership)
import ts_admin.models.cache.ts_group # noqa: F401
import ts_admin.models.cache.ts_metadata       # noqa: F401
import ts_admin.models.cache.ts_tag            # noqa: F401
import ts_admin.models.cache.ts_org            # noqa: F401
import ts_admin.models.cache.content_permissions # noqa: F401
import ts_admin.models.archive_record            # noqa: F401

# ── Alembic config ─────────────────────────────────────────────────────────────
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override DB URL with the same path the application uses
from ts_admin.database import DB_PATH
config.set_main_option("sqlalchemy.url", f"sqlite:///{DB_PATH}")

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (generates SQL)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
