import {
  ArrowLeftRight,
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
  History,
  LayoutGrid,
  Printer,
  Share2,
  SlidersHorizontal,
  Sparkles,
  User,
  Users,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
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
import type {
  AssignmentResponse,
  ClassGroupResponse,
  CourseSessionResponse,
  RoomResponse,
  ScheduleSummaryResponse,
  TeacherResponse,
  TimeSlotResponse,
} from "@/api/generated/models";
import { http } from "@/api/http";
import { canPublishCurrentSet, canScheduleCurrentSet, isReadOnlyMember, useAppUser, useScheduleAccessRole } from "@/app/user-context";
import { CalendarDispatchPanel } from "@/components/calendar-dispatch-panel";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { SopSteps } from "@/components/sop-steps";
import { PublicLinksPage } from "@/pages/public-links-page";
import { ReschedulePage, type ReschedulePrefill } from "@/pages/reschedule-page";
import { VersionsPage } from "@/pages/versions-page";
import { asArray, errorMessage, formatRoom, formatSlot } from "@/lib/format";
import { statusLabel } from "@/lib/labels";
import { SCHEDULE_VIEW_LABELS, SCHEDULE_VIEWS, assistantPath, parseScheduleView, type ScheduleView } from "@/lib/routes";
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

const VIEW_ICONS: Record<ScheduleView, typeof Users> = {
  schedule: CalendarDays,
  adjust: SlidersHorizontal,
  history: History,
  share: Share2,
};

/** 头部与选项里统一的版本状态口径：草稿 / 当前使用中 / 已归档。 */
function versionBadge(status?: string | null): { label: string; tone: BadgeTone } {
  if (status === "published") return { label: "当前使用中", tone: "green" };
  if (status === "draft") return { label: "草稿", tone: "yellow" };
  if (status === "archived") return { label: "已归档", tone: "neutral" };
  return { label: statusLabel(status), tone: "neutral" };
}

function periodOfDay(startTime?: string): string {
  const hour = Number((startTime ?? "").split(":")[0]);
  if (!startTime || !Number.isFinite(hour)) return "";
  return hour < 12 ? "上午" : hour < 18 ? "下午" : "晚";
}

function formatMonthDay(dateStr: string): string {
  const [, month, day] = dateStr.split("-");
  return month && day ? `${Number(month)}月${Number(day)}日` : dateStr;
}

interface LessonDescription {
  courseName: string;
  className: string;
  teacherName: string;
  when: string;
  /** 「交给助手继续处理」预填到需求输入框的一句话。 */
  prompt: string;
}

function describeLesson(
  item: AssignmentResponse,
  lookup: {
    classes: ClassGroupResponse[];
    teachers: TeacherResponse[];
    slots: TimeSlotResponse[];
    courseMap: Map<string, CourseSessionResponse>;
  },
): LessonDescription {
  const slot = lookup.slots.find((entry) => entry.business_id === item.slot_business_id);
  const weekday = item.lesson_date ? formatWeekday(item.lesson_date) : (slot?.weekday ?? "");
  const when =
    [item.lesson_date ? formatMonthDay(item.lesson_date) : "", `${weekday}${periodOfDay(slot?.start_time)}`]
      .filter(Boolean)
      .join(" ") || formatSlot(item.slot_business_id);
  const className = lookup.classes.find((entry) => entry.business_id === item.class_business_id)?.name ?? item.class_business_id;
  const teacherName = lookup.teachers.find((entry) => entry.business_id === item.teacher_business_id)?.name ?? item.teacher_business_id;
  const course = item.course ?? lookup.courseMap.get(item.course_business_id);
  return {
    courseName: course?.subject || course?.lesson_name || "课程课次",
    className,
    teacherName,
    when,
    prompt: `调整 ${className} ${when} 的课（教师 ${teacherName}）：`,
  };
}

export function SchedulePage() {
  const user = useAppUser();
  const scheduleAccessRole = useScheduleAccessRole();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const canSchedule = canScheduleCurrentSet(user, scheduleAccessRole);
  const canPublish = canPublishCurrentSet(user, scheduleAccessRole);
  // 公开链接与教师日历下发沿用原「公开链接 / 排课求解」页 RoleRoute 的口径：管理员/排课员，且不含只读成员。
  const canShare = (user.role === "admin" || user.role === "scheduler") && !isReadOnlyMember(user, scheduleAccessRole);
  const [mode, setMode] = useState<ViewMode>("class");
  const [layout, setLayout] = useState<LayoutStyle>("timeline");
  const [subject, setSubject] = useState("");
  const [activeWeekIndex, setActiveWeekIndex] = useState(0);
  // 已经定位（切到对应班级、滚到卡片）过的课次：用户自己点选或之后手动换对象时，不再被 URL 里的 lesson 拉回去。
  const revealedLesson = useRef("");

  const schedules = useListSchedulesApiV1SchedulesGet();
  const slots = useListTimeSlotsApiV1TimeSlotsGet();
  const classes = useListClassGroupsApiV1ClassGroupsGet();
  const teachers = useListTeachersApiV1TeachersGet();
  const rooms = useListRoomsApiV1RoomsGet();
  const courseSessions = useListCourseSessionsApiV1CourseSessionsGet();

  // 视图、所选版本、所选课次都放在 URL 里：切换视图不丢上下文，链接也能直接带人到对应课次。
  const requestedView = parseScheduleView(searchParams.get("view"));
  const availableViews = SCHEDULE_VIEWS.filter((item) =>
    item === "adjust" ? canSchedule : item === "share" ? canShare : true,
  );
  const view = availableViews.includes(requestedView) ? requestedView : "schedule";
  const versionParam = searchParams.get("version") ?? "";
  const lessonId = searchParams.get("lesson") ?? "";

  const updateParams = (patch: Record<string, string | null>, replace = true) =>
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        for (const [key, value] of Object.entries(patch)) {
          if (value === null || value === "") next.delete(key);
          else next.set(key, value);
        }
        return next;
      },
      { replace },
    );
  const selectView = (next: ScheduleView) => updateParams({ view: next === "schedule" ? null : next }, false);

  const scheduleList = useMemo(() => asArray<ScheduleSummaryResponse>(schedules.data), [schedules.data]);
  const scheduleId = scheduleList.some((item) => item.id === versionParam)
    ? versionParam
    : (preferredSchedule(scheduleList)?.id ?? "");
  const selectedSummary = scheduleList.find((item) => item.id === scheduleId);
  const publishedSummary = scheduleList.find((item) => item.status === "published");

  useEffect(() => {
    const source = mode === "class" ? classes.data : mode === "teacher" ? teachers.data : rooms.data;
    if (source?.[0]) {
      setSubject((current) => (source.some((item) => item.business_id === current) ? current : source[0].business_id));
    }
    setActiveWeekIndex(0);
  }, [classes.data, mode, rooms.data, teachers.data]);

  // 明细含全部排课行，很重：只在看课表或需要按所选课次预填时才取。
  const detail = useGetScheduleApiV1SchedulesScheduleIdGet(scheduleId, {
    query: { enabled: Boolean(scheduleId) && (view === "schedule" || Boolean(lessonId)) },
  });
  const schedule = detail.data;
  const slotRows = useMemo(() => asArray<TimeSlotResponse>(slots.data), [slots.data]);

  // Fast lookup for course details (subject, lesson name, stage, session_no)
  const courseMap = useMemo(() => {
    const map = new Map<string, CourseSessionResponse>();
    for (const cs of asArray<CourseSessionResponse>(courseSessions.data)) {
      map.set(cs.business_id, cs);
    }
    return map;
  }, [courseSessions.data]);

  const filtered = useMemo(
    () =>
      asArray<AssignmentResponse>(schedule?.assignments).filter((item) =>
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

  // 选中的课次：以课次业务号在所选版本里找；换到没有这节课的版本时自然为空。
  const selectedAssignment = useMemo(
    () => (lessonId ? asArray<AssignmentResponse>(schedule?.assignments).find((item) => item.course_business_id === lessonId) : undefined),
    [lessonId, schedule],
  );
  const selectedVisible = selectedAssignment ? filtered.includes(selectedAssignment) : false;
  const selectedWeekIndex = selectedAssignment ? activeWeeks.findIndex((week) => week.items.includes(selectedAssignment)) : -1;

  useEffect(() => {
    if (!selectedAssignment || revealedLesson.current === lessonId) return;
    if (!selectedVisible) {
      // 链接带来的课次不在当前对象下：切到它所在的班级，下一轮渲染可见后再滚动定位。
      setMode("class");
      setSubject(selectedAssignment.class_business_id);
      return;
    }
    revealedLesson.current = lessonId;
    document.getElementById(`lesson-${lessonId}`)?.scrollIntoView?.({ block: "center" });
  }, [lessonId, selectedAssignment, selectedVisible]);

  useEffect(() => {
    if (layout === "matrix" && selectedWeekIndex >= 0) setActiveWeekIndex(selectedWeekIndex);
  }, [layout, selectedWeekIndex]);

  const all = [schedules, slots, classes, teachers, rooms, courseSessions];
  // 只有「课表」视图需要主数据和课程明细全部就绪；其它视图各自内嵌的页面会自己处理加载。
  const blocking = view === "schedule" ? all : [schedules];
  if (blocking.some((item) => item.isPending)) return <LoadingState />;
  if (blocking.some((item) => item.isError)) {
    return <ErrorState retry={() => all.forEach((item) => void item.refetch())} />;
  }

  const options =
    mode === "class" ? asArray<ClassGroupResponse>(classes.data) : mode === "teacher" ? asArray<TeacherResponse>(teachers.data) : asArray<RoomResponse>(rooms.data);
  const currentSubjectObj = options.find((item) => item.business_id === subject);

  const selectedLesson = selectedAssignment
    ? describeLesson(selectedAssignment, {
        classes: asArray<ClassGroupResponse>(classes.data),
        teachers: asArray<TeacherResponse>(teachers.data),
        slots: slotRows,
        courseMap,
      })
    : undefined;
  const prefill: ReschedulePrefill | undefined =
    selectedAssignment && selectedLesson
      ? {
          lessonId: selectedAssignment.course_business_id,
          label: `${selectedLesson.className} ${selectedLesson.when}，教师 ${selectedLesson.teacherName}`,
          teacherId: selectedAssignment.teacher_business_id,
          roomId: selectedAssignment.room_business_id,
          slotId: selectedAssignment.slot_business_id,
        }
      : undefined;

  const toggleLesson = (id: string) => {
    revealedLesson.current = id;
    updateParams({ lesson: id === lessonId ? null : id });
  };
  const lessonCardProps = (item: AssignmentResponse) => ({
    id: `lesson-${item.course_business_id}`,
    role: "button" as const,
    tabIndex: 0,
    "aria-pressed": item.course_business_id === lessonId,
    "data-testid": `lesson-card-${item.course_business_id}`,
    onClick: () => toggleLesson(item.course_business_id),
    onKeyDown: (event: KeyboardEvent<HTMLElement>) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleLesson(item.course_business_id);
      }
    },
  });
  const lessonCardClass = (item: AssignmentResponse) =>
    cn("cursor-pointer", item.course_business_id === lessonId && "ring-2 ring-blue-500 ring-offset-1 print:ring-0");

  const download = async () => {
    if (!selectedSummary) return;
    try {
      const response = await http.get(`/api/v1/schedules/${selectedSummary.id}/export.xlsx`, {
        responseType: "blob",
      });
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `tupai-schedule-v${selectedSummary.version_no}.xlsx`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const matrixGridStyle = {
    gridTemplateColumns: `80px repeat(7, minmax(0, 1fr))`,
  };

  const selectedBadge = selectedSummary ? versionBadge(selectedSummary.status) : undefined;

  const onTabKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : -1;
    const next = availableViews[(availableViews.indexOf(view) + step + availableViews.length) % availableViews.length];
    selectView(next);
    document.getElementById(`schedule-tab-${next}`)?.focus();
  };

  return (
    <div className="space-y-5 animate-fade-in print:bg-white">
      <PageHeader
        title="课表"
        actions={
          <div className="flex flex-wrap items-center gap-2.5 print:hidden">
            <Select
              aria-label="课表版本"
              selectSize="sm"
              containerClassName="w-48"
              value={scheduleId}
              onChange={(event) => updateParams({ version: event.target.value })}
            >
              {scheduleList.map((item) => (
                <option key={item.id} value={item.id}>
                  v{item.version_no} / {versionBadge(item.status).label}
                </option>
              ))}
            </Select>
            {selectedBadge ? (
              <Badge tone={selectedBadge.tone} data-testid="version-status">
                {selectedBadge.label}
              </Badge>
            ) : null}
            <Button size="sm" variant="outline" onClick={download} disabled={!selectedSummary}>
              <Download className="size-3.5" />
              导出 XLSX
            </Button>
            {view === "schedule" ? (
              <Button size="sm" variant="outline" onClick={() => window.print()}>
                <Printer className="size-3.5" />
                打印
              </Button>
            ) : null}
          </div>
        }
      >
        <SopSteps />
      </PageHeader>

      {/* 四个视图共用同一张所选课表：看课、选课、调课、发布和分发都围绕它。 */}
      <div
        role="tablist"
        aria-label="课表内容"
        onKeyDown={onTabKeyDown}
        className="flex w-fit max-w-full items-center gap-1 overflow-x-auto rounded-lg border border-zinc-200/80 bg-zinc-50/70 p-1 print:hidden"
      >
        {availableViews.map((item) => {
          const Icon = VIEW_ICONS[item];
          const active = item === view;
          return (
            <button
              key={item}
              id={`schedule-tab-${item}`}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls="schedule-panel"
              tabIndex={active ? 0 : -1}
              className={cn(
                "flex shrink-0 items-center gap-1.5 rounded-md px-3.5 py-1.5 text-xs font-medium transition-all duration-150",
                active
                  ? "border border-zinc-200/60 bg-white text-zinc-900 shadow-2xs"
                  : "text-zinc-500 hover:bg-zinc-100/60 hover:text-zinc-900",
              )}
              onClick={() => selectView(item)}
            >
              <Icon className="size-3.5" />
              {SCHEDULE_VIEW_LABELS[item]}
            </button>
          );
        })}
      </div>

      <div id="schedule-panel" role="tabpanel" aria-labelledby={`schedule-tab-${view}`} className="space-y-5">
        {view === "schedule" && selectedSummary?.status === "draft" ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs leading-5 text-amber-900 print:hidden">
            正在查看草稿 v{selectedSummary.version_no}：它还没有生效，也不会对外分享或下发。
            {canPublish ? "确认无误后，可在" : "需由有审批权限的人在"}
            <button type="button" className="mx-0.5 font-medium underline underline-offset-2" onClick={() => selectView("history")}>
              历史版本
            </button>
            {canPublish ? "发布。" : "核对并发布。"}
          </div>
        ) : null}

        {view === "schedule" ? (
          <>
            {/* View Mode Switcher and Subject Select Bar（交互工具栏，print 隐藏） */}
            <section className="flex flex-col gap-3.5 rounded-xl border border-zinc-200 bg-white p-4 shadow-2xs print:hidden">
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

            {/* 选中课次后的就近操作：调整这节课 / 交给助手继续处理（只预填，不自动提交）。 */}
            {selectedLesson ? (
              <section
                aria-label="已选课次"
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-blue-200 bg-blue-50/60 p-3.5 text-sm print:hidden"
              >
                <div className="min-w-0 text-zinc-700">
                  <span className="text-xs font-medium text-blue-700">已选课次</span>
                  <div className="mt-0.5 break-words">
                    <strong className="font-semibold text-zinc-900">{selectedLesson.courseName}</strong>
                    <span className="mx-1.5 text-zinc-300">|</span>
                    {selectedLesson.className}
                    <span className="mx-1.5 text-zinc-300">|</span>
                    {selectedLesson.when}
                    <span className="mx-1.5 text-zinc-300">|</span>
                    教师 {selectedLesson.teacherName}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {canSchedule ? (
                    <>
                      <Button size="sm" onClick={() => updateParams({ view: "adjust" }, false)}>
                        <ArrowLeftRight className="size-3.5" />
                        调整这节课
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => navigate(assistantPath({ prompt: selectedLesson.prompt }))}>
                        <Sparkles className="size-3.5" />
                        交给助手继续处理
                      </Button>
                    </>
                  ) : (
                    <span className="text-xs text-zinc-500">
                      {isReadOnlyMember(user, scheduleAccessRole) ? "只读成员可以查看课次，不能调整。" : "调整课次需要排课权限。"}
                    </span>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => updateParams({ lesson: null })}>
                    取消选择
                  </Button>
                </div>
              </section>
            ) : null}

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
                          const cs = item.course ?? courseMap.get(item.course_business_id);
                          const courseName = cs?.subject || cs?.lesson_name || "课程课次";

                          return (
                            <div
                              key={item.course_session_id}
                              {...lessonCardProps(item)}
                              className={cn(
                                "flex flex-col md:flex-row md:items-center justify-between gap-3.5 rounded-xl border p-4 transition-all duration-150 hover:shadow-xs",
                                isFirst
                                  ? "border-emerald-300 bg-emerald-50/40"
                                  : isLast
                                    ? "border-purple-300 bg-purple-50/40"
                                    : "border-zinc-200/80 bg-zinc-50/40 hover:bg-white hover:border-zinc-300",
                                lessonCardClass(item),
                              )}
                            >
                              {/* 1. Date & Calendar Tag */}
                              <div className="flex items-center gap-3 md:min-w-[190px]">
                                <div className="grid size-12 place-items-center rounded-xl bg-white border border-zinc-200 text-center shadow-2xs shrink-0">
                                  <div className="text-[10px] font-semibold text-blue-600 leading-none">
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
                                </div>
                              </div>

                              {/* 2. Course Name / Subject Badge + Stage */}
                              <div className="flex items-center gap-2 md:min-w-[180px]">
                                <div className="flex items-start gap-2 rounded-lg border border-indigo-100 bg-indigo-50/80 px-3 py-2 text-indigo-950 w-full">
                                  <BookOpen className="size-4 text-indigo-600 shrink-0 mt-0.5" />
                                  <div>
                                    <div className="text-xs font-bold leading-tight break-words">{courseName}</div>
                                    {cs?.stage && (
                                      <div className="mt-1">
                                        <span className="font-bold text-[10px] text-amber-900 bg-amber-100/90 px-1.5 py-0.2 rounded">
                                          {cs.stage}
                                        </span>
                                      </div>
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
                {/* Active Weeks Selector Bar - Fully aligned in ONE Single Line（交互层，print 隐藏） */}
                <div className="flex flex-wrap sm:flex-nowrap items-center justify-between gap-2.5 rounded-xl border border-zinc-200 bg-white p-3 shadow-2xs print:hidden">
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
                                const cs = assignment.course ?? courseMap.get(assignment.course_business_id);
                                const courseName = cs?.subject || cs?.lesson_name || "";

                                return (
                                  <div
                                    key={assignment.course_session_id}
                                    title={`课次编号: ${assignment.course_business_id}`}
                                    {...lessonCardProps(assignment)}
                                    className={cn(
                                      "group rounded-lg border p-2.5 text-left transition-all duration-150 shadow-2xs hover:shadow-xs",
                                      items.length > 1
                                        ? "border-red-200 bg-red-50/90 text-red-900"
                                        : "border-blue-200/90 bg-blue-50/80 text-blue-950 hover:bg-blue-100/70 hover:border-blue-300",
                                      lessonCardClass(assignment),
                                    )}
                                  >
                                    {/* 1. Full Course Name / Subject + Stage Badge */}
                                    {(courseName || cs?.stage) ? (
                                      <div className="flex flex-wrap items-center gap-1 mb-1.5 leading-tight">
                                        {courseName && (
                                          <span className="inline-flex items-center gap-1 text-[11px] font-bold text-indigo-900 bg-indigo-100/90 px-1.5 py-0.5 rounded break-words">
                                            <BookOpen className="size-3 text-indigo-600 shrink-0" />
                                            {courseName}
                                          </span>
                                        )}
                                        {cs?.stage && (
                                          <span className="text-[10px] font-bold text-amber-900 bg-amber-100 px-1.5 py-0.5 rounded break-words">
                                            {cs.stage}
                                          </span>
                                        )}
                                      </div>
                                    ) : null}

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

            {/* Footer Info Legend（print 保留对象与统计，白底） */}
            <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-zinc-500 bg-zinc-50/60 p-3 rounded-lg border border-zinc-200/80 print:bg-white print:border-zinc-300">
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
          </>
        ) : null}

        {view === "adjust" ? (
          <ReschedulePage embedded parentScheduleId={scheduleId || undefined} prefill={prefill} />
        ) : null}

        {view === "history" ? <VersionsPage embedded highlightId={scheduleId || undefined} /> : null}

        {view === "share" ? (
          <>
            {/* 公开链接挂在课表方案上、始终展示当前已发布版本；教师日历下发按所选版本，两者都不会外发草稿。 */}
            <section className="rounded-lg border border-zinc-200 bg-zinc-50 p-4 text-xs leading-5 text-zinc-600">
              公开链接与日历订阅展示的是「当前使用中」的已发布课表
              {publishedSummary ? `（v${publishedSummary.version_no}）` : ""}，发布新版本后自动更新。草稿不会对外分享或下发，需先由有审批权限的人发布。
              {publishedSummary && selectedSummary && selectedSummary.id !== publishedSummary.id ? (
                <span className="mt-2 block border-l-2 border-amber-500 bg-amber-50 px-3 py-1.5 text-amber-900">
                  当前所选的 v{selectedSummary.version_no}（{versionBadge(selectedSummary.status).label}）不在对外范围内；教师日历下发也按所选版本执行，正式下发仅对当前使用中的版本开放。
                  {selectedSummary.status === "draft" ? (
                    <button type="button" className="ml-1 font-medium underline underline-offset-2" onClick={() => selectView("history")}>
                      去历史版本{canPublish ? "发布" : "查看"}
                    </button>
                  ) : null}
                </span>
              ) : null}
              {!publishedSummary ? (
                <span className="mt-2 block border-l-2 border-amber-500 bg-amber-50 px-3 py-1.5 text-amber-900">
                  还没有已发布的课表版本，暂时不能新建公开链接或正式下发教师日历。
                  <button type="button" className="ml-1 font-medium underline underline-offset-2" onClick={() => selectView("history")}>
                    去历史版本
                  </button>
                </span>
              ) : null}
            </section>
            <PublicLinksPage
              embedded
              createDisabledReason={publishedSummary ? undefined : "还没有已发布的课表版本，先发布后再创建公开链接"}
            />
            {/* 预检结果只对它检查的那个版本有效：换版本就重挂载，避免上一版的数字挂在新版本名下。 */}
            <CalendarDispatchPanel key={selectedSummary?.id} schedule={selectedSummary} />
          </>
        ) : null}
      </div>
    </div>
  );
}
