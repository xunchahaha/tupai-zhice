/**
 * 局部调课的前端口径：调课任务是异步的，「创建成功」只代表任务已入队，不代表候选课表已经生成；
 * 事件范围也必须让教务看得见——只调选中的一节课，还是登记成影响一批课的事件。
 */

/** 调课事件此刻的结局：只有 ready 才有可以打开的候选草稿。 */
export type RescheduleOutcome = "generating" | "ready" | "no_candidate" | "failed" | "discarded";

export function rescheduleOutcome(event: { status?: string | null; candidate_schedule_id?: string | null }): RescheduleOutcome {
  // 有候选编号才算「已生成」：状态字单独不作数（候选被删除后后端会同时置空编号并改状态）。
  if (event.candidate_schedule_id) return "ready";
  if (event.status === "failed") return "failed";
  if (event.status === "no_candidate") return "no_candidate";
  if (event.status === "candidate_discarded") return "discarded";
  return "generating";
}

/** 展示影响范围要用到的课次字段（AssignmentResponse 的子集）。 */
interface ImpactAssignment {
  teacher_business_id?: string | null;
  room_business_id?: string | null;
  slot_business_id?: string | null;
  lesson_date?: string | null;
}

export interface RescheduleImpact {
  /** 会进入调整范围的课次数（与后端邻域的种子口径一致：该教师/教室的全部课次）。 */
  total: number;
  /** 其中恰好落在所选时段的课次数；没选时段则等于 total。 */
  inSlot: number;
  dateFrom: string | null;
  dateTo: string | null;
}

/** 登记成「教师请假 / 教室停用」事件（不限定某一节课）时，这份课表里有多少课次会被卷进来。 */
export function rescheduleImpact(
  assignments: ImpactAssignment[],
  target: { eventType: "teacher_leave" | "room_outage"; teacherId?: string; roomId?: string; slotId?: string },
): RescheduleImpact {
  const matched = assignments.filter((item) =>
    target.eventType === "teacher_leave"
      ? Boolean(target.teacherId) && item.teacher_business_id === target.teacherId
      : Boolean(target.roomId) && item.room_business_id === target.roomId,
  );
  const dates = matched.map((item) => item.lesson_date).filter((value): value is string => Boolean(value)).sort();
  return {
    total: matched.length,
    inSlot: target.slotId ? matched.filter((item) => item.slot_business_id === target.slotId).length : matched.length,
    dateFrom: dates[0] ?? null,
    dateTo: dates[dates.length - 1] ?? null,
  };
}
