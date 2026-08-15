from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Campus,
    ClassGroup,
    CourseSession,
    Room,
    ScheduleSet,
    ScheduleSetMember,
    Teacher,
    TimeSlot,
    User,
)
from ..security import hash_password


def bootstrap_admin(db: Session, username: str, password: str) -> User:
    user = db.scalar(select(User).where(User.username == username))
    if user:
        return user
    user = User(username=username, password_hash=hash_password(password), role="admin")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def ensure_default_schedule_set(db: Session, user: User | None = None) -> ScheduleSet:
    schedule_set = db.get(ScheduleSet, "default")
    if schedule_set is None:
        schedule_set = ScheduleSet(
            id="default",
            code="SET001",
            name="第一套课表",
            display_order=0,
            is_active=True,
            created_by=user.id if user else None,
        )
        db.add(schedule_set)
        db.flush()
    if user is not None and user.role != "admin":
        member = db.scalar(
            select(ScheduleSetMember).where(
                ScheduleSetMember.schedule_set_id == schedule_set.id,
                ScheduleSetMember.user_id == user.id,
            )
        )
        if member is None:
            db.add(
                ScheduleSetMember(
                    schedule_set_id=schedule_set.id,
                    user_id=user.id,
                    access_role=("approver" if user.role == "approver" else user.role),
                    granted_by=user.id,
                )
            )
    db.commit()
    db.refresh(schedule_set)
    return schedule_set


def seed_demo_data(db: Session) -> None:
    """创建示范主数据，仅用于自动化测试，不参与正式运行。"""
    campus = Campus(business_id="CAMPUS-DEMO", name="示范校区")
    db.add(campus)
    db.flush()

    teacher_subjects = {
        "T01": ("教师甲", "数学"),
        "T02": ("教师乙", "英语"),
        "T03": ("教师丙", "物理"),
        "T04": ("教师丁", "化学"),
        "T05": ("教师戊", "语文"),
        "T06": ("教师己", "编程"),
    }
    for business_id, (name, subject) in teacher_subjects.items():
        db.add(
            Teacher(
                campus_id=campus.id,
                business_id=business_id,
                name=name,
                subject=subject,
            )
        )

    class_rows = [
        ("B01", "初一数学A", "初一", "数学", "T01"),
        ("B02", "初一英语A", "初一", "英语", "T02"),
        ("B03", "初二物理A", "初二", "物理", "T03"),
        ("B04", "初三化学A", "初三", "化学", "T04"),
        ("B05", "初三语文A", "初三", "语文", "T05"),
        ("B06", "高中编程A", "高一", "编程", "T06"),
        ("B07", "初一数学B", "初一", "数学", "T01"),
        ("B08", "初一英语B", "初一", "英语", "T02"),
        ("B09", "初二物理B", "初二", "物理", "T03"),
        ("B10", "初三化学B", "初三", "化学", "T04"),
        ("B11", "初三语文B", "初三", "语文", "T05"),
        ("B12", "高中编程B", "高一", "编程", "T06"),
    ]
    for business_id, name, _grade, _subject, _teacher_business_id in class_rows:
        # 班级只存身份，班型/业务线/教师由课次聚合。
        db.add(ClassGroup(campus_id=campus.id, business_id=business_id, name=name))

    room_names = [
        ("R01", "小班教室1"),
        ("R02", "小班教室2"),
        ("R03", "机房"),
        ("R04", "大班教室1"),
        ("R05", "大班教室2"),
        ("R06", "大班综合教室"),
    ]
    for business_id, name in room_names:
        db.add(Room(campus_id=campus.id, business_id=business_id, name=name, is_active=True))

    slot_rows = [
        ("S01", "周一", "18:30", "20:00", "晚间1"),
        ("S02", "周一", "20:10", "21:40", "晚间2"),
        ("S03", "周二", "18:30", "20:00", "晚间1"),
        ("S04", "周二", "20:10", "21:40", "晚间2"),
        ("S05", "周三", "18:30", "20:00", "晚间1"),
        ("S06", "周三", "20:10", "21:40", "晚间2"),
        ("S07", "周四", "18:30", "20:00", "晚间1"),
        ("S08", "周四", "20:10", "21:40", "晚间2"),
        ("S09", "周五", "18:30", "20:00", "晚间1"),
        ("S10", "周五", "20:10", "21:40", "晚间2"),
    ]
    for sequence, (business_id, weekday, start_time, end_time, kind) in enumerate(
        slot_rows, start=1
    ):
        db.add(
            TimeSlot(
                campus_id=campus.id,
                business_id=business_id,
                weekday=weekday,
                start_time=start_time,
                end_time=end_time,
                kind=kind,
                sequence=sequence,
            )
        )

    for business_id, _name, _grade, subject, teacher_business_id in class_rows:
        for session_index in (1, 2):
            db.add(
                CourseSession(
                    campus_id=campus.id,
                    business_id=f"{business_id}-{session_index}",
                    class_business_id=business_id,
                    teacher_business_id=teacher_business_id,
                    subject=subject,
                    lesson_name=f"{subject}·示范课节{session_index}",
                    schedule_source="示范课表",
                    stage="基础阶段",
                    planned_sessions=2,
                    planned_hours=6,
                    session_no=session_index,
                    duration_minutes=90,
                )
            )
    db.commit()
