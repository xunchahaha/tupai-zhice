from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ortools.sat.python import cp_model

STATUS_NAMES = {
    cp_model.OPTIMAL: "OPTIMAL",
    cp_model.FEASIBLE: "FEASIBLE",
    cp_model.INFEASIBLE: "INFEASIBLE",
    cp_model.MODEL_INVALID: "UNKNOWN",
    cp_model.UNKNOWN: "UNKNOWN",
}

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _rule_signature(rule: dict[str, Any]) -> tuple[Any, ...]:
    return (
        rule.get("actor_type"),
        tuple(sorted(rule.get("actor_ids") or [])),
        rule.get("constraint_type"),
        json.dumps(rule.get("scope") or {}, ensure_ascii=False, sort_keys=True),
        rule.get("hardness"),
    )


def _rule_priority_key(rule: dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if rule.get("hardness") == "hard" else 1,
        -(int(rule.get("weight") or 0)),
        -(float(rule.get("confidence") or 0)),
        -(int(rule.get("version") or 1)),
        str(rule.get("business_id") or ""),
    )


def _priority_recommendation(
    payload: dict[str, Any], conflict_rule_ids: list[str]
) -> tuple[list[str], list[str]]:
    rules = [
        rule for rule in payload.get("rules", []) if rule.get("business_id") in conflict_rule_ids
    ]
    if len(rules) < 2:
        return [], []
    ordered = sorted(rules, key=_rule_priority_key)
    priority_ids = [str(rule["business_id"]) for rule in ordered]
    explanations: list[str] = []
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for rule in rules:
        groups.setdefault(_rule_signature(rule), []).append(rule)
    for group in groups.values():
        if len(group) < 2:
            continue
        group_ordered = sorted(group, key=_rule_priority_key)
        preferred = str(group_ordered[0]["business_id"])
        others = "、".join(str(rule["business_id"]) for rule in group_ordered[1:])
        explanations.append(f"规则 {preferred} 与 {others} 的条件重复，建议优先保留 {preferred}。")
    if not explanations:
        preferred = ordered[0]
        preferred_id = str(preferred["business_id"])
        preferred_kind = "硬约束" if preferred.get("hardness") == "hard" else "软约束"
        explanations.append(
            f"无解时建议优先保留规则 {preferred_id}（{preferred_kind}、"
            f"权重 {int(preferred.get('weight') or 0)}、"
            f"置信度 {float(preferred.get('confidence') or 0):.2f}），"
            "再审查其余冲突规则。"
        )
    return priority_ids, explanations


@dataclass
class BuiltModel:
    model: cp_model.CpModel
    variables: dict[tuple[str, str, str], cp_model.IntVar]
    assumption_index: dict[int, str]
    sessions: list[dict[str, Any]]


def _event_blocks(
    event: dict[str, Any], session: dict[str, Any], room_id: str, slot_id: str
) -> bool:
    event_type = event.get("event_type")
    slots = set(event.get("slot_business_ids") or [])
    in_window = not slots or slot_id in slots
    if event_type == "teacher_leave":
        return in_window and str(event.get("teacher_business_id") or "") in _session_teacher_ids(
            session
        )
    if event_type == "room_outage":
        return in_window and room_id == event.get("room_business_id")
    return False


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _clock_minutes(value: str) -> int:
    hours, minutes = (int(part) for part in value.split(":", 1))
    return hours * 60 + minutes


def _candidate_clock_windows(session: dict[str, Any]) -> list[tuple[str, str]]:
    windows: set[tuple[str, str]] = set()
    for item in session.get("candidate_clock_windows") or []:
        if not isinstance(item, dict):
            continue
        start = str(item.get("start_time") or "")
        end = str(item.get("end_time") or "")
        if start and end and _clock_minutes(end) > _clock_minutes(start):
            windows.add((start, end))
    fixed_start = str(session.get("fixed_start_time") or "")
    fixed_end = str(session.get("fixed_end_time") or "")
    if not windows and fixed_start and fixed_end:
        windows.add((fixed_start, fixed_end))
    return sorted(windows, key=lambda item: (_clock_minutes(item[0]), _clock_minutes(item[1])))


def _calendar_user_ids(session: dict[str, Any], teachers: dict[str, dict[str, Any]]) -> list[str]:
    explicit = str(session.get("calendar_user_id") or "").strip()
    if explicit:
        return [explicit]
    return sorted(
        {
            str(teachers.get(teacher_id, {}).get("calendar_user_id") or "").strip()
            for teacher_id in _session_teacher_ids(session)
            if str(teachers.get(teacher_id, {}).get("calendar_user_id") or "").strip()
        }
    )


def _session_matches_rule(session: dict[str, Any], room_id: str, rule: dict[str, Any]) -> bool:
    actor_ids = set(rule.get("actor_ids") or [])
    actor_type = str(rule.get("actor_type") or "").lower()
    if actor_type in {"room", "classroom", "教室"}:
        return not actor_ids or not room_id or room_id in actor_ids
    return (
        not actor_ids
        or session["business_id"] in actor_ids
        or bool(actor_ids.intersection(_session_teacher_ids(session)))
        or session["class_business_id"] in actor_ids
        or room_id in actor_ids
    )


def _session_product_types(session: dict[str, Any]) -> list[str]:
    values = [str(item) for item in session.get("product_types") or [] if item]
    primary = str(session.get("product_type") or "")
    if primary and primary not in values:
        values.append(primary)
    return sorted(set(values)) or [""]


def _session_teacher_ids(session: dict[str, Any]) -> list[str]:
    values = [str(item) for item in session.get("teacher_business_ids") or [] if item]
    primary = str(session.get("teacher_business_id") or "")
    if primary and primary not in values:
        values.append(primary)
    return sorted(set(values))


def _class_scope_keys(session: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Identify every student group occupied by a teaching demand.

    Official data reuses labels such as “走读SMART班” across different
    product types. Those are parallel business scenarios, not one physical
    class, so the label alone must not create a class-overlap constraint. A
    preprocessed shared lesson can, however, belong to product A and B at once;
    it must reserve both student groups while still consuming one room.
    """
    business_line = str(session.get("business_line") or "")
    class_id = str(session.get("class_business_id") or "")
    return [
        (business_line, product_type, class_id) for product_type in _session_product_types(session)
    ]


def _class_scope_key(session: dict[str, Any]) -> tuple[str, str, str]:
    """Compatibility helper for callers that need a stable primary key."""
    return _class_scope_keys(session)[0]


def _rule_items(value: object) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for entry in value for item in _rule_items(entry)]
    if not isinstance(value, dict):
        return []
    if value.get("constraint_type") or value.get("type") or value.get("rule_type"):
        return [value]
    nested_rules: list[dict[str, Any]] = []
    for key in (
        "rules",
        "constraints",
        "items",
        "dynamic_rules",
        "recognized_rules",
        "hard_constraints",
        "soft_constraints",
        "rule_contract",
        "contract",
    ):
        items = _rule_items(value.get(key))
        if key in {"hard_constraints", "soft_constraints"}:
            default_hardness = "hard" if key == "hard_constraints" else "soft"
            items = [
                {**item, "hardness": item.get("hardness") or default_hardness} for item in items
            ]
        nested_rules.extend(items)
    for key, nested in value.items():
        if key in {
            "rules",
            "constraints",
            "items",
            "dynamic_rules",
            "recognized_rules",
            "hard_constraints",
            "soft_constraints",
            "rule_contract",
            "contract",
        }:
            continue
        if isinstance(nested, dict) and key in {
            "fixed_room",
            "preferred_room",
            "forbidden_room",
            "unavailable_room",
            "fixed_date",
            "preferred_date",
            "date_window",
            "date_range",
            "allowed_date_range",
        }:
            nested_rules.extend(_rule_items({"type": key, **nested}))
    return nested_rules


def _normalized_rules(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_rules: list[dict[str, Any]] = []
    for key in (
        "rules",
        "dynamic_rules",
        "structured_rules",
        "recognized_rules",
        "recognized_rule_contract",
        "rule_contract",
        "hard_constraints",
        "soft_constraints",
    ):
        items = _rule_items(payload.get(key))
        if key in {"hard_constraints", "soft_constraints"}:
            default_hardness = "hard" if key == "hard_constraints" else "soft"
            items = [
                {**item, "hardness": item.get("hardness") or default_hardness} for item in items
            ]
        raw_rules.extend(items)

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rules):
        constraint_type = str(
            raw.get("constraint_type") or raw.get("type") or raw.get("rule_type") or ""
        ).strip()
        if not constraint_type:
            continue
        scope = dict(raw.get("scope") or {})
        for key in (
            "room_id",
            "room_ids",
            "room_business_id",
            "room_business_ids",
            "slot_id",
            "slot_ids",
            "slot_business_id",
            "slot_business_ids",
            "date",
            "date_from",
            "date_to",
            "date_window_days",
            "window_days",
            "before_days",
            "after_days",
            "days_before",
            "days_after",
            "days",
        ):
            if key in raw and key not in scope:
                scope[key] = raw[key]
        if scope.get("room_business_id") and not scope.get("room_id"):
            scope["room_id"] = scope["room_business_id"]
        if scope.get("room_business_ids") and not scope.get("room_ids"):
            scope["room_ids"] = scope["room_business_ids"]
        if scope.get("slot_business_id") and not scope.get("slot_id"):
            scope["slot_id"] = scope["slot_business_id"]
        if scope.get("slot_business_ids") and not scope.get("slot_ids"):
            scope["slot_ids"] = scope["slot_business_ids"]
        for key in ("room_ids", "slot_ids"):
            value = scope.get(key)
            if value and not isinstance(value, list):
                scope[key] = [value]
        if scope.get("window_days") is not None and scope.get("date_window_days") is None:
            scope["date_window_days"] = scope["window_days"]
        if scope.get("days_before") is not None and scope.get("before_days") is None:
            scope["before_days"] = scope["days_before"]
        if scope.get("days_after") is not None and scope.get("after_days") is None:
            scope["after_days"] = scope["days_after"]
        raw_actor_ids = raw.get("actor_ids") or raw.get("targets") or []
        actor_ids = list(raw_actor_ids) if isinstance(raw_actor_ids, list) else [raw_actor_ids]
        for key in (
            "course_business_id",
            "course_business_ids",
            "course_id",
            "course_ids",
            "class_business_id",
            "class_business_ids",
            "class_id",
            "class_ids",
            "teacher_business_id",
            "teacher_business_ids",
            "teacher_id",
            "teacher_ids",
            "target_id",
            "target_ids",
            "actor_id",
        ):
            value = raw.get(key)
            if isinstance(value, list):
                actor_ids.extend(value)
            elif value:
                actor_ids.append(value)
        actor_type = str(raw.get("actor_type") or "")
        if actor_type.lower() in {"room", "classroom", "教室"} and constraint_type in {
            "fixed_room",
            "preferred_room",
            "forbidden_room",
            "unavailable_room",
        }:
            if not scope.get("room_id") and not scope.get("room_ids"):
                scope["room_ids"] = actor_ids
            actor_ids = []
        normalized.append(
            {
                **raw,
                "business_id": str(raw.get("business_id") or f"DYNAMIC-{index + 1}"),
                "actor_type": actor_type,
                "actor_ids": list(dict.fromkeys(str(item) for item in actor_ids if item)),
                "constraint_type": constraint_type,
                "scope": scope,
                "hardness": str(raw.get("hardness") or "hard").lower(),
                "weight": int(raw.get("weight") or 1),
            }
        )
    return normalized


def _rule_room_ids(rule: dict[str, Any]) -> set[str]:
    scope = rule.get("scope") or {}
    room_ids = {str(item) for item in scope.get("room_ids") or [] if item}
    if scope.get("room_id"):
        room_ids.add(str(scope["room_id"]))
    return room_ids


def _slot_for_date(
    slots: dict[tuple[object, object, object], str], lesson_date: date, start: str, end: str
) -> str:
    return slots.get((WEEKDAYS[lesson_date.weekday()], start, end), "")


def _event_targets_session(event: dict[str, Any], session: dict[str, Any]) -> bool:
    course_id = event.get("course_business_id")
    if course_id and course_id != session["business_id"]:
        return False
    if event.get("event_type") == "teacher_leave":
        teacher_id = event.get("teacher_business_id")
        calendar_user_id = event.get("calendar_user_id")
        return bool(
            (teacher_id and str(teacher_id) in _session_teacher_ids(session))
            or (calendar_user_id and calendar_user_id == session.get("calendar_user_id"))
        )
    return True


def _event_blocks_date(
    event: dict[str, Any],
    session: dict[str, Any],
    candidate: date,
    slots: dict[tuple[object, object, object], str],
) -> bool:
    """判断请假事件是否挡住某个候选日期。

    事件可以按时段、按日期，或两者同时限定范围。两者都没有时表示该教师
    在整个求解窗口内都不可用——这必然无解，因此接口层不允许创建这种事件。
    """
    if event.get("event_type") != "teacher_leave" or not _event_targets_session(event, session):
        return False
    date_from = _parse_date(event.get("date_from"))
    date_to = _parse_date(event.get("date_to"))
    if date_from and candidate < date_from:
        return False
    if date_to and candidate > date_to:
        return False
    slot_ids = set(event.get("slot_business_ids") or [])
    if not slot_ids:
        return True
    candidate_slot = _slot_for_date(
        slots, candidate, str(session["fixed_start_time"]), str(session["fixed_end_time"])
    )
    return candidate_slot in slot_ids


def _event_blocks_option(
    event: dict[str, Any], session: dict[str, Any], candidate: date, slot_id: str
) -> bool:
    if event.get("event_type") != "teacher_leave" or not _event_targets_session(event, session):
        return False
    date_from = _parse_date(event.get("date_from"))
    date_to = _parse_date(event.get("date_to"))
    if date_from and candidate < date_from:
        return False
    if date_to and candidate > date_to:
        return False
    slot_ids = set(event.get("slot_business_ids") or [])
    return not slot_ids or slot_id in slot_ids


def _selected_sessions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    business_lines = set(payload.get("business_lines") or [])
    product_types = set(payload.get("product_types") or [])
    class_ids = set(payload.get("class_business_ids") or [])
    # 增量调课用它把求解范围收敛到受影响的局部邻域，邻域外的课次作为固定占用。
    course_ids = {str(item) for item in payload.get("course_business_ids") or []}
    date_from = _parse_date(payload.get("date_from"))
    date_to = _parse_date(payload.get("date_to"))
    previous = {
        str(item.get("course_business_id")): item
        for item in payload.get("previous_assignments", [])
    }
    selected: list[dict[str, Any]] = []
    for session in payload.get("course_sessions", []):
        if not session.get("is_active", True):
            continue
        lesson_date = _parse_date(
            previous.get(str(session.get("business_id")), {}).get("lesson_date")
            or session.get("lesson_date")
        )
        if course_ids and str(session.get("business_id")) not in course_ids:
            continue
        if business_lines and session.get("business_line") not in business_lines:
            continue
        if product_types and not product_types.intersection(_session_product_types(session)):
            continue
        if class_ids and session["class_business_id"] not in class_ids:
            continue
        if date_from and lesson_date and lesson_date < date_from:
            continue
        if date_to and lesson_date and lesson_date > date_to:
            continue
        selected.append(session)
    return selected


def _has_date_information(session: dict[str, Any]) -> bool:
    windows = session.get("candidate_clock_windows") or []
    return bool(
        _parse_date(session.get("lesson_date"))
        and (windows or (session.get("fixed_start_time") and session.get("fixed_end_time")))
    )


def _uses_date_aware_model(payload: dict[str, Any]) -> bool:
    sessions = _selected_sessions(payload)
    return bool(sessions) and all(_has_date_information(item) for item in sessions)


def _empty_result(status: str = "INFEASIBLE", *, presolved: bool = False) -> dict[str, Any]:
    """presolved 表示结论来自求解前的预检，CP-SAT 并未运行。

    「已证明无解」和「预检判定无解」对教务的含义完全不同，落库和接口都必须区分，
    否则 wall_time=0 的短路结论会被当成 CP-SAT 的数学证明。
    """
    return {
        "presolve_infeasible": presolved,
        "model_status": status,
        "objective_value": None,
        "best_bound": None,
        "wall_time_seconds": 0.0,
        "assignments": [],
        "conflict_rule_ids": [],
        "priority_rule_ids": [],
        "priority_explanations": [],
    }


def _date_infeasible_diagnostics(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return traceable IDs for date-aware hard constraints.

    The date-aware model uses built-in constraints instead of CP-SAT
    assumptions, so OR-Tools has no assumption core to return. Expose stable
    system rule IDs and any active dynamic rule IDs instead of leaving the UI
    with an empty conflict list.
    """
    ids: list[str] = []
    dynamic_ids = [
        str(rule.get("business_id"))
        for rule in _normalized_rules(payload)
        if rule.get("hardness") == "hard" and rule.get("business_id")
    ]
    ids.extend(dynamic_ids)
    ids.append("SYSTEM-FIXED-TIME")
    capacity_explanations = _date_capacity_explanations(payload)
    if any(item.startswith("SYSTEM-CLASS-NO-OVERLAP") for item in capacity_explanations):
        ids.append("SYSTEM-CLASS-NO-OVERLAP")
    solver_rules = set(
        payload.get("solver_rules")
        or {
            "fixed_time",
            "room_no_overlap",
            "teacher_no_overlap",
            "calendar_no_overlap",
            "minimize_changes",
        }
    )
    if "room_no_overlap" in solver_rules and any(
        item.startswith("SYSTEM-ROOM-NO-OVERLAP") for item in capacity_explanations
    ):
        ids.append("SYSTEM-ROOM-NO-OVERLAP")
    if "teacher_no_overlap" in solver_rules and any(
        item.startswith("SYSTEM-TEACHER-NO-OVERLAP") for item in capacity_explanations
    ):
        ids.append("SYSTEM-TEACHER-NO-OVERLAP")
    if "calendar_no_overlap" in solver_rules and any(
        item.startswith("SYSTEM-CALENDAR-NO-OVERLAP") for item in capacity_explanations
    ):
        ids.append("SYSTEM-CALENDAR-NO-OVERLAP")
    if len(ids) == len(dynamic_ids) + 1:
        ids.extend(
            [
                "SYSTEM-CLASS-NO-OVERLAP",
                *(["SYSTEM-ROOM-NO-OVERLAP"] if "room_no_overlap" in solver_rules else []),
                *(["SYSTEM-TEACHER-NO-OVERLAP"] if "teacher_no_overlap" in solver_rules else []),
                *(["SYSTEM-CALENDAR-NO-OVERLAP"] if "calendar_no_overlap" in solver_rules else []),
            ]
        )
    ids = list(dict.fromkeys(ids))
    dynamic_text = "、".join(dynamic_ids) if dynamic_ids else "当前请求未携带动态规则 ID"
    detail_text = " ".join(capacity_explanations)
    explanations = [
        "日期感知模型未使用可供 CP-SAT 提取的假设文字；本次无解按内置硬约束标记。",
        f"约束范围：{dynamic_text}；系统约束：{'、'.join(ids[len(dynamic_ids) :])}。",
        detail_text or "优先检查同一业务场景的班级重叠、固定上课时段、教室容量和具体日程账号占用。",
    ]
    return ids, explanations


def _date_capacity_explanations(payload: dict[str, Any]) -> list[str]:
    """Find simple Hall-style overloads in the date-aware candidate windows."""
    from collections import defaultdict

    sessions = _selected_sessions(payload)
    window = max(0, int(payload.get("date_window_days", 7)))
    request_from = _parse_date(payload.get("date_from"))
    request_to = _parse_date(payload.get("date_to"))
    teachers = {str(item.get("business_id")): item for item in payload.get("teachers", [])}
    active_rooms = sum(1 for item in payload.get("rooms", []) if item.get("is_active"))
    solver_rules = set(
        payload.get("solver_rules")
        or {
            "fixed_time",
            "room_no_overlap",
            "teacher_no_overlap",
            "calendar_no_overlap",
            "minimize_changes",
        }
    )
    groups: dict[tuple[Any, Any], list[tuple[date, date, str]]] = defaultdict(list)
    calendar_groups: dict[tuple[Any, Any], list[tuple[date, date, str]]] = defaultdict(list)
    person_groups: dict[tuple[Any, Any], list[tuple[date, date, str]]] = defaultdict(list)
    room_groups: dict[Any, list[tuple[date, date, str]]] = defaultdict(list)
    for session in sessions:
        original = _parse_date(session.get("lesson_date"))
        if original is None:
            continue
        lower = original - timedelta(days=window)
        upper = original + timedelta(days=window)
        if request_from:
            lower = max(lower, request_from)
        if request_to:
            upper = min(upper, request_to)
        if session.get("is_locked"):
            lower = upper = original
        interval = (lower, upper, str(session.get("business_id")))
        windows = _candidate_clock_windows(session)
        # 只有单一时钟窗口时才可做这个 Hall 式预估。多个候选时段的需求不能被
        # 强行塞进主值时段，否则会把本来可行的表误报为容量无解。
        if len(windows) != 1:
            continue
        clock = windows[0]
        for class_key in _class_scope_keys(session):
            groups[(class_key, clock)].append(interval)
        room_groups[clock].append(interval)
        for calendar_user_id in _calendar_user_ids(session, teachers):
            calendar_groups[(calendar_user_id, clock)].append(interval)
        for teacher_id in _session_teacher_ids(session):
            if _is_person(teachers.get(teacher_id, {})):
                person_groups[(teacher_id, clock)].append(interval)

    explanations: list[str] = []

    def scan(
        grouped: dict[Any, list[tuple[date, date, str]]],
        capacity: int,
        rule_id: str,
        label: str,
    ) -> None:
        if capacity <= 0:
            return
        for key, intervals in grouped.items():
            if len(intervals) <= capacity:
                continue
            boundaries = sorted(
                {point for lower, upper, _ in intervals for point in (lower, upper)}
            )
            for start in boundaries:
                for end in boundaries:
                    if end < start:
                        continue
                    contained = [item for item in intervals if item[0] >= start and item[1] <= end]
                    available = (end - start).days + 1
                    if len(contained) <= capacity * available:
                        continue
                    sample = "、".join(item[2] for item in contained[:3])
                    scope = (
                        " / ".join(str(item) for item in key)
                        if isinstance(key, tuple)
                        else str(key)
                    )
                    explanations.append(
                        f"{rule_id}：{label} {scope} 在 {start.isoformat()} 至 "
                        f"{end.isoformat()} 的候选窗口内有 {len(contained)} 场，"
                        f"最多容纳 {capacity * available} 场；示例课次：{sample}。"
                    )
                    return

    scan(groups, 1, "SYSTEM-CLASS-NO-OVERLAP", "班级场景")
    if "room_no_overlap" in solver_rules:
        scan(room_groups, active_rooms, "SYSTEM-ROOM-NO-OVERLAP", "教室")
    if "teacher_no_overlap" in solver_rules:
        scan(person_groups, 1, "SYSTEM-TEACHER-NO-OVERLAP", "教师")
    if "calendar_no_overlap" in solver_rules:
        scan(calendar_groups, 1, "SYSTEM-CALENDAR-NO-OVERLAP", "具体日程账号")
    return explanations[:3]


def _is_person(teacher: dict[str, Any]) -> bool:
    """教研组可以同时开课，自然人不行。

    缺省按自然人处理：任何没有显式声明为教研组的数据集，都必须拿到「同一教师同一
    时刻至多一节课」这条硬约束，否则换一份没有飞书日历映射的数据就会静默排出双排课表。
    """
    return not bool(teacher.get("is_group"))


def _can_use_discrete_date_grid(payload: dict[str, Any]) -> bool:
    """Use the compact grid model when source clock windows never overlap.

    Zhengzhou's source has three disjoint clock windows. Modelling each demand
    against every individual room creates large room-symmetry branches, while
    a per-date/window capacity constraint is mathematically equivalent. Cases
    with overlapping clocks or room-specific rules stay on the interval model.
    """
    # 父版本的教室也是最小变更目标的一部分，须与日期/时段共同决策。
    if payload.get("previous_assignments"):
        return False
    sessions = _selected_sessions(payload)
    if not sessions or any(session.get("is_locked") for session in sessions):
        return False
    event = payload.get("event") or {}
    if event.get("event_type") == "room_outage":
        return False
    room_types = {"fixed_room", "preferred_room", "forbidden_room", "unavailable_room"}
    if any(rule.get("constraint_type") in room_types for rule in _normalized_rules(payload)):
        return False
    selected_ids = {str(item["business_id"]) for item in sessions}
    if any(
        str(item.get("course_business_id") or "") not in selected_ids
        for item in payload.get("previous_assignments", [])
    ):
        return False
    windows = sorted(
        {window for session in sessions for window in _candidate_clock_windows(session)},
        key=lambda item: (_clock_minutes(item[0]), _clock_minutes(item[1])),
    )
    return all(
        _clock_minutes(left[1]) <= _clock_minutes(right[0])
        for left, right in zip(windows, windows[1:], strict=False)
    )


def _solve_date_aware_grid(payload: dict[str, Any]) -> dict[str, Any]:
    model = cp_model.CpModel()
    sessions = _selected_sessions(payload)
    rooms = {
        str(item["business_id"]): item for item in payload.get("rooms", []) if item.get("is_active")
    }
    teachers = {str(item["business_id"]): item for item in payload.get("teachers", [])}
    slots = {
        (item.get("weekday"), item.get("start_time"), item.get("end_time")): str(
            item["business_id"]
        )
        for item in payload.get("time_slots", [])
        if item.get("is_open")
    }
    defines_time_slots = bool(payload.get("time_slots"))
    rules = _normalized_rules(payload)
    solver_rules = set(
        payload.get("solver_rules")
        or {
            "fixed_time",
            "room_no_overlap",
            "teacher_no_overlap",
            "calendar_no_overlap",
            "minimize_changes",
        }
    )
    event = payload.get("event") or {}
    date_window = int(payload.get("date_window_days", 7))
    request_date_from = _parse_date(payload.get("date_from"))
    request_date_to = _parse_date(payload.get("date_to"))
    change_weight = (
        int(payload.get("change_weight", 100000)) if "minimize_changes" in solver_rules else 0
    )
    result = _empty_result(presolved=True)
    if not sessions or not rooms:
        result["conflict_rule_ids"], result["priority_explanations"] = _date_infeasible_diagnostics(
            payload
        )
        result["priority_rule_ids"] = list(result["conflict_rule_ids"])
        return result

    choices: dict[str, list[tuple[dict[str, Any], cp_model.IntVar]]] = {}
    room_capacity: dict[tuple[int, str, str], list[cp_model.IntVar]] = {}
    class_capacity: dict[tuple[tuple[str, str, str], int, str, str], list[cp_model.IntVar]] = {}
    person_capacity: dict[tuple[str, int, str, str], list[cp_model.IntVar]] = {}
    calendar_capacity: dict[tuple[str, int, str, str], list[cp_model.IntVar]] = {}
    objective_terms: list[Any] = []

    for session in sessions:
        course_id = str(session["business_id"])
        original_date = _parse_date(session.get("lesson_date"))
        if original_date is None:
            model.add_bool_or([])
            continue
        lower = original_date - timedelta(days=date_window)
        upper = original_date + timedelta(days=date_window)
        if request_date_from:
            lower = max(lower, request_date_from)
        if request_date_to:
            upper = min(upper, request_date_to)
        matching_rules = [rule for rule in rules if _session_matches_rule(session, "", rule)]
        for rule in matching_rules:
            if rule.get("hardness") != "hard":
                continue
            scope = rule.get("scope") or {}
            constraint_type = rule.get("constraint_type")
            if constraint_type in {"date_window", "date_range", "allowed_date_range"}:
                symmetric_days = scope.get("date_window_days", scope.get("days"))
                before_days = scope.get("before_days", symmetric_days)
                after_days = scope.get("after_days", symmetric_days)
                if before_days is not None:
                    lower = max(lower, original_date - timedelta(days=int(before_days)))
                if after_days is not None:
                    upper = min(upper, original_date + timedelta(days=int(after_days)))
                rule_from = _parse_date(scope.get("date_from"))
                rule_to = _parse_date(scope.get("date_to"))
                if rule_from:
                    lower = max(lower, rule_from)
                if rule_to:
                    upper = min(upper, rule_to)
            elif constraint_type == "fixed_date":
                fixed_date = _parse_date(scope.get("date") or scope.get("date_from"))
                if fixed_date:
                    lower = max(lower, fixed_date)
                    upper = min(upper, fixed_date)
        allowed_dates = (
            [lower + timedelta(days=offset) for offset in range((upper - lower).days + 1)]
            if lower <= upper
            else []
        )
        own_choices: list[tuple[dict[str, Any], cp_model.IntVar]] = []
        for candidate_date in allowed_dates:
            for start_time, end_time in _candidate_clock_windows(session):
                slot_id = _slot_for_date(slots, candidate_date, start_time, end_time)
                if defines_time_slots and not slot_id:
                    continue
                blocked = False
                for rule in matching_rules:
                    if rule.get("hardness") != "hard":
                        continue
                    scope = rule.get("scope") or {}
                    slot_ids = {str(item) for item in scope.get("slot_ids") or [] if item}
                    if scope.get("slot_id"):
                        slot_ids.add(str(scope["slot_id"]))
                    constraint_type = rule.get("constraint_type")
                    if (constraint_type == "fixed_slot" and slot_id not in slot_ids) or (
                        constraint_type in {"forbidden_slot", "unavailable_slot"}
                        and slot_id in slot_ids
                    ):
                        blocked = True
                        break
                if blocked or _event_blocks_option(event, session, candidate_date, slot_id):
                    continue
                start_minute = _clock_minutes(start_time)
                duration = max(1, _clock_minutes(end_time) - start_minute)
                option = {
                    "lesson_date": candidate_date,
                    "start_time": start_time,
                    "end_time": end_time,
                    "slot_business_id": slot_id or str(session.get("suggested_slot_id") or ""),
                    "absolute_start": candidate_date.toordinal() * 1440 + start_minute,
                    "duration": duration,
                }
                variable = model.new_bool_var(
                    f"grid_{course_id}_{candidate_date.toordinal()}_{start_minute}"
                )
                own_choices.append((option, variable))
                time_key = (
                    candidate_date.toordinal(),
                    start_time,
                    end_time,
                )
                room_capacity.setdefault(time_key, []).append(variable)
                for class_key in _class_scope_keys(session):
                    class_capacity.setdefault((class_key, *time_key), []).append(variable)
                if "teacher_no_overlap" in solver_rules:
                    for teacher_id in _session_teacher_ids(session):
                        if _is_person(teachers.get(teacher_id, {})):
                            person_capacity.setdefault((teacher_id, *time_key), []).append(variable)
                if "calendar_no_overlap" in solver_rules:
                    for calendar_user_id in _calendar_user_ids(session, teachers):
                        calendar_capacity.setdefault((calendar_user_id, *time_key), []).append(
                            variable
                        )
                delta = abs((candidate_date - original_date).days)
                if delta:
                    objective_terms.append(change_weight * delta * variable)

                for rule in matching_rules:
                    if rule.get("hardness") != "soft":
                        continue
                    scope = rule.get("scope") or {}
                    constraint_type = rule.get("constraint_type")
                    penalized = False
                    if constraint_type in {"fixed_date", "preferred_date"}:
                        preferred = _parse_date(scope.get("date") or scope.get("date_from"))
                        penalized = bool(preferred and candidate_date != preferred)
                    elif constraint_type in {
                        "date_window",
                        "date_range",
                        "allowed_date_range",
                    }:
                        rule_from = _parse_date(scope.get("date_from"))
                        rule_to = _parse_date(scope.get("date_to"))
                        symmetric_days = scope.get("date_window_days", scope.get("days"))
                        if symmetric_days is not None:
                            rule_from = original_date - timedelta(days=int(symmetric_days))
                            rule_to = original_date + timedelta(days=int(symmetric_days))
                        penalized = bool(
                            (rule_from and candidate_date < rule_from)
                            or (rule_to and candidate_date > rule_to)
                        )
                    elif constraint_type in {
                        "fixed_slot",
                        "preferred_slot",
                        "forbidden_slot",
                        "unavailable_slot",
                    }:
                        rule_slots = {str(item) for item in scope.get("slot_ids") or [] if item}
                        if scope.get("slot_id"):
                            rule_slots.add(str(scope["slot_id"]))
                        matches = slot_id in rule_slots
                        penalized = (
                            not matches
                            if constraint_type in {"fixed_slot", "preferred_slot"}
                            else matches
                        )
                    if penalized:
                        objective_terms.append(max(1, int(rule.get("weight") or 1)) * variable)
        if own_choices:
            model.add_exactly_one(variable for _option, variable in own_choices)
            choices[course_id] = own_choices
        else:
            model.add_bool_or([])

    if "room_no_overlap" in solver_rules:
        for variables in room_capacity.values():
            model.add(sum(variables) <= len(rooms))
    for variables in class_capacity.values():
        model.add_at_most_one(variables)
    if "teacher_no_overlap" in solver_rules:
        for variables in person_capacity.values():
            model.add_at_most_one(variables)
    if "calendar_no_overlap" in solver_rules:
        for variables in calendar_capacity.values():
            model.add_at_most_one(variables)

    result["presolve_infeasible"] = False
    model.minimize(sum(objective_terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(payload.get("time_limit_seconds", 30))
    solver.parameters.random_seed = int(payload.get("random_seed", 2026))
    solver.parameters.num_search_workers = max(1, int(payload.get("search_workers", 8)))
    status_code = solver.solve(model)
    result["model_status"] = STATUS_NAMES.get(status_code, "UNKNOWN")
    result["wall_time_seconds"] = solver.wall_time
    if status_code not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        if status_code == cp_model.INFEASIBLE:
            result["conflict_rule_ids"], result["priority_explanations"] = (
                _date_infeasible_diagnostics(payload)
            )
            result["priority_rule_ids"] = list(result["conflict_rule_ids"])
        return result

    result["objective_value"] = solver.objective_value
    result["best_bound"] = solver.best_objective_bound
    selected_options: dict[str, dict[str, Any]] = {}
    for course_id, own_choices in choices.items():
        selected_options[course_id] = next(
            option for option, variable in own_choices if solver.boolean_value(variable)
        )

    room_end = {room_id: -1 for room_id in rooms}
    assigned_rooms: dict[str, str] = {}
    all_sessions = {str(item["business_id"]): item for item in payload.get("course_sessions", [])}
    for course_id, room_option in sorted(
        selected_options.items(),
        key=lambda item: (
            int(item[1]["absolute_start"]),
            int(item[1]["absolute_start"]) + int(item[1]["duration"]),
            item[0],
        ),
    ):
        start = int(room_option["absolute_start"])
        end = start + int(room_option["duration"])
        session = all_sessions[course_id]
        preferred_rooms = [
            str(item)
            for item in session.get("candidate_room_business_ids") or []
            if str(item) in rooms
        ]
        original = str(session.get("original_room_business_id") or "")
        if original in rooms and original not in preferred_rooms:
            preferred_rooms.append(original)
        available = [
            room_id for room_id, occupied_until in room_end.items() if occupied_until <= start
        ]
        candidates = available if "room_no_overlap" in solver_rules else list(rooms)
        chosen_room = next((item for item in preferred_rooms if item in candidates), None)
        if chosen_room is None:
            chosen_room = min(candidates, key=lambda item: (room_end[item], item))
        assigned_rooms[course_id] = chosen_room
        room_end[chosen_room] = end

    for session in sessions:
        course_id = str(session["business_id"])
        selected_option = selected_options.get(course_id)
        if selected_option is None:
            continue
        result["assignments"].append(
            {
                "course_session_id": session["id"],
                "course_business_id": course_id,
                "class_business_id": session["class_business_id"],
                "teacher_business_id": session["teacher_business_id"],
                "lesson_date": selected_option["lesson_date"].isoformat(),
                "room_business_id": assigned_rooms[course_id],
                "slot_business_id": selected_option["slot_business_id"],
                "start_time": selected_option["start_time"],
                "end_time": selected_option["end_time"],
            }
        )
    return result


def _solve_date_aware(payload: dict[str, Any]) -> dict[str, Any]:
    if _can_use_discrete_date_grid(payload):
        return _solve_date_aware_grid(payload)
    model = cp_model.CpModel()
    sessions = _selected_sessions(payload)
    rooms = {
        item["business_id"]: item for item in payload.get("rooms", []) if item.get("is_active")
    }
    teachers = {item["business_id"]: item for item in payload.get("teachers", [])}
    slot_details = {
        str(item["business_id"]): item
        for item in payload.get("time_slots", [])
        if item.get("is_open")
    }
    # 时段标识按 (星期, 开始, 结束) 定位：同一开始时间可以有多种时长。
    slots = {
        (item.get("weekday"), item.get("start_time"), item.get("end_time")): item["business_id"]
        for item in slot_details.values()
    }
    defines_time_slots = bool(payload.get("time_slots"))
    rules = _normalized_rules(payload)
    solver_rules = set(
        payload.get("solver_rules")
        or {
            "fixed_time",
            "room_no_overlap",
            "teacher_no_overlap",
            "calendar_no_overlap",
            "minimize_changes",
        }
    )
    event = payload.get("event") or {}
    date_window = int(payload.get("date_window_days", 7))
    request_date_from = _parse_date(payload.get("date_from"))
    request_date_to = _parse_date(payload.get("date_to"))
    change_weight = (
        int(payload.get("change_weight", 100000)) if "minimize_changes" in solver_rules else 0
    )

    result = _empty_result(presolved=True)
    if not sessions or not rooms:
        result["conflict_rule_ids"], result["priority_explanations"] = _date_infeasible_diagnostics(
            payload
        )
        result["priority_rule_ids"] = list(result["conflict_rule_ids"])
        return result

    previous_assignments = list(payload.get("previous_assignments", []))
    previous_by_course = {
        str(item.get("course_business_id")): item for item in previous_assignments
    }
    selected_business_ids = {str(item["business_id"]) for item in sessions}
    has_fixed_parent_rooms = any(
        str(item.get("course_business_id") or "") not in selected_business_ids
        for item in previous_assignments
    )
    room_constraint_types = {
        "fixed_room",
        "preferred_room",
        "forbidden_room",
        "unavailable_room",
    }
    explicit_room_choices = bool(
        previous_assignments
        or has_fixed_parent_rooms
        or event.get("event_type") == "room_outage"
        or any(session.get("is_locked") for session in sessions)
        or any(rule.get("constraint_type") in room_constraint_types for rule in rules)
    )

    intervals: dict[str, cp_model.IntervalVar] = {}
    time_choices: dict[str, cp_model.IntVar] = {}
    time_options: dict[str, list[dict[str, Any]]] = {}
    room_choices: dict[tuple[str, str], cp_model.IntVar] = {}
    room_intervals: dict[str, list[cp_model.IntervalVar]] = {room_id: [] for room_id in rooms}
    aggregate_room_intervals: list[cp_model.IntervalVar] = []
    class_intervals: dict[tuple[str, str, str], list[cp_model.IntervalVar]] = {}
    calendar_intervals: dict[str, list[cp_model.IntervalVar]] = {}
    person_intervals: dict[str, list[cp_model.IntervalVar]] = {}
    objective_terms: list[Any] = []
    changed_terms: list[Any] = []
    secondary_upper_bound = 0

    all_sessions = {str(item["business_id"]): item for item in payload.get("course_sessions", [])}
    for index, previous in enumerate(previous_assignments):
        course_id = str(previous.get("course_business_id") or "")
        if not course_id or course_id in selected_business_ids:
            continue
        course = all_sessions.get(course_id)
        lesson_date = _parse_date(previous.get("lesson_date"))
        if not course or not lesson_date:
            continue
        assigned_slot = slot_details.get(str(previous.get("slot_business_id") or ""))
        if assigned_slot:
            fixed_start = str(assigned_slot.get("start_time") or "")
            fixed_end = str(assigned_slot.get("end_time") or "")
        else:
            windows = _candidate_clock_windows(course)
            if not windows:
                continue
            fixed_start, fixed_end = windows[0]
        if not fixed_start or not fixed_end:
            continue
        start_minute = _clock_minutes(fixed_start)
        duration = max(1, _clock_minutes(fixed_end) - start_minute)
        fixed_interval = model.new_fixed_size_interval_var(
            lesson_date.toordinal() * 1440 + start_minute,
            duration,
            f"parent_interval_{index}_{course_id}",
        )
        room_id = str(previous.get("room_business_id") or "")
        if explicit_room_choices and room_id in room_intervals:
            room_intervals[room_id].append(fixed_interval)
        else:
            aggregate_room_intervals.append(fixed_interval)
        for class_key in _class_scope_keys(course):
            class_intervals.setdefault(class_key, []).append(fixed_interval)
        for calendar_user_id in _calendar_user_ids(course, teachers):
            calendar_intervals.setdefault(calendar_user_id, []).append(fixed_interval)
        for teacher_id in _session_teacher_ids(course):
            if _is_person(teachers.get(teacher_id, {})):
                person_intervals.setdefault(teacher_id, []).append(fixed_interval)

    for session in sessions:
        course_id = str(session["business_id"])
        original_date = _parse_date(session.get("lesson_date"))
        if original_date is None:
            model.add_bool_or([])
            continue
        previous = previous_by_course.get(course_id)
        baseline_date = _parse_date(previous.get("lesson_date")) if previous else original_date
        baseline_date = baseline_date or original_date
        lower = baseline_date - timedelta(days=date_window)
        upper = baseline_date + timedelta(days=date_window)
        if request_date_from:
            lower = max(lower, request_date_from)
        if request_date_to:
            upper = min(upper, request_date_to)
        matching_rules = [rule for rule in rules if _session_matches_rule(session, "", rule)]
        for rule in matching_rules:
            if rule.get("hardness") != "hard":
                continue
            scope = rule.get("scope") or {}
            constraint_type = rule.get("constraint_type")
            if constraint_type in {"date_window", "date_range", "allowed_date_range"}:
                symmetric_days = scope.get("date_window_days", scope.get("days"))
                before_days = scope.get("before_days", symmetric_days)
                after_days = scope.get("after_days", symmetric_days)
                if before_days is not None:
                    lower = max(lower, original_date - timedelta(days=int(before_days)))
                if after_days is not None:
                    upper = min(upper, original_date + timedelta(days=int(after_days)))
                rule_from = _parse_date(scope.get("date_from"))
                rule_to = _parse_date(scope.get("date_to"))
                if rule_from:
                    lower = max(lower, rule_from)
                if rule_to:
                    upper = min(upper, rule_to)
            elif constraint_type == "fixed_date":
                fixed_date = _parse_date(scope.get("date") or scope.get("date_from"))
                if fixed_date:
                    lower = max(lower, fixed_date)
                    upper = min(upper, fixed_date)
        allowed_dates = (
            [lower + timedelta(days=index) for index in range((upper - lower).days + 1)]
            if lower <= upper
            else []
        )
        if session.get("is_locked"):
            allowed_dates = [item for item in allowed_dates if item == baseline_date]

        windows = _candidate_clock_windows(session)
        if session.get("is_locked") and windows:
            parent_slot = slot_details.get(str((previous or {}).get("slot_business_id"))) or {}
            fixed_window = (
                str(parent_slot.get("start_time") or session.get("fixed_start_time") or ""),
                str(parent_slot.get("end_time") or session.get("fixed_end_time") or ""),
            )
            windows = [fixed_window] if fixed_window in windows else [windows[0]]

        options: list[dict[str, Any]] = []
        for candidate_date in allowed_dates:
            for start_time, end_time in windows:
                slot_id = _slot_for_date(slots, candidate_date, start_time, end_time)
                if defines_time_slots and not slot_id:
                    continue
                blocked = False
                for rule in matching_rules:
                    if rule.get("hardness") != "hard":
                        continue
                    scope = rule.get("scope") or {}
                    slot_ids = {str(item) for item in scope.get("slot_ids") or [] if item}
                    if scope.get("slot_id"):
                        slot_ids.add(str(scope["slot_id"]))
                    constraint_type = rule.get("constraint_type")
                    if (constraint_type == "fixed_slot" and slot_id not in slot_ids) or (
                        constraint_type in {"forbidden_slot", "unavailable_slot"}
                        and slot_id in slot_ids
                    ):
                        blocked = True
                if blocked or _event_blocks_option(event, session, candidate_date, slot_id):
                    continue
                start_minute = _clock_minutes(start_time)
                duration = max(1, _clock_minutes(end_time) - start_minute)
                options.append(
                    {
                        "lesson_date": candidate_date,
                        "start_time": start_time,
                        "end_time": end_time,
                        "slot_business_id": slot_id or str(session.get("suggested_slot_id") or ""),
                        "absolute_start": candidate_date.toordinal() * 1440 + start_minute,
                        "duration": duration,
                    }
                )
        if not options:
            model.add_bool_or([])
            continue

        choice_var = model.new_int_var(0, len(options) - 1, f"time_choice_{course_id}")
        ordinals = sorted({item["lesson_date"].toordinal() for item in options})
        day_var = model.new_int_var_from_domain(
            cp_model.Domain.from_values(ordinals), f"day_{course_id}"
        )
        start_values = [int(item["absolute_start"]) for item in options]
        duration_values = [int(item["duration"]) for item in options]
        end_values = [int(item["absolute_start"]) + int(item["duration"]) for item in options]
        start_var = model.new_int_var(min(start_values), max(start_values), f"start_{course_id}")
        duration_var = model.new_int_var(
            min(duration_values), max(duration_values), f"duration_{course_id}"
        )
        end_var = model.new_int_var(min(end_values), max(end_values), f"end_{course_id}")
        model.add_element(
            choice_var, [item["lesson_date"].toordinal() for item in options], day_var
        )
        model.add_element(choice_var, start_values, start_var)
        model.add_element(choice_var, duration_values, duration_var)
        model.add_element(choice_var, end_values, end_var)
        interval = model.new_interval_var(start_var, duration_var, end_var, f"interval_{course_id}")
        intervals[course_id] = interval
        if not explicit_room_choices:
            aggregate_room_intervals.append(interval)
        time_choices[course_id] = choice_var
        time_options[course_id] = options

        date_deltas = [abs((item["lesson_date"] - baseline_date).days) for item in options]
        date_delta = model.new_int_var(
            min(date_deltas), max(date_deltas), f"date_delta_{course_id}"
        )
        model.add_element(choice_var, date_deltas, date_delta)
        distance_weight = 1 if previous and change_weight else change_weight
        objective_terms.append(distance_weight * date_delta)
        secondary_upper_bound += distance_weight * max(date_deltas)

        for rule in matching_rules:
            if rule.get("hardness") != "soft":
                continue
            constraint_type = rule.get("constraint_type")
            scope = rule.get("scope") or {}
            penalties: list[int] | None = None
            if constraint_type in {
                "fixed_date",
                "preferred_date",
                "date_window",
                "date_range",
                "allowed_date_range",
            }:
                preferred_dates = {item["lesson_date"].toordinal() for item in options}
                fixed_date = _parse_date(scope.get("date") or scope.get("date_from"))
                if constraint_type in {"fixed_date", "preferred_date"} and fixed_date:
                    preferred_dates = {fixed_date.toordinal()}
                elif constraint_type in {"date_window", "date_range", "allowed_date_range"}:
                    rule_from = _parse_date(scope.get("date_from"))
                    rule_to = _parse_date(scope.get("date_to"))
                    symmetric_days = scope.get("date_window_days", scope.get("days"))
                    if symmetric_days is not None:
                        rule_from = original_date - timedelta(days=int(symmetric_days))
                        rule_to = original_date + timedelta(days=int(symmetric_days))
                    preferred_dates = {
                        item["lesson_date"].toordinal()
                        for item in options
                        if (not rule_from or item["lesson_date"] >= rule_from)
                        and (not rule_to or item["lesson_date"] <= rule_to)
                    }
                penalties = [
                    int(item["lesson_date"].toordinal() not in preferred_dates) for item in options
                ]
            elif constraint_type in {
                "fixed_slot",
                "preferred_slot",
                "forbidden_slot",
                "unavailable_slot",
            }:
                rule_slot_ids = {str(item) for item in scope.get("slot_ids") or [] if item}
                if scope.get("slot_id"):
                    rule_slot_ids.add(str(scope["slot_id"]))
                prefer_match = constraint_type in {"fixed_slot", "preferred_slot"}
                penalties = [
                    int((str(item["slot_business_id"]) in rule_slot_ids) != prefer_match)
                    for item in options
                ]
            if penalties is None:
                continue
            penalty = model.new_int_var(
                0, 1, f"time_rule_penalty_{course_id}_{rule['business_id']}"
            )
            model.add_element(choice_var, penalties, penalty)
            weight = max(1, int(rule.get("weight") or 1))
            objective_terms.append(weight * penalty)
            secondary_upper_bound += weight

        if explicit_room_choices:
            valid_room_choices: list[cp_model.IntVar] = []
            source_rooms = {
                str(item) for item in session.get("candidate_room_business_ids") or [] if item
            }
            original_room = str(session.get("original_room_business_id") or "")
            if original_room:
                source_rooms.add(original_room)
            fixed_room_ids: set[str] | None = None
            forbidden_room_ids: set[str] = set()
            for rule in matching_rules:
                if rule.get("hardness") != "hard":
                    continue
                constraint_type = rule.get("constraint_type")
                rule_room_ids = _rule_room_ids(rule)
                if constraint_type == "fixed_room":
                    fixed_room_ids = (
                        rule_room_ids
                        if fixed_room_ids is None
                        else fixed_room_ids.intersection(rule_room_ids)
                    )
                elif constraint_type in {"forbidden_room", "unavailable_room"}:
                    forbidden_room_ids.update(rule_room_ids)
            locked_room_id = str((previous or {}).get("room_business_id") or original_room)
            if session.get("is_locked") and locked_room_id:
                locked_room = {locked_room_id}
                fixed_room_ids = (
                    locked_room
                    if fixed_room_ids is None
                    else fixed_room_ids.intersection(locked_room)
                )

            event_from = _parse_date(event.get("date_from"))
            event_to = _parse_date(event.get("date_to"))
            for room_id in rooms:
                if fixed_room_ids is not None and room_id not in fixed_room_ids:
                    continue
                if room_id in forbidden_room_ids:
                    continue
                room_event = (
                    event.get("event_type") == "room_outage"
                    and room_id == event.get("room_business_id")
                    and _event_targets_session(event, session)
                )
                if (
                    room_event
                    and not event.get("slot_business_ids")
                    and not event_from
                    and not event_to
                ):
                    continue
                selected = model.new_bool_var(f"room_{course_id}_{room_id}")
                optional = model.new_optional_interval_var(
                    start_var,
                    duration_var,
                    end_var,
                    selected,
                    f"room_interval_{course_id}_{room_id}",
                )
                room_choices[(course_id, room_id)] = selected
                room_intervals[room_id].append(optional)
                valid_room_choices.append(selected)
                if source_rooms and room_id not in source_rooms:
                    objective_terms.append(selected)
                    secondary_upper_bound += 1
                if room_event:
                    outage_slots = set(event.get("slot_business_ids") or [])
                    for option_index, option in enumerate(options):
                        option_date = option["lesson_date"]
                        if event_from and option_date < event_from:
                            continue
                        if event_to and option_date > event_to:
                            continue
                        if outage_slots and option["slot_business_id"] not in outage_slots:
                            continue
                        model.add(choice_var != option_index).only_enforce_if(selected)
                for rule in matching_rules:
                    if rule.get("hardness") != "soft":
                        continue
                    constraint_type = rule.get("constraint_type")
                    rule_room_ids = _rule_room_ids(rule)
                    penalized = (
                        constraint_type in {"fixed_room", "preferred_room"}
                        and room_id not in rule_room_ids
                    ) or (
                        constraint_type in {"forbidden_room", "unavailable_room"}
                        and room_id in rule_room_ids
                    )
                    if penalized:
                        weight = max(1, int(rule.get("weight") or 1))
                        objective_terms.append(weight * selected)
                        secondary_upper_bound += weight
            if valid_room_choices:
                model.add_exactly_one(valid_room_choices)
            else:
                model.add_bool_or([])

        if previous and change_weight:
            time_differences = [
                int(item["lesson_date"] != baseline_date
                    or item["slot_business_id"] != previous.get("slot_business_id"))
                for item in options
            ]
            time_changed = model.new_bool_var(f"time_changed_{course_id}")
            model.add_element(choice_var, time_differences, time_changed)
            same_room = room_choices.get((course_id, str(previous.get("room_business_id"))), 0)
            changed = model.new_bool_var(f"changed_{course_id}")
            model.add_max_equality(changed, [time_changed, 1 - same_room])
            changed_terms.append(changed)

    if "room_no_overlap" in solver_rules:
        if explicit_room_choices:
            for grouped in room_intervals.values():
                if grouped:
                    model.add_no_overlap(grouped)
        elif aggregate_room_intervals:
            model.add_cumulative(
                aggregate_room_intervals,
                [1] * len(aggregate_room_intervals),
                len(rooms),
            )

    for session in sessions:
        class_interval = intervals.get(str(session["business_id"]))
        if class_interval is None:
            continue
        for class_key in _class_scope_keys(session):
            class_intervals.setdefault(class_key, []).append(class_interval)
    for grouped in class_intervals.values():
        if grouped:
            model.add_no_overlap(grouped)

    for session in sessions:
        teacher_interval = intervals.get(str(session["business_id"]))
        if teacher_interval is None:
            continue
        for calendar_user_id in _calendar_user_ids(session, teachers):
            calendar_intervals.setdefault(calendar_user_id, []).append(teacher_interval)
        for teacher_id in _session_teacher_ids(session):
            if _is_person(teachers.get(teacher_id, {})):
                person_intervals.setdefault(teacher_id, []).append(teacher_interval)
    if "teacher_no_overlap" in solver_rules:
        for grouped in person_intervals.values():
            if grouped:
                model.add_no_overlap(grouped)
    if "calendar_no_overlap" in solver_rules:
        for grouped in calendar_intervals.values():
            if grouped:
                model.add_no_overlap(grouped)

    result["presolve_infeasible"] = False
    # 上界来自实际候选域和规则权重，使少改一节严格优先于所有次级偏好。
    model.minimize((secondary_upper_bound + 1) * sum(changed_terms) + sum(objective_terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(payload.get("time_limit_seconds", 30))
    solver.parameters.random_seed = int(payload.get("random_seed", 2026))
    solver.parameters.num_search_workers = max(1, int(payload.get("search_workers", 8)))
    status_code = solver.solve(model)
    result["model_status"] = STATUS_NAMES.get(status_code, "UNKNOWN")
    result["wall_time_seconds"] = solver.wall_time
    if status_code not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        if status_code == cp_model.INFEASIBLE:
            result["conflict_rule_ids"], result["priority_explanations"] = (
                _date_infeasible_diagnostics(payload)
            )
            result["priority_rule_ids"] = list(result["conflict_rule_ids"])
        return result
    result["objective_value"] = solver.objective_value
    result["best_bound"] = solver.best_objective_bound
    selected_options = {
        course_id: options[solver.value(time_choices[course_id])]
        for course_id, options in time_options.items()
    }
    assigned_rooms: dict[str, str] = {}
    if not explicit_room_choices:
        room_end = {room_id: -1 for room_id in rooms}
        for course_id, option in sorted(
            selected_options.items(),
            key=lambda item: (
                int(item[1]["absolute_start"]),
                int(item[1]["absolute_start"]) + int(item[1]["duration"]),
                item[0],
            ),
        ):
            start = int(option["absolute_start"])
            end = start + int(option["duration"])
            session = all_sessions[course_id]
            preferred = [
                str(item)
                for item in session.get("candidate_room_business_ids") or []
                if str(item) in rooms
            ]
            original = str(session.get("original_room_business_id") or "")
            if original in rooms and original not in preferred:
                preferred.append(original)
            available = [
                room_id for room_id, occupied_until in room_end.items() if occupied_until <= start
            ]
            candidates = available if "room_no_overlap" in solver_rules else list(rooms)
            chosen_room = next((item for item in preferred if item in candidates), None)
            if chosen_room is None:
                chosen_room = min(candidates, key=lambda item: (room_end[item], item))
            assigned_rooms[course_id] = chosen_room
            room_end[chosen_room] = end
    for session in sessions:
        course_id = str(session["business_id"])
        selected_choice = time_choices.get(course_id)
        if selected_choice is None:
            continue
        option = selected_options[course_id]
        room_id = (
            next(
                room_id
                for candidate_course, room_id in room_choices
                if candidate_course == course_id
                and solver.boolean_value(room_choices[(candidate_course, room_id)])
            )
            if explicit_room_choices
            else assigned_rooms[course_id]
        )
        result["assignments"].append(
            {
                "course_session_id": session["id"],
                "course_business_id": course_id,
                "class_business_id": session["class_business_id"],
                "teacher_business_id": session["teacher_business_id"],
                "lesson_date": option["lesson_date"].isoformat(),
                "room_business_id": room_id,
                "slot_business_id": option["slot_business_id"],
                "start_time": option["start_time"],
                "end_time": option["end_time"],
            }
        )
    return result


def _build_model(payload: dict[str, Any], enabled_rule_ids: set[str] | None = None) -> BuiltModel:
    model = cp_model.CpModel()
    teachers = {item["business_id"]: item for item in payload["teachers"]}
    rooms = {item["business_id"]: item for item in payload["rooms"]}
    slots = {item["business_id"]: item for item in payload["time_slots"] if item["is_open"]}
    sessions = _selected_sessions(payload)
    session_by_id = {item["business_id"]: item for item in sessions}
    event = payload.get("event") or {}
    variables: dict[tuple[str, str, str], cp_model.IntVar] = {}

    # 范围外的已发布课次不重新决策，但它们占用的教室/教师/班级时段必须让出来，
    # 否则「只调一个班」会把别的班排进已经占用的资源。
    selected_ids = set(session_by_id)
    all_sessions = {str(item["business_id"]): item for item in payload.get("course_sessions", [])}
    busy_rooms: set[tuple[str, str]] = set()
    busy_teachers: set[tuple[str, str]] = set()
    busy_classes: set[tuple[tuple[str, str, str], str]] = set()
    for previous in payload.get("previous_assignments", []):
        course_id = str(previous.get("course_business_id") or "")
        slot_id = str(previous.get("slot_business_id") or "")
        if not course_id or course_id in selected_ids or not slot_id:
            continue
        course = all_sessions.get(course_id)
        if course is None:
            continue
        room_id = str(previous.get("room_business_id") or "")
        if room_id:
            busy_rooms.add((room_id, slot_id))
        for teacher_id in _session_teacher_ids(course):
            if _is_person(teachers.get(teacher_id, {})):
                busy_teachers.add((teacher_id, slot_id))
        for class_key in _class_scope_keys(course):
            busy_classes.add((class_key, slot_id))

    for course in sessions:
        feasible: list[cp_model.IntVar] = []
        course_teachers = _session_teacher_ids(course)
        course_classes = _class_scope_keys(course)
        for room_id, room in rooms.items():
            if not room["is_active"]:
                continue
            for slot_id in slots:
                if _event_blocks(event, course, room_id, slot_id):
                    continue
                if (room_id, slot_id) in busy_rooms:
                    continue
                if any((teacher_id, slot_id) in busy_teachers for teacher_id in course_teachers):
                    continue
                if any((class_key, slot_id) in busy_classes for class_key in course_classes):
                    continue
                variable = model.new_bool_var(f"x_{course['business_id']}_{room_id}_{slot_id}")
                variables[(course["business_id"], room_id, slot_id)] = variable
                feasible.append(variable)
        if feasible:
            model.add_exactly_one(feasible)
        else:
            model.add_bool_or([])

    solver_rules = set(
        payload.get("solver_rules")
        or {
            "fixed_time",
            "room_no_overlap",
            "teacher_no_overlap",
            "calendar_no_overlap",
            "minimize_changes",
        }
    )
    person_teacher_ids = (
        [key for key, item in teachers.items() if _is_person(item)]
        if "teacher_no_overlap" in solver_rules
        else []
    )
    for teacher_id in person_teacher_ids:
        for slot_id in slots:
            model.add_at_most_one(
                variable
                for (course_id, room_id, current_slot), variable in variables.items()
                if current_slot == slot_id
                and teacher_id in _session_teacher_ids(session_by_id[course_id])
            )
    class_ids = {key for item in sessions for key in _class_scope_keys(item)}
    for class_id in class_ids:
        for slot_id in slots:
            model.add_at_most_one(
                variable
                for (course_id, room_id, current_slot), variable in variables.items()
                if current_slot == slot_id
                and class_id in _class_scope_keys(session_by_id[course_id])
            )
    for room_id in rooms:
        for slot_id in slots:
            model.add_at_most_one(
                variable
                for (course_id, current_room, current_slot), variable in variables.items()
                if current_room == room_id and current_slot == slot_id
            )

    assumption_index: dict[int, str] = {}
    supported_hard_rules = {"fixed_slot", "forbidden_slot", "unavailable_slot", "fixed_room"}
    for rule in _normalized_rules(payload):
        if rule["hardness"] != "hard" or rule["constraint_type"] not in supported_hard_rules:
            continue
        if enabled_rule_ids is not None and rule["business_id"] not in enabled_rule_ids:
            continue
        literal = model.new_bool_var(f"assume_{rule['business_id']}")
        assumption_index[literal.index] = rule["business_id"]
        model.add_assumption(literal)
        scope = rule.get("scope") or {}
        actor_ids = set(rule.get("actor_ids") or [])
        matching: list[cp_model.IntVar] = []
        for (course_id, room_id, slot_id), variable in variables.items():
            course = session_by_id[course_id]
            actor_matches = (
                not actor_ids
                or course_id in actor_ids
                or bool(actor_ids.intersection(_session_teacher_ids(course)))
                or course["class_business_id"] in actor_ids
                or room_id in actor_ids
            )
            if not actor_matches:
                continue
            if rule["constraint_type"] == "fixed_slot" and slot_id == scope.get("slot_id"):
                matching.append(variable)
            elif rule["constraint_type"] in {
                "forbidden_slot",
                "unavailable_slot",
            } and slot_id in set(scope.get("slot_ids") or [scope.get("slot_id")]):
                model.add(variable == 0).only_enforce_if(literal)
            elif rule["constraint_type"] == "fixed_room" and room_id == scope.get("room_id"):
                matching.append(variable)
        if rule["constraint_type"] in {"fixed_slot", "fixed_room"}:
            if matching:
                model.add(sum(matching) == 1).only_enforce_if(literal)
            else:
                model.add_bool_or([]).only_enforce_if(literal)

    change_weight = int(payload.get("change_weight", 100000))
    previous = {
        item["course_business_id"]: (item["room_business_id"], item["slot_business_id"])
        for item in payload.get("previous_assignments", [])
    }
    objective_terms: list[Any] = []
    for (course_id, room_id, slot_id), variable in variables.items():
        if course_id in previous and previous[course_id] != (room_id, slot_id):
            objective_terms.append(change_weight * variable)

    soft_rules = [rule for rule in _normalized_rules(payload) if rule["hardness"] == "soft"]
    for rule in soft_rules:
        weight = max(1, int(rule.get("weight") or 1))
        scope = rule.get("scope") or {}
        actor_ids = set(rule.get("actor_ids") or [])
        constraint_type = rule["constraint_type"]
        slot_ids = set(scope.get("slot_ids") or [])
        if scope.get("slot_id"):
            slot_ids.add(scope["slot_id"])
        for (course_id, room_id, slot_id), variable in variables.items():
            course = session_by_id[course_id]
            actor_matches = (
                not actor_ids
                or course_id in actor_ids
                or bool(actor_ids.intersection(_session_teacher_ids(course)))
                or course["class_business_id"] in actor_ids
                or room_id in actor_ids
            )
            if not actor_matches:
                continue
            penalized = (
                (constraint_type in {"forbidden_slot", "unavailable_slot"} and slot_id in slot_ids)
                or (constraint_type in {"fixed_slot", "preferred_slot"} and slot_id not in slot_ids)
                or (constraint_type == "fixed_room" and room_id != scope.get("room_id"))
            )
            if penalized:
                objective_terms.append(weight * variable)

        if constraint_type == "consecutive_sessions":
            matching_courses = [
                course
                for course in sessions
                if not actor_ids
                or course["business_id"] in actor_ids
                or bool(actor_ids.intersection(_session_teacher_ids(course)))
                or course["class_business_id"] in actor_ids
            ]
            matching_courses.sort(key=lambda item: item["business_id"])
            ordered_slots = sorted(
                slots.values(), key=lambda item: (item.get("weekday", ""), item.get("sequence", 0))
            )
            adjacent_pairs = {
                (first["business_id"], second["business_id"])
                for first, second in zip(ordered_slots, ordered_slots[1:], strict=False)
                if first.get("weekday") == second.get("weekday")
            }
            slot_choices: dict[tuple[str, str], cp_model.IntVar] = {}
            for course in matching_courses:
                for slot_id in slots:
                    room_choices = [
                        variable
                        for (current_course, _room_id, current_slot), variable in variables.items()
                        if current_course == course["business_id"] and current_slot == slot_id
                    ]
                    if not room_choices:
                        continue
                    choice = model.new_bool_var(f"slot_{course['business_id']}_{slot_id}")
                    model.add(choice == sum(room_choices))
                    slot_choices[(course["business_id"], slot_id)] = choice
            for index in range(0, len(matching_courses) - 1, 2):
                first = matching_courses[index]["business_id"]
                second = matching_courses[index + 1]["business_id"]
                adjacency_vars: list[cp_model.IntVar] = []
                for first_slot, second_slot in adjacent_pairs:
                    for left_slot, right_slot in (
                        (first_slot, second_slot),
                        (second_slot, first_slot),
                    ):
                        left = slot_choices.get((first, left_slot))
                        right = slot_choices.get((second, right_slot))
                        if left is None or right is None:
                            continue
                        adjacent = model.new_bool_var(
                            f"adjacent_{first}_{second}_{left_slot}_{right_slot}"
                        )
                        model.add(adjacent <= left)
                        model.add(adjacent <= right)
                        model.add(adjacent >= left + right - 1)
                        adjacency_vars.append(adjacent)
                if adjacency_vars:
                    objective_terms.append(weight * (1 - sum(adjacency_vars)))
                else:
                    objective_terms.append(weight)
    model.minimize(sum(objective_terms))
    return BuiltModel(
        model=model, variables=variables, assumption_index=assumption_index, sessions=sessions
    )


def _solve_once(
    payload: dict[str, Any],
    enabled_rule_ids: set[str] | None = None,
    time_limit: float | None = None,
) -> dict[str, Any]:
    built = _build_model(payload, enabled_rule_ids)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(
        time_limit or payload.get("time_limit_seconds", 30)
    )
    solver.parameters.random_seed = int(payload.get("random_seed", 2026))
    solver.parameters.num_search_workers = max(1, int(payload.get("search_workers", 8)))
    status_code = solver.solve(built.model)
    status_name = STATUS_NAMES.get(status_code, "UNKNOWN")
    result: dict[str, Any] = {
        "presolve_infeasible": False,
        "model_status": status_name,
        "objective_value": None,
        "best_bound": None,
        "wall_time_seconds": solver.wall_time,
        "assignments": [],
        "conflict_rule_ids": [],
        "priority_rule_ids": [],
        "priority_explanations": [],
    }
    if status_code in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        result["objective_value"] = solver.objective_value
        result["best_bound"] = solver.best_objective_bound
        sessions = {item["business_id"]: item for item in built.sessions}
        for (course_id, room_id, slot_id), variable in built.variables.items():
            if solver.boolean_value(variable):
                course = sessions[course_id]
                result["assignments"].append(
                    {
                        "course_session_id": course["id"],
                        "course_business_id": course_id,
                        "class_business_id": course["class_business_id"],
                        "teacher_business_id": course["teacher_business_id"],
                        "room_business_id": room_id,
                        "slot_business_id": slot_id,
                    }
                )
    elif status_code == cp_model.INFEASIBLE:
        core = solver.sufficient_assumptions_for_infeasibility()
        result["conflict_rule_ids"] = [
            built.assumption_index[index] for index in core if index in built.assumption_index
        ]
    return result


def solve_problem(payload: dict[str, Any]) -> dict[str, Any]:
    selected = _selected_sessions(payload)
    if not selected:
        return _empty_result("OPTIMAL", presolved=True)
    dated = [item for item in selected if _has_date_information(item)]
    if dated and len(dated) != len(selected):
        # 混排会让整批退化成无日期模型，已有日期的课次会被静默清空并重排到别的星期。
        missing = [str(item["business_id"]) for item in selected if not _has_date_information(item)]
        raise ValueError(
            "求解范围内混有缺少上课日期或固定起止时间的课次，无法确定使用哪种模型："
            + "、".join(missing[:10])
            + (f" 等 {len(missing)} 条" if len(missing) > 10 else "")
        )
    if dated:
        return _solve_date_aware(payload)
    result = _solve_once(payload)
    if result["model_status"] == "INFEASIBLE":
        priority_ids, explanations = _priority_recommendation(payload, result["conflict_rule_ids"])
        result["priority_rule_ids"] = priority_ids
        result["priority_explanations"] = explanations
    if result["model_status"] != "INFEASIBLE" or len(result["conflict_rule_ids"]) < 2:
        return result
    reduced = list(dict.fromkeys(result["conflict_rule_ids"]))
    for rule_id in list(reduced):
        candidate = set(reduced) - {rule_id}
        check = _solve_once(payload, enabled_rule_ids=candidate, time_limit=2.0)
        if check["model_status"] == "INFEASIBLE":
            reduced.remove(rule_id)
    result["conflict_rule_ids"] = reduced
    priority_ids, explanations = _priority_recommendation(payload, reduced)
    result["priority_rule_ids"] = priority_ids
    result["priority_explanations"] = explanations
    return result
