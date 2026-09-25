import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, ChevronUp, CircleAlert, Flag, Target } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import {
  getGetGoalApiV1GoalsGoalIdGetQueryKey,
  useAbandonGoalApiV1GoalsGoalIdAbandonPost,
  useGetGoalApiV1GoalsGoalIdGet,
  useListClassGroupsApiV1ClassGroupsGet,
  useListGoalsApiV1GoalsGet,
  useListRoomsApiV1RoomsGet,
  useListTeachersApiV1TeachersGet,
  useListTimeSlotsApiV1TimeSlotsGet,
  useReplaceGoalChecklistApiV1GoalsGoalIdChecklistPatch,
} from "@/api/generated/client";
import {
  type ClassGroupResponse,
  type GoalChecklistItem,
  type GoalDetailResponse,
  type GoalResponse,
  type RoomResponse,
  type TeacherResponse,
  type TimeSlotResponse,
} from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { Select } from "@/components/ui/select";
import { asArray, datetime, errorMessage, formatSlot } from "@/lib/format";
import { goalAcceptanceLabel, goalKindLabel, goalStatusLabel, GOAL_STATUS_TONE, isBottomLineItem, parseGoalReport } from "@/lib/goal";
import { modelStatusLabel, statusLabel } from "@/lib/labels";
import { modelStatusTone, statusTone } from "@/lib/status";

/** 生成模型里 checklist 项是宽松结构，这里收敛出本页要读的字段。 */
interface ChecklistEntry {
  key?: string;
  kind?: string;
  requirement?: string;
  params?: Record<string, unknown>;
  [extra: string]: unknown;
}

/** checklist_history 快照（MEM-D3）：后端存 dict，这里收敛出展示字段。 */
interface ChecklistHistorySnapshot {
  version?: number;
  saved_at?: string;
  saved_by?: string | null;
  items?: ChecklistEntry[];
}

/** MEM-E2/E2a：PATCH 响应带的旧结论标注（latest_run 报告所属清单版本）。 */
interface LatestReportMeta {
  run_id?: string;
  checklist_version?: number;
  is_current_version?: boolean;
  note?: string;
}

const GOAL_SUBJECT_TYPE_OPTIONS: Array<{ value: "teacher" | "cohort" | "classroom"; label: string }> = [
  { value: "teacher", label: "教师" },
  { value: "cohort", label: "班级" },
  { value: "classroom", label: "教室" },
];

function goalSubjectTypeLabel(value: string): string {
  return GOAL_SUBJECT_TYPE_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

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
      <GoalDetailDialog goalId={openId} onClose={() => setOpenId("")} onAbandon={(goal) => setAbandonTarget(goal)} />
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

function GoalDetailDialog({
  goalId,
  onClose,
  onAbandon,
}: {
  goalId: string;
  onClose: () => void;
  onAbandon: (goal: GoalDetailResponse) => void;
}) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const teachers = useListTeachersApiV1TeachersGet();
  const classes = useListClassGroupsApiV1ClassGroupsGet();
  const rooms = useListRoomsApiV1RoomsGet();
  const slots = useListTimeSlotsApiV1TimeSlotsGet();
  const detail = useGetGoalApiV1GoalsGoalIdGet(goalId, { query: { enabled: Boolean(goalId) } });
  const goal = detail.data as GoalDetailResponse | undefined;
  const report = parseGoalReport(goal?.latest_report);
  // MEM-D3（A7/A4）：历史版本与补参表单的展开状态；切换目标时复位。
  const [showHistory, setShowHistory] = useState(false);
  const [paramFormKeys, setParamFormKeys] = useState<string[]>([]);
  useEffect(() => {
    setShowHistory(false);
    setParamFormKeys([]);
  }, [goalId]);
  const replaceChecklist = useReplaceGoalChecklistApiV1GoalsGoalIdChecklistPatch({
    mutation: {
      onSuccess: (updated) => {
        toast.success(`验收清单已更新至 v${updated.checklist_version}`);
        void client.invalidateQueries({ queryKey: getGetGoalApiV1GoalsGoalIdGetQueryKey(goalId) });
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  if (!goal) {
    return (
      <Dialog open={Boolean(goalId)} onOpenChange={(open) => { if (!open) onClose(); }}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogTitle className="text-base font-semibold">目标详情</DialogTitle>
          <DialogDescription className="mt-1 text-sm text-zinc-500">
            原始指令与逐项验收结论；验收由代码按清单执行，不信任求解器自报。
          </DialogDescription>
          <p className="mt-4 text-sm text-zinc-500">正在加载目标…</p>
          <div className="mt-5 flex justify-end">
            <Button variant="outline" onClick={onClose}>关闭</Button>
          </div>
        </DialogContent>
      </Dialog>
    );
  }
  const entries = (goal.checklist ?? []) as ChecklistEntry[];
  const checklistHistory = (goal.checklist_history ?? []) as ChecklistHistorySnapshot[];
  // MEM-D3（A4）：禁排占位项（needs_params）可以补齐参数后重新参与验收。
  const needParamItems = entries.filter(
    (entry) => entry.kind === "forbidden_slot_free" && Boolean(entry.params?.needs_params),
  );
  const toggleParamForm = (key: string) =>
    setParamFormKeys((keys) => (keys.includes(key) ? keys.filter((item) => item !== key) : [...keys, key]));
  /** 量化禁排占位项：重建整份清单走 PATCH 端点（服务端复验 key/底线并记版本）。 */
  const quantizeForbidden = (entry: ChecklistEntry, subjectType: string, subjectIds: string[], slotBusinessIds: string[]) => {
    const updated: GoalChecklistItem = {
      key: String(entry.key),
      kind: "forbidden_slot_free",
      requirement: `${goalSubjectTypeLabel(subjectType)} ${subjectIds.join("、")} 不占用指定时段（${slotBusinessIds.join("、")}）——独立复核，不信任求解器自报`,
      params: {
        subject_type: subjectType,
        subject_ids: subjectIds,
        slot_business_ids: slotBusinessIds,
      },
    };
    replaceChecklist.mutate({
      goalId: goal.id,
      data: { checklist: entries.map((item) => (String(item.key) === String(entry.key) ? updated : (item as unknown as GoalChecklistItem))) },
    });
    setParamFormKeys((keys) => keys.filter((key) => key !== String(entry.key)));
  };
  // MEM-D3（A5）：open/awaiting_decision 都给出「继续处理」入口——补救不脱离原目标。
  const continueable = goal.status === "open" || goal.status === "awaiting_decision";
  return (
    <Dialog open={Boolean(goalId)} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogTitle className="text-base font-semibold">目标详情</DialogTitle>
        <DialogDescription className="mt-1 text-sm text-zinc-500">
          原始指令与逐项验收结论；验收由代码按清单执行，不信任求解器自报。
        </DialogDescription>
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
          {continueable ? (
            <div className="rounded-md border border-blue-200 bg-blue-50/50 px-4 py-3">
              <div className="flex items-center gap-1.5 text-xs font-medium text-blue-800">
                <Target className="size-3.5" />继续处理
              </div>
              {/* awaiting_decision 的验收结论完整展示，不截断。 */}
              <p className="mt-1 text-xs leading-5 text-zinc-700">
                {report?.decision?.reason
                  ?? (goal.status === "awaiting_decision"
                    ? "目标存在需要教务放宽或裁决的缺口：先处理对应缺口，再重新求解回灌同一目标。"
                    : "目标尚未达成：存在允许的补救动作（修正范围/补参数/修订清单），处理后重新求解即可。")}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button size="sm" onClick={() => navigate(`/solver?goal=${goal.id}`)}>修正范围后重新求解</Button>
                {needParamItems.length ? (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setParamFormKeys(needParamItems.map((item) => String(item.key ?? "")))}
                  >
                    补齐禁排参数
                  </Button>
                ) : null}
                <Button size="sm" variant="outline" onClick={() => onAbandon(goal)}>放弃目标</Button>
              </div>
            </div>
          ) : null}
          <div>
            <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-zinc-500">
              <span>验收清单 v{goal.checklist_version ?? 1}（底线项不可删除，与附加项并列验收）</span>
              {checklistHistory.length ? (
                <button
                  type="button"
                  aria-expanded={showHistory}
                  className="inline-flex items-center gap-0.5 text-blue-600 transition-colors hover:underline"
                  onClick={() => setShowHistory((value) => !value)}
                >
                  {showHistory ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
                  {showHistory ? "收起历史版本" : `历史版本（${checklistHistory.length}）`}
                </button>
              ) : null}
            </div>
            {showHistory ? (
              <ul className="mt-2 space-y-2">
                {[...checklistHistory].reverse().map((snapshot, snapshotIndex) => (
                  <li key={String(snapshot.version ?? snapshotIndex)} className="rounded border border-zinc-100 bg-zinc-50/60 px-3 py-2">
                    <div className="text-xs text-zinc-500">
                      v{snapshot.version ?? "?"} · 保存于 {snapshot.saved_at ? datetime(snapshot.saved_at) : "—"}
                    </div>
                    <ul className="mt-1 space-y-0.5">
                      {(snapshot.items ?? []).map((item, index) => (
                        <li key={String(item.key ?? index)} className="text-xs leading-5 text-zinc-500">
                          {goalKindLabel(item.kind)} · {item.requirement ?? ""}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            ) : null}
            <ul className="mt-2 space-y-1.5">
              {entries.map((entry, index) => {
                const key = String(entry.key ?? `${entry.kind}-${index}`);
                const needsParams = entry.kind === "forbidden_slot_free" && Boolean(entry.params?.needs_params);
                const paramFormOpen = paramFormKeys.includes(key);
                return (
                  <li key={key} className="rounded border border-zinc-100 px-3 py-2 text-xs leading-5 text-zinc-700">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-medium">{goalKindLabel(entry.kind)}</span>
                      {isBottomLineItem({ params: entry.params }) ? <Badge tone="blue">底线</Badge> : null}
                      <span>· {entry.requirement ?? ""}</span>
                      {needsParams ? (
                        <Button size="sm" variant="outline" aria-expanded={paramFormOpen} onClick={() => toggleParamForm(key)}>
                          补齐参数
                        </Button>
                      ) : null}
                    </div>
                    {needsParams && paramFormOpen ? (
                      <ForbiddenParamForm
                        pending={replaceChecklist.isPending}
                        teacherOptions={asArray<TeacherResponse>(teachers.data)}
                        classOptions={asArray<ClassGroupResponse>(classes.data)}
                        roomOptions={asArray<RoomResponse>(rooms.data)}
                        slotOptions={asArray<TimeSlotResponse>(slots.data)}
                        onCancel={() => toggleParamForm(key)}
                        onSave={(subjectType, subjectIds, slotBusinessIds) => quantizeForbidden(entry, subjectType, subjectIds, slotBusinessIds)}
                      />
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
            {report ? (
              (() => {
                // MEM-E2/E2a：报告绑定清单版本。验收 pending（清单修订后未重新
                // 验收）时，旧报告是「历史版本结论」，不得当成当前口径展示——
                // 当前结论区显示「等待新验收（v{n}）」。
                const goalVersion = Number(goal.checklist_version ?? 1);
                const reportVersion = Number(report.meta?.checklist_version ?? 1);
                const staleReport =
                  goal.acceptance_status === "pending" && reportVersion < goalVersion;
                if (staleReport) {
                  return (
                    <div>
                      <div className="flex items-center gap-2 text-xs font-medium text-zinc-500">
                        当前结论
                        <Badge tone="blue">等待新验收（v{goalVersion}）</Badge>
                      </div>
                      <p className="mt-1 text-xs leading-5 text-zinc-600">
                        清单已修订至 v{goalVersion}，旧验收结论按当时口径保留在下方求解记录中；
                        重新关联求解后按 v{goalVersion} 出具新结论。
                      </p>
                    </div>
                  );
                }
                return (
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
                      {reportVersion < goalVersion ? (
                        <Badge tone="neutral">历史版本 v{reportVersion} 的结论</Badge>
                      ) : null}
                    </div>
                    {report.meta?.version_note ? (
                      <p className="mt-1 text-xs text-zinc-500">{report.meta.version_note}</p>
                    ) : null}
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
                        {/* 07 §5.2：remedy 动作化——raise_budget 跳求解页由挂载 action
                            一键加预算重跑（不重选范围）；fix_checklist 复用清单补参锚点；
                            resolve_scope 回求解页聚焦范围区；await_admin 是人的裁决，
                            保持文本、无按钮。 */}
                        <ul className="list-disc space-y-0.5 pl-4">
                          {report.gaps.map((gap) => (
                            <li key={gap.key} className="flex flex-wrap items-center gap-2">
                              <span>{gap.next_step}</span>
                              {gap.remedy === "raise_budget" ? (
                                <Button size="sm" variant="outline" onClick={() => navigate(`/solver?goal=${goal.id}&action=raise_budget`)}>加大时间预算重跑</Button>
                              ) : null}
                              {gap.remedy === "fix_checklist" && needParamItems.length ? (
                                <Button size="sm" variant="outline" onClick={() => setParamFormKeys(needParamItems.map((item) => String(item.key ?? "")))}>修订目标清单</Button>
                              ) : null}
                              {gap.remedy === "resolve_scope" ? (
                                <Button size="sm" variant="outline" onClick={() => navigate(`/solver?goal=${goal.id}&action=resolve_scope`)}>回求解页修正范围</Button>
                              ) : null}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </div>
                );
              })()
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
          </div>
        <div className="mt-5 flex justify-end">
          <Button variant="outline" onClick={onClose}>关闭</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * 禁排占位项补参表单（MEM-D3/A4）：主体类型+主体下拉+时段多选，保存走
 * PATCH /goals/{id}/checklist 整体替换；参数齐了该项从「恒不通过」恢复参与验收。
 */
function ForbiddenParamForm({
  pending,
  teacherOptions,
  classOptions,
  roomOptions,
  slotOptions,
  onCancel,
  onSave,
}: {
  pending?: boolean;
  teacherOptions: TeacherResponse[];
  classOptions: ClassGroupResponse[];
  roomOptions: RoomResponse[];
  slotOptions: TimeSlotResponse[];
  onCancel: () => void;
  onSave: (subjectType: string, subjectIds: string[], slotBusinessIds: string[]) => void;
}) {
  const [subjectType, setSubjectType] = useState("teacher");
  const [subjectId, setSubjectId] = useState("");
  const [slotIds, setSlotIds] = useState<string[]>([]);
  const subjectOptions = subjectType === "teacher" ? teacherOptions : subjectType === "cohort" ? classOptions : roomOptions;
  const slotLabel = (id: string): string => {
    const slot = slotOptions.find((item) => item.business_id === id);
    return slot ? `${slot.weekday} ${slot.start_time}-${slot.end_time}` : formatSlot(id);
  };
  const valid = Boolean(subjectId) && slotIds.length > 0;
  return (
    <form
      className="mt-2 rounded-md border border-zinc-200 bg-zinc-50/60 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) onSave(subjectType, [subjectId], slotIds);
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-sm text-zinc-700">
          主体类型
          <Select
            aria-label="禁排主体类型"
            selectSize="sm"
            containerClassName="mt-1"
            value={subjectType}
            onChange={(event) => {
              setSubjectType(event.target.value);
              setSubjectId("");
            }}
          >
            {GOAL_SUBJECT_TYPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </Select>
        </label>
        <label className="block text-sm text-zinc-700">
          主体
          <Select
            aria-label="禁排主体"
            selectSize="sm"
            containerClassName="mt-1"
            value={subjectId}
            onChange={(event) => setSubjectId(event.target.value)}
          >
            <option value="">选择{goalSubjectTypeLabel(subjectType)}…</option>
            {subjectOptions.map((item) => (
              <option key={item.business_id} value={item.business_id}>{item.name}</option>
            ))}
          </Select>
        </label>
      </div>
      <div className="mt-3 text-sm text-zinc-700">
        禁排时段（多选）
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {slotIds.map((id) => (
            <span key={id} className="inline-flex items-center gap-1 rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-700">
              {slotLabel(id)}
              <button
                type="button"
                aria-label={`移除 ${slotLabel(id)}`}
                className="text-zinc-400 hover:text-zinc-700"
                onClick={() => setSlotIds((ids) => ids.filter((item) => item !== id))}
              >
                ×
              </button>
            </span>
          ))}
          <Select
            aria-label="添加禁排时段"
            selectSize="sm"
            containerClassName="w-44"
            value=""
            onChange={(event) => {
              if (event.target.value && !slotIds.includes(event.target.value)) {
                setSlotIds((ids) => [...ids, event.target.value]);
              }
            }}
          >
            <option value="">添加时段…</option>
            {slotOptions.filter((item) => !slotIds.includes(item.business_id)).map((item) => (
              <option key={item.business_id} value={item.business_id}>
                {item.weekday} {item.start_time}-{item.end_time}
              </option>
            ))}
          </Select>
        </div>
      </div>
      <div className="mt-3 flex gap-2">
        <Button type="submit" size="sm" disabled={pending || !valid}>
          保存参数
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onCancel} disabled={pending}>
          取消
        </Button>
      </div>
    </form>
  );
}
