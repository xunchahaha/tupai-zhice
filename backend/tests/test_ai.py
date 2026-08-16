from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.api import settings
from app.db import SessionLocal
from app.models import AIProviderConfiguration, SolverRun
from app.services.ai import AISecretCipher, AIService, AIServiceError


def test_admin_configures_ai_provider_and_secret_is_encrypted(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    encryption_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(settings, "ai_base_url", "")
    monkeypatch.setattr(settings, "ai_api_key", "")
    monkeypatch.setattr(settings, "ai_model", "")
    monkeypatch.setattr(settings, "ai_token_encryption_key", encryption_key)
    configured = client.post(
        "/api/v1/integrations/ai/configuration",
        headers=auth_headers,
        json={
            "provider": "openai_compatible",
            "base_url": "https://model.example/v1",
            "api_key": "secret-ai-key-value",
            "model": "scheduling-model",
        },
    )
    assert configured.status_code == 200, configured.text
    assert configured.json() == {
        "configured": True,
        "source": "frontend",
        "provider": "openai_compatible",
        "base_url": "https://model.example/v1",
        "api_key_configured": True,
        "model": "scheduling-model",
    }
    assert "secret-ai-key-value" not in configured.text
    with SessionLocal() as db:
        stored = db.get(AIProviderConfiguration, "default")
        assert stored is not None
        assert stored.api_key_encrypted != "secret-ai-key-value"
        assert AISecretCipher(encryption_key).decrypt(stored.api_key_encrypted) == (
            "secret-ai-key-value"
        )
        db.delete(stored)
        db.commit()


def test_ai_service_sends_business_context_and_parses_json(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "ai_base_url", "https://model.example/v1")
    monkeypatch.setattr(settings, "ai_api_key", "environment-ai-key")
    monkeypatch.setattr(settings, "ai_model", "scheduling-model")

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        assert url == "https://model.example/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer environment-ai-key"
        request_body = kwargs["json"]
        assert request_body["model"] == "scheduling-model"
        assert "当前日期为" in request_body["messages"][0]["content"]
        assert "考研" in request_body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "business_lines": ["考研"],
                                    "product_types": [],
                                    "class_business_ids": [],
                                    "date_from": "2026-08-17",
                                    "date_to": "2026-08-23",
                                    "date_window_days": 2,
                                    "recognized_rules": ["固定时段不可调整"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.services.ai.httpx.post", fake_post)
    with SessionLocal() as db:
        parsed = AIService(settings, db).interpret_instruction(
            "下周重排考研课程",
            context={
                "business_lines": ["考研", "公职"],
                "product_types": [],
                "class_business_ids": [],
                "fixed_rule_labels": ["固定时段不可调整"],
            },
        )
    assert parsed["business_lines"] == ["考研"]
    assert parsed["date_from"] == "2026-08-17"


def test_ai_service_parses_fenced_json_and_typed_content(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "ai_base_url", "https://model.example/v1")
    monkeypatch.setattr(settings, "ai_api_key", "environment-ai-key")
    monkeypatch.setattr(settings, "ai_model", "scheduling-model")

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "<think>先分析规则</think>\n"},
                                {
                                    "type": "text",
                                    "text": '```json\n{"business_lines":["考研"]}\n```\n已完成。',
                                },
                            ]
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.services.ai.httpx.post", fake_post)
    with SessionLocal() as db:
        parsed = AIService(settings, db).interpret_instruction(
            "解析考研课程",
            context={"business_lines": ["考研"]},
        )
    assert parsed == {"business_lines": ["考研"]}


def test_ai_service_explains_when_console_url_returns_html(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "ai_base_url", "https://model.example/console/v1")
    monkeypatch.setattr(settings, "ai_api_key", "environment-ai-key")
    monkeypatch.setattr(settings, "ai_model", "scheduling-model")

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200,
            text="<!doctype html><html><body>管理控制台</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.services.ai.httpx.post", fake_post)
    with SessionLocal() as db:
        with pytest.raises(AIServiceError, match="不要填写管理控制台地址"):
            AIService(settings, db).interpret_instruction(
                "解析考研课程",
                context={"business_lines": ["考研"]},
            )


def test_assistant_interpret_uses_configured_ai_provider(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(settings, "ai_base_url", "https://model.example/v1")
    monkeypatch.setattr(settings, "ai_api_key", "environment-ai-key")
    monkeypatch.setattr(settings, "ai_model", "scheduling-model")
    monkeypatch.setattr(
        "app.api.AIService.interpret_instruction",
        lambda *args, **kwargs: {
            "business_lines": [],
            "product_types": [],
            "class_business_ids": [],
            "date_from": None,
            "date_to": None,
            "date_window_days": 3,
            "recognized_rules": [
                "固定时段不可调整",
                "同一教室真实时间区间不可重叠",
            ],
        },
    )
    interpreted = client.post(
        "/api/v1/assistant/interpret",
        headers=auth_headers,
        json={"instruction": "固定时段不变，所有课程允许前后调整三天"},
    )
    assert interpreted.status_code == 200, interpreted.text
    payload = interpreted.json()
    assert payload["source"] == "openai_compatible"
    assert payload["ai_configured"] is True
    assert payload["date_window_days"] == 3
    assert "fixed_time" in payload["solver_rules"]


def _solve_once(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/v1/solver-runs",
        headers=headers,
        json={"wait": True, "time_limit_seconds": 10, "change_weight": 100000},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_explanation_falls_back_to_deterministic_text_without_ai(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    """AI 不可用时仍要给出人话解释：SYSTEM-* 的翻译本来就是代码能做完的部分。"""
    monkeypatch.setattr(settings, "ai_base_url", "https://model.example/v1")
    monkeypatch.setattr(settings, "ai_api_key", "environment-ai-key")
    monkeypatch.setattr(settings, "ai_model", "scheduling-model")

    def unreachable(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("模型服务不可达", request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.ai.httpx.post", unreachable)
    run = _solve_once(client, auth_headers)
    response = client.post(
        f"/api/v1/solver-runs/{run['id']}/explanation",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"] == "deterministic"
    assert payload["ai_error"]
    assert payload["headline"]
    assert payload["explanation"]


def test_explanation_translates_builtin_conflict_ids(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    from app.services.explain import SYSTEM_RULE_GLOSSARY, describe_conflict_rule

    with SessionLocal() as db:
        described = describe_conflict_rule(db, "SYSTEM-CLASS-NO-OVERLAP")
    assert described["origin"] == "solver_builtin"
    assert described["meaning"] == SYSTEM_RULE_GLOSSARY["SYSTEM-CLASS-NO-OVERLAP"]
    assert "SYSTEM" not in described["meaning"]


def test_explanation_uses_ai_wording_and_records_token_usage(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(settings, "ai_base_url", "https://model.example/v1")
    monkeypatch.setattr(settings, "ai_api_key", "environment-ai-key")
    monkeypatch.setattr(settings, "ai_model", "scheduling-model")
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["body"] = kwargs["json"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "headline": "已排定全部课次",
                                    "explanation": ["同一班级没有被排到同一时刻。"],
                                    "next_actions": [],
                                    "intent_review": {"verdict": "matched", "concerns": []},
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 812, "completion_tokens": 96, "total_tokens": 908},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.services.ai.httpx.post", fake_post)
    run = _solve_once(client, auth_headers)
    response = client.post(
        f"/api/v1/solver-runs/{run['id']}/explanation",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"] == "ai"
    assert payload["headline"] == "已排定全部课次"
    assert payload["intent_review"] == {"verdict": "matched", "concerns": []}
    assert payload["usage"]["total_tokens"] == 908

    # 事实包必须带上确定性重算的冲突指标，且 prompt 明确禁止模型改写这个结论。
    system_prompt = captured["body"]["messages"][0]["content"]
    assert "禁止判断排课结果是否正确" in system_prompt
    facts = json.loads(captured["body"]["messages"][1]["content"])
    assert "hard_conflicts" in facts["schedule"]["metrics"]

    # 解释已落库，再次请求不应重复调用模型。
    monkeypatch.setattr(
        "app.services.ai.httpx.post",
        lambda *args, **kwargs: pytest.fail("已存解释不应重新调用 AI"),
    )
    cached = client.post(
        f"/api/v1/solver-runs/{run['id']}/explanation",
        headers=auth_headers,
    )
    assert cached.status_code == 200
    assert cached.json()["headline"] == "已排定全部课次"


def test_explanation_rejects_a_run_that_has_not_finished(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    run = _solve_once(client, auth_headers)
    with SessionLocal() as db:
        stored = db.get(SolverRun, run["id"])
        assert stored is not None
        stored.status = "running"
        stored.explanation = None
        db.commit()
    response = client.post(
        f"/api/v1/solver-runs/{run['id']}/explanation",
        headers=auth_headers,
    )
    assert response.status_code == 409
