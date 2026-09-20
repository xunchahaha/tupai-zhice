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

## Sources

- Anthropic：Building Effective Agents、Effective Context Engineering、Claude Agent SDK 文档、官方 Memory Tool 公告（anthropic.com/engineering、code.claude.com、platform.claude.com）
- Hermes Agent：hermes-agent.nousresearch.com/docs、github.com/NousResearch/hermes-agent（MIT）、《Hermes Agent Memory 系统深度分析》《Loop Engineering》（misaka-9982.com）
- 记忆系统：Mem0 论文 arXiv 2504.19413、Letta docs、Zep/Graphiti（均 Apache-2.0）
- 偏好学习：AAAI 2005 Calendar Assistants、PTIME（SRI）、groupTime（CHI 2006）、NESA（arXiv 1809.01316）、PEARL（Li et al., 2026）、Reflexion（NeurIPS 2023）、A Survey of Self-Evolving Agents
- Hu/Koren 隐式反馈经典结论（Matrix Factorization Techniques for Recommender Systems）
