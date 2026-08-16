import {
  BookOpen,
  Calendar,
  CalendarCheck2,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Clock,
  DoorOpen,
  Download,
  GraduationCap,
  LayoutGrid,
  ListFilter,
  Sparkles,
  Timer,
  User,
  Users,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  useGetScheduleApiV1SchedulesScheduleIdGet,
  useListClassGroupsApiV1ClassGroupsGet,
  useListCourseSessionsApiV1CourseSessionsGet,
  useListRoomsApiV1RoomsGet,
  useListSchedulesApiV1SchedulesGet,
  useListTeachersApiV1TeachersGet,
  useListTimeSlotsApiV1TimeSlotsGet,
} from "@/api/generated/client";
import type { AssignmentResponse, CourseSessionResponse, TimeSlotResponse } from "@/api/generated/models";
import { http } from "@/api/http";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { errorMessage, formatRoom, formatSlot } from "@/lib/format";
import { statusLabel } from "@/lib/labels";
import { preferredSchedule } from "@/lib/schedule";
import { cn } from "@/lib/cn";

type ViewMode = "class" | "teacher" | "room";
type LayoutStyle = "timeline" | "matrix";

const MODES: Array<{ value: ViewMode; label: string; icon: typeof Users }> = [
  { value: "class", label: "班级课表", icon: Users },
  { value: "teacher", label: "教师课表", icon: GraduationCap },
  { value: "room", label: "教室课表", icon: DoorOpen },
];

function clockRows(slots: TimeSlotResponse[]): Array<{ key: string; start: string; end: string }> {
  const seen = new Map<string, { key: string; start: string; end: string }>();
  for (const slot of slots) {
    const key = `${slot.start_time}-${slot.end_time}`;
    if (!seen.has(key)) seen.set(key, { key, start: slot.start_time, end: slot.end_time });
  }
  return [...seen.values()].sort((a, b) => a.start.localeCompare(b.start));
}

function slotClockKey(slots: TimeSlotResponse[], slotBusinessId: string): string {
  const slot = slots.find((item) => item.business_id === slotBusinessId);
  return slot ? `${slot.start_time}-${slot.end_time}` : "";
}

function getSlotTimeRange(slots: TimeSlotResponse[], slotBusinessId: string): string {
  const slot = slots.find((item) => item.business_id === slotBusinessId);
  if (slot?.start_time && slot?.end_time) {
    return `${slot.start_time} - ${slot.end_time}`;
  }
  return formatSlot(slotBusinessId);
}

function getMondayOfDate(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  const day = d.getDay();
  const diff = d.getDate() - day + (day === 0 ? -6 : 1);
  const monday = new Date(d.setDate(diff));
  const year = monday.getFullYear();
  const month = String(monday.getMonth() + 1).padStart(2, "0");
  const date = String(monday.getDate()).padStart(2, "0");
  return `${year}-${month}-${date}`;
}

function getWeekDays(mondayStr: string): Array<{ date: string; monthDay: string; weekday: string }> {
  const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
  const res = [];
  const monday = new Date(mondayStr + "T00:00:00");
  for (let i = 0; i < 7; i++) {
    const cur = new Date(monday);
    cur.setDate(monday.getDate() + i);
    const year = cur.getFullYear();
    const month = String(cur.getMonth() + 1).padStart(2, "0");
    const date = String(cur.getDate()).padStart(2, "0");
    const dateStr = `${year}-${month}-${date}`;
    res.push({
      date: dateStr,
      monthDay: `${cur.getMonth() + 1}月${cur.getDate()}日`,
      weekday: weekdays[i],
    });
  }
  return res;
}

function formatWeekday(dateStr: string): string {
  try {
    const d = new Date(dateStr + "T00:00:00");
    if (!Number.isNaN(d.getTime())) {
      const weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
      return weekdays[d.getDay()];
    }
  } catch {
    // ignore
  }
  return "";
}

export function SchedulePage() {
  const [scheduleId, setScheduleId] = useState("");
  const [mode, setMode] = useState<ViewMode>("class");
  const [layout, setLayout] = useState<LayoutStyle>("timeline");
  const [subject, setSubject] = useState("");
  const [activeWeekIndex, setActiveWeekIndex] = useState(0);

  const schedules = useListSchedulesApiV1SchedulesGet();
  const slots = useListTimeSlotsApiV1TimeSlotsGet();
  const classes = useListClassGroupsApiV1ClassGroupsGet();
  const teachers = useListTeachersApiV1TeachersGet();
  const rooms = useListRoomsApiV1RoomsGet();
  const courseSessions = useListCourseSessionsApiV1CourseSessionsGet();

  useEffect(() => {
    const initial = preferredSchedule(schedules.data);
    if (!scheduleId && initial) setScheduleId(initial.id);
  }, [scheduleId, schedules.data]);

  useEffect(() => {
    const source = mode === "class" ? classes.data : mode === "teacher" ? teachers.data : rooms.data;
    if (source?.[0]) setSubject(source[0].business_id);
    setActiveWeekIndex(0);
  }, [classes.data, mode, rooms.data, teachers.data]);

  const detail = useGetScheduleApiV1SchedulesScheduleIdGet(scheduleId, {
    query: { enabled: Boolean(scheduleId) },
  });
  const schedule = detail.data;
  const slotRows = useMemo(() => slots.data ?? [], [slots.data]);

  // Fast lookup for course details (subject, lesson name, stage, session_no)
  const courseMap = useMemo(() => {
    const map = new Map<string, CourseSessionResponse>();
    for (const cs of courseSessions.data ?? []) {
      map.set(cs.business_id, cs);
    }
    return map;
  }, [courseSessions.data]);

  const filtered = useMemo(
    () =>
      (schedule?.assignments ?? []).filter((item) =>
        mode === "class"
          ? item.class_business_id === subject
          : mode === "teacher"
            ? item.teacher_business_id === subject
            : item.room_business_id === subject,
      ),
    [mode, schedule, subject],
  );

  // Chronologically sorted assignments
  const sortedAssignments = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const dateA = a.lesson_date ?? "";
      const dateB = b.lesson_date ?? "";
      if (dateA !== dateB) return dateA.localeCompare(dateB);
      const slotA = slotRows.find((s) => s.business_id === a.slot_business_id)?.start_time ?? "";
      const slotB = slotRows.find((s) => s.business_id === b.slot_business_id)?.start_time ?? "";
      return slotA.localeCompare(slotB);
    });
  }, [filtered, slotRows]);

  // Group assignments by month for Timeline view
  const groupedByMonth = useMemo(() => {
    const groups: Array<{ monthKey: string; monthLabel: string; items: AssignmentResponse[] }> = [];
    const map = new Map<string, AssignmentResponse[]>();

    for (const item of sortedAssignments) {
      let key = "全部日程";
      let label = "全部课次安排";
      if (item.lesson_date) {
        const parts = item.lesson_date.split("-");
        if (parts.length >= 2) {
          key = `${parts[0]}-${parts[1]}`;
          label = `${parts[0]}年 ${Number(parts[1])}月`;
        }
      }
      if (!map.has(key)) {
        map.set(key, []);
        groups.push({ monthKey: key, monthLabel: label, items: map.get(key)! });
      }
      map.get(key)!.push(item);
    }

    return groups;
  }, [sortedAssignments]);

  // Active Weeks calculation for weekly calendar matrix
  const activeWeeks = useMemo(() => {
    const mondayMap = new Map<string, AssignmentResponse[]>();
    for (const item of sortedAssignments) {
      if (item.lesson_date) {
        const monday = getMondayOfDate(item.lesson_date);
        if (!mondayMap.has(monday)) {
          mondayMap.set(monday, []);
        }
        mondayMap.get(monday)!.push(item);
      }
    }

    const mondays = [...mondayMap.keys()].sort();
    return mondays.map((monday, idx) => {
      const days = getWeekDays(monday);
      const items = mondayMap.get(monday) ?? [];
      const startDay = days[0].monthDay;
      const endDay = days[6].monthDay;
      const isFirst = idx === 0;
      const isLast = idx === mondays.length - 1;

      let tag = `第 ${idx + 1} 阶段`;
      if (isFirst && isLast) tag = "全课程周";
      else if (isFirst) tag = "开营首周";
      else if (isLast) tag = "结课尾周";

      return {
        index: idx,
        monday,
        tag,
        startDay,
        endDay,
        label: `${tag} · ${startDay} ~ ${endDay} (共 ${items.length} 节)`,
        days,
        items,
      };
    });
  }, [sortedAssignments]);

  // Current active week for matrix
  const currentWeek = activeWeeks[activeWeekIndex] ?? activeWeeks[0];
  const weekDays = currentWeek?.days ?? [];

  // Matrix cell map for the selected active week
  const matrixCells = useMemo(() => {
    const map = new Map<string, AssignmentResponse[]>();
    const items = currentWeek ? currentWeek.items : filtered;

    for (const item of items) {
      const colKey = item.lesson_date ?? "";
      const rowKey = slotClockKey(slotRows, item.slot_business_id);
      const key = `${colKey}|${rowKey}`;
      map.set(key, [...(map.get(key) ?? []), item]);
    }
    return map;
  }, [currentWeek, filtered, slotRows]);

  const rows = clockRows(slotRows);

  const all = [schedules, slots, classes, teachers, rooms, courseSessions];
  if (all.some((item) => item.isPending)) return <LoadingState />;
  if (all.some((item) => item.isError)) {
    return <ErrorState retry={() => all.forEach((item) => void item.refetch())} />;
  }

  const options =
    mode === "class" ? classes.data ?? [] : mode === "teacher" ? teachers.data ?? [] : rooms.data ?? [];
  const currentSubjectObj = options.find((item) => item.business_id === subject);

  const download = async () => {
    if (!schedule) return;
    try {
      const response = await http.get(`/api/v1/schedules/${schedule.id}/export.xlsx`, {
        responseType: "blob",
      });
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `tupai-schedule-v${schedule.version_no}.xlsx`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const matrixGridStyle = {
    gridTemplateColumns: `80px repeat(7, minmax(0, 1fr))`,
  };

  return (
    <div className="space-y-5 animate-fade-in">
      <PageHeader
        title="课表视图"
        actions={
          <div className="flex flex-wrap items-center gap-2.5">
            <Select
              aria-label="课表版本"
              selectSize="sm"
              containerClassName="w-48"
              value={scheduleId}
              onChange={(event) => setScheduleId(event.target.value)}
            >
              {schedules.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  v{item.version_no} / {statusLabel(item.status)}
                </option>
              ))}
            </Select>
            <Button size="sm" variant="outline" onClick={download} disabled={!schedule}>
              <Download className="size-3.5" />
              导出 XLSX
            </Button>
          </div>
        }
      />

      {/* View Mode Switcher and Subject Select Bar */}
      <section className="flex flex-col gap-3.5 rounded-xl border border-zinc-200 bg-white p-4 shadow-2xs">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* View Category: Class / Teacher / Room */}
          <div className="flex items-center gap-1.5 rounded-lg border border-zinc-200/80 bg-zinc-50/70 p-1">
            {MODES.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                type="button"
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3.5 py-1.5 text-xs font-medium transition-all duration-150",
                  mode === value
                    ? "bg-white text-zinc-900 shadow-2xs border border-zinc-200/60"
                    : "text-zinc-500 hover:text-zinc-900 hover:bg-zinc-100/60",
                )}
                onClick={() => setMode(value)}
              >
                <Icon className="size-3.5" />
                {label}
              </button>
            ))}
          </div>

          {/* Layout Style Toggle: Timeline (All Courses) vs Matrix (Standard Week Calendar) */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-zinc-400 font-medium">视图模式:</span>
            <div className="flex items-center rounded-lg border border-zinc-200/80 bg-zinc-50/70 p-0.5">
              <button
                type="button"
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-all",
                  layout === "timeline"
                    ? "bg-white text-blue-700 shadow-2xs font-semibold"
                    : "text-zinc-600 hover:text-zinc-900",
                )}
                onClick={() => setLayout("timeline")}
              >
                <CalendarCheck2 className="size-3.5" />
                全部课次流 (全景直观)
              </button>
              <button
                type="button"
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-all",
                  layout === "matrix"
                    ? "bg-white text-blue-700 shadow-2xs font-semibold"
                    : "text-zinc-600 hover:text-zinc-900",
                )}
                onClick={() => setLayout("matrix")}
              >
                <LayoutGrid className="size-3.5" />
                标准周历矩阵 (首尾周切换)
              </button>
            </div>
          </div>
        </div>

        {/* Target Subject Selector */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-zinc-100 pt-3 text-xs">
          <div className="flex items-center gap-2">
            <span className="text-zinc-500 font-medium">
              选择{mode === "class" ? "班级" : mode === "teacher" ? "教师" : "教室"}:
            </span>
            <Select
              aria-label="排课对象"
              selectSize="sm"
              containerClassName="w-72"
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
            >
              {options.map((item) => (
                <option key={item.id} value={item.business_id}>
                  {item.name} {item.business_id !== item.name ? `(${item.business_id})` : ""}
                </option>
              ))}
            </Select>
          </div>

          <div className="flex items-center gap-3 text-zinc-500">
            <span>
              已安排: <strong className="font-semibold text-zinc-900">{filtered.length}</strong> 节课次
            </span>
            {activeWeeks.length > 0 && (
              <span>
                涵盖: <strong className="font-semibold text-zinc-900">{activeWeeks.length}</strong> 个有课周阶段
              </span>
            )}
          </div>
        </div>
      </section>

      {/* VIEW 1: Timeline / Session Flow View (Shows ALL sessions with explicit course/subject, teacher & time!) */}
      {layout === "timeline" && (
        <section className="space-y-4">
          {sortedAssignments.length === 0 ? (
            <div className="grid min-h-60 place-items-center rounded-xl border border-zinc-200 bg-white p-8 text-center text-sm text-zinc-400 shadow-2xs">
              <div>
                <Calendar className="mx-auto mb-2 size-8 text-zinc-300" />
                <p>当前对象在所选版本中暂无排课记录</p>
              </div>
            </div>
          ) : (
            groupedByMonth.map((group) => (
              <div
                key={group.monthKey}
                className="rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xs"
              >
                {/* Month Group Header */}
                <div className="flex items-center justify-between border-b border-zinc-100 pb-3">
                  <div className="flex items-center gap-2">
                    <CalendarDays className="size-4.5 text-blue-600" />
                    <h3 className="font-bold text-zinc-900 text-sm">{group.monthLabel}</h3>
                  </div>
                  <span className="text-xs font-semibold text-blue-700 bg-blue-50 px-2.5 py-1 rounded-full">
                    本月共 {group.items.length} 节课
                  </span>
                </div>

                {/* Agenda List View */}
                <div className="mt-4 space-y-3">
                  {group.items.map((item) => {
                    const globalIndex = sortedAssignments.indexOf(item);
                    const isFirst = globalIndex === 0;
                    const isLast = globalIndex === sortedAssignments.length - 1;
                    const weekday = item.lesson_date ? formatWeekday(item.lesson_date) : "";
                    const timeRange = getSlotTimeRange(slotRows, item.slot_business_id);
                    const cs = courseMap.get(item.course_business_id);
                    const courseName = cs?.subject || cs?.lesson_name || "课程课次";

                    return (
                      <div
                        key={item.course_session_id}
                        className={cn(
                          "flex flex-col md:flex-row md:items-center justify-between gap-3.5 rounded-xl border p-4 transition-all duration-150 hover:shadow-xs",
                          isFirst
                            ? "border-emerald-300 bg-emerald-50/40"
                            : isLast
                              ? "border-purple-300 bg-purple-50/40"
                              : "border-zinc-200/80 bg-zinc-50/40 hover:bg-white hover:border-zinc-300",
                        )}
                      >
                        {/* 1. Date & Calendar Tag */}
                        <div className="flex items-center gap-3 md:min-w-[200px]">
                          <div className="grid size-12 place-items-center rounded-xl bg-white border border-zinc-200 text-center shadow-2xs shrink-0">
                            <div className="text-[10px] font-medium text-blue-600 leading-none">
                              {weekday || "排课"}
                            </div>
                            <div className="text-xs font-bold text-zinc-900 leading-none mt-1">
                              {item.lesson_date ? item.lesson_date.slice(5) : "循环"}
                            </div>
                          </div>
                          <div>
                            <div className="flex items-center gap-2">
                              <span className="font-bold text-sm text-zinc-900">
                                {item.lesson_date ?? "时段循环"}
                              </span>
                              <span className="text-xs font-semibold text-blue-600">({weekday})</span>
                            </div>
                            <div className="mt-1">
                              <span
                                className={cn(
                                  "text-[10px] font-semibold px-2 py-0.5 rounded-full",
                                  isFirst
                                    ? "bg-emerald-100 text-emerald-800"
                                    : isLast
                                      ? "bg-purple-100 text-purple-800"
                                      : "bg-zinc-200/80 text-zinc-600",
                                )}
                              >
                                {isFirst ? "开营首课" : isLast ? "结课尾课" : `第 ${globalIndex + 1} 讲`}
                              </span>
                            </div>
                          </div>
                        </div>

                        {/* 2. Course Name / Subject Badge */}
                        <div className="flex items-center gap-2 md:min-w-[170px]">
                          <div className="flex items-center gap-1.5 rounded-lg border border-indigo-100 bg-indigo-50/80 px-3 py-2 text-indigo-950 w-full">
                            <BookOpen className="size-4 text-indigo-600 shrink-0" />
                            <div>
                              <div className="text-xs font-bold leading-tight break-words">{courseName}</div>
                              {cs?.stage && (
                                <div className="text-[10px] text-indigo-600 font-medium">{cs.stage}</div>
                              )}
                            </div>
                          </div>
                        </div>

                        {/* 3. Explicit Time Range Badge */}
                        <div className="flex items-center gap-2 rounded-lg border border-blue-100 bg-blue-50/80 px-3 py-2 text-blue-950 md:min-w-[160px]">
                          <Clock className="size-4 text-blue-600 shrink-0" />
                          <div>
                            <div className="text-xs font-bold tracking-tight">{timeRange}</div>
                            <div className="text-[10px] text-blue-600 font-medium">标准授课时段</div>
                          </div>
                        </div>

                        {/* 4. Teacher, Class and Room Details */}
                        <div className="flex flex-1 flex-wrap items-center gap-4 text-xs">
                          {mode !== "class" && (
                            <div className="flex items-center gap-1.5 text-zinc-800">
                              <Users className="size-4 text-indigo-600 shrink-0" />
                              <span className="font-semibold break-words">{item.class_business_id}</span>
                            </div>
                          )}

                          {mode !== "teacher" && (
                            <div className="flex items-center gap-1.5 text-zinc-800">
                              <User className="size-4 text-blue-600 shrink-0" />
                              <span className="font-semibold break-words">{item.teacher_business_id}</span>
                            </div>
                          )}

                          {mode !== "room" && (
                            <div className="flex items-center gap-1.5 text-zinc-600">
                              <DoorOpen className="size-4 text-zinc-400 shrink-0" />
                              <span className="font-medium">{formatRoom(item.room_business_id)}教室</span>
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </section>
      )}

      {/* VIEW 2: Standard 7-Day Weekly Calendar with First/Last Active Week Jumper in ONE LINE */}
      {layout === "matrix" && (
        <section className="space-y-4">
          {/* Active Weeks Selector Bar - Fully aligned in ONE Single Line */}
          <div className="flex flex-wrap sm:flex-nowrap items-center justify-between gap-2.5 rounded-xl border border-zinc-200 bg-white p-3 shadow-2xs">
            {/* Left: Quick Jumper Buttons (First Week / Last Week) */}
            <div className="flex items-center gap-2 shrink-0">
              <Button
                size="sm"
                variant={activeWeekIndex === 0 ? "primary" : "outline"}
                className="h-8 text-xs font-semibold whitespace-nowrap"
                onClick={() => setActiveWeekIndex(0)}
                disabled={activeWeeks.length === 0}
              >
                <Zap className="size-3.5 text-amber-500" />
                开营首周 ({activeWeeks[0]?.startDay ?? "-"})
              </Button>
              <Button
                size="sm"
                variant={activeWeekIndex === activeWeeks.length - 1 ? "primary" : "outline"}
                className="h-8 text-xs font-semibold whitespace-nowrap"
                onClick={() => setActiveWeekIndex(activeWeeks.length - 1)}
                disabled={activeWeeks.length === 0}
              >
                <CalendarCheck2 className="size-3.5 text-purple-500" />
                结课尾周 ({activeWeeks[activeWeeks.length - 1]?.startDay ?? "-"})
              </Button>
            </div>

            {/* Right: Active Week Selector & Stepper */}
            {activeWeeks.length > 1 && (
              <div className="flex items-center gap-1.5 shrink-0">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 px-2 text-xs"
                  onClick={() => setActiveWeekIndex((curr) => Math.max(0, curr - 1))}
                  disabled={activeWeekIndex <= 0}
                >
                  <ChevronLeft className="size-3.5 mr-0.5" />
                  上一周
                </Button>

                <Select
                  aria-label="选择有课周次"
                  selectSize="sm"
                  containerClassName="w-64"
                  value={String(activeWeekIndex)}
                  onChange={(e) => setActiveWeekIndex(Number(e.target.value))}
                >
                  {activeWeeks.map((wk) => (
                    <option key={wk.index} value={String(wk.index)}>
                      {wk.label}
                    </option>
                  ))}
                </Select>

                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 px-2 text-xs"
                  onClick={() => setActiveWeekIndex((curr) => Math.min(activeWeeks.length - 1, curr + 1))}
                  disabled={activeWeekIndex >= activeWeeks.length - 1}
                >
                  下一周
                  <ChevronRight className="size-3.5 ml-0.5" />
                </Button>
              </div>
            )}
          </div>

          {/* Standard 7-Day (Mon-Sun) Timetable Grid */}
          <div className="rounded-xl border border-zinc-200 bg-white shadow-2xs overflow-hidden">
            <div className="w-full">
              {/* Header 7 Days (Mon ~ Sun) */}
              <div className="grid border-b border-zinc-200 bg-zinc-50/90" style={matrixGridStyle}>
                <div className="flex items-center justify-center p-3 text-xs font-medium text-zinc-400">
                  <Clock className="size-3.5 mr-1" />
                  时段
                </div>
                {weekDays.map((col) => {
                  const hasClasses = currentWeek?.items.some((it) => it.lesson_date === col.date);
                  return (
                    <div
                      key={col.date}
                      className={cn(
                        "border-l border-zinc-200/80 p-2.5 text-center transition-colors",
                        hasClasses ? "bg-blue-50/40" : "hover:bg-zinc-100/40",
                      )}
                    >
                      <div className="text-xs font-bold text-zinc-900 tabular-nums">{col.monthDay}</div>
                      <div className={cn("text-[11px] font-semibold mt-0.5", hasClasses ? "text-blue-600 font-bold" : "text-zinc-500")}>
                        {col.weekday}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Timetable Rows (Time Slots) */}
              {rows.map((row) => (
                <div
                  key={row.key}
                  className="grid border-b border-zinc-100 last:border-0 hover:bg-zinc-50/20 transition-colors"
                  style={matrixGridStyle}
                >
                  {/* Time Axis Column */}
                  <div className="flex flex-col justify-center p-2 text-center text-xs tabular-nums border-r border-zinc-100 bg-zinc-50/40">
                    <span className="font-bold text-zinc-800">{row.start}</span>
                    <span className="text-[10px] text-zinc-400 my-0.5">至</span>
                    <span className="font-bold text-zinc-800">{row.end}</span>
                  </div>

                  {/* 7-Day Slot Cells */}
                  {weekDays.map((col) => {
                    const items = matrixCells.get(`${col.date}|${row.key}`) ?? [];
                    return (
                      <div
                        key={col.date}
                        className={cn(
                          "min-h-28 space-y-1.5 border-l border-zinc-100 p-2 flex flex-col justify-start",
                          items.length > 0 ? "bg-blue-50/15" : "",
                        )}
                      >
                        {items.map((assignment) => {
                          const cs = courseMap.get(assignment.course_business_id);
                          const courseName = cs?.subject || cs?.lesson_name || "";

                          return (
                            <div
                              key={assignment.course_session_id}
                              title={`课次编号: ${assignment.course_business_id}`}
                              className={cn(
                                "group rounded-lg border p-2.5 text-left transition-all duration-150 shadow-2xs hover:shadow-xs",
                                items.length > 1
                                  ? "border-red-200 bg-red-50/90 text-red-900"
                                  : "border-blue-200/90 bg-blue-50/80 text-blue-950 hover:bg-blue-100/70 hover:border-blue-300",
                              )}
                            >
                              {/* 1. Time Badge + Course Subject Badge */}
                              <div className="flex items-center justify-between gap-1 mb-1.5">
                                <span className="text-[10px] font-mono font-bold text-blue-700 flex items-center gap-0.5">
                                  <Timer className="size-2.5" />
                                  {row.start}~{row.end}
                                </span>
                                {courseName && (
                                  <span className="text-[10px] font-semibold bg-indigo-100/90 text-indigo-800 px-1.5 py-0.2 rounded break-all truncate max-w-[85px]">
                                    {courseName}
                                  </span>
                                )}
                              </div>

                              {/* 2. Main Contextual Subject & Teacher */}
                              {mode === "class" ? (
                                <>
                                  <div className="flex items-start gap-1 text-xs font-bold text-zinc-900 group-hover:text-blue-700 leading-tight">
                                    <User className="size-3 text-blue-600 shrink-0 mt-0.5" />
                                    <span className="break-words">
                                      {assignment.teacher_business_id || "未定教师"}
                                    </span>
                                  </div>
                                  <div className="mt-1 flex items-center gap-1 text-[11px] text-zinc-600">
                                    <DoorOpen className="size-3 text-zinc-400 shrink-0" />
                                    <span className="font-medium break-words">
                                      {formatRoom(assignment.room_business_id)}教室
                                    </span>
                                  </div>
                                </>
                              ) : mode === "teacher" ? (
                                <>
                                  <div className="flex items-start gap-1 text-xs font-bold text-zinc-900 group-hover:text-blue-700 leading-tight">
                                    <Users className="size-3 text-indigo-600 shrink-0 mt-0.5" />
                                    <span className="break-words">
                                      {assignment.class_business_id || "未定班级"}
                                    </span>
                                  </div>
                                  <div className="mt-1 flex items-center gap-1 text-[11px] text-zinc-600">
                                    <DoorOpen className="size-3 text-zinc-400 shrink-0" />
                                    <span className="font-medium break-words">
                                      {formatRoom(assignment.room_business_id)}教室
                                    </span>
                                  </div>
                                </>
                              ) : (
                                <>
                                  <div className="flex items-start gap-1 text-xs font-bold text-zinc-900 group-hover:text-blue-700 leading-tight">
                                    <Users className="size-3 text-indigo-600 shrink-0 mt-0.5" />
                                    <span className="break-words">
                                      {assignment.class_business_id || "未定班级"}
                                    </span>
                                  </div>
                                  <div className="mt-1 flex items-start gap-1 text-[11px] text-zinc-600 leading-tight">
                                    <User className="size-3 text-zinc-400 shrink-0 mt-0.5" />
                                    <span className="break-words">
                                      {assignment.teacher_business_id || "未定教师"}
                                    </span>
                                  </div>
                                </>
                              )}
                            </div>
                          );
                        })}

                        {items.length > 1 ? (
                          <div className="rounded bg-red-100 px-1.5 py-0.5 text-center text-[10px] font-semibold text-red-700">
                            ⚠ 此时段冲突 ({items.length} 节)
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ))}

              {!rows.length ? (
                <div className="grid min-h-48 place-items-center p-8 text-sm text-zinc-400">
                  当前主数据里还没有时段信息
                </div>
              ) : null}
            </div>
          </div>
        </section>
      )}

      {/* Footer Info Legend */}
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-zinc-500 bg-zinc-50/60 p-3 rounded-lg border border-zinc-200/80">
        <div className="flex items-center gap-2">
          <Badge tone="blue">
            {mode === "class" ? "班级" : mode === "teacher" ? "教师" : "教室"}
          </Badge>
          <span className="font-semibold text-zinc-900">{currentSubjectObj?.name ?? subject}</span>
          <span className="text-zinc-300">|</span>
          <span>
            共安排 <strong className="font-bold text-zinc-900">{filtered.length}</strong> 节课次
            {detail.isFetching ? "（数据同步中...）" : ""}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-block size-2 rounded-full bg-blue-500"></span>
          <span>
            {layout === "timeline"
              ? "全部日程流：完整科目课程 + 授课教师 + 醒目时段"
              : `标准周历：当前定位在 ${currentWeek?.tag ?? "首周"} (${currentWeek?.startDay ?? ""} ~ ${currentWeek?.endDay ?? ""})`}
          </span>
        </div>
      </div>
    </div>
  );
}
