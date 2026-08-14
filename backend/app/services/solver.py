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
        rule
        for rule in payload.get("rules", [])
        if rule.get("business_id") in conflict_rule_ids
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
        return in_window and session["teacher_business_id"] == event.get("teacher_business_id")
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


def _session_matches_rule(session: dict[str, Any], room_id: str, rule: dict[str, Any]) -> bool:
    actor_ids = set(rule.get("actor_ids") or [])
    actor_type = str(rule.get("actor_type") or "").lower()
    if actor_type in {"room", "classroom", "教室"}:
        return not actor_ids or not room_id or room_id in actor_ids
    return (
        not actor_ids
        or session["business_id"] in actor_ids
        or session["teacher_business_id"] in actor_ids
        or session["class_business_id"] in actor_ids
        or room_id in actor_ids
    )


def _class_scope_key(session: dict[str, Any]) -> tuple[str, str, str]:
    """Identify a real class within its product scenario.

    Official data reuses labels such as “走读SMART班” across different
    product types. Those are parallel business scenarios, not one physical
    class, so the label alone must not create a class-overlap constraint.
    """
    return (
        str(session.get("business_line") or ""),
        str(session.get("product_type") or ""),
        str(session.get("class_business_id") or ""),
    )


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
                {**item, "hardness": item.get("hardness") or default_hardness}
                for item in items
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
                {**item, "hardness": item.get("hardness") or default_hardness}
                for item in items
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


def _slot_for_date(slots: dict[tuple[object, object], str], lesson_date: date, start: str) -> str:
    return slots.get((WEEKDAYS[lesson_date.weekday()], start), "")


def _event_targets_session(event: dict[str, Any], session: dict[str, Any]) -> bool:
    course_id = event.get("course_business_id")
    if course_id and course_id != session["business_id"]:
        return False
    if event.get("event_type") == "teacher_leave":
        teacher_id = event.get("teacher_business_id")
        calendar_user_id = event.get("calendar_user_id")
        return bool(
            (teacher_id and teacher_id == session["teacher_business_id"])
            or (calendar_user_id and calendar_user_id == session.get("calendar_user_id"))
        )
    return True


def _event_blocks_date(
    event: dict[str, Any],
    session: dict[str, Any],
    candidate: date,
    slots: dict[tuple[object, object], str],
) -> bool:
    if event.get("event_type") != "teacher_leave" or not _event_targets_session(event, session):
        return False
    slot_ids = set(event.get("slot_business_ids") or [])
    candidate_slot = _slot_for_date(slots, candidate, str(session["fixed_start_time"]))
    return not slot_ids or candidate_slot in slot_ids


def _selected_sessions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    business_lines = set(payload.get("business_lines") or [])
    product_types = set(payload.get("product_types") or [])
    class_ids = set(payload.get("class_business_ids") or [])
    date_from = _parse_date(payload.get("date_from"))
    date_to = _parse_date(payload.get("date_to"))
    selected: list[dict[str, Any]] = []
    for session in payload.get("course_sessions", []):
        lesson_date = _parse_date(session.get("lesson_date"))
        if business_lines and session.get("business_line") not in business_lines:
            continue
        if product_types and session.get("product_type") not in product_types:
            continue
        if class_ids and session["class_business_id"] not in class_ids:
            continue
        if date_from and lesson_date and lesson_date < date_from:
            continue
        if date_to and lesson_date and lesson_date > date_to:
            continue
        selected.append(session)
    return selected


def _uses_date_aware_model(payload: dict[str, Any]) -> bool:
    sessions = _selected_sessions(payload)
    return bool(sessions) and all(
        _parse_date(item.get("lesson_date"))
        and item.get("fixed_start_time")
        and item.get("fixed_end_time")
        for item in sessions
    )


def _empty_result(status: str = "INFEASIBLE") -> dict[str, Any]:
    return {
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
        or {"fixed_time", "room_no_overlap", "calendar_no_overlap", "minimize_changes"}
    )
    if "room_no_overlap" in solver_rules and any(
        item.startswith("SYSTEM-ROOM-NO-OVERLAP") for item in capacity_explanations
    ):
        ids.append("SYSTEM-ROOM-NO-OVERLAP")
    if "calendar_no_overlap" in solver_rules and any(
        item.startswith("SYSTEM-CALENDAR-NO-OVERLAP") for item in capacity_explanations
    ):
        ids.append("SYSTEM-CALENDAR-NO-OVERLAP")
    if len(ids) == len(dynamic_ids) + 1:
        ids.extend(
            [
                "SYSTEM-CLASS-NO-OVERLAP",
                *(
                    ["SYSTEM-ROOM-NO-OVERLAP"]
                    if "room_no_overlap" in solver_rules
                    else []
                ),
                *(
                    ["SYSTEM-CALENDAR-NO-OVERLAP"]
                    if "calendar_no_overlap" in solver_rules
                    else []
                ),
            ]
        )
    ids = list(dict.fromkeys(ids))
    dynamic_text = "、".join(dynamic_ids) if dynamic_ids else "当前请求未携带动态规则 ID"
    detail_text = " ".join(capacity_explanations)
    explanations = [
        "日期感知模型未使用可供 CP-SAT 提取的假设文字；本次无解按内置硬约束标记。",
        f"约束范围：{dynamic_text}；系统约束：{'、'.join(ids[len(dynamic_ids):])}。",
        detail_text
        or "优先检查同一业务场景的班级重叠、固定上课时段、教室容量和具体日程账号占用。",
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
        or {"fixed_time", "room_no_overlap", "calendar_no_overlap", "minimize_changes"}
    )
    groups: dict[tuple[str, Any], list[tuple[date, date, str]]] = defaultdict(list)
    calendar_groups: dict[tuple[str, Any], list[tuple[date, date, str]]] = defaultdict(list)
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
        clock = (session.get("fixed_start_time"), session.get("fixed_end_time"))
        groups[(_class_scope_key(session), clock)].append(interval)
        room_groups[clock].append(interval)
        teacher = teachers.get(str(session.get("teacher_business_id")), {})
        calendar_user_id = str(
            session.get("calendar_user_id") or teacher.get("calendar_user_id") or ""
        ).strip()
        if calendar_user_id:
            calendar_groups[(calendar_user_id, clock)].append(interval)

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
                    contained = [
                        item for item in intervals if item[0] >= start and item[1] <= end
                    ]
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
    if "calendar_no_overlap" in solver_rules:
        scan(calendar_groups, 1, "SYSTEM-CALENDAR-NO-OVERLAP", "具体日程账号")
    return explanations[:3]


def _solve_date_aware(payload: dict[str, Any]) -> dict[str, Any]:
    model = cp_model.CpModel()
    sessions = _selected_sessions(payload)
    rooms = {
        item["business_id"]: item for item in payload.get("rooms", []) if item.get("is_active")
    }
    teachers = {item["business_id"]: item for item in payload.get("teachers", [])}
    slots = {
        (item.get("weekday"), item.get("start_time")): item["business_id"]
        for item in payload.get("time_slots", [])
        if item.get("is_open")
    }
    rules = _normalized_rules(payload)
    solver_rules = set(
        payload.get("solver_rules")
        or {"fixed_time", "room_no_overlap", "calendar_no_overlap", "minimize_changes"}
    )
    event = payload.get("event") or {}
    date_window = int(payload.get("date_window_days", 7))
    request_date_from = _parse_date(payload.get("date_from"))
    request_date_to = _parse_date(payload.get("date_to"))
    change_weight = (
        int(payload.get("change_weight", 100000)) if "minimize_changes" in solver_rules else 0
    )

    result = _empty_result()
    if not sessions or not rooms:
        result["conflict_rule_ids"], result["priority_explanations"] = (
            _date_infeasible_diagnostics(payload)
        )
        result["priority_rule_ids"] = list(result["conflict_rule_ids"])
        return result
    capacity_explanations = _date_capacity_explanations(payload)
    if capacity_explanations:
        result["conflict_rule_ids"], result["priority_explanations"] = (
            _date_infeasible_diagnostics(payload)
        )
        result["priority_rule_ids"] = list(result["conflict_rule_ids"])
        return result

    date_vars: dict[str, cp_model.IntVar] = {}
    start_vars: dict[str, cp_model.IntVar] = {}
    intervals: dict[str, cp_model.IntervalVar] = {}
    room_choices: dict[tuple[str, str], cp_model.IntVar] = {}
    room_intervals: dict[str, list[cp_model.IntervalVar]] = {room_id: [] for room_id in rooms}
    class_intervals: dict[tuple[str, str, str], list[cp_model.IntervalVar]] = {}
    teacher_intervals: dict[str, list[cp_model.IntervalVar]] = {}
    objective_terms: list[Any] = []

    selected_business_ids = {str(item["business_id"]) for item in sessions}
    all_sessions = {
        str(item["business_id"]): item for item in payload.get("course_sessions", [])
    }
    for index, previous in enumerate(payload.get("previous_assignments", [])):
        course_id = str(previous.get("course_business_id") or "")
        if not course_id or course_id in selected_business_ids:
            continue
        course = all_sessions.get(course_id)
        lesson_date = _parse_date(previous.get("lesson_date"))
        if not course or not lesson_date:
            continue
        fixed_start = str(course.get("fixed_start_time") or "")
        fixed_end = str(course.get("fixed_end_time") or "")
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
        if room_id in room_intervals:
            room_intervals[room_id].append(fixed_interval)
        class_intervals.setdefault(_class_scope_key(course), []).append(fixed_interval)
        teacher = teachers.get(str(course["teacher_business_id"]), {})
        calendar_user_id = str(
            course.get("calendar_user_id") or teacher.get("calendar_user_id") or ""
        ).strip()
        if calendar_user_id:
            teacher_intervals.setdefault(calendar_user_id, []).append(fixed_interval)

    for session in sessions:
        course_id = session["business_id"]
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
        matching_rules = [
            rule for rule in rules if _session_matches_rule(session, "", rule)
        ]
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

        fixed_start = str(session["fixed_start_time"])
        fixed_end = str(session["fixed_end_time"])
        for rule in matching_rules:
            if rule.get("hardness") != "hard":
                continue
            scope = rule.get("scope") or {}
            slot_ids = set(scope.get("slot_ids") or [])
            if scope.get("slot_id"):
                slot_ids.add(scope["slot_id"])
            constraint_type = rule.get("constraint_type")
            if constraint_type in {"fixed_slot", "forbidden_slot", "unavailable_slot"}:
                matching_dates = {
                    candidate
                    for candidate in allowed_dates
                    if slots.get((WEEKDAYS[candidate.weekday()], fixed_start)) in slot_ids
                }
                if constraint_type == "fixed_slot":
                    allowed_dates = [item for item in allowed_dates if item in matching_dates]
                else:
                    allowed_dates = [item for item in allowed_dates if item not in matching_dates]
        allowed_dates = [
            item
            for item in allowed_dates
            if not _event_blocks_date(event, session, item, slots)
        ]
        if session.get("is_locked"):
            allowed_dates = [item for item in allowed_dates if item == original_date]
        if not allowed_dates:
            model.add_bool_or([])
            continue

        ordinals = sorted(item.toordinal() for item in allowed_dates)
        day_var = model.new_int_var_from_domain(
            cp_model.Domain.from_values(ordinals), f"day_{course_id}"
        )
        start_minute = _clock_minutes(fixed_start)
        duration = max(1, _clock_minutes(fixed_end) - start_minute)
        start_var = model.new_int_var(
            ordinals[0] * 1440 + start_minute,
            ordinals[-1] * 1440 + start_minute,
            f"start_{course_id}",
        )
        model.add(start_var == day_var * 1440 + start_minute)
        interval = model.new_fixed_size_interval_var(start_var, duration, f"interval_{course_id}")
        date_vars[course_id] = day_var
        start_vars[course_id] = start_var
        intervals[course_id] = interval

        max_date_delta = max(abs(item - original_date).days for item in allowed_dates)
        date_delta = model.new_int_var(0, max_date_delta, f"date_delta_{course_id}")
        model.add_abs_equality(date_delta, day_var - original_date.toordinal())
        objective_terms.append(change_weight * date_delta)

        for rule in matching_rules:
            if rule.get("hardness") != "soft":
                continue
            constraint_type = rule.get("constraint_type")
            if constraint_type not in {
                "fixed_date",
                "preferred_date",
                "date_window",
                "date_range",
                "allowed_date_range",
            }:
                continue
            scope = rule.get("scope") or {}
            preferred_dates = set(ordinals)
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
                    ordinal
                    for ordinal in ordinals
                    if (not rule_from or ordinal >= rule_from.toordinal())
                    and (not rule_to or ordinal <= rule_to.toordinal())
                }
            penalty = model.new_bool_var(f"date_rule_penalty_{course_id}_{rule['business_id']}")
            for ordinal in ordinals:
                if ordinal not in preferred_dates:
                    model.add(day_var != ordinal).only_enforce_if(penalty.Not())
            objective_terms.append(max(1, int(rule.get("weight") or 1)) * penalty)

        valid_room_choices: list[cp_model.IntVar] = []
        original_room = session.get("original_room_business_id")
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
        if session.get("is_locked") and original_room:
            locked_room = {str(original_room)}
            fixed_room_ids = (
                locked_room if fixed_room_ids is None else fixed_room_ids.intersection(locked_room)
            )
        for room_id in rooms:
            if fixed_room_ids is not None and room_id not in fixed_room_ids:
                continue
            if room_id in forbidden_room_ids:
                continue
            if event.get("event_type") == "room_outage" and room_id == event.get(
                "room_business_id"
            ):
                if not _event_targets_session(event, session):
                    pass
                elif not event.get("slot_business_ids"):
                    continue
            selected = model.new_bool_var(f"room_{course_id}_{room_id}")
            optional = model.new_optional_fixed_size_interval_var(
                start_var, duration, selected, f"room_interval_{course_id}_{room_id}"
            )
            room_choices[(course_id, room_id)] = selected
            room_intervals[room_id].append(optional)
            valid_room_choices.append(selected)
            if original_room and room_id != original_room:
                objective_terms.append(selected)
            if (
                event.get("event_type") == "room_outage"
                and room_id == event.get("room_business_id")
                and _event_targets_session(event, session)
            ):
                outage_slots = set(event.get("slot_business_ids") or [])
                for candidate in allowed_dates:
                    if _slot_for_date(slots, candidate, fixed_start) in outage_slots:
                        model.add(day_var != candidate.toordinal()).only_enforce_if(selected)
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
                    objective_terms.append(max(1, int(rule.get("weight") or 1)) * selected)
        if valid_room_choices:
            model.add_exactly_one(valid_room_choices)
        else:
            model.add_bool_or([])

    if "room_no_overlap" in solver_rules:
        for grouped in room_intervals.values():
            if grouped:
                model.add_no_overlap(grouped)

    for session in sessions:
        class_interval = intervals.get(session["business_id"])
        if class_interval is not None:
            class_intervals.setdefault(_class_scope_key(session), []).append(class_interval)
    for grouped in class_intervals.values():
        if grouped:
            model.add_no_overlap(grouped)

    # 原始“授课教师”是教研组。只有映射到具体飞书用户后，才按个人日历做不重叠约束。
    for session in sessions:
        teacher = teachers.get(session["teacher_business_id"], {})
        calendar_user_id = str(
            session.get("calendar_user_id") or teacher.get("calendar_user_id") or ""
        ).strip()
        teacher_interval = intervals.get(session["business_id"])
        if calendar_user_id and teacher_interval is not None:
            teacher_intervals.setdefault(calendar_user_id, []).append(teacher_interval)
    if "calendar_no_overlap" in solver_rules:
        for grouped in teacher_intervals.values():
            if grouped:
                model.add_no_overlap(grouped)

    model.minimize(sum(objective_terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(payload.get("time_limit_seconds", 30))
    solver.parameters.random_seed = int(payload.get("random_seed", 2026))
    solver.parameters.num_search_workers = 1
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
    for session in sessions:
        course_id = session["business_id"]
        result_day_var = date_vars.get(course_id)
        if result_day_var is None:
            continue
        lesson_date = date.fromordinal(solver.value(result_day_var))
        room_id = next(
            room_id
            for candidate_course, room_id in room_choices
            if candidate_course == course_id
            and solver.boolean_value(room_choices[(candidate_course, room_id)])
        )
        slot_id = slots.get(
            (WEEKDAYS[lesson_date.weekday()], str(session["fixed_start_time"]))
        )
        if not slot_id:
            slot_id = str(session.get("suggested_slot_id") or "")
        result["assignments"].append(
            {
                "course_session_id": session["id"],
                "course_business_id": course_id,
                "class_business_id": session["class_business_id"],
                "teacher_business_id": session["teacher_business_id"],
                "lesson_date": lesson_date.isoformat(),
                "room_business_id": room_id,
                "slot_business_id": slot_id,
            }
        )
    return result


def _build_model(payload: dict[str, Any], enabled_rule_ids: set[str] | None = None) -> BuiltModel:
    model = cp_model.CpModel()
    teachers = {item["business_id"]: item for item in payload["teachers"]}
    rooms = {item["business_id"]: item for item in payload["rooms"]}
    slots = {item["business_id"]: item for item in payload["time_slots"] if item["is_open"]}
    sessions = payload["course_sessions"]
    event = payload.get("event") or {}
    variables: dict[tuple[str, str, str], cp_model.IntVar] = {}

    for course in sessions:
        feasible: list[cp_model.IntVar] = []
        for room_id, room in rooms.items():
            if not room["is_active"]:
                continue
            for slot_id in slots:
                if _event_blocks(event, course, room_id, slot_id):
                    continue
                variable = model.new_bool_var(f"x_{course['business_id']}_{room_id}_{slot_id}")
                variables[(course["business_id"], room_id, slot_id)] = variable
                feasible.append(variable)
        if feasible:
            model.add_exactly_one(feasible)
        else:
            model.add_bool_or([])

    for teacher_id in teachers:
        for slot_id in slots:
            model.add_at_most_one(
                variable
                for (course_id, room_id, current_slot), variable in variables.items()
                if current_slot == slot_id
                and next(item for item in sessions if item["business_id"] == course_id)[
                    "teacher_business_id"
                ]
                == teacher_id
            )
    class_ids = {_class_scope_key(item) for item in sessions}
    for class_id in class_ids:
        for slot_id in slots:
            model.add_at_most_one(
                variable
                for (course_id, room_id, current_slot), variable in variables.items()
                if current_slot == slot_id
                and _class_scope_key(
                    next(item for item in sessions if item["business_id"] == course_id)
                )
                == class_id
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
            course = next(item for item in sessions if item["business_id"] == course_id)
            actor_matches = (
                not actor_ids
                or course_id in actor_ids
                or course["teacher_business_id"] in actor_ids
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
            course = next(item for item in sessions if item["business_id"] == course_id)
            actor_matches = (
                not actor_ids
                or course_id in actor_ids
                or course["teacher_business_id"] in actor_ids
                or course["class_business_id"] in actor_ids
                or room_id in actor_ids
            )
            if not actor_matches:
                continue
            penalized = (
                constraint_type in {"forbidden_slot", "unavailable_slot"}
                and slot_id in slot_ids
            ) or (
                constraint_type in {"fixed_slot", "preferred_slot"}
                and slot_id not in slot_ids
            ) or (constraint_type == "fixed_room" and room_id != scope.get("room_id"))
            if penalized:
                objective_terms.append(weight * variable)

        if constraint_type == "consecutive_sessions":
            matching_courses = [
                course
                for course in sessions
                if not actor_ids
                or course["business_id"] in actor_ids
                or course["teacher_business_id"] in actor_ids
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
    solver.parameters.num_search_workers = 1
    status_code = solver.solve(built.model)
    status_name = STATUS_NAMES.get(status_code, "UNKNOWN")
    result: dict[str, Any] = {
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
        return _empty_result("OPTIMAL")
    if _uses_date_aware_model(payload):
        return _solve_date_aware(payload)
    result = _solve_once(payload)
    if result["model_status"] == "INFEASIBLE":
        priority_ids, explanations = _priority_recommendation(
            payload, result["conflict_rule_ids"]
        )
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
