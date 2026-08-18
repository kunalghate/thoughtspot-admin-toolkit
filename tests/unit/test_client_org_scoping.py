"""
Regression test: a 404 from ThoughtSpot must carry ThoughtSpot's own explanation.

A support bundle from a failed `sync:users` reported only:

    TSObjectNotFoundError: resource not found: '/api/rest/2.0/users/search'

…which is not enough to tell a genuinely missing object from a rejected request
parameter, so the failure could not be diagnosed from the bundle at all. The 400
branch already surfaced the response body; the 404 branch threw it away.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ts_admin.ts_client.auth import BearerTokenAuth
from ts_admin.ts_client.client import ThoughtSpotClient
from ts_admin.ts_client.exceptions import TSObjectNotFoundError

BASE = "https://ts.example.com"


@respx.mock
@pytest.mark.anyio
async def test_404_error_includes_thoughtspot_response_body():
    respx.post(f"{BASE}/api/rest/2.0/users/search").mock(
        return_value=httpx.Response(404, json={"error": {"code": 10002, "message": "Org not found"}})
    )

    with pytest.raises(TSObjectNotFoundError) as exc_info:
        async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
            async for _ in client.search_users(org_id=0):
                pass

    assert "Org not found" in str(exc_info.value)
    assert "/api/rest/2.0/users/search" in str(exc_info.value)


@respx.mock
@pytest.mark.anyio
async def test_404_without_a_body_still_names_the_path():
    respx.get(f"{BASE}/api/rest/2.0/system").mock(return_value=httpx.Response(404, text=""))

    with pytest.raises(TSObjectNotFoundError) as exc_info:
        async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
            await client.test_connection()

    assert "/api/rest/2.0/system" in str(exc_info.value)


@respx.mock
@pytest.mark.anyio
async def test_404_surfaces_the_guid_thoughtspot_could_not_resolve():
    """A 404 from a *search* endpoint usually means ThoughtSpot choked on an
    object referenced by a row in the result set, not on anything we asked for.

    Observed live on a demo cluster: `users/search` with `org_identifiers: [0]`
    404s at record_offset 249 and succeeds at every other offset, because one
    user's membership points at a group id the server cannot resolve. The body's
    nested debug array is the ONLY thing naming that id — without lifting it into
    the message the failure is undiagnosable from a support bundle.
    """
    guid = "53d3d10e-165a-4498-b8ae-af12074b7e69"
    body = {"error": {"message": {"debug": {"code": 13003, "debug": f'["{guid}", ""]'}}}}
    respx.post(f"{BASE}/api/rest/2.0/users/search").mock(return_value=httpx.Response(404, json=body))

    with pytest.raises(TSObjectNotFoundError) as exc_info:
        async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
            async for _ in client.search_users(org_id=0):
                pass

    assert guid in str(exc_info.value)


@respx.mock
@pytest.mark.anyio
async def test_404_without_a_debug_guid_array_still_reports_the_body():
    """No guids named — the raw body must still come through unharmed."""
    respx.post(f"{BASE}/api/rest/2.0/users/search").mock(
        return_value=httpx.Response(404, json={"error": {"message": {"debug": {"code": 10002}}}})
    )

    with pytest.raises(TSObjectNotFoundError) as exc_info:
        async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
            async for _ in client.search_users(org_id=0):
                pass

    assert "10002" in str(exc_info.value)


# ── Org scoping is applied client-side ────────────────────────────────────────


def _user(uid: str, orgs: list[int] | None):
    rec: dict = {"id": uid, "name": f"user-{uid}", "display_name": uid, "email": ""}
    if orgs is not None:
        rec["orgs"] = [{"id": o, "name": f"org-{o}"} for o in orgs]
    return rec


@respx.mock
@pytest.mark.anyio
async def test_search_users_never_sends_org_identifiers():
    """The server-side org filter makes ThoughtSpot resolve every result's org and
    group membership, and one unresolvable membership 404s the entire page —
    observed on a live cluster where `org_identifiers: [0]` fails at record 250
    while the unfiltered call returns all 325 users. Scoping must stay client-side.
    """
    seen: list[dict] = []

    def _handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=[_user("a", [0]), _user("b", [7])])

    respx.post(f"{BASE}/api/rest/2.0/users/search").mock(side_effect=_handler)

    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        async for _ in client.search_users(org_id=0):
            pass

    assert seen, "no request was made"
    for body in seen:
        assert "org_identifiers" not in body, f"org_identifiers must not be sent, got {body}"


@respx.mock
@pytest.mark.anyio
async def test_search_users_filters_to_the_requested_org():
    respx.post(f"{BASE}/api/rest/2.0/users/search").mock(
        return_value=httpx.Response(200, json=[_user("a", [0]), _user("b", [7]), _user("c", [0, 7])])
    )

    got = []
    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        async for page in client.search_users(org_id=0):
            got += [u.id for u in page]

    assert got == ["a", "c"]


@respx.mock
@pytest.mark.anyio
async def test_search_users_without_an_org_returns_every_user():
    respx.post(f"{BASE}/api/rest/2.0/users/search").mock(
        return_value=httpx.Response(200, json=[_user("a", [0]), _user("b", [7])])
    )

    got = []
    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        async for page in client.search_users():
            got += [u.id for u in page]

    assert got == ["a", "b"]


@respx.mock
@pytest.mark.anyio
async def test_search_users_keeps_records_with_no_orgs_key():
    """Orgs-disabled clusters omit `orgs` entirely — dropping those users would
    empty the sync rather than scope it."""
    respx.post(f"{BASE}/api/rest/2.0/users/search").mock(
        return_value=httpx.Response(200, json=[_user("a", None), _user("b", [7])])
    )

    got = []
    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        async for page in client.search_users(org_id=0):
            got += [u.id for u in page]

    assert got == ["a"]
