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

    expect(screen.getByText("当前课表暂无求解记录")).toBeInTheDocument();
    // 达成率和已排入课次都没有评估来源：统一写「暂无评估」，不拿基础资料的 4544 个课次顶替。
    expect(screen.getAllByText("暂无评估")).toHaveLength(2);
    expect(screen.getByText("暂无同步样本")).toBeInTheDocument();
    expect(screen.queryByText("100%", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText(/已通过硬性约束校验/)).not.toBeInTheDocument();
    expect(screen.queryByText(/硬冲突: 0 处/)).not.toBeInTheDocument();
    expect(screen.queryByText(/全部达成/)).not.toBeInTheDocument();
    // 遥测术语和没有数据支撑的「理论最优」一并删除。
    for (const removed of [/对账差额/, /逼近理论最优/, /遥测监控窗口/, /数据吞吐读写量/, /平均同步耗时/, /累计重试/]) {
      expect(screen.queryByText(removed)).not.toBeInTheDocument();
    }
    // 概览已自动轮询，不再放「刷新数据」按钮。
    expect(screen.queryByRole("button", { name: /刷新数据/ })).not.toBeInTheDocument();
  });

  it("有求解记录但没有规则评估时，不输出「全部达成」或 100%", () => {
    mocks.analytics.current = {
      data: {
        teacher_workload: { total_teachers: 0, assigned_teachers: 0, total_hours: 0, top_teachers: [], buckets: [] },
        room_heatmap: { period_cells: [] },
        optimization_penalties: { solver_run_id: "run-1", solver_status: "FEASIBLE", objective_value: 0, best_bound: 0, total_soft_penalty: 0, reconciliation_error: 0, evaluated_assignment_count: 0, soft_constraints: [] },
        sync_health: { total_syncs: 0, completed_syncs: 0, failed_syncs: 0, retry_count: 0, retry_samples: 0, records_read: 0, records_written: 0, average_duration_ms: null, latest_sync_at: null },
      },
      isPending: false,
      isError: false,
      refetch: mocks.analyticsRefetch,
    };
    renderPage();

    expect(screen.getAllByText("暂无评估")).toHaveLength(2);
    expect(screen.getByText("本次求解没有返回规则评估")).toBeInTheDocument();
    // 有求解记录时，下方空态不能再说「暂无求解记录」。
    expect(screen.getByText("该次求解没有可逐条列出的规则评估")).toBeInTheDocument();
    expect(screen.queryByText(/暂无求解记录/)).not.toBeInTheDocument();
    expect(screen.queryByText(/全部达成/)).not.toBeInTheDocument();
    expect(screen.queryByText("100%", { exact: true })).not.toBeInTheDocument();
  });

  it("软约束偏离和同步失败的提示保留且醒目；满足率缺失时显示「—」而不是 100%", () => {
    mocks.analytics.current = {
      data: {
        teacher_workload: { total_teachers: 0, assigned_teachers: 0, total_hours: 0, top_teachers: [], buckets: [] },
        room_heatmap: { period_cells: [] },
        optimization_penalties: {
          solver_run_id: "run-1",
          solver_status: "FEASIBLE",
          objective_value: 12,
          best_bound: 10,
          total_soft_penalty: 12,
          reconciliation_error: 0,
          evaluated_assignment_count: 320,
          soft_constraints: [
            { rule_id: "R-1", label: "教师周三晚尽量不排课", violations: 3, satisfaction_rate: 0.9 },
            { rule_id: "R-2", label: "同班一天不超过两节", violations: 0, satisfaction_rate: null },
          ],
        },
        sync_health: { total_syncs: 4, completed_syncs: 3, failed_syncs: 1, retry_count: 2, retry_samples: 2, records_read: 10, records_written: 8, average_duration_ms: 1200, latest_sync_at: null },
      },
      isPending: false,
      isError: false,
      refetch: mocks.analyticsRefetch,
    };
    renderPage();

    // 达成率是能验证的规则满足率的平均（R-2 没有满足率，不进分母），不是「100 − 违规次数」。
    expect(screen.getAllByText("90%")).toHaveLength(2);
    expect(screen.queryByText("97%")).not.toBeInTheDocument();
    expect(screen.getByText("3 处微调偏离")).toBeInTheDocument();
    expect(screen.getByText("3 处微调")).toBeInTheDocument();
    expect(screen.getByText("320")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("100%", { exact: true })).not.toBeInTheDocument();
    expect(screen.getByText("存在重试/预警")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
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

  function penaltiesWith(softConstraints: unknown[], reconciliationError = 0) {
    mocks.analytics.current = {
      data: {
        teacher_workload: { total_teachers: 0, assigned_teachers: 0, total_hours: 0, top_teachers: [], buckets: [] },
        room_heatmap: { period_cells: [] },
        optimization_penalties: {
          solver_run_id: "run-1",
          solver_status: "FEASIBLE",
          objective_value: 0,
          best_bound: 0,
          total_soft_penalty: 0,
          reconciliation_error: reconciliationError,
          evaluated_assignment_count: 320,
          soft_constraints: softConstraints,
        },
        sync_health: { total_syncs: 0, completed_syncs: 0, failed_syncs: 0, retry_count: 0, retry_samples: 0, records_read: 0, records_written: 0, average_duration_ms: null, latest_sync_at: null },
      },
      isPending: false,
      isError: false,
      refetch: mocks.analyticsRefetch,
    };
  }

  it("尚无可重算口径的规则（violations=null）显示「暂时无法验证」，不算通过", () => {
    penaltiesWith([
      { rule_id: "R-1", label: "同班一天不超过两节", violations: 0, satisfaction_rate: 1 },
      { rule_id: "R-2", label: "教师连堂尽量集中", violations: null, satisfaction_rate: null },
    ]);
    renderPage();
    // 行内徽标：无法验证的一行不再显示绿色「完全符合」。
    expect(screen.getAllByText("完全符合")).toHaveLength(1);
    expect(screen.getByText("暂时无法验证")).toBeInTheDocument();
    // 顶部：有无法验证的规则就不写「各项规则完全符合」。
    expect(screen.getByText("1 条规则暂时无法验证")).toBeInTheDocument();
    expect(screen.queryByText("各项规则完全符合")).not.toBeInTheDocument();
  });

  it("所有规则都无法重算时，顶部不写 100%", () => {
    penaltiesWith([
      { rule_id: "R-1", label: "教师连堂尽量集中", violations: null, satisfaction_rate: null },
      { rule_id: "R-2", label: "晚间尽量不排", violations: null, satisfaction_rate: null },
    ]);
    renderPage();
    expect(screen.queryByText("100%", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText("各项规则完全符合")).not.toBeInTheDocument();
    expect(screen.getByText("2 条规则暂时无法验证")).toBeInTheDocument();
    expect(screen.getAllByText("暂时无法验证")).toHaveLength(2);
  });

  it("全部规则都满足时才写 100% 和「各项规则完全符合」", () => {
    penaltiesWith([{ rule_id: "R-1", label: "同班一天不超过两节", violations: 0, satisfaction_rate: 1 }]);
    renderPage();
    expect(screen.getAllByText("100%")).toHaveLength(2);
    expect(screen.getByText("各项规则完全符合")).toBeInTheDocument();
    expect(screen.queryByText(/对不上/)).not.toBeInTheDocument();
  });

  it("规则评估与求解目标对不上（reconciliation_error>0）时保留一行提示", () => {
    penaltiesWith([{ rule_id: "R-1", label: "教师周三晚尽量不排课", violations: 3, satisfaction_rate: 0.9 }], 4);
    renderPage();
    expect(screen.getByText("规则评估与求解结果对不上，以上数字仅供参考")).toBeInTheDocument();
    // 遥测术语仍然不回来。
    expect(screen.queryByText(/对账差额/)).not.toBeInTheDocument();
  });
});
