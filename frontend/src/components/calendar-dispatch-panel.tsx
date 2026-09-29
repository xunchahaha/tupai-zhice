import { CalendarPlus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { http } from "@/api/http";
import { Button } from "@/components/ui/button";
import { errorMessage } from "@/lib/format";

interface CalendarConflict {
  course_session_id: string;
  calendar_user_id: string;
  lesson_date: string;
  start_time: string;
  end_time: string;
  source: string;
}

interface CalendarResult {
  dry_run: boolean;
  would_publish: number;
  published: number;
  existing: number;
  skipped_unmapped: number;
  conflict_count: number;
  conflicts: CalendarConflict[];
}

export interface CalendarDispatchSchedule {
  id: string;
  name: string;
  status: string;
}

/**
 * 教师日历下发（原排课求解页里的独立区块）：忙闲预检不落任何日程，随时可做；
 * 正式下发会在教师日历里真实创建日程，所以只对「当前使用中」的已发布版本放行——
 * 草稿不会对外下发，需先由有审批权限的人发布。
 */
export function CalendarDispatchPanel({ schedule }: { schedule: CalendarDispatchSchedule | undefined }) {
  const [publishing, setPublishing] = useState<"dry-run" | "publish" | null>(null);
  const [calendarResult, setCalendarResult] = useState<CalendarResult | null>(null);
  const isPublished = schedule?.status === "published";

  const publishCalendar = async (dryRun: boolean) => {
    if (!schedule) return;
    setPublishing(dryRun ? "dry-run" : "publish");
    try {
      const { data } = await http.post<CalendarResult>(`/api/v1/schedules/${schedule.id}/calendar-publish`, {
        calendar_id: "primary",
        need_notification: true,
        dry_run: dryRun,
      });
      setCalendarResult(data);
      toast.success(
        dryRun
          ? `预检完成：预计下发 ${data.would_publish} 个日程，发现 ${data.conflict_count} 个冲突`
          : `已下发 ${data.published} 个日程，发现 ${data.conflict_count} 个冲突`,
      );
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setPublishing(null);
    }
  };

  const conflicts = Array.isArray(calendarResult?.conflicts) ? calendarResult.conflicts : [];

  return (
    <section className="border border-zinc-200 bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <CalendarPlus className="size-4 text-blue-600" />
            <h2 className="font-semibold">教师日历下发</h2>
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            {schedule ? `当前课表：${schedule.name}` : "当前没有可下发课表"}。教师忙闲冲突会告警，但正式下发仍继续创建日程。
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => publishCalendar(true)} disabled={!schedule || publishing !== null}>
            {publishing === "dry-run" ? "预检中" : "忙闲预检"}
          </Button>
          <Button onClick={() => publishCalendar(false)} disabled={!schedule || !isPublished || publishing !== null}>
            {publishing === "publish" ? "正在下发" : "确认下发"}
          </Button>
        </div>
      </div>
      {schedule && !isPublished ? (
        <div className="mt-3 border-l-2 border-amber-500 bg-amber-50 px-4 py-2 text-xs text-amber-900">
          所选版本还不是「当前使用中」的已发布版本：草稿不会下发到教师日历，需先由有审批权限的人发布；在此之前只能做忙闲预检。
        </div>
      ) : null}
      {calendarResult ? (
        <>
          <div className="mt-4 grid gap-2 sm:grid-cols-3 xl:grid-cols-6">
            <Value label="预计下发" value={String(calendarResult.would_publish)} />
            <Value label="本次发布" value={String(calendarResult.published)} />
            <Value label="已存在" value={String(calendarResult.existing)} />
            <Value label="待补账号" value={String(calendarResult.skipped_unmapped)} />
            <Value label="冲突告警" value={String(calendarResult.conflict_count)} />
            <Value label="执行模式" value={calendarResult.dry_run ? "仅预检" : "正式下发"} />
          </div>
          {calendarResult.skipped_unmapped ? (
            <div className="mt-3 border-l-2 border-amber-500 bg-amber-50 px-4 py-2 text-xs text-amber-900">
              未映射具体日历账号的课程已跳过，请先补充课程账号或教师账号。
            </div>
          ) : null}
        </>
      ) : null}
      {conflicts.length ? (
        <div className="mt-4 max-h-48 overflow-auto border-l-2 border-red-500 bg-red-50 px-4 py-2 text-xs text-red-900">
          <div className="mb-1 font-medium">冲突明细（正式下发仍会创建并标记冲突）</div>
          {conflicts.slice(0, 20).map((item, index) => (
            <div key={`${item.course_session_id}-${item.lesson_date}-${item.source}-${index}`}>
              {item.lesson_date} {item.start_time}-{item.end_time} / {item.calendar_user_id} / 课程{" "}
              {item.course_session_id.slice(0, 8)} / {item.source === "feishu_freebusy" ? "日历已有忙碌" : "待下发课表内部重叠"}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function Value({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-zinc-400">{label}</div>
      <div className="mt-1 font-mono text-sm text-zinc-800">{value}</div>
    </div>
  );
}
