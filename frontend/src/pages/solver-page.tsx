import { useQueryClient } from "@tanstack/react-query";
import { Activity, Bot, CalendarPlus, CircleAlert, ClipboardCopy, CornerUpLeft, Loader2, LockKeyhole, MessageSquareText, Play, RefreshCw, Settings2, SlidersHorizontal, Sparkles } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { getListSchedulesApiV1SchedulesGetQueryKey, getListSolverRunsApiV1SolverRunsGetQueryKey, getOverviewApiV1OverviewGetQueryKey, useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet, useGetSolverRunApiV1SolverRunsRunIdGet, useGetScheduleApiV1SchedulesScheduleIdGet, useListCourseSessionsApiV1CourseSessionsGet, useListRulesApiV1RulesGet, useListSchedulesApiV1SchedulesGet, useListSolverRunsApiV1SolverRunsGet, useSubmitSolverRunApiV1SolverRunsPost } from "@/api/generated/client";
import { type CourseSessionResponse, type ScheduleDiffResponse, type ScheduleSummaryResponse, type SolveRequest, type SolverRunExplanation, type SolverRunResponse } from "@/api/generated/models";
import { http } from "@/api/http";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { SetupChecklist } from "@/components/setup-checklist";
import { SopSteps } from "@/components/sop-steps";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { asArray, errorMessage, formatRoom, formatSlot } from "@/lib/format";
import { type Interpretation, streamInterpretInstruction } from "@/lib/interpret-stream";
import { diffKindLabel, modelStatusLabel, statusLabel } from "@/lib/labels";
import { preferredSchedule, scheduleForRun } from "@/lib/schedule";
import { modelStatusTone, statusTone } from "@/lib/status";

/** 解析过程状态机：thinking=请求在途，parsed=成功（参数面板门控），failed=页内错误条。 */
type InterpretPhase = "idle" | "thinking" | "parsed" | "failed";

/**
 * 解析阶段文案：流式回退（或事件空窗期）按 2.5s 顺序轮播；流式通道的 stage
 * 事件键值与这里一一对应，事件到达即跳到对应阶段并锁定轮播。
 */
const INTERPRET_STAGES = [
  { key: "connect", text: "正在连接 AI 模型…" },
  { key: "read", text: "正在理解调整需求…" },
  { key: "match", text: "正在匹配课程与日期…" },
  { key: "validate", text: "正在校验解析结果…" },
];

type SolverRule = NonNullable<SolveRequest["solver_rules"]>[number];

interface SolverParamValues {
  time_limit_seconds: number;
  date_window_days: number;
  change_weight: number;
  solver_rules: SolverRule[];
  business_lines: string[];
  class_business_ids: string[];
  date_from: string | null;
  date_to: string | null;
}

/** 教室、教师冲突是系统级硬约束，界面展示但不允许关闭。 */
const SYSTEM_SOLVER_RULES: Array<{ key: SolverRule; label: string; hint: string }> = [
  { key: "room_no_overlap", label: "教室不重叠", hint: "同一教室的真实时间区间不可重叠，始终生效" },
  { key: "teacher_no_overlap", label: "教师不重叠", hint: "具体个人教师不可同时上两节课；教研组名称不代表已落实到个人" },
];
const OPTIONAL_SOLVER_RULES: Array<{ key: SolverRule; label: string; hint: string }> = [
  { key: "fixed_time", label: "固定上课时段", hint: "只调整日期和教室，不动上课时刻" },
  { key: "calendar_no_overlap", label: "日程账号不重叠", hint: "已映射日历账号的教师按个人日历约束" },
  { key: "minimize_changes", label: "最小化变更", hint: "优先保持与已发布课表一致" },
];
const SYSTEM_RULE_KEYS = SYSTEM_SOLVER_RULES.map((item) => item.key);

const defaultParams: SolverParamValues = {
  time_limit_seconds: 30,
  date_window_days: 7,
  change_weight: 100000,
  solver_rules: [...SYSTEM_SOLVER_RULES, ...OPTIONAL_SOLVER_RULES].map((item) => item.key),
  business_lines: [],
  class_business_ids: [],
  date_from: null,
  date_to: null,
};

/**
 * 排不出来时后端会多给一段可直接粘回「一句话排课」输入框的指令草稿。
 * 这段文字由后端确定性拼出，不在 orval 生成的模型里，故在这里就地扩展类型。
 */
type SolverRunExplanationDetail = SolverRunExplanation & { suggested_instruction?: string | null };

interface CalendarConflict { course_session_id: string; calendar_user_id: string; lesson_date: string; start_time: string; end_time: string; source: string }
interface CalendarResult { dry_run: boolean; would_publish: number; published: number; existing: number; skipped_unmapped: number; conflict_count: number; conflicts: CalendarConflict[] }

export function SolverPage() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params, setParams] = useState<SolverParamValues>(defaultParams);
  const [runId, setRunId] = useState("");
  const [current, setCurrent] = useState<SolverRunResponse | null>(null);
  const [instruction, setInstruction] = useState("请在固定上课时段不变的前提下，重新安排日期和教室，优先最少变更，并检查教室与具体日程账号冲突");
  const [interpretation, setInterpretation] = useState<Interpretation | null>(null);
  const [phase, setPhase] = useState<InterpretPhase>("idle");
  const [stageIndex, setStageIndex] = useState(0);
  // 流式解析期间实时追加的思考文本；完成后的回看仍以 result 里的 thinking 全文为准。
  const [liveThinking, setLiveThinking] = useState("");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [parsedSeconds, setParsedSeconds] = useState(0);
  const [interpretError, setInterpretError] = useState("");
  // 解析出的日期窗口是否已回填手动参数面板（来源徽标用，用户手动改动后即失效）。
  const [aiWindowFilled, setAiWindowFilled] = useState(false);
  const interpretAbort = useRef<AbortController | null>(null);
  const interpretStartedAt = useRef(0);
  // 收到后端 stage 事件后锁定轮播（事件即真实进度），避免计时器把阶段倒拨回去。
  const stageLocked = useRef(false);
  // D6 只预填日期三元组，且用户手动改过的字段不再被下一次解析覆盖。
  const manuallyEdited = useRef({ date_from: false, date_to: false, date_window_days: false });
  const [publishing, setPublishing] = useState<"dry-run" | "publish" | null>(null);
  const [calendarResult, setCalendarResult] = useState<CalendarResult | null>(null);
  const [assistantReady, setAssistantReady] = useState<boolean | null>(null);
  const [assistantEngine, setAssistantEngine] = useState("AI 模型");
  const rules = useListRulesApiV1RulesGet({ status: "active" });
  const courses = useListCourseSessionsApiV1CourseSessionsGet();
  const runs = useListSolverRunsApiV1SolverRunsGet({ query: { refetchInterval: 5000 } });
  const schedules = useListSchedulesApiV1SchedulesGet();
  const [assistantProbeError, setAssistantProbeError] = useState("");
  const probeAssistant = useCallback(() => {
    setAssistantProbeError("");
    void Promise.all([
      http.get<{ configured: boolean; model: string | null }>("/api/v1/integrations/ai/configuration"),
      http.get<{ app_configuration: { aily_configured: boolean } }>("/api/v1/integrations/feishu/connection"),
    ])
      .then(([ai, feishu]) => {
        const aiConfigured = ai.data.configured;
        setAssistantReady(aiConfigured || feishu.data.app_configuration.aily_configured);
        setAssistantEngine(aiConfigured ? (ai.data.model || "通用 AI 模型") : "Aily（可选通道）");
      })
      .catch((error) => {
        // 此前这里直接吞掉错误，界面只显示「正在读取 AI 配置」且没有重试入口。
        setAssistantReady(null);
        setAssistantProbeError(errorMessage(error));
      });
  }, []);
  useEffect(probeAssistant, [probeAssistant]);
  const interpreting = phase === "thinking";
  // thinking 阶段的阶段文案（每 2.5s 顺延）与已用时计时器；离开 thinking 即清理。
  // 收到流式 stage 事件后 stageLocked=true，计时器只更新用时不再轮播阶段。
  useEffect(() => {
    if (phase !== "thinking") return;
    const startedAt = interpretStartedAt.current || Date.now();
    setElapsedSeconds(0);
    setStageIndex(0);
    const timer = window.setInterval(() => {
      const seconds = (Date.now() - startedAt) / 1000;
      setElapsedSeconds(seconds);
      if (!stageLocked.current) setStageIndex(Math.min(Math.floor(seconds / 2.5), INTERPRET_STAGES.length - 1));
    }, 100);
    return () => window.clearInterval(timer);
  }, [phase]);
  const progress = useGetSolverRunApiV1SolverRunsRunIdGet(runId, { query: { enabled: Boolean(runId), refetchInterval: (query) => query.state.data?.status === "completed" || query.state.data?.status === "failed" ? false : 700 } });
  const submit = useSubmitSolverRunApiV1SolverRunsPost({ mutation: { onSuccess: (result) => { setRunId(result.id); setCurrent(result); toast.success("求解任务已创建"); }, onError: (error) => toast.error(errorMessage(error)) } });
  useEffect(() => { if (progress.data) { setCurrent(progress.data); if (progress.data.status === "completed" || progress.data.status === "failed") { void Promise.all([client.invalidateQueries({ queryKey: getListSchedulesApiV1SchedulesGetQueryKey() }), client.invalidateQueries({ queryKey: getListSolverRunsApiV1SolverRunsGetQueryKey() }), client.invalidateQueries({ queryKey: getOverviewApiV1OverviewGetQueryKey() })]); } } }, [client, progress.data]);
  const courseRows = asArray<CourseSessionResponse>(courses.data);
  const scopeOptions = {
    businessLines: [...new Set(courseRows.map((item) => item.business_line ?? "").filter(Boolean))].sort(),
    classes: [...new Set(courseRows.map((item) => item.class_business_id))].sort(),
  };
  const publishedVersion = asArray<ScheduleSummaryResponse>(schedules.data).find((item) => item.status === "published");
  const publishedDetail = useGetScheduleApiV1SchedulesScheduleIdGet(publishedVersion?.id ?? "", { query: { enabled: Boolean(publishedVersion) } });
  const parentDates = new Map(publishedDetail.data?.assignments?.map((item) => [item.course_session_id, item.lesson_date]));
  const selectedCount = courseRows.filter((item) => {
    if (params.business_lines.length && !params.business_lines.includes(item.business_line ?? "")) return false;
    if (params.class_business_ids.length && !params.class_business_ids.includes(item.class_business_id)) return false;
    const lessonDate = parentDates.get(item.id) ?? item.lesson_date ?? "";
    if (params.date_from && lessonDate < params.date_from) return false;
    if (params.date_to && lessonDate > params.date_to) return false;
    return true;
  }).length;
  const runList = asArray<SolverRunResponse>(runs.data);
  const scheduleList = asArray<ScheduleSummaryResponse>(schedules.data);
  const activeRun = current ?? runList[0] ?? null;
  const draftSchedule = scheduleForRun(scheduleList, activeRun);
  const baseSchedule = draftSchedule
    ? (draftSchedule.parent_id
      ? scheduleList.find((item) => item.id === draftSchedule.parent_id)
      : scheduleList.find((item) => item.status === "published" && item.id !== draftSchedule.id))
    : undefined;
  const scheduleDiff = useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet(
    baseSchedule?.id ?? "",
    draftSchedule?.id ?? "",
    { query: { enabled: Boolean(baseSchedule?.id && draftSchedule?.id && baseSchedule.id !== draftSchedule.id) } },
  );
  const schedule = activeRun ? draftSchedule : preferredSchedule(scheduleList);
  if (rules.isPending || runs.isPending || schedules.isPending) return <LoadingState />;
  if (rules.isError || runs.isError || schedules.isError) return <ErrorState retry={() => { void rules.refetch(); void runs.refetch(); void schedules.refetch(); }} />;
  /** 解析成功后的统一收尾：回填、徽标与阶段推进，流式与回退两条路共用。 */
  const applyInterpretation = (data: Interpretation) => {
    setParsedSeconds((Date.now() - interpretStartedAt.current) / 1000);
    setInterpretation(data);
    setAssistantEngine(data.source === "feishu_aily" ? "Aily（可选通道）" : "通用 AI 模型");
    // P1 回填：解析成功后把日期三元组预填进手动参数面板（仅一次，手动改过的不覆盖）。
    setParams((current) => ({
      ...current,
      date_from: manuallyEdited.current.date_from ? current.date_from : data.date_from ?? null,
      date_to: manuallyEdited.current.date_to ? current.date_to : data.date_to ?? null,
      date_window_days: manuallyEdited.current.date_window_days ? current.date_window_days : data.date_window_days ?? current.date_window_days,
    }));
    setAiWindowFilled(!manuallyEdited.current.date_window_days);
    setPhase("parsed");
    toast.success(data.source === "feishu_aily" ? "Aily（可选通道） 已完成解析" : "AI 模型已完成解析");
  };
  const handleInterpretFailure = (message: string) => {
    setInterpretError(message);
    setPhase("failed");
    toast.error(message);
    if (message.includes("配置一句话排课 AI")) setAssistantReady(false);
  };
  const interpret = async () => {
    const controller = new AbortController();
    interpretAbort.current = controller;
    interpretStartedAt.current = Date.now();
    setInterpretError("");
    setLiveThinking("");
    stageLocked.current = false;
    setPhase("thinking");
    try {
      // 优先走 SSE 流式接口：thinking 增量实时上屏，result 与同步接口同构。
      const data = await streamInterpretInstruction(instruction, controller.signal, {
        onThinking: (delta) => setLiveThinking((current) => current + delta),
        onStage: (stage) => {
          const index = INTERPRET_STAGES.findIndex((item) => item.key === stage);
          if (index >= 0) {
            stageLocked.current = true;
            setStageIndex(index);
          }
        },
      });
      applyInterpretation(data);
    } catch (streamError) {
      // 用户主动取消不算失败，安静回到初始态等下一次解析。
      if (controller.signal.aborted) {
        setPhase("idle");
        return;
      }
      try {
        // 流式通道不可用（网络/网关缓冲/协议中断）时回退老接口，降级路径必须保留。
        const { data } = await http.post<Interpretation>("/api/v1/assistant/interpret", { instruction }, { signal: controller.signal });
        applyInterpretation(data);
      } catch (error) {
        if (controller.signal.aborted) {
          setPhase("idle");
          return;
        }
        handleInterpretFailure(errorMessage(error));
      }
    } finally {
      if (interpretAbort.current === controller) interpretAbort.current = null;
    }
  };
  const cancelInterpret = () => interpretAbort.current?.abort();
  /** 手动参数面板字段被用户改过即打脏标记，之后的解析不再覆盖该字段。 */
  const markManualEdit = (field: "date_from" | "date_to" | "date_window_days") => {
    manuallyEdited.current[field] = true;
    if (field === "date_window_days") setAiWindowFilled(false);
  };
  const solveFromInterpretation = async () => { if (!interpretation || interpretation.unsupported_requirements?.length) return; try { const { data } = await http.post<SolverRunResponse>("/api/v1/assistant/solve", { instruction: interpretation.instruction, business_lines: interpretation.business_lines, product_types: interpretation.product_types, class_business_ids: interpretation.class_business_ids, date_from: interpretation.date_from, date_to: interpretation.date_to, date_window_days: interpretation.date_window_days, solver_rules: interpretation.solver_rules, time_limit_seconds: 30, wait: false }); setRunId(data.id); setCurrent(data); toast.success("确认完成，CP-SAT 求解已启动"); } catch (error) { toast.error(errorMessage(error)); } };
  /** 把 AI 给出的建议指令填回输入框，省掉「复制—滚动—粘贴」三步。 */
  const applySuggestedInstruction = (value: string) => {
    setInstruction(value);
    setInterpretation(null);
    setInterpretError("");
    window.scrollTo?.({ top: 0, behavior: "smooth" });
    toast.success("建议指令已填入「一句话排课」，确认无误后可直接解析并重跑");
  };
  const publishCalendar = async (dryRun: boolean) => { if (!schedule) return; setPublishing(dryRun ? "dry-run" : "publish"); try { const { data } = await http.post<CalendarResult>(`/api/v1/schedules/${schedule.id}/calendar-publish`, { calendar_id: "primary", need_notification: true, dry_run: dryRun }); setCalendarResult(data); toast.success(dryRun ? `预检完成：预计下发 ${data.would_publish} 个日程，发现 ${data.conflict_count} 个冲突` : `已下发 ${data.published} 个日程，发现 ${data.conflict_count} 个冲突`); } catch (error) { toast.error(errorMessage(error)); } finally { setPublishing(null); } };
  // D5 渐进披露：解析成功后才展开手动参数面板；但 AI 未配置或探测失败时手动路径是唯一入口，直接展示（D8）。
  const showParams = phase === "parsed" || assistantReady === false || Boolean(assistantProbeError);
  return <div className="space-y-5 animate-fade-in">
    <PageHeader title="排课求解" actions={<Badge tone="blue">AI + CP-SAT</Badge>}><SopSteps /></PageHeader>
    {/* D11 紧凑版就绪清单：数据全部复用本页已有查询，AI 状态直接用页内探测结果。 */}
    <SetupChecklist
      variant="compact"
      masterDataImported={courseRows.length > 0}
      activeRuleCount={Array.isArray(rules.data) ? rules.data.length : 0}
      aiConfigured={assistantReady}
      publishedScheduleCount={scheduleList.filter((item) => item.status === "published").length}
    />
    <section className="border border-blue-200 bg-blue-50/40 p-5"><div className="flex flex-wrap items-center gap-2"><Bot className="size-4 text-blue-600" /><h2 className="font-semibold">一句话排课</h2><Badge tone={assistantReady === true ? "green" : "yellow"}>{assistantReady === true ? `${assistantEngine} 已接入` : assistantReady === false ? "AI 模型待配置" : assistantProbeError ? "AI 配置读取失败" : "正在读取 AI 配置"}</Badge>{assistantProbeError ? <Button size="sm" variant="outline" onClick={probeAssistant}>重试</Button> : null}</div><p className="mt-2 text-xs text-zinc-500">自然语言 → AI 解析业务范围与规则 → 教务确认 → CP-SAT 确定性求解 → 课表与日历下发</p><textarea aria-label="一句话排课指令" className="mt-4 min-h-24 w-full rounded-md border border-zinc-300 bg-white p-3 text-sm outline-none focus:border-blue-500 disabled:bg-zinc-50 disabled:text-zinc-400" value={instruction} disabled={phase === "thinking"} onChange={(event) => { setInstruction(event.target.value); setInterpretation(null); if (phase === "failed") { setPhase("idle"); setInterpretError(""); } }} /><div className="mt-3 flex flex-wrap gap-2"><Button onClick={interpret} disabled={assistantReady !== true || interpreting || instruction.trim().length < 2}><Sparkles className="size-4" />{interpreting ? "AI 正在理解指令" : "让 AI 解析排课指令"}</Button>{interpreting ? <Button variant="outline" onClick={cancelInterpret}>取消解析</Button> : null}{interpretation ? <Button variant="outline" onClick={solveFromInterpretation} disabled={Boolean(interpretation.unsupported_requirements?.length)}><Play className="size-4" />确认并开始求解</Button> : null}</div>{assistantReady === false ? <div className="mt-4 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900"><div>普通集成应用继续负责外部表格和日历；一句话理解改由独立 AI 模型接口完成，不再要求 Aily 应用标识和技能标识。</div><Button className="mt-3" size="sm" variant="outline" onClick={() => navigate("/integrations?section=ai")}><Settings2 className="size-4" />配置一句话排课 AI</Button></div> : null}{phase === "thinking" ? <InterpretProgress stageIndex={stageIndex} elapsedSeconds={elapsedSeconds} liveText={liveThinking} /> : null}{phase === "failed" ? <div role="alert" className="mt-4 border-l-2 border-red-500 bg-red-50 px-4 py-3 text-sm text-red-800"><div>AI 解析失败：{interpretError}</div>{elapsedSeconds > 30 ? <p className="mt-1 text-xs text-amber-800">本次解析超过 30 秒仍未返回，可稍后重试，或改用手动参数求解。</p> : null}<Button className="mt-2" size="sm" variant="outline" onClick={() => void interpret()} disabled={assistantReady !== true || instruction.trim().length < 2}><RefreshCw className="size-3.5" />重试解析</Button></div> : null}{interpretation ? <><p className="mt-3 border-l-2 border-blue-400 bg-white/70 px-3 py-2 text-xs text-zinc-600">{interpretation.summary}；解析来源：{interpretation.source === "feishu_aily" ? "Aily（可选通道）" : assistantEngine}。</p><InterpretThought thinking={interpretation.thinking ?? ""} seconds={parsedSeconds} /><div className="mt-4 grid gap-3 border-t border-blue-200 pt-4 text-sm md:grid-cols-3"><Scope label="业务线" values={interpretation.business_lines} /><Scope label="产品班型" values={interpretation.product_types} /><Scope label="班级范围" values={interpretation.class_business_ids} /><Scope label="日期范围" values={[interpretation.date_from, interpretation.date_to].filter(Boolean) as string[]} /><Scope label="日期调整窗口" values={[`${interpretation.date_window_days} 天`]} /><Scope label="识别规则" values={interpretation.recognized_rules} /></div><div className="mt-3 space-y-2 text-xs text-amber-900">{interpretation.coverage_warnings?.map((warning) => <p key={warning}>{warning}</p>)}{interpretation.unsupported_requirements?.length ? <div role="alert" className="border-l-2 border-amber-500 bg-amber-50 p-3"><strong>以下要求尚未进入求解：</strong><ul>{interpretation.unsupported_requirements.map((requirement) => <li key={requirement}>{requirement}</li>)}</ul><p>请先在规则管理中补充已支持的结构化规则，并修订指令后重新解析。</p></div> : null}</div></> : null}</section>
    <div className="grid gap-2 xl:grid-cols-[360px_minmax(0,1fr)]">
      {showParams ? <div className="animate-fade-in"><SolverParams
        params={params}
        setParams={setParams}
        scope={scopeOptions}
        selectedCount={selectedCount}
        pending={submit.isPending || activeRun?.status === "running"}
        aiWindowBadge={aiWindowFilled && params.date_window_days !== defaultParams.date_window_days}
        onManualEdit={markManualEdit}
        onSubmit={() => submit.mutate({ data: { ...params, solver_rules: [...new Set([...params.solver_rules, ...SYSTEM_RULE_KEYS])], wait: false } })}
      /></div> : null}
      <div className={showParams ? undefined : "xl:col-span-2"}>
        <RunPanel run={activeRun} onUseInstruction={applySuggestedInstruction} />
      </div>
    </div>
    <ScheduleChangePanel
      base={baseSchedule}
      target={draftSchedule}
      diff={scheduleDiff.data}
      loading={scheduleDiff.isPending}
      onOpenVersions={() => navigate("/versions")}
    />
    <section className="border border-zinc-200 bg-white p-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><div className="flex items-center gap-2"><CalendarPlus className="size-4 text-blue-600" /><h2 className="font-semibold">教师日历下发</h2></div><p className="mt-1 text-xs text-zinc-500">{schedule ? `当前课表：${schedule.name}` : "当前没有可下发课表"}。教师忙闲冲突会告警，但正式下发仍继续创建日程。</p></div><div className="flex gap-2"><Button variant="outline" onClick={() => publishCalendar(true)} disabled={!schedule || publishing !== null}>{publishing === "dry-run" ? "预检中" : "忙闲预检"}</Button><Button onClick={() => publishCalendar(false)} disabled={!schedule || publishing !== null}>{publishing === "publish" ? "正在下发" : "确认下发"}</Button></div></div>{calendarResult ? <><div className="mt-4 grid gap-2 sm:grid-cols-3 xl:grid-cols-6"><Value label="预计下发" value={String(calendarResult.would_publish)} /><Value label="本次发布" value={String(calendarResult.published)} /><Value label="已存在" value={String(calendarResult.existing)} /><Value label="待补账号" value={String(calendarResult.skipped_unmapped)} /><Value label="冲突告警" value={String(calendarResult.conflict_count)} /><Value label="执行模式" value={calendarResult.dry_run ? "仅预检" : "正式下发"} /></div>{calendarResult.skipped_unmapped ? <div className="mt-3 border-l-2 border-amber-500 bg-amber-50 px-4 py-2 text-xs text-amber-900">未映射具体日历账号的课程已跳过，请先补充课程账号或教师账号。</div> : null}</> : null}{(Array.isArray(calendarResult?.conflicts) ? calendarResult.conflicts : []).length ? <div className="mt-4 max-h-48 overflow-auto border-l-2 border-red-500 bg-red-50 px-4 py-2 text-xs text-red-900"><div className="mb-1 font-medium">冲突明细（正式下发仍会创建并标记冲突）</div>{(Array.isArray(calendarResult?.conflicts) ? calendarResult.conflicts : []).slice(0, 20).map((item, index) => <div key={`${item.course_session_id}-${item.lesson_date}-${item.source}-${index}`}>{item.lesson_date} {item.start_time}-{item.end_time} / {item.calendar_user_id} / 课程 {item.course_session_id.slice(0, 8)} / {item.source === "feishu_freebusy" ? "日历已有忙碌" : "待下发课表内部重叠"}</div>)}</div> : null}</section>
    <section className="border border-zinc-200 bg-white"><div className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold">求解记录</div><div className="overflow-x-auto"><table className="w-full min-w-[720px] text-left text-sm"><thead className="bg-zinc-50 text-xs text-zinc-500"><tr><th className="h-9 px-4">任务</th><th>状态</th><th>模型结果</th><th>目标值</th><th>最佳界</th><th>耗时</th></tr></thead><tbody>{(Array.isArray(runs.data) ? runs.data : []).map((run) => <tr key={run.id} className="border-t border-zinc-100"><td className="h-10 px-4 font-mono text-xs">{run.id.slice(0, 8)}</td><td><Badge tone={run.status === "completed" ? modelStatusTone(run.model_status) : statusTone(run.status)}>{statusLabel(run.status)}</Badge></td><td>{modelStatusLabel(run.model_status, run.presolve_infeasible)}</td><td>{run.objective_value?.toFixed(1) ?? "-"}</td><td>{run.best_bound?.toFixed(1) ?? "-"}</td><td>{run.wall_time_seconds?.toFixed(2) ?? "-"} 秒</td></tr>)}</tbody></table></div></section>
  </div>;
}

function InfoTooltip({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  return (
    <span className="group relative inline-flex align-middle">
      <button
        type="button"
        aria-label={`${label}说明`}
        aria-describedby={id}
        className="inline-flex size-4 items-center justify-center rounded-full text-zinc-400 outline-none transition-colors hover:text-zinc-700 focus-visible:text-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
      >
        <CircleAlert className="size-3.5" aria-hidden="true" />
      </button>
      <span
        id={id}
        role="tooltip"
        className="pointer-events-none absolute left-0 top-[calc(100%+6px)] z-30 w-64 rounded-md bg-zinc-900 px-3 py-2 text-left text-xs leading-5 text-white opacity-0 shadow-lg transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
      >
        {children}
      </span>
    </span>
  );
}

function NumberField({ label, hint, labelExtra, value, min, max, step, onChange }: { label: string; hint: string; labelExtra?: ReactNode; value: number; min: number; max: number; step: number; onChange: (value: number) => void }) {
  const id = `solver-${label}`;
  return (
    <div className="block text-sm text-zinc-700">
      <span className="flex items-center justify-between">
        <span className="inline-flex items-center gap-1.5">
          <label htmlFor={id} className="cursor-pointer font-medium text-zinc-800">{label}</label>
          {labelExtra}
        </span>
        <InfoTooltip label={label}>{hint}</InfoTooltip>
      </span>
      <input
        id={id}
        className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2.5 text-sm tabular-nums outline-none focus:border-blue-500"
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  );
}

/** 解析进行中的阶段进度区：左侧竖线 + 浅色斜体，当前阶段用现有 animate-pulse 呼吸。 */
function InterpretProgress({ stageIndex, elapsedSeconds, liveText }: { stageIndex: number; elapsedSeconds: number; liveText: string }) {
  // 思考增量持续到达时跟随滚动到底部，最新的推理始终可见（jsdom 无 scrollTo，须容错）。
  const liveRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => { liveRef.current?.scrollTo?.({ top: liveRef.current.scrollHeight }); }, [liveText]);
  return (
    <div className="mt-4 border-l-2 border-zinc-200 pl-3">
      <ol className="space-y-1 text-xs italic">
        {INTERPRET_STAGES.map((stage, index) => {
          const done = index < stageIndex;
          const current = index === stageIndex;
          return (
            <li key={stage.key} className={"flex items-center gap-1.5 " + (current ? "animate-pulse text-zinc-600" : done ? "text-zinc-400" : "text-zinc-300")}>
              <span aria-hidden>{done ? "✓" : current ? "…" : "○"}</span>
              {stage.text}
            </li>
          );
        })}
      </ol>
      {liveText ? (
        <div ref={liveRef} className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap border-l-2 border-zinc-200 pl-3 text-xs italic leading-5 text-zinc-500">
          {liveText}
        </div>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs not-italic text-zinc-500">
        <span className="tabular-nums">已用时 {elapsedSeconds.toFixed(1)} 秒</span>
        {elapsedSeconds > 30 ? <span className="text-amber-700">解析耗时较长，可取消后重试</span> : null}
      </div>
    </div>
  );
}

/** 思考过程回看：完成后默认折叠为「已解析完成（用时 N 秒）」，正文限高防长思考撑爆页面。 */
function InterpretThought({ thinking, seconds }: { thinking: string; seconds: number }) {
  const [expanded, setExpanded] = useState(false);
  if (!thinking) {
    // Aily 或无思考模型：只给阶段完成与用时，不放空折叠块。
    return <p className="mt-3 border-l-2 border-zinc-200 pl-3 text-xs text-zinc-500">已解析完成 · 用时 {seconds.toFixed(1)} 秒 · 本次模型未输出思考过程</p>;
  }
  return (
    <div className="mt-3">
      <button type="button" className="text-xs text-zinc-500 transition-colors hover:text-zinc-700" onClick={() => setExpanded((value) => !value)}>
        {expanded ? "收起思考过程" : `已解析完成（用时 ${seconds.toFixed(1)} 秒）· 展开回看思考过程`}
      </button>
      {expanded ? (
        <div className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap border-l-2 border-zinc-200 pl-3 text-xs italic leading-5 text-zinc-500">
          {thinking}
        </div>
      ) : null}
    </div>
  );
}

function SolverParams({ params, setParams, scope, selectedCount, pending, aiWindowBadge, onManualEdit, onSubmit }: { params: SolverParamValues; setParams: React.Dispatch<React.SetStateAction<SolverParamValues>>; scope: { businessLines: string[]; classes: string[] }; selectedCount: number; pending: boolean; aiWindowBadge?: boolean; onManualEdit?: (field: "date_from" | "date_to" | "date_window_days") => void; onSubmit: () => void }) {
  const toggleRule = (key: SolverRule) =>
    setParams((current) => ({
      ...current,
      solver_rules: current.solver_rules.includes(key)
        ? current.solver_rules.filter((item) => item !== key)
        : [...current.solver_rules, key],
    }));
  return (
    <section className="border border-zinc-200 bg-white p-5">
      <div className="flex items-center gap-2">
        <SlidersHorizontal className="size-4 text-blue-600" />
        <h2 className="font-semibold">手动求解参数</h2>
      </div>
      <fieldset className="mt-5 grid gap-3 border-b border-zinc-100 pb-4">
        <legend className="sr-only">求解范围</legend>
        <div className="text-xs font-medium text-zinc-500">求解范围</div>
        <label className="block text-sm text-zinc-700">
          业务线
          <Select selectSize="md" containerClassName="mt-1.5" value={params.business_lines[0] ?? ""} onChange={(event) =>
              setParams((current) => ({
                ...current,
                business_lines: event.target.value ? [event.target.value] : [],
              }))
            }
          >
            <option value="">全部业务线</option>
            {(Array.isArray(scope.businessLines) ? scope.businessLines : []).map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </Select>
        </label>
        <label className="block text-sm text-zinc-700">
          班级
          <Select selectSize="md" containerClassName="mt-1.5" value={params.class_business_ids[0] ?? ""} onChange={(event) =>
              setParams((current) => ({
                ...current,
                class_business_ids: event.target.value ? [event.target.value] : [],
              }))
            }
          >
            <option value="">全部班级</option>
            {(Array.isArray(scope.classes) ? scope.classes : []).map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </Select>
        </label>
        <div className="grid grid-cols-2 gap-3">
          {(["date_from", "date_to"] as const).map((key) => {
            const label = key === "date_from" ? "起始日期" : "结束日期";
            const id = `solver-${key}`;
            return (
              <div key={key} className="block text-sm text-zinc-700">
                <span className="inline-flex items-center gap-1.5">
                  <label htmlFor={id}>{label}</label>
                  <InfoTooltip label={label}>
                    {key === "date_from"
                      ? "只选择原课表日期不早于这一天的课次，并限制新日期不早于这一天；还会与日期调整窗口共同生效。留空不设下界。"
                      : "只选择原课表日期不晚于这一天的课次，并限制新日期不晚于这一天；还会与日期调整窗口共同生效。留空不设上界。"}
                  </InfoTooltip>
                </span>
                <input
                  id={id}
                  className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2 text-sm"
                  type="date"
                  value={params[key] ?? ""}
                  onChange={(event) => {
                    onManualEdit?.(key);
                    setParams((current) => ({ ...current, [key]: event.target.value || null }));
                  }}
                />
              </div>
            );
          })}
        </div>
        <p className={"text-xs leading-5 " + (selectedCount > 1500 ? "text-amber-700" : "text-zinc-400")}>
          当前范围命中 <span className="font-mono tabular-nums">{selectedCount}</span> 个课次。
          {selectedCount > 1500 ? "课次过多时求解会超时，建议按班级或按周分批。" : ""}
        </p>
      </fieldset>
      <div className="mt-4 grid gap-4">
        <NumberField label="求解时限（秒）" hint="CP-SAT 最多运行多久。超时可能返回已有可行解，或 UNKNOWN（尚未找到解，不代表无解）；课次范围越大，通常需要越长时间。" value={params.time_limit_seconds} min={1} max={900} step={5} onChange={(value) => setParams((current) => ({ ...current, time_limit_seconds: value }))} />
        <NumberField label="日期调整窗口（天）" hint="每节课相对原日期最多可前后挪动几天。实际新日期还必须落在起始日期与结束日期设定的边界内；设为 0 表示不调日期。" labelExtra={aiWindowBadge ? <Badge tone="blue">来自 AI 解析</Badge> : undefined} value={params.date_window_days} min={0} max={31} step={1} onChange={(value) => { onManualEdit?.("date_window_days"); setParams((current) => ({ ...current, date_window_days: value })); }} />
        <NumberField label="变更权重" hint="每挪动一天的代价。数值越大，求解器越倾向保持原课表。" value={params.change_weight} min={0} max={1000000} step={1000} onChange={(value) => setParams((current) => ({ ...current, change_weight: value }))} />
      </div>
      <fieldset className="mt-5 border-t border-zinc-100 pt-4">
        <legend className="sr-only">硬约束与可选策略</legend>
        <div className="text-xs font-medium text-zinc-500">系统硬约束</div>
        <div className="mt-2 grid gap-2">
          {SYSTEM_SOLVER_RULES.map((rule) => (
            <div key={rule.key} className="flex items-start gap-2 rounded border border-emerald-100 bg-emerald-50/60 px-3 py-2 text-sm text-zinc-700">
              <LockKeyhole className="mt-0.5 size-3.5 shrink-0 text-emerald-700" />
              <span>
                <span className="inline-flex items-center gap-1.5">{rule.label}<InfoTooltip label={rule.label}>{rule.hint}</InfoTooltip><span className="text-xs text-emerald-700">始终生效</span></span>
              </span>
            </div>
          ))}
        </div>
        <div className="mt-4 text-xs font-medium text-zinc-500">可选策略</div>
        <div className="mt-2 grid gap-2">
          {OPTIONAL_SOLVER_RULES.map((rule) => (
            <div key={rule.key} className="flex items-start gap-2 text-sm text-zinc-700">
              <input id={`solver-rule-${rule.key}`} className="mt-1 accent-blue-600" type="checkbox" checked={params.solver_rules.includes(rule.key)} onChange={() => toggleRule(rule.key)} />
              <span className="inline-flex items-center gap-1.5">
                <label className="cursor-pointer" htmlFor={`solver-rule-${rule.key}`}>{rule.label}</label>
                <InfoTooltip label={rule.label}>{rule.hint}</InfoTooltip>
              </span>
            </div>
          ))}
        </div>
      </fieldset>
      <Button className="mt-6 w-full" onClick={onSubmit} disabled={pending}>
        <Play className="size-4" />
        按参数开始求解
      </Button>
    </section>
  );
}

function Scope({ label, values }: { label: string; values: string[] }) { return <div><div className="text-xs text-zinc-400">{label}</div><div className="mt-1 flex flex-wrap gap-1">{values.length ? values.map((value) => <Badge key={value} tone="blue">{value}</Badge>) : <span className="text-xs text-zinc-500">全部</span>}</div></div>; }

function ScheduleChangePanel({
  base,
  target,
  diff,
  loading,
  onOpenVersions,
}: {
  base: { version_no: number; name: string } | undefined;
  target: { version_no: number; name: string } | undefined;
  diff: ScheduleDiffResponse | undefined;
  loading: boolean;
  onOpenVersions: () => void;
}) {
  if (!target) return null;
  if (!base) {
    return <section className="border border-amber-200 bg-amber-50/50 p-5 text-sm text-amber-900">本次已生成 v{target.version_no} 草稿，但还没有可比较的基准版本。</section>;
  }
  if (loading) return <section className="border border-zinc-200 bg-white p-5 text-sm text-zinc-500">正在整理本次排课调整……</section>;
  if (!diff) return null;
  const changed = diff.items.filter((item) => item.change_kind !== "unchanged");
  const dateChanges = changed.filter((item) => item.before_lesson_date !== item.after_lesson_date).length;
  const slotChanges = changed.filter((item) => item.before_slot_id !== item.after_slot_id).length;
  const roomChanges = changed.filter((item) => item.before_room_id !== item.after_room_id).length;
  return (
    <section className="border border-blue-200 bg-blue-50/40 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">本次排课调整</h2>
          <p className="mt-1 text-xs text-zinc-600">v{base.version_no}「{base.name}」→ v{target.version_no}「{target.name}」。下面列出实际发生变化的课次。</p>
          <p className="mt-1 text-xs text-zinc-500">调整依据：教室与教师不重叠始终生效；启用最小变更时，以父课表的日期、时段和教室为基准，优先少改课次，再比较次级偏好。</p>
        </div>
        <Button size="sm" variant="outline" onClick={onOpenVersions}>查看完整版本对比</Button>
      </div>
      <div className="mt-4 grid gap-2 sm:grid-cols-4">
        <Value label="变更课次" value={String(diff.changed_count)} />
        <Value label="日期变化" value={String(dateChanges)} />
        <Value label="时段变化" value={String(slotChanges)} />
        <Value label="教室变化" value={String(roomChanges)} />
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
                    <div className="font-medium text-zinc-900 truncate max-w-[180px]" title={item.course_business_id}>
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
    </section>
  );
}

function RunPanel({ run, onUseInstruction }: { run: SolverRunResponse | null; onUseInstruction?: (value: string) => void }) {
  const presolved = Boolean(run?.presolve_infeasible);
  const finished = run?.status === "completed";
  return (
    <section className="border border-zinc-200 bg-white p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="size-4 text-blue-600" />
          <h2 className="font-semibold">实时状态</h2>
        </div>
        <Badge tone={finished ? modelStatusTone(run?.model_status) : statusTone(run?.status)}>
          {finished ? modelStatusLabel(run?.model_status, presolved) : (statusLabel(run?.status) ?? "待启动")}
        </Badge>
      </div>
      <div className="mt-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Value label="模型状态" value={modelStatusLabel(run?.model_status, presolved)} />
        <Value label="目标函数值" value={run?.objective_value?.toFixed(1) ?? "-"} />
        <Value label="最佳界" value={run?.best_bound?.toFixed(1) ?? "-"} />
        <Value label="耗时" value={run?.wall_time_seconds ? `${run.wall_time_seconds.toFixed(2)} 秒` : "-"} />
      </div>
      {presolved ? (
        <div className="mt-6 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          本次结论来自求解前的数据预检，CP-SAT 未运行，因此不能表述为「已证明无解」。请先按下方诊断修正输入数据。
        </div>
      ) : null}
      {run?.model_status === "UNKNOWN" ? <p role="status" className="mt-3 text-sm text-amber-800">本次尚未找到可用解，尚未证明无解；请增加时限或缩小范围。本次没有候选课表。</p> : null}
      {run?.model_status === "INFEASIBLE" && !presolved ? (
        <div className="mt-6 border-l-2 border-red-600 bg-red-50 px-4 py-3 text-sm text-red-800">
          冲突规则：{run.conflict_rule_ids.join("、") || "模型未返回可追溯规则"}
        </div>
      ) : null}
      {(Array.isArray(run?.priority_explanations) ? run.priority_explanations : []).length ? (
        <div className="mt-4 space-y-2 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="font-medium">冲突诊断</div>
          {(Array.isArray(run?.priority_explanations) ? run.priority_explanations : []).map((explanation, index) => (
            <div key={`${index}-${explanation}`}>{explanation}</div>
          ))}
        </div>
      ) : null}
      {run?.error_message ? <div className="mt-4 text-sm text-red-700">{run.error_message}</div> : null}
      <ExplanationPanel run={run} onUseInstruction={onUseInstruction} />
    </section>
  );
}

const INTENT_VERDICTS: Record<string, { label: string; tone: "green" | "yellow" | "neutral" }> = {
  matched: { label: "与原始意图一致", tone: "green" },
  deviated: { label: "可能偏离原始意图", tone: "yellow" },
  unclear: { label: "无法判断", tone: "neutral" },
};

async function copyInstruction(value: string) {
  try {
    // 非 HTTPS 或旧浏览器下 navigator.clipboard 可能不存在，这里必须退化成提示而不是抛错。
    if (!navigator.clipboard?.writeText) throw new Error("当前浏览器不允许自动复制");
    await navigator.clipboard.writeText(value);
    toast.success("建议指令已复制，可粘贴到「一句话排课」输入框");
  } catch (error) {
    toast.error(`复制失败，请手动选中复制：${errorMessage(error)}`);
  }
}

function ExplanationPanel({ run, onUseInstruction }: { run: SolverRunResponse | null; onUseInstruction?: (value: string) => void }) {
  const client = useQueryClient();
  const [explanation, setExplanation] = useState<SolverRunExplanationDetail | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [failure, setFailure] = useState("");
  // 换一次求解任务就清掉上一条解释，否则会把旧结论挂在新任务下面。
  useEffect(() => { setExplanation(run?.explanation ?? null); setFailure(""); }, [run?.id, run?.explanation]);
  // 这里刻意不用 orval 生成的 useMutation：自动解析是在 effect 里发起的，
  // StrictMode 下 mutation observer 会在双次 effect 之间被摘掉，请求成功也回不到组件，
  // 加载态就永远停在「解析中」。改成和本页其他动作一样直接调 http，finally 一定收尾。
  const analyze = useCallback(
    async (runId: string, options: { refresh: boolean; auto: boolean }) => {
      setAnalyzing(true);
      setFailure("");
      try {
        const { data } = await http.post<SolverRunExplanationDetail>(
          `/api/v1/solver-runs/${runId}/explanation`,
          undefined,
          { params: { refresh: options.refresh } },
        );
        setExplanation(data);
        void client.invalidateQueries({ queryKey: getListSolverRunsApiV1SolverRunsGetQueryKey() });
      } catch (error) {
        const message = errorMessage(error);
        setFailure(message);
        // 自动解析失败不弹 toast：用户没点过任何东西，弹窗只会让人以为求解本身出了问题。
        if (!options.auto) toast.error(message);
      } finally {
        setAnalyzing(false);
      }
    },
    [client],
  );
  const runId = run?.id ?? "";
  const finished = run?.status === "completed";
  const explained = Boolean(explanation);
  // 求解一到终态就自动解析，用户不用再点一次；同一个任务只自动发一次，
  // 失败后由「重新解析」接管，避免解析接口不可用时无限重试。
  const autoRequested = useRef("");
  useEffect(() => {
    if (!finished || !runId || explained || autoRequested.current === runId) return;
    autoRequested.current = runId;
    void analyze(runId, { refresh: false, auto: true });
  }, [analyze, explained, finished, runId]);
  if (!run || !finished) return null;
  const intent = explanation?.intent_review;
  const verdict = intent ? INTENT_VERDICTS[intent.verdict] ?? INTENT_VERDICTS.unclear : null;
  const suggested = explanation?.suggested_instruction ?? "";
  const explanationItems = Array.isArray(explanation?.explanation) ? explanation.explanation : [];
  const nextActions = Array.isArray(explanation?.next_actions) ? explanation.next_actions : [];
  const concerns = Array.isArray(intent?.concerns) ? intent.concerns : [];

  return (
    <section className="mt-6 border-t border-zinc-100 pt-5">
      <div className="flex flex-wrap items-center gap-2">
        <MessageSquareText className="size-4 text-blue-600" />
        <h3 className="text-sm font-semibold">结果解释</h3>
        {explanation ? <Badge tone={explanation.source === "ai" ? "blue" : "neutral"}>{explanation.source === "ai" ? "AI 措辞" : "系统兜底措辞"}</Badge> : null}
        <Button
          className="ml-auto"
          size="sm"
          variant="outline"
          disabled={analyzing}
          onClick={() => void analyze(run.id, { refresh: Boolean(explanation), auto: false })}
        >
          {analyzing ? <Loader2 className="size-3.5 animate-spin" /> : explanation ? <RefreshCw className="size-3.5" /> : <Sparkles className="size-3.5" />}
          {analyzing ? "AI 解析中" : "重新解析"}
        </Button>
      </div>
      {analyzing && !explanation ? (
        <div className="mt-3 flex items-center gap-2 border-l-2 border-blue-400 bg-blue-50/60 px-4 py-3 text-sm text-blue-900">
          <Loader2 className="size-4 animate-spin" />
          求解已结束，AI 正在自动解读这次结果……
        </div>
      ) : null}
      {!explanation && !analyzing && failure ? (
        <div className="mt-3 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          自动解析没能完成：{failure}。求解结果本身不受影响，可点右上角「重新解析」重试。
        </div>
      ) : null}
      {explanation ? (
        <div className="mt-3 space-y-3 text-sm">
          <p className="font-medium text-zinc-900">{explanation.headline}</p>
          {explanationItems.length ? (
            <ul className="list-disc space-y-1 pl-5 text-zinc-700">
              {explanationItems.map((item, index) => <li key={`${index}-${item.slice(0, 12)}`}>{item}</li>)}
            </ul>
          ) : null}
          {nextActions.length ? (
            <div className="border-l-2 border-blue-400 bg-blue-50/60 px-4 py-3 text-zinc-800">
              <div className="text-xs font-medium text-blue-800">下一步可以做什么</div>
              <ul className="mt-1 list-disc space-y-1 pl-5">
                {nextActions.map((item, index) => <li key={`${index}-${item.slice(0, 12)}`}>{item}</li>)}
              </ul>
            </div>
          ) : null}
          {suggested ? (
            <div className="border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-amber-900">
              <div className="flex flex-wrap items-center gap-2">
                <div className="text-xs font-medium">建议改成这条排课指令</div>
                <div className="ml-auto flex gap-2">
                  <Button size="sm" variant="outline" onClick={() => void copyInstruction(suggested)}>
                    <ClipboardCopy className="size-3.5" />
                    复制
                  </Button>
                  {onUseInstruction ? (
                    <Button size="sm" variant="outline" onClick={() => onUseInstruction(suggested)}>
                      <CornerUpLeft className="size-3.5" />
                      填入指令框
                    </Button>
                  ) : null}
                </div>
              </div>
              <p className="mt-2 leading-6">{suggested}</p>
              <p className="mt-2 text-xs text-amber-800">
                这条指令按本次范围内真实存在的班级、班型和日期拼出，粘回上方「一句话排课」即可重跑；班级和日期都可以再改。
              </p>
            </div>
          ) : null}
          {intent && verdict ? (
            <div className="border-l-2 border-zinc-300 bg-zinc-50 px-4 py-3">
              <div className="flex items-center gap-2 text-xs text-zinc-500">
                意图核对
                <Badge tone={verdict.tone}>{verdict.label}</Badge>
              </div>
              {concerns.length ? (
                <ul className="mt-1.5 list-disc space-y-1 pl-5 text-zinc-700">
                  {concerns.map((item, index) => <li key={`${index}-${item.slice(0, 12)}`}>{item}</li>)}
                </ul>
              ) : null}
              <p className="mt-2 text-xs text-zinc-500">
                意图核对由模型给出，仅供参考。硬冲突是否存在由 CP-SAT 与独立重算的指标判定，不受这里影响。
              </p>
            </div>
          ) : null}
          {explanation.ai_error ? (
            <p className="border-l-2 border-amber-500 bg-amber-50 px-4 py-2 text-xs text-amber-900">
              AI 措辞不可用，已回退到系统兜底解释：{explanation.ai_error}
            </p>
          ) : null}
        </div>
      ) : analyzing || failure ? null : (
        <p className="mt-2 text-xs text-zinc-500">求解结束后会自动把模型状态、冲突规则和课表指标翻译成教务能读的结论；AI 不可用时会回退到系统兜底措辞。</p>
      )}
    </section>
  );
}
function Value({ label, value }: { label: string; value: string }) { return <div><div className="text-xs text-zinc-400">{label}</div><div className="mt-1 font-mono text-sm text-zinc-800">{value}</div></div>; }
