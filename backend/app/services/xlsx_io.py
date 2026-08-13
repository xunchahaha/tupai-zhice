from __future__ import annotations

from io import BytesIO
from typing import cast

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CourseSession, Room, ScheduleAssignment, ScheduleVersion, Teacher


def export_schedule_xlsx(db: Session, schedule: ScheduleVersion) -> bytes:
    assignments = list(
        db.scalars(
            select(ScheduleAssignment)
            .where(ScheduleAssignment.schedule_version_id == schedule.id)
            .order_by(
                ScheduleAssignment.lesson_date,
                ScheduleAssignment.room_business_id,
                ScheduleAssignment.course_session_id,
            )
        )
    )
    courses = {item.id: item for item in db.scalars(select(CourseSession))}
    rooms = {item.business_id: item for item in db.scalars(select(Room))}
    teachers = {item.business_id: item for item in db.scalars(select(Teacher))}
    workbook = Workbook()
    sheet = cast(Worksheet, workbook.active)
    sheet.title = "课表"
    headers = [
        "版本",
        "场次ID",
        "业务线",
        "产品班型",
        "班级ID",
        "教师ID",
        "具体日程账号",
        "课节名称",
        "上课日期",
        "固定开始时间",
        "固定结束时间",
        "时段ID",
        "原始教室ID",
        "原始教室名称",
        "最终教室ID",
        "最终教室名称",
        "变更状态",
    ]
    sheet.append(headers)
    for assignment in assignments:
        course = courses[assignment.course_session_id]
        original_room = rooms.get(course.original_room_business_id or "")
        final_room = rooms.get(assignment.room_business_id)
        teacher = teachers.get(course.teacher_business_id)
        calendar_user_id = course.calendar_user_id or (
            teacher.calendar_user_id if teacher else None
        )
        sheet.append(
            [
                schedule.version_no,
                course.business_id,
                course.business_line,
                course.product_type,
                course.class_business_id,
                course.teacher_business_id,
                calendar_user_id or "",
                course.lesson_name,
                assignment.lesson_date.isoformat() if assignment.lesson_date else "",
                course.fixed_start_time,
                course.fixed_end_time,
                assignment.slot_business_id,
                course.original_room_business_id or "",
                original_room.name if original_room else "",
                assignment.room_business_id,
                final_room.name if final_room else "",
                assignment.change_kind,
            ]
        )
    header_fill = PatternFill("solid", fgColor="1F2937")
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    widths = [10, 16, 12, 24, 16, 16, 22, 24, 12, 14, 14, 16, 16, 20, 16, 20, 12]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
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
