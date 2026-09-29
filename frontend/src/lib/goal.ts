import { classifyRun, type RunKindInput, stuckCause } from "@/lib/run-kind";

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

/** 生成模型里 checklist 项是宽松结构，这里收敛出目标组件要读的字段。 */
export interface GoalChecklistEntry {
  key?: string;
  kind?: string;
  requirement?: string;
  params?: Record<string, unknown>;
  [extra: string]: unknown;
}

/** checklist_history 快照（MEM-D3）：后端存 dict，这里收敛出展示字段。 */
export interface GoalChecklistSnapshot {
  version?: number;
  saved_at?: string;
  saved_by?: string | null;
  items?: GoalChecklistEntry[];
}

/** MEM-D3（A4）：禁排占位项（needs_params）补齐参数后才能重新参与验收。 */
export function isNeedsParamsItem(entry: GoalChecklistEntry): boolean {
  return entry.kind === "forbidden_slot_free" && Boolean(entry.params?.needs_params);
}

/** 任务清单里还没补齐参数的占位项（助手各卡片「还差一个条件」的唯一判据）。 */
export function needsParamsItems(goal: { checklist?: unknown[] | null } | null | undefined): GoalChecklistEntry[] {
  return ((goal?.checklist ?? []) as GoalChecklistEntry[]).filter(isNeedsParamsItem);
}

export function goalNeedsParams(goal: { checklist?: unknown[] | null } | null | undefined): boolean {
  return needsParamsItems(goal).length > 0;
}

/** 补参表单的主体类型（教师 / 班级 / 教室）：放在 lib 里，组件文件只导出组件，热更新不退化。 */
export const GOAL_SUBJECT_TYPE_OPTIONS: Array<{ value: "teacher" | "cohort" | "classroom"; label: string }> = [
  { value: "teacher", label: "教师" },
  { value: "cohort", label: "班级" },
  { value: "classroom", label: "教室" },
];

export function goalSubjectTypeLabel(value: string): string {
  return GOAL_SUBJECT_TYPE_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

/** checklist/report 项是否为底线验收项（MEM-D2/D4c，前端「底线」徽标依据）。 */
export function isBottomLineItem(item: {
  params?: Record<string, unknown> | null;
  bottom_line?: boolean;
}): boolean {
  if (typeof item.bottom_line === "boolean") return item.bottom_line;
  return Boolean(item.params?.bottom_line);
}

/** 进行中的任务：还需要教务继续办（open / awaiting_decision）；已达成、已放弃不再出现在「正在处理」里。 */
export function isActiveGoal(status?: string | null): boolean {
  return status === "open" || status === "awaiting_decision";
}

/**
 * 不再接受新求解的任务：只有人工放弃（后端对 abandoned 返回 409）。
 * 已达成的任务仍可继续调整——后一次验收没过会回退为进行中，历史验收报告也应留在同一任务下。
 */
export function isClosedGoal(status?: string | null): boolean {
  return status === "abandoned";
}

export type GoalProgressTone = "green" | "yellow" | "blue" | "neutral" | "red";

interface GoalProgress {
  text: string;
  tone: GoalProgressTone;
}

interface GoalProgressGoal {
  status: string;
  acceptance_status?: string | null;
  run_count?: number | null;
  checklist?: unknown[] | null;
}

type GoalProgressRun = RunKindInput & { goal_report?: unknown };

const ACCEPTANCE_FAILED_TEXT = "已生成草稿，但要求核对没有完成，可以重新核对";

/**
 * 任务卡上的一句话进展（业务语言）：目标状态 + 最新一次求解 + 最新验收报告推导，
 * 不重复计算验收——报告里的通过/缺口以后端验收器落库的为准。
 * latestRun 传 undefined 表示「没取到最新求解」（未求解，或列表里找不到）。
 */
export function goalProgress(goal: GoalProgressGoal, latestRun?: GoalProgressRun | null): GoalProgress {
  if (goal.status === "abandoned") return { text: "已放弃，不再跟进", tone: "neutral" };
  if (!latestRun) {
    if ((goal.run_count ?? 0) === 0) {
      return goalNeedsParams(goal)
        ? { text: "还差一个条件，补充后才能开始排课", tone: "yellow" }
        : { text: "已登记，还没有开始排课", tone: "blue" };
    }
    if (goal.status === "achieved") return { text: "已生成草稿，全部要求已落实", tone: "green" };
    return { text: "已有排课记录，继续处理可查看最新进展", tone: "blue" };
  }
  const kind = classifyRun(latestRun);
  if (kind === "solving") return { text: "求解中，完成后会自动核对要求", tone: "blue" };
  if (kind === "failed") return { text: "求解没有完成，可以重新处理", tone: "red" };
  if (kind === "stuck") {
    const cause = stuckCause(latestRun);
    if (cause === "presolve") return { text: "求解前的数据检查没通过，需要先修正输入", tone: "red" };
    if (cause === "timeout") return { text: "时间用完还没排出来，可以加大时间预算再试", tone: "yellow" };
    return { text: "没能排出来，需要你调整范围", tone: "red" };
  }
  const report = parseGoalReport(latestRun.goal_report);
  if (goal.status === "achieved" || report?.all_passed) return { text: "已生成草稿，全部要求已落实", tone: "green" };
  if (!report) {
    return goal.acceptance_status === "failed"
      ? { text: ACCEPTANCE_FAILED_TEXT, tone: "yellow" }
      : { text: "已生成草稿，正在核对要求", tone: "blue" };
  }
  if (report.acceptance_status === "failed") return { text: ACCEPTANCE_FAILED_TEXT, tone: "yellow" };
  const open = report.failed_count || report.gaps.length || report.unverifiable_count || 1;
  return { text: `已生成草稿，还有 ${open} 项要求需要确认`, tone: "yellow" };
}
