import type { GoalChecklistItem } from "@/api/generated/models";

/**
 * 目标验收闭环（MEM-C3）的前端口径：后端 report 由代码验收器落库
 * （SolverRun.goal_report），这里只做类型收敛与文案，不重复计算。
 */

export interface GoalReportItem {
  key: string;
  requirement: string;
  kind: string;
  passed: boolean;
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
  items: GoalReportItem[];
  gaps: GoalGap[];
  decision: { status: string; reason: string } | null;
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
    items: report.items as GoalReportItem[],
    gaps: (Array.isArray(report.gaps) ? report.gaps : []) as GoalGap[],
    decision:
      report.decision && typeof report.decision === "object"
        ? (report.decision as GoalReport["decision"])
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

export const GOAL_KIND_LABELS: Record<string, string> = {
  coverage: "课次覆盖",
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
