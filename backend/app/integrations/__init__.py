"""集成抽象层：飞书从「前提」降级为「适配器」（docs/roadmap/03-integrations.md）。

v1 边界：显式注册（不做 entry point）、只有只读清单端点（不做 verify 触发
端点、不做 integration_installations 表，均为二期）。新增一个集成的步骤见
本包 README.md。
"""

from .base import (
    Capability,
    Integration,
    IntegrationManifest,
    IntegrationStatus,
    VerifyResult,
)
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

# 内置适配器：import 即注册（顺序即目录，与 docs/roadmap/03-integrations.md 一致）。
register(LocalAdapter)
register(FeishuAdapter)

# planned 集成：仅 manifest、无适配器实例（社区共建中，能力声明为预期目标）。
register_manifest(
    IntegrationManifest(
        id="dingtalk",
        name="钉钉",
        description="钉钉 AI 表格、日历、通知与审批（社区共建中）。",
        capabilities=frozenset(
            {
                Capability.TABLE_STORE,
                Capability.CALENDAR,
                Capability.NOTIFIER,
                Capability.APPROVAL,
            }
        ),
        status_class="planned",
        docs_url="docs/integrations/dingtalk.md",
    )
)
register_manifest(
    IntegrationManifest(
        id="wecom",
        name="企业微信",
        description="企业微信智能表格、日程与通知（社区共建中；审批仅支持模板代发）。",
        capabilities=frozenset(
            {Capability.TABLE_STORE, Capability.CALENDAR, Capability.NOTIFIER}
        ),
        status_class="planned",
        docs_url="docs/integrations/wecom.md",
    )
)
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
    "FeishuAdapter",
    "Integration",
    "IntegrationEntry",
    "IntegrationManifest",
    "IntegrationStatus",
    "LocalAdapter",
    "VerifyResult",
    "get_integration",
    "get_integrations",
    "has_capability",
    "manifests",
    "register",
    "register_manifest",
]
