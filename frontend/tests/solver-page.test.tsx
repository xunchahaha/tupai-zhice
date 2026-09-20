import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SolverPage } from "@/pages/solver-page";

const mocks = vi.hoisted(() => ({
  runs: [] as Record<string, unknown>[],
  schedules: [] as Record<string, unknown>[],
  diff: vi.fn<(...args: unknown[]) => { data: undefined }>(() => ({ data: undefined })),
  get: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => ({ data: { configured: false, app_configuration: { aily_configured: false } } })),
  post: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => ({ data: {} })),
}));

vi.mock("@/api/http", () => ({ http: { get: mocks.get, post: mocks.post } }));

vi.mock("@/api/generated/client", () => ({
  getListSchedulesApiV1SchedulesGetQueryKey: () => ["schedules"],
  getListSolverRunsApiV1SolverRunsGetQueryKey: () => ["runs"],
  getOverviewApiV1OverviewGetQueryKey: () => ["overview"],
  useListRulesApiV1RulesGet: () => ({ data: [] }),
  useListCourseSessionsApiV1CourseSessionsGet: () => ({ data: [] }),
  useListSchedulesApiV1SchedulesGet: () => ({ data: mocks.schedules }),
  useListSolverRunsApiV1SolverRunsGet: () => ({ data: mocks.runs }),
  useGetScheduleApiV1SchedulesScheduleIdGet: () => ({ data: undefined }),
  useGetSolverRunApiV1SolverRunsRunIdGet: () => ({ data: undefined }),
  useSubmitSolverRunApiV1SolverRunsPost: () => ({ mutate: vi.fn() }),
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

function renderPage() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><SolverPage /></MemoryRouter>
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
  });

  it("keeps manual params hidden before a parse and reveals them with backfill after success", async () => {
    mockAiConfigured();
    mocks.post.mockResolvedValue({ data: mockInterpretation() });
    renderPage();
    // D5：解析成功前只有 RunPanel 常驻，手动参数不渲染。
    expect(await screen.findByText("实时状态")).toBeInTheDocument();
    expect(screen.queryByText("手动求解参数")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "让 AI 解析排课指令" }));
    // 思考区完成后折叠为「已解析完成（用时 N 秒）」，可展开回看。
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    // D6：日期三元组回填进参数面板，窗口 ≠ 默认 7 时带「来自 AI 解析」徽标。
    expect(screen.getByText("手动求解参数")).toBeInTheDocument();
    expect(screen.getByDisplayValue("3")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2026-08-17")).toBeInTheDocument();
    expect(screen.getByText("来自 AI 解析")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /展开回看思考过程/ }));
    expect(screen.getByText("用户要求 3 天窗口，先核对候选业务线。")).toBeInTheDocument();
  });

  it("shows stage progress and cancel while parsing, and returns to idle on cancel", async () => {
    mockAiConfigured();
    mocks.post.mockImplementation((...args: unknown[]) => new Promise((_resolve, reject) => {
      const config = args[2] as { signal?: AbortSignal } | undefined;
      config?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
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

  it("renders a persistent error bar with retry when the parse fails", async () => {
    mockAiConfigured();
    mocks.post.mockRejectedValue(new Error("AI 模型请求失败：连接超时"));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "让 AI 解析排课指令" }));
    expect(await screen.findByText(/AI 解析失败：AI 模型请求失败：连接超时/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重试解析/ })).toBeInTheDocument();
    expect(screen.queryByText("手动求解参数")).not.toBeInTheDocument();
  });

  it("promotes manual params to the primary entry when AI is not configured", async () => {
    renderPage();
    expect(await screen.findByText("手动求解参数")).toBeInTheDocument();
    expect(screen.getByText("实时状态")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "让 AI 解析排课指令" })).toBeDisabled();
  });
});
