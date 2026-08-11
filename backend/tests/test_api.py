from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook


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
    assert {"教师", "班级", "教室", "时段", "课程需求"}.issubset(workbook.sheetnames)


def test_sample_solve_publish_and_xlsx(client: TestClient, auth_headers: dict[str, str]) -> None:
    run = solve(client, auth_headers)
    assert run["model_status"] in {"OPTIMAL", "FEASIBLE"}
    schedules = client.get("/api/v1/schedules", headers=auth_headers).json()
    schedule = schedules[0]
    assert len(schedule["assignments"]) == 24
    assert schedule["metrics"]["hard_conflicts"] == 0
    assert schedule["metrics"]["seat_utilization"] >= 0.65

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
