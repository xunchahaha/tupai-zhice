import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, Flag } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
  getGetGoalApiV1GoalsGoalIdGetQueryKey,
  useAbandonGoalApiV1GoalsGoalIdAbandonPost,
  useGetGoalApiV1GoalsGoalIdGet,
  useListGoalsApiV1GoalsGet,
} from "@/api/generated/client";
import { type GoalDetailResponse, type GoalResponse } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { asArray, datetime, errorMessage } from "@/lib/format";
import { goalAcceptanceLabel, goalKindLabel, goalStatusLabel, GOAL_STATUS_TONE, isBottomLineItem, parseGoalReport } from "@/lib/goal";
import { modelStatusLabel, statusLabel } from "@/lib/labels";
import { modelStatusTone, statusTone } from "@/lib/status";

/**
 * 目标跟踪页（MEM-C3）：一句话目标 ≠ 一次求解任务。这里列出每个持久目标的
 * 状态、最新验收结论与关联求解记录；缺口与允许的下一步以验收器落库的报告为准。
 */
export function GoalsPage() {
  const client = useQueryClient();
  const goals = useListGoalsApiV1GoalsGet();
  const [openId, setOpenId] = useState("");
  const [abandonTarget, setAbandonTarget] = useState<GoalResponse | null>(null);
  const abandon = useAbandonGoalApiV1GoalsGoalIdAbandonPost({
    mutation: {
      onSuccess: () => {
        toast.success("目标已放弃");
        setAbandonTarget(null);
        void client.invalidateQueries({ queryKey: getGetGoalApiV1GoalsGoalIdGetQueryKey(openId) });
        void client.invalidateQueries();
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  if (goals.isPending) return <LoadingState />;
  if (goals.isError) return <ErrorState error={goals.error} retry={() => void goals.refetch()} />;
  const rows = asArray<GoalResponse>(goals.data);
  return (
    <div className="space-y-5 animate-fade-in">
      <PageHeader title="目标跟踪">
        <p className="mt-1 text-sm text-zinc-500">
          求解结束不代表目标完成：每条一句话目标会拆成可逐项验收的清单，求解后由代码验收器出报告；发布永远等待有权限的人。
        </p>
      </PageHeader>
      {rows.length === 0 ? (
        <div className="grid min-h-48 place-items-center rounded-lg border border-zinc-200 bg-white text-sm text-zinc-400">
          还没有跟踪中的目标；在「排课求解」确认一句话排课并勾选「以此为目标跟踪」即可创建
        </div>
      ) : (
        <section className="overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-2xs">
          <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3 text-sm font-semibold">
            <Flag className="size-4 text-blue-600" /> 跟踪中的目标
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="bg-zinc-50 text-xs text-zinc-500">
                <tr>
                  <th className="h-9 px-4 font-medium">原始指令</th>
                  <th className="font-medium">状态</th>
                  <th className="font-medium">验收项</th>
                  <th className="font-medium">关联求解</th>
                  <th className="font-medium">创建时间</th>
                  <th className="font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((goal) => {
                  const passed = goal.checklist.length
                    ? `${goal.checklist.length} 项`
                    : "—";
                  return (
                    <tr key={goal.id} className="border-t border-zinc-100 transition-colors hover:bg-zinc-50/60">
                      <td className="max-w-[320px] px-4 py-2.5">
                        <div className="truncate text-zinc-800" title={goal.instruction}>{goal.instruction}</div>
                      </td>
                      <td><Badge tone={GOAL_STATUS_TONE[goal.status] ?? "blue"}>{goalStatusLabel(goal.status)}</Badge></td>
                      <td className="text-zinc-600">{passed}</td>
                      <td className="tabular-nums text-zinc-600">{goal.run_count} 次</td>
                      <td className="text-xs text-zinc-500">{datetime(goal.created_at)}</td>
                      <td>
                        <div className="flex gap-1.5">
                          <Button size="sm" variant="outline" onClick={() => setOpenId(goal.id)}>详情</Button>
                          {goal.status !== "abandoned" ? (
                            <Button size="sm" variant="outline" onClick={() => setAbandonTarget(goal)}>放弃</Button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <GoalDetailDialog goalId={openId} onClose={() => setOpenId("")} />
      <ConfirmDialog
        open={abandonTarget !== null}
        title="放弃这个目标？"
        description={`放弃后不再对「${abandonTarget?.instruction ?? ""}」做自动验收，且不能再次关联新的求解任务；历史验收报告保留可查。`}
        confirmLabel="放弃目标"
        danger
        pending={abandon.isPending}
        onOpenChange={(open) => { if (!open) setAbandonTarget(null); }}
        onConfirm={() => { if (abandonTarget) abandon.mutate({ goalId: abandonTarget.id }); }}
      />
    </div>
  );
}

function GoalDetailDialog({ goalId, onClose }: { goalId: string; onClose: () => void }) {
  const detail = useGetGoalApiV1GoalsGoalIdGet(goalId, { query: { enabled: Boolean(goalId) } });
  const goal = detail.data as GoalDetailResponse | undefined;
  const report = parseGoalReport(goal?.latest_report);
  return (
    <Dialog open={Boolean(goalId)} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogTitle className="text-base font-semibold">目标详情</DialogTitle>
        <DialogDescription className="mt-1 text-sm text-zinc-500">
          原始指令与逐项验收结论；验收由代码按清单执行，不信任求解器自报。
        </DialogDescription>
        {detail.isPending || !goal ? (
          <p className="mt-4 text-sm text-zinc-500">正在加载目标…</p>
        ) : (
          <div className="mt-4 space-y-4 text-sm">
            <div className="rounded-md border border-zinc-200 bg-zinc-50/60 px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={GOAL_STATUS_TONE[goal.status] ?? "blue"}>{goalStatusLabel(goal.status)}</Badge>
                <Badge tone={goal.acceptance_status === "failed" ? "yellow" : goal.acceptance_status === "completed" ? "green" : "blue"}>
                  {goalAcceptanceLabel(goal.acceptance_status)}
                </Badge>
                <span className="text-xs text-zinc-500">创建于 {datetime(goal.created_at)}</span>
              </div>
              <p className="mt-2 leading-6 text-zinc-800">{goal.instruction}</p>
              {goal.acceptance_status === "failed" && goal.acceptance_detail ? (
                <p className="mt-2 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs text-amber-900">验收失败：{goal.acceptance_detail}</p>
              ) : null}
            </div>
            <div>
              <div className="text-xs font-medium text-zinc-500">验收清单（底线项不可删除，与附加项并列验收）</div>
              <ul className="mt-2 space-y-1.5">
                {goal.checklist.map((item) => {
                  const entry = item as { key?: string; kind?: string; requirement?: string; params?: Record<string, unknown> };
                  return (
                    <li key={String(entry.key ?? entry.kind)} className="rounded border border-zinc-100 px-3 py-2 text-xs leading-5 text-zinc-700">
                      <span className="font-medium">{goalKindLabel(entry.kind)}</span>
                      {isBottomLineItem({ params: entry.params }) ? <Badge tone="blue">底线</Badge> : null}
                      {" "}· {entry.requirement ?? ""}
                    </li>
                  );
                })}
              </ul>
            </div>
            {report ? (
              <div>
                <div className="flex items-center gap-2 text-xs font-medium text-zinc-500">
                  最新验收
                  {report.acceptance_status === "failed" ? (
                    <Badge tone="yellow">验收失败</Badge>
                  ) : (
                    <Badge tone={report.all_passed ? "green" : "yellow"}>
                      {report.all_passed ? `全部 ${report.passed_count} 项通过` : `${report.failed_count} 项缺口`}
                    </Badge>
                  )}
                </div>
                {report.acceptance_status === "failed" ? (
                  <p className="mt-1 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                    验收失败：{report.acceptance_error ?? "验收器执行时发生异常，未能生成报告"}
                  </p>
                ) : null}
                <ul className="mt-2 space-y-1 text-xs">
                  {report.items.map((item) => (
                    <li key={item.key} className="flex gap-2">
                      <span aria-hidden className={item.passed ? "text-emerald-600" : item.verdict === "unverifiable" ? "text-amber-600" : "text-red-600"}>{item.passed ? "✓" : item.verdict === "unverifiable" ? "?" : "✗"}</span>
                      <span className="text-zinc-700">
                        {goalKindLabel(item.kind)}
                        {item.bottom_line ? <Badge tone="blue">底线</Badge> : null}
                        {item.verdict === "unverifiable" ? <Badge tone="yellow">无法验证</Badge> : null}
                        ——{item.detail}
                      </span>
                    </li>
                  ))}
                </ul>
                {report.gaps.length ? (
                  <div className="mt-2 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                    <ul className="list-disc space-y-0.5 pl-4">
                      {report.gaps.map((gap) => <li key={gap.key}>{gap.next_step}</li>)}
                    </ul>
                  </div>
                ) : null}
              </div>
            ) : null}
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
                            <span className={runReport.all_passed ? "text-emerald-700" : "text-amber-700"}>
                              验收 {runReport.passed_count}/{runReport.items.length} 项通过
                            </span>
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
          </div>
        )}
        <div className="mt-5 flex justify-end">
          <Button variant="outline" onClick={onClose}>关闭</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
