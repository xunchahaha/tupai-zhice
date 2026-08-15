from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    CourseSession,
    DataSnapshot,
    ScheduleAssignment,
    ScheduleVersion,
    SolverRun,
)


def create_master_record(
    client: TestClient,
    headers: dict[str, str],
    resource: str,
    payload: dict[str, object],
) -> dict[str, object]:
    response = client.post(f"/api/v1/{resource}", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_master_data_batch_update_delete_and_reference_guards(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    campus = client.get("/api/v1/campuses", headers=auth_headers).json()[0]
    campus_id = campus["id"]

    teachers = [
        create_master_record(
            client,
            auth_headers,
            "teachers",
            {
                "campus_id": campus_id,
                "business_id": f"BATCH-T{i}",
                "name": f"批量教师{i}",
                "subject": "原学科",
            },
        )
        for i in (1, 2)
    ]
    replacement_teacher = create_master_record(
        client,
        auth_headers,
        "teachers",
        {
            "campus_id": campus_id,
            "business_id": "BATCH-T-NEW",
            "name": "批量替换教师",
            "subject": "新学科",
        },
    )
    classes = [
        create_master_record(
            client,
            auth_headers,
            "class-groups",
            {
                "campus_id": campus_id,
                "business_id": f"BATCH-C{i}",
                "name": f"批量班级{i}",
            },
        )
        for i in (1, 2)
    ]
    # 还没有课次的班级：聚合字段一律为空，不是数据丢了，是这个班还没排课。
    assert all(item["product_types"] == [] for item in classes)
    assert all(item["teacher_business_ids"] == [] for item in classes)
    assert all(item["session_count"] == 0 for item in classes)
    rooms = [
        create_master_record(
            client,
            auth_headers,
            "rooms",
            {
                "campus_id": campus_id,
                "business_id": f"BATCH-R{i}",
                "name": f"批量教室{i}",
                "is_active": True,
            },
        )
        for i in (1, 2)
    ]
    slots = [
        create_master_record(
            client,
            auth_headers,
            "time-slots",
            {
                "campus_id": campus_id,
                "business_id": f"BATCH-S{i}",
                "weekday": "周一",
                "start_time": f"0{7 + i}:00",
                "end_time": f"{10 + i}:00",
                "kind": "测试",
                "sequence": 100 + i,
                "is_open": True,
            },
        )
        for i in (1, 2)
    ]

    teacher_update = client.post(
        "/api/v1/teachers/batch-update",
        headers=auth_headers,
        json={
            "object_ids": [item["id"] for item in teachers],
            "subject": "批量新学科",
            "calendar_user_id": "ou_batch_teacher",
        },
    )
    assert teacher_update.status_code == 200
    assert teacher_update.json()["affected_count"] == 2

    # 班型/业务线/教师都是课次的属性，班级上没有可批量修改的字段，端点已经取消。
    class_update = client.post(
        "/api/v1/class-groups/batch-update",
        headers=auth_headers,
        json={"object_ids": [item["id"] for item in classes], "grade": "批量新班型"},
    )
    assert class_update.status_code == 405

    room_update = client.post(
        "/api/v1/rooms/batch-update",
        headers=auth_headers,
        json={"object_ids": [item["id"] for item in rooms], "is_active": False},
    )
    assert room_update.status_code == 200
    assert room_update.json()["affected_count"] == 2

    slot_update = client.post(
        "/api/v1/time-slots/batch-update",
        headers=auth_headers,
        json={"object_ids": [item["id"] for item in slots], "is_open": False},
    )
    assert slot_update.status_code == 200
    assert slot_update.json()["affected_count"] == 2

    teacher_rows = {
        item["id"]: item for item in client.get("/api/v1/teachers", headers=auth_headers).json()
    }
    assert all(teacher_rows[item["id"]]["subject"] == "批量新学科" for item in teachers)
    assert all(
        teacher_rows[item["id"]]["calendar_user_id"] == "ou_batch_teacher"
        for item in teachers
    )

    courses = [
        create_master_record(
            client,
            auth_headers,
            "course-sessions",
            {
                "campus_id": campus_id,
                "business_id": f"BATCH-COURSE-{i}",
                "class_business_id": classes[i - 1]["business_id"],
                "teacher_business_id": teachers[i - 1]["business_id"],
                "lesson_name": f"批量引用课程{i}",
                "session_no": i,
                "lesson_date": "2026-10-10",
                "duration_minutes": 180,
                "suggested_slot_id": slots[0]["business_id"] if i == 1 else None,
                "fixed_start_time": "09:00",
                "fixed_end_time": "12:00",
                "original_room_business_id": rooms[0]["business_id"] if i == 1 else None,
            },
        )
        for i in (1, 2)
    ]

    with SessionLocal() as db:
        second_course = db.scalar(
            select(CourseSession).where(CourseSession.id == courses[1]["id"])
        )
        assert second_course is not None
        revision = int(db.scalar(select(func.max(DataSnapshot.revision))) or 0) + 1
        snapshot = DataSnapshot(
            revision=revision,
            checksum=f"batch-master-{revision}",
            payload={},
        )
        db.add(snapshot)
        db.flush()
        run = SolverRun(snapshot_id=snapshot.id, run_type="batch-master-test", status="succeeded")
        db.add(run)
        db.flush()
        version_no = int(db.scalar(select(func.max(ScheduleVersion.version_no))) or 0) + 1
        version = ScheduleVersion(
            version_no=version_no,
            name="批量主数据引用保护测试",
            status="draft",
            solver_run_id=run.id,
        )
        db.add(version)
        db.flush()
        assignment = ScheduleAssignment(
            schedule_version_id=version.id,
            course_session_id=second_course.id,
            lesson_date=date(2026, 10, 10),
            slot_business_id=str(slots[1]["business_id"]),
            room_business_id=str(rooms[1]["business_id"]),
        )
        db.add(assignment)
        db.commit()
        assignment_id = assignment.id
        version_id = version.id
        run_id = run.id
        snapshot_id = snapshot.id

    protected_records = [
        ("teachers", teachers[0]),
        ("class-groups", classes[0]),
        ("rooms", rooms[0]),
        ("time-slots", slots[0]),
    ]
    for resource, record in protected_records:
        response = client.delete(
            f"/api/v1/master-data/{resource}/{record['id']}", headers=auth_headers
        )
        assert response.status_code == 409, response.text

    for resource, records in (
        ("teachers", teachers),
        ("class-groups", classes),
        ("rooms", rooms),
        ("time-slots", slots),
    ):
        response = client.post(
            f"/api/v1/{resource}/batch-delete",
            headers=auth_headers,
            json={"object_ids": [item["id"] for item in records]},
        )
        assert response.status_code == 409, response.text

    with SessionLocal() as db:
        assignment = db.get(ScheduleAssignment, assignment_id)
        assert assignment is not None
        db.delete(assignment)
        db.commit()

    course_delete = client.post(
        "/api/v1/course-sessions/batch-delete",
        headers=auth_headers,
        json={"object_ids": [item["id"] for item in courses]},
    )
    assert course_delete.status_code == 200

    for resource, records in (
        ("class-groups", classes),
        ("rooms", rooms),
        ("time-slots", slots),
        ("teachers", [*teachers, replacement_teacher]),
    ):
        response = client.post(
            f"/api/v1/{resource}/batch-delete",
            headers=auth_headers,
            json={"object_ids": [item["id"] for item in records]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["affected_count"] == len(records)

    with SessionLocal() as db:
        version = db.get(ScheduleVersion, version_id)
        run = db.get(SolverRun, run_id)
        snapshot = db.get(DataSnapshot, snapshot_id)
        assert version is not None and run is not None and snapshot is not None
        db.delete(version)
        db.delete(run)
        db.delete(snapshot)
        db.commit()


def test_member_cannot_use_master_data_batch_write(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/users",
        headers=auth_headers,
        json={"username": "batch_member", "password": "batch-member-pass-2026"},
    )
    assert created.status_code == 201
    member = created.json()
    login = client.post(
        "/api/v1/auth/token",
        data={"username": "batch_member", "password": "batch-member-pass-2026"},
    )
    assert login.status_code == 200
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    teacher = client.get("/api/v1/teachers", headers=member_headers).json()[0]

    forbidden = client.post(
        "/api/v1/teachers/batch-update",
        headers=member_headers,
        json={"object_ids": [teacher["id"]], "subject": "成员不可修改"},
    )
    assert forbidden.status_code == 403

    disabled = client.patch(
        f"/api/v1/users/{member['id']}/status",
        headers=auth_headers,
        json={"is_active": False},
    )
    assert disabled.status_code == 200
