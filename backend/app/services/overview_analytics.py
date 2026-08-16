"""Read-only statistics used by the overview dashboard.

The projection deliberately reads a single schedule set and, for timetable
metrics, a single schedule version.  It does not denormalize these values into
new tables, so publishing or rolling back a version immediately changes the
dashboard without a migration or a background aggregation job.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Campus,
    CourseSession,
    DataSnapshot,
    IntegrationSync,
    Room,
    ScheduleAssignment,
    ScheduleVersion,
    SolverRun,
    Teacher,
    TimeSlot,
)
from ..timezone import as_shanghai, shanghai_now

WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
PERIOD_LABELS = ("上午", "下午", "晚自习")


def _parse_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _teacher_ids(course: CourseSession) -> list[str]:
    values = [str(item) for item in (course.teacher_business_ids or []) if str(item).strip()]
    primary = str(course.teacher_business_id or "").strip()
    if primary:
        values.insert(0, primary)
    return list(dict.fromkeys(values))


def _load_assignment_context(
    db: Session,
    schedule: ScheduleVersion | None,
    date_from: date | None,
    date_to: date | None,
) -> list[tuple[ScheduleAssignment, CourseSession]]:
    if schedule is None:
        return []
    statement = (
        select(ScheduleAssignment, CourseSession)
        .join(CourseSession, CourseSession.id == ScheduleAssignment.course_session_id)
        .where(
            ScheduleAssignment.schedule_version_id == schedule.id,
            CourseSession.schedule_set_id == schedule.schedule_set_id,
        )
    )
    # An undated assignment has no defensible position inside a requested
    # calendar window, so it is included only when no date boundary is given.
    if date_from is not None:
        statement = statement.where(ScheduleAssignment.lesson_date >= date_from)
    if date_to is not None:
        statement = statement.where(ScheduleAssignment.lesson_date <= date_to)
    return list(db.execute(statement).tuples().all())


def _teacher_workload(
    db: Session,
    schedule_set_id: str,
    schedule: ScheduleVersion | None,
    assignments: list[tuple[ScheduleAssignment, CourseSession]],
    date_from: date | None,
    date_to: date | None,
) -> dict[str, Any]:
    campuses = {
        item.id: item.name
        for item in db.scalars(select(Campus).where(Campus.schedule_set_id == schedule_set_id))
    }
    teachers = list(
        db.scalars(
            select(Teacher)
            .where(Teacher.schedule_set_id == schedule_set_id)
            .order_by(Teacher.business_id, Teacher.name)
        )
    )
    teacher_meta: dict[tuple[str, str], dict[str, str]] = {}
    for teacher in teachers:
        teacher_key = (teacher.campus_id, teacher.business_id)
        item = teacher_meta.setdefault(
            teacher_key,
            {
                "name": teacher.name,
                "subject": teacher.subject,
                "campus_name": campuses.get(teacher.campus_id, teacher.campus_id),
            },
        )
        if teacher.name and teacher.name not in item["name"].split(" / "):
            item["name"] = " / ".join((item["name"], teacher.name))
        if teacher.subject and teacher.subject not in item["subject"].split(" / "):
            item["subject"] = " / ".join(filter(None, (item["subject"], teacher.subject)))

    session_counts: dict[tuple[str, str], int] = defaultdict(int)
    minutes: dict[tuple[str, str], int] = defaultdict(int)
    for _assignment, course in assignments:
        for teacher_id in _teacher_ids(course):
            teacher_key = (course.campus_id, teacher_id)
            session_counts[teacher_key] += 1
            minutes[teacher_key] += max(0, int(course.duration_minutes or 0))
            teacher_meta.setdefault(
                teacher_key,
                {
                    "name": teacher_id,
                    "subject": str(course.subject or ""),
                    "campus_name": campuses.get(course.campus_id, course.campus_id),
                },
            )

    all_teacher_keys = sorted(teacher_meta)
    total_hours = round(sum(minutes.values()) / 60, 2)
    ranked_keys = sorted(
        all_teacher_keys,
        key=lambda teacher_key: (
            -minutes.get(teacher_key, 0),
            -session_counts.get(teacher_key, 0),
            teacher_meta[teacher_key]["name"],
            teacher_key,
        ),
    )
    rows: list[dict[str, Any]] = []
    for rank, teacher_key in enumerate(ranked_keys, start=1):
        campus_id, teacher_id = teacher_key
        hours = round(minutes.get(teacher_key, 0) / 60, 2)
        rows.append(
            {
                "campus_id": campus_id,
                "campus_name": teacher_meta[teacher_key]["campus_name"],
                "teacher_business_id": teacher_id,
                "teacher_name": teacher_meta[teacher_key]["name"],
                "subject": teacher_meta[teacher_key]["subject"],
                "total_sessions": session_counts.get(teacher_key, 0),
                "total_hours": hours,
                "load_share": round(hours / total_hours, 4) if total_hours else 0.0,
                "rank": rank,
            }
        )

    bucket_specs = (("0-10 节", 0, 10), ("10-20 节", 10, 20), ("20+ 节", 20, None))
    buckets = [
        {
            "label": label,
            "min_sessions": lower,
            "max_sessions": upper,
            "teacher_count": sum(
                1
                for teacher_key in all_teacher_keys
                if session_counts.get(teacher_key, 0) >= lower
                and (upper is None or session_counts.get(teacher_key, 0) < upper)
            ),
        }
        for label, lower, upper in bucket_specs
    ]
    return {
        "schedule_set_id": schedule_set_id,
        "schedule_version_id": schedule.id if schedule else None,
        "schedule_version_no": schedule.version_no if schedule else None,
        "date_from": date_from,
        "date_to": date_to,
        "total_teachers": len(all_teacher_keys),
        "assigned_teachers": sum(
            session_counts.get(item, 0) > 0 for item in all_teacher_keys
        ),
        "total_sessions": sum(session_counts.values()),
        "total_hours": total_hours,
        "top_teachers": [item for item in rows if item["total_sessions"] > 0][:5],
        "teachers": rows,
        "buckets": buckets,
    }


def _weekday_index(label: str) -> int | None:
    normalized = str(label or "").strip()
    aliases = {
        "星期一": 0,
        "星期二": 1,
        "星期三": 2,
        "星期四": 3,
        "星期五": 4,
        "星期六": 5,
        "星期日": 6,
        "星期天": 6,
    }
    if normalized in WEEKDAY_LABELS:
        return WEEKDAY_LABELS.index(normalized)
    return aliases.get(normalized)


def _slot_period(slot: TimeSlot) -> str:
    """Map an imported slot to the three dashboard heatmap periods."""

    kind = str(slot.kind or "").strip()
    if any(token in kind for token in ("上午", "早上", "早间")):
        return "上午"
    if any(token in kind for token in ("下午", "午后")):
        return "下午"
    if any(token in kind for token in ("晚自习", "晚间", "晚上", "夜间")):
        return "晚自习"
    try:
        hour = int(str(slot.start_time or "").split(":", 1)[0])
    except (TypeError, ValueError):
        hour = 12
    if hour < 12:
        return "上午"
    if hour < 18:
        return "下午"
    return "晚自习"


def _dates_for_weekday(date_from: date, date_to: date, weekday: int) -> int:
    first_offset = (weekday - date_from.weekday()) % 7
    first = date_from + timedelta(days=first_offset)
    if first > date_to:
        return 0
    return ((date_to - first).days // 7) + 1


def _room_heatmap(
    db: Session,
    schedule_set_id: str,
    schedule: ScheduleVersion | None,
    assignments: list[tuple[ScheduleAssignment, CourseSession]],
    date_from: date | None,
    date_to: date | None,
) -> dict[str, Any]:
    rooms = list(
        db.scalars(
            select(Room).where(
                Room.schedule_set_id == schedule_set_id,
                Room.is_active.is_(True),
            )
        )
    )
    slots = list(
        db.scalars(
            select(TimeSlot)
            .where(
                TimeSlot.schedule_set_id == schedule_set_id,
                TimeSlot.is_open.is_(True),
            )
            .order_by(TimeSlot.sequence, TimeSlot.weekday, TimeSlot.start_time)
        )
    )
    room_ids_by_campus: dict[str, set[str]] = defaultdict(set)
    for room in rooms:
        room_ids_by_campus[room.campus_id].add(room.business_id)

    occupancy: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    observed_dates: dict[tuple[str, str], set[date]] = defaultdict(set)
    assignment_dates = sorted(
        assignment.lesson_date
        for assignment, _course in assignments
        if assignment.lesson_date is not None
    )
    effective_date_from = date_from or (assignment_dates[0] if assignment_dates else None)
    effective_date_to = date_to or (assignment_dates[-1] if assignment_dates else None)
    for assignment, course in assignments:
        slot_key = (course.campus_id, assignment.slot_business_id)
        if assignment.room_business_id not in room_ids_by_campus.get(course.campus_id, set()):
            continue
        day_key = assignment.lesson_date.isoformat() if assignment.lesson_date else "undated"
        occupancy[slot_key].add((day_key, assignment.room_business_id))
        if assignment.lesson_date is not None:
            observed_dates[slot_key].add(assignment.lesson_date)

    cells: list[dict[str, Any]] = []
    period_totals: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {
            "occupied_room_slots": 0,
            "available_room_slots": 0,
            "observed_days": 0,
            "slot_count": 0,
        }
    )
    for slot in slots:
        slot_key = (slot.campus_id, slot.business_id)
        room_count = len(room_ids_by_campus.get(slot.campus_id, set()))
        slot_weekday_index = _weekday_index(slot.weekday)
        if (
            effective_date_from is not None
            and effective_date_to is not None
            and slot_weekday_index is not None
        ):
            day_count = _dates_for_weekday(
                effective_date_from, effective_date_to, slot_weekday_index
            )
        else:
            day_count = len(observed_dates.get(slot_key, set()))
            if day_count == 0 and room_count:
                # Undated timetables still represent one canonical weekly grid.
                day_count = 1
        available = room_count * day_count
        occupied = len(occupancy.get(slot_key, set()))
        period = _slot_period(slot)
        cells.append(
            {
                "weekday": slot.weekday,
                "slot_business_id": slot.business_id,
                "slot_label": f"{slot.start_time}-{slot.end_time}",
                "period": period,
                "sequence": slot.sequence,
                "occupied_room_slots": occupied,
                "available_room_slots": available,
                "occupancy_rate": round(min(1.0, occupied / available), 4)
                if available
                else 0.0,
                "observed_days": day_count,
            }
        )
        weekday_index = _weekday_index(slot.weekday)
        weekday_label = (
            WEEKDAY_LABELS[weekday_index] if weekday_index is not None else slot.weekday
        )
        aggregate = period_totals[(weekday_label, period)]
        aggregate["occupied_room_slots"] += occupied
        aggregate["available_room_slots"] += available
        aggregate["observed_days"] = max(aggregate["observed_days"], day_count)
        aggregate["slot_count"] += 1

    period_cells = []
    for weekday_label in WEEKDAY_LABELS:
        for period in PERIOD_LABELS:
            aggregate = period_totals[(weekday_label, period)]
            available = aggregate["available_room_slots"]
            period_cells.append(
                {
                    "weekday": weekday_label,
                    "period": period,
                    "occupied_room_slots": aggregate["occupied_room_slots"],
                    "available_room_slots": available,
                    "occupancy_rate": round(
                        min(1.0, aggregate["occupied_room_slots"] / available), 4
                    )
                    if available
                    else 0.0,
                    "observed_days": aggregate["observed_days"],
                    "slot_count": aggregate["slot_count"],
                }
            )
    return {
        "schedule_set_id": schedule_set_id,
        "schedule_version_id": schedule.id if schedule else None,
        "schedule_version_no": schedule.version_no if schedule else None,
        "date_from": date_from,
        "date_to": date_to,
        "effective_date_from": effective_date_from,
        "effective_date_to": effective_date_to,
        "total_rooms": len(rooms),
        "cells": cells,
        "period_cells": period_cells,
    }


def _rule_actor_matches(
    rule: dict[str, Any], assignment: ScheduleAssignment, course: CourseSession
) -> bool:
    actor_ids = {str(item) for item in (rule.get("actor_ids") or []) if str(item).strip()}
    if not actor_ids:
        return True
    return bool(
        course.business_id in actor_ids
        or course.class_business_id in actor_ids
        or assignment.room_business_id in actor_ids
        or actor_ids.intersection(_teacher_ids(course))
    )


def _rule_scope_ids(scope: dict[str, Any], singular: str, plural: str) -> set[str]:
    values = {str(item) for item in (scope.get(plural) or []) if str(item).strip()}
    if scope.get(singular):
        values.add(str(scope[singular]))
    return values


def _evaluate_soft_rule(
    rule: dict[str, Any],
    assignments: list[tuple[ScheduleAssignment, CourseSession]],
    slots: dict[tuple[str, str], TimeSlot],
) -> tuple[int | None, int, str]:
    applicable = [
        (assignment, course)
        for assignment, course in assignments
        if _rule_actor_matches(rule, assignment, course)
    ]
    constraint_type = str(rule.get("constraint_type") or "")
    scope = dict(rule.get("scope") or {})
    violations = 0

    if constraint_type in {"fixed_slot", "preferred_slot", "forbidden_slot", "unavailable_slot"}:
        target_ids = _rule_scope_ids(scope, "slot_id", "slot_ids")
        prefer_match = constraint_type in {"fixed_slot", "preferred_slot"}
        violations = sum(
            ((assignment.slot_business_id in target_ids) != prefer_match)
            for assignment, _course in applicable
        )
        return violations, len(applicable), "computed"

    if constraint_type in {"fixed_room", "preferred_room", "forbidden_room", "unavailable_room"}:
        target_ids = _rule_scope_ids(scope, "room_id", "room_ids")
        prefer_match = constraint_type in {"fixed_room", "preferred_room"}
        violations = sum(
            ((assignment.room_business_id in target_ids) != prefer_match)
            for assignment, _course in applicable
        )
        return violations, len(applicable), "computed"

    if constraint_type in {"fixed_date", "preferred_date"}:
        target = _parse_date(scope.get("date") or scope.get("date_from"))
        if target is None:
            return None, len(applicable), "declared"
        violations = sum(assignment.lesson_date != target for assignment, _course in applicable)
        return violations, len(applicable), "computed"

    if constraint_type in {"date_window", "date_range", "allowed_date_range"}:
        lower = _parse_date(scope.get("date_from"))
        upper = _parse_date(scope.get("date_to"))
        # A symmetric window is relative to each course's source date.
        symmetric_days = scope.get("date_window_days", scope.get("days"))
        evaluated = 0
        for assignment, course in applicable:
            if assignment.lesson_date is None:
                violations += 1
                evaluated += 1
                continue
            rule_lower = lower
            rule_upper = upper
            if symmetric_days is not None and course.lesson_date is not None:
                rule_lower = course.lesson_date - timedelta(days=int(symmetric_days))
                rule_upper = course.lesson_date + timedelta(days=int(symmetric_days))
            if rule_lower is None and rule_upper is None:
                continue
            evaluated += 1
            if (rule_lower and assignment.lesson_date < rule_lower) or (
                rule_upper and assignment.lesson_date > rule_upper
            ):
                violations += 1
        if evaluated == 0 and lower is None and upper is None and symmetric_days is None:
            return None, len(applicable), "declared"
        return violations, evaluated, "computed"

    if constraint_type == "consecutive_sessions":
        ordered = sorted(applicable, key=lambda item: item[1].business_id)
        pair_count = len(ordered) // 2
        for index in range(0, pair_count * 2, 2):
            first_assignment, first_course = ordered[index]
            second_assignment, second_course = ordered[index + 1]
            first_slot = slots.get((first_course.campus_id, first_assignment.slot_business_id))
            second_slot = slots.get((second_course.campus_id, second_assignment.slot_business_id))
            adjacent = bool(
                first_slot
                and second_slot
                and first_slot.campus_id == second_slot.campus_id
                and first_slot.weekday == second_slot.weekday
                and first_assignment.lesson_date == second_assignment.lesson_date
                and abs(first_slot.sequence - second_slot.sequence) == 1
            )
            if not adjacent:
                violations += 1
        return violations, pair_count, "computed"

    return None, len(applicable), "declared"


def _course_product_types(course: CourseSession) -> set[str]:
    values = {str(item) for item in (course.product_types or []) if str(item).strip()}
    if course.product_type:
        values.add(str(course.product_type))
    return values


def _solver_scope_assignments(
    run: SolverRun,
    assignments: list[tuple[ScheduleAssignment, CourseSession]],
) -> tuple[list[tuple[ScheduleAssignment, CourseSession]], str]:
    """Reconstruct the course scope used by the solver before parent merging.

    New solver runs persist the solved business IDs explicitly.  Historical
    runs fall back to the same request filters used by ``solver._selected_sessions``.
    """

    result_payload = dict(run.result_payload or {})
    persisted_ids = {
        str(item)
        for item in (result_payload.get("solved_course_business_ids") or [])
        if str(item).strip()
    }
    if persisted_ids:
        return (
            [item for item in assignments if item[1].business_id in persisted_ids],
            "persisted_solver_scope",
        )

    request = dict(run.request_payload or {})
    course_ids = {
        str(item) for item in (request.get("course_business_ids") or []) if str(item).strip()
    }
    business_lines = {
        str(item) for item in (request.get("business_lines") or []) if str(item).strip()
    }
    product_types = {
        str(item) for item in (request.get("product_types") or []) if str(item).strip()
    }
    class_ids = {
        str(item) for item in (request.get("class_business_ids") or []) if str(item).strip()
    }
    date_from = _parse_date(request.get("date_from"))
    date_to = _parse_date(request.get("date_to"))
    has_filter = bool(
        course_ids or business_lines or product_types or class_ids or date_from or date_to
    )
    if not has_filter:
        return assignments, "full_schedule_version"

    scoped: list[tuple[ScheduleAssignment, CourseSession]] = []
    for item in assignments:
        _assignment, course = item
        if course_ids and course.business_id not in course_ids:
            continue
        if business_lines and course.business_line not in business_lines:
            continue
        if product_types and not product_types.intersection(_course_product_types(course)):
            continue
        if class_ids and course.class_business_id not in class_ids:
            continue
        # Match solver._selected_sessions: request dates filter the imported
        # source lesson date, not the final assignment date.
        if date_from and course.lesson_date and course.lesson_date < date_from:
            continue
        if date_to and course.lesson_date and course.lesson_date > date_to:
            continue
        scoped.append(item)
    return scoped, "reconstructed_request_scope"


def _optimization_penalties(
    db: Session,
    schedule_set_id: str,
    schedule: ScheduleVersion | None,
    assignments: list[tuple[ScheduleAssignment, CourseSession]],
) -> dict[str, Any]:
    run = db.get(SolverRun, schedule.solver_run_id) if schedule else None
    if run is None:
        run = db.scalar(
            select(SolverRun)
            .where(SolverRun.schedule_set_id == schedule_set_id)
            .order_by(SolverRun.created_at.desc())
        )
    if run is None:
        return {
            "schedule_set_id": schedule_set_id,
            "solver_run_id": None,
            "solver_status": None,
            "objective_value": None,
            "best_bound": None,
            "total_soft_penalty": None,
            "unattributed_objective_value": None,
            "reconciliation_error": None,
            "breakdown_source": "unavailable",
            "scope_source": "unavailable",
            "evaluated_assignment_count": 0,
            "soft_constraints": [],
            "notes": ["当前课表尚无求解记录，暂无软约束扣分数据。"],
        }

    snapshot = db.get(DataSnapshot, run.snapshot_id)
    scoped_assignments, scope_source = _solver_scope_assignments(run, assignments)
    raw_rules = (snapshot.payload or {}).get("rules", []) if snapshot else []
    soft_rules = [
        dict(item)
        for item in raw_rules
        if isinstance(item, dict) and str(item.get("hardness") or "").lower() == "soft"
    ]
    slot_rows = db.scalars(
        select(TimeSlot).where(TimeSlot.schedule_set_id == schedule_set_id)
    ).all()
    slots = {(item.campus_id, item.business_id): item for item in slot_rows}
    items: list[dict[str, Any]] = []
    notes: list[str] = []
    known_penalty = 0.0
    for index, rule in enumerate(soft_rules, start=1):
        violations, evaluated, source = _evaluate_soft_rule(rule, scoped_assignments, slots)
        weight = float(max(1, int(rule.get("weight") or 1)))
        penalty = weight * violations if violations is not None else None
        if penalty is not None:
            known_penalty += penalty
        else:
            notes.append(
                f"规则 {rule.get('business_id') or index} 的类型 "
                f"{rule.get('constraint_type') or 'unknown'} 尚无可重算口径。"
            )
        items.append(
            {
                "rule_id": str(rule.get("business_id") or f"SOFT-{index}"),
                "constraint_type": str(rule.get("constraint_type") or "unknown"),
                "label": str(
                    rule.get("source_text")
                    or rule.get("constraint_type")
                    or f"软约束 {index}"
                ),
                "hardness": "soft",
                "weight": weight,
                "violations": violations,
                "evaluated_count": evaluated,
                "penalty": penalty,
                "satisfaction_rate": (
                    round(1 - violations / evaluated, 4)
                    if violations is not None and evaluated
                    else None
                ),
                "source": "estimated" if source == "computed" else source,
            }
        )

    if not soft_rules:
        notes.append("该次求解快照没有已启用的教务软约束。")
    objective = float(run.objective_value) if run.objective_value is not None else None
    objective_delta = objective - known_penalty if objective is not None else None
    unattributed = max(0.0, objective_delta) if objective_delta is not None else None
    reconciliation_error = (
        abs(objective_delta) if objective_delta is not None and objective_delta < 0 else 0.0
    )
    if unattributed:
        notes.append(
            "目标值还包含最小化日期/教室变更等内建目标；未把无法逐项还原的部分归到教务规则。"
        )
    if reconciliation_error:
        notes.append(
            "按求解范围重算的软规则扣分高于求解目标值；已在 reconciliation_error 明示差额，"
            "未将其静默压成 0。"
        )
    notes.append(
        "当前分项来自求解范围内最终安排的确定性重算；新运行会保存真实求解课次范围，"
        "历史运行按请求筛选条件重建范围。"
    )
    return {
        "schedule_set_id": schedule_set_id,
        "solver_run_id": run.id,
        "solver_status": run.model_status or run.status,
        "objective_value": objective,
        "best_bound": float(run.best_bound) if run.best_bound is not None else None,
        "total_soft_penalty": round(known_penalty, 4),
        "unattributed_objective_value": round(unattributed, 4)
        if unattributed is not None
        else None,
        "reconciliation_error": round(reconciliation_error, 4),
        "breakdown_source": "estimated_from_solver_scope",
        "scope_source": scope_source,
        "evaluated_assignment_count": len(scoped_assignments),
        "soft_constraints": items,
        "notes": notes,
    }


def _duration_ms(detail: dict[str, Any]) -> float | None:
    for key in ("duration_ms", "elapsed_ms"):
        value = detail.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    for key in ("duration_seconds", "elapsed_seconds"):
        value = detail.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return float(value) * 1000
    return None


def _sync_health(
    db: Session,
    schedule_set_id: str,
    now: datetime,
    window_hours: int,
) -> dict[str, Any]:
    window_start = now - timedelta(hours=window_hours)
    syncs = list(
        db.scalars(
            select(IntegrationSync)
            .where(
                IntegrationSync.schedule_set_id == schedule_set_id,
                IntegrationSync.provider == "feishu",
                IntegrationSync.created_at >= window_start,
            )
            .order_by(IntegrationSync.created_at.desc())
        )
    )
    by_resource: dict[str, dict[str, Any]] = {}
    durations: list[float] = []
    retry_count = 0
    retry_samples = 0
    for sync in syncs:
        item = by_resource.setdefault(
            sync.resource,
            {
                "resource": sync.resource,
                "total_syncs": 0,
                "completed_syncs": 0,
                "failed_syncs": 0,
                "records_read": 0,
                "records_written": 0,
            },
        )
        item["total_syncs"] += 1
        item["completed_syncs"] += int(sync.status == "completed")
        item["failed_syncs"] += int(sync.status == "failed")
        item["records_read"] += max(0, int(sync.records_read or 0))
        item["records_written"] += max(0, int(sync.records_written or 0))
        detail = dict(sync.detail or {})
        duration = _duration_ms(detail)
        if duration is not None:
            durations.append(duration)
        if isinstance(detail.get("retry_count"), int) and detail["retry_count"] >= 0:
            retry_count += int(detail["retry_count"])
            retry_samples += 1

    notes = [
        f"统计窗口按 Asia/Shanghai（UTC+8）最近 {window_hours} 小时计算。",
        "写入量为同步日志 records_written 之和，不等同于飞书表当前总行数。",
        "duration_ms 为单资源处理耗时；批量同步共享的前置表结构预检不重复计入各资源。",
    ]
    if not durations:
        notes.append("历史同步日志没有显式耗时字段，平均耗时返回 null。")
    if not syncs or retry_samples != len(syncs):
        notes.append("部分或全部同步日志没有显式重试计数；retry_count 只汇总已记录样本。")
    latest_sync_at = as_shanghai(syncs[0].created_at) if syncs else None
    return {
        "schedule_set_id": schedule_set_id,
        "window_start": window_start,
        "window_end": now,
        "total_syncs": len(syncs),
        "completed_syncs": sum(item.status == "completed" for item in syncs),
        "failed_syncs": sum(item.status == "failed" for item in syncs),
        "retry_count": retry_count,
        "records_read": sum(max(0, int(item.records_read or 0)) for item in syncs),
        "records_written": sum(max(0, int(item.records_written or 0)) for item in syncs),
        "average_duration_ms": round(sum(durations) / len(durations), 2)
        if durations
        else None,
        "latest_sync_at": latest_sync_at,
        "duration_samples": len(durations),
        "resources": [by_resource[key] for key in sorted(by_resource)],
        "notes": notes,
    }


def build_overview_analytics(
    db: Session,
    schedule_set_id: str,
    schedule: ScheduleVersion | None,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    sync_window_hours: int = 24,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = as_shanghai(now) if now is not None else shanghai_now()
    assert generated_at is not None
    assignments = _load_assignment_context(db, schedule, date_from, date_to)
    # The solver objective covers the complete version.  A dashboard date
    # filter may narrow workload/occupancy, but applying that filter to the
    # penalty terms would compare a partial recomputation with a full objective.
    optimization_assignments = (
        _load_assignment_context(db, schedule, None, None)
        if date_from is not None or date_to is not None
        else assignments
    )
    return {
        "schedule_set_id": schedule_set_id,
        "generated_at": generated_at,
        "teacher_workload": _teacher_workload(
            db,
            schedule_set_id,
            schedule,
            assignments,
            date_from,
            date_to,
        ),
        "room_heatmap": _room_heatmap(
            db,
            schedule_set_id,
            schedule,
            assignments,
            date_from,
            date_to,
        ),
        "optimization_penalties": _optimization_penalties(
            db,
            schedule_set_id,
            schedule,
            optimization_assignments,
        ),
        "sync_health": _sync_health(db, schedule_set_id, generated_at, sync_window_hours),
    }
