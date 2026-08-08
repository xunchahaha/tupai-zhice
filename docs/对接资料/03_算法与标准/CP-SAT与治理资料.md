# CP-SAT、排课与数据治理资料

访问日期：2026-08-08

## 求解器

- CP-SAT Solver：https://developers.google.com/optimization/cp/cp_solver
- Employee Scheduling：https://developers.google.com/optimization/scheduling/employee_scheduling
- Job Shop：https://developers.google.com/optimization/scheduling/job_shop
- Assumptions API：https://or-tools.github.io/docs/pdoc/ortools/sat/python/cp_model.html#CpSolver.sufficient_assumptions_for_infeasibility

系统保留 `OPTIMAL`、`FEASIBLE`、`INFEASIBLE`、`UNKNOWN` 原始状态。Assumptions API 返回足以导致不可行的规则子集，后端再做有时间上限的删除式缩减，不把它描述为数学意义上的最小冲突集。

## 排课基准

- ITC-2007：https://www.eeecs.qub.ac.uk/itc2007/curriculmcourse/course_curriculm_index.htm
- UniTime 问题定义：https://www.unitime.org/uct_description.php

## 数据治理

- 中华人民共和国个人信息保护法：https://www.spp.gov.cn/spp/fl/202108/t20210820_527244.shtml
- 生成式人工智能服务管理暂行办法：https://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm
- JY/T 0643-2025《智慧教育平台个人信息保护通用要求》
- JY/T 0661-2025《教育数据分类分级指南》
- GB/T 35273-2020《信息安全技术 个人信息安全规范》

原型仅保存匿名业务 ID、班级人数、设备、偏好和课表所需字段，不采集学生姓名、电话和住址。

