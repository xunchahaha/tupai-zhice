from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

import app.api as api_module
from app.services.feishu import FeishuService, FeishuServiceError


def test_batch_sync_telemetry_includes_preflight_and_promotes_substep_failures(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    events: list[str] = []
    clock_values = iter((10.0, 12.0, 20.0, 21.0, 30.0, 31.5))

    def perf_counter() -> float:
        events.append("clock")
        return next(clock_values)

    monkeypatch.setattr(
        api_module,
        "time_module",
        SimpleNamespace(perf_counter=perf_counter),
    )

    def prepare_sync_resources(self: FeishuService, *_args: Any, **_kwargs: Any) -> dict:
        events.append("preflight")
        self._request_retry_count += 2
        return {}

    retry_increments = {"teachers": 1, "schedule": 2, "public_summary": 3}

    def sync_rows(
        self: FeishuService,
        _user_id: str,
        resource: str,
        _rows: list[dict[str, Any]],
        _workspace_id: str | None = None,
        _schedule_set_id: str = "default",
    ) -> dict[str, Any]:
        events.append(f"sync:{resource}")
        self._request_retry_count += retry_increments[resource]
        result: dict[str, Any] = {
            "records_read": 4,
            "records_written": 3,
            # API orchestration must replace this method-local value with the
            # checkpoint delta that also includes batch preflight.
            "retry_count": 999,
        }
        if resource == "schedule":
            result["view_sync"] = {
                "status": "failed",
                "error": "班级视图接口超时",
            }
        if resource == "public_summary":
            result["duplicate_cleanup"] = {
                "status": "failed",
                "error": "仍有 2 条重复记录未删除",
            }
        return result

    monkeypatch.setattr(FeishuService, "prepare_sync_resources", prepare_sync_resources)
    monkeypatch.setattr(FeishuService, "sync_rows", sync_rows)

    response = client.post(
        "/api/v1/integrations/feishu/sync-batch",
        headers=auth_headers,
        json={"resources": ["teachers", "schedule", "public_summary"]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "partial"
    assert payload["completed_count"] == 1
    assert payload["failed_count"] == 2

    by_resource = {item["resource"]: item for item in payload["results"]}
    teachers = by_resource["teachers"]
    assert teachers["status"] == "completed"
    assert teachers["detail"]["duration_ms"] == 2000
    assert teachers["detail"]["retry_count"] == 3

    schedule = by_resource["schedule"]
    assert schedule["status"] == "failed"
    assert schedule["detail"]["duration_ms"] == 1000
    assert schedule["detail"]["retry_count"] == 2
    assert "班级视图同步失败" in schedule["detail"]["error"]

    public_summary = by_resource["public_summary"]
    assert public_summary["status"] == "failed"
    assert public_summary["detail"]["duration_ms"] == 1500
    assert public_summary["detail"]["retry_count"] == 3
    assert "重复记录清理失败" in public_summary["detail"]["error"]

    # The batch timer/checkpoint starts before preflight, while shared
    # preflight latency/retries are attributed exactly once to the first row.
    assert events[:2] == ["clock", "preflight"]
    assert sum(item["detail"]["retry_count"] for item in payload["results"]) == 8


def test_single_sync_preflight_failure_persists_retry_count_and_reauthorization(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    events: list[str] = []
    clock_values = iter((5.0, 7.5))

    def perf_counter() -> float:
        events.append("clock")
        return next(clock_values)

    monkeypatch.setattr(
        api_module,
        "time_module",
        SimpleNamespace(perf_counter=perf_counter),
    )

    def prepare_sync_resources(self: FeishuService, *_args: Any, **_kwargs: Any) -> dict:
        events.append("preflight")
        self._request_retry_count += 2
        raise FeishuServiceError(
            "当前用户令牌需要重新授权",
            reauthorization_required=True,
        )

    monkeypatch.setattr(FeishuService, "prepare_sync_resources", prepare_sync_resources)
    monkeypatch.setattr(
        FeishuService,
        "sync_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("preflight failure must stop this resource before row sync")
        ),
    )

    response = client.post(
        "/api/v1/integrations/feishu/sync",
        headers=auth_headers,
        json={"resource": "teachers"},
    )
    assert response.status_code == 409, response.text
    assert events[:2] == ["clock", "preflight"]

    history = client.get("/api/v1/integrations/feishu/syncs", headers=auth_headers)
    assert history.status_code == 200, history.text
    failed = next(
        item
        for item in history.json()
        if item["resource"] == "teachers"
        and item["detail"].get("trigger") == "single_resource"
    )
    assert failed["status"] == "failed"
    assert failed["detail"]["duration_ms"] == 2500
    assert failed["detail"]["retry_count"] == 2
    assert failed["detail"]["reauthorization_required"] is True


def test_batch_preflight_exception_isolated_to_durable_resource_results(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    clock_values = iter((1.0, 4.0, 10.0, 10.5))
    monkeypatch.setattr(
        api_module,
        "time_module",
        SimpleNamespace(perf_counter=lambda: next(clock_values)),
    )

    def prepare_sync_resources(self: FeishuService, *_args: Any, **_kwargs: Any) -> dict:
        self._request_retry_count += 2
        raise RuntimeError("预检元数据响应格式异常")

    monkeypatch.setattr(FeishuService, "prepare_sync_resources", prepare_sync_resources)
    monkeypatch.setattr(
        FeishuService,
        "sync_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a failed shared preflight must prevent every row sync")
        ),
    )

    response = client.post(
        "/api/v1/integrations/feishu/sync-batch",
        headers=auth_headers,
        json={"resources": ["teachers", "rooms"]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["completed_count"] == 0
    assert payload["failed_count"] == 2

    by_resource = {item["resource"]: item for item in payload["results"]}
    assert by_resource["teachers"]["detail"]["duration_ms"] == 3000
    assert by_resource["teachers"]["detail"]["retry_count"] == 2
    assert by_resource["rooms"]["detail"]["duration_ms"] == 500
    assert by_resource["rooms"]["detail"]["retry_count"] == 0
    assert all(
        item["detail"]["error"] == "预检元数据响应格式异常"
        for item in payload["results"]
    )
