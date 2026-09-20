# 集成抽象层（app/integrations）

> 设计依据：docs/roadmap/03-integrations.md §2（Protocol 能力接口 + manifest 清单）。
> v1 边界：显式注册、只读清单端点；verify 触发端点、`integration_installations`
> 安装表、entry point 插件机制均为**二期**。

## 包结构

| 文件 | 职责 |
| --- | --- |
| `base.py` | `Capability`（table_store/calendar/notifier/approval/nl）、`IntegrationManifest`、`VerifyResult`、`Integration` Protocol |
| `registry.py` | `@register` 显式注册 + `get_integrations()` / `get_integration(id)` / `has_capability()` |
| `local.py` | LocalAdapter：内置 SQLite + 站内通知，默认可用（status 恒 configured） |
| `feishu/adapter.py` | FeishuAdapter：**纯委托** `services/feishu.py`，不搬移其内部逻辑 |

registry 同时保存两类条目：实现了 `Integration` 协议的适配器，以及仅有 manifest 的
planned 集成（dingtalk / wecom / google_workspace，社区共建占位，无适配器实例）。

## 语义约定

- `manifest.capabilities` 是该集成**能力上限**的静态声明；`capabilities()` 是
  **运行时协商**结果——未就绪的集成返回空集（如飞书未配置应用时）。
- 端点 `GET /api/v1/integrations` 返回的 `status`：planned 集成为 `planned`；
  适配器集成运行时能提供任一能力即 `configured`，否则回落到 manifest 的
  `status_class`（飞书未配置应用时为 `available`）。
- `verify()` 是统一「测试连接」（≈ Airbyte Check）：不得抛异常，失败用
  `VerifyResult(ok=False, detail=...)` 表达；v1 只读本地配置，不发外部网络请求。
- 适配器构造契约统一为 `(settings, db)`，由 registry 在请求现场实例化，因此适配器
  可以安全持有请求级 Session。

## 如何新增一个集成

1. **实现 Protocol**：新建 `app/integrations/<id>/adapter.py`，类上给出
   `manifest: ClassVar[IntegrationManifest]`，实现 `capabilities()` 与
   `async verify()`。能力按真实支持声明（参照 roadmap §2.1 的平台差异结论：
   钉钉 AI 表格缺视图/权限 API、企微智能表格受限、Google Sheets 是另一种范式、
   Google 无原生审批）。
2. **写 manifest**：`id` 用稳定英文短名；`status_class` 取 `available`（可配置）
   或 `planned`（仅占位时直接用 `register_manifest`，不需要适配器类）；`docs_url`
   指向 `docs/integrations/<id>.md`（Diátaxis 七节结构，见 roadmap §2.4）。
3. **@register**：在 `app/integrations/__init__.py` 中 `register(<Adapter>)`；
   仅 manifest 的 planned 集成用 `register_manifest(IntegrationManifest(...))`。
4. **测试**：在 `backend/tests/test_integrations.py` 补三条断言——registry 能查到、
   manifest 完整（非空 id/能力/文档路径）、`GET /integrations` 清单含该集成且
   状态符合语义约定。

不需要改 registry 本身，也不需要 entry point（`pyproject.toml` 的
`[project.entry-points."tupai.integrations"]` 留给二期社区插件）。

## 接缝迁移路线（strangler，v1 只动了第 1 处）

集成能力真实被消费的四个接缝（roadmap §1.1）：

| # | 接缝 | 位置 | v1 状态 |
| --- | --- | --- | --- |
| 1 | 日历下发（教师忙闲 + 日程创建） | `api.py` `publish_schedule_to_calendar` | **已迁移**：进入忙闲查询前经 `registry.has_capability(Capability.CALENDAR)` 协商；无可用的日历集成时返回 409，detail 与原 `FeishuServiceError` 未配置文案逐字一致；无可下发课次时不过门控（保持原 200 空结果行为） |
| 2 | 发布/回滚后 `trigger_published_data_sync` | `api.py` 同名函数 | 二期：收敛到 `TableStore` 能力接口之后 |
| 3 | `POST /integrations/feishu/sync[-batch]` 批量外发 | `api.py` `feishu_sync` / `feishu_sync_batch` | 二期：同上 |
| 4 | Teacher/CourseSession 的 `calendar_user_id` 字段语义 | `models.py` | 二期：字段名中性化（「日历账号」），仅前端文案先行 |

二期迁移某个接缝时：在 `base.py` 定义对应能力 Protocol（签名对齐 roadmap §2.2，
如 `TableStore.ensure_table/upsert_records/...`），让 FeishuAdapter 委托
`services/feishu.py` 实现它，再把消费点从直接调用 `FeishuService` 改为经 registry
按能力取适配器；`services/feishu.py` 内部逻辑与 6 张 `Feishu*` 私有存储表不动。
