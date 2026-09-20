# 05 · 求解页交互闭环与全局 SOP 重排

> 状态：定稿（10 智能体调研工作流 + 三方案评审裁决综合）
> 关联：[README](README.md) · [04-implementation-plan.md](04-implementation-plan.md)
> 用户痛点：P1 参数与规则脱节 · P2 手动参数应渐进披露 · P3 解析加载态与思考过程展示 · P4 侧边栏/SOP 信息架构

## 1. 根因（行号级勘察结论）

| 痛点 | 根因 | 位置 |
| --- | --- | --- |
| P1 | `defaultParams.date_window_days: 7` 硬编码；AI 解析出的 `interpretation.date_window_days` 只在确认卡展示，**从未回写**参数面板；两条路径零联动 | solver-page.tsx L62-71/L330；ai.py L453-465（prompt 固定「示例 7」） |
| P1（后端） | 无系统级默认参数：schemas 三处 `default=7`；后端语义=每课次相对原日期对称浮动天数（solver.py L763-768 与请求级窗口取交集）——**管道本身是通的**，15 种约束里 `date_range/date_window/allowed_date_range` 的 scope 已支持 `date_window_days: 3` | schemas.py L521/L1102/L1131；solver.py L570/L763-788 |
| P2 | `SolverParams` 无条件渲染（与 AI 解析零关联的两套入口） | solver-page.tsx L181-191 |
| P3 | interpret 完全同步阻塞（httpx 非流式跑在请求线程，60s 超时）；`reasoning_content` 仅在 content 为空时兜底、`<think>` 块被正则直接删除；前端加载反馈只有按钮换字「AI 正在理解指令」 | ai.py L235/L259/L322-324；api.py L5208；solver-page L180 |
| P4 | 侧边栏零图标零徽标零位置感知；流程语义与管理域语义分组混排；8 处断头路（master-data→rules、rules→solver、diagnostics→rules、schedule→reschedule、reschedule→versions、versions→schedule 等）；overview 快捷入口缺主数据/诊断/调课且对只读成员渲染会落空 | app-shell.tsx L18-29；各页面 CTA 普查 |

## 2. 定稿决策（三方案评审裁决：方案 1 为主干 + 方案 2 骨架 + 方案 3 内核）

### PR1 · 求解页交互闭环（UX-A 批次）

| # | 决策 | 级别 |
| --- | --- | --- |
| D1 | **后端 thinking 透出（档 A，同步）**：ai.py 保留 `reasoning_content` + 被剥离的 `<think>` 文本，沿 interpret 链路返回；`AssistantInterpretResponse` 增加 `thinking: str \| None` 字段。`_parse_json_object` 签名变更需排查 explanation 链路全部调用点 | 【需后端】 |
| D2 | **InterpretPhase 状态机**（idle/thinking/parsed/failed）替代散落布尔量，`interpreting` 派生化 | 【纯前端】 |
| D3 | **STAGES 键值阶段常量**（connect/read/match/validate）：非流式下按 2.5-3s 顺序切换阶段文案 + 已用时计时器；键值与二期 SSE 事件一一对应，升级流式只换数据源 | 【纯前端】 |
| D4 | **思考展示**：解析中=展开态（左侧竖线 border-l + 浅色斜体 + animate-pulse 占位），完成后折叠为「已解析完成（用时 N 秒）」可回看；正文 `max-h-48 overflow-y-auto` 防长思考溢出；**否决 shimmer/新 keyframes**（用现有 animate-pulse） | 【纯前端】 |
| D5 | **渐进披露**：解析成功前不渲染手动参数面板；成功后 fadeIn 进入；**RunPanel（实时状态/求解记录）必须常驻**——回访用户需要历史任务锚点（否决 grid 整体门控），门控仅作用于左栏 SolverParams | 【纯前端】 |
| D6 | **P1 回填**：解析成功后预填参数面板的日期三元组（date_from/date_to/date_window_days），值≠默认时显示来源徽标「来自 AI 解析」；用户可覆盖（预填一次，不做双向绑定）；**明确拒绝** business_lines/class_business_ids 等数组字段强映射（类型漂移风险） | 【纯前端】 |
| D7 | **AbortController + 取消按钮**；>30s 提示可重试；Aily 引擎时 thinking 为空则只显示阶段进度 | 【纯前端】 |
| D8 | 验收标准：「输入 3 天以内 → 参数面板显示 3 且带来源提示」「未配置 AI 时手动路径自动升为主入口」「思考区完成后可展开回看且不溢出」 | — |

### PR2 · 侧边栏与全局 SOP（UX-B 批次）

| # | 决策 | 级别 |
| --- | --- | --- |
| D9 | **导航数组重排**（app-shell L18-29 整体替换）：工作台（总览/主数据）→ 排课流程（规则工作台/排课求解/课表视图/无解诊断）→ 变更（局部调课/版本与回滚）→ 设置（/settings）；「集成」组并入设置语义 | 【纯前端】 |
| D10 | **`lib/sop.ts` 的 `SOP_STEPS` 单一事实源** + `SopSteps` 步骤条组件（纯 CSS，现有 token），接入 5 个流程页 PageHeader（PageHeader 已支持 children）：主数据→规则→求解→（诊断）→调课→版本发布，当前位置高亮 | 【纯前端】 |
| D11 | **`SetupChecklist` 就绪度清单**：overview 完整版 + solver 页紧凑版（主数据是否就绪/规则是否确认/AI 是否配置——数据驱动现有列表接口） | 【纯前端】 |
| D12 | **7 条 CTA 矩阵**补断头路：master-data→「去配规则」、rules→「去求解」、diagnostics→深链冲突规则、schedule→「去调课」、reschedule→「查看版本」、versions→「查看新课表」、overview 快捷入口对只读成员过滤 + 缺失三卡（主数据/诊断/调课） | 【纯前端】 |
| D13 | 账户菜单：backdrop 点击关闭 + Escape 关闭（不改视觉） | 【纯前端】 |

### PR3 · 二期（本次不做，记录）

- SSE 流式 interpret（`POST /assistant/interpret/stream`：`reasoning_content` delta → `event: thinking`，结束发 `event: result`；EventSource 不支持 POST，须 fetch reader；注意网关差异优雅降级、Aily 无流式退化为单条 result、反代需 `X-Accel-Buffering: no`）
- `ScheduleSet.solver_defaults` JSON 列 + create_solver_run 合流点（请求显式值 > defaults > 7）——系统级默认参数持久化
- 侧边栏全面图标化（需用户确认视觉解冻）

## 3. 文件改动地图

- **PR1**：backend/app/services/ai.py（thinking 保留）、schemas.py（+thinking 字段）、api.py（interpret 透出）；frontend/src/pages/solver-page.tsx（状态机/阶段/思考区/门控/回填）、frontend/src/api/generated（orval 重跑）
- **PR2**：frontend/src/app/app-shell.tsx、frontend/src/lib/sop.ts（新）、frontend/src/components/sop-steps.tsx（新）、frontend/src/components/setup-checklist.tsx（新）、5 个流程页 PageHeader 接入、overview-page（清单+快捷入口调整）

## 4. 风格冻结合规

零新依赖；零新 keyframes（复用 animate-fade-in/animate-pulse/pulseGlow + Tailwind transition）；思考区用 border-l + text-zinc-500 italic 弱化层级（Claude/DeepSeek 业界同款范式）；徽标/步骤条用现有 Badge/Button + zinc/blue 色阶。

## 5. Sources（精选）

- NN/g Progressive Disclosure 与 Response Time Limits（1s/10s 阈值）
- AI Elements Reasoning 组件（isStreaming 自动开合 + duration 模式）；assistant-ui ChainOfThought
- AI UX Playground Status Steps（ChatGPT「Thought for Ns」折叠范式与反模式）
- Fluent 2 Wait UX；Pencil & Paper Loading Patterns；ACM 2025 生成式 AI 感知等待研究
- 完整清单见工作流日志（5 路勘察与 3 方案原文）
