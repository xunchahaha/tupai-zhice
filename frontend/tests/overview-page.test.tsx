import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OverviewInsights } from "@/pages/overview-page";

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
}));

function renderPage(props: { canOpenSettings?: boolean } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/assistant"]}>
        <OverviewInsights {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("OverviewInsights analytics error handling", () => {
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

  it("展示实体统计，且不再带指向旧路径的快捷入口九宫格", () => {
    renderPage();

    expect(screen.getByText("教师总数")).toBeInTheDocument();
    expect(screen.getByText("4544")).toBeInTheDocument();
    // 左栏已收敛为三个业务入口，概览里不再重复；旧路径也不该再被链接。
    expect(screen.queryByText("排课工作台快捷入口")).not.toBeInTheDocument();
    const hrefs = screen.queryAllByRole("link").map((link) => link.getAttribute("href"));
    for (const legacy of ["/solver", "/diagnostics", "/reschedule", "/versions", "/overview", "/integrations"]) {
      expect(hrefs).not.toContain(legacy);
    }
  });

  it("概览里不重复排课准备清单（助手首页自己带紧凑版）", () => {
    renderPage();
    expect(screen.queryByText("排课准备清单")).not.toBeInTheDocument();
  });

  it("同步健康度里的集成入口指向设置", () => {
    mocks.analytics.current = {
      data: {
        teacher_workload: { total_teachers: 0, assigned_teachers: 0, total_hours: 0, top_teachers: [], buckets: [] },
        room_heatmap: { period_cells: [] },
        optimization_penalties: { solver_run_id: null, solver_status: null, objective_value: null, best_bound: null, total_soft_penalty: null, reconciliation_error: null, evaluated_assignment_count: 0, soft_constraints: [] },
        sync_health: { total_syncs: 0, completed_syncs: 0, failed_syncs: 0, retry_count: 0, retry_samples: 0, records_read: 0, records_written: 0, average_duration_ms: null, latest_sync_at: null },
      },
      isPending: false,
      isError: false,
      refetch: mocks.analyticsRefetch,
    };
    renderPage();
    expect(screen.getByRole("link", { name: /前往集成配置/ })).toHaveAttribute("href", "/settings");
  });

  it("没有设置权限的角色看不到「前往集成配置」，避免点了被弹回原地", () => {
    mocks.analytics.current = {
      data: {
        teacher_workload: { total_teachers: 0, assigned_teachers: 0, total_hours: 0, top_teachers: [], buckets: [] },
        room_heatmap: { period_cells: [] },
        optimization_penalties: { solver_run_id: null, solver_status: null, objective_value: null, best_bound: null, total_soft_penalty: null, reconciliation_error: null, evaluated_assignment_count: 0, soft_constraints: [] },
        sync_health: { total_syncs: 0, completed_syncs: 0, failed_syncs: 0, retry_count: 0, retry_samples: 0, records_read: 0, records_written: 0, average_duration_ms: null, latest_sync_at: null },
      },
      isPending: false,
      isError: false,
      refetch: mocks.analyticsRefetch,
    };
    renderPage({ canOpenSettings: false });
    expect(screen.getByText("教师总数")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /前往集成配置/ })).not.toBeInTheDocument();
  });
});
