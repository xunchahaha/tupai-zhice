"""本地模式适配器：内置 SQLite 存储 + 站内通知，默认可用（roadmap INT-2）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from .base import Capability, IntegrationManifest, VerifyResult

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..config import Settings


class LocalAdapter:
    """零平台依赖的默认集成，保证自托管试用零门槛（Paperless-ngx 模式）。

    v1 清单只声明 table_store 与 notifier 两项既有内置能力；能力接口
    （TableStore / Notifier Protocol）在二期收敛消费接缝时逐个接入。
    """

    manifest: ClassVar[IntegrationManifest] = IntegrationManifest(
        id="local",
        name="本地模式",
        description="无需任何平台凭据：数据存于内置 SQLite，通知走站内消息。",
        capabilities=frozenset({Capability.TABLE_STORE, Capability.NOTIFIER}),
        status_class="configured",
        docs_url="docs/integrations/local.md",
    )

    def __init__(self, settings: Settings, db: Session) -> None:
        self.settings = settings
        self.db = db

    def capabilities(self) -> set[Capability]:
        """本地模式恒可用，能力不随配置变化。"""

        return set(self.manifest.capabilities)

    async def verify(self) -> VerifyResult:
        return VerifyResult(ok=True, detail="本地模式默认可用，无需测试连接。")
