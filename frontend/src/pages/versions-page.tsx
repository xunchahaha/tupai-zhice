import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftRight,
  ArrowRight,
  FileSpreadsheet,
  Filter,
  History,
  RotateCcw,
  Search,
  Send,
  Trash2,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey,
  getListSchedulesApiV1SchedulesGetQueryKey,
  useDeleteScheduleApiV1SchedulesScheduleIdDelete,
  useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet,
  useFeishuSyncBatchApiV1IntegrationsFeishuSyncBatchPost,
  useListAuditLogsApiV1AuditLogsGet,
  useListSchedulesApiV1SchedulesGet,
  usePublishScheduleApiV1SchedulesScheduleIdPublishPost,
  useRollbackScheduleApiV1SchedulesScheduleIdRollbackPost,
} from "@/api/generated/client";
import { UserResponseRole, type ScheduleSummaryResponse } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { useAppUser, useScheduleAccessRole } from "@/app/user-context";
import { auditActionLabel, diffKindLabel, resourceLabel, statusLabel } from "@/lib/labels";
import { asArray, datetime, errorMessage, formatRoom, formatSlot } from "@/lib/format";
import { preferredSchedule } from "@/lib/schedule";
import { statusTone } from "@/lib/status";
import { cn } from "@/lib/cn";

const VERSION_WRITE_ROLES: readonly UserResponseRole[] = [UserResponseRole.admin, UserResponseRole.approver];
const OFFICIAL_VERSION_SUFFIX = "官方原始课表";
const localActionLabels: Record<string, string> = { delete: "删除" };

const INITIAL_CHUNK_SIZE = 200;
const INCREMENT_CHUNK_SIZE = 150;

export function VersionsPage() {
  const user = useAppUser();
  const scheduleAccessRole = useScheduleAccessRole();
  const canManageVersions = VERSION_WRITE_ROLES.includes(user.role) && scheduleAccessRole === "approver";
  const canViewAudit = user.role === UserResponseRole.admin;

  const client = useQueryClient();
  const schedules = useListSchedulesApiV1SchedulesGet();
  const logs = useListAuditLogsApiV1AuditLogsGet({ limit: 12 }, { query: { enabled: canViewAudit } });

  const [base, setBase] = useState("");
  const [target, setTarget] = useState("");
  const [removedIds, setRemovedIds] = useState<readonly string[]>([]);
  const [pendingDelete, setPendingDelete] = useState<ScheduleSummaryResponse | null>(null);

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

  const diff = useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet(base, target, {
    query: { enabled: Boolean(base && target && base !== target) },
  });

  const refresh = () => void client.invalidateQueries({ queryKey: getListSchedulesApiV1SchedulesGetQueryKey() });

  const syncPublishedData = useFeishuSyncBatchApiV1IntegrationsFeishuSyncBatchPost({
    mutation: {
      onSuccess: async (data) => {
        if (data.failed_count === 0) {
          toast.success(`发布数据已同步到飞书（${data.records_written} 条）`);
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

  const publish = usePublishScheduleApiV1SchedulesScheduleIdPublishPost({
    mutation: {
      onSuccess: () => {
        toast.success("版本已发布为当前课表，已触发发布数据同步");
        refresh();
        void client.invalidateQueries({ queryKey: getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey() });
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });

  const rollback = useRollbackScheduleApiV1SchedulesScheduleIdRollbackPost({
    mutation: {
      onSuccess: () => {
        toast.success("已恢复到历史版本，已触发发布数据同步");
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

  // Filtered Diff Items
  const filteredDiffItems = useMemo(() => {
    if (!diff.data) return [];
    let items = diff.data.items.filter((item) => item.change_kind !== "unchanged");

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
  }, [diff.data, diffKindFilter, diffSearch]);

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
      <PageHeader
        title="版本与回滚"
        actions={
          canManageVersions ? (
            <Button size="sm" variant="outline" onClick={retryPublishedData} disabled={syncPublishedData.isPending}>
              <Send className="size-3.5" />
              {syncPublishedData.isPending ? "正在同步发布数据" : "重新同步发布数据"}
            </Button>
          ) : null
        }
      />

      <section
        className={
          canManageVersions
            ? "rounded-lg border border-blue-200 bg-blue-50/40 p-4 text-sm text-blue-900 shadow-2xs"
            : "rounded-lg border border-zinc-200 bg-zinc-50 p-4 text-sm text-zinc-600 shadow-2xs"
        }
      >
        {canManageVersions
          ? "发布/回滚会先更新本地当前版本，再自动同步当前方案的“课表”、领导展示汇总、班级链接目录和调课通知。同步失败不会撤销本地版本；可在“飞书集成”逐表重试，或在此重新同步发布数据。"
          : "当前账号可以查看和比较版本记录；发布、回滚、删除等变更操作仅由具备相应权限的账号执行。"}
      </section>

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
                    v{item.version_no} / {statusLabel(item.status)}
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
                    v{item.version_no} / {statusLabel(item.status)}
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
                <p>当前选择相同或尚未选择两个不同版本</p>
                <p className="mt-1 text-xs text-zinc-400">请选择基准版本与目标版本以查看排课差异详情</p>
              </div>
            </div>
          ) : diff.isPending ? (
            <LoadingState />
          ) : diff.isError ? (
            <ErrorState retry={() => void diff.refetch()} />
          ) : diff.data ? (
            <div className="flex-1 flex flex-col min-h-0">
              {/* Search & Filter Bar */}
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-100 bg-zinc-50/50 p-3 text-xs">
                <div className="relative min-w-[200px] flex-1 max-w-sm">
                  <Search className="absolute left-2.5 top-2 size-3.5 text-zinc-400" />
                  <input
                    type="text"
                    value={diffSearch}
                    onChange={(e) => setDiffSearch(e.target.value)}
                    placeholder="按班级、教师、教室或日期搜索..."
                    className="h-8 w-full rounded-md border border-zinc-300 bg-white pl-8 pr-2.5 text-xs shadow-2xs outline-none transition-all focus:border-blue-600 focus:ring-2 focus:ring-blue-500/20"
                  />
                </div>
                <div className="flex items-center gap-1.5">
                  <Filter className="size-3 text-zinc-400" />
                  <Select
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
                <div className="text-zinc-500 tabular-nums">
                  共 {filteredDiffItems.length} 条差异
                </div>
              </div>

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
                          {diffSearch || diffKindFilter !== "all"
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
          <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3 text-sm font-semibold">
            <span className="flex items-center gap-1.5">
              <History className="size-4 text-blue-600" />
              版本记录
            </span>
            <span className="text-xs font-normal text-zinc-400">共 {versions.length} 个版本</span>
          </div>
          <div className="divide-y divide-zinc-100 max-h-[520px] overflow-y-auto scrollbar-thin">
            {versions.map((item) => {
              const canPublish = canManageVersions && item.status === "draft";
              const canRollback =
                canManageVersions && (item.status === "archived" || item.status === "rolled_back");
              const canDelete =
                canManageVersions && item.status !== "published" && !item.name.endsWith(OFFICIAL_VERSION_SUFFIX);
              return (
                <div
                  key={item.id}
                  data-testid={`version-card-${item.id}`}
                  className="p-4 transition-colors hover:bg-zinc-50/50"
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="font-semibold text-zinc-900">v{item.version_no}</span>
                      <span
                        className="ml-2 font-normal text-zinc-600 text-xs truncate max-w-[140px] inline-block align-bottom"
                        title={item.name}
                      >
                        {item.name}
                      </span>
                    </div>
                    <Badge tone={statusTone(item.status)}>
                      {item.status === "published" ? "当前使用中" : statusLabel(item.status)}
                    </Badge>
                  </div>
                  <div className="mt-1 flex items-center justify-between text-xs text-zinc-400">
                    <span>{datetime(item.created_at)}</span>
                    <span>{item.assignment_count ?? 0} 条课次</span>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {canPublish ? (
                      <Button
                        size="sm"
                        variant="outline"
                        aria-label={`发布版本 v${item.version_no}`}
                        onClick={() => publish.mutate({ scheduleId: item.id })}
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
                        onClick={() => rollback.mutate({ scheduleId: item.id })}
                      >
                        <RotateCcw className="size-3.5" />
                        回滚到此版本
                      </Button>
                    ) : null}
                    {item.status === "published" ? (
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 bg-emerald-50 px-2 py-1 rounded">
                        <Zap className="size-3" />
                        当前生效版本
                      </span>
                    ) : null}
                    {canDelete ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="ml-auto text-red-600 hover:bg-red-50 hover:text-red-700"
                        aria-label={`删除版本 v${item.version_no}`}
                        onClick={() => setPendingDelete(item)}
                      >
                        <Trash2 className="size-3.5" />
                        删除
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
                  <th className="py-2.5 px-3 font-medium w-28">资源类型</th>
                  <th className="py-2.5 px-3 font-medium w-28">资源 ID</th>
                  <th className="py-2.5 px-4 font-medium">详情明细</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100">
                {(Array.isArray(logs.data) ? logs.data : []).map((log) => (
                  <tr key={log.id} className="hover:bg-zinc-50/60 transition-colors">
                    <td className="py-2.5 px-4 text-zinc-400 whitespace-nowrap">{datetime(log.created_at)}</td>
                    <td className="py-2.5 px-3 font-medium text-zinc-700 whitespace-nowrap">{actionLabel(log.action)}</td>
                    <td className="py-2.5 px-3 text-zinc-600 whitespace-nowrap">{resourceLabel(log.resource_type)}</td>
                    <td className="py-2.5 px-3 font-mono text-zinc-500 whitespace-nowrap">{log.resource_id ?? "-"}</td>
                    <td className="py-2.5 px-4 font-mono text-zinc-500 break-all max-w-xl">
                      {Object.keys(log.detail ?? {}).length ? JSON.stringify(log.detail) : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : (
        <section className="rounded-lg border border-zinc-200 bg-zinc-50 p-4 text-xs text-zinc-500 shadow-2xs">
          审计日志仅管理员可见；版本列表、版本对比和当前课表状态仍可正常查看。
        </section>
      )}

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
  } 条排课记录一起清除，无法恢复，版本对比与回滚都不再包含它。${lineage}求解任务和数据快照会保留；已下发飞书日历或被调课事件引用的版本，服务端同样会拒绝删除。`;
}
