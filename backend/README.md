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

将 `.env.example` 复制为 `.env` 后填写本机配置。默认 SQLite 文件位于项目 `data` 目录。真实飞书模式需要应用 ID、应用密钥、多维表格 app token 和资源到 table ID 的映射。

## API 约定

- API 前缀：`/api/v1`
- 用户鉴权：OAuth2 password flow + JWT Bearer
- Aily 鉴权：`X-Aily-Key`
- 求解进度：`GET /api/v1/solver-runs/{id}/events`
- OpenAPI：`openapi.json`

