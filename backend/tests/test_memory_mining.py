"""偏好挖掘的证据支持性校验 + 拒绝记忆与矛盾消解（MEM-C2）。

覆盖 docs/roadmap/02-agent-memory.md §6 第 3、4 组修正的「不会悄悄做错」系列：
- 李老师的事件不能支持张老师的候选（证据主体一致性）；
- 单证据候选被剔；约束引用非证据时段被剔；
- 同批被拒证据再挖掘不复现，带新证据允许重提且标注此前被拒原因；
- 临时公差/教师请假类不产生长期候选，教师要求类 ≥2 次才产候选；
- 矛盾三分支：new_replaces / time_sliced / conflict_flagged。
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
    PreferenceRejection,
    RescheduleEvent,
    Room,
    ScheduleVersion,
    SolverRun,
    Teacher,
    TimeSlot,
)
from app.services.ai import AIService
from app.services.memory_solver import (
    compile_memory_state,
    refresh_conflict_flags,
    resolve_conflicts_for_new_entry,
)
from app.timezone import shanghai_now


@pytest.fixture
def mining_scope(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """独立课表方案：一套主数据 + 一个可挂调课事件的父版本（求解被 stub）。"""
    monkeypatch.setattr(api, "enqueue_solver_run", lambda run_id: None)
    created = client.post(
        "/api/v1/schedule-sets",
        headers=auth_headers,
        json={"name": f"挖掘校验-{uuid4().hex[:8]}"},
    )
    assert created.status_code == 201, created.text
    scope_id = created.json()["id"]
    headers = {**auth_headers, "X-Schedule-Set-Id": scope_id}
    with SessionLocal() as db:
        campus = Campus(schedule_set_id=scope_id, business_id="C", name="挖掘校区")
        snapshot = DataSnapshot(
            schedule_set_id=scope_id, revision=1, checksum=uuid4().hex, payload={}
        )
        db.add_all([campus, snapshot])
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
            name="挖掘草稿",
            solver_run_id=run.id,
            status="draft",
        )
        db.add(version)
        db.commit()
    return {"headers": headers, "scope_id": scope_id, "version_id": version.id}


def _add_event(
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
        "predicate": "prefer_slot",
        "constraint": {"slot_ids": ["S1"]},
        "modality": "soft",
        "confidence": 0.8,
        "source": "explicit_stated",
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


def _mock_ai(monkeypatch: pytest.MonkeyPatch, candidates: list[dict[str, Any]]) -> None:
    """把 AI 通道指到固定输出，绕过真实网关。"""

    def fake_mine_preferences(
        self: AIService, views: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return candidates, {"total_tokens": 7}

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


# ---------------------------------------------------------------- 证据支持性校验


def test_teacher_evidence_cannot_support_other_teacher_candidate(
    client: TestClient, mining_scope: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """李老师的事件不能支持张老师的候选：证据事件主体必须与候选主体一致。"""
    scope_id = mining_scope["scope_id"]
    version_id = mining_scope["version_id"]
    li_first = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "LI", "slot_business_ids": ["S1"]},
        declared_reason="教师要求固定周一",
    )
    li_second = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "LI", "slot_business_ids": ["S1"]},
    )
    _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "ZHANG", "slot_business_ids": ["S2"]},
    )
    _mock_ai(
        monkeypatch,
        [
            # 全部证据都是李老师的事件，却给张老师提候选：整体剔除。
            {
                "subject_type": "teacher",
                "subject_id": "ZHANG",
                "predicate": "avoid_slot",
                "constraint": {"slot_ids": ["S1"]},
                "evidence_ids": [li_first.id, li_second.id],
                "rationale": "拿别人的事件凑数",
            },
            # 混入一条他师证据：同样剔除。
            {
                "subject_type": "teacher",
                "subject_id": "LI",
                "predicate": "avoid_slot",
                "constraint": {"slot_ids": ["S1"]},
                "evidence_ids": [li_first.id, "zhang-event"],
                "rationale": "坏证据",
            },
            # 合法候选：两条同主体证据 + 约束来自证据时段。
            {
                "subject_type": "teacher",
                "subject_id": "LI",
                "predicate": "avoid_slot",
                "constraint": {"slot_ids": ["S1"]},
                "evidence_ids": [li_first.id, li_second.id],
                "rationale": "李老师两次周一请假",
            },
        ],
    )

    run = client.post("/api/v1/memory/mining-runs", headers=mining_scope["headers"])
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["engine"] == "ai"
    assert body["skipped_invalid"] == 2
    assert len(body["created"]) == 1
    assert body["created"][0]["subject_id"] == "LI"
    assert body["created"][0]["predicate"] == "avoid_slot"


def test_single_evidence_and_non_evidence_constraint_are_rejected(
    client: TestClient, mining_scope: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """单证据候选被剔；约束引用了证据之外的时段被剔。"""
    scope_id = mining_scope["scope_id"]
    version_id = mining_scope["version_id"]
    first = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T9", "slot_business_ids": ["S1"]},
    )
    second = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T9", "slot_business_ids": ["S1"]},
    )
    _mock_ai(
        monkeypatch,
        [
            {
                # 只有 1 条证据：不足。
                "subject_type": "teacher",
                "subject_id": "T9",
                "predicate": "avoid_slot",
                "constraint": {"slot_ids": ["S1"]},
                "evidence_ids": [first.id],
                "rationale": "单条臆测",
            },
            {
                # 约束引用了证据事件里没出现过的时段：无中生有。
                "subject_type": "teacher",
                "subject_id": "T9",
                "predicate": "avoid_slot",
                "constraint": {"slot_ids": ["S-GHOST"]},
                "evidence_ids": [first.id, second.id],
                "rationale": "时段是编的",
            },
            {
                # 合法候选。
                "subject_type": "teacher",
                "subject_id": "T9",
                "predicate": "avoid_slot",
                "constraint": {"slot_ids": ["S1"]},
                "evidence_ids": [first.id, second.id],
                "rationale": "两次周一请假",
            },
        ],
    )

    run = client.post("/api/v1/memory/mining-runs", headers=mining_scope["headers"])
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["skipped_invalid"] == 2
    assert len(body["created"]) == 1
    assert body["created"][0]["constraint"] == {"slot_ids": ["S1"]}


# ---------------------------------------------------------------- 拒绝记忆与去重防复现


def test_rejected_evidence_blocks_remining_but_new_evidence_reopens(
    client: TestClient, mining_scope: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """同批证据被拒后不再复现；带新证据允许重提且卡上带被拒原因。"""
    scope_id = mining_scope["scope_id"]
    version_id = mining_scope["version_id"]
    headers = mining_scope["headers"]
    first = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T9", "slot_business_ids": ["S1"]},
    )
    second = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T9", "slot_business_ids": ["S1"]},
    )
    candidate_payload: dict[str, Any] = {
        "subject_type": "teacher",
        "subject_id": "T9",
        "predicate": "avoid_slot",
        "constraint": {"slot_ids": ["S1"]},
        "evidence_ids": [first.id, second.id],
        "rationale": "反复周一请假",
    }
    _mock_ai(monkeypatch, [dict(candidate_payload)])

    first_run = client.post("/api/v1/memory/mining-runs", headers=headers)
    assert first_run.status_code == 200, first_run.text
    assert len(first_run.json()["created"]) == 1
    entry_id = first_run.json()["created"][0]["id"]

    # 拒绝（带受控枚举原因）→ 拒绝记录落库。
    rejected = client.post(
        f"/api/v1/memory/preferences/{entry_id}/transition",
        headers=headers,
        json={
            "target_status": "rejected",
            "rejection_reason": "wrong_generalization",
            "reason": "他是临时调课，不是长期偏好",
        },
    )
    assert rejected.status_code == 200, rejected.text
    with SessionLocal() as db:
        record = db.query(PreferenceRejection).filter_by(schedule_set_id=scope_id).one()
        assert record.reason == "wrong_generalization"
        assert record.note == "他是临时调课，不是长期偏好"
        assert set(record.evidence) == {first.id, second.id}

    # 同批证据再挖掘：不复现。
    rerun = client.post("/api/v1/memory/mining-runs", headers=headers)
    assert rerun.status_code == 200, rerun.text
    assert rerun.json()["created"] == []
    assert rerun.json()["skipped_rejected"] == 1

    # 出现实质新证据：允许重提，候选标注此前被拒原因。
    third = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T9", "slot_business_ids": ["S1"]},
    )
    _mock_ai(
        monkeypatch,
        [{**candidate_payload, "evidence_ids": [first.id, second.id, third.id]}],
    )
    third_run = client.post("/api/v1/memory/mining-runs", headers=headers)
    assert third_run.status_code == 200, third_run.text
    created = third_run.json()["created"]
    assert len(created) == 1
    assert created[0]["provenance"]["previously_rejected"]["reason"] == "wrong_generalization"
    assert set(created[0]["evidence"]) == {first.id, second.id, third.id}


def test_rejection_reason_defaults_to_other_when_omitted(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """API 缺省拒绝原因按「其他」落库（前端必填由 UI 保证）。"""
    entry = _add_entry(
        schedule_set_id=mining_scope["scope_id"],
        status="probation",
        evidence=["evt-1", "evt-2"],
    )
    response = client.post(
        f"/api/v1/memory/preferences/{entry.id}/transition",
        headers=mining_scope["headers"],
        json={"target_status": "rejected"},
    )
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        record = (
            db.query(PreferenceRejection)
            .filter_by(schedule_set_id=mining_scope["scope_id"])
            .one()
        )
        assert record.reason == "other"
        assert set(record.evidence) == {"evt-1", "evt-2"}


# ---------------------------------------------------------------- 统计降级消噪


def test_deterministic_mining_noise_filters_by_declared_reason(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """临时公差/教师请假不出长期候选；教师要求 ≥2 次产候选；90 天窗口生效。"""
    scope_id = mining_scope["scope_id"]
    version_id = mining_scope["version_id"]
    headers = mining_scope["headers"]
    # 临时公差 ×2：不产生候选。
    _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T-TRIP", "slot_business_ids": ["S1"]},
        declared_reason="临时公差",
    )
    _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T-TRIP", "slot_business_ids": ["S1"]},
        declared_reason="临时公差",
    )
    # 教师请假（归因文本）×2：同样视为被迫/临时，不产生候选。
    _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T-SICK", "slot_business_ids": ["S1"]},
        declared_reason="教师请假",
    )
    _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T-SICK", "slot_business_ids": ["S1"]},
        declared_reason="教师请假",
    )
    # 教师要求 ×2：产生候选。
    request_first = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T-REQ", "slot_business_ids": ["S1"]},
        declared_reason="教师要求",
    )
    request_second = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T-REQ", "slot_business_ids": ["S1"]},
        declared_reason="教师要求",
    )
    # 超出 90 天窗口的旧事件：不进入挖掘范围。
    stale = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T-STALE", "slot_business_ids": ["S1"]},
        declared_reason="教师要求",
    )
    with SessionLocal() as db:
        stale_row = db.get(RescheduleEvent, stale.id)
        assert stale_row is not None
        stale_row.created_at = shanghai_now() - timedelta(days=100)
        db.commit()

    run = client.post("/api/v1/memory/mining-runs", headers=headers)
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["engine"] == "deterministic"
    assert body["events_scanned"] == 6  # 共 7 条事件，100 天前的旧事件被窗口过滤
    assert len(body["created"]) == 1
    candidate = body["created"][0]
    assert candidate["subject_id"] == "T-REQ"
    assert candidate["predicate"] == "avoid_slot"
    assert set(candidate["evidence"]) == {request_first.id, request_second.id}
    # 文案（MEM-C2 修正 3）：建议口吻而不是断言偏好。
    assert "发现 2 次相似调整" in candidate["provenance"]["rationale"]
    assert "建议教务确认" in candidate["provenance"]["rationale"]


# ---------------------------------------------------------------- 矛盾消解三分支


def _new_entry(**overrides: Any) -> PreferenceEntry:
    data: dict[str, Any] = {
        "schedule_set_id": "default",
        "subject_type": "teacher",
        "subject_id": "T-CONF",
        "predicate": "prefer_slot",
        "constraint": {"slot_ids": ["S1"]},
        "modality": "soft",
        "confidence": 0.5,
        "source": "induced_from_adjustment",
        "status": "probation",
        "weight": 40,
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


def test_conflict_resolution_new_replaces_branch() -> None:
    """窗口不重叠且不相邻：旧条目 expired，provenance 记 superseded_by。"""
    old = _add_entry(
        subject_id="T-REPL",
        valid_from=date(2026, 1, 1),
        valid_until=date(2026, 3, 1),
    )
    new = _new_entry(
        subject_id="T-REPL",
        valid_from=date(2026, 6, 1),
        valid_until=date(2026, 12, 31),
    )
    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        new_row = db.get(PreferenceEntry, new.id)
        assert old_row is not None and new_row is not None
        branch = resolve_conflicts_for_new_entry(db, new_row)
        db.commit()
    assert branch == "new_replaces"
    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        assert old_row is not None
        assert old_row.status == "expired"
        assert old_row.provenance["superseded_by"] == new.id


def test_conflict_resolution_time_sliced_branch() -> None:
    """相邻不重叠（旧窗口止于新窗口前一日）：并存，互不打扰。"""
    today = shanghai_now().date()
    old = _add_entry(
        subject_id="T-SLICE",
        valid_from=today - timedelta(days=90),
        valid_until=today - timedelta(days=1),
    )
    new = _new_entry(
        subject_id="T-SLICE",
        valid_from=today,
        valid_until=today + timedelta(days=180),
    )
    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        new_row = db.get(PreferenceEntry, new.id)
        assert old_row is not None and new_row is not None
        branch = resolve_conflicts_for_new_entry(db, new_row)
        db.commit()
    assert branch == "time_sliced"
    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        new_row = db.get(PreferenceEntry, new.id)
        assert old_row is not None and new_row is not None
        assert old_row.status == "confirmed"
        assert old_row.conflict is False
        assert new_row.status == "probation"
        assert new_row.conflict is False


def test_conflict_resolution_conflict_flagged_branch_and_compile_exclusion() -> None:
    """窗口重叠且约束互斥：旧条目打 conflict 标，编译期不进求解输入；
    一侧退出活跃集后标记自动解除。"""
    today = shanghai_now().date()
    old = _add_entry(
        subject_id="T-FLAG",
        constraint={"slot_ids": ["S1"]},
        valid_from=today - timedelta(days=10),
        valid_until=today + timedelta(days=60),
    )
    new = _new_entry(
        subject_id="T-FLAG",
        constraint={"slot_ids": ["S2"]},
        valid_from=today,
        valid_until=today + timedelta(days=180),
    )
    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        new_row = db.get(PreferenceEntry, new.id)
        assert old_row is not None and new_row is not None
        branch = resolve_conflicts_for_new_entry(db, new_row)
        db.commit()
    assert branch == "conflict_flagged"
    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        new_row = db.get(PreferenceEntry, new.id)
        assert old_row is not None and new_row is not None
        assert old_row.conflict is True
        assert old_row.provenance["conflict_with"] == [new.id]
        assert new_row.conflict is False
        assert new_row.provenance["conflict_with"] == [old.id]

        # compile 侧（最小处理）：conflict 条目不进求解输入，outcome 显式体现。
        state = compile_memory_state(db, "default")
        outcomes = {item["entry_id"]: item for item in state["outcomes"]}
        assert outcomes[old.id]["outcome"] == "conflict_unresolved"
        assert outcomes[new.id]["outcome"] == "not_authorized"
        assert all(rule["memory_entry_id"] != old.id for rule in state["compiled_rules"])

    # 教务一键「以新替旧」：旧条目退出后新条目的 conflict 标自动解除。
    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        assert old_row is not None
        old_row.status = "expired"
        refresh_conflict_flags(
            db, old_row.schedule_set_id, old_row.subject_type, old_row.subject_id, old_row.predicate
        )
        db.commit()
    with SessionLocal() as db:
        new_row = db.get(PreferenceEntry, new.id)
        assert new_row is not None
        assert new_row.conflict is False
        state = compile_memory_state(db, "default")
        outcomes = {item["entry_id"]: item for item in state["outcomes"]}
        assert outcomes[new.id]["outcome"] == "not_authorized"  # probation 未授权，语义不变
