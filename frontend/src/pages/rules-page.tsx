import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Check, FileText, Pencil, Plus, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  getListRulesApiV1RulesGetQueryKey,
  useCreateRuleApiV1RulesPost,
  useListRulesApiV1RulesGet,
  useTransitionRuleApiV1RulesRuleIdTransitionPost,
  useUpdateRuleApiV1RulesRuleIdPut,
} from "@/api/generated/client";
import { type RuleResponse } from "@/api/generated/models";
import { isReadOnlyMember, useAppUser } from "@/app/user-context";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { actorTypeLabel, constraintLabel, hardnessLabel, statusLabel } from "@/lib/labels";
import { errorMessage } from "@/lib/format";
import { statusTone } from "@/lib/status";

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
    {readOnly ? <section className="border border-zinc-200 bg-zinc-50 px-4 py-3 text-sm text-zinc-600">当前账号为成员，只能查看规则及其生效状态。</section> : <RuleIntakeForm create={create} />}
    <div className="grid gap-2 xl:grid-cols-[minmax(340px,0.9fr)_minmax(0,1.6fr)]">
      <section className="border border-zinc-200 bg-white">
        <div className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold">候选与正式规则 <span className="ml-1 text-xs font-normal text-zinc-400">{visible.length}</span></div>
        <div className="max-h-[calc(100vh-360px)] overflow-y-auto">{visible.map((rule) => <button key={rule.id} onClick={() => setSelected(rule)} className={`block w-full border-b border-zinc-100 px-4 py-3 text-left hover:bg-zinc-50 ${selected?.id === rule.id ? "bg-blue-50/60" : ""}`}>
          <div className="flex items-center justify-between gap-2"><span className="font-mono text-xs text-zinc-500">{rule.business_id}</span><Badge tone={statusTone(rule.status)}>{statusLabel(rule.status)}</Badge></div>
          <p className="mt-1 line-clamp-2 text-sm text-zinc-800">{rule.source_text}</p>
          <div className="mt-2 flex gap-2 text-xs text-zinc-400"><span>{constraintLabel(rule.constraint_type)}</span><span>{hardnessLabel(rule.hardness)}{rule.hardness === "soft" ? `，权重 ${rule.weight ?? "-"}` : ""}</span></div>
        </button>)}</div>
      </section>
      <RuleDetail readOnly={readOnly} rule={selected} onEdit={setEditing} onTransition={(rule, status) => transition.mutate({ ruleId: rule.id, data: { status } })} />
    </div>
    {readOnly ? null : <RuleEditor rule={editing} close={() => setEditing(null)} afterSave={() => { setEditing(null); invalidate(); }} />}
  </div>;
}

function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return <span className="mt-1 block text-xs text-red-600" role="alert">{message}</span>;
}

function RuleIntakeForm({ create }: { create: ReturnType<typeof useCreateRuleApiV1RulesPost> }) {
  const form = useForm<IntakeValues>({ resolver: zodResolver(intakeSchema), defaultValues: { hardness: "soft", weight: 10, source_doc: "管理端录入" } });
  const errors = form.formState.errors;
  const hardness = form.watch("hardness");
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
        className="mt-4 grid items-start gap-3 xl:grid-cols-[minmax(0,1fr)_180px_180px_auto]"
        onSubmit={form.handleSubmit((value) =>
          create.mutate({
            data: {
              source_text: value.source_text,
              actor_type: "system",
              actor_ids: [],
              constraint_type: "declared_constraint",
              scope: {},
              hardness: value.hardness,
              weight: value.hardness === "soft" ? value.weight : null,
              structured_expression: { natural_language: value.source_text },
              source_doc: value.source_doc || "管理端录入",
              confidence: 1,
            },
          }),
        )}
      >
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
            <option value="soft">软约束</option>
            <option value="hard">硬约束</option>
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
        <Button className="self-start xl:mt-6" type="submit" disabled={create.isPending}>
          <Plus className="size-3.5" />
          提交待确认
        </Button>
      </form>
    </section>
  );
}

function RuleDetail({ readOnly, rule, onEdit, onTransition }: { readOnly: boolean; rule: RuleResponse | null; onEdit: (rule: RuleResponse) => void; onTransition: (rule: RuleResponse, status: "active" | "rejected") => void }) {
  if (!rule) return <section className="grid min-h-72 place-items-center border border-zinc-200 bg-white text-sm text-zinc-400">选择一条规则查看详情</section>;
  return <section className="border border-zinc-200 bg-white"><div className="flex items-center justify-between border-b border-zinc-200 px-5 py-4"><div><div className="font-mono text-xs text-zinc-400">{rule.business_id} / 第 {rule.version} 版</div><h2 className="mt-1 text-base font-semibold">{constraintLabel(rule.constraint_type)}</h2></div><Badge tone={statusTone(rule.status)}>{statusLabel(rule.status)}</Badge></div><div className="grid gap-5 p-5"><div><div className="mb-1 text-xs text-zinc-400">原始表达</div><p className="leading-6 text-zinc-800">{rule.source_text}</p></div><div className="grid gap-4 sm:grid-cols-2"><Field label="作用对象" value={`${actorTypeLabel(rule.actor_type)}：${(rule.actor_ids ?? []).join("、") || "全局"}`} /><Field label="约束级别" value={`${hardnessLabel(rule.hardness)}${rule.hardness === "soft" ? `，权重 ${rule.weight ?? "-"}` : ""}`} /><Field label="范围" value={JSON.stringify(rule.scope)} /><Field label="来源" value={rule.source_doc ?? "-"} /></div>{!readOnly || rule.source_doc ? <div className="flex flex-wrap gap-2 border-t border-zinc-100 pt-4">{!readOnly && rule.status === "awaiting_confirmation" ? <><Button size="sm" onClick={() => onTransition(rule, "active")}><Check className="size-3.5" />确认生效</Button><Button size="sm" variant="outline" onClick={() => onEdit(rule)}><Pencil className="size-3.5" />编辑</Button><Button size="sm" variant="outline" onClick={() => onTransition(rule, "rejected")}><X className="size-3.5" />拒绝</Button></> : null}{rule.source_doc ? <Button size="sm" variant="ghost"><FileText className="size-3.5" />查看制度来源</Button> : null}</div> : null}</div></section>;
}

function Field({ label, value }: { label: string; value: string }) { return <div><div className="text-xs text-zinc-400">{label}</div><div className="mt-1 break-words text-sm text-zinc-700">{value}</div></div>; }

function RuleEditor({ rule, close, afterSave }: { rule: RuleResponse | null; close: () => void; afterSave: () => void }) {
  const form = useForm<EditValues>({ resolver: zodResolver(editSchema) });
  const update = useUpdateRuleApiV1RulesRuleIdPut({ mutation: { onSuccess: () => { toast.success("规则已保存"); afterSave(); }, onError: (error) => toast.error(errorMessage(error)) } });
  useEffect(() => { if (rule) form.reset({ source_text: rule.source_text, hardness: rule.hardness, weight: rule.weight ?? undefined }); }, [form, rule]);
  return <Dialog open={Boolean(rule)} onOpenChange={(open) => { if (!open) close(); }}><DialogContent><DialogTitle className="text-base font-semibold">编辑候选规则</DialogTitle><DialogDescription className="mt-1 text-sm text-zinc-500">只有待确认的规则可以编辑，保存后仍需人工确认才会参与排课。</DialogDescription><form className="mt-5 space-y-4" onSubmit={form.handleSubmit((value) => { if (!rule) return; update.mutate({ ruleId: rule.id, data: { source_text: value.source_text, actor_type: rule.actor_type, actor_ids: rule.actor_ids ?? [], constraint_type: rule.constraint_type, scope: rule.scope ?? {}, hardness: value.hardness, weight: value.hardness === "soft" ? value.weight : null, structured_expression: rule.structured_expression ?? {}, source_doc: rule.source_doc, confidence: rule.confidence } }); })}><label className="block text-sm text-zinc-700">规则原文<textarea className="mt-1.5 min-h-24 w-full rounded-md border border-zinc-300 p-2.5 outline-none focus:border-blue-500" aria-invalid={Boolean(form.formState.errors.source_text)} {...form.register("source_text")} /><FieldError message={form.formState.errors.source_text?.message} /></label><div className="grid grid-cols-2 gap-3"><label className="text-sm text-zinc-700">约束级别<select className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-2" {...form.register("hardness")}><option value="hard">硬约束</option><option value="soft">软约束</option></select></label><label className="text-sm text-zinc-700">权重<input className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-2" type="number" min="1" aria-invalid={Boolean(form.formState.errors.weight)} {...form.register("weight", { valueAsNumber: true })} /><FieldError message={form.formState.errors.weight?.message} /></label></div><div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={close}>取消</Button><Button type="submit" disabled={update.isPending}>保存</Button></div></form></DialogContent></Dialog>;
}
