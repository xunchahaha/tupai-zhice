from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Any

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select

from app.db import SessionLocal
from app.models import CourseSession, ScheduleAssignment, SolverRun, Teacher


def solve(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post(
        "/api/v1/solver-runs",
        headers=headers,
        json={
            "wait": True,
            "time_limit_seconds": 10,
            "preference_weight": 100,
            "seat_waste_weight": 1,
            "change_weight": 100000,
        },
    )
    assert response.status_code == 202
    return response.json()


def test_seeded_overview(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/overview", headers=auth_headers)
    assert response.status_code == 200
    counts = response.json()["counts"]
    assert counts["teachers"] == 6
    assert counts["class_groups"] == 12
    assert counts["rooms"] == 6
    assert counts["time_slots"] == 10
    assert counts["course_sessions"] == 24


def test_download_master_data_sample(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/imports/sample.xlsx", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    workbook = load_workbook(BytesIO(response.content), read_only=True, data_only=True)
    assert {"使用说明", "课表数据源"}.issubset(workbook.sheetnames)


def test_sample_solve_publish_and_xlsx(client: TestClient, auth_headers: dict[str, str]) -> None:
    run = solve(client, auth_headers)
    assert run["model_status"] in {"OPTIMAL", "FEASIBLE"}
    schedules = client.get("/api/v1/schedules", headers=auth_headers).json()
    schedule = schedules[0]
    assert len(schedule["assignments"]) == 24
    assert schedule["metrics"]["hard_conflicts"] == 0
    assert schedule["metrics"]["room_slot_occupancy"] >= 0

    published = client.post(f"/api/v1/schedules/{schedule['id']}/publish", headers=auth_headers)
    assert published.status_code == 200
    assert published.json()["status"] == "published"

    exported = client.get(f"/api/v1/schedules/{schedule['id']}/export.xlsx", headers=auth_headers)
    assert exported.status_code == 200
    workbook = load_workbook(BytesIO(exported.content), read_only=True, data_only=True)
    assert workbook.sheetnames == ["课表", "指标"]
    assert workbook["课表"].max_row == 25


def test_infeasible_rules_return_traceable_core(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    rule_ids: list[str] = []
    for business_id, course_id in [("TEST-FIX-1", "B01-1"), ("TEST-FIX-2", "B07-1")]:
        created = client.post(
            "/api/v1/rules",
            headers=auth_headers,
            json={
                "business_id": business_id,
                "source_text": f"{course_id} 固定到 S01",
                "actor_type": "course",
                "actor_ids": [course_id],
                "constraint_type": "fixed_slot",
                "scope": {"slot_id": "S01"},
                "hardness": "hard",
                "status": "awaiting_confirmation",
            },
        )
        assert created.status_code == 201
        rule_id = created.json()["id"]
        rule_ids.append(rule_id)
        activated = client.post(
            f"/api/v1/rules/{rule_id}/transition",
            headers=auth_headers,
            json={"status": "active"},
        )
        assert activated.status_code == 200

    run = solve(client, auth_headers)
    assert run["model_status"] == "INFEASIBLE"
    assert set(run["conflict_rule_ids"]) == {"TEST-FIX-1", "TEST-FIX-2"}
    assert run["priority_rule_ids"] == ["TEST-FIX-1", "TEST-FIX-2"]
    assert run["priority_explanations"]

    for rule_id in rule_ids:
        retired = client.post(
            f"/api/v1/rules/{rule_id}/transition",
            headers=auth_headers,
            json={"status": "retired"},
        )
        assert retired.status_code == 200


def test_feishu_requires_production_configuration(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    connection = client.get("/api/v1/integrations/feishu/connection", headers=auth_headers)
    assert connection.status_code == 200
    payload = connection.json()
    assert payload["status"] == "unconfigured"
    assert payload["app_configured"] is False
    assert "应用编号" in payload["missing_fields"]
    assert payload["app_configuration"]["source"] == "none"
    assert payload["console_url"].startswith("https://open.feishu.cn/")
    sync = client.post(
        "/api/v1/integrations/feishu/sync",
        headers=auth_headers,
        json={"direction": "export", "resource": "teachers"},
    )
    assert sync.status_code == 409
    assert "填写飞书应用编号和应用密钥" in sync.json()["detail"]


def test_aily_context_proposal_confirmation_and_solve(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    aily_headers = {"X-Aily-Key": "aily-demo-key"}
    context = client.get("/api/v1/aily/context", headers=aily_headers)
    assert context.status_code == 200
    assert len(context.json()["entities"]["teachers"]) == 6
    assert {item["type"] for item in context.json()["constraint_catalog"]} >= {
        "unavailable_slot",
        "consecutive_sessions",
    }

    response = client.post(
        "/api/v1/aily/rule-proposals",
        headers=aily_headers,
        json={
            "source_text": "教师甲周三不排晚课，初三冲刺班尽量连续两节；机房周四晚间检修。",
            "source_doc": "Aily 端到端基准句",
            "proposals": [
                {
                    "source_text": "教师甲周三不排晚课",
                    "actor_type": "teacher",
                    "actor_ids": ["T01"],
                    "constraint_type": "unavailable_slot",
                    "scope": {"slot_ids": ["S05", "S06"]},
                    "hardness": "hard",
                    "confidence": 0.98,
                },
                {
                    "source_text": "初三冲刺班尽量连续两节",
                    "actor_type": "class",
                    "actor_ids": ["B04", "B05", "B10"],
                    "constraint_type": "consecutive_sessions",
                    "scope": {"minimum_consecutive": 2},
                    "hardness": "soft",
                    "weight": 50,
                    "confidence": 0.88,
                },
                {
                    "source_text": "机房周四晚间检修",
                    "actor_type": "room",
                    "actor_ids": ["R03"],
                    "constraint_type": "unavailable_slot",
                    "scope": {"slot_ids": ["S07", "S08"]},
                    "hardness": "hard",
                    "confidence": 0.99,
                },
            ],
        },
    )
    assert response.status_code == 200
    proposals = response.json()
    assert len(proposals) == 3
    assert {item["status"] for item in proposals} == {"awaiting_confirmation"}

    for proposal in proposals:
        transition = client.post(
            f"/api/v1/rules/{proposal['id']}/transition",
            headers=auth_headers,
            json={"status": "active", "reason": "基准句人工确认"},
        )
        assert transition.status_code == 200

    run = solve(client, auth_headers)
    assert run["model_status"] in {"OPTIMAL", "FEASIBLE"}

    for proposal in proposals:
        retired = client.post(
            f"/api/v1/rules/{proposal['id']}/transition",
            headers=auth_headers,
            json={"status": "retired", "reason": "测试清理"},
        )
        assert retired.status_code == 200


def test_product_loop_calendar_assistant_export_and_public_summary(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    run = solve(client, auth_headers)
    schedules = client.get("/api/v1/schedules", headers=auth_headers).json()
    schedule = next(item for item in schedules if item["solver_run_id"] == run["id"])
    published = client.post(f"/api/v1/schedules/{schedule['id']}/publish", headers=auth_headers)
    assert published.status_code == 200

    with SessionLocal() as db:
        assignment = db.scalar(
            select(ScheduleAssignment).where(
                ScheduleAssignment.schedule_version_id == schedule["id"]
            )
        )
        assert assignment is not None
        course = db.get(CourseSession, assignment.course_session_id)
        assert course is not None
        teacher = db.scalar(
            select(Teacher).where(Teacher.business_id == course.teacher_business_id)
        )
        assert teacher is not None
        assignment.lesson_date = date(2026, 9, 5)
        course.business_line = "内部业务线"
        course.product_type = "内部产品班型"
        course.fixed_start_time = "09:00"
        course.fixed_end_time = "12:00"
        course.original_room_business_id = assignment.room_business_id
        course.calendar_user_id = None
        teacher.calendar_user_id = "ou_calendar_teacher"
        db.commit()

    exported = client.get(f"/api/v1/schedules/{schedule['id']}/export.xlsx", headers=auth_headers)
    workbook = load_workbook(BytesIO(exported.content), read_only=True, data_only=True)
    headers = [cell.value for cell in next(workbook["课表"].iter_rows(max_row=1))]
    assert {
        "上课日期",
        "业务线",
        "产品班型",
        "固定开始时间",
        "固定结束时间",
        "原始教室ID",
        "最终教室ID",
        "具体日程账号",
    } <= set(headers)

    context = client.get("/api/v1/aily/context", headers={"X-Aily-Key": "aily-demo-key"})
    mapped = [
        item
        for item in context.json()["entities"]["courses"]
        if item["calendar_mapping_status"] == "mapped"
    ]
    assert mapped and mapped[0]["fixed_start_time"]

    monkeypatch.setattr("app.api.enqueue_solver_run", lambda run_id: None)
    monkeypatch.setattr("app.api.settings.aily_app_id", "spring_test")
    monkeypatch.setattr("app.api.settings.aily_skill_id", "skill_test")
    monkeypatch.setattr(
        "app.api.FeishuService.start_aily_skill",
        lambda *args, **kwargs: {
            "business_lines": ["内部业务线"],
            "product_types": [],
            "class_business_ids": [],
            "date_from": None,
            "date_to": None,
            "date_window_days": 2,
            "recognized_rules": [
                "固定时段不可调整",
                "同一教室真实时间区间不可重叠",
                "优先最小化日期和教室变更",
            ],
        },
    )
    interpreted = client.post(
        "/api/v1/assistant/interpret",
        headers=auth_headers,
        json={
            "instruction": "内部业务线在固定时段不变的前提下尽量不变，日期范围 2 天"
        },
    )
    assert interpreted.status_code == 200
    assert interpreted.json()["source"] == "feishu_aily"
    assert interpreted.json()["business_lines"] == ["内部业务线"]
    assert interpreted.json()["date_window_days"] == 2
    assert "固定时段不可调整" in interpreted.json()["recognized_rules"]
    assert "fixed_time" in interpreted.json()["solver_rules"]
    assistant = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "仅重排内部业务线，日期前后允许移动两天",
            "business_lines": ["内部业务线"],
            "date_window_days": 2,
        },
    )
    assert assistant.status_code == 202
    with SessionLocal() as db:
        assistant_run = db.get(SolverRun, assistant.json()["id"])
        assert assistant_run is not None
        assert assistant_run.request_payload["instruction"].startswith("仅重排")
        assert assistant_run.request_payload["assistant_entry"] is True
        assert "room_no_overlap" in assistant_run.request_payload["solver_rules"]
        assert assistant_run.request_payload["parent_schedule_id"] == schedule["id"]
        assert len(assistant_run.request_payload["previous_assignments"]) == len(
            schedule["assignments"]
        )

    def freebusy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert len(kwargs["user_ids"]) <= 10
        return {
            "freebusy_lists": [
                {
                    "user_id": "ou_calendar_teacher",
                    "freebusy_list": [
                        {
                            "start_time": "2026-09-05T10:00:00+08:00",
                            "end_time": "2026-09-05T11:00:00+08:00",
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr("app.api.FeishuService.batch_freebusy", freebusy)
    monkeypatch.setattr(
        "app.api.FeishuService.create_calendar_event",
        lambda *args, **kwargs: {"event": {"event_id": "event-test-1"}},
    )
    monkeypatch.setattr(
        "app.api.FeishuService.add_event_attendee", lambda *args, **kwargs: {"attendees": []}
    )
    dry_run = client.post(
        f"/api/v1/schedules/{schedule['id']}/calendar-publish",
        headers=auth_headers,
        json={"dry_run": True},
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["published"] == 0
    assert dry_run.json()["would_publish"] == 1
    assert dry_run.json()["conflict_count"] == 1

    created = client.post(
        f"/api/v1/schedules/{schedule['id']}/calendar-publish",
        headers=auth_headers,
        json={},
    )
    assert created.status_code == 200
    assert created.json()["published"] == 1
    assert created.json()["conflict_count"] == 1
    bindings = client.get(
        f"/api/v1/schedules/{schedule['id']}/calendar-bindings", headers=auth_headers
    ).json()
    assert bindings[0]["status"] == "published_with_conflict"
    repeated = client.post(
        f"/api/v1/schedules/{schedule['id']}/calendar-publish",
        headers=auth_headers,
        json={},
    )
    assert repeated.json()["published"] == 0
    assert repeated.json()["existing"] == 1

    from app.api import export_resource_rows

    with SessionLocal() as db:
        public_rows = export_resource_rows(db, "public_summary")
    assert any(item["指标名称"] == "总课次" for item in public_rows)
    assert "内部业务线" not in str(public_rows)
    assert "内部产品班型" not in str(public_rows)


def test_assistant_rejects_unknown_scope(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "仅重排不存在的业务线",
            "business_lines": ["不存在的业务线"],
        },
    )
    assert response.status_code == 422
    assert "未知业务实体" in response.text
