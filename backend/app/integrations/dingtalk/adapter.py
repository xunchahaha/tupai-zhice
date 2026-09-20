"""钉钉适配器：manifest 声明能力上限，capabilities() 按配置运行时协商。

v1 凭据走设置页直填（CredentialStore，无管理端 OAuth 流，二期补装）；审批
能力依赖可选的模板 process_code，未配置时运行时不声明 APPROVAL。verify() 用
获取 access_token 做轻量探测——与飞书的只读本地配置不同，钉钉没有等价的本地
探测点，token 请求即最低成本的凭据校验（带缓存，不产生频控压力）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from ..base import Capability, IntegrationManifest, VerifyResult
from ..credentials import CredentialStore
from .client import DEFAULT_BASE_URL, DingTalkClient, DingTalkConfig, DingTalkError

if TYPE_CHECKING:
    import httpx
    from sqlalchemy.orm import Session

    from ...config import Settings

UNCONFIGURED_DETAIL = "请先在「设置 → 集成」填写钉钉 AppKey 与 AppSecret"


class DingTalkAdapter:
    """钉钉集成适配器：表 / 日历 / 通知配置即用，审批按 process_code 协商。"""

    manifest: ClassVar[IntegrationManifest] = IntegrationManifest(
        id="dingtalk",
        name="钉钉",
        description=(
            "钉钉 AI 表格记录读写、日程、工作通知（≤100 人/次自动分批）"
            "与 OA 审批发起（需企业预建模板 process_code）。"
        ),
        capabilities=frozenset(
            {
                Capability.TABLE_STORE,
                Capability.CALENDAR,
                Capability.NOTIFIER,
                Capability.APPROVAL,
            }
        ),
        status_class="available",
        docs_url="docs/integrations/dingtalk.md",
        config_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "title": "钉钉集成配置",
            "required": ["app_key", "app_secret"],
            "properties": {
                "app_key": {
                    "type": "string",
                    "title": "AppKey",
                    "description": "钉钉企业内部应用的 AppKey（开放平台 → 应用凭证）",
                },
                "app_secret": {
                    "type": "string",
                    "title": "AppSecret",
                    "description": "应用密钥；加密存储，回显只显示是否已配置，留空表示保持不变",
                },
                "process_code": {
                    "type": "string",
                    "title": "审批模板 process_code",
                    "description": "可选：OA 审批模板编码；不填则不启用审批代发起能力",
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

    def _stored_config(self) -> dict[str, Any] | None:
        """已保存且必填凭据齐全时返回合并配置，否则 None（未配置）。"""

        stored = CredentialStore(self.settings, self.db).load("dingtalk")
        if stored is None:
            return None
        merged = stored.merged()
        app_key = str(merged.get("app_key") or "").strip()
        app_secret = str(merged.get("app_secret") or "").strip()
        if not app_key or not app_secret:
            return None
        return merged

    def _client(self) -> DingTalkClient | None:
        config = self._stored_config()
        if config is None:
            return None
        return DingTalkClient(
            DingTalkConfig(
                app_key=str(config["app_key"]).strip(),
                app_secret=str(config["app_secret"]).strip(),
                base_url=str(config.get("base_url") or "").strip() or DEFAULT_BASE_URL,
                process_code=str(config.get("process_code") or "").strip(),
            ),
            transport=self._transport,
        )

    def capabilities(self) -> set[Capability]:
        """已配置凭据即声明表/日历/通知；审批依赖可选的 process_code。"""

        client = self._client()
        if client is None:
            return set()
        negotiated = {Capability.TABLE_STORE, Capability.CALENDAR, Capability.NOTIFIER}
        if client.config.process_code:
            negotiated.add(Capability.APPROVAL)
        return negotiated

    async def verify(self) -> VerifyResult:
        """轻量探测：获取一次 access_token（带缓存），失败以结果表达不抛异常。"""

        client = self._client()
        if client is None:
            return VerifyResult(ok=False, detail=UNCONFIGURED_DETAIL)
        try:
            await client.access_token()
        except DingTalkError as exc:
            return VerifyResult(ok=False, detail=str(exc))
        return VerifyResult(ok=True, detail="钉钉 access_token 获取成功，凭据有效。")
