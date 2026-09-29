import { useQueryClient } from "@tanstack/react-query";
import { CalendarPlus, DoorClosed, UserRoundX } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { getListRescheduleEventsApiV1RescheduleEventsGetQueryKey, getListSchedulesApiV1SchedulesGetQueryKey, useCreateRescheduleEventApiV1RescheduleEventsPost, useListRescheduleEventsApiV1RescheduleEventsGet, useListRoomsApiV1RoomsGet, useListSchedulesApiV1SchedulesGet, useListTeachersApiV1TeachersGet, useListTimeSlotsApiV1TimeSlotsGet } from "@/api/generated/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { datetime, errorMessage } from "@/lib/format";
import { eventTypeLabel, statusLabel } from "@/lib/labels";
import { schedulePath } from "@/lib/routes";
import { preferredSchedule } from "@/lib/schedule";
import { statusTone } from "@/lib/status";

type EventType = "teacher_leave" | "room_outage" | "extra_class";

// 一键归因 chips（02 文档 §3.3）：区分「想换」与「被迫换」，是偏好挖掘的消噪关键。
// declared_reason 是自由文本（≤80 字），其他选项直接落教务输入的原话。
const REASON_CHIPS: { value: string; label: string }[] = [
  { value: "教师要求", label: "教师要求" },
  { value: "教室冲突", label: "教室冲突" },
  { value: "临时公差", label: "临时公差" },
  { value: "other", label: "其他" },
];

/** 课表里选中的课次带进来的预填值：教师请假用该课教师、教室停用用该课教室、影响时段用该课时段。 */
export interface ReschedulePrefill {
  /** 课次业务号，仅用来识别「换了一节课」以便重新预填。 */
  lessonId: string;
  /** 给人看的一句话，例如「三年二班 10月12日 周三晚」。 */
  label: string;
  teacherId?: string;
  roomId?: string;
  slotId?: string;
}

export interface ReschedulePageProps {
  /** 并入课表页时不再渲染自己的页头；默认 false 保持独立页面行为。 */
  embedded?: boolean;
  /** 课表页头部所选版本，作为「父课表」的默认值（表单里仍可改）。 */
  parentScheduleId?: string;
  prefill?: ReschedulePrefill;
}

export function ReschedulePage({ embedded = false, parentScheduleId, prefill }: ReschedulePageProps = {}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient(); const events = useListRescheduleEventsApiV1RescheduleEventsGet(); const schedules = useListSchedulesApiV1SchedulesGet(); const teachers = useListTeachersApiV1TeachersGet(); const rooms = useListRoomsApiV1RoomsGet(); const slots = useListTimeSlotsApiV1TimeSlotsGet();
  const [eventType, setEventType] = useState<EventType>("teacher_leave"); const [parent, setParent] = useState(parentScheduleId ?? ""); const [teacher, setTeacher] = useState(prefill?.teacherId ?? ""); const [room, setRoom] = useState(prefill?.roomId ?? ""); const [slot, setSlot] = useState(prefill?.slotId ?? ""); const [description, setDescription] = useState("");
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [reasonChoice, setReasonChoice] = useState<string | null>(null); const [reasonText, setReasonText] = useState("");
  const declaredReason = reasonChoice === null ? null : reasonChoice === "other" ? (reasonText.trim() || null) : reasonChoice;
  useEffect(() => { const initialSchedule = preferredSchedule(schedules.data); if (!parent && initialSchedule) setParent(initialSchedule.id); if (!teacher && teachers.data?.[0]) setTeacher(teachers.data[0].business_id); if (!room && rooms.data?.[0]) setRoom(rooms.data[0].business_id); if (!slot && slots.data?.[0]) setSlot(slots.data[0].business_id); }, [parent, room, rooms.data, slot, slots.data, schedules.data, teacher, teachers.data]);
  // 课表头部换了版本，表单的父课表跟着走；之后用户在表单里手动改仍然有效。
  useEffect(() => { if (parentScheduleId) setParent(parentScheduleId); }, [parentScheduleId]);
  // 换选另一节课才重新预填，避免覆盖用户在表单里已经改过的值。
  const prefillLessonId = prefill?.lessonId; const prefillTeacher = prefill?.teacherId; const prefillRoom = prefill?.roomId; const prefillSlot = prefill?.slotId;
  useEffect(() => { if (prefillTeacher) setTeacher(prefillTeacher); if (prefillRoom) setRoom(prefillRoom); if (prefillSlot) setSlot(prefillSlot); }, [prefillLessonId, prefillTeacher, prefillRoom, prefillSlot]);
  const create = useCreateRescheduleEventApiV1RescheduleEventsPost({ mutation: { onSuccess: (event) => { toast.success("局部调课任务已创建"); void queryClient.invalidateQueries({ queryKey: getListRescheduleEventsApiV1RescheduleEventsGetQueryKey() }); void queryClient.invalidateQueries({ queryKey: getListSchedulesApiV1SchedulesGetQueryKey() }); setCandidateId(event.candidate_schedule_id ?? null); setDescription(""); setReasonChoice(null); setReasonText(""); }, onError: (error) => toast.error(errorMessage(error)) } });
  const all = [events, schedules, teachers, rooms, slots]; if (all.some((item) => item.isPending)) return <LoadingState />; if (all.some((item) => item.isError)) return <ErrorState retry={() => all.forEach((item) => void item.refetch())} />;
  const submit = () => create.mutate({ data: { event_type: eventType, description: description || typeLabel(eventType), parent_schedule_id: parent, declared_reason: declaredReason, teacher_business_id: eventType === "teacher_leave" ? teacher : null, room_business_id: eventType === "room_outage" ? room : null, slot_business_ids: slot ? [slot] : [], course_business_id: null, time_limit_seconds: 30 } });
  const scheduleList = Array.isArray(schedules.data) ? schedules.data : [];
  const teacherList = Array.isArray(teachers.data) ? teachers.data : [];
  const roomList = Array.isArray(rooms.data) ? rooms.data : [];
  const slotList = Array.isArray(slots.data) ? slots.data : [];
  const eventList = Array.isArray(events.data) ? events.data : [];

  return (
    <div className="space-y-5 animate-fade-in">
      {embedded ? null : <PageHeader title="局部调课" />}
      <div className="grid gap-2 xl:grid-cols-[390px_minmax(0,1fr)]">
        <section className="border border-zinc-200 bg-white p-5">
          <div className="flex items-center gap-2">
            <CalendarPlus className="size-4 text-blue-600" />
            <h2 className="font-semibold">创建变更事件</h2>
          </div>
          {prefill ? (
            <p className="mt-3 rounded-md border border-blue-100 bg-blue-50/60 px-3 py-2 text-xs leading-5 text-blue-900">
              已按所选课次带入：{prefill.label}。下面的教师、教室和影响时段仍可修改。
            </p>
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
              value={parent}
              onChange={(event) => setParent(event.target.value)}
            >
              {scheduleList.map((item) => (
                <option key={item.id} value={item.id}>
                  v{item.version_no} / {statusLabel(item.status)}
                </option>
              ))}
            </Select>
          </label>
          {eventType === "teacher_leave" ? (
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
          {eventType === "room_outage" ? (
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
          <Button className="mt-5 w-full" onClick={submit} disabled={!parent || create.isPending}>
            生成候选方案
          </Button>
          {/* 生成、发布是两个独立动作：候选方案只是草稿，去历史版本核对后由有审批权限的人发布。 */}
          {create.isSuccess ? (
            <div className="mt-3 space-y-2">
              <p className="text-xs leading-5 text-zinc-500">候选方案已生成为草稿，尚未生效；需由有审批权限的人在「历史版本」核对后发布。</p>
              <Button className="w-full" variant="outline" onClick={() => navigate(schedulePath({ view: "history", version: candidateId ?? undefined }))}>
                在历史版本中查看新草稿
              </Button>
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
