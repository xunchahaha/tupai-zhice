import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftRight,
  ArrowRight,
  CalendarDays,
  FileSpreadsheet,
  Filter,
  History,
  RotateCcw,
  Search,
  Send,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import {
  getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey,
  getListSchedulesApiV1SchedulesGetQueryKey,
  useDeleteScheduleApiV1SchedulesScheduleIdDelete,
  useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet,
  useFeishuSyncBatchApiV1IntegrationsFeishuSyncBatchPost,
  useListAuditLogsApiV1AuditLogsGet,
  useListSchedulesApiV1SchedulesGet,
  useRollbackScheduleApiV1SchedulesScheduleIdRollbackPost,
} from "@/api/generated/client";
import { UserResponseRole, type ScheduleSummaryResponse } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { CopyableId } from "@/components/table-cell";
import { TechDetails } from "@/components/tech-details";
import { canPublishCurrentSet, useAppUser, useScheduleAccessRole } from "@/app/user-context";
import { auditSummary } from "@/lib/audit-summary";
import { auditActionLabel, diffKindLabel, resourceLabel, statusLabel } from "@/lib/labels";
import { asArray, datetime, errorMessage, formatRoom, formatSlot } from "@/lib/format";
import { isAutoVersionName } from "@/lib/humanize";
import { schedulePath } from "@/lib/routes";
import { preferredSchedule, versionStatus } from "@/lib/schedule";
import { usePublishSchedule } from "@/lib/use-publish-schedule";
import { cn } from "@/lib/cn";

const OFFICIAL_VERSION_SUFFIX = "官方原始课表";
const localActionLabels: Record<string, string> = { delete: "删除" };

const INITIAL_CHUNK_SIZE = 200;
const INCREMENT_CHUNK_SIZE = 150;

/** 差异超过这么多条才给搜索和类型筛选；少量差异一眼就能看完。 */
const DIFF_TOOLS_THRESHOLD = 20;

export interface VersionsPageProps {
  /** 并入课表页时不再渲染自己的页头（课表 tab 与版本卡上的「看课表」已覆盖回课表）。默认 false 保持独立页面行为。 */
  embedded?: boolean;
  /** 课表页头部所选的版本：版本记录里高亮它，并默认拿它和来源版本做对比。 */
  highlightId?: string;
}

export function VersionsPage({ embedded = false, highlightId }: VersionsPageProps = {}) {
  const user = useAppUser();
  const scheduleAccessRole = useScheduleAccessRole();
  const navigate = useNavigate();
  const canManageVersions = canPublishCurrentSet(user, scheduleAccessRole);
  const canViewAudit = user.role === UserResponseRole.admin;

  const client = useQueryClient();
  const schedules = useListSchedulesApiV1SchedulesGet();
  const logs = useListAuditLogsApiV1AuditLogsGet({ limit: 12 }, { query: { enabled: canViewAudit } });

  const [base, setBase] = useState("");
  const [target, setTarget] = useState("");
  const [removedIds, setRemovedIds] = useState<readonly string[]>([]);
  const [pendingDelete, setPendingDelete] = useState<ScheduleSummaryResponse | null>(null);
  // 发布/回滚都会改变对外的当前课表，和助手里的发布一样先二次确认再提交。
  const [pendingSwitch, setPendingSwitch] = useState<{ kind: "publish" | "rollback"; item: ScheduleSummaryResponse } | null>(null);

  // Search & Dynamic Infinite Scroll for Diff Table
  const [diffSearch, setDiffSearch] = useState("");
  const [diffKindFilter, setDiffKindFilter] = useState("all");
  const [visibleLimit, setVisibleLimit] = useState(INITIAL_CHUNK_SIZE);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const versions = useMemo(
    () => asArray<ScheduleSummaryResponse>(schedules.data).filter((item) => !removedIds.includes(item.id)),
    [schedules.data, removedIds],
  );

  useEffect(() => {
    const current = preferredSchedule(versions);
    if (!current) {
      setBase("");
      setTarget("");
      return;
    }
    const alive = (id: string) => versions.some((item) => item.id === id);
    setTarget((previous) => (alive(previous) ? previous : current.id));
    setBase((previous) => (alive(previous) ? previous : versions.find((item) => item.id !== current.id)?.id ?? ""));
  }, [versions]);

  // 头部所选版本变化时，对比默认落到「该版本 vs 它的来源版本」，方便核对刚生成的草稿；
  // 每个高亮版本只落一次，之后用户手动选的基准/目标不会被覆盖。
  const primedFor = useRef("");
  useEffect(() => {
    if (!highlightId || primedFor.current === highlightId) return;
    const picked = versions.find((item) => item.id === highlightId);
    if (!picked) return;
    primedFor.current = highlightId;
    const parentAlive = picked.parent_id ? versions.some((item) => item.id === picked.parent_id) : false;
    setTarget(picked.id);
    setBase(parentAlive ? (picked.parent_id as string) : versions.find((item) => item.id !== picked.id)?.id ?? "");
  }, [highlightId, versions]);

  const diff = useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet(base, target, {
    query: { enabled: Boolean(base && target && base !== target) },
  });

  const refresh = () => void client.invalidateQueries({ queryKey: getListSchedulesApiV1SchedulesGetQueryKey() });

  const syncPublishedData = useFeishuSyncBatchApiV1IntegrationsFeishuSyncBatchPost({
    mutation: {
      onSuccess: async (data) => {
        if (data.failed_count === 0) {
          toast.success(`发布数据已同步到外部集成（${data.records_written} 条）`);
        } else {
          toast.warning(`发布数据同步完成：${data.completed_count} 项成功，${data.failed_count} 项待重试。`);
        }
        await client.invalidateQueries({ queryKey: getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey() });
      },
      onError: (error) => toast.error(`发布数据同步未完成：${errorMessage(error)}`),
    },
  });

  const retryPublishedData = () =>
    syncPublishedData.mutate({
      data: { resources: ["schedule", "public_summary", "public_adjustment_notice", "public_class_links"] },
    });

  const publish = usePublishSchedule({ onPublished: () => setPendingSwitch(null) });

  const rollback = useRollbackScheduleApiV1SchedulesScheduleIdRollbackPost({
    mutation: {
      onSuccess: () => {
        toast.success("已恢复到历史版本，已触发发布数据同步");
        setPendingSwitch(null);
        refresh();
        void client.invalidateQueries({ queryKey: getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey() });
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });

  const remove = useDeleteScheduleApiV1SchedulesScheduleIdDelete({
    mutation: {
      onSuccess: (_result, { scheduleId }) => {
        toast.success("版本已删除");
        setRemovedIds((ids) => (ids.includes(scheduleId) ? ids : [...ids, scheduleId]));
        setPendingDelete(null);
        refresh();
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });

  // 搜索和类型筛选只在差异较多时出现；工具条不显示时，之前留下的筛选条件也不能悄悄生效。
  const diffToolsVisible = (diff.data?.changed_count ?? 0) > DIFF_TOOLS_THRESHOLD;
  const diffFiltered = diffToolsVisible && (Boolean(diffSearch.trim()) || diffKindFilter !== "all");

  // Filtered Diff Items
  const filteredDiffItems = useMemo(() => {
    if (!diff.data) return [];
    let items = diff.data.items.filter((item) => item.change_kind !== "unchanged");
    if (!diffToolsVisible) return items;

    if (diffKindFilter !== "all") {
      items = items.filter((item) => item.change_kind === diffKindFilter);
    }

    if (diffSearch.trim()) {
      const q = diffSearch.trim().toLowerCase();
      items = items.filter((item) => {
        return (
          item.class_business_id?.toLowerCase().includes(q) ||
          item.teacher_business_id?.toLowerCase().includes(q) ||
          item.course_business_id?.toLowerCase().includes(q) ||
          item.before_room_id?.toLowerCase().includes(q) ||
          item.after_room_id?.toLowerCase().includes(q) ||
          item.before_lesson_date?.includes(q) ||
          item.after_lesson_date?.includes(q)
        );
      });
    }

    return items;
  }, [diff.data, diffKindFilter, diffSearch, diffToolsVisible]);

  // Reset visible limit on search/filter/version change
  useEffect(() => {
    setVisibleLimit(INITIAL_CHUNK_SIZE);
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = 0;
    }
  }, [diffSearch, diffKindFilter, base, target]);

  // Handle Dynamic Scroll Loading (Infinite Scroll)
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const { scrollTop, scrollHeight, clientHeight } = el;
    // When user scrolls within 180px of the bottom, load more items
    if (scrollTop + clientHeight >= scrollHeight - 180) {
      setVisibleLimit((current) => {
        if (current < filteredDiffItems.length) {
          return Math.min(current + INCREMENT_CHUNK_SIZE, filteredDiffItems.length);
        }
        return current;
      });
    }
  }, [filteredDiffItems.length]);

  const visibleItems = useMemo(() => {
    return filteredDiffItems.slice(0, visibleLimit);
  }, [filteredDiffItems, visibleLimit]);

  if (schedules.isPending || (canViewAudit && logs.isPending)) return <LoadingState />;
  if (schedules.isError || (canViewAudit && logs.isError))
    return (
      <ErrorState
        retry={() => {
          void schedules.refetch();
          if (canViewAudit) void logs.refetch();
        }}
      />
    );

  return (
    <div className="space-y-5 animate-fade-in">
      {embedded ? null : (
        <PageHeader
          title="版本与回滚"
          actions={
            // 独立页才需要回课表的入口；内嵌时课表 tab 和版本卡上的「看课表」已经覆盖。
            <Button size="sm" variant="outline" onClick={() => navigate(schedulePath({ version: highlightId }))}>
              <CalendarDays className="size-3.5" />
              查看课表
            </Button>
          }
        />
      )}

      {canManageVersions ? (
        <p className="text-xs leading-5 text-zinc-500">
          发布或回滚后会自动同步到已启用的外部集成；同步失败不影响本地版本，可点「重新同步发布数据」重试。
        </p>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(340px,0.85fr)]">
        {/* Left: Version Diff Panel */}
        <section className="rounded-lg border border-zinc-200 bg-white shadow-2xs flex flex-col min-h-0">
          {/* Header Version Selectors */}
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-200 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Select
                aria-label="基准版本"
                selectSize="sm"
                containerClassName="w-44"
                value={base}
                onChange={(event) => setBase(event.target.value)}
              >
                <option value="">基准版本</option>
                {versions.map((item) => (
                  <option key={item.id} value={item.id}>
                    v{item.version_no} / {versionStatus(item.status).label}
                  </option>
                ))}
              </Select>
              <ArrowLeftRight className="size-4 text-zinc-400" />
              <Select
                aria-label="目标版本"
                selectSize="sm"
                containerClassName="w-44"
                value={target}
                onChange={(event) => setTarget(event.target.value)}
              >
                <option value="">目标版本</option>
                {versions.map((item) => (
                  <option key={item.id} value={item.id}>
                    v{item.version_no} / {versionStatus(item.status).label}
                  </option>
                ))}
              </Select>
            </div>
            {diff.data && (
              <div className="flex items-center gap-3 text-xs text-zinc-500">
                <span>变更: <strong className="font-semibold text-blue-700">{diff.data.changed_count}</strong> 节</span>
                <span>保持: <strong className="font-semibold text-zinc-700">{diff.data.unchanged_count}</strong> 节</span>
              </div>
            )}
          </div>

          {!base || !target || base === target ? (
            <div className="grid min-h-60 place-items-center p-8 text-center text-sm text-zinc-400">
              <div>
                <FileSpreadsheet className="mx-auto mb-2 size-8 text-zinc-300" />
                <p>选择两个不同版本查看差异</p>
              </div>
            </div>
          ) : diff.isPending ? (
            <LoadingState />
          ) : diff.isError ? (
            <ErrorState retry={() => void diff.refetch()} />
          ) : diff.data ? (
            <div className="flex-1 flex flex-col min-h-0">
              {/* 搜索和类型筛选：差异超过 20 条才出现；筛选后的条数只在真的筛选时显示。 */}
              {diffToolsVisible ? (
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-100 bg-zinc-50/50 p-3 text-xs">
                  <div className="relative min-w-[200px] flex-1 max-w-sm">
                    <Search className="absolute left-2.5 top-2 size-3.5 text-zinc-400" />
                    <input
                      type="text"
                      aria-label="搜索差异"
                      value={diffSearch}
                      onChange={(e) => setDiffSearch(e.target.value)}
                      placeholder="按班级、教师、教室或日期搜索..."
                      className="h-8 w-full rounded-md border border-zinc-300 bg-white pl-8 pr-2.5 text-xs shadow-2xs outline-none transition-all focus:border-blue-600 focus:ring-2 focus:ring-blue-500/20"
                    />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Filter className="size-3 text-zinc-400" />
                    <Select
                      aria-label="变更类型"
                      selectSize="sm"
                      containerClassName="w-32"
                      value={diffKindFilter}
                      onChange={(e) => setDiffKindFilter(e.target.value)}
                    >
                      <option value="all">全部变更类型</option>
                      <option value="moved">已移动</option>
                      <option value="room_changed">更换教室</option>
                      <option value="slot_changed">调整时段</option>
                      <option value="date_changed">调整日期</option>
                    </Select>
                  </div>
                  {diffFiltered ? (
                    <div className="text-zinc-500 tabular-nums">
                      共 {filteredDiffItems.length} 条差异
                    </div>
                  ) : null}
                </div>
              ) : null}

              {/* Dynamic Scrollable Diff Table - Fixed 100% width, no horizontal scroll */}
              <div
                ref={scrollContainerRef}
                onScroll={handleScroll}
                className="max-h-[500px] overflow-y-auto overflow-x-hidden scrollbar-thin"
              >
                <table className="w-full text-left text-xs table-fixed">
                  <colgroup>
                    <col className="w-[26%]" />
                    <col className="w-[26%]" />
                    <col className="w-[3%]" />
                    <col className="w-[30%]" />
                    <col className="w-[15%]" />
                  </colgroup>
                  <thead className="sticky top-0 z-10 bg-zinc-50 text-[11px] font-medium text-zinc-500 shadow-2xs">
                    <tr>
                      <th className="h-8 px-3">班级 / 教师</th>
                      <th className="px-2">变更前安排</th>
                      <th className="px-0 text-center"></th>
                      <th className="px-2">变更后安排</th>
                      <th className="px-3 text-right whitespace-nowrap">变更类型</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-100">
                    {visibleItems.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="py-12 text-center text-xs text-zinc-400">
                          {diffFiltered
                            ? "未找到符合筛选条件的差异记录"
                            : "本次对比两个版本之间没有发生变动"}
                        </td>
                      </tr>
                    ) : (
                      visibleItems.map((item) => {
                        const dateDiff = item.before_lesson_date !== item.after_lesson_date;
                        const slotDiff = item.before_slot_id !== item.after_slot_id;
                        const roomDiff = item.before_room_id !== item.after_room_id;

                        return (
                          <tr
                            key={item.course_business_id}
                            className="transition-colors hover:bg-blue-50/20"
                          >
                            <td className="px-3 py-2.5">
                              <div
                                className="font-medium text-zinc-900 leading-snug break-words"
                                title={item.course_business_id}
                              >
                                {item.class_business_id || "未指定班级"}
                              </div>
                              <div className="text-[11px] text-zinc-500 mt-0.5 leading-snug break-words">
                                {item.teacher_business_id || "未指定教师"}
                              </div>
                            </td>
                            <td className="px-2 py-2.5 text-zinc-600">
                              <div className="font-medium text-zinc-700">{item.before_lesson_date ?? "-"}</div>
                              <div className="text-[11px] text-zinc-400 mt-0.5 leading-tight">
                                {formatSlot(item.before_slot_id)} · {formatRoom(item.before_room_id)}教室
                              </div>
                            </td>
                            <td className="px-0 py-2.5 text-center">
                              <ArrowRight className="inline-block size-3 text-zinc-300" />
                            </td>
                            <td className="px-2 py-2.5">
                              <div
                                className={cn(
                                  "font-medium",
                                  dateDiff ? "text-amber-700 font-semibold" : "text-blue-700",
                                )}
                              >
                                {item.after_lesson_date ?? "-"}
                                {dateDiff && <span className="ml-1 text-[10px] text-amber-600 font-normal">(已挪期)</span>}
                              </div>
                              <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] leading-tight">
                                <span
                                  className={cn(
                                    slotDiff
                                      ? "font-semibold text-blue-700 bg-blue-50 px-1 py-0.2 rounded"
                                      : "text-zinc-500",
                                  )}
                                >
                                  {formatSlot(item.after_slot_id)}
                                </span>
                                <span className="text-zinc-300">·</span>
                                <span
                                  className={cn(
                                    roomDiff
                                      ? "font-semibold text-purple-700 bg-purple-50 px-1 py-0.2 rounded"
                                      : "text-zinc-500",
                                  )}
                                >
                                  {formatRoom(item.after_room_id)}教室
                                </span>
                              </div>
                            </td>
                            <td className="px-3 py-2.5 text-right whitespace-nowrap">
                              <Badge tone="blue" className="text-xs px-2 py-0.5 whitespace-nowrap">
                                {diffKindLabel(item.change_kind)}
                              </Badge>
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <div className="grid min-h-60 place-items-center text-sm text-zinc-400">
              选择两个不同版本进行对比
            </div>
          )}
        </section>

        {/* Right: Version Records List */}
        <section className="rounded-lg border border-zinc-200 bg-white shadow-2xs">
          <div className="flex min-h-12 flex-wrap items-center justify-between gap-2 border-b border-zinc-200 px-4 py-2">
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1.5 text-sm font-semibold">
                <History className="size-4 text-blue-600" />
                版本记录
              </span>
              <span className="text-xs text-zinc-400">共 {versions.length} 个版本</span>
            </div>
            {canManageVersions ? (
              <Button size="sm" variant="ghost" onClick={retryPublishedData} disabled={syncPublishedData.isPending}>
                <Send className="size-3.5" />
                {syncPublishedData.isPending ? "正在同步发布数据" : "重新同步发布数据"}
              </Button>
            ) : null}
          </div>
          <div className="divide-y divide-zinc-100 max-h-[520px] overflow-y-auto scrollbar-thin">
            {versions.map((item) => {
              const canPublish = canManageVersions && item.status === "draft";
              const canRollback =
                canManageVersions && (item.status === "archived" || item.status === "rolled_back");
              const canDelete =
                canManageVersions && item.status !== "published" && !item.name.endsWith(OFFICIAL_VERSION_SUFFIX);
              const status = versionStatus(item.status);
              return (
                <div
                  key={item.id}
                  data-testid={`version-card-${item.id}`}
                  data-selected={item.id === highlightId ? "true" : undefined}
                  className={cn("p-4 transition-colors hover:bg-zinc-50/50", item.id === highlightId && "bg-blue-50/40 ring-1 ring-inset ring-blue-300")}
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="font-semibold text-zinc-900">v{item.version_no}</span>
                      {/* 系统自动起的「课表版本 Vn」和版本号重复，不再显示；人起的名字照常显示。 */}
                      {isAutoVersionName(item.name) ? null : (
                        <span
                          className="ml-2 font-normal text-zinc-600 text-xs truncate max-w-[140px] inline-block align-bottom"
                          title={item.name}
                        >
                          {item.name}
                        </span>
                      )}
                    </div>
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </div>
                  <div className="mt-1 flex items-center justify-between text-xs text-zinc-400">
                    <span>{datetime(item.created_at)}</span>
                    <span>{item.assignment_count ?? 0} 条课次</span>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`查看版本 v${item.version_no} 的课表`}
                      onClick={() => navigate(schedulePath({ version: item.id }))}
                    >
                      <CalendarDays className="size-3.5" />
                      看课表
                    </Button>
                    {canPublish ? (
                      <Button
                        size="sm"
                        variant="outline"
                        aria-label={`发布版本 v${item.version_no}`}
                        onClick={() => setPendingSwitch({ kind: "publish", item })}
                      >
                        <Send className="size-3.5" />
                        发布此版本
                      </Button>
                    ) : null}
                    {canRollback ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`回滚到版本 v${item.version_no}`}
                        onClick={() => setPendingSwitch({ kind: "rollback", item })}
                      >
                        <RotateCcw className="size-3.5" />
                        回滚到此版本
                      </Button>
                    ) : null}
                    {canDelete ? (
                      <Button
                        size="icon"
                        variant="ghost"
                        className="ml-auto text-red-600 hover:bg-red-50 hover:text-red-700"
                        aria-label={`删除版本 v${item.version_no}`}
                        title="删除"
                        onClick={() => setPendingDelete(item)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      </div>

      {/* Bottom: Audit Logs with Overflow Prevention */}
      {canViewAudit ? (
        <section className="rounded-lg border border-zinc-200 bg-white shadow-2xs overflow-hidden">
          <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3 text-sm font-semibold">
            <History className="size-4 text-zinc-600" />
            审计日志
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-zinc-50 text-zinc-500 border-b border-zinc-100">
                <tr>
                  <th className="py-2.5 px-4 font-medium w-36">时间</th>
                  <th className="py-2.5 px-3 font-medium w-24">动作</th>
                  <th className="py-2.5 px-3 font-medium w-28">对象</th>
                  <th className="py-2.5 px-3 font-medium w-36">资源</th>
                  <th className="py-2.5 px-4 font-medium">摘要</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100">
                {(Array.isArray(logs.data) ? logs.data : []).map((log) => {
                  const detail = log.detail ?? {};
                  const summary = auditSummary({ action: log.action, resource_type: log.resource_type, detail });
                  return (
                    <tr key={log.id} className="align-top hover:bg-zinc-50/60 transition-colors">
                      <td className="py-2.5 px-4 text-zinc-400 whitespace-nowrap">{datetime(log.created_at)}</td>
                      <td className="py-2.5 px-3 font-medium text-zinc-700 whitespace-nowrap">{actionLabel(log.action)}</td>
                      <td className="py-2.5 px-3 text-zinc-600 whitespace-nowrap">{resourceLabel(log.resource_type)}</td>
                      <td className="py-2.5 px-3 whitespace-nowrap">
                        {log.resource_id ? <CopyableId value={log.resource_id} /> : <span className="text-zinc-300">-</span>}
                      </td>
                      <td className="py-2.5 px-4 text-zinc-700 break-words max-w-xl">
                        <div>{summary.text}</div>
                        {summary.warning ? <div className="mt-0.5 font-medium text-amber-700">{summary.warning}</div> : null}
                        {summary.error ? <div className="mt-0.5 text-red-700">{summary.error}</div> : null}
                        {Object.keys(detail).length ? (
                          <TechDetails summary="原始记录" className="mt-1">
                            <pre className="whitespace-pre-wrap font-mono">{JSON.stringify(detail, null, 2)}</pre>
                          </TechDetails>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {pendingSwitch ? (
        <ConfirmDialog
          open
          pending={pendingSwitch.kind === "publish" ? publish.isPending : rollback.isPending}
          title={
            pendingSwitch.kind === "publish"
              ? `发布草稿 v${pendingSwitch.item.version_no}？`
              : `回滚到版本 v${pendingSwitch.item.version_no}？`
          }
          description={
            pendingSwitch.kind === "publish"
              ? "发布后成为当前课表，并同步到已启用的外部集成；不会自动下发日历。"
              : "回滚后该版本成为当前课表，替换现在使用中的课表，并同步到已启用的外部集成；不会自动下发日历。"
          }
          confirmLabel={pendingSwitch.kind === "publish" ? "确认发布" : "确认回滚"}
          onOpenChange={(open) => {
            if (!open) setPendingSwitch(null);
          }}
          onConfirm={() =>
            pendingSwitch.kind === "publish"
              ? publish.mutate({ scheduleId: pendingSwitch.item.id })
              : rollback.mutate({ scheduleId: pendingSwitch.item.id })
          }
        />
      ) : null}

      {pendingDelete ? (
        <ConfirmDialog
          open
          danger
          pending={remove.isPending}
          title={`删除课表版本 v${pendingDelete.version_no}`}
          description={deleteDescription(
            pendingDelete,
            versions.filter((item) => item.parent_id === pendingDelete.id),
          )}
          confirmLabel="删除"
          onOpenChange={(open) => {
            if (!open) setPendingDelete(null);
          }}
          onConfirm={() => remove.mutate({ scheduleId: pendingDelete.id })}
        />
      ) : null}
    </div>
  );
}

function actionLabel(value: string): string {
  return localActionLabels[value] ?? auditActionLabel(value);
}

function deleteDescription(item: ScheduleSummaryResponse, children: readonly ScheduleSummaryResponse[]): string {
  const lineage = children.length
    ? `该版本是 ${children.map((child) => `v${child.version_no}`).join("、")} 的来源版本，服务端会拒绝删除，请先删掉这些子版本。`
    : "目前没有别的版本以它为来源。";
  return `删除 v${item.version_no}（${statusLabel(item.status)}）会连同它的 ${
    item.assignment_count ?? 0
  } 条排课记录一起清除，无法恢复，版本对比与回滚都不再包含它。${lineage}求解任务和数据快照会保留；已下发外部日历或被调课事件引用的版本，服务端同样会拒绝删除。`;
}
