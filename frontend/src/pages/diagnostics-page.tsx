import { AlertTriangle, CircleAlert, FileQuestion, HelpCircle, ShieldAlert, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useListRulesApiV1RulesGet, useListSolverRunsApiV1SolverRunsGet } from "@/api/generated/client";
import { type RuleResponse, type SolverRunResponse } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { actorTypeLabel, constraintLabel, modelStatusLabel, statusLabel, translateSystemConstraints } from "@/lib/labels";
import { statusTone } from "@/lib/status";
import { asArray, datetime } from "@/lib/format";

export function DiagnosticsPage() {
  const runs = useListSolverRunsApiV1SolverRunsGet();
  const rules = useListRulesApiV1RulesGet();
  const [id, setId] = useState("");

  const runList = asArray<SolverRunResponse>(runs.data);
  const ruleList = asArray<RuleResponse>(rules.data);

  const infeasible = useMemo(
    () => runList.filter((run) => run.model_status === "INFEASIBLE"),
    [runList],
  );

  useEffect(() => {
    if (!id && infeasible[0]) setId(infeasible[0].id);
  }, [id, infeasible]);

  if (runs.isPending || rules.isPending) return <LoadingState />;
  if (runs.isError || rules.isError) return <ErrorState retry={() => { void runs.refetch(); void rules.refetch(); }} />;

  const run = infeasible.find((item) => item.id === id);
  const related = ruleList.filter((rule) => run?.conflict_rule_ids.includes(rule.business_id));
  const priorityRules = (run?.priority_rule_ids ?? [])
    .map((businessId) => related.find((rule) => rule.business_id === businessId))
    .filter((rule): rule is RuleResponse => Boolean(rule));

  return (
    <div className="space-y-5 animate-fade-in">
      <PageHeader title="无解诊断" />
      <section className="grid gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
        {/* Left List of Infeasible Tasks */}
        <div className="rounded-lg border border-zinc-200 bg-white shadow-2xs">
          <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3 text-sm font-semibold">
            <span className="flex items-center gap-1.5 text-zinc-900">
              <AlertTriangle className="size-4 text-red-600" />
              无解任务列表
            </span>
            <span className="text-xs font-normal text-zinc-400">共 {infeasible.length} 个</span>
          </div>
          <div className="divide-y divide-zinc-100">
            {infeasible.length ? (
              infeasible.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`block w-full px-4 py-3.5 text-left transition-all duration-150 hover:bg-zinc-50 ${
                    item.id === id ? "bg-red-50/70 border-l-3 border-l-red-600" : ""
                  }`}
                  onClick={() => setId(item.id)}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs font-medium text-zinc-700">
                      任务 #{item.id.slice(0, 8)}
                    </span>
                    <Badge tone="red">无解</Badge>
                  </div>
                  <div className="mt-1.5 flex items-center justify-between text-xs text-zinc-500">
                    <span>{item.conflict_rule_ids.length} 条冲突约束</span>
                    <span>{datetime(item.created_at)}</span>
                  </div>
                </button>
              ))
            ) : (
              <div className="grid min-h-48 place-items-center p-6 text-center text-sm text-zinc-400">
                <div>
                  <ShieldAlert className="mx-auto mb-2 size-8 text-emerald-500/80" />
                  <p className="font-medium text-zinc-700">当前没有无解任务</p>
                  <p className="mt-1 text-xs text-zinc-400">所有排课求解任务均已成功找到可行解</p>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right Detail Panel */}
        <div className="rounded-lg border border-zinc-200 bg-white shadow-2xs">
          {run ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-200 p-4 sm:px-6">
                <div className="flex items-center gap-2.5">
                  <div className="grid size-8 place-items-center rounded-lg bg-red-100/80 text-red-700">
                    <CircleAlert className="size-4.5" />
                  </div>
                  <div>
                    <h2 className="font-semibold text-zinc-900">冲突核心分析</h2>
                    <p className="text-xs text-zinc-400 font-mono">任务 ID: {run.id}</p>
                  </div>
                </div>
                <Badge tone="red">{modelStatusLabel(run.model_status)}</Badge>
              </div>

              <div className="p-5 sm:p-6 space-y-5">
                <div className="rounded-md border border-red-200 bg-red-50/80 p-3.5 text-xs sm:text-sm leading-6 text-red-900">
                  <strong>💡 诊断提示：</strong>求解器在数学层面已证明在当前设定的硬约束下无法排开全部课程；硬约束不会自动放宽，以下优先级与建议供教务管理人员人工调整。
                </div>

                {run.priority_explanations?.length ? (
                  <section className="rounded-lg border border-amber-200 bg-amber-50/60 p-4 sm:p-5">
                    <h3 className="flex items-center gap-1.5 text-sm font-semibold text-amber-950">
                      <Sparkles className="size-4 text-amber-600" />
                      建议调整优先级
                    </h3>
                    <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-6 text-amber-900">
                      {run.priority_explanations.map((item) => (
                        <li key={item}>
                          <span className="font-medium">{translateSystemConstraints(item)}</span>
                        </li>
                      ))}
                    </ol>
                    {priorityRules.length ? (
                      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-amber-200/60 pt-3">
                        <span className="text-xs text-amber-800">建议优先保留的规则：</span>
                        {priorityRules.map((rule) => (
                          <Badge key={rule.id} tone="green">
                            {rule.business_id}
                          </Badge>
                        ))}
                      </div>
                    ) : null}
                  </section>
                ) : null}

                <div>
                  <h3 className="mb-3 text-sm font-semibold text-zinc-900">涉事规则列表</h3>
                  {related.length ? (
                    <div className="space-y-3">
                      {related.map((rule) => (
                        <article
                          key={rule.id}
                          className="rounded-lg border border-zinc-200 bg-zinc-50/30 p-4 transition-all duration-150 hover:border-zinc-300 hover:bg-white"
                        >
                          <div className="flex items-center justify-between gap-3">
                            <span className="font-mono text-xs font-medium text-zinc-600">
                              {rule.business_id}
                            </span>
                            <Badge tone={statusTone(rule.status)}>{statusLabel(rule.status)}</Badge>
                          </div>
                          <p className="mt-2 text-sm text-zinc-800 leading-6">{rule.source_text}</p>
                          <div className="mt-2 text-xs text-zinc-500">
                            {actorTypeLabel(rule.actor_type)} / {(rule.actor_ids ?? []).join("、") || "全局"} /{" "}
                            {constraintLabel(rule.constraint_type)}
                          </div>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <div className="flex items-start gap-3 rounded-lg border border-zinc-200/80 bg-zinc-50/70 p-4 text-xs leading-5 text-zinc-600">
                      <HelpCircle className="mt-0.5 size-4 shrink-0 text-blue-600" />
                      <div>
                        <span className="font-semibold text-zinc-800">系统内置硬约束生效提示：</span>
                        当前无解主要由系统级固定约束（如固定时段锁定、班级时间不重叠、教室场地不重叠）引起，未直接关联用户自定义的业务规则。
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="grid min-h-64 place-items-center p-8 text-center text-sm text-zinc-400">
              <div>
                <FileQuestion className="mx-auto mb-2 size-8 text-zinc-300" />
                <p>请在左侧选择一个无解任务查看冲突核心与调整建议</p>
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
