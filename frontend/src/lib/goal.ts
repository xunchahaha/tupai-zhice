import type { GoalChecklistItem } from "@/api/generated/models";

/**
 * 目标验收闭环（MEM-C3/MEM-D2）的前端口径：后端 report 由代码验收器落库
 * （SolverRun.goal_report），这里只做类型收敛与文案，不重复计算。
 */

export interface GoalReportItem {
  key: string;
  requirement: string;
  kind: string;
  passed: boolean;
  /** MEM-D2/D4b：unverifiable = 缺日期/缺参数/无课表，无法验证 ≠ 通过。 */
  verdict?: "passed" | "failed" | "unverifiable" | string;
  /** MEM-D2/D4c：底线验收项（不可删除，与用户附加清单并列）。 */
  bottom_line?: boolean;
  detail: string;
  evidence?: Record<string, unknown> | null;
}

export interface GoalGap {
  key: string;
  kind: string;
  summary: string;
  next_step: string;
  remedy?: string;
}

export interface GoalReport {
  goal_id: string;
  instruction: string;
  all_passed: boolean;
  passed_count: number;
  failed_count: number;
  unverifiable_count?: number;
  items: GoalReportItem[];
  gaps: GoalGap[];
  decision: { status: string; reason: string } | null;
  /** MEM-D2/D6：验收执行本身的状态；failed 时 acceptance_error 说明原因。 */
  acceptance_status?: "pending" | "completed" | "failed" | string;
  acceptance_error?: string | null;
  /** MEM-E2/E2a：验收绑定的清单版本与 coverage 参数快照。 */
  meta?: {
    checklist_version?: number;
    checklist_snapshot?: Record<string, Record<string, unknown>>;
    solve_checklist_version?: number;
    version_note?: string;
  } | null;
}

/** SolverRun.goal_report 在生成模型里是宽松的 Record，这里收敛成可渲染结构。 */
export function parseGoalReport(value: unknown): GoalReport | null {
  if (!value || typeof value !== "object") return null;
  const report = value as Partial<GoalReport> & Record<string, unknown>;
  if (!Array.isArray(report.items)) return null;
  return {
    goal_id: String(report.goal_id ?? ""),
    instruction: String(report.instruction ?? ""),
    all_passed: Boolean(report.all_passed),
    passed_count: Number(report.passed_count ?? 0),
    failed_count: Number(report.failed_count ?? 0),
    unverifiable_count: Number(report.unverifiable_count ?? 0),
    items: report.items as GoalReportItem[],
    gaps: (Array.isArray(report.gaps) ? report.gaps : []) as GoalGap[],
    decision:
      report.decision && typeof report.decision === "object"
        ? (report.decision as GoalReport["decision"])
        : null,
    acceptance_status:
      typeof report.acceptance_status === "string" ? report.acceptance_status : undefined,
    acceptance_error:
      typeof report.acceptance_error === "string" ? report.acceptance_error : null,
    meta:
      report.meta && typeof report.meta === "object"
        ? (report.meta as GoalReport["meta"])
        : null,
  };
}

export const GOAL_STATUS_TONE: Record<string, "green" | "yellow" | "blue" | "neutral"> = {
  achieved: "green",
  open: "blue",
  awaiting_decision: "yellow",
  abandoned: "neutral",
};

export function goalStatusLabel(status?: string | null): string {
  const labels: Record<string, string> = {
    open: "进行中",
    awaiting_decision: "待教务裁决",
    achieved: "已达成",
    abandoned: "已放弃",
  };
  return labels[status ?? ""] ?? (status || "未设置");
}

/** 验收执行状态（MEM-D2/D6）：与目标状态机分开的两套口径。 */
export function goalAcceptanceLabel(status?: string | null): string {
  const labels: Record<string, string> = {
    pending: "验收中",
    completed: "验收完成",
    failed: "验收失败",
  };
  return labels[status ?? ""] ?? (status || "未验收");
}

export const GOAL_KIND_LABELS: Record<string, string> = {
  deliverable_exists: "课表产物",
  coverage: "课次覆盖",
  no_duplicate_lessons: "课次不重复",
  forbidden_slot_free: "禁排复核",
  no_hard_conflicts: "硬冲突重算",
  max_changes: "变更上限",
  draft_only: "只出草稿",
  date_range_match: "日期范围",
};

export function goalKindLabel(kind?: string | null): string {
  return GOAL_KIND_LABELS[kind ?? ""] ?? (kind || "未知项");
}

/** 生成模型里 checklist 项的 params 是宽松 Record，读取时按 kind 收敛。 */
export type GoalChecklistDraft = GoalChecklistItem;

/** checklist/report 项是否为底线验收项（MEM-D2/D4c，前端「底线」徽标依据）。 */
export function isBottomLineItem(item: {
  params?: Record<string, unknown> | null;
  bottom_line?: boolean;
}): boolean {
  if (typeof item.bottom_line === "boolean") return item.bottom_line;
  return Boolean(item.params?.bottom_line);
}
