import { ChevronDown, Play, RefreshCw, Sparkles } from "lucide-react";
import { useId, useState } from "react";

import { type ScheduleSummaryResponse } from "@/api/generated/models";
import { AiProbeNotice } from "@/components/assistant/ai-probe-notice";
import { InfoTooltip } from "@/components/assistant/info-tooltip";
import { InterpretThought } from "@/components/assistant/interpret-progress";
import { RefineInput } from "@/components/assistant/refine-input";
import { MemoryReceiptsPanel, ScopeItem, TaskConstraintsPanel } from "@/components/assistant/task-panels";
import { UnsupportedBlock } from "@/components/assistant/unsupported-block";
import { type AssistantTask } from "@/components/assistant/use-assistant-task";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { plainInterpretSummary } from "@/lib/assistant-task";
import { cn } from "@/lib/cn";

/**
 * 「更多选项」：基准版本 / 变更上限这类只有少数场景才需要的验收口径。
 * 语义与旧实现完全一致——默认只记录基准版本用于变更对比，只有明确勾选才写 max_changes 验收项。
 * 「以此为目标跟踪」默认开启并直接生效，这里保留关闭的余地但不打扰。
 */
function MoreOptions({ task, scheduleList }: { task: AssistantTask; scheduleList: ScheduleSummaryResponse[] }) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  return (
    <div className="mt-4 border-t border-blue-200 pt-3">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        className="inline-flex items-center gap-1 text-xs text-zinc-500 transition-colors hover:text-zinc-800"
        onClick={() => setOpen((value) => !value)}
      >
        更多选项
        <ChevronDown className={cn("size-3.5 transition-transform duration-200", open && "rotate-180")} />
      </button>
      {open ? (
        <div id={bodyId} className="mt-3 flex flex-wrap items-end gap-5">
          <label className="flex cursor-pointer items-center gap-2 text-sm text-zinc-700">
            <input aria-label="以此为目标跟踪" className="accent-blue-600" type="checkbox" checked={task.trackGoal} onChange={(event) => task.setTrackGoal(event.target.checked)} />
            <span className="inline-flex items-center gap-1.5">
              以此为目标跟踪
              <InfoTooltip label="以此为目标跟踪">把这次需求登记成持久任务：每次求解结束后按课次覆盖、禁排复核、硬冲突、只出草稿等清单逐项验收，缺口与建议下一步会显示在结果里；发布永远不会被自动执行。</InfoTooltip>
            </span>
          </label>
          {task.trackGoal ? (
            <label className="block text-sm text-zinc-700">
              <span className="inline-flex items-center gap-1.5">
                基准版本
                <InfoTooltip label="基准版本">记录所选基准版本，用于对比本次结果的变更明细与数量；默认不设变更上限，勾选「设置变更上限」后才会按上限验收。「尽量少改」是优化目标，不会被升级为「绝不能改」。</InfoTooltip>
              </span>
              <Select aria-label="基准版本" selectSize="sm" containerClassName="mt-1 w-52" value={task.baselineId} onChange={(event) => task.setBaselineId(event.target.value)}>
                <option value="">不设基准</option>
                {scheduleList.map((item) => (
                  <option key={item.id} value={item.id}>v{item.version_no} {item.name}{item.status === "published" ? "（已发布）" : ""}</option>
                ))}
              </Select>
            </label>
          ) : null}
          {task.trackGoal && task.baselineId ? (
            <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-700">
              <label className="flex cursor-pointer items-center gap-2">
                <input aria-label="设置变更上限" className="accent-blue-600" type="checkbox" checked={task.changeLimitEnabled} onChange={(event) => task.setChangeLimitEnabled(event.target.checked)} />
                <span className="inline-flex items-center gap-1.5">
                  设置变更上限
                  <InfoTooltip label="设置变更上限">默认不设上限：基准版本只用于记录变更明细与数量对比。勾选后创建任务清单才会附带变更数验收项——相对所选基准的变更数超过上限时验收不通过；只设上限，不改变求解行为。</InfoTooltip>
                </span>
              </label>
              {task.changeLimitEnabled ? (
                <label className="block">
                  <span className="inline-flex items-center gap-1.5">
                    验收上限（变更数）
                    <InfoTooltip label="验收上限">已开启变更上限：相对所选基准的变更数超过这个上限时验收不通过；只设上限，不改变求解行为。</InfoTooltip>
                  </span>
                  <input
                    aria-label="变更数验收上限"
                    className="mt-1 h-9 w-24 rounded-md border border-zinc-300 bg-white px-2 text-sm tabular-nums outline-none focus:border-blue-500"
                    type="number"
                    min={0}
                    max={100000}
                    step={5}
                    value={task.maxChangesLimit}
                    onChange={(event) => task.setMaxChangesLimit(Math.max(0, Math.round(Number(event.target.value)) || 0))}
                  />
                </label>
              ) : null}
              <Badge tone="blue">{task.changeLimitEnabled ? "已选基准：清单将附带变更数验收项" : "已选基准：仅记录基准用于变更对比，未设变更上限"}</Badge>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * 「我理解的是……」确认卡：解析摘要 + 范围 + 本次要求 + 记忆动作回执，主按钮「确认并开始求解」。
 * 范围被手动扩大的二次确认、未落实的要求、覆盖警告都直接展示在这张卡上，不折叠。
 */
export function ConfirmCard({ task, scheduleList }: { task: AssistantTask; scheduleList: ScheduleSummaryResponse[] }) {
  const { interpretation, params } = task;
  if (!interpretation) return null;
  const unresolved = Boolean(interpretation.unsupported_requirements?.length);
  const canParse = task.probe.ready === true && !task.submitBlockedReason;
  const engine = interpretation.source === "feishu_aily" ? "Aily（可选通道）" : task.probe.engine;
  return (
    <section aria-label="我理解的需求" className="rounded-lg border border-blue-200 bg-blue-50/40 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles className="size-4 text-blue-600" />
        <h2 className="font-semibold">我理解的是……</h2>
        <span className="text-xs text-zinc-500">确认无误后再开始排课</span>
      </div>
      <p className="mt-3 border-l-2 border-blue-400 bg-white/70 px-3 py-2 text-sm text-zinc-700">{plainInterpretSummary(interpretation.summary)}；解析来源：{engine}。</p>
      <InterpretThought thinking={interpretation.thinking ?? ""} seconds={task.parsedSeconds} />
      <div className="mt-4 grid gap-3 border-t border-blue-200 pt-4 text-sm md:grid-cols-3">
        <ScopeItem label="业务线" values={params.business_lines} />
        <ScopeItem label="产品班型" values={params.product_types} />
        <ScopeItem label="班级范围" values={params.class_business_ids} />
        <ScopeItem label="日期范围" values={[params.date_from, params.date_to].filter(Boolean) as string[]} />
        <ScopeItem label="日期调整窗口" values={[`${params.date_window_days} 天`]} />
        <ScopeItem label="识别到的规则" values={interpretation.recognized_rules} />
      </div>
      <button
        type="button"
        className="mt-2 text-xs text-blue-700 underline-offset-2 hover:underline"
        onClick={() => task.openManual({ focusScope: true })}
      >
        修改范围、日期或求解时限
      </button>
      {interpretation.task_constraints?.length ? <TaskConstraintsPanel constraints={interpretation.task_constraints} /> : null}
      {interpretation.memory_action_receipts?.length ? <MemoryReceiptsPanel receipts={interpretation.memory_action_receipts} /> : null}
      {interpretation.coverage_warnings?.length ? (
        <div className="mt-3 space-y-1 text-xs text-amber-900">
          {interpretation.coverage_warnings.map((warning) => <p key={warning}>{warning}</p>)}
        </div>
      ) : null}
      <UnsupportedBlock
        interpretation={interpretation}
        goal={task.goal}
        goalId={task.goalId}
        supplementing={task.supplementing}
        busy={task.goalBusy}
        onStartSupplement={() => void task.startSupplement()}
        onSaved={() => void task.interpret(interpretation.instruction)}
      />
      {task.trackForced ? (
        <p role="status" className="mt-2 text-xs text-zinc-600">补充的条件要写进任务里才会生效，已经替你打开了「更多选项」里的「以此为目标跟踪」。</p>
      ) : null}
      <MoreOptions task={task} scheduleList={scheduleList} />
      {task.staleInterpretation ? (
        <p role="alert" className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">{task.staleInterpretation}</p>
      ) : null}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button onClick={() => void task.solveFromInterpretation()} disabled={unresolved || task.goalBusy || task.pending || Boolean(task.submitBlockedReason) || Boolean(task.staleInterpretation)}>
          <Play className="size-4" />{task.goalBusy ? "正在登记任务…" : "确认并开始求解"}
        </Button>
        <Button variant="outline" onClick={() => void task.interpret(interpretation.instruction)} disabled={!canParse || task.phase === "thinking"}>
          <RefreshCw className="size-4" />重新解析
        </Button>
        <button
          type="button"
          className="text-xs text-zinc-500 underline-offset-2 hover:text-zinc-800 hover:underline"
          onClick={() => task.setRefine(task.refine.open ? { open: false, text: "" } : { open: true, text: interpretation.instruction })}
        >
          {task.refine.open ? "收起修改" : "修改需求…"}
        </button>
      </div>
      {task.refine.open ? (
        <RefineInput
          label="修改后的需求"
          placeholder="改写这次的需求，再重新解析"
          value={task.refine.text}
          onChange={(text) => task.setRefine({ open: true, text })}
          onSubmit={() => void task.interpret(task.refine.text)}
          onCancel={() => task.setRefine({ open: false, text: "" })}
          disabled={!canParse || task.phase === "thinking"}
          disabledReason={
            task.probe.ready === true
              ? task.submitBlockedReason ?? undefined
              : <AiProbeNotice probe={task.probe} missingText="AI 尚未接入，无法重新解析；可用「手动排课」直接设置参数。" />
          }
        />
      ) : null}
    </section>
  );
}
