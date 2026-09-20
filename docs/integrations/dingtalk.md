# 钉钉（DingTalk）接入指南（社区共建中）

> 状态：规划中（planned）· 暂无适配器实例，能力为目标预期而非现状
> manifest 占位见 `backend/app/integrations/__init__.py`（`register_manifest(id="dingtalk")`）

## 简介

钉钉集成由社区共建，目标是通过途排智策的能力接口接入钉钉 AI 表格、日历、工作通知与 OA 审批。当前代码中钉钉仅为 manifest 占位（`status: planned`），设置页显示「规划中」卡片；本文整理目标能力矩阵、已知平台缺口与调研线索，供贡献者参考。

## 目标能力矩阵

| 能力 | 目标平台组件 | 已知缺口 |
| --- | --- | --- |
| 表格存储 | 钉钉 AI 表格（多维表） | 缺视图/权限类 API，无法对齐飞书的「班级筛选视图」方案，公开投影需另行设计 |
| 日历 | 钉钉日程 | 待调研与飞书 freebusy 等价的忙闲查询接口 |
| 通知 | 工作通知（批量发给组织内成员） | 频控与模板限制待调研 |
| 审批 | OA 审批（代提交 + 状态回传） | **只能基于企业已预建的审批模板发起**，需提供模板 `process_code` 配置；审批流模板仍由本系统内定义 |
| 自然语言 | — | 不在钉钉清单内；一句话排课统一走平台无关的 OpenAI-compatible 模型通道 |

## 鉴权同构性

国内主流平台的鉴权模式同构：`appId + appSecret → access_token`。钉钉与企业微信、飞书可以共用统一 AuthProvider 的设计思路（见 `docs/roadmap/03-integrations.md` §2.1），钉钉适配器可复用飞书适配器已验证的模式：凭据加密存储、令牌刷新加锁、`verify()` 只读本地配置。

## 接口调研线索

钉钉开放平台文档站：<https://open.dingtalk.com>。建议贡献者先行核对的接口族：

1. **AI 表格**：表/字段/记录的增删改查、批量写入上限与分页上限（对照 `TableStore` 能力接口的抽象记录模型）；
2. **日历**：日程创建与忙闲查询的身份体系（userid vs unionId，与教师「日历账号」字段的映射）；
3. **工作通知**：发送频控、是否要求企业内部应用；
4. **OA 审批**：模板 `process_code` 获取方式、代提交 thirdparty 接口与审批事件回调。

核对时请沿用本项目惯例：以官方文档当前版本为准逐条记录页面地址与权限键（参照 `docs/对接资料/02_飞书官方/05_官方契约核对记录_2026-08-10.md` 的做法），不要凭记忆或旧资料写死端点。

## 参与共建

欢迎 PR。适配器开发遵循 [`backend/app/integrations/README.md`](../../backend/app/integrations/README.md) 的四步接入法：

1. 新建 `backend/app/integrations/dingtalk/adapter.py` 实现 `Integration` 协议，能力按**调研核实后的真实支持**声明（勿照抄本文目标矩阵）；
2. 编写 manifest，`docs_url` 指向本文档；
3. 在 `backend/app/integrations/__init__.py` 把 `register_manifest(...)` 占位替换为 `register(DingTalkAdapter)`；
4. 在 `backend/tests/test_integrations.py` 补三条断言（registry 可查到、manifest 完整、`GET /integrations` 清单状态正确）。

建议先在 issue 中同步调研结论（接口核对照、缺口结论）再动手实现，避免返工。

> 企业微信与 Google Workspace 的接入指南待社区补充，manifest 中的占位路径分别为 `docs/integrations/wecom.md` 与 `docs/integrations/google-workspace.md`。
