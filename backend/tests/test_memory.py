"""记忆层 v1（MEM-A）：偏好 CRUD、transition 红线、挖掘与求解联动。

覆盖 docs/roadmap/02-agent-memory.md §3 的三条红线：
① induced_from_adjustment 条目升硬约束一律 422；
② 创建 API 不传 valid_until 时默认「今天 + 180 天」并回显；
③ 挖掘产生的条目初始 status 恒为 probation。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.api as api
from app.db import SessionLocal
from app.models import (
    Campus,
    DataSnapshot,
    PreferenceEntry,
    RescheduleEvent,
    Room,
    ScheduleVersion,
    SolverRun,
    Teacher,
    TimeSlot,
)
from app.services.ai import AIService
from app.services.memory_solver import (
    DEFAULT_VALIDITY_DAYS,
    compile_memory_state,
    compile_preferences,
    default_valid_until,
    deterministic_preference_candidates,
    effective_weight,
)
from app.services.solver import solve_problem
from app.services.tasks import _attach_memory_preferences
from app.timezone import shanghai_now


@pytest.fixture
def memory_scope(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """独立课表方案：一套主数据 + 一个可挂调课事件的父版本。

    后台求解被 stub 掉：调课事件接口只负责落事件与排队，不真正求解。
    """
    monkeypatch.setattr(api, "enqueue_solver_run", lambda run_id: None)
    created = client.post(
        "/api/v1/schedule-sets",
        headers=auth_headers,
        json={"name": f"记忆层-{uuid4().hex[:8]}"},
    )
    assert created.status_code == 201, created.text
    scope_id = created.json()["id"]
    headers = {**auth_headers, "X-Schedule-Set-Id": scope_id}
    with SessionLocal() as db:
        campus = Campus(schedule_set_id=scope_id, business_id="C", name="记忆校区")
        snapshot = DataSnapshot(
            schedule_set_id=scope_id, revision=1, checksum=uuid4().hex, payload={}
        )
        db.add_all([campus, snapshot])
        db.flush()
        db.add_all(
            [
                Room(
                    schedule_set_id=scope_id, campus_id=campus.id, business_id="R1", name="教室1"
                ),
                Teacher(
                    schedule_set_id=scope_id,
                    campus_id=campus.id,
                    business_id="T9",
                    name="教师九",
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
        run = SolverRun(
            schedule_set_id=scope_id,
            snapshot_id=snapshot.id,
            status="completed",
            request_payload={},
        )
        db.add(run)
        db.flush()
        version = ScheduleVersion(
            schedule_set_id=scope_id,
            version_no=1,
            name="记忆草稿",
            solver_run_id=run.id,
            status="draft",
        )
        db.add(version)
        db.commit()
    return {"headers": headers, "scope_id": scope_id, "version_id": version.id}


def _add_reschedule_event(
    scope_id: str,
    version_id: str,
    *,
    event_type: str,
    payload: dict[str, Any],
    declared_reason: str | None = None,
) -> RescheduleEvent:
    with SessionLocal() as db:
        event = RescheduleEvent(
            schedule_set_id=scope_id,
            event_type=event_type,
            description=f"{event_type} 事件",
            declared_reason=declared_reason,
            payload=payload,
            status="candidate_ready",
            parent_schedule_id=version_id,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event


def _add_entry(**overrides: Any) -> PreferenceEntry:
    data: dict[str, Any] = {
        "schedule_set_id": "default",
        "subject_type": "teacher",
        "subject_id": "T-MEM",
        "predicate": "avoid_slot",
        "constraint": {"slot_ids": ["S1"]},
        "modality": "soft",
        "confidence": 0.8,
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


# ---------------------------------------------------------------- 红线② 与 CRUD


def test_preference_create_defaults_valid_until_180_days(
    client: TestClient, memory_scope: dict[str, Any]
) -> None:
    headers = memory_scope["headers"]
    today = shanghai_now().date()
    created = client.post(
        "/api/v1/memory/preferences",
        headers=headers,
        json={
            "subject_type": "teacher",
            "subject_id": "T9",
            "predicate": "avoid_slot",
            "constraint": {"slot_ids": ["S1"]},
            "source": "explicit_stated",
            "note": "周三晚上要接孩子",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["valid_until"] == (today + timedelta(days=DEFAULT_VALIDITY_DAYS)).isoformat()
    assert body["valid_from"] == today.isoformat()
    assert body["status"] == "confirmed"
    assert body["modality"] == "soft"
    assert body["provenance"]["note"] == "周三晚上要接孩子"

    listed = client.get(
        "/api/v1/memory/preferences",
        headers=headers,
        params={"subject_id": "T9", "status": "confirmed"},
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [body["id"]]

    patched = client.patch(
        f"/api/v1/memory/preferences/{body['id']}",
        headers=headers,
        json={"weight": 70, "scope": {"slot_ids": ["S1", "S2"]}},
    )
    assert patched.status_code == 200, patched.text
    patched_body = patched.json()
    assert patched_body["weight"] == 70
    # scope 是合并入口，不整体替换 constraint。
    assert patched_body["constraint"] == {"slot_ids": ["S1", "S2"]}
    assert patched_body["valid_until"] == body["valid_until"]


def test_preference_rejects_unknown_predicate_subject_and_source(
    client: TestClient, memory_scope: dict[str, Any]
) -> None:
    headers = memory_scope["headers"]
    base: dict[str, Any] = {
        "subject_type": "teacher",
        "subject_id": "T9",
        "predicate": "avoid_slot",
        "source": "admin_directive",
    }
    unknown_predicate = client.post(
        "/api/v1/memory/preferences", headers=headers, json={**base, "predicate": "nope"}
    )
    assert unknown_predicate.status_code == 422

    unknown_subject = client.post(
        "/api/v1/memory/preferences",
        headers=headers,
        json={**base, "subject_id": "GHOST"},
    )
    assert unknown_subject.status_code == 422
    assert "GHOST" in unknown_subject.json()["detail"]

    # 红线①的入口闸门：手工创建不允许伪装成挖掘产物。
    induced = client.post(
        "/api/v1/memory/preferences",
        headers=headers,
        json={**base, "source": "induced_from_adjustment"},
    )
    assert induced.status_code == 422


def test_induced_entry_never_transitions_to_hard(
    client: TestClient, memory_scope: dict[str, Any]
) -> None:
    headers = memory_scope["headers"]
    entry = _add_entry(
        schedule_set_id=memory_scope["scope_id"],
        source="induced_from_adjustment",
        status="probation",
        confidence=0.5,
        weight=40,
    )
    response = client.post(
        f"/api/v1/memory/preferences/{entry.id}/transition",
        headers=headers,
        json={"target_status": "confirmed", "target_modality": "hard"},
    )
    assert response.status_code == 422, response.text
    assert "硬约束" in response.json()["detail"]

    confirmed = client.post(
        f"/api/v1/memory/preferences/{entry.id}/transition",
        headers=headers,
        json={"target_status": "confirmed"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["modality"] == "soft"

    # 非法状态机迁移与历史记录编辑都被拒绝。
    repeat = client.post(
        f"/api/v1/memory/preferences/{entry.id}/transition",
        headers=headers,
        json={"target_status": "confirmed"},
    )
    assert repeat.status_code == 409

    expired = client.post(
        f"/api/v1/memory/preferences/{entry.id}/transition",
        headers=headers,
        json={"target_status": "expired"},
    )
    assert expired.status_code == 200
    edited = client.patch(
        f"/api/v1/memory/preferences/{entry.id}", headers=headers, json={"weight": 10}
    )
    assert edited.status_code == 409


# ---------------------------------------------------------------- 挖掘：确定性 + AI


def test_mining_deterministic_candidates_require_two_events(
    client: TestClient, memory_scope: dict[str, Any]
) -> None:
    scope_id = memory_scope["scope_id"]
    version_id = memory_scope["version_id"]
    headers = memory_scope["headers"]
    first = _add_reschedule_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T9", "slot_business_ids": ["S1"]},
        declared_reason="周三要接孩子",
    )
    second = _add_reschedule_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T9", "slot_business_ids": []},
        declared_reason="还是周三",
    )
    _add_reschedule_event(
        scope_id,
        version_id,
        event_type="extra_class",
        payload={"course_business_id": "L1"},
    )

    run = client.post("/api/v1/memory/mining-runs", headers=headers)
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["engine"] == "deterministic"
    assert body["events_scanned"] == 3
    assert body["skipped_invalid"] == 0
    candidates = body["created"]
    assert len(candidates) == 1
    candidate = candidates[0]
    # 红线③：初始 probation + induced 来源 + 证据链指向调课事件。
    assert candidate["status"] == "probation"
    assert candidate["source"] == "induced_from_adjustment"
    assert candidate["subject_type"] == "teacher"
    assert candidate["subject_id"] == "T9"
    assert candidate["predicate"] == "avoid_slot"
    assert candidate["constraint"] == {"slot_ids": ["S1"]}
    assert set(candidate["evidence"]) == {first.id, second.id}
    assert candidate["provenance"]["engine"] == "deterministic"

    # 同请求/跨请求重复候选去重：同 subject+predicate+constraint 已存在则跳过。
    rerun = client.post("/api/v1/memory/mining-runs", headers=headers)
    assert rerun.status_code == 200, rerun.text
    assert rerun.json()["created"] == []
    assert rerun.json()["skipped_existing"] == 1

    probations = client.get(
        "/api/v1/memory/preferences", headers=headers, params={"status": "probation"}
    )
    assert [item["id"] for item in probations.json()] == [candidate["id"]]


def test_mining_uses_ai_channel_with_whitelist_validation(
    client: TestClient, memory_scope: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    scope_id = memory_scope["scope_id"]
    version_id = memory_scope["version_id"]
    headers = memory_scope["headers"]
    events = [
        _add_reschedule_event(
            scope_id,
            version_id,
            event_type="teacher_leave",
            payload={"teacher_business_id": "T9", "slot_business_ids": ["S1"]},
            declared_reason="家里有事",
        ),
        _add_reschedule_event(
            scope_id,
            version_id,
            event_type="teacher_leave",
            payload={"teacher_business_id": "T9", "slot_business_ids": ["S1"]},
        ),
    ]
    captured: dict[str, Any] = {}

    def fake_mine_preferences(
        self: AIService, views: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        captured["views"] = views
        valid_evidence = [str(item["id"]) for item in views][:2]
        return (
            [
                {
                    "subject_type": "teacher",
                    "subject_id": "T9",
                    "predicate": "avoid_slot",
                    "constraint": {"slot_ids": ["S1"]},
                    "evidence_ids": valid_evidence,
                    "rationale": "该教师反复在同时段请假",
                },
                {
                    # 证据引用不存在的事件 id：必须整体丢弃。
                    "subject_type": "teacher",
                    "subject_id": "T9",
                    "predicate": "prefer_slot",
                    "constraint": {},
                    "evidence_ids": ["not-an-event"],
                    "rationale": "坏证据",
                },
                {
                    # 主体没在事件里出现过：同样丢弃。
                    "subject_type": "classroom",
                    "subject_id": "R1",
                    "predicate": "avoid_room",
                    "constraint": {"room_ids": ["R1"]},
                    "evidence_ids": valid_evidence[:1],
                    "rationale": "凭空捏造",
                },
            ],
            {"total_tokens": 42},
        )

    monkeypatch.setattr(AIService, "mine_preferences", fake_mine_preferences)
    monkeypatch.setattr(
        AIService,
        "configuration_view",
        lambda self: {
            "configured": True,
            "source": "frontend",
            "provider": "openai_compatible",
            "base_url": "https://mock.invalid/v1",
            "api_key_configured": True,
            "model": "mock-model",
        },
    )

    run = client.post("/api/v1/memory/mining-runs", headers=headers)
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["engine"] == "ai"
    assert body["skipped_invalid"] == 2
    assert len(body["created"]) == 1
    candidate = body["created"][0]
    assert candidate["status"] == "probation"
    assert candidate["source"] == "induced_from_adjustment"
    assert candidate["provenance"]["model"] == "mock-model"
    assert candidate["provenance"]["usage"] == {"total_tokens": 42}
    assert set(candidate["evidence"]) == {events[0].id, events[1].id}
    # 事件原样进入模型输入，归因理由一并带上（消噪关键）。事件列表按时间
    # 倒序返回，同一秒内创建的两条不保证先后，这里只比较多重集合。
    reasons = sorted(str(item["declared_reason"]) for item in captured["views"])
    assert reasons == sorted(["家里有事", "None"])


def test_declared_reason_round_trips_through_event_table(
    client: TestClient, memory_scope: dict[str, Any]
) -> None:
    headers = memory_scope["headers"]
    response = client.post(
        "/api/v1/reschedule-events",
        headers=headers,
        json={
            "parent_schedule_id": memory_scope["version_id"],
            "event_type": "teacher_leave",
            "teacher_business_id": "T9",
            "description": "教师请假",
            "slot_business_ids": ["S1"],
            "declared_reason": "教师要求固定周三",
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["declared_reason"] == "教师要求固定周三"
    with SessionLocal() as db:
        event = db.get(RescheduleEvent, body["id"])
        assert event is not None
        assert event.declared_reason == "教师要求固定周三"


# ---------------------------------------------------------------- 编译与求解联动


def test_compile_preferences_decay_expiry_and_unsupported_predicate() -> None:
    confirmed = _add_entry(subject_id="T-C1", weight=100, confidence=0.8)
    unauthorized = _add_entry(subject_id="T-P1", status="probation", weight=100, confidence=0.8)
    trial = _add_entry(
        subject_id="T-P2", status="probation", trial_authorized=True, weight=100, confidence=0.8
    )
    _add_entry(subject_id="T-X1", valid_until=shanghai_now().date() - timedelta(days=1))
    _add_entry(subject_id="T-R1", status="rejected")
    _add_entry(subject_id="T-M1", predicate="max_daily_load")

    with SessionLocal() as db:
        rules = compile_preferences(db, "default")
    by_entry = {rule["memory_entry_id"]: rule for rule in rules}
    subjects = {rule["actor_ids"][0] for rule in rules}

    # weight × (confirmed?1.0:0.3) × confidence；确认条目全量权重。
    assert by_entry[confirmed.id]["weight"] == 80
    assert by_entry[confirmed.id]["memory_effective_weight"] == 80.0
    assert effective_weight(trial) == pytest.approx(24.0)
    # 三态拆分（MEM-C1）：未授权试用不进求解输入；授权试用以试用期衰减权重进入。
    assert unauthorized.id not in by_entry
    assert by_entry[trial.id]["weight"] == 24
    assert by_entry[trial.id]["hardness"] == "soft"
    assert by_entry[confirmed.id]["constraint_type"] == "forbidden_slot"
    assert by_entry[confirmed.id]["scope"] == {"slot_ids": ["S1"]}
    assert by_entry[confirmed.id]["business_id"] == f"MEMORY-{confirmed.id}"

    # 过期/拒绝/暂无求解路径的谓词不进目标函数。
    assert "T-X1" not in subjects
    assert "T-R1" not in subjects
    assert "T-M1" not in subjects

    payload: dict[str, Any] = {"rules": [{"business_id": "RL-1"}]}
    with SessionLocal() as db:
        payload["memory"] = compile_memory_state(db, "default")
        _attach_memory_preferences(payload, "default", db)
    assert payload["rules"][0]["business_id"] == "RL-1"
    assert any(rule.get("memory_entry_id") == confirmed.id for rule in payload["rules"])

    # 确定性统计的纯函数行为：同主体同类型 ≥2 次才有候选。
    views = [
        {
            "id": "e1",
            "event_type": "room_outage",
            "room_business_id": "R1",
            "slot_business_ids": [],
        },
        {
            "id": "e2",
            "event_type": "room_outage",
            "room_business_id": "R1",
            "slot_business_ids": [],
        },
    ]
    candidates = deterministic_preference_candidates(views)
    assert len(candidates) == 1
    assert candidates[0]["predicate"] == "avoid_room"
    assert candidates[0]["constraint"] == {"room_ids": ["R1"]}
    assert deterministic_preference_candidates(views[:1]) == []


def test_default_valid_until_matches_red_line() -> None:
    today = shanghai_now().date()
    assert default_valid_until(today) == today + timedelta(days=180)
    assert default_valid_until(date(2026, 1, 1)) == date(2026, 6, 30)


def test_solver_respects_compiled_preference() -> None:
    """小模型集成：教师回避时段的确认偏好会把课次推离该时段。"""
    entry = _add_entry(
        subject_id="T-MEM",
        constraint={"slot_ids": ["S-MON"]},
        weight=100,
        confidence=1.0,
        status="confirmed",
    )
    with SessionLocal() as db:
        memory_rules = compile_preferences(db, "default")
    preference_rule = next(rule for rule in memory_rules if rule["memory_entry_id"] == entry.id)

    base_payload: dict[str, Any] = {
        "teachers": [{"business_id": "T-MEM", "name": "记忆教师", "is_group": False}],
        "rooms": [
            {"business_id": "R1", "name": "教室1", "is_active": True},
            {"business_id": "R2", "name": "教室2", "is_active": True},
        ],
        "time_slots": [
            {
                "business_id": "S-MON",
                "weekday": "周一",
                "start_time": "08:30",
                "end_time": "11:30",
                "is_open": True,
            },
            {
                "business_id": "S-TUE",
                "weekday": "周二",
                "start_time": "08:30",
                "end_time": "11:30",
                "is_open": True,
            },
        ],
        "course_sessions": [
            {
                "id": "db-L1",
                "business_id": "L1",
                "class_business_id": "B1",
                "teacher_business_id": "T-MEM",
                "lesson_date": "2026-09-07",
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

    without_preference = solve_problem({**base_payload, "rules": []})
    assert without_preference["model_status"] in {"OPTIMAL", "FEASIBLE"}
    assert without_preference["assignments"], "基线模型应能排出课次"

    with_preference = solve_problem({**base_payload, "rules": [preference_rule]})
    assert with_preference["model_status"] in {"OPTIMAL", "FEASIBLE"}
    assert with_preference["assignments"], "偏好模型应能排出课次"
    assert with_preference["assignments"][0]["slot_business_id"] != "S-MON"
