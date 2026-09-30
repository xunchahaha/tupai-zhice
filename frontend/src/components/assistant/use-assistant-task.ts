import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  getGetGoalApiV1GoalsGoalIdGetQueryKey,
  getListGoalsApiV1GoalsGetQueryKey,
  getListSchedulesApiV1SchedulesGetQueryKey,
  getListSolverRunsApiV1SolverRunsGetQueryKey,
  getOverviewApiV1OverviewGetQueryKey,
  useGetGoalApiV1GoalsGoalIdGet,
  useGetSolverRunApiV1SolverRunsRunIdGet,
  useSubmitSolverRunApiV1SolverRunsPost,
} from "@/api/generated/client";
import { type GoalChecklistItem, type GoalDetailResponse, type SolverRunResponse } from "@/api/generated/models";
import { http } from "@/api/http";
import { useAssistantProbe } from "@/components/assistant/use-assistant-probe";
import { useHandoff } from "@/components/assistant/use-handoff";
import { useInterpretSession } from "@/components/assistant/use-interpret-session";
import { useRunExplanation } from "@/components/assistant/use-run-explanation";
import { useSubmitGate } from "@/components/assistant/use-submit-gate";
import { useTaskParams } from "@/components/assistant/use-task-params";
import { useTaskUrlSync } from "@/components/assistant/use-task-url-sync";
import { goalScopeConflicts, scopeFromChecklist, truncateText } from "@/lib/assistant-task";
import { errorMessage } from "@/lib/format";
import { isClosedGoal } from "@/lib/goal";
import { type Interpretation } from "@/lib/interpret-stream";
import { classifyRun } from "@/lib/run-kind";
import { type SolverParamValues, withSystemRules } from "@/lib/solver-params";
import { parseGoalContext, raisedBudgetSeconds } from "@/lib/task-context";

export type { RefineState } from "@/components/assistant/use-interpret-session";

/** 解析出的任务级约束有没有随求解请求提交：pending=还没提交，submitted=已随确认求解提交，bypassed=走了手动路径、没带上。 */
type ConstraintUse = "pending" | "submitted" | "bypassed";

/**
 * 排课助手的任务状态机：从 solver-page 演进而来，语义原样保留（流式解析 + 同步回退、
 * goal_id 两条路径携带、范围草稿单一数据源、扩大范围单独确认、加预算公式、验收轮询截止……），
 * 只是把「入口」从 URL + 页面状态重新编排：?goal= 续办、?run= 看结果、?action= 一键补救、
 * ?prompt= 预填、?manual=1 展开手动排课。页面只负责把这些状态摆到对应的卡片上。
 *
 * 拆分：解析状态机在 use-interpret-session，URL 与任务状态对齐在 use-task-url-sync，
 * 求解/重新解析的统一闸门在 use-submit-gate；这里只编排任务、求解与面板。
 */
export function useAssistantTask() {
  const client = useQueryClient();
  const probe = useAssistantProbe();
  const taskParams = useTaskParams();
  const { params, setParams, scopeExpansion } = taskParams;
  const { blockedReason: submitBlockedReason, gated } = useSubmitGate(scopeExpansion);
  // 每次完整重置 +1：在途的建任务/求解请求返回时据此丢弃结果，不落到已经换掉的任务上。
  const epoch = useRef(0);

  // —— 任务（持久目标）——
  // MEM-C3：确认卡默认开启目标跟踪；一次解析只建一个目标，同一目标的重跑直接复用 goal_id，
  // 验收报告按 run 逐次落档。
  const [trackGoal, setTrackGoalState] = useState(true);
  // 补充条件需要任务，跟踪被关着时自动打开——卡内要一句话说明为什么。
  const [trackForced, setTrackForced] = useState(false);
  const [baselineId, setBaselineId] = useState("");
  const [goalId, setGoalId] = useState("");
  const [goalBusy, setGoalBusy] = useState(false);
  // MEM-D3（基准联动）+ 问题4：选基准默认只记录基准版本（用于变更明细/数量对比与优化配置），
  // 不再静默附带 max_changes=50；只有用户明确开启「设置变更上限」才把 max_changes 项写进目标清单。
  const [maxChangesLimit, setMaxChangesLimit] = useState(50);
  const [changeLimitEnabled, setChangeLimitEnabled] = useState(false);
  // MEM-D3（目标连续性）：goalId 绑定状态/口径提示条文案。
  const [goalNotice, setGoalNotice] = useState("");
  // 已按任务上下文恢复过的目标：新建任务、清除关联都要维护它，否则会用旧上下文覆盖用户当前草稿。
  const [restoredGoalId, setRestoredGoalId] = useState("");
  // 补充问题卡：已登记目标、正在行内补齐缺少的条件。
  const [supplementing, setSupplementing] = useState(false);
  // 课表里「交给助手继续处理」带来的调整对象（所选版本 + 某一节课）：求解基准与目标课次都来自它。
  const { handoff, adopt: adoptHandoff, clear: clearHandoff } = useHandoff();
  const goalQuery = useGetGoalApiV1GoalsGoalIdGet(goalId, { query: { enabled: Boolean(goalId) } });
  const goal = goalId ? (goalQuery.data as GoalDetailResponse | undefined) : undefined;
  const latestGoal = useRef(goal);
  useEffect(() => { latestGoal.current = goal; });
  // 已放弃的任务后端不再接受新的求解（409）：只读回看，绝不带着它的 id 去求解或解析。
  // 已达成的任务仍可继续调整，不在此列。
  const goalClosed = Boolean(goal) && isClosedGoal(goal?.status);
  const solveGoalId = goalClosed ? "" : goalId;

  // —— 求解 ——
  const [runId, setRunId] = useState("");
  const [current, setCurrent] = useState<SolverRunResponse | null>(null);
  // 本会话里由这个页面发起的求解：范围草稿就是它提交时用的那份，没有任务也能据此加预算重跑。
  const [sessionRunId, setSessionRunId] = useState("");
  const latestRunId = useRef(runId);
  useEffect(() => { latestRunId.current = runId; });
  // 这份理解是在哪个 run 存在时解析出来的：之后 runId 变了，说明期间又发起过求解（手动路径）。
  const [interpretedAtRun, setInterpretedAtRun] = useState("");
  const [constraintUse, setConstraintUse] = useState<ConstraintUse>("pending");
  // MEM-D2/D6：验收报告在 run completed 之后于独立事务里异步落库。这里记录「带 goal 的 run 已完成
  // 但报告未就绪」的轮询截止时刻，防止验收层挂掉时前端永远轮询（上限约 45 秒）。
  const acceptanceWatch = useRef<{ runId: string; deadline: number } | null>(null);

  // —— 需求与解析 ——
  const session = useInterpretSession({
    probe,
    gated,
    goalIdForParse: solveGoalId,
    onParsed: (data, boundGoalId) => {
      // MEM-D3（目标连续性）：同一会话已有任务时重新解析保留它——用户补救不脱离原任务；
      // 解析口径与任务原范围的比对结果以提示条呈现。
      if (boundGoalId) {
        const conflicts = latestGoal.current ? goalScopeConflicts(latestGoal.current, data) : null;
        setGoalNotice(
          conflicts === null
            ? "本次解析沿用这个任务，重跑仍会核对同一批要求。"
            : conflicts.length
              ? `本次解析的${conflicts.join("、")}与原任务不一致——仍按这个任务排课，需要的话可以在「查看详情」里修改任务的要求。`
              : "本次解析与原任务的范围一致，重跑沿用同一个任务。",
        );
      } else {
        setGoalNotice("");
      }
      taskParams.applyInterpreted(data);
      setConstraintUse("pending");
      setInterpretedAtRun(latestRunId.current);
    },
  });
  const { interpretation, confirmed, setInstruction, setConfirmed } = session;

  // —— 面板 ——
  const [manualOpen, setManualOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [focusScope, setFocusScope] = useState(false);
  const openManual = useCallback((options: { focusScope?: boolean } = {}) => {
    setManualOpen(true);
    setDetailsOpen(true);
    if (options.focusScope) setFocusScope(true);
  }, []);
  const clearFocusScope = useCallback(() => setFocusScope(false), []);

  // —— 重置与 URL 对齐 ——
  const bindGoal = useCallback((id: string) => {
    setGoalId(id);
    setTrackGoalState(true);
    setTrackForced(false);
    setRestoredGoalId("");
  }, []);
  const bindRun = useCallback((id: string) => {
    setRunId(id);
    setCurrent(null);
  }, []);

  /** 回到排课助手首页：清空本会话的任务与草稿（在途的解析/建任务/求解结果一并丢弃）。 */
  const resetTask = () => {
    epoch.current += 1;
    clearHandoff();
    session.reset();
    setRunId("");
    setSessionRunId("");
    setCurrent(null);
    setInterpretedAtRun("");
    setConstraintUse("pending");
    setGoalId("");
    setGoalBusy(false);
    setGoalNotice("");
    setRestoredGoalId("");
    setSupplementing(false);
    setTrackGoalState(true);
    setTrackForced(false);
    setBaselineId("");
    setChangeLimitEnabled(false);
    setMaxChangesLimit(50);
    setDetailsOpen(false);
    setFocusScope(false);
    acceptanceWatch.current = null;
    taskParams.reset();
  };

  const { goalParam, runParam, actionParam, promptParam, manualParam, baseParam, lessonParam, updateSearch } = useTaskUrlSync({
    reset: resetTask,
    bindGoal,
    bindRun,
  });
  // 交接参数进入时记进状态、把所选版本设为基准版本。base/lesson 留在地址栏里，直到任务登记或求解开始
  // （之后由任务自己的上下文承载）——登记之前刷新页面，所选课次和版本还能恢复。
  useEffect(() => {
    if (!baseParam || !lessonParam) return;
    adoptHandoff({ scheduleId: baseParam, lessonId: lessonParam });
    setBaselineId(baseParam);
  }, [adoptHandoff, baseParam, lessonParam]);
  // 读到所选课次后，它就是任务的课次范围（与业务线/班级同一份草稿）：手动排课、加预算重跑、继续调整都沿用。
  const handoffReadyLesson = handoff?.status === "ready" ? handoff.target.lessonId : "";
  const { adoptLessons, commitLessonScope } = taskParams;
  useEffect(() => {
    if (handoffReadyLesson) adoptLessons([handoffReadyLesson]);
  }, [adoptLessons, handoffReadyLesson]);
  const dropHandoffParams = useCallback(() => updateSearch((next) => {
    next.delete("base");
    next.delete("lesson");
  }), [updateSearch]);
  // 原始基准只在任务还没有更新的工作草稿时显式带上：有了草稿，基准前进到它（后端也按「显式 > 工作草稿 > 记下的原始基准」选择）。
  const goalWorkDraft = parseGoalContext(goal?.context)?.work_draft_schedule_id;
  const explicitBase = handoff?.status === "ready" && !goalWorkDraft ? handoff.target.scheduleId : undefined;
  /**
   * 取消「只调整选中课次」的限定。求解前取消只是改主意，直接生效；已随求解提交过的限定被取消就是扩大范围，
   * 走范围扩大的单独确认，确认前保留交接对象（恢复原范围时还要用）。
   */
  const clearLessonScope = () => {
    if (taskParams.clearLessonScope()) return;
    clearHandoff();
    dropHandoffParams();
  };
  const confirmScopeExpansion = () => {
    if (taskParams.scopeExpansion?.course_business_ids) {
      clearHandoff();
      dropHandoffParams();
    }
    taskParams.confirmScopeExpansion();
  };
  useEffect(() => {
    if (manualParam) openManual();
  }, [manualParam, openManual]);

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
  const startedRun = useCallback((run: SolverRunResponse, via: "solve" | "manual") => {
    setRunId(run.id);
    setCurrent(run);
    setSessionRunId(run.id);
    // 课次限定随这次求解提交，成为已确认的范围；base/lesson 从地址栏摘掉，之后由任务上下文承载。
    // 交接对象本身不在这里清除：没产出草稿（超时/无解）的重试仍要用它；有草稿后基准才前进。
    commitLessonScope();
    if (via === "solve") {
      setConfirmed(true);
      setConstraintUse("submitted");
    } else {
      // 手动路径不代表用户确认了那份理解：确认卡和未落实要求继续可见，只记下这次没带上解析出的任务约束。
      setConstraintUse((previous) => (previous === "pending" ? "bypassed" : previous));
    }
    // 新一次求解替代了 URL 里指向的旧求解记录。
    updateSearch((next) => {
      next.delete("run");
      next.delete("base");
      next.delete("lesson");
    });
  }, [commitLessonScope, setConfirmed, updateSearch]);
  const submittedEpoch = useRef(0);
  const submit = useSubmitSolverRunApiV1SolverRunsPost({
    mutation: {
      onSuccess: (result) => {
        if (submittedEpoch.current !== epoch.current) return;
        startedRun(result, "manual");
        toast.success("求解任务已创建");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  const explain = useRunExplanation(current);
  const pending = submit.isPending || current?.status === "running";

  // —— 生命周期：URL → 状态 ——
  // 07 §4.4 续办恢复：不只绑 id——instruction 与 context.scope（范围/日期草稿）一并回填，基准默认
  // 该目标的工作草稿（§4.6）；context 为空的旧目标只回填 instruction，范围留空照旧。
  const restoreScope = taskParams.restoreScope;
  useEffect(() => {
    const detail = goalQuery.data as GoalDetailResponse | undefined;
    if (!detail || detail.id !== goalId || restoredGoalId === detail.id) return;
    setRestoredGoalId(detail.id);
    setInstruction(detail.instruction);
    const context = parseGoalContext(detail.context);
    const fromChecklist = scopeFromChecklist(detail);
    const scope = context?.scope
      ? { ...context.scope, course_business_ids: context.scope.course_business_ids ?? fromChecklist?.course_business_ids }
      : fromChecklist;
    if (scope) restoreScope(scope);
    // 已放弃：只回看，不接续（不回填基准、不自动落到最近一次求解、页面另有说明）。已达成的照常接续。
    if (isClosedGoal(detail.status)) return;
    if (context?.work_draft_schedule_id) setBaselineId(context.work_draft_schedule_id);
    // 没指定求解记录时，续办直接落在该任务最近一次求解的结果上。
    if (!runParam && detail.latest_run_id) setRunId((previous) => previous || (detail.latest_run_id as string));
    setGoalNotice(`已接着办这个任务「${truncateText(detail.instruction, 40)}」：${scope ? "范围与需求已恢复，" : ""}修正范围后重新排课，结果仍会核对同一批要求。`);
    toast.success("已接续该任务，求解结束后自动核对要求");
  }, [goalId, goalQuery.data, restoredGoalId, restoreScope, runParam, setInstruction]);

  const goalLoadFailed = Boolean(goalId) && Boolean(goalQuery.isError);
  const goalLoadError = goalLoadFailed ? errorMessage(goalQuery.error) : "";
  useEffect(() => {
    if (goalLoadFailed) toast.error(`关联任务失败：${goalLoadError}`);
  }, [goalLoadFailed, goalLoadError]);

  // 这次求解属于某个任务：顺手把任务也绑上，后续「继续调整」「补救」才能回灌同一任务。
  // 每个 run 链接只自动绑定一次：用户随后主动「清除关联」不能被这里立刻又绑回去。
  const linkedGoalId = current?.goal_id ?? "";
  const autoBoundRun = useRef("");
  useEffect(() => {
    if (!runParam) { autoBoundRun.current = ""; return; }
    // current 必须已经是 URL 指向的这条 run，否则读到的是切换前旧任务的 goal_id。
    if (current?.id !== runParam || autoBoundRun.current === runParam || goalParam || goalId || !linkedGoalId) return;
    autoBoundRun.current = runParam;
    bindGoal(linkedGoalId);
    updateSearch((next) => next.set("goal", linkedGoalId));
  }, [bindGoal, current?.id, goalId, goalParam, linkedGoalId, runParam, updateSearch]);

  // ?prompt= 预填需求（不自动提交）。
  const promptApplied = useRef("");
  useEffect(() => {
    if (!promptParam) { promptApplied.current = ""; return; }
    if (promptApplied.current === promptParam) return;
    promptApplied.current = promptParam;
    setInstruction(promptParam);
    updateSearch((next) => next.delete("prompt"));
  }, [promptParam, setInstruction, updateSearch]);

  // AI 未配置或探测失败时手动路径是唯一入口，默认展开（用户之后仍可自己收起）。
  const autoManual = useRef(false);
  useEffect(() => {
    if (autoManual.current || !(probe.ready === false || probe.probeError)) return;
    autoManual.current = true;
    setManualOpen(true);
  }, [probe.ready, probe.probeError]);

  // MEM-D2/D6：求解到终态后刷新课表/求解记录/总览缓存（带任务的还要刷新任务列表与详情，
  // 验收报告落库晚一步，报告到达也再刷一次）。
  useEffect(() => {
    const data = progress.data;
    if (!data) return;
    setCurrent(data);
    // 产出了草稿：基准前进到任务自己的工作草稿，最初交接的那一版不再作为显式基准（课次范围不受影响）。
    if (data.status === "completed" && !data.presolve_infeasible && (data.model_status === "OPTIMAL" || data.model_status === "FEASIBLE")) clearHandoff();
    if (data.status !== "completed" && data.status !== "failed") return;
    void Promise.all([
      client.invalidateQueries({ queryKey: getListSchedulesApiV1SchedulesGetQueryKey() }),
      client.invalidateQueries({ queryKey: getListSolverRunsApiV1SolverRunsGetQueryKey() }),
      client.invalidateQueries({ queryKey: getOverviewApiV1OverviewGetQueryKey() }),
      client.invalidateQueries({ queryKey: getListGoalsApiV1GoalsGetQueryKey() }),
      ...(data.goal_id ? [client.invalidateQueries({ queryKey: getGetGoalApiV1GoalsGoalIdGetQueryKey(data.goal_id) })] : []),
    ]);
  }, [clearHandoff, client, progress.data]);

  // —— 动作 ——
  /** 手动路径的统一提交：范围/日期/规则键全部来自共享参数草稿；任务已结束时不带 goal_id。 */
  const startManualRun = (nextParams: SolverParamValues) => {
    // 带着交接对象来的，读不到它就不能开始：不能悄悄退回「当前已发布版本 + 整批范围」。
    if (handoff && handoff.status !== "ready") {
      toast.error(`${handoff.description}可以先取消这一节课的限定，再按参数排课。`);
      return;
    }
    submittedEpoch.current = epoch.current;
    submit.mutate({
      // 课次范围随 nextParams 一并提交（加预算重跑同样沿用）；基准只在还没有更新的工作草稿时显式带上原始那一版。
      data: { ...nextParams, solver_rules: withSystemRules(nextParams.solver_rules), goal_id: solveGoalId || null, wait: false, ...(explicitBase ? { parent_schedule_id: explicitBase } : {}) },
    });
  };

  /** 手动排课：沿用原手动求解提交语义（goal_id、系统硬约束并入）。 */
  const submitManual = gated(() => startManualRun(params));

  /**
   * 加预算重跑只在两种情况下可用：已绑定任务且该任务的范围草稿已恢复；或这次求解就是本会话发起的
   * （草稿范围即它提交时的范围）。范围草稿要是没恢复、也不是本会话提交的，提交的就是默认全范围——
   * 悄悄改变排课范围；这类求解记录改走「按当前范围重新排课」，由界面明确展示将使用的范围。
   */
  const sessionRunOwnsDraft = !solveGoalId && Boolean(sessionRunId) && sessionRunId === runId;
  const canRaiseBudget = (Boolean(solveGoalId) && restoredGoalId === solveGoalId && !goalLoadFailed) || sessionRunOwnsDraft;
  const budgetBlockedByGoal = canRaiseBudget
    ? null
    : goalClosed
      ? "这个任务已放弃，不能再重跑；可以「按当前范围重新排课」。"
      : !goalId
        ? "这次求解没有关联任务，无法确认原来的排课范围；请先「修正范围」，或按当前范围重新排课。"
        : goalLoadFailed
          ? "任务没能读取，暂时无法恢复它的排课范围，请先重试读取任务。"
          : "正在恢复这个任务的排课范围，请稍候再试。";
  const raiseBudgetBlockedReason = submitBlockedReason ?? (pending ? "上一次求解还在进行中，结束后再重跑。" : budgetBlockedByGoal);
  /**
   * 07 §5.2 加预算重跑的独立提交函数：刻意不经过 solveFromInterpretation——该函数开头的
   * interpretation/unsupported 守卫在「刷新后的助手页、从旧链接进来」等无解析状态场景必然提前返回。
   * 这里直接复用手动求解提交路径：只把时限按 min(max(当前×3, 90), 900) 抬升，
   * 范围/日期/规则键全部沿用共享参数草稿（MEM-I2），不重新解析、不重选范围。
   */
  const raiseBudget = gated(() => {
    if (raiseBudgetBlockedReason) {
      toast.error(raiseBudgetBlockedReason);
      return;
    }
    const nextParams = { ...params, time_limit_seconds: raisedBudgetSeconds(params.time_limit_seconds) };
    setParams(nextParams);
    toast.success(`时间预算已加大至 ${nextParams.time_limit_seconds} 秒，正在重新提交求解`);
    startManualRun(nextParams);
  });

  // ?action=：等目标绑定与上下文回填完成后一次性执行，执行后摘掉参数，刷新不会重复提交。
  const actionHandled = useRef("");
  useEffect(() => {
    if (!actionParam) { actionHandled.current = ""; return; }
    if (actionHandled.current === actionParam) return;
    if (actionParam === "raise_budget") {
      if (goalParam && goalId !== goalParam) return; // URL 里的任务还没绑定上
      if (goalId && restoredGoalId !== goalId && !goalLoadFailed) return; // 范围草稿还在恢复
      actionHandled.current = actionParam;
      raiseBudget();
      updateSearch((next) => next.delete("action"));
      return;
    }
    if (actionParam === "resolve_scope") {
      // 聚焦范围区：先确保手动排课面板已展开，范围字段挂载后再滚动。
      actionHandled.current = actionParam;
      openManual({ focusScope: true });
      updateSearch((next) => next.delete("action"));
    }
  }, [actionParam, goalId, goalLoadFailed, goalParam, openManual, raiseBudget, restoredGoalId, updateSearch]);

  /**
   * 按解析结果登记持久目标（清单用后端预填草稿，前端可改的项暂不展开）。
   * 「确认并开始求解」与「补充条件」共用这一份，清单/范围口径不会各说各话。
   * 返回空串表示请求期间任务已被切走，调用方应直接放弃后续动作。
   */
  const createGoalFromInterpretation = async (parsed: Interpretation): Promise<string> => {
    const startedEpoch = epoch.current;
    // MEM-D3（基准联动）+ 问题4：基准版本默认只记录下来；只有用户明确开启「设置变更上限」
    // 才把 max_changes 项写进清单（上限取「验收上限」输入，默认 50），不用文案掩盖阈值的有无。
    const checklistDraft: GoalChecklistItem[] = [...(parsed.goal_checklist_draft ?? [])];
    if (baselineId && changeLimitEnabled && !checklistDraft.some((item) => item.kind === "max_changes")) {
      checklistDraft.push({
        key: "max_changes-baseline",
        kind: "max_changes",
        requirement: `相对所选基准版本的变更数 ≤ ${maxChangesLimit}（优化类目标：只设验收上限，「尽量少改」不升级为「绝不改」）`,
        params: { max_changes: maxChangesLimit, baseline_schedule_version_id: baselineId },
      });
    }
    const { data: created } = await http.post<{ id: string }>("/api/v1/goals", {
      instruction: parsed.instruction,
      // 问题2：目标清单与求解共用同一份手动草稿的范围口径，不会各说各话。
      business_lines: params.business_lines,
      product_types: params.product_types,
      class_business_ids: params.class_business_ids,
      course_business_ids: params.course_business_ids,
      date_from: params.date_from,
      date_to: params.date_to,
      checklist: checklistDraft,
      baseline_schedule_version_id: baselineId || null,
      forbid_publish: true,
    });
    if (startedEpoch !== epoch.current) return "";
    setGoalId(created.id);
    // 新建的任务不需要再用它自己的上下文覆盖当前草稿。
    setRestoredGoalId(created.id);
    // 07 §4.5：新建任务成功即把 goal_id 同步进 URL（replace 不新增历史记录），刷新/分享链接可回到
    // 同一任务上下文；updateSearch 同步前移 URL 基线，这次写入不会被当成外部导航而触发重置。
    updateSearch((next) => {
      next.set("goal", created.id);
      next.delete("action");
      // 任务登记后，课次范围与基准由任务上下文承载，不再靠地址栏里的交接参数。
      next.delete("base");
      next.delete("lesson");
    });
    setGoalNotice(`已登记为任务${baselineId ? (changeLimitEnabled ? "，按所选基准的变更数验收" : "，已记录基准版本用于变更对比（未设变更上限）") : ""}。`);
    return created.id;
  };

  /**
   * 有要求没能落实（主体/时段无法确认）时的出口：先登记任务，再在确认卡内行内补齐条件。
   * 任务要到点击「确认并开始求解」才会创建，而该按钮恰恰因这些要求被禁用，所以需要这个先手；
   * 已关联任务（续办）时直接展开，不重复创建。补充的条件写进任务，只有求解带着 goal_id 才会生效，
   * 所以「以此为目标跟踪」被关着时自动打开。
   */
  const startSupplement = async () => {
    if (!interpretation || goalBusy) return;
    try {
      setGoalBusy(true);
      if (!solveGoalId) {
        if (!(await createGoalFromInterpretation(interpretation))) return;
        toast.success("已登记任务，请补齐缺少的条件");
      }
      if (!trackGoal) {
        setTrackGoalState(true);
        setTrackForced(true);
      }
      setSupplementing(true);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setGoalBusy(false);
    }
  };

  const solveFromInterpretation = gated(async () => {
    if (!interpretation || interpretation.unsupported_requirements?.length) return;
    // 带着交接对象来的，读不到它就不能开始：不能悄悄退回「当前已发布版本 + 整批范围」。
    if (handoff && handoff.status !== "ready") {
      toast.error(`${handoff.description}可以先取消这一节课的限定，再按需求整体排课。`);
      return;
    }
    const startedEpoch = epoch.current;
    try {
      // 目标验收闭环（MEM-C3）：先按解析结果登记持久目标，再把 goal_id 带进求解任务；run 结束后自动验收。
      let activeGoalId = solveGoalId;
      if (trackGoal && !activeGoalId) {
        setGoalBusy(true);
        activeGoalId = await createGoalFromInterpretation(interpretation);
        if (!activeGoalId) return;
      }
      const { data } = await http.post<SolverRunResponse>("/api/v1/assistant/solve", {
        instruction: interpretation.instruction,
        // 问题2（单一数据源）：范围/日期/预算统一读手动参数草稿——确认卡与手动面板改的是同一份，
        // 两个入口发出的范围口径永远一致，改预算不动范围。
        business_lines: params.business_lines,
        product_types: params.product_types,
        class_business_ids: params.class_business_ids,
        date_from: params.date_from,
        date_to: params.date_to,
        date_window_days: params.date_window_days,
        solver_rules: interpretation.solver_rules,
        time_limit_seconds: params.time_limit_seconds,
        // 07 §6.5：任务级约束随请求体全量携带（软约束链的前端填充——goal.context.soft_task_constraints
        // 的唯一来源），与确认卡展示同源；hard 约束走清单编译（§2.4），请求体带全量由后端同键去重兜底。
        task_constraints: interpretation.task_constraints ?? [],
        // 课次范围来自共享参数草稿（课表交接 / 续办恢复）；基准只在还没有更新的工作草稿时显式带上原始那一版。
        course_business_ids: params.course_business_ids,
        ...(explicitBase ? { parent_schedule_id: explicitBase } : {}),
        goal_id: trackGoal ? activeGoalId : null,
        wait: false,
      });
      if (startedEpoch !== epoch.current) return;
      startedRun(data, "solve");
      toast.success(trackGoal ? "已开始排课，完成后会自动核对要求" : "确认完成，已开始排课");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setGoalBusy(false);
    }
  });

  const clearGoal = () => {
    setGoalId("");
    setGoalNotice("");
    setSupplementing(false);
    setRestoredGoalId("");
    // 07 §4.5：清除目标关联时把 goal 参数从 URL 一并删除（action 同步摘除）。
    updateSearch((next) => {
      next.delete("goal");
      next.delete("action");
    });
  };

  const taskMode = Boolean(goalParam || runParam || goalId || runId || current || interpretation);
  // 待确认的理解之后又发起过求解（手动路径）：确认卡和那次求解的进度/结果同时展示。
  const runSupersedesInterpretation = Boolean(interpretation) && !confirmed && Boolean(runId) && runId !== interpretedAtRun;
  /** 「继续调整」有没有可继承的上下文：没有任务、没有原始需求时只能重新提需求。 */
  const canRefine = Boolean(goal || session.originalInstruction || interpretation || session.instruction.trim());
  /** 「手动排课」入口：首页展开/收起面板；任务视图里面板在「查看详情」内，展开时连详情一起打开。 */
  const toggleManual = () => {
    if (manualOpen && (!taskMode || detailsOpen)) setManualOpen(false);
    else openManual();
  };
  return {
    // 视图
    taskMode,
    goalParam,
    runParam,
    probe,
    // 范围草稿
    ...taskParams,
    // 需求与解析
    instruction: session.instruction,
    editInstruction: session.editInstruction,
    originalInstruction: session.originalInstruction,
    interpretation,
    confirmed,
    phase: session.phase,
    stageIndex: session.stageIndex,
    liveThinking: session.liveThinking,
    elapsedSeconds: session.elapsedSeconds,
    parsedSeconds: session.parsedSeconds,
    interpretError: session.interpretError,
    interpret: session.interpret,
    cancelInterpret: session.cancelInterpret,
    retryInterpret: session.retryInterpret,
    refineInterpret: (extra: string, draftId?: string) => {
      if (draftId) setBaselineId(draftId);
      return session.refineInterpret(extra);
    },
    canRefine,
    applySuggestedInstruction: session.applySuggestedInstruction,
    refine: session.refine,
    setRefine: session.setRefine,
    runSupersedesInterpretation,
    /** 旧结果还在、但新的理解等着确认：进展条以「已理解（待确认）」为当前。 */
    awaitingReconfirmation: Boolean(interpretation) && !confirmed && Boolean(current) && !runSupersedesInterpretation,
    /** 解析出的任务约束没有随求解提交（走了手动路径），「本次要求」面板据此如实说明。 */
    constraintsWithheld: constraintUse === "bypassed",
    // 任务
    goalId,
    goal,
    goalClosed,
    goalNotice,
    goalBusy,
    goalLoadFailed,
    goalLoadError,
    retryGoal: () => void goalQuery.refetch(),
    trackGoal,
    setTrackGoal: (value: boolean) => {
      setTrackGoalState(value);
      setTrackForced(false);
    },
    trackForced,
    baselineId,
    setBaselineId,
    handoff,
    clearHandoff,
    changeLimitEnabled,
    setChangeLimitEnabled,
    maxChangesLimit,
    setMaxChangesLimit,
    supplementing,
    startSupplement,
    solveFromInterpretation,
    clearGoal,
    resetTask,
    // 求解
    runId,
    activeRun: current,
    runKind: classifyRun(current),
    runLoadFailed: Boolean(runId) && !current && Boolean(progress.isError),
    retryRun: () => void progress.refetch(),
    pending,
    /** 扩大范围待确认时的统一拦截原因；所有提交类按钮据此 disabled。 */
    submitBlockedReason,
    submitManual,
    canRaiseBudget,
    /** 加预算重跑当前不可用的原因（含扩大范围待确认）；null=可用。 */
    raiseBudgetBlockedReason,
    raiseBudget,
    explain,
    // 面板
    manualOpen,
    setManualOpen,
    detailsOpen,
    setDetailsOpen,
    openManual,
    toggleManual,
    focusScope,
    clearFocusScope,
    // 覆盖 ...taskParams 里的同名入口：课次限定的取消/确认还要同步交接状态与地址栏。
    clearLessonScope,
    confirmScopeExpansion,
  };
}

export type AssistantTask = ReturnType<typeof useAssistantTask>;
