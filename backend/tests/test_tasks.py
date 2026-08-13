from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    Campus,
    CourseSession,
    DataSnapshot,
    Room,
    ScheduleAssignment,
    ScheduleVersion,
    SolverRun,
    TimeSlot,
)
from app.services import tasks


def test_persist_result_writes_final_lesson_date(monkeypatch, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'tasks.db').as_posix()}")
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with testing_session() as db:
        campus = Campus(business_id="CAMPUS", name="测试校区")
        db.add(campus)
        db.flush()
        db.add(Room(campus_id=campus.id, business_id="R1", name="教室1", is_active=True))
        db.add(
            TimeSlot(
                campus_id=campus.id,
                business_id="S-周二-0830",
                weekday="周二",
                start_time="08:30",
                end_time="11:30",
                kind="08:30-11:30",
                sequence=1,
            )
        )
        course = CourseSession(
            campus_id=campus.id,
            business_id="C1",
            source_row_id="SRC-C1",
            business_line="考研",
            product_type="考研·公共课标准编排",
            class_business_id="B1",
            teacher_business_id="郑州考研英语教研组",
            lesson_date=date(2026, 9, 7),
            fixed_start_time="08:30",
            fixed_end_time="11:30",
            original_room_business_id="R1",
        )
        snapshot = DataSnapshot(revision=1, checksum="checksum", payload={})
        db.add_all([course, snapshot])
        db.flush()
        run = SolverRun(snapshot_id=snapshot.id, status="running", request_payload={})
        db.add(run)
        db.commit()
        run_id = run.id
        course_id = course.id

    monkeypatch.setattr(tasks, "SessionLocal", testing_session)
    tasks._persist_result(
        run_id,
        {
            "model_status": "OPTIMAL",
            "objective_value": 0,
            "best_bound": 0,
            "wall_time_seconds": 0.01,
            "conflict_rule_ids": [],
            "priority_rule_ids": [],
            "priority_explanations": [],
            "assignments": [
                {
                    "course_session_id": course_id,
                    "course_business_id": "C1",
                    "class_business_id": "B1",
                    "teacher_business_id": "郑州考研英语教研组",
                    "lesson_date": "2026-09-08",
                    "room_business_id": "R1",
                    "slot_business_id": "S-周二-0830",
                }
            ],
        },
    )

    with testing_session() as db:
        schedule = db.query(ScheduleVersion).one()
        assignment = db.query(ScheduleAssignment).one()
        assert schedule.metrics["assignment_count"] == 1
        assert assignment.lesson_date == date(2026, 9, 8)
        assert assignment.room_business_id == "R1"


def test_persist_result_merges_unselected_parent_assignments(monkeypatch, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'partial-tasks.db').as_posix()}")
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with testing_session() as db:
        campus = Campus(business_id="CAMPUS", name="测试校区")
        db.add(campus)
        db.flush()
        db.add(Room(campus_id=campus.id, business_id="R1", name="教室1", is_active=True))
        courses = [
            CourseSession(
                campus_id=campus.id,
                business_id=business_id,
                class_business_id=f"B{index}",
                teacher_business_id="教研组",
                lesson_date=date(2026, 9, 7),
                fixed_start_time="08:30",
                fixed_end_time="11:30",
                original_room_business_id="R1",
            )
            for index, business_id in enumerate(("C1", "C2"), start=1)
        ]
        snapshot = DataSnapshot(revision=1, checksum="checksum-partial", payload={})
        db.add_all([*courses, snapshot])
        db.flush()
        parent_run = SolverRun(snapshot_id=snapshot.id, status="completed", request_payload={})
        db.add(parent_run)
        db.flush()
        parent = ScheduleVersion(
            version_no=1,
            name="父课表",
            status="published",
            solver_run_id=parent_run.id,
            metrics={},
        )
        db.add(parent)
        db.flush()
        db.add_all(
            [
                ScheduleAssignment(
                    schedule_version_id=parent.id,
                    course_session_id=course.id,
                    lesson_date=date(2026, 9, 7),
                    slot_business_id="S-周一-0830",
                    room_business_id="R1",
                )
                for course in courses
            ]
        )
        run = SolverRun(
            snapshot_id=snapshot.id,
            status="running",
            request_payload={
                "parent_schedule_id": parent.id,
                "previous_assignments": [
                    {
                        "course_business_id": course.business_id,
                        "lesson_date": "2026-09-07",
                        "room_business_id": "R1",
                        "slot_business_id": "S-周一-0830",
                    }
                    for course in courses[:1]
                ],
            },
        )
        db.add(run)
        db.commit()
        run_id = run.id
        solved_course_id = courses[0].id

    monkeypatch.setattr(tasks, "SessionLocal", testing_session)
    tasks._persist_result(
        run_id,
        {
            "model_status": "OPTIMAL",
            "objective_value": 0,
            "best_bound": 0,
            "wall_time_seconds": 0.01,
            "conflict_rule_ids": [],
            "priority_rule_ids": [],
            "priority_explanations": [],
            "assignments": [
                {
                    "course_session_id": solved_course_id,
                    "course_business_id": "C1",
                    "lesson_date": "2026-09-08",
                    "room_business_id": "R1",
                    "slot_business_id": "S-周二-0830",
                }
            ],
        },
    )

    with testing_session() as db:
        candidate = db.query(ScheduleVersion).filter(ScheduleVersion.version_no == 2).one()
        assignments = (
            db.query(ScheduleAssignment)
            .filter(ScheduleAssignment.schedule_version_id == candidate.id)
            .all()
        )
        assert len(assignments) == 2
        unchanged = next(item for item in assignments if item.course_session_id != solved_course_id)
        assert unchanged.lesson_date == date(2026, 9, 7)
        assert unchanged.change_kind == "unchanged"
        assert candidate.metrics["assignment_count"] == 2
