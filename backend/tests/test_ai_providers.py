"""AI 厂商适配（DeepSeek / 智谱 GLM）：识别、发包差异、用量口径、空内容重试、提示词缓存友好、
预设清单与连接测试。

发包规则依据各家官方文档（见 app/services/ai_providers.py 模块说明）；这里用假的 httpx 响应
验证「发出去的请求体长什么样」，不访问真实厂商。
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.api import settings
from app.db import SessionLocal
from app.models import AIProviderConfiguration
from app.services.ai import AIService, AIServiceError
from app.services.ai_providers import (
    PRESETS,
    build_chat_body,
    normalize_usage,
    preset_id_for,
    resolve_profile,
)

# 在任何桩安装之前记下真实的异步客户端：同一个用例里多次安装流式桩时不能层层嵌套。
_REAL_ASYNC_CLIENT = httpx.AsyncClient

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def _body(
    base_url: str, model: str, *, stream: bool = False, effort: str = "auto"
) -> dict[str, Any]:
    return build_chat_body(
        resolve_profile(base_url, model), messages=MESSAGES, stream=stream, reasoning_effort=effort
    )


# ------------------------------------------------- 识别


@pytest.mark.parametrize(
    ("base_url", "model", "family", "official"),
    [
        ("https://api.deepseek.com", "deepseek-flash", "deepseek", True),
        ("https://api.deepseek.com/", "deepseek-v4-pro", "deepseek", True),
        ("https://open.bigmodel.cn/api/paas/v4", "glm-5.3", "glm", True),
        ("https://api.z.ai/api/paas/v4/", "glm-5.3", "glm", True),
        # 第三方网关：只按模型名认族，不算官方端点。
        ("https://relay.example/v1", "deepseek/deepseek-v4-flash", "deepseek", False),
        ("https://relay.example/v1", "zai-org/GLM-5.2", "glm", False),
        ("https://relay.example/v1", "gpt-4.1", "generic", False),
        # 模型名里碰巧含有 glm 字样的单词不算。
        ("https://relay.example/v1", "englishmodel", "generic", False),
        # 域名后缀伪造不算官方。
        ("https://api.deepseek.com.evil.example/v1", "deepseek-flash", "deepseek", False),
    ],
)
def test_profile_is_recognised_from_the_endpoint_and_the_model_name(
    base_url: str, model: str, family: str, official: bool
) -> None:
    profile = resolve_profile(base_url, model)
    assert (profile.family, profile.official) == (family, official)


def test_preset_cards_are_recognised_from_their_base_url() -> None:
    assert preset_id_for("https://api.deepseek.com/") == "deepseek"
    assert preset_id_for("https://open.bigmodel.cn/api/paas/v4") == "zhipu"
    assert preset_id_for("https://api.z.ai/api/paas/v4") == "zai"
    assert preset_id_for("https://somewhere.example/v1") == "custom"


def test_presets_never_point_at_the_restricted_coding_plan_endpoint() -> None:
    """智谱 Coding Plan 端点官方限定只能在指定编码工具里用，自建应用必须走标准 API。"""
    assert all("/coding/" not in item.base_url for item in PRESETS)
    assert [item.id for item in PRESETS][0] == "custom"


# ------------------------------------------------- DeepSeek 发包


def test_official_deepseek_request_carries_thinking_controls_and_enough_output_room() -> None:
    body = _body("https://api.deepseek.com", "deepseek-flash")
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "low"  # auto：抽取类任务按 low
    assert body["max_tokens"] >= 16384  # JSON 输出要给足，避免被截断
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0  # 思考模式下官方忽略它但不报错
    assert "stream" not in body
    # 缓存默认开启，没有也不需要任何缓存参数。
    assert not {key for key in body if "cache" in key.lower()}


def test_deepseek_effort_can_be_raised() -> None:
    assert (
        _body("https://api.deepseek.com", "deepseek-v4-pro", effort="max")["reasoning_effort"]
        == "max"
    )


def test_deepseek_stream_asks_for_usage() -> None:
    body = _body("https://api.deepseek.com", "deepseek-flash", stream=True)
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}


def test_deepseek_behind_a_gateway_gets_no_vendor_specific_parameters() -> None:
    """网关未必透传厂商参数，带上可能被拒绝：只保留通用字段。"""
    body = _body("https://relay.example/v1", "deepseek/deepseek-v4-flash")
    assert set(body) == {"model", "temperature", "response_format", "messages"}


# ------------------------------------------------- GLM 发包


def test_official_glm_53_always_enables_thinking_because_it_cannot_be_turned_off() -> None:
    body = _body("https://open.bigmodel.cn/api/paas/v4", "glm-5.3")
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "low"
    assert "max_tokens" not in body  # 官方默认 65536，不必设


@pytest.mark.parametrize("effort", ["auto", "low", "high", "max"])
@pytest.mark.parametrize(
    "model",
    [
        "glm-5.3",
        "glm-5.3-flash",
        "glm-5.3-flashx",
        "glm-5.2",
        "glm-4.7",
        "glm-4.5-flash",
        "deepseek-flash",
    ],
)
@pytest.mark.parametrize("stream", [False, True])
def test_no_official_request_ever_disables_thinking(model: str, effort: str, stream: bool) -> None:
    """GLM-5.3 / 5.3-Flash 强制思考，`thinking.type=disabled` 会直接失败：任何组合都不能发出它。"""
    hosts = {
        "deepseek-flash": "https://api.deepseek.com",
    }
    body = _body(
        hosts.get(model, "https://open.bigmodel.cn/api/paas/v4"),
        model,
        stream=stream,
        effort=effort,
    )
    assert body.get("thinking", {}).get("type") != "disabled"


def test_glm_reasoning_effort_only_goes_to_models_that_document_it() -> None:
    """reasoning_effort 仅 GLM-5.2 及以上支持：更老的模型不带，免得被拒绝。"""
    base = "https://open.bigmodel.cn/api/paas/v4"
    assert "reasoning_effort" in _body(base, "glm-5.2")
    assert "reasoning_effort" not in _body(base, "glm-5.1")
    assert "reasoning_effort" not in _body(base, "glm-4.7")
    assert "thinking" not in _body(base, "glm-4.5-flash")


def test_glm_stream_does_not_send_the_undocumented_stream_options() -> None:
    body = _body("https://open.bigmodel.cn/api/paas/v4", "glm-5.3", stream=True)
    assert body["stream"] is True and "stream_options" not in body
    international = _body("https://api.z.ai/api/paas/v4", "glm-5.3", stream=True)
    assert "stream_options" not in international


def test_glm_behind_a_gateway_gets_no_vendor_specific_parameters() -> None:
    body = _body("https://relay.example/v1", "glm-5.3")
    assert set(body) == {"model", "temperature", "response_format", "messages"}


# ------------------------------------------------- 用量口径


def test_usage_reports_cache_hits_for_each_vendor() -> None:
    deepseek = normalize_usage(
        "deepseek-flash",
        {
            "prompt_tokens": 2000,
            "completion_tokens": 50,
            "total_tokens": 2050,
            "prompt_cache_hit_tokens": 1536,
            "prompt_cache_miss_tokens": 464,
        },
    )
    assert (deepseek["cached_tokens"], deepseek["cache_miss_tokens"]) == (1536, 464)
    glm = normalize_usage(
        "glm-5.3",
        {
            "prompt_tokens": 1075,
            "completion_tokens": 241,
            "total_tokens": 1316,
            "prompt_tokens_details": {"cached_tokens": 1024},
            "completion_tokens_details": {"reasoning_tokens": 120},
        },
    )
    assert (glm["cached_tokens"], glm["cache_miss_tokens"], glm["reasoning_tokens"]) == (
        1024,
        51,
        120,
    )


def test_usage_without_cache_fields_stays_as_before() -> None:
    assert normalize_usage(
        "m", {"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11}
    ) == {
        "model": "m",
        "prompt_tokens": 5,
        "completion_tokens": 6,
        "total_tokens": 11,
    }
    assert normalize_usage("m", None)["prompt_tokens"] is None


# ------------------------------------------------- 经 AIService 的真实发包


def _configure_env(
    monkeypatch: pytest.MonkeyPatch, base_url: str, model: str, effort: str = "auto"
) -> None:
    monkeypatch.setattr(settings, "ai_base_url", base_url)
    monkeypatch.setattr(settings, "ai_api_key", "environment-ai-key")
    monkeypatch.setattr(settings, "ai_model", model)
    monkeypatch.setattr(settings, "ai_reasoning_effort", effort)


def _response(
    url: str, content: str, usage: dict[str, Any] | None = None, **message: Any
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content, **message}}],
            "usage": usage or {},
        },
        request=httpx.Request("POST", url),
    )


def test_service_sends_the_official_deepseek_request_and_reports_cache_usage(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    _configure_env(monkeypatch, "https://api.deepseek.com", "deepseek-flash", "high")
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        captured.update(url=url, **kwargs)
        return _response(
            url,
            '{"ok": true}',
            {
                "prompt_tokens": 1200,
                "completion_tokens": 30,
                "total_tokens": 1230,
                "prompt_cache_hit_tokens": 1024,
                "prompt_cache_miss_tokens": 176,
            },
            reasoning_content="先想一想",
        )

    monkeypatch.setattr("app.services.ai.httpx.post", fake_post)
    parsed, thinking, usage = AIService(settings, db_session)._chat_json("只输出 JSON", "ping")
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer environment-ai-key"
    assert captured["json"]["thinking"] == {"type": "enabled"}
    assert captured["json"]["reasoning_effort"] == "high"
    assert parsed == {"ok": True} and thinking == "先想一想"
    assert usage["cached_tokens"] == 1024 and usage["cache_miss_tokens"] == 176


def test_deepseek_empty_content_is_retried_once(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    """官方文档承认 JSON 输出偶尔返回空内容：重试一次。"""
    _configure_env(monkeypatch, "https://api.deepseek.com", "deepseek-flash")
    replies = iter(["", '{"ok": true}'])
    calls: list[int] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        calls.append(1)
        return _response(url, next(replies))

    monkeypatch.setattr("app.services.ai.httpx.post", fake_post)
    parsed, _thinking, _usage = AIService(settings, db_session)._chat_json("只输出 JSON", "ping")
    assert parsed == {"ok": True} and len(calls) == 2


def test_deepseek_empty_content_twice_fails_clearly(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    _configure_env(monkeypatch, "https://api.deepseek.com", "deepseek-flash")
    calls: list[int] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        calls.append(1)
        return _response(url, "")

    monkeypatch.setattr("app.services.ai.httpx.post", fake_post)
    with pytest.raises(AIServiceError, match="空内容"):
        AIService(settings, db_session)._chat_json("只输出 JSON", "ping")
    assert len(calls) == 2


def test_other_vendors_are_not_retried_on_empty_content(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    _configure_env(monkeypatch, "https://relay.example/v1", "gpt-4.1")
    calls: list[int] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        calls.append(1)
        return _response(url, "")

    monkeypatch.setattr("app.services.ai.httpx.post", fake_post)
    with pytest.raises(AIServiceError):
        AIService(settings, db_session)._chat_json("只输出 JSON", "ping")
    assert len(calls) == 1


# ------------------------------------------------- 提示词：JSON 字样与缓存友好


def test_every_prompt_mentions_json_because_deepseek_json_mode_requires_it(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    seen: list[str] = []

    def fake_chat_json(self: AIService, system_prompt: str, user_content: str, **kwargs: Any):
        seen.append(system_prompt)
        return {"mappings": [], "candidates": []}, None, {}

    monkeypatch.setattr(AIService, "_chat_json", fake_chat_json)
    service = AIService(settings, db_session)
    service.explain_solver_run({})
    service.map_import_columns([], [])
    service.mine_preferences([])
    seen.append(AIService._interpret_system_prompt({}))
    assert len(seen) == 4
    assert all("json" in prompt.lower() for prompt in seen)


def test_interpret_prompt_keeps_the_stable_part_first_so_prefix_caches_hit() -> None:
    """DeepSeek / GLM 的上下文缓存都按请求前缀命中：随请求变化的内容（偏好、任务上下文、日期）
    放在后面，前面的规则、输出结构和同一方案下稳定的候选值才能被缓存复用。"""
    stable = {
        "business_lines": ["考研"],
        "product_types": ["暑期集训"],
        "class_business_ids": ["B01"],
        "teachers": [{"business_id": "T01", "name": "张老师"}],
        "time_slots": [{"business_id": "S05", "weekday": "周三"}],
        "fixed_rule_labels": ["固定时段不可调整"],
    }
    first = AIService._interpret_system_prompt(
        {**stable, "active_preferences": [{"id": "p1"}], "task_context": {"goal_id": "g1"}}
    )
    second = AIService._interpret_system_prompt(
        {**stable, "active_preferences": [{"id": "p2"}, {"id": "p3"}], "task_context": None}
    )
    shared = os.path.commonprefix([first, second])
    assert shared.index("输出结构：") < len(shared)  # 规则与输出结构在共享前缀里
    assert '"fixed_rule_labels": ["固定时段不可调整"]' in shared  # 稳定候选值也在共享前缀里
    # 随请求变化的内容排在稳定候选值之后：共享前缀在它们的值之前就断开了。
    assert first.index('"fixed_rule_labels"') < first.index('"active_preferences"')
    assert len(shared) < first.index('"task_context"')
    # 每天都变的日期放在最后。
    assert first.rstrip().endswith("转换成明确的 YYYY-MM-DD。")
    assert first.index("输入上下文：") < first.index("当前日期为")


def test_interpret_prompt_context_line_is_still_a_single_parsable_json_object() -> None:
    """假模型服务（scripts/fake_model_server.py）从「输入上下文：」后面取 JSON。"""
    prompt = AIService._interpret_system_prompt({"teachers": [], "active_preferences": []})
    tail = prompt[prompt.index("输入上下文：") + len("输入上下文：") :]
    value, _ = json.JSONDecoder().raw_decode(tail)
    assert list(value) == ["teachers", "active_preferences"]


# ------------------------------------------------- 预设清单、配置与连接测试接口


@pytest.fixture
def db_session():
    with SessionLocal() as db:
        yield db


@pytest.fixture
def clean_ai_config(monkeypatch: pytest.MonkeyPatch):
    async def fake_stream(self: AIService, *args: Any, **kwargs: Any):
        yield "result", {"parsed": {"ok": True}, "usage": {}, "thinking": None}

    monkeypatch.setattr(AIService, "_chat_stream_json", fake_stream)
    monkeypatch.setattr(settings, "ai_base_url", "")
    monkeypatch.setattr(settings, "ai_api_key", "")
    monkeypatch.setattr(settings, "ai_model", "")
    monkeypatch.setattr(settings, "ai_token_encryption_key", Fernet.generate_key().decode("ascii"))
    with SessionLocal() as db:
        stored = db.get(AIProviderConfiguration, "default")
        if stored is not None:
            db.delete(stored)
            db.commit()
    yield
    with SessionLocal() as db:
        stored = db.get(AIProviderConfiguration, "default")
        if stored is not None:
            db.delete(stored)
            db.commit()


def test_preset_list_is_served_for_the_provider_picker(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/integrations/ai/presets", headers=auth_headers)
    assert response.status_code == 200, response.text
    by_id = {item["id"]: item for item in response.json()}
    assert list(by_id)[0] == "custom"
    assert by_id["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert by_id["deepseek"]["models"][0] == "deepseek-flash"
    assert by_id["zhipu"]["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert by_id["zhipu"]["family"] == "glm"
    assert any("Coding Plan" in note for note in by_id["zhipu"]["notes"])


def test_saved_configuration_keeps_the_reasoning_effort_and_names_the_preset(
    client: TestClient, auth_headers: dict[str, str], clean_ai_config: None
) -> None:
    saved = client.post(
        "/api/v1/integrations/ai/configuration",
        headers=auth_headers,
        json={
            "base_url": "https://api.deepseek.com",
            "api_key": "deepseek-secret-key",
            "model": "deepseek-flash",
            "reasoning_effort": "high",
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert (body["preset"], body["family"], body["official"], body["reasoning_effort"]) == (
        "deepseek",
        "deepseek",
        True,
        "high",
    )
    assert "deepseek-secret-key" not in saved.text
    assert client.get("/api/v1/integrations/ai/configuration", headers=auth_headers).json() == body
    invalid = client.post(
        "/api/v1/integrations/ai/configuration",
        headers=auth_headers,
        json={
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash",
            "reasoning_effort": "turbo",
        },
    )
    assert invalid.status_code == 422


def test_connection_test_uses_the_unsaved_config_and_reports_what_happened(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    clean_ai_config: None,
) -> None:
    sent: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        sent.update(url=url, **kwargs)
        return _response(
            url,
            '{"ok": true}',
            {
                "prompt_tokens": 40,
                "completion_tokens": 5,
                "total_tokens": 45,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
            reasoning_content="想了想",
        )

    monkeypatch.setattr("app.services.ai.httpx.post", fake_post)
    response = client.post(
        "/api/v1/integrations/ai/test",
        headers=auth_headers,
        json={
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key": "zhipu-secret-key",
            "model": "glm-5.3",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["family"] == "glm" and body["official"] is True
    assert body["thinking_returned"] is True and body["usage"]["prompt_tokens"] == 40
    assert sent["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer zhipu-secret-key"
    assert sent["json"]["thinking"] == {"type": "enabled"}
    assert "zhipu-secret-key" not in response.text
    # 测试不保存任何东西。
    with SessionLocal() as db:
        assert db.get(AIProviderConfiguration, "default") is None


def test_connection_test_failure_is_reported_inline(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    clean_ai_config: None,
) -> None:
    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Authentication Fails"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.services.ai.httpx.post", fake_post)
    response = client.post(
        "/api/v1/integrations/ai/test",
        headers=auth_headers,
        json={
            "base_url": "https://api.deepseek.com",
            "api_key": "wrong-key-value",
            "model": "deepseek-flash",
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert (
        "401" in response.json()["message"] and "Authentication Fails" in response.json()["message"]
    )


def test_connection_test_never_sends_a_saved_key_to_a_different_address(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    clean_ai_config: None,
) -> None:
    """Key 留空 = 沿用已保存的密钥，但只在接口地址没变时；换了地址必须重新填，
    不能把旧厂商的 Key 发给新地址。"""
    saved = client.post(
        "/api/v1/integrations/ai/configuration",
        headers=auth_headers,
        json={
            "base_url": "https://api.deepseek.com",
            "api_key": "deepseek-saved-key",
            "model": "deepseek-flash",
        },
    )
    assert saved.status_code == 200, saved.text
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        sent.append({"url": url, **kwargs})
        return _response(url, '{"ok": true}')

    monkeypatch.setattr("app.services.ai.httpx.post", fake_post)
    same = client.post(
        "/api/v1/integrations/ai/test",
        headers=auth_headers,
        json={"base_url": "https://api.deepseek.com", "model": "deepseek-v4-pro"},
    )
    assert same.json()["ok"] is True
    assert sent[0]["headers"]["Authorization"] == "Bearer deepseek-saved-key"
    other = client.post(
        "/api/v1/integrations/ai/test",
        headers=auth_headers,
        json={"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-5.3"},
    )
    assert other.json()["ok"] is False and "API Key" in other.json()["message"]
    assert len(sent) == 1  # 第二次根本没发出请求


# ------------------------------------------------- 独立审查意见：空内容与思考草稿


def _message_response(
    url: str, content: str | None, reasoning: str | None = None, finish: str = "stop"
) -> httpx.Response:
    message: dict[str, Any] = {"content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return httpx.Response(
        200,
        json={"choices": [{"message": message, "finish_reason": finish}], "usage": {}},
        request=httpx.Request("POST", url),
    )


DRAFT = '先试试 {"business_lines": [], "product_types": ["草稿"]} 再继续想'


def test_deepseek_empty_content_with_reasoning_is_retried_not_parsed_from_the_draft(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    """思考开启时 content 为空、reasoning_content 有草稿：必须重试，不能把草稿里的 {...} 当答案。"""
    _configure_env(monkeypatch, "https://api.deepseek.com", "deepseek-flash")
    replies = iter([(None, DRAFT), ('{"ok": true}', "想好了")])
    calls: list[int] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        calls.append(1)
        content, reasoning = next(replies)
        return _message_response(url, content, reasoning)

    monkeypatch.setattr("app.services.ai.httpx.post", fake_post)
    parsed, _thinking, _usage = AIService(settings, db_session)._chat_json("只输出 JSON", "ping")
    assert parsed == {"ok": True} and len(calls) == 2


@pytest.mark.parametrize(
    ("base_url", "model"),
    [
        ("https://api.deepseek.com", "deepseek-flash"),
        ("https://open.bigmodel.cn/api/paas/v4", "glm-5.3"),
        ("https://relay.example/v1", "deepseek/deepseek-v4-flash"),
    ],
)
def test_a_thinking_draft_is_never_taken_as_the_answer_for_deepseek_and_glm(
    monkeypatch: pytest.MonkeyPatch, db_session: Any, base_url: str, model: str
) -> None:
    _configure_env(monkeypatch, base_url, model)
    monkeypatch.setattr(
        "app.services.ai.httpx.post", lambda url, **kwargs: _message_response(url, "", DRAFT)
    )
    with pytest.raises(AIServiceError, match="空内容"):
        AIService(settings, db_session)._chat_json("只输出 JSON", "ping")


def test_truncated_output_is_reported_as_truncation(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    _configure_env(monkeypatch, "https://api.deepseek.com", "deepseek-flash")
    monkeypatch.setattr(
        "app.services.ai.httpx.post",
        lambda url, **kwargs: _message_response(url, "", DRAFT, finish="length"),
    )
    with pytest.raises(AIServiceError, match="截断"):
        AIService(settings, db_session)._chat_json("只输出 JSON", "ping")


def test_unknown_gateways_keep_the_reasoning_fallback_for_compatibility(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    """认不出厂商的第三方接口，个别推理模型确实把答案放进 reasoning_content：保持原有兜底。"""
    _configure_env(monkeypatch, "https://relay.example/v1", "some-reasoner")
    monkeypatch.setattr(
        "app.services.ai.httpx.post",
        lambda url, **kwargs: _message_response(url, "", '{"ok": true}'),
    )
    parsed, _thinking, _usage = AIService(settings, db_session)._chat_json("只输出 JSON", "ping")
    assert parsed == {"ok": True}


def _stream_frames(content: str, reasoning: str, finish: str = "stop") -> list[str]:
    def frame(delta: dict[str, Any], finish_reason: str | None = None) -> str:
        chunk = {"choices": [{"delta": delta, "finish_reason": finish_reason}]}
        return "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"

    frames = []
    if reasoning:
        frames.append(frame({"reasoning_content": reasoning}))
    if content:
        frames.append(frame({"content": content}))
    frames.append(frame({}, finish))
    frames.append("data: [DONE]\n\n")
    return frames


def _install_stream(monkeypatch: pytest.MonkeyPatch, frames: list[str]) -> None:
    async def body():
        for item in frames:
            yield item.encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body(), headers={"content-type": "text/event-stream"})

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr("app.services.ai.httpx.AsyncClient", factory)


def _run_stream(db_session: Any) -> list[tuple[str, Any]]:
    import asyncio

    async def run() -> list[tuple[str, Any]]:
        return [
            event
            async for event in AIService(settings, db_session)._chat_stream_json("JSON", "ping")
        ]

    return asyncio.run(run())


def test_streaming_does_not_take_a_thinking_draft_as_the_answer_for_deepseek(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    """流式失败时前端会自动回退到带空内容重试的同步接口：这里必须报错，而不是解析出一个错的结果。"""
    _configure_env(monkeypatch, "https://api.deepseek.com", "deepseek-flash")
    _install_stream(monkeypatch, _stream_frames("", DRAFT))
    with pytest.raises(AIServiceError, match="空内容"):
        _run_stream(db_session)
    _install_stream(monkeypatch, _stream_frames("", DRAFT, finish="length"))
    with pytest.raises(AIServiceError, match="截断"):
        _run_stream(db_session)


def test_streaming_with_a_real_answer_still_works_for_deepseek(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    _configure_env(monkeypatch, "https://api.deepseek.com", "deepseek-flash")
    _install_stream(monkeypatch, _stream_frames('{"ok": true}', "先想一想"))
    events = _run_stream(db_session)
    assert events[-1][0] == "result" and events[-1][1]["parsed"] == {"ok": True}


# ------------------------------------------------- 独立审查意见：保存与测试的边界


def test_saving_with_a_blank_key_and_a_changed_address_is_refused(
    client: TestClient, auth_headers: dict[str, str], clean_ai_config: None
) -> None:
    """留空 Key = 沿用已保存的；但换了接口地址就是换了厂商，旧厂商的 Key 不能悄悄发给新地址。"""
    first = client.post(
        "/api/v1/integrations/ai/configuration",
        headers=auth_headers,
        json={
            "base_url": "https://api.deepseek.com",
            "api_key": "deepseek-saved-key",
            "model": "deepseek-flash",
        },
    )
    assert first.status_code == 200, first.text
    switched = client.post(
        "/api/v1/integrations/ai/configuration",
        headers=auth_headers,
        json={"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-5.3"},
    )
    assert switched.status_code == 409 and "重新填写" in switched.json()["detail"]
    same_address = client.post(
        "/api/v1/integrations/ai/configuration",
        headers=auth_headers,
        json={
            "base_url": "https://api.deepseek.com/",
            "model": "deepseek-v4-pro",
            "reasoning_effort": "high",
        },
    )
    assert same_address.status_code == 200, same_address.text
    assert same_address.json()["model"] == "deepseek-v4-pro"
    with SessionLocal() as db:
        stored = db.get(AIProviderConfiguration, "default")
        assert stored is not None and stored.base_url == "https://api.deepseek.com"


@pytest.mark.parametrize(
    "base_url",
    ["https://[bad", "https://api.deepseek.com:evil", "https://"],
)
def test_malformed_addresses_are_reported_inline_instead_of_failing_the_request(
    client: TestClient, auth_headers: dict[str, str], clean_ai_config: None, base_url: str
) -> None:
    response = client.post(
        "/api/v1/integrations/ai/test",
        headers=auth_headers,
        json={"base_url": base_url, "api_key": "some-secret-key", "model": "deepseek-flash"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is False and response.json()["message"]


def test_a_non_ascii_key_is_rejected_before_anything_is_sent(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    clean_ai_config: None,
) -> None:
    sent: list[int] = []
    monkeypatch.setattr(
        "app.services.ai.httpx.post", lambda url, **kwargs: sent.append(1) or _response(url, "{}")
    )
    payload = {"base_url": "https://api.deepseek.com", "api_key": "密钥ｋｅｙ12345", "model": "m"}
    tested = client.post("/api/v1/integrations/ai/test", headers=auth_headers, json=payload)
    assert tested.json()["ok"] is False and "ASCII" in tested.json()["message"]
    saved = client.post("/api/v1/integrations/ai/configuration", headers=auth_headers, json=payload)
    assert saved.status_code == 409 and "ASCII" in saved.json()["detail"]
    assert sent == []


def test_connection_test_also_probes_the_streaming_channel_the_web_page_uses(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    clean_ai_config: None,
) -> None:
    monkeypatch.setattr(
        "app.services.ai.httpx.post", lambda url, **kwargs: _response(url, '{"ok": true}')
    )

    async def broken_stream(self: AIService, *args: Any, **kwargs: Any):
        raise AIServiceError("AI 模型请求返回 400：unknown field stream_options")
        yield  # pragma: no cover - 让它成为异步生成器

    monkeypatch.setattr(AIService, "_chat_stream_json", broken_stream)
    response = client.post(
        "/api/v1/integrations/ai/test",
        headers=auth_headers,
        json={"base_url": "https://relay.example/v1", "api_key": "some-secret-key", "model": "m"},
    )
    body = response.json()
    assert body["ok"] is True and body["stream_ok"] is False
    assert "流式通道失败" in body["message"] and "自动回退" in body["message"]


def test_connection_test_is_audited_without_the_key(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    clean_ai_config: None,
) -> None:
    from app.models import AuditLog

    monkeypatch.setattr(
        "app.services.ai.httpx.post", lambda url, **kwargs: _response(url, '{"ok": true}')
    )
    client.post(
        "/api/v1/integrations/ai/test",
        headers=auth_headers,
        json={"base_url": "https://relay.example/v1", "api_key": "audit-secret-key", "model": "m"},
    )
    with SessionLocal() as db:
        from sqlalchemy import select

        rows = list(
            db.scalars(
                select(AuditLog).where(
                    AuditLog.action == "test", AuditLog.resource_type == "ai_provider"
                )
            )
        )
    assert rows and all("audit-secret-key" not in json.dumps(row.detail) for row in rows)
    assert rows[-1].detail["base_url"] == "https://relay.example/v1"


def test_max_tokens_is_only_sent_for_the_models_the_docs_cover() -> None:
    """旧模型名（文档没覆盖）只带通用字段，免得被拒绝。"""
    assert "max_tokens" in _body("https://api.deepseek.com", "deepseek-flash")
    legacy = _body("https://api.deepseek.com", "deepseek-chat")
    assert set(legacy) == {"model", "temperature", "response_format", "messages"}
