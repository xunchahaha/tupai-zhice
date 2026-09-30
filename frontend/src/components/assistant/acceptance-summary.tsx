import { Target } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { type SolverRunResponse } from "@/api/generated/models";
import { GoalSupplementPanel } from "@/components/goal/goal-supplement-panel";
import { type AssistantTask } from "@/components/assistant/use-assistant-task";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { goalChecklistVersion, goalNeedsParams, historicalReportNote, parseGoalReport, reportChecklistVersion, reportStanding } from "@/lib/goal";

/**
 * 要求落实情况的一句话摘要 + 缺口与建议下一步（直接展示，不折叠）。
 * 三态可见（MEM-D2/D6）：报告未就绪 →「核对中」；核对执行失败 → amber「核对失败」；报告就绪 → 结论。
 * 结论只在报告对应目标**当前**清单版本时才是结论：要求修订后旧报告只作历史留档（「历史 v1 已通过；当前 v2 尚待核对」），
 * 还没读到任务、无法比较版本时也不预设它是当前结论。
 * 无法验证（unverifiable）不算通过，缺口里单独点出。逐项报告全文在「查看详情」里。
 *
 * 缺口的补救动作直接办：加大时间预算重跑（一次性）、补充条件（行内补齐条件）、修正范围（展开手动排课并定位范围区）；
 * await_admin 是人的裁决，保持文本、无按钮。
 */
export function AcceptanceSummary({ run, task }: { run: SolverRunResponse; task: AssistantTask }) {
  const [fixing, setFixing] = useState(false);
  const report = parseGoalReport(run.goal_report);
  const standing = report ? reportStanding(report, task.goal) : null;
  // 报告比已读到的任务还新：两份数据没同步，先刷新任务（每个「报告版本 / 任务版本」组合只刷新一次）。
  const refetchedFor = useRef("");
  const { goal: taskGoal, retryGoal } = task;
  const staleKey = report && taskGoal ? `${reportChecklistVersion(report)}/${goalChecklistVersion(taskGoal)}` : "";
  useEffect(() => {
    if (standing !== "unknown" || !staleKey || refetchedFor.current === staleKey) return;
    refetchedFor.current = staleKey;
    retryGoal();
  }, [retryGoal, staleKey, standing]);
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
  // 核对失败本身不是正面结论，读不到任务时照样如实展示；但旧版本的失败也只是历史，交给下面的历史分支。
  if (report.acceptance_status === "failed" && standing !== "historical") {
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
  if (standing !== "current") {
    return (
      <section aria-label="要求核对" className="mt-4 rounded-md border border-blue-200 bg-blue-50/40 px-4 py-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Target className="size-4 text-blue-600" />
          <span className="text-xs font-medium text-zinc-600">要求核对</span>
          {standing === "historical" && task.goal ? (
            <Badge tone="blue">等待新验收（v{goalChecklistVersion(task.goal)}）</Badge>
          ) : (
            <Badge tone="neutral">{task.goalLoadFailed ? "暂时无法确认" : "正在确认…"}</Badge>
          )}
        </div>
        <p className="mt-1.5 text-xs leading-5 text-zinc-600">
          {standing === "historical" && task.goal
            ? `${historicalReportNote(report, task.goal)}。要求修订后需要重新排课，按新版要求出具当前结论；旧版结论不适用于新版要求。`
            : task.goalLoadFailed
              ? "没能读到这个任务的当前要求，无法确认这份核对结论对应哪一版要求；刷新后再看。"
              : "正在确认这份核对结论对应的是哪一版要求。"}
        </p>
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
