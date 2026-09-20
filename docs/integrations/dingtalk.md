# 钉钉（DingTalk）接入指南

> 状态：**v1 已实现**（HTTP 层已按官方文档实现并单测覆盖；未经生产凭据实测，欢迎 issue 反馈）
> 能力声明见 `backend/app/integrations/dingtalk/adapter.py`；HTTP 封装见 `backend/app/integrations/dingtalk/client.py`

## 1. 简介

钉钉集成通过途排智策的适配器层（`backend/app/integrations/`）接入钉钉开放平台。v1 覆盖四类能力：AI 表格记录读写、日程、工作通知与 OA 审批发起。管理员在设置页一次填写企业内部应用的 AppKey/AppSecret 即完成接入（Fernet 加密落库），不需要任何 OAuth 授权向导。

### 能力矩阵

| 能力 | 支持情况 | 说明 |
| --- | --- | --- |
| 表格存储 | ✔ | 钉钉 AI 表格（notable）记录批量新建/修改/删除/翻页查询；`upsert_records` 按行是否携带 `id` 拆分新建与更新，并按 100 行/批自动分批 |
| 日历 | ✔ | 在用户默认日历（`calendar.default`）创建/修改/删除日程；**忙闲（freebusy）查询未实现**，等价接口待调研 |
| 通知 | ✔ | 工作通知（`asyncsend_v2` 异步任务）；单次 ≤ 100 人（官方频控），超出自动分批 |
| 审批 | ✔* | OA 审批代发起（`/v1.0/workflow/processInstances`）；**只能基于企业已预建的审批模板**，需在配置中提供模板 `process_code`——未配置时运行时不声明审批能力 |
| 自然语言 | ✘ | 不在钉钉清单内；一句话排课统一走平台无关的 OpenAI-compatible 模型通道（设置 → AI 模型） |

> \* 审批为「代理提交」，审批流模板仍由本系统内定义；平台侧模板由管理员预先创建。

## 2. 前置条件

- **钉钉企业内部应用**：在[钉钉开放平台](https://open.dingtalk.com)创建（企业内部应用 / H5 微应用均可），取得 `AppKey` 与 `AppSecret`。
- **接口权限**：按使用的能力在开放平台为应用申请对应权限点——AI 表格读写、日程（通讯录个人信息用于 userid 定位）、工作通知（企业内部应用默认可用）、OA 审批（代发起）。
- **途排智策管理员账号**：凭据录入由 `admin` 角色在设置页完成。

## 3. 配置步骤

1. 钉钉开放平台 → 应用后台：创建企业内部应用，记录 `AppKey`、`AppSecret`；如需审批代发起，在 OA 审批后台预建模板并取得模板 `process_code`。
2. 途排智策 → **设置 → 集成** → 钉钉卡片 → 「配置」，按表单填写：

   | 配置项 | 必填 | 说明 |
   | --- | --- | --- |
   | `app_key` | 是 | 应用 AppKey |
   | `app_secret` | 首次必填 | 应用密钥；**加密存储**，回显只显示是否已配置，留空表示保持不变 |
   | `process_code` | 否 | OA 审批模板编码；不填则不启用审批能力 |
   | `base_url` | 否 | API 基地址，默认 `https://api.dingtalk.com` |

3. 保存即生效（`PUT /api/v1/integrations/dingtalk/configuration`，密钥字段 Fernet 加密落库，`GET` 同路径返回脱敏配置）；保存会立即失效该平台的 access_token 缓存。
4. 「测试连接」：获取一次 access_token 做轻量探测（带缓存，不产生频控压力）。

## 4. 鉴权说明

`AppKey + AppSecret → access_token`（`POST /v1.0/oauth2/accessToken`，有效期 7200 秒）。令牌为进程级内存缓存，到期前 5 分钟自动刷新；新版 v1.0 接口（api.dingtalk.com）以 `x-acs-dingtalk-access-token` 头鉴权，旧版 topapi（oapi.dingtalk.com）以 `access_token` 查询参数鉴权，同一令牌通用。

## 5. 已知限制与风险

- **未经生产凭据实测**：HTTP 层按官方文档实现并全部经 `httpx.MockTransport` 单测覆盖（`tests/test_integrations_cn.py`），但端点路径、字段名与批量上限尚未在真实企业凭据下联调；如有出入请以官方文档为准并提 issue。
- AI 表格记录批量写上限按 **100 行/批** 保守收紧（官方查询分页 maxResults≤100，批量写上限待核对）。
- 钉钉 AI 表格缺视图/权限类 API（roadmap §2.1 调研结论），无法对齐飞书的「班级筛选视图」方案，公开投影需另行设计。
- 工作通知为异步任务，存在企业级频控；本适配器只保证「单次 ≤ 100 人」的入参合规。
- 忙闲查询（freebusy 等价接口）未实现，「日历下发」的冲突预检在钉钉通道下不可用。

## 6. 参与共建

HTTP 封装与能力协商代码在 `backend/app/integrations/dingtalk/`；改动遵循 [`backend/app/integrations/README.md`](../../backend/app/integrations/README.md) 的接入法与测试约定。生产凭据联调结论（接口核对照、缺口清单）欢迎以 issue/PR 形式回填本文。
