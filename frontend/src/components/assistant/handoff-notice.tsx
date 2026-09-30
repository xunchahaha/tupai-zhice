import { Crosshair } from "lucide-react";

import { type AssistantTask } from "@/components/assistant/use-assistant-task";
import { Button } from "@/components/ui/button";

/**
 * 从课表「交给助手继续处理」带来的调整对象：明说这次是基于哪一版课表、哪一节课，
 * 读不到时如实说明并挡住求解（绝不悄悄退回「当前已发布版本 + 整批范围」）。用户随时可以取消这一节课的限定。
 */
export function HandoffNotice({ task }: { task: AssistantTask }) {
  const { handoff } = task;
  if (!handoff) return null;
  const ready = handoff.status === "ready";
  const trouble = handoff.status === "error" || handoff.status === "missing";
  return (
    <section
      aria-label="调整对象"
      role="status"
      className={`flex flex-wrap items-start justify-between gap-3 rounded-md border px-3 py-2.5 text-xs leading-5 ${trouble ? "border-amber-300 bg-amber-50 text-amber-900" : "border-blue-200 bg-blue-50/60 text-blue-900"}`}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 font-medium">
          <Crosshair className="size-3.5" />调整对象
        </div>
        <p className="mt-0.5 break-words">{handoff.description}</p>
        {ready ? <p className="mt-0.5 text-blue-800/80">用「让 AI 解析」确认后排课，会以这一版为基准、只针对这一节课；手动排课按下方参数，不受这一节课限制。</p> : null}
        {trouble ? <p className="mt-0.5">读取好之前不能开始排课；也可以取消这一节课的限定，改为按需求整体排课。</p> : null}
      </div>
      <Button size="sm" variant="outline" onClick={task.clearHandoff}>{ready ? "不限定这一节课" : "取消限定"}</Button>
    </section>
  );
}
