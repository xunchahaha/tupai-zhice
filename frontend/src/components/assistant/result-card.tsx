import { CalendarDays, CheckCircle2, Pencil, Send } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet } from "@/api/generated/client";
import { type ScheduleDiffResponse, type ScheduleSummaryResponse, type SolverRunResponse } from "@/api/generated/models";
import { AcceptanceSummary } from "@/components/assistant/acceptance-summary";
import { AiProbeNotice } from "@/components/assistant/ai-probe-notice";
import { RefineInput } from "@/components/assistant/refine-input";
import { type AssistantTask } from "@/components/assistant/use-assistant-task";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatRoom, formatSlot } from "@/lib/format";
import { diffKindLabel } from "@/lib/labels";
import { assistantPath, schedulePath } from "@/lib/routes";
import { usePublishSchedule } from "@/lib/use-publish-schedule";

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-zinc-400">{label}</div>
      <div className="mt-1 font-mono text-sm text-zinc-800">{value}</div>
    </div>
  );
}

/** 相对基准版本实际发生变化的课次：日期/时段/教室变化数 + 前 30 条明细（默认展开）。 */
function ChangeDetail({ diff }: { diff: ScheduleDiffResponse }) {
  const changed = diff.items.filter((item) => item.change_kind !== "unchanged");
  const dateChanges = changed.filter((item) => item.before_lesson_date !== item.after_lesson_date).length;
  const slotChanges = changed.filter((item) => item.before_slot_id !== item.after_slot_id).length;
  const roomChanges = changed.filter((item) => item.before_room_id !== item.after_room_id).length;
  return (
    <>
      <div className="mt-4 grid gap-2 sm:grid-cols-4">
        <Metric label="变更课次" value={String(diff.changed_count)} />
        <Metric label="日期变化" value={String(dateChanges)} />
        <Metric label="时段变化" value={String(slotChanges)} />
        <Metric label="教室变化" value={String(roomChanges)} />
      </div>
      {changed.length ? (
        <div className="mt-4 max-h-72 overflow-auto rounded-lg border border-blue-100 bg-white">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="sticky top-0 bg-zinc-50 text-zinc-500">
              <tr>
                <th className="h-8 px-3">班级 / 教师</th>
                <th>调整前</th>
                <th>调整后</th>
                <th>调整类型</th>
              </tr>
            </thead>
            <tbody>
              {changed.slice(0, 30).map((item) => (
                <tr key={item.course_business_id} className="border-t border-zinc-100 transition-colors hover:bg-blue-50/20">
                  <td className="px-3 py-2">
                    <div className="max-w-[180px] truncate font-medium text-zinc-900" title={item.course_business_id}>
                      {item.class_business_id || "未指定班级"}
                    </div>
                    <div className="text-xs text-zinc-500">{item.teacher_business_id || "未指定教师"}</div>
                  </td>
                  <td className="px-3 py-2 text-zinc-600">
                    <div>{item.before_lesson_date ?? "-"}</div>
                    <div className="text-xs text-zinc-400">{formatSlot(item.before_slot_id)} · {formatRoom(item.before_room_id)}</div>
                  </td>
                  <td className="px-3 py-2 text-blue-700">
                    <div className="font-medium">{item.after_lesson_date ?? "-"}</div>
                    <div className="text-xs text-blue-600/80">{formatSlot(item.after_slot_id)} · {formatRoom(item.after_room_id)}</div>
                  </td>
                  <td className="px-3 py-2">
                    <Badge tone="blue">{diffKindLabel(item.change_kind)}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {changed.length > 30 ? <p className="border-t border-zinc-100 px-3 py-2 text-xs text-zinc-500">仅展示前 30 条，完整列表请打开版本对比。</p> : null}
        </div>
      ) : <p className="mt-4 text-sm text-zinc-500">本次没有相对基准版本发生变化。</p>}
    </>
  );
}

/**
 * 「已生成草稿，调整了 N 节课」：结果 + 三个明确、彼此独立的动作——
 * 查看课表草稿 / 继续调整 / 审批发布。生成、发布、下发是三件事，发布永远不由生成流程自动触发；
 * 没有发布权限的人只能等待有审批权限的人发布。教师日历下发不在这里（在「课表」的分享与订阅）。
 */
export function ResultCard({
  task,
  run,
  draft,
  scheduleList,
  canPublish,
}: {
  task: AssistantTask;
  run: SolverRunResponse;
  draft: ScheduleSummaryResponse | undefined;
  scheduleList: ScheduleSummaryResponse[];
  canPublish: boolean;
}) {
  const navigate = useNavigate();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const publish = usePublishSchedule({ onPublished: () => setConfirmOpen(false) });
  // 基准：草稿的来源版本；没有来源就取已发布的那一版。
  const base = draft
    ? (draft.parent_id
      ? scheduleList.find((item) => item.id === draft.parent_id)
      : scheduleList.find((item) => item.status === "published" && item.id !== draft.id))
    : undefined;
  const diffQuery = useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet(
    base?.id ?? "",
    draft?.id ?? "",
    { query: { enabled: Boolean(base?.id && draft?.id && base.id !== draft.id) } },
  );
  const diff = base ? diffQuery.data : undefined;
  const published = draft?.status === "published";
  const canParse = task.probe.ready === true && !task.submitBlockedReason;
  const title = !draft
    ? "已生成草稿，正在整理课表…"
    : diff
      ? `已生成草稿，调整了 ${diff.changed_count} 节课`
      : `已生成草稿 v${draft.version_no}`;
  return (
    <section aria-label="排课结果" className="rounded-lg border border-blue-200 bg-blue-50/40 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <CheckCircle2 className="size-4 text-emerald-600" />
        <h2 className="font-semibold">{title}</h2>
        {published ? <Badge tone="green">已发布为当前课表</Badge> : draft ? <Badge tone="yellow">草稿 · 未发布</Badge> : null}
      </div>
      {draft ? (
        <p className="mt-1 text-xs text-zinc-600">
          {base ? `v${base.version_no}「${base.name}」→ v${draft.version_no}「${draft.name}」。下面列出实际发生变化的课次。` : `本次已生成 v${draft.version_no} 草稿，但还没有可比较的基准版本。`}
        </p>
      ) : null}
      {draft && base && diffQuery.isLoading ? <p className="mt-4 text-sm text-zinc-500">正在整理本次排课调整……</p> : null}
      {draft && base && diffQuery.isError ? <p className="mt-4 text-sm text-amber-800">调整明细暂时没读出来，可以打开完整版本对比查看。</p> : null}
      {diff ? <ChangeDetail diff={diff} /> : null}
      {diff && draft ? (
        <div className="mt-2">
          <Link className="text-xs text-blue-700 underline-offset-2 hover:underline" to={schedulePath({ view: "history", version: draft.id })}>查看完整版本对比</Link>
        </div>
      ) : null}
      <AcceptanceSummary run={run} task={task} />
      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-blue-200 pt-4">
        {draft ? (
          <Button onClick={() => navigate(schedulePath({ version: draft.id }))}>
            <CalendarDays className="size-4" />{published ? "查看课表" : "查看课表草稿"}
          </Button>
        ) : null}
        {task.canRefine ? (
          <Button variant="outline" aria-expanded={task.refine.open} onClick={() => task.setRefine(task.refine.open ? { open: false, text: "" } : { open: true, text: "" })}>
            <Pencil className="size-4" />继续调整
          </Button>
        ) : (
          // 没有可继承的需求上下文（手动排课的结果、直接打开的求解记录）：继续调整无从谈起，改为重新提需求。
          <Button variant="outline" onClick={() => { task.resetTask(); navigate(assistantPath()); }}>
            <Pencil className="size-4" />新建需求
          </Button>
        )}
        {draft ? (
          <Button variant="ghost" onClick={() => navigate(schedulePath({ view: "adjust", version: draft.id }))}>在课表里手动调整</Button>
        ) : null}
        {draft && !published && canPublish ? (
          <Button variant="outline" onClick={() => setConfirmOpen(true)}>
            <Send className="size-4" />审批发布
          </Button>
        ) : null}
      </div>
      {draft && !published && !canPublish ? (
        <p className="mt-2 text-xs text-zinc-500">草稿已生成，等待有审批权限的人发布。</p>
      ) : null}
      {task.refine.open ? (
        <RefineInput
          label="还要改什么？"
          placeholder="例如：张老师周三晚上也不能上，其他保持不变"
          value={task.refine.text}
          onChange={(text) => task.setRefine({ open: true, text })}
          onSubmit={() => void task.refineInterpret(task.refine.text, draft?.id)}
          onCancel={() => task.setRefine({ open: false, text: "" })}
          disabled={!canParse || task.phase === "thinking"}
          disabledReason={
            task.probe.ready === true
              ? task.submitBlockedReason ?? undefined
              : <AiProbeNotice probe={task.probe} missingText="AI 尚未接入，无法继续解析；可用「在课表里手动调整」或「手动排课」。" />
          }
        />
      ) : null}
      {draft ? (
        <ConfirmDialog
          open={confirmOpen}
          title={`发布草稿 v${draft.version_no}？`}
          description="发布后成为当前课表，并同步到已启用的外部集成；不会自动下发日历。"
          confirmLabel="确认发布"
          pending={publish.isPending}
          onOpenChange={setConfirmOpen}
          onConfirm={() => publish.mutate({ scheduleId: draft.id })}
        />
      ) : null}
    </section>
  );
}
