import { Crosshair } from "lucide-react";

import { type AssistantTask } from "@/components/assistant/use-assistant-task";
import { Button } from "@/components/ui/button";

/**
 * 课表「交给助手继续处理」带来的调整对象，以及它变成任务约定之后的样子：
 * - 交接还在时：说明这次基于哪一版课表、哪一节课；读不到就如实说明并挡住求解（绝不悄悄退回「当前已发布版本 + 整批范围」）；
 * - 交接对象已经用完（求解产出了草稿）或续办恢复时：课次限定仍在共享的排课范围里，这里继续说明「本任务只针对这几节课」，
 *   重跑、继续调整都沿用；取消它是扩大范围，已提交过的限定要单独确认。
 */
export function HandoffNotice({ task }: { task: AssistantTask }) {
  const { handoff } = task;
  const lessons = task.params.course_business_ids;
  if (!handoff && !lessons.length) return null;
  const ready = handoff?.status === "ready";
  const trouble = handoff?.status === "error" || handoff?.status === "missing";
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
        {handoff ? (
          <p className="mt-0.5 break-words">{handoff.description}</p>
        ) : (
          <p className="mt-0.5 break-words">本任务只针对课表里选中的 {lessons.length} 节课：{lessons.slice(0, 3).join("、")}{lessons.length > 3 ? " 等" : ""}。</p>
        )}
        {ready || !handoff ? <p className="mt-0.5 text-blue-800/80">确认排课、手动排课、加预算重跑和继续调整都会沿用这个课次范围；放宽范围需要单独确认。</p> : null}
        {ready ? (
          <p className="mt-0.5 text-blue-800/80">
            {task.trackGoal
              ? "求解基准是这一版；产出草稿后，继续调整以这个任务自己的工作草稿为基准，不会退回当前已发布版本。"
              : "求解基准是这一版；没有跟踪成任务时，产出草稿后继续调整会以当前已发布版本为基准。"}
          </p>
        ) : null}
        {trouble ? <p className="mt-0.5">读取好之前不能开始排课；也可以取消这一节课的限定，改为按需求整体排课。</p> : null}
      </div>
      <Button size="sm" variant="outline" onClick={task.clearLessonScope}>{trouble ? "取消限定" : "不限定课次"}</Button>
    </section>
  );
}
