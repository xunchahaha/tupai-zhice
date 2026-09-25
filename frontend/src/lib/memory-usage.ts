// 偏好记忆使用情况的前端视图模型（MEM-C1）。后端在创建求解任务时把
// memory_usage 冻结进 SolverRun（结构同 snapshot.payload["memory"]），
// 求解页解释面板与记忆页「最近编译结果」列共用这里的状态与文案。

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
  // 第七轮口径收口：outcome=applied 的含义是「编译进求解输入」（编译资格），
  // 不是「对本次课程实际匹配」更不是「结果满足」，文案按编译口径表述。
  applied: "已获准编译",
  not_authorized: "未授权试用",
  expired: "已过期",
  unsupported_predicate: "暂不支持求解",
  converted_to_rule: "已转正式规则",
  hard_requires_conversion: "待转正式规则",
  conflict_unresolved: "冲突待处理",
  // MEM-D1 D2：constraint 日期窗口与条目有效期交集为空 = 明确不适用（不是冲突）。
  not_applicable: "不适用：日期窗口与约束范围不相交",
  compile_error: "编译失败",
};

export function memoryOutcomeLabel(outcome: string): string {
  return OUTCOME_LABELS[outcome] ?? outcome;
}

export function memoryHeadline(memory: MemoryUsageSnapshot | null | undefined): string | null {
  if (!memory?.status) return null;
  if (memory.status === "compile_failed") return "本次未使用偏好记忆：编译失败";
  const considered = memory.summary?.considered ?? 0;
  if (!considered) return "创建任务时没有可用的偏好记忆";
  // 第七轮口径收口：summary 是「创建时点、方案级」的编译资格统计，不代表这些
  // 偏好作用于本次求解的课次；「已应用」改说「编译进求解输入」，是否作用于
  // 本次课程以解释层按任务范围算出的匹配核对为准。
  return `创建任务时 ${considered} 条偏好记忆获准编译（编译进求解输入 ${memory.summary?.applied ?? 0} / 未编译 ${memory.summary?.unused ?? 0}）；是否作用于本次课程，以解释层的范围核对为准`;
}
