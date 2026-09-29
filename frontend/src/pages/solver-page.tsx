import { useQueryClient } from "@tanstack/react-query";
import { Activity, Bot, CalendarPlus, CircleAlert, ClipboardCopy, CornerUpLeft, Loader2, LockKeyhole, MessageSquareText, Play, RefreshCw, Settings2, SlidersHorizontal, Sparkles, Target } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { getListSchedulesApiV1SchedulesGetQueryKey, getListSolverRunsApiV1SolverRunsGetQueryKey, getOverviewApiV1OverviewGetQueryKey, useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet, useGetSolverRunApiV1SolverRunsRunIdGet, useGetScheduleApiV1SchedulesScheduleIdGet, useListCourseSessionsApiV1CourseSessionsGet, useListRulesApiV1RulesGet, useListSchedulesApiV1SchedulesGet, useListSolverRunsApiV1SolverRunsGet, useSubmitSolverRunApiV1SolverRunsPost } from "@/api/generated/client";
import { type CourseSessionResponse, type GoalChecklistItem, type GoalResponse, type ScheduleDiffResponse, type ScheduleSummaryResponse, type SolveRequest, type SolverRunExplanation, type SolverRunResponse } from "@/api/generated/models";
import { http } from "@/api/http";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { SetupChecklist } from "@/components/setup-checklist";
import { SopSteps } from "@/components/sop-steps";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { asArray, errorMessage, formatRoom, formatSlot } from "@/lib/format";
import { goalKindLabel, isBottomLineItem, parseGoalReport } from "@/lib/goal";
import { type Interpretation, streamInterpretInstruction } from "@/lib/interpret-stream";
import { diffKindLabel, modelStatusLabel, statusLabel } from "@/lib/labels";
import { type MemoryOutcome, type MemoryUsageSnapshot, memoryHeadline, memoryOutcomeLabel } from "@/lib/memory-usage";
import { preferredSchedule, scheduleForRun } from "@/lib/schedule";
import { modelStatusTone, statusTone } from "@/lib/status";
import {
  type AssistantMemoryActionReceipt,
  type AssistantTaskConstraint,
  memoryReceiptStatusLabel,
  parseGoalContext,
  raisedBudgetSeconds,
  TASK_CONSTRAINT_SOURCE_NOTE,
  taskConstraintHardnessLabel,
} from "@/lib/task-context";

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
  product_types: string[];
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
  product_types: [],
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
  // D6/问题2：解析结果回填手动参数草稿（日期三元组 + 业务范围），用户手动改过
  // 的字段不再被下一次解析覆盖。
  const manuallyEdited = useRef({ date_from: false, date_to: false, date_window_days: false, business_lines: false, class_business_ids: false, product_types: false });
  // 问题2（单一草稿）：解析写入草稿时的范围基线。判断「手动清空范围」是否属于
  // 扩大解析确认过的范围时以它为基准，而不是相邻两次编辑的差值——先改成别的
  // 限定值再清空，仍然算扩大。
  const parsedScopeBaseline = useRef({ business_lines: [] as string[], class_business_ids: [] as string[] });
  // 问题2（扩大范围单独确认）：用户把范围字段从限定改为「全部」时暂存原值，
  // 确认前两个求解入口都被禁用；「恢复原范围」按这里的记录回滚。
  const [scopeExpansion, setScopeExpansion] = useState<{ business_lines?: string[]; class_business_ids?: string[] } | null>(null);
  const [publishing, setPublishing] = useState<"dry-run" | "publish" | null>(null);
  const [calendarResult, setCalendarResult] = useState<CalendarResult | null>(null);
  const [assistantReady, setAssistantReady] = useState<boolean | null>(null);
  const [assistantEngine, setAssistantEngine] = useState("AI 模型");
  // 目标验收闭环（MEM-C3）：确认卡默认开启目标跟踪；一次解析只建一个目标，
  // 同一目标的重跑直接复用 goal_id，验收报告按 run 逐次落档。
  const [trackGoal, setTrackGoal] = useState(true);
  const [baselineId, setBaselineId] = useState("");
  const [goalId, setGoalId] = useState("");
  const [goalBusy, setGoalBusy] = useState(false);
  // MEM-D3（基准联动）+ 问题4：选基准默认只记录基准版本（用于变更明细/数量
  // 对比与优化配置），不再静默附带 max_changes=50；只有用户明确开启「设置变更
  // 上限」才把 max_changes 项写进目标清单，上限默认 50、可改。
  const [maxChangesLimit, setMaxChangesLimit] = useState(50);
  const [changeLimitEnabled, setChangeLimitEnabled] = useState(false);
  // MEM-D3（目标连续性）：goalId 绑定状态/口径提示条文案（确认卡内展示）。
  const [goalNotice, setGoalNotice] = useState("");
  // MEM-D3（继续处理入口）：goals 页跳转 /solver?goal=<id> 时挂载即绑定；
  // setSearchParams 供 §4.5 的 URL 同步（新建目标写入 goal、清除关联删除）。
  const [searchParams, setSearchParams] = useSearchParams();
  const goalParamBound = useRef(false);
  // MEM-D2/D6：验收报告在 run completed 之后于独立事务里异步落库。这里记录
  // 「带 goal 的 run 已完成但报告未就绪」的轮询截止时刻，防止验收层挂掉时
  // 前端永远轮询（上限约 45 秒）。
  const acceptanceWatch = useRef<{ runId: string; deadline: number } | null>(null);
  const rules = useListRulesApiV1RulesGet({ status: "active" });
  const courses = useListCourseSessionsApiV1CourseSessionsGet();
  const runs = useListSolverRunsApiV1SolverRunsGet({ query: { refetchInterval: 5000 } });
  const schedules = useListSchedulesApiV1SchedulesGet();
  const [assistantProbeError, setAssistantProbeError] = useState("");
  const probeAssistant = useCallback(() => {
    setAssistantProbeError("");
    // 通用 AI 与飞书 Aily 是两条相互独立的通道：分别请求、分别记账，任一条读取
    // 失败都不拖垮另一条（此前 Promise.all 会让飞书读取失败把已配置好的通用 AI
    // 入口一起打成不可用）。入口可用性 = 通用 AI 已配置，或已确认 Aily 可用；
    // 只有「AI 配置读取失败且没有 Aily 兜底」才显示探测错误并提供重试。
    let ai: { configured: boolean; model: string | null } | "failed" | null = null;
    let aiReadError = "";
    let aily: boolean | null = null;
    const refresh = () => {
      const aiConfigured = ai !== null && ai !== "failed" && ai.configured;
      const aiModel = ai !== null && ai !== "failed" ? ai.model : null;
      const ailyUsable = aily === true;
      if (ai === null && !ailyUsable) return; // 两条通道都还没有可用结论，继续等待
      if (ai !== null && ai !== "failed" && !aiConfigured && aily === null) return; // 通用 AI 未配置，等飞书结论后再定，避免「待配置」闪跳
      if (ai === "failed" && aily === null) return; // AI 配置读取失败，等飞书兜底结论
      if (ai === "failed" && !ailyUsable) {
        setAssistantReady(null);
        setAssistantProbeError(aiReadError || "AI 配置读取失败");
        return;
      }
      if (ai === null) {
        // 通用 AI 配置仍在读取，但 Aily 已确认可用：先放行入口，不等可选通道。
        setAssistantProbeError("");
        setAssistantReady(true);
        setAssistantEngine("Aily（可选通道）");
        return;
      }
      setAssistantProbeError("");
      setAssistantReady(aiConfigured || ailyUsable);
      setAssistantEngine(aiConfigured ? (aiModel || "通用 AI 模型") : "Aily（可选通道）");
    };
    http.get<{ configured: boolean; model: string | null }>("/api/v1/integrations/ai/configuration")
      .then(({ data }) => { ai = data; })
      .catch((error) => { ai = "failed"; aiReadError = errorMessage(error); })
      .finally(refresh);
    // 可选集成的读取失败只失去 Aily 增益，绝不阻断独立路径。
    http.get<{ app_configuration: { aily_configured: boolean } }>("/api/v1/integrations/feishu/connection")
      .then(({ data }) => { aily = Boolean(data.app_configuration?.aily_configured); })
      .catch(() => { aily = false; })
      .finally(refresh);
  }, []);
  useEffect(probeAssistant, [probeAssistant]);
  // MEM-D3（继续处理入口）：goals 页「修正范围后重新求解」跳转 /solver?goal=<id>，
  // 本页挂载即读取并绑定该目标——之后确认求解与手动求解都会带上同一 goal_id。
  // 07 §4.4 续办恢复：不只绑 id——goal.instruction 与 context.scope（范围/日期
  // 草稿）一并回填，基准默认该目标的工作草稿（§4.6）；context 为空的旧目标只
  // 回填 instruction，范围留空照旧；恢复失败不阻塞绑定（降级为现状只绑 id）。
  useEffect(() => {
    const goalParam = searchParams.get("goal");
    if (!goalParam || goalParamBound.current) return;
    goalParamBound.current = true;
    setTrackGoal(true);
    http.get<GoalResponse>(`/api/v1/goals/${goalParam}`)
      .then(({ data: goal }) => {
        setGoalId(goal.id);
        setInstruction(goal.instruction);
        const context = parseGoalContext((goal as GoalResponse & { context?: unknown }).context);
        const scope = context?.scope;
        if (scope) {
          const restored = {
            business_lines: scope.business_lines ?? [],
            product_types: scope.product_types ?? [],
            class_business_ids: scope.class_business_ids ?? [],
            date_from: scope.date_from ?? null,
            date_to: scope.date_to ?? null,
            date_window_days: scope.date_window_days ?? defaultParams.date_window_days,
          };
          setParams((current) => ({ ...current, ...restored }));
          // 恢复的范围成为「扩大范围」判定的草稿基线（MEM-I2 共享草稿之上续办）。
          parsedScopeBaseline.current = { business_lines: restored.business_lines, class_business_ids: restored.class_business_ids };
        }
        if (context?.work_draft_schedule_id) setBaselineId(context.work_draft_schedule_id);
        const instruction = goal.instruction.length > 40 ? `${goal.instruction.slice(0, 40)}…` : goal.instruction;
        setGoalNotice(`已关联目标 #${goal.id.slice(0, 8)}「${instruction}」：${scope ? "已恢复该目标的范围与指令上下文，" : ""}修正范围后重新求解，验收仍回灌同一目标。`);
        toast.success("已关联待继续的目标，求解结束后自动验收");
      })
      .catch((error) => {
        // 绑定失败复位守卫，让用户修正后（或目标存在时）可重试。
        goalParamBound.current = false;
        toast.error(`关联目标失败：${errorMessage(error)}`);
      });
  }, [searchParams]);
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
  // MEM-D2/D6：轮询停止条件从「run completed/failed」扩展为「验收报告就绪或验收
  // 失败」——run completed 只说明求解落库，报告还会晚一步；带 goal_id 的 run 要
  // 继续轮询到 goal_report 出现（成功报告或失败标记都会写这里），并有截止保护。
  const progress = useGetSolverRunApiV1SolverRunsRunIdGet(runId, {
    query: {
      enabled: Boolean(runId),
      refetchInterval: (query) => {
        const data = query.state.data;
        if (!data) return 700;
        if (data.status === "failed") return false;
        if (data.status !== "completed") return 700;
        if (!data.goal_id || data.goal_report) return false;
        const watch = acceptanceWatch.current;
        const deadline = watch && watch.runId === data.id ? watch.deadline : Date.now() + 45000;
        acceptanceWatch.current = { runId: data.id, deadline };
        return Date.now() > deadline ? false : 1000;
      },
    },
  });
  const submit = useSubmitSolverRunApiV1SolverRunsPost({ mutation: { onSuccess: (result) => { setRunId(result.id); setCurrent(result); toast.success("求解任务已创建"); }, onError: (error) => toast.error(errorMessage(error)) } });
  /**
   * 07 §5.2 加预算重跑的独立提交函数：刻意不经过 solveFromInterpretation——该函数
   * 开头的 interpretation/unsupported 守卫在「goals-page 报告区、刷新后的求解页」
   * 等无解析状态场景必然提前返回。这里直接复用手动求解提交路径（SolverParams
   * onSubmit → submit.mutate）：只把时限按 min(max(当前×3, 90), 900) 抬升，
   * 范围/日期/规则键全部沿用共享参数草稿（MEM-I2），不重新解析、不重选范围。
   * options 供挂载 action 场景显式传入回填后的草稿与 goalId（绕过闭包时序）。
   */
  const submitBudgetRetry = (options: { params?: SolverParamValues; goalId?: string } = {}) => {
    const base = options.params ?? params;
    const boundGoalId = options.goalId ?? goalId;
    const nextParams = { ...base, time_limit_seconds: raisedBudgetSeconds(base.time_limit_seconds) };
    setParams(nextParams);
    toast.success(`时间预算已加大至 ${nextParams.time_limit_seconds} 秒，正在重新提交求解`);
    submit.mutate({
      data: {
        ...nextParams,
        solver_rules: [...new Set([...nextParams.solver_rules, ...SYSTEM_RULE_KEYS])],
        goal_id: boundGoalId || null,
        wait: false,
      },
    });
  };
  // 07 §5.2：goals-page 的「加大时间预算重跑」按钮跳转 /solver?goal=<id>&action=raise_budget，
  // 挂载时解析 action 参数——等目标绑定与上下文回填完成（goalId 就位）后执行同一
  // submitBudgetRetry，「一键」语义不依赖解析状态。执行后摘掉 action 参数，防止刷新重复提交。
  const mountActionHandled = useRef(false);
  useEffect(() => {
    const actionParam = searchParams.get("action");
    if (!actionParam || mountActionHandled.current) return;
    if (actionParam === "raise_budget") {
      if (!goalId) return; // 目标尚未绑定完成，等绑定 effect 完成后的下一轮执行。
      mountActionHandled.current = true;
      submitBudgetRetry();
      const next = new URLSearchParams(searchParams);
      next.delete("action");
      setSearchParams(next, { replace: true });
      return;
    }
    if (actionParam === "resolve_scope") {
      // 回求解页聚焦范围区：参数面板按既有展示规则渲染时滚动到范围字段组
      // （未展示或测试环境无滚动实现时安全跳过，行为退化为普通续办绑定）。
      mountActionHandled.current = true;
      document.getElementById("solver-scope-fieldset")?.scrollIntoView?.({ block: "start" });
      const next = new URLSearchParams(searchParams);
      next.delete("action");
      setSearchParams(next, { replace: true });
    }
    // eslint 不在本页校验依赖完整性（沿用文件内其它 effect 的既有风格）。
  }, [goalId, searchParams, setSearchParams, submitBudgetRetry]);
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
  /**
   * 比对解析结果与已关联目标 coverage 项的口径（MEM-D3 目标连续性）：范围或
   * 日期不一致时在确认卡提示「已关联目标 #N，清单可继续修订」——目标归属不变，
   * 用户可以在目标详情修订清单，也可以清除关联后重开目标。
   */
  const checkGoalConsistency = async (boundGoalId: string, data: Interpretation) => {
    try {
      const { data: goal } = await http.get<GoalResponse>(`/api/v1/goals/${boundGoalId}`);
      const coverage = (goal.checklist ?? []).find((item) => item.kind === "coverage");
      const scope = (coverage?.params ?? {}) as Record<string, unknown>;
      const norm = (values: unknown): string[] =>
        Array.isArray(values) ? [...new Set(values.map(String))].sort() : [];
      const conflicts: string[] = [];
      const diff = (label: string, a: string[], b: string[]) => {
        if (a.join("¦") !== b.join("¦")) conflicts.push(label);
      };
      diff("业务线", norm(scope.business_lines), norm(data.business_lines));
      diff("产品班型", norm(scope.product_types), norm(data.product_types));
      diff("班级范围", norm(scope.class_business_ids), norm(data.class_business_ids));
      const goalFrom = scope.date_from ? String(scope.date_from).slice(0, 10) : "";
      const goalTo = scope.date_to ? String(scope.date_to).slice(0, 10) : "";
      if (
        (goalFrom || goalTo)
        && (goalFrom !== (data.date_from ?? "") || goalTo !== (data.date_to ?? ""))
      ) {
        conflicts.push("日期范围");
      }
      setGoalNotice(
        conflicts.length
          ? `已关联目标 #${boundGoalId.slice(0, 8)}，本次解析的${conflicts.join("、")}与原目标清单口径不一致——求解仍关联该目标，清单可在目标详情继续修订。`
          : `已关联目标 #${boundGoalId.slice(0, 8)}，本次解析与目标清单口径一致，重跑沿用同一目标。`,
      );
    } catch {
      // 目标详情读取失败不阻塞排课主流程，提示条保留默认文案。
    }
  };
  /** 解析成功后的统一收尾：回填、徽标与阶段推进，流式与回退两条路共用。 */
  const applyInterpretation = (data: Interpretation) => {
    setParsedSeconds((Date.now() - interpretStartedAt.current) / 1000);
    setInterpretation(data);
    setAssistantEngine(data.source === "feishu_aily" ? "Aily（可选通道）" : "通用 AI 模型");
    // MEM-D3（目标连续性）：同一会话已有 goalId 时重新解析保留它——用户补救
    // 不脱离原目标；解析口径与目标清单的比对结果以提示条呈现。
    if (goalId) {
      setGoalNotice(`已关联目标 #${goalId.slice(0, 8)}；本次解析沿用该目标，清单可继续修订。`);
      void checkGoalConsistency(goalId, data);
    } else {
      setGoalNotice("");
    }
    // 问题2：未确认的「扩大范围」随新解析回滚——确认动作没有完成，草稿不能
    // 带着扩大后的范围静默进入下一次确认。先在闭包内合成回滚后的草稿基线，
    // 再与回填合并成一次 setParams，避免两次更新互相覆盖。
    const draftBase = scopeExpansion
      ? {
          ...params,
          ...(scopeExpansion.business_lines ? { business_lines: scopeExpansion.business_lines } : {}),
          ...(scopeExpansion.class_business_ids ? { class_business_ids: scopeExpansion.class_business_ids } : {}),
        }
      : params;
    if (scopeExpansion) setScopeExpansion(null);
    // P1 回填：解析结果写入手动参数草稿（业务范围 + 日期三元组），用户手动改过
    // 的字段不覆盖。自此确认卡与手动面板读写同一份草稿，范围不会再因改预算或
    // 换入口而悄悄丢失/扩大。
    const nextBusinessLines = manuallyEdited.current.business_lines ? draftBase.business_lines : data.business_lines ?? [];
    const nextProductTypes = manuallyEdited.current.product_types ? draftBase.product_types : data.product_types ?? [];
    const nextClassBusinessIds = manuallyEdited.current.class_business_ids ? draftBase.class_business_ids : data.class_business_ids ?? [];
    setParams((current) => ({
      ...current,
      business_lines: nextBusinessLines,
      product_types: nextProductTypes,
      class_business_ids: nextClassBusinessIds,
      date_from: manuallyEdited.current.date_from ? current.date_from : data.date_from ?? null,
      date_to: manuallyEdited.current.date_to ? current.date_to : data.date_to ?? null,
      date_window_days: manuallyEdited.current.date_window_days ? current.date_window_days : data.date_window_days ?? current.date_window_days,
    }));
    // 记录本次解析写入草稿的范围基线，作为「扩大范围」判定的基准。
    parsedScopeBaseline.current = { business_lines: nextBusinessLines, class_business_ids: nextClassBusinessIds };
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
      // 07 §6.4：已绑定目标时两条通道都必须携带 goal_id（续办增量解析的前置条件）。
      const data = await streamInterpretInstruction(instruction, controller.signal, {
        onThinking: (delta) => setLiveThinking((current) => current + delta),
        onStage: (stage) => {
          const index = INTERPRET_STAGES.findIndex((item) => item.key === stage);
          if (index >= 0) {
            stageLocked.current = true;
            setStageIndex(index);
          }
        },
      }, goalId || undefined);
      applyInterpretation(data);
    } catch {
      // 用户主动取消不算失败，安静回到初始态等下一次解析。
      if (controller.signal.aborted) {
        setPhase("idle");
        return;
      }
      try {
        // 流式通道不可用（网络/网关缓冲/协议中断）时回退老接口，降级路径必须保留；
        // 同步回退的请求体与流式主路径口径一致（同样带 goal_id）。
        const { data } = await http.post<Interpretation>(
          "/api/v1/assistant/interpret",
          { instruction, ...(goalId ? { goal_id: goalId } : {}) },
          { signal: controller.signal },
        );
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
  /**
   * 问题2：范围字段（业务线/班级）的手动编辑入口——打脏标记、写同一份草稿，
   * 并把「限定 → 全部」的扩大动作拦进单独确认（以解析写入草稿时的范围为基线）；
   * 改回限定值即视为放弃扩大，撤销该字段的待确认状态。
   */
  const handleScopeFieldChange = (field: "business_lines" | "class_business_ids", value: string[]) => {
    manuallyEdited.current[field] = true;
    const previous = params[field];
    setParams((current) => ({ ...current, [field]: value }));
    if (!value.length && previous.length && parsedScopeBaseline.current[field].length) {
      setScopeExpansion((current) => ({ ...current, [field]: previous }));
      return;
    }
    setScopeExpansion((current) => {
      if (!current || !(field in current)) return current;
      const next = { ...current };
      delete next[field];
      return Object.keys(next).length ? next : null;
    });
  };
  const confirmScopeExpansion = () => {
    // 明确确认后扩大才生效：解除求解入口禁用，草稿保持用户改后的范围。
    setScopeExpansion(null);
  };
  const revertScopeExpansion = () => {
    if (scopeExpansion) {
      setParams((current) => ({
        ...current,
        ...(scopeExpansion.business_lines ? { business_lines: scopeExpansion.business_lines } : {}),
        ...(scopeExpansion.class_business_ids ? { class_business_ids: scopeExpansion.class_business_ids } : {}),
      }));
    }
    setScopeExpansion(null);
  };
  /**
   * 按解析结果登记持久目标（清单用后端预填草稿，前端可改的项暂不展开）。
   * 「确认并开始求解」与「登记为目标，稍后补充」共用这一份，清单/范围口径不会各说各话。
   * syncUrl：求解路径把新目标的 id 同步进 URL；稍后补充路径随即跳走，不需要。
   */
  const createGoalFromInterpretation = async (current: Interpretation, syncUrl: boolean): Promise<string> => {
    // MEM-D3（基准联动）+ 问题4：基准版本默认只记录下来（用于变更明细/数量
    // 对比与优化配置）；只有用户明确开启「设置变更上限」才把 max_changes 项
    // 写进清单（上限取「验收上限」输入，默认 50），不用文案掩盖阈值的有无。
    const checklistDraft: GoalChecklistItem[] = [...(current.goal_checklist_draft ?? [])];
    if (baselineId && changeLimitEnabled && !checklistDraft.some((item) => item.kind === "max_changes")) {
      checklistDraft.push({
        key: "max_changes-baseline",
        kind: "max_changes",
        requirement: `相对所选基准版本的变更数 ≤ ${maxChangesLimit}（优化类目标：只设验收上限，「尽量少改」不升级为「绝不改」）`,
        params: { max_changes: maxChangesLimit, baseline_schedule_version_id: baselineId },
      });
    }
    const { data: goal } = await http.post<{ id: string }>("/api/v1/goals", {
      instruction: current.instruction,
      // 问题2：目标清单与求解共用同一份手动草稿的范围口径，不会各说各话。
      business_lines: params.business_lines,
      product_types: params.product_types,
      class_business_ids: params.class_business_ids,
      date_from: params.date_from,
      date_to: params.date_to,
      checklist: checklistDraft,
      baseline_schedule_version_id: baselineId || null,
      forbid_publish: true,
    });
    setGoalId(goal.id);
    if (syncUrl) {
      // 07 §4.5：新建目标成功即把 goal_id 同步进 URL（replace 不新增历史记录），
      // 刷新/分享链接可回到同一任务上下文；绑定守卫同步置位，避免 URL 变化
      // 触发绑定 effect 重复拉取与回填。
      goalParamBound.current = true;
      const nextSearchParams = new URLSearchParams(searchParams);
      nextSearchParams.set("goal", goal.id);
      nextSearchParams.delete("action");
      setSearchParams(nextSearchParams, { replace: true });
    }
    setGoalNotice(`已登记目标 #${goal.id.slice(0, 8)}${baselineId ? (changeLimitEnabled ? "，按所选基准的变更数验收" : "，已记录基准版本用于变更对比（未设变更上限）") : ""}。`);
    return goal.id;
  };
  /**
   * 有要求没能落实（主体/时段无法确认）时的出口：先登记目标，带着「待量化」清单项
   * 去「目标跟踪」补齐主体与时段，再回来重新求解。没有这个出口，确认卡上「到目标清单
   * 补参」的提示对新任务不可达——目标要到点击「确认并开始求解」才会创建，而该按钮
   * 恰恰因为这些要求被禁用。已关联目标（续办）时直接前往，不重复创建。
   */
  const registerGoalForLater = async () => {
    if (!interpretation || goalBusy) return;
    try {
      setGoalBusy(true);
      const targetId = goalId || (await createGoalFromInterpretation(interpretation, false));
      toast.success("已登记目标，请在「目标跟踪」补齐待补充的要求");
      navigate(`/goals?goal=${targetId}`);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setGoalBusy(false);
    }
  };
  const solveFromInterpretation = async () => {
    if (!interpretation || interpretation.unsupported_requirements?.length) return;
    // 问题2：扩大范围未确认时不允许任何入口出发求解（按钮已禁用，这里兜底）。
    if (scopeExpansion) return;
    try {
      // 目标验收闭环（MEM-C3）：先按解析结果登记持久目标，再把 goal_id 带进求解任务；
      // run 结束后自动验收。
      let activeGoalId = goalId;
      if (trackGoal && !activeGoalId) {
        setGoalBusy(true);
        activeGoalId = await createGoalFromInterpretation(interpretation, true);
      }
      const { data } = await http.post<SolverRunResponse>("/api/v1/assistant/solve", {
        instruction: interpretation.instruction,
        // 问题2（单一数据源）：范围/日期/预算统一读手动参数草稿——确认卡与手动
        // 面板改的是同一份草稿，两个入口发出的范围口径永远一致，改预算不动范围。
        business_lines: params.business_lines,
        product_types: params.product_types,
        class_business_ids: params.class_business_ids,
        date_from: params.date_from,
        date_to: params.date_to,
        date_window_days: params.date_window_days,
        solver_rules: interpretation.solver_rules,
        time_limit_seconds: params.time_limit_seconds,
        // 07 §6.5：任务级约束随请求体全量携带（软约束链的前端填充——goal.context.
        // soft_task_constraints 的唯一来源），与确认卡展示同源；hard 约束走清单
        // 编译（§2.4），请求体带全量由后端同键去重兜底。
        task_constraints: interpretation.task_constraints ?? [],
        goal_id: trackGoal ? activeGoalId : null,
        wait: false,
      });
      setRunId(data.id);
      setCurrent(data);
      toast.success(trackGoal ? "已登记跟踪目标，CP-SAT 求解已启动" : "确认完成，CP-SAT 求解已启动");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setGoalBusy(false);
    }
  };
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
    <section className="border border-blue-200 bg-blue-50/40 p-5"><div className="flex flex-wrap items-center gap-2"><Bot className="size-4 text-blue-600" /><h2 className="font-semibold">一句话排课</h2><Badge tone={assistantReady === true ? "green" : "yellow"}>{assistantReady === true ? `${assistantEngine} 已接入` : assistantReady === false ? "AI 模型待配置" : assistantProbeError ? "AI 配置读取失败" : "正在读取 AI 配置"}</Badge>{assistantProbeError ? <Button size="sm" variant="outline" onClick={probeAssistant}>重试</Button> : null}</div><p className="mt-2 text-xs text-zinc-500">自然语言 → AI 解析业务范围与规则 → 教务确认 → CP-SAT 确定性求解 → 课表与日历下发</p><textarea aria-label="一句话排课指令" className="mt-4 min-h-24 w-full rounded-md border border-zinc-300 bg-white p-3 text-sm outline-none focus:border-blue-500 disabled:bg-zinc-50 disabled:text-zinc-400" value={instruction} disabled={phase === "thinking"} onChange={(event) => { setInstruction(event.target.value); setInterpretation(null); if (phase === "failed") { setPhase("idle"); setInterpretError(""); } }} /><div className="mt-3 flex flex-wrap gap-2"><Button onClick={interpret} disabled={assistantReady !== true || interpreting || instruction.trim().length < 2}><Sparkles className="size-4" />{interpreting ? "AI 正在理解指令" : "让 AI 解析排课指令"}</Button>{interpreting ? <Button variant="outline" onClick={cancelInterpret}>取消解析</Button> : null}{interpretation ? <Button variant="outline" onClick={solveFromInterpretation} disabled={Boolean(interpretation.unsupported_requirements?.length) || goalBusy || Boolean(scopeExpansion)}><Play className="size-4" />{goalBusy ? "正在登记目标…" : "确认并开始求解"}</Button> : null}{interpretation && interpretation.unsupported_requirements?.length && hasQuantizablePlaceholder(interpretation) ? <Button variant="outline" onClick={() => void registerGoalForLater()} disabled={goalBusy}><Target className="size-4" />{goalId ? "前往目标跟踪补充" : "登记为目标，稍后补充"}</Button> : null}</div>{assistantReady === false ? <div className="mt-4 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900"><div>普通集成应用继续负责外部表格和日历；一句话理解改由独立 AI 模型接口完成，不再要求 Aily 应用标识和技能标识。</div><Button className="mt-3" size="sm" variant="outline" onClick={() => navigate("/integrations?section=ai")}><Settings2 className="size-4" />配置一句话排课 AI</Button></div> : null}{phase === "thinking" ? <InterpretProgress stageIndex={stageIndex} elapsedSeconds={elapsedSeconds} liveText={liveThinking} /> : null}{phase === "failed" ? <div role="alert" className="mt-4 border-l-2 border-red-500 bg-red-50 px-4 py-3 text-sm text-red-800"><div>AI 解析失败：{interpretError}</div>{elapsedSeconds > 30 ? <p className="mt-1 text-xs text-amber-800">本次解析超过 30 秒仍未返回，可稍后重试，或改用手动参数求解。</p> : null}<Button className="mt-2" size="sm" variant="outline" onClick={() => void interpret()} disabled={assistantReady !== true || instruction.trim().length < 2}><RefreshCw className="size-3.5" />重试解析</Button></div> : null}{interpretation ? <><p className="mt-3 border-l-2 border-blue-400 bg-white/70 px-3 py-2 text-xs text-zinc-600">{interpretation.summary}；解析来源：{interpretation.source === "feishu_aily" ? "Aily（可选通道）" : assistantEngine}。</p><InterpretThought thinking={interpretation.thinking ?? ""} seconds={parsedSeconds} /><div className="mt-4 grid gap-3 border-t border-blue-200 pt-4 text-sm md:grid-cols-3"><Scope label="业务线" values={params.business_lines} /><Scope label="产品班型" values={params.product_types} /><Scope label="班级范围" values={params.class_business_ids} /><Scope label="日期范围" values={[params.date_from, params.date_to].filter(Boolean) as string[]} /><Scope label="日期调整窗口" values={[`${params.date_window_days} 天`]} /><Scope label="识别规则" values={interpretation.recognized_rules} /></div>{interpretation.task_constraints?.length ? <TaskConstraintsPanel constraints={interpretation.task_constraints} /> : null}{interpretation.memory_action_receipts?.length ? <MemoryReceiptsPanel receipts={interpretation.memory_action_receipts} /> : null}<div className="mt-4 flex flex-wrap items-end gap-5 border-t border-blue-200 pt-4"><label className="flex cursor-pointer items-center gap-2 text-sm text-zinc-700"><input aria-label="以此为目标跟踪" className="accent-blue-600" type="checkbox" checked={trackGoal} onChange={(event) => setTrackGoal(event.target.checked)} /><span className="inline-flex items-center gap-1.5">以此为目标跟踪<InfoTooltip label="以此为目标跟踪">把这次指令登记成持久目标：每次求解结束后按课次覆盖、禁排复核、硬冲突、只出草稿等清单逐项验收，缺口与建议下一步会显示在下方报告中；发布永远不会被自动执行。</InfoTooltip></span></label>{trackGoal ? <label className="block text-sm text-zinc-700"><span className="inline-flex items-center gap-1.5">基准版本<InfoTooltip label="基准版本">记录所选基准版本，用于对比本次结果的变更明细与数量；默认不设变更上限，勾选「设置变更上限」后才会按上限验收。「尽量少改」是优化目标，不会被升级为「绝不能改」。</InfoTooltip></span><Select aria-label="基准版本" selectSize="sm" containerClassName="mt-1 w-52" value={baselineId} onChange={(event) => setBaselineId(event.target.value)}><option value="">不设基准</option>{scheduleList.map((item) => <option key={item.id} value={item.id}>v{item.version_no} {item.name}{item.status === "published" ? "（已发布）" : ""}</option>)}</Select></label> : null}{trackGoal && baselineId ? <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-700"><label className="flex cursor-pointer items-center gap-2"><input aria-label="设置变更上限" className="accent-blue-600" type="checkbox" checked={changeLimitEnabled} onChange={(event) => setChangeLimitEnabled(event.target.checked)} /><span className="inline-flex items-center gap-1.5">设置变更上限<InfoTooltip label="设置变更上限">默认不设上限：基准版本只用于记录变更明细与数量对比。勾选后创建目标清单才会附带 max_changes 项——相对所选基准的变更数超过上限时验收不通过；只设上限，不改变求解行为。</InfoTooltip></span></label>{changeLimitEnabled ? <label className="block"><span className="inline-flex items-center gap-1.5">验收上限（变更数）<InfoTooltip label="验收上限">已开启变更上限：创建目标清单会附带 max_changes 项，相对所选基准的变更数超过这个上限时验收不通过；只设上限，不改变求解行为。</InfoTooltip></span><input aria-label="变更数验收上限" className="mt-1 h-9 w-24 rounded-md border border-zinc-300 bg-white px-2 text-sm tabular-nums outline-none focus:border-blue-500" type="number" min={0} max={100000} step={5} value={maxChangesLimit} onChange={(event) => setMaxChangesLimit(Math.max(0, Math.round(Number(event.target.value)) || 0))} /></label> : null}<Badge tone="blue">{changeLimitEnabled ? "已选基准：清单将附带变更数验收项" : "已选基准：仅记录基准用于变更对比，未设变更上限"}</Badge></div> : null}</div><div className="mt-3 space-y-2 text-xs text-amber-900">{interpretation.coverage_warnings?.map((warning) => <p key={warning}>{warning}</p>)}{interpretation.unsupported_requirements?.length ? <div role="alert" className="border-l-2 border-amber-500 bg-amber-50 p-3"><strong>以下要求尚未进入求解（待补充）：</strong><ul>{interpretation.unsupported_requirements.map((requirement) => <li key={requirement}>{requirement}</li>)}</ul><p>{hasQuantizablePlaceholder(interpretation) ? "不支持带着未实现的要求开始求解。可以「登记为目标，稍后补充」：目标会带一条待补参的清单项，到「目标跟踪」补齐主体与时段后重新求解，要求即编译进求解；也可以修改指令后重新解析。" : "不支持带着未实现的要求开始求解：请修改指令去掉这些要求后重新解析，或先在「规则工作台」配置对应规则。"}</p></div> : null}</div></> : null}{goalId && goalNotice ? <p role="status" className="mt-3 border-l-2 border-blue-400 bg-blue-50/60 px-3 py-2 text-xs text-zinc-700">{goalNotice}</p> : null}</section>
    <div className="grid gap-2 xl:grid-cols-[360px_minmax(0,1fr)]">
      {showParams ? <div className="animate-fade-in"><SolverParams
        params={params}
        setParams={setParams}
        scope={scopeOptions}
        selectedCount={selectedCount}
        pending={submit.isPending || activeRun?.status === "running"}
        aiWindowBadge={aiWindowFilled && params.date_window_days !== defaultParams.date_window_days}
        onManualEdit={markManualEdit}
        linkedGoalId={goalId}
        onClearGoal={() => {
          setGoalId("");
          setGoalNotice("");
          goalParamBound.current = true;
          // 07 §4.5：清除目标关联时把 goal 参数从 URL 一并删除（action 同步摘除）。
          const next = new URLSearchParams(searchParams);
          next.delete("goal");
          next.delete("action");
          setSearchParams(next, { replace: true });
        }}
        scopeExpansion={scopeExpansion}
        onScopeFieldChange={handleScopeFieldChange}
        onConfirmScopeExpansion={confirmScopeExpansion}
        onRevertScopeExpansion={revertScopeExpansion}
        onSubmit={() => submit.mutate({ data: { ...params, solver_rules: [...new Set([...params.solver_rules, ...SYSTEM_RULE_KEYS])], goal_id: goalId || null, wait: false } })}
      /></div> : null}
      <div className={showParams ? undefined : "xl:col-span-2"}>
        <RunPanel run={activeRun} onUseInstruction={applySuggestedInstruction} onRaiseBudget={goalId ? () => submitBudgetRetry() : undefined} />
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

function SolverParams({ params, setParams, scope, selectedCount, pending, aiWindowBadge, onManualEdit, linkedGoalId, onClearGoal, scopeExpansion, onScopeFieldChange, onConfirmScopeExpansion, onRevertScopeExpansion, onSubmit }: { params: SolverParamValues; setParams: React.Dispatch<React.SetStateAction<SolverParamValues>>; scope: { businessLines: string[]; classes: string[] }; selectedCount: number; pending: boolean; aiWindowBadge?: boolean; onManualEdit?: (field: "date_from" | "date_to" | "date_window_days") => void; linkedGoalId?: string; onClearGoal?: () => void; scopeExpansion?: { business_lines?: string[]; class_business_ids?: string[] } | null; onScopeFieldChange?: (field: "business_lines" | "class_business_ids", value: string[]) => void; onConfirmScopeExpansion?: () => void; onRevertScopeExpansion?: () => void; onSubmit: () => void }) {
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
      {/* 07 §5.2：resolve_scope 补救动作「回求解页聚焦范围区」的滚动锚点。 */}
      <fieldset id="solver-scope-fieldset" className="mt-5 grid gap-3 border-b border-zinc-100 pb-4">
        <legend className="sr-only">求解范围</legend>
        <div className="text-xs font-medium text-zinc-500">求解范围</div>
        <label className="block text-sm text-zinc-700">
          业务线
          <Select aria-label="业务线" selectSize="md" containerClassName="mt-1.5" value={params.business_lines[0] ?? ""} onChange={(event) =>
              onScopeFieldChange?.("business_lines", event.target.value ? [event.target.value] : [])
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
          <Select aria-label="班级" selectSize="md" containerClassName="mt-1.5" value={params.class_business_ids[0] ?? ""} onChange={(event) =>
              onScopeFieldChange?.("class_business_ids", event.target.value ? [event.target.value] : [])
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
      {/* 问题2（扩大范围单独确认）：把范围从限定改成「全部」时，必须在这里明确
          确认后求解入口才会恢复；确认前如实说明扩大尚未生效。 */}
      {scopeExpansion ? (
        <div role="alert" className="mt-4 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div>
            范围将要扩大：{[scopeExpansion.business_lines ? "业务线" : null, scopeExpansion.class_business_ids ? "班级范围" : null].filter(Boolean).join("、")}
            将从限定范围改为「全部」，超出解析确认的范围。扩大范围需要单独确认，确认前不能开始求解。
          </div>
          <div className="mt-2 flex gap-2">
            <Button size="sm" variant="outline" onClick={onConfirmScopeExpansion}>确认扩大范围</Button>
            <Button size="sm" variant="outline" onClick={onRevertScopeExpansion}>恢复原范围</Button>
          </div>
        </div>
      ) : null}
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
      {/* MEM-D3（目标连续性）：会话持有 goalId 时手动求解自动带上同一目标，
          完成后纳入同一验收闭环；可显式清除回到「不带目标」的原有行为。 */}
      {linkedGoalId ? (
        <div className="mt-5 flex flex-wrap items-center gap-2 rounded-md border border-blue-100 bg-blue-50/60 px-3 py-2 text-sm">
          <Target className="size-4 shrink-0 text-blue-600" />
          <span className="text-zinc-700">
            本次求解关联目标 <span className="font-mono text-blue-700">#{linkedGoalId.slice(0, 8)}</span>，完成后自动验收
          </span>
          <button
            type="button"
            aria-label="清除目标关联"
            className="ml-auto text-xs text-zinc-500 underline-offset-2 transition-colors hover:text-zinc-700 hover:underline"
            onClick={onClearGoal}
          >
            清除关联
          </button>
        </div>
      ) : null}
      <Button className="mt-6 w-full" onClick={onSubmit} disabled={pending || Boolean(scopeExpansion)}>
        <Play className="size-4" />
        按参数开始求解
      </Button>
    </section>
  );
}

function Scope({ label, values }: { label: string; values: string[] }) { return <div><div className="text-xs text-zinc-400">{label}</div><div className="mt-1 flex flex-wrap gap-1">{values.length ? values.map((value) => <Badge key={value} tone="blue">{value}</Badge>) : <span className="text-xs text-zinc-500">全部</span>}</div></div>; }

const TASK_SUBJECT_LABELS: Record<string, string> = { teacher: "教师", classroom: "教室", cohort: "班级" };

/** 清单草稿里是否有「待量化」的禁排占位项——有才说明这些未落实要求能靠补参落地。 */
function hasQuantizablePlaceholder(interpretation: Interpretation): boolean {
  return (interpretation.goal_checklist_draft ?? []).some(
    (item) => item.kind === "forbidden_slot_free" && Boolean((item.params as { needs_params?: unknown } | null | undefined)?.needs_params),
  );
}

/**
 * 确认卡「本次任务要求」区（07 §6.1）：解析产出的任务级约束逐条展示
 * （主体×时段 + hard/soft 徽标），固定文案声明作用域——仅本次任务、不进规则库。
 * 与 solveFromInterpretation 请求体的 task_constraints 同源（同一 interpretation 状态）。
 */
function TaskConstraintsPanel({ constraints }: { constraints: AssistantTaskConstraint[] }) {
  return (
    <div aria-label="本次任务要求" className="mt-4 border-t border-blue-200 pt-4">
      <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-zinc-500">
        本次任务要求
        <Badge tone="neutral">{TASK_CONSTRAINT_SOURCE_NOTE}</Badge>
      </div>
      <ul className="mt-2 space-y-1.5">
        {constraints.map((item, index) => (
          <li key={item.id || `${item.source_text}-${index}`} className="flex flex-wrap items-center gap-2 border-l-2 border-zinc-200 pl-3 text-xs leading-5 text-zinc-700">
            <Badge tone={item.hardness === "soft" ? "neutral" : "blue"}>{taskConstraintHardnessLabel(item.hardness)}</Badge>
            <span>{item.source_text}</span>
            <span className="text-zinc-400">
              {TASK_SUBJECT_LABELS[item.subject_type] ?? item.subject_type} {item.subject_ids.join("、")}
              {item.slot_business_ids.length ? ` × 时段 ${item.slot_business_ids.join("、")}` : " × 时段待补充"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * 确认卡「记忆动作回执」区（07 §6.1）：explicit 动作已直接执行→绿色徽标 +
 * 文案（含「可在记忆页修改或撤销」的固定提示，即修改/撤销入口）；推测或降级→
 * 黄色徽标 + 「已放入记忆收件箱待确认」，按既有流程在记忆页审批。
 */
function MemoryReceiptsPanel({ receipts }: { receipts: AssistantMemoryActionReceipt[] }) {
  return (
    <div aria-label="记忆动作回执" className="mt-4 border-t border-blue-200 pt-4">
      <div className="text-xs font-medium text-zinc-500">记忆动作回执</div>
      <ul className="mt-2 space-y-1.5">
        {receipts.map((item, index) => (
          <li key={item.action_id || `${item.receipt}-${index}`} className="flex flex-wrap items-center gap-2 border-l-2 border-zinc-200 pl-3 text-xs leading-5 text-zinc-700">
            <Badge tone={item.status === "executed" ? "green" : "yellow"}>{memoryReceiptStatusLabel(item.status)}</Badge>
            <span>{item.receipt}</span>
            {item.status !== "executed" ? <span className="text-amber-800">已放入记忆收件箱待确认</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

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

function RunPanel({ run, onUseInstruction, onRaiseBudget }: { run: SolverRunResponse | null; onUseInstruction?: (value: string) => void; onRaiseBudget?: () => void }) {
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
      <GoalReportSection run={run} onRaiseBudget={onRaiseBudget} />
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

/** 偏好记忆使用情况（MEM-C1）：读创建任务时冻结的 memory_usage，不依赖解释生成。

第七轮口径收口：这里展示的是编译资格段（创建时点、方案级），标题与 headline
都按编译口径表述；对本次课程的实际匹配与结果满足情况由解释层计算
（解释面板的「偏好记忆」相关行），本组件不再用编译状态冒充本次使用结论。 */
function MemoryUsageSection({ memory }: { memory: MemoryUsageSnapshot | null | undefined }) {
  if (!memory?.status) return null;
  const headline = memoryHeadline(memory);
  if (!headline) return null;
  const failed = memory.status === "compile_failed";
  const unused = (Array.isArray(memory.outcomes) ? memory.outcomes : []).filter(
    (item: MemoryOutcome) => item.outcome !== "applied",
  );
  return (
    <section className="mt-3 rounded-md border border-zinc-200 bg-zinc-50/60 px-4 py-3 text-sm">
      <div className="text-xs font-medium text-zinc-500">偏好记忆（创建时编译口径）</div>
      {failed ? (
        <p className="mt-1.5 border-l-2 border-red-400 bg-red-50 px-3 py-2 text-red-900">
          {headline}
          {memory.detail ? `（${memory.detail}）` : ""}。本次求解照常完成，但未参考任何偏好记忆；修复记忆层后重新求解即可带上偏好。
        </p>
      ) : (
        <div className="mt-1.5">
          <p className="text-zinc-800">{headline}</p>
          {unused.length ? (
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-zinc-600">
              {unused.map((item) => (
                <li key={`${item.entry_id}-${item.outcome}`}>
                  {item.subject_type} {item.subject_id} {item.predicate}：{memoryOutcomeLabel(item.outcome)}
                  {item.detail ? `——${item.detail}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </section>
  );
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
      <MemoryUsageSection memory={run.memory_usage as MemoryUsageSnapshot | null | undefined} />
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

/**
 * 目标验收报告（MEM-C3，验收可见性 MEM-D2/D6）：读取 run.goal_report（后端
 * 代码验收器落库），逐项 ✓/✗ + 缺口与允许的下一步。有 ✗ 时给 amber 提示，
 * 绝不把「求解完成」混同成「目标完成」。三态可见：
 * - 报告未就绪（run completed 但报告在异步事务里）→「验收中…」；
 * - 验收执行失败（acceptance_status=failed 的失败标记）→ amber「验收失败：原因」；
 * - 报告就绪 → 逐项结论，unverifiable（无法验证）单独标注，绝不与通过混同。
 */
function GoalReportSection({ run, onRaiseBudget }: { run: SolverRunResponse | null; onRaiseBudget?: () => void }) {
  const report = parseGoalReport(run?.goal_report);
  if (!report) {
    if (run?.goal_id && run.status === "completed") {
      return (
        <section aria-label="目标验收报告" className="mt-3 rounded-md border border-blue-200 bg-blue-50/40 px-4 py-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Target className="size-4 text-blue-600" />
            <div className="text-xs font-medium text-zinc-500">目标验收报告</div>
            <Badge tone="blue">验收中…</Badge>
          </div>
          <p className="mt-1.5 text-xs text-zinc-500">求解已完成，验收器正在按目标清单逐项核对，报告稍后出现在这里。</p>
        </section>
      );
    }
    return null;
  }
  if (report.acceptance_status === "failed") {
    return (
      <section aria-label="目标验收报告" className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        <div className="flex flex-wrap items-center gap-2">
          <Target className="size-4 text-amber-600" />
          <div className="text-xs font-medium text-zinc-500">目标验收报告</div>
          <Badge tone="yellow">验收失败</Badge>
        </div>
        <p className="mt-1.5 text-xs leading-5">验收失败：{report.acceptance_error || "验收器执行时发生异常，未能生成报告"}。求解结果本身不受影响；请重试求解或联系管理员。</p>
      </section>
    );
  }
  const failed = report.items.filter((item) => !item.passed);
  return (
    <section aria-label="目标验收报告" className="mt-3 rounded-md border border-zinc-200 bg-zinc-50/60 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Target className="size-4 text-blue-600" />
        <div className="text-xs font-medium text-zinc-500">目标验收报告</div>
        <Badge tone={report.all_passed ? "green" : "yellow"}>
          {report.all_passed ? `全部 ${report.passed_count} 项通过 · 目标达成` : `目标未完成：${report.failed_count} 项缺口`}
        </Badge>
      </div>
      <ul className="mt-2 space-y-1.5">
        {report.items.map((item) => (
          <li key={item.key} className="flex gap-2 text-xs leading-5">
            <span aria-hidden className={"shrink-0 " + (item.passed ? "text-emerald-600" : item.verdict === "unverifiable" ? "text-amber-600" : "text-red-600")}>{item.passed ? "✓" : item.verdict === "unverifiable" ? "?" : "✗"}</span>
            <span>
              <span className="font-medium text-zinc-700">{goalKindLabel(item.kind)}</span>
              {isBottomLineItem(item) ? <Badge tone="blue">底线</Badge> : null}
              {item.verdict === "unverifiable" ? <Badge tone="yellow">无法验证</Badge> : null}
              <span className="text-zinc-600"> · {item.requirement}</span>
              <span className="text-zinc-500">——{item.detail}</span>
            </span>
          </li>
        ))}
      </ul>
      {failed.length ? (
        <div role="status" className="mt-2 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-amber-900">
          <div className="text-xs font-medium">目标未完成：{report.failed_count} 项缺口，建议的下一步</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs">
            {report.gaps.map((gap) => (
              <li key={gap.key} className="flex flex-wrap items-center gap-2">
                <span>{gap.next_step}</span>
                {/* 07 §5.2：remedy=raise_budget 的缺口动作化——按钮直接走独立提交路径。 */}
                {gap.remedy === "raise_budget" && onRaiseBudget ? (
                  <Button size="sm" variant="outline" onClick={onRaiseBudget}>加大时间预算重跑</Button>
                ) : null}
              </li>
            ))}
          </ul>
          {report.decision?.reason ? <p className="mt-1 text-xs text-amber-800">{report.decision.reason}。</p> : null}
        </div>
      ) : null}
    </section>
  );
}
