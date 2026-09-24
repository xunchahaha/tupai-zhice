"""公开课表层端到端回归（docs/roadmap/06 §5 验收标准的后端部分）。

覆盖：token 创建/明文一次性、免登录 schedule.json 与 calendar.ics、
过期/停用/伪造 token 统一 404、轮换即旧链接失效、管理端点角色门槛、
show_teacher_names 开关、访问计数节流；ICS 用 icalendar 反解析断言。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from icalendar import Calendar
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    AuditLog,
    CourseSession,
    DataSnapshot,
    PublicLinkToken,
    ScheduleAssignment,
    ScheduleVersion,
    SolverRun,
)
from app.timezone import SHANGHAI_TZ

CLASS_BUSINESS_ID = "B01"
TEACHER_BUSINESS_ID = "T01"
TEACHER_NAME = "教师甲"


def _leak_scan(body: Any) -> str:
    """公开载荷的「业务标识不外泄」检查文本：先抹掉 ISO 时间戳再序列化。

    ``generated_at``/``before_time`` 之类的 ``2026-11-09T01:30:52`` 会在
    01:00–01:59（Asia/Shanghai）之间与工号 ``T01`` 撞子串（``T`` 后跟小时
    ``01``），让白名单断言在特定时段假失败——泄漏口径针对数据字段，不含
    展示用时间戳。"""
    return re.sub(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[+-]\d{2}:\d{2}|Z)?",
        "",
        json.dumps(body, ensure_ascii=False),
    )


def _make_published_pair() -> dict[str, Any]:
    """播种「基线 + 当前」两个发布版本，带确定的 assignment 日期。

    基线：B01 两节课都有 lesson_date；当前：第一节课挪一周（时间调整），
    第二节课的 lesson_date 置空（循环课次，ICS P0 跳过）且换教室。
    """

    with SessionLocal() as db:
        courses = list(
            db.scalars(
                select(CourseSession)
                .where(
                    CourseSession.class_business_id == CLASS_BUSINESS_ID,
                    CourseSession.is_active.is_(True),
                )
                .order_by(CourseSession.business_id)
            )
        )
        assert len(courses) >= 2, "种子主数据缺少 B01 课次"
        course_ids = [course.id for course in courses[:2]]
        campus_id = courses[0].campus_id
        schedule_set_id = courses[0].schedule_set_id

        next_version_no = int(
            db.scalar(
                select(func.max(ScheduleVersion.version_no)).where(
                    ScheduleVersion.schedule_set_id == schedule_set_id
                )
            )
            or 0
        )
        base_specs = [
            (course_ids[0], date(2026, 11, 2), "S01", "R01"),
            (course_ids[1], date(2026, 11, 3), "S02", "R01"),
        ]
        current_specs = [
            (course_ids[0], date(2026, 11, 9), "S01", "R01"),
            (course_ids[1], None, "S02", "R02"),
        ]
        versions: list[ScheduleVersion] = []
        for offset, specs in ((1, base_specs), (2, current_specs)):
            snapshot = DataSnapshot(
                schedule_set_id=schedule_set_id,
                revision=10_000 + offset,
                checksum=f"public-link-fixture-{next_version_no + offset}",
                payload={},
            )
            db.add(snapshot)
            db.flush()
            solver_run = SolverRun(
                schedule_set_id=schedule_set_id,
                snapshot_id=snapshot.id,
                status="completed",
                request_payload={},
                result_payload={},
            )
            db.add(solver_run)
            db.flush()
            version = ScheduleVersion(
                schedule_set_id=schedule_set_id,
                version_no=next_version_no + offset,
                name=f"公开链接夹具 V{next_version_no + offset}",
                status="published",
                solver_run_id=solver_run.id,
                metrics={},
                # 当前版本的发布时间取夹具运行时刻，确保是全库最新发布；
                # 基线再往前推 48 小时，只作为调课对照。
                published_at=(
                    datetime.now(UTC)
                    - timedelta(hours=48 * (2 - offset))
                ),
            )
            db.add(version)
            db.flush()
            for course_id, lesson_date, slot_business_id, room_business_id in specs:
                db.add(
                    ScheduleAssignment(
                        schedule_version_id=version.id,
                        course_session_id=course_id,
                        lesson_date=lesson_date,
                        slot_business_id=slot_business_id,
                        room_business_id=room_business_id,
                        change_kind="assigned",
                    )
                )
            versions.append(version)
        baseline, current = versions
        current.status = "published"
        baseline.status = "archived"
        db.add(
            AuditLog(
                action="publish",
                resource_type="schedule",
                resource_id=current.id,
                detail={"replaced_published_schedule_id": baseline.id},
            )
        )
        db.commit()
        return {
            "schedule_set_id": schedule_set_id,
            "campus_id": campus_id,
            "baseline_id": baseline.id,
            "current_id": current.id,
            "current_version_no": current.version_no,
        }


@pytest.fixture(scope="session")
def published(client: TestClient) -> dict[str, Any]:
    """依赖 client 夹具以确保种子数据与迁移先于播种执行。"""

    return _make_published_pair()


def _create_link(
    client: TestClient,
    auth_headers: dict[str, str],
    published: dict[str, Any],
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "scope": "class",
        "campus_id": published["campus_id"],
        "resource_business_id": CLASS_BUSINESS_ID,
        "show_teacher_names": True,
    }
    payload.update(overrides)
    response = client.post(
        f"/api/v1/schedule-sets/{published['schedule_set_id']}/public-links",
        headers=auth_headers,
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_returns_plaintext_token_exactly_once(
    client: TestClient, auth_headers: dict[str, str], published: dict[str, Any]
) -> None:
    link = _create_link(client, auth_headers, published, note="创建一次性明文")
    token = link["token"]
    assert token
    assert link["token_hint"] == token[-4:]
    assert link["public_url"].endswith(f"/public/t/{token}")
    assert link["status"] == "active"
    assert link["display_name"]  # 默认取班级名

    # 明文只此一次：列表、后续任何管理响应都不再出现。
    listing = client.get(
        f"/api/v1/schedule-sets/{published['schedule_set_id']}/public-links",
        headers=auth_headers,
    )
    assert listing.status_code == 200
    rows = listing.json()
    assert token not in json.dumps(rows, ensure_ascii=False)
    row = next(item for item in rows if item["id"] == link["id"])
    assert row["token_hint"] == token[-4:]
    assert "token" not in row


def test_schedule_json_is_public_and_whitelisted(
    client: TestClient, auth_headers: dict[str, str], published: dict[str, Any]
) -> None:
    link = _create_link(client, auth_headers, published)

    response = client.get(f"/api/v1/public/links/{link['token']}/schedule.json")
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "class"
    assert body["version_no"] == published["current_version_no"]
    assert body["published_at"]
    assert body["first_date"] == "2026-11-09"
    assert body["last_date"] == "2026-11-09"
    assert len(body["rows"]) == 2

    row_keys = {key for row in body["rows"] for key in row}
    assert row_keys == {
        "date",
        "weekday",
        "start",
        "end",
        "class_name",
        "subject",
        "lesson_name",
        "teacher_names",
        "location",
    }
    dated = next(row for row in body["rows"] if row["date"])
    assert dated["date"] == "2026-11-09"
    assert dated["start"] == "18:30"
    assert dated["end"] == "20:00"
    assert dated["location"] == "小班教室1"
    assert dated["teacher_names"] == [TEACHER_NAME]
    recurring = next(row for row in body["rows"] if not row["date"])
    assert recurring["location"] == "小班教室2"

    # 调课横幅数据来自与飞书公告表同一份投影。
    types = {item["type"] for item in body["adjustments"]}
    assert "时间调整" in types
    assert "时间及地点调整" in types
    adjustment_keys = {key for item in body["adjustments"] for key in item}
    assert adjustment_keys == {
        "type",
        "class_name",
        "course_name",
        "before_time",
        "after_time",
        "before_location",
        "after_location",
    }

    serialized = _leak_scan(body)
    assert "T01" not in serialized  # 教师业务标识（工号）不外泄
    assert "teacher_business_id" not in serialized
    assert "campus_id" not in serialized  # 校区内部主键不外泄
    assert "student" not in serialized.lower()


def test_calendar_ics_parses_with_events_and_timezone(
    client: TestClient, auth_headers: dict[str, str], published: dict[str, Any]
) -> None:
    link = _create_link(client, auth_headers, published)
    response = client.get(f"/api/v1/public/links/{link['token']}/calendar.ics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert response.headers["cache-control"] == "public, max-age=3600"
    etag = response.headers["etag"]

    calendar = Calendar.from_ical(response.content)
    events = [component for component in calendar.walk("VEVENT")]
    # 一节有 lesson_date 的普通课次 + 一节循环课次（WEEKLY RRULE，专项用例断言）。
    assert len(events) == 2
    dated = next(event for event in events if "RRULE" not in event)
    assert str(dated["UID"]).startswith(f"tupai-class-{CLASS_BUSINESS_ID}-")
    assert str(dated["UID"]).endswith("@public.tupai")
    assert str(dated["DTSTART"].dt.tzinfo.utcoffset(dated["DTSTART"].dt)) == "8:00:00"
    assert "TZID=Asia/Shanghai" in response.content.decode("utf-8")
    assert "X-WR-TIMEZONE:Asia/Shanghai" in response.content.decode("utf-8")
    assert "BEGIN:VTIMEZONE" in response.content.decode("utf-8")
    assert str(dated["SUMMARY"]) == "数学·示范课节1"
    assert TEACHER_NAME in str(dated["DESCRIPTION"])

    # ETag 命中返回 304。
    conditional = client.get(
        f"/api/v1/public/links/{link['token']}/calendar.ics",
        headers={"If-None-Match": etag},
    )
    assert conditional.status_code == 304


def test_recurring_lesson_expands_to_weekly_rrule(
    client: TestClient, auth_headers: dict[str, str], published: dict[str, Any]
) -> None:
    """循环课次（无 lesson_date，仅 TimeSlot.weekday）生成 WEEKLY RRULE。

    展开窗口 = 当前发布版本有日期课次的 min/max（夹具内只有 2026-11-09 一天）；
    S02 时段为「周一」20:10-21:40，DTSTART 取窗口内首个周一 + 时段；
    UNTIL = 窗口末整天换算 UTC（23:59:59+08:00 → 15:59:59Z）。
    """

    link = _create_link(client, auth_headers, published)
    response = client.get(f"/api/v1/public/links/{link['token']}/calendar.ics")
    assert response.status_code == 200
    events = [component for component in Calendar.from_ical(response.content).walk("VEVENT")]
    recurring = next(event for event in events if "RRULE" in event)

    rrule = recurring["RRULE"]
    assert str(rrule["FREQ"][0]) == "WEEKLY"
    assert str(rrule["BYDAY"][0]) == "MO"
    assert rrule["UNTIL"][0] == datetime(2026, 11, 9, 15, 59, 59, tzinfo=UTC)
    assert recurring["DTSTART"].dt == datetime(2026, 11, 9, 20, 10, tzinfo=SHANGHAI_TZ)
    assert recurring["DTEND"].dt == datetime(2026, 11, 9, 21, 40, tzinfo=SHANGHAI_TZ)
    # RRULE 课次的 UID 同样跨版本稳定（assignment 段为该循环课次的 assignment）。
    assert str(recurring["UID"]).startswith(f"tupai-class-{CLASS_BUSINESS_ID}-")
    assert str(recurring["UID"]) != str(
        next(event for event in events if "RRULE" not in event)["UID"]
    )


def test_invalid_expired_revoked_tokens_all_404(
    client: TestClient, auth_headers: dict[str, str], published: dict[str, Any]
) -> None:
    expired = _create_link(client, auth_headers, published)
    revoked = _create_link(client, auth_headers, published)
    with SessionLocal() as db:
        row = db.get(PublicLinkToken, expired["id"])
        assert row is not None
        row.expires_at = datetime.now(UTC).astimezone() - timedelta(minutes=1)
        db.commit()

    assert (
        client.get(f"/api/v1/public/links/{expired['token']}/schedule.json").status_code
        == 404
    )
    revoke = client.delete(f"/api/v1/public-links/{revoked['id']}", headers=auth_headers)
    assert revoke.status_code == 204
    assert (
        client.get(f"/api/v1/public/links/{revoked['token']}/schedule.json").status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/public/links/{revoked['token']}/calendar.ics").status_code
        == 404
    )
    forged = "a" * 43
    assert client.get(f"/api/v1/public/links/{forged}/schedule.json").status_code == 404
    # 404 同形：伪造与失效返回体一致，不暴露存在性。
    assert (
        client.get(f"/api/v1/public/links/{forged}/schedule.json").json()
        == client.get(f"/api/v1/public/links/{expired['token']}/schedule.json").json()
    )


def test_rotate_invalidates_old_token(
    client: TestClient, auth_headers: dict[str, str], published: dict[str, Any]
) -> None:
    link = _create_link(client, auth_headers, published)
    assert client.get(f"/api/v1/public/links/{link['token']}/schedule.json").status_code == 200

    rotated = client.post(
        f"/api/v1/public-links/{link['id']}/rotate", headers=auth_headers
    )
    assert rotated.status_code == 200
    new_link = rotated.json()
    assert new_link["token"] != link["token"]
    assert new_link["token_hint"] == new_link["token"][-4:]

    assert (
        client.get(f"/api/v1/public/links/{link['token']}/schedule.json").status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/public/links/{new_link['token']}/schedule.json").status_code
        == 200
    )


def test_admin_endpoints_reject_viewer_role(
    client: TestClient, auth_headers: dict[str, str], published: dict[str, Any]
) -> None:
    created = client.post(
        "/api/v1/users",
        headers=auth_headers,
        json={"username": "public_link_viewer", "password": "viewer-pass-2026"},
    )
    assert created.status_code == 201
    login = client.post(
        "/api/v1/auth/token",
        data={"username": "public_link_viewer", "password": "viewer-pass-2026"},
    )
    assert login.status_code == 200
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    list_response = client.get(
        f"/api/v1/schedule-sets/{published['schedule_set_id']}/public-links",
        headers=viewer_headers,
    )
    assert list_response.status_code == 403
    create_response = client.post(
        f"/api/v1/schedule-sets/{published['schedule_set_id']}/public-links",
        headers=viewer_headers,
        json={
            "scope": "class",
            "campus_id": published["campus_id"],
            "resource_business_id": CLASS_BUSINESS_ID,
        },
    )
    assert create_response.status_code == 403


def test_show_teacher_names_false_hides_names_everywhere(
    client: TestClient, auth_headers: dict[str, str], published: dict[str, Any]
) -> None:
    link = _create_link(client, auth_headers, published, show_teacher_names=False)

    body = client.get(f"/api/v1/public/links/{link['token']}/schedule.json").json()
    assert body["show_teacher_names"] is False
    assert all(row["teacher_names"] == [] for row in body["rows"])
    assert TEACHER_NAME not in json.dumps(body, ensure_ascii=False)

    ics_response = client.get(f"/api/v1/public/links/{link['token']}/calendar.ics")
    assert ics_response.status_code == 200
    assert TEACHER_NAME.encode("utf-8") not in ics_response.content


def test_access_count_is_throttled(
    client: TestClient, auth_headers: dict[str, str], published: dict[str, Any]
) -> None:
    link = _create_link(client, auth_headers, published)

    first = client.get(f"/api/v1/public/links/{link['token']}/schedule.json")
    assert first.status_code == 200
    repeat = client.get(f"/api/v1/public/links/{link['token']}/schedule.json")
    assert repeat.status_code == 200
    ics_hit = client.get(f"/api/v1/public/links/{link['token']}/calendar.ics")
    assert ics_hit.status_code == 200

    with SessionLocal() as db:
        row = db.get(PublicLinkToken, link["id"])
        assert row is not None
        assert row.access_count == 1

        # 节流窗口（10 分钟）过后再次命中才累计。
        row.last_seen_at = row.last_seen_at - timedelta(minutes=11) if row.last_seen_at else None
        db.commit()

    assert client.get(f"/api/v1/public/links/{link['token']}/schedule.json").status_code == 200
    with SessionLocal() as db:
        row = db.get(PublicLinkToken, link["id"])
        assert row is not None
        assert row.access_count == 2


def test_school_scope_returns_directory_index(
    client: TestClient, auth_headers: dict[str, str], published: dict[str, Any]
) -> None:
    link = _create_link(
        client,
        auth_headers,
        published,
        scope="school",
        campus_id=None,
        resource_business_id=None,
    )
    assert link["display_name"]

    body = client.get(f"/api/v1/public/links/{link['token']}/schedule.json").json()
    assert body["scope"] == "school"
    classes = {item["class_business_id"]: item for item in body["classes"]}
    assert CLASS_BUSINESS_ID in classes
    assert classes[CLASS_BUSINESS_ID]["class_name"]
    assert classes[CLASS_BUSINESS_ID]["session_count"] >= 1
    assert "campus_id" not in json.dumps(body, ensure_ascii=False)

    ics_response = client.get(f"/api/v1/public/links/{link['token']}/calendar.ics")
    assert ics_response.status_code == 200
    calendar = Calendar.from_ical(ics_response.content)
    events = list(calendar.walk("VEVENT"))
    assert events
    # school 范围不绑定单一资源，UID 资源段为 'all'，assignment 段仍跨版本稳定。
    assert all(str(event["UID"]).startswith("tupai-school-all-") for event in events)


def test_school_link_drills_down_to_single_class(
    client: TestClient, auth_headers: dict[str, str], published: dict[str, Any]
) -> None:
    """school 目录链接下钻到单个班级：数据 = public_class_payload 同一口径。"""

    school = _create_link(
        client,
        auth_headers,
        published,
        scope="school",
        campus_id=None,
        resource_business_id=None,
    )
    drill_url = (
        f"/api/v1/public/links/{school['token']}/class/"
        f"{published['campus_id']}/{CLASS_BUSINESS_ID}/schedule.json"
    )
    response = client.get(drill_url)
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "class"
    assert body["display_name"]
    assert body["version_no"] == published["current_version_no"]
    assert len(body["rows"]) == 2
    serialized = _leak_scan(body)
    assert "T01" not in serialized  # 教师工号不因下钻外泄

    # 仅 school scope 有效：class/teacher 链接本就绑定单一资源，scope 不符
    # 与伪造 token 一样同形 404。
    class_link = _create_link(client, auth_headers, published)
    wrong_scope = (
        f"/api/v1/public/links/{class_link['token']}/class/"
        f"{published['campus_id']}/{CLASS_BUSINESS_ID}/schedule.json"
    )
    forged = (
        f"/api/v1/public/links/{'a' * 43}/class/"
        f"{published['campus_id']}/{CLASS_BUSINESS_ID}/schedule.json"
    )
    assert client.get(wrong_scope).status_code == 404
    assert client.get(forged).status_code == 404
    assert client.get(wrong_scope).json() == client.get(forged).json()

    # 节流计数沿用现有公开端点模式：10 分钟内重复拉取只记一次。
    client.get(drill_url)
    with SessionLocal() as db:
        row = db.get(PublicLinkToken, school["id"])
        assert row is not None
        assert row.access_count == 1


def test_teacher_scope_payload_only_exposes_names(
    client: TestClient, auth_headers: dict[str, str], published: dict[str, Any]
) -> None:
    link = _create_link(
        client,
        auth_headers,
        published,
        scope="teacher",
        resource_business_id=TEACHER_BUSINESS_ID,
    )
    body = client.get(f"/api/v1/public/links/{link['token']}/schedule.json").json()
    assert body["scope"] == "teacher"
    assert body["display_name"] == TEACHER_NAME
    assert body["rows"]
    assert all(row["teacher_names"] == [TEACHER_NAME] for row in body["rows"])
    serialized = _leak_scan(body)
    assert TEACHER_BUSINESS_ID not in serialized


def _make_three_class_version() -> dict[str, Any]:
    """播种覆盖 3 个班级的当前发布版本，供批量生成链接用例使用。

    必须是本文件的最后一个播种动作（用例置于文件末尾）：它会把该方案的
    「当前发布版本」切换为只覆盖 B01/B02/B03 的新版本。
    """

    with SessionLocal() as db:
        picked: list[tuple[str, str]] = []
        campus_id = ""
        schedule_set_id = ""
        for class_business_id in ("B01", "B02", "B03"):
            course = db.scalar(
                select(CourseSession)
                .where(
                    CourseSession.class_business_id == class_business_id,
                    CourseSession.is_active.is_(True),
                )
                .order_by(CourseSession.business_id)
            )
            assert course is not None, f"种子主数据缺少 {class_business_id} 课次"
            picked.append((course.id, class_business_id))
            campus_id = course.campus_id
            schedule_set_id = course.schedule_set_id

        next_version_no = int(
            db.scalar(
                select(func.max(ScheduleVersion.version_no)).where(
                    ScheduleVersion.schedule_set_id == schedule_set_id
                )
            )
            or 0
        )
        snapshot = DataSnapshot(
            schedule_set_id=schedule_set_id,
            revision=20_000 + next_version_no,
            checksum=f"public-link-batch-fixture-{next_version_no}",
            payload={},
        )
        db.add(snapshot)
        db.flush()
        solver_run = SolverRun(
            schedule_set_id=schedule_set_id,
            snapshot_id=snapshot.id,
            status="completed",
            request_payload={},
            result_payload={},
        )
        db.add(solver_run)
        db.flush()
        version = ScheduleVersion(
            schedule_set_id=schedule_set_id,
            version_no=next_version_no + 1,
            name=f"公开链接批量夹具 V{next_version_no + 1}",
            status="published",
            solver_run_id=solver_run.id,
            metrics={},
            published_at=datetime.now(UTC),
        )
        db.add(version)
        db.flush()
        for course_id, _class_business_id in picked:
            db.add(
                ScheduleAssignment(
                    schedule_version_id=version.id,
                    course_session_id=course_id,
                    lesson_date=date(2026, 12, 1),
                    slot_business_id="S01",
                    room_business_id="R01",
                    change_kind="assigned",
                )
            )
        db.commit()
        return {
            "schedule_set_id": schedule_set_id,
            "campus_id": campus_id,
            "version_no": version.version_no,
        }


def test_batch_creates_links_for_every_class_in_published_version(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """按发布版本批量生成：3 班级 → 3 条成功，明文各出现一次，库内只有哈希。"""

    info = _make_three_class_version()
    response = client.post(
        f"/api/v1/schedule-sets/{info['schedule_set_id']}/public-links/batch",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["failed"] == []

    created = body["created"]
    assert len(created) == 3
    tokens = [item["token"] for item in created]
    assert len(set(tokens)) == 3
    assert {item["display_name"] for item in created} == {
        "初一数学A",
        "初一英语A",
        "初二物理A",
    }
    # 明文只在本响应出现：每个 token 仅出现在自己的 url 与 token 字段里
    # （各 1 次，共 2 次），库内此后只有哈希（下方断言）。
    raw = response.text
    for item in created:
        assert raw.count(item["token"]) == 2
        assert item["url"].endswith(f"/public/t/{item['token']}")

    # 数据库只有哈希：token_hash = sha256(明文)，token_hint = 末 4 位。
    with SessionLocal() as db:
        for token in tokens:
            row = db.scalar(
                select(PublicLinkToken).where(
                    PublicLinkToken.token_hash
                    == hashlib.sha256(token.encode("utf-8")).hexdigest()
                )
            )
            assert row is not None
            assert row.scope == "class"
            assert row.token_hint == token[-4:]
            assert token != row.token_hash
            assert token not in repr(row.__dict__)

    # 每个明文链接立即可用，且绑定对应班级。
    for item in created:
        payload = client.get(f"/api/v1/public/links/{item['token']}/schedule.json")
        assert payload.status_code == 200
        assert payload.json()["scope"] == "class"
        assert payload.json()["display_name"] == item["display_name"]
