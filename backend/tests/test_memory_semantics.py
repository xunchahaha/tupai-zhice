"""记忆语义与安全边界（MEM-C1 + MEM-D1 D2）验收测试。

覆盖 docs/roadmap/02-agent-memory.md §6 审查修正表第 1、2、5、6 组与 §7 D2：

1. 三态拆分：纯 probation 候选不进求解输入（snapshot.memory.compiled_rules 为空）；
   「授权试用」后以试用期衰减权重进入；trial_until 到期自动退出。
2. 偏好库只管软偏好：hard 条目不再编译（hard_requires_conversion），
   convert-to-rule 生成 hardness=hard 的正式规则并双向回链。
3. 生效日期窗口：constraint 日期与条目 valid_from/valid_until 取**交集**进规则
   scope（MEM-D1 D2，不再覆盖）；交集为空 → not_applicable 不进编译；
   创建 API 的默认有效期取方案最大上课日期。
4. 偏好冻结进快照：创建任务即编译冻结，事后改/停记忆不影响该 run 的可复现性；
   编译失败显式提示「本次未使用偏好记忆」，不无声出课表。
5. _session_matches_rule 实体匹配严格化：教师 001 不匹配班级 001。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.api as api
from app.db import SessionLocal
from app.models import (
    Campus,
    CourseSession,
    DataSnapshot,
    PreferenceEntry,
    Room,
    SolverRun,
    Teacher,
    TimeSlot,
)
from app.schemas import SolveRequest
from app.services.memory_solver import compile_memory_state
from app.services.solver import _session_matches_rule, solve_problem
from app.services.tasks import execute_solver_run
from app.timezone import shanghai_now


def _make_scope(client: TestClient, auth_headers: dict[str, str]) -> dict[str, Any]:
    """独立课表方案：一套最小主数据，避免污染 default 方案的偏好集合。"""
    created = client.post(
        "/api/v1/schedule-sets",
        headers=auth_headers,
        json={"name": f"记忆语义-{uuid4().hex[:8]}"},
    )
    assert created.status_code == 201, created.text
    scope_id = created.json()["id"]
    with SessionLocal() as db:
        campus = Campus(schedule_set_id=scope_id, business_id="C", name="语义校区")
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
            ]
        )
        db.commit()
    return {"headers": {**auth_headers, "X-Schedule-Set-Id": scope_id}, "scope_id": scope_id}


def _add_entry(scope_id: str, **overrides: Any) -> PreferenceEntry:
    data: dict[str, Any] = {
        "schedule_set_id": scope_id,
        "subject_type": "teacher",
        "subject_id": "T9",
        "predicate": "avoid_slot",
        "constraint": {"slot_ids": ["S1"]},
        "modality": "soft",
        "confidence": 1.0,
        "source": "admin_directive",
        "status": "confirmed",
        "weight": 100,
        "evidence": [],
        "provenance": {},
    }
    data.update(overrides)
    with SessionLocal() as db:
        entry = PreferenceEntry(**data)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry


def _add_session(scope_id: str, business_id: str, lesson_date: date) -> None:
    with SessionLocal() as db:
        campus_id = db.scalar(select(Campus.id).where(Campus.schedule_set_id == scope_id))
        assert campus_id is not None
        db.add(
            CourseSession(
                schedule_set_id=scope_id,
                campus_id=campus_id,
                business_id=business_id,
                class_business_id="B1",
                teacher_business_id="T9",
                lesson_date=lesson_date,
                fixed_start_time="08:30",
                fixed_end_time="11:30",
            )
        )
        db.commit()


def _create_run(scope_id: str) -> SolverRun:
    with SessionLocal() as db:
        return api.create_solver_run(db, None, SolveRequest(), scope_id)


# ------------------------------------------------- 修正 1：三态拆分


def test_probation_candidate_never_enters_solve_input_until_authorized(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    entry = _add_entry(scope["scope_id"], status="probation")
    today = shanghai_now().date()

    # 纯候选：求解输入（snapshot.memory.compiled_rules）必须为空。
    run = _create_run(scope["scope_id"])
    memory = run.memory_usage
    assert memory is not None and memory["status"] == "ok"
    assert memory["compiled_rules"] == []
    with SessionLocal() as db:
        snapshot = db.get(DataSnapshot, run.snapshot_id)
        assert snapshot is not None
        assert snapshot.payload["memory"]["compiled_rules"] == []
    outcome = next(item for item in memory["outcomes"] if item["entry_id"] == entry.id)
    assert outcome["outcome"] == "not_authorized"

    # 授权试用：状态保持 probation，trial_until = 今天 + 30 天（默认）。
    authorized = client.post(
        f"/api/v1/memory/preferences/{entry.id}/transition",
        headers=headers,
        json={"action": "authorize_trial"},
    )
    assert authorized.status_code == 200, authorized.text
    body = authorized.json()
    assert body["status"] == "probation"
    assert body["trial_authorized"] is True
    assert body["trial_until"] == (today + timedelta(days=30)).isoformat()

    trial_run = _create_run(scope["scope_id"])
    trial_memory = trial_run.memory_usage or {}
    rules = trial_memory["compiled_rules"]
    assert len(rules) == 1
    # 授权试用以小权重进入：100 × 0.3（试用期衰减） × 1.0（置信度）。
    assert rules[0]["weight"] == 30
    assert rules[0]["hardness"] == "soft"
    outcomes = {item["entry_id"]: item["outcome"] for item in trial_memory["outcomes"]}
    assert outcomes[entry.id] == "applied"

    # trial_until 过期后自动退出。
    with SessionLocal() as db:
        stored = db.get(PreferenceEntry, entry.id)
        assert stored is not None
        stored.trial_until = today - timedelta(days=1)
        db.commit()
    expired_run = _create_run(scope["scope_id"])
    memory = expired_run.memory_usage
    assert memory is not None
    assert memory["compiled_rules"] == []
    outcome = next(item for item in memory["outcomes"] if item["entry_id"] == entry.id)
    assert outcome["outcome"] == "expired"
    assert "到期" in outcome["detail"]

    # 守卫：只有 probation 条目可授权试用；授权试用不改变状态。
    confirmed = _add_entry(scope["scope_id"], subject_id="T9", status="confirmed")
    wrong_state = client.post(
        f"/api/v1/memory/preferences/{confirmed.id}/transition",
        headers=headers,
        json={"action": "authorize_trial"},
    )
    assert wrong_state.status_code == 409
    mixed = client.post(
        f"/api/v1/memory/preferences/{entry.id}/transition",
        headers=headers,
        json={"action": "authorize_trial", "target_status": "confirmed"},
    )
    assert mixed.status_code == 422


# ------------------------------------------------- 修正 2：hard 转正式规则


def test_hard_entry_requires_conversion_and_convert_to_rule(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    headers = scope["headers"]
    hard = _add_entry(
        scope["scope_id"],
        subject_id="T9",
        modality="hard",
        source="admin_directive",
        constraint={"slot_ids": ["S1"]},
    )

    with SessionLocal() as db:
        state = compile_memory_state(db, scope["scope_id"])
    assert state["compiled_rules"] == []
    outcome = next(item for item in state["outcomes"] if item["entry_id"] == hard.id)
    assert outcome["outcome"] == "hard_requires_conversion"

    converted = client.post(
        f"/api/v1/memory/preferences/{hard.id}/convert-to-rule", headers=headers, json={}
    )
    assert converted.status_code == 201, converted.text
    rule = converted.json()
    assert rule["business_id"] == f"MEMRULE-{hard.id}"
    assert rule["constraint_type"] == "forbidden_slot"
    assert rule["hardness"] == "hard"
    assert rule["status"] == "active"
    assert rule["scope"]["slot_ids"] == ["S1"]
    assert rule["actor_type"] == "teacher"
    # Rule 经 source_doc 回链原记忆条目。
    assert rule["source_doc"] == f"memory:{hard.id}"

    with SessionLocal() as db:
        stored = db.get(PreferenceEntry, hard.id)
        assert stored is not None
        # 条目过期作废不删除，provenance 记录 rule_id 形成双向链。
        assert stored.status == "expired"
        assert stored.provenance["rule_id"] == rule["business_id"]

    # 红线①的转换兜底：induced 来源必须显式传 confirmed_conversion。
    induced = _add_entry(
        scope["scope_id"],
        subject_id="T9",
        modality="hard",
        source="induced_from_adjustment",
        constraint={"slot_ids": ["S1"]},
    )
    denied = client.post(
        f"/api/v1/memory/preferences/{induced.id}/convert-to-rule", headers=headers, json={}
    )
    assert denied.status_code == 422
    confirmed_convert = client.post(
        f"/api/v1/memory/preferences/{induced.id}/convert-to-rule",
        headers=headers,
        json={"confirmed_conversion": True},
    )
    assert confirmed_convert.status_code == 201, confirmed_convert.text

    # soft 条目不属于本端点的职责范围。
    soft = _add_entry(scope["scope_id"], subject_id="T9")
    wrong = client.post(
        f"/api/v1/memory/preferences/{soft.id}/convert-to-rule", headers=headers, json={}
    )
    assert wrong.status_code == 409


# ------------------------------------------------- 修正 5：日期窗口 + 默认有效期


def test_validity_window_scopes_rule_to_lessons(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    entry = _add_entry(
        scope["scope_id"],
        subject_id="T9",
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
    )

    with SessionLocal() as db:
        state = compile_memory_state(db, scope["scope_id"])
    rule = state["compiled_rules"][0]
    assert rule["scope"]["date_from"] == "2026-09-01"
    assert rule["scope"]["date_to"] == "2026-09-30"

    # 10 月课次 + 只有周一一个可选时段：惩罚是否生效完全由日期窗口决定。
    payload: dict[str, Any] = {
        "teachers": [{"business_id": "T9", "name": "教师九", "is_group": False}],
        "rooms": [{"business_id": "R1", "name": "教室1", "is_active": True}],
        "time_slots": [
            {
                "business_id": "S1",
                "weekday": "周一",
                "start_time": "08:30",
                "end_time": "11:30",
                "is_open": True,
            }
        ],
        "course_sessions": [
            {
                "id": "db-L1",
                "business_id": "L1",
                "class_business_id": "B1",
                "teacher_business_id": "T9",
                "lesson_date": "2026-10-05",
                "fixed_start_time": "08:30",
                "fixed_end_time": "11:30",
                "duration_minutes": 180,
            }
        ],
        "change_weight": 0,
        "date_window_days": 2,
        "time_limit_seconds": 5,
        "random_seed": 2026,
    }

    outside = solve_problem({**payload, "rules": [rule]})
    assert outside["model_status"] in {"OPTIMAL", "FEASIBLE"}
    assert outside["assignments"], "窗口外的课次必须照常排出"
    assert outside["objective_value"] == 0, "9/30 失效的偏好不得约束 10 月课次"

    inside_rule = {**rule, "scope": {**rule["scope"], "date_to": "2026-10-31"}}
    inside = solve_problem({**payload, "rules": [inside_rule]})
    assert inside["model_status"] in {"OPTIMAL", "FEASIBLE"}
    assert inside["objective_value"] == entry.weight, "窗口内的课次应吃到偏好惩罚"


# ------------------------------------------------- MEM-D1 D2：两层日期取交集


def test_constraint_dates_intersect_with_entry_validity(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """constraint 日期窗口与条目级有效期取交集，不再互相覆盖：
    constraint 9/24-25 + 条目级到 12/31 → 编译窗口就是 9/24-25。"""
    scope = _make_scope(client, auth_headers)
    entry = _add_entry(
        scope["scope_id"],
        subject_id="T9",
        constraint={"slot_ids": ["S1"], "date_from": "2026-09-24", "date_to": "2026-09-25"},
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 12, 31),
    )

    with SessionLocal() as db:
        state = compile_memory_state(db, scope["scope_id"])
    rule = next(item for item in state["compiled_rules"] if item["memory_entry_id"] == entry.id)
    # 编译窗口 = 两层窗口的交集（窄的一侧），条目级 12/31 不再把两天窗口撑宽。
    assert rule["scope"]["date_from"] == "2026-09-24"
    assert rule["scope"]["date_to"] == "2026-09-25"


def test_disjoint_constraint_and_entry_windows_are_not_applicable(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """两层日期窗口不相交 → outcome=not_applicable，条目不进编译（明确不适用，
    而不是拿另一层窗口顶上）。"""
    scope = _make_scope(client, auth_headers)
    entry = _add_entry(
        scope["scope_id"],
        subject_id="T9",
        constraint={"slot_ids": ["S1"], "date_from": "2026-01-10", "date_to": "2026-01-20"},
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 12, 31),
    )

    with SessionLocal() as db:
        state = compile_memory_state(db, scope["scope_id"])
    outcomes = {item["entry_id"]: item for item in state["outcomes"]}
    assert outcomes[entry.id]["outcome"] == "not_applicable"
    assert "不相交" in outcomes[entry.id]["detail"]
    assert all(rule["memory_entry_id"] != entry.id for rule in state["compiled_rules"])


def test_default_valid_until_uses_schedule_max_lesson_date(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    _add_session(scope["scope_id"], "L-MAX", date(2026, 12, 20))

    created = client.post(
        "/api/v1/memory/preferences",
        headers=scope["headers"],
        json={
            "subject_type": "teacher",
            "subject_id": "T9",
            "predicate": "prefer_slot",
            "constraint": {"slot_ids": ["S1"]},
            "source": "explicit_stated",
        },
    )
    assert created.status_code == 201, created.text
    # 默认有效期优先挂方案最大上课日期（随学期失效），不再一律 +180 天。
    assert created.json()["valid_until"] == "2026-12-20"


# ------------------------------------------------- 修正 6：冻结进快照 + 编译失败可见


def test_memory_frozen_into_snapshot_ignores_later_edits(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scope = _make_scope(client, auth_headers)
    entry = _add_entry(scope["scope_id"], subject_id="T9", weight=80)

    with SessionLocal() as db:
        run = _create_run(scope["scope_id"])
        run_id, snapshot_id = run.id, run.snapshot_id
        snapshot = db.get(DataSnapshot, snapshot_id)
        assert snapshot is not None
        frozen = json.loads(json.dumps(snapshot.payload["memory"]))
    assert [item["memory_entry_id"] for item in frozen["compiled_rules"]] == [entry.id]
    assert frozen["entries"][0]["weight"] == 80

    # 求解创建后修改并停用该条目：该 run 的快照与 memory_usage 必须原封不动。
    with SessionLocal() as db:
        stored = db.get(PreferenceEntry, entry.id)
        assert stored is not None
        stored.weight = 5
        stored.status = "expired"
        db.commit()

    with SessionLocal() as db:
        run = db.get(SolverRun, run_id)
        assert run is not None
        assert run.memory_usage == frozen
        snapshot = db.get(DataSnapshot, snapshot_id)
        assert snapshot is not None
        assert snapshot.payload["memory"] == frozen


def test_compile_failure_is_visible_not_silent(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _make_scope(client, auth_headers)

    def _explode(db: Any, schedule_set_id: str) -> dict[str, Any]:
        raise RuntimeError("模拟编译崩溃")

    monkeypatch.setattr(api, "compile_memory_state", _explode)
    with SessionLocal() as db:
        run = api.create_solver_run(db, None, SolveRequest(), scope["scope_id"])
        run_id = run.id
        memory = run.memory_usage
    assert memory is not None
    assert memory["status"] == "compile_failed"
    assert "模拟编译崩溃" in memory["detail"]

    # 求解照常完成：记忆是加分项，不能拦下主链路。
    result = execute_solver_run(run_id)
    assert result["model_status"] in {"OPTIMAL", "FEASIBLE"}
    with SessionLocal() as db:
        stored = db.get(SolverRun, run_id)
        assert stored is not None
        assert stored.status == "completed"

    # 但解释必须显式说明「本次未使用偏好记忆」，不许无声降级。
    explained = client.post(
        f"/api/v1/solver-runs/{run_id}/explanation", headers=scope["headers"]
    )
    assert explained.status_code == 200, explained.text
    body = explained.json()
    assert any("本次未使用偏好记忆" in line for line in body["explanation"])
    assert any("编译失败" in line for line in body["explanation"])


# ------------------------------------------------- 修正 5：实体匹配严格化


def test_session_matches_rule_requires_type_and_id_alignment() -> None:
    session = {
        "business_id": "L1",
        "class_business_id": "001",
        "teacher_business_id": "T-001",
        "teacher_business_ids": ["T-001"],
    }
    # 教师 001 不匹配班级 001：类型 + 标识必须双重一致。
    assert not _session_matches_rule(
        session, "", {"actor_type": "teacher", "actor_ids": ["001"]}
    )
    assert _session_matches_rule(session, "", {"actor_type": "teacher", "actor_ids": ["T-001"]})
    assert _session_matches_rule(session, "", {"actor_type": "class", "actor_ids": ["001"]})
    assert not _session_matches_rule(
        session, "", {"actor_type": "class", "actor_ids": ["T-001"]}
    )
    # course 主体只认课次标识。
    assert _session_matches_rule(session, "", {"actor_type": "course", "actor_ids": ["L1"]})
    assert not _session_matches_rule(session, "", {"actor_type": "course", "actor_ids": ["001"]})
    # 教室逻辑保持：不点名 = 占用类规则对所有教室生效。
    assert _session_matches_rule(session, "R9", {"actor_type": "room", "actor_ids": []})
    assert _session_matches_rule(session, "R1", {"actor_type": "room", "actor_ids": ["R1"]})
    # 无 actor_ids 的全局规则仍然全匹配。
    assert _session_matches_rule(session, "", {"actor_type": "system", "actor_ids": []})
