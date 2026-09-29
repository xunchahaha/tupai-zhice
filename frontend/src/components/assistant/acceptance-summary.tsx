import { Target } from "lucide-react";
import { useState } from "react";

import { type SolverRunResponse } from "@/api/generated/models";
import { GoalSupplementPanel } from "@/components/goal/goal-supplement-panel";
import { type AssistantTask } from "@/components/assistant/use-assistant-task";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { goalNeedsParams, parseGoalReport } from "@/lib/goal";

/**
 * 要求落实情况的一句话摘要 + 缺口与建议下一步（直接展示，不折叠）。
 * 三态可见（MEM-D2/D6）：报告未就绪 →「核对中」；核对执行失败 → amber「核对失败」；报告就绪 → 结论。
 * 无法验证（unverifiable）不算通过，缺口里单独点出。逐项报告全文在「查看详情」里。
 *
 * 缺口的补救动作直接办：加大时间预算重跑（一次性）、补充条件（行内补齐条件）、修正范围（展开手动排课并定位范围区）；
 * await_admin 是人的裁决，保持文本、无按钮。
 */
export function AcceptanceSummary({ run, task }: { run: SolverRunResponse; task: AssistantTask }) {
  const [fixing, setFixing] = useState(false);
  const report = parseGoalReport(run.goal_report);
  if (!report) {
    if (!(run.goal_id && run.status === "completed")) return null;
    return (
      <section aria-label="要求核对" className="mt-4 rounded-md border border-blue-200 bg-blue-50/40 px-4 py-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Target className="size-4 text-blue-600" />
          <span className="text-xs font-medium text-zinc-600">要求核对</span>
          <Badge tone="blue">核对中…</Badge>
        </div>
        <p className="mt-1.5 text-xs text-zinc-500">求解已完成，正在按你的要求逐项核对，结果稍后出现在这里。</p>
      </section>
    );
  }
  if (report.acceptance_status === "failed") {
    return (
      <section aria-label="要求核对" className="mt-4 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        <div className="flex flex-wrap items-center gap-2">
          <Target className="size-4 text-amber-600" />
          <span className="text-xs font-medium text-zinc-600">要求核对</span>
          <Badge tone="yellow">核对失败</Badge>
        </div>
        <p className="mt-1.5 text-xs leading-5">核对失败：{report.acceptance_error || "核对时发生异常，未能生成报告"}。求解结果本身不受影响；请重试求解或联系管理员。</p>
      </section>
    );
  }
  const openCount = report.failed_count;
  const unverifiable = report.unverifiable_count ?? 0;
  const hasPlaceholder = goalNeedsParams(task.goal);
  return (
    <section aria-label="要求核对" className="mt-4 rounded-md border border-zinc-200 bg-white/70 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Target className="size-4 text-blue-600" />
        <span className="text-xs font-medium text-zinc-600">要求核对</span>
        <Badge tone={report.all_passed ? "green" : "yellow"}>
          {report.all_passed ? `全部 ${report.passed_count} 项要求已落实` : `还有 ${openCount} 项要求没落实`}
        </Badge>
        {unverifiable ? <Badge tone="yellow">{unverifiable} 项暂时无法验证</Badge> : null}
      </div>
      {report.gaps.length ? (
        <div role="status" className="mt-2 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-amber-900">
          <div className="text-xs font-medium">建议的下一步</div>
          <ul className="mt-1 list-disc space-y-1 pl-4 text-xs">
            {report.gaps.map((gap) => (
              <li key={gap.key} className="flex flex-wrap items-center gap-2">
                <span>{gap.next_step}</span>
                {gap.remedy === "raise_budget" ? (
                  <Button size="sm" variant="outline" disabled={task.pending || Boolean(task.raiseBudgetBlockedReason)} title={task.raiseBudgetBlockedReason ?? undefined} onClick={task.raiseBudget}>加大时间预算重跑</Button>
                ) : null}
                {gap.remedy === "resolve_scope" ? (
                  <Button size="sm" variant="outline" onClick={() => task.openManual({ focusScope: true })}>修正范围</Button>
                ) : null}
                {gap.remedy === "fix_checklist" && hasPlaceholder ? (
                  <Button size="sm" variant="outline" aria-expanded={fixing} onClick={() => setFixing((value) => !value)}>补充条件</Button>
                ) : null}
              </li>
            ))}
          </ul>
          {report.decision?.reason ? <p className="mt-1 text-xs text-amber-800">{report.decision.reason}。</p> : null}
        </div>
      ) : null}
      {fixing && task.goal ? (
        <div className="mt-2 rounded-md border border-amber-200 bg-white/70 p-2">
          <GoalSupplementPanel goal={task.goal} autoOpen onSaved={() => setFixing(false)} />
        </div>
      ) : null}
    </section>
  );
}
