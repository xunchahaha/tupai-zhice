/**
 * 任务上下文主线（07-task-context.md，TC-6 批次）的前端契约扩展。
 *
 * 后端 TC-1/TC-2/TC-4 落地并重导 openapi 之前，orval 生成模型里还没有
 * task_constraints / memory_action_receipts / goal.context 字段；沿用仓库既有
 * 「按后端契约就地扩展」模式（interpret-stream.ts 的 thinking、排课助手页的
 * suggested_instruction），在这里集中定义本地类型，后端契约重生成后可整体收敛。
 */

/** 解析产物中的任务级约束：只作用于本次任务，不落全局规则库（07 §2.1）。 */
export interface AssistantTaskConstraint {
  /** 解析侧生成键（如 "tc-1"），确认卡与清单项回溯原话用。 */
  id: string;
  /** 原话片段（如「张老师周三晚上不能上」）。 */
  source_text: string;
  subject_type: "teacher" | "classroom" | "cohort" | string;
  subject_ids: string[];
  slot_business_ids: string[];
  /** hard=这次不能（进清单/编译为规则对象）；soft=尽量（进 goal.context 随求解请求体携带）。 */
  hardness: "hard" | "soft" | string;
  /** 对任务既有要求做什么：add=追加（缺省）、replace=修改 target_id 那一条、remove=取消那一条。 */
  op?: "add" | "replace" | "remove" | string;
  /** replace/remove 指向的既有要求的稳定编号；指不到具体旧项时后端绝不自动删除。 */
  target_id?: string | null;
}

/** 记忆动作回执（07 §3.1）：explicit 直接执行的结论或候选降级说明。 */
export interface AssistantMemoryActionReceipt {
  action_id: string;
  status: "executed" | "pending_confirmation" | "failed_degraded" | string;
  /** executed 时回填，可据此在记忆页定位条目。 */
  entry_id?: string | null;
  /** 一句话回执文案（含可修改/可撤销提示固定后缀）。 */
  receipt: string;
}

/**
 * SolveGoal.context（07 §4.1）：「当前工作状态」三键——范围草稿、软任务约束、
 * 工作草稿指针。旧目标 context=NULL 视为无上下文。
 */
export interface GoalTaskContext {
  schema_version?: number;
  scope?: {
    business_lines?: string[];
    product_types?: string[];
    class_business_ids?: string[];
    /** 单课调整的课次限定（后端随每次求解写回，续办时据此恢复）。 */
    course_business_ids?: string[];
    date_from?: string | null;
    date_to?: string | null;
    date_window_days?: number;
  };
  soft_task_constraints?: AssistantTaskConstraint[];
  /** 该目标正在调整的工作草稿（版本 id 指针），续办时作为基准默认（07 §4.6）。 */
  work_draft_schedule_id?: string | null;
}

/** 上下文是宽松 JSON 列，读取时逐字段收敛，坏形状一律当「无上下文」。 */
export function parseGoalContext(value: unknown): GoalTaskContext | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Partial<GoalTaskContext> & Record<string, unknown>;
  const scope =
    raw.scope && typeof raw.scope === "object" && !Array.isArray(raw.scope)
      ? (raw.scope as GoalTaskContext["scope"])
      : undefined;
  const soft = Array.isArray(raw.soft_task_constraints)
    ? (raw.soft_task_constraints as AssistantTaskConstraint[])
    : undefined;
  const workDraft =
    typeof raw.work_draft_schedule_id === "string" ? raw.work_draft_schedule_id : undefined;
  if (!scope && !soft && !workDraft) return null;
  return {
    schema_version: typeof raw.schema_version === "number" ? raw.schema_version : undefined,
    scope,
    soft_task_constraints: soft,
    work_draft_schedule_id: workDraft ?? null,
  };
}

export const TASK_CONSTRAINT_SOURCE_NOTE = "仅作用于本次任务，不进入规则库";

/** 非「追加」的要求在确认卡上要明说：它改的是任务里已有的哪一条，而不是新增。 */
export function taskConstraintOpLabel(op: AssistantTaskConstraint["op"]): string | null {
  if (op === "replace") return "修改已有要求";
  if (op === "remove") return "取消已有要求";
  return null;
}

export function taskConstraintHardnessLabel(hardness: AssistantTaskConstraint["hardness"]): string {
  return hardness === "soft" ? "软约束" : "硬约束";
}

/** 记忆回执徽标文案（07 §6.1：executed 绿、pending/降级黄）。 */
export function memoryReceiptStatusLabel(status: AssistantMemoryActionReceipt["status"]): string {
  if (status === "executed") return "已执行";
  if (status === "pending_confirmation") return "待确认";
  return "已降级候选";
}

/**
 * §5.2 加预算公式：新时限 = min(max(当前×3, 90), 900)（schemas 上限 900）。
 * 纯函数，确认卡按钮与挂载 action 共用同一口径。
 */
export function raisedBudgetSeconds(current: number): number {
  return Math.min(Math.max(Math.round(current) * 3, 90), 900);
}

const REVISION_PARTS: Array<[string, (items: string) => string]> = [
  ["added_hard", (items) => `新增硬性要求：${items}`],
  ["tightened", (items) => `由「尽量」收紧为硬性要求：${items}`],
  ["added_soft", (items) => `新增软性要求：${items}`],
  ["replaced_soft", (items) => `修改了软性要求：${items}`],
  ["removed_soft", (items) => `取消了软性要求：${items}`],
  ["unresolved", (items) => `没能确定要修改/取消的是哪一条，已保留原要求：${items}`],
  ["kept_hard", (items) => `原本就是硬性要求、这次没有放宽（要放宽请到任务清单里改）：${items}`],
];

/**
 * 这次求解创建时对任务要求做过的修订（后端 task_revision）转成给教务看的一句话：
 * 确认过的要求先成为任务的新版要求、再开始求解，之后重跑与验收都按这一版——说清楚改了什么。
 * 没有修订（含空对象）返回空串。
 */
export function describeTaskRevision(revision: Record<string, string[] | undefined> | null | undefined): string {
  if (!revision) return "";
  const parts = REVISION_PARTS.flatMap(([key, render]) => {
    const items = revision[key];
    return items?.length ? [render(items.map((item) => `「${item}」`).join("、"))] : [];
  });
  return parts.length ? `已更新任务要求：${parts.join("；")}。之后重跑与验收都按这一版。` : "";
}
