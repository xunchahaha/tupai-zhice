import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeftRight, History, RotateCcw, Send, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey, getListSchedulesApiV1SchedulesGetQueryKey, useDeleteScheduleApiV1SchedulesScheduleIdDelete, useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet, useFeishuSyncBatchApiV1IntegrationsFeishuSyncBatchPost, useListAuditLogsApiV1AuditLogsGet, useListSchedulesApiV1SchedulesGet, usePublishScheduleApiV1SchedulesScheduleIdPublishPost, useRollbackScheduleApiV1SchedulesScheduleIdRollbackPost } from "@/api/generated/client";
import { UserResponseRole, type ScheduleSummaryResponse } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { useAppUser, useScheduleAccessRole } from "@/app/user-context";
import { auditActionLabel, diffKindLabel, resourceLabel, statusLabel } from "@/lib/labels";
import { datetime, errorMessage } from "@/lib/format";
import { preferredSchedule } from "@/lib/schedule";
import { statusTone } from "@/lib/status";

// 发布 / 回滚 / 删除后端一律要求 admin | approver。这里按角色白名单判，而不是 isReadOnlyMember：
// isReadOnlyMember 只挡 viewer，排课员会拿到一个点下去必然 403 的按钮。
const VERSION_WRITE_ROLES: readonly UserResponseRole[] = [UserResponseRole.admin, UserResponseRole.approver];
// converter 建立导入基线时写死的版本名后缀，删除它会被服务端拒绝。
const OFFICIAL_VERSION_SUFFIX = "官方原始课表";
// labels.ts 由并发改动的页面共用，这里只在本页补上审计动作的中文，避免动共享文件。
const localActionLabels: Record<string, string> = { delete: "删除" };

export function VersionsPage() {
  const user = useAppUser();
  const scheduleAccessRole = useScheduleAccessRole();
  const canManageVersions = VERSION_WRITE_ROLES.includes(user.role)
    && scheduleAccessRole === "approver";
  const canViewAudit = user.role === UserResponseRole.admin;
  const client = useQueryClient();
  const schedules = useListSchedulesApiV1SchedulesGet();
  const logs = useListAuditLogsApiV1AuditLogsGet({ limit: 12 }, { query: { enabled: canViewAudit } });
  const [base, setBase] = useState("");
  const [target, setTarget] = useState("");
  // 删除成功到列表 refetch 落地之间有一个窗口期，schedules.data 里还留着已删版本。
  // 这段时间如果不把它从本地列表里摘掉，下面的 useEffect 会把 base/target 又填回那个死 id。
  const [removedIds, setRemovedIds] = useState<readonly string[]>([]);
  const [pendingDelete, setPendingDelete] = useState<ScheduleSummaryResponse | null>(null);
  const versions = useMemo(() => (schedules.data ?? []).filter((item) => !removedIds.includes(item.id)), [schedules.data, removedIds]);
  useEffect(() => {
    const current = preferredSchedule(versions);
    if (!current) { setBase(""); setTarget(""); return; }
    const alive = (id: string) => versions.some((item) => item.id === id);
    setTarget((previous) => (alive(previous) ? previous : current.id));
    setBase((previous) => (alive(previous) ? previous : versions.find((item) => item.id !== current.id)?.id ?? ""));
  }, [versions]);
  const diff = useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet(base, target, { query: { enabled: Boolean(base && target && base !== target) } });
  const refresh = () => void client.invalidateQueries({ queryKey: getListSchedulesApiV1SchedulesGetQueryKey() });
  const syncPublishedData = useFeishuSyncBatchApiV1IntegrationsFeishuSyncBatchPost({ mutation: {
    onSuccess: async (data) => {
      if (data.failed_count === 0) {
        toast.success(`发布数据已同步到飞书（${data.records_written} 条）`);
      } else {
        toast.warning(`发布数据同步完成：${data.completed_count} 项成功，${data.failed_count} 项待重试。`);
      }
      await client.invalidateQueries({ queryKey: getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey() });
    },
    onError: (error) => toast.error(`发布数据同步未完成：${errorMessage(error)}`),
  } });
  const retryPublishedData = () => syncPublishedData.mutate({ data: { resources: ["schedule", "public_summary", "public_class_schedule", "public_adjustment_notice"] } });
  const publish = usePublishScheduleApiV1SchedulesScheduleIdPublishPost({ mutation: { onSuccess: () => { toast.success("版本已发布为当前课表，已触发发布数据同步"); refresh(); void client.invalidateQueries({ queryKey: getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey() }); }, onError: (error) => toast.error(errorMessage(error)) } });
  const rollback = useRollbackScheduleApiV1SchedulesScheduleIdRollbackPost({ mutation: { onSuccess: () => { toast.success("已恢复到历史版本，已触发发布数据同步"); refresh(); void client.invalidateQueries({ queryKey: getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey() }); }, onError: (error) => toast.error(errorMessage(error)) } });
  const remove = useDeleteScheduleApiV1SchedulesScheduleIdDelete({ mutation: {
    onSuccess: (_result, { scheduleId }) => {
      toast.success("版本已删除");
      setRemovedIds((ids) => (ids.includes(scheduleId) ? ids : [...ids, scheduleId]));
      setPendingDelete(null);
      refresh();
    },
    // 服务端的 409 话术（已发布 / 官方基线 / 有子版本 / 被调课引用 / 已下发日历）是可读中文，原样透出去。
    onError: (error) => toast.error(errorMessage(error)),
  } });
  if (schedules.isPending || (canViewAudit && logs.isPending)) return <LoadingState />;
  if (schedules.isError || (canViewAudit && logs.isError)) return <ErrorState retry={() => { void schedules.refetch(); if (canViewAudit) void logs.refetch(); }} />;
  return <div className="space-y-5">
    <PageHeader title="版本与回滚" actions={canManageVersions ? <Button size="sm" variant="outline" onClick={retryPublishedData} disabled={syncPublishedData.isPending}><Send className="size-3.5" />{syncPublishedData.isPending ? "正在同步发布数据" : "重新同步发布数据"}</Button> : null} />
    <section className={canManageVersions ? "border border-blue-200 bg-blue-50/40 px-4 py-3 text-sm text-blue-900" : "border border-zinc-200 bg-zinc-50 px-4 py-3 text-sm text-zinc-600"}>{canManageVersions ? "发布/回滚会先更新本地当前版本，再自动同步当前方案的“课表”、领导展示汇总、班级公开课表和调课通知。同步失败不会撤销本地版本；可在“飞书集成”逐表重试，或在此重新同步发布数据。" : "当前账号可以查看和比较版本记录；发布、回滚、删除等变更操作仅由具备相应权限的账号执行。"}</section>
    <div className="grid gap-2 xl:grid-cols-[minmax(0,1.4fr)_minmax(340px,0.8fr)]">
      <section className="border border-zinc-200 bg-white">
        <div className="flex flex-wrap items-center gap-2 border-b border-zinc-200 p-4"><select aria-label="基准版本" className="h-8 min-w-36 rounded-md border border-zinc-300 px-2 text-xs" value={base} onChange={(event) => setBase(event.target.value)}><option value="">基准版本</option>{versions.map((item) => <option key={item.id} value={item.id}>v{item.version_no} / {statusLabel(item.status)}</option>)}</select><ArrowLeftRight className="size-4 text-zinc-400" /><select aria-label="目标版本" className="h-8 min-w-36 rounded-md border border-zinc-300 px-2 text-xs" value={target} onChange={(event) => setTarget(event.target.value)}><option value="">目标版本</option>{versions.map((item) => <option key={item.id} value={item.id}>v{item.version_no} / {statusLabel(item.status)}</option>)}</select></div>
        {!base || !target || base === target ? <div className="grid min-h-44 place-items-center px-6 text-center text-sm text-zinc-400">当前只有一个课表版本。生成第二个版本后即可在这里比较和回滚。</div> : diff.isPending ? <LoadingState /> : diff.isError ? <ErrorState retry={() => void diff.refetch()} /> : diff.data ? <><div className="grid grid-cols-2 border-b border-zinc-100"><Value label="变更课次" value={String(diff.data.changed_count)} /><Value label="保持不变" value={String(diff.data.unchanged_count)} /></div><div className="max-h-[380px] overflow-y-auto"><table className="w-full text-left text-sm"><thead className="sticky top-0 bg-zinc-50 text-xs text-zinc-500"><tr><th className="h-9 px-4">课次</th><th>变更前</th><th>变更后</th><th>变更类型</th></tr></thead><tbody>{diff.data.items.filter((item) => item.change_kind !== "unchanged").map((item) => <tr key={item.course_business_id} className="border-t border-zinc-100"><td className="h-10 px-4 font-mono text-xs">{item.course_business_id}</td><td>{item.before_lesson_date ?? "-"} / {item.before_slot_id ?? "-"} / {item.before_room_id ?? "-"}</td><td>{item.after_lesson_date ?? "-"} / {item.after_slot_id ?? "-"} / {item.after_room_id ?? "-"}</td><td><Badge tone="blue">{diffKindLabel(item.change_kind)}</Badge></td></tr>)}</tbody></table></div></> : <div className="grid min-h-44 place-items-center text-sm text-zinc-400">选择两个不同版本进行对比</div>}
      </section>
      <section className="border border-zinc-200 bg-white"><div className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold">版本记录</div><div className="divide-y divide-zinc-100">{versions.map((item) => { const canPublish = canManageVersions && item.status === "draft"; const canRollback = canManageVersions && (item.status === "archived" || item.status === "rolled_back"); const canDelete = canManageVersions && item.status !== "published" && !item.name.endsWith(OFFICIAL_VERSION_SUFFIX); return <div key={item.id} data-testid={`version-card-${item.id}`} className="p-4"><div className="flex items-center justify-between"><div><span className="font-medium">v{item.version_no}</span><span className="ml-2 text-xs text-zinc-400">{datetime(item.created_at)}</span></div><Badge tone={statusTone(item.status)}>{item.status === "published" ? "当前版本" : statusLabel(item.status)}</Badge></div><div className="mt-2 flex gap-2">{canPublish ? <Button size="sm" variant="outline" aria-label={`发布版本 v${item.version_no}`} onClick={() => publish.mutate({ scheduleId: item.id })}><Send className="size-3.5" />发布此版本</Button> : null}{canRollback ? <Button size="sm" variant="ghost" aria-label={`回滚到版本 v${item.version_no}`} onClick={() => rollback.mutate({ scheduleId: item.id })}><RotateCcw className="size-3.5" />回滚到此版本</Button> : null}{item.status === "published" ? <span className="text-xs text-zinc-400">当前正在使用</span> : null}{canDelete ? <Button size="sm" variant="ghost" className="ml-auto text-red-600 hover:bg-red-50 hover:text-red-700" aria-label={`删除版本 v${item.version_no}`} onClick={() => setPendingDelete(item)}><Trash2 className="size-3.5" />删除</Button> : null}</div></div>; })}</div></section>
    </div>
    {canViewAudit ? <section className="border border-zinc-200 bg-white"><div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3 text-sm font-semibold"><History className="size-4" />审计日志</div><div className="divide-y divide-zinc-100">{logs.data?.map((log) => <div key={log.id} className="flex flex-wrap items-center gap-4 px-4 py-2.5 text-sm"><span className="min-w-32 text-zinc-400">{datetime(log.created_at)}</span><span className="font-mono text-xs text-zinc-600">{actionLabel(log.action)}</span><span className="text-zinc-500">{resourceLabel(log.resource_type)}</span><span className="text-zinc-400">{log.resource_id ?? "-"}</span>{Object.keys(log.detail ?? {}).length ? <span className="text-xs text-zinc-500">{JSON.stringify(log.detail)}</span> : null}</div>)}</div></section> : <section className="border border-zinc-200 bg-zinc-50 px-4 py-3 text-xs text-zinc-500">审计日志仅管理员可见；版本列表、版本对比和当前课表状态仍可正常查看。</section>}
    {pendingDelete ? <ConfirmDialog open danger pending={remove.isPending} title={`删除课表版本 v${pendingDelete.version_no}`} description={deleteDescription(pendingDelete, versions.filter((item) => item.parent_id === pendingDelete.id))} confirmLabel="删除" onOpenChange={(open) => { if (!open) setPendingDelete(null); }} onConfirm={() => remove.mutate({ scheduleId: pendingDelete.id })} /> : null}
  </div>;
}

function actionLabel(value: string): string { return localActionLabels[value] ?? auditActionLabel(value); }

function deleteDescription(item: ScheduleSummaryResponse, children: readonly ScheduleSummaryResponse[]): string {
  const lineage = children.length
    ? `该版本是 ${children.map((child) => `v${child.version_no}`).join("、")} 的来源版本，服务端会拒绝删除，请先删掉这些子版本。`
    : "目前没有别的版本以它为来源。";
  return `删除 v${item.version_no}（${statusLabel(item.status)}）会连同它的 ${item.assignment_count ?? 0} 条排课记录一起清除，无法恢复，版本对比与回滚都不再包含它。${lineage}求解任务和数据快照会保留；已下发飞书日历或被调课事件引用的版本，服务端同样会拒绝删除。`;
}

function Value({ label, value }: { label: string; value: string }) { return <div className="p-4"><div className="text-xs text-zinc-400">{label}</div><div className="mt-1 text-xl font-semibold">{value}</div></div>; }
