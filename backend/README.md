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
  映射，Fix 循环重跑校验）+ 可选 `cell_overrides`，返回 sheet 概览、表头行候选、逐列
  映射建议（目标字段、置信度、理由、样本值）、映射后的行级校验报告与统计。
  **只解析不落库。**
- `POST /api/v1/imports/commit`：文件 + `mapping_json`（必填）+ `mode=insert|upsert` +
  可选 `cell_overrides`。`upsert` 沿用现有业务键（班级+课次序号+课节名称+上课日期+
  上课时段）重复导入即更新；`insert` 只新增，已存在课次原样保留且不做孤儿清理。
  响应与模板直通导入同构，另附 `course_sessions_updated` /
  `course_sessions_skipped_existing`。

**历史映射记忆（historical mapping）**：无 `mapping_json` 时，preview 会把当前表头
序列规范化后算 sha256 指纹（列序敏感），按 `schedule_set_id + 指纹` 查
`import_mapping_history` 表；命中则直接按上次 commit 生效的映射（含手动修正与
「不导入」决策）预填，`matched_by="historical"`、置信度 0.95，响应置
`historical_match=true`，且不再询问 AI。commit 成功后把本次生效映射 upsert 进历史
（`used_count` 累加），失败的导入不记忆。历史记录只在同一课表方案内复用，结构损坏
的记录整份弃用、退回自动建议。

**单元格原地修复（cell_overrides）**：preview/commit 均接受可选表单字段
`cell_overrides`，JSON 形如 `{"<sheet 内 1-based 行号>": {"<表头文本或规范字段名>":
"<新值>"}}`，在解析后、校验前原地改写单元格。行号对不上或列名无法解析的条目一律
忽略并计入响应 `ignored_overrides`，不猜测；生效数计入 `stats.overrides_applied`。
前端向导第 3 步据此在问题行内联修复后重新校验，错误清零才放行提交。

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

Lark 国际版部署额外配置 `FEISHU_BASE_URL=https://open.larksuite.com`：开放 API、
OAuth 授权/令牌（按 `open.`→`accounts.` 前缀推导）与控制台链接随之切换；该切换未经
Lark 实测，见 `docs/integrations/feishu.md` 的「已知限制」。

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
- **三态拆分（MEM-C1）**：待确认候选（`probation` 且未授权试用）**完全不进求解输入**；
  教务点「授权试用」（transition `action=authorize_trial`，默认 30 天）后条目保持
  `probation`，以试用期衰减权重小步参与，`trial_until` 到期自动退出；`confirmed`
  才是全量权重。无感采集，不无感改变排课。
- **偏好库只管软偏好（MEM-C1）**：`modality=hard` 的条目不再直接编译进求解（编译
  逐条标记 `hard_requires_conversion`），由管理员经
  `POST /api/v1/memory/preferences/{id}/convert-to-rule` 转成 `hardness=hard` 的正式
  Rule（kind 对齐 constraint-catalog，`source_doc=memory:<entry_id>` 回链；条目转
  `expired` 并在 provenance 记 `rule_id`）。induced 来源转换必须显式传
  `confirmed_conversion=true`。
- **四条红线（写死在代码与测试里）**：① `induced_from_adjustment` 条目升硬约束一律 422
  （转正式规则须显式确认）——硬约束只能来自教务显式声明或管理员指令；② 创建 API
  不传 `valid_until` 时默认取本方案主数据最大上课日期（无课次回落「当前日期 + 180 天」）
  并回显；③ 挖掘条目初始 `status` 恒为 `probation`；④ 偏好的生效日期窗口
  （constraint 日期与 `valid_from`/`valid_until` 的**交集**，MEM-D1 D2）随编译进入
  规则 scope，只约束窗口内的课次。
- **挖掘**：`POST /api/v1/memory/mining-runs` 回顾近期调课事件（含一键归因
  `declared_reason`）。可学习事件先经**公共前置筛选**（MEM-D1 D3 + MEM-E1b，
  `services/memory_solver.py::learning_basis_events`，AI 与统计两条路径共用）：
  当前方案内 + **最近 90 天**滚动窗口（上限 200 条）+ **调课已被采纳**
  （pending/candidate_ready/candidate_discarded 三个事件状态标签本身都不构成
  「已被接受」；唯一接受依据是事件已产生结果版本且该版本未被后续放弃——
  候选版本 `candidate_schedule_id` 曾被发布（status ∈ published/archived/
  rolled_back）= 教务采纳了这次调课；停在 draft 的候选只是待发布提议。发布
  不回写事件状态，所以排除按候选版本正判实现。未被接受的尝试留历史、可分析
  失败原因，但不作偏好证据）+ `declared_reason` 非临时被迫类（临时公差/教师
  请假/教室故障等一次性事件不进学习集）。配置 AI 时走 `services/ai.py::mine_preferences`
  （模型只提名，允许弃权并可输出 `reasons` 自述失败原因；提示词注明输入已预筛，
  不要求模型自行过滤）；代码做**两道校验**（MEM-C2 修正 3）：结构校验（主体
  在事件里出现过、约束的时段/教室/日期必须来自候选引用的证据事件——教室按事件
  payload `room_business_id` 口径，调课事件载荷没有 before/after 快照）+ 证据校验
  （至少 2 条不同证据，且每条证据事件的主体与候选主体一致，李老师的证据不能支持
  张老师的候选）。未配置或失败时退化为确定性统计——同主体+同类型+同归因类调课
  ≥2 次即产生候选，文案为「发现 N 次相似调整，建议教务确认是否存在长期需求」。
  重复候选（同主体+谓词+约束且已存在 `probation`/`confirmed`）自动跳过。候选
  constraint 自带日期窗口时，条目级默认有效期取**覆盖该窗口的最小范围**（不得比
  constraint 窗口更宽；无日期维持「今天 +180 天」，MEM-D1 D2）。
- **拒绝记忆与矛盾消解（MEM-C2 修正 4 + MEM-D1）**：transition 到 `rejected` 时按
  受控枚举 `rejection_reason`（临时请假/主体识别错误/归纳错误/确实有偏好但已改变/
  其他，API 缺省「其他」）落 `preference_rejections` 表；再挖掘时同签名候选证据 ⊆
  已拒证据则跳过（响应 `skipped_rejected`），含新证据允许重提并在 provenance 标
  「此前被拒：<原因>」（前端 amber 徽标）。创建/挖掘落库时对同主体同谓词旧活跃
  条目做三分支消解：窗口不重叠 → 旧条目 `expired`（provenance 记 `superseded_by`，
  **仅当新条目已获授权**——未授权候选与旧条目窗口错开时并存不动）；相邻不重叠 →
  时间切片并存；窗口重叠且约束互斥（`prefer_*` 目标集不相交或数值谓词取值不同；
  `avoid_*` 取并集恒兼容）→ **候选提出冲突**（MEM-D1 D1）。
- **冲突标记 = 提出方侧 proposed_conflict，裁决是显式操作（MEM-D1 D1 + MEM-E1a）**：
  `conflict` 标记只落在提出方上——提出方**按授权状态判定**（MEM-E1a）：未授权
  条目（`probation` 且未授权试用）永远只能是提出方，创建时间仅用于同授权级别
  内的归属兜底（双方都未授权或都已授权时落较新者）；被点名的对侧状态与编译
  参与度永不因标记而改变。conflict 的**编译排除只作用于未授权条目**（本就不进
  求解输入，outcome=`conflict_unresolved`）；已授权条目带标记照常编译
  （outcome=`applied`，detail 注明「存在未裁决冲突提议，求解仍按现值执行」）。
  裁决三动作：**保留旧弃新** = 候选 `rejected`（走 transition）；**以新替旧** =
  `POST /memory/preferences/{candidate_id}/adjudicate-replace` 原子裁决（MEM-E3：
  同一事务内旧条目 `expired`+`superseded_by`、候选 `confirmed`+`supersedes`、
  `refresh_conflict_flags` 重算、审计、commit；任一步失败整体回滚，旧条目保持
  原状；重复调用幂等，已完成过的裁决返回 `detail=already_applied`，不再出现
  「旧的已退场、新的没生效」的两步请求中间态）；**授权试用** = `action=authorize_trial`
  （旧新并存：旧全权、候选试用期小权重，provenance 记 `conflict_resolved_with`
  不再重打标）。
  `refresh_conflict_flags` 是创建/编辑/确认/失效/拒绝/授权试用共用的唯一冲突
  重算入口：标记按「授权状态定提出方」重算，窗口重叠按**实际生效窗口**
  （约束∩条目级交集，MEM-E1c）判断，任一侧交集为空（not_applicable）不参与
  配对，对方离开活跃集或已显式裁决共存时自动清标；`PATCH /memory/preferences/{id}`
  编辑约束/有效期/谓词后同样触发重算。**空交集守卫（MEM-F/F1）**：新条目自身
  的实际生效窗口为空（constraint 日期与条目级有效期不相交）时，
  `resolve_conflicts_for_new_entry` 直接返回 `not_applicable`——不进入替代/
  冲突/相邻任何分支、不改动任何旧条目（与编译层 not_applicable 同口径：
  「永远不会生效」的条目不得让有效旧偏好退出）。
- **两层日期取交集（MEM-D1 D2 + MEM-E1c）**：编译窗口 = constraint 内
  `date_from/date_to` 与条目级 `valid_from/valid_until` 的**交集**（旧实现是
  条目级覆盖 constraint），交集为空 → outcome=`not_applicable`（明确不适用），
  条目不进编译、也不参与冲突配对——冲突配对与编译共用同一窗口口径，实际作用
  日期不重叠的偏好不再被误标冲突。
- **求解联动（创建时冻结）**：`api.create_solver_run` 在创建任务时即调
  `services/memory_solver.py::compile_memory_state`，把条目快照、编译后的内部软规则、
  逐条使用结果（applied/not_authorized/expired/unsupported_predicate/converted_to_rule/
  hard_requires_conversion/conflict_unresolved/not_applicable/compile_error）冻结进
  `DataSnapshot.payload["memory"]` 与
  `SolverRun.memory_usage`；执行路径（`services/tasks.py::_attach_memory_preferences`）
  只读快照，改记忆不影响在途求解的可复现性。目标权重 =
  `weight × (confirmed?1.0:0.3 授权试用衰减) × confidence`，v1 只映射
  `avoid_slot/prefer_slot/avoid_room/prefer_room/consecutive_sessions` 五个与现有
  软约束术语对齐的谓词；`max_daily_load` 只登记不进目标函数。偏好永远是软约束，
  不会把课表变成无解。编译整体失败时 memory 节标 `compile_failed`，求解照常但
  `services/explain.py` 与前端解释面板必须显式提示「本次未使用偏好记忆」，不无声降级。

## 目标验收闭环（MEM-C3）

求解任务结束 ≠ 目标完成；找到可行解 ≠ 用户全部要求都完成。一句话目标被拆成
**可逐项验收的清单**，每次关联的 SolverRun 到达 `completed` 后由**代码验收器**
出报告（缺口 + 证据 + 允许的下一步）回灌同一目标。设计见
`docs/roadmap/02-agent-memory.md` §6「目标闭环要点」。

- **存储**：`solve_goals` 表（`app/models.py::SolveGoal`：原始指令原文、checklist
  JSON、状态机、`acceptance_status`/`acceptance_detail` 验收执行状态）+
  `solver_runs.goal_id` / `solver_runs.goal_report`（迁移 `b8a4d2e6f9c1`；
  验收状态列为 MEM-D2 迁移 `e5c9a1d3f7b2`）。
- **三个集合口径（MEM-D2/D4a）**：`_persist_result` 在把父版本保留行合并回
  assignments **之前**先冻结 `solved_course_business_ids`（本次求解课次），
  result 载荷里的 `assignments` 则是合并交付课表（含 change_kind=unchanged 的
  保留行）。验收器据此区分：`date_range_match`/`coverage`/`forbidden_slot_free`
  只核对 **目标课次 ∪ 本次求解课次**（`_acceptance_scope_assignments`，保留
  原样的旧课次不算越界/违规），`no_hard_conflicts` 用**合并交付课表**（全局
  资源冲突看全集）。
- **checklist kind 与验收器一一对应**（`app/services/goal.py`）：`deliverable_exists`
  （run 存在非空课表产物）、`coverage`（目标课次集合与结果集合**逐项比对**，并借
  快照+请求范围把缺口拆成「没进求解范围」与「进了范围没安置」两类；**重复检测**：
  同一课次在交付课表出现 ≥2 次直接 failed）、`forbidden_slot_free`（独立复核指定
  主体+时段是否仍被占用，不信任求解器自报；执行前先按当前方案主数据核对
  subject/slot 存在性，**不存在 → unverifiable「禁排对象不存在，请补齐参数」**，
  配合 MEM-D3 补参 UI）、`no_hard_conflicts`（复用 `count_hard_conflicts`
  独立重算）、`max_changes`（与基准版本 diff 后只设上限——「尽量少改」是优化
  目标，**绝不升级为「绝不改」**）、`draft_only`（查审计日志：目标期间无
  publish/calendar_publish 动作；发布永远不在目标自动动作里）、`date_range_match`
  （范围端点核对）。
- **无法验证 ≠ 通过（MEM-D2/D4b）**：验收项遇到日期缺失、课次无 slot、参数
  无法解析、无课表等情况 → `passed=false` 且 `verdict="unverifiable"`（detail
  说明缺什么），绝不因「没检测到越界」判通过；报告带 `unverifiable_count`，
  `all_passed` 要求所有项 passed=true 且无 unverifiable。
- **底线验收（MEM-D2/D4c，MEM-E2/E2b 修订）**：创建/修订目标时
  （`ensure_bottom_line_items`，无论清单来自自动生成还是用户自定义）强制并入
  `deliverable_exists`、`no_hard_conflicts`、`no_duplicate_lessons`（交付课次
  不重复——不依赖目标范围，永远并入；无范围清单的查重兜底），以及有明确目标
  集合（课次/班级/业务线/班型范围）时的 `coverage`；params 带 `bottom_line=true`，
  前端打「底线」徽标。底线不可删除，用户附加清单与底线并列验收——仅 draft_only
  的自定义清单在零课表上也达不成。MEM-E2/E2b：补全传递**完整规范化范围**
  （`normalize_goal_scope`，不再用 has_target_set 布尔）——补入的 coverage 携带
  可解析的范围参数。MEM-F/F3：范围合并（`merge_coverage_scope`）为**三态语义，
  显式 scope 优先于清单 coverage 既有参数**——按 `GoalScopePatch.model_fields_set`
  感知「字段是否显式提交」（dict 调用方保守按「非空即提交」）：未提交=保留旧值，
  显式空列表 / 显式 `null` 日期=清除该维度限制，显式非空=替换；显式提交的字段
  直接写入清单 coverage 项 params（完整清单里的旧 coverage 参数也会被覆盖）；
  补全后统一校验 key 唯一（重复 422）。
- **清单版本绑定（MEM-E2/E2a）**：验收报告 `report.meta.checklist_version`
  记录本次验收所用清单版本（= `checklist_history` 长度+1，与响应
  `checklist_version` 同口径）+ `meta.checklist_snapshot`（当时 coverage 参数
  快照）；`create_solver_run` 冻结 `request_payload.goal_checklist_version`，
  验收时清单若又修订过，meta 注明「求解参数基于 v{m} 清单生成，验收按 v{n}」。
  `PATCH /goals/{id}/checklist` 修订成功后 acceptance_status 为 completed/failed
  强制回 pending（detail=「清单修订至 v{n}，等待新验收」），goal.status 为
  achieved 回退 open；latest_run_id 与历史报告保留，PATCH 响应附
  `latest_report_meta` 标注旧结论版本，前端据此区分「当前版本结论」与「历史
  版本结论」（pending 时当前结论区显示「等待新验收（v{n}）」而非旧通过）。
  **写回前版本复核（MEM-F/F2）**：`apply_goal_evaluation` 在写回阶段事务内
  `db.refresh(goal)` 重读当前 `_checklist_version`，与报告评估版本不一致
  （验收按 v1 计算、写回前清单已被另一会话修订至 v2）时，报告仅作历史保存且
  meta 加注 `evaluated_checklist_version`/`current_checklist_version`，
  `acceptance_status` 置回 pending（detail=「清单已修订至 v{n}，本报告基于
  v{m}，需重新验收」），`goal.status` 不得写 achieved——旧口径结论不覆盖
  「等待新验收」。
- **状态机**：`open → achieved`（全部通过；draft_only 目标在合格草稿交付即达成）；
  存在「必须由教务放宽或裁决」的缺口（硬冲突未消、求解未能安置、禁排仍被占、
  目标期间发生了发布）→ `awaiting_decision`；存在允许的补救动作（范围提取漏课次
  → 修正范围重跑；上限未达 → 加大预算重跑）→ 保持 `open` 并附建议；不存在
  允许动作同样保持 `open` 附终态报告。**验收器只出报告，绝不自动重跑**；状态
  永远反映最近一次验收，`abandoned` 是人工终态，验收器不越权改动。
- **决策消费求解状态（MEM-D2/D6）**：`_decide` 的解释文案按三态分开——
  `UNKNOWN`（含超时）→「求解未得出结论：预算与诊断决定是否继续」，目标保持
  open，不进「约束放不下」叙事；`INFEASIBLE`（presolve 预检不算，CP-SAT 未运行
  不能表述为已证明无解）→「当前模型已证明无解」，进 awaiting_decision 的规则/
  数据调整流程；有可行解但有缺口 → 按缺口逐项处理。
- **验收状态与可见性（MEM-D2/D6）**：`SolveGoal.acceptance_status`
  （pending/completed/failed，与目标状态机正交）——run completed 的事务里先置
  pending，验收成功 → completed，验收异常/求解失败 → failed 且原因同时落
  `acceptance_detail` 与 `run.goal_report` 失败标记（不再只打日志）。前端对
  带 goal 的 run 把轮询停止条件从「run completed」扩展为「报告就绪或验收失败」
  （45s 截止保护）；pending 显示「验收中…」，failed 显示 amber「验收失败：原因」。
- **关联求解**：`POST /api/v1/solver-runs` 与 `POST /api/v1/assistant/solve` 可选
  `goal_id`；自动验收钩子在 `services/tasks.py::_persist_result` 的事务之外单独
  提交——验收层任何异常都不影响求解结果落库。已放弃目标拒绝再关联新任务（409）。
- **任务要求修订（MEM-L1）**：带 `goal_id` 的 `/assistant/solve` 把用户确认过的
  `task_constraints` 先合并成任务的新版要求、再只从任务编译求解（`plan_task_constraint_revision`）。
  身份是内容（主体+时段），不是 id（解析侧 id 按序号生成，跨轮次会撞）：新硬要求追加进清单并
  **原子升清单版本**（同 `PATCH checklist` 的条件 UPDATE：achieved 回退 open、验收回 pending、旧清单进
  历史，并发修订 409 整体回滚）；同内容的旧软要求随之收紧；同 id 同主体换时段 = 修改那一条；
  **硬要求永不被请求里的「尽量」静默放宽**（保留并在 `task_revision.kept_hard` 留痕，放宽只能在清单
  里人工保存）。修订摘要随 run 留档（`task_revision`）与审计。
- **工作草稿指针保护（MEM-L3）**：run 产出草稿后接管 `goal.context.work_draft_schedule_id` 要过
  三关——任务未放弃、求解创建时冻结的 `goal_checklist_version` 仍是当前清单版本、指针现指草稿不是
  由更晚创建的求解产出的；读-改-写在任务行写锁下完成。被拒绝的产物只进历史，原因写进
  `goal_report.meta.work_draft`。
- **清单修订与历史（MEM-D3，MEM-E2/E2a 修订）**：`PATCH /api/v1/goals/{id}/checklist`
  整体替换验收清单——body 为完整 checklist 数组，校验复用创建口径（key 唯一、
  kind 白名单、`ensure_bottom_line_items` 强制并入底线；可选 `scope` 显式给新
  范围字段，未给的维度保留旧 coverage 参数）；每次保存把旧清单快照进
  `solve_goals.checklist_history`（`[{version, saved_at, saved_by, items}]`，
  迁移 `d7f2a9c4b8e1`，down=e5c9a1d3f7b2），当前版本号 =
  `GoalResponse.checklist_version`（历史长度+1，初始 v1），历史只追加不改写。
  修订使既有验收结论失效：acceptance_status 回 pending、achieved 回退 open
  （MEM-E2/E2a，见上）。典型用途：禁排占位项补参（量化 subject/slot 后从
  「恒不通过」恢复参与验收）。已放弃目标 409、跨方案 404。
- **目标连续性（MEM-D3）**：补救不脱离原目标——前端重新解析保留已绑定 goalId
  （口径不一致以提示条说明「清单可继续修订」）；手动求解携带会话持有的
  `goal_id`（无目标时 null，行为不变）；solver 页支持 `?goal=<id>` 深链绑定
  （goals 详情「继续处理 → 修正范围后重新求解」入口跳转，open/awaiting_decision
  均可见，awaiting_decision 的决策文案完整展示）；选基准版本时创建清单自动附带
  `max_changes` 项（params.baseline=所选版本，上限默认 50，仅设验收上限）。
- **与 interpret 打通**：`/assistant/interpret`（含流式）响应携带
  `goal_checklist_draft`（业务范围→coverage、日期→date_range_match、禁排语→
  forbidden_slot_free 占位）与 `checklist_warnings`；占位项 `needs_params=true`，
  补齐主体与时段前验收不会通过。前端可增删项后再创建 goal。
- **解释层**：带 goal 的 run 在事实包附 `goal_acceptance` 摘要
  （`services/explain.py`，改动仅限附加事实字段）。

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
- 智能导入预览：`POST /api/v1/imports/preview`（任意 XLSX/CSV，映射建议 + 行级校验，不落库；支持历史映射记忆与 `cell_overrides`）
- 智能导入提交：`POST /api/v1/imports/commit`（`mapping_json` + `mode=insert|upsert`，支持 `cell_overrides`）
- AI 配置：`GET/POST /api/v1/integrations/ai/configuration`
- 集成清单：`GET /api/v1/integrations`（管理员/排课员；manifest + 运行时状态，不触发探测）
- 集成凭据配置：`GET/PUT /api/v1/integrations/{id}/configuration`（钉钉/企业微信；GET 管理员/排课员脱敏回读，PUT 管理员加密落库）
- 一句话解析：`POST /api/v1/assistant/interpret`
- 一句话解析（流式）：`POST /api/v1/assistant/interpret/stream`（SSE：`stage`/`thinking`/`result`/`error`）
- 解析幂等（MEM-L4）：解析会直接执行「记住…」这类显式授权的记忆动作，请求体可选 `request_id`
  （客户端每条用户指令一个，流式、同步回退、失败重试沿用同一个）。有副作用的解析在动作执行的
  同一事务里写 `assistant_interpret_receipts`（方案 + `request_id` 唯一，存完整响应），重试直接
  返回原响应、不再调用模型；同标识配另一句话 409；并发两次撞唯一约束，输家整体回滚后读回回执
- 按原参数重跑：`POST /api/v1/solver-runs/{id}/rerun`（MEM-L2；只改时间预算，缺省按
  `min(max(原预算×3, 90), 900)` 加大）。范围/日期/课次、规则开关、变更权重、数据快照、偏好记忆、
  基准都取原求解冻结的那一份；有关联任务时任务要求取任务当前版本，无任务的一句话求解沿用当时冻结的
  任务约束；求解未结束 409、任务已放弃 409、调课/导入求解不支持 409。`SolverRunResponse` 新增
  `time_limit_seconds` / `task_revision` / `rerun_of`
- 求解进度：`GET /api/v1/solver-runs/{id}/events`
- 版本差异：`GET /api/v1/schedules/{base_id}/diff/{target_id}`，逐课次返回 `added`、`removed`、`moved`、`unchanged` 及调整前后日期/时段/教室
- 发布/回滚：`POST /api/v1/schedules/{id}/publish`、`POST /api/v1/schedules/{id}/rollback`（审批人权限）
- Feishu 单资源同步：`POST /api/v1/integrations/feishu/sync`
- Feishu 批量同步：`POST /api/v1/integrations/feishu/sync-batch`
- Feishu 同步记录：`GET /api/v1/integrations/feishu/syncs`（当前课表方案最近 50 条）
- 总览基础数据：`GET /api/v1/overview`
- 总览分析数据：`GET /api/v1/overview/analytics`（教师负荷、教室时段热力、软约束指标、飞书同步健康）
- 记忆偏好：`GET/POST /api/v1/memory/preferences`、`PATCH /api/v1/memory/preferences/{id}`、
  `POST /api/v1/memory/preferences/{id}/transition`（含 `action=authorize_trial` 授权试用）、
  `POST /api/v1/memory/preferences/{candidate_id}/adjudicate-replace`（「以新替旧」原子裁决，MEM-E3）、
  `POST /api/v1/memory/preferences/{id}/convert-to-rule`（hard 条目转正式规则，仅管理员）
- 偏好挖掘：`POST /api/v1/memory/mining-runs`
- 调课范围：`POST /api/v1/reschedule-events` 带 `course_business_id`（课次业务号）时，范围就是这一节课（配合 `date_from`/`date_to` 限定到具体那一天），不会因教师/教室相同扩大到其他课次；`include_neighbors`（默认 true，只调整选中课次时前端显式传 false）决定是否把前后 `neighborhood_days` 天内同班/同教室的课次也纳入可挪动范围；指定的课次不在父课表或日期范围内时 422，不会退化成全量重排。事件状态：pending（求解中）→ candidate_ready（有候选）/ no_candidate（无可行候选）/ failed（求解失败）
- 助手求解基准：`POST /api/v1/assistant/solve` 与 `POST /api/v1/solver-runs` 可选 `parent_schedule_id`（显式基准版本）与 `course_business_ids`（目标课次），课表页「交给助手继续处理」据此带上所选版本与课次。关联目标时二者是**任务约定**：课次限定写进 `goal.context.scope.course_business_ids`，显式基准写进 `goal.context.base_schedule_id`（`POST /api/v1/goals` 的 `base_schedule_id` 在**登记任务时**就落库，早于任何求解；它与只用于变更数对比的 `baseline_schedule_version_id` 是两回事）；基准选择顺序为 显式请求 > 目标工作草稿（仍为 draft）> 记下的原始基准（`baseline_source=goal_base`）> 最新已发布版本，所以第一次求解没产出草稿（超时/无解）时，加预算重跑、刷新续办仍以原始基准为准，有了工作草稿后基准才前进；课次限定不会因基准前进而清空，取消它是前端的扩大范围决定
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
