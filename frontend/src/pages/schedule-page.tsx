import {
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
  User,
  Users,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  useGetScheduleApiV1SchedulesScheduleIdGet,
  useListClassGroupsApiV1ClassGroupsGet,
  useListRoomsApiV1RoomsGet,
  useListSchedulesApiV1SchedulesGet,
  useListTeachersApiV1TeachersGet,
  useListTimeSlotsApiV1TimeSlotsGet,
} from "@/api/generated/client";
import type { AssignmentResponse, TimeSlotResponse } from "@/api/generated/models";
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

const COLUMNS_PER_PAGE = 7;

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

function formatColumnHeader(col: string, dated: boolean): { main: string; sub?: string } {
  if (!dated) {
    return { main: col };
  }
  try {
    const d = new Date(col);
    if (!Number.isNaN(d.getTime())) {
      const weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
      const weekday = weekdays[d.getDay()];
      const monthDay = `${d.getMonth() + 1}月${d.getDate()}日`;
      return { main: monthDay, sub: weekday };
    }
  } catch {
    // fallback
  }
  return { main: col };
}

function formatWeekday(dateStr: string): string {
  try {
    const d = new Date(dateStr);
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
  const [page, setPage] = useState(0);

  const schedules = useListSchedulesApiV1SchedulesGet();
  const slots = useListTimeSlotsApiV1TimeSlotsGet();
  const classes = useListClassGroupsApiV1ClassGroupsGet();
  const teachers = useListTeachersApiV1TeachersGet();
  const rooms = useListRoomsApiV1RoomsGet();

  useEffect(() => {
    const initial = preferredSchedule(schedules.data);
    if (!scheduleId && initial) setScheduleId(initial.id);
  }, [scheduleId, schedules.data]);

  useEffect(() => {
    const source = mode === "class" ? classes.data : mode === "teacher" ? teachers.data : rooms.data;
    if (source?.[0]) setSubject(source[0].business_id);
    setPage(0);
  }, [classes.data, mode, rooms.data, teachers.data]);

  const detail = useGetScheduleApiV1SchedulesScheduleIdGet(scheduleId, {
    query: { enabled: Boolean(scheduleId) },
  });
  const schedule = detail.data;
  const slotRows = useMemo(() => slots.data ?? [], [slots.data]);

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

  // Sort assignments chronologically
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

  const dated = filtered.some((item) => item.lesson_date);
  const columns = useMemo(() => {
    if (dated) {
      return [...new Set(filtered.map((item) => item.lesson_date).filter(Boolean))].sort() as string[];
    }
    return [...new Set(slotRows.map((item) => item.weekday))];
  }, [dated, filtered, slotRows]);

  const cells = useMemo(() => {
    const map = new Map<string, AssignmentResponse[]>();
    for (const item of filtered) {
      const column = dated
        ? (item.lesson_date ?? "")
        : slotRows.find((slot) => slot.business_id === item.slot_business_id)?.weekday ?? "";
      const row = slotClockKey(slotRows, item.slot_business_id);
      const key = `${column}|${row}`;
      map.set(key, [...(map.get(key) ?? []), item]);
    }
    return map;
  }, [dated, filtered, slotRows]);

  const rows = clockRows(slotRows);
  const pageCount = Math.max(1, Math.ceil(columns.length / COLUMNS_PER_PAGE));
  const safePage = Math.min(page, pageCount - 1);
  const visible = columns.slice(
    safePage * COLUMNS_PER_PAGE,
    safePage * COLUMNS_PER_PAGE + COLUMNS_PER_PAGE,
  );

  const all = [schedules, slots, classes, teachers, rooms];
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

  const gridTemplate = {
    gridTemplateColumns: `76px repeat(${Math.max(visible.length, 1)}, minmax(0, 1fr))`,
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

          {/* Layout Style Toggle: Timeline (All Courses) vs Matrix (Weekly Grid) */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-zinc-400 font-medium">展示模式:</span>
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
                全部课次流 ({filtered.length}节)
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
                首尾周/矩阵视图
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
            {columns.length > 0 && (
              <span>
                跨越: <strong className="font-semibold text-zinc-900">{columns.length}</strong> 个上课日
              </span>
            )}
          </div>
        </div>
      </section>

      {/* VIEW 1: Timeline / Session Flow View (Shows ALL sessions grouped by month, zero gaps!) */}
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
            groupedByMonth.map((group, groupIndex) => (
              <div
                key={group.monthKey}
                className="rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xs"
              >
                {/* Month Header */}
                <div className="flex items-center justify-between border-b border-zinc-100 pb-3">
                  <div className="flex items-center gap-2">
                    <CalendarDays className="size-4 text-blue-600" />
                    <h3 className="font-semibold text-zinc-900 text-sm">{group.monthLabel}</h3>
                  </div>
                  <span className="text-xs text-zinc-400 font-medium">
                    本阶段共 {group.items.length} 节课
                  </span>
                </div>

                {/* Session Cards Grid */}
                <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {group.items.map((item, index) => {
                    const globalIndex = sortedAssignments.indexOf(item);
                    const isFirst = globalIndex === 0;
                    const isLast = globalIndex === sortedAssignments.length - 1;
                    const weekday = item.lesson_date ? formatWeekday(item.lesson_date) : "";

                    return (
                      <div
                        key={item.course_session_id}
                        className={cn(
                          "relative rounded-xl border p-4 transition-all duration-150 shadow-2xs hover:shadow-xs",
                          isFirst
                            ? "border-emerald-300 bg-emerald-50/40"
                            : isLast
                              ? "border-purple-300 bg-purple-50/40"
                              : "border-zinc-200/80 bg-zinc-50/50 hover:bg-blue-50/30 hover:border-blue-200",
                        )}
                      >
                        {/* Session Order Badge */}
                        <div className="flex items-center justify-between mb-2">
                          <span
                            className={cn(
                              "text-[10px] font-semibold px-2 py-0.5 rounded-full",
                              isFirst
                                ? "bg-emerald-100 text-emerald-800"
                                : isLast
                                  ? "bg-purple-100 text-purple-800"
                                  : "bg-zinc-200/70 text-zinc-600",
                            )}
                          >
                            {isFirst ? "⚡ 开营首课" : isLast ? "🏁 结营尾课" : `第 ${globalIndex + 1} 节`}
                          </span>
                          <span className="font-mono text-xs text-zinc-400">
                            {formatSlot(item.slot_business_id)}
                          </span>
                        </div>

                        {/* Date & Weekday */}
                        <div className="text-sm font-bold text-zinc-900">
                          {item.lesson_date ?? "时段循环"}
                          {weekday && (
                            <span className="ml-1.5 text-xs font-medium text-blue-600">({weekday})</span>
                          )}
                        </div>

                        {/* Details */}
                        <div className="mt-3 space-y-1.5 border-t border-zinc-200/60 pt-2.5 text-xs">
                          {mode !== "class" && (
                            <div className="flex items-start gap-1.5 text-zinc-700">
                              <Users className="size-3.5 text-indigo-600 shrink-0 mt-0.5" />
                              <span className="font-medium break-words leading-tight">
                                {item.class_business_id}
                              </span>
                            </div>
                          )}

                          {mode !== "teacher" && (
                            <div className="flex items-start gap-1.5 text-zinc-700">
                              <User className="size-3.5 text-blue-600 shrink-0 mt-0.5" />
                              <span className="font-medium break-words leading-tight">
                                {item.teacher_business_id}
                              </span>
                            </div>
                          )}

                          {mode !== "room" && (
                            <div className="flex items-center gap-1.5 text-zinc-500">
                              <DoorOpen className="size-3.5 text-zinc-400 shrink-0" />
                              <span className="font-medium text-zinc-600">
                                {formatRoom(item.room_business_id)}教室
                              </span>
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

      {/* VIEW 2: Matrix View with First/Last Week Jumpers */}
      {layout === "matrix" && (
        <section className="space-y-4">
          {/* Quick Jumper Toolbar for Active Weeks */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-zinc-200 bg-white p-3 text-xs shadow-2xs">
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => setPage(0)}
                disabled={safePage === 0}
              >
                <Zap className="size-3 text-emerald-600" />
                跳转首周 (开营)
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => setPage(pageCount - 1)}
                disabled={safePage === pageCount - 1}
              >
                <Zap className="size-3 text-purple-600" />
                跳转尾周 (结营)
              </Button>
            </div>

            {columns.length > COLUMNS_PER_PAGE && (
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  onClick={() => setPage((current) => Math.max(0, current - 1))}
                  disabled={safePage === 0}
                >
                  <ChevronLeft className="size-3" />
                  上一段
                </Button>
                <span className="tabular-nums font-medium text-zinc-700">
                  第 {safePage + 1} / {pageCount} 段 ({visible[0]} ~ {visible[visible.length - 1]})
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}
                  disabled={safePage >= pageCount - 1}
                >
                  下一段
                  <ChevronRight className="size-3" />
                </Button>
              </div>
            )}
          </div>

          {/* Timetable Grid (100% full width, no horizontal scrollbar!) */}
          <div className="rounded-xl border border-zinc-200 bg-white shadow-2xs overflow-hidden">
            <div className="w-full">
              {/* Header Dates */}
              <div className="grid border-b border-zinc-200 bg-zinc-50/80" style={gridTemplate}>
                <div className="flex items-center justify-center p-3 text-xs font-medium text-zinc-400">
                  <Clock className="size-3.5 mr-1" />
                  时段
                </div>
                {visible.map((column) => {
                  const { main, sub } = formatColumnHeader(column, dated);
                  return (
                    <div
                      key={column}
                      className="border-l border-zinc-200/80 p-2.5 text-center transition-colors hover:bg-zinc-100/50"
                    >
                      <div className="text-xs font-semibold text-zinc-900 tabular-nums">{main}</div>
                      {sub && <div className="text-[11px] font-medium text-blue-600 mt-0.5">{sub}</div>}
                    </div>
                  );
                })}
              </div>

              {/* Timetable Rows */}
              {rows.map((row) => (
                <div
                  key={row.key}
                  className="grid border-b border-zinc-100 last:border-0 hover:bg-zinc-50/30 transition-colors"
                  style={gridTemplate}
                >
                  {/* Time Column */}
                  <div className="flex flex-col justify-center p-2 text-center text-xs tabular-nums text-zinc-500 border-r border-zinc-100 bg-zinc-50/30">
                    <span className="font-semibold text-zinc-700">{row.start}</span>
                    <span className="text-[10px] text-zinc-400 mt-0.5">至</span>
                    <span className="font-semibold text-zinc-700">{row.end}</span>
                  </div>

                  {/* Day Slot Cells */}
                  {visible.map((column) => {
                    const items = cells.get(`${column}|${row.key}`) ?? [];
                    return (
                      <div
                        key={column}
                        className="min-h-24 space-y-1.5 border-l border-zinc-100 p-2 flex flex-col justify-start"
                      >
                        {items.map((assignment) => (
                          <div
                            key={assignment.course_session_id}
                            title={`课次编号: ${assignment.course_business_id}`}
                            className={cn(
                              "group rounded-lg border p-2.5 text-left transition-all duration-150 shadow-2xs hover:shadow-xs",
                              items.length > 1
                                ? "border-red-200 bg-red-50/90 text-red-900"
                                : "border-blue-200/80 bg-blue-50/60 text-blue-950 hover:bg-blue-50 hover:border-blue-300",
                            )}
                          >
                            {mode === "class" ? (
                              <>
                                <div className="flex items-start gap-1 text-xs font-semibold text-zinc-900 group-hover:text-blue-700 leading-tight">
                                  <User className="size-3 text-blue-600 shrink-0 mt-0.5" />
                                  <span className="break-words">
                                    {assignment.teacher_business_id || "未定教师"}
                                  </span>
                                </div>
                                <div className="mt-1 flex items-center gap-1 text-[11px] text-zinc-500">
                                  <DoorOpen className="size-3 text-zinc-400 shrink-0" />
                                  <span className="break-words font-medium">
                                    {formatRoom(assignment.room_business_id)}教室
                                  </span>
                                </div>
                              </>
                            ) : mode === "teacher" ? (
                              <>
                                <div className="flex items-start gap-1 text-xs font-semibold text-zinc-900 group-hover:text-blue-700 leading-tight">
                                  <Users className="size-3 text-indigo-600 shrink-0 mt-0.5" />
                                  <span className="break-words">
                                    {assignment.class_business_id || "未定班级"}
                                  </span>
                                </div>
                                <div className="mt-1 flex items-center gap-1 text-[11px] text-zinc-500">
                                  <DoorOpen className="size-3 text-zinc-400 shrink-0" />
                                  <span className="break-words font-medium">
                                    {formatRoom(assignment.room_business_id)}教室
                                  </span>
                                </div>
                              </>
                            ) : (
                              <>
                                <div className="flex items-start gap-1 text-xs font-semibold text-zinc-900 group-hover:text-blue-700 leading-tight">
                                  <Users className="size-3 text-indigo-600 shrink-0 mt-0.5" />
                                  <span className="break-words">
                                    {assignment.class_business_id || "未定班级"}
                                  </span>
                                </div>
                                <div className="mt-1 flex items-start gap-1 text-[11px] text-zinc-500 leading-tight">
                                  <User className="size-3 text-zinc-400 shrink-0 mt-0.5" />
                                  <span className="break-words">
                                    {assignment.teacher_business_id || "未定教师"}
                                  </span>
                                </div>
                              </>
                            )}
                          </div>
                        ))}

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
          <span className="font-medium text-zinc-800">{currentSubjectObj?.name ?? subject}</span>
          <span className="text-zinc-300">|</span>
          <span>
            共安排 <strong className="font-semibold text-zinc-800">{filtered.length}</strong> 节课次
            {detail.isFetching ? "（数据同步中...）" : ""}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-block size-2 rounded-full bg-blue-500"></span>
          <span>{layout === "timeline" ? "按月份阶段呈现完整课次流" : "按有课时段呈现周历矩阵"}</span>
        </div>
      </div>
    </div>
  );
}
