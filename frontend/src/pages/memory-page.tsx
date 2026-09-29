import { useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Check, Inbox, Pencil, ShieldCheck, Sparkles, Timer, X } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import {
  getListPreferencesApiV1MemoryPreferencesGetQueryKey,
  useAdjudicateReplacePreferenceApiV1MemoryPreferencesCandidateIdAdjudicateReplacePost,
  useConvertPreferenceToRuleApiV1MemoryPreferencesEntryIdConvertToRulePost,
  useCreateMemoryMiningRunApiV1MemoryMiningRunsPost,
  useListClassGroupsApiV1ClassGroupsGet,
  useListCourseSessionsApiV1CourseSessionsGet,
  useListPreferencesApiV1MemoryPreferencesGet,
  useListRoomsApiV1RoomsGet,
  useListSolverRunsApiV1SolverRunsGet,
  useListTeachersApiV1TeachersGet,
  useListTimeSlotsApiV1TimeSlotsGet,
  useTransitionPreferenceApiV1MemoryPreferencesEntryIdTransitionPost,
  useUpdatePreferenceApiV1MemoryPreferencesEntryIdPatch,
} from "@/api/generated/client";
import {
  type ClassGroupResponse,
  type CourseSessionResponse,
  type PreferenceResponse,
  type PreferenceTransitionRejectionReason,
  type RoomResponse,
  type SolverRunResponse,
  type TeacherResponse,
  type TimeSlotResponse,
} from "@/api/generated/models";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { DataTable } from "@/components/data-table";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { Select } from "@/components/ui/select";
import { asArray, datetime, errorMessage, formatRoom, formatSlot } from "@/lib/format";
import { type MemoryOutcome, memoryOutcomeLabel } from "@/lib/memory-usage";

// 记忆页的谓词/来源/状态文案只在记忆语境下使用，与规则页的 constraint_type
// 空间不同，因此不进全局 labels 以免误伤其他页面的翻译。
const SUBJECT_TYPE_LABELS: Record<string, string> = {
  teacher: "教师",
  classroom: "教室",
  cohort: "班级",
  course: "课程",
};

const PREDICATE_LABELS: Record<string, string> = {
  avoid_slot: "避开时段",
  prefer_slot: "偏好时段",
  avoid_room: "避开教室",
  prefer_room: "偏好教室",
  consecutive_sessions: "连续上课",
  max_daily_load: "日负荷上限",
};

const SOURCE_LABELS: Record<string, string> = {
  induced_from_adjustment: "调课挖掘",
  explicit_stated: "手动录入",
  admin_directive: "行政指令",
};

/**
 * 一句话排课（assistant_interpret）产生的条目：来源按「怎么来的」如实标注，而不是
 * 沿用 source 字段的字面含义——推测候选为复用 hard 升级限制借用了
 * induced_from_adjustment，页面若照写「调课挖掘」，教务会去找并不存在的调课记录。
 */
function isFromAssistant(entry: PreferenceResponse): boolean {
  return (entry.provenance as { via?: unknown } | null | undefined)?.via === "assistant_interpret";
}

function sourceLabel(entry: PreferenceResponse): string {
  if (isFromAssistant(entry)) {
    return entry.source === "explicit_stated" ? "一句话排课（明确声明）" : "一句话排课（推测）";
  }
  return SOURCE_LABELS[entry.source] ?? entry.source;
}

/** 证据行：一句话来源展示原话，其余展示调课次数。 */
function evidenceLabel(entry: PreferenceResponse): string | null {
  if (!entry.evidence.length) return null;
  if (isFromAssistant(entry)) return `原话：${entry.evidence[0]}`;
  return `来自 ${entry.evidence.length} 次调课`;
}

const STATUS_LABELS: Record<string, string> = {
  probation: "试用观察",
  confirmed: "已确认",
  rejected: "已拒绝",
  expired: "已失效",
};

// 拒绝原因五选（MEM-C2 修正 4）：与后端 PreferenceTransition.rejection_reason
// 枚举一一对应；前端必填，API 缺省「其他」。
const REJECTION_REASONS: { value: string; label: string }[] = [
  { value: "temporary_leave", label: "临时请假" },
  { value: "subject_misidentified", label: "主体识别错误" },
  { value: "wrong_generalization", label: "归纳错误" },
  { value: "preference_changed", label: "确实有偏好但已改变" },
  { value: "other", label: "其他" },
];

const REJECTION_REASON_LABELS: Record<string, string> = Object.fromEntries(
  REJECTION_REASONS.map((item) => [item.value, item.label]),
);

interface PreviouslyRejected {
  reason?: string;
  note?: string | null;
  rejected_at?: string;
}

function previouslyRejected(entry: PreferenceResponse): PreviouslyRejected | null {
  const value = (entry.provenance as { previously_rejected?: unknown } | null | undefined)
    ?.previously_rejected;
  return value && typeof value === "object" ? (value as PreviouslyRejected) : null;
}

function conflictWithIds(entry: PreferenceResponse): string[] {
  const value = (entry.provenance as { conflict_with?: unknown } | null | undefined)?.conflict_with;
  return Array.isArray(value) ? value.map(String) : [];
}

/**
 * 活跃冲突对（MEM-D1 proposed_conflict）：标记只落在提出方（组内较新条目）上，
 * 双方 provenance.conflict_with 互记对方 id 仅供定位。据此从任意一侧的卡片
 * 还原出 (提出方=新, 被点名=旧)：被标记方是旧条目而非候选的场景同样适用——
 * 裁决动作作用于整对，而不是当前这一行。对方已离开活跃集（已裁决/已失效）
 * 时返回 null，不出按钮。
 */
function activeConflictPair(
  entry: PreferenceResponse,
  entries: PreferenceResponse[],
): { proposer: PreferenceResponse; previous: PreferenceResponse } | null {
  const counterpartIds = conflictWithIds(entry);
  if (!counterpartIds.length) return null;
  for (const id of counterpartIds) {
    const other = entries.find((item) => item.id === id);
    if (!other || (other.status !== "probation" && other.status !== "confirmed")) continue;
    const proposer = entry.conflict ? entry : other.conflict ? other : null;
    if (!proposer) continue;
    return { proposer, previous: proposer.id === entry.id ? other : entry };
  }
  return null;
}

function predicateLabel(value: string): string {
  return PREDICATE_LABELS[value] ?? value;
}

function prefStatusTone(status: string): BadgeTone {
  if (status === "probation") return "yellow";
  if (status === "confirmed") return "green";
  return "neutral";
}

function arrayValue(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function constraintSummary(
  constraint: Record<string, unknown> | null | undefined,
  slotLabel: (id: string) => string,
): string {
  const parts: string[] = [];
  const slotIds = arrayValue(constraint?.slot_ids);
  if (slotIds.length) parts.push(slotIds.map(slotLabel).join(" · "));
  const roomIds = arrayValue(constraint?.room_ids);
  if (roomIds.length) parts.push(roomIds.map((id) => formatRoom(id)).join(" · "));
  const minimum = constraint?.minimum_consecutive;
  if (typeof minimum === "number" || typeof minimum === "string") parts.push(`连上 ${minimum} 节`);
  const covered = new Set(["slot_ids", "room_ids", "minimum_consecutive"]);
  for (const [key, value] of Object.entries(constraint ?? {})) {
    if (covered.has(key) || value === null || value === undefined || value === "") continue;
    parts.push(`${key}: ${arrayValue(value).length ? arrayValue(value).join("、") : String(value)}`);
  }
  return parts.join("，") || "无附加条件";
}

function validityLabel(entry: PreferenceResponse): string {
  const from = entry.valid_from ?? "";
  const until = entry.valid_until ?? "";
  if (!from && !until) return "有效期未设置";
  return `${from || "…"} ~ ${until || "长期"}`;
}

/** 最新一次求解里各偏好条目的编译结果：runs 按时间倒序，先见者为最新。

第七轮口径收口：outcome 是创建任务时点的编译资格判定（已获准编译/未授权/
过期…），不代表该偏好作用于那次求解的课次，也不代表结果满足——列名与
徽标文案按编译口径表述。 */
function useLatestOutcomes(runs: SolverRunResponse[]): Map<string, MemoryOutcome> {
  return useMemo(() => {
    const map = new Map<string, MemoryOutcome>();
    for (const run of runs) {
      const memory = run.memory_usage as { outcomes?: MemoryOutcome[] } | null;
      for (const item of Array.isArray(memory?.outcomes) ? memory.outcomes : []) {
        if (!map.has(item.entry_id)) map.set(item.entry_id, item);
      }
    }
    return map;
  }, [runs]);
}

function statusOptions(): [string, string][] {
  return [
    ["all", "全部状态"],
    ...Object.entries(STATUS_LABELS),
  ];
}

function subjectOptions(): [string, string][] {
  return [
    ["all", "全部主体"],
    ...Object.entries(SUBJECT_TYPE_LABELS),
  ];
}

export function MemoryPage() {
  const queryClient = useQueryClient();
  const preferences = useListPreferencesApiV1MemoryPreferencesGet();
  const teachers = useListTeachersApiV1TeachersGet();
  const rooms = useListRoomsApiV1RoomsGet();
  const classes = useListClassGroupsApiV1ClassGroupsGet();
  const courses = useListCourseSessionsApiV1CourseSessionsGet();
  const slots = useListTimeSlotsApiV1TimeSlotsGet();
  // 「最近编译结果」反查最近求解任务冻结的逐条编译判定；查不到就不显示，不编造。
  const runs = useListSolverRunsApiV1SolverRunsGet();
  // Hook 顺序敏感：所有 use* 必须在下面的 loading/error 早退之前调用。
  const latestOutcomes = useLatestOutcomes(asArray<SolverRunResponse>(runs.data));

  const [statusFilter, setStatusFilter] = useState("all");
  const [subjectFilter, setSubjectFilter] = useState("all");
  // adjustingId：正在「调整后采纳」的条目；trialingId：正在「授权试用」的条目；
  // rejecting/expiring/converting 是三种需要二次确认的操作。
  const [adjustingId, setAdjustingId] = useState<string | null>(null);
  // trialingId：正在「授权试用」的条目；天数由内联表单 TrialForm 自己持有。
  const [trialingId, setTrialingId] = useState<string | null>(null);
  // rejecting：正在走「拒绝」弹窗的候选；keepOld=true 表示这是冲突裁决里的
  // 「保留旧弃新」（拒绝的是提出方候选，旧条目保持原样），弹窗文案随之切换。
  const [rejecting, setRejecting] = useState<{ entry: PreferenceResponse; keepOld: boolean } | null>(null);
  const [expiring, setExpiring] = useState<PreferenceResponse | null>(null);
  const [converting, setConverting] = useState<PreferenceResponse | null>(null);

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: getListPreferencesApiV1MemoryPreferencesGetQueryKey() });

  const mining = useCreateMemoryMiningRunApiV1MemoryMiningRunsPost({
    mutation: {
      onSuccess: (data) => {
        const count = data.created.length;
        toast.success(count > 0 ? `挖掘出 ${count} 条候选偏好` : "回顾完成：本学期暂无新的候选偏好");
        invalidate();
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  const transition = useTransitionPreferenceApiV1MemoryPreferencesEntryIdTransitionPost({
    mutation: {
      onSuccess: (_data, vars) => {
        invalidate();
        if (vars.data.action === "authorize_trial") {
          setTrialingId(null);
          toast.success(`已授权试用 ${vars.data.trial_days ?? 30} 天，试用期内以小权重参与排课`);
          return;
        }
        toast.success(
          vars.data.target_status === "confirmed"
            ? "已采纳该偏好，下次求解开始生效"
            : vars.data.target_status === "rejected"
              ? "已拒绝该候选，同批证据的同类归纳不再提醒"
              : "偏好已停用",
        );
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  const convert = useConvertPreferenceToRuleApiV1MemoryPreferencesEntryIdConvertToRulePost({
    mutation: {
      onSuccess: () => {
        invalidate();
        setConverting(null);
        toast.success("已转为正式硬规则，原偏好条目归档为已失效");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  // 「以新替旧」原子裁决（MEM-E3）：单个请求由后端在同一事务内完成旧条目退场
  // 与候选转正——前端不再顺序发两个 transition 请求，第二步失败不会再出现
  // 「旧的已退场、新的没生效」且无法恢复的中间态。
  const replace = useAdjudicateReplacePreferenceApiV1MemoryPreferencesCandidateIdAdjudicateReplacePost({
    mutation: {
      onSuccess: (data) => {
        invalidate();
        toast.success(data.detail === "already_applied" ? "该替换此前已生效" : "已替换生效");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  // 调整后采纳 = 先 PATCH 内容，成功后紧接着确认；两步都走同一个失效。
  const update = useUpdatePreferenceApiV1MemoryPreferencesEntryIdPatch({
    mutation: {
      onSuccess: (_data, vars) => {
        setAdjustingId(null);
        transition.mutate({ entryId: vars.entryId, data: { target_status: "confirmed" } });
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });

  // 主体与时段只用于把 business_id 翻译成人名/可读时段，查不到就退回原值，不阻塞页面。
  const nameMaps = useMemo(() => {
    const build = <T extends { business_id: string }>(rows: T[], label: (row: T) => string) =>
      new Map(rows.map((row) => [row.business_id, label(row)] as const));
    return {
      teachers: build(asArray<TeacherResponse>(teachers.data), (row) => row.name),
      rooms: build(asArray<RoomResponse>(rooms.data), (row) => row.name),
      cohorts: build(asArray<ClassGroupResponse>(classes.data), (row) => row.name),
      courses: build(asArray<CourseSessionResponse>(courses.data), (row) => row.lesson_name || row.business_id),
      slots: build(asArray<TimeSlotResponse>(slots.data), (row) => `${row.weekday} ${row.start_time}-${row.end_time}`),
    };
  }, [teachers.data, rooms.data, classes.data, courses.data, slots.data]);

  const subjectName = (entry: PreferenceResponse): string => {
    const id = entry.subject_id;
    if (entry.subject_type === "teacher") return nameMaps.teachers.get(id) ?? id;
    if (entry.subject_type === "classroom") return nameMaps.rooms.get(id) ?? id;
    if (entry.subject_type === "cohort") return nameMaps.cohorts.get(id) ?? id;
    if (entry.subject_type === "course") return nameMaps.courses.get(id) ?? id;
    return id;
  };
  const slotLabel = (id: string): string => nameMaps.slots.get(id) ?? formatSlot(id);
  const summaryOf = (entry: PreferenceResponse): string => constraintSummary(entry.constraint, slotLabel);
  // amber 提示徽标：与旧偏好冲突 / 此前被拒（带新证据重提的候选）。
  const memoryBadges = (entry: PreferenceResponse) => (
    <>
      {entry.conflict ? <Badge tone="yellow">与旧偏好冲突</Badge> : null}
      {previouslyRejected(entry) ? (
        <Badge tone="yellow">
          此前被拒：
          {REJECTION_REASON_LABELS[previouslyRejected(entry)!.reason ?? "other"] ?? "其他"}
        </Badge>
      ) : null}
    </>
  );

  if (preferences.isPending) return <LoadingState />;
  if (preferences.isError) return <ErrorState error={preferences.error} retry={() => void preferences.refetch()} />;

  const entries = asArray<PreferenceResponse>(preferences.data);
  const probation = entries.filter((entry) => entry.status === "probation");

  // 冲突裁决三动作（MEM-D1，语义以 proposed_conflict 为准）：
  // - 保留旧弃新 = 拒绝提出方候选（带 rejection_reason，走既有拒绝弹窗），旧条目不动；
  // - 以新替旧 = 调用原子裁决端点 adjudicate-replace（MEM-E3）：后端在同一事务内
  //   完成旧条目 expired+superseded_by、候选 confirmed+supersedes 与冲突重算，
  //   前端只发一个请求，失败时后端整体回滚、旧条目保持原状；
  // - 授权试用 = 既有 authorize_trial（旧新并存）。
  // 按钮从冲突对的任意一侧（候选卡或被点名的旧条目行）进入都作用于整对。
  const conflictPairOf = (entry: PreferenceResponse) => activeConflictPair(entry, entries);
  const conflictActions = (entry: PreferenceResponse) => {
    const pair = conflictPairOf(entry);
    if (!pair) return null;
    return (
      <>
        <Button size="sm" variant="outline" disabled={replace.isPending} onClick={() => setRejecting({ entry: pair.proposer, keepOld: true })}>
          保留旧弃新
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={replace.isPending}
          onClick={() => replace.mutate({ candidateId: pair.proposer.id, data: {} })}
        >
          {replace.isPending ? "替换中…" : "以新替旧"}
        </Button>
      </>
    );
  };

  const visible = entries.filter(
    (entry) =>
      (statusFilter === "all" || entry.status === statusFilter)
      && (subjectFilter === "all" || entry.subject_type === subjectFilter),
  );

  const columns: ColumnDef<PreferenceResponse>[] = [
    {
      header: "主体",
      accessorFn: (row) => `${SUBJECT_TYPE_LABELS[row.subject_type] ?? row.subject_type} ${subjectName(row)}`,
      cell: ({ row }) => (
        <span className="flex items-center gap-1.5">
          <Badge>{SUBJECT_TYPE_LABELS[row.original.subject_type] ?? row.original.subject_type}</Badge>
          <span className="text-zinc-800">{subjectName(row.original)}</span>
        </span>
      ),
    },
    {
      header: "谓词",
      accessorKey: "predicate",
      cell: ({ row }) => predicateLabel(row.original.predicate),
    },
    {
      header: "约束",
      accessorFn: (row) => summaryOf(row),
    },
    {
      header: "状态",
      accessorKey: "status",
      cell: ({ row }) => (
        <span className="flex flex-wrap items-center gap-1">
          <Badge tone={prefStatusTone(row.original.status)}>{STATUS_LABELS[row.original.status] ?? row.original.status}</Badge>
          {memoryBadges(row.original)}
        </span>
      ),
    },
    { header: "权重", accessorKey: "weight" },
    {
      header: "来源",
      accessorKey: "source",
      cell: ({ row }) => sourceLabel(row.original),
    },
    {
      header: "生效日期范围",
      accessorFn: (row) => validityLabel(row),
    },
    {
      header: "最近编译结果",
      accessorFn: (row) => latestOutcomes.get(row.id)?.outcome ?? "",
      cell: ({ row }) => {
        const usage = latestOutcomes.get(row.original.id);
        if (!usage) return <span className="text-zinc-300">—</span>;
        return (
          <span title={usage.detail ?? undefined}>
            <Badge tone={usage.outcome === "applied" ? "green" : "neutral"}>
              {memoryOutcomeLabel(usage.outcome)}
            </Badge>
          </span>
        );
      },
    },
    {
      header: "更新时间",
      accessorKey: "updated_at",
      cell: ({ row }) => datetime(row.original.updated_at),
    },
    {
      id: "actions",
      header: "操作",
      enableSorting: false,
      cell: ({ row }) =>
        row.original.status === "confirmed" ? (
          <span className="flex gap-2">
            {row.original.modality === "hard" ? (
              <Button size="sm" variant="outline" onClick={() => setConverting(row.original)}>
                转为硬规则
              </Button>
            ) : null}
            <Button size="sm" variant="outline" onClick={() => setExpiring(row.original)}>
              停用
            </Button>
            {conflictActions(row.original)}
          </span>
        ) : (
          conflictActions(row.original)
        ),
    },
  ];

  return (
    <div className="space-y-5 animate-fade-in">
      <PageHeader
        title="记忆与偏好"
        actions={
          <Button size="sm" onClick={() => mining.mutate()} disabled={mining.isPending}>
            <Sparkles className="size-3.5" />
            {mining.isPending ? "挖掘中…" : "回顾本学期调课"}
          </Button>
        }
      >
        <p className="mt-1 text-sm text-zinc-500">
          系统从调课行为中学习偏好；待确认候选在您采纳或授权试用前不会影响排课，授权试用到期自动退出。
        </p>
      </PageHeader>

      {entries.length === 0 ? (
        <div className="grid min-h-48 place-items-center rounded-lg border border-zinc-200 bg-white text-sm text-zinc-400">
          暂无偏好记录；调课几次后系统可以开始学习
        </div>
      ) : (
        <>
          <section className="rounded-lg border border-zinc-200 bg-white shadow-2xs">
            <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3 text-sm font-semibold">
              <Inbox className="size-4 text-amber-500" />
              待确认记忆
              {probation.length ? <Badge tone="yellow">{probation.length}</Badge> : null}
            </div>
            {probation.length ? (
              <div className="divide-y divide-zinc-100">
                {probation.map((entry) => (
                  <div key={entry.id} className="px-4 py-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge>{SUBJECT_TYPE_LABELS[entry.subject_type] ?? entry.subject_type}</Badge>
                      <span className="text-sm font-medium text-zinc-900">{subjectName(entry)}</span>
                      <span className="text-sm text-zinc-600">{predicateLabel(entry.predicate)}</span>
                      <span className="text-sm text-zinc-800">「{summaryOf(entry)}」</span>
                      {memoryBadges(entry)}
                    </div>
                    {typeof entry.provenance?.rationale === "string" && entry.provenance.rationale ? (
                      <p className="mt-1 text-xs text-zinc-500">{entry.provenance.rationale}</p>
                    ) : null}
                    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-zinc-400">
                      <span>来源：{sourceLabel(entry)}</span>
                      <span>置信度 {Math.round(entry.confidence * 100)}%</span>
                      <span>权重 {entry.weight}</span>
                      <span>{validityLabel(entry)}</span>
                      {evidenceLabel(entry) ? <span>{evidenceLabel(entry)}</span> : null}
                    </div>
                    {adjustingId === entry.id ? (
                      <AdjustForm
                        entry={entry}
                        slotOptions={asArray<TimeSlotResponse>(slots.data)}
                        roomOptions={asArray<RoomResponse>(rooms.data)}
                        slotLabel={slotLabel}
                        pending={update.isPending || transition.isPending}
                        onCancel={() => setAdjustingId(null)}
                        onSave={(weight, constraint) => update.mutate({ entryId: entry.id, data: { weight, constraint } })}
                      />
                    ) : trialingId === entry.id ? (
                      <TrialForm
                        pending={transition.isPending}
                        onCancel={() => setTrialingId(null)}
                        onConfirm={(days) => transition.mutate({ entryId: entry.id, data: { action: "authorize_trial", trial_days: days } })}
                      />
                    ) : (
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setTrialingId(entry.id)}
                        >
                          <Timer className="size-3.5" />
                          授权试用
                        </Button>
                        <Button size="sm" onClick={() => transition.mutate({ entryId: entry.id, data: { target_status: "confirmed" } })}>
                          <Check className="size-3.5" />
                          采纳
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setAdjustingId(entry.id)}>
                          <Pencil className="size-3.5" />
                          调整后采纳
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => setRejecting({ entry, keepOld: false })}>
                          <X className="size-3.5" />
                          拒绝
                        </Button>
                        {entry.modality === "hard" ? (
                          <Button size="sm" variant="outline" onClick={() => setConverting(entry)}>
                            <ShieldCheck className="size-3.5" />
                            转为硬规则
                          </Button>
                        ) : null}
                        {conflictActions(entry)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="grid min-h-32 place-items-center px-4 text-sm text-zinc-400">没有待确认的记忆候选</div>
            )}
          </section>

          <section className="rounded-lg border border-zinc-200 bg-white shadow-2xs">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-200 px-4 py-3">
              <div className="text-sm font-semibold">
                全部偏好 <span className="ml-1 text-xs font-normal text-zinc-400">{visible.length}</span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Select
                  aria-label="按状态筛选"
                  selectSize="sm"
                  containerClassName="w-32"
                  value={statusFilter}
                  onChange={(event) => setStatusFilter(event.target.value)}
                >
                  {statusOptions().map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </Select>
                <Select
                  aria-label="按主体筛选"
                  selectSize="sm"
                  containerClassName="w-32"
                  value={subjectFilter}
                  onChange={(event) => setSubjectFilter(event.target.value)}
                >
                  {subjectOptions().map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </Select>
              </div>
            </div>
            <div className="p-4">
              <DataTable
                columns={columns}
                data={visible}
                getRowId={(row) => row.id}
                empty="没有匹配的偏好记录"
              />
            </div>
          </section>
        </>
      )}

      <RejectDialog
        key={rejecting ? `${rejecting.entry.id}-${rejecting.keepOld}` : "none"}
        entry={rejecting?.entry ?? null}
        keepOld={rejecting?.keepOld ?? false}
        subjectLabel={
          rejecting ? `${subjectName(rejecting.entry)} · ${predicateLabel(rejecting.entry.predicate)}` : ""
        }
        pending={transition.isPending}
        onOpenChange={(open) => {
          if (!open) setRejecting(null);
        }}
        onConfirm={(reason, note) => {
          if (!rejecting) return;
          transition.mutate({
            entryId: rejecting.entry.id,
            data: {
              target_status: "rejected",
              rejection_reason: reason as PreferenceTransitionRejectionReason,
              reason: note,
            },
          });
          setRejecting(null);
        }}
      />
      <ConfirmDialog
        open={Boolean(expiring)}
        title="停用这条偏好？"
        description={
          expiring
            ? `「${subjectName(expiring)} · ${predicateLabel(expiring.predicate)}」停用后不再参与排课。过期与停用仅作废不删除，保留审计记录，但不能恢复。`
            : ""
        }
        confirmLabel="确认停用"
        danger
        pending={transition.isPending}
        onOpenChange={(open) => {
          if (!open) setExpiring(null);
        }}
        onConfirm={() => {
          if (!expiring) return;
          transition.mutate({ entryId: expiring.id, data: { target_status: "expired" } });
          setExpiring(null);
        }}
      />
      <ConfirmDialog
        open={Boolean(converting)}
        title="把这条硬偏好转成正式规则？"
        description={
          converting
            ? `「${subjectName(converting)} · ${predicateLabel(converting.predicate)}」将作为硬规则（不得违反）进入规则库并立即生效，原偏好条目转为已失效归档。${
                converting.source === "induced_from_adjustment"
                  ? "注意：该条目来自调课归纳，确认转换即代表教务认可其作为硬约束。"
                  : ""
              }`
            : ""
        }
        confirmLabel="确认转换"
        pending={convert.isPending}
        onOpenChange={(open) => {
          if (!open) setConverting(null);
        }}
        onConfirm={() => {
          if (!converting) return;
          convert.mutate({
            entryId: converting.id,
            data: { confirmed_conversion: converting.source === "induced_from_adjustment" },
          });
        }}
      />
    </div>
  );
}

function ChipRemove({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-700">
      {label}
      <button type="button" aria-label={`移除 ${label}`} onClick={onRemove} className="text-zinc-400 hover:text-zinc-700">
        <X className="size-3" />
      </button>
    </span>
  );
}

/** 「拒绝」弹窗（MEM-C2 修正 4）：原因五选必填 + 选「其他」时显示备注输入框。
 *  提交后后端把拒绝原因落 preference_rejections，同批证据的候选不再复现。
 *  keepOld=true（MEM-D1 冲突裁决「保留旧弃新」）：拒绝的对象是提出方候选，
 *  被点名的旧条目保持原样，文案据此切换。 */
function RejectDialog({
  entry,
  keepOld,
  subjectLabel,
  pending,
  onOpenChange,
  onConfirm,
}: {
  entry: PreferenceResponse | null;
  keepOld?: boolean;
  subjectLabel: string;
  pending?: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (reason: string, note: string | null) => void;
}) {
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  return (
    <Dialog open={Boolean(entry)} onOpenChange={(next) => { if (!pending) onOpenChange(next); }}>
      <DialogContent className="max-w-md">
        <DialogTitle className="text-base font-semibold text-zinc-950">
          {keepOld ? "保留旧偏好，拒绝这条新候选？" : "拒绝这条候选偏好？"}
        </DialogTitle>
        <DialogDescription className="mt-2 text-sm leading-6 text-zinc-500">
          {entry
            ? `「${subjectLabel}」将被标记为已拒绝，不再参与排课；同一批证据的同类归纳不再提醒。${
                keepOld ? "与之冲突的旧偏好保持生效，不受影响。" : ""
              }`
            : ""}
        </DialogDescription>
        <fieldset className="mt-4">
          <legend className="text-sm font-medium text-zinc-700">拒绝原因</legend>
          <div className="mt-2 space-y-1.5">
            {REJECTION_REASONS.map((item) => (
              <label key={item.value} className="flex items-center gap-2 text-sm text-zinc-700">
                <input
                  type="radio"
                  name="rejection-reason"
                  value={item.value}
                  checked={reason === item.value}
                  onChange={() => setReason(item.value)}
                />
                {item.label}
              </label>
            ))}
          </div>
        </fieldset>
        {reason === "other" ? (
          <label className="mt-3 block text-sm text-zinc-700">
            备注
            <textarea
              aria-label="备注"
              rows={2}
              className="mt-1.5 w-full rounded-md border border-zinc-300 bg-white px-2 py-1.5"
              placeholder="补充说明（可选）"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
        ) : null}
        <div className="mt-6 flex justify-end gap-2 border-t border-zinc-100 pt-4">
          <Button variant="outline" disabled={pending} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            variant="danger"
            disabled={pending || !reason}
            onClick={() => onConfirm(reason, reason === "other" && note.trim() ? note.trim() : null)}
          >
            {keepOld ? "拒绝新候选" : "确认拒绝"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** 「授权试用」的内联小表单：只填试用天数，提交后条目保持 probation、小权重参与。 */
function TrialForm({
  pending,
  onCancel,
  onConfirm,
}: {
  pending?: boolean;
  onCancel: () => void;
  onConfirm: (days: number) => void;
}) {
  const [days, setDays] = useState(30);
  return (
    <form
      className="mt-3 rounded-md border border-zinc-200 bg-zinc-50/60 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        onConfirm(Math.min(365, Math.max(1, Math.round(days) || 30)));
      }}
    >
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm text-zinc-700">
          试用天数
          <input
            aria-label="试用天数"
            className="mt-1.5 h-9 w-24 rounded-md border border-zinc-300 bg-white px-2"
            type="number"
            min={1}
            max={365}
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
          />
        </label>
        <p className="max-w-md flex-1 text-xs text-zinc-500">
          试用期内该候选以小权重参与排课（权重的 30%），到期自动退出；点「采纳」后才转为全量权重。候选在采纳或授权试用前不会影响排课。
        </p>
      </div>
      <div className="mt-3 flex gap-2">
        <Button type="submit" size="sm" disabled={pending}>
          <Check className="size-3.5" />
          确认授权
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onCancel} disabled={pending}>
          取消
        </Button>
      </div>
    </form>
  );
}

/** 「调整后采纳」的内联表单：改权重 + 按谓词改约束，保存后由父级 PATCH + 确认。 */
function AdjustForm({
  entry,
  slotOptions,
  roomOptions,
  slotLabel,
  pending,
  onCancel,
  onSave,
}: {
  entry: PreferenceResponse;
  slotOptions: TimeSlotResponse[];
  roomOptions: RoomResponse[];
  slotLabel: (id: string) => string;
  pending?: boolean;
  onCancel: () => void;
  onSave: (weight: number, constraint: Record<string, unknown>) => void;
}) {
  const [weight, setWeight] = useState(entry.weight);
  const [constraint, setConstraint] = useState<Record<string, unknown>>({ ...(entry.constraint ?? {}) });
  const isSlotPredicate = entry.predicate === "avoid_slot" || entry.predicate === "prefer_slot";
  const isRoomPredicate = entry.predicate === "avoid_room" || entry.predicate === "prefer_room";
  const isConsecutive = entry.predicate === "consecutive_sessions";
  const slotIds = arrayValue(constraint.slot_ids);
  const roomIds = arrayValue(constraint.room_ids);
  const roomName = (id: string) => roomOptions.find((item) => item.business_id === id)?.name ?? formatRoom(id);

  const setList = (key: "slot_ids" | "room_ids", next: string[]) =>
    setConstraint((current) => ({ ...current, [key]: next }));

  return (
    <form
      className="mt-3 rounded-md border border-zinc-200 bg-zinc-50/60 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(Math.min(100, Math.max(0, Math.round(weight) || 0)), constraint);
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm text-zinc-700">
          权重
          <input
            aria-label="偏好权重"
            className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2"
            type="number"
            min={0}
            max={100}
            value={weight}
            onChange={(event) => setWeight(Number(event.target.value))}
          />
        </label>
        {isConsecutive ? (
          <label className="text-sm text-zinc-700">
            连上课次
            <input
              aria-label="连上课次"
              className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2"
              type="number"
              min={2}
              value={typeof constraint.minimum_consecutive === "number" ? constraint.minimum_consecutive : ""}
              onChange={(event) =>
                setConstraint((current) => ({
                  ...current,
                  minimum_consecutive: event.target.value === "" ? "" : Number(event.target.value),
                }))
              }
            />
          </label>
        ) : null}
      </div>
      {isSlotPredicate ? (
        <div className="mt-3 text-sm text-zinc-700">
          约束时段
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {slotIds.map((id) => (
              <ChipRemove key={id} label={slotLabel(id)} onRemove={() => setList("slot_ids", slotIds.filter((item) => item !== id))} />
            ))}
            <Select
              aria-label="添加约束时段"
              selectSize="sm"
              containerClassName="w-44"
              value=""
              placeholder="添加时段…"
              onChange={(event) => {
                if (event.target.value && !slotIds.includes(event.target.value)) setList("slot_ids", [...slotIds, event.target.value]);
              }}
            >
              <option value="">添加时段…</option>
              {slotOptions
                .filter((item) => !slotIds.includes(item.business_id))
                .map((item) => (
                  <option key={item.business_id} value={item.business_id}>
                    {item.weekday} {item.start_time}-{item.end_time}
                  </option>
                ))}
            </Select>
          </div>
        </div>
      ) : null}
      {isRoomPredicate ? (
        <div className="mt-3 text-sm text-zinc-700">
          约束教室
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {roomIds.map((id) => (
              <ChipRemove key={id} label={roomName(id)} onRemove={() => setList("room_ids", roomIds.filter((item) => item !== id))} />
            ))}
            <Select
              aria-label="添加约束教室"
              selectSize="sm"
              containerClassName="w-44"
              value=""
              placeholder="添加教室…"
              onChange={(event) => {
                if (event.target.value && !roomIds.includes(event.target.value)) setList("room_ids", [...roomIds, event.target.value]);
              }}
            >
              <option value="">添加教室…</option>
              {roomOptions
                .filter((item) => !roomIds.includes(item.business_id))
                .map((item) => (
                  <option key={item.business_id} value={item.business_id}>
                    {item.business_id} / {item.name}
                  </option>
                ))}
            </Select>
          </div>
        </div>
      ) : null}
      <div className="mt-3 flex gap-2">
        <Button type="submit" size="sm" disabled={pending}>
          <Check className="size-3.5" />
          保存并采纳
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onCancel} disabled={pending}>
          取消
        </Button>
      </div>
    </form>
  );
}
