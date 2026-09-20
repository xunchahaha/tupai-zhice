"""企业微信适配器：智能表格 / 日程 / 应用消息三能力，审批明确不支持。

v1 凭据走设置页直填（CredentialStore，无管理端 OAuth 流，二期补装）。平台
约束（roadmap §2.1 调研结论）：服务端 API 无审批代发起接口，故 manifest 不
声明 APPROVAL；日程只能落在应用自建日历下；verify() 与钉钉同样用获取
access_token 做轻量探测。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..base import Capability, IntegrationManifest, VerifyResult
from ..credentials import CredentialStore
from .client import DEFAULT_BASE_URL, WeComClient, WeComConfig, WeComError

if TYPE_CHECKING:
    import httpx
    from sqlalchemy.orm import Session

    from ...config import Settings

UNCONFIGURED_DETAIL = "请先在「设置 → 集成」填写企业微信 corpid、应用 secret 与 AgentId"


class WeComAdapter:
    """企业微信集成适配器：凭据齐全即声明表/日历/通知三能力。"""

    manifest: ClassVar[IntegrationManifest] = IntegrationManifest(
        id="wecom",
        name="企业微信",
        description=(
            "企业微信智能表格记录读写、日程创建（仅应用自建日历）与应用消息通知"
            "（≤1000 人/次自动分批）；平台服务端 API 无审批代发起，审批不支持。"
        ),
        capabilities=frozenset(
            {Capability.TABLE_STORE, Capability.CALENDAR, Capability.NOTIFIER}
        ),
        status_class="available",
        docs_url="docs/integrations/wecom.md",
        config_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "title": "企业微信集成配置",
            "required": ["corp_id", "corp_secret", "agent_id"],
            "properties": {
                "corp_id": {
                    "type": "string",
                    "title": "企业 ID（corpid）",
                    "description": "企业微信管理后台 → 我的企业 → 企业信息",
                },
                "corp_secret": {
                    "type": "string",
                    "title": "应用密钥（secret）",
                    "description": (
                        "自建应用的 Secret；加密存储，回显只显示是否已配置，留空表示保持不变"
                    ),
                },
                "agent_id": {
                    "type": "integer",
                    "title": "应用 AgentId",
                    "description": "自建应用的 AgentId，用于日程归属与应用消息发送",
                },
                "base_url": {
                    "type": "string",
                    "title": "API 基地址",
                    "default": DEFAULT_BASE_URL,
                    "description": "一般保持默认，仅联调代理等特殊场景需要修改",
                },
            },
        },
    )

    def __init__(
        self,
        settings: Settings,
        db: Session,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self._transport = transport

    @staticmethod
    def _int_or_zero(value: object) -> int:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return 0

    def _client(self) -> WeComClient | None:
        """已保存且必填凭据齐全时返回客户端，否则 None（未配置）。"""

        stored = CredentialStore(self.settings, self.db).load("wecom")
        if stored is None:
            return None
        merged = stored.merged()
        corp_id = str(merged.get("corp_id") or "").strip()
        corp_secret = str(merged.get("corp_secret") or "").strip()
        agent_id = self._int_or_zero(merged.get("agent_id"))
        if not corp_id or not corp_secret or agent_id <= 0:
            return None
        return WeComClient(
            WeComConfig(
                corp_id=corp_id,
                corp_secret=corp_secret,
                agent_id=agent_id,
                base_url=str(merged.get("base_url") or "").strip() or DEFAULT_BASE_URL,
            ),
            transport=self._transport,
        )

    def capabilities(self) -> set[Capability]:
        """运行时能力协商：凭据未配置齐全时不声明任何能力。"""

        if self._client() is None:
            return set()
        return set(self.manifest.capabilities)

    async def verify(self) -> VerifyResult:
        """轻量探测：获取一次 access_token（带缓存），失败以结果表达不抛异常。"""

        client = self._client()
        if client is None:
            return VerifyResult(ok=False, detail=UNCONFIGURED_DETAIL)
        try:
            await client.access_token()
        except WeComError as exc:
            return VerifyResult(ok=False, detail=str(exc))
        return VerifyResult(ok=True, detail="企业微信 access_token 获取成功，凭据有效。")
