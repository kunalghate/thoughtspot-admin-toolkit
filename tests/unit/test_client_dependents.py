"""
Regression tests for `_flatten_dependent_objects`.

Against a live cluster, `metadata/search`'s `dependent_objects` groups dependents
by an outer TYPE key (`PINBOARD_ANSWER_BOOK`, `QUESTION_ANSWER_BOOK`, `FEEDBACK`,
…) while each item carries `type: null`. The flattener must recover the type from
the group key, otherwise every dependent falls back to LOGICAL_TABLE — which is
exactly the bug that made Liveboards/Answers show up as "Logical Tables" in the
Relationship Visualizer's consumer drawer.
"""

from __future__ import annotations

from ts_admin.ts_client.client import _flatten_dependent_objects

ROOT = "model-1"


def test_group_key_stamped_onto_items_with_null_type():
    items = [
        {
            "metadata_id": ROOT,
            "dependent_objects": {
                ROOT: {
                    "QUESTION_ANSWER_BOOK": [{"id": "a1", "name": "Forecast", "type": None}],
                    "PINBOARD_ANSWER_BOOK": [{"id": "lb1", "name": "Sales Liveboard", "type": None}],
                    "FEEDBACK": [{"id": "fb1", "name": "quota", "type": None}],
                }
            },
        }
    ]
    by_id = {d["id"]: d["type"] for d in _flatten_dependent_objects(items, root_guid=ROOT)}
    assert by_id == {
        "a1": "QUESTION_ANSWER_BOOK",
        "lb1": "PINBOARD_ANSWER_BOOK",
        "fb1": "FEEDBACK",
    }


def test_existing_item_type_is_not_overwritten():
    # Un-keyed `{<TYPE>: [...]}` variant; a real per-item type must win over the key.
    items = [{"dependent_objects": {"LOGICAL_TABLE": [{"id": "m2", "name": "M", "type": "WORKSHEET"}]}}]
    out = _flatten_dependent_objects(items, root_guid="unused")
    assert out[0]["type"] == "WORKSHEET"


def test_items_without_ids_are_ignored():
    items = [{"dependent_objects": {ROOT: {"QUESTION_ANSWER_BOOK": [{"name": "no-id"}]}}}]
    assert _flatten_dependent_objects(items, root_guid=ROOT) == []
