# 02 · 有记忆、自进化、有温度的排课系统

> 状态：草案 v1（Hermes / Loop Engineering 一手调研已完成，等待业界记忆系统横向调研合入）
> 关联：[README](README.md) · [01-data-import.md](01-data-import.md) · [03-integrations.md](03-integrations.md)

## 1. 问题定义

现状：排课前有「规则」（静态表单/自然语言解析产物），排课后有「调课」（一次性操作）。两者互不滋养：

- 张老师这学期 3 次因为「周三晚上要接孩子」调课，系统每次都当新事件处理；
- 教室 301 投影仪坏了两天，修好后系统还记得它「不能用」；
- 教务在自然语言里说过「冲刺班尽量连排」，说完就沉底在规则列表里。

目标：让系统**记住**老师/教室/班级/课程的需求与习惯（记忆），**从调课行为中学习**（自进化），并在解释和建议中**引用这些记忆**（有温度）。

## 2. 方法论依据（调研摘要）

### 2.1 Anthropic agent 工程方法论

（待合入 memory-researcher 报告：Building Effective Agents 的 workflow vs agent 分型、tool 设计原则、human-in-the-loop 模式。）

### 2.2 Nous Research Hermes Agent（GitHub 开源，MIT，Python + uv，与本项目后端同栈）

一手调研（官方文档 + 社区深度分析），四层记忆架构：

| 层级 | Hermes 载体 | 本质 | 对应到途排智策 |
| --- | --- | --- | --- |
| L1 声明性记忆 | MEMORY.md / USER.md | 知道什么事实 | **偏好库**：老师/教室/班级的结构化偏好条目 |
| L2 程序性记忆 | SKILL.md 技能系统 | 会什么方法论 | **调课 playbook**：「教师请假→同科目代课→合班→调期」策略模板 |
| L3 情景记忆 | SQLite + FTS5（含 CJK trigram） | 做过什么历史 | **调课/求解历史** + 全文检索（「张老师上学期为什么总调课」） |
| L4 训练/进化记忆 | batch_runner + trajectory_compressor | 如何变得更好 | **偏好挖掘循环**：从调课历史归纳候选偏好 |

可直接借鉴的工程决策：

1. **Frozen Snapshot 思想** → 求解前把「活跃偏好集」冻结为本次求解的输入快照，课表版本记录该快照，保证可复现、可解释（「这版课表是按当时的 37 条偏好排的」）。
2. **FTS5 trigram 双索引**（标准 tokenizer 会把中文拆成单字）→ 我们的历史检索直接用 SQLite FTS5 trigram，**不需要向量库**——偏好条目量级（数百条）用全文+结构化过滤足够。
3. **周期性 nudge（自我提醒持久化）** → 每次调课/求解结束后触发一次「记忆回顾」任务，而不是指望教务手动录入。
4. **写入安全与防漂移** → 偏好条目带来源与版本，冲突时「不确定就保留旧值并上报」，不静默覆盖。

### 2.3 Loop Engineering（可验证的自我迭代循环）

核心结论对排课系统极其适配：

- **排课主链路不该是 agent loop**——CP-SAT 是确定性求解器，本身就是最完美的 Verifier（硬冲突=0 是系统事实，不是主观判断）。能用固定 workflow 解决的，不要用开放循环。
- **agent loop 用在三个真正需要智能的环节**：
  1. **偏好挖掘循环**（Evaluator-Optimizer + Human-Gated）：LLM 从调课历史归纳「候选偏好」→ 教务确认 → 入库 → 影响下次求解权重。
  2. **调课方案生成**（Fix-Test Loop）：事件（请假/停用）→ 生成最小变更方案 → 求解器验证冲突=0 → 解释 → 教务审批。
  3. **解释与问答**（Research Loop）：引用记忆回答「为什么张三的课都在下午」。
- **Goal Contract + Stop Policy 必备**：偏好挖掘循环的验收=教务确认率与软目标分提升；连续 k 条被拒绝即停止挖掘并上报（no_progress 条款）。
- **成熟度按 L2 做**（有限循环：max_steps + 验证器），不追求全自动。

## 3. 架构设计

### 3.1 记忆模型（L1 偏好库 schema）

```python
# backend/app/models.py 新增
class PreferenceEntry(Base):
    id            # PK
    subject_type  # teacher | classroom | cohort | course
    subject_id    # FK
    kind          # 时间回避/时间偏好/连续性/教室设备/负载上限/其他
    params        # JSONB：如 {"weekday": 3, "period": "evening"} 或 {"min_hours": 8}
    scope         # JSONB：生效范围（学期、日期区间、周期）
    weight        # 0-100，置信度→软约束权重
    source        # manual | nl_parsed | mined   （mined=从调课历史挖掘）
    provenance    # JSONB：原话、来源调课记录 id、挖掘模型与时间
    status        # candidate | active | rejected | expired
    created_at / updated_at / expires_at
```

关键规则：

- **只有 `active` 条目参与求解**；`candidate` 只出现在「待确认」卡片和解释里。
- **硬约束白名单**：硬约束只能来自 `manual` 来源（教务显式设置），`mined` 条目最高只能到软约束——防止记忆漂移演变成数据事故。
- `weight` 与 CP-SAT 软目标权重线性映射；`confidence` 与 `weight` 解耦（置信度高但教务给低权重=「我知道但别太当回事」）。

### 3.2 与求解器联动

```
PreferenceEntry(active) ──映射表──> CP-SAT 软约束项
     kind=时间回避 ──────────────> 禁止变量 + 罚分
     kind=时间偏好 ──────────────> 奖励项 × weight
     kind=连续性   ──────────────> 连堂奖励 × weight
     kind=负载上限 ──────────────> 均衡项 × weight
```

课表版本保存求解时的偏好快照（Frozen Snapshot），回滚时同时恢复当时的偏好上下文——**可解释性的根基**。

### 3.3 自进化循环（核心新增）

```
调课记录 ──> 偏好挖掘器(LLM) ──> 候选偏好卡片 ──> 教务确认(Human-Gated)
                                    │  确认 ↓              ✗ 拒绝
                                 status=active        计入负反馈
                                    │
                              下次求解权重变化 ──> 调课次数下降（北极星指标）
```

- 触发：手动「回顾本学期调课」按钮 + 求解完成后自动浅扫描（nudge）。
- 验收指标：采纳的偏好使后续调课请求数下降；连续拒绝 ≥3 条 → 停止本轮挖掘。
- Trace：每条 `mined` 条目的 provenance 链回溯到具体调课记录，教务能看到「系统为什么认为你喜欢这样」。

### 3.4 有温度的交互（前端，风格不变）

- 调课建议解释引用记忆：「已避开周三晚（张老师需接孩子 · 3 月确认）」。
- 教师/教室详情页新增「偏好与习惯」区块（复用现有卡片组件）。
- 「待确认记忆」收件箱：candidate 条目以卡片流呈现，一键采纳/拒绝/修改。
- 所有新增 UI 复用现有 token / 组件 / 动效，不引入新依赖。

## 4. 与 Anthropic 模式的对应

（待合入：tool-use agent loop 设计、context 管理、confirmation 模式。）

## 5. 实施边界（防过度工程）

- **不做**：向量数据库、多 agent 群聊、全自主排课 agent、RL 训练管线（L4 只做「挖掘→确认→权重」这一条务实闭环）。
- **做**：preferences 表 + 映射表、挖掘循环 v1（单次会话式，非后台 daemon）、FTS5 检索、解释引用、确认收件箱。
- 开源友好：全部新代码用项目现有 license；不强制依赖任何闭源记忆服务。

## Sources

- Hermes Agent 官方文档：https://hermes-agent.nousresearch.com/docs
- Hermes Agent GitHub（NousResearch，MIT）：https://github.com/NousResearch/hermes-agent
- 《Hermes Agent Memory 系统深度分析》：https://www.misaka-9982.com/2026/03/12/hermes-agent-memory-deep-dive/
- 《Loop Engineering，给 AI Agent 设计可验证的自我迭代循环》：https://www.misaka-9982.com/2026/06/17/loop-engineering/
