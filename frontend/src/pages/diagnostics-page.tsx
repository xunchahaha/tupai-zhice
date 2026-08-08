import { CircleAlert, ShieldAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useListRulesApiV1RulesGet, useListSolverRunsApiV1SolverRunsGet } from "@/api/generated/client";
import { type RuleResponse } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { actorTypeLabel, constraintLabel, modelStatusLabel, statusLabel } from "@/lib/labels";
import { statusTone } from "@/lib/status";

export function DiagnosticsPage() {
  const runs = useListSolverRunsApiV1SolverRunsGet();
  const rules = useListRulesApiV1RulesGet();
  const [id, setId] = useState("");
  const infeasible = useMemo(() => (runs.data ?? []).filter((run) => run.model_status === "INFEASIBLE"), [runs.data]);
  useEffect(() => { if (!id && infeasible[0]) setId(infeasible[0].id); }, [id, infeasible]);
  if (runs.isPending || rules.isPending) return <LoadingState />;
  if (runs.isError || rules.isError) return <ErrorState retry={() => { void runs.refetch(); void rules.refetch(); }} />;
  const run = infeasible.find((item) => item.id === id);
  const related = (rules.data ?? []).filter((rule) => run?.conflict_rule_ids.includes(rule.business_id));
  const priorityRules = (run?.priority_rule_ids ?? []).map((businessId) => related.find((rule) => rule.business_id === businessId)).filter((rule): rule is RuleResponse => Boolean(rule));
  return <div className="space-y-5">
    <PageHeader title="无解诊断" />
    <section className="grid gap-2 xl:grid-cols-[340px_minmax(0,1fr)]">
      <div className="border border-zinc-200 bg-white"><div className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold">无解任务</div>{infeasible.length ? infeasible.map((item) => <button key={item.id} className={`block w-full border-b border-zinc-100 px-4 py-3 text-left hover:bg-zinc-50 ${item.id === id ? "bg-red-50" : ""}`} onClick={() => setId(item.id)}><div className="flex items-center justify-between"><span className="font-mono text-xs text-zinc-500">{item.id.slice(0, 8)}</span><Badge tone="red">无解</Badge></div><div className="mt-2 text-sm text-zinc-700">{item.conflict_rule_ids.length} 条冲突规则</div></button>) : <div className="grid min-h-40 place-items-center text-sm text-zinc-400">暂无无解任务</div>}</div>
      <div className="border border-zinc-200 bg-white">{run ? <><div className="flex items-center gap-2 border-b border-zinc-200 px-5 py-4"><CircleAlert className="size-4 text-red-600" /><div><h2 className="font-semibold">冲突核心</h2><p className="mt-0.5 text-xs text-zinc-400">任务 {run.id}</p></div><Badge className="ml-auto" tone="red">{modelStatusLabel(run.model_status)}</Badge></div><div className="p-5"><div className="mb-4 border-l-2 border-red-600 bg-red-50 px-4 py-3 text-sm text-red-800">求解器保留硬约束，不会自动放宽；以下优先级只作为人工调整建议。</div>{run.priority_explanations?.length ? <section className="mb-5 border border-amber-200 bg-amber-50/60 p-4"><h3 className="text-sm font-semibold text-amber-950">建议优先保留</h3><ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-amber-900">{run.priority_explanations.map((item) => <li key={item}>{item}</li>)}</ol>{priorityRules.length ? <div className="mt-3 flex flex-wrap gap-2">{priorityRules.map((rule) => <Badge key={rule!.id} tone="green">{rule!.business_id}</Badge>)}</div> : null}</section> : null}<div className="space-y-3">{related.map((rule) => <article key={rule.id} className="border border-zinc-200 p-4"><div className="flex items-center justify-between gap-3"><div className="font-mono text-xs text-zinc-500">{rule.business_id}</div><Badge tone={statusTone(rule.status)}>{statusLabel(rule.status)}</Badge></div><p className="mt-2 text-sm text-zinc-800">{rule.source_text}</p><div className="mt-2 text-xs text-zinc-500">{actorTypeLabel(rule.actor_type)} / {(rule.actor_ids ?? []).join("、") || "全局"} / {constraintLabel(rule.constraint_type)}</div></article>)}</div>{!related.length ? <div className="text-sm text-zinc-500">模型未返回可关联的规则 ID。</div> : null}</div></> : <div className="grid min-h-56 place-items-center text-sm text-zinc-400"><ShieldAlert className="mr-2 inline size-4" />选择一个无解任务</div>}</div>
    </section>
  </div>;
}
