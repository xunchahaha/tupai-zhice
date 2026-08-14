# 飞书开放平台服务端 API

访问日期：2026-08-10

> 本页只记录当前实现需要遵守的官方接口契约。若官方页面之间存在版本差异，以标明“最新版本”的具体 API 页面为准。

## 用户授权

- 获取授权码：`GET https://accounts.feishu.cn/open-apis/authen/v1/authorize`
- 交换或刷新令牌：`POST https://accounts.feishu.cn/oauth/v3/token`
- 获取授权码文档：https://open.feishu.cn/document/authentication-management/access-token/obtain-oauth-code
- 获取 user_access_token v3：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/get-user-access-token-v3
- 刷新 user_access_token v3：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/refresh-user-access-token-v3

实现约束：

- 授权地址必填参数为 `client_id`（值取应用 App ID）、`response_type=code` 和 `redirect_uri`；`scope`、`state`、`prompt` 为可选参数。
- 回调 URL 必须预先配置在开发者后台的“安全设置 -> 重定向 URL”。
- 授权请求必须携带并校验 `state`；使用 `state` 防止 CSRF。
- 授权码有效期为 5 分钟，且只能使用一次。
- 不使用 PKCE：飞书开放平台创建的应用均为 Confidential Client，凭 `client_secret` 完成客户端认证，官方“获取授权码”请求示例同样不带 `code_challenge`；实测携带 PKCE 参数时令牌端点返回 20049（PKCE code challenge failed），故按官方示例移除。
- 需要后台持续同步时，授权范围必须包含 `offline_access`。
- `refresh_token` 每次刷新后立即轮换，旧值随即失效；新旧令牌替换必须在一次数据库事务内完成。
- 飞书开放平台创建的应用属于 Confidential Client，`App Secret` 只保存在后端。

旧版网页接入指南仍展示 v2 示例地址，但 v2 API 页面已在 2026-06-29 标记为历史版本，并明确要求迁移到上述 v3 端点。代码不得继续使用 v2 地址。

## 身份选择

官方说明：https://open.feishu.cn/document/faq/trouble-shooting/how-to-choose-which-type-of-token-to-use

- `tenant_access_token` 代表应用身份，适合操作应用自己拥有的资源。
- `user_access_token` 代表授权用户身份，适合操作该用户可读写的资源。

途排智策默认使用 `user_access_token` 创建排课多维表格，使表格归属并出现在授权管理员的云空间。`tenant_access_token` 只保留给未来的“应用统一托管中央表格”部署方式，不作为当前主流程。

## 自动创建多维表格

- 创建多维表格：`POST https://open.feishu.cn/open-apis/bitable/v1/apps`
- 新增数据表：`POST https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables`
- 创建多维表格文档：https://open.feishu.cn/document/server-docs/docs/bitable-v1/app/create
- 新增数据表文档：https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table/create

创建多维表格接口支持 `user_access_token`，响应直接返回：

- `app_token`
- `default_table_id`
- `folder_token`
- `url`

新增数据表接口可在创建时一次传入表名、默认视图和字段。系统必须保存接口返回的标识，管理员不需要手工复制 `app_token` 或逐张提供 `table_id`。

## 记录读写与幂等

- 检索记录（按条件搜索）：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/bitable-v1/app-table-record/search
- 批量新增：https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/batch_create
- 批量更新：https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/batch_update

读取既有记录统一使用检索记录接口 `POST /open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search`，分页参数 `page_size`、`page_token` 放在查询参数中，`page_size` 最大 500。该接口的官方权限要求为 `base:record:retrieve`（或完整 `bitable:app`）。

不使用 `GET .../records` 批量获取接口：该接口的官方权限要求是 `bitable:app` 或 `bitable:app:readonly`，不在途排智策的最小权限集内。

每张业务表必须包含稳定的“业务标识”字段。首次发布查询现有业务标识，缺失项批量新增；已有项使用飞书 `record_id` 批量更新。后端保存业务 ID 与 `record_id` 的绑定，重试时先对账再写入，避免重复记录。

## 当前最小权限集合

权限列表：https://open.feishu.cn/document/server-docs/application-scope/scope-list

以下权限键、官方中文名称均按飞书开放平台“API 权限列表”页面逐条核对，全部支持用户身份调用：

| 权限键 | 官方中文名称 | 用途 |
| --- | --- | --- |
| `offline_access` | 持续访问已授权的数据 | 获取并轮换刷新令牌 |
| `base:app:create` | 创建多维表格 | 创建多维表格 |
| `base:app:read` | 获取多维表格信息 | 读取并检查多维表格元数据 |
| `base:table:create` | 新增数据表 | 创建带初始字段的数据表 |
| `base:table:read` | 获取数据表信息 | 对账已创建的数据表 |
| `base:table:update` | 更新数据表 | 将创建接口附带的默认表改名为“接入说明” |
| `base:record:create` | 新增记录 | 批量写入新记录 |
| `base:record:retrieve` | 根据条件搜索记录 | 按业务标识检索记录 |
| `base:record:update` | 更新记录 | 更新已有记录和发布状态 |

当前不申请删除表、删除记录和完整 `bitable:app` 权限。

## 日历接口

- 创建日程：`POST /open-apis/calendar/v4/calendars/{calendar_id}/events`
- 添加日程参与人：`POST /open-apis/calendar/v4/calendars/{calendar_id}/events/{event_id}/attendees`
- 查询主日历忙闲：`POST /open-apis/calendar/v4/freebusy/list`

实现约束（2026-08-15 逐页复核）：

- 三个接口均支持 `user_access_token`，与本项目「资源归属授权管理员」的选择一致。
- 忙闲查询**一次只接受一个** `user_id` 或 `room_id`，参数为 `time_min`、`time_max`、
  `user_id`/`room_id`、`include_external_calendar`、`only_busy`。不存在
  `freebusy/batch` 地址，也不存在复数 `user_ids` 参数。需要查多个教师时逐个请求后汇总。
- 权限均为「开启任一权限即可」：创建日程用 `calendar:calendar.event:create`，
  添加参与人用 `calendar:calendar.event:update`，忙闲查询用
  `calendar:calendar.free_busy:read`；三者也都可由粗粒度的 `calendar:calendar` 覆盖，
  本项目按最小权限申请细粒度键。

## 多维表格插件的边界

官方概述：https://open.feishu.cn/document/base-extensions/base-extension-introduction

官方将两种能力明确分开：

- OpenAPI 用于自有系统与多维表格的数据互通和基础数据操作。
- 多维表格插件用于把自定义界面嵌入某个多维表格，扩展记录视图、数据表视图或自动化操作。

途排智策当前需要的是前者，因此不依赖“多维表格插件”应用能力。以后若要把排课诊断面板直接嵌进表格，再单独开发插件。
