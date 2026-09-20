"""interpret 的 SSE 流式通道测试：服务层事件序列 + 流式端点的 SSE 协议。

mock 用 httpx.MockTransport（不新增依赖）：把 AIService 内部使用的
httpx.AsyncClient 换成挂 MockTransport 的真实客户端，SSE 帧以异步生成器
字节流形式回放，覆盖跨 chunk 的 <think> 标签截断与 usage 尾包。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import settings
from app.db import SessionLocal
from app.models import AIProviderConfiguration
from app.services.ai import AIService, AIServiceError


def _sse_response(frames: list[str]) -> httpx.Response:
    async def body() -> AsyncIterator[bytes]:
        for frame in frames:
            yield frame.encode("utf-8")

    return httpx.Response(
        200,
        content=body(),
        headers={"content-type": "text/event-stream"},
    )


def _install_stream_transport(monkeypatch: Any, handler: Any) -> None:
    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("app.services.ai.httpx.AsyncClient", factory)


def _configure_environment_ai(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "ai_base_url", "https://model.example/v1")
    monkeypatch.setattr(settings, "ai_api_key", "environment-ai-key")
    monkeypatch.setattr(settings, "ai_model", "scheduling-model")


def _clear_stored_configurations() -> None:
    """保证按配置分流的测试不受同会话内其它用例残留的库内配置影响。"""
    with SessionLocal() as db:
        stored = db.get(AIProviderConfiguration, "default")
        if stored is not None:
            db.delete(stored)
            db.commit()


def _chat_frame(delta: dict[str, Any], usage: dict[str, Any] | None = None) -> str:
    chunk: dict[str, Any] = {"choices": [{"delta": delta}]}
    if usage is not None:
        chunk["usage"] = usage
    return "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"


def _collect(
    instruction: str, context: dict[str, Any]
) -> list[tuple[str, Any]]:
    async def run() -> list[tuple[str, Any]]:
        with SessionLocal() as db:
            events = [
                event
                async for event in AIService(settings, db).stream_interpret_instruction(
                    instruction, context=context
                )
            ]
        return events

    return asyncio.run(run())


def _parse_sse(text: str) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    for frame in text.split("\n\n"):
        event = ""
        data_lines: list[str] = []
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        if event:
            events.append((event, "\n".join(data_lines)))
    return events


def test_stream_emits_thinking_deltas_in_order_then_result(monkeypatch: Any) -> None:
    """reasoning_content 与跨 chunk 截断的 <think> 块都要按序进 thinking 流。"""
    _configure_environment_ai(monkeypatch)
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers["Authorization"]
        return _sse_response([
            _chat_frame({"reasoning_content": "用户要求 3 天窗口，"}),
            _chat_frame({"reasoning_content": "先核对候选业务线。"}),
            _chat_frame({"content": "<think>第"}),
            _chat_frame({"content": "一步</think>{\"business_lines\":[\"考"}),
            _chat_frame({"content": "研\"],\"date_window_days\":2}"}),
            _chat_frame(
                {},
                usage={"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11},
            ),
            "data: [DONE]\n\n",
        ])

    _install_stream_transport(monkeypatch, handler)
    events = _collect("三天内重排考研课程", context={"business_lines": ["考研"]})
    assert captured["auth"] == "Bearer environment-ai-key"
    assert captured["body"]["stream"] is True
    assert captured["body"]["stream_options"] == {"include_usage": True}
    assert captured["body"]["model"] == "scheduling-model"
    assert [kind for kind, _payload in events] == [
        "thinking",
        "thinking",
        "thinking",
        "thinking",
        "result",
    ]
    assert [payload for kind, payload in events if kind == "thinking"] == [
        "用户要求 3 天窗口，",
        "先核对候选业务线。",
        "第",
        "一步",
    ]
    result = events[-1][1]
    assert result["parsed"] == {"business_lines": ["考研"], "date_window_days": 2}
    assert result["thinking"] == "用户要求 3 天窗口，先核对候选业务线。\n\n第一步"
    assert result["usage"] == {
        "model": "scheduling-model",
        "prompt_tokens": 5,
        "completion_tokens": 6,
        "total_tokens": 11,
    }


def test_stream_without_thinking_model_yields_only_result(monkeypatch: Any) -> None:
    """无思考模型不应凭空产出 thinking 事件，结果与同步通道一致。"""
    _configure_environment_ai(monkeypatch)
    payload = {"business_lines": ["考研"], "date_window_days": 2}

    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([
            _chat_frame({"role": "assistant", "content": ""}),
            _chat_frame({"content": json.dumps(payload, ensure_ascii=False)}),
            _chat_frame(
                {},
                usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            ),
            "data: [DONE]\n\n",
        ])

    _install_stream_transport(monkeypatch, handler)
    events = _collect("解析考研课程", context={"business_lines": ["考研"]})
    assert [kind for kind, _payload in events] == ["result"]
    result = events[0][1]
    assert result["parsed"] == payload
    assert result["thinking"] is None


def test_stream_reasoning_fallback_does_not_duplicate_thinking(monkeypatch: Any) -> None:
    """content 为空、答案在 reasoning_content 的老兜底：思考文本不重复透出。"""
    _configure_environment_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([
            _chat_frame({"reasoning_content": '{"business_lines":["考研"]}'}),
            "data: [DONE]\n\n",
        ])

    _install_stream_transport(monkeypatch, handler)
    events = _collect("解析考研课程", context={"business_lines": ["考研"]})
    assert [kind for kind, _payload in events] == ["thinking", "result"]
    assert events[0][1] == '{"business_lines":["考研"]}'
    result = events[1][1]
    assert result["parsed"] == {"business_lines": ["考研"]}
    assert result["thinking"] is None


def test_stream_invalid_model_json_raises_service_error(monkeypatch: Any) -> None:
    _configure_environment_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([_chat_frame({"content": "模型这次没有输出 JSON"})])

    _install_stream_transport(monkeypatch, handler)
    with pytest.raises(AIServiceError, match="不是合法 JSON"):
        _collect("解析考研课程", context={"business_lines": ["考研"]})


def test_stream_upstream_error_status_raises_with_detail(monkeypatch: Any) -> None:
    _configure_environment_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    _install_stream_transport(monkeypatch, handler)
    with pytest.raises(AIServiceError, match="AI 模型请求返回 401：invalid api key"):
        _collect("解析考研课程", context={"business_lines": ["考研"]})


def test_assistant_interpret_stream_emits_full_sse_protocol(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    _configure_environment_ai(monkeypatch)
    thinking_text = "先把「三天」换算成 date_window_days=3。"
    content = json.dumps(
        {
            "business_lines": [],
            "product_types": [],
            "class_business_ids": [],
            "date_from": None,
            "date_to": None,
            "date_window_days": 3,
            "recognized_rules": ["固定时段不可调整"],
        },
        ensure_ascii=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([
            _chat_frame({"reasoning_content": thinking_text}),
            _chat_frame({"content": content}),
            "data: [DONE]\n\n",
        ])

    _install_stream_transport(monkeypatch, handler)
    response = client.post(
        "/api/v1/assistant/interpret/stream",
        headers=auth_headers,
        json={"instruction": "三天内重排全部课程，固定时段不可调整"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    events = _parse_sse(response.text)
    assert [event for event, _data in events] == [
        "stage",
        "stage",
        "thinking",
        "stage",
        "result",
    ]
    assert json.loads(events[0][1]) == {"stage": "connect"}
    assert json.loads(events[1][1]) == {"stage": "read"}
    thinking_payload = json.loads(events[2][1])
    assert thinking_payload["delta"] == thinking_text
    assert "elapsed" in thinking_payload
    assert json.loads(events[3][1]) == {"stage": "validate"}
    result = json.loads(events[4][1])
    assert result["source"] == "openai_compatible"
    assert result["ai_configured"] is True
    assert result["date_window_days"] == 3
    assert "fixed_time" in result["solver_rules"]
    assert result["thinking"] == thinking_text
    assert result["summary"].endswith("请教务确认后启动 CP-SAT 求解。")


def test_assistant_interpret_stream_returns_409_when_not_configured(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    _clear_stored_configurations()
    monkeypatch.setattr(settings, "ai_base_url", "")
    monkeypatch.setattr(settings, "ai_api_key", "")
    monkeypatch.setattr(settings, "ai_model", "")
    monkeypatch.setattr(settings, "aily_app_id", "")
    monkeypatch.setattr(settings, "aily_skill_id", "")
    response = client.post(
        "/api/v1/assistant/interpret/stream",
        headers=auth_headers,
        json={"instruction": "三天内重排考研课程"},
    )
    assert response.status_code == 409, response.text
    assert "尚未配置一句话排课 AI" in response.json()["detail"]


def test_assistant_interpret_stream_aily_pseudo_stream_single_result(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    """Aily 无流式：直接发一条 result 事件（伪流式），thinking 为空。"""
    _clear_stored_configurations()
    monkeypatch.setattr(settings, "ai_base_url", "")
    monkeypatch.setattr(settings, "ai_api_key", "")
    monkeypatch.setattr(settings, "ai_model", "")
    monkeypatch.setattr(settings, "aily_app_id", "spring_test")
    monkeypatch.setattr(settings, "aily_skill_id", "skill_test")
    monkeypatch.setattr(
        "app.api.FeishuService.start_aily_skill",
        lambda *args, **kwargs: {
            "business_lines": [],
            "product_types": [],
            "class_business_ids": [],
            "date_from": None,
            "date_to": None,
            "date_window_days": 2,
            "recognized_rules": ["固定时段不可调整"],
        },
    )
    response = client.post(
        "/api/v1/assistant/interpret/stream",
        headers=auth_headers,
        json={"instruction": "全部课程在固定时段不变的前提下尽量不变，日期范围 2 天"},
    )
    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    assert [event for event, _data in events] == ["stage", "stage", "stage", "result"]
    result = json.loads(events[-1][1])
    assert result["source"] == "feishu_aily"
    assert result["date_window_days"] == 2
    assert result["thinking"] is None
    assert result["aily_configured"] is True


def test_assistant_interpret_stream_emits_error_event_on_invalid_model_json(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    """模型输出非法 JSON 时以 error 事件收尾，而不是中断连接或 500。"""
    _configure_environment_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([_chat_frame({"content": "模型这次没有输出 JSON"})])

    _install_stream_transport(monkeypatch, handler)
    response = client.post(
        "/api/v1/assistant/interpret/stream",
        headers=auth_headers,
        json={"instruction": "三天内重排考研课程"},
    )
    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    assert events[-1][0] == "error"
    detail = json.loads(events[-1][1])["detail"]
    assert detail.startswith("AI 指令解析失败：")
    assert "不是合法 JSON" in detail
