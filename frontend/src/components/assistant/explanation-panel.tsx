import { Loader2, MessageSquareText, RefreshCw, Sparkles } from "lucide-react";

import { type SolverRunResponse } from "@/api/generated/models";
import { type RunExplanationState } from "@/components/assistant/use-run-explanation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { type MemoryOutcome, type MemoryUsageSnapshot, memoryHeadline, memoryOutcomeLabel } from "@/lib/memory-usage";

const INTENT_VERDICTS: Record<string, { label: string; tone: "green" | "yellow" | "neutral" }> = {
  matched: { label: "与原始意图一致", tone: "green" },
  deviated: { label: "可能偏离原始意图", tone: "yellow" },
  unclear: { label: "无法判断", tone: "neutral" },
};

/**
 * 偏好记忆使用情况（MEM-C1）：读创建任务时冻结的 memory_usage，不依赖解释生成。
 * 第七轮口径收口：这里展示的是编译资格段（创建时点、方案级），标题与 headline 都按编译口径表述；
 * 对本次课程的实际匹配与结果满足情况由解释层计算，本组件不再用编译状态冒充本次使用结论。
 * 助手页的「本次要求」面板另有面向业务的逐条列表，这里保留完整口径供核对。
 */
export function MemoryUsageSection({ memory }: { memory: MemoryUsageSnapshot | null | undefined }) {
  if (!memory?.status) return null;
  const headline = memoryHeadline(memory);
  if (!headline) return null;
  const failed = memory.status === "compile_failed";
  const unused = (Array.isArray(memory.outcomes) ? memory.outcomes : []).filter(
    (item: MemoryOutcome) => item.outcome !== "applied",
  );
  return (
    <section className="rounded-md border border-zinc-200 bg-zinc-50/60 px-4 py-3 text-sm">
      <div className="text-xs font-medium text-zinc-500">偏好记忆（创建时编译口径）</div>
      {failed ? (
        <p className="mt-1.5 border-l-2 border-red-400 bg-red-50 px-3 py-2 text-red-900">
          {headline}
          {memory.detail ? `（${memory.detail}）` : ""}。本次求解照常完成，但未参考任何偏好记忆；修复记忆层后重新求解即可带上偏好。
        </p>
      ) : (
        <div className="mt-1.5">
          <p className="text-zinc-800">{headline}</p>
          {unused.length ? (
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-zinc-600">
              {unused.map((item) => (
                <li key={`${item.entry_id}-${item.outcome}`}>
                  {item.subject_type} {item.subject_id} {item.predicate}：{memoryOutcomeLabel(item.outcome)}
                  {item.detail ? `——${item.detail}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </section>
  );
}

/** 结果解释全文（含意图核对）。下一步建议与建议指令在「卡住了」卡上已直接展示，这里保留完整版本。 */
export function ExplanationPanel({
  run,
  state,
  onUseInstruction,
}: {
  run: SolverRunResponse | null;
  state: RunExplanationState;
  onUseInstruction: (value: string) => void;
}) {
  const { explanation, analyzing, failure, analyze } = state;
  if (!run || run.status !== "completed") return null;
  const intent = explanation?.intent_review;
  const verdict = intent ? INTENT_VERDICTS[intent.verdict] ?? INTENT_VERDICTS.unclear : null;
  const suggested = explanation?.suggested_instruction ?? "";
  const explanationItems = Array.isArray(explanation?.explanation) ? explanation.explanation : [];
  const nextActions = Array.isArray(explanation?.next_actions) ? explanation.next_actions : [];
  const concerns = Array.isArray(intent?.concerns) ? intent.concerns : [];
  return (
    <section aria-label="结果解释">
      <div className="flex flex-wrap items-center gap-2">
        <MessageSquareText className="size-4 text-blue-600" />
        <h3 className="text-sm font-semibold">结果解释</h3>
        {explanation ? <Badge tone={explanation.source === "ai" ? "blue" : "neutral"}>{explanation.source === "ai" ? "AI 措辞" : "系统兜底措辞"}</Badge> : null}
        <Button
          className="ml-auto"
          size="sm"
          variant="outline"
          disabled={analyzing}
          onClick={() => void analyze(run.id, { refresh: Boolean(explanation), auto: false })}
        >
          {analyzing ? <Loader2 className="size-3.5 animate-spin" /> : explanation ? <RefreshCw className="size-3.5" /> : <Sparkles className="size-3.5" />}
          {analyzing ? "AI 解析中" : "重新解析"}
        </Button>
      </div>
      {analyzing && !explanation ? (
        <div className="mt-3 flex items-center gap-2 border-l-2 border-blue-400 bg-blue-50/60 px-4 py-3 text-sm text-blue-900">
          <Loader2 className="size-4 animate-spin" />
          求解已结束，AI 正在自动解读这次结果……
        </div>
      ) : null}
      {!explanation && !analyzing && failure ? (
        <div className="mt-3 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          自动解析没能完成：{failure}。求解结果本身不受影响，可点右上角「重新解析」重试。
        </div>
      ) : null}
      {explanation ? (
        <div className="mt-3 space-y-3 text-sm">
          <p className="font-medium text-zinc-900">{explanation.headline}</p>
          {explanationItems.length ? (
            <ul className="list-disc space-y-1 pl-5 text-zinc-700">
              {explanationItems.map((item, index) => <li key={`${index}-${item.slice(0, 12)}`}>{item}</li>)}
            </ul>
          ) : null}
          {nextActions.length ? (
            <div className="border-l-2 border-blue-400 bg-blue-50/60 px-4 py-3 text-zinc-800">
              <div className="text-xs font-medium text-blue-800">下一步可以做什么</div>
              <ul className="mt-1 list-disc space-y-1 pl-5">
                {nextActions.map((item, index) => <li key={`${index}-${item.slice(0, 12)}`}>{item}</li>)}
              </ul>
            </div>
          ) : null}
          {suggested ? (
            <div className="border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-amber-900">
              <div className="flex flex-wrap items-center gap-2">
                <div className="text-xs font-medium">建议改成这条需求</div>
                <Button className="ml-auto" size="sm" variant="outline" onClick={() => onUseInstruction(suggested)}>填入指令框</Button>
              </div>
              <p className="mt-2 leading-6">{suggested}</p>
            </div>
          ) : null}
          {intent && verdict ? (
            <div className="border-l-2 border-zinc-300 bg-zinc-50 px-4 py-3">
              <div className="flex items-center gap-2 text-xs text-zinc-500">
                意图核对
                <Badge tone={verdict.tone}>{verdict.label}</Badge>
              </div>
              {concerns.length ? (
                <ul className="mt-1.5 list-disc space-y-1 pl-5 text-zinc-700">
                  {concerns.map((item, index) => <li key={`${index}-${item.slice(0, 12)}`}>{item}</li>)}
                </ul>
              ) : null}
              <p className="mt-2 text-xs text-zinc-500">
                意图核对由模型给出，仅供参考。硬冲突是否存在由求解器与独立重算的指标判定，不受这里影响。
              </p>
            </div>
          ) : null}
          {explanation.ai_error ? (
            <p className="border-l-2 border-amber-500 bg-amber-50 px-4 py-2 text-xs text-amber-900">
              AI 措辞不可用，已回退到系统兜底解释：{explanation.ai_error}
            </p>
          ) : null}
        </div>
      ) : analyzing || failure ? null : (
        <p className="mt-2 text-xs text-zinc-500">求解结束后会自动把模型状态、冲突规则和课表指标翻译成教务能读的结论；AI 不可用时会回退到系统兜底措辞。</p>
      )}
    </section>
  );
}
