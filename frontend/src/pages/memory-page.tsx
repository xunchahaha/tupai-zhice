import { useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Check, Inbox, Pencil, ShieldCheck, Sparkles, Timer, X } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import {
  getListPreferencesApiV1MemoryPreferencesGetQueryKey,
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
  type RoomResponse,
  type SolverRunResponse,
  type TeacherResponse,
  type TimeSlotResponse,
} from "@/api/generated/models";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { DataTable } from "@/components/data-table";
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

const STATUS_LABELS: Record<string, string> = {
  probation: "试用观察",
  confirmed: "已确认",
  rejected: "已拒绝",
  expired: "已失效",
};

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

/** 最新一次求解里各偏好条目的使用结果：runs 按时间倒序，先见者为最新。 */
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
  // 「最近使用」反查最近求解任务冻结的逐条结果；查不到就不显示，不编造。
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
  const [rejecting, setRejecting] = useState<PreferenceResponse | null>(null);
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
              ? "已拒绝该候选，同类归纳将被降权"
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

  if (preferences.isPending) return <LoadingState />;
  if (preferences.isError) return <ErrorState error={preferences.error} retry={() => void preferences.refetch()} />;

  const entries = asArray<PreferenceResponse>(preferences.data);
  const probation = entries.filter((entry) => entry.status === "probation");
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
        <Badge tone={prefStatusTone(row.original.status)}>{STATUS_LABELS[row.original.status] ?? row.original.status}</Badge>
      ),
    },
    { header: "权重", accessorKey: "weight" },
    {
      header: "来源",
      accessorKey: "source",
      cell: ({ row }) => SOURCE_LABELS[row.original.source] ?? row.original.source,
    },
    {
      header: "生效日期范围",
      accessorFn: (row) => validityLabel(row),
    },
    {
      header: "最近使用",
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
          </span>
        ) : null,
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
                    </div>
                    {typeof entry.provenance?.rationale === "string" && entry.provenance.rationale ? (
                      <p className="mt-1 text-xs text-zinc-500">{entry.provenance.rationale}</p>
                    ) : null}
                    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-zinc-400">
                      <span>来源：{SOURCE_LABELS[entry.source] ?? entry.source}</span>
                      <span>置信度 {Math.round(entry.confidence * 100)}%</span>
                      <span>权重 {entry.weight}</span>
                      <span>{validityLabel(entry)}</span>
                      {entry.evidence.length ? <span>来自 {entry.evidence.length} 次调课</span> : null}
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
                        <Button size="sm" variant="outline" onClick={() => setRejecting(entry)}>
                          <X className="size-3.5" />
                          拒绝
                        </Button>
                        {entry.modality === "hard" ? (
                          <Button size="sm" variant="outline" onClick={() => setConverting(entry)}>
                            <ShieldCheck className="size-3.5" />
                            转为硬规则
                          </Button>
                        ) : null}
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

      <ConfirmDialog
        open={Boolean(rejecting)}
        title="拒绝这条候选偏好？"
        description={
          rejecting
            ? `「${subjectName(rejecting)} · ${predicateLabel(rejecting.predicate)}」将被标记为已拒绝，不再参与排课；系统会把同类归纳降权。`
            : ""
        }
        confirmLabel="确认拒绝"
        danger
        pending={transition.isPending}
        onOpenChange={(open) => {
          if (!open) setRejecting(null);
        }}
        onConfirm={() => {
          if (!rejecting) return;
          transition.mutate({ entryId: rejecting.id, data: { target_status: "rejected" } });
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
