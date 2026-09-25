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
7. D6 验收异常可见：acceptance_status=failed 落库且 API 可见（含 run 失败路径）；
8. MEM-F/F2：旧版本验收报告写回不覆盖新版本的 pending（§9 第四轮复审）；
9. MEM-F/F3：范围修订三态语义（未提交保留 / 显式空清除 / 显式非空替换），
   显式 scope 优先于清单 coverage 既有参数；
10. 第五轮复审 F2 收口：验收写回只走数据库条件 UPDATE（WHERE 携带持久化
    checklist_revision = 评估时版本 AND status <> 'abandoned'），按实际行数
    判定——修订/放弃发生在重读之后、提交之前的竞态窗口内，报告只留档为
    历史，不得改写目标当前状态。
11. 第六轮复审收口：修订侧同构保护——清单修订落库只走单条数据库条件 UPDATE
    （WHERE 携带读取时 checklist_revision AND status <> 'abandoned'；SET 无条件
    acceptance_status='pending'、按库中状态把 achieved 回退 open）。先验收后
    修订、修订与放弃交错、并发版本冲突一律返回冲突响应，请求旧快照不得
    覆盖库中状态，冲突路径不留修订审计记录。
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.api as api_module
import app.services.goal as goal_module
from app.db import SessionLocal
from app.models import (
    AuditLog,
    Campus,
    CourseSession,
    DataSnapshot,
    Room,
    SolveGoal,
    SolverRun,
    Teacher,
    TimeSlot,
)
from app.schemas import GoalScopePatch
from app.services.goal import (
    _checklist_version,
    apply_goal_evaluation,
    ensure_bottom_line_items,
    evaluate_goal,
    merge_coverage_scope,
    normalize_goal_scope,
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
    # 求解输入快照带上目标课次：coverage 按范围核对通过，报告 decision=achieved
    # （交错④验证「无竞争时正常完成仍 achieved」需要一份真正的达成报告）。
    run = _make_run(
        scope["scope_id"],
        goal["id"],
        [_assignment(course)],
        snapshot_sessions=[{"business_id": "C1", "is_active": True, "class_business_id": "B1"}],
    )
    with SessionLocal() as db:
        run_row = db.get(SolverRun, run.id)
        assert run_row is not None
        assert apply_goal_evaluation(db, run_row) is not None
        db.commit()
        goal_row = db.get(SolveGoal, goal["id"])
        assert goal_row is not None
        assert goal_row.acceptance_status == "completed"
        assert goal_row.acceptance_detail is None
        # 无竞争时条件 UPDATE 行数=1：结论照常生效，会话内对象经 refresh 与
        # 数据库一致（status/acceptance/latest_run_id 全部可见）。
        assert goal_row.status == "achieved"
        assert goal_row.latest_run_id == run.id


# ------------------------------------------------- MEM-F/F2：旧报告写回不覆盖新版本 pending


def test_stale_report_writeback_keeps_pending_and_never_achieves(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """MEM-F/F2 并发交错：验收按 v1 计算 → 另一会话修订清单至 v2 → 第一会话写回。

    报告仍保存并标注双版本（evaluated_checklist_version/current_checklist_version）；
    acceptance_status 保持 pending；goal.status 不得写 achieved。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    course = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    run = _make_run(scope["scope_id"], goal_id, [_assignment(course)])

    # 会话一：加载 goal（此刻清单还是 v1）。
    with SessionLocal() as db1:
        run1 = db1.get(SolverRun, run.id)
        goal1 = db1.get(SolveGoal, goal_id)
        assert run1 is not None and goal1 is not None
        assert _checklist_version(goal1) == 1

        # 会话二：交错修订清单至 v2 并提交（正常走 PATCH 端点）。
        patched = client.patch(
            f"/api/v1/goals/{goal_id}/checklist",
            headers=headers,
            json={
                "checklist": [
                    {
                        "key": "coverage",
                        "requirement": "覆盖 C1（v2 口径）",
                        "kind": "coverage",
                        "params": {"course_business_ids": ["C1"]},
                    }
                ]
            },
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["checklist_version"] == 2

        # 会话一：按会话内旧快照（v1）验收并写回。
        report = apply_goal_evaluation(db1, run1)
        db1.commit()

    assert report is not None
    meta = report["meta"]
    assert meta["evaluated_checklist_version"] == 1
    assert meta["current_checklist_version"] == 2
    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        # 旧口径结论不得覆盖新版本的 pending，更不得宣告达成。
        assert goal_row.acceptance_status == "pending"
        assert goal_row.status != "achieved"
        assert "清单已修订至 v2" in (goal_row.acceptance_detail or "")
        assert "本报告基于 v1" in (goal_row.acceptance_detail or "")
        assert "需重新验收" in (goal_row.acceptance_detail or "")
        # 报告保留（审计链），meta 双版本可解释。
        run_row = db.get(SolverRun, run.id)
        assert run_row is not None and run_row.goal_report is not None
        stored_meta = run_row.goal_report["meta"]
        assert stored_meta["checklist_version"] == 1
        assert stored_meta["evaluated_checklist_version"] == 1
        assert stored_meta["current_checklist_version"] == 2
        assert goal_row.latest_run_id == run.id

    # 报告保留 ≠ 结论生效：按当前 v2 重新验收后恢复主路径 completed。
    run2 = _make_run(scope["scope_id"], goal_id, [_assignment(course)])
    with SessionLocal() as db:
        apply_goal_evaluation(db, db.get(SolverRun, run2.id))
        db.commit()
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        assert goal_row.acceptance_status == "completed"
        assert goal_row.acceptance_detail is None


# ------------------------- 第五轮复审 F2 收口：数据库条件 UPDATE 挡住写回竞态窗口


def test_revision_between_reread_and_writeback_keeps_report_as_history(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """第五轮复审交错②：修订发生在**重读之后、提交之前**。

    会话一按 v1 完成评估并重读（仍是 v1），会话二此刻把清单修订至 v2，会话一
    再提交。重读挡不住这个窗口——只有写回的数据库条件 UPDATE（WHERE
    checklist_revision = 评估时版本）能挡：行数=0，报告留档为历史，目标当前
    状态一字不改（不写 achieved、不强行写 pending/detail、不动 latest_run_id）。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    course = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    run = _make_run(scope["scope_id"], goal_id, [_assignment(course)])

    with SessionLocal() as db1:
        run1 = db1.get(SolverRun, run.id)
        goal1 = db1.get(SolveGoal, goal_id)
        assert run1 is not None and goal1 is not None

        # 拦截写回路径上的事务内重读：重读一返回（此刻会话一看到的是 v1），
        # 立即让会话二经真实 PATCH 端点把清单修订至 v2——精确复现「重读之后、
        # 条件写回之前」的竞态窗口；只拦第一次，后续 refresh 走原路。
        original_refresh = db1.refresh
        revived = False

        def _refresh_then_revive(target: Any, *args: Any, **kwargs: Any) -> None:
            nonlocal revived
            original_refresh(target, *args, **kwargs)
            if not revived:
                revived = True
                db1.refresh = original_refresh  # type: ignore[method-assign]
                patched = client.patch(
                    f"/api/v1/goals/{goal_id}/checklist",
                    headers=headers,
                    json={
                        "checklist": [
                            {
                                "key": "coverage",
                                "requirement": "覆盖 C1（v2 口径）",
                                "kind": "coverage",
                                "params": {"course_business_ids": ["C1"]},
                            }
                        ]
                    },
                )
                assert patched.status_code == 200, patched.text
                assert patched.json()["checklist_version"] == 2

        db1.refresh = _refresh_then_revive  # type: ignore[method-assign]
        report = apply_goal_evaluation(db1, run1)
        db1.commit()

    assert report is not None
    # 条件 UPDATE 行数=0：meta 注记评估版本与写回时点版本，报告留档为历史。
    meta = report["meta"]
    assert meta["evaluated_checklist_version"] == 1
    assert meta["current_checklist_version"] == 2
    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        # 目标当前状态一字不改：旧 v1 结论不得覆盖修订后的「等待新验收」。
        assert goal_row.status == "open"
        assert goal_row.acceptance_status == "pending"
        assert goal_row.acceptance_detail is None
        assert goal_row.latest_run_id is None
        # 报告作为历史保存（审计链），双版本可解释。
        run_row = db.get(SolverRun, run.id)
        assert run_row is not None and run_row.goal_report is not None
        stored_meta = run_row.goal_report["meta"]
        assert stored_meta["checklist_version"] == 1
        assert stored_meta["evaluated_checklist_version"] == 1
        assert stored_meta["current_checklist_version"] == 2

    # 竞态输家不影响主路径：按 v2 重新验收照常闭环（求解快照带课次，
    # coverage 通过 → decision=achieved）。
    run2 = _make_run(
        scope["scope_id"],
        goal_id,
        [_assignment(course)],
        snapshot_sessions=[{"business_id": "C1", "is_active": True, "class_business_id": "B1"}],
    )
    with SessionLocal() as db:
        apply_goal_evaluation(db, db.get(SolverRun, run2.id))
        db.commit()
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        assert goal_row.status == "achieved"
        assert goal_row.acceptance_status == "completed"
        assert goal_row.latest_run_id == run2.id


def test_abandon_between_reread_and_writeback_is_terminal(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """第五轮复审交错③：验收写回窗口内目标被另一会话人工放弃（终态）。

    写回的条件 UPDATE 携带 status <> 'abandoned'：行数=0，算出的 achieved
    结论不得写回覆盖人工终态，报告只留档。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    course = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    run = _make_run(
        scope["scope_id"],
        goal_id,
        [_assignment(course)],
        snapshot_sessions=[{"business_id": "C1", "is_active": True, "class_business_id": "B1"}],
    )

    with SessionLocal() as db1:
        run1 = db1.get(SolverRun, run.id)
        goal1 = db1.get(SolveGoal, goal_id)
        assert run1 is not None and goal1 is not None

        # 拦截写回路径上的事务内重读：重读一返回，立即让会话二经真实
        # abandon 端点把目标置为 abandoned——复现「放弃插在重读之后、
        # 条件写回之前」的窗口；只拦第一次。
        original_refresh = db1.refresh
        abandoned = False

        def _refresh_then_abandon(target: Any, *args: Any, **kwargs: Any) -> None:
            nonlocal abandoned
            original_refresh(target, *args, **kwargs)
            if not abandoned:
                abandoned = True
                db1.refresh = original_refresh  # type: ignore[method-assign]
                response = client.post(f"/api/v1/goals/{goal_id}/abandon", headers=headers)
                assert response.status_code == 200, response.text

        db1.refresh = _refresh_then_abandon  # type: ignore[method-assign]
        report = apply_goal_evaluation(db1, run1)
        db1.commit()

    # 报告本身按 v1 算出达成，但写回被 status <> 'abandoned' 拦下（行数=0）：
    # 版本未变，meta 注记两版本同为 v1，结论未生效。
    assert report is not None
    assert report["decision"]["status"] == "achieved"
    assert report["meta"]["evaluated_checklist_version"] == 1
    assert report["meta"]["current_checklist_version"] == 1
    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        # 人工终态不被写回覆盖：状态保持 abandoned，验收状态与指针未被改动。
        assert goal_row.status == "abandoned"
        assert goal_row.acceptance_status == "pending"
        assert goal_row.latest_run_id is None
        # 报告仍作为该 run 的历史留档（审计链完整）。
        run_row = db.get(SolverRun, run.id)
        assert run_row is not None and run_row.goal_report is not None
        assert run_row.goal_report["decision"]["status"] == "achieved"


# ------------------- 第六轮复审收口：修订侧同构条件 UPDATE（单条落库 + 行数判定）


def _wrap_revision_before_write(
    monkeypatch: pytest.MonkeyPatch, hook: Callable[[], None]
) -> None:
    """在修订端点「读取之后、条件 UPDATE 之前」插入一次对方动作。

    修订写入抽在 services.apply_goal_checklist_revision（api 层唯一调用方），
    包装它在 app.api 里的绑定：第一次调用先执行 hook（另一会话的交错动作），
    再放行真实落库；同请求链上的后续调用（嵌套 PATCH）直接放行，只拦一次。
    """
    real_revision = goal_module.apply_goal_checklist_revision
    fired = {"done": False}

    def _hooked(db: Any, goal: Any, **kwargs: Any) -> bool:
        if not fired["done"]:
            fired["done"] = True
            hook()
        return real_revision(db, goal, **kwargs)

    monkeypatch.setattr(api_module, "apply_goal_checklist_revision", _hooked)


_V2_COVERAGE_BODY = {
    "checklist": [
        {
            "key": "coverage",
            "requirement": "覆盖 C1（v2 口径）",
            "kind": "coverage",
            "params": {"course_business_ids": ["C1"]},
        }
    ]
}


def test_acceptance_lands_between_read_and_revision_resets_conclusion(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """第六轮复审核心反例（先验收后修订）：修订请求读到 open/pending 后、
    落库前，另一会话按 v1 完成验收（条件 UPDATE 命中，写入 achieved/completed）。

    复位依据必须是数据库落库当时的行状态：单条条件 UPDATE 的 SET 无条件把
    acceptance_status 写回 pending、CASE 把 achieved 回退 open——v2 清单不得
    挂着 v1 的成功结论；v1 报告留档（latest_run_id/latest_report_meta）。
    快照驱动的复位 if 在此窗口会漏（快照仍是 open/pending）；会话内 ORM
    普通赋值会因「与快照同值、无净变化」静默丢复位（复审点名的陷阱）——
    只有条件 UPDATE 的 SET 子句能挡住。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    assert created.json()["status"] == "open"
    assert created.json()["acceptance_status"] == "pending"
    course = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    run = _make_run(
        scope["scope_id"],
        goal_id,
        [_assignment(course)],
        snapshot_sessions=[{"business_id": "C1", "is_active": True, "class_business_id": "B1"}],
    )

    def _accept_on_other_session() -> None:
        # 交错窗口：另一会话按 v1 完成验收——版本未被修订、结论 UPDATE 命中。
        with SessionLocal() as db2:
            run2 = db2.get(SolverRun, run.id)
            assert run2 is not None
            assert apply_goal_evaluation(db2, run2) is not None
            db2.commit()
        with SessionLocal() as db2:
            raced = db2.get(SolveGoal, goal_id)
            assert raced is not None
            assert raced.status == "achieved"
            assert raced.acceptance_status == "completed"

    _wrap_revision_before_write(monkeypatch, _accept_on_other_session)
    patched = client.patch(
        f"/api/v1/goals/{goal_id}/checklist", headers=headers, json=_V2_COVERAGE_BODY
    )
    monkeypatch.undo()

    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["checklist_version"] == 2
    # 复位来自数据库当时状态：v2 清单挂着 open/pending，不是 v1 的成功结论。
    assert body["status"] == "open"
    assert body["acceptance_status"] == "pending"
    assert body["acceptance_detail"] is None
    # v1 报告留档（审计链），响应标注「历史版本结论」。
    assert body["latest_run_id"] == run.id
    meta = body["latest_report_meta"]
    assert meta is not None
    assert meta["run_id"] == run.id
    assert meta["checklist_version"] == 1
    assert meta["is_current_version"] is False
    assert "历史版本 v1" in meta["note"]

    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        assert goal_row.checklist_revision == 2
        assert goal_row.status == "open"
        assert goal_row.acceptance_status == "pending"
        assert goal_row.latest_run_id == run.id
        assert goal_row.checklist_history
        assert goal_row.checklist_history[0]["version"] == 1
        assert goal_row.checklist_history[0]["items"]
        run_row = db.get(SolverRun, run.id)
        assert run_row is not None and run_row.goal_report is not None
        assert run_row.goal_report["meta"]["checklist_version"] == 1


def test_abandon_between_read_and_revision_returns_conflict(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """第六轮复审交错③（修订与放弃）：放弃发生在修订请求读取之后、落库之前。

    条件 UPDATE 的 WHERE status <> 'abandoned' 行数=0：返回 409 冲突，目标
    保持人工终态 abandoned，清单/版本号/历史一字不改，不留下修订审计记录。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    original_keys = [item["key"] for item in created.json()["checklist"]]

    def _abandon_on_other_session() -> None:
        response = client.post(f"/api/v1/goals/{goal_id}/abandon", headers=headers)
        assert response.status_code == 200, response.text

    _wrap_revision_before_write(monkeypatch, _abandon_on_other_session)
    patched = client.patch(
        f"/api/v1/goals/{goal_id}/checklist", headers=headers, json=_V2_COVERAGE_BODY
    )
    monkeypatch.undo()

    assert patched.status_code == 409, patched.text
    assert patched.json()["detail"] == "目标已放弃，清单不再接受修订"
    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        assert goal_row.status == "abandoned"
        assert goal_row.checklist_revision == 1
        assert goal_row.acceptance_status == "pending"
        assert [item["key"] for item in goal_row.checklist] == original_keys
        assert goal_row.checklist_history == []
        # 冲突路径不留修订审计（abandon 自己的审计不在本断言范围）。
        revisions = db.scalars(
            select(AuditLog).where(
                AuditLog.action == "update_checklist", AuditLog.resource_id == goal_id
            )
        ).all()
        assert revisions == []


def test_concurrent_revisions_late_write_gets_conflict(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """第六轮复审交错④（版本冲突）：两次修订竞速——后者以读取时 v1 为条件
    落库，前者已把版本推到 v2：WHERE 行数=0 → 409 冲突。

    后者不得覆盖前者的清单、不得再自增版本、不留审计；冲突响应按重读后的
    当前版本提示刷新。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]

    def _revise_on_other_session() -> None:
        # 先完成的修订（内层 PATCH）完整落库 v2；本包装在嵌套请求里直接放行。
        inner = client.patch(
            f"/api/v1/goals/{goal_id}/checklist",
            headers=headers,
            json={
                "checklist": [
                    {
                        "key": "revision-first",
                        "requirement": "先到者",
                        "kind": "draft_only",
                        "params": {},
                    }
                ]
            },
        )
        assert inner.status_code == 200, inner.text
        assert inner.json()["checklist_version"] == 2

    _wrap_revision_before_write(monkeypatch, _revise_on_other_session)
    outer = client.patch(
        f"/api/v1/goals/{goal_id}/checklist",
        headers=headers,
        json={
            "checklist": [
                {
                    "key": "revision-second",
                    "requirement": "后到者",
                    "kind": "draft_only",
                    "params": {},
                }
            ]
        },
    )
    monkeypatch.undo()

    assert outer.status_code == 409, outer.text
    detail = outer.json()["detail"]
    assert "并发修订" in detail
    assert "v2" in detail
    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        assert goal_row.checklist_revision == 2
        keys = [item["key"] for item in goal_row.checklist]
        assert "revision-first" in keys
        assert "revision-second" not in keys
        assert [entry["version"] for entry in goal_row.checklist_history] == [1]
        revisions = db.scalars(
            select(AuditLog).where(
                AuditLog.action == "update_checklist", AuditLog.resource_id == goal_id
            )
        ).all()
        assert len(revisions) == 1


def test_no_race_revision_keeps_open_and_pending(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """第六轮复审交错⑤（无竞争基线）：open/pending 的 v1 → v2 仍 open/pending。

    快照与库中同值时，复位列依旧出现在条件 UPDATE 的 SET 子句里（无条件
    写回），行为不回归：版本递增、历史快照、清单替换、无 detail 覆写。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    assert created.json()["checklist_version"] == 1

    patched = client.patch(
        f"/api/v1/goals/{goal_id}/checklist", headers=headers, json=_V2_COVERAGE_BODY
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["checklist_version"] == 2
    assert body["status"] == "open"
    assert body["acceptance_status"] == "pending"
    assert body["acceptance_detail"] is None
    assert body["latest_run_id"] is None
    assert body["latest_report_meta"] is None

    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        assert goal_row.checklist_revision == 2
        assert goal_row.status == "open"
        assert goal_row.acceptance_status == "pending"
        assert goal_row.acceptance_detail is None
        keys = [item["key"] for item in goal_row.checklist]
        assert "coverage" in keys
        assert [entry["version"] for entry in goal_row.checklist_history] == [1]


# ------------------------------------------------- MEM-F/F3：范围修订三态 + 契约统一


def test_merge_coverage_scope_three_state_semantics() -> None:
    """MEM-F/F3：scope 合并三态——未提交保留旧值 / 显式空清除 / 显式非空替换。"""
    old = {"course_business_ids": ["C1"], "class_business_ids": ["B2"]}

    def _assert_kept(merged: dict[str, Any]) -> None:
        assert merged["course_business_ids"] == ["C1"]
        assert merged["class_business_ids"] == ["B2"]

    # 未提交（scope 为 None / 空模型）→ 旧值原样保留。
    _assert_kept(merge_coverage_scope(old, None))
    _assert_kept(merge_coverage_scope(old, GoalScopePatch()))
    # 显式空列表 → 清除该维度限制；未提交的班级维度保留。
    cleared = merge_coverage_scope(old, GoalScopePatch(course_business_ids=[]))
    assert cleared["course_business_ids"] == []
    assert cleared["class_business_ids"] == ["B2"]
    # 显式非空 → 替换新值。
    replaced = merge_coverage_scope(old, GoalScopePatch(course_business_ids=["C9"]))
    assert replaced["course_business_ids"] == ["C9"]
    assert replaced["class_business_ids"] == ["B2"]
    # 显式 null 日期 → 清除该端点；未提交的另一端保留。
    dated = {**old, "date_from": "2026-10-01", "date_to": "2026-10-31"}
    cleared_date = merge_coverage_scope(dated, GoalScopePatch(date_from=None))
    assert cleared_date["date_from"] is None
    assert cleared_date["date_to"] == "2026-10-31"
    replaced_date = merge_coverage_scope(dated, GoalScopePatch(date_from="2026-10-05"))
    assert replaced_date["date_from"] == "2026-10-05"
    assert replaced_date["date_to"] == "2026-10-31"
    # dict 调用方保守口径：只有非空值视为提交（normalize_goal_scope 产物里的
    # 空维度不会被误判成「显式清除」）。
    _assert_kept(merge_coverage_scope(old, normalize_goal_scope()))
    dict_scope = normalize_goal_scope(course_business_ids=["C9"])
    conservative = merge_coverage_scope(old, dict_scope)
    assert conservative["course_business_ids"] == ["C9"]
    assert conservative["class_business_ids"] == ["B2"]


def test_bottom_line_scope_overrides_full_checklist_coverage() -> None:
    """MEM-F/F3 契约=scope 胜：完整清单带旧 coverage（B1 班）+ 显式 scope B2 →
    生效 B2；未提交字段不改写；显式空列表在完整清单上同样清除。"""
    checklist = [
        {
            "key": "coverage",
            "requirement": "覆盖 B1 班课次",
            "kind": "coverage",
            "params": {"class_business_ids": ["B1"]},
        }
    ]

    def _coverage(items: list[dict[str, Any]]) -> dict[str, Any]:
        return next(item for item in items if item["kind"] == "coverage")

    # 未提交 → 用户清单的 coverage 参数原样保留。
    kept = ensure_bottom_line_items(deepcopy(checklist))
    assert _coverage(kept)["params"]["class_business_ids"] == ["B1"]
    # 显式非空 scope → 覆盖完整清单里 coverage 项的旧参数（scope 胜）。
    overridden = ensure_bottom_line_items(
        deepcopy(checklist), scope=GoalScopePatch(class_business_ids=["B2"])
    )
    assert _coverage(overridden)["params"]["class_business_ids"] == ["B2"]
    # 显式空列表 → 清除该维度限制。
    cleared = ensure_bottom_line_items(
        deepcopy(checklist), scope=GoalScopePatch(class_business_ids=[])
    )
    assert "class_business_ids" not in _coverage(cleared)["params"]


def test_patch_checklist_scope_three_state_over_coverage_params(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """MEM-F/F3 API 契约：PATCH /goals/{id}/checklist 的 scope 三态——显式 scope
    优先于清单 coverage 既有参数。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={
            "instruction": "重排 B2 班 10 月的课",
            "class_business_ids": ["B2"],
            "course_business_ids": ["C1"],
        },
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    coverage_item = {
        "key": "coverage",
        "requirement": "覆盖目标课次",
        "kind": "coverage",
        "params": {"class_business_ids": ["B2"], "course_business_ids": ["C1"]},
    }

    def _coverage_of(body: dict[str, Any]) -> dict[str, Any]:
        return next(item for item in body["checklist"] if item["kind"] == "coverage")

    # ① 未提交（不带 scope）→ 完整清单的 coverage 参数原样保留。
    patched = client.patch(
        f"/api/v1/goals/{goal_id}/checklist", headers=headers, json={"checklist": [coverage_item]}
    )
    assert patched.status_code == 200, patched.text
    params = _coverage_of(patched.json())["params"]
    assert params["course_business_ids"] == ["C1"]
    assert params["class_business_ids"] == ["B2"]

    # ② 显式空列表 → 旧的 C1 课次限制消失、B2 班保留。
    patched = client.patch(
        f"/api/v1/goals/{goal_id}/checklist",
        headers=headers,
        json={"checklist": [coverage_item], "scope": {"course_business_ids": []}},
    )
    assert patched.status_code == 200, patched.text
    params = _coverage_of(patched.json())["params"]
    assert "course_business_ids" not in params
    assert params["class_business_ids"] == ["B2"]

    # ③ 完整清单带旧 B1 coverage + 显式 scope B2/C9 → 生效 B2/C9（scope 胜）。
    stale_item = {
        **coverage_item,
        "params": {"class_business_ids": ["B1"], "course_business_ids": ["C1"]},
    }
    patched = client.patch(
        f"/api/v1/goals/{goal_id}/checklist",
        headers=headers,
        json={
            "checklist": [stale_item],
            "scope": {"class_business_ids": ["B2"], "course_business_ids": ["C9"]},
        },
    )
    assert patched.status_code == 200, patched.text
    params = _coverage_of(patched.json())["params"]
    assert params["class_business_ids"] == ["B2"]
    assert params["course_business_ids"] == ["C9"]


# ------------------------------------------------- 回归场景⑥：修订后继续执行仍属原目标


def test_continued_runs_after_scope_revision_stay_bound_to_goal(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """第四轮复审回归场景⑥：修改范围或预算后继续执行，后续任务仍属原目标
    （goal_id 绑定断言 + 验收按最新清单版本闭环）。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))

    # 第一次求解：任务与目标绑定，报告归属同一目标。
    first = client.post(
        "/api/v1/solver-runs",
        headers=headers,
        json={"goal_id": goal_id, "wait": True, "time_limit_seconds": 5},
    )
    assert first.status_code == 202, first.text
    first_body = first.json()
    assert first_body["goal_id"] == goal_id
    assert first_body["goal_report"]["goal_id"] == goal_id

    # 修订范围（coverage 改按课次 C1 核对）后继续执行：新任务仍绑定同一目标，
    # 且预算参数照常生效——goal_id 不因补救动作被清空。
    patched = client.patch(
        f"/api/v1/goals/{goal_id}/checklist",
        headers=headers,
        json={
            "checklist": [
                {
                    "key": "coverage",
                    "requirement": "覆盖 C1",
                    "kind": "coverage",
                    "params": {"course_business_ids": ["C1"]},
                }
            ],
            "scope": {"course_business_ids": ["C1"]},
        },
    )
    assert patched.status_code == 200, patched.text
    second = client.post(
        "/api/v1/solver-runs",
        headers=headers,
        json={
            "goal_id": goal_id,
            "course_business_ids": ["C1"],
            "wait": True,
            "time_limit_seconds": 5,
        },
    )
    assert second.status_code == 202, second.text
    second_body = second.json()
    assert second_body["goal_id"] == goal_id
    assert second_body["goal_report"]["goal_id"] == goal_id
    # 报告按修订后的 v2 清单验收：目标连续性贯穿「修订 → 继续执行 → 重新验收」。
    assert second_body["goal_report"]["meta"]["checklist_version"] == 2
    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        assert goal_row.latest_run_id == second_body["id"]
        assert goal_row.acceptance_status == "completed"


# ------- 第七轮复审收口：异常/失败回调统一走版本约束写回（不再 ORM 直赋值）


def _make_old_run_with_frozen_version(
    scope_id: str,
    goal_id: str,
    course: CourseSession,
    *,
    status: str = "completed",
) -> SolverRun:
    """直接落一条旧任务：request_payload 冻结 goal_checklist_version=1。"""
    with SessionLocal() as db:
        snapshot = DataSnapshot(
            schedule_set_id=scope_id,
            revision=1,
            checksum=f"v7-old-{uuid4().hex[:8]}",
            payload={
                "course_sessions": [
                    {"business_id": "C1", "is_active": True, "class_business_id": "B1"}
                ]
            },
        )
        db.add(snapshot)
        db.flush()
        run = SolverRun(
            schedule_set_id=scope_id,
            snapshot_id=snapshot.id,
            run_type="initial",
            status=status,
            model_status="OPTIMAL" if status == "completed" else None,
            request_payload={"goal_checklist_version": 1},
            result_payload={
                "assignments": [_assignment(course)],
                "solved_course_business_ids": ["C1"],
            },
            goal_id=goal_id,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run


def test_acceptance_exception_writeback_blocked_by_newer_version_and_acceptance(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """第七轮复审交错（验收异常路径）：旧任务按 v1 验收 → 回调写回前另一会话
    修订清单至 v2 并完成新版验收（通过）→ 旧任务验收抛异常。

    异常回调的目标写回必须带版本守卫（WHERE checklist_revision = 评估时点
    版本 AND status <> 'abandoned'）：行数=0 时新结论一字不改，旧失败只留在
    旧任务自己的报告上（记录对应 run 与清单版本）。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    course = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    old_run = _make_old_run_with_frozen_version(scope["scope_id"], goal_id, course)

    # 新任务：稍后按 v2 完成验收（在异常回调内部交错执行）。
    new_run = _make_run(
        scope["scope_id"],
        goal_id,
        [_assignment(course)],
        snapshot_sessions=[{"business_id": "C1", "is_active": True, "class_business_id": "B1"}],
    )
    real_apply = goal_module.apply_goal_evaluation

    def _revise_accept_then_boom(*args: Any, **kwargs: Any) -> None:
        # 另一会话：修订清单至 v2 并完成新版验收——都发生在旧回调放行之前。
        patched = client.patch(
            f"/api/v1/goals/{goal_id}/checklist", headers=headers, json=_V2_COVERAGE_BODY
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["checklist_version"] == 2
        with SessionLocal() as db2:
            assert real_apply(db2, db2.get(SolverRun, new_run.id)) is not None
            db2.commit()
        raise RuntimeError("旧任务验收器炸了")

    monkeypatch.setattr(goal_module, "apply_goal_evaluation", _revise_accept_then_boom)
    _evaluate_goal_for_run(old_run.id, goal_id)
    monkeypatch.undo()

    # v2 的新结论不被旧回调改写。
    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        assert goal_row.checklist_revision == 2
        assert goal_row.status == "achieved"
        assert goal_row.acceptance_status == "completed"
        assert goal_row.acceptance_detail is None
        assert goal_row.latest_run_id == new_run.id
        # 旧失败留在旧任务自己的报告上，并记录 run 与清单版本、写回被拦截。
        old_row = db.get(SolverRun, old_run.id)
        assert old_row is not None and old_row.goal_report is not None
        report = old_row.goal_report
        assert report["acceptance_status"] == "failed"
        assert "旧任务验收器炸了" in str(report["acceptance_error"])
        assert report["run_id"] == old_run.id
        assert report["checklist_version"] == 1
        assert report["goal_writeback"] == "skipped"

    detail = client.get(f"/api/v1/goals/{goal_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["acceptance_status"] == "completed"


def test_run_failure_writeback_blocked_by_newer_version_and_acceptance(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """第七轮复审交错（求解失败路径）：run 失败时目标还停在 v1，写回前清单已
    修订至 v2 且新版验收完成——失败标记同样不得改写新结论。"""
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    created = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    course = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    old_run = _make_old_run_with_frozen_version(scope["scope_id"], goal_id, course, status="queued")

    # 交错：修订 v2 + 新任务完成 v2 验收（achieved/completed）。
    new_run = _make_run(
        scope["scope_id"],
        goal_id,
        [_assignment(course)],
        snapshot_sessions=[{"business_id": "C1", "is_active": True, "class_business_id": "B1"}],
    )
    patched = client.patch(
        f"/api/v1/goals/{goal_id}/checklist", headers=headers, json=_V2_COVERAGE_BODY
    )
    assert patched.status_code == 200, patched.text
    with SessionLocal() as db:
        assert apply_goal_evaluation(db, db.get(SolverRun, new_run.id)) is not None
        db.commit()

    # 求解失败的写回发生在修订与新版验收之后。
    _persist_failure(old_run.id, "求解器崩溃")

    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        assert goal_row.checklist_revision == 2
        assert goal_row.status == "achieved"
        assert goal_row.acceptance_status == "completed"
        assert goal_row.acceptance_detail is None
        old_row = db.get(SolverRun, old_run.id)
        assert old_row is not None and old_row.goal_report is not None
        report = old_row.goal_report
        assert report["acceptance_status"] == "failed"
        assert "求解失败" in str(report["acceptance_error"])
        assert report["checklist_version"] == 1
        assert report["goal_writeback"] == "skipped"


def test_acceptance_exception_writeback_skips_abandoned_goal(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收异常分支同样不得越权改写人工终态（abandoned 守卫）。"""
    scope = _make_scope(client, auth_headers)
    created = client.post(
        "/api/v1/goals",
        headers=scope["headers"],
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    course = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    run = _make_old_run_with_frozen_version(scope["scope_id"], goal_id, course)
    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        goal_row.status = "abandoned"
        db.commit()

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("验收器炸了")

    monkeypatch.setattr(goal_module, "apply_goal_evaluation", _boom)
    _evaluate_goal_for_run(run.id, goal_id)
    monkeypatch.undo()

    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        assert goal_row.status == "abandoned"
        assert goal_row.acceptance_status == "pending"
        assert goal_row.acceptance_detail is None
        run_row = db.get(SolverRun, run.id)
        assert run_row is not None and run_row.goal_report is not None
        assert run_row.goal_report["acceptance_status"] == "failed"
        assert run_row.goal_report["goal_writeback"] == "skipped"


def test_acceptance_exception_writeback_lands_on_matching_version(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """无竞争时异常写回照常生效（版本未前移、未放弃）：failed 可见可停轮询，
    且报告记录本次写回的清单版本。"""
    scope = _make_scope(client, auth_headers)
    created = client.post(
        "/api/v1/goals",
        headers=scope["headers"],
        json={"instruction": "排好 B1 班的课", "class_business_ids": ["B1"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    course = _add_course(scope["scope_id"], "C1", lesson_date=date(2026, 10, 5))
    run = _make_old_run_with_frozen_version(scope["scope_id"], goal_id, course)

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("验收器炸了")

    monkeypatch.setattr(goal_module, "apply_goal_evaluation", _boom)
    _evaluate_goal_for_run(run.id, goal_id)
    monkeypatch.undo()

    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal_id)
        assert goal_row is not None
        assert goal_row.acceptance_status == "failed"
        assert goal_row.acceptance_detail and "验收器炸了" in goal_row.acceptance_detail
        run_row = db.get(SolverRun, run.id)
        assert run_row is not None and run_row.goal_report is not None
        assert run_row.goal_report["checklist_version"] == 1
        assert run_row.goal_report["goal_writeback"] == "applied"
