# 途排智策（Tupai Zhice）

**An open-source smart timetabling system: an LLM does the understanding, CP-SAT does the solving.**

途排智策是一个面向中小学与培训机构的智能排课系统：用自然语言描述排课需求，由 AI 解析为结构化规则，再由 OR-Tools CP-SAT 在硬约束下确定性求解。LLM 只负责理解、归纳与解释，课表的正确性始终由求解器与代码复核保证——未配置任何 AI 模型或外部平台时，核心排课链路完整可用。

## 核心特性

- **自然语言规则助手**：用自己的话描述排课需求，模型解析为业务范围、日期窗口与候选规则，教务确认后才生效；解析过程的 thinking 全程透明可回看，未配置模型时可走手动表单完整排课。
- **智能导入**：官方 14 列模板直通，或上传任意教务系统导出的 XLSX/CSV——四层列映射（精确/别名 → 归一化 → 模糊 → 样本形状校验，可选 LLM 语义提名）、行级校验报告、insert/upsert 两种模式；低置信度一律不猜，交给用户确认。
- **CP-SAT 求解与最小变更调课**：教室/教师不重叠等硬约束系统级强制，日期窗口、时段偏好等软约束加权优化；教师请假、教室停用触发最小变更调课；硬冲突独立于求解器复核，存在冲突不允许发布。
- **偏好记忆与挖掘闭环**：老师/教室/班级/课程的需求沉淀为带试用期与有效期的偏好条目；调课可一键归因，归因后能挖掘候选偏好，教务确认后下次求解生效（只进软约束，不会把课表变无解）；无 LLM 时退化为确定性统计，照样可用。
- **公开课表门户**：面向老师/学生/家长的免登录课表链接（班级/教师范围）+ ICS 日历订阅 + 二维码/链接分发；公开 payload 显式白名单，不外泄工号与学生信息；链接可轮换/停用/过期，统一 404 防探测。
- **可插拔集成层**：统一能力接口（表格存储/日历/通知/审批/自然语言）+ manifest 清单 + 注册表；本地模式默认可用（零平台依赖），飞书（Lark）适配器完整支持多维表格同步与日历下发，钉钉、企业微信适配器已实现 v1（未经生产凭据实测），Google Workspace 留作社区共建。
- **审批发布回滚全链路**：求解版本逐课次 diff → 审批发布 → 一键回滚 → 审计日志；发布/回滚是本地事务，外部同步为最佳努力，不阻塞也不撤销本地状态。
- **角色权限矩阵**：admin / scheduler / approver / viewer 四角色 + 课表方案成员授权，接口层统一校验可见范围。

## 快速启动

Windows 一键启动（前后端 + 自动打开浏览器）：双击根目录 `start.bat`；macOS/Linux 运行 `bash scripts/start.sh`。下面的手动步骤等价。

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

1. 分层导入管线——模板直通 + 任意表格智能映射（preview/commit、insert/upsert、数据质量报告），表头指纹记忆与单元格原地修复
2. 自然语言规则助手——SSE 流式解析（thinking 实时透出、失败自动回退手动表单）→ 确认 → 约束目录；指令可登记为持久目标，求解后逐项代码验收（目标跟踪闭环）
3. CP-SAT 求解与版本链路——快照、指标、diff、审批发布、回滚、审计、最小变更调课
4. 偏好记忆层——偏好条目三态生命周期（待确认/试用期/已确认）、调课归因、证据校验与矛盾消解的挖掘闭环、求解软约束编译；解释层如实转述偏好使用情况，编译失败不冒充正常
5. 可插拔集成层——能力接口 + 注册表，本地模式默认可用；飞书适配器完整可用，钉钉/企业微信适配器 v1 已实现（凭据 Fernet 加密落库）
6. 公开课表层——免登录 token 链接（班级/教师/学校目录）、ICS 订阅含 RRULE 循环课次展开、白名单投影、按发布版本批量签发与轮换、H5 张榜打印（print CSS）
7. 工程配套——Windows/macOS/Linux 一键启动、Docker 部署、orval 生成 client 单一事实源、pytest + vitest + Playwright 三层测试

**已知边界（Known limits）**（如实声明，不做过度承诺，欢迎 issue 认领）：

- 钉钉/企业微信适配器未经生产凭据实测（单测与 mock 覆盖，需真实租户联调）；飞书为主开发与验证平台
- Google Workspace 适配器未实现（仅 manifest 占位与指南骨架）
- 求解进度目前为前端高频轮询；后端 events SSE 端点已备，切换留待共建
- 公开链接无效 token 无 per-IP 限流（统一 404 防探测已有，防暴力枚举待补；有效 token 不应限流——日历轮询依赖反复拉取）
- 教室大屏 JSON feed 未做（公开层目前覆盖 H5 与 ICS）
- 求解目标清单的后端验收闭环完整，前端暂为只读展示，清单编辑器待社区共建
- 学期末批量过期等清理类自动化未做（偏好与公开链接按有效期自动失效，无批量清理入口）
- 侧边栏进一步图标化/自定义——视觉冻结约束下留待社区讨论
- 教务系统直连连接器未做，导入以文件上传为边界

设计与批次进度见 [docs/roadmap/](docs/roadmap/README.md)。

## License

License: TBD — 维护者正在 Apache-2.0 与 MIT 之间定夺（分析见 [docs/roadmap/README.md](docs/roadmap/README.md) ADR-4）。在 LICENSE 文件落地前，请勿 fork 后直接商用分发。
