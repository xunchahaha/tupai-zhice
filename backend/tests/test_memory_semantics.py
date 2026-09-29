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
from app.services.explain import build_explanation_facts
from app.services.memory_solver import (
    compile_memory_state,
    resolve_conflicts_for_new_entry,
)
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
    # 窗口相对 today 生成：口径是「有效期窗口决定软惩罚作用范围」，硬编码日期
    # 会随真实时间漂移（valid_until 一旦早于今天，条目先被判 expired）。
    today = shanghai_now().date()
    entry = _add_entry(
        scope["scope_id"],
        subject_id="T9",
        valid_from=today,
        valid_until=today + timedelta(days=30),
    )

    with SessionLocal() as db:
        state = compile_memory_state(db, scope["scope_id"])
    rule = state["compiled_rules"][0]
    assert rule["scope"]["date_from"] == today.isoformat()
    assert rule["scope"]["date_to"] == (today + timedelta(days=30)).isoformat()

    # 窗口外课次 + 只有周一一个可选时段：惩罚是否生效完全由日期窗口决定。
    # 课次日期必须锚定在周一：否则求解器会把它挪到 ±date_window_days 内的周一，
    # 挪动后的日期可能越过 date_to，断言随运行日期的星期漂移。
    lesson_day = today + timedelta(days=60)
    lesson_day -= timedelta(days=lesson_day.weekday())
    outside_date = lesson_day.isoformat()
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
                "lesson_date": outside_date,
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
    assert outside["objective_value"] == 0, "有效期外的偏好不得约束窗口外课次"

    inside_rule = {
        **rule,
        "scope": {**rule["scope"], "date_to": lesson_day.isoformat()},
    }
    inside = solve_problem({**payload, "rules": [inside_rule]})
    assert inside["model_status"] in {"OPTIMAL", "FEASIBLE"}
    assert inside["objective_value"] == entry.weight, "窗口内的课次应吃到偏好惩罚"


# ------------------------------------------------- MEM-D1 D2：两层日期取交集


def test_constraint_dates_intersect_with_entry_validity(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """constraint 日期窗口与条目级有效期取交集，不再互相覆盖：
    constraint 今明两天 + 条目级更宽 → 编译窗口就是 constraint 那两天。
    （窗口相对 today 生成：valid_from 截窄/撑宽与真实日期无关，硬编码日期会
    随时间漂移——MEM-F 复审时已踩中一例。）"""
    scope = _make_scope(client, auth_headers)
    today = shanghai_now().date()
    entry = _add_entry(
        scope["scope_id"],
        subject_id="T9",
        constraint={
            "slot_ids": ["S1"],
            "date_from": today.isoformat(),
            "date_to": (today + timedelta(days=1)).isoformat(),
        },
        valid_from=today - timedelta(days=30),
        valid_until=today + timedelta(days=90),
    )

    with SessionLocal() as db:
        state = compile_memory_state(db, scope["scope_id"])
    rule = next(item for item in state["compiled_rules"] if item["memory_entry_id"] == entry.id)
    # 编译窗口 = 两层窗口的交集（窄的一侧），条目级宽窗口不把两天窗口撑宽。
    assert rule["scope"]["date_from"] == today.isoformat()
    assert rule["scope"]["date_to"] == (today + timedelta(days=1)).isoformat()


def test_disjoint_constraint_and_entry_windows_are_not_applicable(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """两层日期窗口不相交 → outcome=not_applicable，条目不进编译（明确不适用，
    而不是拿另一层窗口顶上）。constraint 窗口整体早于条目级有效期。"""
    scope = _make_scope(client, auth_headers)
    today = shanghai_now().date()
    entry = _add_entry(
        scope["scope_id"],
        subject_id="T9",
        constraint={
            "slot_ids": ["S1"],
            "date_from": (today - timedelta(days=60)).isoformat(),
            "date_to": (today - timedelta(days=50)).isoformat(),
        },
        valid_from=today - timedelta(days=10),
        valid_until=today + timedelta(days=90),
    )

    with SessionLocal() as db:
        state = compile_memory_state(db, scope["scope_id"])
    outcomes = {item["entry_id"]: item for item in state["outcomes"]}
    assert outcomes[entry.id]["outcome"] == "not_applicable"
    assert "不相交" in outcomes[entry.id]["detail"]
    assert all(rule["memory_entry_id"] != entry.id for rule in state["compiled_rules"])


def test_empty_window_new_entry_never_displaces_valid_old_entry(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """MEM-F/F1：空交集的新条目不得触发替代。

    已有有效偏好 A（confirmed，本学期）→ 新增 B（confirmed，constraint 窗口在
    条目 valid_until 之后 → 交集空；等价复审反例「constraint 10/1-2、条目有效期
    至 9/30」，窗口相对 today 生成避免真实日期漂移）。修复前：B 虽然永远不会
    生效，却以 confirmed 授权身份走 new_replaces 分支把 A 顶成 expired；修复后：
    B 直接判 not_applicable，A 保持 confirmed/applied 且在 compiled_rules 里。"""
    scope = _make_scope(client, auth_headers)
    today = shanghai_now().date()
    valid_a = _add_entry(
        scope["scope_id"],
        subject_id="T9",
        constraint={"slot_ids": ["S1"]},
        valid_from=today,
        valid_until=today + timedelta(days=90),
    )
    empty_b = _add_entry(
        scope["scope_id"],
        subject_id="T9",
        constraint={
            "slot_ids": ["S1"],
            "date_from": (today + timedelta(days=1)).isoformat(),
            "date_to": (today + timedelta(days=2)).isoformat(),
        },
        valid_from=today,
        valid_until=today,
    )
    # B 的两层日期窗口确实不相交（守卫的触发条件）。
    with SessionLocal() as db:
        branch = resolve_conflicts_for_new_entry(
            db, db.get(PreferenceEntry, empty_b.id)
        )
        db.commit()
    assert branch == "not_applicable"

    # A 不受牵连：状态未被置 expired，provenance 没有 superseded_by 链。
    with SessionLocal() as db:
        stored_a = db.get(PreferenceEntry, valid_a.id)
        assert stored_a is not None
        assert stored_a.status == "confirmed"
        assert "superseded_by" not in (stored_a.provenance or {})
        state = compile_memory_state(db, scope["scope_id"])
    rule_ids = {rule["memory_entry_id"] for rule in state["compiled_rules"]}
    assert valid_a.id in rule_ids, "有效旧偏好必须照常编译"
    assert empty_b.id not in rule_ids
    outcomes = {item["entry_id"]: item["outcome"] for item in state["outcomes"]}
    assert outcomes[valid_a.id] == "applied"
    assert outcomes[empty_b.id] == "not_applicable"


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


# ------------------------------------------------- 第七轮收口：记忆使用三段口径


def _make_two_teacher_scope_entries_and_courses(
    client: TestClient, auth_headers: dict[str, str]
) -> dict[str, Any]:
    """两个 confirmed 偏好（T9 回避 S1 / T8 偏好 S1）+ 两门课（B1/T9、B2/T9）。

    T8 是不存在课次的教师：它的偏好编译成功（编译资格）但永远不匹配 B1 范围
    的课程——用来区分「已获准编译」与「对本次课程实际匹配」。
    """
    scope = _make_scope(client, auth_headers)
    entry_t9 = _add_entry(
        scope["scope_id"],
        subject_id="T9",
        predicate="avoid_slot",
        constraint={"slot_ids": ["S1"]},
    )
    entry_t8 = _add_entry(
        scope["scope_id"],
        subject_id="T8",
        predicate="prefer_slot",
        constraint={"slot_ids": ["S1"]},
    )
    _add_session(scope["scope_id"], "C1", date(2026, 10, 5))  # B1 / T9
    with SessionLocal() as db:
        campus_id = db.scalar(select(Campus.id).where(Campus.schedule_set_id == scope["scope_id"]))
        assert campus_id is not None
        db.add(
            CourseSession(
                schedule_set_id=scope["scope_id"],
                campus_id=campus_id,
                business_id="C2",
                class_business_id="B2",
                teacher_business_id="T9",
                lesson_date=date(2026, 10, 6),
                fixed_start_time="08:30",
                fixed_end_time="11:30",
            )
        )
        db.commit()
    return {"scope": scope, "entry_t9": entry_t9, "entry_t8": entry_t8}


def test_memory_usage_without_result_has_no_satisfaction_claim(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """无求解结果时 usage 事实不得包含「满足」结论，也不把编译资格说成使用。

    三段口径：compilation=创建时点方案级编译资格；match=按本次任务课程范围
    （B1）核对；satisfaction=None 且 headline 明说「尚无使用证据」。"""
    setup = _make_two_teacher_scope_entries_and_courses(client, auth_headers)
    scope_id = setup["scope"]["scope_id"]
    with SessionLocal() as db:
        run = api.create_solver_run(db, None, SolveRequest(class_business_ids=["B1"]), scope_id)
        run_id = run.id

    with SessionLocal() as db:
        run_row = db.get(SolverRun, run_id)
        assert run_row is not None
        facts = build_explanation_facts(db, run_row)
    usage = facts["memory_usage"]
    # 第一段：已获准编译（创建时点、方案级——两条都编译成功）。
    assert usage["status"] == "ok"
    assert usage["compilation"] == {"considered": 2, "admitted": 2, "excluded": 0}
    # 第二段：按本次任务课程范围（B1，只有 C1 一节课）核对匹配。
    assert usage["match"]["scope_sessions"] == 1
    assert usage["match"]["admitted"] == 2
    assert usage["match"]["matched"] == 1
    matched_by_rule = {
        row["business_id"]: row["matched_sessions"] for row in usage["match"]["rules"]
    }
    assert matched_by_rule[f"MEMORY-{setup['entry_t9'].id}"] == 1
    assert matched_by_rule[f"MEMORY-{setup['entry_t8'].id}"] == 0
    # 第三段：没有结果证据 → 无满足结论。
    assert usage["satisfaction"] is None
    assert "尚无使用证据" in usage["headline"]
    # 编译资格不得被表述成本次使用结论。
    assert "本次参考" not in usage["headline"]
    assert "已应用" not in usage["headline"]
    assert "创建任务时 2 条偏好记忆获准编译" in usage["headline"]
    assert "1 条与本次课程实际匹配" in usage["headline"]


def test_memory_usage_match_and_evidence_computed_by_run_scope(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """有求解结果时：匹配数按本次课程范围计算，满足情况按结果证据计算。

    T9 的 C1 被排在它回避的 S1 → 结果证据记 1 次触碰；T8 的偏好没有作用
    对象（B1 范围内没有 T8 的课），不产生任何证据。"""
    setup = _make_two_teacher_scope_entries_and_courses(client, auth_headers)
    scope_id = setup["scope"]["scope_id"]
    with SessionLocal() as db:
        run = api.create_solver_run(db, None, SolveRequest(class_business_ids=["B1"]), scope_id)
        run_id = run.id
    with SessionLocal() as db:
        run_row = db.get(SolverRun, run_id)
        assert run_row is not None
        course_id = db.scalar(
            select(CourseSession.id).where(
                CourseSession.schedule_set_id == scope_id,
                CourseSession.business_id == "C1",
            )
        )
        assert course_id is not None
        run_row.status = "completed"
        run_row.model_status = "OPTIMAL"
        run_row.result_payload = {
            "assignments": [
                {
                    "course_session_id": course_id,
                    "course_business_id": "C1",
                    "class_business_id": "B1",
                    "teacher_business_id": "T9",
                    "lesson_date": "2026-10-05",
                    "slot_business_id": "S1",
                    "room_business_id": "R1",
                    "change_kind": "assigned",
                }
            ]
        }
        db.commit()
        facts = build_explanation_facts(db, run_row)
    usage = facts["memory_usage"]
    # 匹配数仍按本次课程范围（B1）计算，与无结果时一致。
    assert usage["match"]["scope_sessions"] == 1
    assert usage["match"]["matched"] == 1
    # 第三段：结果存在 → 证据可算：T9 的 avoid_slot S1 被触碰 1 次。
    satisfaction = usage["satisfaction"]
    assert satisfaction is not None
    assert satisfaction["evaluated_assignments"] == 1
    assert satisfaction["avoid_violations"] == 1
    assert satisfaction["prefer_hits"] == 0
    rows = {row["business_id"]: row for row in satisfaction["rules"]}
    assert rows[f"MEMORY-{setup['entry_t9'].id}"]["avoid_violations"] == 1
    assert rows[f"MEMORY-{setup['entry_t8'].id}"]["matched_assignments"] == 0
    # 有证据后 headline 不再说「尚无使用证据」。
    assert "尚无使用证据" not in usage["headline"]
