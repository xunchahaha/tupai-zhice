import { Bot, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";

import { HandoffNotice } from "@/components/assistant/handoff-notice";
import { InterpretFailure, InterpretProgress } from "@/components/assistant/interpret-progress";
import { type AssistantTask } from "@/components/assistant/use-assistant-task";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { settingsPath } from "@/lib/routes";

/**
 * 首页的需求输入卡：一句话交代要排/调什么 →「让 AI 解析」。
 * 「手动排课（自己设置参数）」紧挨着，任何时候都可用——不需要先解析成功，也不依赖 AI 是否配置。
 */
export function RequestCard({ task }: { task: AssistantTask }) {
  const { probe } = task;
  const interpreting = task.phase === "thinking";
  const canParse = probe.ready === true;
  return (
    <section aria-label="交代需求" className="rounded-lg border border-blue-200 bg-blue-50/40 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Bot className="size-4 text-blue-600" />
        <h2 className="font-semibold">告诉助手你要排什么</h2>
        <Badge tone={probe.ready === true ? "green" : "yellow"}>
          {probe.ready === true ? `${probe.engine} 已接入` : probe.ready === false ? "AI 模型待配置" : probe.probeError ? "AI 配置读取失败" : "正在读取 AI 配置"}
        </Badge>
        {probe.probeError ? <Button size="sm" variant="outline" onClick={probe.probe}>重试</Button> : null}
      </div>
      <p className="mt-2 text-xs text-zinc-500">用一句话说清楚要排或要调什么，助手先解析给你确认，再排出草稿；发布始终由有权限的人决定。</p>
      <textarea
        aria-label="排课需求"
        className="mt-4 min-h-24 w-full rounded-md border border-zinc-300 bg-white p-3 text-sm outline-none focus:border-blue-500 disabled:bg-zinc-50 disabled:text-zinc-400"
        placeholder="例如：下周 A 班调课，张老师周三晚上不能上，先出草稿"
        value={task.instruction}
        disabled={interpreting}
        onChange={(event) => task.editInstruction(event.target.value)}
      />
      {task.handoff ? <div className="mt-3"><HandoffNotice task={task} /></div> : null}
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <Button onClick={() => void task.interpret()} disabled={!canParse || interpreting || task.instruction.trim().length < 2}>
          <Sparkles className="size-4" />{interpreting ? "AI 正在理解需求" : "让 AI 解析"}
        </Button>
        {interpreting ? <Button variant="outline" onClick={task.cancelInterpret}>取消解析</Button> : null}
        <button
          type="button"
          aria-expanded={task.manualOpen}
          className="text-sm text-zinc-500 underline-offset-2 transition-colors hover:text-zinc-900 hover:underline"
          onClick={task.toggleManual}
        >
          手动排课（自己设置参数）
        </button>
      </div>
      {probe.ready === false ? (
        <div className="mt-4 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div>还没有配置 AI：需求解析暂时不可用。可以先用「手动排课」直接设置参数排课，配置好 AI 后再用一句话交代需求。</div>
          <Link className="mt-2 inline-block text-sm text-blue-700 underline-offset-2 hover:underline" to={settingsPath("ai")}>去配置</Link>
        </div>
      ) : null}
      {interpreting ? <InterpretProgress stageIndex={task.stageIndex} elapsedSeconds={task.elapsedSeconds} liveText={task.liveThinking} /> : null}
      {task.phase === "failed" ? (
        <InterpretFailure message={task.interpretError} elapsedSeconds={task.elapsedSeconds} canRetry={canParse} onRetry={task.retryInterpret} />
      ) : null}
    </section>
  );
}
