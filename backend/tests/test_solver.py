from __future__ import annotations

from app.services.solver import solve_problem


def test_solver_assigns_every_session_without_conflicts() -> None:
    payload = {
        "teachers": [
            {"business_id": "T1", "name": "教师甲", "subject": "数学"},
        ],
        "rooms": [
            {"business_id": "R1", "name": "教室1", "is_active": True},
            {"business_id": "R2", "name": "教室2", "is_active": True},
        ],
        "time_slots": [
            {"business_id": "S1", "is_open": True},
            {"business_id": "S2", "is_open": True},
        ],
        "course_sessions": [
            {
                "id": "course-db-1",
                "business_id": "C1",
                "class_business_id": "B1",
                "teacher_business_id": "T1",
                "subject": "数学",
            },
            {
                "id": "course-db-2",
                "business_id": "C2",
                "class_business_id": "B1",
                "teacher_business_id": "T1",
                "subject": "数学",
            },
        ],
        "rules": [],
        "time_limit_seconds": 3,
        "random_seed": 2026,
    }
    result = solve_problem(payload)
    assert result["model_status"] == "OPTIMAL"
    assert len(result["assignments"]) == 2


def test_solver_skips_inactive_rooms() -> None:
    payload = {
        "teachers": [
            {"business_id": "T1", "name": "教师甲", "subject": "数学"},
        ],
        "rooms": [
            {"business_id": "R1", "name": "停用教室", "is_active": False},
        ],
        "time_slots": [
            {"business_id": "S1", "is_open": True},
        ],
        "course_sessions": [
            {
                "id": "course-db-1",
                "business_id": "C1",
                "class_business_id": "B1",
                "teacher_business_id": "T1",
                "subject": "数学",
            },
        ],
        "rules": [],
        "time_limit_seconds": 3,
        "random_seed": 2026,
    }
    result = solve_problem(payload)
    assert result["model_status"] == "INFEASIBLE"


def test_solver_applies_consecutive_soft_rule() -> None:
    payload = {
        "teachers": [
            {"business_id": "T1", "name": "教师甲", "subject": "数学"}
        ],
        "rooms": [
            {"business_id": "R1", "name": "教室1", "is_active": True}
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
                "subject": "数学",
            },
            {
                "id": "C2-db",
                "business_id": "C2",
                "class_business_id": "B1",
                "teacher_business_id": "T1",
                "subject": "数学",
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
