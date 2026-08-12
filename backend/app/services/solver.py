from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ortools.sat.python import cp_model

STATUS_NAMES = {
    cp_model.OPTIMAL: "OPTIMAL",
    cp_model.FEASIBLE: "FEASIBLE",
    cp_model.INFEASIBLE: "INFEASIBLE",
    cp_model.MODEL_INVALID: "UNKNOWN",
    cp_model.UNKNOWN: "UNKNOWN",
}


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
    class_ids = {item["class_business_id"] for item in sessions}
    for class_id in class_ids:
        for slot_id in slots:
            model.add_at_most_one(
                variable
                for (course_id, room_id, current_slot), variable in variables.items()
                if current_slot == slot_id
                and next(item for item in sessions if item["business_id"] == course_id)[
                    "class_business_id"
                ]
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
    for rule in payload.get("rules", []):
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

    soft_rules = [rule for rule in payload.get("rules", []) if rule["hardness"] == "soft"]
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
