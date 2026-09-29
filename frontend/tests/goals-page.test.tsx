import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GoalsPage } from "@/pages/goals-page";

const mocks = vi.hoisted(() => ({
  goals: [] as Array<Record<string, unknown>>,
  detail: undefined as Record<string, unknown> | undefined,
  abandon: vi.fn(),
  patch: vi.fn(),
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
  useReplaceGoalChecklistApiV1GoalsGoalIdChecklistPatch: (config?: { mutation?: { onSuccess?: (data: unknown) => void; onError?: (e: unknown) => void } }) => ({
    mutate: (vars: unknown) => {
      mocks.patch(vars);
      config?.mutation?.onSuccess?.({ checklist_version: 2 });
    },
    isPending: false,
  }),
  // 主数据 hooks：补参表单与收件箱共用；这里给出最小可选项。
  useListTeachersApiV1TeachersGet: () => ({ data: [{ id: "t1", business_id: "T9", name: "教师九" }] }),
  useListClassGroupsApiV1ClassGroupsGet: () => ({ data: [{ id: "c1", business_id: "B1", name: "B1 班" }] }),
  useListRoomsApiV1RoomsGet: () => ({ data: [{ id: "r1", business_id: "R1", name: "教室1" }] }),
  useListTimeSlotsApiV1TimeSlotsGet: () => ({
    data: [
      { id: "s1", business_id: "S1", weekday: "周一", start_time: "08:30", end_time: "11:30" },
      { id: "s2", business_id: "S2", weekday: "周二", start_time: "08:30", end_time: "11:30" },
    ],
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
    mocks.patch.mockClear();
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

// MEM-D3 目标连续性闭环：继续处理入口、禁排占位补参、清单版本与历史。
describe("GoalsPage goal continuity (MEM-D3)", () => {
  const user = userEvent.setup();
  afterEach(cleanup);
  beforeEach(() => {
    mocks.abandon.mockClear();
    mocks.patch.mockClear();
    mocks.detail = undefined;
  });

  it("opens the detail with a continue section listing full decision text and action buttons for awaiting_decision", async () => {
    mocks.goals = [goalFixture()];
    mocks.detail = {
      ...goalFixture(),
      checklist_version: 1,
      checklist_history: [],
      runs: [],
      latest_report: {
        goal_id: "goal-1",
        instruction: "排好 B1 班 10 月第一周的课，只出草稿",
        all_passed: false,
        passed_count: 1,
        failed_count: 1,
        items: [],
        gaps: [],
        // awaiting_decision 的决策文案必须完整展示（不截断）。
        decision: {
          status: "awaiting_decision",
          reason: "1 项未通过；其中存在必须由教务放宽或裁决的缺口：求解未能安置 C24，需要教务调整规则或数据后重排（进入规则/数据调整流程）",
        },
      },
    };
    renderPage();
    await user.click(await screen.findByRole("button", { name: "详情" }));
    expect(await screen.findByText("继续处理")).toBeInTheDocument();
    // 完整决策文案逐字可见。
    expect(screen.getByText(/必须由教务放宽或裁决的缺口：求解未能安置 C24/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修正范围后重新求解" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "放弃目标" })).toBeInTheDocument();
  });

  it("offers the param form inline and quantizes a needs_params forbidden item through the checklist PATCH", async () => {
    mocks.goals = [goalFixture({ status: "open" })];
    mocks.detail = {
      ...goalFixture({ status: "open" }),
      checklist_version: 1,
      checklist_history: [],
      runs: [],
      latest_report: null,
      checklist: [
        {
          key: "forbidden_slot_free-draft",
          requirement: "禁排要求待量化：补充主体与具体时段后才能独立复核",
          kind: "forbidden_slot_free",
          params: { subject_type: "teacher", subject_ids: [], slot_business_ids: [], needs_params: true },
        },
      ],
    };
    renderPage();
    await user.click(await screen.findByRole("button", { name: "详情" }));
    // open 状态同样有继续处理入口 + 补齐禁排参数按钮。
    await screen.findByText("继续处理");
    await user.click(screen.getByRole("button", { name: "补齐禁排参数" }));
    // 内联表单：主体类型/主体下拉 + 时段多选。
    expect(screen.getByLabelText("禁排主体类型")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("禁排主体类型"), "teacher");
    await user.selectOptions(screen.getByLabelText("禁排主体"), "T9");
    await user.selectOptions(screen.getByLabelText("添加禁排时段"), "S1");
    await user.click(screen.getByRole("button", { name: "保存参数" }));

    await waitFor(() => expect(mocks.patch).toHaveBeenCalledTimes(1));
    const call = mocks.patch.mock.calls[0][0] as {
      goalId: string;
      data: { checklist: Array<{ key: string; kind: string; params: Record<string, unknown> }> };
    };
    expect(call.goalId).toBe("goal-1");
    // PATCH body 是完整清单（量化后的禁排项替换占位项），kind 走同一白名单。
    expect(call.data.checklist).toHaveLength(1);
    expect(call.data.checklist[0]).toMatchObject({
      key: "forbidden_slot_free-draft",
      kind: "forbidden_slot_free",
      params: { subject_type: "teacher", subject_ids: ["T9"], slot_business_ids: ["S1"] },
    });
    expect(call.data.checklist[0].params).not.toHaveProperty("needs_params", true);
  });

  it("shows the checklist version and expands read-only history snapshots", async () => {
    mocks.goals = [goalFixture()];
    mocks.detail = {
      ...goalFixture(),
      checklist_version: 3,
      checklist_history: [
        { version: 1, saved_at: "2026-09-20T08:00:00+08:00", saved_by: null, items: [{ key: "a", kind: "draft_only", requirement: "只交付草稿" }] },
        { version: 2, saved_at: "2026-09-21T08:00:00+08:00", saved_by: null, items: [{ key: "a", kind: "draft_only", requirement: "改口径：仍只出草稿" }] },
      ],
      runs: [],
      latest_report: null,
    };
    renderPage();
    await user.click(await screen.findByRole("button", { name: "详情" }));
    // 清单版本号 v{n} 展示（当前=历史长度+1=3）。
    expect(await screen.findByText(/验收清单 v3/)).toBeInTheDocument();
    // 历史默认折叠，展开后按版本倒序只读展示。
    expect(screen.queryByText(/v2 · 保存于/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /历史版本（2）/ }));
    expect(await screen.findByText(/v2 · 保存于/)).toBeInTheDocument();
    expect(screen.getByText(/v1 · 保存于/)).toBeInTheDocument();
    expect(screen.getByText(/改口径：仍只出草稿/)).toBeInTheDocument();
  });
});

// MEM-E2：清单版本与验收结论对齐 + 底线项展示。
describe("GoalsPage version alignment (MEM-E2)", () => {
  const user = userEvent.setup();
  afterEach(cleanup);
  beforeEach(() => {
    mocks.abandon.mockClear();
    mocks.patch.mockClear();
    mocks.detail = undefined;
  });

  it("shows 等待新验收（v{n}） in the current-conclusion area while pending after a revision", async () => {
    // 清单修订到 v2、验收 pending：旧 v1 报告不得再当「当前结论」展示。
    mocks.goals = [goalFixture({ acceptance_status: "pending", status: "open" })];
    mocks.detail = {
      ...goalFixture({ acceptance_status: "pending", status: "open" }),
      acceptance_detail: "清单修订至 v2，等待新验收",
      checklist_version: 2,
      checklist_history: [{ version: 1, saved_at: "2026-09-20T08:00:00+08:00", saved_by: null, items: [] }],
      runs: [
        {
          id: "run-1",
          status: "completed",
          model_status: "OPTIMAL",
          created_at: "2026-09-20T09:00:00+08:00",
          goal_report: {
            goal_id: "goal-1",
            all_passed: true,
            passed_count: 2,
            failed_count: 0,
            meta: { checklist_version: 1 },
            items: [
              { key: "coverage", kind: "coverage", passed: true, detail: "命中 1/1" },
            ],
            gaps: [],
            decision: { status: "achieved", reason: "全部通过" },
          },
        },
      ],
      // 修订后 latest_run 的旧报告仍在（GET 口径），但 acceptance_status=pending。
      latest_report: {
        goal_id: "goal-1",
        all_passed: true,
        passed_count: 2,
        failed_count: 0,
        meta: { checklist_version: 1 },
        items: [{ key: "coverage", kind: "coverage", passed: true, detail: "命中 1/1" }],
        gaps: [],
        decision: { status: "achieved", reason: "全部通过" },
      },
    };
    renderPage();
    await user.click(await screen.findByRole("button", { name: "详情" }));
    // 当前结论区显示「等待新验收（v2）」，旧报告不再当「最新验收」展示。
    expect(await screen.findByText(/等待新验收（v2）/)).toBeInTheDocument();
    expect(screen.getByText(/清单已修订至 v2/)).toBeInTheDocument();
    expect(screen.queryByText("最新验收")).not.toBeInTheDocument();
    // 旧结论在求解记录里按历史版本标注。
    expect(screen.getByText("v1 结论")).toBeInTheDocument();
  });

  it("marks a non-current-version report as 历史版本结论 in the latest-acceptance area", async () => {
    // acceptance_status=completed 但最新报告还是 v1（修订后又验收过一次之前的版本……
    // 直接场景：report 版本 < goal 版本且已完成验收 → 徽标「历史版本 v1 的结论」。
    mocks.goals = [goalFixture()];
    mocks.detail = {
      ...goalFixture(),
      checklist_version: 2,
      checklist_history: [{ version: 1, saved_at: "2026-09-20T08:00:00+08:00", saved_by: null, items: [] }],
      runs: [],
      latest_report: {
        goal_id: "goal-1",
        all_passed: false,
        passed_count: 1,
        failed_count: 1,
        meta: { checklist_version: 1 },
        items: [{ key: "coverage", kind: "coverage", passed: false, detail: "缺 C2" }],
        gaps: [],
        decision: { status: "open", reason: "1 项未通过" },
      },
    };
    renderPage();
    await user.click(await screen.findByRole("button", { name: "详情" }));
    expect(await screen.findByText(/历史版本 v1 的结论/)).toBeInTheDocument();
  });

  it("shows bottom-line badges for both coverage and no_duplicate_lessons checklist items", async () => {
    mocks.goals = [goalFixture()];
    mocks.detail = {
      ...goalFixture(),
      checklist_version: 1,
      checklist_history: [],
      runs: [],
      latest_report: null,
      checklist: [
        { key: "coverage", requirement: "覆盖目标课次", kind: "coverage", params: { bottom_line: true, class_business_ids: ["B1"] } },
        { key: "no_duplicate_lessons", requirement: "交付课次不重复", kind: "no_duplicate_lessons", params: { bottom_line: true } },
        { key: "draft_only", requirement: "只交付草稿", kind: "draft_only", params: {} },
      ],
    };
    renderPage();
    await user.click(await screen.findByRole("button", { name: "详情" }));
    const badges = await screen.findAllByText("底线");
    // coverage 与 no_duplicate_lessons 两项底线徽标都显示。
    expect(badges).toHaveLength(2);
    expect(screen.getByText("课次覆盖")).toBeInTheDocument();
    expect(screen.getAllByText("课次不重复").length).toBeGreaterThan(0);
  });

  it("renders the version_note from report meta when solve and acceptance versions differ", async () => {
    mocks.goals = [goalFixture()];
    mocks.detail = {
      ...goalFixture(),
      checklist_version: 3,
      checklist_history: [],
      runs: [],
      latest_report: {
        goal_id: "goal-1",
        all_passed: true,
        passed_count: 1,
        failed_count: 0,
        meta: {
          checklist_version: 3,
          solve_checklist_version: 2,
          version_note: "求解参数基于 v2 清单生成，验收按当前最新版 v3",
        },
        items: [{ key: "coverage", kind: "coverage", passed: true, detail: "命中" }],
        gaps: [],
        decision: { status: "achieved", reason: "全部通过" },
      },
    };
    renderPage();
    await user.click(await screen.findByRole("button", { name: "详情" }));
    expect(
      await screen.findByText(/求解参数基于 v2 清单生成，验收按当前最新版 v3/),
    ).toBeInTheDocument();
  });
});

// TC-6 §5.2：缺口 remedy 动作化——raise_budget/fix_checklist/resolve_scope 出按钮，
// await_admin 保持文本（人的裁决）。
describe("GoalsPage remedy actions (TC-6)", () => {
  const user = userEvent.setup();
  afterEach(cleanup);
  beforeEach(() => {
    mocks.abandon.mockClear();
    mocks.patch.mockClear();
    mocks.detail = undefined;
  });

  function LocationProbe() {
    const location = useLocation();
    return <div data-testid="location-probe">{location.pathname}{location.search}</div>;
  }

  function renderWithProbe(initialEntry = "/goals") {
    return render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/goals" element={<GoalsPage />} />
            <Route path="*" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  function detailWithGaps(gaps: Array<Record<string, unknown>>, checklist: Array<Record<string, unknown>> = goalFixture().checklist as Array<Record<string, unknown>>) {
    return {
      ...goalFixture(),
      checklist_version: 1,
      checklist_history: [],
      runs: [],
      checklist,
      latest_report: {
        goal_id: "goal-1",
        all_passed: false,
        passed_count: 0,
        failed_count: gaps.length,
        items: gaps.map((gap) => ({ key: gap.key, requirement: gap.summary, kind: gap.kind, passed: false, detail: String(gap.summary) })),
        gaps,
        decision: { status: "awaiting_decision", reason: "存在待处理缺口" },
      },
    };
  }

  it("renders a raise-budget remedy button that deep-links to the solver page", async () => {
    mocks.goals = [goalFixture()];
    mocks.detail = detailWithGaps([
      { key: "coverage", kind: "coverage", summary: "求解未安置全部课次", next_step: "加大时间预算后重跑", remedy: "raise_budget" },
    ]);
    renderWithProbe();
    await user.click(await screen.findByRole("button", { name: "详情" }));
    await user.click(await screen.findByRole("button", { name: "加大时间预算重跑" }));
    // 跳 /solver?goal=<id>&action=raise_budget：由求解页挂载解析 action 一键重跑。
    expect(screen.getByTestId("location-probe")).toHaveTextContent("/solver?goal=goal-1&action=raise_budget");
  });

  it("opens the checklist param form from a fix_checklist remedy button", async () => {
    mocks.goals = [goalFixture({ status: "open" })];
    mocks.detail = detailWithGaps(
      [
        { key: "forbidden_slot_free-draft", kind: "forbidden_slot_free", summary: "禁排参数缺失", next_step: "在目标清单中补齐参数后重跑", remedy: "fix_checklist" },
      ],
      [
        {
          key: "forbidden_slot_free-draft",
          requirement: "禁排要求待量化：补充主体与具体时段后才能独立复核",
          kind: "forbidden_slot_free",
          params: { subject_type: "teacher", subject_ids: [], slot_business_ids: [], needs_params: true },
        },
      ],
    );
    renderWithProbe();
    await user.click(await screen.findByRole("button", { name: "详情" }));
    await user.click(await screen.findByRole("button", { name: "修订目标清单" }));
    // 复用 goals-page 既有清单补参锚点：needs_params 项的表单展开。
    expect(screen.getByLabelText("禁排主体类型")).toBeInTheDocument();
  });

  it("deep-links a resolve_scope remedy back to the solver page", async () => {
    mocks.goals = [goalFixture()];
    mocks.detail = detailWithGaps([
      { key: "coverage", kind: "coverage", summary: "范围与目标清单不一致", next_step: "回求解页修正范围后重跑", remedy: "resolve_scope" },
    ]);
    renderWithProbe();
    await user.click(await screen.findByRole("button", { name: "详情" }));
    await user.click(await screen.findByRole("button", { name: "回求解页修正范围" }));
    expect(screen.getByTestId("location-probe")).toHaveTextContent("/solver?goal=goal-1&action=resolve_scope");
  });

  it("keeps await_admin gaps as text without any action button", async () => {
    mocks.goals = [goalFixture()];
    mocks.detail = detailWithGaps([
      { key: "forbidden_slot_free-1", kind: "forbidden_slot_free", summary: "禁排时段仍被占用", next_step: "等待教务放宽或调整排课", remedy: "await_admin" },
    ]);
    renderWithProbe();
    await user.click(await screen.findByRole("button", { name: "详情" }));
    expect(await screen.findByText(/等待教务放宽或调整排课/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "加大时间预算重跑" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "回求解页修正范围" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "修订目标清单" })).not.toBeInTheDocument();
  });
});

describe("GoalsPage deep link from the solver page (later-registration exit)", () => {
  afterEach(cleanup);

  it("opens the goal named by ?goal= straight into the continue section with the param form entry", async () => {
    // 「登记为目标，稍后补充」跳来时带 ?goal=<id>：落地就是详情里的「补齐禁排参数」，
    // 不必在列表里找刚创建的那一行。
    mocks.goals = [goalFixture({ id: "goal-later", status: "open" })];
    mocks.detail = {
      ...goalFixture({ id: "goal-later", status: "open" }),
      checklist_version: 1,
      checklist_history: [],
      runs: [],
      latest_report: null,
      checklist: [
        {
          key: "forbidden_slot_free-draft",
          requirement: "禁排要求待量化：补充主体与具体时段后才能独立复核",
          kind: "forbidden_slot_free",
          params: { subject_type: "teacher", subject_ids: [], slot_business_ids: [], needs_params: true },
        },
      ],
    };
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/goals?goal=goal-later"]}>
          <GoalsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("目标详情")).toBeInTheDocument();
    expect(await screen.findByText("继续处理")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "补齐禁排参数" })).toBeInTheDocument();
  });
});
