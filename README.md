# 途排智策

基于飞书 AI 与 OR-Tools CP-SAT 的智能排课 MVP。工程采用前后端分离结构，正式报名材料和外部资料统一放在 `docs/对接资料`。

## 快速启动

后端：

```powershell
Set-Location -LiteralPath 'E:\飞书大赛\tupai-zhice\backend'
uv sync --group dev
uv run alembic upgrade head
uv run tupai-api
```

Swagger：`http://127.0.0.1:8000/docs`

默认本地账号：`admin` / `tupai-demo`。部署前必须通过 `.env` 更换管理员密码、JWT 密钥和 Aily 集成密钥。

前端：

```powershell
Set-Location -LiteralPath 'E:\飞书大赛\tupai-zhice\frontend'
pnpm install
pnpm dev
```

Web 管理端：`http://127.0.0.1:5173`

## 业务闭环

1. 从正式 XLSX 导入教师、班级、教室、时段和课程场次。
2. Aily 将自然语言拆为候选规则，后端验证实体、状态和权限。
3. 教务确认规则后提交 CP-SAT 求解。
4. 后端保存数据快照、求解状态、课表版本、指标和审计日志。
5. 教师请假或教室停用触发最小变更调课。
6. 审批后发布，并可回滚到历史课表。

## 目录

- `backend`：FastAPI、SQLAlchemy、Alembic、CP-SAT、飞书适配与测试。
- `frontend`：React 管理端。
- `docs/对接资料`：正式材料、官方资料、接口和数据字典。
- `data/imports`：正式脱敏样本。
- `data/exports`：运行时导出目录，不进入版本控制。

