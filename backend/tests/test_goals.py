"""持久目标验收闭环（MEM-C3）验收测试。

覆盖 docs/roadmap/02-agent-memory.md §6「目标闭环要点」：

1. 清单生成器：各 kind 的 checklist 形态与顺序；禁排占位项必须带 needs_params。
2. 验收器逐 kind：
   - coverage 缺一个课次（C24 式缺口）必须 failed，且区分「没进求解范围」与
     「进了范围但没安置」两类缺口；
   - forbidden_slot_free 独立复核：结果里仍有禁排时段课程必须 failed，
     参数未量化永远不通过（宁可卡住也不虚报完成）；
   - no_hard_conflicts 独立重算；
   - max_changes 只设上限（优化类目标，「尽量」不升级为「绝不」）；
   - draft_only 查审计日志：无发布→passed，run 之后发布→failed；
   - date_range_match 范围端点核对。
3. 状态机：全过→achieved；随后一次失败→状态随最近一次验收走；abandoned
   是人工终态，验收器不越权改动。
4. API：创建目标→关联求解→自动验收→放弃；已放弃目标拒绝再关联新任务。
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    AuditLog,
    Campus,
    CourseSession,
    DataSnapshot,
    Room,
    ScheduleAssignment,
    ScheduleVersion,
    SolveGoal,
    SolverRun,
    Teacher,
    TimeSlot,
)
from app.services.explain import build_explanation_facts
from app.services.goal import (
    apply_goal_evaluation,
    build_checklist,
    draft_checklist_from_interpretation,
    evaluate_goal,
)


def _make_scope(client: TestClient, auth_headers: dict[str, str]) -> dict[str, Any]:
    """独立课表方案：最小主数据（1 教室 / 1 个人教师 / 1 个周一上午时段）。"""
    created = client.post(
        "/api/v1/schedule-sets",
        headers=auth_headers,
        json={"name": f"目标闭环-{uuid4().hex[:8]}"},
    )
    assert created.status_code == 201, created.text
    scope_id = created.json()["id"]
    with SessionLocal() as db:
        campus = Campus(schedule_set_id=scope_id, business_id="C", name="目标校区")
        db.add(campus)
        db.flush()
        db.add_all(
            [
                Room(schedule_set_id=scope_id, campus_id=campus.id, business_id="R1", name="教室1"),
                Teacher(
                    schedule_set_id=scope_id, campus_id=campus.id, business_id="T9", name="教师九"
                ),
                TimeSlot(
                    schedule_set_id=scope_id,
                    campus_id=campus.id,
                    business_id="S1",
                    weekday="周一",
                    start_time="08:30",
                    end_time="11:30",
                ),
                TimeSlot(
                    schedule_set_id=scope_id,
                    campus_id=campus.id,
                    business_id="S2",
                    weekday="周二",
                    start_time="08:30",
                    end_time="11:30",
                ),
            ]
        )
        db.commit()
    return {"headers": {**auth_headers, "X-Schedule-Set-Id": scope_id}, "scope_id": scope_id}


def _add_course(
    scope_id: str,
    business_id: str,
    *,
    class_business_id: str = "B1",
    teacher_business_id: str = "T9",
    lesson_date: date | None = None,
) -> CourseSession:
    with SessionLocal() as db:
        campus_id = db.scalar(select(Campus.id).where(Campus.schedule_set_id == scope_id))
        assert campus_id is not None
        course = CourseSession(
            schedule_set_id=scope_id,
            campus_id=campus_id,
            business_id=business_id,
            class_business_id=class_business_id,
            teacher_business_id=teacher_business_id,
            lesson_date=lesson_date,
            fixed_start_time="08:30",
            fixed_end_time="11:30",
        )
        db.add(course)
        db.commit()
        db.refresh(course)
        return course


def _make_goal(
    scope_id: str,
    checklist: list[dict[str, Any]],
    instruction: str = "重排 B1 班课表",
) -> SolveGoal:
    with SessionLocal() as db:
        goal = SolveGoal(schedule_set_id=scope_id, instruction=instruction, checklist=checklist)
        db.add(goal)
        db.commit()
        db.refresh(goal)
        return goal


def _make_run(
    scope_id: str,
    goal_id: str | None,
    assignments: list[dict[str, Any]],
    *,
    model_status: str = "OPTIMAL",
    snapshot_sessions: list[dict[str, Any]] | None = None,
) -> SolverRun:
    """直接落一条 completed 的 SolverRun（不跑 CP-SAT，验收器只看结果载荷）。"""
    with SessionLocal() as db:
        snapshot = DataSnapshot(
            schedule_set_id=scope_id,
            revision=1,
            checksum=f"goal-test-{uuid4().hex[:8]}",
            payload={"course_sessions": snapshot_sessions or []},
        )
        db.add(snapshot)
        db.flush()
        solved = sorted(
            {str(a.get("course_business_id")) for a in assignments if a.get("course_business_id")}
        )
        run = SolverRun(
            schedule_set_id=scope_id,
            snapshot_id=snapshot.id,
            run_type="initial",
            status="completed",
            model_status=model_status,
            request_payload={},
            result_payload={"assignments": assignments, "solved_course_business_ids": solved},
            goal_id=goal_id,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run


def _assignment(course: CourseSession, *, slot: str = "S1", room: str = "R1") -> dict[str, Any]:
    return {
        "course_session_id": course.id,
        "course_business_id": course.business_id,
        "class_business_id": course.class_business_id,
        "teacher_business_id": course.teacher_business_id,
        "lesson_date": course.lesson_date.isoformat() if course.lesson_date else None,
        "slot_business_id": slot,
        "room_business_id": room,
        "change_kind": "assigned",
    }


# ------------------------------------------------- 清单生成器


def test_build_checklist_kinds_and_order() -> None:
    checklist = build_checklist(
        "重排 B1 班 8 月的课，张老师周一别排，尽量少改，别发布",
        business_lines=["考研"],
        class_business_ids=["B1"],
        date_from="2026-08-17",
        date_to="2026-08-19",
        forbidden_slots=[
            {"subject_type": "teacher", "subject_ids": ["T9"], "slot_business_ids": ["S1"]}
        ],
        max_changes=5,
        baseline_schedule_version_id="ver-1",
        forbid_publish=True,
    )
    kinds = [item["kind"] for item in checklist]
    # 底线项（MEM-D2/D4c，MEM-E2/E2b 增补 no_duplicate_lessons）在最前：
    # deliverable_exists、coverage、no_hard_conflicts、no_duplicate_lessons。
    assert kinds == [
        "deliverable_exists",
        "coverage",
        "date_range_match",
        "forbidden_slot_free",
        "no_hard_conflicts",
        "no_duplicate_lessons",
        "max_changes",
        "draft_only",
    ]
    coverage = checklist[1]
    assert coverage["params"]["class_business_ids"] == ["B1"]
    assert coverage["params"]["bottom_line"] is True
    assert checklist[0]["params"]["bottom_line"] is True
    forbidden = checklist[3]
    assert forbidden["params"] == {
        "subject_type": "teacher",
        "subject_ids": ["T9"],
        "slot_business_ids": ["S1"],
    }
    # 「尽量少改」只生成上限验收，绝不升级为「绝不改」。
    assert checklist[6]["params"] == {"max_changes": 5, "baseline_schedule_version_id": "ver-1"}
    assert "上限" in checklist[6]["requirement"]

    light = build_checklist("随便排一下", forbid_publish=False)
    assert [item["kind"] for item in light] == [
        "deliverable_exists",
        "coverage",
        "no_hard_conflicts",
        "no_duplicate_lessons",
    ]


def test_draft_checklist_flags_unquantified_forbidden() -> None:
    parsed = {
        "business_lines": ["考研"],
        "product_types": [],
        "class_business_ids": ["B1"],
        "date_from": "2026-08-17",
        "date_to": "2026-08-19",
    }
    draft, warnings = draft_checklist_from_interpretation("周三晚上不要安排考研课", parsed)
    kinds = [item["kind"] for item in draft]
    # 占位项追加在生成器产物末尾（deliverable_exists/coverage/date/no_hard/
    # no_duplicate/draft_only 之后）。
    assert kinds == [
        "deliverable_exists",
        "coverage",
        "date_range_match",
        "no_hard_conflicts",
        "no_duplicate_lessons",
        "draft_only",
        "forbidden_slot_free",
    ]
    placeholder = draft[-1]
    assert placeholder["params"]["needs_params"] is True
    assert placeholder["params"]["slot_business_ids"] == []
    assert warnings and "待量化" in warnings[0]

    clean, warnings = draft_checklist_from_interpretation("重排 B1 班三天课", parsed)
    assert [item["kind"] for item in clean] == [
        "deliverable_exists",
        "coverage",
        "date_range_match",
        "no_hard_conflicts",
        "no_duplicate_lessons",
        "draft_only",
    ]
    assert warnings == []


# ------------------------------------------------- 验收器逐 kind


def test_coverage_missing_course_fails_and_names_the_gap(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    c23 = _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))
    c24 = _add_course(scope["scope_id"], "C24", lesson_date=date(2026, 10, 6))
    c25 = _add_course(scope["scope_id"], "C25", lesson_date=date(2026, 10, 7))
    goal = _make_goal(
        scope["scope_id"],
        [
        {
            "key": "coverage",
            "requirement": "全覆盖",
            "kind": "coverage",
            "params": {"class_business_ids": ["B1"]},
        }
    ],
    )
    # C24 进了求解范围但没出现在结果里：验收必须点名列出缺口。
    run = _make_run(
        scope["scope_id"],
        goal.id,
        [_assignment(c23), _assignment(c25)],
        snapshot_sessions=[
            {"business_id": "C23", "is_active": True, "class_business_id": "B1"},
            {"business_id": "C24", "is_active": True, "class_business_id": "B1"},
            {"business_id": "C25", "is_active": True, "class_business_id": "B1"},
        ],
    )
    with SessionLocal() as db:
        report = evaluate_goal(db, db.get(SolveGoal, goal.id), db.get(SolverRun, run.id))
    coverage = report["items"][0]
    assert coverage["passed"] is False
    assert "C24" in coverage["detail"]
    assert coverage["evidence"]["missing_from_result"] == ["C24"]
    # 求解器放不下：允许的下一步是等待教务放宽，而不是自动重跑。
    assert report["decision"]["status"] == "awaiting_decision"
    assert report["gaps"][0]["remedy"] == "await_admin"
    assert _course(scope["scope_id"], c24.id) is not None  # 缺口定位到真实课次


def _course(scope_id: str, course_id: str) -> CourseSession | None:
    with SessionLocal() as db:
        return db.get(CourseSession, course_id)


def test_coverage_scope_miss_suggests_fixing_scope(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    c23 = _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))
    _add_course(scope["scope_id"], "C24", lesson_date=date(2026, 10, 6))
    goal = _make_goal(
        scope["scope_id"],
        [
        {
            "key": "coverage",
            "requirement": "全覆盖",
            "kind": "coverage",
            "params": {"class_business_ids": ["B1"]},
        }
    ],
    )
    # 快照（求解输入）里只有 C23/C25：C24 根本没进本次求解范围——范围提取漏课次。
    run = _make_run(
        scope["scope_id"],
        goal.id,
        [_assignment(c23)],
        snapshot_sessions=[
            {"business_id": "C23", "is_active": True, "class_business_id": "B1"},
            {"business_id": "C25", "is_active": True, "class_business_id": "B1"},
        ],
    )
    with SessionLocal() as db:
        report = evaluate_goal(db, db.get(SolveGoal, goal.id), db.get(SolverRun, run.id))
    coverage = report["items"][0]
    assert coverage["passed"] is False
    assert coverage["evidence"]["missing_from_scope"] == ["C24"]
    assert report["decision"]["status"] == "open"
    assert report["gaps"][0]["remedy"] == "resolve_scope"
    assert "修正课程范围" in report["gaps"][0]["next_step"]


def test_forbidden_slot_residue_fails_independently(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    course = _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))
    goal = _make_goal(
        scope["scope_id"],
        [
            {
                "key": "forbidden_slot_free-1",
                "requirement": "T9 不占 S1",
                "kind": "forbidden_slot_free",
                "params": {
                    "subject_type": "teacher",
                    "subject_ids": ["T9"],
                    "slot_business_ids": ["S1"],
                },
            }
        ],
    )
    run = _make_run(scope["scope_id"], goal.id, [_assignment(course, slot="S1")])
    with SessionLocal() as db:
        report = evaluate_goal(db, db.get(SolveGoal, goal.id), db.get(SolverRun, run.id))
    item = report["items"][0]
    assert item["passed"] is False
    assert item["evidence"]["violations"]
    assert report["decision"]["status"] == "awaiting_decision"

    # 换到 S2 之后独立复核通过。
    run_ok = _make_run(scope["scope_id"], goal.id, [_assignment(course, slot="S2")])
    with SessionLocal() as db:
        report_ok = evaluate_goal(db, db.get(SolveGoal, goal.id), db.get(SolverRun, run_ok.id))
    assert report_ok["items"][0]["passed"] is True


def test_unquantified_forbidden_slot_never_passes(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    course = _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))
    goal = _make_goal(
        scope["scope_id"],
        [
            {
                "key": "forbidden_slot_free-draft",
                "requirement": "禁排要求待量化",
                "kind": "forbidden_slot_free",
                "params": {
                    "subject_type": "teacher",
                    "subject_ids": [],
                    "slot_business_ids": [],
                    "needs_params": True,
                },
            }
        ],
    )
    run = _make_run(scope["scope_id"], goal.id, [_assignment(course)])
    with SessionLocal() as db:
        report = evaluate_goal(db, db.get(SolveGoal, goal.id), db.get(SolverRun, run.id))
    assert report["items"][0]["passed"] is False
    assert "未量化" in report["items"][0]["detail"]
    assert report["gaps"][0]["remedy"] == "fix_checklist"


def test_no_hard_conflicts_recomputed(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    c23 = _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))
    c24 = _add_course(scope["scope_id"], "C24", lesson_date=date(2026, 10, 5))
    goal = _make_goal(
        scope["scope_id"],
        [
        {
            "key": "no_hard_conflicts",
            "requirement": "无硬冲突",
            "kind": "no_hard_conflicts",
            "params": {},
        }
    ],
    )
    # 同一教师同一日期同一时段两节课：独立重算必须抓到教师维度冲突。
    run = _make_run(scope["scope_id"], goal.id, [_assignment(c23), _assignment(c24)])
    with SessionLocal() as db:
        report = evaluate_goal(db, db.get(SolveGoal, goal.id), db.get(SolverRun, run.id))
    item = report["items"][0]
    assert item["passed"] is False
    assert item["evidence"]["total"] > 0
    assert report["decision"]["status"] == "awaiting_decision"


def test_max_changes_is_an_upper_bound_not_a_ban(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    course = _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))
    goal = _make_goal(
        scope["scope_id"],
        [
            {
                "key": "max_changes",
                "requirement": "变更 ≤ 上限",
                "kind": "max_changes",
                "params": {"max_changes": 1, "baseline_schedule_version_id": None},
            }
        ],
    )
    with SessionLocal() as db:
        snapshot = DataSnapshot(
            schedule_set_id=scope["scope_id"], revision=1, checksum="base", payload={}
        )
        db.add(snapshot)
        db.flush()
        # 基线版本必须挂在真实 run 上（solver_run_id 唯一外键）。
        baseline_run = SolverRun(
            schedule_set_id=scope["scope_id"], snapshot_id=snapshot.id, status="completed"
        )
        db.add(baseline_run)
        db.flush()
        baseline = ScheduleVersion(
            schedule_set_id=scope["scope_id"],
            version_no=1,
            name="基准",
            status="published",
            solver_run_id=baseline_run.id,
        )
        db.add(baseline)
        db.flush()
        db.add(
            ScheduleAssignment(
                schedule_version_id=baseline.id,
                course_session_id=course.id,
                lesson_date=date(2026, 10, 5),
                slot_business_id="S1",
                room_business_id="R1",
            )
        )
        db.commit()
        goal_row = db.get(SolveGoal, goal.id)
        assert goal_row is not None
        # JSON 列的就地修改不会被 SQLAlchemy 追踪，必须整体重赋值。
        goal_row.checklist = [
            {
                **goal_row.checklist[0],
                "params": {
                    **goal_row.checklist[0]["params"],
                    "baseline_schedule_version_id": baseline.id,
                },
            }
        ]
        db.commit()

        def evaluated(assignments: list[dict[str, Any]]) -> dict[str, Any]:
            run = SolverRun(
                schedule_set_id=scope["scope_id"],
                snapshot_id=snapshot.id,
                status="completed",
                model_status="OPTIMAL",
                request_payload={},
                result_payload={
                    "assignments": assignments,
                    "solved_course_business_ids": [course.business_id],
                },
                goal_id=goal.id,
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            return evaluate_goal(db, goal_row, run)

        # 挪到 S2：变更 1 处，恰好等于上限 1 → 通过（只设上限，不是「绝不能动」）。
        report = evaluated([_assignment(course, slot="S2")])
        assert report["items"][0]["passed"] is True
        assert "上限" in report["items"][0]["detail"]
        # 同日同室同时段回到基准位置：变更 0 处，限内；把上限压到 0 再挪才会失败。
        report_zero = evaluated([_assignment(course, slot="S1", room="R1")])
        assert report_zero["items"][0]["passed"] is True
        goal_row.checklist = [
            {
                **goal_row.checklist[0],
                "params": {**goal_row.checklist[0]["params"], "max_changes": 0},
            }
        ]
        db.commit()
        report_fail = evaluated([_assignment(course, slot="S2")])
        assert report_fail["items"][0]["passed"] is False
        assert report_fail["decision"]["status"] == "open"
        assert report_fail["gaps"][0]["remedy"] == "raise_budget"


def test_draft_only_checks_audit_log(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    course = _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))
    goal = _make_goal(
        scope["scope_id"],
        [{"key": "draft_only", "requirement": "只交付草稿", "kind": "draft_only", "params": {}}],
    )
    run = _make_run(scope["scope_id"], goal.id, [_assignment(course)])
    with SessionLocal() as db:
        report = apply_goal_evaluation(db, db.get(SolverRun, run.id))
        db.commit()
    assert report is not None and report["all_passed"] is True
    with SessionLocal() as db:
        assert db.get(SolveGoal, goal.id).status == "achieved"
        stored = db.get(SolverRun, run.id)
        assert stored is not None and stored.goal_report is not None

    # run 之后发生发布动作：draft_only 立刻失败，状态随最近一次验收回退。
    with SessionLocal() as db:
        version = db.scalar(
            select(ScheduleVersion).where(ScheduleVersion.schedule_set_id == scope["scope_id"])
        )
        if version is None:
            snapshot = db.scalar(
                select(DataSnapshot).where(DataSnapshot.schedule_set_id == scope["scope_id"])
            )
            assert snapshot is not None
            version = ScheduleVersion(
                schedule_set_id=scope["scope_id"],
                version_no=1,
                name="草稿",
                status="draft",
                solver_run_id=run.id,
            )
            db.add(version)
            db.flush()
        db.add(
            AuditLog(
                actor_id=None,
                action="publish",
                resource_type="schedule",
                resource_id=version.id,
                detail={},
            )
        )
        db.commit()
    run2 = _make_run(scope["scope_id"], goal.id, [_assignment(course)])
    with SessionLocal() as db:
        report2 = apply_goal_evaluation(db, db.get(SolverRun, run2.id))
        db.commit()
    assert report2 is not None and report2["all_passed"] is False
    assert report2["items"][0]["passed"] is False
    assert report2["decision"]["status"] == "awaiting_decision"
    with SessionLocal() as db:
        assert db.get(SolveGoal, goal.id).status == "awaiting_decision"


def test_date_range_match_checks_endpoints(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    inside = _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))
    outside = _add_course(scope["scope_id"], "C24", lesson_date=date(2026, 10, 12))
    goal = _make_goal(
        scope["scope_id"],
        [
            {
                "key": "date_range_match",
                "requirement": "日期在范围内",
                "kind": "date_range_match",
                "params": {"date_from": "2026-10-05", "date_to": "2026-10-09"},
            }
        ],
    )
    run = _make_run(scope["scope_id"], goal.id, [_assignment(inside), _assignment(outside)])
    with SessionLocal() as db:
        report = evaluate_goal(db, db.get(SolveGoal, goal.id), db.get(SolverRun, run.id))
    item = report["items"][0]
    assert item["passed"] is False
    assert "C24" in item["detail"]
    assert item["evidence"]["outside"][0]["lesson_date"] == "2026-10-12"

    run_ok = _make_run(scope["scope_id"], goal.id, [_assignment(inside)])
    with SessionLocal() as db:
        report_ok = evaluate_goal(db, db.get(SolveGoal, goal.id), db.get(SolverRun, run_ok.id))
    assert report_ok["items"][0]["passed"] is True


# ------------------------------------------------- 状态机与解释事实


def test_status_follows_latest_evaluation_and_abandon_is_terminal(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    course = _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))
    goal = _make_goal(
        scope["scope_id"],
        [
            {"key": "draft_only", "requirement": "只交付草稿", "kind": "draft_only", "params": {}},
            {
                "key": "forbidden_slot_free-1",
                "requirement": "T9 不占 S1",
                "kind": "forbidden_slot_free",
                "params": {
                    "subject_type": "teacher",
                    "subject_ids": ["T9"],
                    "slot_business_ids": ["S1"],
                },
            },
        ],
    )
    ok_run = _make_run(scope["scope_id"], goal.id, [_assignment(course, slot="S2")])
    with SessionLocal() as db:
        apply_goal_evaluation(db, db.get(SolverRun, ok_run.id))
        db.commit()
        assert db.get(SolveGoal, goal.id).status == "achieved"

    # 后一次跑砸了（禁排时段又被占上）：状态必须随最近一次验收回退，不能停在 achieved。
    bad_run = _make_run(scope["scope_id"], goal.id, [_assignment(course, slot="S1")])
    with SessionLocal() as db:
        apply_goal_evaluation(db, db.get(SolverRun, bad_run.id))
        db.commit()
        assert db.get(SolveGoal, goal.id).status == "awaiting_decision"

    # abandoned 是人工终态：验收器对放弃目标不出报告、不改状态。
    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal.id)
        assert goal_row is not None
        goal_row.status = "abandoned"
        db.commit()
    another = _make_run(scope["scope_id"], goal.id, [_assignment(course, slot="S2")])
    with SessionLocal() as db:
        assert apply_goal_evaluation(db, db.get(SolverRun, another.id)) is None
        assert db.get(SolveGoal, goal.id).status == "abandoned"


def test_explanation_facts_carry_goal_acceptance(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    course = _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))
    goal = _make_goal(
        scope["scope_id"],
        [{"key": "draft_only", "requirement": "只交付草稿", "kind": "draft_only", "params": {}}],
    )
    run = _make_run(scope["scope_id"], goal.id, [_assignment(course)])
    with SessionLocal() as db:
        run_row = db.get(SolverRun, run.id)
        assert run_row is not None
        facts = build_explanation_facts(db, run_row)
        assert facts["goal_acceptance"] is None  # 尚未验收
        apply_goal_evaluation(db, run_row)
        facts = build_explanation_facts(db, run_row)
    acceptance = facts["goal_acceptance"]
    assert acceptance is not None
    assert acceptance["all_passed"] is True
    assert acceptance["instruction"] == "重排 B1 班课表"
    assert acceptance["items"][0]["kind"] == "draft_only"


# ------------------------------------------------- API 全链路


def test_goal_api_create_solve_evaluate_abandon(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))

    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={
            "instruction": "排好 B1 班 10 月第一周的课，只出草稿",
            "class_business_ids": ["B1"],
            "forbid_publish": True,
        },
    )
    assert created.status_code == 201, created.text
    goal = created.json()
    kinds = [item["kind"] for item in goal["checklist"]]
    # 未传日期 → 不生成 date_range_match；forbid_publish → draft_only；
    # 底线（MEM-D2/D4c + MEM-E2/E2b）强制在前：deliverable_exists + coverage +
    # no_hard_conflicts + no_duplicate_lessons。
    assert kinds == [
        "deliverable_exists",
        "coverage",
        "no_hard_conflicts",
        "no_duplicate_lessons",
        "draft_only",
    ]
    assert goal["status"] == "open"

    # 显式传入空清单必须被拒——恒真清单会把缺口洗成达标。
    empty = client.post(
        "/api/v1/goals", headers=headers, json={"instruction": "测试", "checklist": []}
    )
    assert empty.status_code == 422

    # 关联求解（wait=true 内联执行）：完成后自动验收，达标目标转 achieved。
    solved = client.post(
        "/api/v1/solver-runs",
        headers=headers,
        json={
            "class_business_ids": ["B1"],
            "date_from": "2026-10-01",
            "date_to": "2026-10-09",
            "goal_id": goal["id"],
            "wait": True,
        },
    )
    assert solved.status_code == 202, solved.text
    run = solved.json()
    assert run["goal_id"] == goal["id"]
    assert run["goal_report"] is not None
    assert run["goal_report"]["goal_id"] == goal["id"]

    detail = client.get(f"/api/v1/goals/{goal['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["run_count"] == 1
    assert len(body["runs"]) == 1
    assert body["latest_report"] is not None
    # MEM-D2 语义收紧：目标课次（B1 → C23）确已覆盖、课表产物非空且无冲突，
    # 决策必须是确定的 achieved，不再是「任意终态皆可」。
    assert body["latest_report"]["decision"]["status"] == "achieved"
    assert body["status"] == "achieved"
    assert body["acceptance_status"] == "completed"

    # 无效基准版本 / 跨方案 goal_id 防护。
    bad_baseline = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "测试基准", "baseline_schedule_version_id": "nope"},
    )
    assert bad_baseline.status_code == 422

    # 放弃（终态）→ 再次放弃 409 → 已放弃目标拒绝再关联新求解任务。
    abandoned = client.post(f"/api/v1/goals/{goal['id']}/abandon", headers=headers)
    assert abandoned.status_code == 200
    assert abandoned.json()["status"] == "abandoned"
    again = client.post(f"/api/v1/goals/{goal['id']}/abandon", headers=headers)
    assert again.status_code == 409
    blocked = client.post(
        "/api/v1/solver-runs",
        headers=headers,
        json={"goal_id": goal["id"], "wait": True},
    )
    assert blocked.status_code == 409

    listed = client.get("/api/v1/goals", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()
    target = next(row for row in rows if row["id"] == goal["id"])
    assert target["status"] == "abandoned"
    assert target["run_count"] >= 1


# ------------------------------------------------- 清单修订端点（MEM-D3）


def test_goal_checklist_patch_quantifies_forbidden_placeholder(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """禁排占位项补参（MEM-D3）：PATCH 清单量化参数后，该项从「恒不通过」恢复
    参与验收——验收口径永远以当前 checklist 为准，报告随之反映新参数。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    course = _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={
            "instruction": "排 B1 班的课，张老师别排在被点名的时段",
            "checklist": [
                {
                    "key": "forbidden_slot_free-draft",
                    "requirement": "禁排要求待量化：补充主体与具体时段后才能独立复核",
                    "kind": "forbidden_slot_free",
                    "params": {
                        "subject_type": "teacher",
                        "subject_ids": [],
                        "slot_business_ids": [],
                        "needs_params": True,
                    },
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    goal = created.json()
    assert goal["checklist_version"] == 1
    # 未量化前：unverifiable，绝不因「没检测到越界」放行。
    run_unquantified = _make_run(scope["scope_id"], goal["id"], [_assignment(course, slot="S2")])
    with SessionLocal() as db:
        report = apply_goal_evaluation(db, db.get(SolverRun, run_unquantified.id))
    assert report is not None
    forbidden = next(i for i in report["items"] if i["kind"] == "forbidden_slot_free")
    assert forbidden["verdict"] == "unverifiable"

    # PATCH 量化参数（教师 T9 不占 S2）→ 版本 +1，旧清单快照进历史。
    patched = client.patch(
        f"/api/v1/goals/{goal['id']}/checklist",
        headers=headers,
        json={
            "checklist": [
                {
                    "key": "forbidden_slot_free-1",
                    "requirement": "教师 T9 不占用指定时段（S2）——独立复核，不信任求解器自报",
                    "kind": "forbidden_slot_free",
                    "params": {
                        "subject_type": "teacher",
                        "subject_ids": ["T9"],
                        "slot_business_ids": ["S2"],
                    },
                }
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["checklist_version"] == 2
    assert body["checklist"][0]["params"]["slot_business_ids"] == ["S2"]

    # 量化后：占用了禁排时段 → failed（不再是 unverifiable 恒不通过，而是真实复核）。
    run_violating = _make_run(scope["scope_id"], goal["id"], [_assignment(course, slot="S2")])
    with SessionLocal() as db:
        report_bad = apply_goal_evaluation(db, db.get(SolverRun, run_violating.id))
    assert report_bad is not None
    forbidden_bad = next(i for i in report_bad["items"] if i["kind"] == "forbidden_slot_free")
    assert forbidden_bad["passed"] is False
    assert forbidden_bad["verdict"] == "failed"
    assert report_bad["decision"]["status"] == "awaiting_decision"

    # 换到非禁排时段 → 通过。
    run_ok = _make_run(scope["scope_id"], goal["id"], [_assignment(course, slot="S1")])
    with SessionLocal() as db:
        report_ok = apply_goal_evaluation(db, db.get(SolverRun, run_ok.id))
    assert report_ok is not None
    forbidden_ok = next(i for i in report_ok["items"] if i["kind"] == "forbidden_slot_free")
    assert forbidden_ok["passed"] is True

    # 详情带只读历史：v1 快照保存了占位清单原文。
    detail = client.get(f"/api/v1/goals/{goal['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    history = detail.json()["checklist_history"]
    assert [entry["version"] for entry in history] == [1]
    assert history[0]["items"][0]["key"] == "forbidden_slot_free-draft"
    assert history[0]["items"][0]["params"].get("needs_params") is True
    assert detail.json()["checklist_version"] == 2


def test_goal_checklist_patch_validations(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """PATCH 清单校验：复用创建时的校验（空清单/key 唯一/kind 白名单/底线不可删），
    版本号随修订递增，已放弃目标 409，跨方案 404。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "重排 B1 班课表", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]

    def patch(body: dict[str, Any]) -> Any:
        return client.patch(f"/api/v1/goals/{goal_id}/checklist", headers=headers, json=body)

    # 空清单：恒真清单会把缺口洗成达标，必须拒绝（与创建口径一致）。
    assert patch({"checklist": []}).status_code == 422
    # key 重复：验收报告按 key 对齐缺口，重复 key 无法定位。
    dup = patch(
        {
            "checklist": [
                {"key": "a", "requirement": "r", "kind": "draft_only", "params": {}},
                {"key": "a", "requirement": "r2", "kind": "draft_only", "params": {}},
            ]
        }
    )
    assert dup.status_code == 422
    # kind 白名单（schema Literal）：未知验收类型在验收时只能 unverifiable，入口就拒。
    bad_kind = patch(
        {"checklist": [{"key": "a", "requirement": "r", "kind": "no_such_kind", "params": {}}]}
    )
    assert bad_kind.status_code == 422

    # 底线项不可删除：只传 draft_only，响应仍强制并入门线三项。
    # 目标创建时带过 class_business_ids（coverage 口径），修订后底线 coverage 也补回。
    trimmed = patch(
        {
            "checklist": [
                {"key": "custom", "requirement": "只交付草稿", "kind": "draft_only", "params": {}}
            ]
        }
    )
    assert trimmed.status_code == 200, trimmed.text
    kinds = {item["kind"] for item in trimmed.json()["checklist"]}
    assert {"deliverable_exists", "no_hard_conflicts", "coverage", "draft_only"} <= kinds
    assert trimmed.json()["checklist_version"] == 2

    # 第二次修订：版本 3，历史长度 2（版本 1、2 依序快照）。
    second = patch(
        {
            "checklist": [
                {"key": "custom", "requirement": "改口径", "kind": "draft_only", "params": {}}
            ]
        }
    )
    assert second.status_code == 200
    assert second.json()["checklist_version"] == 3
    detail = client.get(f"/api/v1/goals/{goal_id}", headers=headers)
    history = detail.json()["checklist_history"]
    assert [entry["version"] for entry in history] == [1, 2]
    assert all(entry["items"] for entry in history)

    # 已放弃目标：终态，清单不再接受修订。
    abandoned = client.post(f"/api/v1/goals/{goal_id}/abandon", headers=headers)
    assert abandoned.status_code == 200
    after_abandon = patch(
        {"checklist": [{"key": "a", "requirement": "r", "kind": "draft_only", "params": {}}]}
    )
    assert after_abandon.status_code == 409

    # 跨方案：goal 属于另一方案时按 404 处理（不作为侧信道暴露存在性）。
    other = _make_scope(client, auth_headers)
    cross = client.patch(
        f"/api/v1/goals/{goal_id}/checklist",
        headers=other["headers"],
        json={"checklist": [{"key": "a", "requirement": "r", "kind": "draft_only", "params": {}}]},
    )
    assert cross.status_code == 404


# ------------------------------------------------- MEM-E2：版本对齐与底线范围


def test_replace_checklist_resets_acceptance_and_marks_historical_conclusion(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """E2a：清单修订后 acceptance_status 强制 pending、achieved 回退 open；
    latest_run_id 与旧报告保留，但 PATCH 响应把旧结论标注为「历史版本结论」。
    修复前：修订清单后页面同时显示 v2 清单和 v1 的「已达成」。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    course = _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))
    # snapshot_sessions 模拟求解输入快照：C23 进了本次求解范围并被安置。
    run = _make_run(
        scope["scope_id"],
        goal_id,
        [_assignment(course)],
        snapshot_sessions=[{"business_id": "C23", "is_active": True, "class_business_id": "B1"}],
    )
    with SessionLocal() as db:
        assert apply_goal_evaluation(db, db.get(SolverRun, run.id)) is not None
        db.commit()
    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        assert goal_row.status == "achieved"
        assert goal_row.acceptance_status == "completed"
        latest_run_id = goal_row.latest_run_id
        assert latest_run_id is not None

    # 修订清单（把禁排项参数改了——内容本身无所谓，重点是结论必须失效）。
    patched = client.patch(
        f"/api/v1/goals/{goal_id}/checklist",
        headers=headers,
        json={
            "checklist": [
                {
                    "key": "coverage",
                    "requirement": "覆盖目标课次（修订口径）",
                    "kind": "coverage",
                    "params": {"class_business_ids": ["B1"]},
                },
                {
                    "key": "forbidden_slot_free-1",
                    "requirement": "T9 不占 S2",
                    "kind": "forbidden_slot_free",
                    "params": {
                        "subject_type": "teacher",
                        "subject_ids": ["T9"],
                        "slot_business_ids": ["S2"],
                    },
                },
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["checklist_version"] == 2
    # 结论失效：achieved → open，acceptance_status → pending，detail 说明原因。
    assert body["status"] == "open"
    assert body["acceptance_status"] == "pending"
    assert "清单修订至 v2" in (body["acceptance_detail"] or "")
    assert "等待新验收" in (body["acceptance_detail"] or "")
    # 旧报告保留但标注为历史版本结论。
    assert body["latest_run_id"] == latest_run_id
    meta = body["latest_report_meta"]
    assert meta is not None
    assert meta["run_id"] == latest_run_id
    assert meta["checklist_version"] == 1
    assert meta["is_current_version"] is False
    assert "历史版本 v1" in meta["note"]

    # 详情接口同样能看到 pending + 历史报告仍在 runs 里。
    detail = client.get(f"/api/v1/goals/{goal_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["acceptance_status"] == "pending"
    assert any(
        run_item["goal_report"] is not None for run_item in detail.json()["runs"]
    )

    # 修订后按新清单重新验收：状态随之更新为 completed（pending → 主路径）。
    run2 = _make_run(
        scope["scope_id"],
        goal_id,
        [_assignment(course, slot="S1")],
        snapshot_sessions=[{"business_id": "C23", "is_active": True, "class_business_id": "B1"}],
    )
    with SessionLocal() as db:
        report = apply_goal_evaluation(db, db.get(SolverRun, run2.id))
        db.commit()
    assert report is not None
    assert report["meta"]["checklist_version"] == 2
    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        assert goal_row.acceptance_status == "completed"
        assert goal_row.acceptance_detail is None


def test_acceptance_binds_checklist_version_and_snapshot(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """E2a：run 完成验收时把所用 checklist_version 与 coverage 参数快照写进
    report.meta；求解创建晚于清单修订时照常按最新版验收，但 meta 注明两个版本。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    course = _add_course(scope["scope_id"], "C23", lesson_date=date(2026, 10, 5))

    # v1 验收：meta 绑定 v1 + coverage 参数快照。
    run1 = _make_run(scope["scope_id"], goal_id, [_assignment(course)])
    with SessionLocal() as db:
        report1 = apply_goal_evaluation(db, db.get(SolverRun, run1.id))
        db.commit()
    assert report1 is not None
    assert report1["meta"]["checklist_version"] == 1
    snapshot_v1 = report1["meta"]["checklist_snapshot"]["coverage"]
    assert snapshot_v1["class_business_ids"] == ["B1"]
    assert snapshot_v1["bottom_line"] is True

    # 修订到 v2（换范围参数），再次验收：meta 绑定 v2 与新快照。
    patched = client.patch(
        f"/api/v1/goals/{goal_id}/checklist",
        headers=headers,
        json={
            "checklist": [
                {
                    "key": "coverage",
                    "requirement": "覆盖 C23",
                    "kind": "coverage",
                    "params": {"course_business_ids": ["C23"]},
                }
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    run2 = _make_run(scope["scope_id"], goal_id, [_assignment(course)])
    with SessionLocal() as db:
        report2 = apply_goal_evaluation(db, db.get(SolverRun, run2.id))
        db.commit()
    assert report2 is not None
    assert report2["meta"]["checklist_version"] == 2
    snapshot_v2 = report2["meta"]["checklist_snapshot"]["coverage"]
    assert snapshot_v2["course_business_ids"] == ["C23"]
    assert "class_business_ids" not in snapshot_v2
    # 历史报告的 meta 不被改写：v1 报告仍绑 v1。
    with SessionLocal() as db:
        stored1 = db.get(SolverRun, run1.id)
        assert stored1 is not None and stored1.goal_report is not None
        assert stored1.goal_report["meta"]["checklist_version"] == 1

    # run 请求载荷里冻结了创建时的清单版本（真实链路由 create_solver_run 在
    # 创建任务时写入 request_payload.goal_checklist_version；手工 _make_run
    # 不经过那条链路，这里直接落一条带冻结版本的 run 验证端到端口径）。
    run2f = _make_run(scope["scope_id"], goal_id, [_assignment(course)])
    with SessionLocal() as db:
        run2f_row = db.get(SolverRun, run2f.id)
        assert run2f_row is not None
        payload = dict(run2f_row.request_payload or {})
        payload["goal_checklist_version"] = 2
        run2f_row.request_payload = payload
        db.commit()
        report2f = apply_goal_evaluation(db, run2f_row)
        db.commit()
    assert report2f is not None
    assert report2f["meta"]["checklist_version"] == 2
    # 创建时版本 = 验收时版本 → 无版本口径注记。
    assert "version_note" not in report2f["meta"]
    assert "solve_checklist_version" not in report2f["meta"]

    # 求解创建于 v2，但清单在途又修订到 v3 → 照常按 v3 验收，meta 注明口径。
    patched2 = client.patch(
        f"/api/v1/goals/{goal_id}/checklist",
        headers=headers,
        json={
            "checklist": [
                {
                    "key": "coverage",
                    "requirement": "覆盖 C23（第三次修订）",
                    "kind": "coverage",
                    "params": {"course_business_ids": ["C23"]},
                }
            ]
        },
    )
    assert patched2.status_code == 200, patched2.text
    run3 = _make_run(scope["scope_id"], goal_id, [_assignment(course)])
    with SessionLocal() as db:
        run3_row = db.get(SolverRun, run3.id)
        assert run3_row is not None
        # 模拟「run 创建于 v2」：把冻结的版本号回写为 2（真实链路由
        # create_solver_run 落入，这里直接注入以验证 meta 注记逻辑）。
        payload = dict(run3_row.request_payload or {})
        payload["goal_checklist_version"] = 2
        run3_row.request_payload = payload
        db.commit()
        report3 = apply_goal_evaluation(db, run3_row)
    assert report3 is not None
    assert report3["meta"]["checklist_version"] == 3
    assert report3["meta"]["solve_checklist_version"] == 2
    assert "求解参数基于 v2 清单生成，验收按当前最新版 v3" in report3["meta"]["version_note"]


def test_bottom_line_coverage_carries_full_scope_and_accepts(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """E2b 场景①：创建时顶层指定课次 C1/C2、自定义清单只有 draft_only →
    补入的 coverage 带上 C1/C2 参数；真实求解后该项按范围验收（不再
    unverifiable）。修复前：补入的 coverage 无参数 → 排好了也判 unverifiable。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    c1 = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    c2 = _add_course(scope["scope_id"], "C2", lesson_date=date(2026, 10, 6))
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={
            "instruction": "把 C1、C2 排好",
            "course_business_ids": ["C1", "C2"],
            "checklist": [
                {
                    "key": "draft_only",
                    "requirement": "只交付草稿",
                    "kind": "draft_only",
                    "params": {},
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    goal = created.json()
    coverage = next(item for item in goal["checklist"] if item["kind"] == "coverage")
    # 底线补入的 coverage 携带完整规范化范围参数。
    assert coverage["params"]["bottom_line"] is True
    assert coverage["params"]["course_business_ids"] == ["C1", "C2"]

    # 真实求解（wait=true 内联执行 CP-SAT）：两课次都排上 → coverage 按范围验收通过。
    solved = client.post(
        "/api/v1/solver-runs",
        headers=headers,
        json={
            "course_business_ids": ["C1", "C2"],
            "goal_id": goal["id"],
            "wait": True,
        },
    )
    assert solved.status_code == 202, solved.text
    report = solved.json()["goal_report"]
    assert report is not None
    cov_item = next(item for item in report["items"] if item["kind"] == "coverage")
    assert cov_item["passed"] is True, cov_item["detail"]
    assert cov_item["verdict"] == "passed"
    # 独立不重复底线也在报告里。
    dup_item = next(item for item in report["items"] if item["kind"] == "no_duplicate_lessons")
    assert dup_item["passed"] is True
    assert (c1.business_id, c2.business_id) == ("C1", "C2")  # 范围课次确已存在


def test_patch_checklist_keeps_old_coverage_scope(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """E2b 场景②：修订丢 coverage → 旧 B1 班范围保留在补回的底线 coverage 里；
    显式给出新范围字段才覆盖（body.scope）。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "重排 B1 班 10 月的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]

    # 修订：body 只传 draft_only，不带 scope → 旧 coverage 的 B1 范围必须保留。
    patched = client.patch(
        f"/api/v1/goals/{goal_id}/checklist",
        headers=headers,
        json={
            "checklist": [
                {
                    "key": "draft_only",
                    "requirement": "只交付草稿",
                    "kind": "draft_only",
                    "params": {},
                }
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    coverage = next(item for item in body["checklist"] if item["kind"] == "coverage")
    assert coverage["params"]["bottom_line"] is True
    assert coverage["params"]["class_business_ids"] == ["B1"]
    assert body["checklist_version"] == 2

    # 显式给新范围（body.scope.course_business_ids）→ 覆盖旧范围。
    patched2 = client.patch(
        f"/api/v1/goals/{goal_id}/checklist",
        headers=headers,
        json={
            "checklist": [
                {
                    "key": "draft_only",
                    "requirement": "只交付草稿",
                    "kind": "draft_only",
                    "params": {},
                }
            ],
            "scope": {"course_business_ids": ["C23"]},
        },
    )
    assert patched2.status_code == 200, patched2.text
    coverage2 = next(item for item in patched2.json()["checklist"] if item["kind"] == "coverage")
    assert coverage2["params"]["course_business_ids"] == ["C23"]
    # 未在 scope 里显式给出的旧字段（class_business_ids）继续保留。
    assert coverage2["params"]["class_business_ids"] == ["B1"]


def test_no_duplicate_lessons_bottom_line_without_scope(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """E2b 场景③：无范围的自定义清单 → no_duplicate_lessons 底线存在且能抓
    重复交付（不依赖 coverage 的范围口径兜底）。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={
            "instruction": "随便排一下，只出草稿",
            "checklist": [
                {
                    "key": "draft_only",
                    "requirement": "只交付草稿",
                    "kind": "draft_only",
                    "params": {},
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    goal = created.json()
    kinds = [item["kind"] for item in goal["checklist"]]
    assert "no_duplicate_lessons" in kinds
    dup_item = next(item for item in goal["checklist"] if item["kind"] == "no_duplicate_lessons")
    assert dup_item["params"]["bottom_line"] is True
    assert "coverage" not in kinds  # 无范围 → 不补 coverage，重复检测由新底线兜底

    course = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    run = _make_run(scope["scope_id"], goal["id"], [_assignment(course), _assignment(course)])
    with SessionLocal() as db:
        report = apply_goal_evaluation(db, db.get(SolverRun, run.id))
    assert report is not None
    item = next(entry for entry in report["items"] if entry["kind"] == "no_duplicate_lessons")
    assert item["passed"] is False
    assert item["evidence"]["duplicates"][0]["course_business_id"] == "C1"
    assert item["evidence"]["duplicates"][0]["count"] == 2
    assert report["all_passed"] is False
    # 缺口定位：与 coverage 重复缺口同一处置叙事。
    gap = next(gap for gap in report["gaps"] if gap["kind"] == "no_duplicate_lessons")
    assert gap["remedy"] == "await_admin"
    assert "重复导入" in gap["next_step"]

    # 正常无重复交付 → 通过。
    run_ok = _make_run(scope["scope_id"], goal["id"], [_assignment(course)])
    with SessionLocal() as db:
        report_ok = apply_goal_evaluation(db, db.get(SolverRun, run_ok.id))
    assert report_ok is not None
    item_ok = next(entry for entry in report_ok["items"] if entry["kind"] == "no_duplicate_lessons")
    assert item_ok["passed"] is True


def test_bottom_line_items_final_validation_and_frontend_badge_data(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """E2b 场景④：补全后统一校验最终清单 key 唯一（重复 422）；底线项
    bottom_line=True 透传报告——前端两项底线徽标的数据源。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]

    # 与底线补齐项 key 冲突（同一 key 塞两遍）→ 统一校验拒绝。
    dup = client.patch(
        f"/api/v1/goals/{goal_id}/checklist",
        headers=headers,
        json={
            "checklist": [
                {"key": "d1", "requirement": "r", "kind": "draft_only", "params": {}},
                {"key": "d1", "requirement": "r2", "kind": "draft_only", "params": {}},
            ]
        },
    )
    assert dup.status_code == 422

    # 底线徽标数据源：报告 items 的 bottom_line 标记同时覆盖 coverage 与
    # no_duplicate_lessons（前端对两项都显示「底线」徽标）。
    goal_id2 = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班并交付", "class_business_ids": ["B1"]},
    ).json()["id"]
    course = _add_course(scope["scope_id"], "C9", lesson_date=date(2026, 10, 5))
    run = _make_run(scope["scope_id"], goal_id2, [_assignment(course)])
    with SessionLocal() as db:
        report = apply_goal_evaluation(db, db.get(SolverRun, run.id))
    assert report is not None
    bottom_kinds = {
        entry["kind"] for entry in report["items"] if entry.get("bottom_line")
    }
    assert {"coverage", "no_duplicate_lessons"} <= bottom_kinds
    assert "deliverable_exists" in bottom_kinds and "no_hard_conflicts" in bottom_kinds
