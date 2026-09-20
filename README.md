# 途排智策（Tupai Zhice）

**An open-source smart timetabling system: an LLM does the understanding, CP-SAT does the solving.**

途排智策是一个面向中小学与培训机构的智能排课系统：用自然语言描述排课需求，由 AI 解析为结构化规则，再由 OR-Tools CP-SAT 在硬约束下确定性求解。LLM 只负责理解、归纳与解释，课表的正确性始终由求解器与代码复核保证——未配置任何 AI 模型或外部平台时，核心排课链路完整可用。

## 核心特性

- **自然语言规则助手**：用自己的话描述排课需求，模型解析为业务范围、日期窗口与候选规则，教务确认后才生效；解析过程的 thinking 全程透明可回看，未配置模型时可走手动表单完整排课。
- **智能导入**：官方 14 列模板直通，或上传任意教务系统导出的 XLSX/CSV——四层列映射（精确/别名 → 归一化 → 模糊 → 样本形状校验，可选 LLM 语义提名）、行级校验报告、insert/upsert 两种模式；低置信度一律不猜，交给用户确认。
- **CP-SAT 求解与最小变更调课**：教室/教师不重叠等硬约束系统级强制，日期窗口、时段偏好等软约束加权优化；教师请假、教室停用触发最小变更调课；硬冲突独立于求解器复核，存在冲突不允许发布。
- **偏好记忆与挖掘闭环**：老师/教室/班级/课程的需求沉淀为带试用期与有效期的偏好条目；调课可一键归因，归因后能挖掘候选偏好，教务确认后下次求解生效（只进软约束，不会把课表变无解）；无 LLM 时退化为确定性统计，照样可用。
- **公开课表门户**：面向老师/学生/家长的免登录课表链接（班级/教师范围）+ ICS 日历订阅 + 二维码/链接分发；公开 payload 显式白名单，不外泄工号与学生信息；链接可轮换/停用/过期，统一 404 防探测。
- **可插拔集成层**：统一能力接口（表格存储/日历/通知/审批/自然语言）+ manifest 清单 + 注册表；本地模式默认可用（零平台依赖），飞书（Lark）适配器已支持多维表格同步与日历下发，钉钉、企业微信、Google Workspace 规划中。
- **审批发布回滚全链路**：求解版本逐课次 diff → 审批发布 → 一键回滚 → 审计日志；发布/回滚是本地事务，外部同步为最佳努力，不阻塞也不撤销本地状态。
- **角色权限矩阵**：admin / scheduler / approver / viewer 四角色 + 课表方案成员授权，接口层统一校验可见范围。

## 快速启动

后端（[uv](https://docs.astral.sh/uv/)）：

```bash
cd backend
uv sync --group dev
uv run alembic upgrade head
uv run tupai-api
```

Swagger：<http://127.0.0.1:8000/docs>

前端（[pnpm](https://pnpm.io)）：

```bash
cd frontend
pnpm install
pnpm dev
```

Web 管理端：<http://127.0.0.1:5173>

数据库迁移说明：本地开发需先执行 `alembic upgrade head` 建表；容器部署时入口脚本（`backend/docker-entrypoint.sh`）会自动执行同一命令。应用启动时会校验迁移版本，数据库落后或超前都会拒绝启动并提示升级命令，不会静默补表。

默认账号：全新数据库启动时自动创建管理员 `admin` / `tupai-demo-admin-2026!`，首次登录后请在账号管理中修改密码；部署环境可通过 `.env` 的 `BOOTSTRAP_ADMIN_USERNAME`、`BOOTSTRAP_ADMIN_PASSWORD` 覆盖，JWT 密钥应在部署时单独配置。

## 配置

将 `backend/.env.example` 复制为 `.env` 后按需填写。两类外部依赖都是**可选项**：

- **AI 模型**：在前端「设置 → AI 模型」中填写 OpenAI-compatible 的 Base URL、API Key 与模型名称（可接豆包 Ark、DeepSeek 或企业模型网关）；不配置时导入语义层自动跳过，规则助手回落手动表单。
- **外部集成**：不接任何平台即为本地模式，全功能可用；如需多维表格外发与日历下发，在「设置 → 外部集成」中按向导接入飞书。详见 [docs/integrations/](docs/integrations/README.md)。

后端也可用 Docker 部署（`backend/Dockerfile`，入口自动执行迁移）。

## 架构与文档

- [ARCHITECTURE.md](ARCHITECTURE.md) — 系统概览、分层架构、端到端数据流、关键设计决策、目录导览与质量约定
- [backend/README.md](backend/README.md) — 后端命令、导入口径、集成与记忆层细节、API 约定
- [docs/integrations/](docs/integrations/README.md) — 平台接入指南（feishu / local / dingtalk）与能力模型
- [docs/roadmap/](docs/roadmap/README.md) — 开源化升级的设计调研、ADR 与实施进度

## 项目状态

**已实现**（主干能力，均有测试覆盖）：

1. 分层导入管线——模板直通 + 任意表格智能映射（preview/commit、insert/upsert、数据质量报告）
2. 自然语言规则助手——解析 → 确认 → 约束目录，thinking 透出
3. CP-SAT 求解与版本链路——快照、指标、diff、审批发布、回滚、审计、最小变更调课
4. 偏好记忆层——偏好条目、调课归因、挖掘闭环（AI 提名 + 统计降级）、求解软约束编译
5. 可插拔集成层——能力接口 + 注册表，本地模式默认可用，飞书适配器已支持
6. 公开课表层——免登录 token 链接、ICS 订阅、白名单投影、管理端签发与轮换

**规划中**（设计已定稿或已立案，见 [docs/roadmap/04-implementation-plan.md](docs/roadmap/04-implementation-plan.md) 批次表）：

- SSE 流式解析与求解进度（后端 events 端点已有，前端由轮询切换为流式）
- 循环课次 RRULE 展开（ICS 当前只生成有具体日期的课次）
- 钉钉 / 企业微信 / Google Workspace 适配器（manifest 占位与接入指南已就绪）
- 排课解释引用偏好记忆（如「已避开周三晚（张老师需接孩子 · 3 月确认）」）
- 教务系统直连连接器、导入映射历史记忆等社区共建项

欢迎通过 issue 讨论需求与认领规划项。

## License

License: TBD — 维护者正在 Apache-2.0 与 MIT 之间定夺（分析见 [docs/roadmap/README.md](docs/roadmap/README.md) ADR-4）。在 LICENSE 文件落地前，请勿 fork 后直接商用分发。
