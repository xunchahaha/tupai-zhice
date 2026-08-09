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

将 `.env.example` 复制为 `.env` 后填写本机配置。默认 SQLite 文件位于项目 `data` 目录。
飞书生产接入只要求部署人员配置应用 ID、应用密钥、令牌加密密钥和 OAuth 回调地址；
管理员随后在前端授权账号，系统会自动创建多维表格、7 张中文业务表并保存全部标识。
不需要手工填写 `app_token` 或 `table_id`。

## API 约定

- API 前缀：`/api/v1`
- 用户鉴权：OAuth2 password flow + JWT Bearer
- Aily 鉴权：`X-Aily-Key`
- 求解进度：`GET /api/v1/solver-runs/{id}/events`
- OpenAPI：`openapi.json`
