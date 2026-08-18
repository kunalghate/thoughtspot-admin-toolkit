"""
The sync allowlist and the dispatch table must agree, in both directions.

`api.sync.VALID_ENTITIES` is the trigger allowlist AND the render set for
GET /sync; `sync_service.sync_handlers()` is what `run_sync` can actually
dispatch. "permissions" sat in the first with nothing behind it in the second,
so POST /api/v1/sync/permissions returned 200 with a job id for a job that
`run_sync` had already failed with "Unknown entity type" before the response
rendered, and every cluster+org showed a `permissions` row that could never
leave NOT_SYNCED.

The two sets are kept as independent literals ON PURPOSE — deriving one from the
other would make this file vacuous. These tests are the coupling.
"""

from __future__ import annotations

import pytest

from ts_admin.api.sync import STANDARD_ENTITIES, VALID_ENTITIES
from ts_admin.services.sync_service import sync_handlers

SYNC_HANDLERS = sync_handlers()


def test_the_registries_are_not_empty():
    """A parametrised test over an empty set is zero tests and a green run."""
    assert VALID_ENTITIES
    assert SYNC_HANDLERS
    assert STANDARD_ENTITIES


@pytest.mark.parametrize("entity", sorted(VALID_ENTITIES))
def test_every_advertised_entity_has_a_handler(entity):
    """Adding an entity to the API allowlist without a handler fails here."""
    assert entity in SYNC_HANDLERS, f"{entity!r} is advertised by the API but run_sync cannot dispatch it"
    assert callable(SYNC_HANDLERS[entity])


@pytest.mark.parametrize("entity", sorted(SYNC_HANDLERS))
def test_every_handler_is_reachable_from_the_api(entity):
    """A handler no endpoint can reach is dead code, not a feature."""
    assert entity in VALID_ENTITIES, f"{entity!r} has a handler but no endpoint accepts it"


@pytest.mark.parametrize("entity", sorted(STANDARD_ENTITIES))
def test_sync_all_only_fans_out_to_valid_entities(entity):
    """POST /sync/all builds its own set; it must stay a subset of the allowlist."""
    assert entity in VALID_ENTITIES


def test_permissions_is_not_a_sync_entity():
    """Named explicitly: permissions are fetched live, never cached (ADR-004)."""
    assert "permissions" not in VALID_ENTITIES
    assert "permissions" not in SYNC_HANDLERS


async def test_run_sync_fails_the_job_for_an_entity_with_no_handler(monkeypatch):
    """
    The failure mode the allowlist protects against, pinned at the service.

    `run_sync` is a background-task target, so this failure is invisible to the
    caller — it can only surface on the Job row.
    """
    from ts_admin.services import sync_service

    failed: list[tuple[str, str]] = []
    monkeypatch.setattr(sync_service, "mark_failed", lambda job_id, exc: failed.append((job_id, str(exc))))

    await sync_service.run_sync(entity_type="permissions", org_id=0, job_id="job-1", cluster_id="c1")

    assert failed == [("job-1", "Unknown entity type: 'permissions'")]
