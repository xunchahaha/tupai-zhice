import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeftRight, History, RotateCcw, Send } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { getListSchedulesApiV1SchedulesGetQueryKey, useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet, useListAuditLogsApiV1AuditLogsGet, useListSchedulesApiV1SchedulesGet, usePublishScheduleApiV1SchedulesScheduleIdPublishPost, useRollbackScheduleApiV1SchedulesScheduleIdRollbackPost } from "@/api/generated/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { auditActionLabel, diffKindLabel, resourceLabel, statusLabel } from "@/lib/labels";
import { datetime, errorMessage } from "@/lib/format";
import { preferredSchedule } from "@/lib/schedule";
import { statusTone } from "@/lib/status";

export function VersionsPage() {
  const client = useQueryClient();
  const schedules = useListSchedulesApiV1SchedulesGet();
  const logs = useListAuditLogsApiV1AuditLogsGet({ limit: 12 });
  const [base, setBase] = useState("");
  const [target, setTarget] = useState("");
  useEffect(() => {
    const current = preferredSchedule(schedules.data);
    if (!current) return;
    if (!target) setTarget(current.id);
    if (!base) setBase(schedules.data?.find((item) => item.id !== current.id)?.id ?? "");
  }, [base, schedules.data, target]);
  const diff = useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet(base, target, { query: { enabled: Boolean(base && target && base !== target) } });
  const refresh = () => void client.invalidateQueries({ queryKey: getListSchedulesApiV1SchedulesGetQueryKey() });
  const publish = usePublishScheduleApiV1SchedulesScheduleIdPublishPost({ mutation: { onSuccess: () => { toast.success("版本已发布为当前课表"); refresh(); }, onError: (error) => toast.error(errorMessage(error)) } });
  const rollback = useRollbackScheduleApiV1SchedulesScheduleIdRollbackPost({ mutation: { onSuccess: () => { toast.success("已恢复到历史版本"); refresh(); }, onError: (error) => toast.error(errorMessage(error)) } });
  if (schedules.isPending || logs.isPending) return <LoadingState />;
  if (schedules.isError || logs.isError) return <ErrorState retry={() => { void schedules.refetch(); void logs.refetch(); }} />;
  return <div className="space-y-5">
    <PageHeader title="版本与回滚" />
    <section className="border border-blue-200 bg-blue-50/40 px-4 py-3 text-sm text-blue-900">发布：把草稿版本设为当前课表；回滚：恢复已经发布过的历史版本。当前版本不显示操作按钮，草稿不会显示“回滚”。</section>
    <div className="grid gap-2 xl:grid-cols-[minmax(0,1.4fr)_minmax(340px,0.8fr)]">
      <section className="border border-zinc-200 bg-white">
        <div className="flex flex-wrap items-center gap-2 border-b border-zinc-200 p-4"><select aria-label="基准版本" className="h-8 min-w-36 rounded-md border border-zinc-300 px-2 text-xs" value={base} onChange={(event) => setBase(event.target.value)}><option value="">基准版本</option>{schedules.data?.map((item) => <option key={item.id} value={item.id}>v{item.version_no} / {statusLabel(item.status)}</option>)}</select><ArrowLeftRight className="size-4 text-zinc-400" /><select aria-label="目标版本" className="h-8 min-w-36 rounded-md border border-zinc-300 px-2 text-xs" value={target} onChange={(event) => setTarget(event.target.value)}><option value="">目标版本</option>{schedules.data?.map((item) => <option key={item.id} value={item.id}>v{item.version_no} / {statusLabel(item.status)}</option>)}</select></div>
        {diff.isPending ? <LoadingState /> : diff.data ? <><div className="grid grid-cols-2 border-b border-zinc-100"><Value label="变更课次" value={String(diff.data.changed_count)} /><Value label="保持不变" value={String(diff.data.unchanged_count)} /></div><div className="max-h-[380px] overflow-y-auto"><table className="w-full text-left text-sm"><thead className="sticky top-0 bg-zinc-50 text-xs text-zinc-500"><tr><th className="h-9 px-4">课次</th><th>变更前</th><th>变更后</th><th>变更类型</th></tr></thead><tbody>{diff.data.items.filter((item) => item.change_kind !== "unchanged").map((item) => <tr key={item.course_business_id} className="border-t border-zinc-100"><td className="h-10 px-4 font-mono text-xs">{item.course_business_id}</td><td>{item.before_slot_id ?? "-"} / {item.before_room_id ?? "-"}</td><td>{item.after_slot_id ?? "-"} / {item.after_room_id ?? "-"}</td><td><Badge tone="blue">{diffKindLabel(item.change_kind)}</Badge></td></tr>)}</tbody></table></div></> : <div className="grid min-h-44 place-items-center text-sm text-zinc-400">选择两个不同版本进行对比</div>}
      </section>
      <section className="border border-zinc-200 bg-white"><div className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold">版本记录</div><div className="divide-y divide-zinc-100">{schedules.data?.map((item) => { const canPublish = item.status === "draft"; const canRollback = item.status === "archived" || item.status === "rolled_back"; return <div key={item.id} data-testid={`version-card-${item.id}`} className="p-4"><div className="flex items-center justify-between"><div><span className="font-medium">v{item.version_no}</span><span className="ml-2 text-xs text-zinc-400">{datetime(item.created_at)}</span></div><Badge tone={statusTone(item.status)}>{item.status === "published" ? "当前版本" : statusLabel(item.status)}</Badge></div><div className="mt-2 flex gap-2">{canPublish ? <Button size="sm" variant="outline" aria-label={`发布版本 v${item.version_no}`} onClick={() => publish.mutate({ scheduleId: item.id })}><Send className="size-3.5" />发布此版本</Button> : null}{canRollback ? <Button size="sm" variant="ghost" aria-label={`回滚到版本 v${item.version_no}`} onClick={() => rollback.mutate({ scheduleId: item.id })}><RotateCcw className="size-3.5" />回滚到此版本</Button> : null}{item.status === "published" ? <span className="text-xs text-zinc-400">当前正在使用</span> : null}</div></div>; })}</div></section>
    </div>
    <section className="border border-zinc-200 bg-white"><div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3 text-sm font-semibold"><History className="size-4" />审计日志</div><div className="divide-y divide-zinc-100">{logs.data?.map((log) => <div key={log.id} className="flex flex-wrap items-center gap-4 px-4 py-2.5 text-sm"><span className="min-w-32 text-zinc-400">{datetime(log.created_at)}</span><span className="font-mono text-xs text-zinc-600">{auditActionLabel(log.action)}</span><span className="text-zinc-500">{resourceLabel(log.resource_type)}</span><span className="text-zinc-400">{log.resource_id ?? "-"}</span></div>)}</div></section>
  </div>;
}

function Value({ label, value }: { label: string; value: string }) { return <div className="p-4"><div className="text-xs text-zinc-400">{label}</div><div className="mt-1 text-xl font-semibold">{value}</div></div>; }
