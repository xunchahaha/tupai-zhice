"""记忆层（L1 偏好库）的纯逻辑：偏好编译进求解器 + 调课偏好挖掘候选。

设计边界见 docs/roadmap/02-agent-memory.md §3 与 §6（MEM-C1/C2 修正）。职责：

1. ``compile_memory_state`` 把偏好条目编译成可冻结的完整状态节点（条目快照、
   编译后的内部软规则、逐条使用结果），创建求解任务时整体写入快照与
   SolverRun.memory_usage——改记忆不影响在途求解的可复现性。
2. 三态拆分（§6 修正 1）：只有 confirmed 条目与「教务授权试用且未到期」的
   probation 条目进入求解输入；纯候选一律不进（无感采集，不无感改变排课）。
3. 偏好库只管理软偏好（§6 修正 2）：modality=hard 的条目不再编译进求解，
   逐条标记 hard_requires_conversion，由教务走 convert-to-rule 转正式 Rule。
4. 挖掘候选的确定性统计与 AI 输出校验（§6 修正 3）：除结构白名单外，还做
   「证据支持性」校验——约束必须来自候选引用的证据事件、每条证据事件的主体
   必须与候选主体一致、至少 2 条不同证据；同主体同类型调课 ≥2 次才产生候选，
   AI 未配置或失败时功能照常可用（优雅降级，文案明确建议教务确认）。
5. 拒绝记忆与矛盾消解（§6 修正 4）：拒绝记录按规范化签名落库，同批证据被拒
   的候选不再复现；同主体同谓词新旧条目按生效窗口区分 替代/分时段/存疑冲突。

红线（与 API 层共同保证）：induced_from_adjustment 条目在本模块也只会以
软规则形态进入模型——硬约束路径不接收任何记忆来源。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CourseSession, PreferenceEntry, RescheduleEvent
from ..timezone import shanghai_now

# 试用期条目在目标函数里的权重衰减系数（02 文档 §3.2：先观察，不打扰）。
PROBATION_WEIGHT_DECAY = 0.3
# 挖掘产生的候选默认置信度与权重：先给保守值，确认后由 transition 提权。
MINED_DEFAULT_CONFIDENCE = 0.5
MINED_DEFAULT_WEIGHT = 40
# 挖掘扫描的近期调课事件上限：偏好量级是数百条，事件回看窗口不需要全量。
MINING_EVENT_LIMIT = 200
# 挖掘事件的时间窗（MEM-C2 修正 3）：只回顾最近 90 天的调课事件，避免把上学期
# 的历史一次性灌进挖掘。口径为「事件 created_at 距今 ≤90 天」的滚动窗口；
# 相比「自方案最新课次日期起算」，它不随排课进度漂移，行为更可预期。
MINING_EVENT_WINDOW_DAYS = 90
# 创建偏好未显式给有效期时的默认时长（红线②：可空但创建 API 默认 +180 天；
# MEM-C1 起仅在方案内查不到任何上课日期时才回落到它）。
DEFAULT_VALIDITY_DAYS = 180

# 编译器版本：随冻结的 memory 节一起落快照，用于解释「这版课表是哪版编译器排的」。
MEMORY_COMPILER_VERSION = 3

# 逐条使用结果枚举（§6 修正 2/6 + MEM-C2 修正 4）：随 memory 节冻结，解释层与
# 前端按它展示「已应用 X / 未使用 Y 及原因」。
MEMORY_OUTCOMES = (
    "applied",
    "not_authorized",
    "expired",
    "unsupported_predicate",
    "converted_to_rule",
    "hard_requires_conversion",
    "conflict_unresolved",
    "compile_error",
)

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

# convert-to-rule（§6 修正 2）：hard 偏好转正式规则时谓词 → constraint_type 的对齐。
# prefer_* 的「硬化」对应 fixed_*（必须落在其中）；forbidden 走原语义；
# consecutive_sessions / max_daily_load 没有 hard 路径，不开放转换（API 层 422）。
PREFERENCE_RULE_KINDS: dict[str, str] = {
    "avoid_slot": "forbidden_slot",
    "avoid_room": "forbidden_room",
    "prefer_slot": "fixed_slot",
    "prefer_room": "fixed_room",
}

# 正式规则的 actor_type 用 constraint-catalog 口径（cohort 记 class）。
RULE_ACTOR_TYPES: dict[str, str] = {
    "teacher": "teacher",
    "classroom": "room",
    "cohort": "class",
    "course": "course",
}

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


def default_valid_until_for_scope(db: Session, schedule_set_id: str) -> date:
    """创建偏好的默认有效期（§6 修正 5）：优先取本方案主数据里最大的上课日期，
    让「本学期偏好」天然随学期失效；方案内没有任何课次时回落 +180 天。"""
    max_date = db.scalar(
        select(func.max(CourseSession.lesson_date)).where(
            CourseSession.schedule_set_id == schedule_set_id
        )
    )
    return max_date or default_valid_until()


def normalized_constraint(constraint: dict[str, Any] | None) -> str:
    return json.dumps(constraint or {}, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------- 矛盾消解（MEM-C2 修正 4）
# 同主体+同谓词新旧条目按生效窗口三分支：new_replaces（窗口不重叠 → 旧条目
# expired、provenance 记 superseded_by）/ time_sliced（相邻不重叠 → 并存）/
# conflict_flagged（窗口重叠且约束互斥 → 旧条目打 conflict 标，待教务裁决）。

# 「相邻」的判定容差：旧窗口结束日 +1 天即为新窗口开始日视为首尾相接。
ADJACENT_TOLERANCE_DAYS = 1

# 同主体同谓词下，约束互斥的判定（保守口径，只拦真正不能同时成立的组合）：
# prefer_* 目标集完全不相交 → 互斥（不可能同时偏好两个不相交的目标）；
# avoid_* 取并集即可同时成立 → 不互斥；数值型谓词目标值不同 → 互斥。
_CONFLICT_VALUE_KEYS = ("minimum_consecutive", "max_daily_load", "daily_max")


def _constraint_targets(constraint: dict[str, Any] | None, predicate: str) -> set[str]:
    key = "slot_ids" if predicate in {"avoid_slot", "prefer_slot"} else "room_ids"
    values = (constraint or {}).get(key) or []
    return {str(item) for item in values if item}


def constraints_conflict(
    predicate: str, a: dict[str, Any] | None, b: dict[str, Any] | None
) -> bool:
    """判断同谓词的两份约束是否互斥（不能同时成立）。保守：拿不准一律不互斥。"""
    if predicate in {"prefer_slot", "prefer_room"}:
        targets_a = _constraint_targets(a, predicate)
        targets_b = _constraint_targets(b, predicate)
        if targets_a and targets_b:
            return targets_a.isdisjoint(targets_b)
        return False
    if predicate in {"avoid_slot", "avoid_room"}:
        # 回避类取并集即可同时满足，永不互斥。
        return False
    keys = [key for key in _CONFLICT_VALUE_KEYS if key in (a or {})]
    for key in keys:
        value_a = (a or {}).get(key)
        value_b = (b or {}).get(key)
        if value_a is not None and value_b is not None and value_a != value_b:
            return True
    return False


def _entry_window(entry: PreferenceEntry) -> tuple[date, date]:
    """条目的生效窗口；空端按开区间处理（-inf / +inf）。"""
    return (
        entry.valid_from or date.min,
        entry.valid_until or date.max,
    )


def _windows_overlap(
    window_a: tuple[date, date], window_b: tuple[date, date]
) -> bool:
    return window_a[0] <= window_b[1] and window_b[0] <= window_a[1]


def _windows_adjacent(
    window_a: tuple[date, date], window_b: tuple[date, date]
) -> bool:
    """不重叠且首尾相接（间隙 ≤1 天）：同一偏好的时间切片，允许并存。"""
    gap_low = (window_b[0] - window_a[1]).days
    gap_high = (window_a[0] - window_b[1]).days
    return 0 < gap_low <= ADJACENT_TOLERANCE_DAYS or 0 < gap_high <= ADJACENT_TOLERANCE_DAYS


def _active_others(
    db: Session, new_entry: PreferenceEntry
) -> list[PreferenceEntry]:
    """同方案、同主体、同谓词的其他活跃条目（probation/confirmed）。"""
    return list(
        db.scalars(
            select(PreferenceEntry).where(
                PreferenceEntry.schedule_set_id == new_entry.schedule_set_id,
                PreferenceEntry.subject_type == new_entry.subject_type,
                PreferenceEntry.subject_id == new_entry.subject_id,
                PreferenceEntry.predicate == new_entry.predicate,
                PreferenceEntry.status.in_(["probation", "confirmed"]),
                PreferenceEntry.id != new_entry.id,
            )
        )
    )


def resolve_conflicts_for_new_entry(db: Session, new_entry: PreferenceEntry) -> str:
    """新条目落库后调用（未 commit）：对同主体同谓词的旧活跃条目做三分支消解。

    返回本次发生的最强分支：conflict_flagged > new_replaces > time_sliced > coexist。
    只改旧条目（expired / conflict 标记 / provenance），不修改新条目状态。
    """
    new_window = _entry_window(new_entry)
    branch = "coexist"
    for old in _active_others(db, new_entry):
        old_window = _entry_window(old)
        if _windows_overlap(new_window, old_window):
            if constraints_conflict(new_entry.predicate, new_entry.constraint, old.constraint):
                # 存疑保留：旧条目打 conflict 标（编译期跳过），双方 provenance
                # 互记对方 id，前端给出「保留旧弃新 / 以新替旧」一键裁决。
                old.conflict = True
                old.provenance = {
                    **(old.provenance or {}),
                    "conflict_with": sorted(
                        {str(item) for item in (old.provenance or {}).get("conflict_with") or []}
                        | {str(new_entry.id)}
                    ),
                    "conflict_flagged_at": shanghai_now().isoformat(),
                }
                new_entry.provenance = {
                    **(new_entry.provenance or {}),
                    "conflict_with": sorted(
                        {
                            str(item)
                            for item in (new_entry.provenance or {}).get("conflict_with") or []
                        }
                        | {str(old.id)}
                    ),
                }
                branch = "conflict_flagged"
            # 重叠但约束兼容（如回避两个不同时段）：两者可同时成立，并存不动。
            continue
        if _windows_adjacent(new_window, old_window):
            # 分时段并存：编译时日期窗口已进规则 scope，互不越界。
            branch = branch if branch in {"conflict_flagged", "new_replaces"} else "time_sliced"
            continue
        # 窗口不重叠也不相邻：新条目取代旧条目（旧 expired，链路保留审计）。
        old.status = "expired"
        old.provenance = {
            **(old.provenance or {}),
            "superseded_by": str(new_entry.id),
            "superseded_at": shanghai_now().isoformat(),
        }
        branch = branch if branch == "conflict_flagged" else "new_replaces"
    return branch


def refresh_conflict_flags(
    db: Session, schedule_set_id: str, subject_type: str, subject_id: str, predicate: str
) -> None:
    """一条目离开活跃集（expired/rejected）后重算同组 conflict 标：剩余活跃条目
    两两互斥则保持打标，否则清标——教务一键裁决后标记自动解除。"""
    db.flush()  # SessionLocal 是 autoflush=False：先落状态变更，重算才能看到
    actives = list(
        db.scalars(
            select(PreferenceEntry).where(
                PreferenceEntry.schedule_set_id == schedule_set_id,
                PreferenceEntry.subject_type == subject_type,
                PreferenceEntry.subject_id == subject_id,
                PreferenceEntry.predicate == predicate,
                PreferenceEntry.status.in_(["probation", "confirmed"]),
            )
        )
    )
    for entry in actives:
        has_conflict = any(
            constraints_conflict(predicate, entry.constraint, other.constraint)
            for other in actives
            if other.id != entry.id
        )
        entry.conflict = has_conflict


def _is_expired(entry: PreferenceEntry, today: date) -> bool:
    return entry.valid_until is not None and entry.valid_until < today


def trial_active(entry: PreferenceEntry, today: date) -> bool:
    """probation 条目是否处于「授权试用」有效期（三态中的「授权试用」态）。"""
    if entry.status != "probation" or not entry.trial_authorized:
        return False
    return entry.trial_until is None or entry.trial_until >= today


def effective_weight(entry: PreferenceEntry) -> float:
    """entry.weight × 状态衰减 × 置信度；确认条目不打折，授权试用按试用期衰减。"""
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
    # §6 修正 5：条目的生效日期窗口进入规则 scope——软惩罚只作用于 lesson_date
    # 落在窗口内的课次（date-aware 求解路径按 scope 日期过滤）。登记状态的过期
    # 判断仍按「今天 vs valid_until」，但作用范围由这个窗口决定。
    if entry.valid_from is not None:
        scope["date_from"] = entry.valid_from.isoformat()
    if entry.valid_until is not None:
        scope["date_to"] = entry.valid_until.isoformat()
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


def _entry_snapshot(entry: PreferenceEntry) -> dict[str, Any]:
    """条目原文字段快照：冻结进 memory 节，事后改/停记忆不影响历史求解的复现。"""
    return {
        "id": entry.id,
        "subject_type": entry.subject_type,
        "subject_id": entry.subject_id,
        "predicate": entry.predicate,
        "constraint": dict(entry.constraint or {}),
        "modality": entry.modality,
        "confidence": float(entry.confidence or 0.0),
        "source": entry.source,
        "evidence": list(entry.evidence or []),
        "weight": int(entry.weight),
        "status": entry.status,
        "valid_from": entry.valid_from.isoformat() if entry.valid_from else None,
        "valid_until": entry.valid_until.isoformat() if entry.valid_until else None,
        "trial_authorized": bool(entry.trial_authorized),
        "trial_until": entry.trial_until.isoformat() if entry.trial_until else None,
        "provenance": dict(entry.provenance or {}),
    }


def _entry_outcome(entry: PreferenceEntry, today: date) -> tuple[str, str]:
    """逐条判定使用结果，返回 (outcome, detail)。判据顺序即优先级。"""
    if entry.modality == "hard":
        # §6 修正 2：偏好库只管理软偏好。已转正式规则的标记回链，未转的提示教务
        # 走 convert-to-rule；两条路径都不再直接进求解。
        rule_id = (entry.provenance or {}).get("rule_id")
        if rule_id:
            return "converted_to_rule", f"已转为正式规则 {rule_id}"
        return "hard_requires_conversion", "硬偏好需经教务转为正式规则后才会参与求解"
    if _is_expired(entry, today):
        until = entry.valid_until
        return "expired", f"有效期至 {until.isoformat() if until else '?'}，已过期作废"
    if entry.conflict:
        # MEM-C2 修正 4：与其他活跃条目窗口重叠且约束互斥——在教务裁决前不进
        # 求解输入（最小处理），避免两条互斥偏好同时参与目标函数。
        others = (entry.provenance or {}).get("conflict_with") or []
        detail = "与同主体同类偏好冲突，待教务处理"
        if others:
            detail += f"：{'、'.join(map(str, others))}"
        return "conflict_unresolved", detail
    if entry.status == "probation" and not trial_active(entry, today):
        if entry.trial_authorized and entry.trial_until is not None:
            return "expired", f"授权试用已于 {entry.trial_until.isoformat()} 到期"
        return "not_authorized", "待确认候选未经采纳或授权试用，不进入求解输入"
    if entry.predicate not in COMPILE_OPEN_PREDICATES:
        return "unsupported_predicate", "当前版本没有该谓词的求解路径，仅登记不编译"
    return "applied", ""


def compile_memory_state(db: Session, schedule_set_id: str) -> dict[str, Any]:
    """编译偏好记忆并产出可冻结的 memory 节点（§6 修正 6）。

    创建求解任务时调用一次，整体写入 DataSnapshot.payload["memory"] 与
    SolverRun.memory_usage；执行路径只从快照读已编译规则，不再现场读库。
    本函数不做写库操作；单条编译异常降级为该条 compile_error，整体异常由
    调用方兜底为 status=compile_failed（求解照常，解释层显式提示）。
    """
    today = shanghai_now().date()
    entries = list(
        db.scalars(
            select(PreferenceEntry)
            .where(
                PreferenceEntry.schedule_set_id == schedule_set_id,
                PreferenceEntry.status.in_(["confirmed", "probation"]),
            )
            .order_by(PreferenceEntry.created_at, PreferenceEntry.id)
        )
    )
    rules: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for entry in entries:
        outcome, detail = _entry_outcome(entry, today)
        if outcome == "applied":
            try:
                rule = _entry_to_rule(entry)
            except Exception as exc:  # noqa: BLE001 - 单条坏数据不拖垮整轮编译
                outcome, detail = "compile_error", str(exc)
            else:
                if rule is None:
                    outcome = "unsupported_predicate"
                    detail = "当前版本没有该谓词的求解路径，仅登记不编译"
                else:
                    detail = f"以权重 {rule['weight']} 参与求解"
                    rules.append(rule)
        outcomes.append(
            {
                "entry_id": entry.id,
                "subject_type": entry.subject_type,
                "subject_id": entry.subject_id,
                "predicate": entry.predicate,
                "status": entry.status,
                "outcome": outcome,
                "detail": detail,
            }
        )
    return {
        "status": "ok",
        "compiler_version": MEMORY_COMPILER_VERSION,
        "compiled_at": shanghai_now().isoformat(),
        "entries": [_entry_snapshot(entry) for entry in entries],
        "compiled_rules": rules,
        "outcomes": outcomes,
        "summary": {
            "considered": len(entries),
            "applied": len(rules),
            "unused": len(entries) - len(rules),
        },
    }


def compile_failed_state(detail: str) -> dict[str, Any]:
    """编译整体失败时的 memory 节点：求解照常进行，解释层必须显式提示。"""
    return {
        "status": "compile_failed",
        "compiler_version": MEMORY_COMPILER_VERSION,
        "compiled_at": shanghai_now().isoformat(),
        "detail": detail,
        "entries": [],
        "compiled_rules": [],
        "outcomes": [],
        "summary": {"considered": 0, "applied": 0, "unused": 0},
    }


def compile_preferences(db: Session, schedule_set_id: str) -> list[dict[str, Any]]:
    """把活跃偏好编译为内部软规则对象（可直接并入 solver payload 的 rules）。

    只含 confirmed 与「授权试用且未到期」的 probation 条目；纯候选、已过期、
    hard、暂无求解路径的谓词一律不出现（完整判定见 compile_memory_state）。
    """
    return list(compile_memory_state(db, schedule_set_id)["compiled_rules"])


# ---------------------------------------------------------------- 挖掘候选（确定性 + AI 校验）

# 统计降级的 declared_reason 消噪（MEM-C2 修正 3）：被迫/临时类调课（一次性事件）
# 不产生长期偏好候选；「教师要求」类或未注明理由的重复事件才参与统计，最终仍由
# 教务确认把关（human-in-the-loop）。未识别的自由文本按 unknown 中性处理。
FORCED_REASON_MARKERS = ("临时", "公差", "请假", "冲突", "停用", "停电", "故障")
TEACHER_REQUEST_MARKERS = ("教师要求",)

REASON_NOISE_CLASSES = ("forced", "request", "unknown")


def reason_noise_class(declared_reason: str | None) -> str:
    """declared_reason 三分类：forced（被迫/临时，不出候选）/ request（教师要求）/
    unknown（空或自由文本，按中性参与统计）。"""
    text = str(declared_reason or "").strip()
    if not text:
        return "unknown"
    if any(marker in text for marker in FORCED_REASON_MARKERS):
        return "forced"
    if any(marker in text for marker in TEACHER_REQUEST_MARKERS):
        return "request"
    return "unknown"


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
    """无 LLM 的退化路径：同主体+同类型+同归因类调课 ≥2 次即产生候选。

    MEM-C2 修正 3：按 reason 分组——临时公差/教师请假等被迫类不出候选；只对
    「教师要求」类或 reason 为空/自由文本的重复事件产生候选。文案改为
    「发现 N 次相似调整，建议教务确认是否存在长期需求」的建议口吻。
    """
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for view in views:
        subject = _event_subject(view)
        if subject is None:
            continue
        key = (
            *subject,
            str(view.get("event_type") or ""),
            reason_noise_class(view.get("declared_reason")),
        )
        groups.setdefault(key, []).append(view)

    candidates: list[dict[str, Any]] = []
    for (subject_type, subject_id, event_type, noise), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        if noise == "forced":
            # 临时公差/教师请假等被迫调课：一次性事件，不产生长期偏好候选。
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
                "rationale": (
                    f"发现 {len(items)} 次相似调整，建议教务确认是否存在长期需求"
                    "（确定性统计，未使用 AI）"
                ),
            }
        )
    return candidates


def _constraint_backed_by_evidence(
    constraint: dict[str, Any], predicate: str, evidence_views: list[dict[str, Any]]
) -> bool:
    """结构校验（MEM-C2 修正 3）：约束里的每个时段/教室/日期必须有证据出处。

    口径：时段必须出现在证据事件的 ``slot_business_ids`` 内；教室必须出现在
    证据事件的 ``room_business_id`` 内（调课事件载荷没有 before/after 快照字段，
    停用教室是事件里能稳定取得的唯一教室出处，按此口径归集）；日期必须落在
    证据事件的 ``date_from ~ date_to`` 范围内。约束未引用任何目标时放行。
    """
    slots: set[str] = set()
    rooms: set[str] = set()
    date_ranges: list[tuple[str | None, str | None]] = []
    for view in evidence_views:
        slots.update(str(item) for item in view.get("slot_business_ids") or [])
        room = view.get("room_business_id")
        if room:
            rooms.add(str(room))
        date_ranges.append((view.get("date_from"), view.get("date_to")))
    if predicate in {"avoid_slot", "prefer_slot"}:
        targets = _constraint_targets(constraint, predicate)
        if targets and not targets <= slots:
            return False
    if predicate in {"avoid_room", "prefer_room"}:
        targets = _constraint_targets(constraint, predicate)
        if targets and not targets <= rooms:
            return False
    for key in ("date_from", "date_to"):
        value = constraint.get(key)
        if value is None:
            continue
        text = str(value)
        if not any(
            (low is None or str(low) <= text) and (high is None or text <= str(high))
            for low, high in date_ranges
        ):
            return False
    return True


def validate_ai_candidates(
    raw_candidates: list[dict[str, Any]],
    views: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """白名单校验模型提名的候选：结构校验 + 证据支持性校验（MEM-C2 修正 3）。

    结构校验：主体类型合法且主体在事件里出现过、谓词在白名单内、constraint 是
    对象、约束里的时段/教室/日期必须来自该候选引用的证据事件（见
    ``_constraint_backed_by_evidence`` 的口径注释）。
    证据校验：候选至少 2 条不同证据；每条证据事件的主体必须与候选主体一致——
    李老师的请假事件不能支持张老师的候选。归属口径：事件按 payload 的
    ``teacher/room/course_business_id`` 归属主体；调课事件载荷没有 before/after
    快照字段，教师变化事件同样只按其 payload 主体字段归集（当前能稳定取得的
    唯一口径，与 ``_event_subject`` 的挖掘分组一致）。
    返回（合法候选, 被丢弃数）。
    """
    views_by_id = {str(view.get("id")): view for view in views}
    known_subjects = {
        subject
        for view in views
        if (subject := _event_subject(view)) is not None
    }
    accepted: list[dict[str, Any]] = []
    rejected = 0
    for item in raw_candidates:
        subject_type = str(item.get("subject_type") or "")
        subject_id = str(item.get("subject_id") or "")
        predicate = str(item.get("predicate") or "")
        evidence_ids = list(
            dict.fromkeys(str(value) for value in item.get("evidence_ids") or [] if value)
        )
        raw_constraint = item.get("constraint")
        constraint = raw_constraint if isinstance(raw_constraint, dict) else None
        evidence_views = [views_by_id.get(event_id) for event_id in evidence_ids]
        known_views = [view for view in evidence_views if view is not None]
        if (
            subject_type not in SUBJECT_TYPES
            or (subject_type, subject_id) not in known_subjects
            or predicate not in ALL_PREDICATES
            or constraint is None
            # 证据校验：至少 2 条不同证据，且每条证据事件的主体与候选一致。
            or len(evidence_ids) < 2
            or any(view is None for view in evidence_views)
            or any(_event_subject(view) != (subject_type, subject_id) for view in known_views)
            or not _constraint_backed_by_evidence(constraint, predicate, known_views)
        ):
            rejected += 1
            continue
        accepted.append(
            {
                "subject_type": subject_type,
                "subject_id": subject_id,
                "predicate": predicate,
                "constraint": dict(constraint),
                "evidence_ids": evidence_ids,
                "rationale": str(item.get("rationale") or ""),
            }
        )
    return accepted, rejected
