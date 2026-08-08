# Aily Skill 对接

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

## Aily 配置

1. 后端部署到 Aily 可访问的 HTTPS 地址。
2. 在 Aily 中导入或参考后端 OpenAPI，将两个 `/aily` 操作配置为技能。
3. 为请求固定添加 `X-Aily-Key`，值与后端 `.env` 一致。
4. 将规则输出约束为 JSON，实体 ID 必须先通过主数据查询获得。
5. 使用 20 条基准规则回放，检查字段准确率和人工修改率。

Aily Token 用于平台内模型调用，不作为多维表格服务端 API 的身份凭据。

