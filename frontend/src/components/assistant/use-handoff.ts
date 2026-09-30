import { useCallback, useState } from "react";

import { useGetScheduleApiV1SchedulesScheduleIdGet } from "@/api/generated/client";
import { type AssignmentResponse } from "@/api/generated/models";
import { asArray, formatSlot } from "@/lib/format";
import { statusLabel } from "@/lib/labels";

/**
 * 从课表页「交给助手继续处理」带来的调整对象：所选课表版本 + 具体某一节课。
 * 一句话（?prompt=）只给人读，业务身份靠这里的 base（课表版本）与 lesson（课次业务号）——
 * 求解基准、目标课次都来自它，不靠模型从一句话里猜。
 */
export interface HandoffTarget {
  scheduleId: string;
  lessonId: string;
}

export type HandoffState =
  | { status: "loading" | "error" | "missing"; target: HandoffTarget; description: string }
  | { status: "ready"; target: HandoffTarget; description: string };

/**
 * 交接对象的生命周期：URL 里的 base/lesson 只在进入助手时消费一次（调用方 adopt 记进状态后从地址栏摘掉，
 * 与 ?prompt= 同口径），之后由状态承载；求解一开始、重置任务或用户主动取消就清掉。
 * 读不到所选版本/课次时如实说明——绝不能悄悄退回「当前已发布版本 + 整批范围」。
 */
export function useHandoff(): { handoff: HandoffState | null; adopt: (target: HandoffTarget) => void; clear: () => void } {
  const [target, setTarget] = useState<HandoffTarget | null>(null);
  const adopt = useCallback((next: HandoffTarget) => setTarget(next), []);
  const clear = useCallback(() => setTarget(null), []);
  const detail = useGetScheduleApiV1SchedulesScheduleIdGet(target?.scheduleId ?? "", { query: { enabled: Boolean(target) } });
  if (!target) return { handoff: null, adopt, clear };
  if (detail.isError) return { handoff: { status: "error", target, description: "没能读到所选课表版本，无法确认要调整的是哪一节课。" }, adopt, clear };
  if (!detail.data) return { handoff: { status: "loading", target, description: "正在读取所选课次……" }, adopt, clear };
  const lesson = asArray<AssignmentResponse>(detail.data.assignments).find((item) => item.course_business_id === target.lessonId);
  const version = `v${detail.data.version_no}（${statusLabel(detail.data.status)}）`;
  if (!lesson) return { handoff: { status: "missing", target, description: `所选课次不在课表 ${version}里，无法作为调整对象。` }, adopt, clear };
  const when = [lesson.lesson_date, formatSlot(lesson.slot_business_id)].filter(Boolean).join(" ");
  return {
    handoff: {
      status: "ready",
      target,
      description: `基于课表 ${version}的这一节课：${when}，班级 ${lesson.class_business_id}，教师 ${lesson.teacher_business_id}`,
    },
    adopt,
    clear,
  };
}
