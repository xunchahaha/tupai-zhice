import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OverviewPage } from "@/pages/overview-page";

const mocks = vi.hoisted(() => ({
  analyticsRefetch: vi.fn(),
  overviewRefetch: vi.fn(),
}));

vi.mock("@/api/generated/client", () => ({
  useOverviewApiV1OverviewGet: () => ({
    data: {
      counts: { teachers: 4, class_groups: 36, rooms: 16, course_sessions: 4544 },
      latest_run: null,
      latest_schedule: null,
    },
    isPending: false,
    isError: false,
    refetch: mocks.overviewRefetch,
  }),
  useOverviewAnalyticsApiV1OverviewAnalyticsGet: () => ({
    data: undefined,
    error: new Error("Request failed with status code 404"),
    isPending: false,
    isError: true,
    refetch: mocks.analyticsRefetch,
  }),
  useListRulesApiV1RulesGet: () => ({ data: [] }),
  useListSchedulesApiV1SchedulesGet: () => ({ data: [] }),
  useListSolverRunsApiV1SolverRunsGet: () => ({ data: [] }),
}));

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("OverviewPage analytics error handling", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.analyticsRefetch.mockReset();
    mocks.overviewRefetch.mockReset();
  });

  it("统计接口失败时展示错误与重试，不把缺失数据伪装成零值", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByText("统计数据加载失败")).toBeInTheDocument();
    expect(screen.queryByText("同步成功率")).not.toBeInTheDocument();
    expect(screen.queryByText("当前方案暂无教师课时分配数据")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(mocks.analyticsRefetch).toHaveBeenCalledTimes(1);
  });
});
