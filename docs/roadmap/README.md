# 途排智策 · 开源化升级路线（Roadmap）

> 状态：调研进行中 · 分支 `feat/open-source-upgrade`
> 本目录记录项目从「飞书大赛参赛作品」走向「可开源的通用排课系统」的调研结论与设计决策。

## 背景与硬约束

途排智策是 2026 飞书 AI 先锋大赛高途命题（AI 智能排课）的参赛作品：FastAPI + OR-Tools CP-SAT 后端、React 管理端、飞书多维表格/妙搭集成。比赛阶段 many features 以「可演示」为先，部分功能尚未真正接入后端。

开源化升级的**硬约束**：

1. **前端视觉风格与动画完全不变**——所有新页面/新交互必须复用现有设计 token、组件与动效语言。
2. **飞书集成弱化而非删除**——飞书降级为「集成之一」，收纳进设置页；为接入钉钉、企业微信、Google Workspace 及纯本地模式预留同等位置。
3. **业务闭环不被破坏**——导入 → 规则 → 求解 → 调课 → 审批发布 → 回滚的主链路保持可用。

## 四个核心问题

| # | 问题 | 设计文档 |
| --- | --- | --- |
| 1 | **数据导入怎么搞？** 现状是「按高途示范 XLSX 模板手动填表」，生产环境不可能让人一格一格填表。需要：模板保底 + 智能映射任意表格 + 平台数据源连接器的分层方案。 | [01-data-import.md](01-data-import.md) |
| 2 | **怎么让排课系统「有记忆、自进化、有温度」？** 排课前的规则 + 排课后的调课，目前是冷冰冰的静态规则。参考 Anthropic 的 agent 工程方法论与业界 agent 记忆方案（mem0 / Letta / Zep 等），把老师、教室、学生/班级的需求沉淀为「会成长的记忆」，把调课行为当作反馈信号驱动系统进化。 | [02-agent-memory.md](02-agent-memory.md) |
| 3 | **集成层怎么抽象？** 飞书深度耦合不可持续。抽象出「表格存储 / 日历 / 通知 / 审批」等能力接口，飞书作为第一个适配器，配套写《如何接入 XX 平台》文档。 | [03-integrations.md](03-integrations.md) |
| 4 | **脱离飞书后，面向老师/学生/家长的课表怎么展示？** 之前的公开展示靠飞书妙搭公开应用；开源后需要内置展示层：班级/教师公开课表（免登录链接）、日历订阅（ICS）、调课通知，并复用已建好的角色权限体系（admin/scheduler/approver/viewer）。另有求解页交互断点与全局 SOP/侧边栏 IA 重排（用户反馈 P1-P4）。 | [05-ux-sop.md](05-ux-sop.md) · [06-public-showcase.md](06-public-showcase.md) |

## 文档索引

- [01-data-import.md](01-data-import.md) — 数据导入体系设计（分层导入管线）
- [02-agent-memory.md](02-agent-memory.md) — 记忆与自进化架构设计
- [03-integrations.md](03-integrations.md) — 集成抽象层设计与平台接入指南
- [04-implementation-plan.md](04-implementation-plan.md) — 分阶段实施计划与进度
- [05-ux-sop.md](05-ux-sop.md) — 求解页交互闭环与全局 SOP 重排
- [06-public-showcase.md](06-public-showcase.md) — 公开展示层设计（免登录课表门户）
- [07-task-context.md](07-task-context.md) — 任务上下文主线贯通设计（第七轮复审第二批）

## 决策记录（ADR 摘要）

- **ADR-1 导入体系**：三层管线（L1 模板保底 / L2 智能映射任意 Excel / L3 平台连接器）；L2 为开源核心卖点，不引第三方 importer UI 库（视觉冻结约束），LLM 语义层复用现有 OpenAI-compatible 通道且可关闭。详见 [01](01-data-import.md)。
- **ADR-2 记忆与自进化**：整体保持 workflow（CP-SAT 是完美 Verifier），LLM 只做解析/归纳/解释；自研 PreferenceEntry 关系表（双时间轴、试用期状态、归纳条目永不自动升硬约束），不引向量库/图库/厂商托管记忆。详见 [02](02-agent-memory.md)。
- **ADR-3 集成抽象**：Protocol 能力接口（table_store/calendar/notifier/approval/nl）+ manifest 动态表单 + LocalAdapter 默认可用；feishu.py 生产逻辑 strangler 收敛不重写。详见 [03](03-integrations.md)。
- **ADR-4 License（已定，2026-09-23）**：Apache-2.0。LICENSE 文件（官方原文）+ backend/pyproject  字段 + frontend/package.json  字段 + README License 节已全部落地。

## 遗留事项（本次升级范围外，记录待办）

- `GET /solver-runs/{id}/events` SSE 端点后端已实现，前端仍用 700ms 轮询——可切换以省请求量；
- `GET /schedules/{id}/calendar-bindings` 前端无入口，可在版本详情补「日历下发记录」查询；
- 14 列模板缺教室容量/设备/班级人数字段，容量类约束与座位利用率指标未建模（比赛总册附录已声明），开源后按社区需求排期。
