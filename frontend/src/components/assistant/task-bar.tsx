import { ArrowLeft, Check } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { AbandonGoalDialog } from "@/components/assistant/abandon-goal-dialog";
import { type AssistantTask } from "@/components/assistant/use-assistant-task";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { type TaskStep } from "@/lib/assistant-task";
import { GOAL_STATUS_TONE, goalStatusLabel, isActiveGoal } from "@/lib/goal";
import { assistantPath } from "@/lib/routes";

/**
 * 任务条：返回首页 · 你交代了什么 · 现在办到哪里（进展步骤条）。
 * 当前步 = 下一件要办的事；没能排出来的那一步标成琥珀色，后面的步骤保持灰显。
 */
export function TaskBar({ task, steps }: { task: AssistantTask; steps: TaskStep[] }) {
  const navigate = useNavigate();
  const [abandonOpen, setAbandonOpen] = useState(false);
  const { goal } = task;
  const requirement = goal?.instruction || task.originalInstruction || task.interpretation?.instruction || "";
  return (
    <section aria-label="任务进展" className="rounded-lg border border-zinc-200 bg-white p-4 shadow-2xs">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link
          to={assistantPath()}
          className="inline-flex items-center gap-1 text-xs text-blue-700 underline-offset-2 hover:underline"
          onClick={() => task.resetTask()}
        >
          <ArrowLeft className="size-3.5" />返回排课助手首页
        </Link>
        <div className="flex items-center gap-2">
          {goal ? <Badge tone={GOAL_STATUS_TONE[goal.status] ?? "blue"}>{goalStatusLabel(goal.status)}</Badge> : null}
          {goal && goal.status !== "abandoned" ? (
            <button
              type="button"
              className="text-xs text-zinc-500 underline-offset-2 transition-colors hover:text-red-700 hover:underline"
              onClick={() => setAbandonOpen(true)}
            >
              {isActiveGoal(goal.status) ? "不再跟进这个任务" : "不再跟进"}
            </button>
          ) : null}
        </div>
      </div>
      <div className="mt-3">
        <div className="text-xs text-zinc-400">你交代的需求</div>
        {requirement ? (
          <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-zinc-900" title={requirement}>{requirement}</p>
        ) : (
          <p className="mt-1 text-sm text-zinc-500">{task.goalParam || task.goalId ? "正在读取任务…" : "手动排课：按你设置的参数直接求解，没有 AI 解析的需求。"}</p>
        )}
      </div>
      {goal?.acceptance_status === "failed" && goal.acceptance_detail ? (
        <p role="status" className="mt-3 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs text-amber-900">要求核对失败的原因：{goal.acceptance_detail}</p>
      ) : null}
      <ol aria-label="办理进展" className="mt-4 flex items-center gap-1.5 overflow-x-auto whitespace-nowrap text-xs">
        {steps.map((step, index) => (
          <li key={step.key} className="flex items-center gap-1.5" aria-current={step.state === "current" ? "step" : undefined}>
            {index > 0 ? <span aria-hidden className="h-px w-5 shrink-0 border-t border-zinc-200 sm:w-8" /> : null}
            <span
              className={cn(
                "flex shrink-0 items-center gap-1.5",
                step.state === "current" && "font-medium text-blue-700",
                step.state === "done" && "text-blue-600",
                step.state === "blocked" && "font-medium text-amber-700",
                step.state === "todo" && "text-zinc-400",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "grid size-5 shrink-0 place-items-center rounded-full border tabular-nums",
                  step.state === "current" && "border-blue-600 bg-blue-600 font-semibold text-white",
                  step.state === "done" && "border-blue-600 bg-white text-blue-600",
                  step.state === "blocked" && "border-amber-500 bg-amber-500 font-semibold text-white",
                  step.state === "todo" && "border-zinc-300 bg-white text-zinc-400",
                )}
              >
                {step.state === "done" ? <Check className="size-3" /> : index + 1}
              </span>
              {step.label}
            </span>
          </li>
        ))}
      </ol>
      {goal ? (
        <AbandonGoalDialog
          goal={abandonOpen ? goal : null}
          onClose={() => setAbandonOpen(false)}
          // 放弃的正是本页绑定的任务：解除绑定并回首页，不能带着已结束的任务继续求解。
          onAbandoned={() => { task.resetTask(); navigate(assistantPath()); }}
        />
      ) : null}
    </section>
  );
}
