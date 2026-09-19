# 03 · 集成抽象层设计：飞书从「前提」降级为「适配器」

> 状态：定稿 v2（现状盘点 + 业界调研全部合入）
> 关联：[README](README.md) · [01-data-import.md](01-data-import.md)（L3 连接器）

## 1. 现状盘点（代码勘察事实）

### 1.1 后端飞书资产（能力强，值得保留为第一个适配器）

- `services/feishu.py`（2992 行）：OAuth（授权码 + state，无 PKCE——Confidential Client）、token Fernet 加密 + 刷新锁、多维表格自动建表/字段对账/幂等同步（500/1000/500 分批）、日历 freebusy/建日程、Aily skill 启动、妙搭链接字段。
- 6 张持久化表：`FeishuAppConfiguration` / `FeishuOAuthState` / `FeishuConnection` / `FeishuWorkspace` / `FeishuTableBinding` / `FeishuRecordBinding`。
- AI 配置（`AIProviderConfiguration`）**已经是平台无关的** OpenAI-compatible 通道——证明「平台无关配置」在本项目可行。
- Aily 三端点（X-Aily-Key）已是可选高级通道，未配置即 503——弱化的正确先例。
- 关键接缝（集成能力真实被消费的点）：
  1. `POST /schedules/{id}/calendar-publish`——日历下发（教师忙闲 + 日程创建）
  2. 发布/回滚后 `trigger_published_data_sync`（api.py:4654）——发布数据外发
  3. `POST /integrations/feishu/sync[-batch]`——主数据/课表批量外发
  4. Teacher/CourseSession 的 `calendar_user_id` 字段——教师↔日历身份映射（语义通用，名字可中性化）

### 1.2 前端飞书露出点清单（弱化改造对象，全部已定位）

| 级别 | 位置 | 现状 |
| --- | --- | --- |
| 导航 | app-shell.tsx:27 | 侧边项「飞书集成」 |
| 整页 | integrations-page.tsx（102 处） | 标题、五步向导、权限表、同步历史 |
| 面板 | overview-page.tsx:547-553 | 「飞书/多维表格同步健康度」面板 |
| 面板 | solver-page.tsx:199 | 「教师日历下发」整块 |
| 面板 | versions-page.tsx:92/233 | 「重新同步发布数据」+ 同步说明横幅 |
| 字段 | master-data-page.tsx（8 处） | 「飞书日程账号」列/表单/删除文案 |
| 文案 | solver-page.tsx:57/180、rules-page.tsx:390、app-shell.tsx:206 | 飞书字样 |
| E2E | scheduling-flow.spec.ts | 名称含「飞书生产接入引导流程」 |

### 1.3 目标信息架构（前端「设置」页）

```
/settings（替代 /integrations 路由；旧路由重定向保持兼容）
├─ 通用：账户信息 + 修改密码（补齐反向差距：change-password 已有后端无 UI）
├─ AI 模型：现有 AI configuration 表单原样迁移（已是平台无关）
├─ 集成：集成卡片列表
│   ├─ 飞书（Lark）：连接状态徽章 + 「配置」展开五步向导（内容保留、收纳进卡片）
│   ├─ 钉钉 / 企业微信 / Google Workspace：卡片显示「社区共建中 · 查看接入指南」
│   └─ 本地模式（默认可用）：无需任何平台凭据，全功能可用
└─ 关于：版本、仓库链接、文档链接
```

- 「教师日历下发」→「日历下发」，运行时探测已配置集成的能力，未配置任何日历集成时块体显示引导文案（保持现有卡片/空态样式）。
- 同步健康度面板 → 「数据同步健康度」，按集成聚合；无集成时显示「未启用外部集成」。
- 「飞书日程账号」字段 → 「日历账号」（字段语义本就通用）。

## 2. 适配器设计（业界调研已合入）

### 2.1 调研要点（Cal.com / Home Assistant / n8n / Airbyte / Grafana）

五个开源范例的共性：①元数据清单文件声明身份与能力（HA manifest / Cal config.json / Grafana plugin.json）；②JSON Schema 声明配置 → 动态渲染表单（Airbyte spec / n8n properties）；③标准化「测试连接」操作（Airbyte Check / Grafana testDatasource / HA config flow）；④能力按接口族组织（Cal.com 类目）；⑤声明依赖与连接语义（HA `iot_class`、`dependencies`）。

国内平台能力对比结论：

1. 「表格存储」差异最大：飞书多维表格≈Airtable 语义（最全）；钉钉 AI 表格缺视图/权限 API；企微智能表格最受限（10 万行上限）；Google Sheets 是完全不同的范式 → **TableStore 接口必须定义抽象记录模型，不能漏平台概念**。
2. 「审批」是第二梯队缺口（Google 无原生审批、企微只能从模板发起）→ 审批流模板放在本系统内，平台审批只做代理提交+状态回传，或不声明该能力。
3. 三家国内平台鉴权同构（appId+secret → token），适合统一 AuthProvider；**飞书 Adapter 用 base_url 配置即可同时覆盖 Lark 国际版**，不必单写。
4. 「纯本地」模式的成熟先例（Plausible / Umami / Paperless-ngx）：默认值即本地模式、核心功能零外部依赖、增值能力插件化——**LocalAdapter 实现全部能力接口，保证零平台全功能可用**。

### 2.2 接口设计（Python Protocol，能力协商）

```python
class Capability(StrEnum):
    TABLE_STORE = "table_store"; CALENDAR = "calendar"
    NOTIFIER = "notifier"; APPROVAL = "approval"; NL = "nl"

class Integration(Protocol):                # 所有适配器的公共面
    id: ClassVar[str]                       # "feishu" / "dingtalk" / "local" / ...
    manifest: ClassVar[IntegrationManifest] # 元数据 + 配置 JSON Schema + 能力声明
    def capabilities(self) -> set[Capability]: ...
    async def verify(self) -> VerifyResult: ...   # 统一「测试连接」≈ Airbyte Check

class TableStore(Protocol):                 # 抽象记录模型（复用现有 sync_rows 语义）
    async def ensure_table(self, schema) -> None
    async def upsert_records(self, table, rows) -> BulkResult
    async def list_records(self, table, query) -> Page
    async def delete_records(self, table, ids) -> BulkResult

class Calendar(Protocol):                   # 对齐现有 calendar-publish/freebusy
    async def upsert_event(...) -> EventRef
    async def delete_event(ref) -> None
    async def freebusy(user_ids, window) -> BusyMap

class Notifier(Protocol): ...               # 卡片降级为纯文本
class Approval(Protocol): ...               # 代理提交 + 状态回传
class NaturalLanguage(Protocol): ...        # Aily 归位为可选 NL 能力
```

选型理由：用 `Protocol`（结构化类型）而非 ABC——适配器可只实现部分能力，`capabilities()` 做能力协商；manifest 含配置 JSON Schema，后端校验、前端动态渲染表单（Airbyte/n8n/Grafana 三家验证过）。

### 2.3 注册与配置存储

- **目录即集成**（Cal.com 模式）：`backend/app/integrations/{base.py, registry.py, feishu/, local/}`，`@register` 显式注册；entry point（`pyproject.toml` [project.entry-points."tupai.integrations"]）留作第二期为社区插件开门，**第一步不做**（调试成本）。
- ** strangler 迁移**：现有 `services/feishu.py` 内部逻辑不动，把四个消费接缝（日历下发/发布数据同步/批量同步/Aily）收敛到能力接口之后，feishu 包成为第一个适配器；现有 6 张 Feishu* 表保留为该适配器的私有存储。
- **配置存储**：新增 `integration_installations` 表（type, enabled, status, config JSON, secrets Fernet 加密 JSON, last_verified_at），支持同平台多实例；env 变量只作 bootstrap 覆盖（自托管友好）。现有 `AIProviderConfiguration` 平台无关，保持独立。
- OAuth 流集中到 `/integrations/{id}/oauth/start|callback`，适配器只声明 scopes 与端点。

### 2.4 接入文档模板（docs/integrations/<platform>.md）

Diátaxis 四分法 + Good Docs Project 排查模板，七节结构：**简介（能力矩阵徽章）→ 前置条件 → 所需权限清单（权限名+用途+必需性）→ 配置步骤（平台后台与设置页逐步对应）→ 测试连接 → 故障排查（症状→原因→解决）→ 已知限制（限频/上限/降级行为）**。首批写：feishu.md（由现有 04_生产接入操作手册改写去赛语境）、local.md、dingtalk.md（指南性质，标注社区共建中）。

## 3. 决策记录（ADR 摘要）

| # | 决策 | 备选 | 理由 |
| --- | --- | --- | --- |
| INT-1 | Protocol 能力接口 + manifest 清单 | 单一肥接口；全插件动态加载 | 能力协商适配各平台缺口；manifest 驱动动态表单是五家范例共识 |
| INT-2 | LocalAdapter 为默认已连接集成 | 无本地模式 | 开源试用零门槛（Paperless-ngx 模式）；现有功能本就不强制飞书 |
| INT-3 | 飞书逻辑 strangler 收敛，不重写 feishu.py | 全量重写 | 2992 行生产验证过的代码（OAuth/加密/幂等同步），重写风险远大于收益 |
| INT-4 | 审批流模板在本系统内，平台仅代理 | 依赖平台审批 | Google 无审批 API、企微只能模板发起（调研实证） |
| INT-5 | entry point 插件机制推迟到二期 | 一步到位 | 显式 registry 简单可调试；社区生态出现前无真实需求 |
