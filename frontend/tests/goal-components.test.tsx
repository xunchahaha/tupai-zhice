import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ForbiddenParamForm } from "@/components/goal/forbidden-param-form";
import { GoalAcceptanceReport } from "@/components/goal/goal-acceptance-report";
import { GoalChecklist } from "@/components/goal/goal-checklist";
import { GoalRunList } from "@/components/goal/goal-run-list";
import { GoalSupplementPanel } from "@/components/goal/goal-supplement-panel";
import { goalSubjectTypeLabel, parseGoalReport } from "@/lib/goal";

const mocks = vi.hoisted(() => ({
  patch: vi.fn(),
}));

vi.mock("@/api/generated/client", () => ({
  getGetGoalApiV1GoalsGoalIdGetQueryKey: (goalId?: string) => ["goal-detail", goalId],
  useReplaceGoalChecklistApiV1GoalsGoalIdChecklistPatch: (config?: { mutation?: { onSuccess?: (data: unknown) => void } }) => ({
    mutate: (vars: unknown) => {
      mocks.patch(vars);
      config?.mutation?.onSuccess?.({ checklist_version: 2 });
    },
    isPending: false,
  }),
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

const PLACEHOLDER = {
  key: "forbidden_slot_free-draft",
  requirement: "禁排要求待量化：补充主体与具体时段后才能独立复核",
  kind: "forbidden_slot_free",
  params: { subject_type: "teacher", subject_ids: [], slot_business_ids: [], needs_params: true },
};
const COVERAGE = { key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", params: { bottom_line: true } };

function goalFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: "goal-1",
    schedule_set_id: "s1",
    instruction: "排好 B1 班 10 月第一周的课，只出草稿",
    checklist: [COVERAGE, PLACEHOLDER],
    checklist_version: 1,
    checklist_history: [],
    status: "open",
    run_count: 0,
    runs: [],
    created_at: "2026-09-20T08:00:00+08:00",
    updated_at: "2026-09-20T09:00:00+08:00",
    ...overrides,
  } as never;
}

function wrap(node: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>,
  );
}

describe("goal components", () => {
  const user = userEvent.setup();
  afterEach(cleanup);
  beforeEach(() => mocks.patch.mockClear());

  describe("ForbiddenParamForm", () => {
    it("keeps save disabled until a subject and a slot are picked, then reports them", async () => {
      const onSave = vi.fn();
      render(
        <ForbiddenParamForm
          teacherOptions={[{ business_id: "T9", name: "教师九" } as never]}
          classOptions={[]}
          roomOptions={[]}
          slotOptions={[{ business_id: "S1", weekday: "周一", start_time: "08:30", end_time: "11:30" } as never]}
          onCancel={vi.fn()}
          onSave={onSave}
        />,
      );
      expect(screen.getByRole("button", { name: "保存参数" })).toBeDisabled();
      await user.selectOptions(screen.getByLabelText("禁排主体"), "T9");
      await user.selectOptions(screen.getByLabelText("添加禁排时段"), "S1");
      await user.click(screen.getByRole("button", { name: "保存参数" }));
      expect(onSave).toHaveBeenCalledWith("teacher", ["T9"], ["S1"]);
      expect(goalSubjectTypeLabel("cohort")).toBe("班级");
    });
  });

  describe("GoalSupplementPanel", () => {
    it("renders nothing when no forbidden placeholder needs parameters", () => {
      const { container } = wrap(<GoalSupplementPanel goal={goalFixture({ checklist: [COVERAGE] })} />);
      expect(container).toBeEmptyDOMElement();
    });

    it("lists each needs_params item with a collapsed 补齐参数 entry", async () => {
      wrap(<GoalSupplementPanel goal={goalFixture()} />);
      expect(screen.getByText(/禁排要求待量化/)).toBeInTheDocument();
      // 只列出待补项，不带出其它清单项。
      expect(screen.queryByText(/覆盖全部目标课次/)).not.toBeInTheDocument();
      expect(screen.queryByLabelText("禁排主体类型")).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "补齐参数" }));
      expect(screen.getByLabelText("禁排主体类型")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "取消" }));
      expect(screen.queryByLabelText("禁排主体类型")).not.toBeInTheDocument();
    });

    it("opens the form up front with autoOpen and saves the whole checklist through the PATCH, then calls onSaved", async () => {
      const onSaved = vi.fn();
      wrap(<GoalSupplementPanel goal={goalFixture()} autoOpen onSaved={onSaved} />);
      await user.selectOptions(screen.getByLabelText("禁排主体"), "T9");
      await user.selectOptions(screen.getByLabelText("添加禁排时段"), "S2");
      await user.click(screen.getByRole("button", { name: "保存参数" }));
      await waitFor(() => expect(mocks.patch).toHaveBeenCalledTimes(1));
      const call = mocks.patch.mock.calls[0][0] as { goalId: string; data: { checklist: Array<Record<string, unknown>> } };
      expect(call.goalId).toBe("goal-1");
      // 整份替换：底线项原样保留，占位项被量化后的禁排项替换。
      expect(call.data.checklist).toHaveLength(2);
      expect(call.data.checklist[0]).toMatchObject({ key: "coverage" });
      expect(call.data.checklist[1]).toMatchObject({
        key: "forbidden_slot_free-draft",
        params: { subject_type: "teacher", subject_ids: ["T9"], slot_business_ids: ["S2"] },
      });
      expect(call.data.checklist[1].params).not.toHaveProperty("needs_params", true);
      expect(onSaved).toHaveBeenCalledTimes(1);
      // 保存成功后该项的表单收起。
      expect(screen.queryByLabelText("禁排主体类型")).not.toBeInTheDocument();
    });
  });

  describe("GoalChecklist", () => {
    it("is a read-only list without any supplement entry unless the host asks for one", () => {
      wrap(<GoalChecklist goal={goalFixture()} />);
      expect(screen.getByText(/验收清单 v1/)).toBeInTheDocument();
      expect(screen.getByText("底线")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "补齐参数" })).not.toBeInTheDocument();
    });

    it("reports the item key on 补齐参数 and expands the inline form only for keys the host opened", async () => {
      const onQuantizeRequest = vi.fn();
      const onParamFormClose = vi.fn();
      const { rerender } = wrap(<GoalChecklist goal={goalFixture()} onQuantizeRequest={onQuantizeRequest} />);
      await user.click(screen.getByRole("button", { name: "补齐参数" }));
      expect(onQuantizeRequest).toHaveBeenCalledWith("forbidden_slot_free-draft");
      expect(screen.queryByLabelText("禁排主体类型")).not.toBeInTheDocument();
      rerender(
        <QueryClientProvider client={new QueryClient()}>
          <GoalChecklist
            goal={goalFixture()}
            onQuantizeRequest={onQuantizeRequest}
            paramFormKeys={["forbidden_slot_free-draft"]}
            onParamFormClose={onParamFormClose}
          />
        </QueryClientProvider>,
      );
      expect(screen.getByLabelText("禁排主体类型")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "取消" }));
      expect(onParamFormClose).toHaveBeenCalledWith("forbidden_slot_free-draft");
    });

    it("folds the checklist history and shows the newest version first once expanded", async () => {
      wrap(
        <GoalChecklist
          goal={goalFixture({
            checklist_version: 3,
            checklist_history: [
              { version: 1, saved_at: "2026-09-20T08:00:00+08:00", items: [{ key: "a", kind: "draft_only", requirement: "只交付草稿" }] },
              { version: 2, saved_at: "2026-09-21T08:00:00+08:00", items: [{ key: "a", kind: "draft_only", requirement: "改口径" }] },
            ],
          })}
        />,
      );
      expect(screen.queryByText(/v2 · 保存于/)).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: /历史版本（2）/ }));
      const snapshots = screen.getAllByText(/· 保存于/);
      expect(snapshots[0]).toHaveTextContent("v2");
      expect(snapshots[1]).toHaveTextContent("v1");
    });
  });

  describe("GoalAcceptanceReport", () => {
    const gapReport = (remedy: string) =>
      parseGoalReport({
        goal_id: "goal-1",
        all_passed: false,
        passed_count: 0,
        failed_count: 1,
        items: [{ key: "coverage", kind: "coverage", passed: false, detail: "缺 C2" }],
        gaps: [{ key: "coverage", kind: "coverage", summary: "缺课", next_step: "下一步", remedy }],
        decision: null,
      });

    it("renders nothing without a report", () => {
      const { container } = render(<GoalAcceptanceReport goal={goalFixture()} report={null} />);
      expect(container).toBeEmptyDOMElement();
    });

    it("calls back for raise_budget and resolve_scope gaps instead of navigating", async () => {
      const onRaiseBudget = vi.fn();
      const onResolveScope = vi.fn();
      const { rerender } = render(
        <GoalAcceptanceReport goal={goalFixture()} report={gapReport("raise_budget")} onRaiseBudget={onRaiseBudget} onResolveScope={onResolveScope} />,
      );
      await user.click(screen.getByRole("button", { name: "加大时间预算重跑" }));
      expect(onRaiseBudget).toHaveBeenCalledTimes(1);
      rerender(
        <GoalAcceptanceReport goal={goalFixture()} report={gapReport("resolve_scope")} onRaiseBudget={onRaiseBudget} onResolveScope={onResolveScope} />,
      );
      await user.click(screen.getByRole("button", { name: "修正范围" }));
      expect(onResolveScope).toHaveBeenCalledTimes(1);
    });

    it("shows the fix_checklist button only when the checklist still has a placeholder to fill", async () => {
      const onFixChecklist = vi.fn();
      const { rerender } = render(
        <GoalAcceptanceReport goal={goalFixture()} report={gapReport("fix_checklist")} onFixChecklist={onFixChecklist} />,
      );
      await user.click(screen.getByRole("button", { name: "补充条件" }));
      expect(onFixChecklist).toHaveBeenCalledTimes(1);
      rerender(
        <GoalAcceptanceReport goal={goalFixture({ checklist: [COVERAGE] })} report={gapReport("fix_checklist")} onFixChecklist={onFixChecklist} />,
      );
      expect(screen.queryByRole("button", { name: "补充条件" })).not.toBeInTheDocument();
    });

    it("keeps await_admin as text and shows no buttons even when callbacks are given", () => {
      render(
        <GoalAcceptanceReport
          goal={goalFixture()}
          report={gapReport("await_admin")}
          onRaiseBudget={vi.fn()}
          onResolveScope={vi.fn()}
          onFixChecklist={vi.fn()}
        />,
      );
      expect(screen.getByText("下一步")).toBeInTheDocument();
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });

    it("replaces a stale report with 等待新验收 while acceptance is pending after a revision", () => {
      const stale = parseGoalReport({
        goal_id: "goal-1",
        all_passed: true,
        passed_count: 1,
        failed_count: 0,
        meta: { checklist_version: 1 },
        items: [{ key: "coverage", kind: "coverage", passed: true, detail: "命中" }],
        gaps: [],
        decision: null,
      });
      render(<GoalAcceptanceReport goal={goalFixture({ acceptance_status: "pending", checklist_version: 2 })} report={stale} />);
      expect(screen.getByText(/等待新验收（v2）/)).toBeInTheDocument();
      expect(screen.queryByText("最新验收")).not.toBeInTheDocument();
    });

    it("marks both bottom-line items (coverage and no_duplicate_lessons) with the 底线 badge, and no other item", () => {
      const report = parseGoalReport({
        goal_id: "goal-1",
        all_passed: true,
        passed_count: 3,
        failed_count: 0,
        meta: { checklist_version: 1 },
        items: [
          { key: "coverage", kind: "coverage", passed: true, bottom_line: true, detail: "命中全部课次" },
          { key: "no_duplicate_lessons", kind: "no_duplicate_lessons", passed: true, bottom_line: true, detail: "无重复" },
          { key: "draft_only", kind: "draft_only", passed: true, detail: "只出草稿" },
        ],
        gaps: [],
        decision: null,
      });
      render(<GoalAcceptanceReport goal={goalFixture()} report={report} />);
      const rows = screen.getAllByRole("listitem");
      expect(rows).toHaveLength(3);
      expect(rows[0]).toHaveTextContent("底线");
      expect(rows[1]).toHaveTextContent("课次不重复");
      expect(rows[1]).toHaveTextContent("底线");
      expect(rows[2]).not.toHaveTextContent("底线");
      expect(screen.getAllByText("底线")).toHaveLength(2);
    });

    it("shows the report's version_note when the solve and acceptance checklist versions differ", () => {
      const note = "本次求解按清单 v1 进行，验收按 v2 口径出具";
      const report = parseGoalReport({
        goal_id: "goal-1",
        all_passed: true,
        passed_count: 1,
        failed_count: 0,
        meta: { checklist_version: 2, solve_checklist_version: 1, version_note: note },
        items: [{ key: "coverage", kind: "coverage", passed: true, detail: "命中" }],
        gaps: [],
        decision: null,
      });
      render(<GoalAcceptanceReport goal={goalFixture({ checklist_version: 2 })} report={report} />);
      expect(screen.getByText(note)).toBeInTheDocument();
    });

    it("says nothing extra when the report carries no version_note", () => {
      const report = parseGoalReport({
        goal_id: "goal-1",
        all_passed: true,
        passed_count: 1,
        failed_count: 0,
        meta: { checklist_version: 1 },
        items: [{ key: "coverage", kind: "coverage", passed: true, detail: "命中" }],
        gaps: [],
        decision: null,
      });
      render(<GoalAcceptanceReport goal={goalFixture()} report={report} />);
      expect(screen.queryByText(/本次求解按清单/)).not.toBeInTheDocument();
    });

    it("marks unverifiable items and a failed acceptance run", () => {
      const failed = parseGoalReport({
        goal_id: "goal-1",
        all_passed: false,
        passed_count: 0,
        failed_count: 0,
        acceptance_status: "failed",
        acceptance_error: "验收器超时",
        items: [{ key: "date", kind: "date_range_match", passed: false, verdict: "unverifiable", detail: "缺日期" }],
        gaps: [],
        decision: null,
      });
      render(<GoalAcceptanceReport goal={goalFixture()} report={failed} />);
      expect(screen.getByText("无法验证")).toBeInTheDocument();
      expect(screen.getByText(/验收失败：验收器超时/)).toBeInTheDocument();
    });
  });

  describe("GoalRunList", () => {
    it("shows an empty hint without runs", () => {
      render(<GoalRunList goal={goalFixture()} />);
      expect(screen.getByText("求解记录（0）")).toBeInTheDocument();
      expect(screen.getByText("还没有关联的求解任务。")).toBeInTheDocument();
    });

    it("shows per-run acceptance and marks conclusions from an older checklist version", () => {
      render(
        <GoalRunList
          goal={goalFixture({
            checklist_version: 2,
            runs: [
              {
                id: "run-aaaaaaaa-1",
                status: "completed",
                model_status: "OPTIMAL",
                created_at: "2026-09-20T09:00:00+08:00",
                goal_report: {
                  goal_id: "goal-1",
                  all_passed: true,
                  passed_count: 1,
                  failed_count: 0,
                  meta: { checklist_version: 1 },
                  items: [{ key: "coverage", kind: "coverage", passed: true, detail: "命中" }],
                  gaps: [],
                  decision: null,
                },
              },
              { id: "run-bbbbbbbb-2", status: "failed", model_status: null, created_at: "2026-09-20T08:30:00+08:00" },
            ],
          })}
        />,
      );
      expect(screen.getByText("求解记录（2）")).toBeInTheDocument();
      expect(screen.getByText(/验收 1\/1 项通过/)).toBeInTheDocument();
      expect(screen.getByText("v1 结论")).toBeInTheDocument();
      expect(screen.getByText(/尚无验收报告/)).toBeInTheDocument();
    });
  });
});
