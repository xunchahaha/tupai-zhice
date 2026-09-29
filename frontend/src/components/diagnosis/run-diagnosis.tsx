import { CircleAlert, HelpCircle, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";

import { type RuleResponse, type SolverRunResponse } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { actorTypeLabel, constraintLabel, modelStatusLabel, statusLabel, translateSystemConstraints } from "@/lib/labels";
import { ROUTES } from "@/lib/routes";
import { statusTone } from "@/lib/status";

/**
 * 一次无解求解的冲突核心分析：诊断提示、建议调整优先级、涉事规则、系统内置硬约束提示。
 * 只渲染内容（标题栏 + 正文），外框卡片由宿主提供，便于嵌进不同页面。
 * rules 传全部规则即可，组件按 run.conflict_rule_ids 自己筛出涉事的那几条。
 */
export function RunDiagnosis({ run, rules }: { run: SolverRunResponse; rules: RuleResponse[] }) {
  const related = rules.filter((rule) => run.conflict_rule_ids.includes(rule.business_id));
  const priorityRules = (run.priority_rule_ids ?? [])
    .map((businessId) => related.find((rule) => rule.business_id === businessId))
    .filter((rule): rule is RuleResponse => Boolean(rule));
  return (
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
                    <span className="flex shrink-0 items-center gap-2">
                      {/* 规则页按 ?rule= 定位并高亮这一条。 */}
                      <Link
                        to={`${ROUTES.rules}?${new URLSearchParams({ rule: rule.business_id })}`}
                        className="text-xs text-blue-600 transition-colors hover:text-blue-700 hover:underline"
                      >
                        查看规则
                      </Link>
                      <Badge tone={statusTone(rule.status)}>{statusLabel(rule.status)}</Badge>
                    </span>
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
  );
}
