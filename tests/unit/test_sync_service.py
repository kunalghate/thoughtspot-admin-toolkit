"""
Unit tests for sync_service.run_sync error handling.

The contract under test: a sync that fails because the connected account can't
reach an org (or lacks a privilege) must NOT mark the whole cluster's session as
expired. Only a genuine credential/session failure (TSAuthenticationError) does
that. Conflating the two told users to "reconnect this cluster" when reconnecting
could never help — the credentials were always valid.
"""

from __future__ import annotations

import pytest
from sqlmodel import create_engine

from ts_admin.services import connection_status
from ts_admin.services.connection_status import ConnectionState
from ts_admin.ts_client.exceptions import (
    TSAuthenticationError,
    TSConnectionError,
    TSInsufficientPrivilegesError,
)

CLUSTER_ID = "c1"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def in_memory_db(monkeypatch):
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import ts_admin.database as db_module

    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    db_module.init_db()
    return engine


@pytest.fixture
def patched_config(monkeypatch):
    """Minimal active-cluster config so run_sync's logging/log-write paths work."""
    from ts_admin.config import AppConfig, ClusterConfig
    from ts_admin.ts_client.models import AuthType

    cluster_cfg = ClusterConfig(
        id=CLUSTER_ID,
        name="Prod",
        url="https://prod.thoughtspot.cloud",
        username="admin",
        auth_type=AuthType.TRUSTED,
    )
    config = AppConfig(clusters={CLUSTER_ID: cluster_cfg}, active_cluster_id=CLUSTER_ID)
    monkeypatch.setattr("ts_admin.config.load_config", lambda: config)
    return config


@pytest.fixture(autouse=True)
def clear_health():
    connection_status.clear(CLUSTER_ID)
    yield
    connection_status.clear(CLUSTER_ID)


def _make_job(entity: str) -> str:
    from ts_admin.services.job_service import create_job

    return create_job(
        job_type=f"sync:{entity}",
        parameters={"entity_type": entity, "org_id": 349890686},
        cluster_id=CLUSTER_ID,
    )


def _job_status(job_id: str) -> tuple[str, str | None]:
    from ts_admin.database import get_session
    from ts_admin.models.job import Job

    with get_session() as session:
        job = session.get(Job, job_id)
        return job.status, job.error_type


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_org_access_denied_does_not_expire_cluster(monkeypatch, in_memory_db, patched_config):
    """TSInsufficientPrivilegesError → job FAILED, but cluster session stays non-expired."""

    async def _boom(*, org_id: int, job_id: str) -> None:
        raise TSInsufficientPrivilegesError(f"The connected account can't access org {org_id}.")

    monkeypatch.setattr("ts_admin.services.sync_service._sync_metadata", _boom)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=349890686, job_id=job_id)

    status, error_type = _job_status(job_id)
    assert status == "FAILED"
    assert error_type == "TSInsufficientPrivilegesError"
    # The crux: the cluster must NOT be flagged expired (no pointless reconnect loop).
    assert connection_status.get(CLUSTER_ID).state is not ConnectionState.EXPIRED


@pytest.mark.anyio
async def test_user_sync_reports_progress(monkeypatch, in_memory_db, patched_config):
    """A successful paginated sync must climb job.progress as pages arrive and
    finish COMPLETE with progress == record count. Without this the UI can only
    ever show a generic spinner — never a live count or a populated bar."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    now = datetime.now(timezone.utc)

    def _user(guid: str):
        return SimpleNamespace(
            id=guid,
            name=f"user-{guid}",
            display_name=f"User {guid}",
            email=f"{guid}@example.io",
            status=SimpleNamespace(value="ACTIVE"),
            created=now,
            modified=now,
        )

    pages = [[_user("a"), _user("b")], [_user("c")]]
    seen_progress: list[int] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def search_users(self, *, org_id):
            for page in pages:
                yield page

    # Avoid the keychain — the fake client ignores auth anyway.
    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)

    # Capture every progress update so we can prove it climbed, not just landed.
    import ts_admin.services.sync_service as sync_module

    real_update = sync_module.update_progress

    def _spy(job_id: str, progress: int) -> None:
        seen_progress.append(progress)
        real_update(job_id, progress)

    monkeypatch.setattr(sync_module, "update_progress", _spy)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("users")
    await run_sync(entity_type="users", org_id=0, job_id=job_id)

    status, _ = _job_status(job_id)
    assert status == "COMPLETE"

    from ts_admin.database import get_session
    from ts_admin.models.job import Job

    with get_session() as session:
        job = session.get(Job, job_id)
    assert job.progress == 3  # all three users counted
    # Progress was reported per page (climbing), not only at the end.
    assert seen_progress == [2, 3]


@pytest.mark.anyio
async def test_user_resync_updates_timestamps_on_existing_rows(monkeypatch, in_memory_db, patched_config):
    """Re-syncing must refresh created_at/modified_at on rows that already exist.
    Rows cached before timestamp parsing landed have NULLs — if the upsert's
    update branch skips these fields, they stay NULL forever no matter how many
    times the admin re-syncs."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    created = datetime(2025, 1, 1, tzinfo=timezone.utc)
    modified = datetime(2025, 2, 1, tzinfo=timezone.utc)

    from ts_admin.database import get_session
    from ts_admin.models.cache.ts_user import CachedUser

    # Pre-existing cached row with NULL timestamps (pre-fix state).
    with get_session() as session:
        session.add(
            CachedUser(
                cluster_id=CLUSTER_ID,
                ts_guid="guid-a",
                username="stale-name",
                display_name="Stale",
                email="a@example.io",
                status="ACTIVE",
                created_at=None,
                modified_at=None,
                synced_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    fresh = SimpleNamespace(
        id="guid-a",
        name="user-a",
        display_name="User A",
        email="a@example.io",
        status=SimpleNamespace(value="ACTIVE"),
        created=created,
        modified=modified,
    )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def search_users(self, *, org_id):
            yield [fresh]

    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)

    from sqlmodel import select

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("users")
    await run_sync(entity_type="users", org_id=0, job_id=job_id)

    status, _ = _job_status(job_id)
    assert status == "COMPLETE"

    with get_session() as session:
        row = session.exec(
            select(CachedUser).where(CachedUser.cluster_id == CLUSTER_ID, CachedUser.ts_guid == "guid-a")
        ).one()
    assert row.username == "user-a"
    assert row.created_at == created.replace(tzinfo=None) or row.created_at == created
    assert row.modified_at == modified.replace(tzinfo=None) or row.modified_at == modified


@pytest.mark.anyio
async def test_group_sync_populates_memberships_and_purges_stale(monkeypatch, in_memory_db, patched_config):
    """Group sync must write user_group_memberships rows (they power is_admin
    and UserDetail.groups), rewrite them on re-sync, and purge groups that no
    longer exist upstream — otherwise deleted groups linger forever."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.cache.ts_group import CachedGroup
    from ts_admin.models.cache.ts_user import UserGroupMembership

    now = datetime.now(timezone.utc)

    def _group(guid: str, members: list[str]):
        return SimpleNamespace(
            id=guid,
            name=f"group-{guid}",
            display_name=f"Group {guid}",
            description="",
            privileges=["ADMINISTRATION"],
            member_users=members,
            created=now,
            modified=now,
        )

    pages: list[list] = [[_group("g1", ["u1", "u2"]), _group("g2", ["u1"])]]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def search_groups(self, *, org_id):
            for page in pages:
                yield page

    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("groups")
    await run_sync(entity_type="groups", org_id=0, job_id=job_id)
    status, _ = _job_status(job_id)
    assert status == "COMPLETE"

    def _memberships() -> set[tuple[str, str]]:
        with get_session() as session:
            rows = session.exec(select(UserGroupMembership).where(UserGroupMembership.cluster_id == CLUSTER_ID)).all()
        return {(r.user_guid, r.group_guid) for r in rows}

    assert _memberships() == {("u1", "g1"), ("u2", "g1"), ("u1", "g2")}

    # Re-sync: u2 left g1, and g2 was deleted upstream entirely.
    pages[:] = [[_group("g1", ["u1"])]]
    job_id = _make_job("groups")
    await run_sync(entity_type="groups", org_id=0, job_id=job_id)
    status, _ = _job_status(job_id)
    assert status == "COMPLETE"

    # Membership rewritten (no duplicates, no stale members), stale group purged.
    assert _memberships() == {("u1", "g1")}
    with get_session() as session:
        guids = session.exec(select(CachedGroup.ts_guid).where(CachedGroup.cluster_id == CLUSTER_ID)).all()
    assert list(guids) == ["g1"]

    # A sweep that returns NOTHING must not purge. SQLAlchemy renders
    # `not_in(<empty>)` as `NOT IN (NULL) OR (1 = 1)` — always true — so an
    # unguarded purge here would delete every group and membership for the org
    # on any empty response (wrong org context, transient upstream blip).
    pages[:] = []
    job_id = _make_job("groups")
    await run_sync(entity_type="groups", org_id=0, job_id=job_id)
    status, _ = _job_status(job_id)
    assert status == "COMPLETE"

    assert _memberships() == {("u1", "g1")}
    with get_session() as session:
        guids = session.exec(select(CachedGroup.ts_guid).where(CachedGroup.cluster_id == CLUSTER_ID)).all()
    assert list(guids) == ["g1"]


@pytest.mark.anyio
async def test_dependencies_dispatches_to_lineage_build(monkeypatch, in_memory_db, patched_config):
    """run_sync('dependencies') must route to lineage_service.build_object_graph."""
    seen: dict = {}

    async def _fake_build(*, cluster_id, org_id, job_id, finalize=True):
        seen["cluster_id"] = cluster_id
        seen["org_id"] = org_id
        seen["finalize"] = finalize
        # Mirror the real build: finalize marks the job COMPLETE.
        if finalize:
            from ts_admin.services.job_service import mark_complete

            mark_complete(job_id, {"entity_type": "dependencies", "record_count": 7})
        return 7

    import ts_admin.services.lineage_service as lineage_module

    monkeypatch.setattr(lineage_module, "build_object_graph", _fake_build)
    # No Phase 2 column pass wired in this test → object tier finalizes the job.
    monkeypatch.delattr(lineage_module, "build_column_map", raising=False)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("dependencies")
    await run_sync(entity_type="dependencies", org_id=0, job_id=job_id)

    assert seen == {"cluster_id": CLUSTER_ID, "org_id": 0, "finalize": True}
    status, _ = _job_status(job_id)
    assert status == "COMPLETE"


# ── Metadata cache completeness (S23) ─────────────────────────────────────────
#
# `_sync_metadata` commits a DELETE-all for the org and then re-pages in the
# spec order LIVEBOARD, ANSWER, <five logical-table subtypes>, committing per
# page. An interruption therefore leaves the cache NON-EMPTY but TRUNCATED. The
# completeness signal has to be the sync_log row, written before the delete.


def _seed_metadata_success_marker(org_id: int = 0, *, record_count: int = 2) -> None:
    from datetime import datetime, timezone

    from ts_admin.database import get_session
    from ts_admin.models.sync_log import SyncLog

    with get_session() as session:
        session.add(
            SyncLog(
                cluster_id=CLUSTER_ID,
                org_id=org_id,
                entity_type="metadata",
                status="SUCCESS",
                record_count=record_count,
                synced_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        )
        session.commit()


def _metadata_rows(org_id: int = 0) -> list:
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.cache.ts_metadata import CachedMetadata

    with get_session() as session:
        return list(
            session.exec(
                select(CachedMetadata).where(
                    CachedMetadata.cluster_id == CLUSTER_ID,
                    CachedMetadata.org_id == org_id,
                )
            ).all()
        )


def _seed_cached_metadata(*objs: tuple[str, str], org_id: int = 0) -> None:
    from datetime import datetime, timezone

    from ts_admin.database import get_session
    from ts_admin.models.cache.ts_metadata import CachedMetadata

    with get_session() as session:
        for guid, object_type in objs:
            session.add(
                CachedMetadata(
                    cluster_id=CLUSTER_ID,
                    org_id=org_id,
                    ts_guid=guid,
                    name=f"obj-{guid}",
                    object_type=object_type,
                    owner_guid="owner-1",
                    owner_name="Alice",
                    tag_names="[]",
                    synced_at=datetime.now(timezone.utc),
                )
            )
        session.commit()


def _metadata_marker_status(org_id: int = 0) -> str | None:
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.sync_log import SyncLog

    with get_session() as session:
        row = session.exec(
            select(SyncLog).where(
                SyncLog.cluster_id == CLUSTER_ID,
                SyncLog.org_id == org_id,
                SyncLog.entity_type == "metadata",
            )
        ).first()
    return row.status if row else None


def _meta_obj(guid: str, object_type: str):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=guid,
        name=f"obj-{guid}",
        type=SimpleNamespace(value=object_type),
        owner_id="owner-1",
        author_name="Alice",
        tags=[],
        created=now,
        modified=now,
        last_accessed=now,
        view_count=0,
    )


def _install_metadata_client(monkeypatch, pages, *, raise_after: int | None = None, exc: BaseException | None = None):
    """Fake ThoughtSpotClient whose search_metadata yields `pages`, optionally
    raising `exc` after `raise_after` pages to simulate an interrupted crawl."""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def search_metadata(self):
            for idx, page in enumerate(pages):
                if raise_after is not None and idx >= raise_after:
                    raise exc or TSConnectionError("upstream dropped the connection mid-crawl")
                yield page

    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)


# WHY THESE TESTS INTERRUPT WITH CancelledError, NOT TSConnectionError
# --------------------------------------------------------------------
# `run_sync`'s terminal `except Exception` handler already flips the marker to
# FAILED, so an interruption it CATCHES was never the hole — see
# `test_caught_failure_also_invalidates_the_marker` below, which passes with or
# without the write-ahead write. The hole is the interruption that never reaches
# that handler: process kill, SIGINT, server shutdown, task cancellation. The
# in-process stand-in is `asyncio.CancelledError` — a BaseException since 3.8,
# so `except Exception` does not catch it and no terminal marker write happens.
# Only the write-ahead marker can invalidate the cache in that case. Using a
# catchable exception here is exactly what makes an ordering test vacuous.


@pytest.mark.anyio
async def test_interrupted_metadata_sync_is_not_reported_as_synced(monkeypatch, in_memory_db, patched_config):
    """The S23 bug. A metadata sync killed after page 1 leaves liveboards and
    answers cached and every model/table missing, while the previous SUCCESS
    sync_log row survives untouched — so the whole app (dashboard, transfer
    preview, share preview) reads a truncated cache as authoritative and
    silently acts on a subset."""
    import asyncio

    from ts_admin.services.sync_status import metadata_is_authoritative

    _seed_metadata_success_marker()
    # Page 1 = the two archivable types; page 2 = the logical tables that never arrive.
    pages = [
        [_meta_obj("lb-1", "LIVEBOARD"), _meta_obj("ans-1", "ANSWER")],
        [_meta_obj("model-1", "WORKSHEET")],
    ]
    _install_metadata_client(monkeypatch, pages, raise_after=1, exc=asyncio.CancelledError())

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    with pytest.raises(asyncio.CancelledError):
        await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    # No terminal handler ran — the job is still RUNNING, exactly as it would be
    # after a `kill -9`. Nothing downstream can lean on the job row either.
    assert _job_status(job_id)[0] == "RUNNING"

    # ANTI-VACUITY: prove we reproduced the *truncated* shape, not a trivially
    # empty cache. If this is empty the test proves nothing about completeness.
    rows = _metadata_rows()
    assert rows, "expected a partially-populated cache — the truncation was not reproduced"
    assert {r.object_type for r in rows} == {"LIVEBOARD", "ANSWER"}
    assert not any(r.object_type == "WORKSHEET" for r in rows)

    # The crux: a non-empty cache must NOT read as authoritative.
    assert _metadata_marker_status() == "IN_PROGRESS"
    assert metadata_is_authoritative(cluster_id=CLUSTER_ID, org_id=0) is False


@pytest.mark.anyio
async def test_write_ahead_marker_preserves_the_last_completed_sync(monkeypatch, in_memory_db, patched_config):
    """The write-ahead marker says "a sync is running", NOT "a sync just finished
    with 0 rows". `_write_sync_log` upserts the single (cluster, org, entity)
    row, so writing synced_at=now / record_count=0 there would destroy the only
    record of when the cache was last known complete — and the Topbar, which
    renders off exactly those two fields, would report a healthy in-flight sync
    as a zero-row sync that landed a moment ago.

    Drop `preserve_progress=True` from the `_sync_metadata` call and this goes
    red on both assertions.
    """
    import asyncio
    from datetime import datetime, timezone

    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.sync_log import SyncLog

    seeded_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    _seed_metadata_success_marker(record_count=42)
    _install_metadata_client(
        monkeypatch, [[_meta_obj("lb-1", "LIVEBOARD")]], raise_after=0, exc=asyncio.CancelledError()
    )

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    with pytest.raises(asyncio.CancelledError):
        await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    with get_session() as session:
        row = session.exec(select(SyncLog).where(SyncLog.entity_type == "metadata")).one()

    assert row.status == "IN_PROGRESS"  # anti-vacuity: the write-ahead write DID happen
    assert row.record_count == 42, "the last completed sync's record_count was clobbered"
    assert row.synced_at.replace(tzinfo=timezone.utc) == seeded_at, (
        "the last completed sync's timestamp was clobbered — the app now has no "
        "record of when the cache was last known complete"
    )


@pytest.mark.anyio
async def test_marker_invalidated_before_any_row_is_deleted(monkeypatch, in_memory_db, patched_config):
    """Ordering test — pins the marker AHEAD of the delete, not merely somewhere
    before the crawl.

    The window this closes is the gap between the delete's commit and the marker
    write. They are two separate transactions by design (a single one would hold
    the SQLite write lock across the whole crawl — see the comment in
    `_sync_metadata`), so an interruption can land between them. Write-ahead
    makes that window contain "marker invalidated, rows still present", which is
    merely pessimistic. The reverse order makes it contain "rows all gone, marker
    still SUCCESS" — an empty cache certified as complete, which reads as *this
    org has no content* rather than *we don't know*.

    Interrupt exactly at the marker write to sit inside that window: move the
    `_write_sync_log(... IN_PROGRESS)` call below the delete block and this
    goes red.
    """
    import asyncio

    import ts_admin.services.sync_service as sync_module
    from ts_admin.services.sync_status import metadata_is_authoritative

    _seed_metadata_success_marker()
    _seed_cached_metadata(("lb-old", "LIVEBOARD"), ("ws-old", "WORKSHEET"))
    assert len(_metadata_rows()) == 2  # anti-vacuity: there IS something to lose

    real_write = sync_module._write_sync_log

    def _die_on_the_in_progress_write(entity_type, org_id, *, status, **kwargs):
        if entity_type == "metadata" and status == "IN_PROGRESS":
            raise asyncio.CancelledError()
        return real_write(entity_type, org_id, status=status, **kwargs)

    monkeypatch.setattr(sync_module, "_write_sync_log", _die_on_the_in_progress_write)
    _install_metadata_client(monkeypatch, [[_meta_obj("lb-1", "LIVEBOARD")]])

    job_id = _make_job("metadata")
    with pytest.raises(asyncio.CancelledError):
        await sync_module.run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    # THE INVARIANT: never "certified complete AND emptied". Either the marker
    # went first (rows intact, still certified — pessimistically fine) or the
    # marker was invalidated. Both are honest; the conjunction is the lie.
    rows = _metadata_rows()
    certified = metadata_is_authoritative(cluster_id=CLUSTER_ID, org_id=0)
    assert not (certified and rows == []), (
        "the org's whole metadata cache was deleted while its sync_log row still says SUCCESS — "
        "an empty cache certified as complete. Write the IN_PROGRESS marker BEFORE the delete."
    )
    # Concretely, on the correct ordering the delete never ran at all.
    assert len(rows) == 2


@pytest.mark.anyio
async def test_caught_failure_also_invalidates_the_marker(monkeypatch, in_memory_db, patched_config):
    """The pre-existing half of the guarantee, pinned so a refactor of
    `run_sync`'s terminal handler can't quietly drop it. This one passes with or
    without the write-ahead write — that is the point of keeping it separate
    from the two ordering tests above."""
    from ts_admin.services.sync_status import metadata_is_authoritative

    _seed_metadata_success_marker()
    pages = [[_meta_obj("lb-1", "LIVEBOARD")], [_meta_obj("model-1", "WORKSHEET")]]
    _install_metadata_client(monkeypatch, pages, raise_after=1)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    assert _job_status(job_id)[0] == "FAILED"
    assert _metadata_rows()  # truncated, not empty
    assert _metadata_marker_status() == "FAILED"
    assert metadata_is_authoritative(cluster_id=CLUSTER_ID, org_id=0) is False


@pytest.mark.anyio
async def test_completed_metadata_sync_recertifies_the_cache(monkeypatch, in_memory_db, patched_config):
    """The recovery half: after a full re-sync the cache is authoritative again,
    so the refusal guards clear on their own without any manual intervention."""
    from ts_admin.services.sync_status import metadata_is_authoritative

    pages = [
        [_meta_obj("lb-1", "LIVEBOARD")],
        [_meta_obj("model-1", "WORKSHEET")],
    ]
    _install_metadata_client(monkeypatch, pages, raise_after=1)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id)
    assert metadata_is_authoritative(cluster_id=CLUSTER_ID, org_id=0) is False

    # Now the same crawl, uninterrupted.
    _install_metadata_client(monkeypatch, pages)
    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    assert _job_status(job_id)[0] == "COMPLETE"
    assert {r.object_type for r in _metadata_rows()} == {"LIVEBOARD", "WORKSHEET"}
    assert _metadata_marker_status() == "SUCCESS"
    assert metadata_is_authoritative(cluster_id=CLUSTER_ID, org_id=0) is True


@pytest.mark.anyio
async def test_genuine_auth_failure_still_expires_cluster(monkeypatch, in_memory_db, patched_config):
    """TSAuthenticationError → job FAILED AND cluster flipped to EXPIRED (guards the distinction)."""

    async def _boom(*, org_id: int, job_id: str) -> None:
        raise TSAuthenticationError("session expired")

    monkeypatch.setattr("ts_admin.services.sync_service._sync_metadata", _boom)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=349890686, job_id=job_id)

    status, _ = _job_status(job_id)
    assert status == "FAILED"
    assert connection_status.get(CLUSTER_ID).state is ConnectionState.EXPIRED
