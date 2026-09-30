# 04 · 实施计划与进度

> 状态：✅ 十期第八轮复审「主线可用性收口」完成 · VER-10 通过（2026-09-29：后端 ruff/mypy 全过 + pytest 415 passed；前端 tsc 0 错误 + vitest 160 passed（20 文件）+ build + orval 零 diff；playwright 7 passed（原 3 个 + 任务上下文业务场景 4 个）；CI 因账户 billing 仍未启动，runners 未分配）
> 批次策略遵循工作区 AGENTS.md：按模块分批、实现代理自检 + 局部验证、里程碑统一全量验证、每批一个原子提交。

## 勘察修正（重要）

代码勘察结论修正了升级前提：**项目不存在「写出来但没接后端」的功能**——85 个端点全部真实、前端调用全部打到后端。真实差距是：

1. **反向差距**：`POST /auth/change-password`（改密无 UI）、SSE `/solver-runs/{id}/events`、`GET /schedules/{id}/calendar-bindings` 三处后端已实现、前端无入口；
2. **飞书耦合**：前端约 130 处飞书硬编码露出（integrations-page 整页 102 处 + 面板级 + 字段级），无独立设置页；
3. **文档断层**：无面向开发者的架构文档，14 列导入格式只存在于代码里。

## 批次总览

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| DOC-0 | roadmap 骨架 + 四大设计文档定稿 | ✅ 完成 | ceb0a8d, 3b8cc34, 8a04445, 515925c |
| IMP-A | 后端导入管线 v2（映射服务 + preview/commit + L1 补强） | ✅ 完成（18 新用例 + 74 回归全绿） | ae32a95 |
| INT-A | 后端集成抽象层（Protocol 能力接口 + registry，飞书 strangler 收敛为首个适配器，LocalAdapter 默认可用） | ✅ 完成（10 新用例 67 全绿，4212479） | 4212479 |
| SET-A | 前端「设置」页重构（/settings 四分区：通用+改密 / AI 模型 / 集成卡片 / 关于；旧 /integrations 重定向） | ✅ 完成（45/45 vitest + tsc 零错误，7769e14） | 7769e14 |
| SET-B | 前端飞书露出中性化（文案级，5 文件 + e2e 名称；不含 integrations-page 与 app-shell 导航，那两处归 SET-A） | ✅ 完成（44/44 vitest 全绿，abdcb15） | abdcb15 |
| UX-0 | 求解页交互断点与侧边栏 IA 调研（Workflow：勘察→业界参考→三方案→评审综合） | ✅ 完成（简报=05-ux-sop.md，6784ac1） | — |
| UX-A | 求解页交互闭环（日期窗口与规则联动、AI 解析后渐进披露参数、解析加载态 + thinking 展示） | ✅ 完成（PR1 D1-D8，后端 55 + 前端 50 用例全绿，6590afc） | 6590afc |
| UX-B | 侧边栏与全局 SOP 重排（参考开源项目 IA；视觉风格不变） | ✅ 完成（D9-D13 全落地，64 用例全绿，5ebf349） | 5ebf349 |
| PUB-0 | 公开展示层调研（脱离妙搭后，面向老师/学生/家长的课表展示：免登录链接 / ICS 订阅 / 移动只读视图） | ✅ 完成（简报=06-public-showcase.md，6784ac1） | — |
| PUB-A | 后端公开课表 API（匿名 token 链接、ICS 订阅端点、调课通知；复用 public_* 数据资产与权限体系） | ✅ 完成（AST 逐字节等价迁移验证，全量 258 passed，d78c833） | d78c833 |
| PUB-B | 前端公开课表页（移动优先只读视图、二维码/链接分发、复用课表渲染组件） | ✅ 完成（17 文件 91 用例全绿，bb06dd2） | bb06dd2 |
| IMP-B | 前端导入向导（上传→映射→校验→提交，复用现有组件与动效；原地修复属 IMP-4 二期） | ✅ 完成（10 文件 53 用例全绿，86227a2） | 86227a2 |
| MEM-A | 后端记忆层（PreferenceEntry + 一键归因 + 挖掘循环 + 求解权重编译器） | ✅ 完成（83 定向用例全绿，f65eb16） | f65eb16 |
| MEM-B | 前端记忆 UI（待确认收件箱 + 详情页偏好区块 + 解释引用） | ✅ 完成 v1（收件箱/挖掘/偏好表 + 归因 chips；解释引用与教师可见属 v2，72 用例全绿，7a5640f） | 7a5640f |
| OSS-A | 开源文档套件（架构文档、README 重写、接入指南 feishu/local/dingtalk、LICENSE 建议） | ✅ 完成（ARCHITECTURE.md + README 开源版 7c7d61a；接入指南 44f7c16；**LICENSE 文件待用户拍板 Apache-2.0/MIT**；VER-1 顺带清理 backend README 的 tupai-seed 幽灵命令） | 44f7c16, 7c7d61a |
| VER-1 | 里程碑统一验证：后端全量 pytest + ruff/mypy + 前端 vitest + build + orval 再生成 | 里程碑 | — |

## 依赖关系

```
IMP-A ──> IMP-B（前端向导打新端点）
INT-A ──> SET-A / SET-B（设置页 + 露出点中性化）
MEM-A ──> MEM-B
IMP-A / INT-A / MEM-A 串行执行（共享 api.py，避免并发冲突）
前端三批（IMP-B / SET-B / MEM-B）文件不相交，可并行
VER-1 收口
```

## 风格红线（所有前端批次共同遵守）

- 页面根节点 `<div className="space-y-5 animate-fade-in">`；卡片 `rounded-lg border border-zinc-200 bg-white shadow-2xs`；
- 只用现有 ui 组件（button/badge/select/tabs/dialog/data-table/page）；不引入新 UI/动画依赖；
- 图标 lucide-react size-3.5/4；控件 h-8/h-9、text-sm/xs；微交互 150-300ms。

## 二期「规划中清零」批次（用户指令：文档里没做完的全部做完）

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| SCR-0 | 一键启动脚本（start.bat / scripts/start.sh + README 指引） | ✅ | 5e2aaba |
| UX-SSE | interpret SSE 流式（后端 stream 端点 + 前端 fetch reader + 失败回退） | ✅（后端 13 用例 + 前端 12 用例，全量 279 passed，e5c2890） | e5c2890 |
| CN-A | 钉钉 + 企业微信适配器 v1（真实 API 实现 + mock 单测 + 凭据加密配置端点） | ✅（12 用例，全量 279 passed，01e746e；未经生产凭据联调已在文档声明） | 01e746e |
| PUB-C | ICS RRULE 循环课次 + school 单班端点 + 按发布版本批量生成链接 + verify 端点 + Lark base_url settings 化 | ✅（7 新用例，全量 286 passed，b39b738） | b39b738 |
| IMP-C | 导入收尾：historical mapping（表头指纹记忆）+ 单元格原地修复（cell overrides） | ✅（断点接手完成，52 后端 + 104 前端用例绿，33e1859） | 33e1859 |
| MEM-C1 | 审查修正第一波：三态拆分（待确认/授权试用/已确认）、hard 转正式 Rule、课程适用日期窗口、实体匹配严格化、偏好冻结进快照、逐条使用结果 | ✅ 完成（七条验收全过，全量 298 passed / 前端 108 passed，69a70df） | 69a70df |
| MEM-C2 | 审查修正第二波：挖掘证据支持性校验（≥2 条不同证据/主体一致/约束来自证据/declared_reason 消噪/学期过滤）+ 拒绝记忆与矛盾消解 | ✅ 完成（8 新用例，全量 306 passed / 前端 109 passed，9109537） | 9109537 |
| MEM-C3 | 审查修正第三波：Goal 验收闭环（逐项验收清单 + 代码化验收器 + 报告回灌 + 停止规则） | ✅ 完成（13 新用例，全量 319 passed / 前端 117 passed，ee172a7） | ee172a7 |
| OSS-C | print CSS 张榜打印 + README「规划中」节清零 + VER-2 全量（含 e2e） | ✅ 完成（后端 ruff/mypy 全过 + 319 passed；前端 tsc 0 + vitest 117 passed + build 成功 + orval 零 diff；e2e 3 passed；README「项目状态」改为已实现 + 已知边界，ARCHITECTURE 同步） | — |

## 三期「正确性修复」批次（第二轮源码复审，2026-09-22）

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| MEM-D1 | 记忆组合正确性：未授权候选不得经冲突标记间接改变排课（改写固化错误语义的旧测试）；日期两层取交集；编辑后冲突重算；可学习事件公共前置筛选（AI/统计同准入，含最终态被接受过滤） | ✅ 完成（核心验收反转：旧 confirmed 照常编译；全量 339 passed，4704dc0） | 4704dc0 |
| MEM-D2 | 目标验收正确性：三集合分离（目标课次/求解课次/合并交付）；无法验证≠通过；底线验收独立于自定义清单（有交付物/课次不重复/完整性）；禁排参数存在性验证；_decide 消费 UNKNOWN vs INFEASIBLE；验收状态 pending/completed/failed 可见 | ✅ 完成（11 新用例，全量 339 passed / 前端 117 passed，792f629） | 792f629 |
| MEM-D3 | 目标连续性：re-parse 与手动求解均携带 goal_id；目标详情「继续处理」；禁排占位补参端点+UI；选基准自动生成 max_changes；清单修订版本化 + 记忆页裁决新语义适配 | ✅ 完成（前端 127 passed，92599f6） | 92599f6 |
| VER-3 | 终验：全量 pytest + vitest + build + orval 零 diff + e2e + 新增 GitHub Actions CI | ✅（后端 ruff/mypy/341 passed；前端 tsc/127/build/orval 零 diff/e2e 3 passed；CI 已入库 f77ac9c，runner 因账户计费未启动，待修复后自动生效） | 92599f6, f77ac9c |

## 四期「第三轮复审残留修复」批次（2026-09-22）

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| MEM-E1 | 记忆侧：冲突提出方按授权状态判定（较早候选被编辑不再波及较晚确认项）；学习集要求明确接受依据（pending/candidate_ready/draft 不入，采纳=候选版本曾发布）；窗口判断统一用实际生效交集函数 | ✅ 完成（4 新用例，全量 351 passed，9001490） | 9001490 |
| MEM-E2 | 目标侧：清单修订后 acceptance 回 pending、验收绑定 checklist_version 与参数快照；底线补全传完整范围；「交付课次不重复」独立底线项 | ✅ 完成（347 passed / 前端 131 passed，338ac6c） | 338ac6c |
| MEM-E3 | 「以新替旧」后端原子裁决端点（同事务：旧 expired+supersedes、候选 confirmed、冲突重算、审计、幂等），前端改调 | ✅ 完成（6 新用例，全量 357 passed / 前端 132 passed，f36e28c） | f36e28c |
| VER-4 | 终验：全量 pytest + vitest + build + orval 零 diff + e2e；CI 待 billing 修复后自动核验 | ✅ 后端 ruff/mypy/357 passed；前端 tsc/132/build/orval 零 diff/e2e 3 passed；CI 仍待 billing（workflow 就绪 f77ac9c） | — |

## 五期「第四轮复审收口」批次（2026-09-25，功能范围冻结）

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| MEM-F | 三项正确性收口：①空交集新条目不得触发 new_replaces ②旧验收写回带 checklist_version 条件（不覆盖新版本 pending）③范围修订三态语义（未提供/显式空列表/非空，显式 scope 优先）+ 6 业务场景迁入回归 + 4 处时间炸弹测试改相对窗口 | ✅ 完成（全量 363 passed / 前端 132 passed，238f1f8） | 238f1f8 |
| LICENSE | Apache-2.0 定稿（官方原文 LICENSE + 双 manifest 字段 + README License 节） | ✅ | ec050c9 |
| CI-e2e | CI 补 e2e job（自管服务 + playwright chromium + trace 上传），与本地 VER 口径对齐 | ✅ | ec050c9 |
| VER-5 | 终验：全量 pytest + vitest + build + orval 零 diff + e2e | ✅ 后端 ruff/mypy/363 passed；前端 tsc/132/build/orval 零 diff/e2e 3 passed；CI 待 billing（workflow 含 e2e job，ec050c9） | — |

## 六期「第五轮复审 F2 并发收口」批次（2026-09-25，功能范围冻结）

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| MEM-G | 目标验收写回数据库级并发保护：清单版本落为持久化计数列 checklist_revision（alembic 迁移存量回填 + 修订时 SQL 表达式同事务自增），验收结论写回只走条件 UPDATE——WHERE 携带 checklist_revision=评估时版本与 status<>'abandoned' 双条件并按实际行数判定，版本不匹配或目标已放弃时报告仅留档为历史、目标当前状态一字不改；四个并发交错（含重读后、提交前窗口）进入回归测试 | ✅ 完成（后端 ruff/mypy 全过 + pytest 365 passed，1a6a42b） | 1a6a42b |
| VER-6 | 终验：后端 ruff/mypy 全过 + pytest 365 passed；前端未改动，未跑前端套件；e2e 未在本地运行（无前端改动，CI 的 e2e job 将在推送后覆盖，CI 因账户 billing 仍未启动） | ✅（后端口径） | — |

## 七期「第六轮复审修订侧收口」批次（2026-09-25，功能范围冻结）

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| MEM-H | 清单修订侧并发复位保护：修订落库只走单条数据库条件 UPDATE（`apply_goal_checklist_revision`）——历史快照追加、清单替换、版本自增（SQL 表达式取库中当前值）与验收复位在同一语句里，SET 无条件把 acceptance_status 复位 pending 并用 CASE 按数据库当时状态把 achieved 回退 open，WHERE 携带读取时 checklist_revision 与 status<>'abandoned'，rowcount=0 时回滚、按库中状态返回 409 冲突且不留修订审计记录；先验收后修订等反向交错进入回归测试 | ✅ 完成（后端 ruff/mypy 全过 + pytest 369 passed，3b3ef96） | 3b3ef96 |
| VER-7 | 终验：后端 ruff/mypy 全过 + pytest 369 passed；前端 tsc 0 错误 + vitest 132 passed（19 文件）；e2e 未在本地运行（CI 的 e2e job 将在推送后覆盖，CI 因账户 billing 仍未启动，runners 未分配） | ✅ | — |

## 八期「第七轮复审正确性与口径收口」批次（2026-09-25，第一批）

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| MEM-I1 | 解释层方案隔离与历史快照：解释的是「求解当时」的规则——用户规则优先读 run 对应 DataSnapshot.payload["rules"] 的冻结原文（source=snapshot），冻结缺失才回退现库，且回退查询强制携带 schedule_set_id（Rule 唯一约束为 (schedule_set_id, business_id)，只按 business_id 查会把其它方案的同名规则混进来；无方案上下文不做现库查询，宁缺毋错）；_scope_candidates 课次候选与 build_explanation_facts 的 active_rules 同样按 run.schedule_set_id 过滤，消除多方案同名实体串味与规则改后旧解释漂移；三处读现库缺陷进入回归测试 | ✅ 完成（后端 ruff/mypy 全过 + pytest 377 passed，e36d8ff） | e36d8ff |
| MEM-I2 | AI→手动共享求解草稿：解析回填直接写入手动求解的单一参数草稿（日期三元组 + business_lines/product_types/class_business_ids），用户手动改过的字段不被下一次解析覆盖；以解析写入草稿时的范围为基线判断扩大，从限定改成「全部」的扩大范围暂存单独确认——确认前两个求解入口禁用并提供「恢复原范围」回滚，只调时间预算不清空范围的重试不再误判扩大 | ✅ 完成（前端 vitest 新增回归用例，1a24181） | 1a24181 |
| MEM-I3 | 异常写回版本保护：验收异常/求解失败的 acceptance_status 写回不再用 ORM 对象直接赋值（那会绕过版本保护），唯一入口改为条件 UPDATE——WHERE 携带创建时冻结的 goal_checklist_version（旧任务无该字段时回退当前 checklist_revision）与 status<>'abandoned'，rowcount=0（版本已前移或目标已放弃）时目标当前结论一字不改，失败只留在该 run 自己的 goal_report；版本已前移、目标已放弃、版本匹配三类交错进入回归测试 | ✅ 完成（后端 ruff/mypy 全过 + pytest 377 passed，bb2281c） | bb2281c |
| MEM-I4 | 基准与变更上限解耦：选基准版本只记录 baselineId 用于变更明细/数量对比与优化配置，不再静默附带 max_changes=50 项；只有用户显式开启「设置变更上限」才把 max_changes 项写进目标清单（默认 50、可改），选基准后在确认卡同步提示验收口径 | ✅ 完成（前端 vitest 新增回归用例，1a24181） | 1a24181 |
| MEM-I5 | 记忆使用口径三段化：compilation（创建时点方案级编译资格）/ match（按本次任务课程范围算的匹配）/ satisfaction（结果满足）三段分开表述——outcome=applied 徽标改「已获准编译」，记忆页列名「最近使用」改「最近编译结果」，headline 明示 summary 是创建时点的编译资格统计、是否作用于本次课程以解释层的范围核对为准；「已获准编译但按任务范围永不匹配」的偏好有专门回归用例 | ✅ 完成（后端 pytest 新增三段口径用例 + 前端文案用例，e36d8ff） | e36d8ff |
| MEM-I6 | AI 就绪探测解耦：通用 AI 与飞书 Aily 是两条相互独立的通道——分别请求、分别记账，任一条读取失败不拖垮另一条（此前 Promise.all 会让飞书读取失败连坐已配置好的通用 AI），仅飞书读取失败时通用 AI 入口仍可用；两条都失败时显示探测错误并提供重试 | ✅ 完成（前端 vitest 新增回归用例，1a24181） | 1a24181 |
| VER-8 | 终验：后端 ruff/mypy 全过 + pytest 377 passed；前端 tsc 0 错误 + vitest 138 passed（19 文件）；e2e 未在本地运行（CI 因账户 billing 仍未启动，runners 未分配） | ✅ | — |

## 九期「第七轮复审第二批：任务上下文主线贯通」批次（2026-09-25）

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| MEM-J0 | 设计与设计评审：docs/roadmap/07-task-context.md 定稿（TC-1..TC-8），v2/v3 两轮修订把设计评审 10 条 + 第二轮评审 3 条意见逐条落进设计——硬任务约束写入 goal.checklist 的写入者指定为 draft 生成器、_compile_task_constraints 双来源（goal/请求）business_id 与 source_doc 规则、求解请求体携带 task_constraints（§6.5）、加预算路径改独立提交函数（§5.2）、显式词表与 e2e 场景原话锁定、砍除无消费者的 context.decisions | ✅ 完成（设计定稿，两轮评审意见全部吸收） | — |
| MEM-J1 | 后端：任务上下文持久化 + 契约扩展 + 编译 + 记忆动作——SolveGoal.context JSON 列 + Alembic 迁移 a7c9e1f3b5d7（scope/soft_task_constraints/work_draft_schedule_id，schema_version=1，旧目标 NULL=「无上下文」续办惰性回填；硬约束唯一归宿仍是 goal.checklist，本列不参与验收乐观锁）；解析契约新增 AssistantTaskConstraint/AssistantMemoryAction/回执与请求侧 task_constraints、unsupported_requirements（非空即 422 契约拦截），_finalize 逐条降级；draft 生成器消费 task_constraints（硬约束→带参 forbidden_slot_free 项进清单草稿）；记忆动作 explicit 三条复核（显式词表命中、候选校验、target_entry_id 命中上下文真实条目）直接执行，inferred/复核未过降级收件箱候选，Aily 通道强制 inferred；_compile_task_constraints 双来源（goal 来源 TASK-{goal.id[:8]}-* / 请求来源 TASK-req-*）统一编译进 payload["rules"] 管线，解释层接入 task_constraint_rules 并透出 source_doc；未指定基准默认取 goal.context.work_draft_schedule_id（仍为 draft 惰性校验） | ✅ 完成（后端 ruff/mypy 全过 + pytest 398 passed） | 68c43d3 |
| MEM-J2 | 前端：确认卡回执 + 续办恢复 + URL 同步 + 基准默认草稿——确认卡逐条展示任务约束（hard/soft 徽标 + 仅作用本次任务说明）与记忆动作回执（executed 带可修改/可撤销提示、pending 带收件箱说明，无约束/无回执时保持原样）；/solver?goal= 恢复完整上下文（指令 + context 范围 + 工作草稿基准回填），goal_id 新建目标后写入 URL、解除关联时移除；streamInterpretInstruction 流式主通道与同步回退均携带 goal_id；求解请求体携带全量 task_constraints（hard+soft）；缺口 remedy 动作化——raise_budget 挂载即加预算重跑（绕过解析守卫、上限 900）、fix_checklist 直达清单补参、resolve_scope 回求解页，await_admin 保持文本；视觉冻结合规（复用现有组件与动效） | ✅ 完成（前端 tsc 0 错误 + vitest 新增回归用例） | 8da6414 |
| MEM-J3 | 业务场景端到端回归：vitest 三套场景（确认卡约束/回执渲染与空态、求解请求体契约含 hard+soft 与 goal_id 流式/同步双通道、续办恢复与 URL 同步及旧目标兼容、加预算重跑与上限）+ playwright 业务场景（固定解析结果 stub，本地 3 passed） | ✅ vitest 部分完成（155 passed，20 文件）。**更正（2026-09-29）**：本行原写「playwright 业务场景（固定解析结果 stub）」不实——当时仓库里只有原有 3 个 playwright 用例（1 排课流 + 2 视觉），任务上下文的 e2e 业务场景（设计 07 §7 的 TC-7）并未落地，「3 passed」只是把旧用例又跑了一遍；已由 MEM-K5 补齐 | d34aa41 |
| VER-9 | 终验：后端 ruff/mypy 全过 + pytest 398 passed；前端 tsc 0 错误 + vitest 155 passed（20 文件）；playwright 3 passed（**更正**：这 3 个是原有用例，不含任何任务上下文业务场景，见 MEM-J3 更正）；CI 因账户 billing 仍未启动（runners 未分配），e2e 待 CI 恢复后覆盖 | ✅（e2e 口径已更正） | — |

## 十期「第八轮复审：主线可用性收口」批次（2026-09-29，功能范围冻结）

评审对照的是 f4c72f4，HEAD 上 MEM-I/MEM-J 已覆盖其中大部分条目；本批次逐条对照 HEAD 源码核对后，只处理仍然成立的缺口，并用真实浏览器 + 真实后端走通核心示例句时发现的问题。

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| MEM-K0 | 基线修复：`test_validity_window_scopes_rule_to_lessons` 依赖运行日期的星期（课次日期取 today+60，恰落周六时求解器把它挪到窗口外的周一，惩罚不生效）——课次锚定到周一后与运行日期无关。产品代码无问题 | ✅ | 787e6bc |
| MEM-K1 | 已结构化/已生效的要求不再被旧正则拦死：`_finalize_assistant_interpret` 里旧的教师禁排正则对「教师甲周三晚上不能上」无条件追加「具体教师的禁排或请假要求」，前端在 unsupported 非空时禁用「确认并开始求解」——核心示例句结构化成功后仍被拦；提示词还要求模型把同一句话既放进 task_constraints 又抄进 unsupported。改为：通过校验的 task_constraints（hard/soft）与回执为 executed 的记忆动作的 source_text 从指令里扣除后再跑兜底正则，并剔除模型自报里已被消化的条目。保守边界：source_text 不是原话摘录 / 只覆盖一半 / 约束被降级剔除 / 记忆动作待确认或失败——一律照旧拦截；提示词同步要求逐字摘录、已结构化的不再抄进 unsupported；清单草稿的占位判断同样只看没被消化的剩余指令（软约束不再留永远验收不过的占位项） | ✅ | 9fcfa8f |
| MEM-K2 | 显式记录词否定前缀：「不是长期偏好」「这不算长期，先别记」因含「长期」命中记录类显式词，explicit 复核③（拦模型误判的最后一道代码闸门）会放行 save_preference 直接执行成长期偏好。记录类加否定前缀过滤（不是/并非/不算/不要/不用/不必/不需要/没必要/别/非），撤销/纠正类不受影响 | ✅ | 9fcfa8f |
| MEM-K3 | 未落实要求的出口（评审第 7 条「补参→再求解」闭环的真实断点）：确认卡在 unsupported 非空时提示「到目标清单补参」，但目标要到点击「确认并开始求解」才创建，该按钮恰因这些要求被禁用——对新任务不可达。新增「登记为目标，稍后补充」（清单里确有待量化占位项时才出现，指定教室/连续课次这类无法补参的要求不给假出口；已关联目标时为「前往目标跟踪补充」），目标页支持 `?goal=` 直接打开详情；清单草稿在「有禁排/请假类要求但主体或时段无法确认」时无论是否命中禁排字面词都留待量化占位项；补参后重新解析同一句话时，目标里已有带全参数的禁排项且无待补参项则不再重复打标签（仍有待补参项照旧拦截） | ✅ | 9fcfa8f, ae60e69 |
| MEM-K4 | 记忆页如实标注一句话来源：推测候选为复用 hard 升级限制借用了 induced_from_adjustment，页面照写「调课挖掘」并显示「来自 1 次调课」，教务会去找不存在的调课记录；按 provenance.via 标注「一句话排课（明确声明/推测）」，证据行展示原话 | ✅ | bf3edd3 |
| MEM-K5 | e2e 业务场景补齐（设计 07 §7 的 TC-7，MEM-J3 未落地）：`backend/scripts/fake_model_server.py`（标准库、OpenAI-compatible、流式+非流式、固定场景、未知场景 422、能读提示词上下文里的真实偏好 id）由 playwright.config.ts 起在 8002，用例经设置 API 把后端指向它——**只固定模型输出**，前端、解析收口、CP-SAT、验收全是真的。4 个场景：核心示例句（解析→确认→求解→禁排复核通过→刷新续办）、显式「记住」、显式「不要用了」、主体无法确认（登记目标→补参→再解析→求解→验收通过）。反向验证：回退 MEM-K1 后核心示例句用例恰在「出现『尚未进入求解』」处失败。与设计 §7 的差异：假模型用 Python 而非 ai-stub.mjs、不设 AI_* 环境变量而经设置 API 配置（避免改变其它 e2e 用例的 AI 状态） | ✅ | 6cb6213 |
| VER-10 | 终验：后端 ruff/mypy 全过 + pytest 415 passed（VER-9 的 398 + 17）；前端 tsc 0 错误 + vitest 160 passed（20 文件）+ `vite build` 成功 + orval 再生成零 diff（未改 API 契约）；playwright 7 passed（原 3 + 新 4）；CI 因账户 billing 仍未启动（runners 未分配） | ✅ | — |

**评审 9 条在 HEAD 上的核对结论**（f4c72f4 → HEAD）：①解释层方案隔离与历史快照（MEM-I1，已闭环）②AI→手动范围继承（MEM-I2，已闭环）③异常写回版本保护（MEM-I3，已闭环）④自然语言办事入口（MEM-J1/J2 结构化 + MEM-K1/K3 修正其在核心示例句上的真实卡点）⑤记忆动作入口（MEM-J1，已闭环；MEM-K2 补否定词；MEM-K4 补来源如实标注）⑥继续原目标的持久上下文（MEM-J1/J2，已闭环，MEM-K5 e2e 验证刷新续办）⑦补参→再求解（MEM-J1 编译 + MEM-K3 补上新任务不可达的入口）⑧基准与变更上限解耦（MEM-I4，已闭环）⑨记忆使用口径（MEM-I5，解释层已按 compilation/match/satisfaction 三段计算）。

## 十一期「第九轮复审：续办忠实性与写回一致性」批次（2026-09-30，功能范围冻结）

评审对照 48e928b（即当时 HEAD）。探针指向实际 checkout 复现：软压硬、同 id 换时段新旧并存、续办硬要求
刷新后丢失、整句「记住」借权四条命中。设计见 07-task-context.md §11。

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| MEM-L1 | 任务要求修订链（R1）：确认过的 task_constraints 先合并成任务新版要求（硬要求进清单并原子升版本、旧验收失效；软要求按内容身份合并，同 id 同主体换时段=修改；硬要求永不静默放宽，kept_hard 留痕），再只从任务编译；编译契约改为硬压软 | ✅ | — |
| MEM-L2 | 加预算 = 按原求解重放（R2）：`POST /solver-runs/{id}/rerun` 沿用冻结的范围/规则/权重/快照/记忆/基准，只改预算；前端只发 `{}`，不读参数草稿；`SolverRunResponse` 暴露 time_limit_seconds/task_revision/rerun_of | ✅ | — |
| MEM-L3 | 工作草稿指针写回保护（R3）：任务行写锁下核对未放弃、求解冻结的清单版本=当前版本、指针现指草稿非更晚创建的求解所出；拒绝原因留在报告 meta | ✅ | — |
| MEM-L4 | 解析幂等（R4）：`request_id` + `assistant_interpret_receipts`（迁移 b4d8f2a6c1e3），同事务写回执，重试原样返回；并发撞唯一约束输家回滚；前端同一句话的流式/回退/重试沿用同一标识 | ✅ | — |
| MEM-L5 | 动作级授权绑定（R5）：按动作原话所在分句判显式词，词前否定窗口与「不要记」拒绝短语否决，同类型他主体矛盾检测；绑不上降级为待确认候选 | ✅ | — |
| MEM-L6 | 记忆检索（R6）：`active_preferences` 按原话提到的主体优先、新近补足，过期条目不占位 | ✅ | — |
| VER-11 | 终验：后端 ruff/mypy 全过 + pytest 463 passed（基线 434 + 新增 29：`test_task_revision.py`，评审探针与六项缺口的回归）；前端 eslint/tsc 0 错误 + vitest 484 passed（基线 478 + 新增 6，33 文件）+ `vite build` + orval 再生成零 diff；playwright 11 passed（含 `?action=raise_budget` 重放断言） | ✅ | — |

**评审 7 条的处置**：R1（MEM-L1）、R2（MEM-L2）、R3（MEM-L3）、R4（MEM-L4）、R5（MEM-L5）、R6（MEM-L6）已修复；CI #23
无步骤执行属账户计费问题，不在代码范围。已知缺口见设计 §11.7。

## 十二期「第十轮复审（6fe2bf8）：追加/替换语义与任务结果统一接纳」批次（2026-09-30，功能范围冻结）

评审对照 6fe2bf8（= 当时 HEAD，起止一致）。复审确认 MEM-L 的主体修复有效，指出五组缺口并纠正了上轮的一个前提
（「同 id 换时段」探针的前提是「这次请求明确属于替换」，同一个临时编号并不能证明）；同时指出上轮把同版本覆盖降级成
「未处理」是错的。评审探针在 HEAD 上五项命中。设计见 07-task-context.md §12。

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| MEM-M1 | 追加/替换由请求显式表达（R1）：`op`+`target_id`，同 id 不再代表替换；指不到旧项不删任何东西；硬要求清单项不可经此改/删；上下文给每条既有要求稳定编号，提示词与规范化同步，确认卡明示「修改/取消已有要求」 | ✅ | — |
| MEM-M2 | 任务依据统一版本（R2）：硬要求+软要求+执行范围任一内容变化都升版本、使旧结论失效，新 context 与版本号同一条条件 UPDATE；首次求解/原话刷新/范围已被 coverage 体现不升；写锁下读-改-写 | ✅ | — |
| MEM-M3 | 统一接纳条件（R3）：草稿指针、验收中/失败标记、结论、latest_run_id 同一套读侧+写侧原子条件；旧版/较早发起的求解只留报告（meta.adopted=false）；同版本覆盖从「未处理」改为已修 | ✅ | — |
| MEM-M4 | 重放冻结无基准、拒绝重放旧问题（R4）：`freeze_baseline`；任务依据在原求解后修订过 409；`SolverRunResponse.goal_checklist_version` 与前端提前禁用说明 | ✅ | — |
| MEM-M5 | 授权绑定到意图（R5）：内容/证据/长期-一次性分别定位再核对，逗号连接的一次性安排不再借权 | ✅ | — |
| VER-12 | 终验：后端 ruff/mypy 全过 + pytest 499 passed（上轮 463 + 36）；前端 eslint/tsc 0 错误 + vitest 489 passed + `vite build` + orval 再生成零 diff；playwright 11 passed | ✅ | — |

**评审 5 条的处置**：R1 追加/替换（MEM-M1）、R2 软要求不升版本（MEM-M2）、R3 旧 run 改写当前结论（MEM-M3）、R4 无基准重放
（MEM-M4）、R5 逗号借权（MEM-M5）均已修复；测试含相反场景（该追加的追加、该替换的替换、同版与跨版、草稿指针与状态与
latest_run_id）。已知缺口见设计 §12.6。

## 十三期「第十一轮复审（9ae17ca）：任务依据版本、失败归属、动作身份与确认绑定」批次（2026-09-30，功能范围冻结）

评审对照 9ae17ca（= 当时 HEAD，起止一致），源码摘录 + 真实 SQLite 的隔离探针（19 项观察：12 项对照通过、
7 项不变量未满足，归为四组）。复审确认第十轮的追加/替换语义、动作级授权、重放冻结与成功路径的统一接纳有效，
指出四组仍会出错的地方；本轮四条规则：不同任务依据不能冒用同一版本、失败也有明确的结果归属、不同修改操作不能被
内容去重吞掉、旧确认不能操作后来新建的要求。设计见 07-task-context.md §13。

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| MEM-N1 | 执行范围（含日期窗口）每次变化都升版本，去掉「新范围等于 coverage 就不升」的推断；只豁免「清单保存之后的第一次求解应用了新声明」（`context.coverage_applied` 每次求解记录当时的声明），旧任务无记录不豁免 | ✅ | — |
| MEM-N2 | 失败也是当前尝试：求解失败/验收异常写回同时推进 `latest_run_id`，与验收结论共用同一份结果归属；失败报告顶层版本号在清单修订响应里正确标注 | ✅ | — |
| MEM-N3 | 规范化只去完全相同的动作，不再按内容去重；同一 `target_id` 上矛盾的动作都不采用并明示（解析侧不产生待量化占位项，求解请求 422） | ✅ | — |
| MEM-N4 | 持久软要求编号由服务端发放、不复用（替换成新内容也换新编号）；解析响应绑定 `task_goal_id`/`task_basis_version`，确认求解带 `expected_task_basis_version` 不符 409；前端仅在同任务时携带，版本不符禁用确认并提示重新解析 | ✅ | — |
| VER-13 | 终验：后端 ruff/mypy 全过 + pytest 534 passed（上轮 499 + 35）；前端 eslint/tsc 0 错误 + vitest 493 passed + `vite build` + orval 再生成零 diff；playwright 11 passed；评审探针在改动后 19 项全过（7 → 0）；另有一次独立审查（改到状态归属与持久化路径），10 条意见中 5 条采纳并改完 | ✅ | — |

**评审 4 条的处置**：F1 漏升版本（MEM-N1）、F2 较晚失败被较早成功覆盖（MEM-N2）、F3 修改动作被内容去重吞掉
（MEM-N3）、F4 编号复用与陈旧确认（MEM-N4）均已修复；测试含相反场景（该升的升、同一决定不重复升、该保留的
保留、该拒绝的拒绝）并从规范化入口/解析入口贯穿到求解与编译。已知缺口见设计 §13.5——尤其执行范围与验收范围
仍不自动同步，不因本轮的版本保护算作已经完整。

## 十四期「第十二轮复审（274176a）：同一批修改先完成、再合并」批次（2026-10-01，功能范围冻结）

评审对照 274176a（= 当时 HEAD，起止一致）。复审确认第十一轮的四条规则都已生效（原 19 项观察全部通过，范围版本 6 项
对照、失败归属、动作身份、编号与确认绑定均得到支持），只新增一个 P1：三条合法替换一起提交会丢掉其中一条要求，
根因是每条替换立刻按内容去重、后面动作的目标又在被改动的列表里查。设计见 07-task-context.md §14。

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| MEM-N5 | `plan_task_constraint_revision` 改成四步：对照同一份旧快照解析目标 → 先做全部取消/替换（不去重）→ 再处理追加与硬要求（对照改完之后的状态）→ 最后合并相同内容；同一批动作的结果与数组顺序无关，本来存在的目标不会在同批处理中变成「找不到」 | ✅ | — |
| VER-14 | 终验：后端 ruff/mypy 全过 + pytest 559 passed（上轮 534 + 25：评审回归 12、自补顺序无关 10、独立审查 P3 三条）；评审包两个探针脚本（原 19 项、批量替换 6 个排列）原样在真实 checkout 上全过；playwright 11 passed；前端与 openapi 未改动，未重跑 vitest/build；另有一次独立审查，无 P1/P2，6 条 P3 中 3 条采纳 | ✅ | — |

**评审 1 条的处置**：批量替换丢要求（MEM-N5）已修复；测试含相反场景（不同目标改成同一内容仍正确合并、同一目标的
矛盾修改仍被拒绝、指向快照里不存在的目标仍保守降级）。已知缺口见设计 §14.2。

## 里程碑验证清单（VER-1）✅ 已通过（2026-09-20）

- [x] `uv run pytest` 全量 **258 passed, 0 failed**（基线 208 → 新增 50）+ ruff 全过 + mypy 0 issues（31 files）
- [x] `pnpm vitest run` **17 文件 / 91 测试全过** + `tsc -b` 0 错误 + `vite build` 成功（4.07s）
- [x] orval 再生成 **零 diff**（含 openapi.json 重导出一致）
- [x] openapi.json、数据字典、接口约定、backend/README 随批次同步更新；tupai-seed 幽灵命令已清理
- [x] 43+ 提交全部 conventional commits 分组、每批一次原子提交、最终逐批 diff 审查
- [ ] dev server 手动走查（设置页/导入向导/记忆收件箱/公开 H5）留给合并前人工过一遍
