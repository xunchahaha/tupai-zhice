"""调课事件的范围必须由请求决定：选中哪一节课，就只调这一节课（审查 #1）。

场景：同一位老师、同一个班连续三周周一上午都有课。教务只选其中一周的一节课去调整，
请求带上课次号和那一天的日期；后端不能因为「教师相同」把另外两周也当成受影响课次。
"""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, select

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
from app.services.solver import _event_blocks

WEEKS = [date(2026, 9, 7), date(2026, 9, 14), date(2026, 9, 21)]  # 三个周一


@pytest.fixture
def weekly_schedule(client, auth_headers, monkeypatch):
    monkeypatch.setattr(api, "enqueue_solver_run", lambda run_id: None)
    created = client.post(
        "/api/v1/schedule-sets", headers=auth_headers, json={"name": f"调课范围-{uuid4().hex[:8]}"}
    )
    assert created.status_code == 201, created.text
    scope_id = created.json()["id"]
    headers = {**auth_headers, "X-Schedule-Set-Id": scope_id}
    with SessionLocal() as db:
        campus = Campus(schedule_set_id=scope_id, business_id="C", name="校区")
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
            name="三周课表",
            solver_run_id=run.id,
            status="draft",
        )
        db.add(version)
        db.flush()
        for index, lesson_date in enumerate(WEEKS):
            course = CourseSession(
                schedule_set_id=scope_id,
                campus_id=campus.id,
                business_id=f"L{index}",
                class_business_id="B",
                teacher_business_id="T",
                lesson_date=lesson_date,
                fixed_start_time="08:30",
                fixed_end_time="11:30",
            )
            db.add(course)
            db.flush()
            db.add(
                ScheduleAssignment(
                    schedule_version_id=version.id,
                    course_session_id=course.id,
                    lesson_date=lesson_date,
                    slot_business_id="S",
                    room_business_id="R",
                )
            )
        db.commit()
        return headers, version.id


def _lesson_request(version_id: str, **overrides):
    """课表页「调整这节课」应当发出的请求：课次号 + 那一天 + 只动这一节。"""
    payload = {
        "parent_schedule_id": version_id,
        "event_type": "teacher_leave",
        "description": "只调整 9 月 14 日这节课",
        "teacher_business_id": "T",
        "slot_business_ids": ["S"],
        "course_business_id": "L1",
        "date_from": "2026-09-14",
        "date_to": "2026-09-14",
        "include_neighbors": False,
    }
    payload.update(overrides)
    return payload


def _solve_scope(event_id: str) -> list[str]:
    with SessionLocal() as db:
        event = db.get(RescheduleEvent, event_id)
        run = db.get(SolverRun, event.solver_run_id)
        return list(run.request_payload["course_business_ids"])


def test_selected_lesson_only_moves_that_lesson_not_the_teachers_other_weeks(
    client, weekly_schedule
):
    headers, version_id = weekly_schedule
    response = client.post(
        "/api/v1/reschedule-events", headers=headers, json=_lesson_request(version_id)
    )
    assert response.status_code == 202, response.text
    assert _solve_scope(response.json()["id"]) == ["L1"]
    with SessionLocal() as db:
        event = db.get(RescheduleEvent, response.json()["id"])
        run = db.get(SolverRun, event.solver_run_id)
        # 求解器收到的事件本身就限定在这一节课、这一天：其余周不会被封锁。
        assert run.request_payload["event"]["course_business_id"] == "L1"
        assert run.request_payload["event"]["date_from"] == run.request_payload["event"]["date_to"]


def test_contrast_a_registered_event_still_covers_every_matching_lesson(client, weekly_schedule):
    """阳性对照：不带课次号的「教师请假」事件仍是整批（既有语义不变），这才是范围会被放大的来源。"""
    headers, version_id = weekly_schedule
    response = client.post(
        "/api/v1/reschedule-events",
        headers=headers,
        json={
            "parent_schedule_id": version_id,
            "event_type": "teacher_leave",
            "description": "教师请假",
            "teacher_business_id": "T",
            "slot_business_ids": ["S"],
        },
    )
    assert response.status_code == 202, response.text
    assert _solve_scope(response.json()["id"]) == ["L0", "L1", "L2"]


def test_date_window_limits_which_lessons_an_event_touches(client, weekly_schedule):
    headers, version_id = weekly_schedule
    response = client.post(
        "/api/v1/reschedule-events",
        headers=headers,
        json={
            "parent_schedule_id": version_id,
            "event_type": "teacher_leave",
            "description": "9 月 14 日当天请假",
            "teacher_business_id": "T",
            "date_from": "2026-09-14",
            "date_to": "2026-09-14",
            "include_neighbors": False,
        },
    )
    assert response.status_code == 202, response.text
    assert _solve_scope(response.json()["id"]) == ["L1"]


def test_neighbors_join_only_when_explicitly_allowed(client, weekly_schedule):
    headers, version_id = weekly_schedule
    # 明确允许连带调整：同班前后 7 天内的课次（L0、L2）成为可挪动范围。
    allowed = client.post(
        "/api/v1/reschedule-events",
        headers=headers,
        json=_lesson_request(version_id, include_neighbors=True, neighborhood_days=7),
    )
    assert allowed.status_code == 202, allowed.text
    assert _solve_scope(allowed.json()["id"]) == ["L0", "L1", "L2"]
    # 邻域天数收窄到 0：前后一周的课不再进入。
    narrowed = client.post(
        "/api/v1/reschedule-events",
        headers=headers,
        json=_lesson_request(version_id, include_neighbors=True, neighborhood_days=0),
    )
    assert narrowed.status_code == 202, narrowed.text
    assert _solve_scope(narrowed.json()["id"]) == ["L1"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"course_business_id": "NOPE"},  # 课次不在父课表里
        {"date_from": "2026-09-21", "date_to": "2026-09-21"},  # 课次在，但不在事件日期范围内
    ],
)
def test_lesson_outside_the_request_is_rejected_instead_of_widening_to_everything(
    client, weekly_schedule, overrides
):
    """种子为空时邻域是空集，求解器会把它当成「不限范围、全量重排」——必须在建事件前拒绝。"""
    headers, version_id = weekly_schedule
    with SessionLocal() as db:
        events_before = db.scalar(select(func.count()).select_from(RescheduleEvent))
    response = client.post(
        "/api/v1/reschedule-events", headers=headers, json=_lesson_request(version_id, **overrides)
    )
    assert response.status_code == 422, response.text
    assert "所选课次" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RescheduleEvent)) == events_before


def test_solver_event_block_respects_the_selected_lesson():
    """无日期的求解模型也不能把封锁扩散到同一教师的其他课次。"""
    event = {
        "event_type": "teacher_leave",
        "teacher_business_id": "T",
        "slot_business_ids": ["S"],
        "course_business_id": "L1",
    }
    selected = {"business_id": "L1", "teacher_business_id": "T"}
    other = {"business_id": "L2", "teacher_business_id": "T"}
    assert _event_blocks(event, selected, "R", "S") is True
    assert _event_blocks(event, other, "R", "S") is False
    # 没有指定课次时保持原语义：该教师的所有课次都被封锁。
    assert _event_blocks({**event, "course_business_id": None}, other, "R", "S") is True


# ---- 事件生命周期（审查 #4）：没有候选课表时事件必须走到终态，不能永远 pending ----


def _create_lesson_event(client, headers, version_id) -> str:
    response = client.post(
        "/api/v1/reschedule-events", headers=headers, json=_lesson_request(version_id)
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["candidate_schedule_id"] is None
    return body["id"]


def _event_state(event_id: str) -> tuple[str, str | None]:
    with SessionLocal() as db:
        event = db.get(RescheduleEvent, event_id)
        return event.status, event.candidate_schedule_id


def test_event_without_a_feasible_result_ends_as_no_candidate(client, weekly_schedule):
    from app.services import tasks
    from app.services.solver import _empty_result

    headers, version_id = weekly_schedule
    event_id = _create_lesson_event(client, headers, version_id)
    with SessionLocal() as db:
        run_id = db.get(RescheduleEvent, event_id).solver_run_id
    tasks._persist_result(run_id, _empty_result("INFEASIBLE"))
    assert _event_state(event_id) == ("no_candidate", None)


def test_event_whose_solve_failed_ends_as_failed(client, weekly_schedule):
    from app.services import tasks

    headers, version_id = weekly_schedule
    event_id = _create_lesson_event(client, headers, version_id)
    with SessionLocal() as db:
        run_id = db.get(RescheduleEvent, event_id).solver_run_id
    tasks._persist_failure(run_id, "求解器异常")
    assert _event_state(event_id) == ("failed", None)


def test_event_keeps_its_candidate_when_the_solve_succeeds(client, weekly_schedule):
    from app.services import tasks

    headers, version_id = weekly_schedule
    event_id = _create_lesson_event(client, headers, version_id)
    with SessionLocal() as db:
        run_id = db.get(RescheduleEvent, event_id).solver_run_id
    result = {
        "model_status": "OPTIMAL",
        "objective_value": 0,
        "best_bound": 0,
        "wall_time_seconds": 0.1,
        "conflict_rule_ids": [],
        "priority_rule_ids": [],
        "priority_explanations": [],
        "assignments": [],
        "warnings": [],
    }
    tasks._persist_result(run_id, result)
    status, candidate = _event_state(event_id)
    assert status == "candidate_ready"
    assert candidate is not None


# ---- 交给助手继续处理（审查 #5）：求解基准与目标课次来自所选版本，而不是当前已发布版本 ----


def _add_published_version(scope_id: str) -> str:
    """再造一个更新的「当前已发布版本」：没有显式基准时，助手求解会悄悄以它为准。"""
    with SessionLocal() as db:
        snapshot_id = db.scalar(
            select(DataSnapshot.id).where(DataSnapshot.schedule_set_id == scope_id)
        )
        run = SolverRun(
            schedule_set_id=scope_id,
            snapshot_id=snapshot_id,
            status="completed",
            request_payload={},
        )
        db.add(run)
        db.flush()
        latest = ScheduleVersion(
            schedule_set_id=scope_id,
            version_no=2,
            name="当前已发布",
            status="published",
            solver_run_id=run.id,
        )
        db.add(latest)
        db.commit()
        return latest.id


def _assistant_solve(client, headers, **overrides):
    body = {
        "instruction": "把周一上午这节课挪到别处",
        "class_business_ids": ["B"],
        "time_limit_seconds": 5,
        "wait": False,
    }
    body.update(overrides)
    return client.post("/api/v1/assistant/solve", headers=headers, json=body)


def test_assistant_solve_uses_the_selected_version_and_lesson(client, weekly_schedule):
    headers, draft_id = weekly_schedule
    with SessionLocal() as db:
        scope_id = db.get(ScheduleVersion, draft_id).schedule_set_id
    published_id = _add_published_version(scope_id)

    # 阳性对照：没有显式基准 → 悄悄以最新已发布版本为准（这正是要避免的丢失基准）。
    default = _assistant_solve(client, headers)
    assert default.status_code == 202, default.text
    with SessionLocal() as db:
        run = db.get(SolverRun, default.json()["id"])
        assert run.request_payload["parent_schedule_id"] == published_id
        assert run.request_payload["baseline_source"] == "latest_published"

    handed_off = _assistant_solve(
        client, headers, parent_schedule_id=draft_id, course_business_ids=["L1"]
    )
    assert handed_off.status_code == 202, handed_off.text
    with SessionLocal() as db:
        run = db.get(SolverRun, handed_off.json()["id"])
        assert run.request_payload["parent_schedule_id"] == draft_id
        assert run.request_payload["baseline_source"] == "explicit_parent"
        # 基准课表里的三节课都作为已有排布带入，求解范围收敛到所选的这一节。
        assert len(run.request_payload["previous_assignments"]) == 3
        assert run.request_payload["course_business_ids"] == ["L1"]


def test_assistant_solve_rejects_a_base_version_from_elsewhere(client, weekly_schedule):
    headers, _ = weekly_schedule
    response = _assistant_solve(client, headers, parent_schedule_id="no-such-version")
    assert response.status_code == 422, response.text
    assert "基准课表版本" in response.json()["detail"]
