from __future__ import annotations

import json
import os
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


class AIServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class AIProviderCredentials:
    provider: str
    base_url: str
    api_key: str
    model: str
    source: str


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
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AIServiceError("AI 接口地址必须是完整的 HTTP 或 HTTPS 地址")
        return normalized

    def _environment_configuration(self) -> AIProviderCredentials | None:
        if not self.settings.ai_environment_configured:
            return None
        return AIProviderCredentials(
            provider=self.settings.ai_provider.strip() or "openai_compatible",
            base_url=self._validate_url(self.settings.ai_base_url),
            api_key=self.settings.ai_api_key.strip(),
            model=self.settings.ai_model.strip(),
            source="environment",
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
        )

    def configuration_view(self) -> dict[str, Any]:
        environment = self._environment_configuration()
        if environment is not None:
            return {
                "configured": True,
                "source": "environment",
                "provider": environment.provider,
                "base_url": environment.base_url,
                "api_key_configured": True,
                "model": environment.model,
            }
        stored = self._stored_configuration()
        if stored is not None:
            return {
                "configured": True,
                "source": "frontend",
                "provider": stored.provider,
                "base_url": stored.base_url,
                "api_key_configured": bool(stored.api_key_encrypted),
                "model": stored.model,
            }
        return {
            "configured": False,
            "source": "none",
            "provider": None,
            "base_url": None,
            "api_key_configured": False,
            "model": None,
        }

    def save_configuration(
        self,
        user_id: str,
        *,
        provider: str,
        base_url: str,
        api_key: str | None,
        model: str,
    ) -> AIProviderConfiguration:
        if self._environment_configuration() is not None:
            raise AIServiceError("当前 AI 模型接口由部署环境统一管理")
        normalized_provider = provider.strip()
        if normalized_provider != "openai_compatible":
            raise AIServiceError("当前仅支持 OpenAI-compatible 接口")
        normalized_url = self._validate_url(base_url)
        normalized_key = (api_key or "").strip()
        normalized_model = model.strip()
        if not normalized_model:
            raise AIServiceError("必须填写模型名称")

        stored = self._stored_configuration()
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
        stored.configured_by = user_id
        if normalized_key:
            stored.api_key_encrypted = self._cipher().encrypt(normalized_key)
        self.db.commit()
        self.db.refresh(stored)
        return stored

    @staticmethod
    def _chat_completions_url(base_url: str) -> str:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/chat/completions"):
            return normalized
        return f"{normalized}/chat/completions"

    def interpret_instruction(
        self,
        instruction: str,
        *,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        credentials = self.credentials()
        if credentials.provider != "openai_compatible":
            raise AIServiceError(f"暂不支持 AI 提供商：{credentials.provider}")

        system_prompt = (
            "你是高途线下校区的排课指令解析 AI。根据业务候选值和固定约束，把用户指令转换为 JSON。"
            "只输出一个 JSON 对象，不要输出 Markdown、解释或额外字段。"
            "业务线、产品班型、班级标识只能使用候选值；未指定的范围输出空数组。"
            "日期使用 YYYY-MM-DD；未指定时输出 null。date_window_days 是 0 到 31 的整数。"
            "recognized_rules 只能从 fixed_rule_labels 中选择。"
            "业务事实：不同产品线并行运营；课程教师（教研组）与固定开始/结束时间保持原数据；"
            "日期与教室允许重新编排；同一教室和同一具体日程账号的真实时间区间不可重叠；"
            "每个班级的课次号独立编号且允许跳号。\n"
            f"当前日期为 {datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()}，"
            "用户提到今天、明天、下周等相对日期时，转换成明确的 YYYY-MM-DD。\n"
            f"输入上下文：{json.dumps(context, ensure_ascii=False)}\n"
            "输出结构："
            '{"business_lines":[],"product_types":[],"class_business_ids":[],'
            '"date_from":null,"date_to":null,"date_window_days":7,'
            '"recognized_rules":[]}'
        )
        try:
            response = httpx.post(
                self._chat_completions_url(credentials.base_url),
                headers={
                    "Authorization": f"Bearer {credentials.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": credentials.model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": instruction},
                    ],
                },
                timeout=self.settings.ai_request_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                payload = exc.response.json()
                if isinstance(payload, dict):
                    error = payload.get("error")
                    if isinstance(error, dict):
                        detail = str(error.get("message") or "")
                    elif error:
                        detail = str(error)
            except (ValueError, TypeError):
                pass
            suffix = f"：{detail[:300]}" if detail else ""
            raise AIServiceError(f"AI 模型请求返回 {exc.response.status_code}{suffix}") from exc
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI 模型请求失败：{exc}") from exc

        try:
            payload = response.json()
            choices = payload["choices"]
            content = choices[0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIServiceError("AI 模型响应缺少 choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise AIServiceError("AI 模型返回了空内容")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AIServiceError("AI 模型输出不是合法 JSON") from exc
        if not isinstance(parsed, dict):
            raise AIServiceError("AI 模型输出必须是 JSON 对象")
        return parsed
