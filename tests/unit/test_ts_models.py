"""
Unit tests for ts_client response models.

Focus: TS REST v2 users/search and groups/search return timestamps as
`creation_time_in_millis` / `modification_time_in_millis` (epoch ms) —
the models must map those onto `created` / `modified`, otherwise every
synced user/group has NULL timestamps forever.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ts_admin.ts_client.models import TSGroup, TSUser

_JAN_1_2025_MS = 1735689600000  # 2025-01-01T00:00:00Z
_FEB_1_2025_MS = 1738368000000  # 2025-02-01T00:00:00Z


class TestUserTimestamps:
    def test_maps_epoch_ms_fields(self):
        user = TSUser.model_validate(
            {
                "id": "guid-1",
                "name": "alice",
                "creation_time_in_millis": _JAN_1_2025_MS,
                "modification_time_in_millis": _FEB_1_2025_MS,
            }
        )
        assert user.created == datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert user.modified == datetime(2025, 2, 1, tzinfo=timezone.utc)

    def test_missing_time_fields_stay_none(self):
        user = TSUser.model_validate({"id": "guid-1", "name": "alice"})
        assert user.created is None
        assert user.modified is None

    def test_explicit_created_wins_over_millis(self):
        explicit = datetime(2024, 6, 15, tzinfo=timezone.utc)
        user = TSUser.model_validate(
            {
                "id": "guid-1",
                "name": "alice",
                "created": explicit,
                "creation_time_in_millis": _JAN_1_2025_MS,
            }
        )
        assert user.created == explicit

    def test_zero_millis_treated_as_absent(self):
        user = TSUser.model_validate({"id": "guid-1", "name": "alice", "creation_time_in_millis": 0})
        assert user.created is None


class TestGroupMemberUsers:
    """The v2 groups/search response nests members under "users" as objects —
    the model must normalize them into member_users GUIDs, or membership sync
    silently writes nothing."""

    def test_users_objects_become_member_guids(self):
        group = TSGroup.model_validate(
            {
                "id": "g1",
                "name": "admins",
                "users": [{"id": "u1", "name": "alice"}, {"id": "u2", "name": "bob"}],
            }
        )
        assert group.member_users == ["u1", "u2"]

    def test_plain_string_entries_pass_through(self):
        group = TSGroup.model_validate({"id": "g1", "name": "admins", "users": ["u1", "u2"]})
        assert group.member_users == ["u1", "u2"]

    def test_explicit_member_users_wins(self):
        group = TSGroup.model_validate({"id": "g1", "name": "admins", "member_users": ["u9"], "users": [{"id": "u1"}]})
        assert group.member_users == ["u9"]

    def test_author_id_parsed(self):
        group = TSGroup.model_validate({"id": "g1", "name": "admins", "author_id": "u-creator"})
        assert group.author_id == "u-creator"

    def test_absent_author_id_gives_empty_string(self):
        group = TSGroup.model_validate({"id": "g1", "name": "admins"})
        assert group.author_id == ""

    def test_null_author_id_gives_empty_string(self):
        group = TSGroup.model_validate({"id": "g1", "name": "admins", "author_id": None})
        assert group.author_id == ""

    def test_absent_users_key_gives_empty_list(self):
        group = TSGroup.model_validate({"id": "g1", "name": "admins"})
        assert group.member_users == []

    def test_falsy_ids_dropped(self):
        group = TSGroup.model_validate(
            {"id": "g1", "name": "admins", "users": [{"id": ""}, {"name": "no-id"}, {"id": "u1"}]}
        )
        assert group.member_users == ["u1"]

    def test_sub_groups_objects_become_guids(self):
        # Live clusters return sub_groups as objects too — untransformed they
        # fail the list[str] field and abort the whole groups sync.
        group = TSGroup.model_validate({"id": "g1", "name": "admins", "sub_groups": [{"id": "sg1", "name": "child"}]})
        assert group.sub_groups == ["sg1"]


class TestExplicitNulls:
    """TS sends `null` (not an omitted key) for unset optional values. The
    primary org's stock groups have no description, which used to abort the
    whole groups sync with TSResponseParseError."""

    def test_null_description_falls_back_to_default(self):
        group = TSGroup.model_validate({"id": "g1", "name": "Administrator", "description": None})
        assert group.description == ""

    def test_null_lists_and_strings_across_fields(self):
        group = TSGroup.model_validate(
            {
                "id": "g1",
                "name": "Administrator",
                "display_name": None,
                "description": None,
                "privileges": None,
                "users": None,
                "sub_groups": None,
            }
        )
        assert (group.display_name, group.description) == ("", "")
        assert (group.privileges, group.member_users, group.sub_groups) == ([], [], [])

    def test_null_user_fields_fall_back_to_defaults(self):
        user = TSUser.model_validate({"id": "u1", "name": "alice", "display_name": None, "email": None})
        assert (user.display_name, user.email) == ("", "")

    def test_null_on_required_field_still_raises(self):
        # A null where we genuinely need a value is a real broken response —
        # don't silently swallow it.
        with pytest.raises(ValidationError):
            TSGroup.model_validate({"id": None, "name": "admins"})

    def test_null_on_nullable_field_is_preserved(self):
        group = TSGroup.model_validate({"id": "g1", "name": "admins", "created": None})
        assert group.created is None


class TestGroupTimestamps:
    def test_maps_epoch_ms_fields(self):
        group = TSGroup.model_validate(
            {
                "id": "guid-2",
                "name": "admins",
                "creation_time_in_millis": _JAN_1_2025_MS,
                "modification_time_in_millis": _FEB_1_2025_MS,
            }
        )
        assert group.created == datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert group.modified == datetime(2025, 2, 1, tzinfo=timezone.utc)

    def test_missing_time_fields_stay_none(self):
        group = TSGroup.model_validate({"id": "guid-2", "name": "admins"})
        assert group.created is None
        assert group.modified is None
