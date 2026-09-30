from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import AIProviderConfiguration
from .ai_providers import (
    REASONING_EFFORTS,
    ProviderProfile,
    build_chat_body,
    normalize_usage,
    preset_id_for,
    resolve_profile,
)
from .task_context import (
    EXPLICIT_CORRECT_WORDS,
    EXPLICIT_EXPIRE_WORDS,
    EXPLICIT_SAVE_WORDS,
)

logger = logging.getLogger(__name__)


class AIServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class AIProviderCredentials:
    provider: str
    base_url: str
    api_key: str
    model: str
    source: str
    reasoning_effort: str = "auto"


class AISecretCipher:
    def __init__(self, key: str) -> None:
        try:
            self.fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise AIServiceError("AI 配置加密主密钥格式不正确") from exc

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self.fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise AIServiceError("AI 接口密钥密文校验失败，请重新配置") from exc


class AIService:
    def __init__(self, settings: Settings, db: Session) -> None:
        self.settings = settings
        self.db = db

    def _cipher(self) -> AISecretCipher:
        key = self.settings.ai_token_encryption_key.strip()
        if not key:
            path = self.settings.ai_token_key_file
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                key = path.read_text(encoding="ascii").strip()
            else:
                generated = Fernet.generate_key()
                try:
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    key = path.read_text(encoding="ascii").strip()
                else:
                    try:
                        os.write(descriptor, generated + b"\n")
                    finally:
                        os.close(descriptor)
                    key = generated.decode("ascii")
        return AISecretCipher(key)

    @staticmethod
    def _validate_url(value: str) -> str:
        normalized = value.strip().rstrip("/")
        try:
            parsed = urlsplit(normalized)
        except ValueError as exc:
            raise AIServiceError("AI 接口地址格式不正确") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AIServiceError("AI 接口地址必须是完整的 HTTP 或 HTTPS 地址")
        return normalized

    @staticmethod
    def _normalize_key(api_key: str | None) -> str:
        key = (api_key or "").strip()
        if key and not key.isascii():
            raise AIServiceError("API Key 只能包含 ASCII 字符，请检查是否复制进了全角字符")
        return key

    def _stored_effort(self, stored: AIProviderConfiguration) -> str:
        return self._valid_effort((stored.options or {}).get("reasoning_effort"))

    @staticmethod
    def _valid_effort(value: Any) -> str:
        text = str(value or "auto").strip().lower()
        return text if text in REASONING_EFFORTS else "auto"

    def _environment_configuration(self) -> AIProviderCredentials | None:
        if not self.settings.ai_environment_configured:
            return None
        return AIProviderCredentials(
            provider=self.settings.ai_provider.strip() or "openai_compatible",
            base_url=self._validate_url(self.settings.ai_base_url),
            api_key=self.settings.ai_api_key.strip(),
            model=self.settings.ai_model.strip(),
            source="environment",
            reasoning_effort=self._valid_effort(self.settings.ai_reasoning_effort),
        )

    def _stored_configuration(self) -> AIProviderConfiguration | None:
        return self.db.get(AIProviderConfiguration, "default")

    def credentials(self) -> AIProviderCredentials:
        environment = self._environment_configuration()
        if environment is not None:
            return environment
        stored = self._stored_configuration()
        if stored is None:
            raise AIServiceError("请先配置通用 AI 模型接口")
        return AIProviderCredentials(
            provider=stored.provider,
            base_url=stored.base_url,
            api_key=self._cipher().decrypt(stored.api_key_encrypted),
            model=stored.model,
            source="frontend",
            reasoning_effort=self._stored_effort(stored),
        )

    def configuration_view(self) -> dict[str, Any]:
        environment = self._environment_configuration()
        if environment is not None:
            return self._view(environment, api_key_configured=True)
        stored = self._stored_configuration()
        if stored is not None:
            return self._view(
                AIProviderCredentials(
                    provider=stored.provider,
                    base_url=stored.base_url,
                    api_key="",
                    model=stored.model,
                    source="frontend",
                    reasoning_effort=self._stored_effort(stored),
                ),
                api_key_configured=bool(stored.api_key_encrypted),
            )
        return {
            "configured": False,
            "source": "none",
            "provider": None,
            "base_url": None,
            "api_key_configured": False,
            "model": None,
            "preset": None,
            "family": None,
            "official": False,
            "reasoning_effort": "auto",
        }

    @staticmethod
    def _view(credentials: AIProviderCredentials, *, api_key_configured: bool) -> dict[str, Any]:
        profile = resolve_profile(credentials.base_url, credentials.model)
        return {
            "configured": True,
            "source": credentials.source,
            "provider": credentials.provider,
            "base_url": credentials.base_url,
            "api_key_configured": api_key_configured,
            "model": credentials.model,
            "preset": preset_id_for(credentials.base_url),
            "family": profile.family,
            "official": profile.official,
            "reasoning_effort": credentials.reasoning_effort,
        }

    def save_configuration(
        self,
        user_id: str,
        *,
        provider: str,
        base_url: str,
        api_key: str | None,
        model: str,
        reasoning_effort: str = "auto",
    ) -> AIProviderConfiguration:
        if self._environment_configuration() is not None:
            raise AIServiceError("当前 AI 模型接口由部署环境统一管理")
        normalized_provider = provider.strip()
        if normalized_provider != "openai_compatible":
            raise AIServiceError("当前仅支持 OpenAI-compatible 接口")
        normalized_url = self._validate_url(base_url)
        normalized_key = self._normalize_key(api_key)
        normalized_model = model.strip()
        if not normalized_model:
            raise AIServiceError("必须填写模型名称")
        if reasoning_effort not in REASONING_EFFORTS:
            raise AIServiceError("思考强度只能取 auto、low、high、max")

        stored = self._stored_configuration()
        if stored is not None and not normalized_key and stored.base_url != normalized_url:
            # 换了接口地址就是换了厂商：不能让旧厂商的 Key 悄悄发给新地址。
            raise AIServiceError("接口地址变了，不能沿用已保存的密钥，请重新填写 API Key")
        if stored is None and len(normalized_key) < 8:
            raise AIServiceError("首次配置必须填写有效的 AI 接口密钥")
        if normalized_key and len(normalized_key) < 8:
            raise AIServiceError("AI 接口密钥长度不正确")
        if stored is None:
            stored = AIProviderConfiguration(
                id="default",
                provider=normalized_provider,
                base_url=normalized_url,
                api_key_encrypted="",
                model=normalized_model,
                configured_by=user_id,
            )
            self.db.add(stored)
        stored.provider = normalized_provider
        stored.base_url = normalized_url
        stored.model = normalized_model
        stored.options = {**(stored.options or {}), "reasoning_effort": reasoning_effort}
        stored.configured_by = user_id
        if normalized_key:
            stored.api_key_encrypted = self._cipher().encrypt(normalized_key)
        self.db.commit()
        self.db.refresh(stored)
        return stored

    def test_connection(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        reasoning_effort: str = "auto",
    ) -> dict[str, Any]:
        """用给定（尚未保存）的配置发一次最小请求，验证地址、Key、模型名和厂商参数都能通。

        Key 留空表示沿用已保存的密钥，但只在接口地址没变时才沿用——否则会把旧厂商的 Key
        发给新地址。走与正式调用完全相同的发包路径，所以这里通过就说明一句话排课也能通。
        """
        normalized_url = self._validate_url(base_url)
        normalized_model = model.strip()
        if not normalized_model:
            raise AIServiceError("必须填写模型名称")
        if reasoning_effort not in REASONING_EFFORTS:
            raise AIServiceError("思考强度只能取 auto、low、high、max")
        key = self._normalize_key(api_key)
        if not key:
            environment = self._environment_configuration()
            stored = self._stored_configuration()
            if environment is not None and environment.base_url == normalized_url:
                key = environment.api_key
            elif stored is not None and stored.base_url == normalized_url:
                key = self._cipher().decrypt(stored.api_key_encrypted)
            else:
                raise AIServiceError("请填写 API Key（接口地址变了，不能沿用已保存的密钥）")
        credentials = AIProviderCredentials(
            provider="openai_compatible",
            base_url=normalized_url,
            api_key=key,
            model=normalized_model,
            source="test",
            reasoning_effort=reasoning_effort,
        )
        profile = resolve_profile(normalized_url, normalized_model)
        started = time.perf_counter()
        probe_prompt = '你是连通性自检助手。只输出 JSON 对象，不要输出 Markdown。输出：{"ok":true}'
        parsed, thinking, usage = self._chat_json(probe_prompt, "ping", credentials=credentials)
        latency_ms = int((time.perf_counter() - started) * 1000)
        # 网页一句话排课实际走流式通道（失败才回退非流式），两条都探一下，测试通过才真的说明能用。
        stream_error = ""
        try:
            asyncio.run(self._drain_stream(probe_prompt, credentials))
        except AIServiceError as exc:
            stream_error = str(exc)
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "model": normalized_model,
            "family": profile.family,
            "official": profile.official,
            "thinking_returned": bool(thinking),
            "usage": usage,
            "stream_ok": not stream_error,
            "message": self._test_message(parsed, stream_error),
        }

    @staticmethod
    def _test_message(parsed: dict[str, Any], stream_error: str) -> str:
        head = "连接成功"
        if parsed.get("ok") is not True:
            head += "（模型输出与约定略有出入，但 JSON 通道正常）"
        if not stream_error:
            return f"{head}；流式通道也正常"
        return f"{head}；但流式通道失败：{stream_error}（网页解析会自动回退到非流式）"

    async def _drain_stream(self, system_prompt: str, credentials: AIProviderCredentials) -> None:
        async for _event in self._chat_stream_json(system_prompt, "ping", credentials=credentials):
            pass

    @staticmethod
    def _chat_completions_url(base_url: str) -> str:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/chat/completions"):
            return normalized
        return f"{normalized}/chat/completions"

    @staticmethod
    def _content_text(content: Any) -> str:
        """Normalize the content shapes returned by OpenAI-compatible APIs.

        Most providers return a string, while newer APIs may return a list of
        typed content blocks. Keeping this normalization here prevents the
        scheduling parser from depending on one provider's response shape.
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        if isinstance(content, dict):
            text = content.get("text") or content.get("content")
            return text if isinstance(text, str) else ""
        return ""

    @staticmethod
    def _parse_json_object(content: Any) -> tuple[dict[str, Any], str]:
        """Parse JSON even when a model adds reasoning or Markdown fences.

        ``response_format=json_object`` is advisory for several compatible
        gateways. The parser therefore removes common reasoning blocks and
        extracts the first valid JSON object from the response while still
        rejecting genuinely malformed output. Stripped ``<think>`` blocks come
        back as the second element so the interpret flow can show the model's
        reasoning instead of silently discarding it.
        """
        if isinstance(content, dict):
            return content, ""
        text = AIService._content_text(content).strip()
        if not text:
            raise AIServiceError("AI 模型返回了空内容")
        think_blocks = re.findall(r"<think>(.*?)</think>", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
        fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
        candidates = [*fenced, text]
        decoder = json.JSONDecoder()
        thinking = "\n".join(block.strip() for block in think_blocks if block.strip())
        for candidate in candidates:
            candidate = candidate.strip()
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed, thinking
            for index, char in enumerate(candidate):
                if char != "{":
                    continue
                try:
                    parsed, _ = decoder.raw_decode(candidate[index:])
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed, thinking
        preview = re.sub(r"\s+", " ", text)[:240]
        raise AIServiceError(f"AI 模型输出不是合法 JSON，收到内容：{preview}")

    @staticmethod
    def _upstream_error_detail(raw: bytes | str | dict[str, Any]) -> str:
        """提取 OpenAI-compatible 错误响应体里的 error.message，流式与非流式共用。"""
        try:
            payload = raw if isinstance(raw, dict) else json.loads(raw)
        except (TypeError, ValueError):
            return ""
        if not isinstance(payload, dict):
            return ""
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or "")
        if error:
            return str(error)
        return ""

    def _post_chat(
        self,
        credentials: AIProviderCredentials,
        profile: ProviderProfile,
        system_prompt: str,
        user_content: str,
    ) -> dict[str, Any]:
        """发一次非流式请求，返回解码后的响应体；HTTP 与解码错误统一成 AIServiceError。"""
        body = build_chat_body(
            profile,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            stream=False,
            reasoning_effort=credentials.reasoning_effort,
        )
        try:
            response = httpx.post(
                self._chat_completions_url(credentials.base_url),
                headers={
                    "Authorization": f"Bearer {credentials.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.settings.ai_request_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = self._upstream_error_detail(exc.response.content)
            suffix = f"：{detail[:300]}" if detail else ""
            raise AIServiceError(f"AI 模型请求返回 {exc.response.status_code}{suffix}") from exc
        except (httpx.HTTPError, httpx.InvalidURL, UnicodeError) as exc:
            raise AIServiceError(f"AI 模型请求失败：{exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" in content_type or response.text.lstrip().lower().startswith(
                ("<!doctype html", "<html")
            ):
                raise AIServiceError(
                    "AI 接口返回了网页而不是模型 JSON；请填写 API 基础地址"
                    "（通常以 /v1 结尾），不要填写管理控制台地址"
                ) from exc
            raise AIServiceError("AI 模型响应不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise AIServiceError("AI 模型响应不是合法 JSON")
        return payload

    @staticmethod
    def _empty_reason(finish_reason: Any) -> str:
        if finish_reason == "length":
            return "AI 模型输出被截断（finish_reason=length）：请调低思考强度或缩短输入后重试"
        return "AI 模型返回了空内容"

    @staticmethod
    def _log_usage(
        credentials: AIProviderCredentials,
        profile: ProviderProfile,
        usage: dict[str, Any],
        elapsed: float,
    ) -> None:
        """记一行用量：缓存命中数是判断提示词前缀稳不稳定的唯一依据。"""
        logger.info(
            "AI 调用完成 model=%s family=%s official=%s prompt=%s cached=%s completion=%s "
            "reasoning=%s elapsed=%.1fs",
            credentials.model,
            profile.family,
            profile.official,
            usage.get("prompt_tokens"),
            usage.get("cached_tokens"),
            usage.get("completion_tokens"),
            usage.get("reasoning_tokens"),
            elapsed,
        )

    def _chat_json(
        self,
        system_prompt: str,
        user_content: str,
        *,
        credentials: AIProviderCredentials | None = None,
    ) -> tuple[dict[str, Any], str | None, dict[str, Any]]:
        """向 OpenAI-compatible 接口要一个 JSON 对象，返回 (解析结果, 思考文本, token 用量)。

        解析指令与结果解释共用这一条通道：错误分支、think 块清洗、围栏 JSON
        的处理只应该有一份实现。思考文本由 reasoning_content 与被剥离的
        <think> 块拼接而来，两者都没有时为 None，供解析链路透出展示。
        各家发包差异（思考参数、max_tokens、空内容重试）见 ai_providers。
        """
        credentials = credentials or self.credentials()
        if credentials.provider != "openai_compatible":
            raise AIServiceError(f"暂不支持 AI 提供商：{credentials.provider}")
        profile = resolve_profile(credentials.base_url, credentials.model)
        attempts = 2 if profile.retry_on_empty else 1
        started = time.perf_counter()
        for attempt in range(attempts):
            payload = self._post_chat(credentials, profile, system_prompt, user_content)
            try:
                choices = payload["choices"]
                message = choices[0]["message"]
                finish_reason = choices[0].get("finish_reason")
                reasoning = message.get("reasoning_content")
                content = message.get("content")
            except (KeyError, IndexError, TypeError, AttributeError) as exc:
                raise AIServiceError("AI 模型响应缺少 choices[0].message.content") from exc
            has_content = bool(self._content_text(content).strip())
            if not has_content and profile.reasoning_may_hold_answer:
                # 兜底保持兼容：个别认不出厂商的推理模型把最终答案放进 reasoning_content。
                # DeepSeek / GLM 的 reasoning_content 只是思考草稿，不能当答案。
                content = reasoning
                has_content = bool(self._content_text(content).strip())
            if has_content or attempt + 1 >= attempts:
                break
            # DeepSeek 的 JSON 输出偶尔返回空内容（官方文档承认）：重试一次。
            logger.warning("AI 模型返回了空内容，重试一次 model=%s", credentials.model)
        usage = normalize_usage(credentials.model, payload.get("usage"))
        self._log_usage(credentials, profile, usage, time.perf_counter() - started)
        if not has_content:
            raise AIServiceError(self._empty_reason(finish_reason))
        parsed, think_text = self._parse_json_object(content)
        thinking_parts: list[str] = []
        if isinstance(reasoning, str) and reasoning.strip() and content is not reasoning:
            thinking_parts.append(reasoning.strip())
        if think_text:
            thinking_parts.append(think_text)
        thinking = "\n\n".join(thinking_parts) if thinking_parts else None
        return parsed, thinking, usage

    @staticmethod
    def _partial_tag_length(text: str, tag: str) -> int:
        """text 末尾可能是 tag 前缀的最长长度，供流式 <think> 标签跨 chunk 拼接。"""
        for size in range(min(len(text), len(tag) - 1), 0, -1):
            if text.endswith(tag[:size]):
                return size
        return 0

    async def _chat_stream_json(
        self,
        system_prompt: str,
        user_content: str,
        *,
        credentials: AIProviderCredentials | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """流式版本的 _chat_json：边读边产出思考增量，结束时给一个解析结果。

        事件序列：若干 ``("thinking", delta)``，最后恰好一条
        ``("result", {"parsed", "usage", "thinking"})``。思考增量来自
        reasoning_content 与 content 里的 <think> 块（标签可能被 chunk 截断），
        content 原文整体累积，结束后仍走 _parse_json_object 清洗校验，
        与同步通道共享同一套解析语义。基于 httpx.AsyncClient 实现，
        供 async 端点直接 await，不允许退化为事件循环内的阻塞请求。
        content 为空时不在这里重试：流式失败由前端自动回退到同步接口，那里有 DeepSeek 的空内容重试。
        """
        credentials = credentials or self.credentials()
        if credentials.provider != "openai_compatible":
            raise AIServiceError(f"暂不支持 AI 提供商：{credentials.provider}")
        profile = resolve_profile(credentials.base_url, credentials.model)
        started = time.perf_counter()
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        usage: dict[str, Any] | None = None
        finish_reason: Any = None
        # <think> 块拆分器状态：是否处于块内，以及可能被截断的半个标签。
        inside_think = False
        tag_buffer = ""

        def feed_content(delta: str) -> str:
            nonlocal inside_think, tag_buffer
            tag_buffer += delta
            thinking = ""
            while tag_buffer:
                if inside_think:
                    end = tag_buffer.find("</think>")
                    if end >= 0:
                        thinking += tag_buffer[:end]
                        tag_buffer = tag_buffer[end + len("</think>"):]
                        inside_think = False
                        continue
                    keep = self._partial_tag_length(tag_buffer, "</think>")
                    thinking += tag_buffer[: len(tag_buffer) - keep]
                    tag_buffer = tag_buffer[len(tag_buffer) - keep:]
                    break
                start = tag_buffer.find("<think>")
                if start >= 0:
                    tag_buffer = tag_buffer[start + len("<think>"):]
                    inside_think = True
                    continue
                keep = self._partial_tag_length(tag_buffer, "<think>")
                tag_buffer = tag_buffer[len(tag_buffer) - keep:]
                break
            return thinking

        try:
            timeout = self.settings.ai_request_timeout_seconds
            async with (
                httpx.AsyncClient(timeout=timeout) as client,
                client.stream(
                    "POST",
                    self._chat_completions_url(credentials.base_url),
                    headers={
                        "Authorization": f"Bearer {credentials.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=build_chat_body(
                        profile,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                        stream=True,
                        reasoning_effort=credentials.reasoning_effort,
                    ),
                ) as response,
            ):
                if response.status_code >= 400:
                    body = await response.aread()
                    detail = self._upstream_error_detail(body)
                    suffix = f"：{detail[:300]}" if detail else ""
                    raise AIServiceError(f"AI 模型请求返回 {response.status_code}{suffix}")
                async for line in response.aiter_lines():
                    data = line[5:].strip() if line.startswith("data:") else ""
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    raw_usage = chunk.get("usage")
                    if isinstance(raw_usage, dict):
                        usage = raw_usage
                    choices = chunk.get("choices")
                    delta = (
                        choices[0].get("delta")
                        if isinstance(choices, list)
                        and choices
                        and isinstance(choices[0], dict)
                        else None
                    )
                    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                        finish_reason = choices[0].get("finish_reason") or finish_reason
                    if not isinstance(delta, dict):
                        continue
                    reasoning = delta.get("reasoning_content")
                    if isinstance(reasoning, str) and reasoning:
                        reasoning_parts.append(reasoning)
                        yield "thinking", reasoning
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        content_parts.append(content)
                        piece = feed_content(content)
                        if piece:
                            yield "thinking", piece
        except (httpx.HTTPError, httpx.InvalidURL, UnicodeError) as exc:
            raise AIServiceError(f"AI 模型请求失败：{exc}") from exc

        raw_content = "".join(content_parts).strip()
        reasoning_text = "".join(reasoning_parts).strip()
        if not raw_content and reasoning_text and profile.reasoning_may_hold_answer:
            # 兜底保持兼容：个别认不出厂商的推理模型把答案放进 reasoning_content（同 _chat_json）。
            raw_content = reasoning_text
            reasoning_thinking = None
        elif not raw_content:
            raise AIServiceError(self._empty_reason(finish_reason))
        else:
            reasoning_thinking = reasoning_text or None
        normalized_usage = normalize_usage(credentials.model, usage)
        self._log_usage(credentials, profile, normalized_usage, time.perf_counter() - started)
        parsed, think_text = self._parse_json_object(raw_content)
        thinking_parts: list[str] = []
        if reasoning_thinking:
            thinking_parts.append(reasoning_thinking)
        if think_text:
            thinking_parts.append(think_text)
        thinking = "\n\n".join(thinking_parts) if thinking_parts else None
        yield "result", {
            "parsed": parsed,
            "usage": normalized_usage,
            "thinking": thinking,
        }

    def explain_solver_run(self, facts: dict[str, Any]) -> dict[str, Any]:
        """把确定性事实包翻译成教务能读的解释，并做一次意图核对。

        模型只做翻译和意图核对两件事。是否有硬冲突由 CP-SAT 与
        ``tasks.count_hard_conflicts`` 确定性给出，prompt 里明确禁止模型改写这个结论——
        用模型复核一个确定性组件的输出只会降低可靠性，评审时也答不上来。
        """
        system_prompt = (
            "你是高途线下校区排课系统的结果解读助手。输入是一次 CP-SAT 求解的确定性事实包，"
            "你的唯一任务是把它翻译成教务人员读得懂的中文，并做一次意图核对。\n"
            "硬性要求：\n"
            "1. 只能使用事实包里已有的信息。禁止臆造任何数字、班级、教师、教室或日期。\n"
            "2. 禁止判断排课结果是否正确、是否存在冲突。冲突数量已由确定性组件算出，"
            "直接引用 schedule.metrics，不要自行推断或改写。\n"
            "3. conflicts.rules 里的 meaning 已经是业务口径，据此解释为什么排不下，"
            "不要照搬 SYSTEM-* 这类内部标识。\n"
            "4. 若 run.presolve_infeasible 为 true，必须说明这是求解前的数据预检结论，"
            "CP-SAT 没有运行，不能表述为「已证明无解」。\n"
            "5. intent_review 是把 request_scope.instruction 的原始意图和实际结果对照："
            "verdict 取 matched / deviated / unclear；"
            "没有 instruction 时取 unclear 且 concerns 为空。"
            "重点关注「约束层没违规但意图被违背」的情况，"
            "例如用户要求别动上课时间而结果改了时段。\n"
            "只输出一个 JSON 对象，不要输出 Markdown 或额外字段。\n"
            "输出结构："
            '{"headline":"一句话结论","explanation":["分条说明"],'
            '"next_actions":["教务下一步可以做什么"],'
            '"intent_review":{"verdict":"matched","concerns":[]}}'
        )
        facts_json = json.dumps(facts, ensure_ascii=False)
        parsed, _thinking, usage = self._chat_json(system_prompt, facts_json)
        return {
            "headline": str(parsed.get("headline") or ""),
            "explanation": [str(item) for item in (parsed.get("explanation") or [])],
            "next_actions": [str(item) for item in (parsed.get("next_actions") or [])],
            "intent_review": self._normalize_intent_review(parsed.get("intent_review")),
            "source": "ai",
            "usage": usage,
        }

    @staticmethod
    def _normalize_intent_review(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        verdict = str(value.get("verdict") or "unclear")
        if verdict not in {"matched", "deviated", "unclear"}:
            verdict = "unclear"
        concerns = value.get("concerns")
        return {
            "verdict": verdict,
            "concerns": [str(item) for item in concerns] if isinstance(concerns, list) else [],
        }

    def map_import_columns(
        self,
        columns: list[dict[str, Any]],
        valid_targets: Sequence[str],
    ) -> dict[int, str]:
        """导入向导的语义兜底层：把表头机械匹配失败的列交给模型提名。

        模型可以对拿不准的列弃权（target=null），只输出候选目标字段列表内的字段。
        这里只做白名单校验：列下标不存在、目标不在候选里、结构不对的输出一律丢弃——
        模型只提名，映射决定权仍在向导和用户手里。
        """
        system_prompt = (
            "你是排课系统导入向导的列映射助手。用户的表格里有一些未能通过表头匹配的列，"
            "请根据列名和样本值，把它们映射到候选目标字段。\n"
            "硬性要求：\n"
            "1. 只输出一个 JSON 对象，不要输出 Markdown 或额外字段。\n"
            "2. target 只能取候选目标字段的原文；拿不准就输出 null（弃权），禁止硬猜。\n"
            "3. column_index 必须原样引用输入里的列下标。\n"
            "输出结构："
            '{"mappings":[{"column_index":0,"target":"目标字段或null"}]}'
        )
        user_content = json.dumps(
            {
                "候选目标字段": list(valid_targets),
                "列": [
                    {
                        "column_index": item.get("column_index"),
                        "column": item.get("column"),
                        "样本值": item.get("样本值") or [],
                    }
                    for item in columns
                ],
            },
            ensure_ascii=False,
        )
        parsed, _thinking, _usage = self._chat_json(system_prompt, user_content)
        known_indexes = {
            item.get("column_index")
            for item in columns
            if isinstance(item.get("column_index"), int)
        }
        accepted: dict[int, str] = {}
        for entry in parsed.get("mappings") or []:
            if not isinstance(entry, dict):
                continue
            column_index = entry.get("column_index")
            target = entry.get("target")
            if (
                isinstance(column_index, int)
                and column_index in known_indexes
                and isinstance(target, str)
                and target in valid_targets
            ):
                accepted[column_index] = target
        return accepted

    @staticmethod
    def _interpret_system_prompt(context: dict[str, Any]) -> str:
        return (
            "你是高途线下校区的排课指令解析 AI。根据业务候选值和固定约束，把用户指令转换为 JSON。"
            "只输出一个 JSON 对象，不要输出 Markdown、解释或额外字段。"
            "业务线、产品班型、班级标识只能使用候选值；未指定的范围输出空数组。"
            "日期使用 YYYY-MM-DD；未指定时输出 null。date_window_days 是 0 到 31 的整数。"
            "recognized_rules 只能从 fixed_rule_labels 中选择。"
            "逐项核对原指令，把结构化字段、固定标签、task_constraints、memory_actions 都"
            "未覆盖的要求原文列入 unsupported_requirements；"
            "指定教室、连续几节等要求禁止用通用标签冒充已实现。"
            "具体教师/教室/班级的禁排要求能用候选值（teachers/time_slots）结构化时，"
            "只放进 task_constraints，【不要】再抄一份进 unsupported_requirements；"
            "只有主体或时段无法从候选值唯一确定时，才把该要求原文列入 unsupported_requirements。"
            "task_constraints 把【本次任务】的具体禁排/指定要求结构化：subject_type 只能取"
            " teacher|classroom|cohort；subject_ids 与 slot_business_ids 只能使用候选值"
            "（teachers/time_slots）；「这次不能上」「这次避开」这类本次要求输出 hardness=hard；"
            "source_text 必须逐字摘录用户原话中对应该约束的片段，不得改写或概括"
            "（后端靠它判断这句话已被结构化，改写会导致该要求被当作未覆盖）；"
            "长期偏好不要放进 task_constraints，改输出 memory_actions"
            "（其 source_text 同样逐字摘录）。"
            "续办时输入上下文 task_context.active_task_constraints 列出任务里已有的要求，"
            "每条有稳定的 id 与 hardness；task_constraints 的每一条用 op 说明它对既有要求做什么："
            "op=add 表示追加新要求（用户说「另外/也/再加/还有」，默认值）；op=replace 表示修改"
            "某一条既有要求（用户说「改成/换成/改到」），此时 target_id 必须填那一条的 id，"
            "并在 subject_ids/slot_business_ids 里给出修改后的内容；op=remove 表示取消某一条"
            "既有要求（用户说「不用了/取消」），target_id 填那一条的 id。"
            "无法确定指的是哪一条时一律用 op=add 或不输出，禁止猜 target_id；"
            "target_id 只能取 active_task_constraints 里出现的 id，不要用自己的序号 id。"
            "memory_actions 判定：用户原话出现明确命令式声明且主体与时段可从候选唯一确定 →"
            ' basis="explicit"；其余（归纳口吻、主体模糊、需要跨句推断）→ basis="inferred"。'
            "显式声明词表（原话出现才算 explicit）：记录类 "
            f"{'｜'.join(EXPLICIT_SAVE_WORDS)}；撤销/失效类 {'｜'.join(EXPLICIT_EXPIRE_WORDS)}；"
            f"纠正类 {'｜'.join(EXPLICIT_CORRECT_WORDS)}。"
            "save_preference 的 subject_id/predicate/constraint 按候选口径填写"
            "（predicate 如 avoid_slot，constraint 如 {\"slot_ids\":[\"S05\"]}）；"
            "expire_preference/update_preference 的 target_entry_id 只能取输入上下文"
            " active_preferences 里出现的 id，禁止编造；纠正为「只是临时请假」时"
            " target_status=rejected 且 rejection_reason=temporary_leave。"
            "业务事实：不同产品线并行运营；课程教师（教研组）与固定开始/结束时间保持原数据；"
            "日期与教室允许重新编排；同一教室和同一具体日程账号的真实时间区间不可重叠；"
            "每个班级的课次号独立编号且允许跳号。\n"
            "输出结构："
            '{"business_lines":[],"product_types":[],"class_business_ids":[],'
            '"date_from":null,"date_to":null,"date_window_days":7,'
            '"recognized_rules":[],"task_constraints":[],"memory_actions":[],'
            '"unsupported_requirements":[]}\n'
            # 上面全是跨请求不变的规则；下面才是随请求变化的内容。DeepSeek / GLM 的上下文缓存都按
            # 请求前缀命中，变化的内容越靠后，能复用的前缀越长：先放同一方案下稳定的候选值，再放
            # 随本次指令变化的偏好与任务上下文，最后才是每天都变的日期。
            f"输入上下文：{json.dumps(AIService._ordered_context(context), ensure_ascii=False)}\n"
            f"当前日期为 {datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()}，"
            "用户提到今天、明天、下周等相对日期时，转换成明确的 YYYY-MM-DD。"
        )

    # 解析上下文里同一方案下基本不变的键，放在 JSON 最前面以延长可缓存的前缀。
    _STABLE_CONTEXT_KEYS = (
        "business_lines",
        "product_types",
        "class_business_ids",
        "teachers",
        "time_slots",
        "fixed_rule_labels",
    )

    @staticmethod
    def _ordered_context(context: dict[str, Any]) -> dict[str, Any]:
        stable = {key: context[key] for key in AIService._STABLE_CONTEXT_KEYS if key in context}
        return {**stable, **{key: value for key, value in context.items() if key not in stable}}

    def interpret_instruction(
        self,
        instruction: str,
        *,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        """解析排课指令，返回 (结构化结果, 模型思考文本)。"""
        parsed, thinking, _usage = self._chat_json(
            self._interpret_system_prompt(context), instruction
        )
        return parsed, thinking

    async def stream_interpret_instruction(
        self,
        instruction: str,
        *,
        context: dict[str, Any],
    ) -> AsyncIterator[tuple[str, Any]]:
        """interpret_instruction 的流式版本，事件序列见 _chat_stream_json。"""
        async for event in self._chat_stream_json(
            self._interpret_system_prompt(context), instruction
        ):
            yield event

    def mine_preferences(
        self, events: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """从近期调课事件中归纳偏好候选，返回 (候选数组, token 用量)。

        模型只做归纳提名：允许整轮弃权（candidates 为空数组），逐条候选必须
        引用输入事件 id 作为证据，且满足「≥2 条同主体证据 + 约束来自证据」的
        契约（MEM-C2 修正 3/5）；主体归属、证据引用与结构校验在调用方
        ``validate_ai_candidates`` 完成——与列映射同一原则：模型提名，代码裁决。
        模型可选输出 ``reasons`` 字段（为何弃权/为何不足证据），原样随 usage
        返回并落 provenance，便于失败可解释。
        """
        system_prompt = (
            "你是排课系统的偏好挖掘助手。输入是本学期的一批调课事件（JSON 数组），"
            "每条包含事件类型、涉及主体（教师/教室/课程）的业务标识、时段、日期范围，"
            "以及教务声明的原因 declared_reason（可能为空）。\n"
            "输入已经过后端预筛（MEM-D1）：只含方案内、最近时间窗、候选未被取消、"
            "declared_reason 非临时被迫类的可学习事件——你不需要也不应该再做这层"
            "过滤，只需在这批输入内找模式。\n"
            "任务：找出同一主体反复出现的调课模式，归纳为结构化偏好候选。\n"
            "硬性要求：\n"
            "1. 只输出一个 JSON 对象，不要输出 Markdown 或额外字段。\n"
            "2. 结构：{\"candidates\":[...]}。没有把握就输出空数组（允许弃权），禁止硬凑；"
            "弃权或证据不足时可在同层输出 {\"reasons\":[\"一句话原因\"]} 说明。\n"
            "3. 每条候选：{\"subject_type\":\"teacher|classroom|cohort|course\","
            "\"subject_id\":\"输入事件里的业务标识原值\","
            "\"predicate\":\"avoid_slot|prefer_slot|avoid_room|prefer_room|max_daily_load|consecutive_sessions\","
            "\"constraint\":{...},\"evidence_ids\":[\"事件 id\"],\"rationale\":\"一句话依据\"}。\n"
            "4. 每条候选必须引用至少两条【同一主体】的事件作为证据（evidence_ids 里的"
            "事件主体必须与 subject_type/subject_id 完全一致）；禁止把 A 主体的事件当"
            "B 主体的证据，禁止凭单条事件臆测。\n"
            "5. evidence_ids 只能引用输入事件里存在的 id；constraint 里的时段/教室/日期"
            "必须原样来自被引用的证据事件（时段在其 slot_business_ids 内、教室为其"
            "room_business_id、日期落在其 date_from~date_to 内），禁止引入输入之外的目标。\n"
            "输出结构："
            '{"candidates":[{"subject_type":"teacher","subject_id":"郑州考研英语教研组",'
            '"predicate":"avoid_slot","constraint":{"slot_ids":["S-周三-1800"]},'
            '"evidence_ids":["evt-1","evt-2"],"rationale":"该教师多次周三晚调课"}],"reasons":[]}'
        )
        parsed, _thinking, usage = self._chat_json(
            system_prompt, json.dumps(events, ensure_ascii=False)
        )
        raw_candidates = parsed.get("candidates")
        if not isinstance(raw_candidates, list):
            raise AIServiceError("AI 偏好挖掘输出缺少 candidates 数组")
        reasons = parsed.get("reasons")
        if isinstance(reasons, list) and reasons:
            # 失败可解释（MEM-C2 修正 5）：模型的自述原因随 usage 落 provenance。
            usage = {**usage, "reasons": [str(item) for item in reasons if item]}
        return [item for item in raw_candidates if isinstance(item, dict)], usage
