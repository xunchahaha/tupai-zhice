import { useQueryClient } from "@tanstack/react-query";
import { CalendarPlus, DoorClosed, UserRoundX } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { getListRescheduleEventsApiV1RescheduleEventsGetQueryKey, getListSchedulesApiV1SchedulesGetQueryKey, useCreateRescheduleEventApiV1RescheduleEventsPost, useGetScheduleApiV1SchedulesScheduleIdGet, useListRescheduleEventsApiV1RescheduleEventsGet, useListRoomsApiV1RoomsGet, useListSchedulesApiV1SchedulesGet, useListTeachersApiV1TeachersGet, useListTimeSlotsApiV1TimeSlotsGet } from "@/api/generated/client";
import { type AssignmentResponse, type RescheduleResponse } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { asArray, datetime, errorMessage } from "@/lib/format";
import { eventTypeLabel, statusLabel } from "@/lib/labels";
import { rescheduleImpact, rescheduleOutcome } from "@/lib/reschedule";
import { schedulePath } from "@/lib/routes";
import { preferredSchedule } from "@/lib/schedule";
import { statusTone } from "@/lib/status";

type EventType = "teacher_leave" | "room_outage" | "extra_class";

/** 创建后跟踪事件结果的轮询间隔：后台求解完成前每隔一会儿刷新一次事件列表。 */
const EVENT_POLL_MS = 1500;

// 一键归因 chips（02 文档 §3.3）：区分「想换」与「被迫换」，是偏好挖掘的消噪关键。
// declared_reason 是自由文本（≤80 字），其他选项直接落教务输入的原话。
const REASON_CHIPS: { value: string; label: string }[] = [
  { value: "教师要求", label: "教师要求" },
  { value: "教室冲突", label: "教室冲突" },
  { value: "临时公差", label: "临时公差" },
  { value: "other", label: "其他" },
];

/**
 * 课表里选中的课次带进来的对象：教师请假用该课教师、教室停用用该课教室、影响时段用该课时段。
 * 「只调整这一节课」时它就是请求的范围——课次号 + 具体日期 + 所选版本一起提交，
 * 后端按这个范围执行，不会扩大到同一教师/教室的其他课次。
 */
export interface ReschedulePrefill {
  /** 课次业务号：既用来识别「换了一节课」重新预填，也是「只调整这一节课」提交给后端的课次身份。 */
  lessonId: string;
  /** 给人看的一句话，例如「三年二班 10月12日 周三晚」。 */
  label: string;
  teacherId?: string;
  roomId?: string;
  slotId?: string;
  /** 这节课具体的上课日期（YYYY-MM-DD）；同一教师同一时段每周都有课，只有带上日期才能限定到这一天。 */
  lessonDate?: string | null;
  /** 选中这节课时所看的课表版本：课次身份只在这一版里成立，调整基准就是它。 */
  scheduleId?: string;
}

/**
 * 链接指定了课次（?lesson=），但没能定位到它：定位好之前不能提交，也不会悄悄变成普通的教师/教室事件。
 * 只有用户明确「取消单课限定，改为登记事件」才切到事件模式。
 */
export interface LessonProblem {
  kind: "loading" | "error" | "missing" | "version_missing";
  lessonId: string;
  retry?: () => void;
}

const LESSON_PROBLEM_TEXT: Record<LessonProblem["kind"], string> = {
  loading: "正在定位所选课次……定位好之前不能提交。",
  error: "没能读到所选课表版本，无法确认要调整的是哪一节课，不能提交。",
  missing: "所选课次不在这一版课表里（可能已被调整或删除），不能按这一节课提交。",
  version_missing: "链接里指定的课表版本不存在，无法定位所选课次；不会自动换成别的版本，也不能提交。",
};

export interface ReschedulePageProps {
  /** 并入课表页时不再渲染自己的页头；默认 false 保持独立页面行为。 */
  embedded?: boolean;
  /** 课表页头部所选版本，作为「父课表」的默认值（表单里仍可改）。 */
  parentScheduleId?: string;
  prefill?: ReschedulePrefill;
  /** 链接声明了课次但没定位成功；有它且用户没取消限定时，表单处于「待定位」状态（不可提交）。 */
  lessonProblem?: LessonProblem;
}

export function ReschedulePage({ embedded = false, parentScheduleId, prefill, lessonProblem }: ReschedulePageProps = {}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  // 刚创建的调课事件：创建成功只代表任务入队，候选课表要跟踪到事件本身给出结果。
  const [created, setCreated] = useState<RescheduleResponse | null>(null);
  const events = useListRescheduleEventsApiV1RescheduleEventsGet({
    query: {
      refetchInterval: (query) => {
        const tracked = created ? (asArray<RescheduleResponse>(query.state.data).find((item) => item.id === created.id) ?? created) : null;
        return tracked && rescheduleOutcome(tracked) === "generating" ? EVENT_POLL_MS : false;
      },
    },
  });
  const schedules = useListSchedulesApiV1SchedulesGet(); const teachers = useListTeachersApiV1TeachersGet(); const rooms = useListRoomsApiV1RoomsGet(); const slots = useListTimeSlotsApiV1TimeSlotsGet();
  const [eventType, setEventType] = useState<EventType>("teacher_leave"); const [parent, setParent] = useState(parentScheduleId ?? ""); const [teacher, setTeacher] = useState(prefill?.teacherId ?? ""); const [room, setRoom] = useState(prefill?.roomId ?? ""); const [slot, setSlot] = useState(prefill?.slotId ?? ""); const [description, setDescription] = useState("");
  // 选中了课次时：「只调整这一节课」（默认）还是「登记成变更事件」（该教师/教室的全部课次都会参与）。
  const [scope, setScope] = useState<"lesson" | "event">("lesson");
  const [includeNeighbors, setIncludeNeighbors] = useState(false);
  const [neighborhoodDays, setNeighborhoodDays] = useState(7);
  // 用户明确取消了对某个（没能定位的）课次的限定：只对这一个课次号生效，换课次又回到待定位。
  const [dismissedLesson, setDismissedLesson] = useState("");
  const awaitingLesson = Boolean(lessonProblem) && dismissedLesson !== lessonProblem?.lessonId;
  const [reasonChoice, setReasonChoice] = useState<string | null>(null); const [reasonText, setReasonText] = useState("");
  const declaredReason = reasonChoice === null ? null : reasonChoice === "other" ? (reasonText.trim() || null) : reasonChoice;
  useEffect(() => { const initialSchedule = preferredSchedule(schedules.data); if (!parent && initialSchedule) setParent(initialSchedule.id); if (!teacher && teachers.data?.[0]) setTeacher(teachers.data[0].business_id); if (!room && rooms.data?.[0]) setRoom(rooms.data[0].business_id); if (!slot && slots.data?.[0]) setSlot(slots.data[0].business_id); }, [parent, room, rooms.data, slot, slots.data, schedules.data, teacher, teachers.data]);
  // 课表头部换了版本，表单的父课表跟着走；之后用户在表单里手动改仍然有效。
  useEffect(() => { if (parentScheduleId) setParent(parentScheduleId); }, [parentScheduleId]);
  // 换选另一节课才重新预填，避免覆盖用户在表单里已经改过的值；范围也回到默认的「只调整这一节课」。
  const prefillLessonId = prefill?.lessonId; const prefillTeacher = prefill?.teacherId; const prefillRoom = prefill?.roomId; const prefillSlot = prefill?.slotId;
  useEffect(() => { if (prefillTeacher) setTeacher(prefillTeacher); if (prefillRoom) setRoom(prefillRoom); if (prefillSlot) setSlot(prefillSlot); }, [prefillLessonId, prefillTeacher, prefillRoom, prefillSlot]);
  useEffect(() => { setScope("lesson"); setIncludeNeighbors(false); }, [prefillLessonId]);
  // 「只调整这一节课」：课次身份、具体日期与版本都来自选课时的上下文，表单里不再让人改成别的教师/时段。
  const lessonMode = Boolean(prefill) && scope === "lesson" && eventType !== "extra_class";
  // 只有「没在等待定位」且不是单课模式时，才展示教师/教室/时段这些事件表单项。
  const eventFields = !lessonMode && !awaitingLesson;
  const lockedParent = lessonMode ? prefill?.scheduleId : undefined;
  const effectiveParent = lockedParent ?? parent;
  const effectiveTeacher = lessonMode ? (prefill?.teacherId ?? teacher) : teacher;
  const effectiveRoom = lessonMode ? (prefill?.roomId ?? room) : room;
  const effectiveSlot = lessonMode ? (prefill?.slotId ?? slot) : slot;
  // 登记成事件时展示影响范围：读这份课表数一数会有多少课次被卷进来。
  const impactWanted = !lessonMode && !awaitingLesson && eventType !== "extra_class" && Boolean(effectiveParent);
  const parentDetail = useGetScheduleApiV1SchedulesScheduleIdGet(effectiveParent, { query: { enabled: impactWanted } });
  const create = useCreateRescheduleEventApiV1RescheduleEventsPost({ mutation: { onSuccess: (event) => { toast.success("局部调课任务已创建"); void queryClient.invalidateQueries({ queryKey: getListRescheduleEventsApiV1RescheduleEventsGetQueryKey() }); void queryClient.invalidateQueries({ queryKey: getListSchedulesApiV1SchedulesGetQueryKey() }); setCreated(event); setDescription(""); setReasonChoice(null); setReasonText(""); }, onError: (error) => toast.error(errorMessage(error)) } });
  const all = [events, schedules, teachers, rooms, slots]; if (all.some((item) => item.isPending)) return <LoadingState />; if (all.some((item) => item.isError)) return <ErrorState retry={() => all.forEach((item) => void item.refetch())} />;
  const submit = () => awaitingLesson ? undefined : create.mutate({
    data: {
      event_type: eventType,
      description: description || (lessonMode && prefill ? `${typeLabel(eventType)}：${prefill.label}` : typeLabel(eventType)),
      parent_schedule_id: effectiveParent,
      declared_reason: declaredReason,
      teacher_business_id: eventType === "teacher_leave" ? effectiveTeacher : null,
      room_business_id: eventType === "room_outage" ? effectiveRoom : null,
      slot_business_ids: effectiveSlot ? [effectiveSlot] : [],
      // 只调整这一节课：范围就是这一节——课次号 + 那一天，其余课次不动；连带调整必须由人明确勾选。
      ...(lessonMode && prefill
        ? { course_business_id: prefill.lessonId, date_from: prefill.lessonDate ?? null, date_to: prefill.lessonDate ?? null, include_neighbors: includeNeighbors, neighborhood_days: neighborhoodDays }
        : { course_business_id: null }),
      time_limit_seconds: 30,
    },
  });
  const scheduleList = Array.isArray(schedules.data) ? schedules.data : [];
  const teacherList = Array.isArray(teachers.data) ? teachers.data : [];
  const roomList = Array.isArray(rooms.data) ? rooms.data : [];
  const slotList = Array.isArray(slots.data) ? slots.data : [];
  const eventList = Array.isArray(events.data) ? events.data : [];
  // 刚创建的事件以列表里的最新状态为准（后台求解完成后候选编号才会出现），列表还没带上时用创建响应兜底。
  const tracked = created ? (eventList.find((item) => item.id === created.id) ?? created) : null;
  const outcome = tracked ? rescheduleOutcome(tracked) : null;
  const subjectName = eventType === "teacher_leave"
    ? (teacherList.find((item) => item.business_id === effectiveTeacher)?.name ?? effectiveTeacher)
    : (roomList.find((item) => item.business_id === effectiveRoom)?.name ?? effectiveRoom);
  const parentAssignments = (parentDetail.data as { assignments?: unknown } | undefined)?.assignments;
  const impact = impactWanted && Array.isArray(parentAssignments)
    ? rescheduleImpact(parentAssignments as AssignmentResponse[], { eventType, teacherId: effectiveTeacher, roomId: effectiveRoom, slotId: effectiveSlot })
    : null;
  const slotText = (id: string) => { const item = slotList.find((entry) => entry.business_id === id); return item ? `${item.weekday} ${item.start_time}` : id; };

  return (
    <div className="space-y-5 animate-fade-in">
      {embedded ? null : <PageHeader title="局部调课" />}
      <div className="grid gap-2 xl:grid-cols-[390px_minmax(0,1fr)]">
        <section className="border border-zinc-200 bg-white p-5">
          <div className="flex items-center gap-2">
            <CalendarPlus className="size-4 text-blue-600" />
            <h2 className="font-semibold">创建变更事件</h2>
          </div>
          {prefill && eventType !== "extra_class" ? (
            <fieldset className="mt-3 space-y-2 rounded-md border border-blue-100 bg-blue-50/60 px-3 py-2.5 text-xs leading-5 text-blue-900">
              <legend className="px-1 text-xs font-medium text-blue-800">调整范围</legend>
              <label className="flex items-start gap-2">
                <input type="radio" name="reschedule-scope" className="mt-1" checked={scope === "lesson"} onChange={() => setScope("lesson")} />
                <span>
                  <span className="font-medium">只调整这一节课</span>
                  <span className="block text-blue-800/80">已按所选课次带入：{prefill.label}。其余课次保持不动。</span>
                </span>
              </label>
              {scope === "lesson" ? (
                <div className="ml-6 space-y-1.5 border-l-2 border-blue-200 pl-3 text-zinc-700">
                  <label className="flex items-center gap-2">
                    <input type="checkbox" checked={includeNeighbors} onChange={(event) => setIncludeNeighbors(event.target.checked)} />
                    允许连带调整同班级、同教室的邻近课次
                  </label>
                  {includeNeighbors ? (
                    <label className="flex flex-wrap items-center gap-1.5 text-zinc-600">
                      前后各
                      <input
                        type="number"
                        aria-label="连带调整的天数"
                        min={0}
                        max={31}
                        className="h-7 w-16 rounded-md border border-zinc-300 px-2 text-sm"
                        value={neighborhoodDays}
                        onChange={(event) => setNeighborhoodDays(Math.min(31, Math.max(0, Math.trunc(Number(event.target.value) || 0))))}
                      />
                      天内的课次可以被挪动，系统仍会尽量少改。
                    </label>
                  ) : (
                    <p className="text-zinc-500">不勾选时，其他课次只作为固定占用，不会被改动。</p>
                  )}
                </div>
              ) : null}
              <label className="flex items-start gap-2">
                <input type="radio" name="reschedule-scope" className="mt-1" checked={scope === "event"} onChange={() => setScope("event")} />
                <span>
                  <span className="font-medium">登记为{eventType === "teacher_leave" ? "教师请假" : "教室停用"}事件</span>
                  <span className="block text-blue-800/80">{eventType === "teacher_leave" ? "该教师" : "该教室"}在这份课表里的课次都会进入调整范围，不限于所选这一节。</span>
                </span>
              </label>
            </fieldset>
          ) : null}
          {awaitingLesson && lessonProblem ? (
            <div role="alert" className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2.5 text-xs leading-5 text-amber-900">
              <p>{LESSON_PROBLEM_TEXT[lessonProblem.kind]}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {lessonProblem.retry ? <Button size="sm" variant="outline" onClick={lessonProblem.retry}>重试</Button> : null}
                <Button size="sm" variant="outline" onClick={() => setDismissedLesson(lessonProblem.lessonId)}>取消单课限定，改为登记事件</Button>
              </div>
            </div>
          ) : null}
          <label className="mt-5 block text-sm">
            事件类型
            <Select
              aria-label="事件类型"
              selectSize="md"
              containerClassName="mt-1.5"
              value={eventType}
              onChange={(event) => setEventType(event.target.value as EventType)}
            >
              <option value="teacher_leave">教师请假</option>
              <option value="room_outage">教室停用</option>
              <option value="extra_class">临时加课</option>
            </Select>
          </label>
          <label className="mt-4 block text-sm">
            父课表
            <Select
              aria-label="父课表"
              selectSize="md"
              containerClassName="mt-1.5"
              value={effectiveParent}
              disabled={Boolean(lockedParent)}
              onChange={(event) => setParent(event.target.value)}
            >
              {scheduleList.map((item) => (
                <option key={item.id} value={item.id}>
                  v{item.version_no} / {statusLabel(item.status)}
                </option>
              ))}
            </Select>
          </label>
          {lessonMode && lockedParent ? (
            <p className="mt-1.5 text-xs text-zinc-500">课次身份只在选中它时看的这一版里成立，调整基准固定为这一版。</p>
          ) : null}
          {lessonMode ? (
            <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs" aria-label="所选课次">
              <dt className="text-zinc-500">课次</dt>
              <dd className="text-zinc-800">{prefill?.label}</dd>
              {prefill?.lessonDate ? <dt className="text-zinc-500">日期</dt> : null}
              {prefill?.lessonDate ? <dd className="text-zinc-800">{prefill.lessonDate}</dd> : null}
              <dt className="text-zinc-500">{eventType === "teacher_leave" ? "教师" : "教室"}</dt>
              <dd className="text-zinc-800">{subjectName}</dd>
              {effectiveSlot ? <dt className="text-zinc-500">时段</dt> : null}
              {effectiveSlot ? <dd className="text-zinc-800">{slotText(effectiveSlot)}</dd> : null}
            </dl>
          ) : null}
          {eventFields && eventType === "teacher_leave" ? (
            <label className="mt-4 block text-sm">
              教师
              <Select
                aria-label="教师"
                selectSize="md"
                containerClassName="mt-1.5"
                value={teacher}
                onChange={(event) => setTeacher(event.target.value)}
              >
                {teacherList.map((item) => (
                  <option key={item.id} value={item.business_id}>
                    {item.business_id} / {item.name}
                  </option>
                ))}
              </Select>
            </label>
          ) : null}
          {eventFields && eventType === "room_outage" ? (
            <label className="mt-4 block text-sm">
              教室
              <Select
                aria-label="教室"
                selectSize="md"
                containerClassName="mt-1.5"
                value={room}
                onChange={(event) => setRoom(event.target.value)}
              >
                {roomList.map((item) => (
                  <option key={item.id} value={item.business_id}>
                    {item.business_id} / {item.name}
                  </option>
                ))}
              </Select>
            </label>
          ) : null}
          {eventFields ? (
          <label className="mt-4 block text-sm">
            影响时段
            <Select
              aria-label="影响时段"
              selectSize="md"
              containerClassName="mt-1.5"
              value={slot}
              onChange={(event) => setSlot(event.target.value)}
            >
              {slotList.map((item) => (
                <option key={item.id} value={item.business_id}>
                  {item.business_id} / {item.weekday} {item.start_time}
                </option>
              ))}
            </Select>
          </label>
          ) : null}
          {eventFields && impact ? (
            <p role="status" className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
              登记为事件后，{subjectName}在这份课表里共有 {impact.total} 节课会进入调整范围
              {impact.dateFrom && impact.dateTo ? `（${impact.dateFrom} ~ ${impact.dateTo}）` : ""}，其中 {impact.inSlot} 节在所选时段；不限于某一天。
            </p>
          ) : null}
          <label className="mt-4 block text-sm">
            说明
            <textarea
              className="mt-1.5 min-h-20 w-full rounded-md border border-zinc-300 p-2"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
          <div className="mt-4">
            <span className="block text-sm">
              调课原因
              <span className="ml-1.5 text-xs text-zinc-400">选填，帮助系统学习</span>
            </span>
            <div className="mt-1.5 flex flex-wrap gap-1.5" role="group" aria-label="调课原因">
              {REASON_CHIPS.map((chip) => (
                <button
                  key={chip.value}
                  type="button"
                  aria-pressed={reasonChoice === chip.value}
                  className={`inline-flex h-7 items-center rounded-full border px-2.5 text-xs transition-all duration-150 active:scale-[0.97] ${
                    reasonChoice === chip.value
                      ? "border-blue-600 bg-blue-50 font-medium text-blue-700"
                      : "border-zinc-300 bg-white text-zinc-600 hover:border-zinc-400 hover:bg-zinc-50"
                  }`}
                  onClick={() => setReasonChoice((current) => (current === chip.value ? null : chip.value))}
                >
                  {chip.label}
                </button>
              ))}
            </div>
            {reasonChoice === "other" ? (
              <input
                aria-label="其他调课原因"
                className="mt-1.5 h-8 w-full rounded-md border border-zinc-300 px-2 text-sm"
                placeholder="一句话描述原因（选填）"
                maxLength={80}
                value={reasonText}
                onChange={(event) => setReasonText(event.target.value)}
              />
            ) : null}
          </div>
          <Button className="mt-5 w-full" onClick={submit} disabled={!effectiveParent || create.isPending || awaitingLesson}>
            生成候选方案
          </Button>
          {/* 创建成功只代表任务入队；候选草稿要等事件本身给出结果。生成、发布是两个独立动作：候选方案只是草稿，去历史版本核对后由有审批权限的人发布。 */}
          {tracked && outcome ? (
            <div className="mt-3 space-y-2" role="status" aria-label="调课结果">
              {outcome === "generating" ? (
                <p className="text-xs leading-5 text-zinc-500">正在生成候选方案……完成后这里会出现「查看新草稿」入口；也可以先离开，稍后在右侧「调课事件」里查看。</p>
              ) : null}
              {outcome === "ready" ? (
                <>
                  <p className="text-xs leading-5 text-zinc-500">候选方案已生成为草稿，尚未生效；需由有审批权限的人在「历史版本」核对后发布。</p>
                  <Button className="w-full" variant="outline" onClick={() => navigate(schedulePath({ view: "history", version: tracked.candidate_schedule_id ?? undefined }))}>
                    在历史版本中查看新草稿
                  </Button>
                </>
              ) : null}
              {outcome === "no_candidate" ? (
                <p className="text-xs leading-5 text-amber-800">这次没有找到可行的候选方案，没有生成草稿。可以放宽范围（例如允许连带调整邻近课次）或换个时段后重新生成。</p>
              ) : null}
              {outcome === "failed" ? (
                <p className="text-xs leading-5 text-red-700">求解没有完成，没有生成候选方案。可以调整后重新生成。</p>
              ) : null}
              {outcome === "discarded" ? (
                <p className="text-xs leading-5 text-zinc-500">这次生成的候选草稿已被删除，需要的话可以重新生成。</p>
              ) : null}
            </div>
          ) : null}
        </section>
        <section className="border border-zinc-200 bg-white">
          <div className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold">调课事件</div>
          <div className="divide-y divide-zinc-100">
            {eventList.length ? (
              eventList.map((event) => (
                <div key={event.id} className="flex gap-3 px-4 py-4">
                  <span className="mt-0.5 text-zinc-500">
                    {event.event_type === "teacher_leave" ? (
                      <UserRoundX className="size-4" />
                    ) : (
                      <DoorClosed className="size-4" />
                    )}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">{eventTypeLabel(event.event_type)}</span>
                      <Badge tone={statusTone(event.status)}>{statusLabel(event.status)}</Badge>
                      {event.declared_reason ? <Badge>{event.declared_reason}</Badge> : null}
                      {event.candidate_schedule_id ? (
                        <Button size="sm" variant="ghost" className="h-6 px-2 text-xs" onClick={() => navigate(schedulePath({ view: "history", version: event.candidate_schedule_id ?? undefined }))}>
                          查看候选草稿
                        </Button>
                      ) : null}
                    </div>
                    <p className="mt-1 text-sm text-zinc-600">{event.description}</p>
                    <div className="mt-2 font-mono text-[11px] text-zinc-400">
                      {event.id.slice(0, 8)} / {datetime(event.created_at)}
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <div className="grid min-h-48 place-items-center text-sm text-zinc-400">暂无调课事件</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function typeLabel(value: EventType) { return value === "teacher_leave" ? "教师请假" : value === "room_outage" ? "教室停用" : "临时加课"; }
