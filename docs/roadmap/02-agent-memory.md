# 02 · 有记忆、自进化、有温度的排课系统

> 状态：定稿 v2（Anthropic 方法论 / Hermes / Loop Engineering / 记忆系统四路调研全部合入）；v1 已实现（MEM-A，2026-09-20：PreferenceEntry 表与迁移、memory 组 API、挖掘闭环（AI + 确定性降级）、求解软约束联动、调课归因 declared_reason）
> 关联：[README](README.md) · [01-data-import.md](01-data-import.md) · [03-integrations.md](03-integrations.md)

## 1. 问题定义

现状：排课前有「规则」（静态表单/自然语言解析产物），排课后有「调课」（一次性操作），两者互不滋养：

- 张老师这学期 3 次因为「周三晚上要接孩子」调课，系统每次都当新事件处理；
- 教室 301 投影仪坏了两天，修好后系统还记得它「不能用」；
- 教务说过「冲刺班尽量连排」，说完就沉底在规则列表里。

目标：让系统**记住**老师/教室/班级/课程的需求与习惯（记忆），**从调课行为中学习**（自进化），并在解释和建议中**引用这些记忆**（有温度）。

## 2. 方法论依据（调研摘要）

### 2.1 Anthropic 官方 agent 方法论（一手调研）

- **workflow vs agent 分型**（Building Effective Agents）：workflow=LLM 与工具经预定义代码路径编排；agent=LLM 动态决定流程。官方哲学：「最成功的实现用的是简单、可组合的模式」「只在明显改善结果时才增加复杂度」。五种 workflow 模式：prompt chaining / routing / parallelization / orchestrator-workers / evaluator-optimizer。
  → **本项目定位：排课主体保持确定性求解器（CP-SAT），LLM 只做解析/归纳/解释——整体是 workflow，不是裸 agent loop。**
- **上下文工程**（Effective Context Engineering）：上下文是有限资源，边际收益递减（context rot）；目标是最小高信号 token 集。手段：just-in-time 检索、compaction、结构化笔记（agentic memory）、sub-agent 蒸馏。
- **官方 Memory Tool 模式**：文件即记忆、**存储由应用自身执行和控制**、零厂商锁定——对开源项目最友好的模式；教训：OpenAI 托管记忆（Assistants API）2026-08 停服，**不要把记忆层建在厂商托管 API 上**。
- **工具设计（ACI）**：为「初级开发者」写工具 docstring；Poka-yoke 防呆；避免臃肿工具集。
- **Human-in-the-loop**：检查点暂停等人；显式展示 agent 规划步骤（透明原则）。

### 2.2 Nous Research Hermes Agent（GitHub 开源，MIT，Python + uv 与本项目同栈）

四层记忆架构：

| 层级 | Hermes 载体 | 本质 | 对应到途排智策 |
| --- | --- | --- | --- |
| L1 声明性记忆 | MEMORY.md / USER.md | 知道什么事实 | **偏好库** PreferenceEntry |
| L2 程序性记忆 | SKILL.md 技能 | 会什么方法论 | **调课 playbook**（v2 预留） |
| L3 情景记忆 | SQLite + FTS5（CJK trigram） | 做过什么 | **调课/求解历史** + 全文检索 |
| L4 进化记忆 | RL 数据流水线 | 如何变更好 | **偏好挖掘循环**（务实版） |

可直接借鉴的工程决策：Frozen Snapshot（求解前冻结活跃偏好集，版本可复现可解释）；FTS5 trigram 双索引解决中文检索（**无需向量库**——偏好条目量级数百条）；周期性 nudge（调课/求解结束自动触发记忆回顾，不靠手动录入）；写入防漂移（不确定保留旧值并上报，不静默覆盖）。

### 2.3 业界记忆系统（mem0 / Letta / Zep 对比结论）

- 共识四分层：工作记忆 / 情景记忆 / 语义记忆 / 程序记忆。
- **Mem0**：抽取+更新两阶段，记忆决策引擎判 ADD/UPDATE/DELETE/NOOP 消解新旧冲突（v3 简化为 ADD-only 后被社区反馈会浮现过期事实——**冲突消解逻辑应自实现**）。
- **Letta/MemGPT**：core/recall/archival 三级，agent 可自编辑记忆块。
- **Zep/Graphiti**：**双时间轴**（事实「何时为真」+「系统何时得知」，过期作废不删除、可溯任意历史时点）；本体可用 Pydantic 预定义——适配排课这种实体明确的领域。
- **选型结论**：排课偏好天然是「实体+谓词+时效」形态，**一张关系表 + 索引足够**；向量检索只用于自由文本留言的相似度归并（可选）。不引入图数据库。

### 2.4 从调整行为中学习偏好：先例充分

- 学术谱系：AAAI 2005 Calendar Assistants（观察式学习）→ PTIME「**初始轻量显式征询 + 之后无感在线细化**」→ groupTime / NESA → **PEARL（2026）：从过去调度决策建模长期偏好、适应偏好漂移的自进化 agent**（附 CalConflictBench）——「把调整当隐式反馈」有直接先例。
- 产品：Reclaim / Motion / Clockwise 均是「声明式偏好 + 观察行为 → 自动重排」（闭源）。
- **关键警示（Hu/Koren）**：隐式反馈只有正例、只反映信心不反映偏好——必须靠「调课理由归因」消噪 + 显式确认兜底。
- **差异化判断**：「CP-SAT 排课 + 调课归因 → LLM 归纳候选 → 教务确认」闭环无公开现成实现，是本项目的开源差异化点。

### 2.5 Loop Engineering（可验证的自我迭代循环）

- **排课主链路不该是 agent loop**——CP-SAT 本身就是完美 Verifier（硬冲突=0 是系统事实）。能用固定 workflow 解决的，不用开放循环。
- agent loop 只用在三处：偏好挖掘（evaluator-optimizer + human-gated）、调课方案生成（fix-test：最小变更→求解器验证→解释→审批）、解释问答（research loop，限制轮数）。
- 必备：Goal Contract（可验证验收）+ Stop Policy（连续 k 条拒绝即停）+ Trace（provenance 链）。成熟度先做 L2（有限循环 + 验证器），不追全自动。

## 3. 架构设计

### 3.1 记忆模型（L1 偏好库 schema）

```python
# backend/app/models.py 新增
class PreferenceEntry(Base):
    id            # PK
    subject_type  # teacher | classroom | cohort | course
    subject_id    # FK
    predicate     # avoid_slot | prefer_slot | avoid_room | max_daily_load | need_gap | ...
    constraint    # JSONB 结构化：{weekday, period, weeks, ...}
    modality      # hard | soft
    confidence    # 0-1
    source        # explicit_stated | admin_directive | induced_from_adjustment
    evidence      # JSONB：[adjustment_event_id, ...]（溯源链）
    weight        # 0-100 软约束权重
    status        # probation | confirmed | rejected | expired
    valid_from / valid_until   # 有效期，默认随学期（双时间轴：另存 created/updated）
    provenance    # JSONB：原话、挖掘模型与时间
```

红线（写死在代码里）：

1. **`induced_from_adjustment` 条目永不自动升为硬约束**——硬约束只能来自 `admin_directive`/教务确认；防止记忆漂移演变成数据事故。
2. 有效期必填，默认随学期失效；**过期作废不删除**（双时间轴，保审计链）。
3. 置信度随「长时间无证据」衰减并生成复查提示。
4. 写入时做 ADD/UPDATE/DELETE 冲突判定（自实现，参考 mem0 两阶段）。

### 3.2 与求解器联动

```
PreferenceEntry(confirmed/probation) ──编译器──> CP-SAT 软约束项
    hard + admin_directive + 教务确认 ──> 硬约束
    soft ──> 布尔违反变量进最小化目标，weight = f(confidence, source, 证据数, 新鲜度)
    probation ──> 试用期小权重（先观察，不打扰）
    confirmed ──> 正常权重
    rejected ──> 进负模式库（该归纳模板降权）
```

- 谓词到求解项的映射**复用 solver.py 现有约束路径**（constraint-catalog 的 solver_paths 双路径机制），v1 只开放与现有 15 种约束对齐的谓词，不新造求解项。
- 求解前冻结活跃偏好集为快照（Frozen Snapshot），课表版本记录之——「这版课表是按当时 37 条偏好排的」可复现可解释。

### 3.3 自进化闭环（核心新增）

```
调课（RescheduleEvent，已存在）──补「一键归因」理由──> AdjustmentEvent 证据
        ──> 偏好挖掘器（LLM，干净上下文按主体聚类，输出结构化候选+证据引用）
        ──> 教务确认队列（human-in-the-loop）──确认──> confirmed，下次求解权重生效
                                      ──拒绝──> 负学习，连续 3 拒停止本轮挖掘
```

- **消噪关键**：调课表单加「一键理由」（教师要求 / 教室冲突 / 临时公差 / 其他），区分「想换」与「被迫换」（对应隐式反馈只有正例的噪声本质）。
- 触发：手动「回顾本学期调课」按钮 + 求解/调课完成后的浅扫描 nudge。LLM 未配置时功能优雅降级（候选偏好改为「同主体重复调课」的规则统计呈现，无 LLM 也能用）。
- 验收指标：采纳偏好后调课请求数下降（北极星）；Stop Policy 兜底。
- 冷启动：谓词默认权重先验；导入历史课表时挖掘「从不移动的课程=揭示偏好」；自然语言规则（已有 /assistant/interpret）落库为 explicit_stated。

### 3.4 有温度的交互（前端，风格不变）

- 调课/排课建议解释引用记忆：「已避开周三晚（张老师需接孩子 · 3 月确认）」。
- 教师/教室详情「偏好与习惯」区块（复用现有卡片）。
- 「待确认记忆」收件箱：probation 条目卡片流，一键采纳/拒绝/修改（复用规则页待确认卡片的交互模式）。
- 偏好对教师可见可改（透明原则）。
- 全部复用现有 token/组件/动效（`animate-fade-in`、`rounded-lg border-zinc-200 bg-white shadow-2xs`、现有 ui 组件），零新依赖。

## 4. 与 Anthropic 模式的对应表

| 排课系统环节 | Anthropic 模式 |
| --- | --- |
| NL 规则解析（已有） | prompt chaining + 门控校验 |
| 偏好归纳 | sub-agent + 结构化笔记（just-in-time 拉事件，输出蒸馏候选） |
| 归纳质检 | evaluator-optimizer |
| 教务确认队列 | human-in-the-loop checkpoint（explicit=auto，induced=ask） |
| 调课事件日志 | 环境 ground truth（Verifier 数据源） |
| 解释生成 | 透明原则 |
| 整体 | workflow（LLM 编排预定义路径），非自主 agent loop |

## 5. 实施边界（防过度工程）

- **不做**：向量数据库、图数据库、多 agent 群聊、全自主排课 agent、RL 训练管线、厂商托管记忆。
- **做**：PreferenceEntry 表 + 归因字段 + 挖掘循环 v1（会话式，非后台 daemon）+ 求解权重编译器 + FTS5 检索 + 确认收件箱 + 解释引用。
- License：全自研记忆层（参考 mem0/Graphiti 实现，不引依赖），Apache/MIT 兼容零风险；LLM 调用走已有 OpenAI-compatible 通道。

## 6. 审查修正（2026-09-20 外部深度审查，MEM-C 系列依据）

六组已确认的问题与修正决策。第 **1、2、5、6** 组已随 MEM-C1 修复（2026-09-20），
第 **3、4** 组已随 MEM-C2 修复（2026-09-20，同日）：

| # | 问题 | 修正决策 | 状态 |
| --- | --- | --- | --- |
| 1 | **probation 条目实际影响求解**（权重 40×0.3×0.5=6），与前端「确认后才影响」的承诺背离 | 三态拆分：`待确认候选`（完全不进求解输入）／`授权试用`（教务显式点「授权试用」后以小权重参与，带 trial_until）／`已确认`（正常权重）。无感采集，不无感改变排课 | 已修复(MEM-C1)：`trial_authorized`/`trial_until` 列 + transition `authorize_trial`；compile 只收 confirmed 与授权试用 |
| 2 | `hard` 条目编译期被静默写成 soft；max_daily_load 只登记不编译且绕过规则兼容检查 | 偏好库只管理软偏好；`hard` 语义经授权确认后**转成正式 Rule 并回链原记忆 id**。编译器逐条返回实际使用结果：已应用/待确认/已过期/当前模式不支持/与高优先级冲突/编译失败，落 SolverRun 可见 | 已修复(MEM-C1)：`convert-to-rule` 端点 + 逐条 outcome 落 `SolverRun.memory_usage` |
| 3 | 挖掘校验只查「证据存在」不查「证据支持结论」（他师证据可证张师偏好；无 ≥2 条不同证据强制；约束未对齐证据；统计降级不用 declared_reason；200 条无学期/终态过滤） | 两道校验：结构校验（主体/时段/教室合法且属当前方案）+ 证据校验（每条证据主体一致、次数足够、区分临时/被迫/稳定）。统计降级文案改「发现 N 次相似调整，建议教务确认是否存在长期需求」 | 已修复(MEM-C2)：`validate_ai_candidates` 双道校验（≥2 条同主体证据；约束的时段/教室/日期必须来自候选引用的证据事件，教室按事件 payload `room_business_id` 口径——事件载荷无 before/after 快照）；统计降级按 reason 分组（临时公差/教师请假类不出候选）、事件范围限最近 90 天滚动窗口、候选改建议口吻；AI 提示词补契约与可选 `reasons` 失败自述 |
| 4 | **拒绝后同类候选会重现**（去重只看 probation/confirmed） | 拒绝原因入库（临时请假/主体识别错误/归纳错误/已变化/其他）；同主体+同结论+同证据被拒不再提醒，实质新证据才重启；新旧矛盾区分 替代/分时段/存疑保留 | 已修复(MEM-C2)：`preference_rejections` 表 + 拒绝端点落 `rejection_reason` 五选（API 缺省「其他」）；`_persist_mining_candidates` 证据 ⊆ 已拒证据则跳过（`skipped_rejected`），含新证据允许重提且 provenance 标「此前被拒」；同主体同谓词三分支消解：`new_replaces`（窗口不重叠→旧 expired 记 superseded_by）/`time_sliced`（相邻→并存）/`conflict_flagged`（重叠且约束互斥→`conflict` 标记，编译期跳过，前端 amber 徽标 + 一键保留旧弃新/以新替旧） |
| 5 | 过期判断用「今天 vs valid_until」，没约束到**课程适用日期**；`_session_matches_rule` 跨实体类型按 ID 字符串匹配 | valid_from/until 编译为求解范围内的日期窗口（约束 lesson_date）；默认有效期挂学期而非统一 180 天；实体匹配 = 类型+ID 双重分支 | 已修复(MEM-C1)：日期窗口进规则 scope（date-aware 求解按窗口过滤软惩罚）；默认有效期取方案最大上课日期；`_session_matches_rule` 类型+ID 双重一致 |
| 6 | **偏好未冻结进快照**：create_snapshot 存主数据+规则，偏好执行时现读——修改记忆后无法复现/解释历史求解；编译失败无提示静默降级 | 求解冻结：采纳集、编译后规则、有效权重、证据引用、编译器版本、未使用清单及原因；编译失败显式提示「本次未使用偏好记忆」，不无声出课表 | 已修复(MEM-C1)：创建任务时编译冻结进 `snapshot.payload["memory"]` + `SolverRun.memory_usage`；compile_failed 显式提示 |

配套验收测试（「不会悄悄做错」系列）：未授权候选不进求解输入；李老师证据不能支持张老师候选；同批证据被拒后不复现；月底失效的偏好不作用于下月课程；偏好修改后旧任务仍能恢复当时输入；漏一个课次即使求解成功也不算完成。

四类记忆的定位（不做大聊天记录库）：①正式业务事实与规则（已有 Rule）②长期偏好（PreferenceEntry，补齐本节修正）③当前任务工作记录（见目标闭环，MEM-C3）④受控的处理方案模板（暂不自动执行）。

目标闭环要点（MEM-C3，**已实现(MEM-C3)，2026-09-20**：`solve_goals` 表 + 迁移
`b8a4d2e6f9c1` + `services/goal.py` 生成器/验收器 + `/goals` API + solver-runs
`goal_id` 关联与 completed 后自动验收 + interpret 预填清单草稿 + 解释层
`goal_acceptance` 事实 + 前端确认卡「以此为目标跟踪」与验收报告、`/goals` 页）：
Goal 对象保存原始指令 + 逐项验收清单（课次集合逐项比对/禁排独立检查/硬冲突重算/
与基准变动数/未授权操作检查）；「尽量少改」不得偷偷升级为「绝不改」，「不能上」
「不发布」不得降级为尽量；验收报告（缺口+证据+允许的下一步）回灌同一 Agent；
停止规则 = 无允许的补救动作即停（验收器只出报告，绝不自动重跑）；「只出草稿」
在合格草稿交付即完成，发布必须等待有权限的人。

## 7. 第二轮源码复审（2026-09-22，对照 188f8bc，MEM-D 系列依据）

骨架确认成立（冻结快照/目标验收/三态准入均已进执行路径），但组合正确性有六组必修。核心判据三条：**未经批准不改变依据；无法验证不算通过；继续处理不丢原目标。**

| # | 问题 | 修正决策 | 批次 |
| --- | --- | --- | --- |
| D1 | **未授权候选经冲突标记间接改变排课**：挖掘候选与旧 confirmed 冲突时旧条目被标 conflict → 编译排除 → 未批准的改变已生效；且有测试固化该错误语义 | 候选只能「提出冲突」，不得改动已生效记忆：conflict 标记只落在候选侧（proposed_conflict，复用现有 `conflict` 布尔列承载，提出方=组内较新条目），旧 confirmed 条目编译不受影响（outcome=applied）；冲突裁决三动作全部走既有 transition 端点：保留旧弃新=候选 rejected／以新替旧=旧条目 expired 带 `supersedes` + 候选 confirmed／授权试用=authorize_trial（旧新并存，provenance 记 `conflict_resolved_with`）；`new_replaces` 分支同样只对已授权新条目生效。验收：仅新增/挖掘/拒绝未授权候选，现有有效偏好编译结果不变 | 已修复(MEM-D1) |
| D2 | 日期窗口被覆盖非交集：constraint 日期被条目级 valid_from/until 覆盖，候选统一 180 天导致「两天窗口」被扩成 180 天；refresh_conflict_flags 不看日期重叠；编辑条目不重算冲突 | 两层日期取交集，空交集=`not_applicable`（明确不适用）；冲突判定加条目级窗口重叠检查（窗口不相交 ≠ 冲突）；候选 constraint 自带日期窗口时条目级默认有效期取最小覆盖（不得更宽）；`refresh_conflict_flags` 成为创建/编辑/确认/失效/拒绝/授权试用共用的唯一冲突重算入口，`PATCH /memory/preferences/{id}` 编辑后触发 | 已修复(MEM-D1) |
| D3 | 临时事件消噪只在统计路径，AI 路径无此保证；挖掘事件未按「调课最终被接受」过滤 | 抽公共「可学习事件」前置筛选 `learning_basis_events`（方案内+90 天窗+候选未被取消/拒绝（事件模型稳定口径：`candidate_discarded`=候选被放弃）+非临时原因 `reason_noise_class`），AI 与统计共用——模型有无配置不改变业务边界；AI 提示词注明输入已预筛 | 已修复(MEM-D1) |
| D4 | 目标验收双向误判：①局部重排时合并回的旧课次被判日期越界 ②日期缺失被跳过=通过 ③仅 draft_only 的自定义清单可无课表达成 ④coverage 不查重复；禁排参数未验证存在性 | 区分 目标课次/本次求解课次/合并交付课表 三个集合，日期检查只针对目标课次；缺失日期=「无法验证」不通过；底线验收（有合格交付物+目标课次完整不重复+完整性检查）独立于自定义清单不可删；coverage 查重；禁排参数先验证主体/时段存在 | 已修复(MEM-D2)：`_persist_result` 在合并回填前冻结 `solved_course_business_ids` 作为求解集合口径；date_range/coverage/forbidden_slot_free 只核对 目标课次∪本次求解课次（`_acceptance_scope_assignments`），`no_hard_conflicts` 看合并交付全集；新增 `verdict="unverifiable"`（缺日期/缺参数/无课表不判通过，all_passed 要求无 unverifiable）；创建目标时 `ensure_bottom_line_items` 强制并入 `deliverable_exists`/`no_hard_conflicts`/有明确范围时的 `coverage`（params.bottom_line，前端「底线」徽标）；coverage 增重复检测（同课次 ≥2 次判 failed）；禁排执行前按主数据核对 subject/slot 存在性，不存在判 unverifiable |
| D5 | 用户补救时脱离原目标：重新解析清空 goalId；手动参数不带 goal_id；goals 页无继续入口；禁排占位项无补参路径；选基准版本不生成 max_changes | 人工闭环：目标详情页「继续处理」（open/awaiting_decision 显示引导文案+行动按钮：修正范围后重新求解（`/solver?goal=<id>` 深链绑定）/补齐禁排参数（就地展开补参表单）/放弃目标；awaiting_decision 决策文案完整展示）；re-parse 与手动求解均携带 goal_id（re-parse 保留绑定并在口径不一致时以提示条说明「清单可继续修订」；手动求解参数区显示关联徽标+可清除）；清单参数编辑端点 `PATCH /goals/{id}/checklist`（整体替换+复用创建校验+底线强制并入）+ 前端补参 UI（主体类型+主体+时段多选，量化后从「恒不通过」恢复参与验收）；选基准自动生成 max_changes 项（params.baseline=所选版本，上限默认 50，确认卡标注「按变更数验收」）；清单修订存版本（`checklist_history` 快照 + `checklist_version`，迁移 `d7f2a9c4b8e1`，详情可展开历史只读回看） | 已修复(MEM-D3) |
| D6 | UNKNOWN 被解释成「约束放不下」；验收失败静默（前端 completed 即停轮询，报告可能未生成/失败） | _decide 消费求解状态：UNKNOWN=预算与诊断决定是否继续、INFEASIBLE=已证明无解进调整流程、有可行解未达标=逐项处理；Goal 增验收状态（待验收/完成/失败），验收失败如实展示 | 已修复(MEM-D2)：`_decide` 按 run 状态三态分叙事（UNKNOWN→open「预算与诊断决定是否继续」；INFEASIBLE→awaiting_decision「已证明无解」；presolve 预检不可行→open 并注明 CP-SAT 未运行；有可行解→逐项处理）；`solve_goals` 增 `acceptance_status`(pending/completed/failed) + `acceptance_detail`（迁移 `e5c9a1d3f7b2`，down=b8a4d2e6f9c1）：run completed 事务内置 pending，验收成功→completed，验收异常/求解失败→failed 且原因入库（goal.acceptance_detail + run.goal_report 失败标记）；前端轮询停止条件从 run completed 扩展为「报告就绪或验收失败」（45s 截止保护），pending 显示「验收中…」、failed 显示 amber「验收失败：原因」 |

组合验收测试（防「单功能正确、组合错误」）：未授权冲突候选旁原偏好编译结果不变；约束日期窄于条目有效期时不被扩大；AI 与统计对同一批临时事件同准入；整月课表局部重排一周不误判越界；日期缺失/课次重复/无课表/自定义清单不得误判完成；改范围后续跑仍属原目标；UNKNOWN 与验收异常如实呈现。

实现参数（≥2 证据/90 天/0.3x）为可调实现细节，不属产品承诺。

## 8. 第三轮复审（2026-09-22，对照 84fac81，MEM-E 系列依据）

隔离反例验证（正常场景 1 过、反例 2 失败）确认 D 波修复只覆盖了部分路径，六项残留：

| # | 问题 | 修正决策 | 批次 |
| --- | --- | --- | --- |
| E1a | **冲突提出方按 created_at 判定**——「较早候选被编辑成与较晚 confirmed 冲突」时标记落错侧，已确认偏好再次被排除 | 冲突判定首先依据**授权状态**：未授权条目（probation 未授权）永远只能是提出方；已授权/confirmed 只在被**显式裁决操作**点名时改变状态。创建时间仅用于同授权级别内的提出方归属。回归测试固定「较早候选→较晚确认项→编辑较早候选→确认项仍 applied」 | 已修复(MEM-E1)：`refresh_conflict_flags` 逐对按授权状态落位（`_entry_authorized`）；`_entry_outcome` 中 conflict_unresolved 只对未授权条目生效，已授权条目带标记 outcome=applied + detail 注明「存在未裁决冲突提议，求解仍按现值执行」 |
| E1b | 「最终态被接受」未落实：学习集只排除 candidate_discarded，pending/candidate_ready 都算已接受 | 学习依据必须有**明确接受依据**：调课候选版本被采纳（发布/被教务接受的显式状态或 accepted 标记）；未被接受的尝试留历史但不作偏好证据。口径以模型里可稳定查询的字段实现并注释 | 已修复(MEM-E1)：`LEARNING_EXCLUDED_EVENT_STATUSES` 扩为三态（均不构成接受依据）；接受依据=候选版本（`candidate_schedule_id`）曾被发布（`ACCEPTED_CANDIDATE_VERSION_STATUSES`=published/archived/rolled_back，join 判定；发布不回写事件状态，故 candidate_ready 不能进 SQL 负向清单） |
| E1c | 编译窗口=交集、冲突窗口=条目有效期，两套口径不一致（实际不重叠的偏好被误标冲突） | **所有窗口判断共用 **（约束∩条目级），冲突配对先看实际生效窗口是否重叠 | 已修复(MEM-E1)：`resolve_conflicts_for_new_entry`/`refresh_conflict_flags` 窗口判断统一改用 `_effective_date_window()` 交集（`_effective_windows_overlap`），任一侧交集为空（not_applicable）不参与冲突配对 |
| E2a | 清单修订后目标仍显示旧版「已达成」；验收未绑定 checklist_version | 修订清单 → acceptance_status 强制回 pending（历史报告保留但标注版本）；SolverRun/report 记录所用 checklist_version 与参数快照；在途验收不得被新版本悄悄换口径 | 已修复(MEM-E2)：`replace_goal_checklist` 成功后 acceptance_status 为 completed/failed 强制回 pending（detail=「清单修订至 v{n}，等待新验收」），goal.status 为 achieved 回退 open；latest_run_id/历史报告保留但 PATCH 响应附 `latest_report_meta`（is_current_version=false +「历史版本 v{n} 的结论」）；`evaluate_goal` 把 `_checklist_version`（=history 长度+1，与响应口径一致）与 coverage 参数快照写进 `report.meta.checklist_version`/`checklist_snapshot`；`create_solver_run` 冻结 `request_payload.goal_checklist_version`，验收时若清单又改过则 meta 注明「求解参数基于 v{m} 清单生成，验收按 v{n}」；前端 pending 显示「等待新验收（v{n}）」、历史 run 徽标「v{n} 结论」 |
| E2b | 底线补 coverage 只传 has_target_set 布尔，参数丢失 → 完成了却被无参数检查卡住；无范围清单的「课次不重复」跟着 coverage 一起消失 | 底线补全传递**完整规范化范围**（创建时从请求解析、修订时保留旧参数或显式新范围）；「交付课次不重复」拆成不依赖范围的独立完整性底线项 | 已修复(MEM-E2)：新增 `normalize_goal_scope`（范围字段规范化为 coverage 参数包）；`ensure_bottom_line_items` 改签名 `(checklist, *, scope, previous_checklist)`——创建时传请求解析的完整范围，修订时经 `merge_coverage_scope` 优先保留旧 coverage params、body.scope 显式字段才覆盖；补入的 coverage 携带完整范围参数，用户已有 coverage 项参数缺失时补齐；新底线项 `no_duplicate_lessons`（交付课次在合并课表出现 ≥2 次即 failed，remedy=await_admin）不依赖范围永远并入，coverage 原重复检测保留；补全后统一校验 key 唯一（重复 422）；kind 白名单/`GOAL_CHECKLIST_KINDS`/前端 GOAL_KIND_LABELS 同步扩枚举 |
| E3 | 「以新替旧」是两个独立请求，第二步失败时旧偏好已退场 | 新增后端原子裁决端点 （同一事务：旧 expired+supersedes、候选 confirmed、冲突重算、审计；失败整体不变、重复请求幂等明确）；前端改调它 | 已修复(MEM-E3)：`POST /memory/preferences/{candidate_id}/adjudicate-replace`（admin/scheduler + 方案作用域）——同一事务内旧条目 `expired`（provenance 记 `superseded_by`/`superseded_at`）、候选 `confirmed`（记 `supersedes`）、`refresh_conflict_flags` 重算、审计、commit；被替换条目缺省取唯一活跃冲突对端（`conflict_with` 互指，缺失时经 refresh 判定补链；多对 422 要求显式传 `old_entry_id`；无对端 422）；任一步失败整体回滚（测试固化：monkeypatch 后段抛异常后旧条目保持 confirmed 原状）；重复调用幂等（已完成过的裁决返回 200 + `detail=already_applied`）；前端「以新替旧」改单次 mutation，失败 toast 后端 detail，不再顺序发两个 transition 请求 |

CI 备注：runner 因账户计费未启动（jobs steps=[]，未实际运行）；CI 补 e2e job 使其等价本地 VER。

## 9. 第四轮复审（2026-09-23，对照 c1ac7d46，MEM-F 收口批次）

7 个隔离用例：3 过、4 未达预期，归并为 3 类。功能范围冻结——不再新增记忆/Agent/自动重跑机制，只做现有能力内部的三项正确性保证。

| # | 问题 | 修正决策 | 批次 |
| --- | --- | --- | --- |
| F1 | **空日期交集的新条目仍可触发 new_replaces**：constraint 10/1-2 + 条目有效期至 9/30 → 交集空 → 本应 not_applicable，却作为 confirmed 走替代分支停用有效旧条目 |  顶部守卫：新条目实际窗口为空 → 直接返回 not_applicable（不触发替代/冲突/相邻分支）；验收=不适用的新条目无论状态如何都不能使有效旧条目退出 | 已修复(MEM-F)：`resolve_conflicts_for_new_entry` 顶部 `_effective_date_window(new_entry)` 为 None 即返回 `not_applicable`（与编译层同口径，跳过替代/冲突/相邻并跳过整组冲突重算）；回归固化「有效旧条目 A 保持 confirmed/applied 且在 compiled_rules、新条目 B=not_applicable、A 无 superseded_by 链」（test_memory_semantics.py） |
| F2 | **旧验收写回覆盖新版本 pending**：验收 A 按 v1 算完，写回前清单被修订为 v2（pending），A 写回旧结论 → achieved/completed |  写回时事务内校验当前 checklist_version == 本次验收版本；不一致 → 报告仅作历史保存（标注版本），acceptance_status 保持 pending 且 detail「清单已修订至 v{n}，本报告基于 v{m}，需重新验收」，goal.status 不得写 achieved | 已修复(MEM-F)：`apply_goal_evaluation` 写回前 `db.refresh(goal)` 事务内重读 `_checklist_version`，与报告 `meta.checklist_version` 不一致时报告照常落库但 meta 加注 `evaluated_checklist_version`/`current_checklist_version`，acceptance_status 置回 pending（detail 按上述文案）、goal.status 不写；并发交错测试固化（会话一按 v1 验收 → 会话二 PATCH 修订至 v2 → 会话一写回 → pending+双版本标注，test_goals_correctness.py） |
| F3 | 范围修订语义：显式  清除不生效（真值判断把空列表当未提供）；完整清单与显式 scope 并存时优先级不一致（静默沿用旧范围） | 合并函数区分「未提供（保留旧值）/显式空列表（清除该维度）/非空（替换）」——用 pydantic  感知字段是否提交；契约统一为**显式 scope 优先于清单 coverage 既有参数**并写入接口文档 | 已修复(MEM-F)：`merge_coverage_scope`/`ensure_bottom_line_items` 按 `GoalScopePatch.model_fields_set` 感知显式提交（dict 调用方保守按「非空即提交」兼容旧口径），显式空列表/null 日期清除维度、非空替换、未提交保留，显式字段直接写入清单 coverage 项 params（scope 胜）；PATCH `/goals/{id}/checklist` 直传 scope 模型；契约写入接口文档；测试覆盖三态 + 「完整清单（旧 B1 coverage）+ 显式 scope B2 → 生效 B2」（test_goals_correctness.py） |

配套：6 个业务场景迁入项目回归测试（此前为审查方隔离脚本；MEM-F 收口时逐条核对归位：①「已确认偏好旁的未授权候选不改编译结果」= test_memory_mining.py::test_conflict_flag_lands_on_candidate_only_and_old_entry_still_compiles；②「约束日期窄于条目有效期不被扩大」= test_memory_semantics.py::test_constraint_dates_intersect_with_entry_validity；③「AI 与统计同准入」= test_memory_mining.py::test_learning_basis_prefilter_applies_to_ai_path_too（含 learning_basis_events 输入一致性断言）；④「局部重排一周不误判越界」= test_goals_correctness.py::test_partial_reschedule_kept_sessions_not_flagged_out_of_range；⑤「日期缺失/课次重复/无课表/自定义清单不判完成」= test_goals_correctness.py 三个 D4b/D4c 测试；⑥「修订后继续执行仍属原目标」= test_goals_correctness.py::test_continued_runs_after_scope_revision_stay_bound_to_goal（新增，goal_id 绑定断言）；⑥此前仅由 test_goals.py::test_acceptance_binds_checklist_version_and_snapshot 部分覆盖）；LICENSE 按复审结论定为 **Apache-2.0**（补文件与 manifest/README 同步）；CI 补 e2e job。

MEM-F 附带修正的测试基建：四处日期敏感断言改为相对 today 或先抹 ISO 时间戳——硬编码历史日期窗（9/24-27）会随真实时间漂移成空交集（MEM-E1c 配对/编译测试踩中），公开载荷泄漏断言的 ``T01`` 会在每日 01:00–01:59 与时间戳 ``T01:xx`` 撞子串（test_public_links.py / test_api.py）。

## Sources

- Anthropic：Building Effective Agents、Effective Context Engineering、Claude Agent SDK 文档、官方 Memory Tool 公告（anthropic.com/engineering、code.claude.com、platform.claude.com）
- Hermes Agent：hermes-agent.nousresearch.com/docs、github.com/NousResearch/hermes-agent（MIT）、《Hermes Agent Memory 系统深度分析》《Loop Engineering》（misaka-9982.com）
- 记忆系统：Mem0 论文 arXiv 2504.19413、Letta docs、Zep/Graphiti（均 Apache-2.0）
- 偏好学习：AAAI 2005 Calendar Assistants、PTIME（SRI）、groupTime（CHI 2006）、NESA（arXiv 1809.01316）、PEARL（Li et al., 2026）、Reflexion（NeurIPS 2023）、A Survey of Self-Evolving Agents
- Hu/Koren 隐式反馈经典结论（Matrix Factorization Techniques for Recommender Systems）
