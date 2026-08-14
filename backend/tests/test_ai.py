from __future__ import annotations

import json
from typing import Any

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.api import settings
from app.db import SessionLocal
from app.models import AIProviderConfiguration
from app.services.ai import AISecretCipher, AIService


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
