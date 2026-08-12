from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    Campus,
    ClassGroup,
    CourseSession,
    DataSnapshot,
    Room,
    ScheduleAssignment,
    ScheduleVersion,
    SolverRun,
    Teacher,
    TimeSlot,
)

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
PLACEHOLDER_ROOM = "教室-待校区确认"
CAMPUS_BUSINESS_ID = "CAMPUS-ZZ"
CAMPUS_NAME = "郑州校区"
SHEET_NAME = "课表数据源"
SLOT_START_ORDER = {"08:30": 1, "09:00": 2, "14:00": 3, "18:30": 4}


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _normalize_clock(text: str) -> str:
    hours, minutes = (part.strip() for part in text.split(":", 1))
    return f"{int(hours):02d}:{int(minutes):02d}"


def _add_minutes(clock: str, minutes: int) -> str:
    hours, mins = (int(part) for part in clock.split(":"))
    total = hours * 60 + mins + minutes
    return f"{total // 60:02d}:{total % 60:02d}"


def _lesson_subject(lesson_name: str) -> str:
    if "·" in lesson_name:
        return lesson_name.split("·", 1)[0].strip()
    return ""


def _read_rows(workbook_path: Path) -> list[dict[str, Any]]:
    workbook = load_workbook(workbook_path, data_only=True, read_only=True)
    if SHEET_NAME not in workbook.sheetnames:
        raise RuntimeError(f"工作簿缺少「{SHEET_NAME}」工作表，实际为：{workbook.sheetnames}")
    rows: list[dict[str, Any]] = []
    sheet = workbook[SHEET_NAME]
    for values in sheet.iter_rows(min_row=2, values_only=True):
        if not values or values[2] is None or values[10] is None or values[11] is None:
            continue
        lesson_date = _parse_date(values[10])
        time_text = str(values[11]).strip()
        if lesson_date is None or "-" not in time_text:
            continue
        start_text, end_text = (part.strip() for part in time_text.split("-", 1))
        try:
            start = _normalize_clock(start_text)
            end = _normalize_clock(end_text)
        except (ValueError, TypeError):
            continue
        rows.append(
            {
                "业务线": str(values[0] or "").strip(),
                "产品班型": str(values[1] or "").strip(),
                "班级标签": str(values[2]).strip(),
                "教室标签": str(values[3] or "").strip(),
                "编排来源": str(values[4] or "").strip(),
                "编排阶段": str(values[5] or "").strip(),
                "计划课次": int(float(str(values[6]))) if values[6] is not None else 0,
                "计划课时": float(str(values[7])) if values[7] is not None else 0,
                "课次序号": int(float(str(values[8]))) if values[8] is not None else 0,
                "课节名称": str(values[9] or "").strip(),
                "上课日期": lesson_date,
                "上课时段": f"{start}-{end}",
                "开始时间": start,
                "结束时间": end,
                "星期": WEEKDAYS[lesson_date.weekday()],
                "课节时长小时": float(str(values[12])) if values[12] is not None else 0,
                "授课教师": str(values[13] or "").strip(),
            }
        )
    workbook.close()
    return rows


def _upsert(
    db: Session, model: type[Any], match: dict[str, Any], values: dict[str, Any]
) -> Any:
    statement = select(model)
    for key, value in match.items():
        statement = statement.where(getattr(model, key) == value)
    instance = db.scalar(statement)
    if instance is None:
        instance = model(**match, **values)
        db.add(instance)
    else:
        for key, value in values.items():
            setattr(instance, key, value)
    return instance


def import_schedule_workbook(
    db: Session,
    workbook_path: Path,
    campus_business_id: str = CAMPUS_BUSINESS_ID,
    campus_name: str = CAMPUS_NAME,
) -> dict[str, Any]:
    rows = _read_rows(workbook_path)
    if not rows:
        raise RuntimeError(f"未从 {workbook_path.name} 解析到任何数据行")

    campus = _upsert(
        db, Campus, {"business_id": campus_business_id}, {"name": campus_name}
    )
    db.flush()

    teachers = sorted({row["授课教师"] for row in rows if row["授课教师"]})
    for name in teachers:
        subjects = Counter(
            _lesson_subject(row["课节名称"])
            for row in rows
            if row["授课教师"] == name and _lesson_subject(row["课节名称"])
        )
        _upsert(
            db,
            Teacher,
            {"campus_id": campus.id, "business_id": name},
            {
                "name": name,
                "subject": subjects.most_common(1)[0][0] if subjects else "",
            },
        )

    class_labels = sorted({row["班级标签"] for row in rows})
    for label in class_labels:
        class_rows = [row for row in rows if row["班级标签"] == label]
        _upsert(
            db,
            ClassGroup,
            {"campus_id": campus.id, "business_id": label},
            {
                "name": label,
                "grade": Counter(row["产品班型"] for row in class_rows).most_common(1)[0][0],
                "subject": Counter(row["业务线"] for row in class_rows).most_common(1)[0][0],
                "teacher_business_id": Counter(
                    row["授课教师"] for row in class_rows
                ).most_common(1)[0][0],
            },
        )

    room_labels = sorted({row["教室标签"] for row in rows if row["教室标签"]})
    for label in room_labels:
        _upsert(
            db,
            Room,
            {"campus_id": campus.id, "business_id": label},
            {
                "name": label,
                "is_active": label != PLACEHOLDER_ROOM,
            },
        )

    slot_keys = sorted(
        {(row["星期"], row["开始时间"]) for row in rows},
        key=lambda item: (WEEKDAYS.index(item[0]), SLOT_START_ORDER.get(item[1], 9), item[1]),
    )
    slot_business_ids: dict[tuple[str, str], str] = {}
    for sequence, (weekday, start) in enumerate(slot_keys, 1):
        business_id = f"SLOT-{weekday}-{start.replace(':', '')}"
        slot_business_ids[(weekday, start)] = business_id
        _upsert(
            db,
            TimeSlot,
            {"campus_id": campus.id, "business_id": business_id},
            {
                "weekday": weekday,
                "start_time": start,
                "end_time": _add_minutes(start, 180),
                "kind": f"{start}-{_add_minutes(start, 180)}",
                "sequence": sequence,
            },
        )

    class_index = {name: index for index, name in enumerate(sorted(class_labels), 1)}
    lesson_index = {
        name: index for index, name in enumerate(sorted({row["课节名称"] for row in rows}), 1)
    }
    slot_index = {key: index for index, key in enumerate(slot_keys, 1)}
    teacher_index = {name: index for index, name in enumerate(teachers, 1)}
    room_index = {name: index for index, name in enumerate(room_labels, 1)}

    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    placeholder_room_rows = 0
    for row in rows:
        if row["教室标签"] == PLACEHOLDER_ROOM:
            placeholder_room_rows += 1
        key = (
            row["班级标签"],
            row["课次序号"],
            row["课节名称"],
            row["授课教师"],
            row["教室标签"],
            row["星期"],
            row["开始时间"],
        )
        deduped[key] = row

    existing_ids = set(
        db.scalars(
            select(CourseSession.business_id).where(CourseSession.campus_id == campus.id)
        )
    )
    session_rows: dict[str, dict[str, Any]] = {}
    for row in deduped.values():
        business_id = (
            f"ZZ-{class_index[row['班级标签']]:02d}-{row['课次序号']:03d}-"
            f"{lesson_index[row['课节名称']]:02d}-"
            f"{slot_index[(row['星期'], row['开始时间'])]:02d}-"
            f"{teacher_index[row['授课教师']]:02d}-{room_index[row['教室标签']]:02d}"
        )
        session_rows[business_id] = row

    new_rows = [
        (business_id, row)
        for business_id, row in session_rows.items()
        if business_id not in existing_ids
    ]
    for start_index in range(0, len(new_rows), 500):
        chunk = new_rows[start_index : start_index + 500]
        db.add_all(
            [
                CourseSession(
                    campus_id=campus.id,
                    business_id=business_id,
                    class_business_id=row["班级标签"],
                    teacher_business_id=row["授课教师"],
                    subject=_lesson_subject(row["课节名称"]),
                    lesson_name=row["课节名称"],
                    schedule_source=row["编排来源"],
                    stage=row["编排阶段"],
                    planned_sessions=row["计划课次"],
                    planned_hours=row["计划课时"],
                    session_no=row["课次序号"],
                    lesson_date=row["上课日期"],
                    duration_minutes=int(row["课节时长小时"] * 60),
                    suggested_slot_id=slot_business_ids[(row["星期"], row["开始时间"])],
                )
                for business_id, row in chunk
            ]
        )
        db.flush()

    session_ids: dict[str, str] = {
        business_id: session_id
        for business_id, session_id in db.execute(
            select(CourseSession.business_id, CourseSession.id).where(
                CourseSession.campus_id == campus.id
            )
        )
    }

    checksum = hashlib.sha256(workbook_path.read_bytes()).hexdigest()
    revision = (db.scalar(select(func.max(DataSnapshot.revision))) or 0) + 1
    snapshot = DataSnapshot(
        revision=revision,
        checksum=checksum,
        payload={
            "source": workbook_path.name,
            "rows_total": len(rows),
            "rows_deduped": len(deduped),
        },
    )
    db.add(snapshot)
    db.flush()

    groups: dict[tuple[str, str], list[str]] = {}
    for business_id, row in session_rows.items():
        groups.setdefault((row["编排来源"], row["编排阶段"]), []).append(business_id)

    version_stats: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for (source, stage) in sorted(groups):
        name = f"{source}-{stage}"
        version = db.scalar(select(ScheduleVersion).where(ScheduleVersion.name == name))
        if version is None:
            run = SolverRun(
                snapshot_id=snapshot.id,
                run_type="import",
                status="completed",
                request_payload={"source": source, "stage": stage},
            )
            db.add(run)
            db.flush()
            version_no = (db.scalar(select(func.max(ScheduleVersion.version_no))) or 0) + 1
            version = ScheduleVersion(
                version_no=version_no,
                name=name,
                status="published",
                solver_run_id=run.id,
                published_at=now,
            )
            db.add(version)
            db.flush()
        else:
            db.execute(
                delete(ScheduleAssignment).where(
                    ScheduleAssignment.schedule_version_id == version.id
                )
            )
            version.status = "published"
            version.published_at = now
        for business_id in groups[(source, stage)]:
            row = session_rows[business_id]
            db.add(
                ScheduleAssignment(
                    schedule_version_id=version.id,
                    course_session_id=session_ids[business_id],
                    slot_business_id=slot_business_ids[(row["星期"], row["开始时间"])],
                    room_business_id=row["教室标签"],
                )
            )
        db.flush()
        version_stats.append({"name": name, "rows": len(groups[(source, stage)])})

    hour_warnings: list[dict[str, Any]] = []
    for 班型 in sorted({row["产品班型"] for row in rows}):
        plans = {
            (row["计划课次"], row["计划课时"])
            for row in rows
            if row["产品班型"] == 班型
        }
        for planned_sessions, planned_hours in plans:
            if planned_sessions and abs(planned_sessions * 3 - planned_hours) > 0.01:
                hour_warnings.append(
                    {
                        "产品班型": 班型,
                        "计划课次": planned_sessions,
                        "计划课时": planned_hours,
                        "按课次×3小时": planned_sessions * 3,
                    }
                )

    return {
        "campus": CAMPUS_BUSINESS_ID,
        "rows_total": len(rows),
        "rows_deduped": len(deduped),
        "teachers": len(teachers),
        "class_groups": len(class_labels),
        "rooms": len(room_labels),
        "time_slots": len(slot_keys),
        "course_sessions_created": len(new_rows),
        "schedule_versions": version_stats,
        "warnings": {
            "placeholder_room_rows": placeholder_room_rows,
            "placeholder_room": PLACEHOLDER_ROOM,
            "teachers_are_groups": teachers,
            "planned_hours_mismatch": hour_warnings,
        },
    }


def import_zhengzhou(db: Session, workbook_path: Path) -> dict[str, Any]:
    return import_schedule_workbook(db, workbook_path)


def main() -> None:
    if len(sys.argv) < 2:
        print("用法：python -m app.services.converter_zhengzhou <xlsx 路径>")
        raise SystemExit(2)
    workbook_path = Path(sys.argv[1])
    with SessionLocal() as db:
        result = import_schedule_workbook(db, workbook_path)
        db.commit()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
