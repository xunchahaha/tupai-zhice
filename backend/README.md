# 途排智策后端

## 命令

```powershell
uv sync --group dev
uv run alembic upgrade head
uv run tupai-api
```

本地开发需手动执行一次 `uv run alembic upgrade head`（应用启动守卫会在库结构落后时 fail-fast，不做自动迁移）；容器启动入口则会先自动执行 `alembic upgrade head`，再启动 API。因此全新持久化卷会先完成建表，随后由应用启动流程创建默认管理员。已有数据库只执行幂等迁移，不会覆盖管理员自行修改过的密码。

## 课表数据导入

导入按官方模板（`data/imports/sample.xlsx` 的「课表数据源」工作表，14 列）读取，
表头与模板不一致会直接拒绝，不做位置猜测。校区可以指定，默认郑州校区：

```powershell
uv run tupai-zhengzhou "E:\飞书大赛\郑州考研公职专升本课表数据源_教室班级标签版.xlsx"
```

郑州官方数据的实测口径：

- 56,344 个有效行 → 丢弃「教室-待校区确认」19,734 行 → 36,610 行 →
  完全相同行去重 9,340 → 按课次身份收敛 **9,169** 条课程场次。
- 「教室-待校区确认」经业务确认整行丢弃：它标记该课次不占用校区教室，
  全部来自「公职无限学」「专升本全年班」两个非集训营班级（各 100% 无真实教室）。
- 业务标识由「班级 + 课次序号 + 课节名称 + 上课日期 + 上课时段」生成，
  教室/教师/编排来源属于课次属性而非身份。**修改表格再导入是更新而不是新增**，
  源表中已删除的课次会被清理（被求解产出的课表版本引用过的会保留并报出）。
- 导入结果包含数据质量报告：跳过行（带行号与原因）、语义重复课次、
  同一班级同一时段的多节课、孤儿清理数、占位教室丢弃明细、计划课时不一致的班型。
  计划课时按该班型自己的课节时长核对，不假定每课次固定 3 小时。

### L2 智能映射导入（任意 XLSX/CSV）

除了官方模板直通，管理员还可以上传**任意**教务导出的 XLSX/CSV，走「预览映射 → 修复 →
确认提交」的导入向导：

- `POST /api/v1/imports/preview`：上传文件（≤64MB）+ 可选 `mapping_json`（用户修正后的
  映射，Fix 循环重跑校验），返回 sheet 概览、表头行候选、逐列映射建议（目标字段、
  置信度、理由、样本值）、映射后的行级校验报告与统计。**只解析不落库。**
- `POST /api/v1/imports/commit`：文件 + `mapping_json`（必填）+ `mode=insert|upsert`。
  `upsert` 沿用现有业务键（班级+课次序号+课节名称+上课日期+上课时段）重复导入即更新；
  `insert` 只新增，已存在课次原样保留且不做孤儿清理。响应与模板直通导入同构，
  另附 `course_sessions_updated` / `course_sessions_skipped_existing`。

列映射由 `app/services/import_mapping.py` 提供，按四层匹配并给出 0–1 置信度：

1. **Exact / 别名表**：表头与 14 个规范字段或业务别名（「任课教师」→ 授课教师、
   「时段」→ 上课时段等）逐字相等；
2. **Normalized**：小写、去空白标点、全半角（NFKC）归一后相等；
3. **Fuzzy**：`difflib` 编辑相似度（阈值 0.6，置信度随比例衰减），只用标准库；
4. **样本形状校验**：前 20 行采样判断「日期列像日期、数字列像数字、时段列像
   HH:MM-HH:MM」，形状不符直接把置信度压到阈值之下。

置信度 < 0.5 的列一律返回未匹配（`target=null`），**不硬猜**。可选 LLM 语义层：AI
接口已配置时对剩余未匹配列做语义提名（模型可弃权，输出按白名单过滤），未配置则
自动跳过；行级校验与入库复用与模板导入同一条核心管线
（`app/services/converter_core.py`），两条入口的校验口径与业务键语义完全一致。

坐标约定：`column_index`、`header_row_index` 均为 0-based 网格下标，Fix 循环原样回传。

质量检查：

```powershell
uv run ruff check .
uv run mypy app
uv run pytest
uv run python scripts/export_openapi.py
```

## 排课参数与时间口径

- `date_from`、`date_to` 是本次求解要处理的**原始课次日期范围**，两端均包含；同时会把候选日期窗口裁剪到该范围。只填写一端时表示开区间边界。
- `date_window_days` 是每节未锁定课次围绕原始日期可前后移动的天数，默认 `7`，允许 `0–31`。锁定课次仍只能使用原日期；规则中的 `before_days`、`after_days` 和固定日期还会继续收窄窗口。
- `room_no_overlap`（教室不重叠）与 `teacher_no_overlap`（教师不重叠）是系统级硬约束。即使调用方从 `solver_rules` 中省略，后端也会自动补齐；前端不应把它们当作可关闭的普通选项。
- 求解超时由 `time_limit_seconds` 控制，允许 `1–900` 秒；超时会返回当前求解状态和已知目标界，不等于自动发布。`wait=false` 只入队，`wait=true` 在请求内执行。

应用业务时间统一按 `Asia/Shanghai`（UTC+8）展示和落库，接口响应中的 `created_at`、`updated_at`、
`published_at`、授权过期时间等会带 UTC+8 偏移。OAuth/JWT 等协议内部仍按 UTC 计算过期瞬间，
只在协议边界转换，禁止在业务层把 UTC 字符串直接当作本地时间。

## 配置

将 `.env.example` 复制为 `.env` 后填写数据库、JWT、管理员账号和跨域配置。默认 SQLite
文件位于项目 `data` 目录。

飞书普通接入不修改 `.env`：管理员在前端“飞书集成”中一次填写 `App ID` 和
`App Secret`，后端自动生成本地加密主密钥并加密保存凭据。完成 OAuth 授权后，系统会
自动创建多维表格、10 张中文业务表并保存全部标识，不需要手工填写 `app_token` 或
`table_id`。默认表为：教师、班级、教室、时段、课程场次、规则、课表、公开展示汇总、
公开调课通知、班级链接索引。班级链接索引每个班级一行，用于登记学生/家长妙搭链接、
多维表格视图链接、访问模式和链接状态。旧版“班级公开课表”保留为兼容资源，不再进入默认同步。

集中式容器部署可以使用 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、
`FEISHU_TOKEN_ENCRYPTION_KEY`、`FEISHU_OAUTH_REDIRECT_URI` 和 `FRONTEND_URL`
覆盖前端配置。该模式面向部署平台或 KMS，不属于管理员首次接入步骤。

### 飞书授权与重新授权

新授权至少需要 `base:field:read`、`base:field:create`、`bitable:app:readonly`（或完整
`bitable:app`）以及记录读写权限；重复记录清理还需要 `base:record:delete`，按班级创建/维护
多维表格视图需要 `base:view:write_only`（完整 `bitable:app` 也可满足）。飞书控制台勾选权限后
必须发布应用版本，再在“飞书集成”中点击“解除连接”→“重新授权管理员账号”；刷新页面或刷新旧
令牌不会增加权限。后端检测到授权过期或错误码 `99991679` 时会将连接标记为
`reauthorization_required`，同步结果的 `detail.reauthorization_required=true`，前端应直接展示
重新授权入口。

### 同步语义与性能

`POST /api/v1/integrations/feishu/sync-batch` 默认同步教师、班级、教室、时段、课程场次、规则、
课表、公开展示汇总、公开调课通知和班级链接索引 10 个资源；旧版“班级公开课表”只保留为兼容
资源，不在默认列表中。同步按业务标识幂等：先分页读取（每页 500），再批量新增/更新（每批 1000），
删除系统拥有的过期或重复投影（每批 500），网络短暂失败最多重试 2 次。当前“课表”是当前发布版本
的运行投影，业务标识与版本号无关，因此 V2 会更新 V1 的同一行，不会每次追加数千条历史明细。
`public_class_links` 每个班级一行，手工维护的妙搭链接、公开视图链接、访问模式、状态、失效时间和
备注必须在后续同步中保留。

一句话排课 AI 在前端单独配置 `Base URL`、`API Key` 和模型名称，后端使用
OpenAI-compatible `/chat/completions` 接口解析指令，并对 API Key 加密保存。集中部署也可使用
`AI_BASE_URL`、`AI_API_KEY`、`AI_MODEL` 和 `AI_TOKEN_ENCRYPTION_KEY`。Aily 的
`spring_...__c` 与 `skill_...` 已调整为可选高级接入项。

一句话解析默认走 SSE 流式接口 `POST /api/v1/assistant/interpret/stream`（事件：
`stage` → `thinking` 增量 → `result`/`error`，响应头带 `X-Accel-Buffering: no`）。模型思考
增量（`reasoning_content` 与 `<think>` 块）逐段下行，最终 `result` 与同步接口
`POST /api/v1/assistant/interpret` 响应同构；Aily 无流式时退化为单条 `result`（伪流式）。
流式失败由前端自动回退同步接口，同步端点保留不动；事件协议详见
`docs/对接资料/04_接口与数据字典/后端接口与运行约定.md` 的「一句话解析流式接口」。

## 集成抽象层

`app/integrations/` 把外部平台从「前提」降级为「适配器」：`Integration` Protocol +
`IntegrationManifest` 清单 + 显式 `@register` 注册表（`registry.py`）。内置四类适配器：

- **LocalAdapter**：本地模式，默认可用（零平台凭据全功能可用）。
- **FeishuAdapter**：纯委托 `services/feishu.py`，不搬移其内部逻辑。
- **DingTalkAdapter**（v1）：钉钉 AI 表格记录读写、日程、工作通知（≤100 人/次自动分批）
  与 OA 审批发起（需企业预建模板 `process_code`，未配置时运行时不声明审批能力）。
- **WeComAdapter**（v1）：企业微信智能表格读写、日程创建（仅应用自建日历）与应用消息
  （≤1000 人/次自动分批）；平台服务端 API 无审批代发起，审批不支持。

钉钉/企业微信凭据在设置页按 manifest 的 config schema 直填，经通用端点
`GET/PUT /api/v1/integrations/{id}/configuration` 读写：密钥字段 Fernet 加密落
`integration_credentials` 表，`GET` 只回脱敏配置（是否已配置布尔），保存即失效该
平台 access_token 缓存。两适配器的 HTTP 层按官方文档实现并经 MockTransport 全量
单测覆盖（`tests/test_integrations_cn.py`，全程无真实网络），**未经生产凭据联调**；
「测试连接」为各自平台的轻量 access_token 探测。Google Workspace 以仅 manifest 的
planned 形式占位。清单经 `GET /api/v1/integrations`（管理员/排课员）暴露，返回 id、
能力、`configured/available/planned` 状态与文档入口。

消费接缝按 strangler 方式逐个迁移：日历下发（`calendar-publish`）已先经
`registry.has_capability(Capability.CALENDAR)` 做运行时能力协商，无可用的日历
集成时返回 409，提示与未配置飞书时一致；其余接缝与新增集成步骤见
`app/integrations/README.md`。

## 记忆层（偏好库 v1）

系统会记住老师/教室/班级/课程的需求与习惯，并从调课行为中学习。设计边界与红线见
`docs/roadmap/02-agent-memory.md` §3，接口语义见
`docs/对接资料/04_接口与数据字典/后端接口与运行约定.md` 的「记忆层」小节。

- **存储**：`preference_entries` 表（`app/models.py::PreferenceEntry`），多态主体
  `subject_type`（teacher/classroom/cohort/course）+ 业务标识定位，`constraint` JSON
  承载时段/教室等结构化范围，按 `schedule_set_id` 隔离。
- **来源与生命周期**：手工登记（`explicit_stated`/`admin_directive`）直接 `confirmed`；
  挖掘候选一律 `induced_from_adjustment` + `probation`，经
  `POST /api/v1/memory/preferences/{id}/transition` 确认或拒绝，过期作废不删除。
- **三条红线（写死在代码与测试里）**：① `induced_from_adjustment` 条目升硬约束一律 422——
  硬约束只能来自教务显式声明或管理员指令；② 创建 API 不传 `valid_until` 时默认
  「当前日期 + 180 天」并回显；③ 挖掘条目初始 `status` 恒为 `probation`。
- **挖掘**：`POST /api/v1/memory/mining-runs` 回顾近期调课事件（含一键归因
  `declared_reason`）。配置 AI 时走 `services/ai.py::mine_preferences`（模型只提名，
  主体/谓词/证据按白名单校验，允许弃权）；未配置或失败时退化为确定性统计——
  同主体+同类型调课 ≥2 次即产生候选，无 LLM 也能用。重复候选（同主体+谓词+约束
  且已存在 `probation`/`confirmed`）自动跳过。
- **求解联动**：`services/memory_solver.py::compile_preferences` 在每次求解前把
  `confirmed`/`probation` 且未过期的偏好翻译为内部软规则对象，并入 solver 现有
  软约束管线（`services/tasks.py::_attach_memory_preferences`），不新造求解项。
  目标权重 = `weight × (confirmed?1.0:0.3 试用期衰减) × confidence`，v1 只映射
  `avoid_slot/prefer_slot/avoid_room/prefer_room/consecutive_sessions` 五个与现有
  软约束术语对齐的谓词；`max_daily_load` 只登记不进目标函数。偏好永远是软约束，
  不会把课表变成无解。

## 公开课表层（capability-link）

脱离飞书妙搭后，面向老师（对外）/学生/家长/督导的免登录课表门户由 capability-link
（凭证链接）承载，设计定稿见 `docs/roadmap/06-public-showcase.md`。公开面与管理端
RBAC 正交：链接的签发/轮换/停用复用 `admin/scheduler` 角色，撤回手段 =
停用/轮换/过期，公开端点不声明 `CurrentUser`/`ViewerScope` 即绕过 JWT。

- **存储**：`public_link_tokens` 表（`app/models.py::PublicLinkToken`），只存
  SHA-256 哈希 + 末 4 位 `token_hint`；`scope`（class/teacher/school）+
  `campus_id`/`resource_business_id` 定位目标，`expires_at`/`revoked_at`/
  `last_seen_at`/`access_count` 管生命周期，按 `schedule_set_id` 隔离。
- **管理端点**（JWT + admin/scheduler + 方案 viewer 作用域）：
  `GET/POST /schedule-sets/{id}/public-links`、
  `POST /public-links/{id}/rotate`、`DELETE /public-links/{id}`（置 revoked 软删）。
  token 用 `secrets.token_urlsafe(32)` 生成，**明文仅在创建/轮换响应返回一次**
  （拼 `frontend_url` 成 `/public/t/{token}` 完整 URL）；列表与审计日志只落
  `token_hint`。创建不传 `expires_at` 时默认「当前时间 + `PUBLIC_DEFAULT_TTL_DAYS`（180）天」。
- **公开端点**（免登录）：
  `GET /public/links/{token}/schedule.json` 返回显式 Pydantic 白名单 payload
  （班级名/科目/课节名/教师姓名/教室/时间/日期 + 调课通知 + 版本元信息），
  **禁止整模型透传**——教师业务标识（工号）与校区内部主键任何分支不出现在序列化
  结果里；`GET /public/links/{token}/calendar.ics` 返回订阅日历
  （`Cache-Control: public, max-age=3600` + `ETag=sha256(version_id+published_at)`
  支持 `If-None-Match` 304）。伪造/过期/停用 token 一律同形 404，不暴露存在性；
  命中后节流更新 `last_seen_at`/`access_count`（10 分钟内只记一次）。
- **投影复用**：`services/public_projection.py` 承载原 api.py 的 `_public_*`
  内存投影纯函数（逐字节等价迁入，api.py 同名 re-import，飞书同步分发零改动），
  公开 payload 与飞书公开表共用同一份口径；调课通知直接复用
  `_public_adjustment_notice_rows` 按范围过滤。
- **ICS**：`services/ics.py` 依赖 `icalendar`（BSD-2）与 `tzdata`（Apache-2.0）。
  P0 只生成有 `lesson_date` 的课次（循环课次 RRULE 属 P1）；UID
  `tupai-{scope}-{resource_business_id}-{assignment_id}@public.tupai` 跨版本稳定，
  `DTSTART/DTEND;TZID=Asia/Shanghai`（静态 VTIMEZONE，+0800 无夏令时）、
  `DTSTAMP=published_at(UTC)`、`SEQUENCE=version_no`，重新发布后客户端原位更新。
- **总开关**：`PUBLIC_LINKS_ENABLED=false` 时全部公开端点按 404 处理。部署前提：
  公网可达 + HTTPS；日志只落 token_hint。

## API 约定

- API 前缀：`/api/v1`
- 用户鉴权：OAuth2 password flow + JWT Bearer
- Aily 鉴权：`X-Aily-Key`
- 模板直通导入：`POST /api/v1/imports/xlsx`（官方 14 列表头，严格匹配）
- 智能导入预览：`POST /api/v1/imports/preview`（任意 XLSX/CSV，映射建议 + 行级校验，不落库）
- 智能导入提交：`POST /api/v1/imports/commit`（`mapping_json` + `mode=insert|upsert`）
- AI 配置：`GET/POST /api/v1/integrations/ai/configuration`
- 集成清单：`GET /api/v1/integrations`（管理员/排课员；manifest + 运行时状态，不触发探测）
- 集成凭据配置：`GET/PUT /api/v1/integrations/{id}/configuration`（钉钉/企业微信；GET 管理员/排课员脱敏回读，PUT 管理员加密落库）
- 一句话解析：`POST /api/v1/assistant/interpret`
- 一句话解析（流式）：`POST /api/v1/assistant/interpret/stream`（SSE：`stage`/`thinking`/`result`/`error`）
- 求解进度：`GET /api/v1/solver-runs/{id}/events`
- 版本差异：`GET /api/v1/schedules/{base_id}/diff/{target_id}`，逐课次返回 `added`、`removed`、`moved`、`unchanged` 及调整前后日期/时段/教室
- 发布/回滚：`POST /api/v1/schedules/{id}/publish`、`POST /api/v1/schedules/{id}/rollback`（审批人权限）
- Feishu 单资源同步：`POST /api/v1/integrations/feishu/sync`
- Feishu 批量同步：`POST /api/v1/integrations/feishu/sync-batch`
- Feishu 同步记录：`GET /api/v1/integrations/feishu/syncs`（当前课表方案最近 50 条）
- 总览基础数据：`GET /api/v1/overview`
- 总览分析数据：`GET /api/v1/overview/analytics`（教师负荷、教室时段热力、软约束指标、飞书同步健康）
- 记忆偏好：`GET/POST /api/v1/memory/preferences`、`PATCH /api/v1/memory/preferences/{id}`、
  `POST /api/v1/memory/preferences/{id}/transition`
- 偏好挖掘：`POST /api/v1/memory/mining-runs`
- 调课归因：`POST /api/v1/reschedule-events` 请求体可选 `declared_reason`
- 课表方案：`GET/POST /api/v1/schedule-sets`、`PATCH /api/v1/schedule-sets/{id}`
- 课表方案成员：`GET /api/v1/schedule-sets/{id}/members`，`PUT/DELETE /api/v1/schedule-sets/{id}/members/{user_id}`
- 飞书连接诊断：`GET /api/v1/integrations/feishu/connection`
- OpenAPI：`openapi.json`

所有业务查询和写入都在当前课表方案作用域内执行。排课员和成员只能看到管理员在“账号列表 →
课表访问权限”中授予的方案；管理员默认可管理全部方案。`schedule_set_id` 不应由前端自行拼接
到另一个方案，后端会在依赖注入层校验可见范围。

## 总览分析接口的数据口径

`GET /api/v1/overview/analytics` 只读取当前已发布课表、当前方案的求解结果和同步日志，不改变
排课或同步状态。返回四组可直接供前端图表使用的数据：

- `teacher_workload`：按“校区 + 教师业务标识”汇总排课节数和小时数，含
  `top_teachers`（前 5 名）与 `buckets`（`0-10 节`、`10-20 节`、`20+ 节`），
  避免不同校区复用同一教师编号时被误合并。
  周/学期切换由 `date_from`、`date_to` 查询参数控制；未传时统计选定课表版本的全部安排。
- `room_heatmap`：`cells` 按原始星期×课节统计已用教室数、可用教室数和占用率；
  `period_cells` 额外给出固定 7×3（周一至周日 × 上午/下午/晚自习）汇总。日期参数
  缺一侧或全部省略时，响应中的 `effective_date_from/to` 会用当前版本最早/最晚课次补齐，
  完全空闲的日期仍计入容量分母。
- `optimization_penalties`：返回最近一次求解的目标值、界、软规则数量和可解释的
  扣分/满足率明细。分项严格限制在该次求解的课次范围；新运行保存真实求解课次 ID，
  历史运行按请求筛选条件重建范围，并通过 `breakdown_source`、`scope_source` 和
  `reconciliation_error` 明示估算口径与异常差额。没有可评估样本时满足率返回 `null`。
  没有可分解的历史求解数据时，接口会明确返回空明细，而不会把目标值伪装成规则分项。
- `sync_health`：默认统计最近 24 小时飞书同步次数、成功/失败数、读写记录数、平均耗时、
  重试次数和最近一次同步时间；可用 `sync_window_hours` 调整窗口。

指标基于已发布版本，草稿或未执行的求解不会进入公开总览。后端使用课程实际 `duration_minutes`
计算教师课时，不用“每节课固定 3 小时”的假设。

## 发布、回滚与同步边界

发布/回滚是本地版本状态操作，完成并提交本地事务后同步触发一次最佳努力的飞书投影同步；“一键同步当前方案”
只读取当前数据并写入飞书，不会重新调用求解器，也不会自动生成新的课表版本。同步失败不会撤销
本地发布/回滚，失败资源可在同步历史中单独重试。
