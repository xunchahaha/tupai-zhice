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
    # 底线项（MEM-D2/D4c）在最前：deliverable_exists、coverage、no_hard_conflicts。
    assert kinds == [
        "deliverable_exists",
        "coverage",
        "date_range_match",
        "forbidden_slot_free",
        "no_hard_conflicts",
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
    assert checklist[5]["params"] == {"max_changes": 5, "baseline_schedule_version_id": "ver-1"}
    assert "上限" in checklist[5]["requirement"]

    light = build_checklist("随便排一下", forbid_publish=False)
    assert [item["kind"] for item in light] == [
        "deliverable_exists",
        "coverage",
        "no_hard_conflicts",
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
    # 占位项追加在生成器产物末尾（deliverable_exists/coverage/date/no_hard/draft_only 之后）。
    assert kinds == [
        "deliverable_exists",
        "coverage",
        "date_range_match",
        "no_hard_conflicts",
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
    # 底线（MEM-D2/D4c）强制在前：deliverable_exists + coverage + no_hard_conflicts。
    assert kinds == ["deliverable_exists", "coverage", "no_hard_conflicts", "draft_only"]
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
