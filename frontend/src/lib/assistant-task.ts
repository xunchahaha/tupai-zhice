import type { GoalDetailResponse } from "@/api/generated/models";
import type { Interpretation } from "@/lib/interpret-stream";
import { type RunKind, type RunKindInput, stuckCause } from "@/lib/run-kind";
import type { SolverParamValues } from "@/lib/solver-params";
import type { AssistantTaskConstraint, GoalTaskContext } from "@/lib/task-context";

/**
 * 解析阶段文案：流式回退（或事件空窗期）按 2.5s 顺序轮播；流式通道的 stage
 * 事件键值与这里一一对应，事件到达即跳到对应阶段并锁定轮播。
 */
export const INTERPRET_STAGES = [
  { key: "connect", text: "正在连接 AI 模型…" },
  { key: "read", text: "正在理解调整需求…" },
  { key: "match", text: "正在匹配课程与日期…" },
  { key: "validate", text: "正在校验解析结果…" },
];

/** 解析过程状态机：thinking=请求在途，parsed=成功，failed=页内错误条。 */
export type InterpretPhase = "idle" | "thinking" | "parsed" | "failed";

export type TaskStepState = "done" | "current" | "todo" | "blocked";

export interface TaskStep {
  key: "understood" | "confirmed" | "solving" | "draft" | "publish";
  label: string;
  state: TaskStepState;
}

/**
 * 任务条的进展步骤：已理解 → 已确认 → 求解中 → 草稿已生成 → 待发布 / 已发布。
 * 「当前」= 第一个还没完成的步骤（也就是下一步要办的事）；求解没排出来时该步标为
 * 「没能排出」并阻塞，后面的步骤保持灰显。
 */
export function buildTaskSteps(input: {
  understood: boolean;
  confirmed: boolean;
  runKind: RunKind | null;
  draftStatus?: string | null;
  /**
   * 旧结果还在，但「继续调整」刚解析出一份新的理解、等着确认：当前步是确认新的理解，
   * 旧结果的「待发布」不能再标成当前——否则会让人以为可以直接发布旧草稿。
   */
  pendingConfirmation?: boolean;
}): TaskStep[] {
  if (input.pendingConfirmation) {
    return [
      { key: "understood", label: "已理解（待确认）", state: "current" },
      { key: "confirmed", label: "已确认", state: "todo" },
      { key: "solving", label: "求解中", state: "todo" },
      { key: "draft", label: "草稿已生成", state: "todo" },
      { key: "publish", label: "待发布", state: "todo" },
    ];
  }
  const { runKind } = input;
  const hasRun = runKind !== null;
  const solved = runKind === "result";
  const blocked = runKind === "stuck" || runKind === "failed";
  const published = solved && input.draftStatus === "published";
  const raw: Array<{ key: TaskStep["key"]; label: string; done: boolean; blocked?: boolean }> = [
    { key: "understood", label: "已理解", done: input.understood || hasRun },
    { key: "confirmed", label: "已确认", done: input.confirmed || hasRun },
    { key: "solving", label: blocked ? "没能排出" : "求解中", done: solved, blocked },
    { key: "draft", label: "草稿已生成", done: solved },
    { key: "publish", label: published ? "已发布" : "待发布", done: published },
  ];
  let currentTaken = false;
  return raw.map((step) => {
    if (step.blocked) {
      currentTaken = true;
      return { key: step.key, label: step.label, state: "blocked" };
    }
    if (step.done) return { key: step.key, label: step.label, state: "done" };
    if (!currentTaken) {
      currentTaken = true;
      return { key: step.key, label: step.label, state: "current" };
    }
    return { key: step.key, label: step.label, state: "todo" };
  });
}

/** 卡住卡（与只读诊断）的标题：业务语言写原因；预检结论不说「已证明无解」，超时不等于无解。 */
export function stuckHeadline(run: RunKindInput): string {
  switch (stuckCause(run)) {
    case "failed": return "这次求解没有完成";
    case "presolve": return "求解前的数据检查没通过，还没开始排";
    case "timeout": return "时间用完了，还没找到可用的排法";
    case "infeasible": return "在现有的硬性要求下排不出全部课";
    default: return "这次没有得到可用的排课结果";
  }
}

/** 把范围草稿拆成人话片段；全是空的时候返回空数组，由调用方补「全部课次」。 */
export function describeScope(scope: Pick<SolverParamValues, "business_lines" | "product_types" | "class_business_ids" | "date_from" | "date_to">): string[] {
  const parts: string[] = [];
  if (scope.business_lines.length) parts.push(`业务线 ${scope.business_lines.join("、")}`);
  if (scope.product_types.length) parts.push(`班型 ${scope.product_types.join("、")}`);
  if (scope.class_business_ids.length) parts.push(`班级 ${scope.class_business_ids.join("、")}`);
  if (scope.date_from || scope.date_to) parts.push(`日期 ${scope.date_from ?? "不限"} 至 ${scope.date_to ?? "不限"}`);
  return parts;
}

/**
 * 「本次要求」一行摘要：范围 + 任务级约束原话 + 先出草稿。
 * 全部由已展示给用户的范围草稿与约束推导，不引入新的口径。
 */
export function buildRequirementItems(input: {
  scope: Pick<SolverParamValues, "business_lines" | "product_types" | "class_business_ids" | "date_from" | "date_to">;
  constraints: readonly AssistantTaskConstraint[];
}): string[] {
  const scopeParts = describeScope(input.scope);
  const items = [scopeParts.length ? `只调整${scopeParts.join("，")}` : "调整范围：全部课次"];
  for (const item of input.constraints) {
    items.push(item.hardness === "soft" ? `${item.source_text}（尽量）` : item.source_text);
  }
  items.push("先出草稿，不会自动发布");
  return items;
}

/**
 * 续办/重新解析时，解析口径与目标清单 coverage 项口径的比对（MEM-D3 目标连续性）。
 * 返回不一致的项名；空数组 = 一致。范围或日期不一致只提示，目标归属不变。
 */
export function goalScopeConflicts(goal: Pick<GoalDetailResponse, "checklist"> | undefined, data: Interpretation): string[] {
  const coverage = (goal?.checklist ?? []).find((item) => item.kind === "coverage");
  const scope = (coverage?.params ?? {}) as Record<string, unknown>;
  const norm = (values: unknown): string[] => (Array.isArray(values) ? [...new Set(values.map(String))].sort() : []);
  const conflicts: string[] = [];
  const diff = (label: string, a: string[], b: string[]) => {
    if (a.join("¦") !== b.join("¦")) conflicts.push(label);
  };
  diff("业务线", norm(scope.business_lines), norm(data.business_lines));
  diff("产品班型", norm(scope.product_types), norm(data.product_types));
  diff("班级范围", norm(scope.class_business_ids), norm(data.class_business_ids));
  const goalFrom = scope.date_from ? String(scope.date_from).slice(0, 10) : "";
  const goalTo = scope.date_to ? String(scope.date_to).slice(0, 10) : "";
  if ((goalFrom || goalTo) && (goalFrom !== (data.date_from ?? "") || goalTo !== (data.date_to ?? ""))) {
    conflicts.push("日期范围");
  }
  return conflicts;
}

/** 清单草稿里是否有「待量化」的禁排占位项——有才说明这些未落实要求能靠补参落地。 */
export function hasQuantizablePlaceholder(interpretation: Interpretation): boolean {
  return (interpretation.goal_checklist_draft ?? []).some(
    (item) => item.kind === "forbidden_slot_free" && Boolean((item.params as { needs_params?: unknown } | null | undefined)?.needs_params),
  );
}

/**
 * 没有 context.scope 的旧任务：范围只留在清单的 coverage 项参数里。续办时用它回填范围草稿，
 * 否则加预算重跑会悄悄退回默认的全范围。coverage 项里没有的字段一律不动。
 */
export function scopeFromChecklist(goal: Pick<GoalDetailResponse, "checklist">): NonNullable<GoalTaskContext["scope"]> | undefined {
  const coverage = (goal.checklist ?? []).find((item) => item.kind === "coverage");
  const raw = (coverage?.params ?? undefined) as Record<string, unknown> | undefined;
  if (!raw) return undefined;
  const list = (value: unknown): string[] | undefined => (Array.isArray(value) ? value.map(String) : undefined);
  const day = (value: unknown): string | null | undefined => (typeof value === "string" && value ? value.slice(0, 10) : undefined);
  const scope: NonNullable<GoalTaskContext["scope"]> = {
    business_lines: list(raw.business_lines),
    product_types: list(raw.product_types),
    class_business_ids: list(raw.class_business_ids),
    date_from: day(raw.date_from),
    date_to: day(raw.date_to),
  };
  return Object.values(scope).some((value) => value !== undefined) ? scope : undefined;
}

/**
 * 确认卡的解析摘要：后端固定以「请教务确认后启动 CP-SAT 求解。」收尾，这句话对教务是技术黑话，
 * 且和卡片自己拼的「；解析来源」撞出「。；」。这里换成业务说法并去掉句末标点，由卡片统一收尾；
 * 后端原文不改，仍可在「查看详情」里核对。
 */
export function plainInterpretSummary(summary: string): string {
  return summary
    .replace(/启动\s*CP-SAT\s*求解/gi, "开始排课")
    .replace(/CP-SAT\s*求解器?/gi, "排课")
    .replace(/CP-SAT/gi, "排课")
    .replace(/[\s。；;.，,]+$/, "")
    .trim();
}

/**
 * 「继续调整」发给解析的指令：已关联任务时只发追加的话（后端按任务上下文增量解析）；
 * 没有任务时接在原始需求后面；连原始需求都没有（手动排课的结果、直接打开的求解记录）
 * 就只用追加的话本身，不能拼出以「；」开头的句子。
 */
export function buildRefineInstruction(input: { goalBound: boolean; base: string; extra: string }): string {
  const extra = input.extra.trim();
  const base = input.base.trim();
  return input.goalBound || !base ? extra : `${base}；${extra}`;
}

/** 需求文本截断（任务卡、放弃确认里用）。 */
export function truncateText(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}
