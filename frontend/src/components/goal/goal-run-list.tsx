import { CheckCircle2, CircleAlert } from "lucide-react";

import { type GoalDetailResponse } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { datetime } from "@/lib/format";
import { parseGoalReport } from "@/lib/goal";
import { modelStatusLabel, statusLabel } from "@/lib/labels";
import { modelStatusTone, statusTone } from "@/lib/status";

/** 目标关联的求解记录：每次求解的状态与它自己的验收结论（按报告所属清单版本标注旧结论）。 */
export function GoalRunList({ goal }: { goal: GoalDetailResponse }) {
  return (
    <div>
      <div className="flex items-center gap-2 text-xs font-medium text-zinc-500">
        求解记录（{goal.runs?.length ?? 0}）
        {(goal.runs ?? []).some((run) => run.goal_report) ? <CheckCircle2 className="size-3.5 text-emerald-500" /> : null}
      </div>
      {!(goal.runs ?? []).length ? (
        <p className="mt-2 text-xs text-zinc-500">还没有关联的求解任务。</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {(goal.runs ?? []).map((run) => {
            const runReport = parseGoalReport(run.goal_report);
            return (
              <li key={run.id} className="flex flex-wrap items-center gap-2 rounded border border-zinc-100 px-3 py-2 text-xs">
                <span className="font-mono text-zinc-500">{run.id.slice(0, 8)}</span>
                <Badge tone={run.status === "completed" ? modelStatusTone(run.model_status) : statusTone(run.status)}>
                  {run.status === "completed" ? modelStatusLabel(run.model_status) : statusLabel(run.status)}
                </Badge>
                {runReport ? (
                  runReport.acceptance_status === "failed" ? (
                    <span className="inline-flex items-center gap-1 text-amber-700" title={runReport.acceptance_error ?? undefined}>
                      <CircleAlert className="size-3" />验收失败：{runReport.acceptance_error ?? "原因未记录"}
                    </span>
                  ) : (
                    <>
                      <span className={runReport.all_passed ? "text-emerald-700" : "text-amber-700"}>
                        验收 {runReport.passed_count}/{runReport.items.length} 项通过
                      </span>
                      {/* MEM-E2/E2a：按报告自身 checklist_version 展示历史版本结论。 */}
                      {Number(runReport.meta?.checklist_version ?? 1) < Number(goal.checklist_version ?? 1) ? (
                        <Badge tone="neutral">v{runReport.meta?.checklist_version ?? 1} 结论</Badge>
                      ) : null}
                    </>
                  )
                ) : run.goal_id && run.status === "completed" ? (
                  <span className="text-zinc-400">验收中…</span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-zinc-400"><CircleAlert className="size-3" />尚无验收报告</span>
                )}
                <span className="ml-auto text-zinc-400">{datetime(run.created_at)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
