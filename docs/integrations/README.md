# 平台接入指南（docs/integrations）

途排智策通过统一的**能力接口**（表格存储 / 日历 / 通知 / 审批 / 自然语言）接入外部平台；**未接入任何平台时，本地模式即全功能可用**，排课求解、审批发布、调课等核心链路零外部依赖。

## 能力模型

后端以 `Capability` 枚举定义五类能力（见 `backend/app/integrations/base.py`）：

| 能力 | 含义 | 典型消费场景 |
| --- | --- | --- |
| 表格存储（table_store） | 主数据与课表外发到平台多维表格/智能表格 | 发布同步、一键同步、公开展示汇总 |
| 日历（calendar） | 教师忙闲查询 + 课表日程创建 | 「日历下发」 |
| 通知（notifier） | 向师生/家长推送消息 | 调课通知、发布提醒 |
| 审批（approval） | 代理提交平台审批并回传状态 | 发布审批流代填 |
| 自然语言（nl） | 自然语言理解 | 一句话排课（默认走平台无关的 OpenAI-compatible 模型通道） |

适配器按真实支持声明能力，`capabilities()` 做**运行时协商**：未配置就绪的集成不声明任何能力；消费点（如日历下发）在能力不可用时返回引导文案而不是报错。

## 集成矩阵

| 集成 | 状态 | 能力 | 接入指南 |
| --- | --- | --- | --- |
| 飞书（Lark / 妙搭） | 已支持 | 表格存储 ✔ · 日历 ✔ · 通知 ✔ · 审批 ✔ · 自然语言 ✔ | [feishu.md](feishu.md) |
| 本地模式 | 默认可用（已启用） | 表格存储 ✔ · 通知 ✔（审批与 NL 由系统内置组件承担） | [local.md](local.md) |
| 钉钉（DingTalk） | 规划中 · 社区共建 | 目标：AI 表格 / 日历 / 工作通知 / OA 审批 | [dingtalk.md](dingtalk.md) |
| 企业微信（WeCom） | 规划中 · 社区共建 | 目标：智能表格 / 日程 / 通知（审批仅支持模板代发） | 待社区补充（manifest 指向 `wecom.md`） |
| Google Workspace | 规划中 · 社区共建 | 目标：Sheets / Calendar（无原生审批） | 待社区补充（manifest 指向 `google-workspace.md`） |

> 说明：状态与能力声明以 `backend/app/integrations/registry.py` 的 manifest 为准。已注册适配器的集成在设置页显示实时连接状态；规划中的集成为纯 manifest 占位（`status: planned`），能力为目标预期而非现状。

## 如何使用

1. 自部署管理员：登录后进入 **设置 → 外部集成**，各集成卡片内有接入向导与连接状态徽章；对应文档见上方链接。
2. 不接平台：什么都不做，本地模式默认可用（见 [local.md](local.md)）。

## 如何开发一个新集成

完整四步接入法（实现 Protocol → 写 manifest → `@register` 注册 → 补测试断言）见
[`backend/app/integrations/README.md`](../../backend/app/integrations/README.md)。

接入文档请遵循七节模板（简介与能力矩阵 → 前置条件 → 权限清单 → 配置步骤 → 测试连接 → 故障排查 → 已知限制），模板说明见
[`docs/roadmap/03-integrations.md` §2.4](../roadmap/03-integrations.md)。
