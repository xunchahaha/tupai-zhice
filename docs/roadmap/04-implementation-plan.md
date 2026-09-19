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
| DOC-0 | roadmap 骨架 + 三大设计文档 | 进行中 | ceb0a8d, 3b8cc34 |
| IMP-A | 后端导入管线 v2（映射服务 + preview/commit + L1 补强） | 进行中（实现代理 backend-import） | — |
| INT-A | 后端集成抽象层（registry + 能力接口，飞书降级为首个适配器） | 待集成调研合入 | — |
| SET-A | 前端「设置」页重构（通用/AI/集成 分区 + 改密 UI 补齐反向差距） | 待 INT-A | — |
| SET-B | 前端飞书露出点中性化（130 处 → 平台无关文案，风格不变） | 待 INT-A | — |
| IMP-B | 前端导入向导（上传→映射→校验→修复→提交，复用现有组件与动效） | 待 IMP-A | — |
| MEM-A | 后端记忆层（偏好条目 + 挖掘循环 + 求解联动） | 待记忆调研合入 | — |
| MEM-B | 前端记忆 UI（待确认收件箱 + 详情页偏好区块 + 解释引用） | 待 MEM-A | — |
| OSS-A | 开源文档套件（架构文档、README 重写、接入指南模板、CONTRIBUTING） | 部分 |
| VER-1 | 里程碑统一验证：后端全量 pytest + 前端 vitest + build + orval 再生成 | 里程碑 | — |

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
