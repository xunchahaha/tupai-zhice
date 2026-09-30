"""第九轮审查（48e928b）回归：任务要求修订、按原参数重跑、工作草稿保护、解析幂等、
动作级授权绑定、按主体检索记忆。

评审方的隔离探针（contract_probes.py）在这里迁成项目回归——不是口头确认：

1. R1 任务要求修订：软压硬、同 id 软项换时段新旧并存、续办新增硬要求刷新重跑后丢失；
2. R2 加预算重跑：300 秒不会降成 90 秒、规则开关/权重/范围/数据/记忆不变；
3. R3 工作草稿指针：旧求解晚结束不得夺回基准；
4. R4 解析幂等：流式失败回退同步后「记住」只执行一次；
5. R5 授权绑定：整句里别处的「记住」不能替另一条动作授权；
6. R6 记忆检索：旧偏好被大量新记忆挤出最近 N 条后仍能用自然语言纠正。
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.api as api_module
import app.services.tasks as tasks_module
from app.api import settings
from app.db import SessionLocal
from app.models import (
    AssistantInterpretReceipt,
    Campus,
    DataSnapshot,
    PreferenceEntry,
    ScheduleVersion,
    SolveGoal,
    SolverRun,
    Teacher,
    TimeSlot,
    User,
)
from app.schemas import AssistantInterpretRequest
from app.services.goal import plan_task_constraint_revision
from app.services.task_context import authorization_clause, scoped_word_hits
from app.timezone import shanghai_now

# ---------------------------------------------------------------- 工具


def _make_goal(
    scope_id: str = "default",
    *,
    instruction: str = "重排 B01 班的课",
    checklist: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    **fields: Any,
) -> SolveGoal:
    with SessionLocal() as db:
        goal = SolveGoal(
            schedule_set_id=scope_id,
            instruction=instruction,
            checklist=checklist or [],
            context=context,
            **fields,
        )
        db.add(goal)
        db.commit()
        db.refresh(goal)
        return goal


def _hard_item(key: str, subject_id: str, slots: list[str]) -> dict[str, Any]:
    return {
        "key": key,
        "requirement": f"教师 {subject_id} 不占用指定时段（{'、'.join(slots)}）——独立复核",
        "kind": "forbidden_slot_free",
        "params": {
            "subject_type": "teacher",
            "subject_ids": [subject_id],
            "slot_business_ids": slots,
        },
    }


def _soft(constraint_id: str, subject_id: str, slots: list[str], text: str = "") -> dict[str, Any]:
    return {
        "id": constraint_id,
        "subject_type": "teacher",
        "subject_ids": [subject_id],
        "slot_business_ids": slots,
        "source_text": text or f"{subject_id} 尽量别排 {'、'.join(slots)}",
    }


def _constraint(
    constraint_id: str, subject_id: str, slots: list[str], hardness: str, text: str = ""
) -> dict[str, Any]:
    return {
        "id": constraint_id,
        "source_text": text or f"{subject_id} {hardness} {'、'.join(slots)}",
        "subject_type": "teacher",
        "subject_ids": [subject_id],
        "slot_business_ids": slots,
        "hardness": hardness,
    }


def _stored_run(run_id: str) -> SolverRun:
    with SessionLocal() as db:
        run = db.get(SolverRun, run_id)
        assert run is not None
        db.expunge(run)
        return run


def _task_rules(run_id: str) -> list[dict[str, Any]]:
    return list(_stored_run(run_id).request_payload.get("task_constraint_rules") or [])


@pytest.fixture
def no_solve(monkeypatch: pytest.MonkeyPatch) -> None:
    """只验证创建任务时冻结了什么：后台求解整体 stub 掉。"""
    monkeypatch.setattr(api_module, "enqueue_solver_run", lambda run_id: None)
    monkeypatch.setattr(api_module, "execute_solver_run", lambda run_id: {})


@pytest.fixture
def isolated_scope(client: TestClient, auth_headers: dict[str, str]) -> dict[str, Any]:
    """独立课表方案：两位教师（名称带可识别称呼）+ 两个时段，互不污染默认方案。"""
    created = client.post(
        "/api/v1/schedule-sets",
        headers=auth_headers,
        json={"name": f"修订回归-{uuid4().hex[:8]}"},
    )
    assert created.status_code == 201, created.text
    scope_id = created.json()["id"]
    with SessionLocal() as db:
        campus = Campus(schedule_set_id=scope_id, business_id="C", name="回归校区")
        db.add(campus)
        db.flush()
        db.add_all(
            [
                Teacher(
                    schedule_set_id=scope_id, campus_id=campus.id, business_id="T91", name="赵一"
                ),
                Teacher(
                    schedule_set_id=scope_id, campus_id=campus.id, business_id="T92", name="钱二"
                ),
                Teacher(
                    schedule_set_id=scope_id, campus_id=campus.id, business_id="T93", name="孙三"
                ),
            ]
        )
        for business_id, weekday in (("S1", "周三"), ("S2", "周四")):
            db.add(
                TimeSlot(
                    schedule_set_id=scope_id,
                    campus_id=campus.id,
                    business_id=business_id,
                    weekday=weekday,
                    start_time="19:00",
                    end_time="21:00",
                )
            )
        db.commit()
    return {"scope_id": scope_id, "headers": {**auth_headers, "X-Schedule-Set-Id": scope_id}}


# ------------------------------------------------- R1 任务要求修订（纯函数）


def test_plan_revision_hard_request_tightens_persisted_soft() -> None:
    """探针 existing_soft_masks_new_hard：同内容的旧软要求被新硬要求收紧，不并存。"""
    constraints = [
        api_module.AssistantTaskConstraint(**_constraint("tc-1", "T01", ["S05"], "hard"))
    ]
    revision = plan_task_constraint_revision([], [_soft("tc-1", "T01", ["S05"])], constraints)
    assert revision.checklist_changed and revision.soft_changed
    assert revision.tightened == ["T01 hard S05"]
    assert revision.soft_constraints == []
    assert [item["params"]["subject_ids"] for item in revision.checklist] == [["T01"]]
    assert revision.checklist[0]["params"]["task_constraint_id"] == "tc-1"


def test_plan_revision_never_weakens_a_persisted_hard() -> None:
    """请求里的「尽量」撞上已落实的硬要求：保留硬要求并留痕，放宽只能由人在清单里保存。"""
    constraints = [
        api_module.AssistantTaskConstraint(**_constraint("tc-1", "T01", ["S05"], "soft"))
    ]
    revision = plan_task_constraint_revision(
        [_hard_item("forbidden_slot_free-1", "T01", ["S05"])], [], constraints
    )
    assert not revision.changed
    assert revision.kept_hard == ["T01 soft S05"]
    assert revision.summary() == {"kept_hard": ["T01 soft S05"]}


def test_plan_revision_same_id_same_subject_replaces_soft_slots() -> None:
    """探针 same_id_soft_update：同 id、同主体、时段不同 = 对那一条的修改，不是新旧并存。"""
    constraints = [
        api_module.AssistantTaskConstraint(**_constraint("tc-1", "T01", ["S06"], "soft"))
    ]
    revision = plan_task_constraint_revision([], [_soft("tc-1", "T01", ["S05"])], constraints)
    assert revision.replaced_soft == ["T01 soft S06"]
    assert [item["slot_business_ids"] for item in revision.soft_constraints] == [["S06"]]


def test_plan_revision_positional_id_collision_keeps_both_softs() -> None:
    """解析侧 id 按序号生成，跨轮次的 tc-1 可能是完全不同的要求：主体不同时两条都留，id 不覆盖。"""
    constraints = [
        api_module.AssistantTaskConstraint(**_constraint("tc-1", "T02", ["S05"], "soft"))
    ]
    revision = plan_task_constraint_revision([], [_soft("tc-1", "T01", ["S05"])], constraints)
    assert revision.added_soft and not revision.replaced_soft
    assert sorted(item["subject_ids"][0] for item in revision.soft_constraints) == ["T01", "T02"]
    assert len({item["id"] for item in revision.soft_constraints}) == 2


def test_plan_revision_is_idempotent_for_identical_requirements() -> None:
    constraints = [
        api_module.AssistantTaskConstraint(**_constraint("tc-1", "T01", ["S05"], "hard")),
        api_module.AssistantTaskConstraint(**_constraint("tc-2", "T02", ["S06"], "soft")),
    ]
    first = plan_task_constraint_revision([], [], constraints)
    second = plan_task_constraint_revision(first.checklist, first.soft_constraints, constraints)
    assert first.changed and not second.changed
    assert second.summary() is None


def test_compile_hard_beats_soft_regardless_of_source_order() -> None:
    """编译契约：同内容下硬要求压过软要求，软项让位（不论来自任务还是请求、先后如何）。"""
    goal = _make_goal(
        context={"schema_version": 1, "soft_task_constraints": [_soft("tc-1", "T01", ["S05"])]}
    )
    rules = api_module._compile_task_constraints(
        goal=goal,
        request_constraints=[
            api_module.AssistantTaskConstraint(**_constraint("tc-9", "T01", ["S05"], "hard"))
        ],
    )
    assert [rule["hardness"] for rule in rules] == ["hard"]
    # 请求里软项排在同内容硬项之前，也不能先占位。
    rules = api_module._compile_task_constraints(
        request_constraints=[
            api_module.AssistantTaskConstraint(**_constraint("a", "T01", ["S05"], "soft")),
            api_module.AssistantTaskConstraint(**_constraint("b", "T01", ["S05"], "hard")),
        ],
    )
    assert [rule["hardness"] for rule in rules] == ["hard"]


# ------------------------------------------------- R1 任务要求修订（端到端）


def test_soft_to_hard_revision_survives_reload_and_invalidates_old_verdict(
    client: TestClient, auth_headers: dict[str, str], no_solve: None
) -> None:
    """第一轮「尽量别排」→ 第二轮确认为「绝对不能」：当次求解按硬执行、任务要求升版本、
    旧验收结论失效；刷新后不带请求约束的重跑仍然是硬要求。"""
    goal = _make_goal(
        context={
            "schema_version": 1,
            "soft_task_constraints": [_soft("tc-1", "T01", ["S05"], "T01 周三晚尽量别排")],
        },
        status="achieved",
        acceptance_status="completed",
    )
    response = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "重排 B01，T01 周三晚绝对不能上",
            "class_business_ids": ["B01"],
            "goal_id": goal.id,
            "task_constraints": [
                _constraint("tc-1", "T01", ["S05"], "hard", "T01 周三晚绝对不能上")
            ],
        },
    )
    assert response.status_code == 202, response.text
    rules = _task_rules(response.json()["id"])
    assert [(rule["hardness"], rule["actor_ids"]) for rule in rules] == [("hard", ["T01"])]
    assert response.json()["task_revision"] == {"tightened": ["T01 周三晚绝对不能上"]}
    with SessionLocal() as db:
        row = db.get(SolveGoal, goal.id)
        assert row is not None
        assert row.checklist_revision == 2
        assert row.status == "open" and row.acceptance_status == "pending"
        assert (row.context or {}).get("soft_task_constraints") == []
        assert [item["params"]["subject_ids"] for item in row.checklist] == [["T01"]]
        assert row.checklist_history[-1]["version"] == 1
    assert _stored_run(response.json()["id"]).request_payload["goal_checklist_version"] == 2

    # 「刷新后」：不带请求约束的重跑只读任务保存的新版要求。
    retry = client.post(
        "/api/v1/solver-runs",
        headers=auth_headers,
        json={"goal_id": goal.id, "class_business_ids": ["B01"]},
    )
    assert retry.status_code == 202, retry.text
    assert [(rule["hardness"], rule["actor_ids"]) for rule in _task_rules(retry.json()["id"])] == [
        ("hard", ["T01"])
    ]


def test_new_hard_requirement_on_continuation_is_persisted_not_one_shot(
    client: TestClient, auth_headers: dict[str, str], no_solve: None
) -> None:
    """探针 hard_request_not_in_persisted_checklist_is_absent_on_retry：续办新增的硬要求
    不能只在当次请求里生效——写进任务清单后，之后的重跑与验收都面对同一份。"""
    goal = _make_goal(checklist=[_hard_item("forbidden_slot_free-1", "T01", ["S05"])])
    first = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "重排 B01，T02 周五不能上",
            "class_business_ids": ["B01"],
            "goal_id": goal.id,
            "task_constraints": [
                _constraint("tc-1", "T01", ["S05"], "hard"),
                _constraint("tc-2", "T02", ["S07"], "hard", "T02 周五不能上"),
            ],
        },
    )
    assert first.status_code == 202, first.text
    assert first.json()["task_revision"] == {"added_hard": ["T02 周五不能上"]}
    assert len(_task_rules(first.json()["id"])) == 2
    retry = client.post(
        "/api/v1/solver-runs",
        headers=auth_headers,
        json={"goal_id": goal.id, "class_business_ids": ["B01"]},
    )
    assert retry.status_code == 202, retry.text
    assert sorted(rule["actor_ids"][0] for rule in _task_rules(retry.json()["id"])) == [
        "T01",
        "T02",
    ]
    # 同一份要求再确认一次是幂等的：不再升版本。
    again = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "重排 B01，T02 周五不能上",
            "class_business_ids": ["B01"],
            "goal_id": goal.id,
            "task_constraints": [_constraint("tc-2", "T02", ["S07"], "hard")],
        },
    )
    assert again.status_code == 202, again.text
    assert again.json()["task_revision"] is None
    with SessionLocal() as db:
        row = db.get(SolveGoal, goal.id)
        assert row is not None
        assert row.checklist_revision == 2


def test_request_soft_cannot_weaken_persisted_hard(
    client: TestClient, auth_headers: dict[str, str], no_solve: None
) -> None:
    goal = _make_goal(checklist=[_hard_item("forbidden_slot_free-1", "T01", ["S05"])])
    response = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "重排 B01",
            "class_business_ids": ["B01"],
            "goal_id": goal.id,
            "task_constraints": [_constraint("tc-1", "T01", ["S05"], "soft", "T01 周三晚尽量别排")],
        },
    )
    assert response.status_code == 202, response.text
    assert [rule["hardness"] for rule in _task_rules(response.json()["id"])] == ["hard"]
    assert response.json()["task_revision"] == {"kept_hard": ["T01 周三晚尽量别排"]}
    with SessionLocal() as db:
        row = db.get(SolveGoal, goal.id)
        assert row is not None
        assert row.checklist_revision == 1
        assert (row.context or {}).get("soft_task_constraints") == []


def test_soft_edit_replaces_old_slots_in_run_and_context(
    client: TestClient, auth_headers: dict[str, str], no_solve: None
) -> None:
    """同 id 同主体换时段：这次求解只带新时段，context 只剩新版（求解与持久化一致）。"""
    goal = _make_goal(
        context={"schema_version": 1, "soft_task_constraints": [_soft("tc-1", "T01", ["S05"])]}
    )
    response = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "重排 B01",
            "class_business_ids": ["B01"],
            "goal_id": goal.id,
            "task_constraints": [_constraint("tc-1", "T01", ["S07"], "soft")],
        },
    )
    assert response.status_code == 202, response.text
    assert [rule["scope"]["slot_ids"] for rule in _task_rules(response.json()["id"])] == [["S07"]]
    with SessionLocal() as db:
        row = db.get(SolveGoal, goal.id)
        assert row is not None
        assert [
            item["slot_business_ids"] for item in (row.context or {})["soft_task_constraints"]
        ] == [["S07"]]


def test_concurrent_task_revision_is_rejected_atomically(
    client: TestClient,
    auth_headers: dict[str, str],
    no_solve: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """修订读取后清单被并发改到新版本：整体 409 回滚，不带着旧快照提交，也不留半份修订。"""
    goal = _make_goal()
    real = api_module.apply_goal_checklist_revision

    def raced(db: Any, goal_row: SolveGoal, **kwargs: Any) -> bool:
        with SessionLocal() as other:
            other.execute(
                SolveGoal.__table__.update()
                .where(SolveGoal.__table__.c.id == goal_row.id)
                .values(checklist_revision=SolveGoal.__table__.c.checklist_revision + 1)
            )
            other.commit()
        return real(db, goal_row, **kwargs)

    monkeypatch.setattr(api_module, "apply_goal_checklist_revision", raced)
    response = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "重排 B01",
            "class_business_ids": ["B01"],
            "goal_id": goal.id,
            "task_constraints": [_constraint("tc-1", "T01", ["S05"], "hard")],
        },
    )
    assert response.status_code == 409, response.text
    with SessionLocal() as db:
        row = db.get(SolveGoal, goal.id)
        assert row is not None
        assert row.checklist == []
        assert not list(db.scalars(select(SolverRun).where(SolverRun.goal_id == goal.id)))


# ------------------------------------------------- R2 按原参数重跑


def _completed_run(client: TestClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    response = client.post(
        "/api/v1/solver-runs",
        headers=headers,
        json={"class_business_ids": ["B01"], "wait": False, **body},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    with SessionLocal() as db:
        row = db.get(SolverRun, run_id)
        assert row is not None
        row.status = "completed"
        db.commit()
    return response.json()


def test_rerun_keeps_budget_base_rules_weight_scope_and_frozen_inputs(
    client: TestClient, auth_headers: dict[str, str], no_solve: None
) -> None:
    """原预算 300 秒、关掉了固定时段与日程不重叠、自定义权重：加预算后预算按 300 计、其余一字不变，
    数据快照与偏好记忆沿用原求解冻结的那一份。"""
    source = _completed_run(
        client,
        auth_headers,
        time_limit_seconds=300,
        solver_rules=["room_no_overlap", "teacher_no_overlap"],
        change_weight=500,
        date_window_days=3,
    )
    assert source["time_limit_seconds"] == 300
    rerun = client.post(f"/api/v1/solver-runs/{source['id']}/rerun", headers=auth_headers, json={})
    assert rerun.status_code == 202, rerun.text
    body = rerun.json()
    frozen = _stored_run(source["id"])
    again = _stored_run(body["id"])
    assert body["time_limit_seconds"] == 900  # min(max(300×3, 90), 900)，而不是默认草稿的 90
    assert body["rerun_of"] == source["id"]
    assert again.snapshot_id == frozen.snapshot_id
    assert again.memory_usage == frozen.memory_usage
    for key in (
        "solver_rules",
        "change_weight",
        "class_business_ids",
        "business_lines",
        "date_window_days",
        "parent_schedule_id",
        "previous_assignments",
        "baseline_source",
    ):
        assert again.request_payload.get(key) == frozen.request_payload.get(key), key
    assert "fixed_time" not in again.request_payload["solver_rules"]
    assert "calendar_no_overlap" not in again.request_payload["solver_rules"]


def test_rerun_budget_formula_and_explicit_override(
    client: TestClient, auth_headers: dict[str, str], no_solve: None
) -> None:
    short = _completed_run(client, auth_headers, time_limit_seconds=30)
    assert (
        client.post(
            f"/api/v1/solver-runs/{short['id']}/rerun", headers=auth_headers, json={}
        ).json()["time_limit_seconds"]
        == 90
    )
    mid = _completed_run(client, auth_headers, time_limit_seconds=120)
    assert (
        client.post(f"/api/v1/solver-runs/{mid['id']}/rerun", headers=auth_headers, json={}).json()[
            "time_limit_seconds"
        ]
        == 360
    )
    explicit = client.post(
        f"/api/v1/solver-runs/{short['id']}/rerun",
        headers=auth_headers,
        json={"time_limit_seconds": 45},
    )
    assert explicit.json()["time_limit_seconds"] == 45


def test_rerun_refuses_unfinished_run_and_abandoned_goal(
    client: TestClient, auth_headers: dict[str, str], no_solve: None
) -> None:
    queued = client.post(
        "/api/v1/solver-runs", headers=auth_headers, json={"class_business_ids": ["B01"]}
    ).json()
    assert (
        client.post(
            f"/api/v1/solver-runs/{queued['id']}/rerun", headers=auth_headers, json={}
        ).status_code
        == 409
    )
    goal = _make_goal()
    source = _completed_run(client, auth_headers, goal_id=goal.id)
    abandoned = client.post(f"/api/v1/goals/{goal.id}/abandon", headers=auth_headers)
    assert abandoned.status_code == 200, abandoned.text
    assert (
        client.post(
            f"/api/v1/solver-runs/{source['id']}/rerun", headers=auth_headers, json={}
        ).status_code
        == 409
    )


def test_rerun_refuses_reschedule_and_import_runs(
    client: TestClient, auth_headers: dict[str, str], no_solve: None
) -> None:
    """调课/导入求解带着各自专属的冻结参数：按排课求解的那一套重放会悄悄丢掉它们，所以明确拒绝。"""
    source = _completed_run(client, auth_headers)
    with SessionLocal() as db:
        row = db.get(SolverRun, source["id"])
        assert row is not None
        row.run_type = "reschedule"
        db.commit()
    refused = client.post(
        f"/api/v1/solver-runs/{source['id']}/rerun", headers=auth_headers, json={}
    )
    assert refused.status_code == 409, refused.text
    assert "不支持按原参数重跑" in refused.json()["detail"]


def test_rerun_uses_task_current_requirements_for_goal_runs_and_frozen_ones_otherwise(
    client: TestClient, auth_headers: dict[str, str], no_solve: None
) -> None:
    """有任务：重跑编译任务当前版本（与验收同一份）；无任务的一句话求解：沿用当时冻结的任务约束。"""
    goal = _make_goal(checklist=[_hard_item("forbidden_slot_free-1", "T01", ["S05"])])
    source = _completed_run(client, auth_headers, goal_id=goal.id)
    with SessionLocal() as db:
        row = db.get(SolveGoal, goal.id)
        assert row is not None
        row.checklist = [*row.checklist, _hard_item("forbidden_slot_free-2", "T02", ["S07"])]
        db.commit()
    rerun = client.post(f"/api/v1/solver-runs/{source['id']}/rerun", headers=auth_headers, json={})
    assert sorted(rule["actor_ids"][0] for rule in _task_rules(rerun.json()["id"])) == [
        "T01",
        "T02",
    ]

    loose = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "重排 B01，T01 周三晚不能上",
            "class_business_ids": ["B01"],
            "task_constraints": [_constraint("tc-1", "T01", ["S05"], "hard")],
        },
    )
    assert loose.status_code == 202, loose.text
    with SessionLocal() as db:
        run = db.get(SolverRun, loose.json()["id"])
        assert run is not None
        run.status = "completed"
        db.commit()
    rerun_loose = client.post(
        f"/api/v1/solver-runs/{loose.json()['id']}/rerun", headers=auth_headers, json={}
    )
    assert rerun_loose.status_code == 202, rerun_loose.text
    assert [rule["actor_ids"] for rule in _task_rules(rerun_loose.json()["id"])] == [["T01"]]
    stored = _stored_run(rerun_loose.json()["id"])
    assert stored.request_payload["assistant_entry"] is True
    assert stored.request_payload["instruction"] == "重排 B01，T01 周三晚不能上"


# ------------------------------------------------- R3 工作草稿指针保护


def _scoped_run_with_draft(
    scope_id: str, goal_id: str, *, age_seconds: int, version_no: int, anchor: int | None = 1
) -> tuple[SolverRun, ScheduleVersion]:
    with SessionLocal() as db:
        snapshot = DataSnapshot(
            schedule_set_id=scope_id,
            revision=version_no,
            checksum=f"wd-{uuid4().hex[:10]}",
            payload={"course_sessions": []},
        )
        db.add(snapshot)
        db.flush()
        payload: dict[str, Any] = {}
        if anchor is not None:
            payload["goal_checklist_version"] = anchor
        run = SolverRun(
            schedule_set_id=scope_id,
            snapshot_id=snapshot.id,
            status="completed",
            model_status="OPTIMAL",
            request_payload=payload,
            goal_id=goal_id,
            created_at=shanghai_now() - timedelta(seconds=age_seconds),
        )
        db.add(run)
        db.flush()
        version = ScheduleVersion(
            schedule_set_id=scope_id,
            version_no=version_no,
            name=f"工作草稿 V{version_no}",
            status="draft",
            solver_run_id=run.id,
            metrics={},
        )
        db.add(version)
        db.commit()
        db.refresh(run)
        db.refresh(version)
        return run, version


def _promote(goal_id: str, run: SolverRun, version: ScheduleVersion) -> dict[str, Any]:
    with SessionLocal() as db:
        live_run = db.get(SolverRun, run.id)
        live_version = db.get(ScheduleVersion, version.id)
        assert live_run is not None and live_version is not None
        outcome = tasks_module._promote_work_draft(db, live_run, goal_id, live_version)
        db.commit()
        return outcome


def _pointer(goal_id: str) -> str | None:
    with SessionLocal() as db:
        row = db.get(SolveGoal, goal_id)
        assert row is not None
        return (row.context or {}).get("work_draft_schedule_id")


def test_older_run_finishing_late_does_not_take_back_the_work_draft(
    isolated_scope: dict[str, Any],
) -> None:
    """R1（旧）先发起、R2（新）后发起；R2 先完成产出 D2，R1 晚到的 D1 不能把基准换回去。"""
    scope_id = isolated_scope["scope_id"]
    goal = _make_goal(scope_id)
    older, older_draft = _scoped_run_with_draft(scope_id, goal.id, age_seconds=60, version_no=1)
    newer, newer_draft = _scoped_run_with_draft(scope_id, goal.id, age_seconds=5, version_no=2)
    assert _promote(goal.id, newer, newer_draft) == {"promoted": True, "reason": None}
    late = _promote(goal.id, older, older_draft)
    assert late["promoted"] is False and "更晚" in late["reason"]
    assert _pointer(goal.id) == newer_draft.id


def test_in_order_completion_advances_the_work_draft(isolated_scope: dict[str, Any]) -> None:
    scope_id = isolated_scope["scope_id"]
    goal = _make_goal(scope_id)
    older, older_draft = _scoped_run_with_draft(scope_id, goal.id, age_seconds=60, version_no=1)
    newer, newer_draft = _scoped_run_with_draft(scope_id, goal.id, age_seconds=5, version_no=2)
    assert _promote(goal.id, older, older_draft)["promoted"] is True
    assert _pointer(goal.id) == older_draft.id
    assert _promote(goal.id, newer, newer_draft)["promoted"] is True
    assert _pointer(goal.id) == newer_draft.id


def test_run_built_on_an_outdated_requirement_version_stays_in_history(
    isolated_scope: dict[str, Any],
) -> None:
    """任务要求已修订到 v2，基于 v1 的求解结果只进历史，不接管当前基准。"""
    scope_id = isolated_scope["scope_id"]
    goal = _make_goal(scope_id, checklist_revision=2)
    stale, stale_draft = _scoped_run_with_draft(
        scope_id, goal.id, age_seconds=30, version_no=1, anchor=1
    )
    outcome = _promote(goal.id, stale, stale_draft)
    assert outcome["promoted"] is False and "v2" in outcome["reason"]
    assert _pointer(goal.id) is None
    current, current_draft = _scoped_run_with_draft(
        scope_id, goal.id, age_seconds=1, version_no=2, anchor=2
    )
    assert _promote(goal.id, current, current_draft)["promoted"] is True
    assert _pointer(goal.id) == current_draft.id


def test_evaluation_records_why_a_draft_was_not_promoted(isolated_scope: dict[str, Any]) -> None:
    """端到端：验收写回路径上被拒绝接管的产物，报告 meta 里留下原因。"""
    scope_id = isolated_scope["scope_id"]
    goal = _make_goal(scope_id, checklist_revision=3)
    stale, stale_draft = _scoped_run_with_draft(
        scope_id, goal.id, age_seconds=30, version_no=1, anchor=1
    )
    tasks_module._evaluate_goal_for_run(stale.id, goal.id)
    with SessionLocal() as db:
        run = db.get(SolverRun, stale.id)
        assert run is not None and run.goal_report is not None
        assert run.goal_report["meta"]["work_draft"]["promoted"] is False
    assert _pointer(goal.id) is None


# ------------------------------------------------- R4 解析幂等


def _mock_interpret(monkeypatch: pytest.MonkeyPatch, output: dict[str, Any]) -> None:
    monkeypatch.setattr(settings, "ai_base_url", "https://model.example/v1")
    monkeypatch.setattr(settings, "ai_api_key", "environment-ai-key")
    monkeypatch.setattr(settings, "ai_model", "scheduling-model")
    monkeypatch.setattr(
        "app.api.AIService.interpret_instruction", lambda *args, **kwargs: (output, None)
    )


def _output(**overrides: Any) -> dict[str, Any]:
    return {
        "business_lines": [],
        "product_types": [],
        "class_business_ids": [],
        "date_from": None,
        "date_to": None,
        "date_window_days": 7,
        "recognized_rules": [],
        **overrides,
    }


def _save_action(
    subject_id: str, source_text: str, slots: list[str], **extra: Any
) -> dict[str, Any]:
    return {
        "action": "save_preference",
        "basis": "explicit",
        "source_text": source_text,
        "subject_type": "teacher",
        "subject_id": subject_id,
        "predicate": "avoid_slot",
        "constraint": {"slot_ids": slots},
        **extra,
    }


def _entries(scope_id: str, subject_id: str | None = None) -> list[PreferenceEntry]:
    with SessionLocal() as db:
        query = select(PreferenceEntry).where(PreferenceEntry.schedule_set_id == scope_id)
        if subject_id:
            query = query.where(PreferenceEntry.subject_id == subject_id)
        rows = list(db.scalars(query).all())
        for row in rows:
            db.expunge(row)
        return rows


def test_interpret_retry_with_same_request_id_executes_remember_once(
    client: TestClient, isolated_scope: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """提交成功后结果在网络里丢失 → 前端用同一句话、同一个 request_id 回退同步接口：
    「记住」只执行一次，回执原样返回，撤销一条不会留下另一条继续生效。"""
    scope_id, headers = isolated_scope["scope_id"], isolated_scope["headers"]
    instruction = "记住，赵一周三晚都不排课，另外重排 B01"
    _mock_interpret(
        monkeypatch,
        _output(memory_actions=[_save_action("T91", "记住，赵一周三晚都不排课", ["S1"])]),
    )
    request_id = uuid4().hex
    first = client.post(
        "/api/v1/assistant/interpret",
        headers=headers,
        json={"instruction": instruction, "request_id": request_id},
    )
    assert first.status_code == 200, first.text
    receipt = first.json()["memory_action_receipts"][0]
    assert receipt["status"] == "executed"
    second = client.post(
        "/api/v1/assistant/interpret",
        headers=headers,
        json={"instruction": instruction, "request_id": request_id},
    )
    assert second.status_code == 200, second.text
    assert second.json()["memory_action_receipts"] == first.json()["memory_action_receipts"]
    assert len(_entries(scope_id, "T91")) == 1
    # 同一个标识配另一句话：不是重试，是误用——拒绝而不是默默返回别人的回执。
    other = client.post(
        "/api/v1/assistant/interpret",
        headers=headers,
        json={"instruction": "记住，钱二周四都不排课", "request_id": request_id},
    )
    assert other.status_code == 409, other.text


def test_stream_replays_the_stored_result_without_reexecuting(
    client: TestClient, isolated_scope: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    scope_id, headers = isolated_scope["scope_id"], isolated_scope["headers"]
    instruction = "记住，钱二周四都不排课，另外重排 B01"
    _mock_interpret(
        monkeypatch,
        _output(memory_actions=[_save_action("T92", "记住，钱二周四都不排课", ["S2"])]),
    )
    request_id = uuid4().hex
    first = client.post(
        "/api/v1/assistant/interpret",
        headers=headers,
        json={"instruction": instruction, "request_id": request_id},
    )
    assert first.status_code == 200, first.text
    streamed = client.post(
        "/api/v1/assistant/interpret/stream",
        headers=headers,
        json={"instruction": instruction, "request_id": request_id},
    )
    assert streamed.status_code == 200, streamed.text
    frames = [block for block in streamed.text.split("\n\n") if block.strip()]
    assert frames[-1].startswith("event: result")
    replayed = json.loads(frames[-1].split("data: ", 1)[1])
    assert replayed["memory_action_receipts"] == first.json()["memory_action_receipts"]
    assert len(_entries(scope_id, "T92")) == 1


def test_concurrent_identical_requests_commit_only_one_side_effect(
    isolated_scope: dict[str, Any],
) -> None:
    """两个相同请求都越过了「是否已有回执」的检查：后提交的撞唯一约束，整体回滚后读回先提交者的
    回执，它执行的记忆动作随回滚撤销（SQLite 单写者，交错用顺序模拟：赢家先完整提交，输家没做
    回执检查就直接进入收口）。"""
    scope_id = isolated_scope["scope_id"]
    instruction = "记住，孙三周三晚都不排课"
    request = AssistantInterpretRequest(instruction=instruction, request_id=uuid4().hex)
    output = _output(memory_actions=[_save_action("T93", "记住，孙三周三晚都不排课", ["S1"])])

    def run_once() -> Any:
        with SessionLocal() as db:
            actor = db.scalar(select(User).where(User.username == "admin"))
            assert actor is not None
            context = api_module._interpret_context(db, scope_id, None, instruction)
            normalized = api_module._finalize_assistant_interpret(
                db,
                request,
                output,
                scope_id,
                source="openai_compatible",
                ai_configured=True,
                aily_configured=False,
                thinking=None,
                context=context,
                actor=actor,
            )
            return api_module._commit_interpret(db, actor, scope_id, request, normalized, {})

    winner = run_once()
    loser = run_once()
    assert loser.memory_action_receipts == winner.memory_action_receipts
    assert len(_entries(scope_id, "T93")) == 1
    with SessionLocal() as db:
        receipts = db.scalars(
            select(AssistantInterpretReceipt).where(
                AssistantInterpretReceipt.request_id == request.request_id
            )
        ).all()
        assert len(list(receipts)) == 1


# ------------------------------------------------- R5 动作级授权绑定


def test_authorization_clause_and_negation_table() -> None:
    mixed = "记住张老师偏好上午；李老师这次先放周四，不要记成长期偏好。"
    clause, reason = authorization_clause(mixed, "李老师这次先放周四，不要记成长期偏好。")
    assert reason is None and clause == "李老师这次先放周四，不要记成长期偏好"
    assert "save" not in scoped_word_hits(clause)
    zhang, _ = authorization_clause(mixed, "记住张老师偏好上午")
    assert scoped_word_hits(zhang or "") == {"save": ["记住"]}
    # 定位不到 / 太短 / 跨句：都绑不上授权。
    assert authorization_clause(mixed, "王老师周五不排课")[0] is None
    assert authorization_clause(mixed, "记住")[0] is None
    assert "跨越多句" in (authorization_clause(mixed, mixed)[1] or "")
    # 否定可以隔着几个字；正向话术照常命中。
    for refused in ("先别记住这个", "不用记成长期偏好", "这不算长期，先别存", "不是长期偏好"):
        assert "save" not in scoped_word_hits(refused), refused
    for accepted in (
        "记住，这学期张老师周三晚尽量别排",
        "张老师周三晚别排，记住",
        "特别记住：李老师周四不排",
        "以后都不要排晚课",
        "长期都别排周三晚",
    ):
        assert scoped_word_hits(accepted).get("save"), accepted
    assert "expire" not in scoped_word_hits("别撤销张老师那条")
    assert scoped_word_hits("张老师那条不要用了").get("expire")


def test_one_off_clause_cannot_borrow_another_clauses_remember(
    client: TestClient, isolated_scope: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """探针 global_save_word_authorizes_unrelated_one_off_action：模型把「这次先放周四，不要记成
    长期偏好」误标成 explicit 时，整句里张老师那条的「记住」不能替它授权——降级成待确认候选。"""
    scope_id, headers = isolated_scope["scope_id"], isolated_scope["headers"]
    instruction = "记住赵一周三晚都不排课；钱二这次先放周四，不要记成长期偏好。"
    _mock_interpret(
        monkeypatch,
        _output(
            memory_actions=[
                _save_action("T91", "记住赵一周三晚都不排课", ["S1"]),
                _save_action("T92", "钱二这次先放周四，不要记成长期偏好。", ["S2"]),
            ]
        ),
    )
    response = client.post(
        "/api/v1/assistant/interpret", headers=headers, json={"instruction": instruction}
    )
    assert response.status_code == 200, response.text
    receipts = {
        item["action_id"]: item["status"] for item in response.json()["memory_action_receipts"]
    }
    assert receipts == {"ma-1": "executed", "ma-2": "pending_confirmation"}
    (zhao,) = _entries(scope_id, "T91")
    (qian,) = _entries(scope_id, "T92")
    assert zhao.status == "confirmed"
    assert qian.status == "probation" and qian.trial_authorized is False


def test_action_pointing_at_another_subject_or_unlocatable_text_is_not_authorized(
    client: TestClient, isolated_scope: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    scope_id, headers = isolated_scope["scope_id"], isolated_scope["headers"]
    instruction = "记住，赵一周三晚都不排课"
    _mock_interpret(
        monkeypatch,
        _output(
            memory_actions=[
                # 分句点名的是赵一，动作却落在钱二身上：授权不能借给别的主体。
                _save_action("T92", "记住，赵一周三晚都不排课", ["S1"]),
                # 原话片段不在指令里：无法证明授权属于哪一句。
                _save_action("T91", "记住孙三周四都不排课", ["S2"]),
            ]
        ),
    )
    response = client.post(
        "/api/v1/assistant/interpret", headers=headers, json={"instruction": instruction}
    )
    assert response.status_code == 200, response.text
    statuses = [item["status"] for item in response.json()["memory_action_receipts"]]
    assert statuses == ["pending_confirmation", "pending_confirmation"]
    assert all(entry.status == "probation" for entry in _entries(scope_id))


# ------------------------------------------------- R6 记忆检索按主体


def _seed_entry(scope_id: str, subject_id: str, *, age_days: int, valid_until: Any = None) -> str:
    today = shanghai_now().date()
    with SessionLocal() as db:
        entry = PreferenceEntry(
            schedule_set_id=scope_id,
            subject_type="teacher",
            subject_id=subject_id,
            predicate="avoid_slot",
            constraint={"slot_ids": ["S1"]},
            modality="soft",
            confidence=1.0,
            source="explicit_stated",
            evidence=[],
            weight=50,
            status="confirmed",
            valid_from=today - timedelta(days=30),
            valid_until=valid_until or today + timedelta(days=90),
            provenance={},
            created_at=shanghai_now() - timedelta(days=age_days),
        )
        db.add(entry)
        db.commit()
        return entry.id


def test_old_preference_stays_reachable_after_many_newer_unrelated_memories(
    isolated_scope: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """张老师的旧偏好之后又新增了一堆别人的记忆：最近 N 条里没有它，但原话提到了赵一，
    它就必须在注入列表里（否则仍参与排课却没法用自然语言纠正）。已过期的不占位。"""
    scope_id = isolated_scope["scope_id"]
    monkeypatch.setattr(api_module, "_INTERPRET_PREFERENCE_CAP", 4)
    monkeypatch.setattr(api_module, "_INTERPRET_RELEVANT_PREFERENCES", 2)
    old = _seed_entry(scope_id, "T91", age_days=200)
    newer = [_seed_entry(scope_id, "T92", age_days=days) for days in range(1, 7)]
    expired = _seed_entry(
        scope_id, "T91", age_days=1, valid_until=shanghai_now().date() - timedelta(days=1)
    )
    with SessionLocal() as db:
        unrelated = api_module._interpret_context(db, scope_id, None, "重排 B01")
        related = api_module._interpret_context(db, scope_id, None, "赵一那条旧偏好不要用了")
    unrelated_ids = {item["id"] for item in unrelated["active_preferences"]}
    related_ids = [item["id"] for item in related["active_preferences"]]
    assert old not in unrelated_ids  # 只看新近时，旧偏好被挤出去了
    assert related_ids[0] == old  # 与原话相关的优先注入
    assert old in related_ids and expired not in related_ids
    assert len(related_ids) == 4 and set(related_ids) - {old} <= set(newer)


def test_natural_language_can_expire_an_old_preference_outside_the_recent_window(
    client: TestClient, isolated_scope: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    scope_id, headers = isolated_scope["scope_id"], isolated_scope["headers"]
    monkeypatch.setattr(api_module, "_INTERPRET_PREFERENCE_CAP", 3)
    monkeypatch.setattr(api_module, "_INTERPRET_RELEVANT_PREFERENCES", 2)
    old = _seed_entry(scope_id, "T91", age_days=300)
    for days in range(1, 6):
        _seed_entry(scope_id, "T93", age_days=days)
    instruction = "赵一周三晚那条旧偏好不要用了"
    _mock_interpret(
        monkeypatch,
        _output(
            memory_actions=[
                {
                    "action": "expire_preference",
                    "basis": "explicit",
                    "source_text": "赵一周三晚那条旧偏好不要用了",
                    "target_entry_id": old,
                }
            ]
        ),
    )
    response = client.post(
        "/api/v1/assistant/interpret", headers=headers, json={"instruction": instruction}
    )
    assert response.status_code == 200, response.text
    assert response.json()["memory_action_receipts"][0]["status"] == "executed"
    with SessionLocal() as db:
        entry = db.get(PreferenceEntry, old)
        assert entry is not None and entry.status == "expired"


def test_revision_contract_is_exposed_on_the_run_response_schema(client: TestClient) -> None:
    """前端读取的三个字段都在 OpenAPI 契约里（orval 据此生成类型）。"""
    schema = client.get("/openapi.json").json()["components"]["schemas"]["SolverRunResponse"]
    assert {"time_limit_seconds", "task_revision", "rerun_of"} <= set(schema["properties"])
    assert "/api/v1/solver-runs/{run_id}/rerun" in client.get("/openapi.json").json()["paths"]
