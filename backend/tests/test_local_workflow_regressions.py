from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select

import app.api as api
from app.db import SessionLocal
from app.models import (
    Campus,
    CourseSession,
    DataSnapshot,
    RescheduleEvent,
    Room,
    ScheduleAssignment,
    ScheduleVersion,
    SolverRun,
    Teacher,
    TimeSlot,
)


@pytest.fixture
def scoped_schedule(client, auth_headers, monkeypatch):
    monkeypatch.setattr(api, "enqueue_solver_run", lambda run_id: None)
    monkeypatch.setattr(api, "trigger_published_data_sync", lambda *args, **kwargs: None)
    created = client.post(
        "/api/v1/schedule-sets", headers=auth_headers, json={"name": f"本地回归-{uuid4().hex[:8]}"}
    )
    assert created.status_code == 201, created.text
    scope_id = created.json()["id"]
    headers = {**auth_headers, "X-Schedule-Set-Id": scope_id}
    with SessionLocal() as db:
        campus = Campus(schedule_set_id=scope_id, business_id="C", name="回归校区")
        snapshot = DataSnapshot(
            schedule_set_id=scope_id, revision=1, checksum=uuid4().hex, payload={}
        )
        db.add_all([campus, snapshot])
        db.flush()
        db.add_all(
            [
                Room(schedule_set_id=scope_id, campus_id=campus.id, business_id="R", name="教室"),
                Teacher(
                    schedule_set_id=scope_id, campus_id=campus.id, business_id="T", name="教师"
                ),
                TimeSlot(
                    schedule_set_id=scope_id,
                    campus_id=campus.id,
                    business_id="S",
                    weekday="周一",
                    start_time="08:30",
                    end_time="11:30",
                ),
            ]
        )
        run = SolverRun(
            schedule_set_id=scope_id,
            snapshot_id=snapshot.id,
            status="completed",
            request_payload={},
        )
        db.add(run)
        db.flush()
        version = ScheduleVersion(
            schedule_set_id=scope_id,
            version_no=1,
            name="测试草稿",
            solver_run_id=run.id,
            status="draft",
        )
        db.add(version)
        db.flush()
        for index in range(2):
            course = CourseSession(
                schedule_set_id=scope_id,
                campus_id=campus.id,
                business_id=f"L{index}",
                class_business_id=f"B{index}",
                teacher_business_id="T",
                lesson_date=date(2026, 9, 7),
                fixed_start_time="08:30",
                fixed_end_time="11:30",
            )
            db.add(course)
            db.flush()
            db.add(
                ScheduleAssignment(
                    schedule_version_id=version.id,
                    course_session_id=course.id,
                    lesson_date=date(2026, 9, 7),
                    slot_business_id="S",
                    room_business_id="R",
                )
            )
        db.commit()
        return headers, version.id, scope_id


@pytest.mark.parametrize("dated_event", [True, False])
def test_dated_reschedule_payloads_are_json_serializable(client, scoped_schedule, dated_event):
    headers, version_id, _ = scoped_schedule
    payload = {
        "parent_schedule_id": version_id,
        "event_type": "teacher_leave",
        "teacher_business_id": "T",
        "description": "教师请假",
    }
    if dated_event:
        payload.update(date_from="2026-09-07", date_to="2026-09-08")
    else:
        payload["slot_business_ids"] = ["S"]
    response = client.post("/api/v1/reschedule-events", headers=headers, json=payload)
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        event = db.get(RescheduleEvent, response.json()["id"])
        run = db.get(SolverRun, event.solver_run_id)
        assert run.status == "queued"
        assert run.request_payload["previous_assignments"][0]["lesson_date"] == "2026-09-07"
        if dated_event:
            assert event.payload["date_from"] == "2026-09-07"
            assert run.request_payload["event"]["date_to"] == "2026-09-08"


def test_publish_detects_conflicts_in_nondefault_scope(client, scoped_schedule):
    headers, version_id, _ = scoped_schedule
    response = client.post(f"/api/v1/schedules/{version_id}/publish", headers=headers)
    assert response.status_code == 409, response.text
    assert "教室" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.get(ScheduleVersion, version_id).status == "draft"


@pytest.mark.parametrize("field", ["room_business_id", "slot_business_id", "course_session_id"])
def test_publish_reports_missing_references(client, scoped_schedule, field):
    headers, version_id, _ = scoped_schedule
    with SessionLocal() as db:
        assignment = db.scalar(
            select(ScheduleAssignment).where(ScheduleAssignment.schedule_version_id == version_id)
        )
        if field == "course_session_id":
            # Existing course from another scope: FK-valid but outside this schedule set.
            other = db.scalar(
                select(CourseSession).where(CourseSession.schedule_set_id == "default")
            )
            setattr(assignment, field, other.id)
        else:
            setattr(assignment, field, "missing")
        db.commit()
    response = client.post(f"/api/v1/schedules/{version_id}/publish", headers=headers)
    assert response.status_code == 409, response.text
    assert "完整性" in response.json()["detail"]


def test_full_scope_solve_uses_current_published_parent(client, scoped_schedule):
    headers, version_id, _ = scoped_schedule
    with SessionLocal() as db:
        db.get(ScheduleVersion, version_id).status = "published"
        db.commit()
    response = client.post(
        "/api/v1/solver-runs", headers=headers, json={"wait": False, "time_limit_seconds": 1}
    )
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        run = db.get(SolverRun, response.json()["id"])
        assert run.request_payload["parent_schedule_id"] == version_id
        assert len(run.request_payload["previous_assignments"]) == 2


@pytest.mark.parametrize(
    "constraint_type,scope,hardness,weight",
    [
        ("declared_constraint", {}, "hard", None),
        ("consecutive_sessions", {"minimum_consecutive": 2}, "soft", 10),
        ("consecutive_sessions", {"minimum_consecutive": 3}, "soft", 10),
    ],
)
def test_rule_activation_requires_implementation_for_current_date_model(
    client, scoped_schedule, constraint_type, scope, hardness, weight
):
    headers, _, _ = scoped_schedule
    created = client.post(
        "/api/v1/rules",
        headers=headers,
        json={
            "source_text": "待验证规则",
            "actor_type": "system",
            "actor_ids": [],
            "constraint_type": constraint_type,
            "scope": scope,
            "hardness": hardness,
            "weight": weight,
        },
    )
    assert created.status_code == 201, created.text
    response = client.post(
        f"/api/v1/rules/{created.json()['id']}/transition",
        headers=headers,
        json={"status": "active"},
    )
    assert response.status_code == 422, response.text
    assert "尚未接入" in response.text


def test_existing_unsupported_active_rule_stops_solve(client, scoped_schedule):
    from app.models import Rule

    headers, _, scope_id = scoped_schedule
    with SessionLocal() as db:
        db.add(
            Rule(
                schedule_set_id=scope_id,
                business_id="legacy-consecutive",
                source_text="三节",
                actor_type="system",
                actor_ids=[],
                constraint_type="consecutive_sessions",
                scope={"minimum_consecutive": 3},
                hardness="soft",
                weight=10,
                status="active",
            )
        )
        db.commit()
    response = client.post("/api/v1/solver-runs", headers=headers, json={"wait": False})
    assert response.status_code == 422, response.text
    assert "legacy-consecutive" in response.text


def test_interpretation_exposes_unmodeled_requirements(client, scoped_schedule, monkeypatch):
    headers, _, _ = scoped_schedule
    monkeypatch.setattr(api.AIService, "configuration_view", lambda self: {"configured": True})
    monkeypatch.setattr(
        api.AIService,
        "interpret_instruction",
        lambda *args, **kwargs: (
            {
                "business_lines": [],
                "product_types": [],
                "class_business_ids": [],
                "date_from": None,
                "date_to": None,
                "date_window_days": 7,
                "recognized_rules": ["虚构标签"],
                "unsupported_requirements": ["额外复杂要求"],
            },
            None,
        ),
    )
    response = client.post(
        "/api/v1/assistant/interpret",
        headers=headers,
        json={
            "instruction": "张老师下周三不排晚课，A 班只能用 302 教室，而且连续排三节",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["recognized_rules"] == []
    assert set(data["unsupported_requirements"]) >= {
        "具体教师的禁排或请假要求",
        "指定教室要求",
        "精确连续课次要求",
        "额外复杂要求",
        "虚构标签",
    }
    assert data["coverage_warnings"]


def test_assistant_scope_uses_parent_lesson_date(client, scoped_schedule):
    headers, version_id, _ = scoped_schedule
    with SessionLocal() as db:
        db.get(ScheduleVersion, version_id).status = "published"
        for row in db.scalars(
            select(ScheduleAssignment).where(ScheduleAssignment.schedule_version_id == version_id)
        ):
            row.lesson_date = date(2026, 9, 9)
        db.commit()
    response = client.post(
        "/api/v1/assistant/solve",
        headers=headers,
        json={
            "instruction": "重排九月九日课程",
            "date_from": "2026-09-09",
            "date_to": "2026-09-09",
            "wait": False,
        },
    )
    assert response.status_code == 202, response.text


def test_fixed_clock_instruction_is_not_misread_as_a_room(client, scoped_schedule, monkeypatch):
    headers, _, _ = scoped_schedule
    monkeypatch.setattr(api.AIService, "configuration_view", lambda self: {"configured": True})
    monkeypatch.setattr(
        api.AIService,
        "interpret_instruction",
        lambda *args, **kwargs: (
            {
                "business_lines": [],
                "product_types": [],
                "class_business_ids": [],
                "date_from": None,
                "date_to": None,
                "date_window_days": 7,
                "recognized_rules": [],
                "unsupported_requirements": [],
            },
            None,
        ),
    )
    response = client.post(
        "/api/v1/assistant/interpret",
        headers=headers,
        json={
            "instruction": "保持固定 08:30-11:30 时段，仅调整日期",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["unsupported_requirements"] == []
