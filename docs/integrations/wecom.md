# 企业微信（WeCom）接入指南

> 状态：**v1 已实现**（HTTP 层已按官方文档实现并单测覆盖；未经生产凭据实测，欢迎 issue 反馈）
> 能力声明见 `backend/app/integrations/wecom/adapter.py`；HTTP 封装见 `backend/app/integrations/wecom/client.py`

## 1. 简介

企业微信集成通过途排智策的适配器层（`backend/app/integrations/`）接入企业微信服务端 API。v1 覆盖三类能力：智能表格记录读写、日程创建与应用消息通知。管理员在设置页一次填写企业 corpid、应用 secret 与 AgentId 即完成接入（Fernet 加密落库），不需要任何 OAuth 授权向导。

### 能力矩阵

| 能力 | 支持情况 | 说明 |
| --- | --- | --- |
| 表格存储 | ✔ | 企业微信智能表格（wedoc smartsheet）记录批量新增/修改/删除/翻页查询；`upsert_records` 按行是否携带 `record_id` 拆分新建与更新，按 100 行/批自动分批 |
| 日历 | ✔* | 日程创建/修改/删除（`/cgi-bin/oa/schedule/*`，时间为 Unix 秒）；**仅能落在应用自建日历下**，不能指定其他日历，也不支持忙闲查询 |
| 通知 | ✔ | 应用文本消息（`/cgi-bin/message/send`）；touser 单次 ≤ 1000 人（官方上限，`\|` 连接），超出自动分批；返回平台回执的无效 userid |
| 审批 | ✘ | **不支持**：企业微信服务端 API 无审批代发起接口（roadmap §2.1 调研结论），manifest 不声明该能力；发布审批由系统内审批流承担 |
| 自然语言 | ✘ | 一句话排课统一走平台无关的 OpenAI-compatible 模型通道（设置 → AI 模型） |

> \* 日历能力的平台限制见第 5 节；「日历下发」的消费接缝按 `Capability.CALENDAR` 运行时协商，钉钉/企微/飞书任一配置可用即可。

## 2. 前置条件

- **企业微信自建应用**：在[企业微信管理后台](https://work.weixin.qq.com) → 应用管理创建自建应用，取得 `AgentId` 与应用 `secret`；在企业信息页取得企业 `corpid`。
- **应用可见范围**：需要接收通知/日程的成员必须在应用可见范围内，否则 `message/send` 会返回无效 userid。
- **接口权限**：智能表格（文档）、日程（OA）、消息发送权限按需在后台开启。
- **途排智策管理员账号**：凭据录入由 `admin` 角色在设置页完成。

## 3. 配置步骤

1. 企业微信管理后台：创建自建应用，记录 `corpid`、应用 `secret`、`AgentId`。
2. 途排智策 → **设置 → 集成** → 企业微信卡片 → 「配置」，按表单填写：

   | 配置项 | 必填 | 说明 |
   | --- | --- | --- |
   | `corp_id` | 是 | 企业 ID（corpid） |
   | `corp_secret` | 首次必填 | 应用密钥（secret）；**加密存储**，回显只显示是否已配置，留空表示保持不变 |
   | `agent_id` | 是 | 应用 AgentId（整数），用于日程归属与消息发送 |
   | `base_url` | 否 | API 基地址，默认 `https://qyapi.weixin.qq.com` |

3. 保存即生效（`PUT /api/v1/integrations/wecom/configuration`，密钥字段 Fernet 加密落库，`GET` 同路径返回脱敏配置）；保存会立即失效该平台的 access_token 缓存。
4. 「测试连接」：调用 `gettoken` 做轻量探测（带缓存，不产生频控压力）。

## 4. 鉴权说明

`corpid + corpsecret → access_token`（`GET /cgi-bin/gettoken`，有效期 7200 秒）。令牌为进程级内存缓存，到期前 5 分钟自动刷新；所有业务接口以 `access_token` 查询参数鉴权。企业微信以 HTTP 200 + `errcode` 表达业务失败，凭据错误（如 `errcode=40001`）会在测试连接中以软失败呈现。

## 5. 已知限制与风险

- **未经生产凭据实测**：HTTP 层按官方文档实现并全部经 `httpx.MockTransport` 单测覆盖（`tests/test_integrations_cn.py`），但端点路径、字段名与批量上限尚未在真实企业凭据下联调；如有出入请以官方文档为准并提 issue。
- **日程仅应用自建日历**：企业微信日程接口不能指定日历，日程固定落在应用自建日历下；教师个人日历场景无法覆盖，忙闲查询亦无对应接口。
- **审批不支持**：平台无服务端审批代发起 API；如后续开放，可在适配器上扩展并更新 manifest。
- 智能表格记录批量写上限按 **100 行/批** 保守收紧（官方单页查询上限 1000，批量写上限待核对）；智能表格本身有约 10 万行容量上限（roadmap §2.1），不适合大体量课表归档。
- 应用消息依赖应用可见范围；发往范围外成员会收到 `invaliduser` 回执，调用方需自行决定补发或忽略。

## 6. 参与共建

HTTP 封装与能力协商代码在 `backend/app/integrations/wecom/`；改动遵循 [`backend/app/integrations/README.md`](../../backend/app/integrations/README.md) 的接入法与测试约定。生产凭据联调结论（接口核对照、缺口清单）欢迎以 issue/PR 形式回填本文。
