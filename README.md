# 途排智策

基于 AI 自然语言理解、飞书多维表格/日历/妙搭与 OR-Tools CP-SAT 的智能排课 MVP。工程采用前后端分离结构，正式报名材料和外部资料统一放在 `docs/对接资料`。

## 快速启动

后端：

```powershell
Set-Location -LiteralPath 'E:\飞书大赛\tupai-zhice\backend'
uv sync --group dev
uv run alembic upgrade head
uv run tupai-api
```

Swagger：`http://127.0.0.1:8000/docs`

新数据库启动时会自动创建默认管理员：`admin` / `tupai-demo-admin-2026!`。首次登录后建议在账号管理中修改密码；部署环境也可通过 `.env` 的 `BOOTSTRAP_ADMIN_USERNAME`、`BOOTSTRAP_ADMIN_PASSWORD` 覆盖。JWT 密钥仍应在部署时单独配置。

前端：

```powershell
Set-Location -LiteralPath 'E:\飞书大赛\tupai-zhice\frontend'
pnpm install
pnpm dev
```

Web 管理端：`http://127.0.0.1:5173`

## 业务闭环

1. 按官方模板导入 XLSX：校验表头、报出被跳过的行、按课次身份去重，并给出语义重复与同班同时段冲突的数据质量报告。
2. 已配置的 OpenAI-compatible 模型（可接豆包 Ark、DeepSeek 或企业模型网关）将自然语言解析为业务范围、日期窗口和候选规则，后端验证实体、状态和权限；Aily Workflow 保留为可选通道。
3. 教务确认规则后提交 CP-SAT 求解。
4. 后端保存数据快照、求解状态、课表版本、指标和审计日志；硬冲突独立于求解器实算，存在冲突时不允许发布。
5. 教师请假或教室停用触发最小变更调课。
6. 审批后发布，并可回滚到历史课表。

## 目录

- `backend`：FastAPI、SQLAlchemy、Alembic、CP-SAT、飞书适配与测试。
- `frontend`：React 管理端。
- `docs/对接资料`：正式材料、官方资料、接口和数据字典。
- `data/imports`：正式脱敏样本。
- `data/exports`：运行时导出目录，不进入版本控制。
