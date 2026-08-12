from __future__ import annotations

from io import BytesIO
from typing import cast

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CourseSession, Room, ScheduleAssignment, ScheduleVersion, TimeSlot


def export_schedule_xlsx(db: Session, schedule: ScheduleVersion) -> bytes:
    assignments = list(
        db.scalars(
            select(ScheduleAssignment)
            .where(ScheduleAssignment.schedule_version_id == schedule.id)
            .order_by(ScheduleAssignment.slot_business_id, ScheduleAssignment.room_business_id)
        )
    )
    courses = {item.id: item for item in db.scalars(select(CourseSession))}
    rooms = {item.business_id: item for item in db.scalars(select(Room))}
    slots = {item.business_id: item for item in db.scalars(select(TimeSlot))}
    workbook = Workbook()
    sheet = cast(Worksheet, workbook.active)
    sheet.title = "课表"
    headers = [
        "版本",
        "场次ID",
        "班级ID",
        "教师ID",
        "课节名称",
        "星期",
        "开始时间",
        "结束时间",
        "时段ID",
        "教室ID",
        "教室名称",
        "变更状态",
    ]
    sheet.append(headers)
    for assignment in assignments:
        course = courses[assignment.course_session_id]
        room = rooms[assignment.room_business_id]
        slot = slots[assignment.slot_business_id]
        sheet.append(
            [
                schedule.version_no,
                course.business_id,
                course.class_business_id,
                course.teacher_business_id,
                course.lesson_name,
                slot.weekday,
                slot.start_time,
                slot.end_time,
                slot.business_id,
                room.business_id,
                room.name,
                assignment.change_kind,
            ]
        )
    header_fill = PatternFill("solid", fgColor="1F2937")
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    widths = [10, 14, 12, 12, 22, 10, 12, 12, 10, 10, 18, 12]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    metrics = workbook.create_sheet("指标")
    metrics.append(["指标", "值"])
    for key, value in schedule.metrics.items():
        metrics.append([key, value])
    metrics.column_dimensions["A"].width = 28
    metrics.column_dimensions["B"].width = 18
    for cell in metrics[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
