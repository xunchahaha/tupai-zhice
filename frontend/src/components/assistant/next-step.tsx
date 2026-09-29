import { Loader2, Sparkles } from "lucide-react";
import { type ReactNode } from "react";
import { Link } from "react-router-dom";

import { type GoalDetailResponse, type ScheduleSummaryResponse } from "@/api/generated/models";
import { AiProbeNotice } from "@/components/assistant/ai-probe-notice";
import { ConfirmCard } from "@/components/assistant/confirm-card";
import { InterpretFailure, InterpretProgress } from "@/components/assistant/interpret-progress";
import { ResultCard } from "@/components/assistant/result-card";
import { ScopeExpansionAlert } from "@/components/assistant/scope-expansion-alert";
import { StuckCard } from "@/components/assistant/stuck-card";
import { type AssistantTask } from "@/components/assistant/use-assistant-task";
import { GoalSupplementPanel } from "@/components/goal/goal-supplement-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { goalNeedsParams } from "@/lib/goal";
import { statusLabel } from "@/lib/labels";
import { assistantPath } from "@/lib/routes";
import { scheduleForRun } from "@/lib/schedule";

function SolvingCard({ status }: { status: string }) {
  return (
    <section aria-label="正在排课" className="rounded-lg border border-blue-200 bg-blue-50/40 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Loader2 className="size-4 animate-spin text-blue-600" />
        <h2 className="font-semibold">正在排课…</h2>
        <Badge tone="yellow">{statusLabel(status)}</Badge>
      </div>
      <p className="mt-2 text-sm text-zinc-600">助手正在按你确认的范围和要求排课，通常几十秒内完成。完成后会自动核对要求并生成草稿，不用一直等在这里。</p>
    </section>
  );
}

function UnderstandingCard({ task }: { task: AssistantTask }) {
  return (
    <section aria-label="正在理解需求" className="rounded-lg border border-blue-200 bg-blue-50/40 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles className="size-4 text-blue-600" />
        <h2 className="font-semibold">正在理解你的需求…</h2>
        <Button className="ml-auto" size="sm" variant="outline" onClick={task.cancelInterpret}>取消解析</Button>
      </div>
      <InterpretProgress stageIndex={task.stageIndex} elapsedSeconds={task.elapsedSeconds} liveText={task.liveThinking} />
    </section>
  );
}

/** 续办但还没有新的解析、也没有求解结果：任务已记住，接着重新解析确认，或先补齐缺少的条件。 */
function ResumeCard({ task }: { task: AssistantTask }) {
  const { goal } = task;
  const canParse = task.probe.ready === true && !task.submitBlockedReason;
  const needsParams = goalNeedsParams(goal);
  return (
    <section aria-label="接着办" className="rounded-lg border border-blue-200 bg-blue-50/40 p-5">
      <h2 className="font-semibold">接着办这个任务</h2>
      <p className="mt-1 text-xs text-zinc-600">助手已经记住了这个任务的需求和范围。确认无误后重新解析，再开始求解；也可以先改需求，或用手动排课自己设置参数。</p>
      {!goal ? <p className="mt-3 text-sm text-zinc-500">正在读取任务…</p> : null}
      {goal && needsParams ? (
        <div className="mt-3 border-l-2 border-amber-500 bg-amber-50 p-3 text-xs text-amber-900">
          <p className="font-medium">还差一个条件：补充具体的教师 / 班级 / 教室和时段后，这些要求才能真正参与排课。</p>
          <div className="mt-2 rounded-md border border-amber-200 bg-white/70 p-2">
            <GoalSupplementPanel goal={goal} />
          </div>
        </div>
      ) : null}
      <textarea
        aria-label="任务需求"
        className="mt-3 min-h-24 w-full rounded-md border border-zinc-300 bg-white p-3 text-sm outline-none focus:border-blue-500"
        value={task.instruction}
        onChange={(event) => task.editInstruction(event.target.value)}
      />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button onClick={() => void task.interpret()} disabled={!canParse || task.instruction.trim().length < 2}>
          <Sparkles className="size-4" />让 AI 解析
        </Button>
      </div>
      {task.probe.ready !== true ? (
        <p className="mt-2 text-xs text-amber-800">
          <AiProbeNotice probe={task.probe} missingText="AI 尚未接入，暂时不能解析需求；可以用「手动排课」直接设置参数求解。" />
        </p>
      ) : null}
    </section>
  );
}

/**
 * 已放弃的任务只读回看：后端不再接受它的新求解，所以这里不给任何求解入口，
 * 用一句话说清原因，并提供「以同样需求新建」把原话带回首页重新交代。
 * 已达成的任务不在此列：可以继续调整，后一次验收没过会回退为进行中。
 */
function ClosedGoalNotice({ goal, showLatest }: { goal: GoalDetailResponse; showLatest: boolean }) {
  const linkClass = "text-blue-700 underline-offset-2 hover:underline";
  return (
    <section aria-label="任务已结束" role="status" className="rounded-lg border border-zinc-200 bg-zinc-50 p-4 text-sm text-zinc-700">
      <p>这个任务已放弃，不能继续求解。想再排的话，可以按同样的需求新建一个任务。</p>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <Link className={linkClass} to={assistantPath({ prompt: goal.instruction })}>以同样需求新建</Link>
        {showLatest && goal.latest_run_id ? <Link className={linkClass} to={assistantPath({ run: goal.latest_run_id })}>查看最新结果</Link> : null}
      </div>
    </section>
  );
}

/**
 * 「下一步需要你做什么」：按当前状态出现且只出现一张主卡——
 * 理解中 → 确认卡（含补充问题）→ 进行中 → 结果 / 卡住；续办无结果时是「接着办」。
 * 范围扩大的二次确认与解析失败提示不属于某一张卡，压在主卡上方直接展示。
 * 唯一的例外：待确认的理解还在，期间又走手动路径发起了求解——确认卡和那次求解的进度/结果并列，
 * 手动排课不会让「未落实的要求」和确认卡悄悄消失。
 */
export function NextStep({
  task,
  scheduleList,
  canPublish,
  defaultDiagnosisOpen,
}: {
  task: AssistantTask;
  scheduleList: ScheduleSummaryResponse[];
  canPublish: boolean;
  defaultDiagnosisOpen: boolean;
}) {
  const { activeRun: run, runKind } = task;
  // 候选课表按 solver_run_id 归属：INFEASIBLE / UNKNOWN 之后旧候选不会挂到新任务名下。
  const draft = scheduleForRun(scheduleList, run);
  const runCard = (): ReactNode => {
    if (!run) return null;
    if (runKind === "solving") return <SolvingCard status={run.status} />;
    if (runKind === "result") return <ResultCard task={task} run={run} draft={draft} scheduleList={scheduleList} canPublish={canPublish} />;
    return <StuckCard task={task} run={run} defaultDiagnosisOpen={defaultDiagnosisOpen} />;
  };
  let card: ReactNode;
  if (task.phase === "thinking") {
    card = <UnderstandingCard task={task} />;
  } else if (task.interpretation && !task.confirmed) {
    card = (
      <>
        {task.runSupersedesInterpretation ? runCard() : null}
        <ConfirmCard task={task} scheduleList={scheduleList} />
      </>
    );
  } else if (run) {
    card = runCard();
  } else if (task.goalLoadFailed || task.runLoadFailed) {
    card = (
      <section role="alert" className="rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-800">
        <p>{task.goalLoadFailed ? `关联任务失败：${task.goalLoadError}` : "没能读取这次求解记录，可能已被清理或链接有误。"}</p>
        <Button className="mt-2" size="sm" variant="outline" onClick={task.goalLoadFailed ? task.retryGoal : task.retryRun}>重试</Button>
      </section>
    );
  } else if (task.runId || (task.goalId && !task.goal)) {
    card = <p className="rounded-lg border border-zinc-200 bg-white p-5 text-sm text-zinc-500">正在读取任务与排课结果…</p>;
  } else if (task.goalClosed) {
    card = null; // 由上方的「任务已结束」说明承担
  } else {
    card = <ResumeCard task={task} />;
  }
  return (
    <div className="space-y-3">
      {task.goalClosed && task.goal ? <ClosedGoalNotice goal={task.goal} showLatest={!run} /> : null}
      {task.scopeExpansion ? (
        <ScopeExpansionAlert
          expansion={task.scopeExpansion}
          onConfirm={task.confirmScopeExpansion}
          onRevert={task.revertScopeExpansion}
        />
      ) : null}
      {task.phase === "failed" ? (
        <InterpretFailure
          message={task.interpretError}
          elapsedSeconds={task.elapsedSeconds}
          canRetry={task.probe.ready === true && !task.submitBlockedReason}
          onRetry={task.retryInterpret}
        />
      ) : null}
      {card}
    </div>
  );
}
