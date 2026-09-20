"""飞书适配器：纯委托 ``services/feishu.py``，不搬移内部逻辑（roadmap INT-3）。

2992 行生产验证过的 OAuth / 加密 / 幂等同步代码保持原样（strangler 迁移）；
本适配器只做三件事：声明 manifest 能力上限、运行时能力协商、统一 verify。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ...services.feishu import FeishuService
from ..base import Capability, IntegrationManifest, VerifyResult

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ...config import Settings

# 与 services/feishu.py 未配置时抛出的 FeishuServiceError 保持同一文案：
# calendar-publish 接缝在飞书未配置时对用户的提示不变（行为等价改造）。
UNCONFIGURED_DETAIL = "请先在当前页面填写飞书应用编号和应用密钥"


class FeishuAdapter:
    """飞书集成适配器：manifest 声明能力上限，capabilities() 做运行时协商。"""

    manifest: ClassVar[IntegrationManifest] = IntegrationManifest(
        id="feishu",
        name="飞书（Lark）",
        description=(
            "多维表格同步、教师日历下发、通知、审批代理与 Aily 自然语言；"
            "base_url 可切换 Lark 国际版。"
        ),
        capabilities=frozenset(
            {
                Capability.TABLE_STORE,
                Capability.CALENDAR,
                Capability.NOTIFIER,
                Capability.APPROVAL,
                Capability.NL,
            }
        ),
        status_class="available",
        docs_url="docs/integrations/feishu.md",
    )

    def __init__(self, settings: Settings, db: Session) -> None:
        self.settings = settings
        self.db = db

    def _service(self) -> FeishuService:
        return FeishuService(self.settings, self.db)

    def _app_configured(self) -> bool:
        # configuration_view 是现有连接状态查询的应用配置部分：
        # 环境变量或页面保存的应用配置任一存在即视为已配置。
        return bool(self._service().configuration_view()["configured"])

    def capabilities(self) -> set[Capability]:
        """运行时能力协商：应用未配置时不声明任何能力。"""

        if not self._app_configured():
            return set()
        return set(self.manifest.capabilities)

    async def verify(self) -> VerifyResult:
        """委托现有连接状态查询；只读本地配置，不发外部网络请求。"""

        view = self._service().configuration_view()
        if not view["configured"]:
            return VerifyResult(ok=False, detail=UNCONFIGURED_DETAIL)
        source = "部署环境" if view["source"] == "environment" else "页面配置"
        return VerifyResult(
            ok=True,
            detail=f"飞书应用配置已就绪（来源：{source}）；管理员授权状态见「设置 → 集成」。",
        )
