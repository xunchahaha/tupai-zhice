"""集成公共契约：能力枚举、清单、验证结果与适配器协议。

设计见 docs/roadmap/03-integrations.md §2：Protocol 结构化类型（适配器可只
实现部分能力，capabilities() 做能力协商）+ manifest 元数据清单（身份、能力、
状态与文档入口；配置 JSON Schema 留给二期动态表单）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..config import Settings

IntegrationStatus = Literal["available", "configured", "planned"]


class Capability(StrEnum):
    """集成能力类别（按接口族组织，参照 Cal.com 类目）。"""

    TABLE_STORE = "table_store"
    CALENDAR = "calendar"
    NOTIFIER = "notifier"
    APPROVAL = "approval"
    NL = "nl"


@dataclass(frozen=True)
class IntegrationManifest:
    """集成元数据清单（Home Assistant manifest / Grafana plugin.json 模式）。"""

    id: str
    name: str
    description: str
    capabilities: frozenset[Capability]
    status_class: IntegrationStatus
    docs_url: str = ""
    # 配置 JSON Schema：钉钉/企业微信 v1 已用于设置页凭据直填（凭据端点按它
    # 校验与拆分密钥字段）；飞书沿用专用端点，前端动态渲染表单二期铺开
    # （Airbyte spec / n8n properties 模式）。
    config_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class VerifyResult:
    """统一「测试连接」结果（≈ Airbyte Check / Grafana testDatasource）。"""

    ok: bool
    detail: str


class Integration(Protocol):
    """所有适配器的公共面。

    构造契约统一为 ``(settings, db)``：registry 在请求现场实例化适配器，
    适配器内部委托既有服务（如 ``services/feishu.py``），不搬移其内部逻辑。
    """

    manifest: ClassVar[IntegrationManifest]

    def __init__(self, settings: Settings, db: Session) -> None: ...

    def capabilities(self) -> set[Capability]:
        """运行时能力协商：未就绪的集成返回空集。"""
        ...

    async def verify(self) -> VerifyResult:
        """统一「测试连接」；不得抛异常，失败用 VerifyResult 表达。"""
        ...
