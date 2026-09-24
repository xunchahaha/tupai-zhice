"""偏好挖掘的证据支持性校验 + 拒绝记忆与矛盾消解（MEM-C2/MEM-D1）。

覆盖 docs/roadmap/02-agent-memory.md §6 第 3、4 组修正与 §7 D1/D2/D3 的
「不会悄悄做错」系列：
- 李老师的事件不能支持张老师的候选（证据主体一致性）；
- 单证据候选被剔；约束引用非证据时段被剔；
- 同批被拒证据再挖掘不复现，带新证据允许重提且标注此前被拒原因；
- 临时公差/教师请假类与已取消的调课事件不进学习集（AI 与统计同一前置筛选）；
- 矛盾三分支：new_replaces / time_sliced / conflict_flagged；
- MEM-D1 + MEM-E1a：conflict 是提出方侧 proposed_conflict 标，提出方按授权状态
  判定（未授权条目永远是提出方，同授权级别内取较新者）；conflict 的编译排除
  只作用于未授权条目，已授权条目带标记照常编译；冲突裁决三动作（保留旧弃新/
  以新替旧/授权试用）全部走既有 transition 端点；
- MEM-D1 D2：候选 constraint 自带日期窗口时条目级默认有效期取最小覆盖；
  编辑有效期后冲突标重算；
- MEM-E1c：冲突配对与编译共用实际生效窗口（约束∩条目级）口径；
- MEM-E1b：学习集要求「已产生结果版本且未被后续放弃」的接受依据。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

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
    learning_basis_events,
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
    status: str = "candidate_ready",
    candidate_status: str | None = None,
) -> RescheduleEvent:
    """落一条调课事件。MEM-E1b 接受依据：candidate_ready 事件默认挂一个已发布
    的候选版本（candidate_schedule_id 非空 = 已产生结果版本且未被后续放弃）；
    candidate_status="draft" 表示候选停留在待发布（未被接受）；pending /
    candidate_discarded 不挂候选，与真实链路一致（创建时无候选；删除候选版本时
    api.delete_schedule 会同步置空该列并置 candidate_discarded）。"""
    with SessionLocal() as db:
        candidate_id: str | None = None
        if status == "candidate_ready":
            snapshot_id = db.scalar(
                select(DataSnapshot.id).where(DataSnapshot.schedule_set_id == scope_id)
            )
            assert snapshot_id is not None
            next_no = int(
                db.scalar(
                    select(func.max(ScheduleVersion.version_no)).where(
                        ScheduleVersion.schedule_set_id == scope_id
                    )
                )
                or 0
            ) + 1
            run = SolverRun(
                schedule_set_id=scope_id,
                snapshot_id=snapshot_id,
                status="completed",
                request_payload={},
            )
            db.add(run)
            db.flush()
            candidate = ScheduleVersion(
                schedule_set_id=scope_id,
                version_no=next_no,
                name=f"调课候选 V{next_no}",
                solver_run_id=run.id,
                status=candidate_status or "published",
            )
            db.add(candidate)
            db.flush()
            candidate_id = candidate.id
        event = RescheduleEvent(
            schedule_set_id=scope_id,
            event_type=event_type,
            description=f"{event_type} 事件",
            declared_reason=declared_reason,
            payload=payload,
            status=status,
            parent_schedule_id=version_id,
            candidate_schedule_id=candidate_id,
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


def _add_scoped_entry(scope_id: str, **overrides: Any) -> PreferenceEntry:
    """在指定方案内落一条偏好（裁决类 API 测试用，与 default 方案隔离）。"""
    data: dict[str, Any] = {
        "schedule_set_id": scope_id,
        "subject_type": "teacher",
        "subject_id": "T-D1",
        "predicate": "prefer_slot",
        "constraint": {"slot_ids": ["S1"]},
        "modality": "soft",
        "confidence": 1.0,
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


def _seed_conflict_pair(scope_id: str, subject: str) -> tuple[PreferenceEntry, PreferenceEntry]:
    """同一方案内造一对「旧 confirmed + 未授权候选」的互斥偏好（标记落候选侧）。"""
    old = _add_scoped_entry(scope_id, subject_id=subject, constraint={"slot_ids": ["S1"]})
    candidate = _add_scoped_entry(
        scope_id,
        subject_id=subject,
        status="probation",
        source="induced_from_adjustment",
        confidence=0.5,
        weight=40,
        constraint={"slot_ids": ["S2"]},
    )
    with SessionLocal() as db:
        cand_row = db.get(PreferenceEntry, candidate.id)
        assert cand_row is not None
        branch = resolve_conflicts_for_new_entry(db, cand_row)
        db.commit()
    assert branch == "conflict_flagged"
    return old, candidate


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
    """临时公差/教师请假不出长期候选；教师要求 ≥2 次产候选；90 天窗口生效。

    MEM-D1 D3：被迫类事件在公共前置筛选（learning_basis_events）就已出局，
    不再进入统计输入——events_scanned 只统计可学习事件。"""
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
    # 共 7 条事件：100 天前的旧事件被时间窗过滤；T-TRIP（临时公差）×2 与
    # T-SICK（教师请假）×2 被可学习事件前置筛选取剔——只剩 T-REQ ×2。
    assert body["events_scanned"] == 2
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
    """新条目已获授权且窗口不重叠也不相邻：旧条目 expired，provenance 记
    superseded_by；未授权候选与旧条目窗口错开时并存不动（MEM-D1）。"""
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
    # 候选（probation 未授权）不得让旧条目失效：未经批准不改变依据（MEM-D1）。
    assert branch == "coexist"
    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        assert old_row is not None
        assert old_row.status == "confirmed"
        assert old_row.provenance.get("superseded_by") is None

    # 授权后的新条目（confirmed）窗口错开：才发生替代，链路保留审计。
    successor = _new_entry(
        subject_id="T-REPL",
        status="confirmed",
        source="explicit_stated",
        valid_from=date(2026, 6, 1),
        valid_until=date(2026, 12, 31),
    )
    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        succ_row = db.get(PreferenceEntry, successor.id)
        assert old_row is not None and succ_row is not None
        branch = resolve_conflicts_for_new_entry(db, succ_row)
        db.commit()
    assert branch == "new_replaces"
    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        assert old_row is not None
        assert old_row.status == "expired"
        assert old_row.provenance["superseded_by"] == successor.id


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


def test_conflict_flag_lands_on_candidate_only_and_old_entry_still_compiles() -> None:
    """MEM-D1 核心验收：未授权冲突候选旁，旧 confirmed 条目照常编译进求解输入。

    conflict 是候选侧 proposed_conflict 标：只落在提出方（候选）上；旧条目
    outcome=applied 且 compiled_rules 含它。候选一侧退出活跃集后标记自动解除。"""
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
        # proposed_conflict 只落在候选侧；旧条目状态与编译参与度都不变。
        assert old_row.status == "confirmed"
        assert old_row.conflict is False
        assert new_row.conflict is True
        # provenance 互记对方 id，仅供审计与前端定位，不改变编译。
        assert old_row.provenance["conflict_with"] == [new.id]
        assert new_row.provenance["conflict_with"] == [old.id]

        # 编译侧：旧条目照常 applied 且进 compiled_rules；未授权候选带
        # proposed_conflict 标 → conflict_unresolved（MEM-E1a：conflict 的编译
        # 排除只作用于未授权条目，这里标出具体原因）。
        state = compile_memory_state(db, "default")
        outcomes = {item["entry_id"]: item for item in state["outcomes"]}
        assert outcomes[old.id]["outcome"] == "applied"
        assert outcomes[new.id]["outcome"] == "conflict_unresolved"
        assert any(rule["memory_entry_id"] == old.id for rule in state["compiled_rules"])
        assert all(rule["memory_entry_id"] != new.id for rule in state["compiled_rules"])

    # 教务裁决后（这里直接让旧条目退出活跃集）候选的 proposed_conflict 标自动解除。
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
        # 标清了以后，未授权候选回到普通 not_authorized 语义。
        assert outcomes[new.id]["outcome"] == "not_authorized"


def test_authorized_conflict_proposer_still_compiles() -> None:
    """MEM-E1a：已授权条目（confirmed / 授权试用）带 proposed_conflict 标记时
    编译参与度不受影响——conflict 标记的编译排除只作用于未授权条目，未经显式
    裁决不改变已生效依据，求解仍按现值执行；标记保留供教务 amber 徽标裁决。"""
    today = shanghai_now().date()
    old = _add_entry(
        subject_id="T-TRIAL-FLAG",
        constraint={"slot_ids": ["S1"]},
        valid_from=today - timedelta(days=10),
        valid_until=today + timedelta(days=60),
    )
    new = _new_entry(
        subject_id="T-TRIAL-FLAG",
        constraint={"slot_ids": ["S2"]},
        valid_from=today,
        valid_until=today + timedelta(days=180),
    )
    with SessionLocal() as db:
        new_row = db.get(PreferenceEntry, new.id)
        assert new_row is not None
        new_row.trial_authorized = True
        new_row.trial_until = today + timedelta(days=30)
        assert resolve_conflicts_for_new_entry(db, new_row) == "conflict_flagged"
        db.commit()
    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        new_row = db.get(PreferenceEntry, new.id)
        assert old_row is not None and new_row is not None
        # 双方都已授权：标记落较新者（提出方），但两侧照常编译。
        assert new_row.conflict is True
        assert old_row.conflict is False
        state = compile_memory_state(db, "default")
        outcomes = {item["entry_id"]: item for item in state["outcomes"]}
        assert outcomes[old.id]["outcome"] == "applied"
        assert outcomes[new.id]["outcome"] == "applied"
        assert "存在未裁决冲突提议" in outcomes[new.id]["detail"]
        assert any(rule["memory_entry_id"] == old.id for rule in state["compiled_rules"])
        assert any(rule["memory_entry_id"] == new.id for rule in state["compiled_rules"])


def test_conflict_flag_lands_on_earlier_unauthorized_candidate_after_edit(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """MEM-E1a 核心回归：较早创建的未授权候选 P 被编辑成与较晚 confirmed C 互斥
    后，proposed_conflict 标记必须落 P 侧（未授权条目永远是提出方，不按
    created_at 猜），C 照常 applied 且在 compiled_rules——旧实现按创建时间把
    标记落到 C 上，让已确认偏好再次被排除。"""
    scope_id = mining_scope["scope_id"]
    headers = mining_scope["headers"]
    earlier = _add_scoped_entry(
        scope_id,
        subject_id="T-E1A-EDIT",
        status="probation",
        source="induced_from_adjustment",
        confidence=0.5,
        weight=40,
        constraint={"slot_ids": ["S1"]},
        created_at=shanghai_now() - timedelta(minutes=5),
    )
    later = _add_scoped_entry(
        scope_id,
        subject_id="T-E1A-EDIT",
        constraint={"slot_ids": ["S1"]},
    )
    # 编辑较早候选成 S2：与 C（S1）互斥（prefer_slot 目标集不相交）。
    patched = client.patch(
        f"/api/v1/memory/preferences/{earlier.id}",
        headers=headers,
        json={"constraint": {"slot_ids": ["S2"]}},
    )
    assert patched.status_code == 200, patched.text

    with SessionLocal() as db:
        candidate = db.get(PreferenceEntry, earlier.id)
        confirmed = db.get(PreferenceEntry, later.id)
        assert candidate is not None and confirmed is not None
        # 提出方 = 未授权候选 P（尽管它创建得更早）；C 不带标记、状态不变。
        assert candidate.conflict is True
        assert confirmed.conflict is False
        assert confirmed.status == "confirmed"
        assert str(candidate.id) in {
            str(item) for item in confirmed.provenance.get("conflict_with") or []
        }
        state = compile_memory_state(db, scope_id)
    outcomes = {item["entry_id"]: item for item in state["outcomes"]}
    assert outcomes[later.id]["outcome"] == "applied"
    assert outcomes[earlier.id]["outcome"] == "conflict_unresolved"
    assert any(rule["memory_entry_id"] == later.id for rule in state["compiled_rules"])
    assert all(rule["memory_entry_id"] != earlier.id for rule in state["compiled_rules"])


def test_conflict_flag_lands_on_newer_when_both_unauthorized(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """MEM-E1a 同级兜底：双方都未授权时，标记落较新者（创建时间仅在同级内
    作提出方归属）。"""
    scope_id = mining_scope["scope_id"]
    older = _add_scoped_entry(
        scope_id,
        subject_id="T-E1A-BOTH",
        status="probation",
        source="induced_from_adjustment",
        confidence=0.5,
        weight=40,
        constraint={"slot_ids": ["S1"]},
        created_at=shanghai_now() - timedelta(minutes=5),
    )
    newer = _add_scoped_entry(
        scope_id,
        subject_id="T-E1A-BOTH",
        status="probation",
        source="induced_from_adjustment",
        confidence=0.5,
        weight=40,
        constraint={"slot_ids": ["S2"]},
    )
    with SessionLocal() as db:
        newer_row = db.get(PreferenceEntry, newer.id)
        assert newer_row is not None
        assert resolve_conflicts_for_new_entry(db, newer_row) == "conflict_flagged"
        db.commit()
    with SessionLocal() as db:
        older_row = db.get(PreferenceEntry, older.id)
        newer_row = db.get(PreferenceEntry, newer.id)
        assert older_row is not None and newer_row is not None
        assert newer_row.conflict is True
        assert older_row.conflict is False


# ---------------------------------------------------------------- 冲突裁决三动作（MEM-D1）


def test_adjudication_keep_old_rejects_candidate(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """保留旧弃新：候选 transition 到 rejected，旧条目编译参与度全程不变。"""
    scope_id = mining_scope["scope_id"]
    headers = mining_scope["headers"]
    old, candidate = _seed_conflict_pair(scope_id, "T-ADJ-KEEP")

    # 裁决前：旧条目 applied 且在 compiled_rules 里；候选带未裁决冲突提议
    # （MEM-E1a：未授权 + 标记 → conflict_unresolved，本就不进求解输入）。
    with SessionLocal() as db:
        state = compile_memory_state(db, scope_id)
        outcomes = {item["entry_id"]: item["outcome"] for item in state["outcomes"]}
        assert outcomes[old.id] == "applied"
        assert any(rule["memory_entry_id"] == old.id for rule in state["compiled_rules"])
        assert outcomes[candidate.id] == "conflict_unresolved"

    rejected = client.post(
        f"/api/v1/memory/preferences/{candidate.id}/transition",
        headers=headers,
        json={"target_status": "rejected", "rejection_reason": "wrong_generalization"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        cand_row = db.get(PreferenceEntry, candidate.id)
        assert old_row is not None and cand_row is not None
        assert old_row.status == "confirmed"
        assert old_row.conflict is False
        assert cand_row.conflict is False
        state = compile_memory_state(db, scope_id)
        outcomes = {item["entry_id"]: item["outcome"] for item in state["outcomes"]}
        assert outcomes[old.id] == "applied"
        assert any(rule["memory_entry_id"] == old.id for rule in state["compiled_rules"])


def test_adjudication_replace_old_switches_only_after_decision(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """以新替旧：切换只发生在显式裁决后——旧条目 transition 到 expired（带
    supersedes 审计链）、候选 transition 到 confirmed，此后编译才切换。"""
    scope_id = mining_scope["scope_id"]
    headers = mining_scope["headers"]
    old, candidate = _seed_conflict_pair(scope_id, "T-ADJ-REPLACE")

    # 裁决前：旧条目仍在编译输入里。
    with SessionLocal() as db:
        state = compile_memory_state(db, scope_id)
        assert any(rule["memory_entry_id"] == old.id for rule in state["compiled_rules"])
        assert all(rule["memory_entry_id"] != candidate.id for rule in state["compiled_rules"])

    expired = client.post(
        f"/api/v1/memory/preferences/{old.id}/transition",
        headers=headers,
        json={"target_status": "expired", "supersedes": candidate.id},
    )
    assert expired.status_code == 200, expired.text
    confirmed = client.post(
        f"/api/v1/memory/preferences/{candidate.id}/transition",
        headers=headers,
        json={"target_status": "confirmed"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        assert old_row is not None
        assert old_row.status == "expired"
        assert old_row.provenance["superseded_by"] == candidate.id
        state = compile_memory_state(db, scope_id)
        outcomes = {item["entry_id"]: item["outcome"] for item in state["outcomes"]}
        # 已失效条目不再进编译视野（outcomes 只含 confirmed/probation），
        # 编译输入里只剩确认后的候选——切换完成。
        assert old.id not in outcomes
        assert outcomes[candidate.id] == "applied"
        assert [
            rule["memory_entry_id"] for rule in state["compiled_rules"]
        ] == [candidate.id]


def test_adjudication_authorize_trial_keeps_both_entries(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """授权试用：旧新并存——旧条目全权重编译，候选以试用期衰减权重进入，
    proposed_conflict 标被显式裁决清除且不再重打。"""
    scope_id = mining_scope["scope_id"]
    headers = mining_scope["headers"]
    old, candidate = _seed_conflict_pair(scope_id, "T-ADJ-TRIAL")
    assert old.weight == 100 and old.confidence == 1.0

    authorized = client.post(
        f"/api/v1/memory/preferences/{candidate.id}/transition",
        headers=headers,
        json={"action": "authorize_trial"},
    )
    assert authorized.status_code == 200, authorized.text
    body = authorized.json()
    assert body["status"] == "probation"
    assert body["conflict"] is False

    with SessionLocal() as db:
        cand_row = db.get(PreferenceEntry, candidate.id)
        assert cand_row is not None
        assert cand_row.conflict is False
        assert cand_row.provenance["conflict_resolved_with"] == [old.id]
        assert cand_row.provenance["conflict_resolved_mode"] == "authorize_trial"
        # 重算不会给已裁决共存的组合重打标。
        refresh_conflict_flags(
            db,
            cand_row.schedule_set_id,
            cand_row.subject_type,
            cand_row.subject_id,
            cand_row.predicate,
        )
        db.commit()
        assert cand_row.conflict is False
        state = compile_memory_state(db, scope_id)
    by_entry = {rule["memory_entry_id"]: rule for rule in state["compiled_rules"]}
    assert by_entry[old.id]["weight"] == 100  # 旧条目全权
    assert by_entry[candidate.id]["weight"] == 6  # 40 × 0.3（试用衰减） × 0.5（置信度）
    outcomes = {item["entry_id"]: item["outcome"] for item in state["outcomes"]}
    assert outcomes[old.id] == "applied"
    assert outcomes[candidate.id] == "applied"


def test_editing_validity_recomputes_conflict_flags(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """编辑条目有效期后冲突标重算（MEM-D1 D2）：窗口由重叠改错开 → 候选清标。"""
    scope_id = mining_scope["scope_id"]
    headers = mining_scope["headers"]
    today = shanghai_now().date()
    old = _add_scoped_entry(
        scope_id,
        subject_id="T-ADJ-EDIT",
        constraint={"slot_ids": ["S1"]},
        valid_from=today - timedelta(days=10),
        valid_until=today + timedelta(days=60),
    )
    candidate = _add_scoped_entry(
        scope_id,
        subject_id="T-ADJ-EDIT",
        status="probation",
        source="induced_from_adjustment",
        confidence=0.5,
        weight=40,
        constraint={"slot_ids": ["S2"]},
        valid_from=today,
        valid_until=today + timedelta(days=180),
    )
    with SessionLocal() as db:
        cand_row = db.get(PreferenceEntry, candidate.id)
        assert cand_row is not None
        assert resolve_conflicts_for_new_entry(db, cand_row) == "conflict_flagged"
        db.commit()
        assert cand_row.conflict is True

    # 把旧条目有效期缩到候选窗口之前 → 两窗口错开 → 候选的 proposed_conflict 清标。
    patched = client.patch(
        f"/api/v1/memory/preferences/{old.id}",
        headers=headers,
        json={"valid_until": (today - timedelta(days=1)).isoformat()},
    )
    assert patched.status_code == 200, patched.text

    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        cand_row = db.get(PreferenceEntry, candidate.id)
        assert old_row is not None and cand_row is not None
        assert cand_row.conflict is False
        assert old_row.conflict is False


# ------------------------------------------------- 「以新替旧」原子裁决（MEM-E3）


def test_adjudicate_replace_switches_entries_in_one_request(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """MEM-E3：单次请求完成「以新替旧」——同一事务内旧条目 expired+superseded_by、
    候选 confirmed+supersedes、冲突清标；编译输入随之切换。"""
    scope_id = mining_scope["scope_id"]
    headers = mining_scope["headers"]
    old, candidate = _seed_conflict_pair(scope_id, "T-ADJ-REPLACE-ATOM")

    replaced = client.post(
        f"/api/v1/memory/preferences/{candidate.id}/adjudicate-replace",
        headers=headers,
        json={"rejection_reason": "preference_changed"},
    )
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    assert body["detail"] == "replaced"
    assert body["candidate"]["status"] == "confirmed"
    assert body["old_entry"]["status"] == "expired"

    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        cand_row = db.get(PreferenceEntry, candidate.id)
        assert old_row is not None and cand_row is not None
        assert old_row.status == "expired"
        assert old_row.provenance["superseded_by"] == candidate.id
        assert old_row.provenance["superseded_at"]
        assert old_row.provenance["rejection_reason"] == "preference_changed"
        assert old_row.conflict is False
        assert cand_row.status == "confirmed"
        assert cand_row.provenance["supersedes"] == old.id
        assert cand_row.conflict is False
        # 编译输入只剩候选：切换完成，旧的已退场且新的已生效，没有中间态。
        state = compile_memory_state(db, scope_id)
        outcomes = {item["entry_id"]: item["outcome"] for item in state["outcomes"]}
        assert old.id not in outcomes
        assert outcomes[candidate.id] == "applied"
        assert [rule["memory_entry_id"] for rule in state["compiled_rules"]] == [candidate.id]


def test_adjudicate_replace_rolls_back_when_later_step_fails(
    client: TestClient, mining_scope: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEM-E3：事务后段失败时整体回滚——旧条目保持 confirmed 原状（没有
    superseded_by 审计链），候选保持 probation 带标，不出现「旧的已退场、
    新的没生效」的中间态。"""
    scope_id = mining_scope["scope_id"]
    headers = mining_scope["headers"]
    old, candidate = _seed_conflict_pair(scope_id, "T-ADJ-REPLACE-ROLLBACK")

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("模拟事务后段失败")

    monkeypatch.setattr(api, "refresh_conflict_flags", _explode)
    with pytest.raises(RuntimeError, match="模拟事务后段失败"):
        client.post(
            f"/api/v1/memory/preferences/{candidate.id}/adjudicate-replace",
            headers=headers,
            json={},
        )

    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        cand_row = db.get(PreferenceEntry, candidate.id)
        assert old_row is not None and cand_row is not None
        # 回滚生效：两端都保持裁决前的原状。
        assert old_row.status == "confirmed"
        assert "superseded_by" not in (old_row.provenance or {})
        assert cand_row.status == "probation"
        assert "supersedes" not in (cand_row.provenance or {})
        assert cand_row.conflict is True


def test_adjudicate_replace_is_idempotent_on_replay(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """MEM-E3：重复调用幂等——已完成过的裁决原样返回（detail=already_applied），
    不做二次变更。"""
    scope_id = mining_scope["scope_id"]
    headers = mining_scope["headers"]
    old, candidate = _seed_conflict_pair(scope_id, "T-ADJ-REPLACE-REPLAY")

    first = client.post(
        f"/api/v1/memory/preferences/{candidate.id}/adjudicate-replace", headers=headers, json={}
    )
    assert first.status_code == 200, first.text
    assert first.json()["detail"] == "replaced"

    second = client.post(
        f"/api/v1/memory/preferences/{candidate.id}/adjudicate-replace", headers=headers, json={}
    )
    assert second.status_code == 200, second.text
    assert second.json()["detail"] == "already_applied"
    assert second.json()["candidate"]["status"] == "confirmed"
    assert second.json()["old_entry"]["status"] == "expired"

    with SessionLocal() as db:
        old_row = db.get(PreferenceEntry, old.id)
        cand_row = db.get(PreferenceEntry, candidate.id)
        assert old_row is not None and cand_row is not None
        assert old_row.status == "expired"
        assert cand_row.status == "confirmed"
        assert cand_row.provenance["supersedes"] == old.id


def test_adjudicate_replace_without_counterpart_is_rejected(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """MEM-E3：候选没有活跃冲突对端 → 422，不产生任何状态变更。"""
    scope_id = mining_scope["scope_id"]
    headers = mining_scope["headers"]
    lonely = _add_scoped_entry(
        scope_id,
        subject_id="T-ADJ-REPLACE-LONELY",
        status="probation",
        source="induced_from_adjustment",
        confidence=0.5,
        weight=40,
        constraint={"slot_ids": ["S2"]},
    )

    rejected = client.post(
        f"/api/v1/memory/preferences/{lonely.id}/adjudicate-replace",
        headers=headers,
        json={},
    )
    assert rejected.status_code == 422, rejected.text
    assert "冲突对端" in rejected.json()["detail"]

    with SessionLocal() as db:
        row = db.get(PreferenceEntry, lonely.id)
        assert row is not None
        assert row.status == "probation"


def test_adjudicate_replace_with_multiple_counterparts_requires_explicit_old(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """MEM-E3：候选与多个活跃条目互指冲突 → 不显式指定 old_entry_id 时 422；
    显式指定后只替换被点名的那条。"""
    scope_id = mining_scope["scope_id"]
    headers = mining_scope["headers"]
    subject = "T-ADJ-REPLACE-MULTI"
    old_primary = _add_scoped_entry(
        scope_id, subject_id=subject, constraint={"slot_ids": ["S1"]}
    )
    old_secondary = _add_scoped_entry(
        scope_id,
        subject_id=subject,
        constraint={"slot_ids": ["S2"]},
        provenance={},
    )
    candidate = _add_scoped_entry(
        scope_id,
        subject_id=subject,
        status="probation",
        source="induced_from_adjustment",
        confidence=0.5,
        weight=40,
        constraint={"slot_ids": ["S1", "S2"]},
    )
    with SessionLocal() as db:
        cand_row = db.get(PreferenceEntry, candidate.id)
        primary_row = db.get(PreferenceEntry, old_primary.id)
        secondary_row = db.get(PreferenceEntry, old_secondary.id)
        assert cand_row is not None and primary_row is not None and secondary_row is not None
        cand_row.provenance = {
            **(cand_row.provenance or {}),
            "conflict_with": [old_primary.id, old_secondary.id],
        }
        primary_row.provenance = {
            **(primary_row.provenance or {}),
            "conflict_with": [candidate.id],
        }
        secondary_row.provenance = {
            **(secondary_row.provenance or {}),
            "conflict_with": [candidate.id],
        }
        db.commit()

    ambiguous = client.post(
        f"/api/v1/memory/preferences/{candidate.id}/adjudicate-replace", headers=headers, json={}
    )
    assert ambiguous.status_code == 422, ambiguous.text
    assert "old_entry_id" in ambiguous.json()["detail"]

    bogus = client.post(
        f"/api/v1/memory/preferences/{candidate.id}/adjudicate-replace",
        headers=headers,
        json={"old_entry_id": "not-a-counterpart"},
    )
    assert bogus.status_code == 422, bogus.text

    explicit = client.post(
        f"/api/v1/memory/preferences/{candidate.id}/adjudicate-replace",
        headers=headers,
        json={"old_entry_id": old_primary.id},
    )
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["detail"] == "replaced"

    with SessionLocal() as db:
        primary_row = db.get(PreferenceEntry, old_primary.id)
        secondary_row = db.get(PreferenceEntry, old_secondary.id)
        assert primary_row is not None and secondary_row is not None
        assert primary_row.status == "expired"
        assert primary_row.provenance["superseded_by"] == candidate.id
        # 未被点名的那条保持原状。
        assert secondary_row.status == "confirmed"


def test_adjudicate_replace_permission_matrix(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """MEM-E3 权限矩阵：未登录 401；方案 approver 403；方案 scheduler 放行。"""
    suffix = uuid4().hex[:8]
    created = client.post(
        "/api/v1/schedule-sets", headers=auth_headers, json={"name": f"裁决权限-{suffix}"}
    )
    assert created.status_code == 201, created.text
    scope_id = created.json()["id"]

    users: dict[str, str] = {}
    for role in ("scheduler", "approver"):
        username = f"{role}_{suffix}"
        member = client.post(
            "/api/v1/users",
            headers=auth_headers,
            json={"username": username, "password": "scope-policy-2026", "role": role},
        )
        assert member.status_code == 201, member.text
        users[role] = member.json()["id"]
        grant = client.put(
            f"/api/v1/schedule-sets/{scope_id}/members/{users[role]}",
            headers=auth_headers,
            json={"user_id": users[role], "access_role": role},
        )
        assert grant.status_code == 200, grant.text

    tokens = {
        role: client.post(
            "/api/v1/auth/token",
            data={"username": f"{role}_{suffix}", "password": "scope-policy-2026"},
        ).json()["access_token"]
        for role in ("scheduler", "approver")
    }

    old, candidate = _seed_conflict_pair(scope_id, "T-ADJ-REPLACE-PERM")
    url = f"/api/v1/memory/preferences/{candidate.id}/adjudicate-replace"

    anonymous = client.post(url, json={})
    assert anonymous.status_code == 401, anonymous.text

    approver = client.post(
        url,
        headers={"Authorization": f"Bearer {tokens['approver']}", "X-Schedule-Set-Id": scope_id},
        json={},
    )
    assert approver.status_code == 403, approver.text

    scheduler = client.post(
        url,
        headers={"Authorization": f"Bearer {tokens['scheduler']}", "X-Schedule-Set-Id": scope_id},
        json={"old_entry_id": old.id},
    )
    assert scheduler.status_code == 200, scheduler.text
    assert scheduler.json()["detail"] == "replaced"


# ------------------------------------------------- 候选默认有效期与公共前置筛选（MEM-D1 D2/D3）


def test_candidate_entry_validity_follows_constraint_window(
    client: TestClient, mining_scope: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """候选 constraint 自带日期窗口时，条目级默认有效期覆盖该窗口且不更宽。"""
    scope_id = mining_scope["scope_id"]
    version_id = mining_scope["version_id"]
    first = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={
            "teacher_business_id": "T-WIN",
            "slot_business_ids": ["S1"],
            "date_from": "2026-09-20",
            "date_to": "2026-09-26",
        },
        declared_reason="教师要求",
    )
    second = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={
            "teacher_business_id": "T-WIN",
            "slot_business_ids": ["S1"],
            "date_from": "2026-09-24",
            "date_to": "2026-09-25",
        },
        declared_reason="教师要求",
    )
    _mock_ai(
        monkeypatch,
        [
            {
                "subject_type": "teacher",
                "subject_id": "T-WIN",
                "predicate": "avoid_slot",
                "constraint": {
                    "slot_ids": ["S1"],
                    "date_from": "2026-09-24",
                    "date_to": "2026-09-25",
                },
                "evidence_ids": [first.id, second.id],
                "rationale": "该教师 9/24-25 反复调课",
            }
        ],
    )

    run = client.post("/api/v1/memory/mining-runs", headers=mining_scope["headers"])
    assert run.status_code == 200, run.text
    created = run.json()["created"]
    assert len(created) == 1
    candidate = created[0]
    # 条目级窗口 = constraint 窗口本身（最小覆盖），不再一律 +180 天。
    assert candidate["valid_from"] == "2026-09-24"
    assert candidate["valid_until"] == "2026-09-25"


def test_learning_basis_prefilter_applies_to_ai_path_too(
    client: TestClient, mining_scope: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """公共前置筛选对 AI 路径同样生效（MEM-D1 D3）：临时被迫类与已取消的调课
    事件不进学习集——AI 收到的输入里没有它们，引用它们的候选按坏证据剔除。"""
    scope_id = mining_scope["scope_id"]
    version_id = mining_scope["version_id"]
    # 临时被迫类 ×2（模型有无配置都必须被剔除）。
    for _ in range(2):
        _add_event(
            scope_id,
            version_id,
            event_type="teacher_leave",
            payload={"teacher_business_id": "T-FORCED", "slot_business_ids": ["S1"]},
            declared_reason="临时公差",
        )
    # 候选已被取消（版本被删除）的调课事件 ×2。
    cancelled = [
        _add_event(
            scope_id,
            version_id,
            event_type="teacher_leave",
            payload={"teacher_business_id": "T-CXL", "slot_business_ids": ["S1"]},
            status="candidate_discarded",
        )
        for _ in range(2)
    ]
    # 可学习事件 ×2（同主体，未取消，理由非被迫）。
    ok = [
        _add_event(
            scope_id,
            version_id,
            event_type="teacher_leave",
            payload={"teacher_business_id": "T9", "slot_business_ids": ["S1"]},
        )
        for _ in range(2)
    ]
    captured: dict[str, Any] = {}

    def fake_mine_preferences(
        self: AIService, views: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        captured["views"] = views
        return (
            [
                {
                    "subject_type": "teacher",
                    "subject_id": "T9",
                    "predicate": "avoid_slot",
                    "constraint": {"slot_ids": ["S1"]},
                    "evidence_ids": [ok[0].id, ok[1].id],
                    "rationale": "反复同时段调课",
                },
                {
                    # 引用已取消事件作证据：输入里没有 → 坏证据，剔除。
                    "subject_type": "teacher",
                    "subject_id": "T-CXL",
                    "predicate": "avoid_slot",
                    "constraint": {"slot_ids": ["S1"]},
                    "evidence_ids": [cancelled[0].id, cancelled[1].id],
                    "rationale": "引用被取消的调课",
                },
            ],
            {"total_tokens": 5},
        )

    _mock_ai(monkeypatch, [])
    monkeypatch.setattr(AIService, "mine_preferences", fake_mine_preferences)

    run = client.post("/api/v1/memory/mining-runs", headers=mining_scope["headers"])
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["engine"] == "ai"
    # 只有 2 条可学习事件进入模型输入；临时被迫类与已取消事件都在前置筛选出局。
    assert body["events_scanned"] == 2
    assert {str(item["id"]) for item in captured["views"]} == {ok[0].id, ok[1].id}
    assert all("临时" not in str(item.get("declared_reason")) for item in captured["views"])
    assert body["skipped_invalid"] == 1
    assert len(body["created"]) == 1
    assert body["created"][0]["subject_id"] == "T9"

    # MEM-F 回归场景③：AI 路径输入与公共前置筛选（learning_basis_events，即
    # 确定性统计路径的同一输入源）完全一致——模型有无配置不改变业务边界。
    with SessionLocal() as db:
        basis = learning_basis_events(db, scope_id)
    assert {str(event.id) for event in basis} == {str(item["id"]) for item in captured["views"]}


# ------------------------------------------------- 冲突窗口共用口径（MEM-E1c）


def test_conflict_pairing_uses_effective_date_window(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """MEM-E1c：冲突配对与编译共用实际生效窗口（约束∩条目级交集）口径。

    两条同主体 prefer_slot 偏好（目标集不相交，若条目级全学期窗口重叠会被旧
    口径误标冲突），约束窗口「今明两天」与「后两天」实际不重叠 → 不冲突、双双
    applied；任一侧交集为空（not_applicable）也不参与配对。窗口相对 today 生成：
    该口径以「约束∩条目级有效期」的交集为准，硬编码历史日期会随时间漂移成
    空交集或被 valid_from 截窄（MEM-F 复审当日即踩中一次）。"""
    scope_id = mining_scope["scope_id"]
    headers = mining_scope["headers"]
    today = shanghai_now().date()
    window_1 = (today, today + timedelta(days=1))
    window_2 = (today + timedelta(days=2), today + timedelta(days=3))

    def _constraint(slot: str, window: tuple[date, date]) -> dict[str, Any]:
        return {
            "slot_ids": [slot],
            "date_from": window[0].isoformat(),
            "date_to": window[1].isoformat(),
        }

    first = client.post(
        "/api/v1/memory/preferences",
        headers=headers,
        json={
            "subject_type": "teacher",
            "subject_id": "T9",
            "predicate": "prefer_slot",
            "constraint": _constraint("S1", window_1),
            "source": "explicit_stated",
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/memory/preferences",
        headers=headers,
        json={
            "subject_type": "teacher",
            "subject_id": "T9",
            "predicate": "prefer_slot",
            "constraint": _constraint("S2", window_2),
            "source": "explicit_stated",
        },
    )
    assert second.status_code == 201, second.text
    first_id, second_id = first.json()["id"], second.json()["id"]

    with SessionLocal() as db:
        first_row = db.get(PreferenceEntry, first_id)
        second_row = db.get(PreferenceEntry, second_id)
        assert first_row is not None and second_row is not None
        # 实际生效窗口不重叠：双双清标（条目级窗口都是全学期也不误标冲突）。
        assert first_row.conflict is False
        assert second_row.conflict is False
        assert first_row.provenance.get("conflict_with") is None
        assert second_row.provenance.get("conflict_with") is None
        state = compile_memory_state(db, scope_id)
    outcomes = {item["entry_id"]: item for item in state["outcomes"]}
    assert outcomes[first_id]["outcome"] == "applied"
    assert outcomes[second_id]["outcome"] == "applied"
    rules = {rule["memory_entry_id"]: rule for rule in state["compiled_rules"]}
    assert set(rules) == {first_id, second_id}
    # 每条规则的 scope 按各自的交集窗口切片，互不越界（交集 = 约束窗口本身：
    # 条目级默认有效期从今天起且覆盖两个窗口）。
    assert rules[first_id]["scope"]["date_from"] == window_1[0].isoformat()
    assert rules[first_id]["scope"]["date_to"] == window_1[1].isoformat()
    assert rules[second_id]["scope"]["date_from"] == window_2[0].isoformat()
    assert rules[second_id]["scope"]["date_to"] == window_2[1].isoformat()


# ------------------------------------------------- 学习集接受依据（MEM-E1b）


def test_learning_basis_requires_accepted_basis(
    client: TestClient, mining_scope: dict[str, Any]
) -> None:
    """MEM-E1b：「最终态被接受」落实——pending（未产出候选）与候选停在待发布
    （candidate_ready 但未被接受）的事件都不进学习集；只有已产生结果版本且未被
    后续放弃（candidate_schedule_id 非空）的事件才算有接受依据。"""
    scope_id = mining_scope["scope_id"]
    version_id = mining_scope["version_id"]
    pending = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T-PEND", "slot_business_ids": ["S1"]},
        status="pending",
    )
    draft = _add_event(
        scope_id,
        version_id,
        event_type="teacher_leave",
        payload={"teacher_business_id": "T-DRAFT", "slot_business_ids": ["S1"]},
        candidate_status="draft",
    )
    adopted = [
        _add_event(
            scope_id,
            version_id,
            event_type="teacher_leave",
            payload={"teacher_business_id": "T9", "slot_business_ids": ["S1"]},
        )
        for _ in range(2)
    ]
    with SessionLocal() as db:
        assert pending.candidate_schedule_id is None
        assert draft.candidate_schedule_id is not None
        events = learning_basis_events(db, scope_id)
    assert {event.id for event in events} == {event.id for event in adopted}
