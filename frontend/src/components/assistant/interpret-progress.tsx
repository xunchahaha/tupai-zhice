import { RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { INTERPRET_STAGES } from "@/lib/assistant-task";

/** 解析进行中的阶段进度区：左侧竖线 + 浅色斜体，当前阶段用现有 animate-pulse 呼吸。 */
export function InterpretProgress({ stageIndex, elapsedSeconds, liveText }: { stageIndex: number; elapsedSeconds: number; liveText: string }) {
  // 思考增量持续到达时跟随滚动到底部，最新的推理始终可见（jsdom 无 scrollTo，须容错）。
  const liveRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => { liveRef.current?.scrollTo?.({ top: liveRef.current.scrollHeight }); }, [liveText]);
  return (
    <div className="mt-4 border-l-2 border-zinc-200 pl-3">
      <ol className="space-y-1 text-xs italic">
        {INTERPRET_STAGES.map((stage, index) => {
          const done = index < stageIndex;
          const current = index === stageIndex;
          return (
            <li key={stage.key} className={"flex items-center gap-1.5 " + (current ? "animate-pulse text-zinc-600" : done ? "text-zinc-400" : "text-zinc-300")}>
              <span aria-hidden>{done ? "✓" : current ? "…" : "○"}</span>
              {stage.text}
            </li>
          );
        })}
      </ol>
      {liveText ? (
        <div ref={liveRef} className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap border-l-2 border-zinc-200 pl-3 text-xs italic leading-5 text-zinc-500">
          {liveText}
        </div>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs not-italic text-zinc-500">
        <span className="tabular-nums">已用时 {elapsedSeconds.toFixed(1)} 秒</span>
        {elapsedSeconds > 30 ? <span className="text-amber-700">解析耗时较长，可取消后重试</span> : null}
      </div>
    </div>
  );
}

/** 思考过程回看：完成后默认折叠为「已解析完成（用时 N 秒）」，正文限高防长思考撑爆页面。 */
export function InterpretThought({ thinking, seconds }: { thinking: string; seconds: number }) {
  const [expanded, setExpanded] = useState(false);
  if (!thinking) {
    // Aily 或无思考模型：只给阶段完成与用时，不放空折叠块。
    return <p className="mt-3 border-l-2 border-zinc-200 pl-3 text-xs text-zinc-500">已解析完成 · 用时 {seconds.toFixed(1)} 秒 · 本次模型未输出思考过程</p>;
  }
  return (
    <div className="mt-3">
      <button type="button" className="text-xs text-zinc-500 transition-colors hover:text-zinc-700" onClick={() => setExpanded((value) => !value)}>
        {expanded ? "收起思考过程" : `已解析完成（用时 ${seconds.toFixed(1)} 秒）· 展开回看思考过程`}
      </button>
      {expanded ? (
        <div className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap border-l-2 border-zinc-200 pl-3 text-xs italic leading-5 text-zinc-500">
          {thinking}
        </div>
      ) : null}
    </div>
  );
}

/** 解析失败的页内错误条：保留「重试解析」，超过 30 秒的提示引导改用手动排课。 */
export function InterpretFailure({
  message,
  elapsedSeconds,
  canRetry,
  onRetry,
}: {
  message: string;
  elapsedSeconds: number;
  canRetry: boolean;
  onRetry: () => void;
}) {
  return (
    <div role="alert" className="mt-4 border-l-2 border-red-500 bg-red-50 px-4 py-3 text-sm text-red-800">
      <div>AI 解析失败：{message}</div>
      {elapsedSeconds > 30 ? <p className="mt-1 text-xs text-amber-800">本次解析超过 30 秒仍未返回，可稍后重试，或改用手动排课。</p> : null}
      <Button className="mt-2" size="sm" variant="outline" onClick={onRetry} disabled={!canRetry}>
        <RefreshCw className="size-3.5" />重试解析
      </Button>
    </div>
  );
}
