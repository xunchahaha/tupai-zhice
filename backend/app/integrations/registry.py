"""显式注册表：「目录即集成」（Cal.com 模式），不做 entry point（roadmap INT-5）。

registry 保存两类条目：实现了 ``Integration`` 协议的适配器类（经 ``@register``
注册），以及仅声明 manifest 的 planned 集成（社区共建占位，无适配器实例）。
两个内部字典由 register/register_manifest 保持互斥，同 id 后注册者生效。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import Capability, Integration, IntegrationManifest

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..config import Settings


@dataclass(frozen=True)
class IntegrationEntry:
    """清单条目：planned 集成只有 manifest，没有适配器类。"""

    manifest: IntegrationManifest
    adapter: type[Integration] | None = None


_ADAPTERS: dict[str, type[Integration]] = {}
_PLANNED: dict[str, IntegrationManifest] = {}


def register(adapter: type[Integration]) -> type[Integration]:
    """类装饰器：按 ``manifest.id`` 注册适配器。"""

    _PLANNED.pop(adapter.manifest.id, None)
    _ADAPTERS[adapter.manifest.id] = adapter
    return adapter


def register_manifest(manifest: IntegrationManifest) -> None:
    """登记暂无适配器实例的 planned 集成。"""

    _ADAPTERS.pop(manifest.id, None)
    _PLANNED[manifest.id] = manifest


def entries() -> list[IntegrationEntry]:
    """全部清单条目，按 manifest.id 稳定排序。"""

    items = [
        IntegrationEntry(manifest=manifest, adapter=_ADAPTERS.get(manifest.id))
        for manifest in (
            *_PLANNED.values(),
            *(_adapter.manifest for _adapter in _ADAPTERS.values()),
        )
    ]
    return sorted(items, key=lambda entry: entry.manifest.id)


def manifests() -> list[IntegrationManifest]:
    """全部集成清单（含 planned），按 id 排序。"""

    return [entry.manifest for entry in entries()]


def get_integration(integration_id: str, settings: Settings, db: Session) -> Integration | None:
    """按 id 实例化适配器；planned 集成或未知 id 返回 None。"""

    adapter = _ADAPTERS.get(integration_id)
    return adapter(settings=settings, db=db) if adapter else None


def get_integrations(settings: Settings, db: Session) -> list[Integration]:
    """实例化全部适配器（按 manifest.id 排序）；planned 集成不含在内。"""

    return [adapter(settings=settings, db=db) for _, adapter in sorted(_ADAPTERS.items())]


def has_capability(capability: Capability, settings: Settings, db: Session) -> bool:
    """是否有已就绪的集成提供该能力（运行时能力协商的唯一入口）。"""

    return any(capability in item.capabilities() for item in get_integrations(settings, db))
