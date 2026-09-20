import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GoalsPage } from "@/pages/goals-page";

const mocks = vi.hoisted(() => ({
  goals: [] as Array<Record<string, unknown>>,
  detail: undefined as Record<string, unknown> | undefined,
  abandon: vi.fn(),
}));

vi.mock("@/api/generated/client", () => ({
  getListGoalsApiV1GoalsGet: () => ({ data: mocks.goals, isPending: false, isError: false, refetch: vi.fn() }),
  useListGoalsApiV1GoalsGet: () => ({ data: mocks.goals, isPending: false, isError: false, refetch: vi.fn() }),
  getGetGoalApiV1GoalsGoalIdGetQueryKey: (goalId?: string) => ["goal-detail", goalId],
  useGetGoalApiV1GoalsGoalIdGet: (_goalId: string, options?: { query?: { enabled?: boolean } }) => ({
    data: options?.query?.enabled ? mocks.detail : undefined,
    isPending: false,
    isError: false,
  }),
  useAbandonGoalApiV1GoalsGoalIdAbandonPost: (config?: { mutation?: { onSuccess?: () => void; onError?: (e: unknown) => void } }) => ({
    mutate: (vars: unknown) => {
      mocks.abandon(vars);
      config?.mutation?.onSuccess?.();
    },
    isPending: false,
  }),
}));

function goalFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: "goal-1",
    schedule_set_id: "s1",
    instruction: "排好 B1 班 10 月第一周的课，只出草稿",
    checklist: [
      { key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", params: {} },
      { key: "draft_only", requirement: "只交付草稿", kind: "draft_only", params: {} },
    ],
    status: "awaiting_decision",
    latest_run_id: "run-1",
    run_count: 2,
    created_by: null,
    created_at: "2026-09-20T08:00:00+08:00",
    updated_at: "2026-09-20T09:00:00+08:00",
    ...overrides,
  };
}

function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <GoalsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("GoalsPage (MEM-C3)", () => {
  const user = userEvent.setup();
  afterEach(cleanup);
  beforeEach(() => {
    mocks.abandon.mockClear();
    mocks.detail = undefined;
  });

  it("shows an empty hint when no goals exist", async () => {
    mocks.goals = [];
    renderPage();
    expect(await screen.findByText(/还没有跟踪中的目标/)).toBeInTheDocument();
  });

  it("lists goals with status badges and run counts", async () => {
    mocks.goals = [
      goalFixture(),
      goalFixture({ id: "goal-2", status: "achieved", instruction: "重排三天课", run_count: 1 }),
    ];
    renderPage();
    expect(await screen.findByText("排好 B1 班 10 月第一周的课，只出草稿")).toBeInTheDocument();
    expect(screen.getByText("待教务裁决")).toBeInTheDocument();
    expect(screen.getByText("已达成")).toBeInTheDocument();
    expect(screen.getByText("2 次")).toBeInTheDocument();
    // 未放弃的目标都提供放弃入口（achieved 也可人工放弃）。
    const abandonButtons = screen.getAllByRole("button", { name: "放弃" });
    expect(abandonButtons).toHaveLength(2);
  });

  it("opens the detail dialog with checklist, latest report and run history", async () => {
    mocks.goals = [goalFixture()];
    mocks.detail = {
      ...goalFixture(),
      runs: [
        {
          id: "run-1",
          status: "completed",
          model_status: "OPTIMAL",
          created_at: "2026-09-20T09:00:00+08:00",
          goal_report: {
            goal_id: "goal-1",
            instruction: "排好 B1 班 10 月第一周的课，只出草稿",
            all_passed: false,
            passed_count: 1,
            failed_count: 1,
            items: [
              { key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", passed: true, detail: "命中 1/1" },
              { key: "forbidden_slot_free-1", requirement: "T9 不占 S1", kind: "forbidden_slot_free", passed: false, detail: "禁排时段仍被占用 1 处：C24" },
            ],
            gaps: [
              { key: "forbidden_slot_free-1", kind: "forbidden_slot_free", summary: "禁排时段仍被占用", next_step: "等待教务放宽或调整排课" },
            ],
            decision: { status: "awaiting_decision", reason: "1 项未通过" },
          },
        },
        { id: "run-0", status: "failed", model_status: null, created_at: "2026-09-20T08:30:00+08:00" },
      ],
      latest_report: null,
    };
    mocks.detail.latest_report = (mocks.detail.runs as Array<Record<string, unknown>>)[0].goal_report;
    renderPage();
    await user.click(await screen.findByRole("button", { name: "详情" }));
    expect(await screen.findByText("目标详情")).toBeInTheDocument();
    expect(screen.getByText(/逐项验收结论/)).toBeInTheDocument();
    // 清单与最新验收里各出现一次「课次覆盖」。
    expect(screen.getAllByText(/课次覆盖/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/验收 1\/2 项通过/)).toBeInTheDocument();
    expect(screen.getByText(/等待教务放宽或调整排课/)).toBeInTheDocument();
    expect(screen.getByText(/尚无验收报告/)).toBeInTheDocument(); // failed run 没有报告
  });

  it("abandons a goal from the list", async () => {
    mocks.goals = [goalFixture()];
    renderPage();
    await user.click(await screen.findByRole("button", { name: "放弃" }));
    const dialog = await screen.findByText("放弃这个目标？");
    expect(dialog).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "放弃目标" }));
    await waitFor(() => expect(mocks.abandon).toHaveBeenCalledWith({ goalId: "goal-1" }));
  });
});
