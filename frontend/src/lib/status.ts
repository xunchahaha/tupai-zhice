import type { BadgeTone } from "@/components/ui/badge";

export function statusTone(status?: string | null): BadgeTone {
  if (["completed", "OPTIMAL", "FEASIBLE", "active", "published"].includes(status ?? "")) return "green";
  if (["failed", "INFEASIBLE", "rejected"].includes(status ?? "")) return "red";
  if (["queued", "running", "awaiting_confirmation", "pending", "partial"].includes(status ?? "")) return "yellow";
  return "blue";
}

/** 求解结论的色调。任务「已完成」不代表排出了课表——无解和超时都必须自己的颜色。 */
export function modelStatusTone(modelStatus?: string | null): BadgeTone {
  if (["OPTIMAL", "FEASIBLE"].includes(modelStatus ?? "")) return "green";
  if (modelStatus === "INFEASIBLE") return "red";
  if (modelStatus === "UNKNOWN") return "yellow";
  return "blue";
}
