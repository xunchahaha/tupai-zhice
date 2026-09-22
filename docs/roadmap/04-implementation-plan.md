# 04 · 实施计划与进度

> 状态：三期「第二轮源码复审正确性修复」进行中（2026-09-22，依 02 文档 §7）
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
| INT-A | 后端集成抽象层（Protocol 能力接口 + registry，飞书 strangler 收敛为首个适配器，LocalAdapter 默认可用） | ✅ 完成（10 新用例 67 全绿，4212479） | 4212479 |
| SET-A | 前端「设置」页重构（/settings 四分区：通用+改密 / AI 模型 / 集成卡片 / 关于；旧 /integrations 重定向） | ✅ 完成（45/45 vitest + tsc 零错误，7769e14） | 7769e14 |
| SET-B | 前端飞书露出中性化（文案级，5 文件 + e2e 名称；不含 integrations-page 与 app-shell 导航，那两处归 SET-A） | ✅ 完成（44/44 vitest 全绿，abdcb15） | abdcb15 |
| UX-0 | 求解页交互断点与侧边栏 IA 调研（Workflow：勘察→业界参考→三方案→评审综合） | ✅ 完成（简报=05-ux-sop.md，6784ac1） | — |
| UX-A | 求解页交互闭环（日期窗口与规则联动、AI 解析后渐进披露参数、解析加载态 + thinking 展示） | ✅ 完成（PR1 D1-D8，后端 55 + 前端 50 用例全绿，6590afc） | 6590afc |
| UX-B | 侧边栏与全局 SOP 重排（参考开源项目 IA；视觉风格不变） | ✅ 完成（D9-D13 全落地，64 用例全绿，5ebf349） | 5ebf349 |
| PUB-0 | 公开展示层调研（脱离妙搭后，面向老师/学生/家长的课表展示：免登录链接 / ICS 订阅 / 移动只读视图） | ✅ 完成（简报=06-public-showcase.md，6784ac1） | — |
| PUB-A | 后端公开课表 API（匿名 token 链接、ICS 订阅端点、调课通知；复用 public_* 数据资产与权限体系） | ✅ 完成（AST 逐字节等价迁移验证，全量 258 passed，d78c833） | d78c833 |
| PUB-B | 前端公开课表页（移动优先只读视图、二维码/链接分发、复用课表渲染组件） | ✅ 完成（17 文件 91 用例全绿，bb06dd2） | bb06dd2 |
| IMP-B | 前端导入向导（上传→映射→校验→提交，复用现有组件与动效；原地修复属 IMP-4 二期） | ✅ 完成（10 文件 53 用例全绿，86227a2） | 86227a2 |
| MEM-A | 后端记忆层（PreferenceEntry + 一键归因 + 挖掘循环 + 求解权重编译器） | ✅ 完成（83 定向用例全绿，f65eb16） | f65eb16 |
| MEM-B | 前端记忆 UI（待确认收件箱 + 详情页偏好区块 + 解释引用） | ✅ 完成 v1（收件箱/挖掘/偏好表 + 归因 chips；解释引用与教师可见属 v2，72 用例全绿，7a5640f） | 7a5640f |
| OSS-A | 开源文档套件（架构文档、README 重写、接入指南 feishu/local/dingtalk、LICENSE 建议） | ✅ 完成（ARCHITECTURE.md + README 开源版 7c7d61a；接入指南 44f7c16；**LICENSE 文件待用户拍板 Apache-2.0/MIT**；VER-1 顺带清理 backend README 的 tupai-seed 幽灵命令） | 44f7c16, 7c7d61a |
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

## 二期「规划中清零」批次（用户指令：文档里没做完的全部做完）

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| SCR-0 | 一键启动脚本（start.bat / scripts/start.sh + README 指引） | ✅ | 5e2aaba |
| UX-SSE | interpret SSE 流式（后端 stream 端点 + 前端 fetch reader + 失败回退） | ✅（后端 13 用例 + 前端 12 用例，全量 279 passed，e5c2890） | e5c2890 |
| CN-A | 钉钉 + 企业微信适配器 v1（真实 API 实现 + mock 单测 + 凭据加密配置端点） | ✅（12 用例，全量 279 passed，01e746e；未经生产凭据联调已在文档声明） | 01e746e |
| PUB-C | ICS RRULE 循环课次 + school 单班端点 + 按发布版本批量生成链接 + verify 端点 + Lark base_url settings 化 | ✅（7 新用例，全量 286 passed，b39b738） | b39b738 |
| IMP-C | 导入收尾：historical mapping（表头指纹记忆）+ 单元格原地修复（cell overrides） | ✅（断点接手完成，52 后端 + 104 前端用例绿，33e1859） | 33e1859 |
| MEM-C1 | 审查修正第一波：三态拆分（待确认/授权试用/已确认）、hard 转正式 Rule、课程适用日期窗口、实体匹配严格化、偏好冻结进快照、逐条使用结果 | ✅ 完成（七条验收全过，全量 298 passed / 前端 108 passed，69a70df） | 69a70df |
| MEM-C2 | 审查修正第二波：挖掘证据支持性校验（≥2 条不同证据/主体一致/约束来自证据/declared_reason 消噪/学期过滤）+ 拒绝记忆与矛盾消解 | ✅ 完成（8 新用例，全量 306 passed / 前端 109 passed，9109537） | 9109537 |
| MEM-C3 | 审查修正第三波：Goal 验收闭环（逐项验收清单 + 代码化验收器 + 报告回灌 + 停止规则） | ✅ 完成（13 新用例，全量 319 passed / 前端 117 passed，ee172a7） | ee172a7 |
| OSS-C | print CSS 张榜打印 + README「规划中」节清零 + VER-2 全量（含 e2e） | ✅ 完成（后端 ruff/mypy 全过 + 319 passed；前端 tsc 0 + vitest 117 passed + build 成功 + orval 零 diff；e2e 3 passed；README「项目状态」改为已实现 + 已知边界，ARCHITECTURE 同步） | — |

## 三期「正确性修复」批次（第二轮源码复审，2026-09-22）

| 批次 | 内容 | 状态 | 提交 |
| --- | --- | --- | --- |
| MEM-D1 | 记忆组合正确性：未授权候选不得经冲突标记间接改变排课（改写固化错误语义的旧测试）；日期两层取交集；编辑后冲突重算；可学习事件公共前置筛选（AI/统计同准入，含最终态被接受过滤） | ✅ 完成（核心验收反转：旧 confirmed 照常编译；全量 339 passed，4704dc0） | 4704dc0 |
| MEM-D2 | 目标验收正确性：三集合分离（目标课次/求解课次/合并交付）；无法验证≠通过；底线验收独立于自定义清单（有交付物/课次不重复/完整性）；禁排参数存在性验证；_decide 消费 UNKNOWN vs INFEASIBLE；验收状态 pending/completed/failed 可见 | ✅ 完成（11 新用例，全量 339 passed / 前端 117 passed，792f629） | 792f629 |
| MEM-D3 | 目标连续性：re-parse 与手动求解均携带 goal_id；目标详情「继续处理」；禁排占位补参端点+UI；选基准自动生成 max_changes；清单修订版本化 + 记忆页裁决新语义适配 | 🔄 实现中 | — |
| VER-3 | 终验：全量 pytest + vitest + build + orval 零 diff + e2e + **新增 GitHub Actions CI**（使「全绿」可被仓库记录独立核验） | 排队 | — |

## 里程碑验证清单（VER-1）✅ 已通过（2026-09-20）

- [x] `uv run pytest` 全量 **258 passed, 0 failed**（基线 208 → 新增 50）+ ruff 全过 + mypy 0 issues（31 files）
- [x] `pnpm vitest run` **17 文件 / 91 测试全过** + `tsc -b` 0 错误 + `vite build` 成功（4.07s）
- [x] orval 再生成 **零 diff**（含 openapi.json 重导出一致）
- [x] openapi.json、数据字典、接口约定、backend/README 随批次同步更新；tupai-seed 幽灵命令已清理
- [x] 43+ 提交全部 conventional commits 分组、每批一次原子提交、最终逐批 diff 审查
- [ ] dev server 手动走查（设置页/导入向导/记忆收件箱/公开 H5）留给合并前人工过一遍
