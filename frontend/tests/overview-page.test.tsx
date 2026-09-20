import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OverviewPage } from "@/pages/overview-page";

const mocks = vi.hoisted(() => ({
  analytics: { current: {} as Record<string, unknown> },
  analyticsRefetch: vi.fn(),
  overviewRefetch: vi.fn(),
  httpGet: vi.fn(),
}));

vi.mock("@/api/http", () => ({ http: { get: mocks.httpGet } }));

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
  useOverviewAnalyticsApiV1OverviewAnalyticsGet: () => mocks.analytics.current,
  useListRulesApiV1RulesGet: () => ({ data: [] }),
  useListSchedulesApiV1SchedulesGet: () => ({ data: [] }),
  useListSolverRunsApiV1SolverRunsGet: () => ({ data: [] }),
}));

type RenderUser = { id: string; username: string; role: "admin" | "viewer" };

function renderPage(user: RenderUser = { id: "admin-id", username: "admin", role: "admin" }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const scheduleAccessRole = user.role === "viewer" ? "viewer" : "approver";
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/overview"]}>
        <Routes>
          <Route element={<Outlet context={{ user, scheduleAccessRole }} />}>
            <Route path="/overview" element={<OverviewPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("OverviewPage analytics error handling", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.analyticsRefetch.mockReset();
    mocks.overviewRefetch.mockReset();
    mocks.httpGet.mockReset();
    mocks.httpGet.mockResolvedValue({ data: { configured: false, model: null } });
    mocks.analytics.current = {
      data: undefined,
      error: new Error("Request failed with status code 404"),
      isPending: false,
      isError: true,
      refetch: mocks.analyticsRefetch,
    };
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

  it("没有求解或同步样本时展示暂无数据，不宣称 100% 健康", () => {
    mocks.analytics.current = {
      data: {
        teacher_workload: {
          total_teachers: 0,
          assigned_teachers: 0,
          total_hours: 0,
          top_teachers: [],
          buckets: [],
        },
        room_heatmap: { period_cells: [] },
        optimization_penalties: {
          solver_run_id: null,
          solver_status: null,
          objective_value: null,
          best_bound: null,
          total_soft_penalty: null,
          reconciliation_error: null,
          evaluated_assignment_count: 0,
          soft_constraints: [],
        },
        sync_health: {
          total_syncs: 0,
          completed_syncs: 0,
          failed_syncs: 0,
          retry_count: 0,
          retry_samples: 0,
          records_read: 0,
          records_written: 0,
          average_duration_ms: null,
          latest_sync_at: null,
        },
      },
      isPending: false,
      isError: false,
      refetch: mocks.analyticsRefetch,
    };

    renderPage();

    expect(screen.getByText("当前课表暂无求解记录或软约束评估数据")).toBeInTheDocument();
    expect(screen.getByText("对账差额: 暂无数据")).toBeInTheDocument();
    expect(screen.getByText("暂无同步样本")).toBeInTheDocument();
    expect(screen.getByText("累计重试: 暂无样本")).toBeInTheDocument();
    expect(screen.queryByText("100%", { exact: true })).not.toBeInTheDocument();
  });

  it("呈现排课准备清单与快捷入口，只读成员看不到求解/调课入口", () => {
    renderPage();

    // 清单四项齐备；规则/发布/AI 均未就绪时保留去完成链接。
    expect(screen.getByText("排课准备清单")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去配规则" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去配置" })).toBeInTheDocument();

    // 管理员能看到全部快捷入口（含新增的主数据/诊断/调课）。
    expect(screen.getByRole("link", { name: /智能求解排课/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /主数据管理/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /无解诊断/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /局部调课/ })).toBeInTheDocument();
  });

  it("只读成员的快捷入口不再有会落空的求解与调课卡", () => {
    renderPage({ id: "viewer-id", username: "member_demo", role: "viewer" });

    expect(screen.queryByRole("link", { name: /智能求解排课/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /局部调课/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /主数据管理/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /无解诊断/ })).toBeInTheDocument();
  });
});
