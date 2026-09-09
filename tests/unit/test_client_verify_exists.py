"""
Regression tests for `ThoughtSpotClient.verify_metadata_exists`.

Measured against SE Demo: `metadata/search` does NOT silently omit an unknown
identifier — one bogus GUID alongside real ones rejects the WHOLE request with
a 400. The v2 docs do not specify either behaviour, so this method bisects a
rejected batch rather than assuming: it is correct whichever way ThoughtSpot
behaves, and it isolates each dead id to a request of one.

Why it matters: `assign_metadata_owner` sends 50 ids per call and
`execute_transfer` buckets the whole chunk on failure, with no bisection on that
path. Without this pre-flight, one object deleted upstream since the last sync
costs 49 healthy transfers.
"""

from __future__ import annotations

import asyncio

import pytest

from ts_admin.ts_client.client import ThoughtSpotClient
from ts_admin.ts_client.exceptions import TSInvalidParametersError, TSServerError

REAL = {"a", "b", "c", "d"}


def _client(dead: set[str], *, calls: list[list[str]] | None = None, raise_500: bool = False):
    """A client whose `_request` mimics TS: 400 the batch if it holds a dead id."""
    client = ThoughtSpotClient.__new__(ThoughtSpotClient)

    async def _fake_request(method, path, *, json=None, context=""):
        ids = [m["identifier"] for m in json["metadata"]]
        if calls is not None:
            calls.append(ids)
        if raise_500:
            raise TSServerError(status_code=503, body="upstream is unwell")
        if any(i in dead for i in ids):
            raise TSInvalidParametersError(f"Invalid parameters for POST {path}")
        return [{"metadata_id": i} for i in ids]

    client._request = _fake_request  # type: ignore[method-assign]
    return client


def test_all_present():
    c = _client(dead=set())
    found = asyncio.run(c.verify_metadata_exists(object_ids=["a", "b", "c"]))
    assert found == {"a", "b", "c"}


def test_isolates_a_single_dead_id():
    c = _client(dead={"x"})
    found = asyncio.run(c.verify_metadata_exists(object_ids=["a", "b", "x", "c"]))
    assert found == {"a", "b", "c"}, "the three live objects must still be reported live"


def test_isolates_several_dead_ids():
    c = _client(dead={"x", "y"})
    found = asyncio.run(c.verify_metadata_exists(object_ids=["a", "x", "b", "y", "c", "d"]))
    assert found == {"a", "b", "c", "d"}


def test_every_id_dead():
    c = _client(dead={"x", "y"})
    assert asyncio.run(c.verify_metadata_exists(object_ids=["x", "y"])) == set()


def test_empty_input_makes_no_call():
    calls: list[list[str]] = []
    c = _client(dead=set(), calls=calls)
    assert asyncio.run(c.verify_metadata_exists(object_ids=[])) == set()
    assert calls == []


def test_bisects_rather_than_probing_one_by_one():
    """O(k log n), not O(n) — the point of bisecting instead of a loop."""
    calls: list[list[str]] = []
    ids = [f"id-{i}" for i in range(32)]
    c = _client(dead={"id-17"}, calls=calls)

    found = asyncio.run(c.verify_metadata_exists(object_ids=ids))

    assert found == set(ids) - {"id-17"}
    # One whole-batch attempt plus the bisect path down to the single bad id —
    # far fewer than the 32 a per-object probe would make.
    assert len(calls) < 15, f"expected a bisect, got {len(calls)} requests"


def test_a_server_error_is_not_reported_as_missing_objects():
    """A 503 must propagate. Reporting it as 'all of these are gone' would make
    the pre-flight silently drop an entire transfer."""
    c = _client(dead=set(), raise_500=True)
    with pytest.raises(TSServerError):
        asyncio.run(c.verify_metadata_exists(object_ids=["a", "b"]))
