/**
 * 排课主流程 SOP（单一事实源）：流程页步骤条与总览快捷入口共用同一份时序。
 * 无解诊断与课表视图是观察页，不算流程步骤，不进清单。
 */
export interface SopStep {
  key: string;
  label: string;
  to: string;
}

export const SOP_STEPS: SopStep[] = [
  { key: "master-data", label: "主数据", to: "/master-data" },
  { key: "rules", label: "规则", to: "/rules" },
  { key: "solver", label: "求解", to: "/solver" },
  { key: "reschedule", label: "调课", to: "/reschedule" },
  { key: "versions", label: "发布", to: "/versions" },
];
