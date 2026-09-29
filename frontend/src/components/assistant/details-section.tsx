import { type ReactNode, useEffect, useState } from "react";

import { CollapsibleSection } from "@/components/assistant/collapsible-section";
import { ExplanationPanel, MemoryUsageSection } from "@/components/assistant/explanation-panel";
import { type AssistantTask } from "@/components/assistant/use-assistant-task";
import { GoalAcceptanceReport } from "@/components/goal/goal-acceptance-report";
import { GoalChecklist } from "@/components/goal/goal-checklist";
import { GoalRunList } from "@/components/goal/goal-run-list";
import { plainInterpretSummary } from "@/lib/assistant-task";
import { needsParamsItems, parseGoalReport } from "@/lib/goal";
import { modelStatusLabel } from "@/lib/labels";
import { type MemoryUsageSnapshot } from "@/lib/memory-usage";

function Value({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-zinc-400">{label}</div>
      <div className="mt-1 font-mono text-sm text-zinc-800">{value}</div>
    </div>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-zinc-100 pt-4 first:border-t-0 first:pt-0">
      <h3 className="mb-2 text-xs font-medium text-zinc-500">{title}</h3>
      {children}
    </section>
  );
}

/**
 * 「查看详情」折叠区（默认收起）：低频、偏技术的内容都收在这里——手动排课参数、验收清单与历史、
 * 逐项验收报告全文、关联求解记录、结果解释全文（含意图核对）、技术指标、思考过程全文。
 * 需要立刻决策的内容（未落实的要求、范围扩大确认、缺口与补救、卡住的原因）不在这里，见对应的卡。
 */
export function DetailsSection({ task, manualPanel }: { task: AssistantTask; manualPanel: ReactNode }) {
  const { goal, activeRun: run, interpretation } = task;
  // 补参表单的展开状态（清单项按钮与验收缺口的「补充条件」共用）；切换任务时复位。
  const [paramFormKeys, setParamFormKeys] = useState<string[]>([]);
  useEffect(() => { setParamFormKeys([]); }, [goal?.id]);
  const needParamItems = needsParamsItems(goal);
  const report = parseGoalReport(goal?.latest_report ?? run?.goal_report);
  const presolved = Boolean(run?.presolve_infeasible);
  return (
    <CollapsibleSection title="查看详情" hint="手动排课、验收清单、结果解释、技术指标" open={task.detailsOpen} onOpenChange={task.setDetailsOpen}>
      <div className="space-y-5">
        <CollapsibleSection title="手动排课参数" hint="自己设置范围与时限，随时可用" open={task.manualOpen} onOpenChange={task.setManualOpen} className="shadow-none">
          {manualPanel}
        </CollapsibleSection>
        {goal ? (
          <Block title="验收清单与历史">
            <GoalChecklist
              key={goal.id}
              goal={goal}
              onQuantizeRequest={(key) => setParamFormKeys((keys) => (keys.includes(key) ? keys.filter((item) => item !== key) : [...keys, key]))}
              paramFormKeys={paramFormKeys}
              onParamFormClose={(key) => setParamFormKeys((keys) => keys.filter((item) => item !== key))}
            />
          </Block>
        ) : null}
        {goal && report ? (
          <Block title="逐项验收报告">
            <GoalAcceptanceReport
              goal={goal}
              report={report}
              onRaiseBudget={task.raiseBudget}
              raiseBudgetBlockedReason={task.raiseBudgetBlockedReason}
              onResolveScope={() => task.openManual({ focusScope: true })}
              onFixChecklist={() => setParamFormKeys(needParamItems.map((item) => String(item.key ?? "")))}
            />
          </Block>
        ) : null}
        {goal ? (
          <Block title="关联求解记录">
            <GoalRunList goal={goal} />
          </Block>
        ) : null}
        {run?.status === "completed" ? (
          <Block title="结果解释">
            <div className="space-y-3">
              <MemoryUsageSection memory={run.memory_usage as MemoryUsageSnapshot | null | undefined} />
              <ExplanationPanel run={run} state={task.explain} onUseInstruction={task.applySuggestedInstruction} />
            </div>
          </Block>
        ) : null}
        {run ? (
          <Block title="技术指标">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <Value label="模型状态" value={modelStatusLabel(run.model_status, presolved)} />
              <Value label="目标函数值" value={run.objective_value?.toFixed(1) ?? "-"} />
              <Value label="最佳界" value={run.best_bound?.toFixed(1) ?? "-"} />
              <Value label="耗时" value={run.wall_time_seconds ? `${run.wall_time_seconds.toFixed(2)} 秒` : "-"} />
            </div>
            <p className="mt-2 text-xs text-zinc-400">求解记录 #{run.id.slice(0, 8)}</p>
          </Block>
        ) : null}
        {interpretation && interpretation.summary !== plainInterpretSummary(interpretation.summary) ? (
          <Block title="解析原文">
            <p className="text-xs leading-5 text-zinc-600">{interpretation.summary}</p>
          </Block>
        ) : null}
        {interpretation?.thinking ? (
          <Block title="思考过程全文">
            <div className="max-h-48 overflow-y-auto whitespace-pre-wrap border-l-2 border-zinc-200 pl-3 text-xs italic leading-5 text-zinc-500">
              {interpretation.thinking}
            </div>
          </Block>
        ) : null}
      </div>
    </CollapsibleSection>
  );
}
