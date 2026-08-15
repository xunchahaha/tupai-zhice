import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Check, FileText, Pencil, Plus, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  getListRulesApiV1RulesGetQueryKey,
  useCreateRuleApiV1RulesPost,
  useListClassGroupsApiV1ClassGroupsGet,
  useListConstraintCatalogApiV1RulesConstraintCatalogGet,
  useListCourseSessionsApiV1CourseSessionsGet,
  useListRoomsApiV1RoomsGet,
  useListRulesApiV1RulesGet,
  useListTeachersApiV1TeachersGet,
  useListTimeSlotsApiV1TimeSlotsGet,
  useTransitionRuleApiV1RulesRuleIdTransitionPost,
  useUpdateRuleApiV1RulesRuleIdPut,
} from "@/api/generated/client";
import { type ConstraintCatalogEntry, type ConstraintScopeField, type RuleResponse } from "@/api/generated/models";
import { isReadOnlyMember, useAppUser } from "@/app/user-context";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { actorTypeLabel, constraintLabel, hardnessLabel, statusLabel } from "@/lib/labels";
import { errorMessage } from "@/lib/format";
import { statusTone } from "@/lib/status";

type Hardness = "hard" | "soft";
type ScopeValue = string | string[] | number;
type ScopeState = Record<string, ScopeValue>;

interface ConstraintDraft {
  constraint_type: string;
  actor_type: string;
  actor_ids: string[];
  scope: ScopeState;
}

interface PickerOption {
  value: string;
  label: string;
  hint?: string;
}

const ACTOR_TYPES: { value: string; label: string }[] = [
  { value: "system", label: "全局（所有课次）" },
  { value: "teacher", label: "教师" },
  { value: "class", label: "班级" },
  { value: "room", label: "教室" },
  { value: "course", label: "课程场次" },
];

const SOLVER_PATH_LABELS: Record<string, string> = {
  date: "日期感知路径（郑州真实数据走这条）",
  slot: "时段矩阵路径（演示数据走这条）",
};

const emptyDraft: ConstraintDraft = {
  constraint_type: "declared_constraint",
  actor_type: "system",
  actor_ids: [],
  scope: {},
};

function isEmptyScopeValue(value: ScopeValue | undefined): boolean {
  if (value === undefined || value === null || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

function cleanScope(scope: ScopeState): Record<string, unknown> {
  return Object.fromEntries(Object.entries(scope).filter(([, value]) => !isEmptyScopeValue(value)));
}

/** 目录里 scope 的语义是「任意一个字段有值即算完整」，与后端校验保持一致。 */
function scopeIsComplete(entry: ConstraintCatalogEntry | undefined, scope: ScopeState): boolean {
  const required = entry?.scope ?? [];
  if (!required.length) return true;
  return required.some((key) => !isEmptyScopeValue(scope[key]));
}

function toScopeState(raw: unknown): ScopeState {
  if (!raw || typeof raw !== "object") return {};
  const result: ScopeState = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (Array.isArray(value)) result[key] = value.map((item) => String(item));
    else if (typeof value === "number") result[key] = value;
    else if (value !== null && value !== undefined) result[key] = String(value);
  }
  return result;
}

const editSchema = z.object({
  source_text: z.string().min(2, "请输入至少 2 个字的规则"),
  hardness: z.enum(["hard", "soft"]),
  weight: z.number().int("权重必须是整数").positive("权重必须大于 0").optional(),
});
const intakeSchema = z.object({
  source_text: z.string().min(2, "请输入至少 2 个字的规则"),
  source_doc: z.string().optional(),
  hardness: z.enum(["hard", "soft"]),
  weight: z.number().int("权重必须是整数").positive("权重必须大于 0"),
});
type EditValues = z.infer<typeof editSchema>;
type IntakeValues = z.infer<typeof intakeSchema>;

export function RulesPage() {
  const user = useAppUser();
  const readOnly = isReadOnlyMember(user);
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<RuleResponse | null>(null);
  const [editing, setEditing] = useState<RuleResponse | null>(null);
  const rules = useListRulesApiV1RulesGet();
  const catalog = useListConstraintCatalogApiV1RulesConstraintCatalogGet();
  const entries = useMemo(() => catalog.data ?? [], [catalog.data]);
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: getListRulesApiV1RulesGetQueryKey() });
  const transition = useTransitionRuleApiV1RulesRuleIdTransitionPost({
    mutation: {
      onSuccess: () => { invalidate(); toast.success("规则状态已更新"); },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  const create = useCreateRuleApiV1RulesPost({
    mutation: {
      onSuccess: () => { invalidate(); toast.success("规则已录入，等待确认"); },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  const visible = useMemo(
    () => (rules.data ?? []).filter((rule) => filter === "all" || rule.status === filter),
    [rules.data, filter],
  );
  if (rules.isPending) return <LoadingState />;
  if (rules.isError) return <ErrorState retry={() => void rules.refetch()} />;
  return <div className="space-y-5">
    <PageHeader
      title="规则工作台"
      actions={<div className="inline-flex h-8 items-center rounded-md border border-zinc-200 bg-white p-0.5">
        {[["all", "全部"], ["awaiting_confirmation", "待确认"], ["active", "已生效"]].map(([value, label]) => <button key={value} className={`h-6 rounded px-2 text-xs ${filter === value ? "bg-zinc-900 text-white" : "text-zinc-500 hover:bg-zinc-100"}`} onClick={() => setFilter(value)}>{label}</button>)}
      </div>}
    />
    {readOnly ? <section className="border border-zinc-200 bg-zinc-50 px-4 py-3 text-sm text-zinc-600">当前账号为成员，只能查看规则及其生效状态。</section> : <RuleIntakeForm create={create} entries={entries} />}
    <div className="grid gap-2 xl:grid-cols-[minmax(340px,0.9fr)_minmax(0,1.6fr)]">
      <section className="border border-zinc-200 bg-white">
        <div className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold">候选与正式规则 <span className="ml-1 text-xs font-normal text-zinc-400">{visible.length}</span></div>
        <div className="max-h-[calc(100vh-360px)] overflow-y-auto">{visible.map((rule) => <button key={rule.id} onClick={() => setSelected(rule)} className={`block w-full border-b border-zinc-100 px-4 py-3 text-left hover:bg-zinc-50 ${selected?.id === rule.id ? "bg-blue-50/60" : ""}`}>
          <div className="flex items-center justify-between gap-2"><span className="font-mono text-xs text-zinc-500">{rule.business_id}</span><Badge tone={statusTone(rule.status)}>{statusLabel(rule.status)}</Badge></div>
          <p className="mt-1 line-clamp-2 text-sm text-zinc-800">{rule.source_text}</p>
          <div className="mt-2 flex gap-2 text-xs text-zinc-400"><span>{entryLabel(entries, rule.constraint_type)}</span><span>{hardnessLabel(rule.hardness)}{rule.hardness === "soft" ? `，权重 ${rule.weight ?? "-"}` : ""}</span></div>
        </button>)}</div>
      </section>
      <RuleDetail readOnly={readOnly} rule={selected} entries={entries} onEdit={setEditing} onTransition={(rule, status) => transition.mutate({ ruleId: rule.id, data: { status } })} />
    </div>
    {readOnly ? null : <RuleEditor rule={editing} entries={entries} close={() => setEditing(null)} afterSave={() => { setEditing(null); invalidate(); }} />}
  </div>;
}

function entryLabel(entries: ConstraintCatalogEntry[], type?: string | null): string {
  if (!type) return "未设置";
  return entries.find((item) => item.type === type)?.label ?? constraintLabel(type);
}

function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return <span className="mt-1 block text-xs text-red-600" role="alert">{message}</span>;
}

function EntityPicker({ options, value, multiple, loading, emptyHint, onChange }: {
  options: PickerOption[];
  value: string[];
  multiple: boolean;
  loading?: boolean;
  emptyHint?: string;
  onChange: (next: string[]) => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matched = needle
      ? options.filter((item) => `${item.value} ${item.label} ${item.hint ?? ""}`.toLowerCase().includes(needle))
      : options;
    return matched.slice(0, 200);
  }, [options, query]);
  const toggle = (candidate: string) => {
    if (!multiple) { onChange(value[0] === candidate ? [] : [candidate]); return; }
    onChange(value.includes(candidate) ? value.filter((item) => item !== candidate) : [...value, candidate]);
  };
  const labelFor = (candidate: string) => options.find((item) => item.value === candidate)?.label ?? candidate;
  return (
    <div className="mt-1.5 rounded-md border border-zinc-300 bg-white">
      {value.length ? (
        <div className="flex flex-wrap gap-1 border-b border-zinc-200 p-1.5">
          {value.map((item) => (
            <span key={item} className="inline-flex items-center gap-1 rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-700">
              {labelFor(item)}
              <button type="button" aria-label={`移除 ${labelFor(item)}`} onClick={() => toggle(item)} className="text-zinc-400 hover:text-zinc-700"><X className="size-3" /></button>
            </span>
          ))}
        </div>
      ) : null}
      <div className="flex items-center gap-1.5 border-b border-zinc-200 px-2">
        <Search className="size-3.5 shrink-0 text-zinc-400" />
        <input className="h-8 w-full bg-transparent text-sm outline-none" placeholder={multiple ? "搜索后勾选，可多选" : "搜索后选择一项"} value={query} onChange={(event) => setQuery(event.target.value)} />
      </div>
      <div className="max-h-40 overflow-y-auto">
        {loading ? <p className="px-2.5 py-2 text-xs text-zinc-400">加载中…</p> : null}
        {!loading && !filtered.length ? <p className="px-2.5 py-2 text-xs text-zinc-400">{emptyHint ?? "没有匹配项"}</p> : null}
        {filtered.map((item) => (
          <button
            key={item.value}
            type="button"
            onClick={() => toggle(item.value)}
            className={`flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left text-sm hover:bg-zinc-50 ${value.includes(item.value) ? "bg-blue-50/70" : ""}`}
          >
            <span className="truncate">{item.label}{item.hint ? <span className="ml-1.5 text-xs text-zinc-400">{item.hint}</span> : null}</span>
            {value.includes(item.value) ? <Check className="size-3.5 shrink-0 text-blue-600" /> : null}
          </button>
        ))}
        {options.length > filtered.length ? <p className="px-2.5 py-1.5 text-xs text-zinc-400">仅显示前 200 项，请继续输入以缩小范围。</p> : null}
      </div>
    </div>
  );
}

function SolverEffectNotice({ entry, hardness }: { entry?: ConstraintCatalogEntry; hardness: Hardness }) {
  if (!entry) return null;
  const paths = entry.solver_paths?.[hardness] ?? [];
  const weightHint = hardness === "soft" && entry.soft_weight_hint ? (
    <p className="border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs text-amber-900">{entry.soft_weight_hint}</p>
  ) : null;
  if (!paths.length) {
    return (
      <p className="border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs text-amber-900">
        「{entry.label}」在{hardnessLabel(hardness)}下不进入任何求解模型，只会登记留痕。要让规则真正影响排课，请改选下面带有生效路径的类型。
      </p>
    );
  }
  const missing = (["date", "slot"] as const).filter((path) => !paths.includes(path));
  return (
    <div className="grid gap-2">
      <p className="border-l-2 border-emerald-600 bg-emerald-50 px-3 py-2 text-xs text-emerald-900">
        生效于：{paths.map((path) => SOLVER_PATH_LABELS[path] ?? path).join("、")}。
        {missing.length ? `在${missing.map((path) => SOLVER_PATH_LABELS[path] ?? path).join("、")}下不生效。` : ""}
      </p>
      {weightHint}
    </div>
  );
}

function ConstraintFields({ entries, draft, hardness, onChange }: {
  entries: ConstraintCatalogEntry[];
  draft: ConstraintDraft;
  hardness: Hardness;
  onChange: (next: ConstraintDraft) => void;
}) {
  const entry = entries.find((item) => item.type === draft.constraint_type);
  const scopeFields = entry?.scope_fields ?? [];
  const needsSlots = scopeFields.some((field) => field.kind === "slot");
  const needsRooms = scopeFields.some((field) => field.kind === "room") || draft.actor_type === "room";
  const teachers = useListTeachersApiV1TeachersGet({ query: { enabled: draft.actor_type === "teacher" } });
  const classes = useListClassGroupsApiV1ClassGroupsGet({ query: { enabled: draft.actor_type === "class" } });
  const courses = useListCourseSessionsApiV1CourseSessionsGet({ query: { enabled: draft.actor_type === "course" } });
  const rooms = useListRoomsApiV1RoomsGet({ query: { enabled: needsRooms } });
  const slots = useListTimeSlotsApiV1TimeSlotsGet({ query: { enabled: needsSlots } });

  const roomOptions: PickerOption[] = useMemo(
    () => (rooms.data ?? []).map((item) => ({ value: item.business_id, label: item.name, hint: item.is_active === false ? "已停用" : undefined })),
    [rooms.data],
  );
  const slotOptions: PickerOption[] = useMemo(
    () => (slots.data ?? []).map((item) => ({ value: item.business_id, label: `${item.weekday} ${item.start_time}-${item.end_time}`, hint: item.is_open === false ? "未开放" : item.kind })),
    [slots.data],
  );
  const actorOptions: PickerOption[] = useMemo(() => {
    if (draft.actor_type === "teacher") return (teachers.data ?? []).map((item) => ({ value: item.business_id, label: item.name, hint: item.subject }));
    // 班型是多值（走班制下一个班同时有含数学/无数学），提示行里拼串显示。
    if (draft.actor_type === "class") return (classes.data ?? []).map((item) => ({ value: item.business_id, label: item.name, hint: item.product_types.join(" / ") }));
    if (draft.actor_type === "room") return roomOptions;
    if (draft.actor_type === "course") return (courses.data ?? []).map((item) => ({ value: item.business_id, label: item.lesson_name || item.business_id, hint: [item.class_business_id, item.lesson_date].filter(Boolean).join(" · ") }));
    return [];
  }, [draft.actor_type, teachers.data, classes.data, courses.data, roomOptions]);
  const actorLoading = (draft.actor_type === "teacher" && teachers.isPending) || (draft.actor_type === "class" && classes.isPending) || (draft.actor_type === "course" && courses.isPending) || (draft.actor_type === "room" && rooms.isPending);

  const setScopeValue = (name: string, value: ScopeValue) => onChange({ ...draft, scope: { ...draft.scope, [name]: value } });
  const renderScopeField = (field: ConstraintScopeField) => {
    const current = draft.scope[field.name];
    if (field.kind === "date") {
      return (
        <label key={field.name} className="text-sm text-zinc-700">
          {field.label}
          <input type="date" className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2" value={typeof current === "string" ? current : ""} onChange={(event) => setScopeValue(field.name, event.target.value)} />
        </label>
      );
    }
    if (field.kind === "integer") {
      return (
        <label key={field.name} className="text-sm text-zinc-700">
          {field.label}
          <input type="number" min={field.minimum ?? 0} className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2" value={typeof current === "number" || typeof current === "string" ? String(current) : ""} onChange={(event) => setScopeValue(field.name, event.target.value === "" ? "" : Number(event.target.value))} />
        </label>
      );
    }
    const options = field.kind === "slot" ? slotOptions : roomOptions;
    const loading = field.kind === "slot" ? slots.isPending : rooms.isPending;
    const value = Array.isArray(current) ? current : current === undefined || current === "" ? [] : [String(current)];
    return (
      <div key={field.name} className="text-sm text-zinc-700 sm:col-span-2">
        {field.label}{field.multiple ? "（可多选）" : ""}
        <EntityPicker
          options={options}
          value={value}
          multiple={Boolean(field.multiple)}
          loading={loading}
          emptyHint={field.kind === "slot" ? "还没有时段数据" : "还没有教室数据"}
          onChange={(next) => setScopeValue(field.name, field.multiple ? next : (next[0] ?? ""))}
        />
      </div>
    );
  };

  const selectable = entries.filter((item) => !item.alias_of);
  return (
    <div className="grid gap-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm text-zinc-700">
          约束类型
          <select
            className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2"
            value={draft.constraint_type}
            onChange={(event) => onChange({ ...draft, constraint_type: event.target.value, scope: {} })}
          >
            {selectable.map((item) => <option key={item.type} value={item.type}>{item.label}</option>)}
            {entry?.alias_of ? <option value={entry.type}>{entry.label}</option> : null}
          </select>
        </label>
        <label className="text-sm text-zinc-700">
          作用对象
          <select
            className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2"
            value={draft.actor_type}
            onChange={(event) => onChange({ ...draft, actor_type: event.target.value, actor_ids: [] })}
          >
            {ACTOR_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
      </div>
      {entry?.description ? <p className="text-xs text-zinc-500">{entry.description}</p> : null}
      <SolverEffectNotice entry={entry} hardness={hardness} />
      {draft.actor_type === "system" ? null : (
        <div className="text-sm text-zinc-700">
          限定到具体{actorTypeLabel(draft.actor_type)}
          <EntityPicker
            options={actorOptions}
            value={draft.actor_ids}
            multiple
            loading={actorLoading}
            emptyHint="没有可选实体，请先在基础数据里维护"
            onChange={(next) => onChange({ ...draft, actor_ids: next })}
          />
          {draft.actor_ids.length ? null : <p className="mt-1 text-xs text-amber-700">未选择具体对象时，这条规则会作用于全部课次。</p>}
        </div>
      )}
      {scopeFields.length ? <div className="grid gap-3 sm:grid-cols-2">{scopeFields.map(renderScopeField)}</div> : null}
    </div>
  );
}

function RuleIntakeForm({ create, entries }: { create: ReturnType<typeof useCreateRuleApiV1RulesPost>; entries: ConstraintCatalogEntry[] }) {
  const form = useForm<IntakeValues>({ resolver: zodResolver(intakeSchema), defaultValues: { hardness: "soft", weight: 10, source_doc: "管理端录入" } });
  const errors = form.formState.errors;
  const hardness = form.watch("hardness");
  const [draft, setDraft] = useState<ConstraintDraft>(emptyDraft);
  const [scopeError, setScopeError] = useState<string | null>(null);
  const entry = entries.find((item) => item.type === draft.constraint_type);
  const allowedHardness = useMemo<Hardness[]>(() => entry?.hardness ?? ["hard", "soft"], [entry]);
  // 目录决定该类型允许的硬软属性，切换类型后要把不合法的取值拨回来，否则提交必然 422。
  useEffect(() => {
    if (!allowedHardness.includes(hardness)) form.setValue("hardness", allowedHardness[0] as Hardness);
  }, [allowedHardness, hardness, form]);
  return (
    <section className="border border-blue-200 bg-blue-50/40 p-5">
      <div className="flex items-center gap-2">
        <Plus className="size-4 text-blue-600" />
        <h2 className="font-semibold">输入规则</h2>
        <Badge tone="blue">先确认后求解</Badge>
      </div>
      <p className="mt-2 text-sm text-zinc-600">
        可直接输入自然语言规则。人工录入和 Aily 提交的规则都会先进入“待确认”，确认后才参与排课。
      </p>
      <form
        className="mt-4 grid gap-4"
        onSubmit={form.handleSubmit((value) => {
          if (!scopeIsComplete(entry, draft.scope)) {
            setScopeError(`「${entry?.label ?? draft.constraint_type}」需要填写：${(entry?.scope ?? []).join(" 或 ")}`);
            return;
          }
          setScopeError(null);
          create.mutate({
            data: {
              source_text: value.source_text,
              actor_type: draft.actor_type,
              actor_ids: draft.actor_ids,
              constraint_type: draft.constraint_type,
              scope: cleanScope(draft.scope),
              hardness: value.hardness,
              weight: value.hardness === "soft" ? value.weight : null,
              structured_expression: { natural_language: value.source_text },
              source_doc: value.source_doc || "管理端录入",
              confidence: 1,
            },
          });
        })}
      >
        <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,1fr)_180px_180px]">
          <label className="text-sm text-zinc-700">
            规则原文
            <textarea
              className="mt-1.5 min-h-20 w-full rounded-md border border-zinc-300 bg-white p-2.5"
              placeholder="例如：教师甲周三不排晚课"
              aria-invalid={Boolean(errors.source_text)}
              {...form.register("source_text")}
            />
            <FieldError message={errors.source_text?.message} />
          </label>
          <label className="text-sm text-zinc-700">
            约束级别
            <select className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2" {...form.register("hardness")}>
              {allowedHardness.includes("soft") ? <option value="soft">软约束</option> : null}
              {allowedHardness.includes("hard") ? <option value="hard">硬约束</option> : null}
            </select>
          </label>
          <label className="text-sm text-zinc-700">
            软约束权重
            <input
              className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2 disabled:bg-zinc-100 disabled:text-zinc-400"
              type="number"
              min="1"
              disabled={hardness === "hard"}
              aria-invalid={Boolean(errors.weight)}
              {...form.register("weight", { valueAsNumber: true })}
            />
            <FieldError message={hardness === "soft" ? errors.weight?.message : undefined} />
          </label>
        </div>
        <ConstraintFields entries={entries} draft={draft} hardness={hardness} onChange={(next) => { setDraft(next); setScopeError(null); }} />
        <FieldError message={scopeError ?? undefined} />
        <div>
          <Button type="submit" disabled={create.isPending || !entries.length}>
            <Plus className="size-3.5" />
            提交待确认
          </Button>
        </div>
      </form>
    </section>
  );
}

function ScopeSummary({ entry, scope }: { entry?: ConstraintCatalogEntry; scope?: unknown }) {
  const state = toScopeState(scope);
  const keys = Object.keys(state);
  if (!keys.length) return <span className="text-zinc-400">无</span>;
  const labels = new Map((entry?.scope_fields ?? []).map((field) => [field.name, field.label]));
  return <>{keys.map((key) => {
    const value = state[key];
    return <div key={key}>{labels.get(key) ?? key}：{Array.isArray(value) ? value.join("、") : String(value)}</div>;
  })}</>;
}

function RuleDetail({ readOnly, rule, entries, onEdit, onTransition }: { readOnly: boolean; rule: RuleResponse | null; entries: ConstraintCatalogEntry[]; onEdit: (rule: RuleResponse) => void; onTransition: (rule: RuleResponse, status: "active" | "rejected") => void }) {
  if (!rule) return <section className="grid min-h-72 place-items-center border border-zinc-200 bg-white text-sm text-zinc-400">选择一条规则查看详情</section>;
  const entry = entries.find((item) => item.type === rule.constraint_type);
  return <section className="border border-zinc-200 bg-white"><div className="flex items-center justify-between border-b border-zinc-200 px-5 py-4"><div><div className="font-mono text-xs text-zinc-400">{rule.business_id} / 第 {rule.version} 版</div><h2 className="mt-1 text-base font-semibold">{entryLabel(entries, rule.constraint_type)}</h2></div><Badge tone={statusTone(rule.status)}>{statusLabel(rule.status)}</Badge></div><div className="grid gap-5 p-5"><div><div className="mb-1 text-xs text-zinc-400">原始表达</div><p className="leading-6 text-zinc-800">{rule.source_text}</p></div><SolverEffectNotice entry={entry} hardness={rule.hardness as Hardness} /><div className="grid gap-4 sm:grid-cols-2"><Field label="作用对象" value={`${actorTypeLabel(rule.actor_type)}：${(rule.actor_ids ?? []).join("、") || "全局"}`} /><Field label="约束级别" value={`${hardnessLabel(rule.hardness)}${rule.hardness === "soft" ? `，权重 ${rule.weight ?? "-"}` : ""}`} /><div><div className="text-xs text-zinc-400">范围</div><div className="mt-1 break-words text-sm text-zinc-700"><ScopeSummary entry={entry} scope={rule.scope} /></div></div><Field label="来源" value={rule.source_doc ?? "-"} /></div>{!readOnly || rule.source_doc ? <div className="flex flex-wrap gap-2 border-t border-zinc-100 pt-4">{!readOnly && rule.status === "awaiting_confirmation" ? <><Button size="sm" onClick={() => onTransition(rule, "active")}><Check className="size-3.5" />确认生效</Button><Button size="sm" variant="outline" onClick={() => onEdit(rule)}><Pencil className="size-3.5" />编辑</Button><Button size="sm" variant="outline" onClick={() => onTransition(rule, "rejected")}><X className="size-3.5" />拒绝</Button></> : null}{rule.source_doc ? <Button size="sm" variant="ghost"><FileText className="size-3.5" />查看制度来源</Button> : null}</div> : null}</div></section>;
}

function Field({ label, value }: { label: string; value: string }) { return <div><div className="text-xs text-zinc-400">{label}</div><div className="mt-1 break-words text-sm text-zinc-700">{value}</div></div>; }

function RuleEditor({ rule, entries, close, afterSave }: { rule: RuleResponse | null; entries: ConstraintCatalogEntry[]; close: () => void; afterSave: () => void }) {
  const form = useForm<EditValues>({ resolver: zodResolver(editSchema) });
  const [draft, setDraft] = useState<ConstraintDraft>(emptyDraft);
  const [scopeError, setScopeError] = useState<string | null>(null);
  const update = useUpdateRuleApiV1RulesRuleIdPut({ mutation: { onSuccess: () => { toast.success("规则已保存"); afterSave(); }, onError: (error) => toast.error(errorMessage(error)) } });
  const hardness = form.watch("hardness") ?? "soft";
  const entry = entries.find((item) => item.type === draft.constraint_type);
  const allowedHardness = useMemo<Hardness[]>(() => entry?.hardness ?? ["hard", "soft"], [entry]);
  useEffect(() => {
    if (!rule) return;
    form.reset({ source_text: rule.source_text, hardness: rule.hardness, weight: rule.weight ?? undefined });
    setDraft({ constraint_type: rule.constraint_type, actor_type: rule.actor_type, actor_ids: rule.actor_ids ?? [], scope: toScopeState(rule.scope) });
    setScopeError(null);
  }, [form, rule]);
  useEffect(() => {
    if (rule && !allowedHardness.includes(hardness)) form.setValue("hardness", allowedHardness[0] as Hardness);
  }, [allowedHardness, hardness, form, rule]);
  return <Dialog open={Boolean(rule)} onOpenChange={(open) => { if (!open) close(); }}><DialogContent><DialogTitle className="text-base font-semibold">编辑候选规则</DialogTitle><DialogDescription className="mt-1 text-sm text-zinc-500">只有待确认的规则可以编辑，保存后仍需人工确认才会参与排课。</DialogDescription><form className="mt-5 space-y-4" onSubmit={form.handleSubmit((value) => {
    if (!rule) return;
    if (!scopeIsComplete(entry, draft.scope)) {
      setScopeError(`「${entry?.label ?? draft.constraint_type}」需要填写：${(entry?.scope ?? []).join(" 或 ")}`);
      return;
    }
    setScopeError(null);
    update.mutate({ ruleId: rule.id, data: { source_text: value.source_text, actor_type: draft.actor_type, actor_ids: draft.actor_ids, constraint_type: draft.constraint_type, scope: cleanScope(draft.scope), hardness: value.hardness, weight: value.hardness === "soft" ? value.weight : null, structured_expression: rule.structured_expression ?? {}, source_doc: rule.source_doc, confidence: rule.confidence } });
  })}><label className="block text-sm text-zinc-700">规则原文<textarea className="mt-1.5 min-h-24 w-full rounded-md border border-zinc-300 p-2.5 outline-none focus:border-blue-500" aria-invalid={Boolean(form.formState.errors.source_text)} {...form.register("source_text")} /><FieldError message={form.formState.errors.source_text?.message} /></label><div className="grid grid-cols-2 gap-3"><label className="text-sm text-zinc-700">约束级别<select className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-2" {...form.register("hardness")}>{allowedHardness.includes("hard") ? <option value="hard">硬约束</option> : null}{allowedHardness.includes("soft") ? <option value="soft">软约束</option> : null}</select></label><label className="text-sm text-zinc-700">权重<input className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-2" type="number" min="1" disabled={hardness === "hard"} aria-invalid={Boolean(form.formState.errors.weight)} {...form.register("weight", { valueAsNumber: true })} /><FieldError message={hardness === "soft" ? form.formState.errors.weight?.message : undefined} /></label></div><ConstraintFields entries={entries} draft={draft} hardness={hardness} onChange={(next) => { setDraft(next); setScopeError(null); }} /><FieldError message={scopeError ?? undefined} /><div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={close}>取消</Button><Button type="submit" disabled={update.isPending}>保存</Button></div></form></DialogContent></Dialog>;
}
