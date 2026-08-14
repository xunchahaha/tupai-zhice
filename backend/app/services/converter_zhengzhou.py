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
    CalendarEventBinding,
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
OFFICIAL_VERSION_NAME = "郑州官方原始课表（完全重复行已去重）"


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


def _clock_minutes(clock: str) -> int:
    hours, minutes = (int(part) for part in clock.split(":", 1))
    return hours * 60 + minutes


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


def _lesson_group(row: dict[str, Any]) -> tuple[str, str, str]:
    """课次组：同一班级在同一天同一时段最多只能有一节课。"""
    return (row["班级标签"], row["上课日期"].isoformat(), row["上课时段"])


def _split_placeholder_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """丢弃教室待确认的行。

    经业务确认，「教室-待校区确认」不是待补的教室，而是标记该课次不占用校区教室。
    这些课次不进入教室排课范围，因此在导入阶段整行丢弃，而不是建成停用教室后继续参与求解。
    丢弃口径必须可追溯，所以同时返回被丢弃的行数、课次组数和受影响班级。
    """
    kept = [row for row in rows if row["教室标签"] != PLACEHOLDER_ROOM]
    dropped = [row for row in rows if row["教室标签"] == PLACEHOLDER_ROOM]
    kept_groups = {_lesson_group(row) for row in kept}
    dropped_groups = {_lesson_group(row) for row in dropped}
    return kept, {
        "placeholder_room": PLACEHOLDER_ROOM,
        "dropped_rows": len(dropped),
        "dropped_lesson_groups": len(dropped_groups - kept_groups),
        "affected_classes": sorted({row["班级标签"] for row in dropped}),
    }


def _row_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    """官方确认只删除完全相同的重复行，因此身份键覆盖源表全部 14 个字段。"""
    return (
        row["业务线"],
        row["产品班型"],
        row["班级标签"],
        row["教室标签"],
        row["编排来源"],
        row["编排阶段"],
        row["计划课次"],
        row["计划课时"],
        row["课次序号"],
        row["课节名称"],
        row["上课日期"].isoformat(),
        row["上课时段"],
        row["课节时长小时"],
        row["授课教师"],
    )


LESSON_IDENTITY_FIELDS = ("班级标签", "课次序号", "课节名称", "上课日期", "上课时段")
MUTABLE_FIELDS = (
    "业务线",
    "产品班型",
    "教室标签",
    "编排来源",
    "编排阶段",
    "计划课次",
    "计划课时",
    "课节时长小时",
    "授课教师",
)


def _lesson_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    """一节课的稳定身份。

    只由「哪个班、第几课次、什么课、哪天、什么时段」决定。教室、教师、编排来源等
    都是这节课的**属性**而非身份——把它们放进身份键，会让修正表格里的一个单元格
    变成"新增一节课"，重新导入即成倍产生脏数据。
    """
    return (
        row["班级标签"],
        row["课次序号"],
        row["课节名称"],
        row["上课日期"].isoformat(),
        row["上课时段"],
    )


def _business_id(row: dict[str, Any], campus_business_id: str) -> str:
    digest = _digest(_lesson_identity(row))
    return f"{campus_business_id}-{digest[:16]}"


def _digest(value: tuple[Any, ...]) -> str:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().upper()


def _source_row_id(row: dict[str, Any]) -> str:
    return _digest(_row_identity(row))


def _collect_lesson_rows(
    rows: list[dict[str, Any]], campus_business_id: str
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """按稳定身份收敛到「每节课一条」，并报告身份相同但内容冲突的行。

    完全相同的行直接消除。身份相同、可变字段不同的行是**语义重复**（同一个班同一
    时刻被登记了两节不同的课或两个不同教师），源表里真实存在，必须报出来而不是
    静默双写进课表。取用顺序按整行内容排序，保证同一份表多次导入结果一致。
    """
    grouped: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = {}
    for row in rows:
        business_id = _business_id(row, campus_business_id)
        grouped.setdefault(business_id, {})[_row_identity(row)] = row

    session_rows: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for business_id, variants in grouped.items():
        ordered = [variants[key] for key in sorted(variants)]
        session_rows[business_id] = ordered[0]
        if len(ordered) == 1:
            continue
        differing = sorted(
            field for field in MUTABLE_FIELDS if len({str(item[field]) for item in ordered}) > 1
        )
        conflicts.append(
            {
                "业务标识": business_id,
                "班级标签": ordered[0]["班级标签"],
                "课次序号": int(str(ordered[0]["课次序号"])),
                "上课日期": ordered[0]["上课日期"].isoformat(),
                "上课时段": ordered[0]["上课时段"],
                "冲突行数": len(ordered),
                "差异字段": differing,
                "取用": {field: str(ordered[0][field]) for field in differing},
                "丢弃": [
                    {field: str(item[field]) for field in differing} for item in ordered[1:]
                ],
            }
        )
    conflicts.sort(key=lambda item: str(item["业务标识"]))
    return session_rows, {
        "conflicting_lessons": len(conflicts),
        "discarded_rows": sum(int(str(item["冲突行数"])) - 1 for item in conflicts),
        "examples": conflicts[:20],
    }


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


def _class_slot_conflicts(session_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """报告「同一班级同一天同一时段有多节课」。

    这是排课的基本不变量，源表里却真实存在。不报出来的话，教务只会看到求解器返回
    INFEASIBLE，无从知道问题出在输入数据而不是约束配置。
    """
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in session_rows.values():
        grouped.setdefault(_lesson_group(row), []).append(row)
    conflicts = [
        {
            "班级标签": key[0],
            "上课日期": key[1],
            "上课时段": key[2],
            "课节数": len(items),
            "课节名称": sorted({str(item["课节名称"]) for item in items}),
        }
        for key, items in grouped.items()
        if len(items) > 1
    ]
    conflicts.sort(key=lambda item: (str(item["班级标签"]), str(item["上课日期"])))
    return {
        "lesson_groups": len(grouped),
        "conflicting_groups": len(conflicts),
        "extra_lessons": sum(int(str(item["课节数"])) - 1 for item in conflicts),
        "examples": conflicts[:20],
    }


def _remove_orphan_sessions(
    db: Session, campus_id: str, keep_business_ids: set[str]
) -> dict[str, Any]:
    """删除源表里已经不存在的课次，让导入收敛到工作簿的当前状态。

    唯一的例外是被求解产出的课表版本引用过的课次：直接删会破坏历史版本与回滚链，
    因此保留并报出来，由教务决定怎么处理。
    """
    orphans = [
        item
        for item in db.scalars(select(CourseSession).where(CourseSession.campus_id == campus_id))
        if item.business_id not in keep_business_ids
    ]
    if not orphans:
        return {"deleted": 0, "retained_by_schedule": 0, "retained_examples": []}

    official_version_id = db.scalar(
        select(ScheduleVersion.id).where(ScheduleVersion.name == OFFICIAL_VERSION_NAME)
    )
    orphan_ids = {item.id for item in orphans}
    referenced_elsewhere = set(
        db.scalars(
            select(ScheduleAssignment.course_session_id).where(
                ScheduleAssignment.course_session_id.in_(orphan_ids),
                ScheduleAssignment.schedule_version_id != official_version_id
                if official_version_id
                else ScheduleAssignment.course_session_id.is_not(None),
            )
        )
    )
    removable = [item for item in orphans if item.id not in referenced_elsewhere]
    retained = [item for item in orphans if item.id in referenced_elsewhere]
    if removable:
        removable_ids = [item.id for item in removable]
        db.execute(
            delete(CalendarEventBinding).where(
                CalendarEventBinding.course_session_id.in_(removable_ids)
            )
        )
        db.execute(
            delete(ScheduleAssignment).where(
                ScheduleAssignment.course_session_id.in_(removable_ids)
            )
        )
        db.execute(delete(CourseSession).where(CourseSession.id.in_(removable_ids)))
        db.flush()
    return {
        "deleted": len(removable),
        "retained_by_schedule": len(retained),
        "retained_examples": sorted(item.business_id for item in retained)[:20],
    }


def import_schedule_workbook(
    db: Session,
    workbook_path: Path,
    campus_business_id: str = CAMPUS_BUSINESS_ID,
    campus_name: str = CAMPUS_NAME,
) -> dict[str, Any]:
    source_rows = _read_rows(workbook_path)
    if not source_rows:
        raise RuntimeError(f"未从 {workbook_path.name} 解析到任何数据行")
    rows, placeholder_report = _split_placeholder_rows(source_rows)
    if not rows:
        raise RuntimeError(
            f"{workbook_path.name} 的全部 {len(source_rows)} 行教室标签均为"
            f"「{PLACEHOLDER_ROOM}」，没有可排课的课次"
        )

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
                # 源表「授课教师」列填的是教研组，不是自然人。
                "is_group": True,
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
            {"name": label, "is_active": True},
        )

    # 时段顺序按真实上课时间排序，不依赖任何校区的固定作息表。
    clock_ranges = sorted(
        {(row["开始时间"], row["结束时间"]) for row in rows},
        key=lambda item: (_clock_minutes(item[0]), _clock_minutes(item[1])),
    )
    slot_keys = sorted(
        {(weekday, start, end) for weekday in WEEKDAYS for start, end in clock_ranges},
        key=lambda item: (
            WEEKDAYS.index(item[0]),
            _clock_minutes(item[1]),
            _clock_minutes(item[2]),
        ),
    )
    # 时段标识必须带结束时间：同一开始时间可以对应不同时长（例如 08:30-10:00 与
    # 08:30-11:30），只用开始时间会让两个时段撞同一个业务标识并触发唯一约束冲突。
    slot_business_ids: dict[tuple[str, str, str], str] = {}
    for sequence, (weekday, start, end) in enumerate(slot_keys, 1):
        business_id = f"SLOT-{weekday}-{start.replace(':', '')}-{end.replace(':', '')}"
        slot_business_ids[(weekday, start, end)] = business_id
        _upsert(
            db,
            TimeSlot,
            {"campus_id": campus.id, "business_id": business_id},
            {
                "weekday": weekday,
                "start_time": start,
                "end_time": end,
                "kind": f"{start}-{end}",
                "sequence": sequence,
            },
        )

    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        deduped[_row_identity(row)] = row

    session_rows, duplicate_report = _collect_lesson_rows(
        list(deduped.values()), campus_business_id
    )

    existing_sessions = {
        item.business_id: item
        for item in db.scalars(
            select(CourseSession).where(CourseSession.campus_id == campus.id)
        )
    }
    new_rows = []
    for business_id, row in session_rows.items():
        values = {
            "source_row_id": _source_row_id(row),
            "business_line": row["业务线"],
            "product_type": row["产品班型"],
            "class_business_id": row["班级标签"],
            "teacher_business_id": row["授课教师"],
            "subject": _lesson_subject(row["课节名称"]),
            "lesson_name": row["课节名称"],
            "schedule_source": row["编排来源"],
            "stage": row["编排阶段"],
            "planned_sessions": row["计划课次"],
            "planned_hours": row["计划课时"],
            "session_no": row["课次序号"],
            "lesson_date": row["上课日期"],
            "duration_minutes": int(row["课节时长小时"] * 60),
            "suggested_slot_id": slot_business_ids[(row["星期"], row["开始时间"], row["结束时间"])],
            "fixed_start_time": row["开始时间"],
            "fixed_end_time": row["结束时间"],
            "original_room_business_id": row["教室标签"],
        }
        existing = existing_sessions.get(business_id)
        if existing is None:
            new_rows.append((business_id, values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
    for start_index in range(0, len(new_rows), 500):
        chunk = new_rows[start_index : start_index + 500]
        db.add_all(
            [
                CourseSession(
                    campus_id=campus.id,
                    business_id=business_id,
                    **values,
                )
                for business_id, values in chunk
            ]
        )
        db.flush()

    orphan_report = _remove_orphan_sessions(db, campus.id, set(session_rows))

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
            "rows_total": len(source_rows),
            "rows_dropped_placeholder_room": placeholder_report["dropped_rows"],
            "rows_kept": len(rows),
            "rows_deduped": len(deduped),
        },
    )
    db.add(snapshot)
    db.flush()

    version_stats: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    version = db.scalar(
        select(ScheduleVersion).where(ScheduleVersion.name == OFFICIAL_VERSION_NAME)
    )
    if version is None:
        run = SolverRun(
            snapshot_id=snapshot.id,
            run_type="import",
            status="completed",
            model_status="IMPORTED",
            request_payload={"source": workbook_path.name, "deduplication": "exact_row"},
        )
        db.add(run)
        db.flush()
        version_no = (db.scalar(select(func.max(ScheduleVersion.version_no))) or 0) + 1
        version = ScheduleVersion(
            version_no=version_no,
            name=OFFICIAL_VERSION_NAME,
            status="published",
            solver_run_id=run.id,
            published_at=now,
            metrics={"assignment_count": len(session_rows), "source_rows": len(rows)},
        )
        db.add(version)
        db.flush()
    else:
        db.execute(
            delete(ScheduleAssignment).where(ScheduleAssignment.schedule_version_id == version.id)
        )
        version.status = "published"
        version.published_at = now
        version.metrics = {"assignment_count": len(session_rows), "source_rows": len(rows)}
    for business_id, row in session_rows.items():
        db.add(
            ScheduleAssignment(
                schedule_version_id=version.id,
                course_session_id=session_ids[business_id],
                lesson_date=row["上课日期"],
                slot_business_id=slot_business_ids[(row["星期"], row["开始时间"], row["结束时间"])],
                room_business_id=row["教室标签"],
            )
        )
    db.flush()
    version_stats.append({"name": OFFICIAL_VERSION_NAME, "rows": len(session_rows)})

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
        "campus": campus_business_id,
        "rows_total": len(source_rows),
        "rows_dropped_placeholder_room": placeholder_report["dropped_rows"],
        "rows_kept": len(rows),
        "rows_deduped": len(deduped),
        "teachers": len(teachers),
        "class_groups": len(class_labels),
        "rooms": len(room_labels),
        "time_slots": len(slot_keys),
        "course_sessions_created": len(new_rows),
        "schedule_versions": version_stats,
        "lessons": len(session_rows),
        "duplicate_lessons": duplicate_report,
        "class_slot_conflicts": _class_slot_conflicts(session_rows),
        "orphans": orphan_report,
        "warnings": {
            "dropped_placeholder_room": placeholder_report,
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
