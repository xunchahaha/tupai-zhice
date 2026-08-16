import { ChevronLeft, ChevronRight, Download } from "lucide-react";
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
import { errorMessage } from "@/lib/format";
import { statusLabel } from "@/lib/labels";
import { preferredSchedule } from "@/lib/schedule";

type ViewMode = "class" | "teacher" | "room";

const MODES: Array<[ViewMode, string]> = [
  ["class", "班级"],
  ["teacher", "教师"],
  ["room", "教室"],
];
const COLUMNS_PER_PAGE = 7;

/** 一行代表一个上课时刻。日期感知的课表按「日期 × 时刻」排布，不能用星期。 */
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

export function SchedulePage() {
  const [scheduleId, setScheduleId] = useState("");
  const [mode, setMode] = useState<ViewMode>("class");
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

  // 列表接口只给概要，排课行按需从详情接口取——真实数据下每个版本上万行。
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

  // 有日期就按日期排布；没有日期的经典时段课表退回按星期排布。
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
      const column = dated ? (item.lesson_date ?? "") : slotRows.find((slot) => slot.business_id === item.slot_business_id)?.weekday ?? "";
      const row = slotClockKey(slotRows, item.slot_business_id);
      const key = `${column}|${row}`;
      // 用「列+时刻」做键并保留数组：此前用 slot_business_id 单值覆盖，
      // 跨日期的同时段课次会被静默丢掉。
      map.set(key, [...(map.get(key) ?? []), item]);
    }
    return map;
  }, [dated, filtered, slotRows]);

  const rows = clockRows(slotRows);
  const pageCount = Math.max(1, Math.ceil(columns.length / COLUMNS_PER_PAGE));
  const safePage = Math.min(page, pageCount - 1);
  const visible = columns.slice(safePage * COLUMNS_PER_PAGE, safePage * COLUMNS_PER_PAGE + COLUMNS_PER_PAGE);

  const all = [schedules, slots, classes, teachers, rooms];
  if (all.some((item) => item.isPending)) return <LoadingState />;
  if (all.some((item) => item.isError)) {
    return <ErrorState retry={() => all.forEach((item) => void item.refetch())} />;
  }
  const options = mode === "class" ? classes.data ?? [] : mode === "teacher" ? teachers.data ?? [] : rooms.data ?? [];

  const download = async () => {
    if (!schedule) return;
    try {
      const response = await http.get(`/api/v1/schedules/${schedule.id}/export.xlsx`, { responseType: "blob" });
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `tupai-schedule-v${schedule.version_no}.xlsx`;
      anchor.click();
      // 立刻 revoke 会让部分浏览器来不及取数据，交给下一轮事件循环。
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const gridTemplate = { gridTemplateColumns: `110px repeat(${Math.max(visible.length, 1)}, minmax(150px, 1fr))` };

  return (
    <div className="space-y-5 animate-fade-in">
      <PageHeader
        title="课表视图"
        actions={
          <>
            <Select aria-label="课表版本" selectSize="sm" containerClassName="w-48" value={scheduleId} onChange={(event) => setScheduleId(event.target.value)}
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
          </>
        }
      />

      <section className="flex flex-col gap-3 border border-zinc-200 bg-white p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="inline-flex h-8 rounded-md border border-zinc-200 p-0.5">
          {MODES.map(([value, label]) => (
            <button
              key={value}
              className={"rounded px-3 text-xs " + (mode === value ? "bg-zinc-900 text-white" : "text-zinc-500 hover:bg-zinc-100")}
              onClick={() => setMode(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <Select aria-label="排课对象" selectSize="sm" containerClassName="w-48" value={subject} onChange={(event) => setSubject(event.target.value)}
        >
          {options.map((item) => (
            <option key={item.id} value={item.business_id}>
              {item.business_id} / {item.name}
            </option>
          ))}
        </Select>
      </section>

      {columns.length > COLUMNS_PER_PAGE ? (
        <div className="flex items-center justify-between border border-zinc-200 bg-white px-4 py-2 text-xs text-zinc-500">
          <Button size="sm" variant="outline" onClick={() => setPage((current) => Math.max(0, current - 1))} disabled={safePage === 0}>
            <ChevronLeft className="size-3.5" />
            上一页
          </Button>
          <span className="tabular-nums">
            {visible[0]} ~ {visible[visible.length - 1]}（第 {safePage + 1} / {pageCount} 页，共 {columns.length} 天）
          </span>
          <Button size="sm" variant="outline" onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))} disabled={safePage >= pageCount - 1}>
            下一页
            <ChevronRight className="size-3.5" />
          </Button>
        </div>
      ) : null}

      <section className="overflow-x-auto border border-zinc-200 bg-white">
        <div className="min-w-[860px]">
          <div className="grid border-b border-zinc-200 bg-zinc-50" style={gridTemplate}>
            <div className="p-3 text-xs text-zinc-400">时段</div>
            {visible.map((column) => (
              <div key={column} className="border-l border-zinc-200 p-3 text-sm font-semibold tabular-nums">
                {column}
              </div>
            ))}
          </div>
          {rows.map((row) => (
            <div key={row.key} className="grid border-b border-zinc-100 last:border-0" style={gridTemplate}>
              <div className="min-h-28 p-3 text-xs tabular-nums text-zinc-500">
                {row.start}
                <br />
                {row.end}
              </div>
              {visible.map((column) => {
                const items = cells.get(`${column}|${row.key}`) ?? [];
                return (
                  <div key={column} className="min-h-28 space-y-1 border-l border-zinc-100 p-2">
                    {items.map((assignment) => (
                      <div
                        key={assignment.course_session_id}
                        className={
                          "rounded-md border p-2 text-left " +
                          (items.length > 1 ? "border-red-300 bg-red-50" : "border-blue-200 bg-blue-50")
                        }
                      >
                        <div className="font-mono text-[11px] text-blue-600">{assignment.course_business_id}</div>
                        <div className="mt-1 text-sm font-medium text-zinc-800">{assignment.class_business_id}</div>
                        <div className="mt-1 text-xs text-zinc-500">
                          {assignment.teacher_business_id} / {assignment.room_business_id}
                        </div>
                      </div>
                    ))}
                    {items.length > 1 ? (
                      <div className="text-[11px] font-medium text-red-600">同一时刻 {items.length} 节课</div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ))}
          {!rows.length ? (
            <div className="grid min-h-40 place-items-center text-sm text-zinc-400">当前主数据里还没有时段</div>
          ) : null}
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
        <Badge tone="blue">{mode === "class" ? "班级" : mode === "teacher" ? "教师" : "教室"}</Badge>
        <span>{subject || "未选择对象"}</span>
        <span>已显示 {filtered.length} 个课次{detail.isFetching ? "（加载中）" : ""}</span>
        {dated ? <span>按上课日期排布</span> : <span>按星期排布（该课表没有具体日期）</span>}
      </div>
    </div>
  );
}
