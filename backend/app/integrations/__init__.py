"""集成抽象层：飞书从「前提」降级为「适配器」（docs/roadmap/03-integrations.md）。

v1 边界：显式注册（不做 entry point）、清单端点只读 + 通用凭据配置端点
（钉钉/企业微信凭据按 config schema 直填，加密落库；管理端 OAuth 流与
integration_installations 安装表为二期）。新增一个集成的步骤见本包 README.md。
"""

from .base import (
    Capability,
    Integration,
    IntegrationManifest,
    IntegrationStatus,
    VerifyResult,
)
from .dingtalk import DingTalkAdapter
from .feishu.adapter import FeishuAdapter
from .local import LocalAdapter
from .registry import (
    IntegrationEntry,
    get_integration,
    get_integrations,
    has_capability,
    manifests,
    register,
    register_manifest,
)
from .wecom import WeComAdapter

# 内置适配器：import 即注册（顺序即目录，与 docs/roadmap/03-integrations.md 一致）。
register(LocalAdapter)
register(FeishuAdapter)
register(DingTalkAdapter)
register(WeComAdapter)

# planned 集成：仅 manifest、无适配器实例（社区共建中，能力声明为预期目标）。
register_manifest(
    IntegrationManifest(
        id="google_workspace",
        name="Google Workspace",
        description="Google Sheets 与 Google Calendar（社区共建中；无原生审批）。",
        capabilities=frozenset({Capability.TABLE_STORE, Capability.CALENDAR}),
        status_class="planned",
        docs_url="docs/integrations/google-workspace.md",
    )
)

__all__ = [
    "Capability",
    "DingTalkAdapter",
    "FeishuAdapter",
    "Integration",
    "IntegrationEntry",
    "IntegrationManifest",
    "IntegrationStatus",
    "LocalAdapter",
    "VerifyResult",
    "WeComAdapter",
    "get_integration",
    "get_integrations",
    "has_capability",
    "manifests",
    "register",
    "register_manifest",
]
