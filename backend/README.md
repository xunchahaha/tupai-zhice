# 途排智策后端

## 命令

```powershell
uv sync --group dev
uv run alembic upgrade head
uv run tupai-seed
uv run tupai-api
```

## 课表数据导入

导入按官方模板（`data/imports/sample.xlsx` 的「课表数据源」工作表，14 列）读取，
表头与模板不一致会直接拒绝，不做位置猜测。校区可以指定，默认郑州校区：

```powershell
uv run tupai-zhengzhou "E:\飞书大赛\郑州考研公职专升本课表数据源_教室班级标签版.xlsx"
```

郑州官方数据的实测口径：

- 56,344 个有效行 → 丢弃「教室-待校区确认」19,734 行 → 36,610 行 →
  完全相同行去重 9,340 → 按课次身份收敛 **9,169** 条课程场次。
- 「教室-待校区确认」经业务确认整行丢弃：它标记该课次不占用校区教室，
  全部来自「公职无限学」「专升本全年班」两个非集训营班级（各 100% 无真实教室）。
- 业务标识由「班级 + 课次序号 + 课节名称 + 上课日期 + 上课时段」生成，
  教室/教师/编排来源属于课次属性而非身份。**修改表格再导入是更新而不是新增**，
  源表中已删除的课次会被清理（被求解产出的课表版本引用过的会保留并报出）。
- 导入结果包含数据质量报告：跳过行（带行号与原因）、语义重复课次、
  同一班级同一时段的多节课、孤儿清理数、占位教室丢弃明细、计划课时不一致的班型。
  计划课时按该班型自己的课节时长核对，不假定每课次固定 3 小时。

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
