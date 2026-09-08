import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SolverPage } from "@/pages/solver-page";

const mocks = vi.hoisted(() => ({
  runs: [] as Record<string, unknown>[],
  schedules: [] as Record<string, unknown>[],
  diff: vi.fn<(...args: unknown[]) => { data: undefined }>(() => ({ data: undefined })),
}));

vi.mock("@/api/http", () => ({ http: {
  get: vi.fn(async () => ({ data: { configured: false, app_configuration: { aily_configured: false } } })),
  post: vi.fn(async () => ({ data: {} })),
} }));

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
