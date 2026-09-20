// 偏好记忆使用情况的前端视图模型（MEM-C1）。后端在创建求解任务时把
// memory_usage 冻结进 SolverRun（结构同 snapshot.payload["memory"]），
// 求解页解释面板与记忆页「最近使用」列共用这里的状态与文案。

export interface MemoryOutcome {
  entry_id: string;
  subject_type?: string;
  subject_id?: string;
  predicate?: string;
  status?: string;
  outcome: string;
  detail?: string;
}

export interface MemoryUsageSnapshot {
  status?: string;
  detail?: string;
  compiled_at?: string;
  outcomes?: MemoryOutcome[];
  summary?: { considered?: number; applied?: number; unused?: number };
}

const OUTCOME_LABELS: Record<string, string> = {
  applied: "已应用",
  not_authorized: "未授权试用",
  expired: "已过期",
  unsupported_predicate: "暂不支持求解",
  converted_to_rule: "已转正式规则",
  hard_requires_conversion: "待转正式规则",
  conflict_unresolved: "冲突待处理",
  compile_error: "编译失败",
};

export function memoryOutcomeLabel(outcome: string): string {
  return OUTCOME_LABELS[outcome] ?? outcome;
}

export function memoryHeadline(memory: MemoryUsageSnapshot | null | undefined): string | null {
  if (!memory?.status) return null;
  if (memory.status === "compile_failed") return "本次未使用偏好记忆：编译失败";
  const considered = memory.summary?.considered ?? 0;
  if (!considered) return "本次没有可用的偏好记忆";
  return `本次参考 ${considered} 条偏好记忆（已应用 ${memory.summary?.applied ?? 0} / 未使用 ${memory.summary?.unused ?? 0}）`;
}
