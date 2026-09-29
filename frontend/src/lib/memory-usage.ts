import { ROUTES } from "@/lib/routes";

// 偏好记忆使用情况的前端视图模型（MEM-C1）。后端在创建求解任务时把
// memory_usage 冻结进 SolverRun（结构同 snapshot.payload["memory"]），
// 助手页解释面板与记忆页「最近编译结果」列共用这里的状态与文案。

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

// 偏好条目的业务描述：助手页「参考的常用偏好」逐条列出时用。记忆页有自己的一套
// 文案（不导出），这里只保留展示一条偏好所需的最小词表。
const SUBJECT_TYPE_LABELS: Record<string, string> = {
  teacher: "教师",
  classroom: "教室",
  cohort: "班级",
  course: "课程",
};

const PREDICATE_LABELS: Record<string, string> = {
  avoid_slot: "避开时段",
  prefer_slot: "偏好时段",
  avoid_room: "避开教室",
  prefer_room: "偏好教室",
  consecutive_sessions: "连续上课",
  max_daily_load: "日负荷上限",
};

export function memoryOutcomeDescription(item: MemoryOutcome): string {
  const subject = [SUBJECT_TYPE_LABELS[item.subject_type ?? ""] ?? item.subject_type, item.subject_id].filter(Boolean).join(" ");
  const predicate = item.predicate ? (PREDICATE_LABELS[item.predicate] ?? item.predicate) : "";
  return [subject, predicate].filter(Boolean).join(" · ") || "一条常用偏好";
}

/** 记忆页按 ?entry= 高亮并滚动到该偏好（设置线实现，这里只负责生成链接）。 */
export function memoryEntryPath(entryId: string): string {
  return `${ROUTES.memory}?${new URLSearchParams({ entry: entryId })}`;
}

export interface MemoryUsageSplit {
  compileFailed: boolean;
  applied: MemoryOutcome[];
  notApplied: MemoryOutcome[];
}

/** 参与本次求解的偏好 / 未采用的偏好（编译失败单独标记，必须直接可见）。 */
export function splitMemoryOutcomes(memory: MemoryUsageSnapshot | null | undefined): MemoryUsageSplit {
  const outcomes = Array.isArray(memory?.outcomes) ? memory.outcomes : [];
  return {
    compileFailed: memory?.status === "compile_failed",
    applied: outcomes.filter((item) => item.outcome === "applied"),
    notApplied: outcomes.filter((item) => item.outcome !== "applied"),
  };
}
