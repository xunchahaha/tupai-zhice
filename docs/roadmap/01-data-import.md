# 01 · 数据导入体系设计：从「一张模板表」到分层导入管线

> 状态：调研完成（业界调研 + 代码勘察已合入）
> 关联：[README](README.md) · [02-agent-memory.md](02-agent-memory.md) · [03-integrations.md](03-integrations.md)

## 1. 现状盘点（代码勘察事实）

- 导入入口仅一个：`POST /api/v1/imports/xlsx` → `backend/app/services/converter_zhengzhou.py`（987 行）。
- 格式：`data/imports/sample.xlsx`「课表数据源」工作表，**固定 14 列表头严格匹配**（标准业务线/标准产品班型/集训营班级标签/教室标签/课表编排来源/编排阶段/计划课次/计划课时/课次序号/课节名称/上课日期/上课时段/课节时长(小时)/授课教师），表头不一致直接 4xx。
- 已有的好底子：按业务键（班级+课次序号+课节名称+上课日期+上课时段）**重复导入即更新**；导入产出数据质量报告（跳过行/语义重复/同班同时段冲突/占位教室）。
- 真实数据背景：郑州官方表 56,344 行 → 9,169 课程场次；14 列中**没有**教室容量/设备/班级人数；源表本质是「计划候选清单」而非已排课表。
- 痛点即命题：生产环境不可能要求每个校区的人手动按我们的 14 列模板一格一格填 Excel。

## 2. 业界调研结论（摘要）

成熟产品（Flatfile / OneSchema / CSVBox / TableFlow / Frappe Data Import / Airtable）的导入向导收敛为同一条五步流水线：

**Upload 上传解析 → Match 列映射 → Validate 行级校验 → Fix 原地修复 → Review & Commit 确认提交**

关键实证：

1. **四层列映射**是事实标准：Exact → Normalized（去空格/标点/全半角）→ Fuzzy（编辑距离）→ Semantic（LLM 语义）。分数驱动 UI：高置信自动映射、中置信预填待确认、低置信留空；**绝不在低置信度自动应用**——「自信的错误映射」比「没映射」更危险。
2. **样本数据形状校验**（前 20 行采样判断列内容类型）是最被低估的技术，能纠正「表头像但数据不像」的错配。
3. **按业务唯一键 upsert** 是生产刚需（教师用工号、教室用编号），只 append 会导致重复上传即全乱。
4. **关系字段是公认难点**：主数据导入顺序必须固定（先教师/教室/班级 → 后课程 → 最后课表），或按名称/编码解析成外键。
5. **LLM 列映射不需要贵模型**（GPT-3.5 级即可达 GPT-4 级映射质量，Buss et al. 2025）；教育域 LLM 的最大增值不是列映射而是**复合字段拆解**（「周一第1-2节」→ day=1, periods=[1,2]、多级表头、合并单元格）。
6. **教务系统普遍无开放 API**（正方/强智/青果等的数据出口就是导出 Excel）——「任意导出文件一键导入」比纯连接器路线更贴中国校园的真实入口。
7. 连接器抽象的共性：manifest（元数据/凭证/能力）+ discover（schema 发现）+ read（分页拉取）+ 增量（cursor/webhook）。

## 3. 设计：三层导入管线

```
L1 模板上传（保底，已有，补强）
L2 智能映射任意 Excel（核心新增，「任意教务 Excel 一键导入」）
L3 平台连接器（飞书/钉钉/企微多维表格 → 见 03-integrations.md，复用集成层）
```

### L1 · 模板上传补强（低成本高确定性）

- **把 14 列定义写进文档与模板**（目前 14 列名单只存在于代码 `TEMPLATE_HEADERS`，文档从未逐列列出）。
- 模板升级：字段说明 sheet + 示例行 + 表头批注；生成器与校验器共用同一份 schema 定义（单一事实源）。
- 错误报告支持导出错误行 XLSX（改完重传），而不只是页面上看。

### L2 · 智能映射导入向导（本次大升级的主战场)

后端新增三个端点（均为真实落库，不引入 mock）：

```
POST /api/v1/imports/preview     上传任意 XLSX/CSV → 返回 sheet 概览、表头行候选、
                                 四层映射建议（含置信度与样本形状校验）+ 映射后的行级校验报告
POST /api/v1/imports/preview     （带修正后的 mapping 重跑校验，Fix 循环）
POST /api/v1/imports/commit      按确认的 mapping + upsert 语义正式导入
```

- **映射服务** `services/import_mapping.py`：别名表（零成本）+ 规范化 + 编辑距离 + 样本形状；LLM 语义层**复用现有 `services/ai.py` 的 OpenAI-compatible 通道**（可关闭，不配置 AI 则只走前三层），允许「不映射」弃权输出，结果按文件指纹缓存。
- **目标 schema 复用五类主数据 + 课次的现有校验逻辑**（Pydantic/ORM），不另造一套。
- 主数据依赖顺序内建：一次导入多个 sheet 时按 教师/教室/校区/时段 → 班级 → 课次 拓扑序处理。
- **historical mapping**：记住用户上次的映射决策（按表头指纹），下次自动预填——参考 OneSchema。
- 前端向导复用现有组件体系（`PageHeader` + `data-table` + `Dialog` + `animate-fade-in` 等），**不引入第三方 importer UI 库**（react-spreadsheet-import 等自带视觉体系，与「风格不变」约束冲突，已评估否决）。五步：上传 → 确认映射（高亮低置信）→ 校验报告 → 原地修复（show only errors）→ 确认提交。
- 语义：必须支持 insert / upsert(by 业务键) 两种模式。

### L3 · 平台连接器（依赖集成层，见 03 文档）

- 首批：飞书多维表格（已有 sync 链路可复用）；预留钉钉智能表格、企业微信智能表格位置。
- 教务系统 API 缺位由 L2 兜底；爬虫型连接器不进核心（合规/维护成本），留社区插件扩展点。

## 4. 实施切分

| 批次 | 内容 | 风险 | 验证 |
| --- | --- | --- | --- |
| IMP-1 | L1 补强：模板 schema 单一事实源、文档列清单、错误行导出 | 低 | 定向 pytest（converter） |
| IMP-2 | 后端映射服务 + preview/commit 端点 + openapi 再生成 | 中 | 新增 pytest（映射/校验/upsert）+ 既有 208 测试不回归 |
| IMP-3 | 前端导入向导（复用现有组件与动效） | 中 | vitest 页面测试 + 手动走查 |
| IMP-4 | historical mapping + LLM 语义层（可选开关） | 中 | test_ai 模式新增用例 |

## Sources

- Flatfile / OneSchema / CSVBox（AI column mapping 四层匹配拆解）：csvbox.io/resources/ai-column-mapping
- Frappe Data Import（模板范式与高频坑）：docs.frappe.io、discuss.frappe.io
- Airtable / Notion merge 导入（upsert 语义）：support.airtable.com、notion.com/help/import-data
- 开源 importer 对比：oneschema.co/blog/open-source-csv-importers（TableFlow / ImportCSV / YoBulk / react-spreadsheet-import）
- Buss et al. 2025《Towards Scalable Schema Mapping using LLMs》
- Airbyte CDK / n8n node / NocoDB external data（连接器抽象参考）：docs.airbyte.com、docs.n8n.io、nocodb.com
- UniTime XML 接口与 externalId 机制：unitime.org/uct_interfaces.php；FET 格式：fetviewer.com
- 飞书多维表格 batch_create（≤1000/批）：open.feishu.cn；钉钉/企微智能表格 API：open.dingtalk.com、developer.work.weixin.qq.com
