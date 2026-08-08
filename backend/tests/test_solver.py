from __future__ import annotations

from app.services.solver import solve_problem


def test_solver_respects_capacity_equipment_and_unavailability() -> None:
    payload = {
        "teachers": [
            {
                "business_id": "T1",
                "unavailable_slot_ids": ["S2"],
                "preferred_slot_ids": ["S1"],
            }
        ],
        "rooms": [
            {
                "business_id": "R1",
                "capacity": 20,
                "devices": ["projector"],
                "available_slot_ids": ["S1", "S2"],
                "is_active": True,
            },
            {
                "business_id": "R2",
                "capacity": 50,
                "devices": [],
                "available_slot_ids": ["S1", "S2"],
                "is_active": True,
            },
        ],
        "time_slots": [
            {"business_id": "S1", "is_open": True},
            {"business_id": "S2", "is_open": True},
        ],
        "course_sessions": [
            {
                "id": "course-db-id",
                "business_id": "C1",
                "class_business_id": "B1",
                "teacher_business_id": "T1",
                "student_count": 18,
                "required_devices": ["projector"],
            }
        ],
        "rules": [],
        "time_limit_seconds": 3,
        "random_seed": 2026,
    }
    result = solve_problem(payload)
    assert result["model_status"] == "OPTIMAL"
    assert result["assignments"][0]["room_business_id"] == "R1"
    assert result["assignments"][0]["slot_business_id"] == "S1"


def test_solver_applies_consecutive_soft_rule() -> None:
    payload = {
        "teachers": [
            {"business_id": "T1", "unavailable_slot_ids": [], "preferred_slot_ids": []}
        ],
        "rooms": [
            {
                "business_id": "R1",
                "capacity": 30,
                "devices": [],
                "available_slot_ids": ["S1", "S2", "S3"],
                "is_active": True,
            }
        ],
        "time_slots": [
            {"business_id": "S1", "weekday": "周一", "sequence": 1, "is_open": True},
            {"business_id": "S2", "weekday": "周一", "sequence": 2, "is_open": True},
            {"business_id": "S3", "weekday": "周一", "sequence": 3, "is_open": True},
        ],
        "course_sessions": [
            {
                "id": "C1-db",
                "business_id": "C1",
                "class_business_id": "B1",
                "teacher_business_id": "T1",
                "student_count": 20,
                "required_devices": [],
            },
            {
                "id": "C2-db",
                "business_id": "C2",
                "class_business_id": "B1",
                "teacher_business_id": "T1",
                "student_count": 20,
                "required_devices": [],
            },
        ],
        "rules": [
            {
                "business_id": "SOFT-CONSECUTIVE",
                "actor_ids": ["B1"],
                "constraint_type": "consecutive_sessions",
                "scope": {"minimum_consecutive": 2},
                "hardness": "soft",
                "weight": 100,
            }
        ],
        "time_limit_seconds": 3,
        "random_seed": 2026,
    }
    result = solve_problem(payload)
    assert result["model_status"] == "OPTIMAL"
    assigned = sorted(int(item["slot_business_id"][1:]) for item in result["assignments"])
    assert assigned[1] - assigned[0] == 1
