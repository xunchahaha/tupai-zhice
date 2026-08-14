# 途排智策后端

## 命令

```powershell
uv sync --group dev
uv run alembic upgrade head
uv run tupai-seed
uv run tupai-api
```

## 郑州真实课表数据导入

将「郑州考研公职专升本课表数据源」导入为郑州校区（`CAMPUS-ZZ`）主数据、课程场次和
按「编排来源 + 编排阶段」拆分的已发布课表版本：

```powershell
uv run tupai-zhengzhou "E:\飞书大赛\郑州考研公职专升本课表数据源_教室班级标签版.xlsx"
```

- 56,344 行按 (班级, 课次序号, 课节名称, 教师, 教室, 时段) 去重后为 10,564 条课程场次。
- 业务 ID 使用稳定编号（`ZZ-班级-课次-课节-时段-教师-教室`），重复导入不产生新数据。
- 「教室-待校区确认」保留为停用教室；教师为教研组（`数据级别=教研组`）。
- 导入结果包含清洗警告：占位教室行数、计划课时与课次×3 小时不一致的班型。

质量检查：

```powershell
uv run ruff check .
uv run mypy app
uv run pytest
uv run python scripts/export_openapi.py
```

## 配置

将 `.env.example` 复制为 `.env` 后填写数据库、JWT、管理员账号和跨域配置。默认 SQLite
文件位于项目 `data` 目录。

飞书普通接入不修改 `.env`：管理员在前端“飞书集成”中一次填写 `App ID` 和
`App Secret`，后端自动生成本地加密主密钥并加密保存凭据。完成 OAuth 授权后，系统会
自动创建多维表格、7 张中文业务表并保存全部标识，不需要手工填写 `app_token` 或
`table_id`。

集中式容器部署可以使用 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、
`FEISHU_TOKEN_ENCRYPTION_KEY`、`FEISHU_OAUTH_REDIRECT_URI` 和 `FRONTEND_URL`
覆盖前端配置。该模式面向部署平台或 KMS，不属于管理员首次接入步骤。

一句话排课 AI 在前端单独配置 `Base URL`、`API Key` 和模型名称，后端使用
OpenAI-compatible `/chat/completions` 接口解析指令，并对 API Key 加密保存。集中部署也可使用
`AI_BASE_URL`、`AI_API_KEY`、`AI_MODEL` 和 `AI_TOKEN_ENCRYPTION_KEY`。Aily 的
`spring_...__c` 与 `skill_...` 已调整为可选高级接入项。

## API 约定

- API 前缀：`/api/v1`
- 用户鉴权：OAuth2 password flow + JWT Bearer
- Aily 鉴权：`X-Aily-Key`
- AI 配置：`GET/POST /api/v1/integrations/ai/configuration`
- 一句话解析：`POST /api/v1/assistant/interpret`
- 求解进度：`GET /api/v1/solver-runs/{id}/events`
- OpenAPI：`openapi.json`
