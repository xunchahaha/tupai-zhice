# 飞书开放平台服务端 API

访问日期：2026-08-08

## 鉴权

- 自建应用获取 tenant_access_token：https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal
- 请求地址：`POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal`
- 后端配置：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`
- Token 在内存缓存，并在过期前刷新；日志不得记录应用密钥或完整 Token。

## 多维表格记录

- 批量新增记录：https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/batch_create?lang=zh-CN
- 记录列表：https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list?lang=zh-CN
- 批量更新：https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/batch_update?lang=zh-CN

批量新增单次最多 1,000 条，后端按 1,000 条切批。读取必须处理分页；写入以业务 ID 做幂等匹配，失败记录进入同步日志。

## 应用配置清单

1. 在飞书开放平台创建企业自建应用。
2. 开通多维表格读取与写入所需的最小权限并发布应用版本。
3. 创建目标多维表格，将自建应用加入协作者。
4. 从多维表格 URL 获取 `app_token`，从开放平台或 URL 获取各表 `table_id`。
5. 将资源与 `table_id` 的映射写入 `FEISHU_TABLE_MAP`。
6. 调用 `/api/v1/integrations/feishu/connection` 和同步接口完成冒烟验证。

