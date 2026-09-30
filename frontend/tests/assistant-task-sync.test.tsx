import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { assistantPath } from "@/lib/routes";

import { nav, renderAssistant } from "./support/assistant-render";
import {
  assistantMocks as mocks,
  goalFixture,
  interpretationFixture,
  mockAiConfigured,
  PLACEHOLDER_ITEM,
  resetAssistantMocks,
  runFixture,
  scheduleFixture,
  TASK_CONSTRAINTS,
} from "./support/assistant-mocks";

const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: toasts.success, error: toasts.error } }));
vi.mock("@/api/generated/client", async () => (await import("./support/assistant-mocks")).clientMock);
vi.mock("@/api/http", async () => (await import("./support/assistant-mocks")).httpMock);
vi.mock("@/lib/interpret-stream", async () => (await import("./support/assistant-mocks")).interpretStreamMock);

const PARSE = "让 AI 解析";
const CONFIRM = /确认并开始求解/;
const EXPLAINED = { headline: "已排好", explanation: [], next_actions: [], source: "system" };

async function parse(user: ReturnType<typeof userEvent.setup>, text = "请在三天内重排考研课程") {
  await user.type(await screen.findByLabelText("排课需求"), text);
  await user.click(screen.getByRole("button", { name: PARSE }));
}

/** 模拟浏览器后退 / 侧栏链接这类页面外发起的导航。 */
async function navigateTo(to: string | number) {
  await act(async () => { nav.go(to as never); });
}

/** 两个各有一次求解结果的任务：A 已出草稿，B 时间用完。 */
function seedTasksAB() {
  mocks.goalDetails = {
    "goal-A": goalFixture({ id: "goal-A", instruction: "任务 A 的需求", latest_run_id: "run-A", run_count: 1 }),
    "goal-B": goalFixture({ id: "goal-B", instruction: "任务 B 的需求", latest_run_id: "run-B", run_count: 1 }),
  };
  mocks.runDetails["run-A"] = runFixture({ id: "run-A", goal_id: "goal-A", explanation: EXPLAINED });
  mocks.runDetails["run-B"] = runFixture({ id: "run-B", goal_id: "goal-B", model_status: "UNKNOWN", explanation: EXPLAINED });
  mocks.schedules = [scheduleFixture({ id: "draft-A", solver_run_id: "run-A" })];
}

beforeEach(() => {
  resetAssistantMocks();
  toasts.success.mockReset();
  toasts.error.mockReset();
});
afterEach(cleanup);

describe("URL is the source of truth for the task view (parity.F3)", () => {
  it("goes back to the home view when the browser goes back to a URL without goal", async () => {
    seedTasksAB();
    renderAssistant(["/assistant", "/assistant?goal=goal-A"]);
    expect(await screen.findByRole("heading", { name: /已生成草稿/ })).toBeInTheDocument();
    await navigateTo(-1);
    expect(await screen.findByLabelText("排课需求")).toHaveValue("");
    expect(screen.queryByLabelText("任务进展")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /已生成草稿/ })).not.toBeInTheDocument();
  });

  it("goes back to the home view when the sidebar link points at the bare assistant route", async () => {
    seedTasksAB();
    renderAssistant("/assistant?run=run-B");
    expect(await screen.findByRole("heading", { name: "时间用完了，还没找到可用的排法" })).toBeInTheDocument();
    await navigateTo("/assistant");
    expect(await screen.findByLabelText("排课需求")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "时间用完了，还没找到可用的排法" })).not.toBeInTheDocument();
  });

  it("never pairs task A's bar with task B's result when the URL switches between goals", async () => {
    seedTasksAB();
    renderAssistant(["/assistant?goal=goal-A", "/assistant", "/assistant?goal=goal-B"], "admin", { initialIndex: 0 });
    expect(await screen.findByRole("heading", { name: /已生成草稿/ })).toBeInTheDocument();
    expect(within(screen.getByLabelText("任务进展")).getByText("任务 A 的需求")).toBeInTheDocument();
    await navigateTo("/assistant");
    expect(await screen.findByLabelText("排课需求")).toBeInTheDocument();
    await navigateTo("/assistant?goal=goal-B");
    expect(await screen.findByRole("heading", { name: "时间用完了，还没找到可用的排法" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("任务进展")).getByText("任务 B 的需求")).toBeInTheDocument();
    // 直接从 B 跳回 A（后退到更早的历史）：结果卡也必须换成 A 的。
    await navigateTo("/assistant?goal=goal-A");
    expect(await screen.findByRole("heading", { name: /已生成草稿/ })).toBeInTheDocument();
    const bar = screen.getByLabelText("任务进展");
    expect(within(bar).getByText("任务 A 的需求")).toBeInTheDocument();
    expect(within(bar).queryByText("任务 B 的需求")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "时间用完了，还没找到可用的排法" })).not.toBeInTheDocument();
  });

  it("switches goal A to goal B directly, without a home stop in between", async () => {
    seedTasksAB();
    renderAssistant("/assistant?goal=goal-A");
    expect(await screen.findByRole("heading", { name: /已生成草稿/ })).toBeInTheDocument();
    await navigateTo("/assistant?goal=goal-B");
    expect(await screen.findByRole("heading", { name: "时间用完了，还没找到可用的排法" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("任务进展")).getByText("任务 B 的需求")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /已生成草稿/ })).not.toBeInTheDocument();
  });

  it("drops an in-flight parse that finishes after the URL moved to another task", async () => {
    mockAiConfigured();
    seedTasksAB();
    let resolveStream: (value: unknown) => void = () => undefined;
    mocks.stream.mockImplementation(() => new Promise((resolve) => { resolveStream = resolve; }));
    const user = userEvent.setup();
    renderAssistant("/assistant");
    await parse(user);
    await waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(1));
    await navigateTo("/assistant?goal=goal-B");
    expect(await screen.findByRole("heading", { name: "时间用完了，还没找到可用的排法" })).toBeInTheDocument();
    await act(async () => { resolveStream(interpretationFixture()); });
    // 迟到的解析结果不能落到任务 B 上。
    expect(screen.queryByRole("heading", { name: "我理解的是……" })).not.toBeInTheDocument();
    expect(within(screen.getByLabelText("任务进展")).getByText("任务 B 的需求")).toBeInTheDocument();
  });

  it("does not reset the task when the hook itself writes the goal param after creating a task", async () => {
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture());
    mocks.schedules = [scheduleFixture({ id: "draft-1", solver_run_id: "run-1" })];
    mocks.runDetails["run-1"] = runFixture({ id: "run-1", goal_id: "goal-1", explanation: EXPLAINED });
    mocks.goalDetails = { "goal-1": goalFixture({ id: "goal-1", instruction: "请在三天内重排考研课程", latest_run_id: "run-1", run_count: 1 }) };
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      if (String(args[0]) === "/api/v1/goals") return { data: { id: "goal-1", status: "open", checklist: [] } };
      return { data: { id: "run-1", status: "queued", model_status: null, goal_id: "goal-1" } };
    });
    const user = userEvent.setup();
    renderAssistant();
    await parse(user);
    await user.click(await screen.findByRole("button", { name: CONFIRM }));
    expect(await screen.findByRole("heading", { name: /已生成草稿/ })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant\?goal=goal-1$/));
    // URL 写入 goal 之后任务视图仍在（没被当成外部导航重置）。
    expect(within(screen.getByLabelText("任务进展")).getByText("请在三天内重排考研课程")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /已生成草稿/ })).toBeInTheDocument();
  });
});

describe("one gate for every solve / re-parse entry (parity.F1/F2)", () => {
  const scopedContext = {
    schema_version: 1,
    scope: { business_lines: ["考研"], product_types: [], class_business_ids: ["B01"], date_from: null, date_to: null, date_window_days: 3 },
  };

  function seedScopedStuckGoal() {
    mockAiConfigured();
    mocks.courses = [{ id: "cs-1", business_line: "考研", class_business_id: "B01", lesson_date: "2026-09-28" }];
    mocks.goalDetails = { "goal-77": goalFixture({ id: "goal-77", context: scopedContext, latest_run_id: "run-u", run_count: 1 }) };
    mocks.runDetails["run-u"] = runFixture({ id: "run-u", goal_id: "goal-77", model_status: "UNKNOWN", explanation: EXPLAINED });
  }

  it("disables the budget rerun, the manual submit and re-parsing while a widened scope waits for confirmation", async () => {
    seedScopedStuckGoal();
    const user = userEvent.setup();
    renderAssistant("/assistant?goal=goal-77");
    const raise = await screen.findByRole("button", { name: "加大时间预算重跑" });
    await waitFor(() => expect(raise).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "手动排课（自己设置参数）" }));
    await user.selectOptions(screen.getByLabelText("班级"), "");
    expect(screen.getByText(/扩大范围需要单独确认/)).toBeInTheDocument();
    expect(raise).toBeDisabled();
    expect(screen.getByRole("button", { name: /按参数开始求解/ })).toBeDisabled();
    // 重新解析的入口也被拦住，并把原因写在输入框旁边。
    await user.click(screen.getByRole("button", { name: "修改要求后重新解析" }));
    await user.type(screen.getByLabelText("修改后的要求"), "放宽周三晚上");
    const refineBox = screen.getByLabelText("修改后的要求").closest("label")?.parentElement as HTMLElement;
    expect(within(refineBox).getByRole("button", { name: "重新解析" })).toBeDisabled();
    expect(screen.getAllByText(/排课范围比确认过的更大/).length).toBeGreaterThan(0);
    expect(mocks.submitMutate).not.toHaveBeenCalled();
    expect(mocks.stream).not.toHaveBeenCalled();
    // 确认扩大后恢复可用。
    await user.click(screen.getByRole("button", { name: "确认扩大范围" }));
    await waitFor(() => expect(raise).toBeEnabled());
    expect(screen.getByRole("button", { name: /按参数开始求解/ })).toBeEnabled();
  });

  it("disables the gap remedy and the follow-up refine on a result while a widened scope waits", async () => {
    mockAiConfigured();
    mocks.courses = [{ id: "cs-1", business_line: "考研", class_business_id: "B01", lesson_date: "2026-09-28" }];
    mocks.goalDetails = { "goal-9": goalFixture({ id: "goal-9", context: scopedContext, latest_run_id: "run-g", run_count: 1 }) };
    mocks.schedules = [scheduleFixture({ id: "draft-g", solver_run_id: "run-g" })];
    mocks.runDetails["run-g"] = runFixture({
      id: "run-g",
      goal_id: "goal-9",
      explanation: EXPLAINED,
      goal_report: {
        goal_id: "goal-9",
        instruction: "重排",
        all_passed: false,
        passed_count: 1,
        failed_count: 1,
        items: [{ key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", passed: false, detail: "缺 1 个课次" }],
        gaps: [{ key: "coverage", kind: "coverage", summary: "未安置", next_step: "加大时间预算后重跑", remedy: "raise_budget" }],
        decision: { status: "awaiting_decision", reason: "1 项未通过" },
      },
    });
    const user = userEvent.setup();
    renderAssistant("/assistant?goal=goal-9");
    const remedy = await screen.findByRole("button", { name: "加大时间预算重跑" });
    await waitFor(() => expect(remedy).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "手动排课（自己设置参数）" }));
    await user.selectOptions(screen.getByLabelText("班级"), "");
    expect(remedy).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "继续调整" }));
    await user.type(screen.getByLabelText("还要改什么？"), "再避开周五");
    const refineBox = screen.getByLabelText("还要改什么？").closest("label")?.parentElement as HTMLElement;
    expect(within(refineBox).getByRole("button", { name: "重新解析" })).toBeDisabled();
    expect(mocks.stream).not.toHaveBeenCalled();
  });

  it("keeps ?action=raise_budget from resubmitting a run without a task: it explains itself and drops the param", async () => {
    renderAssistant("/assistant?action=raise_budget");
    await screen.findByLabelText("排课需求");
    await waitFor(() => expect(toasts.error).toHaveBeenCalledWith(expect.stringContaining("没有关联任务")));
    expect(mocks.submitMutate).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant$/));
  });

  it("replays the run a legacy task last solved for ?action=raise_budget, without reading any scope from the draft", async () => {
    mocks.goalDetails = {
      "goal-old": goalFixture({
        id: "goal-old",
        latest_run_id: "run-old",
        run_count: 1,
        checklist: [{ key: "coverage", kind: "coverage", requirement: "覆盖全部目标课次", params: { business_lines: ["考研"], class_business_ids: ["B01"], date_from: "2026-09-28T00:00:00+08:00" } }],
      }),
    };
    mocks.runDetails["run-old"] = runFixture({ id: "run-old", goal_id: "goal-old", model_status: "UNKNOWN", explanation: EXPLAINED });
    renderAssistant("/assistant?goal=goal-old&action=raise_budget");
    // 等到任务恢复、它的最近一次求解读到之后才重放；范围/规则/预算基数全部来自那条求解记录。
    await waitFor(() => expect(mocks.rerunMutate).toHaveBeenCalledTimes(1));
    expect(mocks.rerunMutate).toHaveBeenCalledWith({ runId: "run-old", data: {} });
    expect(mocks.submitMutate).not.toHaveBeenCalled();
  });

  it("explains itself instead of guessing when the task has no run to replay", async () => {
    mocks.goalDetails = { "goal-none": goalFixture({ id: "goal-none" }) };
    renderAssistant("/assistant?goal=goal-none&action=raise_budget");
    await waitFor(() => expect(toasts.error).toHaveBeenCalledWith(expect.stringContaining("没有可重跑的求解记录")));
    expect(mocks.rerunMutate).not.toHaveBeenCalled();
    expect(mocks.submitMutate).not.toHaveBeenCalled();
  });
});

describe("closed goals are read-only (parity.F4)", () => {
  it("opens an abandoned goal by URL without letting it take part in solving", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.goalDetails = { "goal-ab": goalFixture({ id: "goal-ab", status: "abandoned", instruction: "重排 B01 班一周课表", latest_run_id: "run-z" }) };
    renderAssistant("/assistant?goal=goal-ab");
    const notice = await screen.findByLabelText("任务已结束");
    expect(within(notice).getByText(/这个任务已放弃，不能继续求解/)).toBeInTheDocument();
    expect(within(notice).getByRole("link", { name: "以同样需求新建" })).toHaveAttribute("href", assistantPath({ prompt: "重排 B01 班一周课表" }));
    expect(within(notice).getByRole("link", { name: "查看最新结果" })).toHaveAttribute("href", "/assistant?run=run-z");
    expect(screen.queryByLabelText("接着办")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: PARSE })).not.toBeInTheDocument();
    // 手动排课仍可用，但不再带着已结束任务的 id。
    await user.click(screen.getByRole("button", { name: "手动排课（自己设置参数）" }));
    await user.click(await screen.findByRole("button", { name: /按参数开始求解/ }));
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    expect((mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> }).data.goal_id).toBeNull();
  });

  it("starts a fresh request from the same wording, leaving the closed goal behind", async () => {
    const user = userEvent.setup();
    mocks.goalDetails = { "goal-gone": goalFixture({ id: "goal-gone", status: "abandoned", instruction: "重排 B01 班一周课表" }) };
    renderAssistant("/assistant?goal=goal-gone");
    expect(await screen.findByText(/这个任务已放弃，不能继续求解/)).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "以同样需求新建" }));
    expect(await screen.findByLabelText("排课需求")).toHaveValue("重排 B01 班一周课表");
    expect(screen.queryByLabelText("任务进展")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant$/));
  });

  it("offers 继续处理 for achieved tasks on the home list, and only a read-only latest result for abandoned ones", async () => {
    const user = userEvent.setup();
    mocks.goals = [
      goalFixture({ id: "g-open", instruction: "进行中的任务", updated_at: "2026-09-03T09:00:00+08:00" }),
      goalFixture({ id: "g-done", instruction: "已达成的任务", status: "achieved", latest_run_id: "run-done", run_count: 1, updated_at: "2026-09-02T09:00:00+08:00" }),
      goalFixture({ id: "g-ab", instruction: "已放弃的任务", status: "abandoned", latest_run_id: "run-ab", run_count: 1, updated_at: "2026-09-01T09:00:00+08:00" }),
      goalFixture({ id: "g-ab0", instruction: "已放弃且没跑过的任务", status: "abandoned", updated_at: "2026-08-31T09:00:00+08:00" }),
    ];
    renderAssistant();
    const section = await screen.findByLabelText("正在处理的任务");
    await user.click(within(section).getByRole("button", { name: "查看全部任务（4）" }));
    const rows = within(section).getAllByRole("listitem");
    expect(within(rows[0]).getByRole("button", { name: "继续处理" })).toBeInTheDocument();
    // 已达成的任务后端仍接受新求解（后一次验收没过会回退为进行中），所以可以继续调整。
    expect(within(rows[1]).getByRole("button", { name: "继续处理" })).toBeInTheDocument();
    expect(within(rows[1]).queryByRole("button", { name: "查看最新结果" })).not.toBeInTheDocument();
    // 已放弃：后端 409，只给只读入口；没有求解记录时什么入口都没有。
    expect(within(rows[2]).queryByRole("button", { name: "继续处理" })).not.toBeInTheDocument();
    expect(within(rows[2]).getByRole("button", { name: "查看最新结果" })).toBeInTheDocument();
    expect(within(rows[3]).queryByRole("button", { name: /继续处理|查看最新结果/ })).not.toBeInTheDocument();
    await user.click(within(rows[2]).getByRole("button", { name: "查看最新结果" }));
    expect(screen.getByTestId("location-probe")).toHaveTextContent("/assistant?run=run-ab");
  });

  it("keeps an achieved task continuable: no closed notice, and a re-run stays on the same task", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.goalDetails = { "goal-done": goalFixture({ id: "goal-done", status: "achieved", instruction: "重排 B01 班一周课表", latest_run_id: "run-done", run_count: 1 }) };
    mocks.runDetails["run-done"] = runFixture({ id: "run-done", goal_id: "goal-done", explanation: EXPLAINED });
    renderAssistant("/assistant?goal=goal-done");
    expect(await screen.findByText("重排 B01 班一周课表")).toBeInTheDocument();
    expect(screen.queryByLabelText("任务已结束")).not.toBeInTheDocument();
    expect(screen.queryByText(/不能继续求解/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "手动排课（自己设置参数）" }));
    await user.click(await screen.findByRole("button", { name: /按参数开始求解/ }));
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    expect((mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> }).data.goal_id).toBe("goal-done");
  });
});

describe("a manual submit does not confirm the pending understanding (parity.F5)", () => {
  it("keeps the confirmation card, the unresolved requirements and says the parsed task requirements were not sent", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture({
      unsupported_requirements: ["指定教室要求"],
      task_constraints: TASK_CONSTRAINTS,
      goal_checklist_draft: [{ key: "coverage", kind: "coverage", requirement: "覆盖全部目标课次", params: {} }],
    }));
    mocks.submitResult = { id: "run-m", status: "running", model_status: null };
    mocks.runDetails["run-m"] = runFixture({ id: "run-m", status: "running", model_status: null });
    renderAssistant();
    await parse(user);
    await screen.findByText(/已解析完成（用时/);
    await user.click(screen.getByRole("button", { name: "手动排课（自己设置参数）" }));
    await user.click(screen.getByRole("button", { name: /按参数开始求解/ }));
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    // 手动求解的进度出现了，确认卡与未落实的要求仍在。
    expect(await screen.findByLabelText("正在排课")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "我理解的是……" })).toBeInTheDocument();
    expect(screen.getByText("以下要求尚未进入求解（待补充）：")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: CONFIRM })).toBeDisabled();
    // 「本次要求」如实说明：这次没有带上解析出的任务约束。
    const panel = screen.getByLabelText("本次要求");
    expect(panel).toHaveTextContent("这次是按参数手动排课，没有带上解析出的本次要求");
    expect(panel).not.toHaveTextContent("张老师周三晚上不能上；李老师周三晚尽量别排（尽量）；先出草稿");
    const requestBody = (mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> }).data;
    expect(requestBody).not.toHaveProperty("task_constraints");
  });
});

describe("AI probe states in the task view (parity.F6)", () => {
  beforeEach(() => {
    mocks.goalDetails = { "goal-77": goalFixture() };
  });

  it("says AI is not configured only when it really is not, with a way to configure it", async () => {
    renderAssistant("/assistant?goal=goal-77");
    expect(await screen.findByText(/AI 尚未接入，暂时不能解析需求/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去配置" })).toHaveAttribute("href", "/settings?section=ai");
  });

  it("says the read failed (and offers a retry) instead of claiming AI is not configured", async () => {
    const user = userEvent.setup();
    mocks.get.mockRejectedValue(new Error("网关超时"));
    renderAssistant("/assistant?goal=goal-77");
    expect(await screen.findByText(/AI 配置读取失败（网关超时）/)).toBeInTheDocument();
    expect(screen.queryByText(/AI 尚未接入/)).not.toBeInTheDocument();
    mockAiConfigured();
    await user.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(screen.getByRole("button", { name: PARSE })).toBeEnabled());
    expect(screen.queryByText(/AI 配置读取失败/)).not.toBeInTheDocument();
  });

  it("says it is still reading while the probe has not answered", async () => {
    mocks.get.mockReturnValue(new Promise(() => undefined));
    renderAssistant("/assistant?goal=goal-77");
    expect(await screen.findByText(/正在读取 AI 配置/)).toBeInTheDocument();
    expect(screen.queryByText(/AI 尚未接入/)).not.toBeInTheDocument();
  });

  it("gives the result card's follow-up input the same retry", async () => {
    const user = userEvent.setup();
    mocks.goalDetails = { "goal-77": goalFixture({ latest_run_id: "run-1", run_count: 1 }) };
    mocks.runDetails["run-1"] = runFixture({ id: "run-1", goal_id: "goal-77", explanation: EXPLAINED });
    mocks.schedules = [scheduleFixture()];
    mocks.get.mockRejectedValue(new Error("网关超时"));
    renderAssistant("/assistant?goal=goal-77");
    await user.click(await screen.findByRole("button", { name: "继续调整" }));
    expect(await screen.findByText(/AI 配置读取失败（网关超时）/)).toBeInTheDocument();
    expect(within(screen.getByLabelText("排课结果")).getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});

describe("continue adjusting with and without context (parity.F7 / coverage.F13)", () => {
  it("offers 新建需求 instead of 继续调整 for a manual result that has no request to inherit", async () => {
    const user = userEvent.setup();
    mocks.submitResult = { id: "run-m", status: "completed", model_status: "OPTIMAL" };
    mocks.runDetails["run-m"] = runFixture({ id: "run-m", explanation: EXPLAINED });
    mocks.schedules = [scheduleFixture({ id: "draft-m", solver_run_id: "run-m" })];
    renderAssistant();
    // AI 未配置时首页默认就展开了手动排课。
    await user.click(await screen.findByRole("button", { name: /按参数开始求解/ }));
    expect(await screen.findByRole("heading", { name: /已生成草稿/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "继续调整" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "新建需求" }));
    expect(await screen.findByLabelText("排课需求")).toHaveValue("");
    expect(screen.queryByLabelText("任务进展")).not.toBeInTheDocument();
  });

  it("marks 已理解（待确认） as the current step after a follow-up parse, not the old draft's 待发布", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture());
    mocks.schedules = [scheduleFixture({ id: "draft-1", solver_run_id: "run-1" })];
    mocks.runDetails["run-1"] = runFixture({ id: "run-1", goal_id: "goal-1", explanation: EXPLAINED });
    mocks.goalDetails = { "goal-1": goalFixture({ id: "goal-1", instruction: "请在三天内重排考研课程" }) };
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      if (String(args[0]) === "/api/v1/goals") return { data: { id: "goal-1", status: "open", checklist: [] } };
      return { data: { id: "run-1", status: "queued", model_status: null, goal_id: "goal-1" } };
    });
    renderAssistant();
    await parse(user);
    await user.click(await screen.findByRole("button", { name: CONFIRM }));
    await screen.findByRole("heading", { name: /已生成草稿/ });
    const steps = () => within(screen.getByLabelText("办理进展"));
    expect(steps().getByText("待发布").closest("li")).toHaveAttribute("aria-current", "step");
    await user.click(screen.getByRole("button", { name: "继续调整" }));
    await user.type(screen.getByLabelText("还要改什么？"), "张老师周三晚上也不能上");
    await user.click(screen.getByRole("button", { name: "重新解析" }));
    expect(await screen.findByRole("heading", { name: "我理解的是……" })).toBeInTheDocument();
    expect(steps().getByText("已理解（待确认）").closest("li")).toHaveAttribute("aria-current", "step");
    expect(steps().getByText("待发布").closest("li")).not.toHaveAttribute("aria-current");
  });
});

describe("supplementing a condition turns tracking back on (parity.F8)", () => {
  it("re-enables 以此为目标跟踪 and says why, so the supplemented goal_id rides on the solve", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture({
      unsupported_requirements: ["具体教师的禁排或请假要求"],
      goal_checklist_draft: [{ key: "coverage", kind: "coverage", requirement: "覆盖全部目标课次", params: {} }, PLACEHOLDER_ITEM],
    }));
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      if (String(args[0]) === "/api/v1/goals") return { data: { id: "goal-later", status: "open", checklist: [PLACEHOLDER_ITEM] } };
      return { data: {} };
    });
    mocks.goalDetails = { "goal-later": goalFixture({ id: "goal-later", checklist: [PLACEHOLDER_ITEM] }) };
    renderAssistant();
    await parse(user);
    await user.click(await screen.findByRole("button", { name: /更多选项/ }));
    await user.click(screen.getByLabelText("以此为目标跟踪"));
    expect(screen.getByLabelText("以此为目标跟踪")).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "补充条件" }));
    await waitFor(() => expect(screen.getByLabelText("以此为目标跟踪")).toBeChecked());
    expect(screen.getByText(/补充的条件要写进任务里才会生效/)).toBeInTheDocument();
    expect(await screen.findByLabelText("禁排主体类型")).toBeInTheDocument();
  });
});
