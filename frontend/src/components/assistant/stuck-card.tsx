import { ClipboardCopy, CornerUpLeft, Loader2, OctagonAlert, Stethoscope } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useListRulesApiV1RulesGet } from "@/api/generated/client";
import { type RuleResponse, type SolverRunResponse } from "@/api/generated/models";
import { AcceptanceSummary } from "@/components/assistant/acceptance-summary";
import { AiProbeNotice } from "@/components/assistant/ai-probe-notice";
import { RefineInput } from "@/components/assistant/refine-input";
import { type AssistantTask } from "@/components/assistant/use-assistant-task";
import { RunDiagnosis } from "@/components/diagnosis/run-diagnosis";
import { LoadingState } from "@/components/page";
import { Button } from "@/components/ui/button";
import { describeScope, stuckHeadline } from "@/lib/assistant-task";
import { asArray, errorMessage } from "@/lib/format";
import { translateSystemConstraints } from "@/lib/labels";

async function copyInstruction(value: string) {
  try {
    // 非 HTTPS 或旧浏览器下 navigator.clipboard 可能不存在，这里必须退化成提示而不是抛错。
    if (!navigator.clipboard?.writeText) throw new Error("当前浏览器不允许自动复制");
    await navigator.clipboard.writeText(value);
    toast.success("建议指令已复制，可粘贴到需求输入框");
  } catch (error) {
    toast.error(`复制失败，请手动选中复制：${errorMessage(error)}`);
  }
}

/** 冲突核心分析（涉事规则可点开 /rules?rule=）：展开时才取规则列表。 */
function DiagnosisPanel({ run }: { run: SolverRunResponse }) {
  const rules = useListRulesApiV1RulesGet();
  return (
    <div className="mt-4 rounded-lg border border-zinc-200 bg-white">
      {rules.isPending ? <LoadingState rows={2} /> : <RunDiagnosis run={run} rules={asArray<RuleResponse>(rules.data)} />}
    </div>
  );
}

/**
 * 「哪里卡住了、下一步可以做什么」：INFEASIBLE / UNKNOWN / 失败 / 预检无解时出现，业务语言写原因。
 * 措辞口径不变：预检结论不能说「已证明无解」；UNKNOWN（超时未定）不等于无解；本次没有候选课表。
 * 冲突核心分析（涉事规则等）在「展开详细诊断」里。
 */
export function StuckCard({ task, run, defaultDiagnosisOpen }: { task: AssistantTask; run: SolverRunResponse; defaultDiagnosisOpen: boolean }) {
  const [diagnosisOpen, setDiagnosisOpen] = useState(defaultDiagnosisOpen);
  const { explanation, analyzing, failure } = task.explain;
  const presolved = Boolean(run.presolve_infeasible);
  const provenInfeasible = run.model_status === "INFEASIBLE" && !presolved && run.status === "completed";
  const priorities = Array.isArray(run.priority_explanations) ? run.priority_explanations : [];
  const nextActions = Array.isArray(explanation?.next_actions) ? explanation.next_actions : [];
  const suggested = explanation?.suggested_instruction ?? "";
  const canParse = task.probe.ready === true && !task.submitBlockedReason;
  // 超时/失败才谈得上「加时间」；加预算沿用任务里记着的范围，没有任务就不知道原来的范围。
  const timeRelated = run.status === "failed" || run.model_status === "UNKNOWN";
  const hasOpenGoal = Boolean(task.goalId) && !task.goalClosed;
  const scopeText = describeScope(task.params).join("，") || "全部课次";
  return (
    <section aria-label="排课卡住了" className="rounded-lg border border-amber-300 bg-amber-50/50 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <OctagonAlert className="size-4 text-amber-600" />
        <h2 className="font-semibold">{stuckHeadline(run)}</h2>
      </div>
      <p className="mt-1 text-xs text-zinc-600">哪里卡住了、下一步可以做什么，都列在下面。</p>
      {presolved ? (
        <div className="mt-4 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          本次结论来自求解前的数据预检，求解器未运行，因此不能表述为「已证明无解」。请先按下方诊断修正输入数据。
        </div>
      ) : null}
      {run.model_status === "UNKNOWN" && run.status === "completed" && !presolved ? (
        <p role="status" className="mt-4 text-sm text-amber-800">本次尚未找到可用解，尚未证明无解；请增加时限或缩小范围。本次没有候选课表。</p>
      ) : null}
      {provenInfeasible ? (
        <div className="mt-4 border-l-2 border-red-600 bg-red-50 px-4 py-3 text-sm text-red-800">
          已确认在当前硬性要求下无法排开全部课程；硬性要求不会自动放宽，需要你调整范围或放宽条件后重新排。
          {run.conflict_rule_ids.length ? `（涉及 ${run.conflict_rule_ids.length} 条规则，见「展开详细诊断」）` : ""}
        </div>
      ) : null}
      {run.error_message ? <div className="mt-4 text-sm text-red-700">{run.error_message}</div> : null}
      {priorities.length ? (
        <div className="mt-4 space-y-2 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="font-medium">可能的原因</div>
          {priorities.map((item, index) => <div key={`${index}-${item}`}>{translateSystemConstraints(item)}</div>)}
        </div>
      ) : null}
      {analyzing && !explanation ? (
        <div className="mt-4 flex items-center gap-2 border-l-2 border-blue-400 bg-blue-50/60 px-4 py-3 text-sm text-blue-900">
          <Loader2 className="size-4 animate-spin" />
          求解已结束，AI 正在解读这次结果……
        </div>
      ) : null}
      {!explanation && !analyzing && failure ? (
        <div className="mt-4 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          自动解读没能完成：{failure}。求解结果本身不受影响，可在「查看详情」里点「重新解析」重试。
        </div>
      ) : null}
      {nextActions.length ? (
        <div className="mt-4 border-l-2 border-blue-400 bg-blue-50/60 px-4 py-3 text-sm text-zinc-800">
          <div className="text-xs font-medium text-blue-800">下一步可以做什么</div>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {nextActions.map((item, index) => <li key={`${index}-${item.slice(0, 12)}`}>{item}</li>)}
          </ul>
        </div>
      ) : null}
      <AcceptanceSummary run={run} task={task} />
      {timeRelated && !hasOpenGoal && !task.canRaiseBudget ? (
        <div role="status" className="mt-4 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-900">
          {task.goalClosed
            ? <p>这个任务已放弃，不能再重跑，所以不提供「加大时间预算重跑」。可以先「修正范围」，或按下面的范围重新排课。</p>
            : <p>这次求解没有关联任务，没法确认它原来的排课范围，所以不提供「加大时间预算重跑」。可以先「修正范围」，或按下面的范围重新排课。</p>}
          <p className="mt-1 font-medium">将使用的范围：{scopeText}；求解时限 {task.params.time_limit_seconds} 秒</p>
        </div>
      ) : null}
      {suggested ? (
        <div className="mt-4 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-xs font-medium">建议改成这条需求</div>
            <div className="ml-auto flex gap-2">
              <Button size="sm" variant="outline" onClick={() => void copyInstruction(suggested)}>
                <ClipboardCopy className="size-3.5" />复制
              </Button>
              <Button size="sm" variant="outline" onClick={() => task.applySuggestedInstruction(suggested)}>
                <CornerUpLeft className="size-3.5" />填入指令框
              </Button>
            </div>
          </div>
          <p className="mt-2 leading-6">{suggested}</p>
          <p className="mt-2 text-xs text-amber-800">这条需求按本次范围内真实存在的班级、班型和日期拼出，填入后可以再改，再重新解析并重跑。</p>
        </div>
      ) : null}
      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-amber-200 pt-4">
        {timeRelated && (hasOpenGoal || task.canRaiseBudget) ? (
          <Button disabled={task.pending || Boolean(task.raiseBudgetBlockedReason)} onClick={task.raiseBudget}>加大时间预算重跑</Button>
        ) : null}
        {timeRelated && !hasOpenGoal && !task.canRaiseBudget ? (
          <Button disabled={task.pending || Boolean(task.submitBlockedReason)} onClick={task.submitManual}>按当前范围重新排课</Button>
        ) : null}
        <Button variant="outline" aria-expanded={task.refine.open} onClick={() => task.setRefine(task.refine.open ? { open: false, text: "" } : { open: true, text: "" })}>
          修改要求后重新解析
        </Button>
        <Button variant="outline" onClick={() => task.openManual({ focusScope: true })}>修正范围</Button>
        {provenInfeasible ? (
          <Button variant="ghost" aria-expanded={diagnosisOpen} onClick={() => setDiagnosisOpen((value) => !value)}>
            <Stethoscope className="size-4" />{diagnosisOpen ? "收起详细诊断" : "展开详细诊断"}
          </Button>
        ) : null}
      </div>
      {timeRelated && (hasOpenGoal || task.canRaiseBudget) && task.raiseBudgetBlockedReason && !task.submitBlockedReason ? (
        <p role="status" className="mt-2 text-xs text-amber-800">{task.raiseBudgetBlockedReason}</p>
      ) : null}
      {task.refine.open ? (
        <RefineInput
          label="修改后的要求"
          placeholder="放宽或改写这次的要求，再重新解析"
          value={task.refine.text}
          onChange={(text) => task.setRefine({ open: true, text })}
          onSubmit={() => void task.interpret(task.refine.text)}
          onCancel={() => task.setRefine({ open: false, text: "" })}
          disabled={!canParse || task.phase === "thinking"}
          disabledReason={
            task.probe.ready === true
              ? task.submitBlockedReason ?? undefined
              : <AiProbeNotice probe={task.probe} missingText="AI 尚未接入，无法重新解析；可用「修正范围」后手动排课。" />
          }
        />
      ) : null}
      {provenInfeasible && diagnosisOpen ? <DiagnosisPanel run={run} /> : null}
    </section>
  );
}
