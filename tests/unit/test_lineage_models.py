"""
Phase 0 foundations: the three lineage cache tables are created by init_db()
and round-trip cleanly. Guards the model definitions + their registration in
database.init_db() (a missing import there = table silently absent at runtime).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select


@pytest.fixture
def in_memory_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import ts_admin.database as db_module

    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    db_module.init_db()
    return engine


def test_init_db_creates_lineage_tables(in_memory_db):
    from sqlalchemy import inspect

    tables = set(inspect(in_memory_db).get_table_names())
    assert {"ts_dependencies", "ts_column_lineage", "ts_column_usage"} <= tables


def test_cached_dependency_round_trips(in_memory_db):
    from ts_admin.models.cache.ts_dependency import CachedDependency

    now = datetime.now(timezone.utc)
    with Session(in_memory_db) as session:
        session.add(
            CachedDependency(
                cluster_id="c1",
                org_id=0,
                source_guid="answer-1",
                source_type="ANSWER",
                source_name="Sales Answer",
                target_guid="model-1",
                target_type="MODEL",
                target_name="Sales Model",
                relation="USES",
                synced_at=now,
            )
        )
        session.commit()

        row = session.exec(
            select(CachedDependency).where(
                CachedDependency.cluster_id == "c1",
                CachedDependency.source_guid == "answer-1",
            )
        ).one()
        assert row.target_guid == "model-1"
        assert row.target_type == "MODEL"
        assert row.relation == "USES"


def test_column_lineage_and_usage_round_trip(in_memory_db):
    from ts_admin.models.cache.ts_column_lineage import CachedColumnLineage
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage

    with Session(in_memory_db) as session:
        session.add(
            CachedColumnLineage(
                cluster_id="c1",
                org_id=0,
                model_guid="model-1",
                model_column_name="Revenue",
                table_guid="table-1",
                table_column_name="revenue",
                db_table="SALES",
                db_column_name="REVENUE",
                connection_name="Snowflake Prod",
            )
        )
        session.add(
            CachedColumnUsage(
                cluster_id="c1",
                org_id=0,
                model_guid="model-1",
                model_column_name="Revenue",
                consumer_guid="answer-1",
                consumer_type="ANSWER",
                consumer_name="Sales Answer",
            )
        )
        session.commit()

        lin = session.exec(select(CachedColumnLineage).where(CachedColumnLineage.model_guid == "model-1")).one()
        assert lin.db_column_name == "REVENUE"
        assert lin.connection_name == "Snowflake Prod"

        use = session.exec(select(CachedColumnUsage).where(CachedColumnUsage.consumer_guid == "answer-1")).one()
        assert use.model_column_name == "Revenue"
        assert use.consumer_type == "ANSWER"
