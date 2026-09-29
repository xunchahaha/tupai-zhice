import type { SolveRequest } from "@/api/generated/models";

export type SolverRule = NonNullable<SolveRequest["solver_rules"]>[number];

export interface SolverParamValues {
  time_limit_seconds: number;
  date_window_days: number;
  change_weight: number;
  solver_rules: SolverRule[];
  business_lines: string[];
  product_types: string[];
  class_business_ids: string[];
  date_from: string | null;
  date_to: string | null;
}

/** 教室、教师冲突是系统级硬约束，界面展示但不允许关闭。 */
export const SYSTEM_SOLVER_RULES: Array<{ key: SolverRule; label: string; hint: string }> = [
  { key: "room_no_overlap", label: "教室不重叠", hint: "同一教室的真实时间区间不可重叠，始终生效" },
  { key: "teacher_no_overlap", label: "教师不重叠", hint: "具体个人教师不可同时上两节课；教研组名称不代表已落实到个人" },
];
export const OPTIONAL_SOLVER_RULES: Array<{ key: SolverRule; label: string; hint: string }> = [
  { key: "fixed_time", label: "固定上课时段", hint: "只调整日期和教室，不动上课时刻" },
  { key: "calendar_no_overlap", label: "日程账号不重叠", hint: "已映射日历账号的教师按个人日历约束" },
  { key: "minimize_changes", label: "最小化变更", hint: "优先保持与已发布课表一致" },
];
export const SYSTEM_RULE_KEYS = SYSTEM_SOLVER_RULES.map((item) => item.key);

export const defaultParams: SolverParamValues = {
  time_limit_seconds: 30,
  date_window_days: 7,
  change_weight: 100000,
  solver_rules: [...SYSTEM_SOLVER_RULES, ...OPTIONAL_SOLVER_RULES].map((item) => item.key),
  business_lines: [],
  product_types: [],
  class_business_ids: [],
  date_from: null,
  date_to: null,
};

/** 范围字段被手动从限定改为「全部」时暂存的原值（扩大范围需单独确认，见 use-task-params）。 */
export interface ScopeExpansion {
  business_lines?: string[];
  class_business_ids?: string[];
}

/** 提交求解时系统硬约束必须在场，且去重。 */
export function withSystemRules(rules: readonly SolverRule[]): SolverRule[] {
  return [...new Set([...rules, ...SYSTEM_RULE_KEYS])];
}
