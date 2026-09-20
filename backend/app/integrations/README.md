# 集成抽象层（app/integrations）

> 设计依据：docs/roadmap/03-integrations.md §2（Protocol 能力接口 + manifest 清单）。
> v1 边界：显式注册、清单端点 + 通用凭据配置端点（钉钉/企业微信凭据按 config
> schema 直填、Fernet 加密落库）；管理端 OAuth 安装流、`integration_installations`
> 多实例安装表、verify 触发端点、entry point 插件机制均为**二期**。

## 包结构

| 文件 | 职责 |
| --- | --- |
| `base.py` | `Capability`（table_store/calendar/notifier/approval/nl）、`IntegrationManifest`、`VerifyResult`、`Integration` Protocol |
| `registry.py` | `@register` 显式注册 + `get_integrations()` / `get_integration(id)` / `has_capability()` |
| `local.py` | LocalAdapter：内置 SQLite + 站内通知，默认可用（status 恒 configured） |
| `feishu/adapter.py` | FeishuAdapter：**纯委托** `services/feishu.py`，不搬移其内部逻辑 |
| `dingtalk/` | DingTalkAdapter + client：AI 表格 / 日历 / 工作通知（≤100 人/次）/ OA 审批（需 process_code） |
| `wecom/` | WeComAdapter + client：智能表格 / 日程（仅应用自建日历）/ 应用消息（≤1000 人/次）；审批平台不支持 |
| `credentials.py` | `IntegrationCredential` 存取：config 明文 JSON + 密钥字段 Fernet 加密 JSON，每集成一行 |
| `platform_api.py` | 国内平台共享件：access_token 内存缓存（互斥 + 过期余量）与批量分片 |

registry 同时保存两类条目：实现了 `Integration` 协议的适配器，以及仅有 manifest 的
planned 集成（google_workspace，社区共建占位，无适配器实例）。钉钉/企业微信已从
planned 占位升级为真实适配器（v1：HTTP 层按官方文档实现并 MockTransport 单测覆盖，
未经生产凭据联调——见 docs/integrations/{dingtalk,wecom}.md）。

## 语义约定

- `manifest.capabilities` 是该集成**能力上限**的静态声明；`capabilities()` 是
  **运行时协商**结果——未就绪的集成返回空集（如飞书未配置应用时）。协商可以
  比 manifest 更细：钉钉未配置 `process_code` 时不声明 `APPROVAL`。
- 端点 `GET /api/v1/integrations` 返回的 `status`：planned 集成为 `planned`；
  适配器集成运行时能提供任一能力即 `configured`，否则回落到 manifest 的
  `status_class`（飞书/钉钉/企微未配置凭据时为 `available`）。
- `verify()` 是统一「测试连接」（≈ Airbyte Check）：不得抛异常，失败用
  `VerifyResult(ok=False, detail=...)` 表达。飞书/本地模式只读本地配置不发网络
  请求；钉钉/企业微信 v1 没有等价的本地探测点，用获取 access_token 做**轻量
  探测**（带缓存，不产生频控压力）。
- 适配器构造契约统一为 `(settings, db)`，由 registry 在请求现场实例化，因此适配器
  可以安全持有请求级 Session；客户端（client.py）另收可选 `transport` 关键字参数，
  供测试注入 `httpx.MockTransport`（全链路无真实网络）。

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
