from __future__ import annotations

from app.services.solver import solve_problem


def _date_payload(
    sessions: list[dict],
    *,
    rooms: list[dict] | None = None,
    teachers: list[dict] | None = None,
    date_window_days: int = 0,
    **extra: object,
) -> dict:
    payload = {
        "teachers": teachers or [],
        "rooms": rooms
        or [
            {"business_id": "R1", "name": "教室1", "is_active": True},
            {"business_id": "R2", "name": "教室2", "is_active": True},
        ],
        "time_slots": [
            {
                "business_id": f"S-{weekday}-0830",
                "weekday": weekday,
                "start_time": "08:30",
                "end_time": "11:30",
                "is_open": True,
            }
            for weekday in ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
        ]
        + [
            {
                "business_id": f"S-{weekday}-0900",
                "weekday": weekday,
                "start_time": "09:00",
                "end_time": "12:00",
                "is_open": True,
            }
            for weekday in ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
        ],
        "course_sessions": sessions,
        "rules": [],
        "date_window_days": date_window_days,
        "time_limit_seconds": 3,
        "random_seed": 2026,
    }
    payload.update(extra)
    return payload


def _course(
    business_id: str,
    *,
    class_id: str,
    teacher_id: str = "郑州考研英语教研组",
    lesson_date: str = "2026-09-07",
    start: str = "08:30",
    end: str = "11:30",
    room: str = "R1",
    business_line: str = "考研",
    product_type: str = "考研·公共课标准编排",
    calendar_user_id: str | None = None,
) -> dict:
    return {
        "id": f"db-{business_id}",
        "business_id": business_id,
        "source_row_id": f"SRC-{business_id}",
        "business_line": business_line,
        "product_type": product_type,
        "class_business_id": class_id,
        "teacher_business_id": teacher_id,
        "calendar_user_id": calendar_user_id,
        "lesson_date": lesson_date,
        "fixed_start_time": start,
        "fixed_end_time": end,
        "original_room_business_id": room,
        "suggested_slot_id": "S-周一-0830" if start == "08:30" else "S-周一-0900",
    }


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


def test_date_solver_treats_overlapping_clock_ranges_as_room_conflict() -> None:
    payload = _date_payload(
        [
            _course("C1", class_id="B1", start="08:30", end="11:30"),
            _course("C2", class_id="B2", start="09:00", end="12:00"),
        ],
        rooms=[{"business_id": "R1", "name": "教室1", "is_active": True}],
    )

    result = solve_problem(payload)

    assert result["model_status"] == "INFEASIBLE"


def test_date_solver_prevents_overlapping_sessions_for_same_class() -> None:
    payload = _date_payload(
        [
            _course("C1", class_id="B1", room="R1"),
            _course("C2", class_id="B1", room="R2"),
        ]
    )

    result = solve_problem(payload)

    assert result["model_status"] == "INFEASIBLE"
    assert "SYSTEM-CLASS-NO-OVERLAP" in result["conflict_rule_ids"]
    assert result["priority_explanations"]


def test_date_solver_allows_reused_class_label_across_product_types() -> None:
    payload = _date_payload(
        [
            _course("C1", class_id="走读SMART班", room="R1"),
            _course(
                "C2",
                class_id="走读SMART班",
                room="R2",
                product_type="考研·走读SMART春季（无数学）",
            ),
        ],
        teachers=[
            {
                "business_id": "郑州考研英语教研组",
                "name": "郑州考研英语教研组",
                "is_group": True,
            }
        ],
        date_window_days=0,
    )

    result = solve_problem(payload)

    assert result["model_status"] == "OPTIMAL"
    assert len(result["assignments"]) == 2


def test_teacher_group_text_does_not_create_personal_calendar_conflict() -> None:
    payload = _date_payload(
        [
            _course("C1", class_id="B1", room="R1"),
            _course("C2", class_id="B2", room="R2"),
        ],
        teachers=[
            {
                "business_id": "郑州考研英语教研组",
                "name": "郑州考研英语教研组",
                "is_group": True,
                "calendar_user_id": None,
            }
        ],
    )

    result = solve_problem(payload)

    assert result["model_status"] == "OPTIMAL"
    assert {item["teacher_business_id"] for item in result["assignments"]} == {
        "郑州考研英语教研组"
    }


def test_concrete_calendar_user_cannot_have_overlapping_sessions() -> None:
    payload = _date_payload(
        [
            _course("C1", class_id="B1", room="R1"),
            _course("C2", class_id="B2", room="R2"),
        ],
        teachers=[
            {
                "business_id": "郑州考研英语教研组",
                "name": "郑州考研英语教研组",
                "is_group": True,
                "calendar_user_id": "ou_teacher_1",
            }
        ],
    )

    result = solve_problem(payload)

    assert result["model_status"] == "INFEASIBLE"


def test_solver_rule_contract_is_applied_by_date_model() -> None:
    payload = _date_payload(
        [
            _course("C1", class_id="B1", room="R1"),
            _course("C2", class_id="B2", room="R2"),
        ],
        teachers=[
            {
                "business_id": "郑州考研英语教研组",
                "name": "郑州考研英语教研组",
                "is_group": True,
                "calendar_user_id": "ou_teacher_1",
            }
        ],
        solver_rules=["fixed_time", "room_no_overlap", "minimize_changes"],
    )

    result = solve_problem(payload)

    assert result["model_status"] == "OPTIMAL"


def test_course_calendar_user_takes_priority_over_teacher_mapping() -> None:
    payload = _date_payload(
        [
            _course(
                "C1",
                class_id="B1",
                room="R1",
                calendar_user_id="ou_session_1",
            ),
            _course(
                "C2",
                class_id="B2",
                room="R2",
                calendar_user_id="ou_session_2",
            ),
        ],
        teachers=[
            {
                "business_id": "郑州考研英语教研组",
                "name": "郑州考研英语教研组",
                "is_group": True,
                "calendar_user_id": "ou_teacher_shared",
            }
        ],
    )

    result = solve_problem(payload)

    assert result["model_status"] == "OPTIMAL"


def test_date_solver_moves_only_date_and_room_within_request_boundaries() -> None:
    payload = _date_payload(
        [
            _course("C1", class_id="B1", room="R1"),
            _course("C2", class_id="B2", room="R1"),
        ],
        rooms=[{"business_id": "R1", "name": "教室1", "is_active": True}],
        date_window_days=1,
        date_from="2026-09-07",
        date_to="2026-09-08",
    )

    result = solve_problem(payload)

    assert result["model_status"] == "OPTIMAL"
    assert {item["lesson_date"] for item in result["assignments"]} == {
        "2026-09-07",
        "2026-09-08",
    }
    assert {item["room_business_id"] for item in result["assignments"]} == {"R1"}
    assert {item["slot_business_id"] for item in result["assignments"]} == {
        "S-周一-0830",
        "S-周二-0830",
    }


def test_course_level_business_filters_do_not_use_class_majority_fields() -> None:
    payload = _date_payload(
        [
            _course(
                "C1",
                class_id="SHARED",
                business_line="考研",
                product_type="考研·公共课标准编排",
            ),
            _course(
                "C2",
                class_id="SHARED",
                business_line="公职",
                product_type="公职·结构化面试先导",
            ),
        ],
        business_lines=["公职"],
        product_types=["公职·结构化面试先导"],
    )

    result = solve_problem(payload)

    assert result["model_status"] == "OPTIMAL"
    assert [item["course_business_id"] for item in result["assignments"]] == ["C2"]


def test_date_solver_applies_teacher_leave_and_room_outage_events() -> None:
    teacher_leave = _date_payload(
        [_course("C1", class_id="B1", room="R1")],
        date_window_days=1,
        event={
            "event_type": "teacher_leave",
            "teacher_business_id": "郑州考研英语教研组",
            "slot_business_ids": ["S-周一-0830"],
        },
    )

    leave_result = solve_problem(teacher_leave)

    assert leave_result["model_status"] == "OPTIMAL"
    assert leave_result["assignments"][0]["lesson_date"] != "2026-09-07"

    room_outage = _date_payload(
        [_course("C1", class_id="B1", room="R1")],
        rooms=[
            {"business_id": "R1", "name": "教室1", "is_active": True},
            {"business_id": "R2", "name": "教室2", "is_active": True},
        ],
        event={
            "event_type": "room_outage",
            "room_business_id": "R1",
            "slot_business_ids": ["S-周一-0830"],
        },
    )

    outage_result = solve_problem(room_outage)

    assert outage_result["model_status"] == "OPTIMAL"
    assert outage_result["assignments"][0]["room_business_id"] == "R2"


def test_locked_course_is_infeasible_when_teacher_leave_blocks_original_date() -> None:
    payload = _date_payload(
        [_course("C1", class_id="B1", room="R1") | {"is_locked": True}],
        date_window_days=1,
        event={
            "event_type": "teacher_leave",
            "teacher_business_id": "郑州考研英语教研组",
            "slot_business_ids": ["S-周一-0830"],
        },
    )

    result = solve_problem(payload)

    assert result["model_status"] == "INFEASIBLE"


def test_locked_course_still_obeys_conflicting_hard_date_rule() -> None:
    payload = _date_payload(
        [_course("C1", class_id="B1", room="R1") | {"is_locked": True}],
        date_window_days=1,
        dynamic_rules={
            "hard_constraints": [
                {
                    "type": "fixed_date",
                    "course_business_id": "C1",
                    "date": "2026-09-08",
                }
            ]
        },
    )

    result = solve_problem(payload)

    assert result["model_status"] == "INFEASIBLE"


def test_soft_constraints_contract_infers_soft_hardness() -> None:
    payload = _date_payload(
        [_course("C1", class_id="B1", room="R1")],
        rooms=[
            {"business_id": "R1", "name": "教室1", "is_active": True},
            {"business_id": "R2", "name": "教室2", "is_active": True},
        ],
        soft_constraints=[
            {
                "type": "preferred_room",
                "course_id": "C1",
                "room_business_id": "R2",
                "weight": 10,
            }
        ],
    )

    result = solve_problem(payload)

    assert result["model_status"] == "OPTIMAL"
    assert result["assignments"][0]["room_business_id"] == "R2"


def test_dynamic_rule_contract_applies_date_window_and_fixed_room() -> None:
    payload = _date_payload(
        [_course("C1", class_id="B1", room="R1")],
        rooms=[
            {"business_id": "R1", "name": "教室1", "is_active": True},
            {"business_id": "R2", "name": "教室2", "is_active": True},
        ],
        date_window_days=7,
        dynamic_rules={
            "rules": [
                {
                    "type": "date_window",
                    "course_business_id": "C1",
                    "date_from": "2026-09-08",
                    "date_to": "2026-09-08",
                    "hardness": "hard",
                }
            ]
        },
        recognized_rule_contract={
            "rules": [
                {
                    "type": "fixed_room",
                    "course_business_id": "C1",
                    "room_id": "R2",
                    "hardness": "hard",
                },
            ]
        },
    )

    result = solve_problem(payload)

    assert result["model_status"] == "OPTIMAL"
    assert result["assignments"][0]["lesson_date"] != "2026-09-07"
    assert result["assignments"][0]["room_business_id"] == "R2"


def test_room_actor_rule_and_multiple_fixed_rooms_do_not_override_each_other() -> None:
    room_actor_payload = _date_payload(
        [_course("C1", class_id="B1", room="R1")],
        rooms=[
            {"business_id": "R1", "name": "教室1", "is_active": True},
            {"business_id": "R2", "name": "教室2", "is_active": True},
        ],
        rules=[
            {
                "business_id": "ROOM-R2-OUTAGE",
                "actor_type": "room",
                "actor_ids": ["R2"],
                "constraint_type": "unavailable_room",
                "scope": {},
                "hardness": "hard",
            }
        ],
    )

    room_actor_result = solve_problem(room_actor_payload)

    assert room_actor_result["model_status"] == "OPTIMAL"
    assert room_actor_result["assignments"][0]["room_business_id"] == "R1"

    conflicting_fixed_rooms = _date_payload(
        [_course("C1", class_id="B1", room="R1")],
        rules=[
            {
                "business_id": "FIX-R1",
                "actor_ids": ["C1"],
                "constraint_type": "fixed_room",
                "scope": {"room_id": "R1"},
                "hardness": "hard",
            },
            {
                "business_id": "FIX-R2",
                "actor_ids": ["C1"],
                "constraint_type": "fixed_room",
                "scope": {"room_id": "R2"},
                "hardness": "hard",
            },
        ],
    )

    conflict_result = solve_problem(conflicting_fixed_rooms)

    assert conflict_result["model_status"] == "INFEASIBLE"


def test_date_solver_applies_soft_preferred_room() -> None:
    payload = _date_payload(
        [_course("C1", class_id="B1", room="R1")],
        rooms=[
            {"business_id": "R1", "name": "教室1", "is_active": True},
            {"business_id": "R2", "name": "教室2", "is_active": True},
        ],
        rules=[
            {
                "business_id": "PREFER-R2",
                "actor_ids": ["C1"],
                "constraint_type": "preferred_room",
                "scope": {"room_id": "R2"},
                "hardness": "soft",
                "weight": 10,
            }
        ],
    )

    result = solve_problem(payload)

    assert result["model_status"] == "OPTIMAL"
    assert result["assignments"][0]["room_business_id"] == "R2"


def test_filtered_solver_respects_unselected_parent_assignment_as_fixed_occupancy() -> None:
    selected = _course("C1", class_id="B1", room="R1", business_line="考研")
    unselected = _course("C2", class_id="B2", room="R1", business_line="公职")
    payload = _date_payload(
        [selected, unselected],
        rooms=[{"business_id": "R1", "name": "教室1", "is_active": True}],
        business_lines=["考研"],
        date_window_days=1,
        previous_assignments=[
            {
                "course_session_id": unselected["id"],
                "course_business_id": "C2",
                "lesson_date": "2026-09-07",
                "room_business_id": "R1",
                "slot_business_id": "S-周一-0830",
            }
        ],
    )

    result = solve_problem(payload)

    assert result["model_status"] == "OPTIMAL"
    assert result["assignments"][0]["lesson_date"] != "2026-09-07"


def test_plain_teacher_cannot_be_double_booked_without_calendar_mapping() -> None:
    """没有飞书日历映射的普通教师，也必须拿到「同一时刻至多一节课」这条硬约束。

    这是「换一所学校也能用」的底线：以前教师互斥只在 calendar_user_id 非空时才建，
    而导入器从不写这个字段，等于任何新导入的数据都没有教师约束。
    """
    payload = _date_payload(
        [
            _course("C1", class_id="B1", teacher_id="T-1001", room="R1"),
            _course("C2", class_id="B2", teacher_id="T-1001", room="R2"),
        ],
        teachers=[{"business_id": "T-1001", "name": "王老师", "calendar_user_id": None}],
    )

    result = solve_problem(payload)

    assert result["model_status"] == "INFEASIBLE"
    assert "SYSTEM-TEACHER-NO-OVERLAP" in result["conflict_rule_ids"]


def test_unknown_teacher_defaults_to_person_not_group() -> None:
    """主数据里查不到的教师按自然人处理——宁可多约束，也不要静默排出双排课表。"""
    payload = _date_payload(
        [
            _course("C1", class_id="B1", teacher_id="T-未登记", room="R1"),
            _course("C2", class_id="B2", teacher_id="T-未登记", room="R2"),
        ],
        teachers=[],
    )

    assert solve_problem(payload)["model_status"] == "INFEASIBLE"


def test_teaching_group_may_run_parallel_sessions_when_declared() -> None:
    """教研组代表多名自然人，显式声明后允许并行开课。"""
    payload = _date_payload(
        [
            _course("C1", class_id="B1", teacher_id="英语教研组", room="R1"),
            _course("C2", class_id="B2", teacher_id="英语教研组", room="R2"),
        ],
        teachers=[{"business_id": "英语教研组", "name": "英语教研组", "is_group": True}],
    )

    result = solve_problem(payload)

    assert result["model_status"] == "OPTIMAL"
    assert len(result["assignments"]) == 2


def test_slot_matrix_path_also_skips_teacher_conflict_for_groups() -> None:
    """两条求解路径的教师语义必须一致，否则同一份数据会得到互相矛盾的可行性判断。"""
    slots = [
        {
            "business_id": "S1",
            "weekday": "周一",
            "start_time": "08:30",
            "end_time": "11:30",
            "kind": "上午",
            "sequence": 1,
            "is_open": True,
        }
    ]
    rooms = [
        {"business_id": "R1", "name": "教室1", "is_active": True},
        {"business_id": "R2", "name": "教室2", "is_active": True},
    ]

    def payload(is_group: bool) -> dict:
        return {
            "teachers": [
                {"business_id": "T1", "name": "教师一", "is_group": is_group},
            ],
            "rooms": rooms,
            "time_slots": slots,
            "course_sessions": [
                {
                    "id": "db-C1",
                    "business_id": "C1",
                    "class_business_id": "B1",
                    "teacher_business_id": "T1",
                },
                {
                    "id": "db-C2",
                    "business_id": "C2",
                    "class_business_id": "B2",
                    "teacher_business_id": "T1",
                },
            ],
            "rules": [],
            "time_limit_seconds": 5,
        }

    assert solve_problem(payload(is_group=False))["model_status"] == "INFEASIBLE"
    assert solve_problem(payload(is_group=True))["model_status"] == "OPTIMAL"


def test_presolve_infeasible_is_distinguishable_from_a_proved_infeasibility() -> None:
    """短路返回的无解必须打标记：CP-SAT 没跑过，不能说成「已证明无解」。"""
    presolved = solve_problem(
        _date_payload(
            [_course("C1", class_id="B1", room="R1")],
            rooms=[{"business_id": "R1", "name": "教室1", "is_active": False}],
        )
    )
    assert presolved["model_status"] == "INFEASIBLE"
    assert presolved["presolve_infeasible"] is True
    assert presolved["wall_time_seconds"] == 0.0

    solved = solve_problem(
        _date_payload(
            [_course("C1", class_id="B1", room="R1")],
            teachers=[{"business_id": "郑州考研英语教研组", "is_group": True}],
        )
    )
    assert solved["model_status"] == "OPTIMAL"
    assert solved["presolve_infeasible"] is False
    assert solved["wall_time_seconds"] > 0


def test_empty_scope_is_not_reported_as_a_proved_optimum() -> None:
    """选不到课次时返回 OPTIMAL，但必须标明这不是求解器的结论。"""
    result = solve_problem(
        _date_payload(
            [_course("C1", class_id="B1", room="R1")],
            class_business_ids=["不存在的班级"],
        )
    )
    assert result["model_status"] == "OPTIMAL"
    assert result["presolve_infeasible"] is True


def test_slot_matrix_path_only_reschedules_the_requested_scope() -> None:
    """时段矩阵路径此前直接读全库课次，「只调一个班」会把全库重排。"""
    payload = {
        "teachers": [{"business_id": "T1", "name": "教师一", "is_group": True}],
        "rooms": [{"business_id": "R1", "name": "教室1", "is_active": True}],
        "time_slots": [
            {"business_id": "S1", "weekday": "周一", "sequence": 1, "is_open": True},
            {"business_id": "S2", "weekday": "周一", "sequence": 2, "is_open": True},
        ],
        "course_sessions": [
            {
                "id": f"db-C{index}",
                "business_id": f"C{index}",
                "class_business_id": f"B{index}",
                "teacher_business_id": "T1",
            }
            for index in range(3)
        ],
        "class_business_ids": ["B0"],
        "rules": [],
        "time_limit_seconds": 5,
    }

    result = solve_problem(payload)

    assert result["model_status"] == "OPTIMAL"
    assert [item["course_business_id"] for item in result["assignments"]] == ["C0"]


def test_slot_matrix_path_treats_out_of_scope_assignments_as_occupied() -> None:
    """范围外的已发布课次必须让出它占用的教室时段，否则会被排进同一格。"""
    payload = {
        "teachers": [{"business_id": "T1", "name": "教师一", "is_group": True}],
        "rooms": [{"business_id": "R1", "name": "教室1", "is_active": True}],
        "time_slots": [{"business_id": "S1", "weekday": "周一", "sequence": 1, "is_open": True}],
        "course_sessions": [
            {
                "id": "db-C0",
                "business_id": "C0",
                "class_business_id": "B0",
                "teacher_business_id": "T1",
            },
            {
                "id": "db-C1",
                "business_id": "C1",
                "class_business_id": "B1",
                "teacher_business_id": "T1",
            },
        ],
        "class_business_ids": ["B0"],
        "previous_assignments": [
            {"course_business_id": "C1", "room_business_id": "R1", "slot_business_id": "S1"}
        ],
        "rules": [],
        "time_limit_seconds": 5,
    }

    assert solve_problem(payload)["model_status"] == "INFEASIBLE"


def test_mixing_dated_and_undated_sessions_fails_loudly() -> None:
    """混排会让整批退化成无日期模型并清空已有日期，必须报错而不是静默降级。"""
    payload = _date_payload(
        [
            _course("C1", class_id="B1", room="R1"),
            {
                "id": "db-LEGACY",
                "business_id": "LEGACY",
                "class_business_id": "B2",
                "teacher_business_id": "郑州考研英语教研组",
            },
        ],
        teachers=[{"business_id": "郑州考研英语教研组", "is_group": True}],
    )

    try:
        solve_problem(payload)
    except ValueError as exc:
        assert "LEGACY" in str(exc)
    else:
        raise AssertionError("混合数据集必须抛错")


def test_date_model_respects_closed_time_slots() -> None:
    """时段全部关闭时不能照排，也不能回填一个星期对不上的时段标识。"""
    payload = _date_payload(
        [_course("C1", class_id="B1", room="R1")],
        teachers=[{"business_id": "郑州考研英语教研组", "is_group": True}],
        date_window_days=3,
    )
    for slot in payload["time_slots"]:
        slot["is_open"] = False

    assert solve_problem(payload)["model_status"] == "INFEASIBLE"


def test_teacher_leave_without_any_window_no_longer_blocks_every_date() -> None:
    """按日期限定的请假只挡住那几天，其余候选日期照常可用。"""
    payload = _date_payload(
        [_course("C1", class_id="B1", room="R1")],
        teachers=[{"business_id": "郑州考研英语教研组", "is_group": True}],
        date_window_days=5,
        event={
            "event_type": "teacher_leave",
            "teacher_business_id": "郑州考研英语教研组",
            "date_from": "2026-09-07",
            "date_to": "2026-09-07",
        },
    )

    result = solve_problem(payload)

    assert result["model_status"] == "OPTIMAL"
    assert result["assignments"][0]["lesson_date"] != "2026-09-07"
