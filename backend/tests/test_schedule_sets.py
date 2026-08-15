from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import export_resource_rows, settings
from app.config import FEISHU_REQUIRED_SCOPES
from app.db import SessionLocal
from app.models import (
    Campus,
    ClassGroup,
    CourseSession,
    DataSnapshot,
    FeishuConnection,
    FeishuWorkspace,
    Room,
    ScheduleAssignment,
    ScheduleVersion,
    SolverRun,
    Teacher,
    TimeSlot,
)
from app.services.feishu import FeishuService


def _headers(token: str, schedule_set_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Schedule-Set-Id": schedule_set_id,
    }


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/token", data={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _create_scoped_version(schedule_set_id: str, name: str) -> tuple[str, str]:
    """Persist tiny independent histories without invoking the long solver path."""
    with SessionLocal() as db:
        snapshot = DataSnapshot(
            schedule_set_id=schedule_set_id,
            revision=1,
            checksum=f"scope-{uuid4().hex}",
            payload={},
        )
        db.add(snapshot)
        db.flush()
        run = SolverRun(
            schedule_set_id=schedule_set_id,
            snapshot_id=snapshot.id,
            status="completed",
            request_payload={},
            result_payload={},
        )
        db.add(run)
        db.flush()
        version = ScheduleVersion(
            schedule_set_id=schedule_set_id,
            version_no=1,
            name=name,
            status="published",
            solver_run_id=run.id,
            metrics={},
            published_at=datetime.now(UTC),
        )
        db.add(version)
        db.flush()
        course_session_id = db.scalar(
            select(CourseSession.id).where(CourseSession.schedule_set_id == schedule_set_id)
        )
        if course_session_id is None:
            campus = Campus(
                schedule_set_id=schedule_set_id,
                business_id=f"fixture-campus-{schedule_set_id}",
                name="同步测试校区",
            )
            db.add(campus)
            db.flush()
            course = CourseSession(
                schedule_set_id=schedule_set_id,
                campus_id=campus.id,
                business_id=f"fixture-course-{schedule_set_id}",
                class_business_id="",
                teacher_business_id="",
                lesson_name="同步测试课程",
            )
            db.add(course)
            db.flush()
            course_session_id = course.id
        db.add(
            ScheduleAssignment(
                schedule_version_id=version.id,
                course_session_id=course_session_id,
                slot_business_id="",
                room_business_id="",
            )
        )
        db.commit()
        return version.id, run.id


def test_feishu_master_data_export_is_scoped_to_the_selected_schedule_set(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    suffix = uuid4().hex[:8]
    first = client.post(
        "/api/v1/schedule-sets", headers=auth_headers, json={"name": f"导出一-{suffix}"}
    )
    second = client.post(
        "/api/v1/schedule-sets", headers=auth_headers, json={"name": f"导出二-{suffix}"}
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    first_id = first.json()["id"]
    second_id = second.json()["id"]

    with SessionLocal() as db:
        for schedule_set_id, marker in ((first_id, "一"), (second_id, "二")):
            campus = Campus(
                schedule_set_id=schedule_set_id,
                business_id=f"C-{suffix}",
                name=f"校区{marker}",
            )
            db.add(campus)
            db.flush()
            db.add_all(
                [
                    Teacher(
                        schedule_set_id=schedule_set_id,
                        campus_id=campus.id,
                        business_id=f"T-{suffix}",
                        name=f"教师{marker}",
                    ),
                    ClassGroup(
                        schedule_set_id=schedule_set_id,
                        campus_id=campus.id,
                        business_id=f"G-{suffix}",
                        name=f"班级{marker}",
                    ),
                    Room(
                        schedule_set_id=schedule_set_id,
                        campus_id=campus.id,
                        business_id=f"R-{suffix}",
                        name=f"教室{marker}",
                    ),
                    TimeSlot(
                        schedule_set_id=schedule_set_id,
                        campus_id=campus.id,
                        business_id=f"S-{suffix}",
                        weekday="周一",
                        start_time="09:00",
                        end_time="10:00",
                    ),
                    CourseSession(
                        schedule_set_id=schedule_set_id,
                        campus_id=campus.id,
                        business_id=f"CS-{suffix}",
                        class_business_id=f"G-{suffix}",
                        teacher_business_id=f"T-{suffix}",
                        lesson_name=f"课程{marker}",
                    ),
                ]
            )
        db.commit()

        assert [row["教师名称"] for row in export_resource_rows(db, "teachers", first_id)] == [
            "教师一"
        ]
        assert [row["班级名称"] for row in export_resource_rows(db, "class_groups", first_id)] == [
            "班级一"
        ]
        assert [
            row["教室名称"] for row in export_resource_rows(db, "rooms", first_id)
        ] == ["教室一"]
        assert [row["业务标识"] for row in export_resource_rows(db, "time_slots", first_id)] == [
            f"S-{suffix}"
        ]
        courses = export_resource_rows(db, "course_sessions", first_id)
        assert [row["业务标识"] for row in courses] == [f"CS-{suffix}"]
        assert set(courses[0]) == {
            "业务标识",
            "业务线",
            "产品班型",
            "班级标识",
            "教师标识",
            "具体日程账号",
            "学科",
            "课节名称",
            "编排来源",
            "编排阶段",
            "计划课次",
            "计划课时",
            "课次序号",
            "上课日期",
            "时长分钟",
            "建议时段",
            "固定开始时间",
            "固定结束时间",
            "原始教室标识",
            "是否锁定",
        }


def test_schedule_set_visibility_and_per_set_operation_permissions(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: Any
) -> None:
    suffix = uuid4().hex[:8]
    set_one = client.post(
        "/api/v1/schedule-sets",
        headers=auth_headers,
        json={"name": f"郑州排课-{suffix}"},
    )
    set_two = client.post(
        "/api/v1/schedule-sets",
        headers=auth_headers,
        json={"name": f"开封排课-{suffix}"},
    )
    assert set_one.status_code == 201, set_one.text
    assert set_two.status_code == 201, set_two.text
    first = set_one.json()
    second = set_two.json()

    created: dict[str, str] = {}
    for username in (f"scope_a_{suffix}", f"scope_b_{suffix}", f"scope_viewer_{suffix}"):
        role = "viewer" if "viewer" in username else "scheduler"
        response = client.post(
            "/api/v1/users",
            headers=auth_headers,
            json={"username": username, "password": "scope-password-2026", "role": role},
        )
        assert response.status_code == 201, response.text
        created[username] = response.json()["id"]

    # New non-admin accounts inherit the old default timetable for backwards
    # compatibility.  This test removes that bootstrap grant so the matrix below
    # exactly models A -> 1, B -> 1/2, viewer -> 1.
    for user_id in created.values():
        assert (
            client.delete(
                f"/api/v1/schedule-sets/default/members/{user_id}", headers=auth_headers
            ).status_code
            == 204
        )

    def grant(set_id: str, user_id: str, access_role: str) -> None:
        response = client.put(
            f"/api/v1/schedule-sets/{set_id}/members/{user_id}",
            headers=auth_headers,
            json={"user_id": user_id, "access_role": access_role},
        )
        assert response.status_code == 200, response.text

    user_a = created[f"scope_a_{suffix}"]
    user_b = created[f"scope_b_{suffix}"]
    viewer = created[f"scope_viewer_{suffix}"]
    grant(first["id"], user_a, "scheduler")
    grant(first["id"], user_b, "scheduler")
    grant(second["id"], user_b, "scheduler")
    grant(first["id"], viewer, "viewer")

    first_version, _ = _create_scoped_version(first["id"], f"课表一-{suffix}")
    second_version, second_run = _create_scoped_version(second["id"], f"课表二-{suffix}")

    token_a = _login(client, f"scope_a_{suffix}", "scope-password-2026")
    token_b = _login(client, f"scope_b_{suffix}", "scope-password-2026")
    token_viewer = _login(client, f"scope_viewer_{suffix}", "scope-password-2026")

    visible_a = client.get("/api/v1/schedule-sets", headers={"Authorization": f"Bearer {token_a}"})
    assert visible_a.status_code == 200
    assert [item["id"] for item in visible_a.json()] == [first["id"]]

    schedules_a = client.get("/api/v1/schedules", headers=_headers(token_a, first["id"]))
    assert schedules_a.status_code == 200
    assert [item["id"] for item in schedules_a.json()] == [first_version]
    denied_second_schedule = client.get(
        "/api/v1/schedules", headers=_headers(token_a, second["id"])
    )
    assert denied_second_schedule.status_code == 404
    assert (
        client.get(
            f"/api/v1/solver-runs/{second_run}", headers=_headers(token_a, first["id"])
        ).status_code
        == 404
    )

    visible_b = client.get("/api/v1/schedule-sets", headers={"Authorization": f"Bearer {token_b}"})
    assert visible_b.status_code == 200
    assert {item["id"] for item in visible_b.json()} == {first["id"], second["id"]}
    schedules_b = client.get("/api/v1/schedules", headers=_headers(token_b, second["id"]))
    assert schedules_b.status_code == 200
    assert [item["id"] for item in schedules_b.json()] == [second_version]

    # A viewer can read the assigned plan but cannot create a solver run in it.
    viewer_schedules = client.get(
        "/api/v1/schedules", headers=_headers(token_viewer, first["id"])
    )
    assert viewer_schedules.status_code == 200
    denied = client.post(
        "/api/v1/solver-runs",
        headers=_headers(token_viewer, first["id"]),
        json={"wait": True},
    )
    assert denied.status_code == 403

    # Export/sync selection follows the active timetable too: a workspace from
    # plan 2 is never returned while plan 1 is selected, and its schedule rows
    # cannot leak into plan 1's Bitable sync.
    with SessionLocal() as db:
        connection = FeishuConnection(
            user_id=user_a,
            access_token_encrypted="encrypted-access",
            refresh_token_encrypted="encrypted-refresh",
                access_expires_at=datetime.now(UTC) + timedelta(hours=1),
                scopes=list(FEISHU_REQUIRED_SCOPES),
            status="active",
        )
        db.add(connection)
        db.flush()
        workspace_one = FeishuWorkspace(
            connection_id=connection.id,
            schedule_set_id=first["id"],
            name=f"workspace-one-{suffix}",
            app_token=f"app-one-{suffix}",
            default_table_id="tbl-one",
            url="https://example.test/one",
            status="active",
        )
        workspace_two = FeishuWorkspace(
            connection_id=connection.id,
            schedule_set_id=second["id"],
            name=f"workspace-two-{suffix}",
            app_token=f"app-two-{suffix}",
            default_table_id="tbl-two",
            url="https://example.test/two",
            status="active",
        )
        db.add_all([workspace_one, workspace_two])
        db.commit()
        first_connection = FeishuService(settings, db).connection_view(user_a, first["id"])
        second_connection = FeishuService(settings, db).connection_view(user_a, second["id"])
        assert first_connection["workspace"]["id"] == workspace_one.id
        assert second_connection["workspace"]["id"] == workspace_two.id
        # B has timetable access but did not OAuth-authorize a separate Feishu
        # account.  The selected timetable's admin-owned workspace must still
        # be reported as ready, otherwise the UI leaves B's sync controls grey.
        shared_connection = FeishuService(settings, db).connection_view(user_b, first["id"])
        assert shared_connection["authorized"] is True
        assert shared_connection["workspace"]["id"] == workspace_one.id

        first_export = export_resource_rows(db, "schedule", first["id"])
        second_export = export_resource_rows(db, "schedule", second["id"])
        assert [row["版本名称"] for row in first_export] == [f"课表一-{suffix}"]
        assert [row["版本名称"] for row in second_export] == [f"课表二-{suffix}"]
        workspace_one_id = workspace_one.id
        # Close the read transaction before the TestClient opens its own writer
        # session against the shared SQLite test database.
        db.rollback()

        calls: list[tuple[str, str, str | None]] = []

        def sync_rows(
            service: object,
            user_id: str,
            resource: str,
            rows: list[dict[str, object]],
            workspace_id: str | None = None,
            schedule_set_id: str = "default",
        ) -> dict[str, object]:
            del service, user_id, rows
            calls.append((resource, schedule_set_id, workspace_id))
            return {
                "records_read": 0,
                "records_written": 1,
                "records_created": 1,
                "records_updated": 0,
                "workspace_id": workspace_one_id,
            }

        monkeypatch.setattr(FeishuService, "sync_rows", sync_rows)
        monkeypatch.setattr(FeishuService, "prepare_sync_resources", lambda *_args, **_kwargs: {})
        batch = client.post(
            "/api/v1/integrations/feishu/sync-batch",
            headers=_headers(token_a, first["id"]),
            json={"resources": ["schedule", "public_summary"]},
        )
        assert batch.status_code == 200, batch.text
        batch_payload = batch.json()
        assert batch_payload["schedule_set_id"] == first["id"]
        assert batch_payload["status"] == "completed"
        assert calls == [
            ("schedule", first["id"], None),
            ("public_summary", first["id"], None),
        ]
        assert all(
            item["detail"]["schedule_set_id"] == first["id"]
            for item in batch_payload["results"]
        )

        # The shared test database is reused by the Feishu OAuth tests.  Remove
        # this deliberately synthetic connection so it cannot become their
        # unqualified first connection record.
        db.delete(workspace_one)
        db.delete(workspace_two)
        db.delete(connection)
        db.commit()
