"""记忆层（L1 偏好库）的纯逻辑：偏好编译进求解器 + 调课偏好挖掘候选。

设计边界见 docs/roadmap/02-agent-memory.md §3。v1 只做两件事：

1. ``compile_preferences`` 把 confirmed/probation 且未过期的偏好条目翻译成
   内部软规则对象，注入 solver 现有软约束管线（不新造求解项）。
2. 挖掘候选的确定性统计与 AI 输出校验：同主体同类型调课 ≥2 次即产生候选，
   AI 未配置或失败时功能照常可用（优雅降级）。

红线（与 API 层共同保证）：induced_from_adjustment 条目在本模块也只会以
软规则形态进入模型——硬约束路径不接收任何记忆来源。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PreferenceEntry, RescheduleEvent
from ..timezone import shanghai_now

# 试用期条目在目标函数里的权重衰减系数（02 文档 §3.2：先观察，不打扰）。
PROBATION_WEIGHT_DECAY = 0.3
# 挖掘产生的候选默认置信度与权重：先给保守值，确认后由 transition 提权。
MINED_DEFAULT_CONFIDENCE = 0.5
MINED_DEFAULT_WEIGHT = 40
# 挖掘扫描的近期调课事件上限：偏好量级是数百条，事件回看窗口不需要全量。
MINING_EVENT_LIMIT = 200
# 创建偏好未显式给有效期时的默认时长（红线②：可空但创建 API 默认 +180 天）。
DEFAULT_VALIDITY_DAYS = 180

SUBJECT_TYPES = ("teacher", "classroom", "cohort", "course")
PREFERENCE_STATUSES = ("probation", "confirmed", "rejected", "expired")

# v1 开放的谓词 → solver 现有软约束类型。max_daily_load 暂无对应求解路径，
# 允许作为记忆条目存在，但编译时跳过（不新造求解项）。
PREDICATE_SOLVER_PATHS: dict[str, dict[str, Any]] = {
    "avoid_slot": {"constraint_type": "forbidden_slot", "scope_key": "slot_ids"},
    "prefer_slot": {"constraint_type": "preferred_slot", "scope_key": "slot_ids"},
    "avoid_room": {"constraint_type": "forbidden_room", "scope_key": "room_ids"},
    "prefer_room": {"constraint_type": "preferred_room", "scope_key": "room_ids"},
    "consecutive_sessions": {"constraint_type": "consecutive_sessions"},
}
COMPILE_OPEN_PREDICATES = frozenset(PREDICATE_SOLVER_PATHS)
ALL_PREDICATES = frozenset({*COMPILE_OPEN_PREDICATES, "max_daily_load"})

SUBJECT_ACTOR_TYPES = {
    "teacher": "teacher",
    "classroom": "room",
    "cohort": "cohort",
    "course": "course",
}

# 调课事件类型 → （主体类型, 偏好谓词）。room_outage 的主体是出事的教室，
# 反复坏说明该教室在该场景下该被回避；extra_class 反复加在某类时段说明偏好。
EVENT_PATTERNS: dict[str, tuple[str, str]] = {
    "teacher_leave": ("teacher", "avoid_slot"),
    "room_outage": ("classroom", "avoid_room"),
    "extra_class": ("course", "prefer_slot"),
}

PREFERENCE_TRANSITIONS: dict[str, set[str]] = {
    "probation": {"confirmed", "rejected", "expired"},
    "confirmed": {"expired", "rejected"},
    "rejected": set(),
    "expired": set(),
}


def default_valid_until(today: date | None = None) -> date:
    base = today or shanghai_now().date()
    return base + timedelta(days=DEFAULT_VALIDITY_DAYS)


def normalized_constraint(constraint: dict[str, Any] | None) -> str:
    return json.dumps(constraint or {}, ensure_ascii=False, sort_keys=True)


def _is_expired(entry: PreferenceEntry, today: date) -> bool:
    return entry.valid_until is not None and entry.valid_until < today


def effective_weight(entry: PreferenceEntry) -> float:
    """entry.weight × 状态衰减 × 置信度；确认条目不打折。"""
    decay = 1.0 if entry.status == "confirmed" else PROBATION_WEIGHT_DECAY
    return float(entry.weight) * decay * float(entry.confidence or 0.0)


def _entry_to_rule(entry: PreferenceEntry) -> dict[str, Any] | None:
    path = PREDICATE_SOLVER_PATHS.get(entry.predicate)
    if path is None:
        # max_daily_load 等暂未接入求解项的谓词：保留在记忆里，不进目标函数。
        return None
    constraint = entry.constraint or {}
    scope: dict[str, Any] = {}
    if path.get("scope_key"):
        values = constraint.get(path["scope_key"]) or []
        scope[path["scope_key"]] = [str(item) for item in values if item]
    for key in ("date_from", "date_to", "minimum_consecutive"):
        if constraint.get(key) is not None:
            scope[key] = constraint[key]
    return {
        "business_id": f"MEMORY-{entry.id}",
        "actor_type": SUBJECT_ACTOR_TYPES.get(entry.subject_type, entry.subject_type),
        "actor_ids": [entry.subject_id],
        "constraint_type": path["constraint_type"],
        "scope": scope,
        "hardness": "soft",
        "weight": max(1, round(effective_weight(entry))),
        "confidence": float(entry.confidence or 0.0),
        "version": 1,
        "source_text": f"记忆偏好：{entry.subject_type} {entry.subject_id} {entry.predicate}",
        "memory_entry_id": entry.id,
        "memory_status": entry.status,
        "memory_effective_weight": round(effective_weight(entry), 2),
    }


def compile_preferences(db: Session, schedule_set_id: str) -> list[dict[str, Any]]:
    """把活跃偏好编译为内部软规则对象（可直接并入 solver payload 的 rules）。"""
    today = shanghai_now().date()
    entries = list(
        db.scalars(
            select(PreferenceEntry)
            .where(
                PreferenceEntry.schedule_set_id == schedule_set_id,
                PreferenceEntry.status.in_(["confirmed", "probation"]),
            )
            .order_by(PreferenceEntry.created_at)
        )
    )
    rules: list[dict[str, Any]] = []
    for entry in entries:
        if _is_expired(entry, today):
            continue
        rule = _entry_to_rule(entry)
        if rule is not None:
            rules.append(rule)
    return rules


def mining_event_view(event: RescheduleEvent) -> dict[str, Any]:
    """把调课事件压成挖掘需要的最小事实包（含归因理由）。"""
    payload = event.payload or {}
    slots = payload.get("slot_business_ids") or []
    return {
        "id": event.id,
        "event_type": event.event_type,
        "declared_reason": event.declared_reason,
        "description": event.description,
        "teacher_business_id": payload.get("teacher_business_id"),
        "room_business_id": payload.get("room_business_id"),
        "course_business_id": payload.get("course_business_id"),
        "slot_business_ids": [str(item) for item in slots if item],
        "date_from": payload.get("date_from"),
        "date_to": payload.get("date_to"),
    }


def _event_subject(view: dict[str, Any]) -> tuple[str, str] | None:
    pattern = EVENT_PATTERNS.get(str(view.get("event_type") or ""))
    if pattern is None:
        return None
    subject_type, _predicate = pattern
    field = {
        "teacher": "teacher_business_id",
        "classroom": "room_business_id",
        "course": "course_business_id",
    }[subject_type]
    subject_id = str(view.get(field) or "")
    if not subject_id:
        return None
    return subject_type, subject_id


def deterministic_preference_candidates(
    views: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """无 LLM 的退化路径：同主体+同类型调课 ≥2 次即产生候选。"""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for view in views:
        subject = _event_subject(view)
        if subject is None:
            continue
        key = (*subject, str(view.get("event_type") or ""))
        groups.setdefault(key, []).append(view)

    candidates: list[dict[str, Any]] = []
    for (subject_type, subject_id, event_type), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        _subject_type, predicate = EVENT_PATTERNS[event_type]
        if predicate == "avoid_room":
            constraint: dict[str, Any] = {"room_ids": sorted({str(items[0]["room_business_id"])})}
        else:
            slot_ids: set[str] = set()
            for item in items:
                slot_ids.update(item.get("slot_business_ids") or [])
            constraint = {"slot_ids": sorted(slot_ids)}
        candidates.append(
            {
                "subject_type": subject_type,
                "subject_id": subject_id,
                "predicate": predicate,
                "constraint": constraint,
                "evidence_ids": [str(item["id"]) for item in items],
                "rationale": f"本学期同主体同类型调课 {len(items)} 次（确定性统计，未使用 AI）",
            }
        )
    return candidates


def validate_ai_candidates(
    raw_candidates: list[dict[str, Any]],
    views: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """白名单校验模型提名的候选：只做结构过滤，不做语义加工。

    主体必须在事件里出现过、证据必须引用真实事件 id，防止模型臆造记忆。
    返回（合法候选, 被丢弃数）。
    """
    known_subjects = set()
    known_event_ids: set[str] = set()
    for view in views:
        subject = _event_subject(view)
        if subject is not None:
            known_subjects.add(subject)
        known_event_ids.add(str(view.get("id")))
    accepted: list[dict[str, Any]] = []
    rejected = 0
    for item in raw_candidates:
        subject_type = str(item.get("subject_type") or "")
        subject_id = str(item.get("subject_id") or "")
        predicate = str(item.get("predicate") or "")
        evidence_ids = [str(value) for value in item.get("evidence_ids") or []]
        if (
            subject_type not in SUBJECT_TYPES
            or (subject_type, subject_id) not in known_subjects
            or predicate not in ALL_PREDICATES
            or not evidence_ids
            or any(event_id not in known_event_ids for event_id in evidence_ids)
            or not isinstance(item.get("constraint") or {}, dict)
        ):
            rejected += 1
            continue
        accepted.append(
            {
                "subject_type": subject_type,
                "subject_id": subject_id,
                "predicate": predicate,
                "constraint": dict(item.get("constraint") or {}),
                "evidence_ids": list(dict.fromkeys(evidence_ids)),
                "rationale": str(item.get("rationale") or ""),
            }
        )
    return accepted, rejected
