import { useQueryClient } from "@tanstack/react-query";
import { Activity, Bot, CalendarPlus, Play, Settings2, SlidersHorizontal, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { getListSchedulesApiV1SchedulesGetQueryKey, getListSolverRunsApiV1SolverRunsGetQueryKey, getOverviewApiV1OverviewGetQueryKey, useGetSolverRunApiV1SolverRunsRunIdGet, useListRulesApiV1RulesGet, useListSchedulesApiV1SchedulesGet, useListSolverRunsApiV1SolverRunsGet, useSubmitSolverRunApiV1SolverRunsPost } from "@/api/generated/client";
import { type SolverRunResponse } from "@/api/generated/models";
import { http } from "@/api/http";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { errorMessage } from "@/lib/format";
import { modelStatusLabel, statusLabel } from "@/lib/labels";
import { latestDraftSchedule, preferredSchedule } from "@/lib/schedule";
import { statusTone } from "@/lib/status";

interface Interpretation {
  instruction: string;
  source: "feishu_aily";
  aily_configured: boolean;
  business_lines: string[];
  product_types: string[];
  class_business_ids: string[];
  date_from: string | null;
  date_to: string | null;
  date_window_days: number;
  recognized_rules: string[];
  solver_rules: string[];
  summary: string;
}

interface CalendarConflict { course_session_id: string; calendar_user_id: string; lesson_date: string; start_time: string; end_time: string; source: string }
interface CalendarResult { dry_run: boolean; would_publish: number; published: number; existing: number; skipped_unmapped: number; conflict_count: number; conflicts: CalendarConflict[] }

export function SolverPage() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [preferenceWeight, setPreferenceWeight] = useState(100);
  const [runId, setRunId] = useState("");
  const [current, setCurrent] = useState<SolverRunResponse | null>(null);
  const [instruction, setInstruction] = useState("请在固定上课时段不变的前提下，重新安排日期和教室，优先最少变更，并检查教室与具体日程账号冲突");
  const [interpretation, setInterpretation] = useState<Interpretation | null>(null);
  const [interpreting, setInterpreting] = useState(false);
  const [publishing, setPublishing] = useState<"dry-run" | "publish" | null>(null);
  const [calendarResult, setCalendarResult] = useState<CalendarResult | null>(null);
  const [ailyReady, setAilyReady] = useState<boolean | null>(null);
  const rules = useListRulesApiV1RulesGet({ status: "active" });
  const runs = useListSolverRunsApiV1SolverRunsGet({ query: { refetchInterval: 5000 } });
  const schedules = useListSchedulesApiV1SchedulesGet();
  useEffect(() => {
    void http.get<{ app_configuration: { aily_configured: boolean } }>("/api/v1/integrations/feishu/connection")
      .then(({ data }) => setAilyReady(data.app_configuration.aily_configured))
      .catch(() => setAilyReady(null));
  }, []);
  const progress = useGetSolverRunApiV1SolverRunsRunIdGet(runId, { query: { enabled: Boolean(runId), refetchInterval: (query) => query.state.data?.status === "completed" || query.state.data?.status === "failed" ? false : 700 } });
  const submit = useSubmitSolverRunApiV1SolverRunsPost({ mutation: { onSuccess: (result) => { setRunId(result.id); setCurrent(result); toast.success("求解任务已创建"); }, onError: (error) => toast.error(errorMessage(error)) } });
  useEffect(() => { if (progress.data) { setCurrent(progress.data); if (progress.data.status === "completed" || progress.data.status === "failed") { void Promise.all([client.invalidateQueries({ queryKey: getListSchedulesApiV1SchedulesGetQueryKey() }), client.invalidateQueries({ queryKey: getListSolverRunsApiV1SolverRunsGetQueryKey() }), client.invalidateQueries({ queryKey: getOverviewApiV1OverviewGetQueryKey() })]); } } }, [client, progress.data]);
  if (rules.isPending || runs.isPending || schedules.isPending) return <LoadingState />;
  if (rules.isError || runs.isError || schedules.isError) return <ErrorState retry={() => { void rules.refetch(); void runs.refetch(); void schedules.refetch(); }} />;
  const activeRun = current ?? runs.data?.[0] ?? null;
  const schedule = activeRun?.status === "completed"
    ? (latestDraftSchedule(schedules.data) ?? preferredSchedule(schedules.data))
    : preferredSchedule(schedules.data);
  const interpret = async () => {
    setInterpreting(true);
    try {
      const { data } = await http.post<Interpretation>("/api/v1/assistant/interpret", { instruction });
      setInterpretation(data);
      toast.success("飞书 Aily 已完成解析");
    } catch (error) {
      const message = errorMessage(error);
      toast.error(message);
      if (message.includes("配置飞书 Aily")) setAilyReady(false);
    } finally {
      setInterpreting(false);
    }
  };
  const solveFromInterpretation = async () => { if (!interpretation) return; try { const { data } = await http.post<SolverRunResponse>("/api/v1/assistant/solve", { instruction: interpretation.instruction, business_lines: interpretation.business_lines, product_types: interpretation.product_types, class_business_ids: interpretation.class_business_ids, date_from: interpretation.date_from, date_to: interpretation.date_to, date_window_days: interpretation.date_window_days, solver_rules: interpretation.solver_rules, time_limit_seconds: 30, wait: false }); setRunId(data.id); setCurrent(data); toast.success("确认完成，CP-SAT 求解已启动"); } catch (error) { toast.error(errorMessage(error)); } };
  const publishCalendar = async (dryRun: boolean) => { if (!schedule) return; setPublishing(dryRun ? "dry-run" : "publish"); try { const { data } = await http.post<CalendarResult>(`/api/v1/schedules/${schedule.id}/calendar-publish`, { calendar_id: "primary", need_notification: true, dry_run: dryRun }); setCalendarResult(data); toast.success(dryRun ? `预检完成：预计下发 ${data.would_publish} 个日程，发现 ${data.conflict_count} 个冲突` : `已下发 ${data.published} 个日程，发现 ${data.conflict_count} 个冲突`); } catch (error) { toast.error(errorMessage(error)); } finally { setPublishing(null); } };
  return <div className="space-y-5">
    <PageHeader title="排课求解" actions={<Badge tone="blue">Aily + CP-SAT</Badge>} />
    <section className="border border-blue-200 bg-blue-50/40 p-5"><div className="flex items-center gap-2"><Bot className="size-4 text-blue-600" /><h2 className="font-semibold">一句话排课</h2><Badge tone={ailyReady === true ? "green" : "yellow"}>{ailyReady === true ? "飞书 Aily 已配置" : ailyReady === false ? "飞书 Aily 待配置" : "正在读取 Aily 配置"}</Badge></div><p className="mt-2 text-xs text-zinc-500">自然语言 → 飞书 Aily 结构化范围与规则 → 人工确认 → CP-SAT 确定性求解</p>{ailyReady === false ? <div className="mt-4 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900"><div>请先在“飞书集成”中配置 Aily 应用标识和技能标识。</div><Button className="mt-3" size="sm" variant="outline" onClick={() => navigate("/integrations?section=aily")}><Settings2 className="size-4" />前往配置飞书 Aily</Button></div> : ailyReady === true ? <><textarea aria-label="一句话排课指令" className="mt-4 min-h-24 w-full rounded-md border border-zinc-300 bg-white p-3 text-sm outline-none focus:border-blue-500" value={instruction} onChange={(event) => { setInstruction(event.target.value); setInterpretation(null); }} /><div className="mt-3 flex flex-wrap gap-2"><Button onClick={interpret} disabled={interpreting || instruction.trim().length < 2}><Sparkles className="size-4" />{interpreting ? "正在解析" : "解析排课指令"}</Button>{interpretation ? <Button variant="outline" onClick={solveFromInterpretation}><Play className="size-4" />确认并开始求解</Button> : null}</div></> : <div className="mt-4 h-16 animate-pulse bg-white/70" />}{interpretation ? <><p className="mt-3 border-l-2 border-blue-400 bg-white/70 px-3 py-2 text-xs text-zinc-600">{interpretation.summary}；请确认下方范围后再启动求解。</p><div className="mt-4 grid gap-3 border-t border-blue-200 pt-4 text-sm md:grid-cols-3"><Scope label="业务线" values={interpretation.business_lines} /><Scope label="产品班型" values={interpretation.product_types} /><Scope label="班级范围" values={interpretation.class_business_ids} /><Scope label="日期范围" values={[interpretation.date_from, interpretation.date_to].filter(Boolean) as string[]} /><Scope label="日期调整窗口" values={[`${interpretation.date_window_days} 天`]} /><Scope label="识别规则" values={interpretation.recognized_rules} /></div></> : null}</section>
    <div className="grid gap-2 xl:grid-cols-[360px_minmax(0,1fr)]"><section className="border border-zinc-200 bg-white p-5"><div className="flex items-center gap-2"><SlidersHorizontal className="size-4 text-blue-600" /><h2 className="font-semibold">手动求解参数</h2></div><label className="mt-6 block text-sm text-zinc-700">教师偏好 <span className="float-right font-mono text-zinc-500">{preferenceWeight}</span><input className="mt-3 w-full accent-blue-600" type="range" min="0" max="300" step="10" value={preferenceWeight} onChange={(event) => setPreferenceWeight(Number(event.target.value))} /></label><div className="mt-5 grid grid-cols-2 gap-3 text-sm"><div className="border-l-2 border-zinc-300 pl-3"><div className="text-xs text-zinc-400">座位浪费</div><div className="mt-1">1</div></div><div className="border-l-2 border-zinc-300 pl-3"><div className="text-xs text-zinc-400">最小变更</div><div className="mt-1">100000</div></div></div><Button className="mt-7 w-full" onClick={() => submit.mutate({ data: { time_limit_seconds: 30, preference_weight: preferenceWeight, seat_waste_weight: 1, change_weight: 100000, wait: false } })} disabled={submit.isPending || activeRun?.status === "running"}><Play className="size-4" />按参数开始求解</Button></section><RunPanel run={activeRun} /></div>
    <section className="border border-zinc-200 bg-white p-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><div className="flex items-center gap-2"><CalendarPlus className="size-4 text-blue-600" /><h2 className="font-semibold">教师日历下发</h2></div><p className="mt-1 text-xs text-zinc-500">{schedule ? `当前课表：${schedule.name}` : "当前没有可下发课表"}。教师忙闲冲突会告警，但正式下发仍继续创建日程。</p></div><div className="flex gap-2"><Button variant="outline" onClick={() => publishCalendar(true)} disabled={!schedule || publishing !== null}>{publishing === "dry-run" ? "预检中" : "忙闲预检"}</Button><Button onClick={() => publishCalendar(false)} disabled={!schedule || publishing !== null}>{publishing === "publish" ? "正在下发" : "确认下发"}</Button></div></div>{calendarResult ? <><div className="mt-4 grid gap-2 sm:grid-cols-3 xl:grid-cols-6"><Value label="预计下发" value={String(calendarResult.would_publish)} /><Value label="本次发布" value={String(calendarResult.published)} /><Value label="已存在" value={String(calendarResult.existing)} /><Value label="待补账号" value={String(calendarResult.skipped_unmapped)} /><Value label="冲突告警" value={String(calendarResult.conflict_count)} /><Value label="执行模式" value={calendarResult.dry_run ? "仅预检" : "正式下发"} /></div>{calendarResult.skipped_unmapped ? <div className="mt-3 border-l-2 border-amber-500 bg-amber-50 px-4 py-2 text-xs text-amber-900">未映射具体飞书账号的课程已跳过，请先补充课程账号或教师账号。</div> : null}</> : null}{calendarResult?.conflicts.length ? <div className="mt-4 max-h-48 overflow-auto border-l-2 border-red-500 bg-red-50 px-4 py-2 text-xs text-red-900"><div className="mb-1 font-medium">冲突明细（正式下发仍会创建并标记冲突）</div>{calendarResult.conflicts.slice(0, 20).map((item, index) => <div key={`${item.course_session_id}-${item.lesson_date}-${item.source}-${index}`}>{item.lesson_date} {item.start_time}-{item.end_time} / {item.calendar_user_id} / 课程 {item.course_session_id.slice(0, 8)} / {item.source === "feishu_freebusy" ? "飞书已有忙碌" : "待下发课表内部重叠"}</div>)}</div> : null}</section>
    <section className="border border-zinc-200 bg-white"><div className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold">求解记录</div><div className="overflow-x-auto"><table className="w-full min-w-[720px] text-left text-sm"><thead className="bg-zinc-50 text-xs text-zinc-500"><tr><th className="h-9 px-4">任务</th><th>状态</th><th>模型结果</th><th>目标值</th><th>最佳界</th><th>耗时</th></tr></thead><tbody>{runs.data?.map((run) => <tr key={run.id} className="border-t border-zinc-100"><td className="h-10 px-4 font-mono text-xs">{run.id.slice(0, 8)}</td><td><Badge tone={statusTone(run.status)}>{statusLabel(run.status)}</Badge></td><td>{modelStatusLabel(run.model_status)}</td><td>{run.objective_value?.toFixed(1) ?? "-"}</td><td>{run.best_bound?.toFixed(1) ?? "-"}</td><td>{run.wall_time_seconds?.toFixed(2) ?? "-"} 秒</td></tr>)}</tbody></table></div></section>
  </div>;
}

function Scope({ label, values }: { label: string; values: string[] }) { return <div><div className="text-xs text-zinc-400">{label}</div><div className="mt-1 flex flex-wrap gap-1">{values.length ? values.map((value) => <Badge key={value} tone="blue">{value}</Badge>) : <span className="text-xs text-zinc-500">全部</span>}</div></div>; }
function RunPanel({ run }: { run: SolverRunResponse | null }) { return <section className="border border-zinc-200 bg-white p-5"><div className="flex items-center justify-between"><div className="flex items-center gap-2"><Activity className="size-4 text-blue-600" /><h2 className="font-semibold">实时状态</h2></div><Badge tone={statusTone(run?.status)}>{statusLabel(run?.status) ?? "待启动"}</Badge></div><div className="mt-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Value label="模型状态" value={modelStatusLabel(run?.model_status)} /><Value label="目标函数值" value={run?.objective_value?.toFixed(1) ?? "-"} /><Value label="最佳界" value={run?.best_bound?.toFixed(1) ?? "-"} /><Value label="耗时" value={run?.wall_time_seconds ? `${run.wall_time_seconds.toFixed(2)} 秒` : "-"} /></div>{run?.model_status === "INFEASIBLE" ? <div className="mt-6 border-l-2 border-red-600 bg-red-50 px-4 py-3 text-sm text-red-800">冲突规则：{run.conflict_rule_ids.join("、") || "模型未返回可追溯规则"}</div> : null}{run?.priority_explanations?.length ? <div className="mt-4 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">{run.priority_explanations[0]}</div> : null}{run?.error_message ? <div className="mt-4 text-sm text-red-700">{run.error_message}</div> : null}</section>; }
function Value({ label, value }: { label: string; value: string }) { return <div><div className="text-xs text-zinc-400">{label}</div><div className="mt-1 font-mono text-sm text-zinc-800">{value}</div></div>; }
