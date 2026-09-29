import { vi } from "vitest";

/**
 * 排课助手页测试共用的接口 mock：页面要用到的 orval 钩子、http、流式解析统一在这里，
 * 各测试文件只需 `vi.mock(路径, async () => (await import("./support/assistant-mocks")).xxxMock)`
 * 并读写 assistantMocks 上的数据。本模块不能 import 页面（vi.mock 工厂会反向 import 它，会成环）。
 */
type Rec = Record<string, unknown>;

export const assistantMocks = {
  rules: [] as Rec[],
  courses: [] as Rec[],
  runs: [] as Rec[],
  schedules: [] as Rec[],
  goals: [] as Rec[],
  /** 数据概览的统计（useOverviewAnalytics…）；不设时保持「读取中」。 */
  analytics: undefined as Rec | undefined,
  /** 任务详情（useGetGoal…），只在 id 命中时返回。 */
  goalDetail: undefined as Rec | undefined,
  /** 多个任务详情按 id 取（A→B 切换类用例）；优先于 goalDetail。 */
  goalDetails: {} as Record<string, Rec>,
  /** 设置后，手动提交（useSubmitSolverRun…）会同步走 onSuccess，就像接口已返回这条 run。 */
  submitResult: undefined as Rec | undefined,
  /** 轮询钩子按 run id 返回的详情。 */
  runDetails: {} as Record<string, Rec>,
  diff: vi.fn<(...args: unknown[]) => { data?: unknown; isLoading?: boolean; isError?: boolean }>(() => ({ data: undefined })),
  submitMutate: vi.fn(),
  publishMutate: vi.fn(),
  abandonMutate: vi.fn(),
  checklistPatch: vi.fn(),
  overviewHook: vi.fn(),
  get: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => ({ data: { configured: false, app_configuration: { aily_configured: false } } })),
  post: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => ({ data: {} })),
  stream: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => {
    throw new Error("stream unavailable");
  }),
};

/** 默认：AI 未配置、无数据、流式不可用。每个用例 beforeEach 调用一次。 */
export function resetAssistantMocks() {
  const m = assistantMocks;
  m.rules = [];
  m.courses = [];
  m.runs = [];
  m.schedules = [];
  m.goals = [];
  m.analytics = undefined;
  m.goalDetail = undefined;
  m.goalDetails = {};
  m.submitResult = undefined;
  m.runDetails = {};
  m.diff.mockReset().mockReturnValue({ data: undefined });
  m.submitMutate.mockReset();
  m.publishMutate.mockReset();
  m.abandonMutate.mockReset();
  m.checklistPatch.mockReset();
  m.overviewHook.mockReset();
  m.get.mockReset().mockResolvedValue({ data: { configured: false, model: null, app_configuration: { aily_configured: false } } });
  m.post.mockReset().mockResolvedValue({ data: {} });
  m.stream.mockReset().mockRejectedValue(new Error("stream unavailable"));
}

/** 探测接口返回 AI 已配置（两个探测 GET 共用一份返回，各自只取自己要的字段）。 */
export function mockAiConfigured() {
  assistantMocks.get.mockResolvedValue({ data: { configured: true, model: "text-model", app_configuration: { aily_configured: false } } });
}

const query = (data: unknown) => ({ data, isPending: false, isError: false, isLoading: false, refetch: vi.fn() });

export const clientMock = {
  getListSchedulesApiV1SchedulesGetQueryKey: () => ["schedules"],
  getListSolverRunsApiV1SolverRunsGetQueryKey: () => ["runs"],
  getOverviewApiV1OverviewGetQueryKey: () => ["overview"],
  getListGoalsApiV1GoalsGetQueryKey: () => ["goals"],
  getGetGoalApiV1GoalsGoalIdGetQueryKey: (goalId?: string) => ["goal-detail", goalId],
  getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey: () => ["feishu-syncs"],
  useListRulesApiV1RulesGet: () => query(assistantMocks.rules),
  useListCourseSessionsApiV1CourseSessionsGet: () => query(assistantMocks.courses),
  useListSchedulesApiV1SchedulesGet: () => query(assistantMocks.schedules),
  useListSolverRunsApiV1SolverRunsGet: () => query(assistantMocks.runs),
  useListGoalsApiV1GoalsGet: () => query(assistantMocks.goals),
  useGetScheduleApiV1SchedulesScheduleIdGet: () => ({ data: undefined }),
  useGetSolverRunApiV1SolverRunsRunIdGet: (runId: string, options?: { query?: { enabled?: boolean } }) => ({
    ...query(options?.query?.enabled && runId ? assistantMocks.runDetails[runId] : undefined),
  }),
  useGetGoalApiV1GoalsGoalIdGet: (goalId: string, options?: { query?: { enabled?: boolean } }) => {
    const detail = assistantMocks.goalDetails[goalId] ?? (assistantMocks.goalDetail?.id === goalId ? assistantMocks.goalDetail : undefined);
    return query(options?.query?.enabled && detail ? detail : undefined);
  },
  useSubmitSolverRunApiV1SolverRunsPost: (config?: { mutation?: { onSuccess?: (data: unknown) => void } }) => ({
    mutate: (variables: unknown) => {
      assistantMocks.submitMutate(variables);
      if (assistantMocks.submitResult) config?.mutation?.onSuccess?.(assistantMocks.submitResult);
    },
    isPending: false,
  }),
  useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet: (...args: unknown[]) => ({
    isLoading: false,
    isError: false,
    ...assistantMocks.diff(...args),
  }),
  usePublishScheduleApiV1SchedulesScheduleIdPublishPost: (config?: { mutation?: { onSuccess?: () => void } }) => ({
    mutate: (variables: unknown) => {
      assistantMocks.publishMutate(variables);
      config?.mutation?.onSuccess?.();
    },
    isPending: false,
  }),
  useAbandonGoalApiV1GoalsGoalIdAbandonPost: (config?: { mutation?: { onSuccess?: () => void } }) => ({
    mutate: (variables: unknown) => {
      assistantMocks.abandonMutate(variables);
      config?.mutation?.onSuccess?.();
    },
    isPending: false,
  }),
  useReplaceGoalChecklistApiV1GoalsGoalIdChecklistPatch: (config?: { mutation?: { onSuccess?: (data: unknown) => void } }) => ({
    mutate: (variables: unknown) => {
      assistantMocks.checklistPatch(variables);
      config?.mutation?.onSuccess?.({ checklist_version: 2 });
    },
    isPending: false,
  }),
  useListTeachersApiV1TeachersGet: () => ({ data: [{ id: "t1", business_id: "T9", name: "教师九" }] }),
  useListClassGroupsApiV1ClassGroupsGet: () => ({ data: [{ id: "c1", business_id: "B1", name: "B1 班" }] }),
  useListRoomsApiV1RoomsGet: () => ({ data: [{ id: "r1", business_id: "R1", name: "教室1" }] }),
  useListTimeSlotsApiV1TimeSlotsGet: () => ({
    data: [
      { id: "s1", business_id: "S1", weekday: "周一", start_time: "08:30", end_time: "11:30" },
      { id: "s2", business_id: "S2", weekday: "周二", start_time: "08:30", end_time: "11:30" },
    ],
  }),
  useOverviewApiV1OverviewGet: () => {
    assistantMocks.overviewHook();
    return query({ counts: { teachers: 4, class_groups: 36, rooms: 16, course_sessions: 4544 }, latest_run: null, latest_schedule: null });
  },
  useOverviewAnalyticsApiV1OverviewAnalyticsGet: () => ({ data: assistantMocks.analytics, isPending: !assistantMocks.analytics, isError: false, refetch: vi.fn() }),
};

export const httpMock = { http: { get: assistantMocks.get, post: assistantMocks.post } };
export const interpretStreamMock = { streamInterpretInstruction: assistantMocks.stream };

// —— 夹具 ——
export function interpretationFixture(overrides: Rec = {}) {
  return {
    instruction: "请在三天内重排考研课程",
    source: "openai_compatible",
    ai_configured: true,
    aily_configured: false,
    business_lines: ["考研"],
    product_types: [],
    class_business_ids: [],
    date_from: "2026-08-17",
    date_to: "2026-08-19",
    date_window_days: 3,
    recognized_rules: ["固定时段不可调整"],
    solver_rules: ["fixed_time"],
    unsupported_requirements: [],
    coverage_warnings: [],
    summary: "已解析排课范围",
    thinking: "用户要求 3 天窗口，先核对候选业务线。",
    ...overrides,
  };
}

export function goalFixture(overrides: Rec = {}) {
  return {
    id: "goal-77",
    schedule_set_id: "s1",
    instruction: "重排 B01 班一周课表，避开周三晚间",
    checklist: [],
    checklist_version: 1,
    checklist_history: [],
    status: "open",
    acceptance_status: "completed",
    latest_run_id: null,
    run_count: 0,
    runs: [],
    latest_report: null,
    created_at: "2026-09-20T08:00:00+08:00",
    updated_at: "2026-09-20T09:00:00+08:00",
    ...overrides,
  };
}

export function runFixture(overrides: Rec = {}) {
  return {
    id: "run-1",
    status: "completed",
    model_status: "OPTIMAL",
    presolve_infeasible: false,
    conflict_rule_ids: [],
    priority_rule_ids: [],
    priority_explanations: [],
    objective_value: 1,
    best_bound: 1,
    wall_time_seconds: 1.2,
    created_at: "2026-09-20T09:00:00+08:00",
    ...overrides,
  };
}

export function scheduleFixture(overrides: Rec = {}) {
  return {
    id: "draft-1",
    version_no: 2,
    name: "9 月排课草稿",
    status: "draft",
    parent_id: "pub-1",
    solver_run_id: "run-1",
    assignment_count: 12,
    created_at: "2026-09-20T09:00:00+08:00",
    ...overrides,
  };
}

export const PLACEHOLDER_ITEM = {
  key: "forbidden_slot_free-draft",
  kind: "forbidden_slot_free",
  requirement: "禁排要求待量化：补充主体与具体时段后才能独立复核",
  params: { subject_type: "teacher", subject_ids: [], slot_business_ids: [], needs_params: true },
};

export const TASK_CONSTRAINTS = [
  { id: "tc-1", source_text: "张老师周三晚上不能上", subject_type: "teacher", subject_ids: ["T01"], slot_business_ids: ["S05", "S06"], hardness: "hard" },
  { id: "tc-2", source_text: "李老师周三晚尽量别排", subject_type: "teacher", subject_ids: ["T02"], slot_business_ids: ["S05"], hardness: "soft" },
];
