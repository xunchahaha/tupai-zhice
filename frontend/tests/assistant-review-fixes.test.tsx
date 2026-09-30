import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderAssistant } from "./support/assistant-render";
import {
  assistantMocks as mocks,
  goalFixture,
  interpretationFixture,
  mockAiConfigured,
  resetAssistantMocks,
  runFixture,
  scheduleFixture,
} from "./support/assistant-mocks";

vi.mock("@/api/generated/client", async () => (await import("./support/assistant-mocks")).clientMock);
vi.mock("@/api/http", async () => (await import("./support/assistant-mocks")).httpMock);
vi.mock("@/lib/interpret-stream", async () => (await import("./support/assistant-mocks")).interpretStreamMock);

const MANUAL = "手动排课（自己设置参数）";
const EXPLAINED = { headline: "已解释", explanation: [], next_actions: [], source: "system" };

const ANALYTICS = {
  teacher_workload: { total_teachers: 0, assigned_teachers: 0, total_hours: 0, top_teachers: [], buckets: [] },
  room_heatmap: { period_cells: [] },
  optimization_penalties: { solver_run_id: null, solver_status: null, objective_value: null, best_bound: null, total_soft_penalty: null, reconciliation_error: null, evaluated_assignment_count: 0, soft_constraints: [] },
  sync_health: { total_syncs: 0, completed_syncs: 0, failed_syncs: 0, retry_count: 0, retry_samples: 0, records_read: 0, records_written: 0, average_duration_ms: null, latest_sync_at: null },
};

beforeEach(resetAssistantMocks);
afterEach(cleanup);

describe("a ?goal= deep link keeps its task on a manual solve (hygiene.F13)", () => {
  it("carries goal_id and shows the linked-task badge when solving by parameters", async () => {
    const user = userEvent.setup();
    mocks.goalDetail = goalFixture({ id: "goal-77" });
    renderAssistant("/assistant?goal=goal-77");
    await screen.findByText(/已接着办这个任务/);
    await user.click(screen.getByRole("button", { name: MANUAL }));
    expect(await screen.findByText(/本次求解关联任务/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "按参数开始求解" }));
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    const submitted = (mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> }).data;
    expect(submitted.goal_id).toBe("goal-77");
  });

  it("does not claim a link, nor send goal_id, for a task that is already abandoned", async () => {
    const user = userEvent.setup();
    mocks.goalDetail = goalFixture({ id: "goal-77", status: "abandoned" });
    renderAssistant("/assistant?goal=goal-77");
    await screen.findByText(/这个任务已放弃/);
    await user.click(screen.getByRole("button", { name: MANUAL }));
    await screen.findByRole("heading", { name: "手动排课参数" });
    expect(screen.queryByText(/本次求解关联任务/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "按参数开始求解" }));
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    expect((mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> }).data.goal_id).toBeNull();
  });
});

describe("read-only diagnosis for accounts that cannot schedule (coverage.F3)", () => {
  const stuckRun = () => {
    mocks.rules = [{ id: "id-R-1", business_id: "R-1", source_text: "T9 周一上午不排课", actor_type: "teacher", actor_ids: ["T9"], constraint_type: "forbidden_slot", status: "active" }];
    mocks.runDetails["run-x"] = runFixture({
      id: "run-x",
      model_status: "INFEASIBLE",
      conflict_rule_ids: ["R-1"],
      priority_explanations: ["建议先放宽 T9 的周一上午限制"],
      explanation: { ...EXPLAINED, next_actions: ["请排课员调整范围后重排"] },
    });
  };

  it.each(["approver", "viewer"] as const)("a %s opening ?run= sees why it got stuck, the rules involved and next steps, with no submit buttons", async (role) => {
    stuckRun();
    renderAssistant("/assistant?run=run-x", role);
    const region = await screen.findByLabelText("求解诊断（只读）");
    expect(within(region).getByText("在现有的硬性要求下排不出全部课")).toBeInTheDocument();
    expect(await within(region).findByText("冲突核心分析")).toBeInTheDocument();
    expect(within(region).getByText("T9 周一上午不排课")).toBeInTheDocument();
    expect(within(region).getByText("请排课员调整范围后重排")).toBeInTheDocument();
    // 重新解析、加预算、修正范围都是排课员的动作：这里一个按钮都不能有。
    expect(within(region).queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByLabelText("排课需求")).not.toBeInTheDocument();
    expect(mocks.submitMutate).not.toHaveBeenCalled();
    expect(mocks.stream).not.toHaveBeenCalled();
  });

  it("a scheduler who is read-only in this schedule set gets the same read-only view", async () => {
    stuckRun();
    renderAssistant("/assistant?run=run-x", "scheduler", { accessRole: "viewer" });
    const region = await screen.findByLabelText("求解诊断（只读）");
    expect(region).toBeInTheDocument();
    expect(within(region).queryAllByRole("button")).toHaveLength(0);
  });

  it("points read-only accounts at the school-wide rules, which every role may read", async () => {
    renderAssistant("/assistant", "approver");
    expect(await screen.findByRole("link", { name: "查看学校通用规则" })).toHaveAttribute("href", "/rules");
  });

  it("says a run that produced a draft can be looked at in the schedule page, without diagnosis noise", async () => {
    mocks.runDetails["run-ok"] = runFixture({ id: "run-ok" });
    renderAssistant("/assistant?run=run-ok", "viewer");
    const region = await screen.findByLabelText("求解诊断（只读）");
    expect(within(region).getByText("这次求解已经排出了草稿")).toBeInTheDocument();
    expect(within(region).queryByText("冲突核心分析")).not.toBeInTheDocument();
  });
});

describe("the data overview only links to settings for roles that can open it (coverage.F12)", () => {
  it("hides 前往集成配置 for an approver and for a scheduler who is read-only in this set", async () => {
    mocks.analytics = ANALYTICS;
    renderAssistant("/assistant", "approver");
    expect(await screen.findByText("教师总数")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /前往集成配置/ })).not.toBeInTheDocument();
    cleanup();
    renderAssistant("/assistant", "scheduler", { accessRole: "viewer" });
    expect(await screen.findByText("教师总数")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /前往集成配置/ })).not.toBeInTheDocument();
  });

  it("keeps the link for a scheduler who can schedule", async () => {
    const user = userEvent.setup();
    mocks.analytics = ANALYTICS;
    renderAssistant("/assistant", "scheduler");
    await user.click(await screen.findByRole("button", { name: /数据概览/ }));
    expect(await screen.findByRole("link", { name: /前往集成配置/ })).toHaveAttribute("href", "/settings");
  });
});

describe("no flash of 「没有排课权限」 while the schedule set is still loading (parity.F9)", () => {
  it("shows a loading state instead of the restricted page, then the real one", async () => {
    mockAiConfigured();
    renderAssistant("/assistant", "scheduler", { accessRole: null, scheduleSetLoading: true });
    expect(await screen.findByText("加载数据中…")).toBeInTheDocument();
    expect(screen.queryByText(/没有排课权限/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("排课需求")).not.toBeInTheDocument();
    cleanup();
    // 加载结束、方案里确实是排课角色：直接是排课助手。
    renderAssistant("/assistant", "scheduler", { accessRole: "scheduler", scheduleSetLoading: false });
    expect(await screen.findByLabelText("排课需求")).toBeInTheDocument();
  });

  it("still explains the restriction once loading has finished with no usable role", async () => {
    renderAssistant("/assistant", "scheduler", { accessRole: null, scheduleSetLoading: false });
    expect(await screen.findByText(/没有排课权限/)).toBeInTheDocument();
  });
});

describe("the publish confirmation states what is being published (coverage.F14)", () => {
  const goalReport = (over: Record<string, unknown> = {}) => ({
    goal_id: "goal-1",
    all_passed: false,
    // 后端口径：failed_count = 全部验收项 − 已通过，本来就包含「无法验证」的项。
    passed_count: 2,
    failed_count: 2,
    unverifiable_count: 1,
    items: [],
    gaps: [],
    decision: null,
    ...over,
  });

  async function openConfirm(user: ReturnType<typeof userEvent.setup>) {
    const drafts = await screen.findByLabelText("待发布的草稿");
    await user.click(within(drafts).getByRole("button", { name: /审批发布/ }));
  }

  it("carries the number of changed lessons against the published version and the open requirements", async () => {
    const user = userEvent.setup();
    mocks.schedules = [scheduleFixture({ id: "pub-1", status: "published", version_no: 1, name: "已发布", parent_id: null, solver_run_id: "run-0" }), scheduleFixture()];
    mocks.diff.mockReturnValue({ data: { changed_count: 7, items: [] } });
    mocks.runDetails["run-1"] = runFixture({ id: "run-1", goal_id: "goal-1", goal_report: goalReport() });
    mocks.goalDetails["goal-1"] = goalFixture({ id: "goal-1" });
    renderAssistant("/assistant", "approver");
    await openConfirm(user);
    expect(await screen.findByText(/相对当前已发布的 v1，这份草稿调整了 7 节课/)).toBeInTheDocument();
    // 共 4 项：通过 2 项、失败 1 项、无法验证 1 项……后端给的 failed_count=2 已含无法验证的那 1 项，不能再加一遍。
    expect(screen.getByText(/它关联的求解还有 2 项要求没落实（其中 1 项暂时无法验证）/)).toBeInTheDocument();
    expect(screen.getByText(/发布后成为当前课表，并同步到已启用的外部集成；不会自动下发日历。/)).toBeInTheDocument();
  });

  it("says all requirements were met when nothing is open", async () => {
    const user = userEvent.setup();
    mocks.schedules = [scheduleFixture({ id: "pub-1", status: "published", version_no: 1, parent_id: null }), scheduleFixture()];
    mocks.diff.mockReturnValue({ data: { changed_count: 0, items: [] } });
    mocks.runDetails["run-1"] = runFixture({ id: "run-1", goal_id: "goal-1", goal_report: goalReport({ all_passed: true, passed_count: 3, failed_count: 0, unverifiable_count: 0 }) });
    mocks.goalDetails["goal-1"] = goalFixture({ id: "goal-1" });
    renderAssistant("/assistant", "approver");
    await openConfirm(user);
    expect(await screen.findByText(/它关联的求解已落实全部 3 项要求/)).toBeInTheDocument();
  });

  // 审查 #3：验收器异常时后端落的是「all_passed=false、items=[]、没有计数」的报告，
  // 计数缺省成 0 不能被读成「未通过数为 0 = 全部落实」。
  it("never says everything is met for a failed acceptance report that carries no counts", async () => {
    const user = userEvent.setup();
    mocks.schedules = [scheduleFixture({ id: "pub-1", status: "published", version_no: 1, parent_id: null }), scheduleFixture()];
    mocks.diff.mockReturnValue({ data: { changed_count: 2, items: [] } });
    mocks.runDetails["run-1"] = runFixture({
      id: "run-1",
      goal_id: "goal-1",
      goal_report: { goal_id: "goal-1", acceptance_status: "failed", acceptance_error: "验收器异常：boom", all_passed: false, items: [], gaps: [], decision: null, checklist_version: 1 },
    });
    mocks.goalDetails["goal-1"] = goalFixture({ id: "goal-1", acceptance_status: "failed" });
    renderAssistant("/assistant", "approver");
    await openConfirm(user);
    expect(await screen.findByText(/要求核对失败（验收器异常：boom），尚不能确认要求已落实/)).toBeInTheDocument();
    expect(screen.queryByText(/已落实全部/)).not.toBeInTheDocument();
  });

  // 审查 #2：要求修订后，旧版「全部通过」只是历史结论，不能在审批时被当成当前要求已落实。
  it("shows an all-passed report from an older checklist version as history, not as the current verdict", async () => {
    const user = userEvent.setup();
    mocks.schedules = [scheduleFixture({ id: "pub-1", status: "published", version_no: 1, parent_id: null }), scheduleFixture()];
    mocks.diff.mockReturnValue({ data: { changed_count: 2, items: [] } });
    mocks.runDetails["run-1"] = runFixture({
      id: "run-1",
      goal_id: "goal-1",
      goal_report: goalReport({ all_passed: true, passed_count: 3, failed_count: 0, unverifiable_count: 0, meta: { checklist_version: 1 } }),
    });
    mocks.goalDetails["goal-1"] = goalFixture({ id: "goal-1", checklist_version: 2, acceptance_status: "pending" });
    renderAssistant("/assistant", "approver");
    await openConfirm(user);
    expect(await screen.findByText(/历史 v1 已通过；当前 v2 尚待核对，尚不能确认当前要求已落实/)).toBeInTheDocument();
    expect(screen.queryByText(/已落实全部/)).not.toBeInTheDocument();
  });

  it("does not claim the requirements are met while the task behind the report cannot be read", async () => {
    const user = userEvent.setup();
    mocks.schedules = [scheduleFixture({ id: "pub-1", status: "published", version_no: 1, parent_id: null }), scheduleFixture()];
    mocks.diff.mockReturnValue({ data: { changed_count: 2, items: [] } });
    mocks.runDetails["run-1"] = runFixture({ id: "run-1", goal_id: "goal-gone", goal_report: goalReport({ all_passed: true, passed_count: 3, failed_count: 0, unverifiable_count: 0 }) });
    renderAssistant("/assistant", "approver");
    await openConfirm(user);
    expect(await screen.findByText(/无法确认这份求解的要求核对对应哪一版要求，尚不能确认要求已落实/)).toBeInTheDocument();
    expect(screen.queryByText(/已落实全部/)).not.toBeInTheDocument();
  });

  it("admits when there is no baseline to compare with instead of inventing a number", async () => {
    const user = userEvent.setup();
    mocks.schedules = [scheduleFixture({ solver_run_id: null })];
    renderAssistant("/assistant", "approver");
    await openConfirm(user);
    expect(await screen.findByText(/暂无对比基准（当前还没有已发布的版本）/)).toBeInTheDocument();
    expect(screen.getByText(/这份草稿不是由助手任务生成的，没有要求核对记录/)).toBeInTheDocument();
    expect(screen.queryByText(/调整了 \d+ 节课/)).not.toBeInTheDocument();
  });

  it("admits it too when the comparison could not be read", async () => {
    const user = userEvent.setup();
    mocks.schedules = [scheduleFixture({ id: "pub-1", status: "published", version_no: 1, parent_id: null }), scheduleFixture()];
    mocks.diff.mockReturnValue({ isError: true });
    mocks.runDetails["run-1"] = runFixture({ id: "run-1" });
    renderAssistant("/assistant", "approver");
    await openConfirm(user);
    expect(await screen.findByText(/暂无对比基准（调整明细没读出来/)).toBeInTheDocument();
  });
});

describe("task list and task bar surface who needs to decide and why acceptance failed (parity.F10 / coverage.F15)", () => {
  it("labels an awaiting-decision task on the default list, not only after 查看全部任务", async () => {
    mocks.goals = [
      goalFixture({ id: "g1", instruction: "等裁决的任务", status: "awaiting_decision", run_count: 1, latest_run_id: "r1" }),
      goalFixture({ id: "g2", instruction: "普通进行中的任务", updated_at: "2026-09-19T09:00:00+08:00" }),
    ];
    mocks.runs = [runFixture({ id: "r1" })];
    renderAssistant();
    const section = await screen.findByLabelText("正在处理的任务");
    const items = within(section).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("等裁决的任务");
    expect(items[0]).toHaveTextContent("待教务裁决");
    expect(items[1]).not.toHaveTextContent("待教务裁决");
  });

  it("shows the goal-level acceptance failure reason on the list and on the task bar", async () => {
    const failed = goalFixture({ id: "goal-77", acceptance_status: "failed", acceptance_detail: "核对时读取课表超时" });
    mocks.goals = [failed];
    mocks.goalDetail = failed;
    renderAssistant();
    expect(await screen.findByText(/要求核对失败的原因：核对时读取课表超时/)).toBeInTheDocument();
    cleanup();
    renderAssistant("/assistant?goal=goal-77");
    const bar = await screen.findByLabelText("任务进展");
    expect(await within(bar).findByText(/要求核对失败的原因：核对时读取课表超时/)).toBeInTheDocument();
  });

  it("says nothing about a failure when acceptance did not fail", async () => {
    mocks.goals = [goalFixture({ id: "goal-77", acceptance_status: "completed", acceptance_detail: "不该出现" })];
    renderAssistant();
    await screen.findByLabelText("正在处理的任务");
    expect(screen.queryByText(/要求核对失败的原因/)).not.toBeInTheDocument();
  });

  it("uses business words for giving a task up, in the task bar and in the dialog", async () => {
    const user = userEvent.setup();
    mocks.goalDetail = goalFixture({ id: "goal-77" });
    renderAssistant("/assistant?goal=goal-77");
    const bar = await screen.findByLabelText("任务进展");
    await user.click(await within(bar).findByRole("button", { name: "不再跟进这个任务" }));
    expect(await screen.findByText("不再跟进这个任务？")).toBeInTheDocument();
    expect(screen.queryByText(/放弃这个目标/)).not.toBeInTheDocument();
  });
});

describe("the confirmation card speaks to the school office (hygiene.F5)", () => {
  it("swaps the solver jargon for plain words, avoids 。； and keeps the original under 查看详情", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    const summary = "已解析为考研业务线 8 月 17 日至 19 日的重排，请教务确认后启动 CP-SAT 求解。";
    mocks.stream.mockResolvedValue(interpretationFixture({ summary }));
    renderAssistant();
    await user.type(await screen.findByLabelText("排课需求"), "请在三天内重排考研课程");
    await user.click(screen.getByRole("button", { name: "让 AI 解析" }));
    const card = await screen.findByLabelText("我理解的需求");
    expect(card).toHaveTextContent("请教务确认后开始排课；解析来源");
    expect(card).not.toHaveTextContent(/CP-SAT/);
    expect(card).not.toHaveTextContent("。；");
    await user.click(screen.getByRole("button", { name: /查看详情/ }));
    const original = await screen.findByText(summary);
    expect(original).toBeInTheDocument();
    expect(card).not.toContainElement(original);
  });

  it("adds no duplicate original when the summary was already plain", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture({ summary: "已解析排课范围" }));
    renderAssistant();
    await user.type(await screen.findByLabelText("排课需求"), "请在三天内重排考研课程");
    await user.click(screen.getByRole("button", { name: "让 AI 解析" }));
    await screen.findByLabelText("我理解的需求");
    await user.click(screen.getByRole("button", { name: /查看详情/ }));
    expect(screen.queryByText("解析原文")).not.toBeInTheDocument();
  });
});
