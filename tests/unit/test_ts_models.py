"""
Unit tests for ts_client response models.

Focus: TS REST v2 users/search and groups/search return timestamps as
`creation_time_in_millis` / `modification_time_in_millis` (epoch ms) —
the models must map those onto `created` / `modified`, otherwise every
synced user/group has NULL timestamps forever.
"""

from datetime import datetime, timezone

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
