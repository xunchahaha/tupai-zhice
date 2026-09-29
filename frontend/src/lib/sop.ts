import { ROUTES } from "@/lib/routes";

/**
 * 排课主流程 SOP（单一事实源）：三个顶层页面头部的步骤条共用同一份时序，
 * 与左侧三个业务入口一一对应。规则、调整、发布等是各步内部的就近操作，不再各占一步。
 */
export interface SopStep {
  key: string;
  label: string;
  to: string;
}

export const SOP_STEPS: SopStep[] = [
  { key: "master-data", label: "基础资料", to: ROUTES.masterData },
  { key: "assistant", label: "排课助手", to: ROUTES.assistant },
  { key: "schedule", label: "课表", to: ROUTES.schedule },
];
