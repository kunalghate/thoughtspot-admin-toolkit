"""
Wire-shape guards for the ThoughtSpot v2 write endpoints.

Three of these paths did not exist. Probed live against ps-internal-prod
(26.8.0.cl) with an empty-body POST:

    404  /api/rest/2.0/security/share          ← bulk sharing called this
    404  /api/rest/2.0/tags/delete             ← delete-tag-only called this
    404  /api/rest/2.0/users/delete            ← bulk user delete called this

All three succeed with a bare **204 No Content**, so the response carries no
evidence whatsoever that the right thing happened — a stub returning `{}` is
indistinguishable from a correct call. The REQUEST is the only thing worth
asserting, which is why every test here asserts the exact URL and then asserts
that the dead keys appear in NO request body.

`respx` fails any request that matches no registered route, so calling a wrong
path fails the test on its own; the dead routes are registered anyway so the
failure names the path instead of reporting a generic "not mocked".
"""

from __future__ import annotations

import json as jsonlib

import httpx
import pytest
import respx

from ts_admin.ts_client.auth import BearerTokenAuth
from ts_admin.ts_client.client import ThoughtSpotClient
from ts_admin.ts_client.models import MetadataType, SharePermission

BASE = "https://ts.example.com"

# Keys that were on the wire and are not in any v2 schema. None of them may ever
# appear in a request body again.
DEAD_KEYS = (
    "metadata_list",  # security/metadata/share — the key is `metadata`
    "users",  # users/{id}/delete — identifier is a path segment, there is no body
    "tag",  # tags/{id}/delete — likewise
    "metadata_type",  # principals/fetch-permissions — the key is `default_metadata_type`
    "export_associated_objects",  # metadata/tml/export — the key is `export_associated`
)


def _client() -> ThoughtSpotClient:
    return ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t"))


def _body(route) -> dict:
    """The JSON body of the one request that hit `route`."""
    content = route.calls.last.request.content
    return jsonlib.loads(content) if content else {}


def _assert_no_dead_keys(body: dict) -> None:
    for key in DEAD_KEYS:
        assert key not in body, f"{key!r} is not a v2 schema key and must not be sent"


# ── share_objects ─────────────────────────────────────────────────────────────


@respx.mock
async def test_share_posts_metadata_share_never_security_share():
    dead = respx.post(f"{BASE}/api/rest/2.0/security/share").mock(return_value=httpx.Response(404))
    live = respx.post(f"{BASE}/api/rest/2.0/security/metadata/share").mock(return_value=httpx.Response(204))

    async with _client() as client:
        await client.share_objects(
            object_ids=["lb-1", "lb-2"],
            principal_ids=["u-1"],
            permission=SharePermission.MODIFY,
            message="handover",
            notify=True,
        )

    assert dead.call_count == 0, "/security/share is a 404 on every cluster"
    assert live.call_count == 1

    body = _body(live)
    _assert_no_dead_keys(body)
    assert body["metadata"] == [{"identifier": "lb-1"}, {"identifier": "lb-2"}]
    assert body["permissions"] == [{"principal": {"identifier": "u-1"}, "share_mode": "MODIFY"}]


@respx.mock
async def test_share_always_sends_the_required_message_key():
    """`message` is in the schema's `required` list — omit it and the call 400s."""
    live = respx.post(f"{BASE}/api/rest/2.0/security/metadata/share").mock(return_value=httpx.Response(204))

    async with _client() as client:
        await client.share_objects(object_ids=["lb-1"], principal_ids=["u-1"])

    assert "message" in _body(live)


@respx.mock
@pytest.mark.parametrize("notify", [True, False])
async def test_share_always_states_notify_on_share_explicitly(notify: bool):
    """The wire default is TRUE, so leaving the key out emails every recipient."""
    live = respx.post(f"{BASE}/api/rest/2.0/security/metadata/share").mock(return_value=httpx.Response(204))

    async with _client() as client:
        await client.share_objects(object_ids=["lb-1"], principal_ids=["u-1"], notify=notify)

    assert _body(live)["notify_on_share"] is notify


@respx.mock
async def test_share_sends_no_type_so_cache_subtypes_can_never_leak():
    """We always pass GUIDs, for which `type` is optional — and our cache's
    object_type values (WORKSHEET, DATASET, …) are not members of this
    endpoint's enum, so sending one would be a 400."""
    live = respx.post(f"{BASE}/api/rest/2.0/security/metadata/share").mock(return_value=httpx.Response(204))

    async with _client() as client:
        await client.share_objects(object_ids=["ws-1"], principal_ids=["u-1"])

    assert all("type" not in item for item in _body(live)["metadata"])


# ── delete_tag ────────────────────────────────────────────────────────────────


@respx.mock
async def test_delete_tag_puts_the_identifier_in_the_path_and_sends_no_body():
    dead = respx.post(f"{BASE}/api/rest/2.0/tags/delete").mock(return_value=httpx.Response(404))
    live = respx.post(f"{BASE}/api/rest/2.0/tags/tag-todo/delete").mock(return_value=httpx.Response(204))

    async with _client() as client:
        await client.delete_tag(tag_id="tag-todo")

    assert dead.call_count == 0, "/tags/delete is a 404 on every cluster"
    assert live.call_count == 1
    assert live.calls.last.request.content == b"", "deleteTag takes no request body"


@respx.mock
async def test_delete_tag_percent_encodes_the_identifier():
    """A tag name is a legal identifier; an unescaped `/` would rewrite the path."""
    live = respx.post(f"{BASE}/api/rest/2.0/tags/a%2Fb/delete").mock(return_value=httpx.Response(204))

    async with _client() as client:
        await client.delete_tag(tag_id="a/b")

    assert live.call_count == 1


# ── delete_user ───────────────────────────────────────────────────────────────


@respx.mock
async def test_delete_user_puts_the_identifier_in_the_path_and_sends_no_body():
    dead = respx.post(f"{BASE}/api/rest/2.0/users/delete").mock(return_value=httpx.Response(404))
    live = respx.post(f"{BASE}/api/rest/2.0/users/u-1/delete").mock(return_value=httpx.Response(204))

    async with _client() as client:
        await client.delete_user(user_identifier="u-1")

    assert dead.call_count == 0, "/users/delete is a 404 on every cluster"
    assert live.call_count == 1
    assert live.calls.last.request.content == b"", "deleteUser takes no request body"


@respx.mock
async def test_delete_user_percent_encodes_the_identifier():
    """The identifier may be a username, and usernames are not path-safe."""
    live = respx.post(f"{BASE}/api/rest/2.0/users/a%40b.com/delete").mock(return_value=httpx.Response(204))

    async with _client() as client:
        await client.delete_user(user_identifier="a@b.com")

    assert live.call_count == 1


def test_there_is_no_bulk_user_delete():
    """`users/delete` does not exist, so no plural helper may come back."""
    assert not hasattr(ThoughtSpotClient, "delete_users")


# ── principal_permissions ─────────────────────────────────────────────────────

# The full request schema of security/principals/fetch-permissions.
PRINCIPAL_PERMISSION_KEYS = {"principals", "metadata", "record_offset", "record_size", "default_metadata_type"}


@respx.mock
async def test_principal_permissions_sends_no_key_outside_the_schema():
    """v2 silently ignores unknown body keys, so an invented one is a no-op that
    reads like a working filter. `permission_type` is the specific trap: it is a
    real key on security/METADATA/fetch-permissions and no key at all here."""
    live = respx.post(f"{BASE}/api/rest/2.0/security/principals/fetch-permissions").mock(
        return_value=httpx.Response(200, json={"principal_permission_details": []})
    )

    async with _client() as client:
        await client.principal_permissions(principal_identifier="u-1")

    body = _body(live)
    _assert_no_dead_keys(body)
    assert "permission_type" not in body
    assert set(body) <= PRINCIPAL_PERMISSION_KEYS, f"unknown keys: {set(body) - PRINCIPAL_PERMISSION_KEYS}"


@respx.mock
async def test_principal_permissions_sends_default_metadata_type_as_a_bare_string():
    """Measured live: the array form was ignored (23,197 rows, all types); the
    string form filtered (372 rows)."""
    live = respx.post(f"{BASE}/api/rest/2.0/security/principals/fetch-permissions").mock(
        return_value=httpx.Response(200, json={"principal_permission_details": []})
    )

    async with _client() as client:
        await client.principal_permissions(principal_identifier="u-1", default_metadata_type="LIVEBOARD")

    body = _body(live)
    assert body["default_metadata_type"] == "LIVEBOARD"
    assert not isinstance(body["default_metadata_type"], list)
    assert set(body) <= PRINCIPAL_PERMISSION_KEYS


@respx.mock
async def test_principal_permissions_distinguishes_a_direct_share_from_an_inherited_one():
    """`permission` is the EFFECTIVE access; only `shared_permission` /
    `group_permission[]` say where it came from. Reading `permission` alone is
    why transfer-sharing could not tell the two apart."""
    respx.post(f"{BASE}/api/rest/2.0/security/principals/fetch-permissions").mock(
        return_value=httpx.Response(
            200,
            json={
                "principal_permission_details": [
                    {
                        "principal_id": "u-1",
                        "metadata_permission_info": [
                            {
                                "metadata_type": "LIVEBOARD",
                                "metadata_permissions": [
                                    {
                                        "metadata_id": "direct",
                                        "metadata_name": "Shared to me",
                                        "permission": "MODIFY",
                                        "shared_permission": "MODIFY",
                                        "group_permission": [],
                                    },
                                    {
                                        "metadata_id": "inherited",
                                        "metadata_name": "Via a group",
                                        "permission": "READ_ONLY",
                                        "shared_permission": "NO_ACCESS",
                                        "group_permission": [{"id": "g-1", "permission": "READ_ONLY"}],
                                    },
                                    {
                                        "metadata_id": "none",
                                        "metadata_name": "No access",
                                        "permission": "NO_ACCESS",
                                    },
                                ],
                            }
                        ],
                    }
                ]
            },
        )
    )

    async with _client() as client:
        rows = await client.principal_permissions(principal_identifier="u-1")

    by_id = {r["metadata_id"]: r for r in rows}
    assert set(by_id) == {"direct", "inherited"}, "NO_ACCESS rows are still dropped"
    assert by_id["direct"]["is_direct_share"] is True
    assert by_id["inherited"]["is_direct_share"] is False
    assert by_id["inherited"]["group_permissions"] == [{"id": "g-1", "permission": "READ_ONLY"}]
    assert by_id["inherited"]["share_mode"] == "READ_ONLY"


# ── delete_metadata / fetch_permissions type enums ────────────────────────────

# `deleteMetadata`'s `type` enum, verbatim from the v2 reference.
DELETE_METADATA_TYPES = {"LIVEBOARD", "ANSWER", "LOGICAL_TABLE", "LOGICAL_COLUMN", "LOGICAL_RELATIONSHIP"}
# `fetchPermissionsOnMetadata`'s `type` enum, verbatim from the v2 reference.
FETCH_PERMISSIONS_TYPES = {"LIVEBOARD", "ANSWER", "LOGICAL_TABLE", "LOGICAL_COLUMN", "CONNECTION", "COLLECTION"}


def test_every_metadata_type_member_is_covered_by_this_test():
    """Guards the two `it.each`-style loops below against a vacuous pass."""
    assert len(list(MetadataType)) == 9


@respx.mock
@pytest.mark.parametrize("member", list(MetadataType), ids=lambda m: m.value)
async def test_delete_metadata_maps_every_metadata_type_into_the_delete_enum(member: MetadataType):
    """`CachedMetadata.object_type` feeds this directly, and it holds values —
    WORKSHEET, ONE_TO_ONE_LOGICAL, AGGR_WORKSHEET, SQL_VIEW, USER_DEFINED, and
    our own derived DATASET — that are not members of the delete enum. A new
    MetadataType member must fail this test, not a live delete."""
    live = respx.post(f"{BASE}/api/rest/2.0/metadata/delete").mock(return_value=httpx.Response(204))

    async with _client() as client:
        await client.delete_metadata(object_ids=["x"], object_type=member)

    sent = _body(live)["metadata"][0]["type"]
    assert sent in DELETE_METADATA_TYPES, f"{member.value} maps to {sent!r}, not a deleteMetadata enum member"


@respx.mock
@pytest.mark.parametrize("member", list(MetadataType), ids=lambda m: m.value)
async def test_fetch_permissions_maps_every_metadata_type_into_its_enum(member: MetadataType):
    live = respx.post(f"{BASE}/api/rest/2.0/security/metadata/fetch-permissions").mock(
        return_value=httpx.Response(200, json={"metadata_permission_details": []})
    )

    async with _client() as client:
        await client.fetch_permissions(ts_guid="x", object_type=member)

    sent = _body(live)["metadata"][0]["type"]
    assert sent in FETCH_PERMISSIONS_TYPES, f"{member.value} maps to {sent!r}, not a fetch-permissions enum member"


@respx.mock
async def test_fetch_permissions_keeps_permission_type_which_is_real_on_this_endpoint():
    """Unlike the principals endpoint, `security/metadata/fetch-permissions`
    genuinely defines `permission_type` (10.3.0.cl+). Dropping it here would be
    a regression, so the two endpoints are asserted separately on purpose."""
    live = respx.post(f"{BASE}/api/rest/2.0/security/metadata/fetch-permissions").mock(
        return_value=httpx.Response(200, json={"metadata_permission_details": []})
    )

    async with _client() as client:
        await client.fetch_permissions(ts_guid="x", object_type=MetadataType.LIVEBOARD)

    assert _body(live)["permission_type"] == "DEFINED"


# ── tml_export ────────────────────────────────────────────────────────────────


@respx.mock
async def test_tml_export_sends_the_real_export_associated_key_as_a_boolean():
    live = respx.post(f"{BASE}/api/rest/2.0/metadata/tml/export").mock(return_value=httpx.Response(200, json=[]))

    async with _client() as client:
        await client.tml_export(object_ids=["lb-1"])

    body = _body(live)
    _assert_no_dead_keys(body)
    assert body["export_associated"] is False, "spec key is `export_associated`, and it is a boolean"
