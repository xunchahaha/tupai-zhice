"""Copy to backend/tests/ in the actual repository and run with its existing uv environment.

These tests call repository functions directly. They do not start CP-SAT or an LLM.
The review environment ran the accompanying excerpt-based probes, NOT this repository test.
"""
from itertools import permutations

import pytest

from app.api import _settle_task_constraint_operations
from app.schemas import AssistantTaskConstraint, contradictory_targets
from app.services.goal import plan_task_constraint_revision


def make_batch():
    old = [
        {"id": key, "subject_type": "teacher", "subject_ids": ["T01"],
         "slot_business_ids": [slot], "source_text": f"尽量避开{slot}"}
        for key, slot in [("sc-old-a", "S1"), ("sc-old-b", "S2"), ("sc-old-c", "S3")]
    ]
    actions = [
        AssistantTaskConstraint(
            id=f"tc-{i}", source_text=f"把{key}改成尽量避开{slot}",
            subject_type="teacher", subject_ids=["T01"], slot_business_ids=[slot],
            hardness="soft", op="replace", target_id=key,
        )
        for i, (key, slot) in enumerate(
            [("sc-old-a", "S2"), ("sc-old-b", "S3"), ("sc-old-c", "S4")], 1
        )
    ]
    return old, actions


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_distinct_replacements_keep_all_confirmed_requirements(order):
    old, actions = make_batch()
    actions = [actions[i] for i in order]
    assert contradictory_targets(actions) == set()
    conflicts, warnings = [], []
    normalized = _settle_task_constraint_operations(actions, conflicts=conflicts, warnings=warnings)
    assert len(normalized) == 3
    assert conflicts == warnings == []
    revision = plan_task_constraint_revision([], old, normalized)
    actual = sorted(
        slot for entry in revision.soft_constraints for slot in entry["slot_business_ids"]
    )
    assert actual == ["S2", "S3", "S4"], (order, actual, revision.summary())
    # The planner must not mutate the caller's original version.
    assert [entry["slot_business_ids"] for entry in old] == [["S1"], ["S2"], ["S3"]]


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_valid_original_targets_never_become_unresolved_inside_one_batch(order):
    old, actions = make_batch()
    actions = [actions[i] for i in order]
    assert all(item.target_id in {entry["id"] for entry in old} for item in actions)
    revision = plan_task_constraint_revision([], old, actions)
    assert revision.unresolved == [], (order, revision.summary())
