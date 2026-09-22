"""目标验收正确性修复（MEM-D2，第二轮源码复审 D4/D6 组）验收测试。

对照 docs/roadmap/02-agent-memory.md §7 表 D4/D6 与组合验收要求：

1. D4a 三集合分离：整月课表局部重排一周时，合并回填的旧课次（保留原样）
   不被判日期越界——date_range/coverage/forbidden 只看 目标课次 ∪ 本次求解课次，
   no_hard_conflicts 才看合并交付课表；
2. D4b 无法验证 ≠ 通过：目标课次缺日期 → verdict="unverifiable" 且 all_passed=false；
3. D4c 底线验收独立于自定义清单：仅 draft_only 的自定义清单在零课表上也
   达不了成（deliverable_exists 强制并入）；
4. D4c coverage 查重：缺一课与重复一课各判 failed；
5. D4d 禁排参数存在性：subject/slot 不在当前方案主数据 → unverifiable；
6. D6 决策消费求解状态：UNKNOWN 与 INFEASIBLE 的 _decide 文案/状态分支；
7. D6 验收异常可见：acceptance_status=failed 落库且 API 可见（含 run 失败路径）。
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.services.goal as goal_module
from app.db import SessionLocal
from app.models import (
    Campus,
    CourseSession,
    DataSnapshot,
    Room,
    SolveGoal,
    SolverRun,
    Teacher,
    TimeSlot,
)
from app.services.goal import (
    apply_goal_evaluation,
    evaluate_goal,
)
from app.services.tasks import _evaluate_goal_for_run, _persist_failure


def _make_scope(client: TestClient, auth_headers: dict[str, str]) -> dict[str, Any]:
    """独立课表方案：最小主数据（1 教室 / 1 个人教师 / 两个时段）。"""
    created = client.post(
        "/api/v1/schedule-sets",
        headers=auth_headers,
        json={"name": f"目标正确性-{uuid4().hex[:8]}"},
    )
    assert created.status_code == 201, created.text
    scope_id = created.json()["id"]
    with SessionLocal() as db:
        campus = Campus(schedule_set_id=scope_id, business_id="C", name="正确性校区")
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
    instruction: str = "局部重排一周课表",
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
    solved_course_business_ids: list[str] | None = None,
    presolve_infeasible: bool = False,
    snapshot_sessions: list[dict[str, Any]] | None = None,
) -> SolverRun:
    """直接落一条 completed 的 SolverRun（不跑 CP-SAT，验收器只看结果载荷）。

    solved_course_business_ids 显式传入以模拟 _persist_result 的合并前口径；
    缺省时按 assignments 全集推导（相当于无合并回填的整段求解）。
    snapshot_sessions 模拟求解输入快照（coverage 用它区分「没进范围」与
    「进了范围没安置」）。
    """
    with SessionLocal() as db:
        snapshot = DataSnapshot(
            schedule_set_id=scope_id,
            revision=1,
            checksum=f"goal-d2-{uuid4().hex[:8]}",
            payload={"course_sessions": snapshot_sessions or []},
        )
        db.add(snapshot)
        db.flush()
        solved = (
            solved_course_business_ids
            if solved_course_business_ids is not None
            else sorted(
                {
                    str(a.get("course_business_id"))
                    for a in assignments
                    if a.get("course_business_id")
                }
            )
        )
        run = SolverRun(
            schedule_set_id=scope_id,
            snapshot_id=snapshot.id,
            run_type="initial",
            status="completed",
            model_status=model_status,
            request_payload={},
            result_payload={
                "assignments": assignments,
                "solved_course_business_ids": solved,
                "presolve_infeasible": presolve_infeasible,
            },
            goal_id=goal_id,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run


def _assignment(
    course: CourseSession,
    *,
    slot: str = "S1",
    room: str = "R1",
    change_kind: str | None = "assigned",
    lesson_date: date | None = None,
) -> dict[str, Any]:
    effective_date = lesson_date if lesson_date is not None else course.lesson_date
    item = {
        "course_session_id": course.id,
        "course_business_id": course.business_id,
        "class_business_id": course.class_business_id,
        "teacher_business_id": course.teacher_business_id,
        "lesson_date": effective_date.isoformat() if effective_date else None,
        "slot_business_id": slot,
        "room_business_id": room,
    }
    if change_kind is not None:
        item["change_kind"] = change_kind
    return item


def _evaluate(goal: SolveGoal, run: SolverRun) -> dict[str, Any]:
    with SessionLocal() as db:
        return evaluate_goal(db, db.get(SolveGoal, goal.id), db.get(SolverRun, run.id))


def _item_by_kind(report: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(entry for entry in report["items"] if entry["kind"] == kind)


# ------------------------------------------------- D4a 三集合分离


def test_partial_reschedule_kept_sessions_not_flagged_out_of_range(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """整月课表局部重排一周：保留的其他日期课次不被判越界。"""
    scope = _make_scope(client, auth_headers)
    # 目标课次：8 月那一周；保留课次：9 月（父版本合并回填，本次求解没碰）。
    target = _add_course(scope["scope_id"], "C_IN", lesson_date=date(2026, 8, 19))
    kept = _add_course(scope["scope_id"], "C_KEEP", lesson_date=date(2026, 9, 2))
    goal = _make_goal(
        scope["scope_id"],
        [
            {
                "key": "coverage",
                "requirement": "覆盖目标周课次",
                "kind": "coverage",
                "params": {"course_business_ids": ["C_IN"]},
            },
            {
                "key": "date_range_match",
                "requirement": "日期在 8 月那一周内",
                "kind": "date_range_match",
                "params": {"date_from": "2026-08-17", "date_to": "2026-08-21"},
            },
        ],
    )
    run = _make_run(
        scope["scope_id"],
        goal.id,
        [
            _assignment(target),
            # 合并回填行：change_kind=unchanged，且不在 solved 集合里。
            _assignment(kept, change_kind="unchanged"),
        ],
        solved_course_business_ids=["C_IN"],
        snapshot_sessions=[{"business_id": "C_IN", "is_active": True, "class_business_id": "B1"}],
    )
    report = _evaluate(goal, run)
    date_item = _item_by_kind(report, "date_range_match")
    # 修复前：合并回填的 9 月课次会被判越界；修复后：只核对 目标 ∪ 求解 集合。
    assert date_item["passed"] is True
    assert "C_KEEP" not in (date_item["detail"] or "")
    assert _item_by_kind(report, "coverage")["passed"] is True
    assert report["all_passed"] is True
    # 回执里不出现保留课次：核对对象只含目标课次。
    assert date_item["evidence"]["checked_count"] == 1

    # 阳性对照：目标课次本身越界必须抓到——「不误伤保留课次」不是放松检查。
    run_bad = _make_run(
        scope["scope_id"],
        goal.id,
        [
            _assignment(target, lesson_date=date(2026, 9, 2)),
            _assignment(kept, change_kind="unchanged"),
        ],
        solved_course_business_ids=["C_IN"],
        snapshot_sessions=[{"business_id": "C_IN", "is_active": True, "class_business_id": "B1"}],
    )
    report_bad = _evaluate(goal, run_bad)
    assert _item_by_kind(report_bad, "date_range_match")["passed"] is False
    assert "C_IN" in _item_by_kind(report_bad, "date_range_match")["detail"]


def test_hard_conflicts_still_see_merged_deliverable(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """no_hard_conflicts 口径 = 合并交付课表（全局资源冲突看全集）。"""
    scope = _make_scope(client, auth_headers)
    target = _add_course(scope["scope_id"], "C_IN", lesson_date=date(2026, 8, 19))
    kept = _add_course(
        scope["scope_id"], "C_KEEP", lesson_date=date(2026, 8, 19), teacher_business_id="T9"
    )
    goal = _make_goal(
        scope["scope_id"],
        [
            {
                "key": "no_hard_conflicts",
                "requirement": "无硬冲突",
                "kind": "no_hard_conflicts",
                "params": {"bottom_line": True},
            }
        ],
    )
    # 目标课次 C_IN 排进求解集合，保留课次 C_KEEP 是合并回填行；同教师同日
    # 同时段 → 冲突在「合并全集」上，必须被抓到。
    run = _make_run(
        scope["scope_id"],
        goal.id,
        [_assignment(target), _assignment(kept, change_kind="unchanged")],
        solved_course_business_ids=["C_IN"],
    )
    report = _evaluate(goal, run)
    item = _item_by_kind(report, "no_hard_conflicts")
    assert item["passed"] is False
    assert item["evidence"]["total"] > 0


# ------------------------------------------------- D4b 无法验证 ≠ 通过


def test_missing_target_date_is_unverifiable_not_passed(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """目标课次日期缺失 → 该项 unverifiable 且 all_passed=false。"""
    scope = _make_scope(client, auth_headers)
    target = _add_course(scope["scope_id"], "C1", lesson_date=None)
    goal = _make_goal(
        scope["scope_id"],
        [
            {
                "key": "coverage",
                "requirement": "覆盖目标课次",
                "kind": "coverage",
                "params": {"course_business_ids": ["C1"]},
            },
            {
                "key": "date_range_match",
                "requirement": "日期在范围内",
                "kind": "date_range_match",
                "params": {"date_from": "2026-08-17", "date_to": "2026-08-21"},
            },
        ],
    )
    run = _make_run(scope["scope_id"], goal.id, [_assignment(target, lesson_date=None)])
    report = _evaluate(goal, run)
    date_item = _item_by_kind(report, "date_range_match")
    assert date_item["passed"] is False
    assert date_item["verdict"] == "unverifiable"
    assert "缺少上课日期" in date_item["detail"]
    assert report["all_passed"] is False
    assert report["unverifiable_count"] >= 1

    # 无日期参数 + 参数无法解析，同样 unverifiable，不因「没检测到越界」放行。
    goal_noparams = _make_goal(
        scope["scope_id"],
        [
            {
                "key": "date_range_match",
                "requirement": "日期在范围内",
                "kind": "date_range_match",
                "params": {},
            }
        ],
    )
    run_ok = _make_run(scope["scope_id"], goal_noparams.id, [_assignment(target)])
    report_noparams = _evaluate(goal_noparams, run_ok)
    assert _item_by_kind(report_noparams, "date_range_match")["verdict"] == "unverifiable"
    assert report_noparams["all_passed"] is False


# ------------------------------------------------- D4c 底线验收独立于自定义清单


def test_draft_only_custom_checklist_cannot_pass_without_schedule(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """仅 draft_only 的自定义清单：run 无课表产物 → deliverable_exists 不通过。"""
    scope = _make_scope(client, auth_headers)
    created = client.post(
        "/api/v1/goals",
        headers=scope["headers"],
        json={
            "instruction": "只出草稿，别的不用管",
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
    # 底线强制并入：deliverable_exists / no_hard_conflicts / no_duplicate_lessons
    # （MEM-E2/E2b：不重复检查独立于范围）；无范围字段 → 不补 coverage。
    assert "deliverable_exists" in kinds
    assert "no_hard_conflicts" in kinds
    assert "no_duplicate_lessons" in kinds
    assert "coverage" not in kinds
    bottom = [item for item in goal["checklist"] if item["params"].get("bottom_line")]
    assert {item["kind"] for item in bottom} == {
        "deliverable_exists",
        "no_hard_conflicts",
        "no_duplicate_lessons",
    }

    # 关联一次「成功但零课表」的求解（空范围 → OPTIMAL + 0 assignments）。
    solved = client.post(
        "/api/v1/solver-runs",
        headers=scope["headers"],
        json={"goal_id": goal["id"], "wait": True},
    )
    assert solved.status_code == 202, solved.text
    run = solved.json()
    assert run["goal_report"] is not None
    report = run["goal_report"]
    assert report["all_passed"] is False
    deliverable = next(item for item in report["items"] if item["kind"] == "deliverable_exists")
    assert deliverable["passed"] is False
    # 跑完 ≠ 达成：目标不得转 achieved。
    detail = client.get(f"/api/v1/goals/{goal['id']}", headers=scope["headers"])
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] != "achieved"
    assert detail.json()["acceptance_status"] == "completed"


def test_coverage_detects_missing_and_duplicated_courses(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """coverage：缺一课 + 重复一课各一条断言。"""
    scope = _make_scope(client, auth_headers)
    c1 = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    _add_course(scope["scope_id"], "C2", lesson_date=date(2026, 10, 6))
    goal = _make_goal(
        scope["scope_id"],
        [
            {
                "key": "coverage",
                "requirement": "目标课次完整且不重复",
                "kind": "coverage",
                "params": {"course_business_ids": ["C1", "C2"]},
            }
        ],
    )
    # 缺一课：C2 进范围但未安置。
    run_missing = _make_run(
        scope["scope_id"],
        goal.id,
        [_assignment(c1)],
        solved_course_business_ids=["C1"],
        snapshot_sessions=[
            {"business_id": "C1", "is_active": True, "class_business_id": "B1"},
            {"business_id": "C2", "is_active": True, "class_business_id": "B1"},
        ],
    )
    report = _evaluate(goal, run_missing)
    coverage = _item_by_kind(report, "coverage")
    assert coverage["passed"] is False
    assert coverage["evidence"]["missing_from_result"] == ["C2"]

    # 重复一课：同一课次出现 ≥2 次判 failed。
    run_dup = _make_run(
        scope["scope_id"],
        goal.id,
        [_assignment(c1), _assignment(c1)],
        solved_course_business_ids=["C1"],
        snapshot_sessions=[{"business_id": "C1", "is_active": True, "class_business_id": "B1"}],
    )
    report_dup = _evaluate(goal, run_dup)
    coverage_dup = _item_by_kind(report_dup, "coverage")
    assert coverage_dup["passed"] is False
    assert coverage_dup["evidence"]["duplicates"][0]["course_business_id"] == "C1"
    assert coverage_dup["evidence"]["duplicates"][0]["count"] == 2
    assert "重复" in coverage_dup["detail"]
    assert report_dup["all_passed"] is False


# ------------------------------------------------- D4d 禁排参数存在性


def test_forbidden_slot_with_nonexistent_objects_is_unverifiable(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """禁排参数指向不存在的时段（或主体）→ unverifiable，不当成已满足。"""
    scope = _make_scope(client, auth_headers)
    course = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    goal = _make_goal(
        scope["scope_id"],
        [
            {
                "key": "forbidden_slot_free-1",
                "requirement": "T9 不占不存在时段",
                "kind": "forbidden_slot_free",
                "params": {
                    "subject_type": "teacher",
                    "subject_ids": ["T9"],
                    "slot_business_ids": ["S_NOPE"],
                },
            },
            {
                "key": "forbidden_slot_free-2",
                "requirement": "不存在教师不占 S1",
                "kind": "forbidden_slot_free",
                "params": {
                    "subject_type": "teacher",
                    "subject_ids": ["T_NOPE"],
                    "slot_business_ids": ["S1"],
                },
            },
        ],
    )
    run = _make_run(scope["scope_id"], goal.id, [_assignment(course, slot="S1")])
    report = _evaluate(goal, run)
    for key in ("forbidden_slot_free-1", "forbidden_slot_free-2"):
        item = next(entry for entry in report["items"] if entry["key"] == key)
        assert item["passed"] is False, key
        assert item["verdict"] == "unverifiable", key
        assert "禁排对象不存在，请补齐参数" in item["detail"], key
    assert report["all_passed"] is False


# ------------------------------------------------- D6 决策消费求解状态


def test_decide_unknown_keeps_goal_open_with_budget_narrative(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """UNKNOWN（含超时）：预算与诊断决定是否继续，goal 保持 open。"""
    scope = _make_scope(client, auth_headers)
    goal = _make_goal(
        scope["scope_id"],
        [
            {
                "key": "deliverable_exists",
                "requirement": "存在非空课表产物",
                "kind": "deliverable_exists",
                "params": {"bottom_line": True},
            }
        ],
    )
    run = _make_run(scope["scope_id"], goal.id, [], model_status="UNKNOWN")
    report = _evaluate(goal, run)
    assert report["decision"]["status"] == "open"
    assert "UNKNOWN" in report["decision"]["reason"]
    assert "预算与诊断" in report["decision"]["reason"]
    assert report["decision"]["status"] != "awaiting_decision"
    assert report["gaps"][0]["remedy"] == "raise_budget"


def test_decide_infeasible_enters_awaiting_decision(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """INFEASIBLE：「当前模型已证明无解」进 awaiting_decision 调整流程。"""
    scope = _make_scope(client, auth_headers)
    goal = _make_goal(
        scope["scope_id"],
        [
            {
                "key": "deliverable_exists",
                "requirement": "存在非空课表产物",
                "kind": "deliverable_exists",
                "params": {"bottom_line": True},
            }
        ],
    )
    run = _make_run(scope["scope_id"], goal.id, [], model_status="INFEASIBLE")
    report = _evaluate(goal, run)
    assert report["decision"]["status"] == "awaiting_decision"
    assert "已证明无解" in report["decision"]["reason"]

    # 求解前预检不可行 ≠ 已证明无解（CP-SAT 未运行）：保持 open，如实说明。
    run_presolve = _make_run(
        scope["scope_id"],
        goal.id,
        [],
        model_status="INFEASIBLE",
        presolve_infeasible=True,
    )
    report_presolve = _evaluate(goal, run_presolve)
    assert report_presolve["decision"]["status"] == "open"
    assert "预检" in report_presolve["decision"]["reason"]
    # 预检路径不得使用「当前模型已证明无解」的结论性叙事。
    assert "当前模型已证明无解" not in report_presolve["decision"]["reason"]


# ------------------------------------------------- D6 验收可见性


def test_acceptance_exception_marks_goal_failed_and_visible_via_api(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收异常 → acceptance_status=failed 且 API 可见，原因入库。"""
    scope = _make_scope(client, auth_headers)
    created = client.post(
        "/api/v1/goals",
        headers=scope["headers"],
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal = created.json()
    course = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    run = _make_run(scope["scope_id"], goal["id"], [_assignment(course)])

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("验收器炸了")

    monkeypatch.setattr(goal_module, "apply_goal_evaluation", _boom)
    _evaluate_goal_for_run(run.id, goal["id"])
    monkeypatch.undo()

    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal["id"])
        assert goal_row is not None
        assert goal_row.acceptance_status == "failed"
        assert goal_row.acceptance_detail and "验收器炸了" in goal_row.acceptance_detail
        run_row = db.get(SolverRun, run.id)
        assert run_row is not None and run_row.goal_report is not None
        assert run_row.goal_report["acceptance_status"] == "failed"
        assert "验收器炸了" in str(run_row.goal_report["acceptance_error"])

    detail = client.get(f"/api/v1/goals/{goal['id']}", headers=scope["headers"])
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["acceptance_status"] == "failed"
    assert "验收器炸了" in (body["acceptance_detail"] or "")


def test_run_failure_marks_acceptance_failed_so_frontend_can_stop(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """run 失败 = 不会有验收报告：同步置 failed 并落失败标记，前端可停轮询。"""
    scope = _make_scope(client, auth_headers)
    created = client.post(
        "/api/v1/goals",
        headers=scope["headers"],
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal = created.json()
    with SessionLocal() as db:
        snapshot = DataSnapshot(
            schedule_set_id=scope["scope_id"],
            revision=1,
            checksum=f"goal-fail-{uuid4().hex[:8]}",
            payload={},
        )
        db.add(snapshot)
        db.flush()
        run = SolverRun(
            schedule_set_id=scope["scope_id"],
            snapshot_id=snapshot.id,
            status="queued",
            request_payload={},
            goal_id=goal["id"],
        )
        db.add(run)
        db.commit()
        run_id = run.id
    _persist_failure(run_id, "求解器崩溃")
    detail = client.get(f"/api/v1/goals/{goal['id']}", headers=scope["headers"])
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["acceptance_status"] == "failed"
    assert "求解失败" in (body["acceptance_detail"] or "")


def test_apply_goal_evaluation_marks_completed_on_success(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """验收成功 → acceptance_status=completed（pending→completed 的主路径）。"""
    scope = _make_scope(client, auth_headers)
    created = client.post(
        "/api/v1/goals",
        headers=scope["headers"],
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal = created.json()
    course = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    run = _make_run(scope["scope_id"], goal["id"], [_assignment(course)])
    with SessionLocal() as db:
        run_row = db.get(SolverRun, run.id)
        assert run_row is not None
        assert apply_goal_evaluation(db, run_row) is not None
        db.commit()
        goal_row = db.get(SolveGoal, goal["id"])
        assert goal_row is not None
        assert goal_row.acceptance_status == "completed"
        assert goal_row.acceptance_detail is None
