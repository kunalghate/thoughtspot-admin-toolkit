"""
The fail-closed contract for a truncated metadata cache (S23).

`_sync_metadata` deletes every row for the org and re-pages in spec order
(LIVEBOARD, ANSWER, then the five logical-table subtypes), committing per page.
An interrupted sync therefore leaves the cache non-empty but TRUNCATED — and
five operations derive their *input set* from that cache, so they would silently
act on a subset: transfer would leave objects behind on a departing user, a
NO_ACCESS revoke would leave access live, and downstream-delete would report "no
dependents" for a root that has plenty.

The three read/preview sites RAISE. The two `execute_*` sites are different:
they only ever run as Starlette background tasks, AFTER the 202 has been sent,
so a raise there can never become a response and would strand the Job row in
QUEUED forever. Their real refusal lives in the routers (`api/sharing.py::
execute`, `api/users.py::transfer_execute`) — see
`tests/integration/test_stale_cache_endpoints.py`, which is the ONLY place that
class of bug is visible. The service-level guard is kept as defense in depth and
marks the job FAILED instead of raising.

Read paths must NOT refuse — browsing a partial cache is still useful, it just
gets flagged.

Deliberately NOT covered: `archiver_service`. NOTE: the reason is NOT that its
input is superset-correct. `search_metadata` paginates WITHIN each spec
(`ts_client/client.py:~352-375`) and `_sync_metadata` commits per page, so an
interruption can leave a strict SUBSET of the org's liveboards, not a superset.
The exemption holds for a different reason: truncation only ever NARROWS the set
of objects the archiver offers, so it under-archives. Missing a candidate is
recoverable (re-sync and run again); acting on a set the user believes is
complete is not. The archiver fails safe by construction; the five sites above
fail dangerous.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, create_engine

from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cache.ts_user import CachedUser
from ts_admin.models.sync_log import SyncLog
from ts_admin.ts_client.exceptions import StaleCacheError

CLUSTER_ID = "c1"
ORG_ID = 0


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
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


@pytest.fixture(autouse=True)
def patched_config(monkeypatch):
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
    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    return config


@pytest.fixture(autouse=True)
def fake_ts_client(monkeypatch):
    """No live calls. Every method a guarded site can reach is a no-op, so any
    exception that does escape is ours, not the network's."""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def fetch_dependents(self, *, objects):
            return {}

        async def fetch_permissions(self, *, ts_guid, object_type):
            return []

        async def assign_metadata_owner(self, *, object_ids, new_owner_identifier):
            return None

        async def share_objects(self, *, object_ids, principal_ids, permission):
            return None

    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)
    return FakeClient


@pytest.fixture(autouse=True)
def seeded(in_memory_db):
    """A TRUNCATED cache: liveboards + answers present, models/tables absent.
    Row counts alone therefore look perfectly healthy."""
    now = datetime.now(tz=timezone.utc)
    with Session(in_memory_db) as s:
        for guid, name in [("u-alice", "alice"), ("u-bob", "bob")]:
            s.add(
                CachedUser(
                    cluster_id=CLUSTER_ID,
                    ts_guid=guid,
                    username=name,
                    display_name=name.title(),
                    email=f"{name}@co.com",
                    status="ACTIVE",
                    synced_at=now,
                )
            )
        s.add(
            CachedMetadata(
                cluster_id=CLUSTER_ID,
                org_id=ORG_ID,
                ts_guid="lb-1",
                name="Sales",
                object_type="LIVEBOARD",
                owner_guid="u-alice",
                owner_name="Alice",
                tag_names=json.dumps([]),
                synced_at=now,
            )
        )
        s.commit()


def _certify(engine) -> None:
    """Write the SUCCESS marker a completed metadata sync leaves behind."""
    with Session(engine) as s:
        s.add(
            SyncLog(
                cluster_id=CLUSTER_ID,
                org_id=ORG_ID,
                entity_type="metadata",
                status="SUCCESS",
                record_count=1,
            )
        )
        s.commit()


def _job(job_type: str) -> str:
    from ts_admin.services.job_service import create_job

    return create_job(job_type=job_type, parameters={}, cluster_id=CLUSTER_ID)


# ── The five refusal sites ────────────────────────────────────────────────────


async def _call_resolve_downstream() -> None:
    from ts_admin.services import deleter_service

    await deleter_service.resolve_downstream(
        root_guid="ws-1", root_type="LOGICAL_TABLE", cluster_id=CLUSTER_ID, org_id=ORG_ID
    )


async def _call_preview_transfer() -> None:
    from ts_admin.services import user_management_service as svc

    svc.preview_transfer(cluster_id=CLUSTER_ID, org_id=ORG_ID, from_user_guid="u-alice")


async def _call_execute_transfer(job_id: str) -> None:
    from ts_admin.services import user_management_service as svc

    await svc.execute_transfer(
        job_id,
        CLUSTER_ID,
        ORG_ID,
        "u-alice",
        "bob",
        ["lb-1"],
    )


async def _call_preview_share() -> None:
    from ts_admin.services import bulk_sharing_service as svc

    await svc.preview_share(
        cluster_id=CLUSTER_ID,
        org_id=ORG_ID,
        object_guids=["lb-1"],
        principal_guids=["u-bob"],
        mode="READ_ONLY",
    )


async def _call_execute_share(job_id: str) -> None:
    from ts_admin.services import bulk_sharing_service as svc

    await svc.execute_share(
        job_id,
        CLUSTER_ID,
        ORG_ID,
        ["lb-1"],
        ["u-bob"],
        "READ_ONLY",
    )


# Sites reached synchronously from a request, where raising IS the response.
REFUSAL_SITES = [
    pytest.param(_call_resolve_downstream, id="deleter.resolve_downstream"),
    pytest.param(_call_preview_transfer, id="user_management.preview_transfer"),
    pytest.param(_call_preview_share, id="bulk_sharing.preview_share"),
]

# Sites that only ever run as background tasks. `job_type` matters: the record
# written just past the guard differs per site (ShareRecord vs UserActionRecord).
JOB_SITES = [
    pytest.param(_call_execute_share, "bulk_share", id="bulk_sharing.execute_share"),
    pytest.param(_call_execute_transfer, "user_transfer_ownership", id="user_management.execute_transfer"),
]


def _fail_marker(engine) -> None:
    with Session(engine) as s:
        s.add(
            SyncLog(
                cluster_id=CLUSTER_ID,
                org_id=ORG_ID,
                entity_type="metadata",
                status="FAILED",
                error="upstream dropped the connection mid-crawl",
            )
        )
        s.commit()


@pytest.mark.parametrize("call", REFUSAL_SITES)
@pytest.mark.anyio
async def test_refuses_without_a_success_marker(call):
    """Fail-closed. A raise, never a logged warning — a warning nobody reads is
    indistinguishable from the silent data loss this exists to prevent."""
    with pytest.raises(StaleCacheError) as excinfo:
        await call()
    assert excinfo.value.entity_type == "metadata"
    assert excinfo.value.status == "NOT_SYNCED"


@pytest.mark.parametrize("call", REFUSAL_SITES)
@pytest.mark.anyio
async def test_proceeds_past_the_guard_once_certified(call, in_memory_db):
    """Anti-vacuity for the test above: with the marker present the very same
    call runs through. Without this, deleting the marker check entirely would
    still leave the suite green in a suspicious 'everything raises' way."""
    _certify(in_memory_db)
    await call()  # must not raise


@pytest.mark.parametrize("call", REFUSAL_SITES)
@pytest.mark.anyio
async def test_a_failed_marker_still_refuses(call, in_memory_db):
    """SUCCESS is the only certification. A FAILED row must not be read as one."""
    _fail_marker(in_memory_db)
    with pytest.raises(StaleCacheError) as excinfo:
        await call()
    assert excinfo.value.status == "FAILED"


# ── The two background-task sites ─────────────────────────────────────────────
#
# These CANNOT raise: Starlette runs them after the 202 response has been sent,
# so an escaping exception becomes "Caught handled exception, but response
# already started" and leaves the Job stuck in QUEUED with error=None until the
# next server restart reaps it. The guard therefore fails the job instead — and
# the actual refusal, the one the user sees, is asserted at the endpoint in
# tests/integration/test_stale_cache_endpoints.py.


def _job_row(job_id: str):
    from ts_admin.database import get_session
    from ts_admin.models.job import Job

    with get_session() as session:
        return session.get(Job, job_id)


@pytest.mark.parametrize(("call", "job_type"), JOB_SITES)
@pytest.mark.anyio
async def test_background_site_fails_the_job_instead_of_stranding_it(call, job_type):
    """Never QUEUED-with-no-error. A stranded job is worse than a failed one:
    the UI polls it forever and the admin has no idea the work never started."""
    job_id = _job(job_type)
    await call(job_id)  # must NOT raise — nothing is listening

    job = _job_row(job_id)
    assert job.status == "FAILED"
    assert job.error_type == "StaleCacheError"
    assert job.error  # an actionable message, not None


@pytest.mark.parametrize(("call", "job_type"), JOB_SITES)
@pytest.mark.anyio
async def test_background_site_guard_runs_before_any_work(call, job_type):
    """GUARD PLACEMENT, pinned. Moving `require_authoritative_metadata` below
    `mark_running` (or below the record write) leaves every other assertion in
    this file green — a reviewer did exactly that and the suite stayed at 368
    passed. These two assertions are what make placement observable:

      * `total == 0` and `started_at is None` — `mark_running` never ran.
      * no ShareRecord / UserActionRecord — the audit-trail row that the very
        next lines write was never created for an operation that never happened.
    """
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.share_record import ShareRecord
    from ts_admin.models.user_action_record import UserActionRecord

    job_id = _job(job_type)
    await call(job_id)

    job = _job_row(job_id)
    assert job.total == 0, "mark_running() ran — the guard is placed after it"
    assert job.started_at is None, "mark_running() ran — the guard is placed after it"

    with get_session() as session:
        assert session.exec(select(ShareRecord)).all() == []
        assert session.exec(select(UserActionRecord)).all() == []


@pytest.mark.parametrize(("call", "job_type"), JOB_SITES)
@pytest.mark.anyio
async def test_background_site_proceeds_once_certified(call, job_type, in_memory_db):
    """Anti-vacuity: with the SUCCESS marker the same call runs to completion,
    so the two tests above are not passing because everything always fails."""
    _certify(in_memory_db)
    job_id = _job(job_type)
    await call(job_id)

    job = _job_row(job_id)
    assert job.status != "FAILED"
    assert job.error_type != "StaleCacheError"


@pytest.mark.parametrize(("call", "job_type"), JOB_SITES)
@pytest.mark.anyio
async def test_background_site_fails_the_job_on_a_failed_marker(call, job_type, in_memory_db):
    """SUCCESS is the only certification — a FAILED row must not read as one."""
    _fail_marker(in_memory_db)
    job_id = _job(job_type)
    await call(job_id)
    assert _job_row(job_id).error_type == "StaleCacheError"


# ── The read path must NOT refuse ─────────────────────────────────────────────


class TestReadPathIsFlagOnly:
    def test_search_still_returns_rows_without_a_marker(self):
        from ts_admin.services.metadata_service import MetadataService

        items, total = MetadataService.search(cluster_id=CLUSTER_ID, org_id=ORG_ID)
        assert total == 1
        assert items[0].ts_guid == "lb-1"

    def test_search_signature_is_unchanged(self):
        """`search` returns tuple[list, int] — the flag rides on the API response
        model, not on this signature. Changing it would break every caller."""
        from ts_admin.services.metadata_service import MetadataService

        result = MetadataService.search(cluster_id=CLUSTER_ID, org_id=ORG_ID)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_stats_flags_the_truncation_without_raising(self, in_memory_db):
        from ts_admin.services.metadata_service import MetadataService

        stats = MetadataService.stats(cluster_id=CLUSTER_ID, org_id=ORG_ID)
        assert stats["total"] == 1  # still usable
        assert stats["cache_authoritative"] is False

        _certify(in_memory_db)
        assert MetadataService.stats(cluster_id=CLUSTER_ID, org_id=ORG_ID)["cache_authoritative"] is True
