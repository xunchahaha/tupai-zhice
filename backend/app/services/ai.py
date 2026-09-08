from __future__ import annotations

import json
import os
import re
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
    def _parse_json_object(content: Any) -> dict[str, Any]:
        """Parse JSON even when a model adds reasoning or Markdown fences.

        ``response_format=json_object`` is advisory for several compatible
        gateways. The parser therefore removes common reasoning blocks and
        extracts the first valid JSON object from the response while still
        rejecting genuinely malformed output.
        """
        if isinstance(content, dict):
            return content
        text = AIService._content_text(content).strip()
        if not text:
            raise AIServiceError("AI 模型返回了空内容")
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
        fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
        candidates = [*fenced, text]
        decoder = json.JSONDecoder()
        for candidate in candidates:
            candidate = candidate.strip()
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
            for index, char in enumerate(candidate):
                if char != "{":
                    continue
                try:
                    parsed, _ = decoder.raw_decode(candidate[index:])
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
        preview = re.sub(r"\s+", " ", text)[:240]
        raise AIServiceError(f"AI 模型输出不是合法 JSON，收到内容：{preview}")

    def _chat_json(
        self, system_prompt: str, user_content: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """向 OpenAI-compatible 接口要一个 JSON 对象，返回 (解析结果, token 用量)。

        解析指令与结果解释共用这一条通道：错误分支、think 块清洗、围栏 JSON
        的处理只应该有一份实现。
        """
        credentials = self.credentials()
        if credentials.provider != "openai_compatible":
            raise AIServiceError(f"暂不支持 AI 提供商：{credentials.provider}")
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
                        {"role": "user", "content": user_content},
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

        try:
            choices = payload["choices"]
            message = choices[0]["message"]
            content = message.get("content")
            if content in (None, ""):
                content = message.get("reasoning_content")
        except (KeyError, IndexError, TypeError) as exc:
            raise AIServiceError("AI 模型响应缺少 choices[0].message.content") from exc
        raw_usage = payload.get("usage") if isinstance(payload, dict) else None
        usage = {
            "model": credentials.model,
            "prompt_tokens": (raw_usage or {}).get("prompt_tokens"),
            "completion_tokens": (raw_usage or {}).get("completion_tokens"),
            "total_tokens": (raw_usage or {}).get("total_tokens"),
        }
        return self._parse_json_object(content), usage

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
        parsed, usage = self._chat_json(system_prompt, json.dumps(facts, ensure_ascii=False))
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

    def interpret_instruction(
        self,
        instruction: str,
        *,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        system_prompt = (
            "你是高途线下校区的排课指令解析 AI。根据业务候选值和固定约束，把用户指令转换为 JSON。"
            "只输出一个 JSON 对象，不要输出 Markdown、解释或额外字段。"
            "业务线、产品班型、班级标识只能使用候选值；未指定的范围输出空数组。"
            "日期使用 YYYY-MM-DD；未指定时输出 null。date_window_days 是 0 到 31 的整数。"
            "recognized_rules 只能从 fixed_rule_labels 中选择。"
            "逐项核对原指令，把结构化字段和固定标签未覆盖的要求原文列入 unsupported_requirements；"
            "尤其是具体教师禁排、指定教室、连续几节等要求，禁止用通用标签冒充已实现。"
            "业务事实：不同产品线并行运营；课程教师（教研组）与固定开始/结束时间保持原数据；"
            "日期与教室允许重新编排；同一教室和同一具体日程账号的真实时间区间不可重叠；"
            "每个班级的课次号独立编号且允许跳号。\n"
            f"当前日期为 {datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()}，"
            "用户提到今天、明天、下周等相对日期时，转换成明确的 YYYY-MM-DD。\n"
            f"输入上下文：{json.dumps(context, ensure_ascii=False)}\n"
            "输出结构："
            '{"business_lines":[],"product_types":[],"class_business_ids":[],'
            '"date_from":null,"date_to":null,"date_window_days":7,'
            '"recognized_rules":[],"unsupported_requirements":[]}'
        )
        parsed, _usage = self._chat_json(system_prompt, instruction)
        return parsed
