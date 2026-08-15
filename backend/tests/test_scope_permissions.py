from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/token", data={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _schedule_headers(token: str, schedule_set_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Schedule-Set-Id": schedule_set_id,
    }


def _rule_payload(suffix: str) -> dict[str, object]:
    return {
        "source_text": f"{suffix} 的系统规则",
        "actor_type": "system",
        "actor_ids": [],
        "constraint_type": "declared_constraint",
        "scope": {},
        "hardness": "hard",
    }


def test_role_boundaries_keep_approvers_out_of_scheduling_and_master_data(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    suffix = uuid4().hex[:8]
    schedule_set = client.post(
        "/api/v1/schedule-sets",
        headers=auth_headers,
        json={"name": f"权限边界-{suffix}"},
    )
    assert schedule_set.status_code == 201, schedule_set.text
    schedule_set_id = schedule_set.json()["id"]

    users: dict[str, str] = {}
    for role in ("scheduler", "approver"):
        username = f"{role}_{suffix}"
        created = client.post(
            "/api/v1/users",
            headers=auth_headers,
            json={"username": username, "password": "scope-policy-2026", "role": role},
        )
        assert created.status_code == 201, created.text
        users[role] = created.json()["id"]

    scheduler_grant = client.put(
        f"/api/v1/schedule-sets/{schedule_set_id}/members/{users['scheduler']}",
        headers=auth_headers,
        json={"user_id": users["scheduler"], "access_role": "scheduler"},
    )
    assert scheduler_grant.status_code == 200, scheduler_grant.text

    forbidden_approver_grant = client.put(
        f"/api/v1/schedule-sets/{schedule_set_id}/members/{users['approver']}",
        headers=auth_headers,
        json={"user_id": users["approver"], "access_role": "scheduler"},
    )
    assert forbidden_approver_grant.status_code == 422
    approver_grant = client.put(
        f"/api/v1/schedule-sets/{schedule_set_id}/members/{users['approver']}",
        headers=auth_headers,
        json={"user_id": users["approver"], "access_role": "approver"},
    )
    assert approver_grant.status_code == 200, approver_grant.text

    scheduler_token = _login(client, f"scheduler_{suffix}", "scope-policy-2026")
    approver_token = _login(client, f"approver_{suffix}", "scope-policy-2026")
    scheduler_headers = _schedule_headers(scheduler_token, schedule_set_id)
    approver_headers = _schedule_headers(approver_token, schedule_set_id)

    campus = client.get("/api/v1/campuses", headers=scheduler_headers).json()[0]
    master_write = client.post(
        "/api/v1/rooms",
        headers=scheduler_headers,
        json={
            "campus_id": campus["id"],
            "business_id": f"SCHEDULER-ROOM-{suffix}",
            "name": "排课员不可维护的教室",
            "is_active": True,
        },
    )
    assert master_write.status_code == 403

    scheduler_rule = client.post(
        "/api/v1/rules", headers=scheduler_headers, json=_rule_payload(suffix)
    )
    assert scheduler_rule.status_code == 201, scheduler_rule.text
    monkeypatch.setattr("app.api.enqueue_solver_run", lambda _run_id: None)
    scheduler_run = client.post("/api/v1/solver-runs", headers=scheduler_headers, json={})
    assert scheduler_run.status_code == 202, scheduler_run.text

    approver_rule = client.post(
        "/api/v1/rules", headers=approver_headers, json=_rule_payload(suffix)
    )
    assert approver_rule.status_code == 403
    assert client.post("/api/v1/solver-runs", headers=approver_headers, json={}).status_code == 403
    assert (
        client.post(
            "/api/v1/integrations/feishu/sync",
            headers=approver_headers,
            json={"resource": "teachers"},
        ).status_code
        == 403
    )

    promoted_to_approver = client.patch(
        f"/api/v1/users/{users['scheduler']}/role",
        headers=auth_headers,
        json={"role": "approver"},
    )
    assert promoted_to_approver.status_code == 200, promoted_to_approver.text
    members = client.get(
        f"/api/v1/schedule-sets/{schedule_set_id}/members", headers=auth_headers
    )
    updated_scheduler = next(
        item for item in members.json() if item["user_id"] == users["scheduler"]
    )
    assert updated_scheduler["access_role"] == "approver"


def test_aily_calls_write_rules_and_runs_to_the_selected_schedule_set(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    suffix = uuid4().hex[:8]
    schedule_set = client.post(
        "/api/v1/schedule-sets",
        headers=auth_headers,
        json={"name": f"Aily 方案-{suffix}"},
    )
    assert schedule_set.status_code == 201, schedule_set.text
    schedule_set_id = schedule_set.json()["id"]
    aily_headers = {
        "X-Aily-Key": "test-aily-key-not-the-repo-default",
        "X-Schedule-Set-Id": schedule_set_id,
    }

    context = client.get("/api/v1/aily/context", headers=aily_headers)
    assert context.status_code == 200, context.text
    assert context.json()["schedule_set_id"] == schedule_set_id
    assert client.get(
        "/api/v1/aily/context", headers={"X-Aily-Key": aily_headers["X-Aily-Key"]}
    ).status_code == 409

    proposal = client.post(
        "/api/v1/aily/rule-proposals",
        headers=aily_headers,
        json={"source_text": f"{suffix} Aily 规则", "proposals": [_rule_payload(suffix)]},
    )
    assert proposal.status_code == 200, proposal.text
    proposal_id = proposal.json()[0]["id"]
    schedule_headers = {**auth_headers, "X-Schedule-Set-Id": schedule_set_id}
    scoped_rules = client.get("/api/v1/rules", headers=schedule_headers)
    assert scoped_rules.status_code == 200
    assert [item["id"] for item in scoped_rules.json()] == [proposal_id]

    monkeypatch.setattr("app.api.enqueue_solver_run", lambda _run_id: None)
    run = client.post("/api/v1/aily/solve", headers=aily_headers, json={})
    assert run.status_code == 202, run.text
    scoped_run = client.get(f"/api/v1/solver-runs/{run.json()['id']}", headers=schedule_headers)
    assert scoped_run.status_code == 200
    assert (
        client.get(
            f"/api/v1/solver-runs/{run.json()['id']}",
            headers={**auth_headers, "X-Schedule-Set-Id": "default"},
        ).status_code
        == 404
    )


def test_admin_downgrade_restores_default_schedule_membership(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    suffix = uuid4().hex[:8]
    created = client.post(
        "/api/v1/users",
        headers=auth_headers,
        json={
            "username": f"downgrade_admin_{suffix}",
            "password": "downgrade-policy-2026",
            "role": "admin",
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]

    members_before = client.get("/api/v1/schedule-sets/default/members", headers=auth_headers)
    assert members_before.status_code == 200
    assert all(item["user_id"] != user_id for item in members_before.json())

    downgraded = client.patch(
        f"/api/v1/users/{user_id}/role",
        headers=auth_headers,
        json={"role": "scheduler"},
    )
    assert downgraded.status_code == 200, downgraded.text

    members_after = client.get("/api/v1/schedule-sets/default/members", headers=auth_headers)
    membership = next(item for item in members_after.json() if item["user_id"] == user_id)
    assert membership["is_active"] is True
    assert membership["access_role"] == "scheduler"

    token = _login(client, f"downgrade_admin_{suffix}", "downgrade-policy-2026")
    visible = client.get("/api/v1/schedule-sets", headers={"Authorization": f"Bearer {token}"})
    assert visible.status_code == 200
    assert [item["id"] for item in visible.json()] == ["default"]
