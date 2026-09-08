from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    ClassGroup,
    CourseSession,
    DataSnapshot,
    Room,
    Rule,
    ScheduleVersion,
    SolverRun,
    Teacher,
    TimeSlot,
)


def _model_dict(instance: object, fields: list[str]) -> dict[str, object]:
    return {field: getattr(instance, field) for field in fields}


def version_course_map(db: Session, schedule: ScheduleVersion) -> dict[str, CourseSession]:
    """只读版本视图：叠加历史快照，使用脱离会话的新对象，绝不修改主数据。"""
    current = list(db.scalars(select(CourseSession).where(
        CourseSession.schedule_set_id == schedule.schedule_set_id,
    )))
    run = db.get(SolverRun, schedule.solver_run_id)
    snapshot = db.get(DataSnapshot, run.snapshot_id) if run else None
    frozen = {item["id"]: item for item in (
        snapshot.payload.get("course_sessions", []) if snapshot else []
    )}
    columns = {column.name for column in CourseSession.__table__.columns}
    result = {}
    for item in current:
        values = {column: getattr(item, column) for column in columns}
        values.update({
            key: value for key, value in frozen.get(item.id, {}).items() if key in columns
        })
        if isinstance(values.get("lesson_date"), str):
            values["lesson_date"] = date.fromisoformat(values["lesson_date"])
        result[item.id] = CourseSession(**values)
    return result


def _json_default(value: Any) -> str:
    """Convert database scalar values to the JSON form used by snapshots."""
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def build_snapshot_payload(db: Session, schedule_set_id: str) -> dict[str, object]:
    teachers = list(
        db.scalars(
            select(Teacher)
            .where(Teacher.schedule_set_id == schedule_set_id)
            .order_by(Teacher.business_id)
        )
    )
    classes = list(
        db.scalars(
            select(ClassGroup)
            .where(ClassGroup.schedule_set_id == schedule_set_id)
            .order_by(ClassGroup.business_id)
        )
    )
    rooms = list(
        db.scalars(
            select(Room).where(Room.schedule_set_id == schedule_set_id).order_by(Room.business_id)
        )
    )
    slots = list(
        db.scalars(
            select(TimeSlot)
            .where(TimeSlot.schedule_set_id == schedule_set_id)
            .order_by(TimeSlot.sequence)
        )
    )
    sessions = list(
        db.scalars(
            select(CourseSession)
            .where(CourseSession.schedule_set_id == schedule_set_id)
            .order_by(CourseSession.business_id)
        )
    )
    rules = list(
        db.scalars(
            select(Rule)
            .where(Rule.schedule_set_id == schedule_set_id, Rule.status == "active")
            .order_by(Rule.business_id)
        )
    )
    return {
        "teachers": [
            _model_dict(
                item,
                ["id", "business_id", "name", "subject", "calendar_user_id", "is_group"],
            )
            for item in teachers
        ],
        # 班级只剩身份。班型/业务线/教师是课次的属性，course_sessions 那一段已经带着，
        # 不在这里再存一份聚合快照——存了就要跟着漂。
        "class_groups": [_model_dict(item, ["id", "business_id", "name"]) for item in classes],
        "rooms": [
            _model_dict(
                item,
                ["id", "business_id", "name", "is_active"],
            )
            for item in rooms
        ],
        "time_slots": [
            _model_dict(
                item,
                [
                    "id",
                    "business_id",
                    "weekday",
                    "start_time",
                    "end_time",
                    "kind",
                    "sequence",
                    "is_open",
                ],
            )
            for item in slots
        ],
        "course_sessions": [
            _model_dict(
                item,
                [
                    "id",
                    "business_id",
                    "source_row_id",
                    "business_line",
                    "product_type",
                    "product_types",
                    "product_contexts",
                    "class_business_id",
                    "teacher_business_id",
                    "teacher_business_ids",
                    "calendar_user_id",
                    "subject",
                    "lesson_name",
                    "lesson_names",
                    "schedule_source",
                    "stage",
                    "stages",
                    "planned_sessions",
                    "planned_hours",
                    "session_no",
                    "lesson_date",
                    "duration_minutes",
                    "suggested_slot_id",
                    "candidate_slot_ids",
                    "candidate_clock_windows",
                    "fixed_start_time",
                    "fixed_end_time",
                    "original_room_business_id",
                    "candidate_room_business_ids",
                    "source_variant_count",
                    "is_active",
                    "is_locked",
                ],
            )
            for item in sessions
        ],
        "rules": [
            _model_dict(
                item,
                [
                    "id",
                    "business_id",
                    "source_text",
                    "actor_type",
                    "actor_ids",
                    "constraint_type",
                    "scope",
                    "hardness",
                    "weight",
                    "structured_expression",
                    "source_doc",
                    "confidence",
                    "version",
                ],
            )
            for item in rules
        ],
    }


def create_snapshot(
    db: Session, created_by: str | None, schedule_set_id: str = "default"
) -> DataSnapshot:
    payload = build_snapshot_payload(db, schedule_set_id)
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    payload = json.loads(serialized)
    checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    existing = db.scalar(
        select(DataSnapshot).where(
            DataSnapshot.schedule_set_id == schedule_set_id,
            DataSnapshot.checksum == checksum,
        )
    )
    if existing:
        return existing
    revision = (
        int(
            db.scalar(
                select(func.coalesce(func.max(DataSnapshot.revision), 0)).where(
                    DataSnapshot.schedule_set_id == schedule_set_id
                )
            )
            or 0
        )
        + 1
    )
    snapshot = DataSnapshot(
        schedule_set_id=schedule_set_id,
        revision=revision,
        checksum=checksum,
        payload=payload,
        created_by=created_by,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot
