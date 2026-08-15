"""按筛选条件批量操作课程场次。

前端「全选筛选结果」在真实数据下是两万条，逐条把 id 塞进请求体既撑请求也撑 SQLite 的
绑定变量上限。这里守住的是：条件入参能用、按 id 的老用法不变、以及删除必须带
expected_count 且对不上就拒绝——筛选条件在两次请求之间漂移过就不该继续删。
"""

from __future__ import annotations

from datetime import date
from typing import Any

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

LINE = "筛选批量测试线"
KEEP_TYPE = "筛选批量·保留班型"
MOVE_TYPE = "筛选批量·改期班型"


def _create_course(
    client: TestClient, headers: dict[str, str], campus_id: str, **overrides: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "campus_id": campus_id,
        "business_line": LINE,
        "class_business_id": "FILTER-CLASS",
        "teacher_business_id": "FILTER-TEACHER",
        "session_no": 1,
        "duration_minutes": 180,
        "lesson_date": "2026-11-01",
    }
    payload.update(overrides)
    response = client.post("/api/v1/course-sessions", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _seed(client: TestClient, headers: dict[str, str], campus_id: str) -> list[dict[str, Any]]:
    rows = [
        (f"FILTER-MOVE-{index}", MOVE_TYPE, "数学") for index in range(3)
    ] + [(f"FILTER-KEEP-{index}", KEEP_TYPE, "英语") for index in range(2)]
    return [
        _create_course(
            client,
            headers,
            campus_id,
            business_id=business_id,
            product_type=product_type,
            subject=subject,
            lesson_name=f"{subject}·筛选批量课节",
        )
        for business_id, product_type, subject in rows
    ]


def _cleanup(client: TestClient, headers: dict[str, str], expected: int) -> None:
    response = client.post(
        "/api/v1/course-sessions/batch-delete",
        headers=headers,
        json={"filter": {"business_line": LINE}, "expected_count": expected},
    )
    assert response.status_code == 200, response.text


def test_filter_batch_update_changes_only_matching_rows(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    campus_id = client.get("/api/v1/campuses", headers=auth_headers).json()[0]["id"]
    _seed(client, auth_headers, campus_id)

    moved = client.post(
        "/api/v1/course-sessions/batch-update",
        headers=auth_headers,
        json={
            "filter": {"business_line": LINE, "product_type": MOVE_TYPE},
            "expected_count": 3,
            "lesson_date": "2026-11-09",
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["affected_count"] == 3

    rows = {
        item["business_id"]: item
        for item in client.get("/api/v1/course-sessions", headers=auth_headers).json()
        if item["business_line"] == LINE
    }
    assert {rows[f"FILTER-MOVE-{index}"]["lesson_date"] for index in range(3)} == {"2026-11-09"}
    assert {rows[f"FILTER-KEEP-{index}"]["lesson_date"] for index in range(2)} == {"2026-11-01"}

    _cleanup(client, auth_headers, 5)


def test_filter_batch_update_room_is_validated_against_the_campus(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    campus_id = client.get("/api/v1/campuses", headers=auth_headers).json()[0]["id"]
    _seed(client, auth_headers, campus_id)
    room = client.get("/api/v1/rooms", headers=auth_headers).json()[0]

    rejected = client.post(
        "/api/v1/course-sessions/batch-update",
        headers=auth_headers,
        json={
            "filter": {"business_line": LINE},
            "original_room_business_id": "FILTER-NO-SUCH-ROOM",
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert "不属于所选课程的校区" in rejected.text

    accepted = client.post(
        "/api/v1/course-sessions/batch-update",
        headers=auth_headers,
        json={
            "filter": {"business_line": LINE},
            "original_room_business_id": room["business_id"],
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["affected_count"] == 5

    _cleanup(client, auth_headers, 5)


def test_filter_search_reproduces_the_cross_field_match(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """搜索框在前端是八个字段拼成一串再做子串匹配，服务端必须逐字段同序复刻。

    拼接顺序错了，「班级 教师」这种跨字段查询词就会两边命中不同的行——而这两个数字
    正是删除防呆比对的依据。
    """
    campus_id = client.get("/api/v1/campuses", headers=auth_headers).json()[0]["id"]
    _seed(client, auth_headers, campus_id)

    def matched(term: str) -> int:
        response = client.post(
            "/api/v1/course-sessions/batch-update",
            headers=auth_headers,
            json={"filter": {"business_line": LINE, "search": term}, "lesson_date": None},
        )
        assert response.status_code == 200, response.text
        return int(response.json()["affected_count"])

    assert matched("FILTER-MOVE") == 3
    # 跨字段：business_id 的尾巴接上 class_business_id 的头，中间只有一个空格。
    assert matched("FILTER-MOVE-0 FILTER-CLASS") == 1
    # 顺序反过来就不该命中——拼接是有序的，不是集合。
    assert matched("FILTER-CLASS FILTER-MOVE-0") == 0
    # 通配符按字面处理，不许被当成 LIKE 元字符放大命中范围。
    assert matched("FILTER%MOVE") == 0

    _cleanup(client, auth_headers, 5)


def test_filter_delete_requires_a_matching_expected_count(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    campus_id = client.get("/api/v1/campuses", headers=auth_headers).json()[0]["id"]
    _seed(client, auth_headers, campus_id)

    missing_guard = client.post(
        "/api/v1/course-sessions/batch-delete",
        headers=auth_headers,
        json={"filter": {"business_line": LINE}},
    )
    assert missing_guard.status_code == 422, missing_guard.text
    assert "expected_count" in missing_guard.text

    drifted = client.post(
        "/api/v1/course-sessions/batch-delete",
        headers=auth_headers,
        json={"filter": {"business_line": LINE}, "expected_count": 4},
    )
    assert drifted.status_code == 422, drifted.text
    assert "筛选结果已变化" in drifted.json()["detail"]
    assert "预期命中 4 条，实际命中 5 条" in drifted.json()["detail"]

    with SessionLocal() as db:
        survivors = db.scalar(
            select(func.count()).select_from(CourseSession).where(
                CourseSession.business_line == LINE
            )
        )
    assert survivors == 5

    deleted = client.post(
        "/api/v1/course-sessions/batch-delete",
        headers=auth_headers,
        json={"filter": {"business_line": LINE, "product_type": MOVE_TYPE}, "expected_count": 3},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["affected_count"] == 3

    _cleanup(client, auth_headers, 2)


def test_filter_delete_refuses_when_any_row_is_referenced(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """整批拒绝，不做部分删除——被课表版本引用的课次删掉会破坏历史版本。"""
    campus_id = client.get("/api/v1/campuses", headers=auth_headers).json()[0]["id"]
    courses = _seed(client, auth_headers, campus_id)

    with SessionLocal() as db:
        revision = int(db.scalar(select(func.max(DataSnapshot.revision))) or 0) + 1
        snapshot = DataSnapshot(revision=revision, checksum=f"filter-batch-{revision}", payload={})
        db.add(snapshot)
        db.flush()
        run = SolverRun(snapshot_id=snapshot.id, run_type="filter-batch-test", status="succeeded")
        db.add(run)
        db.flush()
        version_no = int(db.scalar(select(func.max(ScheduleVersion.version_no))) or 0) + 1
        version = ScheduleVersion(
            version_no=version_no,
            name="筛选批量引用保护测试",
            status="draft",
            solver_run_id=run.id,
        )
        db.add(version)
        db.flush()
        db.add(
            ScheduleAssignment(
                schedule_version_id=version.id,
                course_session_id=courses[0]["id"],
                lesson_date=date(2026, 11, 1),
                slot_business_id="S01",
                room_business_id="R01",
            )
        )
        db.commit()
        version_id = version.id
        run_id = run.id
        snapshot_id = snapshot.id

    blocked = client.post(
        "/api/v1/course-sessions/batch-delete",
        headers=auth_headers,
        json={"filter": {"business_line": LINE}, "expected_count": 5},
    )
    assert blocked.status_code == 409, blocked.text
    assert "FILTER-MOVE-0" in blocked.json()["detail"]

    with SessionLocal() as db:
        survivors = db.scalar(
            select(func.count()).select_from(CourseSession).where(
                CourseSession.business_line == LINE
            )
        )
        assert survivors == 5
        assignment = db.scalar(
            select(ScheduleAssignment).where(
                ScheduleAssignment.schedule_version_id == version_id
            )
        )
        assert assignment is not None
        db.delete(assignment)
        db.commit()

    _cleanup(client, auth_headers, 5)

    with SessionLocal() as db:
        db.delete(db.get(ScheduleVersion, version_id))
        db.delete(db.get(SolverRun, run_id))
        db.delete(db.get(DataSnapshot, snapshot_id))
        db.commit()


def test_selection_must_be_object_ids_or_filter_but_not_both(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    campus_id = client.get("/api/v1/campuses", headers=auth_headers).json()[0]["id"]
    courses = _seed(client, auth_headers, campus_id)

    neither = client.post(
        "/api/v1/course-sessions/batch-update",
        headers=auth_headers,
        json={"lesson_date": "2026-11-20"},
    )
    assert neither.status_code == 422, neither.text

    both = client.post(
        "/api/v1/course-sessions/batch-update",
        headers=auth_headers,
        json={
            "object_ids": [courses[0]["id"]],
            "filter": {"business_line": LINE},
            "lesson_date": "2026-11-20",
        },
    )
    assert both.status_code == 422, both.text

    # 老用法不变：按 id 依旧能改，并且 expected_count 对 id 模式同样生效。
    by_ids = client.post(
        "/api/v1/course-sessions/batch-update",
        headers=auth_headers,
        json={
            "object_ids": [courses[0]["id"], courses[1]["id"]],
            "expected_count": 2,
            "lesson_date": "2026-11-20",
        },
    )
    assert by_ids.status_code == 200, by_ids.text
    assert by_ids.json()["affected_count"] == 2

    missing_id = client.post(
        "/api/v1/course-sessions/batch-update",
        headers=auth_headers,
        json={"object_ids": ["no-such-course"], "lesson_date": "2026-11-20"},
    )
    assert missing_id.status_code == 404, missing_id.text

    _cleanup(client, auth_headers, 5)


def test_empty_filter_means_every_course_and_still_needs_the_guard(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """没有任何筛选时「全选」就是全表，这是合法用法；防呆靠 expected_count，不靠禁止。

    这里刻意只走拒绝分支：能验证「空条件=全表」，又不会真的把整库课次改掉。
    """
    campus_id = client.get("/api/v1/campuses", headers=auth_headers).json()[0]["id"]
    _seed(client, auth_headers, campus_id)

    with SessionLocal() as db:
        total = int(db.scalar(select(func.count()).select_from(CourseSession)) or 0)
    assert total > 5

    wrong = client.post(
        "/api/v1/course-sessions/batch-delete",
        headers=auth_headers,
        json={"filter": {}, "expected_count": total - 1},
    )
    assert wrong.status_code == 422, wrong.text
    assert f"实际命中 {total} 条" in wrong.json()["detail"]

    with SessionLocal() as db:
        assert int(db.scalar(select(func.count()).select_from(CourseSession)) or 0) == total

    _cleanup(client, auth_headers, 5)
