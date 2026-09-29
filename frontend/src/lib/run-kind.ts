/**
 * 一次求解在页面上的「所处阶段」：助手页按它决定显示进行中 / 结果 / 卡住哪张卡，
 * 目标进度一句话（lib/goal.ts）与进展步骤条（lib/assistant-task.ts）共用同一口径。
 */
export type RunKind = "solving" | "result" | "stuck" | "failed";

export interface RunKindInput {
  status?: string | null;
  model_status?: string | null;
  presolve_infeasible?: boolean | null;
}

export function classifyRun(run: RunKindInput | null | undefined): RunKind | null {
  if (!run) return null;
  if (run.status === "failed") return "failed";
  if (run.status !== "completed") return "solving";
  // 预检没过时求解器根本没跑：即便模型状态写着 OPTIMAL（范围内无课次）也不算有草稿。
  if (run.presolve_infeasible) return "stuck";
  if (run.model_status === "OPTIMAL" || run.model_status === "FEASIBLE") return "result";
  // INFEASIBLE / UNKNOWN / 未返回：都没有可用候选课表。
  return "stuck";
}

export type StuckCause = "failed" | "presolve" | "timeout" | "infeasible" | "unknown";

/** 没排出来的原因（业务上只分这几类）：卡住卡的标题与任务卡的一句话进展共用这一处分支。 */
export function stuckCause(run: RunKindInput): StuckCause {
  if (run.status === "failed") return "failed";
  if (run.presolve_infeasible) return "presolve";
  if (run.model_status === "UNKNOWN") return "timeout";
  if (run.model_status === "INFEASIBLE") return "infeasible";
  return "unknown";
}
