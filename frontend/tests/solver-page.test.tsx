import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act, type ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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

/** 探测接口返回 AI 已配置（Promise.all 的两个 get 共用一份返回，各自只取自己要的字段）。 */
function mockAiConfigured() {
  mocks.get.mockResolvedValue({ data: { configured: true, model: "text-model", app_configuration: { aily_configured: false } } });
}

function mockInterpretation() {
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
  };
}

function renderPage(initialEntry = "/solver", router: ReactNode | null = null) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {router ?? <MemoryRouter initialEntries={[initialEntry]}><SolverPage /></MemoryRouter>}
  </QueryClientProvider>);
}

describe("SolverPage candidate ownership", () => {
  afterEach(cleanup);
  beforeEach(() => {
    mocks.diff.mockClear();
    mocks.schedules = [{ id: "old", status: "draft", solver_run_id: "old-run", version_no: 1, name: "旧候选" }];
  });
  it.each(["INFEASIBLE", "UNKNOWN"])("clears an old candidate after a completed %s task", async (model_status) => {
    mocks.runs = [{ id: "old-run", status: "completed", model_status: "OPTIMAL" }];
    const page = renderPage();
    await screen.findByText(/本次已生成 v1 草稿/);
    mocks.runs = [{ id: "new-run", status: "completed", model_status, conflict_rule_ids: [] }];
    page.rerender(<QueryClientProvider client={new QueryClient()}><MemoryRouter><SolverPage /></MemoryRouter></QueryClientProvider>);
    await waitFor(() => expect(screen.queryByText(/本次已生成 v1 草稿/)).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "确认下发" })).toBeDisabled();
    if (model_status === "UNKNOWN") expect(screen.getByText(/尚未证明无解；请增加时限/)).toBeInTheDocument();
    const lastCall = mocks.diff.mock.calls.at(-1) as unknown as [string, string, { query: { enabled: boolean } }];
    expect(lastCall[2].query.enabled).toBe(false);
  });
});

describe("SolverPage interpret phases", () => {
  const user = userEvent.setup();
  afterEach(cleanup);
  beforeEach(() => {
    mocks.diff.mockClear();
    mocks.runs = [];
    mocks.schedules = [];
    mocks.get.mockReset().mockResolvedValue({ data: { configured: false, app_configuration: { aily_configured: false } } });
    mocks.post.mockReset().mockResolvedValue({ data: {} });
    mocks.stream.mockReset().mockRejectedValue(new Error("stream unavailable"));
  });

  it("keeps manual params hidden before a parse and reveals them with backfill after success", async () => {
    mockAiConfigured();
    mocks.stream.mockResolvedValue(mockInterpretation());
    renderPage();
    // D5：解析成功前只有 RunPanel 常驻，手动参数不渲染。
    expect(await screen.findByText("实时状态")).toBeInTheDocument();
    expect(screen.queryByText("手动求解参数")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "让 AI 解析排课指令" }));
    // 思考区完成后折叠为「已解析完成（用时 N 秒）」，可展开回看。
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    // 流式成功不应触发同步回退。
    expect(mocks.post).not.toHaveBeenCalled();
    // D6：日期三元组回填进参数面板，窗口 ≠ 默认 7 时带「来自 AI 解析」徽标。
    expect(screen.getByText("手动求解参数")).toBeInTheDocument();
    expect(screen.getByDisplayValue("3")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2026-08-17")).toBeInTheDocument();
    expect(screen.getByText("来自 AI 解析")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /展开回看思考过程/ }));
    expect(screen.getByText("用户要求 3 天窗口，先核对候选业务线。")).toBeInTheDocument();
  });

  it("appends streaming thinking deltas live and keeps the stage list as fallback", async () => {
    mockAiConfigured();
    let handlers: { onThinking?: (delta: string, elapsed: number) => void } | null = null;
    mocks.stream.mockImplementation((...args: unknown[]) => {
      handlers = (args[2] as { onThinking?: (delta: string, elapsed: number) => void } | undefined) ?? null;
      return new Promise(() => { /* 挂起，模拟流式在途 */ });
    });
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    expect(await screen.findByText("正在连接 AI 模型…")).toBeInTheDocument();
    // 无增量的空窗期：只有阶段文案，没有实时思考块。
    expect(screen.queryByText(/先核对候选业务线/)).not.toBeInTheDocument();
    await act(async () => {
      handlers?.onThinking?.("先核对候选业务线。", 0.4);
      handlers?.onThinking?.("再把三天换算成窗口。", 0.8);
    });
    expect(await screen.findByText(/先核对候选业务线。再把三天换算成窗口。/)).toBeInTheDocument();
    expect(screen.getByText("正在连接 AI 模型…")).toBeInTheDocument();
  });

  it("shows stage progress and cancel while parsing, and returns to idle on cancel", async () => {
    mockAiConfigured();
    mocks.stream.mockImplementation((...args: unknown[]) => new Promise((_resolve, reject) => {
      (args[1] as AbortSignal).addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    expect(await screen.findByText("正在连接 AI 模型…")).toBeInTheDocument();
    expect(screen.getByText(/已用时/)).toBeInTheDocument();
    expect(screen.getByLabelText("一句话排课指令")).toBeDisabled();
    expect(screen.queryByText("手动求解参数")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "取消解析" }));
    await waitFor(() => expect(screen.getByLabelText("一句话排课指令")).toBeEnabled());
    expect(screen.queryByText("正在连接 AI 模型…")).not.toBeInTheDocument();
  });

  it("renders a persistent error bar with retry when stream and fallback both fail", async () => {
    mockAiConfigured();
    mocks.stream.mockRejectedValue(new Error("stream unavailable"));
    mocks.post.mockRejectedValue(new Error("AI 模型请求失败：连接超时"));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    expect(await screen.findByText(/AI 解析失败：AI 模型请求失败：连接超时/)).toBeInTheDocument();
    // 流式失败后必须先尝试同步回退，再展示最终错误。
    expect(mocks.post).toHaveBeenCalledWith("/api/v1/assistant/interpret", { instruction: expect.any(String) }, expect.anything());
    expect(screen.getByRole("button", { name: /重试解析/ })).toBeInTheDocument();
    expect(screen.queryByText("手动求解参数")).not.toBeInTheDocument();
  });

  it("falls back to the sync endpoint and completes the parse when streaming fails", async () => {
    mockAiConfigured();
    mocks.stream.mockRejectedValue(new Error("网关不支持流式响应"));
    mocks.post.mockResolvedValue({ data: mockInterpretation() });
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    expect(screen.getByText("手动求解参数")).toBeInTheDocument();
    expect(screen.getByDisplayValue("3")).toBeInTheDocument();
  });

  it("promotes manual params to the primary entry when AI is not configured", async () => {
    renderPage();
    expect(await screen.findByText("手动求解参数")).toBeInTheDocument();
    expect(screen.getByText("实时状态")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "让 AI 解析排课指令" })).toBeDisabled();
  });

  it("keeps the AI entry usable when only the feishu configuration read fails (问题6)", async () => {
    // 飞书配置读取失败不得把已配置好的通用 AI 入口一起打成不可用（可选集成解耦）。
    mocks.get.mockImplementation(async (...args: unknown[]) => {
      const url = String(args[0]);
      if (url === "/api/v1/integrations/feishu/connection") throw new Error("feishu 配置读取失败");
      return { data: { configured: true, model: null, app_configuration: { aily_configured: false } } };
    });
    renderPage();
    expect(await screen.findByText("通用 AI 模型 已接入")).toBeInTheDocument();
    expect(screen.queryByText("AI 配置读取失败")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "让 AI 解析排课指令" })).toBeEnabled();
  });

  it("still shows the probe error with retry when both AI and feishu reads fail (问题6)", async () => {
    mocks.get.mockRejectedValue(new Error("网络中断"));
    renderPage();
    expect(await screen.findByText("AI 配置读取失败")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "让 AI 解析排课指令" })).toBeDisabled();
  });
});

describe("SolverPage goal acceptance loop (MEM-C3)", () => {
  afterEach(cleanup);
  beforeEach(() => {
    mocks.diff.mockClear();
    mocks.submitMutate.mockClear();
    mocks.runs = [];
    mocks.schedules = [{ id: "v1", status: "published", solver_run_id: "run-1", version_no: 1, name: "已发布课表" }];
    mocks.courses = [];
    // userEvent 实例跨测试共享会把 pointer 状态带进下一个用例（上一用例卸载
    // 组件时指针未释放），这里每条用例独立 setup 并复位接口 mock。
    mocks.get.mockReset().mockResolvedValue({ data: { configured: true, model: "text-model", app_configuration: { aily_configured: false } } });
    mocks.post.mockReset().mockResolvedValue({ data: {} });
    mocks.stream.mockReset().mockRejectedValue(new Error("stream unavailable"));
    // MEM-D3：手动求解 POST /api/v1/solver-runs 的默认回包。
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      const url = String(args[0]);
      if (url === "/api/v1/solver-runs") return { data: { id: "run-manual", status: "queued", model_status: null } };
      return { data: {} };
    });
  });

  it("creates a tracking goal with the prefilled checklist, then solves with goal_id", async () => {
    mockAiConfigured();
    const interpretation = mockInterpretation() as ReturnType<typeof mockInterpretation> & {
      goal_checklist_draft: Array<{ key: string; requirement: string; kind: string; params: Record<string, never> }>;
    };
    interpretation.goal_checklist_draft = [
      { key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", params: {} },
      { key: "draft_only", requirement: "只交付草稿", kind: "draft_only", params: {} },
    ];
    mocks.stream.mockResolvedValue(interpretation);
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      const url = String(args[0]);
      if (url === "/api/v1/goals") return { data: { id: "goal-1", status: "open", checklist: [] } };
      return { data: { id: "run-1", status: "queued", model_status: null } };
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    expect(await screen.findByLabelText("以此为目标跟踪")).toBeChecked();
    await user.click(screen.getByRole("button", { name: /确认并开始求解/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    const [goalUrl, goalBody] = mocks.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(goalUrl).toBe("/api/v1/goals");
    expect(goalBody.instruction).toBe("请在三天内重排考研课程");
    expect((goalBody.checklist as unknown[]).length).toBe(2);
    expect(goalBody.forbid_publish).toBe(true);
    const [solveUrl, solveBody] = mocks.post.mock.calls[1] as [string, Record<string, unknown>];
    expect(solveUrl).toBe("/api/v1/assistant/solve");
    expect(solveBody.goal_id).toBe("goal-1");
    // 第二次确认同一解析复用同一目标，不再重复建 goal。
    mocks.post.mockClear();
    mocks.post.mockImplementation(async () => ({ data: { id: "run-2", status: "queued" } }));
    await user.click(screen.getByRole("button", { name: /确认并开始求解/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect((mocks.post.mock.calls[0] as unknown[])[0]).toBe("/api/v1/assistant/solve");
  });

  it("skips goal creation when tracking is toggled off and sends goal_id null", async () => {
    mockAiConfigured();
    mocks.stream.mockResolvedValue(mockInterpretation());
    mocks.post.mockResolvedValue({ data: { id: "run-3", status: "queued" } });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    await user.click(await screen.findByLabelText("以此为目标跟踪"));
    expect(screen.queryByLabelText("基准版本")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /确认并开始求解/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    const [solveUrl, solveBody] = mocks.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(solveUrl).toBe("/api/v1/assistant/solve");
    expect(solveBody.goal_id).toBeNull();
  });

  it("keeps the goal bound across re-parse and annotates the confirmation card (MEM-D3)", async () => {
    mockAiConfigured();
    const interpretation = mockInterpretation() as ReturnType<typeof mockInterpretation> & {
      goal_checklist_draft: Array<{ key: string; requirement: string; kind: string; params: Record<string, never> }>;
    };
    interpretation.goal_checklist_draft = [
      { key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", params: {} },
    ];
    mocks.stream.mockResolvedValue(interpretation);
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      const url = String(args[0]);
      if (url === "/api/v1/goals") return { data: { id: "goal-1", status: "open", checklist: [] } };
      return { data: { id: "run-1", status: "queued", model_status: null } };
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    await user.click(screen.getByRole("button", { name: /确认并开始求解/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    const [firstSolveUrl, firstSolveBody] = mocks.post.mock.calls[1] as [string, Record<string, unknown>];
    expect(firstSolveUrl).toBe("/api/v1/assistant/solve");
    expect(firstSolveBody.goal_id).toBe("goal-1");

    // 重新解析：不脱离原目标——goalId 保留，确认卡出现「已关联目标 #N」提示条。
    mocks.post.mockClear();
    mocks.post.mockImplementation(async () => ({ data: { id: "run-2", status: "queued" } }));
    await user.click(screen.getByRole("button", { name: "让 AI 解析排课指令" }));
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    expect(screen.getByText(/已关联目标 #goal-1/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /确认并开始求解/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    // 不再新建 goal，直接复用已绑定的目标求解。
    const [solveUrl, reparseSolveBody] = mocks.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(solveUrl).toBe("/api/v1/assistant/solve");
    expect(reparseSolveBody.goal_id).toBe("goal-1");
  });

  it("records only the baseline without a change limit unless the limit toggle is enabled (问题4)", async () => {
    mockAiConfigured();
    const interpretation = mockInterpretation() as ReturnType<typeof mockInterpretation> & {
      goal_checklist_draft: Array<{ key: string; requirement: string; kind: string; params: Record<string, never> }>;
    };
    interpretation.goal_checklist_draft = [
      { key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", params: {} },
    ];
    mocks.stream.mockResolvedValue(interpretation);
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      const url = String(args[0]);
      if (url === "/api/v1/goals") return { data: { id: "goal-2", status: "open", checklist: [] } };
      return { data: { id: "run-1", status: "queued", model_status: null } };
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    await user.selectOptions(screen.getByLabelText("基准版本"), "v1");
    // 默认不设上限：确认卡如实说明「未设变更上限」，上限输入也不出现。
    expect(screen.getByText("已选基准：仅记录基准用于变更对比，未设变更上限")).toBeInTheDocument();
    expect(screen.queryByLabelText("变更数验收上限")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /确认并开始求解/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    const [goalUrl, goalBody] = mocks.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(goalUrl).toBe("/api/v1/goals");
    const checklist = goalBody.checklist as Array<{ kind: string }>;
    // 选基准不再静默附带 max_changes=50：清单里没有任何 max_changes 项。
    expect(checklist.some((item) => item.kind === "max_changes")).toBe(false);
    // 基准本身仍被记录（用于变更明细/数量对比与优化配置）。
    expect(goalBody.baseline_schedule_version_id).toBe("v1");
  });

  it("attaches the max_changes checklist item only after 设置变更上限 is enabled (问题4)", async () => {
    mockAiConfigured();
    const interpretation = mockInterpretation() as ReturnType<typeof mockInterpretation> & {
      goal_checklist_draft: Array<{ key: string; requirement: string; kind: string; params: Record<string, never> }>;
    };
    interpretation.goal_checklist_draft = [
      { key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", params: {} },
    ];
    mocks.stream.mockResolvedValue(interpretation);
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      const url = String(args[0]);
      if (url === "/api/v1/goals") return { data: { id: "goal-3", status: "open", checklist: [] } };
      return { data: { id: "run-1", status: "queued", model_status: null } };
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    await user.selectOptions(screen.getByLabelText("基准版本"), "v1");
    // 明确开启「设置变更上限」后上限输入才出现，此时确认卡说明将附带验收项。
    await user.click(screen.getByLabelText("设置变更上限"));
    expect(screen.getByText("已选基准：清单将附带变更数验收项")).toBeInTheDocument();
    const limitInput = screen.getByLabelText("变更数验收上限");
    await user.clear(limitInput);
    await user.type(limitInput, "3");
    await user.click(screen.getByRole("button", { name: /确认并开始求解/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    const [goalUrl, goalBody] = mocks.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(goalUrl).toBe("/api/v1/goals");
    const checklist = goalBody.checklist as Array<{ kind: string; params: Record<string, unknown> }>;
    const maxChanges = checklist.find((item) => item.kind === "max_changes");
    expect(maxChanges).toBeDefined();
    expect(maxChanges!.params.max_changes).toBe(3);
    expect(maxChanges!.params.baseline_schedule_version_id).toBe("v1");
    expect(goalBody.baseline_schedule_version_id).toBe("v1");
  });

  it("keeps the parsed scope intact when only the time budget is adjusted before a manual retry (问题2)", async () => {
    mockAiConfigured();
    mocks.stream.mockResolvedValue({
      ...mockInterpretation(),
      business_lines: ["考研"],
      product_types: ["暑期集训"],
      class_business_ids: ["C-001"],
    });
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      const url = String(args[0]);
      if (url === "/api/v1/goals") return { data: { id: "goal-scope", status: "open", checklist: [] } };
      return { data: { id: "run-scope", status: "queued", model_status: null } };
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    await user.click(await screen.findByRole("button", { name: /确认并开始求解/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    const [solveUrl, solveBody] = mocks.post.mock.calls[1] as [string, Record<string, unknown>];
    expect(solveUrl).toBe("/api/v1/assistant/solve");
    expect(solveBody.business_lines).toEqual(["考研"]);
    expect(solveBody.product_types).toEqual(["暑期集训"]);
    expect(solveBody.class_business_ids).toEqual(["C-001"]);
    expect(solveBody.date_from).toBe("2026-08-17");
    expect(solveBody.date_to).toBe("2026-08-19");
    expect(solveBody.goal_id).toBe("goal-scope");

    // 只调时间预算（30 → 60），不动任何范围字段，然后走手动入口重试。
    const timeInput = screen.getByLabelText("求解时限（秒）");
    await user.clear(timeInput);
    await user.type(timeInput, "60");
    await user.click(screen.getByRole("button", { name: /按参数开始求解/ }));
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    const submitted = mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> };
    expect(submitted.data.time_limit_seconds).toBe(60);
    // 除预算外，范围字段与第一次请求完全一致（含 goal_id 关联场景）。
    expect(submitted.data.business_lines).toEqual(["考研"]);
    expect(submitted.data.product_types).toEqual(["暑期集训"]);
    expect(submitted.data.class_business_ids).toEqual(["C-001"]);
    expect(submitted.data.date_from).toBe("2026-08-17");
    expect(submitted.data.date_to).toBe("2026-08-19");
    expect(submitted.data.date_window_days).toBe(3);
    expect(submitted.data.goal_id).toBe("goal-scope");
  });

  it("gates a widened scope behind an explicit confirmation before any solve request (问题2)", async () => {
    mockAiConfigured();
    mocks.courses = [{ id: "cs-1", business_line: "考研", class_business_id: "C-001", lesson_date: "2026-08-17" }];
    mocks.stream.mockResolvedValue({ ...mockInterpretation(), class_business_ids: ["C-001"] });
    mocks.post.mockResolvedValue({ data: { id: "run-wide", status: "queued", model_status: null } });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    // 把班级从解析出的 C-001 改成「全部班级」＝扩大范围，需要单独确认。
    await user.selectOptions(screen.getByLabelText("班级"), "");
    expect(screen.getByText(/扩大范围需要单独确认/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /按参数开始求解/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /确认并开始求解/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "确认扩大范围" }));
    expect(screen.queryByText(/扩大范围需要单独确认/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /按参数开始求解/ }));
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    const submitted = mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> };
    expect(submitted.data.class_business_ids).toEqual([]);
  });

  it("keeps manually edited scope fields across a re-parse (问题2)", async () => {
    mockAiConfigured();
    mocks.courses = [
      { id: "cs-1", business_line: "考研", class_business_id: "C-001", lesson_date: "2026-08-17" },
      { id: "cs-2", business_line: "考研", class_business_id: "C-002", lesson_date: "2026-08-18" },
    ];
    mocks.stream.mockResolvedValue({ ...mockInterpretation(), class_business_ids: ["C-001"] });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("班级"), "C-002");
    // 重新解析同一指令：用户手动改过的范围字段不被解析结果覆盖。
    await user.click(screen.getByRole("button", { name: "让 AI 解析排课指令" }));
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    expect(screen.getByLabelText("班级")).toHaveValue("C-002");
  });

  it("binds a goal from the ?goal= deep link and carries it through manual solving (MEM-D3)", async () => {
    const user = userEvent.setup();
    // AI 未配置 → 手动参数是唯一入口；目标详情按 URL 路由返回固定 goal。
    mocks.get.mockImplementation(async (...args: unknown[]) => {
      const url = String(args[0]);
      if (url === "/api/v1/goals/goal-77") {
        return { data: { id: "goal-77", status: "open", instruction: "重排 B1 班三天课", checklist: [] } };
      }
      return { data: { configured: false, model: null, app_configuration: { aily_configured: false } } };
    });
    renderPage("/solver?goal=goal-77");
    // 挂载即拉取目标并绑定，提示条出现。
    expect(await screen.findByText(/已关联目标 #goal-77/)).toBeInTheDocument();
    expect(await screen.findByText("手动求解参数")).toBeInTheDocument();
    // 手动求解参数区显示关联徽标；提交 /solver-runs 时带上 goal_id。
    expect(screen.getByText(/本次求解关联目标/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /按参数开始求解/ }));
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    const submitted = mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> };
    // 手动求解携带当前会话持有的 goal_id（MEM-D3 目标连续性）。
    expect(submitted.data.goal_id).toBe("goal-77");
  });

  it("shows no goal badge and omits goal_id when the session has no bound goal", async () => {
    const user = userEvent.setup();
    mocks.get.mockResolvedValue({ data: { configured: false, model: null, app_configuration: { aily_configured: false } } });
    renderPage("/solver");
    expect(await screen.findByText("手动求解参数")).toBeInTheDocument();
    expect(screen.queryByText(/本次求解关联目标/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /按参数开始求解/ }));
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    const submitted = mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> };
    // 没有 goal 时行为不变：字段为 null（后端 goal_id 可选，等价于未关联）。
    expect(submitted.data.goal_id).toBeNull();
    mocks.submitMutate.mockClear();
  });

  it("renders the acceptance report with per-item results and gap next steps", async () => {
    mocks.schedules = [];
    mocks.runs = [{
      id: "run-goal",
      status: "completed",
      model_status: "OPTIMAL",
      goal_id: "goal-9",
      goal_report: {
        goal_id: "goal-9",
        instruction: "重排 B1 班三天课",
        all_passed: false,
        passed_count: 2,
        failed_count: 1,
        items: [
          { key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", passed: true, detail: "目标课次 3 个，结果命中 3 个" },
          { key: "draft_only", requirement: "只交付草稿", kind: "draft_only", passed: true, detail: "目标期间无发布动作" },
          { key: "forbidden_slot_free-1", requirement: "T9 不占 S1", kind: "forbidden_slot_free", passed: false, detail: "禁排时段仍被占用 1 处：C24" },
        ],
        gaps: [
          { key: "forbidden_slot_free-1", kind: "forbidden_slot_free", summary: "禁排时段仍被占用", next_step: "等待教务放宽或调整排课", remedy: "await_admin" },
        ],
        decision: { status: "awaiting_decision", reason: "1 项未通过；其中存在必须由教务放宽或裁决的缺口" },
      },
    }];
    renderPage();
    expect(await screen.findByText("目标验收报告")).toBeInTheDocument();
    // 徽标与 amber 提示条各出现一次「目标未完成」。
    expect(screen.getAllByText(/目标未完成：1 项缺口/)).toHaveLength(2);
    expect(screen.getByText("目标未完成：1 项缺口，建议的下一步")).toBeInTheDocument();
    expect(screen.getByText(/课次覆盖/)).toBeInTheDocument();
    expect(screen.getByText(/禁排复核/)).toBeInTheDocument();
    expect(screen.getByText(/等待教务放宽或调整排课/)).toBeInTheDocument();
    expect(screen.getAllByText("✓")).toHaveLength(2);
    expect(screen.getAllByText("✗")).toHaveLength(1);
  });

  it("renders an all-passed report as goal achieved without the amber banner", async () => {
    mocks.schedules = [];
    mocks.runs = [{
      id: "run-goal-ok",
      status: "completed",
      model_status: "OPTIMAL",
      goal_report: {
        goal_id: "goal-8",
        instruction: "重排 B1 班三天课",
        all_passed: true,
        passed_count: 2,
        failed_count: 0,
        items: [
          { key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", passed: true, detail: "目标课次 1 个，结果命中 1 个" },
          { key: "draft_only", requirement: "只交付草稿", kind: "draft_only", passed: true, detail: "目标期间无发布/日历下发动作" },
        ],
        gaps: [],
        decision: { status: "achieved", reason: "全部验收项通过" },
      },
    }];
    renderPage();
    expect(await screen.findByText(/全部 2 项通过 · 目标达成/)).toBeInTheDocument();
    expect(screen.queryByText(/目标未完成/)).not.toBeInTheDocument();
  });
});

describe("SolverPage preference memory usage (MEM-C1)", () => {
  afterEach(cleanup);
  beforeEach(() => {
    mocks.diff.mockClear();
    mocks.runs = [];
    mocks.schedules = [];
  });

  it("shows the frozen memory usage summary with per-entry outcomes", async () => {
    mocks.runs = [{
      id: "run-mem",
      status: "completed",
      model_status: "OPTIMAL",
      memory_usage: {
        status: "ok",
        summary: { considered: 2, applied: 1, unused: 1 },
        outcomes: [
          { entry_id: "e1", subject_type: "teacher", subject_id: "T9", predicate: "avoid_slot", outcome: "applied", detail: "以权重 30 参与求解" },
          { entry_id: "e2", subject_type: "teacher", subject_id: "T8", predicate: "prefer_slot", outcome: "not_authorized", detail: "待确认候选未经采纳或授权试用，不进入求解输入" },
        ],
      },
    }];
    renderPage();
    expect(await screen.findByText("偏好记忆（创建时编译口径）")).toBeInTheDocument();
    expect(screen.getByText(/创建任务时 2 条偏好记忆获准编译（编译进求解输入 1 \/ 未编译 1）/)).toBeInTheDocument();
    expect(screen.getByText(/待确认候选未经采纳或授权试用，不进入求解输入/)).toBeInTheDocument();
  });

  it("states explicitly that preferences were not used when compilation failed", async () => {
    mocks.runs = [{
      id: "run-fail",
      status: "completed",
      model_status: "OPTIMAL",
      memory_usage: { status: "compile_failed", detail: "模拟编译崩溃" },
    }];
    renderPage();
    expect(await screen.findByText(/本次未使用偏好记忆：编译失败（模拟编译崩溃）/)).toBeInTheDocument();
  });
});
