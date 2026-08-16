from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Campus,
    CourseSession,
    DataSnapshot,
    IntegrationSync,
    Room,
    ScheduleAssignment,
    ScheduleSet,
    ScheduleVersion,
    SolverRun,
    Teacher,
    TimeSlot,
)
from app.timezone import shanghai_now


def _analytics_fixture() -> tuple[str, str, str]:
    suffix = uuid4().hex[:8]
    schedule_set_id = f"analytics-{suffix}"
    with SessionLocal() as db:
        schedule_set = ScheduleSet(
            id=schedule_set_id,
            code=f"AN{suffix[:6].upper()}",
            name=f"统计课表-{suffix}",
            display_order=100,
        )
        db.add(schedule_set)
        campus = Campus(
            schedule_set_id=schedule_set_id,
            business_id="C1",
            name="统计校区",
        )
        db.add(campus)
        db.flush()
        second_campus = Campus(
            schedule_set_id=schedule_set_id,
            business_id="C2",
            name="统计第二校区",
        )
        db.add(second_campus)
        db.flush()
        for teacher_id, name in (("T1", "高负荷教师"), ("T2", "普通教师"), ("T3", "未排教师")):
            db.add(
                Teacher(
                    schedule_set_id=schedule_set_id,
                    campus_id=campus.id,
                    business_id=teacher_id,
                    name=name,
                    subject="数学",
                )
            )
        db.add(
            Teacher(
                schedule_set_id=schedule_set_id,
                campus_id=second_campus.id,
                business_id="T1",
                name="跨校区同码教师",
                subject="英语",
            )
        )
        for room_id in ("R1", "R2"):
            db.add(
                Room(
                    schedule_set_id=schedule_set_id,
                    campus_id=campus.id,
                    business_id=room_id,
                    name=f"教室 {room_id}",
                )
            )
        for sequence, slot_id in enumerate(("S1", "S2"), start=1):
            db.add(
                TimeSlot(
                    schedule_set_id=schedule_set_id,
                    campus_id=campus.id,
                    business_id=slot_id,
                    weekday="周一",
                    start_time="08:00" if slot_id == "S1" else "10:00",
                    end_time="09:30" if slot_id == "S1" else "11:30",
                    sequence=sequence,
                )
            )
        courses: list[CourseSession] = []
        for index, teacher_id in enumerate(("T1", "T1", "T2"), start=1):
            course = CourseSession(
                schedule_set_id=schedule_set_id,
                campus_id=campus.id,
                business_id=f"COURSE-{index}",
                class_business_id=f"CLASS-{index}",
                teacher_business_id=teacher_id,
                business_line="考研" if index < 3 else "素养",
                subject="数学",
                duration_minutes=90,
                lesson_date=date(2026, 8, 17),
            )
            db.add(course)
            courses.append(course)
        snapshot = DataSnapshot(
            schedule_set_id=schedule_set_id,
            revision=1,
            checksum=f"analytics-{suffix}",
            payload={
                "rules": [
                    {
                        "business_id": "SOFT-ROOM",
                        "source_text": "优先使用 R1",
                        "constraint_type": "preferred_room",
                        "hardness": "soft",
                        "weight": 5,
                        "scope": {"room_ids": ["R1"]},
                        "actor_ids": [],
                    },
                    {
                        "business_id": "SOFT-SLOT",
                        "source_text": "优先第一时段",
                        "constraint_type": "preferred_slot",
                        "hardness": "soft",
                        "weight": 3,
                        "scope": {"slot_ids": ["S1"]},
                        "actor_ids": [],
                    },
                    {
                        "business_id": "SOFT-CONSEC",
                        "source_text": "指定教师尽量连堂",
                        "constraint_type": "consecutive_sessions",
                        "hardness": "soft",
                        "weight": 4,
                        "scope": {},
                        "actor_ids": ["T3"],
                    },
                ]
            },
        )
        db.add(snapshot)
        db.flush()
        run = SolverRun(
            schedule_set_id=schedule_set_id,
            snapshot_id=snapshot.id,
            status="completed",
            model_status="OPTIMAL",
            objective_value=20,
            best_bound=20,
            request_payload={},
            result_payload={},
        )
        db.add(run)
        db.flush()
        version = ScheduleVersion(
            schedule_set_id=schedule_set_id,
            version_no=1,
            name="统计版本",
            status="published",
            solver_run_id=run.id,
            metrics={},
            published_at=shanghai_now(),
        )
        db.add(version)
        db.flush()
        placements = (("S1", "R1"), ("S1", "R2"), ("S2", "R1"))
        for course, (slot_id, room_id) in zip(courses, placements, strict=True):
            db.add(
                ScheduleAssignment(
                    schedule_version_id=version.id,
                    course_session_id=course.id,
                    lesson_date=date(2026, 8, 17),
                    slot_business_id=slot_id,
                    room_business_id=room_id,
                )
            )
        db.add(
            IntegrationSync(
                schedule_set_id=schedule_set_id,
                resource="schedule",
                direction="export",
                status="completed",
                records_read=2,
                records_written=3,
                detail={"duration_ms": 1200, "retry_count": 1},
                created_at=shanghai_now() - timedelta(hours=1),
            )
        )
        other_set = ScheduleSet(
            id=f"other-{suffix}",
            code=f"OT{suffix[:6].upper()}",
            name=f"其他课表-{suffix}",
            display_order=101,
        )
        db.add(other_set)
        db.flush()
        db.add(
            IntegrationSync(
                schedule_set_id=other_set.id,
                resource="schedule",
                direction="export",
                status="failed",
                records_read=999,
                records_written=999,
                detail={"duration_ms": 9999, "retry_count": 9},
                created_at=shanghai_now() - timedelta(hours=1),
            )
        )
        db.commit()
        return schedule_set_id, version.id, other_set.id


def test_overview_analytics_returns_scoped_dashboard_metrics(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    schedule_set_id, version_id, _other_set_id = _analytics_fixture()
    response = client.get(
        "/api/v1/overview/analytics",
        headers={**auth_headers, "X-Schedule-Set-Id": schedule_set_id},
        params={"date_from": "2026-08-17", "date_to": "2026-08-23"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schedule_set_id"] == schedule_set_id
    assert payload["generated_at"].endswith("+08:00")

    workload = payload["teacher_workload"]
    assert workload["schedule_version_id"] == version_id
    assert workload["total_teachers"] == 4
    assert workload["assigned_teachers"] == 2
    assert workload["total_sessions"] == 3
    assert workload["total_hours"] == 4.5
    assert workload["top_teachers"][0]["teacher_business_id"] == "T1"
    assert workload["top_teachers"][0]["total_sessions"] == 2
    assert workload["buckets"][0]["teacher_count"] == 4
    duplicate_business_id_rows = [
        item for item in workload["teachers"] if item["teacher_business_id"] == "T1"
    ]
    assert len(duplicate_business_id_rows) == 2
    assert {item["campus_name"] for item in duplicate_business_id_rows} == {
        "统计校区",
        "统计第二校区",
    }

    heatmap = {item["slot_business_id"]: item for item in payload["room_heatmap"]["cells"]}
    assert heatmap["S1"]["occupied_room_slots"] == 2
    assert heatmap["S1"]["available_room_slots"] == 2
    assert heatmap["S1"]["occupancy_rate"] == 1.0
    assert heatmap["S2"]["occupancy_rate"] == 0.5
    assert payload["room_heatmap"]["effective_date_from"] == "2026-08-17"
    assert payload["room_heatmap"]["effective_date_to"] == "2026-08-23"
    period_heatmap = {
        (item["weekday"], item["period"]): item
        for item in payload["room_heatmap"]["period_cells"]
    }
    assert len(period_heatmap) == 21
    assert period_heatmap[("周一", "上午")]["occupied_room_slots"] == 3
    assert period_heatmap[("周一", "上午")]["available_room_slots"] == 4
    assert period_heatmap[("周一", "上午")]["occupancy_rate"] == 0.75

    penalties = payload["optimization_penalties"]
    assert penalties["objective_value"] == 20
    assert penalties["total_soft_penalty"] == 8
    assert penalties["unattributed_objective_value"] == 12
    by_rule = {item["rule_id"]: item for item in penalties["soft_constraints"]}
    assert by_rule["SOFT-ROOM"]["violations"] == 1
    assert by_rule["SOFT-SLOT"]["violations"] == 1
    assert by_rule["SOFT-CONSEC"]["evaluated_count"] == 0
    assert by_rule["SOFT-CONSEC"]["satisfaction_rate"] is None

    sync_health = payload["sync_health"]
    assert sync_health["window_start"].endswith("+08:00")
    assert sync_health["total_syncs"] == 1
    assert sync_health["records_written"] == 3
    assert sync_health["retry_count"] == 1
    assert sync_health["average_duration_ms"] == 1200


def test_overview_analytics_rejects_cross_scope_version_and_reversed_dates(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    schedule_set_id, version_id, other_set_id = _analytics_fixture()
    cross_scope = client.get(
        "/api/v1/overview/analytics",
        headers={**auth_headers, "X-Schedule-Set-Id": other_set_id},
        params={"schedule_id": version_id},
    )
    assert cross_scope.status_code == 404

    reversed_dates = client.get(
        "/api/v1/overview/analytics",
        headers={**auth_headers, "X-Schedule-Set-Id": schedule_set_id},
        params={"date_from": "2026-08-20", "date_to": "2026-08-17"},
    )
    assert reversed_dates.status_code == 422


def test_room_heatmap_uses_full_assignment_span_when_dates_are_omitted(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    schedule_set_id, version_id, _other_set_id = _analytics_fixture()
    with SessionLocal() as db:
        version = db.get(ScheduleVersion, version_id)
        assert version is not None
        campus = db.scalar(
            select(Campus).where(
                Campus.schedule_set_id == schedule_set_id,
                Campus.business_id == "C1",
            )
        )
        assert campus is not None
        course = CourseSession(
            schedule_set_id=schedule_set_id,
            campus_id=campus.id,
            business_id=f"COURSE-LATE-{uuid4().hex[:8]}",
            class_business_id="CLASS-LATE",
            teacher_business_id="T1",
            subject="数学",
            duration_minutes=90,
            lesson_date=date(2026, 8, 31),
        )
        db.add(course)
        db.flush()
        db.add(
            ScheduleAssignment(
                schedule_version_id=version.id,
                course_session_id=course.id,
                lesson_date=date(2026, 8, 31),
                slot_business_id="S2",
                room_business_id="R2",
            )
        )
        db.commit()

    response = client.get(
        "/api/v1/overview/analytics",
        headers={**auth_headers, "X-Schedule-Set-Id": schedule_set_id},
    )
    assert response.status_code == 200, response.text
    room_heatmap = response.json()["room_heatmap"]
    assert room_heatmap["effective_date_from"] == "2026-08-17"
    assert room_heatmap["effective_date_to"] == "2026-08-31"
    cells = {item["slot_business_id"]: item for item in room_heatmap["cells"]}
    # Three Mondays exist in the full span; the completely empty middle Monday
    # remains in the capacity denominator.
    assert cells["S1"]["available_room_slots"] == 6
    assert cells["S1"]["occupied_room_slots"] == 2
    assert cells["S1"]["occupancy_rate"] == 0.3333


def test_penalty_breakdown_reconstructs_partial_solver_scope(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    schedule_set_id, version_id, _other_set_id = _analytics_fixture()
    with SessionLocal() as db:
        version = db.get(ScheduleVersion, version_id)
        assert version is not None
        run = db.get(SolverRun, version.solver_run_id)
        assert run is not None
        run.request_payload = {"business_lines": ["考研"]}
        db.commit()

    response = client.get(
        "/api/v1/overview/analytics",
        headers={**auth_headers, "X-Schedule-Set-Id": schedule_set_id},
    )
    assert response.status_code == 200, response.text
    penalties = response.json()["optimization_penalties"]
    assert penalties["scope_source"] == "reconstructed_request_scope"
    assert penalties["evaluated_assignment_count"] == 2
    assert penalties["total_soft_penalty"] == 5
    assert penalties["unattributed_objective_value"] == 15
    assert penalties["reconciliation_error"] == 0
