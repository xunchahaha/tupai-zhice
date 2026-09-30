import { CalendarDays, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet,
  useGetGoalApiV1GoalsGoalIdGet,
  useGetSolverRunApiV1SolverRunsRunIdGet,
  useListSchedulesApiV1SchedulesGet,
} from "@/api/generated/client";
import { type ScheduleSummaryResponse } from "@/api/generated/models";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import { asArray, datetime } from "@/lib/format";
import { goalChecklistVersion, parseGoalReport, reportChecklistVersion, requirementsNote } from "@/lib/goal";
import { schedulePath } from "@/lib/routes";
import { usePublishSchedule } from "@/lib/use-publish-schedule";

/** 首页默认只列最近几份草稿：多次重试会留下几十份，全部展开会把首页重新变成长列表。 */
const DRAFT_LIMIT = 3;

const PUBLISH_CONSEQUENCE = "发布后成为当前课表，并同步到已启用的外部集成；不会自动下发日历。";

/**
 * 发布确认框里的事实：这份草稿相对当前已发布版本改了多少节课、它关联的求解还有几项要求没落实。
 * 审批的人不必先钻进历史版本才知道发布的分量；读不到就如实说没有，不编造。
 * 「已落实」只在报告明确通过且对应任务当前版本的要求时才说；核对失败、旧版本结论、读不到任务都不能写成落实。
 * 只在确认框打开时挂载，列表本身不为每一行发这两个请求。
 */
function PublishConfirm({
  draft,
  published,
  pending,
  onCancel,
  onConfirm,
}: {
  draft: ScheduleSummaryResponse;
  published: ScheduleSummaryResponse | undefined;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const diff = useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet(
    published?.id ?? "",
    draft.id,
    { query: { enabled: Boolean(published) } },
  );
  const runId = draft.solver_run_id ?? "";
  const run = useGetSolverRunApiV1SolverRunsRunIdGet(runId, { query: { enabled: Boolean(runId) } });
  const report = parseGoalReport(run.data?.goal_report);
  // 报告要和任务当前的清单版本比对，才知道它是不是当前要求的结论。
  const goalId = run.data?.goal_id || report?.goal_id || "";
  const goal = useGetGoalApiV1GoalsGoalIdGet(goalId, { query: { enabled: Boolean(goalId) } });

  // 报告比已读到的任务还新：先刷新任务再判断（每个组合只刷新一次）。
  const refetchedFor = useRef("");
  const staleKey = report && goal.data && reportChecklistVersion(report) > goalChecklistVersion(goal.data) ? `${reportChecklistVersion(report)}/${goalChecklistVersion(goal.data)}` : "";
  const refetchGoal = goal.refetch;
  useEffect(() => {
    if (!staleKey || refetchedFor.current === staleKey) return;
    refetchedFor.current = staleKey;
    void refetchGoal();
  }, [refetchGoal, staleKey]);

  let changes: string;
  if (!published) changes = "暂无对比基准（当前还没有已发布的版本）。";
  else if (diff.data) changes = `相对当前已发布的 v${published.version_no}，这份草稿调整了 ${diff.data.changed_count} 节课。`;
  else if (diff.isError) changes = "暂无对比基准（调整明细没读出来，可以先到「课表」里查看版本对比）。";
  else changes = "正在对比当前已发布的版本……";

  let requirements: string;
  if (!runId) requirements = "这份草稿不是由助手任务生成的，没有要求核对记录。";
  else if (report) {
    // 核对失败不依赖任务版本；其余结论要等任务读到才能判断是不是当前要求。
    if (report.acceptance_status !== "failed" && goalId && goal.isPending) requirements = "正在核对要求落实情况……";
    else requirements = requirementsNote(report, goal.data);
  } else if (run.isError) requirements = "要求落实情况暂时没读出来。";
  else if (run.data) requirements = goalId ? "要求核对还没有结果，尚不能确认要求已落实。" : "这份草稿没有要求核对记录。";
  else requirements = "正在读取要求落实情况……";

  return (
    <ConfirmDialog
      open
      title={`发布草稿 v${draft.version_no}？`}
      description={`${PUBLISH_CONSEQUENCE}${changes}${requirements}`}
      confirmLabel="确认发布"
      pending={pending}
      onOpenChange={(open) => { if (!open) onCancel(); }}
      onConfirm={onConfirm}
    />
  );
}

/**
 * 「待发布的草稿」：只给有发布权限的人看。发布是独立、明确的一步——先看课表，再二次确认；
 * 发布后成为当前课表并同步到已启用的外部集成，不会自动下发日历。
 */
export function PendingDrafts() {
  const navigate = useNavigate();
  const schedules = useListSchedulesApiV1SchedulesGet();
  const [target, setTarget] = useState<ScheduleSummaryResponse | null>(null);
  const [showAll, setShowAll] = useState(false);
  const publish = usePublishSchedule({ onPublished: () => setTarget(null) });
  const all = asArray<ScheduleSummaryResponse>(schedules.data);
  const drafts = all.filter((item) => item.status === "draft").sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  const shown = showAll ? drafts : drafts.slice(0, DRAFT_LIMIT);
  const published = all.find((item) => item.status === "published");
  if (!drafts.length) return null;
  return (
    <section aria-label="待发布的草稿" className="rounded-lg border border-amber-200 bg-amber-50/40 shadow-2xs">
      <div className="border-b border-amber-100 px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-900">待发布的草稿（{drafts.length}）</h2>
        <p className="mt-0.5 text-xs text-zinc-500">排课员生成的草稿在这里等你审批；先看课表，确认无误再发布。</p>
      </div>
      <ul className="divide-y divide-amber-100">
        {shown.map((item) => (
          <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-zinc-900">v{item.version_no} {item.name}</p>
              <p className="mt-0.5 text-xs text-zinc-500">生成于 {datetime(item.created_at)} · {item.assignment_count ?? 0} 条排课记录</p>
            </div>
            <div className="flex gap-1.5">
              <Button size="sm" variant="outline" onClick={() => navigate(schedulePath({ version: item.id, view: "schedule" }))}>
                <CalendarDays className="size-3.5" />查看课表
              </Button>
              <Button size="sm" onClick={() => setTarget(item)}>
                <Send className="size-3.5" />审批发布
              </Button>
            </div>
          </li>
        ))}
      </ul>
      {drafts.length > DRAFT_LIMIT ? (
        <div className="border-t border-amber-100 px-4 py-2">
          <button
            type="button"
            aria-expanded={showAll}
            className="text-xs text-blue-700 underline-offset-2 hover:underline"
            onClick={() => setShowAll((value) => !value)}
          >
            {showAll ? `只看最近 ${DRAFT_LIMIT} 份` : `查看全部草稿（${drafts.length}）`}
          </button>
        </div>
      ) : null}
      {target ? (
        <PublishConfirm
          draft={target}
          published={published}
          pending={publish.isPending}
          onCancel={() => setTarget(null)}
          onConfirm={() => publish.mutate({ scheduleId: target.id })}
        />
      ) : null}
    </section>
  );
}
