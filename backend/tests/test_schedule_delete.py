"""删除课表版本。

删除是发布/回滚的破坏性孪生操作，权限同为 Approver。SQLite 默认不校验外键，直接
db.delete 在本地全绿、换成 Postgres 立刻炸，所以清理必须写在应用层——这些用例断言的
是「关联行确实被处理了」，不是「删除返回了 204」。
"""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    AuditLog,
    CalendarEventBinding,
    CourseSession,
    DataSnapshot,
    RescheduleEvent,
    ScheduleAssignment,
    ScheduleVersion,
    SolverRun,
)


def _make_version(
    name: str,
    *,
    status: str = "draft",
    parent_id: str | None = None,
    assignments: int = 0,
) -> tuple[str, str, str]:
    """直接建版本，不跑求解器——这些用例关心的是删除的拦截与清理，不是排课质量。"""
    with SessionLocal() as db:
        revision = int(db.scalar(select(func.max(DataSnapshot.revision))) or 0) + 1
        snapshot = DataSnapshot(revision=revision, checksum=f"delete-{revision}", payload={})
        db.add(snapshot)
        db.flush()
        run = SolverRun(snapshot_id=snapshot.id, run_type="delete-test", status="succeeded")
        db.add(run)
        db.flush()
        version_no = int(db.scalar(select(func.max(ScheduleVersion.version_no))) or 0) + 1
        version = ScheduleVersion(
            version_no=version_no,
            name=name,
            status=status,
            parent_id=parent_id,
            solver_run_id=run.id,
        )
        db.add(version)
        db.flush()
        if assignments:
            course_ids = list(db.scalars(select(CourseSession.id).limit(assignments)))
            assert len(course_ids) == assignments
            for index, course_id in enumerate(course_ids):
                db.add(
                    ScheduleAssignment(
                        schedule_version_id=version.id,
                        course_session_id=course_id,
                        lesson_date=date(2026, 12, 1),
                        slot_business_id=f"S0{index % 9 + 1}",
                        room_business_id="R01",
                    )
                )
        db.commit()
        return version.id, run.id, snapshot.id


def _drop(version_id: str | None, run_id: str, snapshot_id: str) -> None:
    with SessionLocal() as db:
        if version_id is not None:
            version = db.get(ScheduleVersion, version_id)
            if version is not None:
                db.execute(
                    ScheduleAssignment.__table__.delete().where(
                        ScheduleAssignment.schedule_version_id == version_id
                    )
                )
                db.delete(version)
        run = db.get(SolverRun, run_id)
        if run is not None:
            db.delete(run)
        snapshot = db.get(DataSnapshot, snapshot_id)
        if snapshot is not None:
            db.delete(snapshot)
        db.commit()


def test_delete_draft_version_clears_assignments_and_keeps_the_solver_run(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    version_id, run_id, snapshot_id = _make_version("待删除草稿版本", assignments=3)

    response = client.delete(f"/api/v1/schedules/{version_id}", headers=auth_headers)
    assert response.status_code == 204, response.text

    with SessionLocal() as db:
        assert db.get(ScheduleVersion, version_id) is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(ScheduleAssignment)
                .where(ScheduleAssignment.schedule_version_id == version_id)
            )
            == 0
        )
        # 求解痕迹是审计链，不随版本消失。
        assert db.get(SolverRun, run_id) is not None
        assert db.get(DataSnapshot, snapshot_id) is not None

        entry = db.scalar(
            select(AuditLog)
            .where(AuditLog.action == "delete", AuditLog.resource_id == version_id)
            .order_by(AuditLog.created_at.desc())
        )
        assert entry is not None
        # 行已经没了，resource_id 那个 UUID 事后查不回来，detail 是唯一幸存的记录。
        assert entry.detail["name"] == "待删除草稿版本"
        assert entry.detail["assignment_count"] == 3
        assert entry.detail["status"] == "draft"
        assert isinstance(entry.detail["version_no"], int)
        assert entry.detail["solver_run_id"] == run_id

    _drop(None, run_id, snapshot_id)


def test_delete_rejects_the_published_version(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    version_id, run_id, snapshot_id = _make_version("正在使用的版本", status="published")

    response = client.delete(f"/api/v1/schedules/{version_id}", headers=auth_headers)
    assert response.status_code == 409, response.text
    assert "请先回滚到其他版本" in response.json()["detail"]

    with SessionLocal() as db:
        assert db.get(ScheduleVersion, version_id) is not None
    _drop(version_id, run_id, snapshot_id)


def test_delete_rejects_the_official_import_baseline(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """官方原始课表恰好总是 published，但那是导入器的实现巧合，不是模型层的保证。"""
    version_id, run_id, snapshot_id = _make_version("示范校区官方原始课表", status="archived")

    response = client.delete(f"/api/v1/schedules/{version_id}", headers=auth_headers)
    assert response.status_code == 409, response.text
    assert "官方原始课表" in response.json()["detail"]

    _drop(version_id, run_id, snapshot_id)


def test_delete_rejects_a_version_that_still_has_children(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """父版本是子版本 diff「变更前」的唯一来源，只能从叶子往上删。"""
    parent_id, parent_run, parent_snapshot = _make_version("父版本")
    child_id, child_run, child_snapshot = _make_version("子版本", parent_id=parent_id)

    blocked = client.delete(f"/api/v1/schedules/{parent_id}", headers=auth_headers)
    assert blocked.status_code == 409, blocked.text
    assert "来源版本" in blocked.json()["detail"]

    assert client.delete(f"/api/v1/schedules/{child_id}", headers=auth_headers).status_code == 204
    assert client.delete(f"/api/v1/schedules/{parent_id}", headers=auth_headers).status_code == 204

    _drop(None, child_run, child_snapshot)
    _drop(None, parent_run, parent_snapshot)


def test_delete_rejects_a_version_referenced_by_a_reschedule_event(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    version_id, run_id, snapshot_id = _make_version("调课父版本")
    with SessionLocal() as db:
        event = RescheduleEvent(
            event_type="teacher_leave",
            description="删除保护用调课事件",
            parent_schedule_id=version_id,
        )
        db.add(event)
        db.commit()
        event_id = event.id

    response = client.delete(f"/api/v1/schedules/{version_id}", headers=auth_headers)
    assert response.status_code == 409, response.text
    assert "调课事件" in response.json()["detail"]

    with SessionLocal() as db:
        db.delete(db.get(RescheduleEvent, event_id))
        db.commit()
    _drop(version_id, run_id, snapshot_id)


def test_delete_rejects_a_version_with_calendar_bindings(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """飞书只有建日程没有删日程的能力，绑定行是唯一的回收凭据，删了就收不回来了。"""
    version_id, run_id, snapshot_id = _make_version("已下发日历的版本")
    with SessionLocal() as db:
        course_id = db.scalar(select(CourseSession.id))
        assert course_id is not None
        db.add(
            CalendarEventBinding(
                schedule_version_id=version_id,
                course_session_id=course_id,
                calendar_id="cal-delete-test",
                event_id="event-delete-test",
                calendar_user_id="ou_delete_test",
                idempotency_key=f"tupai:{version_id}:{course_id}",
            )
        )
        db.commit()

    response = client.delete(f"/api/v1/schedules/{version_id}", headers=auth_headers)
    assert response.status_code == 409, response.text
    assert "无法回收已创建的日程" in response.json()["detail"]

    with SessionLocal() as db:
        db.execute(
            CalendarEventBinding.__table__.delete().where(
                CalendarEventBinding.schedule_version_id == version_id
            )
        )
        db.commit()
    _drop(version_id, run_id, snapshot_id)


def test_delete_discards_the_candidate_reference_instead_of_blocking(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """调课跑出来的废候选正是最该能删的东西：这一列可空，同事务里置空并标记事件。"""
    parent_id, parent_run, parent_snapshot = _make_version("候选调课父版本")
    candidate_id, candidate_run, candidate_snapshot = _make_version("候选版本")
    with SessionLocal() as db:
        event = RescheduleEvent(
            event_type="teacher_leave",
            description="候选删除用调课事件",
            status="candidate_ready",
            parent_schedule_id=parent_id,
            candidate_schedule_id=candidate_id,
        )
        db.add(event)
        db.commit()
        event_id = event.id

    response = client.delete(f"/api/v1/schedules/{candidate_id}", headers=auth_headers)
    assert response.status_code == 204, response.text

    with SessionLocal() as db:
        event = db.get(RescheduleEvent, event_id)
        assert event is not None
        assert event.candidate_schedule_id is None
        # 留在 candidate_ready 会让调课页显示「候选已生成」却点不开。
        assert event.status == "candidate_discarded"
        entry = db.scalar(
            select(AuditLog)
            .where(AuditLog.action == "delete", AuditLog.resource_id == candidate_id)
            .order_by(AuditLog.created_at.desc())
        )
        assert entry is not None
        assert entry.detail["discarded_candidate_events"] == [event_id]
        db.delete(db.get(RescheduleEvent, event_id))
        db.commit()

    _drop(None, candidate_run, candidate_snapshot)
    _drop(parent_id, parent_run, parent_snapshot)


def test_delete_is_approver_only_and_404s_on_unknown_ids(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    version_id, run_id, snapshot_id = _make_version("权限校验版本")

    created = client.post(
        "/api/v1/users",
        headers=auth_headers,
        json={"username": "delete_scheduler", "password": "delete-scheduler-2026"},
    )
    assert created.status_code == 201, created.text
    member = created.json()
    promoted = client.patch(
        f"/api/v1/users/{member['id']}/role", headers=auth_headers, json={"role": "scheduler"}
    )
    assert promoted.status_code == 200, promoted.text
    login = client.post(
        "/api/v1/auth/token",
        data={"username": "delete_scheduler", "password": "delete-scheduler-2026"},
    )
    assert login.status_code == 200
    scheduler_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # 排课员能提交求解造出草稿，却不能自己清理——删除与发布/回滚同档，都是 Approver。
    forbidden = client.delete(f"/api/v1/schedules/{version_id}", headers=scheduler_headers)
    assert forbidden.status_code == 403, forbidden.text

    missing = client.delete("/api/v1/schedules/no-such-version", headers=auth_headers)
    assert missing.status_code == 404, missing.text

    assert client.delete(f"/api/v1/schedules/{version_id}", headers=auth_headers).status_code == 204

    disabled = client.patch(
        f"/api/v1/users/{member['id']}/status", headers=auth_headers, json={"is_active": False}
    )
    assert disabled.status_code == 200
    _drop(None, run_id, snapshot_id)
