# 一句话排课 AI 与 Aily 可选对接

## 标识结论

用户提供的《OpenAPI 接入与接口说明》采用 Session → Message → Run 调用链。普通飞书自建应用
`cli_...` 用于取得 user/tenant access token；创建 Run 时的 `app_id` 仍是 Aily 应用标识
`spring_...__c`。`skill_id` 可以省略，让 Aily 自行选择技能，但 Aily 应用标识仍然存在。

因此普通飞书应用 ID 不能直接替代 Aily App ID。本项目将 Aily 调整为可选高级通道，默认使用
可配置的 OpenAI-compatible 模型完成自然语言理解。

## 当前主链路

1. 教务在“排课求解”输入一句话。
2. `POST /api/v1/assistant/interpret` 将当前合法业务线、班型、班级和固定业务规则发给已配置模型。
3. 模型只返回严格 JSON：业务范围、日期范围、日期调整窗口和识别出的规则。
4. 后端校验模型有没有引用未知实体，并强制保留固定时段、教室冲突和日程账号冲突等基础规则。
5. 教务确认结构化结果后，CP-SAT 执行确定性求解。

AI 配置接口：

- `GET /api/v1/integrations/ai/configuration`
- `POST /api/v1/integrations/ai/configuration`
- 配置项：`provider`、`base_url`、`api_key`、`model`
- API Key 只以密文保存，读取接口只返回是否已经配置。

## 后端入口

- 候选规则：`POST /api/v1/aily/rule-proposals`
- 触发求解：`POST /api/v1/aily/solve`
- 鉴权请求头：`X-Aily-Key`
- OpenAPI 文件：`backend/openapi.json`

## 候选规则流程

1. Aily 检索制度和历史规则。
2. 将一句话拆为一条或多条候选规则。
3. 每条规则提供实体类型、实体业务 ID、约束类型、范围、硬软建议、权重、来源和置信度。
4. 后端拒绝不存在的实体和不合法字段，并强制状态为 `awaiting_confirmation`。
5. 教务在 Web 管理端修改并确认，确认后状态转为 `active`。

## Aily 高级配置（可选）

1. 后端部署到 Aily 可访问的 HTTPS 地址。
2. 在 Aily 中导入或参考后端 OpenAPI，将两个 `/aily` 操作配置为技能。
3. 为请求固定添加 `X-Aily-Key`，值与后端 `.env` 一致。
4. 将规则输出约束为 JSON，实体 ID 必须先通过主数据查询获得。
5. 使用 20 条基准规则回放，检查字段准确率和人工修改率。

Aily Token 用于平台内模型调用，不作为多维表格服务端 API 的身份凭据。未填写 Aily 两项标识时，
飞书 OAuth、多维表格、日历、妙搭数据链路和通用 AI 一句话排课均照常运行。
