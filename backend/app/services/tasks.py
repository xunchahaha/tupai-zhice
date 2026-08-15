from __future__ import annotations

from concurrent.futures import Future, ProcessPoolExecutor
from datetime import date
from typing import Any

from sqlalchemy import func, select

from ..config import get_settings
from ..db import SessionLocal
from ..models import (
    CourseSession,
    DataSnapshot,
    RescheduleEvent,
    Room,
    ScheduleAssignment,
    ScheduleVersion,
    SolverRun,
    Teacher,
    TimeSlot,
)
from .solver import solve_problem

settings = get_settings()
_executor: ProcessPoolExecutor | None = None


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return None


def _get_executor() -> ProcessPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ProcessPoolExecutor(max_workers=settings.solver_workers)
    return _executor


def _clock_minutes(value: str) -> int:
    hours, minutes = (int(part) for part in value.split(":", 1))
    return hours * 60 + minutes


def _occupancy_window(course: Any, item: dict[str, Any], slots: dict[str, Any]) -> tuple[Any, ...]:
    """课次实际占用的时间区间。

    日期感知课表用「日期 + 固定起止时刻」，经典时段课表退化为时段标识本身。
    """
    lesson_date = item.get("lesson_date")
    slot = slots.get(str(item.get("slot_business_id") or ""))
    start = str(
        (slot.start_time if slot else None)
        or item.get("start_time")
        or getattr(course, "fixed_start_time", "")
        or ""
    )
    end = str(
        (slot.end_time if slot else None)
        or item.get("end_time")
        or getattr(course, "fixed_end_time", "")
        or ""
    )
    if lesson_date and start and end:
        return ("clock", str(lesson_date), _clock_minutes(start), _clock_minutes(end))
    return ("slot", str(lesson_date or ""), str(item["slot_business_id"]))


def _windows_overlap(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
    if left[0] != right[0] or left[1] != right[1]:
        return False
    if left[0] == "slot":
        return left[2] == right[2]
    return left[2] < right[3] and right[2] < left[3]


def count_hard_conflicts(db: Any, assignments: list[dict[str, Any]]) -> dict[str, int]:
    """按维度实算硬冲突记录数。

    此前该指标是写死的 0，等于用一个恒真值去证明正确性。这里独立于求解器重算一遍，
    既是发布门禁，也是求解器建模出错时的兜底。
    """
    courses = {item.id: item for item in db.scalars(select(CourseSession)).all()}
    teachers = {item.business_id: item for item in db.scalars(select(Teacher)).all()}
    slots = {item.business_id: item for item in db.scalars(select(TimeSlot)).all()}
    entries: list[tuple[dict[str, Any], Any, tuple[Any, ...]]] = []
    for item in assignments:
        course = courses.get(item["course_session_id"])
        if course is None:
            continue
        entries.append((item, course, _occupancy_window(course, item, slots)))

    def dimension_keys(item: dict[str, Any], course: Any, name: str) -> list[str]:
        if name == "room":
            value = str(item.get("room_business_id") or "")
            return [value] if value else []
        if name == "class":
            product_types = list(getattr(course, "product_types", None) or [])
            primary = str(getattr(course, "product_type", "") or "")
            if primary and primary not in product_types:
                product_types.append(primary)
            return [
                "\x00".join(
                    (
                        str(getattr(course, "business_line", "") or ""),
                        str(product_type),
                        str(item.get("class_business_id") or ""),
                    )
                )
                for product_type in sorted(set(product_types or [""]))
            ]
        teacher_ids = list(getattr(course, "teacher_business_ids", None) or [])
        primary_teacher = str(item.get("teacher_business_id") or "")
        if primary_teacher and primary_teacher not in teacher_ids:
            teacher_ids.append(primary_teacher)
        if name == "teacher":
            return [
                teacher_id
                for teacher_id in teacher_ids
                if teacher_id
                and not bool(teachers.get(teacher_id) and teachers[teacher_id].is_group)
            ]
        explicit = str(getattr(course, "calendar_user_id", None) or "").strip()
        if explicit:
            return [explicit]
        return sorted(
            {
                str(teachers[teacher_id].calendar_user_id or "").strip()
                for teacher_id in teacher_ids
                if teacher_id in teachers and teachers[teacher_id].calendar_user_id
            }
        )

    breakdown: dict[str, int] = {}
    for name in ("room", "class", "teacher", "calendar"):
        grouped: dict[str, list[tuple[dict[str, Any], tuple[Any, ...]]]] = {}
        for item, course, window in entries:
            for key in dimension_keys(item, course, name):
                grouped.setdefault(key, []).append((item, window))
        involved: set[str] = set()
        for rows in grouped.values():
            for index, (left_item, left_window) in enumerate(rows):
                for right_item, right_window in rows[index + 1 :]:
                    if _windows_overlap(left_window, right_window):
                        involved.add(str(left_item["course_session_id"]))
                        involved.add(str(right_item["course_session_id"]))
        breakdown[name] = len(involved)
    breakdown["total"] = sum(breakdown[name] for name in ("room", "class", "teacher", "calendar"))
    return breakdown


def _room_slot_capacity(
    rooms: dict[str, Any], open_slots: list[Any], occupied: set[tuple[str, str | None, str]]
) -> int:
    """可用的「教室 × 时间格」总数。

    分母必须和分子用同一种格子定义，否则占用率会算出 200% 这种没有意义的数。
    经典时段课表的一个格子就是一个时段标识；日期感知课表的一个格子是
    「日期 + 固定起止时刻」，同一时刻落在不同星期上共用一个时钟窗口，
    所以只有后者才按起止时刻去重。两种课次混在一份课表里时分别算再相加。
    """
    active_rooms = sum(1 for room in rooms.values() if room.is_active)
    if not active_rooms:
        return 0
    scheduled_dates = {lesson_date for _room, lesson_date, _slot in occupied if lesson_date}
    capacity = 0
    if any(lesson_date is None for _room, lesson_date, _slot in occupied):
        capacity += active_rooms * len({slot.business_id for slot in open_slots})
    if scheduled_dates:
        capacity += (
            active_rooms
            * len(scheduled_dates)
            * len({(slot.start_time, slot.end_time) for slot in open_slots})
        )
    return capacity


def calculate_metrics(db: Any, assignments: list[dict[str, Any]]) -> dict[str, Any]:
    rooms = {item.business_id: item for item in db.scalars(select(Room)).all()}
    open_slots = list(db.scalars(select(TimeSlot).where(TimeSlot.is_open.is_(True))))
    room_slots: set[tuple[str, str | None, str]] = set()
    unknown_rooms: set[str] = set()
    for item in assignments:
        room = rooms.get(item["room_business_id"])
        if room is None:
            unknown_rooms.add(str(item["room_business_id"]))
            continue
        room_slots.add((room.business_id, item.get("lesson_date"), item["slot_business_id"]))
    available_room_slots = _room_slot_capacity(rooms, open_slots, room_slots)
    conflicts = count_hard_conflicts(db, assignments)
    return {
        "assignment_count": len(assignments),
        "hard_conflicts": conflicts["total"],
        "hard_conflicts_by_dimension": {
            key: value for key, value in conflicts.items() if key != "total"
        },
        "unassigned_rooms": len(unknown_rooms),
        "room_slot_occupancy": round(len(room_slots) / available_room_slots, 4)
        if available_room_slots
        else 0,
    }


def _persist_result(run_id: str, result: dict[str, Any]) -> None:
    with SessionLocal() as db:
        run = db.get(SolverRun, run_id)
        if run is None:
            return
        run.model_status = result["model_status"]
        run.objective_value = result["objective_value"]
        run.best_bound = result["best_bound"]
        run.wall_time_seconds = result["wall_time_seconds"]
        run.conflict_rule_ids = result["conflict_rule_ids"]
        run.priority_rule_ids = result["priority_rule_ids"]
        run.priority_explanations = result["priority_explanations"]
        run.result_payload = result
        run.status = "completed"
        if result["model_status"] in {"OPTIMAL", "FEASIBLE"}:
            version_no = (
                int(db.scalar(select(func.coalesce(func.max(ScheduleVersion.version_no), 0))) or 0)
                + 1
            )
            parent_id = run.request_payload.get("parent_schedule_id")
            assignments = list(result["assignments"])
            parent_previous: dict[str, tuple[str | None, str, str]] = {}
            if parent_id:
                solved_course_ids = {str(item["course_session_id"]) for item in assignments}
                parent_rows = db.scalars(
                    select(ScheduleAssignment).where(
                        ScheduleAssignment.schedule_version_id == parent_id
                    )
                ).all()
                parent_courses = {
                    item.id: item
                    for item in db.scalars(
                        select(CourseSession).where(
                            CourseSession.id.in_([item.course_session_id for item in parent_rows])
                        )
                    )
                }
                for parent_item in parent_rows:
                    parent_course = parent_courses.get(parent_item.course_session_id)
                    course_business_id = (
                        parent_course.business_id
                        if parent_course
                        else parent_item.course_session_id
                    )
                    parent_previous[course_business_id] = (
                        parent_item.lesson_date.isoformat() if parent_item.lesson_date else None,
                        parent_item.room_business_id,
                        parent_item.slot_business_id,
                    )
                    if parent_item.course_session_id in solved_course_ids:
                        continue
                    assignments.append(
                        {
                            "course_session_id": parent_item.course_session_id,
                            "course_business_id": course_business_id,
                            "class_business_id": (
                                parent_course.class_business_id if parent_course else ""
                            ),
                            "teacher_business_id": (
                                parent_course.teacher_business_id if parent_course else ""
                            ),
                            "lesson_date": (
                                parent_item.lesson_date.isoformat()
                                if parent_item.lesson_date
                                else None
                            ),
                            "room_business_id": parent_item.room_business_id,
                            "slot_business_id": parent_item.slot_business_id,
                            "change_kind": "unchanged",
                        }
                    )
            result["assignments"] = assignments
            run.result_payload = result
            previous = parent_previous | {
                item["course_business_id"]: (
                    item.get("lesson_date"),
                    item["room_business_id"],
                    item["slot_business_id"],
                )
                for item in run.request_payload.get("previous_assignments", [])
            }
            metrics = calculate_metrics(db, assignments)
            metrics["changed_assignments"] = sum(
                1
                for item in assignments
                if item["course_business_id"] in previous
                and previous[item["course_business_id"]]
                != (item.get("lesson_date"), item["room_business_id"], item["slot_business_id"])
            )
            schedule = ScheduleVersion(
                version_no=version_no,
                name=f"课表版本 V{version_no}",
                status="draft",
                parent_id=parent_id,
                solver_run_id=run.id,
                metrics=metrics,
                created_by=run.created_by,
            )
            db.add(schedule)
            db.flush()
            for item in assignments:
                old = previous.get(item["course_business_id"])
                change_kind = (
                    "unchanged"
                    if old
                    == (item.get("lesson_date"), item["room_business_id"], item["slot_business_id"])
                    else ("changed" if old else "assigned")
                )
                db.add(
                    ScheduleAssignment(
                        schedule_version_id=schedule.id,
                        course_session_id=item["course_session_id"],
                        lesson_date=_parse_date(item.get("lesson_date")),
                        slot_business_id=item["slot_business_id"],
                        room_business_id=item["room_business_id"],
                        change_kind=change_kind,
                    )
                )
            event = db.scalar(
                select(RescheduleEvent).where(RescheduleEvent.solver_run_id == run.id)
            )
            if event:
                event.status = "candidate_ready"
                event.candidate_schedule_id = schedule.id
        db.commit()


def _persist_failure(run_id: str, message: str) -> None:
    with SessionLocal() as db:
        run = db.get(SolverRun, run_id)
        if run:
            run.status = "failed"
            run.error_message = message
            db.commit()


def execute_solver_run(run_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        run = db.get(SolverRun, run_id)
        if run is None:
            raise ValueError(f"Unknown solver run: {run_id}")
        snapshot = db.get(DataSnapshot, run.snapshot_id)
        if snapshot is None:
            raise ValueError(f"Missing data snapshot: {run.snapshot_id}")
        run.status = "running"
        db.commit()
        payload = dict(snapshot.payload)
        payload.update(run.request_payload)
    try:
        result = solve_problem(payload)
        _persist_result(run_id, result)
        return result
    except Exception as exc:
        _persist_failure(run_id, str(exc))
        raise


def enqueue_solver_run(run_id: str) -> None:
    with SessionLocal() as db:
        run = db.get(SolverRun, run_id)
        if run is None:
            raise ValueError(f"Unknown solver run: {run_id}")
        snapshot = db.get(DataSnapshot, run.snapshot_id)
        if snapshot is None:
            raise ValueError(f"Missing data snapshot: {run.snapshot_id}")
        run.status = "running"
        db.commit()
        payload = dict(snapshot.payload)
        payload.update(run.request_payload)

    future: Future[dict[str, Any]] = _get_executor().submit(solve_problem, payload)

    def done_callback(completed: Future[dict[str, Any]]) -> None:
        try:
            _persist_result(run_id, completed.result())
        except Exception as exc:
            _persist_failure(run_id, str(exc))

    future.add_done_callback(done_callback)
