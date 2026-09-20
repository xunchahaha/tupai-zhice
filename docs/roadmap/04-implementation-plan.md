# 04 · 实施计划与进度

> 状态：执行中 · 分支 `feat/open-source-upgrade`
> 批次策略遵循工作区 AGENTS.md：按模块分批、实现代理自检 + 局部验证、里程碑统一全量验证、每批一个原子提交。

## 勘察修正（重要）

代码勘察结论修正了升级前提：**项目不存在「写出来但没接后端」的功能**——85 个端点全部真实、前端调用全部打到后端。真实差距是：

1. **反向差距**：`POST /auth/change-password`（改密无 UI）、SSE `/solver-runs/{id}/events`、`GET /schedules/{id}/calendar-bindings` 三处后端已实现、前端无入口；
2. **飞书耦合**：前端约 130 处飞书硬编码露出（integrations-page 整页 102 处 + 面板级 + 字段级），无独立设置页；
3. **文档断层**：无面向开发者的架构文档，14 列导入格式只存在于代码里。

## 批次总览

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| DOC-0 | roadmap 骨架 + 四大设计文档定稿 | ✅ 完成 | ceb0a8d, 3b8cc34, 8a04445, 515925c |
| IMP-A | 后端导入管线 v2（映射服务 + preview/commit + L1 补强） | ✅ 完成（18 新用例 + 74 回归全绿） | ae32a95 |
| INT-A | 后端集成抽象层（Protocol 能力接口 + registry，飞书 strangler 收敛为首个适配器，LocalAdapter 默认可用） | 🔄 实现中 | — |
| SET-A | 前端「设置」页重构（/settings 四分区：通用+改密 / AI 模型 / 集成卡片 / 关于；旧 /integrations 重定向） | ✅ 完成（45/45 vitest + tsc 零错误，7769e14） | 7769e14 |
| SET-B | 前端飞书露出中性化（文案级，5 文件 + e2e 名称；不含 integrations-page 与 app-shell 导航，那两处归 SET-A） | ✅ 完成（44/44 vitest 全绿，abdcb15） | abdcb15 |
| UX-0 | 求解页交互断点与侧边栏 IA 调研（Workflow：勘察→业界参考→三方案→评审综合） | ✅ 完成（简报=05-ux-sop.md，6784ac1） | — |
| UX-A | 求解页交互闭环（日期窗口与规则联动、AI 解析后渐进披露参数、解析加载态 + thinking 展示） | 待 UX-0 简报 | — |
| UX-B | 侧边栏与全局 SOP 重排（参考开源项目 IA；视觉风格不变） | 待 UX-0 简报 | — |
| PUB-0 | 公开展示层调研（脱离妙搭后，面向老师/学生/家长的课表展示：免登录链接 / ICS 订阅 / 移动只读视图） | ✅ 完成（简报=06-public-showcase.md，6784ac1） | — |
| PUB-A | 后端公开课表 API（匿名 token 链接、ICS 订阅端点、调课通知；复用 public_* 数据资产与权限体系） | 待 PUB-0 简报 | — |
| PUB-B | 前端公开课表页（移动优先只读视图、二维码/链接分发、复用课表渲染组件） | 待 PUB-A | — |
| IMP-B | 前端导入向导（上传→映射→校验→提交，复用现有组件与动效；原地修复属 IMP-4 二期） | 🔄 实现中（与 SET-A 文件不相交） | — |
| MEM-A | 后端记忆层（PreferenceEntry + 一键归因 + 挖掘循环 + 求解权重编译器） | 排队（等 INT-A） | — |
| MEM-B | 前端记忆 UI（待确认收件箱 + 详情页偏好区块 + 解释引用） | 排队（等 MEM-A） | — |
| OSS-A | 开源文档套件（架构文档、README 重写、接入指南模板 feishu/local/dingtalk、LICENSE 建议） | 排队（等 INT-A 定形后写，避免返工） | — |
| VER-1 | 里程碑统一验证：后端全量 pytest + ruff/mypy + 前端 vitest + build + orval 再生成 | 里程碑 | — |

## 依赖关系

```
IMP-A ──> IMP-B（前端向导打新端点）
INT-A ──> SET-A / SET-B（设置页 + 露出点中性化）
MEM-A ──> MEM-B
IMP-A / INT-A / MEM-A 串行执行（共享 api.py，避免并发冲突）
前端三批（IMP-B / SET-B / MEM-B）文件不相交，可并行
VER-1 收口
```

## 风格红线（所有前端批次共同遵守）

- 页面根节点 `<div className="space-y-5 animate-fade-in">`；卡片 `rounded-lg border border-zinc-200 bg-white shadow-2xs`；
- 只用现有 ui 组件（button/badge/select/tabs/dialog/data-table/page）；不引入新 UI/动画依赖；
- 图标 lucide-react size-3.5/4；控件 h-8/h-9、text-sm/xs；微交互 150-300ms。

## 里程碑验证清单（VER-1）

- [ ] `uv run pytest`（基线 208 passed）+ ruff + mypy
- [ ] `pnpm vitest run` + `pnpm build` + orval 再生成无 diff
- [ ] 手动走查：设置页、导入向导、记忆收件箱（dev server）
- [ ] openapi.json、数据字典、接口约定、backend/README 同步更新
- [ ] 全部提交按 conventional commits 分组，最终 diff 审查
