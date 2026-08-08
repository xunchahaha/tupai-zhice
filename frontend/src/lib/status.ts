import type { BadgeTone } from "@/components/ui/badge";

export function statusTone(status?: string | null): BadgeTone {
  if (["completed", "OPTIMAL", "FEASIBLE", "active", "published"].includes(status ?? "")) return "green";
  if (["failed", "INFEASIBLE", "rejected"].includes(status ?? "")) return "red";
  if (["queued", "running", "awaiting_confirmation", "pending"].includes(status ?? "")) return "yellow";
  return "blue";
}
