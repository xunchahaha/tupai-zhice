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


def calculate_metrics(db: Any, assignments: list[dict[str, Any]]) -> dict[str, Any]:
    rooms = {item.business_id: item for item in db.scalars(select(Room)).all()}
    open_slots = list(db.scalars(select(TimeSlot).where(TimeSlot.is_open.is_(True))))
    room_slots: set[tuple[str, str | None, str]] = set()
    for item in assignments:
        room = rooms[item["room_business_id"]]
        room_slots.add((room.business_id, item.get("lesson_date"), item["slot_business_id"]))
    scheduled_dates = {item.get("lesson_date") for item in assignments if item.get("lesson_date")}
    available_room_slots = (
        sum(1 for room in rooms.values() if room.is_active)
        * max(len(scheduled_dates), 1)
        * max(len({(slot.start_time, slot.end_time) for slot in open_slots}), 1)
    )
    return {
        "assignment_count": len(assignments),
        "hard_conflicts": 0,
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
                solved_course_ids = {
                    str(item["course_session_id"]) for item in assignments
                }
                parent_rows = db.scalars(
                    select(ScheduleAssignment).where(
                        ScheduleAssignment.schedule_version_id == parent_id
                    )
                ).all()
                parent_courses = {
                    item.id: item
                    for item in db.scalars(
                        select(CourseSession).where(
                            CourseSession.id.in_(
                                [item.course_session_id for item in parent_rows]
                            )
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
