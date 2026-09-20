# 06 · 公开展示层设计：脱离妙搭后的老师/学生/家长课表门户

> 状态：定稿（6 智能体专项工作流：本地资产勘察 + 展示方案调研 + ICS 调研 → 双方案 → 综合裁决）
> 关联：[README](README.md) · [03-integrations.md](03-integrations.md)
> 用户问题：之前的公开展示靠飞书妙搭，脱离飞书后，面向老师/学生/家长怎么展示？权限管理怎么接？

## 1. 展示矩阵（定稿）

| 受众 | 入口 | 登录态 | 可见粒度 | 明确不展示 |
| --- | --- | --- | --- | --- |
| 老师（内部） | 现有登录端 /schedule、/reschedule | JWT + 角色（现状不动） | 个人课表、全校视图、调课审批 | — |
| 老师（对外） | `/public/t/{teacher-token}` | 免登录 | 本人已发布课表（P1） | 工号不外显，只显姓名 |
| 学生/家长 | 班级二维码 / 群链接 → `/public/t/{class-token}` | 免登录 | **本班课表**（日卡片流+周切换）+ 调课通知横幅 + ICS 订阅 | 学生姓名及一切学生字段（数据源本身无，天然合规） |
| 公众/督导 | 学校目录 token | 免登录 | 班级索引 → 班级页，print CSS 张榜公示（P2） | 利用率等内部运营指标 |

**与权限体系的关系（回答用户问题）**：公开层**不进角色权限体系**——管理端 RBAC（admin/scheduler/approver/viewer + 成员矩阵）完全不动；公开面用 **capability-link（凭证链接）**模型与之正交：链接的签发/轮换/停用权限 = 现有 `require_roles("admin","scheduler")`；撤回手段 = token 停用/轮换/过期。对标 WebUntis Public Timetables 与 Purdue UniTime 公开页的同构做法。

**分发判断（调研结论）**：中国校园主流是「二维码 → 群链接 → 扫码即看」的免登录 H5（草料扫码习惯、钉钉家校群课表共享），ICS 日历订阅是黏性通道（家长不用回访，日历自动更新）。两者不互斥，挂同一 token。

## 2. 关键架构事实（勘察实证）

1. **public 四表不是数据库表**，而是内存投影纯函数（api.py `_public_class_index_rows` L4143、`_public_adjustment_notice_rows` L4271、`_public_summary` L5623 等，签名 `(db, schedule_set_id)`）——公开端点直接复用，零模型改动。
2. **免登录零侵入**：router 无 router 级依赖，端点不声明 `CurrentUser`/`ViewerScope` 即天然绕过 JWT（先例：`require_aily_key` api.py L5200 + AilyScope）。
3. `public_class_links` 索引表的链接列本就故意留空由运营手填——自建 `/public/t/{token}` URL 语义完全对位（P2 可自动回填）。
4. ICS 数据齐备：`lesson_date` + `TimeSlot.start/end`（回退 `fixed_start/end`，与 `_public_assignment_snapshot` 同取值链）+ `published_at` 作 DTSTAMP；匿名化先例已存在（公开面「待定」不泄 ID、「班级A」脱敏行）。

## 3. 定稿决策

### PUB-A · 后端（批次）

| # | 决策 |
| --- | --- |
| A1 | 新表 `public_link_tokens`（只存 SHA-256 哈希 + token_hint 末4位；scope=class/teacher/school；expires_at/revoked_at/last_seen_at/access_count/display_name/show_teacher_names）；注册进 Base 走现有建表机制 |
| A2 | 投影函数迁移：`_public_*` 12 个纯函数原样搬入 `services/public_projection.py`，api.py 同名 re-import（飞书同步分发零改动）；新增 `public_class_payload` / `public_teacher_payload` / `public_directory_payload` |
| A3 | 管理端点（JWT+admin/scheduler）：`GET/POST /schedule-sets/{id}/public-links`、`POST /public-links/{id}/rotate`、`DELETE /public-links/{id}`；`secrets.token_urlsafe(32)` 生成，**明文仅在创建响应返回一次** |
| A4 | 公开端点（免登录）：`GET /public/links/{token}/schedule.json`（显式 Pydantic 白名单：班级名/科目/教师姓名/教室/时间，**禁止整模型透传**）+ `GET /public/links/{token}/calendar.ics`；无效/停用/过期一律 404 不暴露存在性 |
| A5 | `services/ics.py`：`icalendar`（BSD-2）+ `tzdata`；P0 只生成有 `lesson_date` 的课次，TZID=Asia/Shanghai 静态 VTIMEZONE、UID 跨版本稳定（`tupai-{scope}-{id}@public.tupai`）、`SEQUENCE=version_no`、DTSTAMP=published_at、ETag=sha256(version+published) 支持 304；P1 再做 RRULE 周重复展开 |
| A6 | config：`public_links_enabled=True` 总开关、`public_default_ttl_days=180`；`Cache-Control: public, max-age=3600`；访问计数节流写（10 分钟） |

### PUB-B · 前端（批次）

| # | 决策 |
| --- | --- |
| B1 | 匿名路由 `/public/t/:token`（router.tsx /login 平级、AuthBoundary 外、兜底规则前）；公开页**不 import http 实例**（避免带 Authorization 与 401 广播），裸 fetch + `API_BASE_URL` 常量 |
| B2 | H5 页（移动优先，新组件不碰管理端）：头部（班级名 + V{n} + 更新时间）→ sticky 调课横幅（amber 语义色，点开全量 before→after 对照）→ 日期 chips（今天 blue-600 高亮）→ 按日分组卡片流（进行中课程 ring-2 ring-blue-500）→ 底部「订阅到日历」（iOS `webcal://` 一键；Android Google 日历 cid 跳转 + 复制链接，注明 12-24h 刷新延迟） |
| B3 | `<meta referrer=no-referrer>` + `noindex` |
| B4 | 管理页 `/public-links`（RoleRoute admin/scheduler，侧边栏「变更」组相邻新增）：表格（范围 badge/状态/访问数/最后访问）+ 创建弹窗（班级下拉=当前发布版本班级、有效期预设、教师姓名开关）+ 轮换/停用/复制 + 分享弹窗 `react-qr-code`（MIT，纯 SVG 二维码） |
| B5 | P1：教师 scope UI、按发布版本批量生成全部班级链接、桌面 ≥768px 周网格（届时从 schedule-page 抽 `MatrixView` 组件复用 + `teacherNameMap` 替换 business_id 显示）、print CSS 张榜打印、`public_class_links` 链接列自动回填 |
| B6 | P2：school scope 目录页（督导公示）、教室大屏 JSON feed、学期末批量过期按钮、无效 token per-IP 限流（**有效 token 不限流**——日历轮询器依赖反复拉取） |

## 4. 合规与安全清单

1. 学生隐私红线：公开 payload 显式字段白名单；测试断言序列化结果无学生字段/工号/电话格式；code review 检查「禁止 from_attributes 整模型透传」。
2. 教师 business_id（工号）任何分支不外泄，只出 `Teacher.name`；`show_teacher_names` 每链接可关。
3. Token：256-bit 熵、库存哈希、可过期可撤销、轮换即旧链接立即失效、统一 404 防探测、日志只落 token_hint。
4. 部署前提：公网可达 + HTTPS（Google 订阅不支持认证）；`public_links_enabled` 可一键关闭公开面。
5. 内部指标（利用率等）不进公开页。

## 5. 验收标准

- 未登录 curl schedule.json / calendar.ics 返回 200；过期/停用/伪造 token 一律 404。
- Apple/Google 日历实际订阅成功、时间正确（Asia/Shanghai）；重新发布后 DTSTAMP/SEQUENCE 变化、客户端原位更新。
- 公开 payload grep 无学生/工号字段；管理端所有既有页面视觉零 diff（P0 不碰 schedule-page）。
- 二维码扫码可开 H5；轮换后旧链接立即 404。

## 6. 工作量与切分

PUB-A 后端 M（投影迁移 + token 表 + 6 端点 + ICS 服务）；PUB-B 前端 M（H5 页 + 管理页 + 分享弹窗 + 路由）。P0 合计约 3-4 人日（单实现智能体可顺序交付）；P1 再 1-2 天。

## Sources（精选）

- WebUntis Public Timetables（help.untis.at）；Purdue UniTime 公开课表（timetable.mypurdue.purdue.edu）
- 钉钉家校群课程表共享；企业微信家校通知扫码；草料二维码扫码查（cli.im）
- 教育部《规范办学行为指导意见》课表张榜公布；广州 2025 课程公示制度
- ClassIsland（班级大屏，github.com/ClassIsland/ClassIsland）；FET HTML 导出；WakeUp 课程表分享码模式
- OWASP capability URL / `secrets.token_urlsafe`；`icalendar`（BSD-2）；`react-qr-code`（MIT）
- 完整清单见工作流日志（3 路调研 + 2 方案原文，.artifact-work/pub-materials/）
