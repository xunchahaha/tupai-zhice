import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SolverPage } from "@/pages/solver-page";

const mocks = vi.hoisted(() => ({
  runs: [] as Record<string, unknown>[],
  schedules: [] as Record<string, unknown>[],
  courses: [] as Record<string, unknown>[],
  diff: vi.fn<(...args: unknown[]) => { data: undefined }>(() => ({ data: undefined })),
  get: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => ({ data: { configured: false, app_configuration: { aily_configured: false } } })),
  post: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => ({ data: {} })),
  submitMutate: vi.fn(),
  stream: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => {
    throw new Error("stream unavailable");
  }),
}));

vi.mock("@/api/http", () => ({ http: { get: mocks.get, post: mocks.post } }));

// 流式通道单独 mock：页面默认走 stream，失败时才回退 http.post。
vi.mock("@/lib/interpret-stream", () => ({ streamInterpretInstruction: mocks.stream }));

vi.mock("@/api/generated/client", () => ({
  getListSchedulesApiV1SchedulesGetQueryKey: () => ["schedules"],
  getListSolverRunsApiV1SolverRunsGetQueryKey: () => ["runs"],
  getOverviewApiV1OverviewGetQueryKey: () => ["overview"],
  useListRulesApiV1RulesGet: () => ({ data: [] }),
  useListCourseSessionsApiV1CourseSessionsGet: () => ({ data: mocks.courses }),
  useListSchedulesApiV1SchedulesGet: () => ({ data: mocks.schedules }),
  useListSolverRunsApiV1SolverRunsGet: () => ({ data: mocks.runs }),
  useGetScheduleApiV1SchedulesScheduleIdGet: () => ({ data: undefined }),
  useGetSolverRunApiV1SolverRunsRunIdGet: () => ({ data: undefined }),
  useSubmitSolverRunApiV1SolverRunsPost: () => ({ mutate: mocks.submitMutate }),
  useDiffSchedulesApiV1SchedulesScheduleIdDiffTargetScheduleIdGet: (...args: unknown[]) => mocks.diff(...args),
}));

/** 路由探针：断言 §4.5 的 URL 同步（goal 写入/删除、action 摘除）。 */
function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{location.pathname}{location.search}</div>;
}

function renderPage(initialEntry = "/solver") {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/solver" element={<SolverPage />} />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockAiConfigured() {
  mocks.get.mockResolvedValue({ data: { configured: true, model: "text-model", app_configuration: { aily_configured: false } } });
}

/** 目标详情走路由返回；其余 GET（AI/飞书探测）按 aiConfigured 应答。 */
function mockGoalRoute(goal: Record<string, unknown>, aiConfigured = false) {
  mocks.get.mockImplementation(async (...args: unknown[]) => {
    const url = String(args[0]);
    if (url === `/api/v1/goals/${goal.id}`) return { data: goal };
    return { data: { configured: aiConfigured, model: aiConfigured ? "text-model" : null, app_configuration: { aily_configured: false } } };
  });
}

const taskConstraints = [
  { id: "tc-1", source_text: "张老师周三晚上不能上", subject_type: "teacher", subject_ids: ["T01"], slot_business_ids: ["S05", "S06"], hardness: "hard" },
  { id: "tc-2", source_text: "李老师周三晚尽量别排", subject_type: "teacher", subject_ids: ["T02"], slot_business_ids: ["S05"], hardness: "soft" },
];

const memoryReceipts = [
  { action_id: "ma-1", status: "executed", entry_id: "pe-1", receipt: "已记住：张老师 周三晚尽量不排（有效期至 2027-01-31）。可在「记忆」页修改或撤销。" },
  { action_id: "ma-2", status: "pending_confirmation", entry_id: null, receipt: "推测到偏好：李老师好像不太愿意上晚间。" },
];

const goalContext = {
  schema_version: 1,
  scope: {
    business_lines: ["考研"],
    product_types: ["暑期集训"],
    class_business_ids: ["B01"],
    date_from: "2026-09-28",
    date_to: "2026-10-04",
    date_window_days: 3,
  },
  soft_task_constraints: [taskConstraints[1]],
  work_draft_schedule_id: "sched-draft-9",
};

function interpretationFixture(overrides: Record<string, unknown> = {}) {
  return {
    instruction: "请在三天内重排考研课程",
    source: "openai_compatible",
    ai_configured: true,
    aily_configured: false,
    business_lines: ["考研"],
    product_types: ["暑期集训"],
    class_business_ids: ["B01"],
    date_from: "2026-09-28",
    date_to: "2026-10-04",
    date_window_days: 3,
    recognized_rules: ["固定时段不可调整"],
    solver_rules: ["fixed_time"],
    unsupported_requirements: [],
    coverage_warnings: [],
    summary: "已解析排课范围",
    thinking: "先核对候选。",
    ...overrides,
  };
}

function goalFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: "goal-77",
    status: "open",
    instruction: "重排 B01 班一周课表，避开周三晚间",
    checklist: [],
    ...overrides,
  };
}

describe("SolverPage task constraints & memory receipts (TC-6 §6.1)", () => {
  afterEach(cleanup);
  beforeEach(() => {
    mocks.diff.mockClear();
    mocks.runs = [];
    mocks.schedules = [];
    mocks.courses = [];
    mocks.submitMutate.mockClear();
    mocks.get.mockReset().mockResolvedValue({ data: { configured: false, model: null, app_configuration: { aily_configured: false } } });
    mocks.post.mockReset().mockResolvedValue({ data: {} });
    mocks.stream.mockReset().mockRejectedValue(new Error("stream unavailable"));
  });

  it("renders task constraints with hard/soft badges and the scope-only note on the confirmation card", async () => {
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture({ task_constraints: taskConstraints }));
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    const region = await screen.findByLabelText("本次任务要求");
    // 固定文案：仅作用于本次任务，不进入规则库。
    expect(within(region).getByText("仅作用于本次任务，不进入规则库")).toBeInTheDocument();
    // hard/soft 逐条展示：原话 + 徽标 + 主体×时段。
    expect(within(region).getByText("张老师周三晚上不能上")).toBeInTheDocument();
    expect(within(region).getByText("李老师周三晚尽量别排")).toBeInTheDocument();
    expect(within(region).getByText("硬约束")).toBeInTheDocument();
    expect(within(region).getByText("软约束")).toBeInTheDocument();
    expect(within(region).getByText("教师 T01 × 时段 S05、S06")).toBeInTheDocument();
    expect(within(region).getByText("教师 T02 × 时段 S05")).toBeInTheDocument();
  });

  it("renders memory action receipts: executed with the undo hint, pending with the inbox note", async () => {
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture({ memory_action_receipts: memoryReceipts }));
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    const region = await screen.findByLabelText("记忆动作回执");
    // executed：徽标 + 回执文案（含「可在记忆页修改或撤销」的修改/撤销入口提示）。
    expect(within(region).getByText("已执行")).toBeInTheDocument();
    expect(within(region).getByText(/可在「记忆」页修改或撤销/)).toBeInTheDocument();
    // pending_confirmation：黄色徽标 + 收件箱待确认提示。
    expect(within(region).getByText("待确认")).toBeInTheDocument();
    expect(within(region).getByText("已放入记忆收件箱待确认")).toBeInTheDocument();
  });

  it("keeps the confirmation card clean when no task constraints or receipts are present", async () => {
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture());
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    expect(screen.queryByLabelText("本次任务要求")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("记忆动作回执")).not.toBeInTheDocument();
  });
});

describe("SolverPage solve request contract (TC-6 §6.5/§6.4)", () => {
  afterEach(cleanup);
  beforeEach(() => {
    mocks.diff.mockClear();
    mocks.runs = [];
    mocks.schedules = [];
    mocks.courses = [];
    mocks.submitMutate.mockClear();
    mocks.get.mockReset().mockResolvedValue({ data: { configured: false, model: null, app_configuration: { aily_configured: false } } });
    mocks.post.mockReset().mockResolvedValue({ data: {} });
    mocks.stream.mockReset().mockRejectedValue(new Error("stream unavailable"));
  });

  it("carries the full task_constraints list (hard + soft) in the /assistant/solve request body", async () => {
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture({ task_constraints: taskConstraints }));
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      const url = String(args[0]);
      if (url === "/api/v1/goals") return { data: { id: "goal-new-1", status: "open", checklist: [] } };
      return { data: { id: "run-tc", status: "queued", model_status: null } };
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    await screen.findByText(/已解析完成（用时/);
    await user.click(screen.getByRole("button", { name: /确认并开始求解/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    const [solveUrl, solveBody] = mocks.post.mock.calls[1] as [string, Record<string, unknown>];
    expect(solveUrl).toBe("/api/v1/assistant/solve");
    // 软约束链前端填充（§6.5）：请求体带全量 task_constraints（hard+soft），与确认卡同源。
    expect(solveBody.task_constraints).toEqual(taskConstraints);
    expect(solveBody.goal_id).toBe("goal-new-1");
    // §4.5：新建目标成功即把 goal_id 同步进 URL（无残留 action 参数）。
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/solver\?goal=goal-new-1$/));
  });

  it("sends goal_id through the streaming channel when a goal is bound (§6.4)", async () => {
    mockGoalRoute(goalFixture({ context: goalContext }), true);
    mocks.stream.mockResolvedValue(interpretationFixture());
    const user = userEvent.setup();
    renderPage("/solver?goal=goal-77");
    expect(await screen.findByText(/已关联目标 #goal-77/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "让 AI 解析排课指令" }));
    await waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(1));
    const streamArgs = mocks.stream.mock.calls[0] as unknown[];
    // 流式主路径第 4 个参数即 goalId（§6.4 必改点）。
    expect(streamArgs[3]).toBe("goal-77");
  });

  it("omits goal_id on the streaming channel when no goal is bound (§6.4)", async () => {
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture());
    const user = userEvent.setup();
    renderPage("/solver");
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    await waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(1));
    const streamArgs = mocks.stream.mock.calls[0] as unknown[];
    expect(streamArgs[3]).toBeUndefined();
  });

  it("carries goal_id in the sync fallback body when streaming fails (§6.4)", async () => {
    mockGoalRoute(goalFixture({ context: goalContext }), true);
    mocks.stream.mockRejectedValue(new Error("stream unavailable"));
    mocks.post.mockResolvedValue({ data: interpretationFixture() });
    const user = userEvent.setup();
    renderPage("/solver?goal=goal-77");
    expect(await screen.findByText(/已关联目标 #goal-77/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "让 AI 解析排课指令" }));
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    const fallbackCall = mocks.post.mock.calls.find((call) => call[0] === "/api/v1/assistant/interpret") as unknown[] | undefined;
    expect(fallbackCall).toBeDefined();
    // 同步回退与流式主路径口径一致（同样带 goal_id）。
    expect(fallbackCall![1]).toEqual({ instruction: expect.any(String), goal_id: "goal-77" });
  });
});

describe("SolverPage resume restore (TC-6 §4.4/§4.5)", () => {
  afterEach(cleanup);
  beforeEach(() => {
    mocks.diff.mockClear();
    mocks.runs = [];
    mocks.schedules = [];
    mocks.courses = [];
    mocks.submitMutate.mockClear();
    mocks.get.mockReset().mockResolvedValue({ data: { configured: false, model: null, app_configuration: { aily_configured: false } } });
    mocks.post.mockReset().mockResolvedValue({ data: {} });
    mocks.stream.mockReset().mockRejectedValue(new Error("stream unavailable"));
  });

  it("restores instruction, context scope and the work-draft baseline when resuming /solver?goal=", async () => {
    mockGoalRoute(goalFixture({ context: goalContext }), true);
    // 基准下拉需要能命中工作草稿选项；范围下拉需要命中业务线/班级候选。
    mocks.schedules = [{ id: "sched-draft-9", status: "draft", solver_run_id: "run-draft", version_no: 2, name: "工作草稿" }];
    mocks.courses = [{ id: "cs-1", business_line: "考研", class_business_id: "B01", lesson_date: "2026-09-28" }];
    mocks.stream.mockResolvedValue(interpretationFixture());
    const user = userEvent.setup();
    renderPage("/solver?goal=goal-77");
    expect(await screen.findByText(/已关联目标 #goal-77/)).toBeInTheDocument();
    // instruction 回填（不只绑 id），提示条说明上下文已恢复（解析后会换成沿用目标文案）。
    expect(screen.getByLabelText("一句话排课指令")).toHaveValue("重排 B01 班一周课表，避开周三晚间");
    expect(screen.getByText(/已恢复该目标的范围与指令上下文/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "让 AI 解析排课指令" }));
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    // context.scope 回填共享参数草稿。
    expect(screen.getByLabelText("业务线")).toHaveValue("考研");
    expect(screen.getByLabelText("班级")).toHaveValue("B01");
    expect(screen.getByDisplayValue("2026-09-28")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2026-10-04")).toBeInTheDocument();
    // §4.6 前端口径：基准默认该目标正在调整的工作草稿。
    expect(screen.getByLabelText("基准版本")).toHaveValue("sched-draft-9");
  });

  it("keeps old goals without context usable: only instruction is restored", async () => {
    mockGoalRoute(goalFixture());
    const user = userEvent.setup();
    renderPage("/solver?goal=goal-77");
    expect(await screen.findByText(/已关联目标 #goal-77/)).toBeInTheDocument();
    expect(screen.getByLabelText("一句话排课指令")).toHaveValue("重排 B01 班一周课表，避开周三晚间");
    // context 为空 → 范围留空照旧（AI 未配置时手动面板直接可见）。
    expect(await screen.findByText("手动求解参数")).toBeInTheDocument();
    expect(screen.getByLabelText("业务线")).toHaveValue("");
    expect(screen.getByLabelText("班级")).toHaveValue("");
  });

  it("removes the goal param from the URL when the association is cleared (§4.5)", async () => {
    mockGoalRoute(goalFixture());
    const user = userEvent.setup();
    renderPage("/solver?goal=goal-77");
    expect(await screen.findByText(/已关联目标 #goal-77/)).toBeInTheDocument();
    expect(screen.getByTestId("location-probe")).toHaveTextContent("goal=goal-77");
    await user.click(screen.getByRole("button", { name: "清除目标关联" }));
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/solver$/));
  });
});

describe("SolverPage budget retry (TC-6 §5.2)", () => {
  afterEach(cleanup);
  beforeEach(() => {
    mocks.diff.mockClear();
    mocks.runs = [];
    mocks.schedules = [];
    mocks.courses = [];
    mocks.submitMutate.mockClear();
    mocks.get.mockReset().mockResolvedValue({ data: { configured: false, model: null, app_configuration: { aily_configured: false } } });
    mocks.post.mockReset().mockResolvedValue({ data: {} });
    mocks.stream.mockReset().mockRejectedValue(new Error("stream unavailable"));
  });

  it("auto-submits a raised budget on mount when action=raise_budget, bypassing the interpretation guard", async () => {
    mockGoalRoute(goalFixture({ context: goalContext }));
    const user = userEvent.setup();
    renderPage("/solver?goal=goal-77&action=raise_budget");
    // 无任何解析状态（AI 未配置、从未解析）也能提交——不经过 solveFromInterpretation
    // 的 :447 守卫，直接复用手动求解提交路径（§5.2 评审指认的机制修正）。
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    expect(mocks.stream).not.toHaveBeenCalled();
    const submitted = mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> };
    // 时限 30 → max(30×3, 90) = 90；范围来自 context.scope 回填，未被重选。
    expect(submitted.data.time_limit_seconds).toBe(90);
    expect(submitted.data.goal_id).toBe("goal-77");
    expect(submitted.data.business_lines).toEqual(["考研"]);
    expect(submitted.data.class_business_ids).toEqual(["B01"]);
    expect(submitted.data.date_from).toBe("2026-09-28");
    expect(submitted.data.date_to).toBe("2026-10-04");
    expect(submitted.data.solver_rules).toEqual(expect.arrayContaining(["room_no_overlap", "teacher_no_overlap"]));
    // action 参数执行后摘除：刷新不会重复提交。
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/solver\?goal=goal-77$/));
  });

  it("raises the budget from the report remedy button and caps the limit at 900", async () => {
    mockGoalRoute(goalFixture({ id: "goal-9" }));
    mocks.runs = [{
      id: "run-goal",
      status: "completed",
      model_status: "OPTIMAL",
      goal_id: "goal-9",
      goal_report: {
        goal_id: "goal-9",
        instruction: "重排 B1 班三天课",
        all_passed: false,
        passed_count: 1,
        failed_count: 1,
        items: [{ key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", passed: false, detail: "缺 1 个课次" }],
        gaps: [{ key: "coverage", kind: "coverage", summary: "求解未安置全部课次", next_step: "加大时间预算后重跑", remedy: "raise_budget" }],
        decision: { status: "awaiting_decision", reason: "1 项未通过" },
      },
    }];
    const user = userEvent.setup();
    renderPage("/solver?goal=goal-9");
    expect(await screen.findByText(/已关联目标 #goal-9/)).toBeInTheDocument();
    const remedyButton = await screen.findByRole("button", { name: "加大时间预算重跑" });
    // 先把时限改到 350：350×3=1050 → 上限 900。
    const timeInput = screen.getByLabelText("求解时限（秒）");
    await user.clear(timeInput);
    await user.type(timeInput, "350");
    await user.click(remedyButton);
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    const submitted = mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> };
    expect(submitted.data.time_limit_seconds).toBe(900);
    expect(submitted.data.goal_id).toBe("goal-9");
    // 只加预算不重选范围：范围字段保持原状。
    expect(submitted.data.business_lines).toEqual([]);
    expect(submitted.data.date_from).toBeNull();
  });
});

describe("SolverPage exit for requirements that could not be resolved", () => {
  afterEach(cleanup);
  beforeEach(() => {
    mocks.diff.mockClear();
    mocks.runs = [];
    mocks.schedules = [];
    mocks.courses = [];
    mocks.submitMutate.mockClear();
    mocks.get.mockReset().mockResolvedValue({ data: { configured: false, model: null, app_configuration: { aily_configured: false } } });
    mocks.post.mockReset().mockResolvedValue({ data: {} });
    mocks.stream.mockReset().mockRejectedValue(new Error("stream unavailable"));
  });

  const placeholder = {
    key: "forbidden_slot_free-draft",
    kind: "forbidden_slot_free",
    requirement: "禁排要求待量化：补充主体与具体时段后才能独立复核",
    params: { subject_type: "teacher", subject_ids: [], slot_business_ids: [], needs_params: true },
  };

  it("registers the goal with its to-be-quantified item and jumps to that goal, without starting a solve", async () => {
    // 目标要到点击「确认并开始求解」才会创建，而该按钮恰恰因未落实的要求被禁用——
    // 没有这个出口，「到目标清单补齐参数」的提示对新任务不可达。
    mockAiConfigured();
    mocks.stream.mockResolvedValue(
      interpretationFixture({
        unsupported_requirements: ["具体教师的禁排或请假要求"],
        goal_checklist_draft: [
          { key: "coverage", kind: "coverage", requirement: "覆盖全部目标课次", params: {} },
          placeholder,
        ],
      }),
    );
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      if (String(args[0]) === "/api/v1/goals") return { data: { id: "goal-later", status: "open", checklist: [] } };
      return { data: {} };
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    await screen.findByText(/已解析完成（用时/);
    // 未落实的要求仍然拦住求解。
    expect(screen.getByRole("button", { name: /确认并开始求解/ })).toBeDisabled();
    expect(screen.getByText(/可以「登记为目标，稍后补充」/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "登记为目标，稍后补充" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    const [url, body] = mocks.post.mock.calls[0] as [string, { checklist: Array<{ key: string }>; instruction: string }];
    expect(url).toBe("/api/v1/goals");
    expect(body.instruction).toBe("请在三天内重排考研课程");
    // 待量化占位项随目标落库——补参后验收器才有东西可核对。
    expect(body.checklist.map((item) => item.key)).toEqual(["coverage", "forbidden_slot_free-draft"]);
    await waitFor(() => expect(screen.getAllByTestId("location-probe")[0]).toHaveTextContent(/^\/goals\?goal=goal-later$/));
    // 没有发起任何求解。
    expect(mocks.post.mock.calls.some((call) => String(call[0]) === "/api/v1/assistant/solve")).toBe(false);
  });

  it("does not offer a fake exit for requirements that cannot be quantized (room / consecutive)", async () => {
    mockAiConfigured();
    mocks.stream.mockResolvedValue(
      interpretationFixture({
        unsupported_requirements: ["指定教室要求"],
        goal_checklist_draft: [{ key: "coverage", kind: "coverage", requirement: "覆盖全部目标课次", params: {} }],
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    await screen.findByText(/已解析完成（用时/);
    expect(screen.getByRole("button", { name: /确认并开始求解/ })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "登记为目标，稍后补充" })).not.toBeInTheDocument();
    expect(screen.getByText(/请修改指令去掉这些要求后重新解析/)).toBeInTheDocument();
  });

  it("goes straight to the bound goal instead of creating another one when resuming", async () => {
    mockGoalRoute(goalFixture({ context: goalContext }), true);
    mocks.stream.mockResolvedValue(
      interpretationFixture({
        unsupported_requirements: ["具体教师的禁排或请假要求"],
        goal_checklist_draft: [placeholder],
      }),
    );
    const user = userEvent.setup();
    renderPage("/solver?goal=goal-77");
    expect(await screen.findByText(/已关联目标 #goal-77/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "让 AI 解析排课指令" }));
    await screen.findByText(/已解析完成（用时/);
    await user.click(screen.getByRole("button", { name: "前往目标跟踪补充" }));
    await waitFor(() => expect(screen.getAllByTestId("location-probe")[0]).toHaveTextContent(/^\/goals\?goal=goal-77$/));
    expect(mocks.post).not.toHaveBeenCalled();
  });
});
