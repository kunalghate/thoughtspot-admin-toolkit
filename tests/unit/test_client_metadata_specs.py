"""
One metadata spec's failure must not destroy the whole metadata crawl (W3).

`search_metadata` issues seven independent `metadata/search` requests through a
single generator. A failure in any one of them used to propagate out of that
generator and discard every page already yielded, so an admin could end with no
metadata cache at all rather than a partial one.

That is not hypothetical. The v2 reference tags the `subtypes` enum value
`SQL_VIEW` "Version: 10.11.0.cl or later" (read off the `searchMetadata` spec on
2026-08-18) and we query it unconditionally. An out-of-enum *value* is rejected
rather than ignored — the "v2 silently ignores unknown request keys" behaviour is
about *keys*, and `client.LOGICAL_TABLE_SUBTYPES` exists precisely because an
enum-typed field rejects values it does not know — so on a cluster below 10.11
that one spec took LIVEBOARD, ANSWER, WORKSHEET and the rest down with it.

Both live clusters are 26.8, so these tests are the only place the behaviour is
observable at all.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ts_admin.ts_client.auth import BearerTokenAuth
from ts_admin.ts_client.client import ThoughtSpotClient, _parse_release_version, _supports_subtype
from ts_admin.ts_client.exceptions import TSAuthenticationError, TSPartialSuccessError

BASE = "https://ts.example.com"
SEARCH = f"{BASE}/api/rest/2.0/metadata/search"

# The seven specs in the order `search_metadata` issues them. SQL_VIEW is sixth,
# so "the specs after the failing one still ran" is a real assertion here.
ALL_SPECS = [
    "LIVEBOARD",
    "ANSWER",
    "WORKSHEET",
    "ONE_TO_ONE_LOGICAL",
    "AGGR_WORKSHEET",
    "SQL_VIEW",
    "USER_DEFINED",
]


def _obj(guid: str) -> dict:
    return {
        "metadata_id": guid,
        "metadata_name": f"obj-{guid}",
        "metadata_header": {"id": guid, "name": f"obj-{guid}", "author": "u1", "authorDisplayName": "U"},
    }


def _spec_of(request: httpx.Request) -> str:
    """The spec a metadata/search request represents — its subtype, or its type."""
    metadata_filter = json.loads(request.content)["metadata"][0]
    subtypes = metadata_filter.get("subtypes")
    return subtypes[0] if subtypes else metadata_filter["type"]


def _responder(
    seen: list[str],
    *,
    rows: dict[str, list] | None = None,
    fail_on: str | None = None,
    failure: httpx.Response | None = None,
):
    """Record every spec that reaches the wire; optionally fail exactly one."""

    def handler(request: httpx.Request) -> httpx.Response:
        spec = _spec_of(request)
        seen.append(spec)
        if spec == fail_on:
            return failure or httpx.Response(400, json={"error": {"message": "Invalid parameter values: subtypes"}})
        return httpx.Response(200, json=(rows or {}).get(spec, []))

    return handler


async def _crawl(release_version: str | None) -> list[str]:
    """Run a full crawl, returning the GUIDs it yielded."""
    collected: list[str] = []
    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        async for page in client.search_metadata(release_version=release_version):
            collected.extend(o.id for o in page)
    return collected


# ── Per-spec resilience ───────────────────────────────────────────────────────


@respx.mock
@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(400, json={"error": {"message": "Invalid parameter values: subtypes"}}),
        httpx.Response(404, json={"error": {"message": "not found"}}),
        httpx.Response(500, text="internal error"),
    ],
    ids=["400", "404", "500"],
)
async def test_a_failing_spec_does_not_discard_the_specs_that_worked(failure):
    """The W3 bug: LIVEBOARD/ANSWER/WORKSHEET must survive a broken SQL_VIEW."""
    seen: list[str] = []
    rows = {"LIVEBOARD": [_obj("lb1")], "ANSWER": [_obj("a1")], "WORKSHEET": [_obj("w1")]}
    respx.post(SEARCH).mock(side_effect=_responder(seen, rows=rows, fail_on="SQL_VIEW", failure=failure))

    collected: list[str] = []
    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        with pytest.raises(TSPartialSuccessError) as excinfo:
            async for page in client.search_metadata(release_version="26.8.0.cl"):
                collected.extend(o.id for o in page)

    assert collected == ["lb1", "a1", "w1"]
    # USER_DEFINED comes AFTER SQL_VIEW — this is the assertion that the crawl
    # continued rather than unwinding at the first failure.
    assert seen == ALL_SPECS

    # …and the failure is not swallowed: it names the spec, and the specs that
    # did work are named too, so a caller can tell partial from total.
    assert [f for f in excinfo.value.failed if f.startswith("SQL_VIEW")] == excinfo.value.failed
    assert "SQL_VIEW" not in excinfo.value.succeeded
    assert "LIVEBOARD" in excinfo.value.succeeded


@respx.mock
@pytest.mark.anyio
async def test_a_clean_crawl_raises_nothing():
    """Anti-vacuity for the tests above: the raise is conditional, not constant."""
    seen: list[str] = []
    respx.post(SEARCH).mock(side_effect=_responder(seen, rows={"LIVEBOARD": [_obj("lb1")]}))

    assert await _crawl("26.8.0.cl") == ["lb1"]
    assert seen == ALL_SPECS


@respx.mock
@pytest.mark.anyio
async def test_an_expired_session_is_not_recorded_as_a_spec_failure():
    """Auth is a whole-cluster condition — the remaining specs would fail
    identically, so it must propagate immediately instead of being retried six
    more times and reported as six broken object types."""
    seen: list[str] = []
    respx.post(SEARCH).mock(
        side_effect=_responder(seen, fail_on="ANSWER", failure=httpx.Response(401, text="session expired"))
    )

    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        with pytest.raises(TSAuthenticationError):
            async for _ in client.search_metadata(release_version="26.8.0.cl"):
                pass

    assert seen == ["LIVEBOARD", "ANSWER"]


# ── Version gate ──────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.anyio
@pytest.mark.parametrize("release_version", ["10.10.0.cl", "10.9.3.cl", "9.4.0.sw", "10.10.5.sw"])
async def test_the_sql_view_spec_is_not_issued_below_10_11(release_version):
    seen: list[str] = []
    respx.post(SEARCH).mock(side_effect=_responder(seen))

    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        with pytest.raises(TSPartialSuccessError) as excinfo:
            async for _ in client.search_metadata(release_version=release_version):
                pass

    assert "SQL_VIEW" not in seen
    # Every other spec still runs — skipping one must not skip the pass either side.
    assert seen == [s for s in ALL_SPECS if s != "SQL_VIEW"]
    # Not asking is still not fetching: a skipped spec is reported exactly like a
    # failed one, so the caller cannot mistake this crawl for a complete one.
    assert excinfo.value.failed == ["SQL_VIEW: not supported before release 10.11.0"]


@respx.mock
@pytest.mark.anyio
@pytest.mark.parametrize("release_version", ["10.11.0.cl", "10.11.0.sw", "10.11", "10.12.0.cl", "26.8.0.cl"])
async def test_the_sql_view_spec_is_issued_at_or_above_10_11(release_version):
    seen: list[str] = []
    respx.post(SEARCH).mock(side_effect=_responder(seen))

    assert await _crawl(release_version) == []
    assert seen == ALL_SPECS


@respx.mock
@pytest.mark.anyio
@pytest.mark.parametrize("release_version", ["test", "unknown", "", None, "cloud", "10", "vNext.1.2", "26·8·0"])
async def test_an_unreadable_release_version_still_issues_the_spec(release_version):
    """Unknown cuts towards "current". Guessing the other way would cost a 26.8
    customer every SQL view over a version string we failed to parse — and the
    reference's own example value for the field is the literal string "test"."""
    seen: list[str] = []
    respx.post(SEARCH).mock(side_effect=_responder(seen))

    assert await _crawl(release_version) == []
    assert seen == ALL_SPECS


@pytest.mark.parametrize(
    ("release_version", "expected"),
    [
        ("26.8.0.cl", (26, 8, 0)),
        ("10.11.0.sw", (10, 11, 0)),
        ("10.11", (10, 11, 0)),  # short forms pad, so the >= compare stays sound
        ("9.4.0.cl", (9, 4, 0)),
        ("10.11.0.1.cl", (10, 11, 0)),  # a fourth component is ignored, not fatal
        (" 26.8.0.cl ", (26, 8, 0)),
        ("test", None),
        ("unknown", None),
        ("", None),
        (None, None),
        ("10", None),  # one component cannot be compared against major.minor
        ("v10.11.0", None),
        ("²⁶.⁸.⁰", None),  # str.isdigit() is True for these; int() would raise
    ],
)
def test_parse_release_version(release_version, expected):
    assert _parse_release_version(release_version) == expected


def test_only_sql_view_is_version_gated():
    """The other five subtypes carry no version tag in the reference, so gating
    them would be inventing a restriction ThoughtSpot does not have."""
    for subtype in ("WORKSHEET", "ONE_TO_ONE_LOGICAL", "AGGR_WORKSHEET", "USER_DEFINED"):
        assert _supports_subtype(subtype, "1.0.0.cl") is True
    assert _supports_subtype("SQL_VIEW", "1.0.0.cl") is False
