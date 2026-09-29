import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useListGoalsApiV1GoalsGet } from "@/api/generated/client";
import { type GoalResponse, type SolverRunResponse } from "@/api/generated/models";
import { AbandonGoalDialog } from "@/components/assistant/abandon-goal-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { truncateText } from "@/lib/assistant-task";
import { asArray, datetime } from "@/lib/format";
import { GOAL_STATUS_TONE, goalProgress, type GoalProgressTone, goalStatusLabel, isActiveGoal, isClosedGoal } from "@/lib/goal";
import { assistantPath } from "@/lib/routes";

const PROGRESS_TEXT: Record<GoalProgressTone, string> = {
  green: "text-emerald-700",
  yellow: "text-amber-700",
  blue: "text-blue-700",
  red: "text-red-700",
  neutral: "text-zinc-500",
};

const ACTIVE_LIMIT = 3;

/**
 * 「正在处理的任务」：默认只列最多 3 条进行中的，每条一句话进展 +「继续处理」；
 * 「查看全部任务」展开含已达成/已放弃的完整列表，并保留「不再跟进」能力。
 * 已放弃的任务后端不再接受新的求解，所以只给只读入口（查看最新结果）；已达成的仍可「继续处理」（继续调整）。
 */
export function HomeTasks({ runs }: { runs: SolverRunResponse[] }) {
  const navigate = useNavigate();
  const goals = useListGoalsApiV1GoalsGet();
  const [showAll, setShowAll] = useState(false);
  const [abandonTarget, setAbandonTarget] = useState<GoalResponse | null>(null);
  const rows = asArray<GoalResponse>(goals.data);
  const byUpdated = (a: GoalResponse, b: GoalResponse) => String(b.updated_at).localeCompare(String(a.updated_at));
  const active = rows.filter((goal) => isActiveGoal(goal.status)).sort(byUpdated);
  const latestRun = (goal: GoalResponse) => (goal.latest_run_id ? runs.find((run) => run.id === goal.latest_run_id) : undefined);
  const progressOf = (goal: GoalResponse) => goalProgress(goal, latestRun(goal));
  const shown = showAll ? [...rows].sort(byUpdated) : active.slice(0, ACTIVE_LIMIT);
  return (
    <section aria-label="正在处理的任务" className="rounded-lg border border-zinc-200 bg-white shadow-2xs">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-100 px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-900">正在处理的任务</h2>
        {rows.length > 0 ? (
          <button
            type="button"
            aria-expanded={showAll}
            className="text-xs text-blue-700 underline-offset-2 hover:underline"
            onClick={() => setShowAll((value) => !value)}
          >
            {showAll ? "只看进行中的任务" : `查看全部任务（${rows.length}）`}
          </button>
        ) : null}
      </div>
      {goals.isPending ? (
        <p className="px-4 py-6 text-sm text-zinc-500">正在读取任务…</p>
      ) : goals.isError ? (
        <div className="flex items-center gap-3 px-4 py-6 text-sm text-red-700">
          任务列表没读出来。
          <Button size="sm" variant="outline" onClick={() => void goals.refetch()}>重试</Button>
        </div>
      ) : shown.length === 0 ? (
        <p className="px-4 py-6 text-sm text-zinc-500">
          {rows.length === 0
            ? "还没有任务。在上面说一句需求，或点「手动排课」，助手会帮你排出草稿。"
            : "没有进行中的任务了；点「查看全部任务」可以回看已完成或已放弃的。"}
        </p>
      ) : (
        <ul className="divide-y divide-zinc-100">
          {shown.map((goal) => {
            const progress = progressOf(goal);
            return (
              <li key={goal.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-zinc-900" title={goal.instruction}>{truncateText(goal.instruction, 60)}</p>
                  <p className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                    {showAll || goal.status === "awaiting_decision" ? <Badge tone={GOAL_STATUS_TONE[goal.status] ?? "blue"}>{goalStatusLabel(goal.status)}</Badge> : null}
                    <span className={PROGRESS_TEXT[progress.tone]}>{progress.text}</span>
                    {showAll ? <span className="text-zinc-400">创建于 {datetime(goal.created_at)} · 求解 {goal.run_count ?? 0} 次</span> : null}
                  </p>
                  {goal.acceptance_status === "failed" && goal.acceptance_detail ? (
                    <p className="mt-1 text-xs text-amber-800">要求核对失败的原因：{goal.acceptance_detail}</p>
                  ) : null}
                </div>
                <div className="flex gap-1.5">
                  {!isClosedGoal(goal.status) ? (
                    <Button size="sm" onClick={() => navigate(assistantPath({ goal: goal.id }))}>继续处理</Button>
                  ) : goal.latest_run_id ? (
                    <Button size="sm" variant="outline" onClick={() => navigate(assistantPath({ run: goal.latest_run_id as string }))}>查看最新结果</Button>
                  ) : null}
                  {showAll && goal.status !== "abandoned" ? (
                    <Button size="sm" variant="outline" onClick={() => setAbandonTarget(goal)}>不再跟进</Button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <AbandonGoalDialog goal={abandonTarget} onClose={() => setAbandonTarget(null)} />
    </section>
  );
}
