# 07 · 任务上下文主线贯通（第七轮复审第二批）

> 状态：设计定稿（待实施）；v2 修订（2026-09-25）：按设计评审 10 条意见逐条修订——§2.1b 指定硬任务约束写入 goal.checklist 的写入者（draft 生成器消费 task_constraints）、覆盖性校验改校验侧局部合并视图、解释层显式接入 task_constraint_rules 并透出 source_doc、补请求侧 task_constraints 载体、补前端流式通道 goal_id 传递、注明记忆动作是「解析不写 context」的唯一已声明例外、显式词表补齐并与 e2e 场景原话锁定、砍除无消费者的 context.decisions、unsupported 422 表述为契约层拦截（前端闸仍为主防线）、校正三处勘察锚点。
> v3 修订（2026-09-25）：按第二轮评审 3 条意见修订——§6.5 补求解请求体携带 task_constraints（软约束链前端填充，grep 实证前端现状零发送，TC-6 vitest 补 hard+soft 断言、S4 加 soft 伴随场景）；§5.2 加预算提交路径改独立函数复用手动求解路径（solveFromInterpretation 的 :447 守卫在 goals-page/刷新场景必提前返回，不得字面复用）；§2.4 编译函数改双来源签名 _compile_task_constraints（goal 来源 TASK-{goal.id[:8]}-*、请求来源 TASK-req-{constraint_id}，source_doc 分别为 goal:<id>/request:task_constraint）。
> 关联：[README](README.md) · [02-agent-memory.md](02-agent-memory.md)（MEM-A–I 已实现部分） · [04-implementation-plan.md](04-implementation-plan.md)（八期=第七轮第一批 MEM-I1–I6；本批为第二批，批次代号 **TC-1..TC-8**，完成后在 04 登记九期）
> 硬约束沿用 README：前端视觉风格与动画完全不变；飞书集成弱化不删除；业务闭环不破坏。

## 0. 问题定义与主线图

四个需求缺口，共同根因是**同一句话产生的解析结果在系统里走不完主线**：

- **缺口 4（自然语言入口）**：教务说「张老师周三晚上不能上」，这句本次要求目前只能落进
  `unsupported_requirements`（api.py:6927-6940 三路汇聚），禁排语在清单里只剩一条
  `needs_params=True` 的占位项（goal.py:318-335）——**求解器不知道它存在**；而且「有未进入求解
  的要求就禁止求解」这道闸只装在前端（solver-page.tsx:447），`AssistantSolveRequest` 根本没有
  unsupported 字段（schemas.py:1472-1475），绕过前端直接调 `/assistant/solve` 仍可求解。
- **缺口 5（记忆动作）**：「记住，这学期张老师周三晚尽量别排」是长期偏好，现在解析响应不携带任何
  记忆动作字段，用户必须自己跑到记忆页手工建条目；「旧的不要用了」「不是长期偏好只是那两天请假」
  没有入口。生命周期接口其实都在（创建/修订/迁移，api.py:3411-3624），缺的是**解析结果到生命周期
  动作的受授权通道**。
- **缺口 6（任务上下文）**：已确认要求、当前范围、工作草稿、最近求解输入全部散落——
  `SolverRun.request_payload` 冻结在单次任务里、范围草稿只活在前端 `useState`（solver-page.tsx:88）、
  刷新即失；`/solver?goal=<id>` 已能绑定目标（solver-page.tsx:190-207）但**只绑 id 不恢复上下文**；
  新建目标后 goal_id 不进 URL；无指定基准时一律取最新已发布版本（api.py:4187-4195），不是该目标
  正在调整的工作草稿。
- **缺口 7（补救→执行）**：清单修订（PATCH /goals/{id}/checklist）补齐的禁排参数只改了**验收口径**，
  下一次求解输入来自 `run.request_payload`——修订永远编译不进求解；`gaps[].remedy` 已经机器可读
  （goal.py:1378-1499：`raise_budget`/`resolve_scope`/`await_admin`/`fix_checklist`），但前端只渲染
  `next_step` 文本（goals-page.tsx:423），「加预算重跑」仍是人肉三步。

```
教务一句话 ──► /assistant/interpret（携带 goal_id 时注入既有任务上下文）
                 │ 解析产物 = 范围 + 固定规则开关 + 【新增】任务级约束 + 【新增】记忆动作(带授权判定)
                 ▼
           确认卡（人点击 = 决策点）
                 │ 硬任务约束 ──► goal_checklist_draft 带参 forbidden_slot_free 项
                 │   （draft_checklist_from_interpretation 扩展消费 task_constraints）
                 │   ──► 前端建目标原样提交 checklist ──► goal.checklist（单一事实源）
                 │ 软任务约束/已确认口径 ──► goal.context（新增 JSON 列）
                 │ 显式记忆动作 ──► 直接执行（复用偏好生命周期 API）；推测 ──► probation 候选
                 ▼
           /assistant/solve ｜ /solver-runs
                 │ create_solver_run：goal 清单禁排项 + context 软约束 + 请求内任务约束
                 │   ──► 统一编译成 task_constraint_rules，校验用局部合并视图、
                 │   执行时并入 payload["rules"]（与记忆 compiled_rules 同管线）
                 │   ──► 未指定基准时默认取 goal.context.work_draft_schedule_id（缺口 6）
                 ▼
           CP-SAT ──► 草稿版本 ──► 自动验收（run completed → _evaluate_goal_for_run）
                 │ 缺口 = gaps[{summary, next_step, remedy}]
                 ▼
           下一步动作化：raise_budget 一键加预算重跑（不重选范围）／fix_checklist 补参后重跑
                 （重跑时清单补齐的禁排自动编译进求解——缺口 7 闭环）
```

设计总原则（对齐 02 ADR-2）：

1. **CP-SAT 是唯一裁决者，LLM 只提名**——模型输出的任务约束/记忆动作一律经过候选校验与授权分级，
   代码裁决执行；
2. **单一事实源**——硬任务约束的唯一持久归宿是 goal.checklist 的 `forbidden_slot_free` 项
   （验收与求解编译读同一处，杜绝两套口径漂移）；可派生的信息（最近求解输入、草稿版本）不冗余存储；
3. **复用优先**——记忆动作走既有生命周期接口，任务约束走既有规则管线（`payload["rules"]`），
   范围校验走既有 `_validated_assistant_scope`，缺口按钮走既有 `gaps[].remedy`；不新建平行体系。

## 1. 勘察锚点（本设计的全部依据）

| 事实 | 锚点 |
| --- | --- |
| 解析系统提示词 8 字段输出模板、`unsupported_requirements` 硬性要求 | backend/app/services/ai.py:613-632 |
| `_finalize_assistant_interpret` 规范化收口（502/422/三路 unsupported 汇聚/清单草稿） | backend/app/api.py:6888-6974 |
| 同步与流式 interpret 端点（Aily 兜底、双未配置 409） | api.py:6998-7062, 7065-7189 |
| `AssistantInterpretResponse` / `AssistantSolveRequest` / `AilySolveRequest` 契约 | backend/app/schemas.py:1482-1504, 1472-1475, 1453-1469 |
| 前端三道前置拦截与共享参数草稿提交 | frontend/src/pages/solver-page.tsx:446-507（447/449 两道、488-496 草稿取值） |
| 偏好生命周期：创建即 confirmed、PATCH 修订、transition（confirmed→expired/rejected、supersedes） | api.py:3411-3455, 3458-3502, 3505-3624；transitions 表 memory_solver.py:132-137 |
| 未授权 probation 不编译进求解（`_entry_outcome` → not_authorized） | memory_solver.py:631-695（645 只取 confirmed/probation，授权判定在 _entry_outcome） |
| 编译记忆整体入 `payload["rules"]` 同一规则管线 | services/tasks.py:577-597（594-596 追加） |
| 快照冻结规则全集、memory 进 checksum | services/snapshot.py:150-233（176-196 rules 列） |
| `create_solver_run` 组装（goal 版本冻结、无显式基准取最新已发布、`_validate_rule_coverage`） | api.py:4152-4233（4187-4202、4211、4214-4219） |
| 清单生成：`build_checklist(forbidden_slots=…)` 产带参 forbidden_slot_free 项 | services/goal.py:179-296（237-256）；`GoalCreateRequest.forbidden_slots` schemas.py:768 |
| 清单修订：整体替换 + 版本自增 + 条件 UPDATE（并发收口） | api.py:4530-4659 |
| 验收缺口结构 `gaps[{key,kind,summary,next_step,remedy}]`，remedy 四值 | services/goal.py:1361-1504 |
| 验收器 `_check_forbidden_slots`：needs_params/缺参 → unverifiable | services/goal.py:865-907 |
| `/solver?goal=` 挂载绑定（只绑 id） | frontend/src/pages/solver-page.tsx:190-207 |
| 目标页续办入口 `/solver?goal=${goal.id}` | frontend/src/pages/goals-page.tsx:281 |
| 求解时限手动面板可改（1-900）、默认 30 | solver-page.tsx:753、65；`AilySolveRequest.time_limit_seconds ge=1 le=900` schemas.py:1454 |
| AI 环境配置优先于库内配置（AI_BASE_URL/AI_API_KEY/AI_MODEL 三者齐备即 configured） | config.py:106-108, 188-189；services/ai.py:99-112 |
| AIService 走 `httpx.post(base_url + /chat/completions)`，URL 拼接容错 | services/ai.py:192-194, 279-310；流式 364-474 |
| e2e：webServer 双进程、AILY_SKILL_API_KEY、无任何 AI 配置 → interpret 409 | frontend/playwright.config.ts:28-51 |
| e2e 种子：教师 T01-T06、班级 B01-B12、时段 S01-S10（S05/S06=周三晚间） | backend/app/services/seed.py:96-156（时段 142-152） |
| 建目标自定义清单优先（显式 checklist 原样保存，forbidden_slots 仅缺省分支消费） | api.py:4434-4450（自定义分支 4434-4440） |
| `forbidden_slot` hard/soft 求解路径齐备（DATE/SLOT 双路径） | api.py:3010-3016（RULE_CONSTRAINTS） |
| 解释层 frozen_rules 只取快照 rules（TASK- 规则不可见）；库内兜底「找不到对应规则」 | services/explain.py:610-614、104-112；冻结分支返回值无 source_doc（83-89） |
| 执行/入队两处 `payload.update(run.request_payload)` 覆盖点 | services/tasks.py:613、637 |
| 流式解析请求体硬编码 `{instruction}`（goal_id 传递需改此文件） | frontend/src/lib/interpret-stream.ts:77 |
| 偏好拒绝原因五枚举 | backend/app/schemas.py:576-582 |
| `SOLVER_RULE_LABELS` 单一事实源（解析候选 = 求解规则键） | services/explain.py:44-50 |

## 2. TC-1/TC-3 · 缺口 4：任务级约束（本次要求进入求解，不污染全局库）

### 2.1 解析契约扩展（AssistantInterpretResponse 新字段）

`schemas.py` 新增：

```python
class AssistantTaskConstraint(BaseModel):
    """解析产物中的任务级约束：只作用于本次任务，不落全局规则库。"""
    id: str                          # 解析侧生成键（如 "tc-1"），页内操作/对应清单项引用
    source_text: str                 # 原话片段（如「张老师周三晚上不能上」），回执与审计用
    subject_type: Literal["teacher", "classroom", "cohort"] = "teacher"
    subject_ids: list[str]           # 必须取自候选，校验见 2.2
    slot_business_ids: list[str]     # 必须取自时段候选
    hardness: Literal["hard", "soft"] = "hard"
```

`AssistantInterpretResponse` 增加 `task_constraints: list[AssistantTaskConstraint] = []`；
`AssistantInterpretRequest` 增加 `goal_id: str | None = None`（续办增量解析用，见 4.3）。
**请求侧载体**：`AssistantSolveRequest`（schemas.py:1472-1475）增加
`task_constraints: list[AssistantTaskConstraint] = []`（同构模型复用）——API 直调 /assistant/solve
（无解析前置）时任务约束的入口，编译路径见 2.4；`SolveRequest`（手动路径）不加——手动求解的
任务约束只能经由 goal 清单（单一事实源），不允许绕过确认卡直传；`AilySolveRequest` 不加
（外部 Aily 直调通道维持现状，Aily 的任务约束经由解析通道与清单进入）。

解析系统提示词（ai.py:613-632）同步扩展：

- 输出模板追加 `"task_constraints":[]`，字段说明：把**本次任务**的具体禁排/指定要求结构化，
  主体与时段只能用候选值；区分「这次不能」（hard，进 task_constraints）与「长期偏好」
  （走 memory_actions，见 §3）；
- 候选值上下文 `_interpret_context`（api.py:6858-6885）追加两组候选：
  `teachers: [{business_id, name}]`（Teacher 表按方案）、`time_slots: [{business_id, weekday, start_time, end_time}]`。
  体量风险见 §10 待确认项——教师/时段是百级以下，班级候选（现状已全量注入 class_business_ids）不变；
- 保留硬性红线原文：未覆盖要求必须进 `unsupported_requirements`，禁止用通用标签冒充（ai.py:620-621 不动）。

`AILY_INTERPRET_CONTRACT`（api.py:6982-6995）同步追加新字段说明——Aily 通道产出同一结构，
继续走 `_finalize_assistant_interpret` 统一收口（飞书通道弱化不删除）。

### 2.1b 硬任务约束写入 goal.checklist 的唯一机制（写入者指定）

现状断链（评审确认）：`draft_checklist_from_interpretation`（goal.py:299-336）只按禁排语 regex
产 `needs_params=True` 占位项，不消费结构化的 task_constraints；前端建目标走**自定义清单路径**
（solver-page.tsx:459 `checklistDraft = [...interpretation.goal_checklist_draft]` → :476
`checklist: checklistDraft`），而 `create_goal` 自定义清单优先（api.py:4434-4440 原样保存），
`GoalCreateRequest.forbidden_slots`（schemas.py:768，可产带参项 goal.py:237-256）在该路径被忽略——
所以「清单里有带参禁排项」必须在**响应的 goal_checklist_draft 里就已带参**，前端链路才会原样带到。

写入者 = **后端 draft 生成器**，前端零改动：

- `draft_checklist_from_interpretation`（goal.py:299-336）扩展签名消费 `parsed["task_constraints"]`：
  - `hardness="hard"` 且主体/时段参数齐备 → 经 `build_checklist(forbidden_slots=…)` 的既有路径
    生成**带参** forbidden_slot_free 项（goal.py:237-256，requirement 文案、独立复核口径全部复用）；
    item key 与解析侧 constraint id 的对应关系写入 item.params（`{"task_constraint_id": "tc-1"}`），
    供确认卡/编译侧回溯原话；
  - `hardness="hard"` 但参数不齐（降级产物，2.2）→ 维持现状 `needs_params=True` 占位项；
  - `hardness="soft"` → 不进清单（软约束无验收意义），经确认后随求解请求体回到后端、
    由写入点①落 goal.context.soft_task_constraints（§4.2/§6.5 的前端填充链）；
  - 禁排语 regex 占位逻辑保留：有禁排语但没有任何对应 task_constraints 时的兜底（现状行为不变）。
- 前端传递链现状即通：`goal_checklist_draft` → checklistDraft（solver-page.tsx:459）→
  POST /goals `checklist`（:476）→ 自定义清单原样保存（api.py:4434-4440）→ goal.checklist。
  `ensure_bottom_line_items` 底线补全（api.py:4577）对带参项无副作用（只补底线，不删用户项）。
- `GoalCreateRequest.forbidden_slots` 字段保留（API 直接建目标、无解析前置的场景仍可用），
  但本主线不依赖它。

由此 §2.4 编译源、§5.1 缺口7 闭环、e2e S4 的「清单生成带参 forbidden_slot_free 项」断言
全部落在这一条已定义的写入链上。

### 2.2 规范化与降级规则（_finalize 扩展）

`_finalize_assistant_interpret` 对 `task_constraints` 逐条校验，**逐条降级而不是整响应 422**
（与范围未知实体 422 不同：任务约束坏一条不该废掉整次解析）：

1. `subject_ids` / `slot_business_ids` 里有未知值 → 该条剔除，原文追加进 `unsupported_requirements`
   （复用 6935-6940 的去重写回路径），`coverage_warnings` 附「任务约束『{source_text}』因主体/时段
   无法确认未进入结构化结果，请在清单中补参」；
2. 与既有清单口径同键合并去重（同 subject_type+subject_ids+slot_business_ids 视为重复，保留一条）；
3. 产出 `task_constraints` 时按 `hardness` 分流（hard 带参→清单带参项，soft→context，见 §2.1b/§2.4）。

### 2.3 服务端契约拦截：unsupported 不再只靠前端

`AssistantSolveRequest`（schemas.py:1472-1475）增加：

```python
unsupported_requirements: list[str] = Field(default_factory=list)  # 仅用于显式声明「已知悉未实现要求」
```

`assistant_solve`（api.py:7198-7234）在 `_validated_assistant_scope` 之前校验：
**该字段非空即 422**（「存在未进入求解的要求：…；请先在确认卡处置（补参或明确放弃）」）。
语义：这个字段不是给调用方传 unsupported 用的豁免口，而是**显式知悉记录**——契约上要求
AI 客户端把解析响应里的 unsupported 原样带回即被拒绝，留空数组=「本次指令没有未实现要求」。
前端行为不变（solver-page.tsx:447 的 return 保留，仍不发请求），拦截从「仅前端约定」升级为
「服务端契约」。审计 detail 里记录 `unsupported_count`。

**措辞边界（如实陈述强度）**：这是**契约层拦截，不是硬保证**——调用方静默省略该字段即绕过
（服务端无法区分「没见过 unsupported」与「隐瞒 unsupported」）。防线分工写明：前端闸
（确认卡禁用求解）是人面向的**主防线**；服务端闸防的是 API 客户端**明知有 unsupported 却带回**
的场景。文档与实施不得把这条表述为「服务端强制保证无 unsupported 求解」。

> 底线对齐：不擅自放宽硬要求——任何「带 unsupported 硬求解」的口子都不开；
> 用户真想放弃某条要求，唯一路径是在目标清单里把它删掉/改写（PATCH checklist，人工显式动作）。

### 2.4 求解编译路径（TC-3，与缺口 7 合流，见 §5.1）

**单一事实源 = goal.checklist。** 任务级约束进入求解的唯一编译函数：

```python
def _compile_task_constraints(
    *,
    goal: SolveGoal | None = None,
    request_constraints: list[AssistantTaskConstraint] | None = None,
) -> list[dict]:
    """把任务级约束编译成规则对象（与 snapshot.payload['rules'] 同构）。

    两个来源（可并存，goal 优先）：business_id 命名空间区分来源，
    解释层 source_doc 据此识别。
    """
    # goal 来源：
    #   hard：goal.checklist 中 kind=forbidden_slot_free 且参数齐备
    #        （params.needs_params 非真、subject_ids 与 slot_business_ids 均非空）的项
    #        → business_id: f"TASK-{goal.id[:8]}-{item_key}"，
    #          source_doc: f"goal:{goal.id}"
    #   soft：goal.context.soft_task_constraints（§4.1）
    #        → business_id: f"TASK-{goal.id[:8]}-soft-{约束 id}"，hardness="soft"、weight=30
    # 请求来源（无 goal 或请求直调，§2.1 请求侧载体）：
    #   → business_id: f"TASK-req-{constraint.id}"（constraint.id 由解析侧生成、请求直调时
    #     客户端必须提供，后端对缺失 id 按列表序号兜底生成并去重），
    #     source_doc: "request:task_constraint"
    # 公共字段：source_text=原话/清单 requirement,
    #           actor_type=RULE_ACTOR_TYPES[subject_type], actor_ids=params.subject_ids,
    #           constraint_type="forbidden_slot", scope={slot_ids: params.slot_business_ids}
```

- `create_solver_run`（api.py:4152-4233）在 goal_id 有值时调用（4214-4219 版本冻结附近），
  编译产物存 `payload["task_constraint_rules"]`；
  无目标但请求带 `task_constraints`（`AssistantSolveRequest.task_constraints`，§2.1 请求侧载体）
  → 同一编译函数的**请求来源分支**转换后存同键，business_id 用 `TASK-req-{constraint_id}`
  命名空间（区别于 goal 来源的 `TASK-{goal.id[:8]}-*`，同键去重与解释层 source_doc 识别
  各有锚点），一次性生效不落库（request_payload 冻结即全部痕迹）；
  请求项与 goal 清单项同键去重（同 subject_type+subject_ids+slot_business_ids 保留清单侧）；
- 执行侧合并点扩展 `_attach_memory_preferences`（tasks.py:577-597）→ 改名
  `_merge_frozen_extras`：`payload["rules"] = 显式规则 + memory.compiled_rules + payload["task_constraint_rules"]`。
  **不要**把规则对象直接写进 `request_payload["rules"]`——`execute_solver_run` 与 `enqueue_solver_run`
  的 `payload.update(run.request_payload)`（tasks.py:613、637 两处）会用任务级键**整体覆盖**快照冻结的
  规则全集（评审实证两处同陷阱）；
- **覆盖性校验 = 校验侧局部合并视图（不落库）**：`_validate_rule_coverage` 只读
  `payload["rules"]`（api.py:4113），而 task_constraint_rules 在独立键下对它不可见；又不能在
  api.py:4211 之前把规则对象真并进 payload（会随 request_payload 落库、触发上面的覆盖陷阱）。
  因此创建时校验调用改为传**局部合并的临时视图**，合并结果只进校验函数、不进任何持久字段：
  ```python
  _validate_rule_coverage({
      **snapshot.payload, **payload,
      "rules": [*(snapshot.payload.get("rules") or []),
                *(payload.get("task_constraint_rules") or [])],
  })
  ```
  foridden_slot 的 hard/soft 求解路径均已存在（RULE_CONSTRAINTS，api.py:3010-3016，
  DATE_PATH/SLOT_PATH 双路径），任务约束只要时段命中所选课次即可通过；
- **解释层需要显式接入（不是白得）**：`build_explanation_facts` 的 frozen_rules 只取
  `snapshot.payload["rules"]`（explain.py:610-614），TASK- 规则不在快照且永不建 Rule 行（§2.5），
  `describe_conflict_rule` 会落到「库里找不到对应规则」兜底（explain.py:104-112）。修复两处：
  ① frozen_rules 来源扩展为 `snapshot.payload["rules"] + run.request_payload.get("task_constraint_rules", [])`
  （仍是求解时点冻结值，口径不变）；② 冻结分支返回值透出 `source_doc` 字段（现状只回
  business_id/origin/meaning，explain.py:83-89），`goal:<id>` 前缀据此在前端识别「本次任务要求」，
  meaning 按 source_text 转述。

### 2.5 与全局规则库的边界（红线）

- 任务级约束**永不**创建 `Rule` 行、不出现在规则工作台；转长期偏好走 §3 记忆动作（显式授权），
  转正式规则走规则工作台既有流程——两步都是人的显式动作，绝不自动升级；
- `DataSnapshot` 不感知任务约束（它们不进 checksum）——同一批主数据+不同任务约束产生的是不同
  `SolverRun`（request_payload 不同），任务可复现性由 request_payload 保证，方案级复现由快照保证，
  两个层级各司其职。

## 3. TC-2 · 缺口 5：记忆动作（显式直接执行 / 推测走候选，绝不混用）

### 3.1 动作契约（AssistantInterpretResponse 新字段）

```python
class AssistantMemoryAction(BaseModel):
    action: Literal["save_preference", "expire_preference", "update_preference"]
    basis: Literal["explicit", "inferred"]     # 授权判定：显式声明 / 推测归纳
    source_text: str                            # 原话片段
    # save_preference：长期偏好主体与参数（同 PreferenceCreate 口径）
    subject_type: Literal["teacher", "classroom", "cohort", "course"] = "teacher"
    subject_id: str | None = None
    predicate: str | None = None                # 必须 ∈ ALL_PREDICATES（memory_solver.py:89-97）
    constraint: dict = {}                       # 如 {"slot_ids": ["S05", "S06"]}
    weight: int = 50
    valid_until: date | None = None             # 缺省走 default_valid_until_for_scope（随学期失效）
    # expire_preference / update_preference：目标条目 id——只能来自上下文注入的真实条目
    target_entry_id: str | None = None
    target_status: Literal["expired", "rejected"] | None = None   # 纠正为「只是那两天请假」时 rejected
    rejection_reason: Literal["temporary_leave", "subject_misidentified", "wrong_generalization",
                              "preference_changed", "other"] | None = None
    note: str | None = None
```

`AssistantInterpretResponse` 增加
`memory_actions: list[AssistantMemoryAction] = []` 与
`memory_action_receipts: list[AssistantMemoryActionReceipt] = []`：

```python
class AssistantMemoryActionReceipt(BaseModel):
    action_id: str
    status: Literal["executed", "pending_confirmation", "failed_degraded"]
    entry_id: str | None = None        # executed 时回填
    receipt: str                       # 一句话回执文案（可修改/可撤销提示固定后缀）
```

提示词（ai.py:613-632）追加输出字段 `"memory_actions":[]` 与判定规则：
用户原话出现明确命令式声明且主体与时段可从候选唯一确定 → `basis="explicit"`；其余（归纳口吻、
主体模糊、需要跨句推断）→ `basis="inferred"`。**两个 basis 由代码二次校验，不信模型自报**（3.2）。

**显式声明词表**（提示词与代码复核共用同一份常量，单一事实源，放在 services 层）：
记录类 `记住｜以后都｜这学期都｜以后一直｜长期`；撤销/失效类 `不要用了｜不要用｜别用了｜不用了｜
别再用｜作废｜撤销`；纠正类 `不是长期｜不是偏好｜只是那两天｜只是请假｜临时`。词表以 pytest 用例
锁定（TC-2），**必须逐条覆盖 e2e stub 的全部场景原话**（§7.2 的耦合声明）——词表漏配一个词，
对应场景的 explicit 复核就会降级 probation，S2/S3 的状态断言随之失败。

### 3.2 授权判定与执行（代码裁决，同一事务）

`_finalize_assistant_interpret` 产出 `memory_actions` 后，执行器 `_execute_memory_actions`：

- **explicit 判定收口**（模型说 explicit 不算数，代码复核三条全过才执行）：
  ① `save_preference`：subject_id 在候选集内（复用 `_validate_preference_subject`，api.py:3422 同函数）、
  predicate ∈ `ALL_PREDICATES`、constraint 的 slot/room ids 校验存在；
  ② `expire/update_preference`：`target_entry_id` 必须命中 `_interpret_context` 注入的真实活跃条目
  （§3.4），id 是系统生成不可猜——上下文里没有该 id 一律降级；
  ③ 原指令含显式声明词（与 ② 同一正则口径进提示词的词表，代码侧用同表复核）。
- **explicit 通过 → 直接执行，复用既有生命周期函数（不重写逻辑）**：
  - save → 内部调用 `create_preference` 同构逻辑（status="confirmed"，source="explicit_stated"，
    `default_valid_until_for_scope` 兜底有效期，`resolve_conflicts_for_new_entry` 冲突消解）——
    与 POST /memory/preferences（api.py:3411-3455）共用一个 service 函数，API 端点改为薄壳；
  - expire/reject → `PreferenceTransition` 同构逻辑（transition_preference 的 status 分支，
    api.py:3562-3624；rejection_reason 落 preference_rejections 的口径沿用 3605-3615）；
  - update → PATCH 同构（update_preference，api.py:3458-3502）；
  - 执行成功 → receipt `{status:"executed", entry_id, receipt:"已记住：{subject}{predicate 中文}
    （有效期至 {valid_until}）。可在「记忆」页修改或撤销。"}`；执行抛 4xx → 降级
    `pending_confirmation`，receipt 注明原因；
  - 审计沿用既有 `audit()` 调用点（create/transition/update 已覆盖 api.py:3452/3486/3621），
    provenance 追加 `"via": "assistant_interpret", "instruction": …` 形成原话回链。
- **inferred 或降级 → 候选审批**：创建 `PreferenceEntry(status="probation", trial_authorized=False,
  source="inferred_from_instruction")`（来源值的 schema 层扩展方式见 §10.4——库列 String(30)
  无约束，但 `PreferenceSource` Literal 需同步或复用既有值），
  收件箱照常展示，教务按既有 transition/authorize_trial 三动作处置。
  **未授权 probation 永不进求解**——这是现状保证（memory_solver.py 编译只认 confirmed+授权试用），
  本设计不新增任何「候选直接生效」路径。
- **两种授权绝不混用的机器保证**：explicit 执行失败一律降级 pending_confirmation（候选），
  不存在「半执行」；inferred 永不直接执行；Aily 通道（source="feishu_aily"）**一律按 inferred 处理**
  （外部通道输出未经过我方授权判定链，授权降级更安全——飞书增益保留、权限红线不外放）。

### 3.3 三类话术的落点（验收场景对照）

| 话术 | 动作 | basis | 执行 |
| --- | --- | --- | --- |
| 「记住，这学期张老师周三晚尽量别排」 | save_preference（avoid_slot, S05/S06, soft） | explicit | 直接 confirmed，回执可撤销 |
| 「旧的周三晚偏好不要用了」 | expire_preference(target_entry_id) | explicit（id 命中注入条目） | 直接 transition expired |
| 「不是长期偏好，只是那两天请假」 | expire/reject(target_entry_id, temporary_leave) | explicit | 直接 transition（候选条目→rejected 落拒绝记忆） |
| 「张老师好像不太愿意上晚上」 | save_preference | inferred | probation 候选，收件箱审批 |

### 3.4 上下文注入与失效窗口

`_interpret_context` 追加 `active_preferences`：本方案 `status IN (confirmed, probation)` 条目的
摘要 `[{id, subject_type, subject_id, predicate, constraint, modality, status, valid_until}]`，
按 created_at 倒序上限 50 条（防 token 膨胀，见 §10）。模型据此产生 `target_entry_id`；
代码侧校验 id 必须命中该列表——列表外的 id 一律视为幻觉，降级候选。

## 4. TC-4/TC-5 · 缺口 6：任务上下文持久化

### 4.1 存储形状：SolveGoal.context（新 JSON 列）

```python
context: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
# schema_version: 1
# {
#   "scope": {business_lines, product_types, class_business_ids,
#             date_from, date_to, date_window_days},      # 当前确认的范围草稿（决策时点快照）
#   "soft_task_constraints": [{id, subject_type, subject_ids, slot_business_ids,
#                              source_text}],             # 软任务约束（编译见 §2.4；硬约束不在这里）
#   "work_draft_schedule_id": "…",                        # 该目标正在调整的工作草稿（版本 id 指针）
# }
# 注意：不设 decisions 字段——决策明细已由 AuditLog 全量承载（既有 action 集合足够），
# 本设计没有它的声明消费者，写了没人用的字段不做（契约最小性）。
```

**单一事实源划分（context 只存没有其它归宿的状态）**：

| 信息 | 归宿 | 不进 context |
| --- | --- | --- |
| 硬任务级约束 | goal.checklist `forbidden_slot_free` 项（验收=求解编译同源，§2.4/§5.1） | ✓ |
| 原始指令 | goal.instruction（models.py:615 已有） | ✓ |
| 验收口径与版本 | checklist + checklist_revision（models.py:618-631） | ✓ |
| 最近求解输入 | SolverRun.request_payload（goal_id 关联，latest_run_id 指针已有） | ✓ |
| 决策明细 | AuditLog（既有 action 集合足够，不冗余） | ✓ |
| 范围草稿 / 软约束 / 工作草稿指针 | **context** | — |

context 是「当前工作状态」而非「验收口径」，**不设独立版本列、不参与验收乐观锁**
（口径保护已由 checklist_revision 全套覆盖，models.py:624-631）；并发写风险由决策点单写者
（见 4.2）+ 审计兜底，注释写明语义。

**迁移**：新增 alembic revision，`ALTER TABLE solve_goals ADD COLUMN context JSON NULL`；
旧目标 context=NULL 视为「无上下文」，续办时按 4.4 的惰性初始化回填。

### 4.2 写入时机 = 用户决策点（解析不写 context，含一个已声明的例外）

解析**不写 context**（范围/任务约束等解析产物必须经确认卡，未确认的产物不是「已确认要求」）。

**已声明的唯一例外**：§3.2 的 explicit 记忆动作在 `_finalize` 内直接执行（含 DB 写）——这**不是**
对「解析无副作用」的违反，而是行为语义不同：用户原话「记住/不要用了」本身就是显式指令（决策已经
发生），有审计回链与可逆路径（PATCH 修订 / transition expired 撤销）。实施时**不得**把记忆动作
推迟到确认卡——否则响应回执的 `executed` 状态契约（§3.1）不成立，S1-S3 场景断言全部失效。
除该例外，解析产物一律不落库。

写入点两处：

1. **创建求解任务**（submit_solver_run / assistant_solve → create_solver_run，goal_id 有值）：
   本次请求的范围合并进 context（`scope` 整体覆盖），soft 任务约束按 id 幂等合并
   （来源=请求 `AssistantSolveRequest.task_constraints` 中 hardness=soft 的项，§2.1 请求侧载体、
   §6.5 前端填充链；无请求项时维持 context 现值），同事务 + audit `action="update_context"`；
2. **run completed 产出草稿**：`_evaluate_goal_for_run`（tasks.py:465-483）同一独立事务里，
   按 `ScheduleVersion.solver_run_id`（models.py:430 unique）反查本次产物，版本 status=draft 时
   写 `context.work_draft_schedule_id`（发布/回滚不改这个指针——「正在调整」语义由 4.6 的
   惰性校验保证）。

PATCH checklist 不动 context（清单修订是口径决策，不是工作状态变更），决策明细由既有修订审计
（api.py:4623-4643）承载。

### 4.3 增量解析：对既有状态的增量修改

`AssistantInterpretRequest.goal_id` 有值时（续办）：

- `_interpret_context` 追加 `task_context` 节：goal.instruction（原文）、context.scope、
  活跃任务约束（清单 forbidden_slot_free 项 + context.soft_task_constraints）、
  最近一次求解输入摘要（latest_run 的 request_payload 的范围/日期/规则键）、active_preferences（§3.4）；
- 提示词追加一段：**这是既有任务的延续，用户的这句话是对既有状态的增量修改**——未提及的
  范围字段沿用 task_context 现值（输出仍填完整结构化字段，值来自上下文），新要求追加不覆盖
  既有禁排，除非明确撤销；
- `_finalize` 照常收口；前端确认卡展示「在目标 #xxx 基础上新增/修改」的差异
  （沿用既有 checkGoalConsistency 提示条模式，solver-page.tsx:282-312），
  用户确认后经 4.2 决策点落库。

### 4.4 续办恢复：/solver?goal= 恢复完整上下文

solver-page.tsx:190-207 的绑定 useEffect 扩展（绑定的 GET /goals/{id} 响应现在带 context）：

1. `setGoalId` + 提示条（现状）之后：`instruction` 回填 goal.instruction；`params` 回填
   context.scope（business_lines/product_types/class_business_ids/date_from/date_to/
   date_window_days——字段与 SolverParamValues 一一对应，solver-page.tsx:40-50）；
   context 为空（旧目标）时仅回填 instruction，范围留空照旧；
2. `trackGoal=true` 固定（现状 194 已设）；
3. 硬任务约束随清单项在「识别规则/本次任务要求」区展示（§7）；
4. 恢复失败不阻塞绑定（try/catch 降级为现状行为——只绑 id）。

### 4.5 goal_id 同步进 URL

- 新建目标成功处（solver-page.tsx:480-482 `setGoalId(goal.id)`）同步
  `setSearchParams({ goal: goal.id }, { replace: true })`（useSearchParams 已引入，
  solver-page.tsx:4/134——把只读解构改为读写）；
- 手动求解路径同样以 `goalId` 状态为准（SolverParams 提交时已带 goal_id，
  solver-page.tsx:545），URL 在绑定/创建两个时机写入，清除关联（onClearGoal，539-540）时删除该参数；
- 刷新/分享 URL 即可回到同一任务上下文（配合 4.4）。

### 4.6 基准默认工作草稿（非「当前已发布」）

`create_solver_run` 的 parent 兜底（api.py:4187-4195）改为三级：

1. 请求显式带 `parent_schedule_id`（现状保留）；
2. goal_id 有值且 `goal.context.work_draft_schedule_id` 指向的版本**仍为 draft** →
   以它为 parent，previous_assignments 冻结该版本 assignments；
3. 其余情况 → 现状（最新已发布版本）。

理由：续办目标的「最少变更」应相对**该目标上一轮产出**，相对已发布版本会把上一轮草稿的全部
调整都算成变更、诱导求解器回退已确认的成果。三级选择写入 request_payload 的
`baseline_source` 字段（explicit_parent/goal_work_draft/latest_published），解释层可转述。
`minimize_changes`、diff 链路无需改动（它们只读 previous_assignments/parent_id）。

## 5. TC-6 · 缺口 7：补救→执行（后端编译随 TC-3 已闭合）

### 5.1 清单修订编译进下一次求解

由 §2.1b 写入链 + §2.4 单一事实源直接闭环：解析产物经 draft 生成器带参写入 goal.checklist
（§2.1b）后，`_compile_task_constraints`（goal 来源）在**每次**创建求解时从
goal.checklist 现值编译——PATCH /goals/{id}/checklist（api.py:4530-4659）补齐
forbidden_slot_free 参数（ForbiddenParamForm 保存后 `needs_params` 清除，
goals-page.tsx:485-488 注释所载流程）→ 下一次 /solver?goal= 或 /assistant/solve 创建任务时
该禁排自动成为规则对象进入求解，同时验收器 `_check_forbidden_slots`（goal.py:865-907）
因参数齐备恢复可复核。**求解与验收从此读同一份清单**，不存在「验收知道了、求解不知道」的时差。

边界：`_validate_rule_coverage` 对编译产物同样生效（经 §2.4 的校验侧局部合并视图）——补的时段
若与所选课次无求解路径交集，创建任务即 422 并点名规则 business_id（`TASK-…` 前缀），
教务在清单里修正而不是求解悄悄忽略。

### 5.2 加预算路径（remedy=raise_budget 动作化）

后端零改动：`gaps[].remedy` 已四值机读（goal.py:1378-1499）。前端动作化：

- goals-page / solver-page 的报告区，`remedy="raise_budget"` 的缺口渲染按钮
  「加大时间预算重跑」（复用现有 Button/Badge 样式，不改视觉语言）；
- 点击 = 纯前端动作：`params.time_limit_seconds = min(max(当前×3, 90), 900)`（schemas 上限
  900，schemas.py:1454/805）→ 以**同一 goal_id、context.scope 的同一范围**直接提交求解，
  **不重新解析、不重新选范围**——范围与清单未变，MEM-I2 的扩大判定（solver-page.tsx:329-335）
  天然不触发；
- **提交路径（评审指认的守卫冲突，不得字面复用 solveFromInterpretation）**：该函数开头有
  `if (!interpretation || interpretation.unsupported_requirements?.length) return` 守卫
  （solver-page.tsx:447），而加预算的典型落点（goals-page 报告区、刷新后的 solver 页）
  `interpretation` 必为 null——字面复用必然提前返回。改为**独立函数**（如 `submitBudgetRetry()`）
  复用手动求解提交路径（SolverParams onSubmit → submit.mutate，solver-page.tsx:545，
  已带 goal_id；范围/日期来自 §4.4 从 context.scope 回填的 params）；goals-page 的按钮跳转
  `/solver?goal=<id>&action=raise_budget`，solver-page 挂载时解析 action 参数执行同一函数——
  「一键」语义保留，不依赖解析状态；
- `remedy="fix_checklist"` → 按钮跳目标详情清单区（复用 goals-page 既有锚点）；
  `resolve_scope` → 回求解页聚焦范围区；`await_admin` → 保持文本（人的裁决，无按钮）；
- `suggested_instruction`（explain 确定性拼装、可被 interpret 原样重解析，explain.py:821+）
  继续作为文本路径保留——按钮是结构化捷径，不是替代。

### 5.3 新增/放宽重要要求的确认分级

| 变更 | 确认级别 | 机制 |
| --- | --- | --- |
| 只加预算 / 同口径重跑 | 无需确认 | §5.2 直接提交 |
| 新增任务级约束 | 确认卡展示新增项，人点「确认并开始求解」 | 既有确认卡按钮即确认动作 |
| 范围扩大 | 现有 scopeExpansion 单独确认 | solver-page.tsx:113-115, 417-445（不动） |
| 清单放宽/删项 | PATCH checklist 显式保存 | api.py:4530（不动） |
| 放弃 unsupported 原话要求 | 目标清单修订（唯一路径，§2.3） | 同上 |

## 6. 前端设计（TC-6，视觉冻结合规）

全部复用现有 token/组件/动效（border-l-2 提示条、Badge、Button、Select、InfoTooltip、
animate-fade-in），**不新增任何样式语言**：

1. **确认卡扩展**（solver-page 一句话排课确认区，现 529 行区块内追加）：
   - 「本次任务要求」区：task_constraints 列表（主体×时段，hard/soft 徽标）；
     展示「仅作用于本次任务，不进入规则库」固定文案；
   - 「记忆动作回执」区：memory_action_receipts 逐条展示 receipt 文案
     （executed=绿色徽标 + 文案含「可在记忆页修改或撤销」；pending_confirmation=黄色徽标 +
     「已放入记忆收件箱待确认」）；
   - unsupported 区现状保留（amber 警示），文案改为指向两条出路：清单补参 / 放弃需修订目标清单。
2. **报告区下一步按钮**：§5.2 的 remedy 动作化（goals-page 420-423 与 solver-page RunPanel
   goal_report 区同步生效）。
3. **续办恢复**：§4.4 的回填是纯状态动作，无新视觉；goalNotice 文案沿用现有蓝条样式。
4. **增量解析的请求侧改造（必改文件，评审指认）**：`lib/interpret-stream.ts:77` 请求体硬编码
   `{ instruction }`——`streamInterpretInstruction` 签名增加可选 `goalId`，
   body 改为 `JSON.stringify({ instruction, ...(goalId ? { goal_id: goalId } : {}) })`；
   solver-page.tsx 的 `interpret()`（:364-405）**主路径是流式**（:374），传 goalId；
   回退同步 POST /assistant/interpret（:393）同样带 `goal_id`——两条通道口径必须一致，
   只改同步回退会漏掉主路径。这是 §4.3 增量解析在前端的前置条件，缺此改动 goal_id 永远到不了后端。
5. **求解请求体携带任务约束（软约束链的前端填充，评审指认缺失）**：`solveFromInterpretation`
   的 POST /assistant/solve 请求体（solver-page.tsx:484-498）新增
   `task_constraints: interpretation.task_constraints ?? []`——grep 实证前端现状从不发送该字段
   （`rg -c task_constraints solver-page.tsx` 零命中）。不补这一条，soft 约束在后端有入口
   （§2.1 请求侧载体）、前端永不填充、goal.context.soft_task_constraints 永远为空
   （§4.2 写入点①的唯一来源是请求项），整条软约束链静默失效。确认卡展示的 task_constraints
   与请求体同源（同一 interpretation 状态）；goal 来源的 hard 约束不依赖此字段（走清单编译，
   §2.4），请求体带全量由后端同键去重兜底（§2.4）。

## 7. e2e 方案（TC-8）

> **实施说明（2026-09-29）**：本节方案在 MEM-J3 时没有落地（当时只做了 vitest），已由 MEM-K5 以另一种实现补齐：
> 假模型是 `backend/scripts/fake_model_server.py`（Python 标准库，而非下文的 `ai-stub.mjs`），playwright.config.ts
> 的 webServer 起在 8002；用例经 `POST /api/v1/integrations/ai/configuration` 把后端指向它，而不是给后端 webServer
> 设 `AI_*` 环境变量（那会改变其它 e2e 用例的 AI 状态）。落地的场景在
> `frontend/tests/e2e/task-context-flow.spec.ts`：核心示例句、显式「记住」、显式「不要用了」、
> 主体无法确认（登记目标→补参→再解析→求解→验收），与下文 S1–S7 的对应与取舍见 04 十期 MEM-K5。
> 下文保留为设计当时的方案记录。

### 7.1 解析替身注入：环境配置 + 本地 OpenAI-compatible stub（零生产代码改动）

勘察结论：e2e 无任何 AI 配置（playwright.config.ts:36-42 未设 AI_*，e2e 库无 AIProviderConfiguration
行），`/assistant/interpret` 在 e2e 里必 409（api.py:7038-7042）；后端也没有 app_env/testing
分支可挂钩（rg 全仓无 interpret 路径命中）。最省且最真实的替身是**复用既有环境配置优先级**：

- playwright webServer 数组（frontend/playwright.config.ts:30-51）增加第三台：
  `node tests/e2e/stubs/ai-stub.mjs`（node:http 零依赖），监听 `127.0.0.1:8901`，
  提供 `POST /v1/chat/completions`：
  - `stream:false` → 按指令关键词路由返回固定 OpenAI 响应（choices[0].message.content = 固定
    解析 JSON，与 ai.py:613-632 输出契约逐字段一致）；
  - `stream:true` → 返回 SSE：2-3 个 `delta.reasoning_content` 思考帧 + 一个完整 content 帧 +
    `[DONE]`，喂 `_chat_stream_json`（ai.py:364-474）真实解析路径。
    **该分支是必需而非冗余**：前端解析主路径是流式（solver-page.tsx:374 优先 SSE，
    :393 才回退同步），SSE 不实现则所有场景都走回退链路——能跑但偏离真实用户路径，
    流式协议本身的回归价值归零；
  - 关键词路由（场景表见 7.2）：含「记住」→ save_preference 场景 JSON；含「不要用了」→ expire；
  含「请假/只是那两天」→ correct；含「不能上/别排」→ task_constraints 场景；默认 → 基础范围。
- 后端 webServer env（playwright.config.ts:36-42）追加：
  `AI_BASE_URL: "http://127.0.0.1:8901/v1"`、`AI_API_KEY: "e2e-stub-key"`、
  `AI_MODEL: "e2e-stub-model"` → `ai_environment_configured`（config.py:188-189）为真，
  credentials 走 environment（ai.py:99-103）——**AIService→httpx→_parse_json_object→
  _finalize 全链路真实执行，替身只替换模型输出**；`E2E_EXTERNAL_SERVERS=1` 时 env 由外部
  服务自行提供（可在该模式下起同一 stub）。
- 为什么不用依赖注入/测试分支：注入 fake AIService 需要 FastAPI dependency override 或 app_env
  分支——生产代码新增测试挂钩违背轻量策略；stub 方案复用 ai.py:192-194 的 URL 拼接与 279-310
  的请求路径，替身面最小、回归价值最高。

### 7.2 场景清单（新文件 frontend/tests/e2e/task-context-flow.spec.ts）

种子标识（seed.py:96-156）：教师 T01（教师甲）、班级 B01、时段 S05/S06（周三 晚间1/晚间2）。
登录沿用现有 spec 的 admin 表单流（scheduling-flow.spec.ts:41-45）。

| # | 场景 | stub 固定解析 | 断言 |
| --- | --- | --- | --- |
| S1 | 记录偏好→回执 | save_preference(explicit, T01, avoid_slot [S05,S06], soft) | 确认卡回执「已记住…可修改/撤销」；GET /memory/preferences 出现 confirmed 条目（valid_until 有值）；rules 列表无新 Rule 行 |
| S2 | 撤销偏好 | 先 API 造 confirmed 条目 →「旧的周三晚偏好不要用了」→ expire(target_entry_id=注入 id) | 条目 status=expired；回执文案 |
| S3 | 纠正为临时请假 | correct(target_entry_id, rejected, temporary_leave) | status=rejected；preference_rejections 落 temporary_leave |
| S4 | 重排 A 班（含禁排）→ 约束进求解 → 草稿 | task_constraints：hard[T01, [S05,S06]] + **soft 伴随一条[T02, [S05]]** + scope [B01] + 周窗口 | 解析响应 goal_checklist_draft 含带参 forbidden_slot_free 项（§2.1b，仅 hard 项），建目标后 goal.checklist 同项；run.request_payload.task_constraint_rules 含 **hard+soft 两条** forbidden_slot 规则对象（API 查 run，business_id 分别为 TASK-{goal}/TASK-{goal}-soft-*）；goal.context.soft_task_constraints 落库 soft 项（§4.2/§6.5 前端填充链）；草稿版本 T01 无 S05/S06 课次（assignments 断言）；goal.context.work_draft_schedule_id 指向草稿 |
| S5 | 缺口展示（少哪节/为什么/下一步） | 任务约束参数不齐（slot 空）→ unsupported + needs_params 占位 | 确认卡「以下要求尚未进入求解」+ 求解按钮禁用；清单含 needs_params 占位项；API 补参（PATCH checklist）后创建求解 → 产出缺课次报告（或 S4 的 UNKNOWN 变体由 stub 不变式保证确定性）→ goal_report.gaps 有 remedy/next_step，按钮「加大时间预算重跑」出现 |
| S6 | 续办恢复上下文 | S4 完成后从 /goals 点「修正范围后重新求解」 | URL 含 goal=；范围/日期/instruction 已回填（context 恢复）；重新解析（stub 返回增量口径）→ 确认后同 goal 提交 → 新 run 的 previous_assignments 来自工作草稿（baseline_source=goal_work_draft） |
| S7 | 服务端 unsupported 拦截（API 级） | — | POST /assistant/solve 带 unsupported_requirements=["具体教师的禁排或请假要求"] → 422 |

既有 3 个用例（scheduling-flow 1 + schedule-visual 2）不动；本 spec 与 scheduling-flow 同库
序贯（workers=1、fullyParallel=false，playwright.config.ts:16-17）。

**词表耦合声明（S2/S3 的成立前提）**：S2 的「旧的周三晚偏好不要用了」、S3 的「只是那两天请假」
必须命中 §3.1 显式词表的**代码侧**正则——explicit 复核不过就降级 probation，S2 的 expired、
S3 的 rejected 断言即失败。约束关系写死：词表常量的 pytest 用例（TC-2）逐条断言本文档 §3.3
与 §7.2 的全部场景原话能命中对应类别；改 stub 原话或改词表必须同批过该用例。

### 7.3 稳定性措施

- stub 输出**完全固定**（无随机、无时钟依赖；相对日期场景一律由 stub 直接给绝对 YYYY-MM-DD，
  不依赖「当前日期」提示词行为）；
- 断言全部用业务标识（T01/B01/S05）与 API 可查状态（request_payload/status/report），不用坐标
  或渲染位置；页面断言只锚 aria-label/角色（沿用现有 spec 的 getByLabel/getByRole 风格）；
- 轮询用 expect.poll（现状模式，scheduling-flow.spec.ts:53-57/75-78）；验收报告等待沿用
  前端 45 秒截止语义（solver-page.tsx:223-241）；
- stub 失败模式显式：未命中关键词返回 500 + 明确 error 字段，让用例失败可归因到「stub 路由缺场景」
  而不是悬挂超时。

## 8. 实施批次（写入 04 登记九期时沿用此编号）

| 批次 | 内容 | 风险 | 最小验证 |
| --- | --- | --- | --- |
| TC-1 | 解析契约扩展：schemas 新模型、请求侧载体（AssistantSolveRequest.task_constraints）、提示词/上下文扩展（teachers/time_slots/active_preferences/task_context）、_finalize 逐条降级、**draft_checklist_from_interpretation 扩展消费 task_constraints（硬约束带参项写入 goal_checklist_draft，§2.1b 写入者）** | 中（提示词回归） | 后端 pytest：解析单测（monkeypatch httpx 同 backend/tests/test_ai.py:95 既有模式）新增 task_constraints/memory_actions/降级用例、**draft 带参项生成用例（硬约束→带参 forbidden_slot_free；soft→不进清单）** |
| TC-2 | 记忆动作执行器 + 候选降级 + source 枚举扩展 + 回执 + **显式词表常量（单一事实源，提示词/代码复核共用）** | 高（授权红线） | pytest：explicit 三话术执行、inferred 候选、Aily 强制降级、id 幻觉降级、**词表用例逐条断言 §3.3/§7.2 全部场景原话命中对应类别** |
| TC-3 | 任务约束编译（**_compile_task_constraints 双来源：goal 来源与请求来源的 business_id/source_doc 规则（TASK-{goal}/TASK-req-*）** + _merge_frozen_extras 改造 + AssistantSolveRequest.unsupported 422 + **_validate_rule_coverage 局部合并视图 + build_explanation_facts/describe_conflict_rule 接入 task_constraint_rules 并透出 source_doc**） | 高（求解输入正确性） | pytest：编译对象同构、**双来源 business_id/source_doc 规则、请求来源缺 id 序号兜底**、update 覆盖陷阱回归（tasks.py:613/637 两处场景）、422 拦截、覆盖性校验局部合并、**解释层冲突规则转述含任务约束 source_text** |
| TC-4 | SolveGoal.context 列 + 迁移 + GoalResponse/详情暴露 + 决策点写入 | 中 | pytest：两写入点、旧目标 NULL 惰性、审计 |
| TC-5 | 基准默认工作草稿（三级 parent） | 中 | pytest：三级选择、draft 已发布回退 |
| TC-6 | 前端确认卡扩展 + URL 同步 + 续办恢复回填 + remedy 按钮 + **interpret-stream.ts goal_id 传递（流式主路径与同步回退两通道，§6.4）+ solveFromInterpretation 请求体携带 task_constraints（§6.5，软约束链前端填充）+ 加预算独立提交函数（§5.2，不走 :447 守卫路径）** | 中（视觉冻结） | vitest：回执渲染、URL 写入、context 回填、**流式/回退请求体均含 goal_id、求解请求体含 task_constraints（hard+soft）**、raise_budget 提交参数与提交路径（不早退）；tsc |
| TC-7 | e2e：ai-stub.mjs + playwright 配置 + task-context-flow.spec.ts（S1-S7） | 中 | `pnpm playwright test`（本地跑通 3 旧 + 7 新；CI billing 恢复后覆盖） |
| VER-9 | 终验：后端 ruff/mypy/pytest 全量；前端 tsc/vitest 全量；e2e 全量；openapi.json 重生成 + orval 零 diff | — | 全量 |

openapi 影响：**有**（AssistantInterpretResponse/Request、AssistantSolveRequest、GoalResponse
新增字段）→ openapi.json 重导出 + orval 重生成（VER-1 既定流程）。

## 9. 范围边界（明确不做）

- **自动推进策略**：失败后哪些动作自动执行本版不定——`remedy` 只渲染成按钮，默认由人点击；
  验收器的停止规则（不自动无限重跑）现状已是红线（models.py:595-600 注释），不改；
- **方法经验库**（L2 程序性记忆/playbook）不做；
- 不引入向量库/图库（02 ADR-2 既定）；
- 不改前端视觉风格与动画（§6 只做组件级复用扩展）；
- 任务级约束不自动进全局规则库/记忆（§2.5 红线）；候选不自动生效（§3.2）；
- 不做解析多轮对话/澄清追问（unsupported 的出路是清单补参，不是 chat loop）；
- Aily 通道只同步契约字段说明（AILY_INTERPRET_CONTRACT），不做 Aily 侧记忆动作自动执行（§3.2）。

## 10. 待实现期确认项（不编造，如实标注）

1. **`_interpret_context` 注入体量**：teachers/time_slots/active_preferences 全量注入的真实 token
   占用未实测（本仓只有演示种子，生产规模未知）。实施时若超限：teacher 候选按名称前缀/拼音预筛、
   active_preferences 截断为最近 50 条是设计内兜底，阈值需以真实数据量定。
2. **stub SSE 帧与 `_chat_stream_json` 的逐字段对齐**（ai.py:364-474 的 delta/reasoning_content
   解析细节）：以 TC-7 实施时对着该函数写 stub 的用例为准，本文档只锁协议形状（7.1）不锁字节。
3. **work_draft 回写的事务边界**：`_evaluate_goal_for_run`（tasks.py:465-483）与版本反查的先后
   ——`ScheduleVersion` 行在 `_persist_result` 事务里创建（tasks.py），验收钩子在独立事务，
   理论上已可见；若观察到时钟窗，把回写挪进 `_persist_result` 同事务，设计不受影响。
4. **`source="inferred_from_instruction"` 枚举值**：库列是 String(30) 无约束（models.py:528），
   但 schema 层有 `PreferenceSource = Literal["explicit_stated", "admin_directive",
   "induced_from_adjustment"]`（schemas.py:513），挖掘候选统一落
   `induced_from_adjustment`（api.py:3921/4003）。新增值需同步扩展该 Literal（或内部创建复用
   `induced_from_adjustment` 并在 provenance 标 `via=assistant_interpret`，二选一在 TC-2 实施时定）；
   红线①的 hard 限制只针对 induced 来源（api.py:3571），新增来源值不得绕过该判定；前端记忆页
   来源徽标映射需同步。
5. **S5 的 UNKNOWN 变体**：构造「预算不足→UNKNOWN→raise_budget」的确定性 e2e 需要种子课次规模
   足够大才能稳定触发 UNKNOWN；实施时若 30s 内必然 OPTIMAL，则 S5 只断言 fix_checklist 路径，
   raise_budget 路径降级为 vitest 组件级断言（前端提交参数正确）。
