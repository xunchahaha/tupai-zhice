"""公开课表层的内存投影纯函数与公开 payload 组装（docs/roadmap/06 §3 A2）。

public 四表不是数据库表，而是这组以 ``_public_*`` 命名的内存投影纯函数。它们
原样自 ``app/api.py`` 迁入（逐字节等价，签名不变），api.py 顶部同名 re-import，
飞书同步分发链路的调用点因此零改动；公开链接端点复用同一投影，保证公开面与
飞书公开表永远只有一份口径。
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AuditLog,
    ClassGroup,
    CourseSession,
    Room,
    ScheduleAssignment,
    ScheduleSet,
    ScheduleVersion,
    Teacher,
    TimeSlot,
)
from ..timezone import as_shanghai
from .snapshot import version_course_map


def _public_projection_key(schedule_set_id: str, resource: str, source_id: str) -> str:
    """Stable opaque record key for a public Bitable projection.

    Public tables need a key so sync can update rather than append, but a
    course UUID or business ID would become an unnecessary internal identifier
    in the MiaoDa data source.  A namespaced digest is stable within one
    timetable and opaque outside the service.
    """

    value = f"tupai-public|{schedule_set_id}|{resource}|{source_id}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _current_published_schedule(
    db: Session, schedule_set_id: str
) -> ScheduleVersion | None:
    return db.scalar(
        select(ScheduleVersion)
        .where(
            ScheduleVersion.schedule_set_id == schedule_set_id,
            ScheduleVersion.status == "published",
        )
        .order_by(ScheduleVersion.published_at.desc(), ScheduleVersion.version_no.desc())
    )


def _assignment_rows(db: Session, schedule_id: str | None) -> list[ScheduleAssignment]:
    if not schedule_id:
        return []
    return list(
        db.scalars(
            select(ScheduleAssignment)
            .where(ScheduleAssignment.schedule_version_id == schedule_id)
            .order_by(ScheduleAssignment.lesson_date, ScheduleAssignment.course_session_id)
        )
    )


def _public_release_baseline(
    db: Session, schedule: ScheduleVersion | None
) -> ScheduleVersion | None:
    """Return the timetable that this published release actually replaced.

    ``parent_id`` describes a solver lineage and can point to a draft or an
    old ancestor.  The publish/rollback audit event records the version that
    was publicly active immediately before this release, which is the only
    valid baseline for a parent-facing adjustment notice.
    """

    if schedule is None:
        return None
    release = db.scalar(
        select(AuditLog)
        .where(
            AuditLog.resource_type == "schedule",
            AuditLog.resource_id == schedule.id,
            AuditLog.action.in_(("publish", "rollback")),
        )
        .order_by(AuditLog.created_at.desc())
    )
    baseline_id = (
        str((release.detail or {}).get("replaced_published_schedule_id") or "")
        if release
        else ""
    )
    if not baseline_id:
        return None
    baseline = db.get(ScheduleVersion, baseline_id)
    if baseline is None or baseline.schedule_set_id != schedule.schedule_set_id:
        return None
    return baseline


def _public_schedule_maps(
    db: Session, schedule_set_id: str
) -> tuple[
    list[CourseSession],
    dict[tuple[str, str], ClassGroup],
    dict[tuple[str, str], Room],
    dict[tuple[str, str], TimeSlot],
]:
    schedule = _current_published_schedule(db, schedule_set_id)
    courses = list(version_course_map(db, schedule).values()) if schedule else []
    classes = list(
        db.scalars(
            select(ClassGroup).where(ClassGroup.schedule_set_id == schedule_set_id)
        )
    )
    rooms = list(db.scalars(select(Room).where(Room.schedule_set_id == schedule_set_id)))
    slots = list(db.scalars(select(TimeSlot).where(TimeSlot.schedule_set_id == schedule_set_id)))
    return (
        courses,
        {(item.campus_id, item.business_id): item for item in classes},
        {(item.campus_id, item.business_id): item for item in rooms},
        {(item.campus_id, item.business_id): item for item in slots},
    )


def _public_weekday(value: date | None, slot: TimeSlot | None) -> str:
    if value is not None:
        return ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[value.weekday()]
    return slot.weekday if slot else ""


def _public_assignment_snapshot(
    assignment: ScheduleAssignment | None,
    course: CourseSession,
    rooms: dict[tuple[str, str], Room],
    slots: dict[tuple[str, str], TimeSlot],
) -> dict[str, str]:
    if assignment is None:
        return {"date": "", "weekday": "", "start": "", "end": "", "location": ""}
    slot = slots.get((course.campus_id, assignment.slot_business_id))
    room = rooms.get((course.campus_id, assignment.room_business_id))
    return {
        "date": assignment.lesson_date.isoformat() if assignment.lesson_date else "",
        "weekday": _public_weekday(assignment.lesson_date, slot),
        "start": (slot.start_time if slot else course.fixed_start_time) or "",
        "end": (slot.end_time if slot else course.fixed_end_time) or "",
        # Never fall back to a business ID in a public-facing projection.
        "location": room.name if room else "待定",
    }


def _public_time_text(snapshot: dict[str, str]) -> str:
    date_text = snapshot["date"]
    weekday = snapshot["weekday"]
    clocks = "-".join(item for item in (snapshot["start"], snapshot["end"]) if item)
    return " ".join(item for item in (date_text, weekday, clocks) if item)


def _public_projection_updated_at(schedule: ScheduleVersion | None) -> str:
    """Use the published release time instead of sync wall-clock time.

    A manual retry must not make every public record look modified.  The
    projection only changes when the published timetable changes, so its
    visible update time should be stable for that release as well.
    """

    published_at = as_shanghai(schedule.published_at) if schedule else None
    return published_at.isoformat() if published_at else ""


def _public_class_identity(course: CourseSession) -> str:
    """Return a stable class key that remains unique across campuses."""

    return f"{course.campus_id}:{course.class_business_id}"


def _public_schedule_sort_key(row: dict[str, Any]) -> tuple[str, ...]:
    """Sort public timetable rows in display order, with deterministic ties."""

    return (
        str(row.get("上课日期") or ""),
        str(row.get("开始时间") or ""),
        str(row.get("结束时间") or ""),
        str(row.get("班级标识") or ""),
        str(row.get("业务标识") or ""),
    )


def _public_class_index_rows(
    db: Session, schedule_set_id: str
) -> list[dict[str, Any]]:
    schedule = _current_published_schedule(db, schedule_set_id)
    assignments = {
        item.course_session_id: item
        for item in _assignment_rows(db, schedule.id if schedule else None)
    }
    courses, classes, rooms, slots = _public_schedule_maps(db, schedule_set_id)
    updated_at = _public_projection_updated_at(schedule)
    # This is a class index, not the student-facing timetable itself.  The
    # detailed assignment rows already live in the operational ``课表`` table;
    # duplicating thousands of rows here made the public source unnecessarily
    # large.  Aggregate one deterministic row per class for MiaoDa and view
    # navigation.
    grouped: dict[str, dict[str, Any]] = {}
    for course in courses:
        assignment = assignments.get(course.id)
        if assignment is None:
            # The public source is an actual timetable, not an internal audit
            # table.  Empty/unpublished course rows neither help MiaoDa nor
            # should be repeatedly synchronized just to say "不展示".
            continue
        snapshot = _public_assignment_snapshot(assignment, course, rooms, slots)
        class_group = classes.get((course.campus_id, course.class_business_id))
        class_identity = _public_class_identity(course)
        entry = grouped.setdefault(
            class_identity,
            {
                "班级标识": class_identity,
                "班级名称": class_group.name if class_group else "未分班",
                "items": [],
                "subjects": set(),
                "locations": set(),
            },
        )
        entry["items"].append(snapshot)
        if course.subject:
            entry["subjects"].add(course.subject)
        if snapshot["location"]:
            entry["locations"].add(snapshot["location"])

    rows: list[dict[str, Any]] = []
    for class_identity, entry in grouped.items():
        items = sorted(
            entry["items"],
            key=lambda item: (
                item["date"],
                item["start"],
                item["end"],
            ),
        )
        first = items[0] if items else {"date": "", "weekday": "", "start": "", "end": ""}
        dates = [item["date"] for item in items if item["date"]]
        rows.append(
            {
                "业务标识": _public_projection_key(
                    schedule_set_id, "public_class_schedule", class_identity
                ),
                "是否展示": "是",
                "班级标识": entry["班级标识"],
                "班级名称": entry["班级名称"],
                # Keep the first assignment in the legacy display columns so
                # old views remain readable, while the aggregate columns make
                # the row's purpose explicit.
                "上课日期": first["date"],
                "星期": first["weekday"],
                "开始时间": first["start"],
                "结束时间": first["end"],
                "排序键": " ".join(
                    item for item in (first["date"], first["start"], first["end"]) if item
                ),
                "课程名称": f"共{len(items)}节课",
                "学科": " / ".join(sorted(entry["subjects"])),
                "上课地点": " / ".join(sorted(entry["locations"])),
                "课表版本": f"V{schedule.version_no}" if schedule else "",
                "课次总数": len(items),
                "首课日期": dates[0] if dates else "",
                "末课日期": dates[-1] if dates else "",
                "更新时间": updated_at,
            }
        )
    return sorted(rows, key=_public_schedule_sort_key)


def _public_class_schedule_rows(
    db: Session, schedule_set_id: str
) -> list[dict[str, Any]]:
    """Return the retired class projection for compatibility exports only."""

    return _public_class_index_rows(db, schedule_set_id)


def _public_class_links_rows(
    db: Session, schedule_set_id: str
) -> list[dict[str, Any]]:
    """Export one stable MiaoDa/public-view link row per published class.

    MiaoDa links are intentionally blank on first export: an operator creates
    the MiaoDa app/page and pastes its public URL into this index table.  The
    sync layer preserves that hand-authored value on subsequent exports.
    """

    schedule = _current_published_schedule(db, schedule_set_id)
    class_rows = _public_class_index_rows(db, schedule_set_id)
    updated_at = _public_projection_updated_at(schedule)
    version = f"V{schedule.version_no}" if schedule else ""
    return [
        {
            "业务标识": _public_projection_key(
                schedule_set_id, "public_class_links", str(row["班级标识"])
            ),
            "课表版本": version,
            "班级标识": row["班级标识"],
            "班级名称": row["班级名称"],
            "学生/家长妙搭链接": "",
            "公开视图链接": "",
            "公开入口类型": "",
            "访问模式": "",
            "状态": "",
            "更新时间": updated_at,
            "失效时间": "",
            "备注": "",
        }
        for row in class_rows
    ]


def _public_adjustment_notice_rows(
    db: Session, schedule_set_id: str
) -> list[dict[str, Any]]:
    schedule = _current_published_schedule(db, schedule_set_id)
    baseline = _public_release_baseline(db, schedule)
    current_assignments = {
        item.course_session_id: item
        for item in _assignment_rows(db, schedule.id if schedule else None)
    }
    parent_assignments = {
        item.course_session_id: item
        for item in _assignment_rows(db, baseline.id if baseline else None)
    }
    courses, classes, rooms, slots = _public_schedule_maps(db, schedule_set_id)
    updated_at = _public_projection_updated_at(schedule)
    rows: list[dict[str, Any]] = []
    for course in courses:
        before_assignment = parent_assignments.get(course.id)
        after_assignment = current_assignments.get(course.id)
        before = _public_assignment_snapshot(before_assignment, course, rooms, slots)
        after = _public_assignment_snapshot(after_assignment, course, rooms, slots)
        visible = bool(baseline) and before != after
        if visible and before_assignment is None and after_assignment is not None:
            change_type = "新增课程"
        elif visible and before_assignment is not None and after_assignment is None:
            change_type = "取消课程"
        elif visible and (
            before["date"], before["start"], before["end"]
        ) != (
            after["date"], after["start"], after["end"]
        ) and before["location"] != after["location"]:
            change_type = "时间及地点调整"
        elif visible and (
            before["date"], before["start"], before["end"]
        ) != (
            after["date"], after["start"], after["end"]
        ):
            change_type = "时间调整"
        elif visible:
            change_type = "地点调整"
        else:
            # Only actual changes belong in the public notice source.  Keeping
            # thousands of blank "不展示" records turns a retry into a full-table
            # write and makes the public MiaoDa data source needlessly noisy.
            continue
        class_group = classes.get((course.campus_id, course.class_business_id))
        rows.append(
            {
                "业务标识": _public_projection_key(
                    schedule_set_id, "public_adjustment_notice", course.id
                ),
                "是否展示": "是",
                "公告状态": "已生效",
                "通用提示": "课程安排已更新，请以本表为准",
                "调整类型": change_type,
                "班级名称": class_group.name if class_group else "未分班",
                "课程名称": course.lesson_name or "课程安排",
                "原上课时间": _public_time_text(before),
                "新上课时间": _public_time_text(after),
                "原上课地点": before["location"],
                "新上课地点": after["location"],
                "生效版本": f"V{schedule.version_no}" if schedule else "",
                "更新时间": updated_at,
            }
        )
    return rows


def _published_adjustment_count(db: Session, schedule_set_id: str) -> int:
    schedule = _current_published_schedule(db, schedule_set_id)
    baseline = _public_release_baseline(db, schedule)
    if schedule is None or baseline is None:
        return 0
    before = {
        item.course_session_id: (item.lesson_date, item.slot_business_id, item.room_business_id)
        for item in _assignment_rows(db, baseline.id)
    }
    after = {
        item.course_session_id: (item.lesson_date, item.slot_business_id, item.room_business_id)
        for item in _assignment_rows(db, schedule.id)
    }
    return sum(
        before.get(course_id) != after.get(course_id)
        for course_id in set(before) | set(after)
    )


# ---------------------------------------------------------------------------
# 公开链接 payload 组装（显式白名单，禁止整模型透传——docs/roadmap/06 §4）。
# ---------------------------------------------------------------------------


def _course_has_teacher(course: CourseSession, teacher_business_id: str) -> bool:
    return (
        course.teacher_business_id == teacher_business_id
        or teacher_business_id in course.teacher_business_ids
    )


def _scoped_published_courses(
    db: Session,
    schedule_set_id: str,
    *,
    campus_id: str | None = None,
    class_business_id: str | None = None,
    teacher_business_id: str | None = None,
) -> tuple[ScheduleVersion | None, list[CourseSession]]:
    """当前发布版本，以及其中一个链接范围内的课次。"""

    schedule = _current_published_schedule(db, schedule_set_id)
    if schedule is None:
        return None, []
    courses = [
        course
        for course in version_course_map(db, schedule).values()
        if (campus_id is None or course.campus_id == campus_id)
        and (class_business_id is None or course.class_business_id == class_business_id)
        and (
            teacher_business_id is None
            or _course_has_teacher(course, teacher_business_id)
        )
    ]
    return schedule, courses


def public_schedule_entries(
    db: Session,
    schedule_set_id: str,
    *,
    campus_id: str | None = None,
    class_business_id: str | None = None,
    teacher_business_id: str | None = None,
) -> list[dict[str, Any]]:
    """一个公开链接范围内的课表行，按展示顺序排列。

    这是内部形状：全部对公展示字段 + 保证 ICS UID 跨版本稳定的 assignment id。
    JSON 响应必须经 ``_public_row_view`` 投影到白名单，内部标识不出服务层。
    """

    schedule, courses = _scoped_published_courses(
        db,
        schedule_set_id,
        campus_id=campus_id,
        class_business_id=class_business_id,
        teacher_business_id=teacher_business_id,
    )
    if schedule is None:
        return []
    assignments = {
        item.course_session_id: item for item in _assignment_rows(db, schedule.id)
    }
    _courses, classes, rooms, slots = _public_schedule_maps(db, schedule_set_id)
    teachers = {
        item.business_id: item
        for item in db.scalars(
            select(Teacher).where(Teacher.schedule_set_id == schedule_set_id)
        )
    }
    rows: list[dict[str, Any]] = []
    for course in courses:
        assignment = assignments.get(course.id)
        if assignment is None:
            continue
        class_group = classes.get((course.campus_id, course.class_business_id))
        snapshot = _public_assignment_snapshot(assignment, course, rooms, slots)
        names: list[str] = []
        for teacher_id in (course.teacher_business_id, *course.teacher_business_ids):
            if not teacher_id:
                continue
            teacher = teachers.get(teacher_id)
            if teacher is not None and teacher.name not in names:
                names.append(teacher.name)
        rows.append(
            {
                "assignment_id": assignment.id,
                "date": snapshot["date"],
                "weekday": snapshot["weekday"],
                "start": snapshot["start"],
                "end": snapshot["end"],
                "class_name": class_group.name if class_group else "未分班",
                "subject": course.subject,
                "lesson_name": course.lesson_name or course.subject or "课程安排",
                "teacher_names": names,
                "location": snapshot["location"],
            }
        )
    rows.sort(
        key=lambda row: (
            row["date"],
            row["start"],
            row["end"],
            row["class_name"],
            row["lesson_name"],
        )
    )
    return rows


def _public_row_view(row: dict[str, Any], *, show_teacher_names: bool) -> dict[str, Any]:
    """把内部行投影成公开 JSON 白名单字段。"""

    return {
        "date": row["date"],
        "weekday": row["weekday"],
        "start": row["start"],
        "end": row["end"],
        "class_name": row["class_name"],
        "subject": row["subject"],
        "lesson_name": row["lesson_name"],
        "teacher_names": list(row["teacher_names"]) if show_teacher_names else [],
        "location": row["location"],
    }


def _public_adjustment_views(
    db: Session, schedule_set_id: str, courses: list[CourseSession]
) -> list[dict[str, Any]]:
    """调课通知的公开白名单投影，范围随 ``courses`` 过滤。

    复用 ``_public_adjustment_notice_rows`` 并按同一稳定摘要键（业务标识）把
    通知行对回课次，公开链接 payload 与飞书公告表不会出现两份口径。
    """

    if not courses:
        return []
    notice_rows = {
        row["业务标识"]: row
        for row in _public_adjustment_notice_rows(db, schedule_set_id)
    }
    views: list[dict[str, Any]] = []
    for course in courses:
        notice = notice_rows.get(
            _public_projection_key(
                schedule_set_id, "public_adjustment_notice", course.id
            )
        )
        if notice is None:
            continue
        views.append(
            {
                "type": notice["调整类型"],
                "class_name": notice["班级名称"],
                "course_name": notice["课程名称"],
                "before_time": notice["原上课时间"],
                "after_time": notice["新上课时间"],
                "before_location": notice["原上课地点"],
                "after_location": notice["新上课地点"],
            }
        )
    return views


def _public_dates(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(row["date"] for row in rows if row["date"])


def public_class_payload(
    db: Session,
    schedule_set_id: str,
    campus_id: str,
    class_business_id: str,
    *,
    show_teacher_names: bool = True,
) -> dict[str, Any]:
    """班级链接（学生/家长二维码入口）的公开 payload，仅白名单字段。"""

    schedule, courses = _scoped_published_courses(
        db, schedule_set_id, campus_id=campus_id, class_business_id=class_business_id
    )
    rows = public_schedule_entries(
        db, schedule_set_id, campus_id=campus_id, class_business_id=class_business_id
    )
    class_group = db.scalar(
        select(ClassGroup).where(
            ClassGroup.schedule_set_id == schedule_set_id,
            ClassGroup.campus_id == campus_id,
            ClassGroup.business_id == class_business_id,
        )
    )
    dates = _public_dates(rows)
    return {
        "scope": "class",
        "display_name": class_group.name if class_group else "未分班",
        "show_teacher_names": show_teacher_names,
        "version_no": schedule.version_no if schedule else None,
        "published_at": _public_projection_updated_at(schedule),
        "first_date": dates[0] if dates else "",
        "last_date": dates[-1] if dates else "",
        "rows": [
            _public_row_view(row, show_teacher_names=show_teacher_names)
            for row in rows
        ],
        "adjustments": _public_adjustment_views(db, schedule_set_id, courses),
    }


def public_teacher_payload(
    db: Session,
    schedule_set_id: str,
    campus_id: str,
    teacher_business_id: str,
    *,
    show_teacher_names: bool = True,
) -> dict[str, Any]:
    """教师链接（对外课表）的公开 payload；只出姓名，不外泄工号。"""

    schedule, courses = _scoped_published_courses(
        db, schedule_set_id, campus_id=campus_id, teacher_business_id=teacher_business_id
    )
    rows = public_schedule_entries(
        db, schedule_set_id, campus_id=campus_id, teacher_business_id=teacher_business_id
    )
    teacher = db.scalar(
        select(Teacher).where(
            Teacher.schedule_set_id == schedule_set_id,
            Teacher.campus_id == campus_id,
            Teacher.business_id == teacher_business_id,
        )
    )
    dates = _public_dates(rows)
    return {
        "scope": "teacher",
        "display_name": teacher.name if teacher else "教师",
        "show_teacher_names": show_teacher_names,
        "version_no": schedule.version_no if schedule else None,
        "published_at": _public_projection_updated_at(schedule),
        "first_date": dates[0] if dates else "",
        "last_date": dates[-1] if dates else "",
        "rows": [
            _public_row_view(row, show_teacher_names=show_teacher_names)
            for row in rows
        ],
        "adjustments": _public_adjustment_views(db, schedule_set_id, courses),
    }


def public_directory_payload(db: Session, schedule_set_id: str) -> dict[str, Any]:
    """school 范围（督导公示）的班级索引 payload，复用班级索引投影。"""

    schedule = _current_published_schedule(db, schedule_set_id)
    schedule_set = db.get(ScheduleSet, schedule_set_id)
    classes: list[dict[str, Any]] = []
    for row in _public_class_index_rows(db, schedule_set_id):
        # 班级标识形如 "<campus_id>:<business_id>"，公开面只带业务标识，
        # 校区内部主键不外泄。
        identity = str(row.get("班级标识") or "")
        classes.append(
            {
                "class_business_id": identity.rsplit(":", 1)[-1] if identity else "",
                "class_name": str(row.get("班级名称") or ""),
                "session_count": int(row.get("课次总数") or 0),
                "first_date": str(row.get("首课日期") or ""),
                "last_date": str(row.get("末课日期") or ""),
            }
        )
    return {
        "scope": "school",
        "display_name": schedule_set.name if schedule_set else "公开课表",
        "version_no": schedule.version_no if schedule else None,
        "published_at": _public_projection_updated_at(schedule),
        "classes": classes,
    }
