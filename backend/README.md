# 途排智策后端

## 命令

```powershell
uv sync --group dev
uv run alembic upgrade head
uv run tupai-seed
uv run tupai-api
```

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

## API 约定

- API 前缀：`/api/v1`
- 用户鉴权：OAuth2 password flow + JWT Bearer
- Aily 鉴权：`X-Aily-Key`
- 求解进度：`GET /api/v1/solver-runs/{id}/events`
- OpenAPI：`openapi.json`
