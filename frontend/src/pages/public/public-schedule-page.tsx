import { BellRing, CalendarDays, CalendarPlus, ChevronLeft, ChevronRight, Copy, Info, Printer, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { toast } from "sonner";

import type { PublicLinkScheduleRow } from "@/api/generated/models";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/cn";
import {
  addDaysIso,
  fetchPublicLink,
  googleCalendarUrl,
  icsUrl,
  isApplePlatform,
  isDirectoryPayload,
  nowHhMm,
  toIsoDate,
  webcalUrl,
  PublicLinkUnavailableError,
  type PublicLinkPayload,
} from "@/lib/public-api";
import { errorMessage } from "@/lib/format";

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function weekdayOf(iso: string): string {
  return WEEKDAYS[new Date(`${iso}T00:00:00`).getDay()] ?? "";
}

function shortDate(value: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeZone: "Asia/Shanghai" }).format(date);
}

interface LoadState {
  status: "loading" | "ready" | "error";
  payload?: PublicLinkPayload;
  notFound?: boolean;
}

/**
 * 公开课表 H5（06 §3 B2）：免登录、移动优先单列，独立轻量布局——
 * 不进 AppShell/AuthBoundary，数据经裸 fetch（lib/public-api）获取。
 */
export function PublicSchedulePage() {
  const { token = "" } = useParams<{ token: string }>();
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [reloadFlag, setReloadFlag] = useState(0);
  const [weekOffset, setWeekOffset] = useState(0);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [subscribeOpen, setSubscribeOpen] = useState(false);

  // B3：noindex + no-referrer 只作用于公开页，按页注入/卸载，不影响登录端全局 meta。
  useEffect(() => {
    const robots = document.createElement("meta");
    robots.name = "robots";
    robots.content = "noindex, nofollow";
    const referrer = document.createElement("meta");
    referrer.name = "referrer";
    referrer.content = "no-referrer";
    document.head.append(robots, referrer);
    return () => {
      robots.remove();
      referrer.remove();
    };
  }, []);

  const load = useCallback(async () => {
    setState({ status: "loading" });
    try {
      const payload = await fetchPublicLink(token);
      setState({ status: "ready", payload });
    } catch (error) {
      setState({
        status: "error",
        notFound: error instanceof PublicLinkUnavailableError && error.status === 404,
      });
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load, reloadFlag]);

  const today = toIsoDate(new Date());
  const weekStart = addDaysIso(today, weekOffset * 7);
  const chipDates = useMemo(
    () => Array.from({ length: 7 }, (_, index) => addDaysIso(weekStart, index)),
    [weekStart],
  );
  const selected = selectedDate ?? today;

  const payload = state.payload;
  const schedule = payload && !isDirectoryPayload(payload) ? payload : null;
  const directory = payload && isDirectoryPayload(payload) ? payload : null;

  const rowsByDate = useMemo(() => {
    const map = new Map<string, PublicLinkScheduleRow[]>();
    for (const row of schedule?.rows ?? []) {
      const list = map.get(row.date);
      if (list) list.push(row);
      else map.set(row.date, [row]);
    }
    for (const list of map.values()) list.sort((a, b) => a.start.localeCompare(b.start));
    return map;
  }, [schedule]);

  // 选中日置顶，其余按日期先后排在后面；选中日无课时展示空态。
  const visibleDates = useMemo(() => {
    const rest = chipDates.filter((date) => date !== selected && rowsByDate.has(date));
    return [selected, ...rest];
  }, [chipDates, rowsByDate, selected]);

  const nowTime = nowHhMm();
  const adjustments = schedule?.adjustments ?? [];
  const icsHttps = icsUrl(token);

  if (state.status === "loading") {
    return <PublicShell><div className="py-24 text-center text-sm text-zinc-400">正在加载课表…</div></PublicShell>;
  }
  if (state.status === "error" || !payload) {
    return (
      <PublicShell>
        <div className="rounded-xl border border-zinc-200 bg-white p-8 text-center shadow-sm">
          <p className="text-base font-semibold text-zinc-900">{state.notFound ? "链接不存在或已失效" : "课表加载失败"}</p>
          <p className="mt-2 text-sm text-zinc-500">
            {state.notFound
              ? "链接可能已被停用、轮换或过期，请联系学校获取最新链接。"
              : "网络未连通，请稍后重试。"}
          </p>
          <Button variant="outline" className="mt-5" onClick={() => setReloadFlag((value) => value + 1)}>
            <RefreshCw className="size-3.5" />
            重新加载
          </Button>
        </div>
      </PublicShell>
    );
  }

  const versionText = payload.version_no != null ? `V${payload.version_no}` : "未发布版本";

  return (
    <PublicShell>
      {/* ① 头部 */}
      <header className="flex items-start gap-3 px-4 pt-5">
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-blue-600 text-white shadow-sm">
          <ShieldCheck className="size-5" />
        </span>
        <div className="min-w-0">
          <h1 className="break-words text-xl font-bold tracking-tight text-zinc-950">{payload.display_name}</h1>
          <p className="mt-1 text-xs text-zinc-500">
            {versionText} · 更新于 {shortDate(payload.published_at)}
          </p>
          {schedule ? (
            <p className="mt-0.5 text-xs text-zinc-400">覆盖日期 {schedule.first_date} ~ {schedule.last_date}</p>
          ) : null}
        </div>
      </header>

      {/* ② sticky 调课横幅（amber 语义色；print 时取消 sticky 与阴影，保留内容） */}
      {adjustments.length > 0 ? (
        <div className="sticky top-0 z-20 mt-4 px-4 print:static print:z-auto print:px-0">
          <button
            type="button"
            onClick={() => setAdjustOpen((open) => !open)}
            aria-expanded={adjustOpen}
            className="flex w-full items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-left text-sm text-amber-600 shadow-sm transition-colors hover:bg-amber-100/70"
          >
            <BellRing className="size-4 shrink-0" />
            <span className="min-w-0 flex-1 font-medium">
              本周有 {adjustments.length} 条调课
              <span className="ml-1 font-normal opacity-80">点开查看详情</span>
            </span>
            <ChevronRight className={cn("size-4 shrink-0 transition-transform", adjustOpen && "rotate-90")} />
          </button>
          {adjustOpen ? (
            <div className="mt-2 space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs shadow-sm animate-fade-in">
              {adjustments.map((item, index) => (
                <div key={`${item.course_name}-${index}`} className="border-b border-amber-200/60 pb-2 last:border-0 last:pb-0">
                  <div className="font-semibold text-amber-700">
                    {item.class_name} · {item.course_name}
                    <span className="ml-1.5 rounded bg-amber-100 px-1 py-0.5 text-[10px] font-medium">{item.type}</span>
                  </div>
                  <div className="mt-1 text-amber-600">
                    {item.before_time} → {item.after_time}
                    {item.before_location !== item.after_location ? `；地点 ${item.before_location} → ${item.after_location}` : ""}
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {schedule ? (
        <>
          {/* ③ 日期 chips 横滑（交互层，print 时整层隐藏，「打印」按钮也住在这里） */}
          <div className="mt-4 px-4 print:hidden">
            <div className="flex items-center justify-between">
              <Button size="sm" variant="ghost" onClick={() => setWeekOffset((value) => value - 1)} aria-label="上一周">
                <ChevronLeft className="size-4" />
                上一周
              </Button>
              <Button size="sm" variant="ghost" disabled={weekOffset === 0} onClick={() => setWeekOffset(0)}>
                回到本周
              </Button>
              <span className="flex items-center">
                <Button size="sm" variant="ghost" onClick={() => setWeekOffset((value) => value + 1)} aria-label="下一周">
                  下一周
                  <ChevronRight className="size-4" />
                </Button>
                {/* 06 §3 B5：张榜打印——调起浏览器打印，配合 print: 变体输出白底黑字课表 */}
                <Button size="sm" variant="ghost" onClick={() => window.print()}>
                  <Printer className="size-4" />
                  打印
                </Button>
              </span>
            </div>
            <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
              {chipDates.map((date) => {
                const isSelected = date === selected;
                const isToday = date === today;
                return (
                  <button
                    key={date}
                    type="button"
                    onClick={() => setSelectedDate(date)}
                    className={cn(
                      "flex shrink-0 flex-col items-center rounded-full border px-3.5 py-1.5 text-center transition-colors",
                      isSelected
                        ? "border-blue-600 bg-blue-600 text-white shadow-sm"
                        : "border-zinc-200 bg-white text-zinc-600 shadow-sm hover:border-zinc-300",
                    )}
                  >
                    <span className={cn("text-[10px]", isSelected ? "text-blue-100" : isToday ? "font-semibold text-blue-600" : "text-zinc-400")}>
                      {isToday ? "今天" : weekdayOf(date)}
                    </span>
                    <span className="text-xs font-semibold tabular-nums">{date.slice(5).replace("-", "/")}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* ④ 按日分组卡片流（print：白底黑字、去阴影，卡片不断行分页） */}
          <div className="mt-3 space-y-4 px-4 pb-28 print:mt-4 print:px-0 print:pb-0">
            {visibleDates.map((date) => {
              const rows = rowsByDate.get(date) ?? [];
              return (
                <section key={date} className="print:break-inside-avoid">
                  <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-zinc-700 print:text-black">
                    <CalendarDays className="size-4 text-blue-600" />
                    {date.slice(5).replace("-", "/")} {weekdayOf(date)}
                    {date === today ? <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-700">今天</span> : null}
                  </h2>
                  {rows.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-zinc-200 bg-white/60 px-4 py-8 text-center text-sm text-zinc-400">
                      该日无课
                    </div>
                  ) : (
                    <div className="space-y-2.5">
                      {rows.map((row, index) => (
                        <ScheduleCard key={`${row.date}-${row.start}-${index}`} row={row} ongoing={row.date === today && row.start <= nowTime && nowTime < row.end} showClassName={schedule.scope === "teacher"} showTeacher={schedule.show_teacher_names} />
                      ))}
                    </div>
                  )}
                </section>
              );
            })}
          </div>

          {/* ⑤ 底部固定订阅按钮（print 隐藏） */}
          <div className="fixed inset-x-0 bottom-0 z-30 border-t border-zinc-200 bg-white/95 px-4 pb-[max(env(safe-area-inset-bottom),12px)] pt-3 backdrop-blur-xs print:hidden">
            <Button
              className="h-11 w-full bg-blue-600 text-base text-white hover:bg-blue-700"
              onClick={() => {
                // iOS/macOS 的 webcal:// 有系统级注册，直接唤起日历；其余平台走弹层。
                if (isApplePlatform()) {
                  window.location.href = webcalUrl(token);
                  return;
                }
                setSubscribeOpen(true);
              }}
            >
              <CalendarPlus className="size-4" />
              订阅到日历
            </Button>
          </div>

          <Dialog open={subscribeOpen} onOpenChange={setSubscribeOpen}>
            <DialogContent className="max-w-sm">
              <DialogTitle className="text-base font-semibold">订阅课表到日历</DialogTitle>
              <DialogDescription className="mt-1 text-sm text-zinc-500">
                复制下方链接，或跳转 Google 日历完成订阅。
              </DialogDescription>
              <a
                href={googleCalendarUrl(icsHttps)}
                target="_blank"
                rel="noreferrer"
                className="mt-4 flex h-10 w-full items-center justify-center gap-1.5 rounded-md bg-blue-600 text-sm font-medium text-white transition-colors hover:bg-blue-700"
              >
                添加到 Google 日历
              </a>
              <Button
                variant="outline"
                className="mt-2 h-10 w-full"
                onClick={async () => {
                  try {
                    if (!navigator.clipboard?.writeText) throw new Error("当前浏览器不允许自动复制");
                    await navigator.clipboard.writeText(icsHttps);
                    toast.success("ICS 订阅链接已复制");
                  } catch (error) {
                    toast.error(`复制失败，请手动复制：${errorMessage(error)}`);
                  }
                }}
              >
                <Copy className="size-3.5" />
                复制 ICS 订阅链接
              </Button>
              <p className="mt-3 rounded-md bg-zinc-50 p-2.5 text-xs leading-5 text-zinc-500">
                订阅后由日历应用定期刷新，Google 约 12-24 小时；课表调整后会自动更新到你的日历。
              </p>
            </DialogContent>
          </Dialog>
        </>
      ) : null}

      {/* ⑥ school 目录（督导公示）：后端无单班公开端点，目录项仅展示不可点。 */}
      {directory ? (
        <div className="mt-4 space-y-3 px-4 pb-10">
          <div className="flex items-start gap-2 rounded-xl border border-zinc-200 bg-white p-3 text-xs text-zinc-500 shadow-sm">
            <Info className="mt-0.5 size-3.5 shrink-0 text-blue-600" />
            班级目录仅供参考；单个班级的课表链接由学校另行发放（暂不支持点开）。
          </div>
          {directory.classes.length === 0 ? (
            <div className="rounded-xl border border-dashed border-zinc-200 bg-white/60 px-4 py-10 text-center text-sm text-zinc-400">
              暂无班级数据
            </div>
          ) : (
            directory.classes.map((item) => (
              <div key={item.class_business_id} className="flex items-center justify-between gap-3 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-zinc-900">{item.class_name}</div>
                  <div className="mt-0.5 text-xs text-zinc-500">
                    {item.session_count} 节课 · {item.first_date} ~ {item.last_date}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      ) : null}
    </PublicShell>
  );
}

function ScheduleCard({ row, ongoing, showClassName, showTeacher }: { row: PublicLinkScheduleRow; ongoing: boolean; showClassName: boolean; showTeacher: boolean }) {
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm print:break-inside-avoid print:border-zinc-300 print:bg-white print:shadow-none",
        ongoing && "ring-2 ring-blue-500 print:ring-0",
      )}
    >
      <div className="shrink-0 text-sm font-semibold tabular-nums text-blue-600 print:text-blue-800">
        {row.start}-{row.end}
      </div>
      <div className="min-w-0 flex-1">
        <div className="break-words text-sm font-semibold text-zinc-900 print:text-black">
          {row.subject}
          {row.lesson_name && row.lesson_name !== row.subject ? (
            <span className="ml-1.5 text-xs font-normal text-zinc-500">{row.lesson_name}</span>
          ) : null}
        </div>
        {showClassName ? <div className="mt-0.5 text-xs text-zinc-500">{row.class_name}</div> : null}
        {showTeacher && row.teacher_names.length > 0 ? (
          <div className="mt-1 text-xs text-zinc-600 print:text-zinc-700">教师：{row.teacher_names.join("、")}</div>
        ) : null}
      </div>
      <div className="shrink-0 text-right">
        {ongoing ? <div className="mb-1 text-[10px] font-semibold text-blue-600 print:hidden">进行中</div> : null}
        <div className="break-words text-xs text-zinc-500 print:text-zinc-700">{row.location}</div>
      </div>
    </div>
  );
}

function PublicShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto min-h-dvh w-full max-w-md bg-zinc-50 print:min-h-0 print:max-w-none print:bg-white">
      {children}
    </main>
  );
}
