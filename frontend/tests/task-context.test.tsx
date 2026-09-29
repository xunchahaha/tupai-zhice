import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderAssistant } from "./support/assistant-render";
import {
  assistantMocks as mocks,
  goalFixture,
  interpretationFixture as baseInterpretation,
  mockAiConfigured,
  PLACEHOLDER_ITEM,
  resetAssistantMocks,
  runFixture,
  scheduleFixture,
  TASK_CONSTRAINTS as taskConstraints,
} from "./support/assistant-mocks";

vi.mock("@/api/generated/client", async () => (await import("./support/assistant-mocks")).clientMock);
vi.mock("@/api/http", async () => (await import("./support/assistant-mocks")).httpMock);
vi.mock("@/lib/interpret-stream", async () => (await import("./support/assistant-mocks")).interpretStreamMock);

const PARSE = "让 AI 解析";
const CONFIRM = /确认并开始求解/;

const memoryReceipts = [
  { action_id: "ma-1", status: "executed", entry_id: "pe-1", receipt: "已记住：张老师 周三晚尽量不排（有效期至 2027-01-31）。可在「记忆」页修改或撤销。" },
  { action_id: "ma-2", status: "pending_confirmation", entry_id: null, receipt: "推测到偏好：李老师好像不太愿意上晚间。" },
];

const goalContext = {
  schema_version: 1,
  scope: {
    business_lines: ["考研"],
    product_types: ["暑期集训"],
    class_business_ids: ["B01"],
    date_from: "2026-09-28",
    date_to: "2026-10-04",
    date_window_days: 3,
  },
  soft_task_constraints: [taskConstraints[1]],
  work_draft_schedule_id: "sched-draft-9",
};

function interpretationFixture(overrides: Record<string, unknown> = {}) {
  return baseInterpretation({
    product_types: ["暑期集训"],
    class_business_ids: ["B01"],
    date_from: "2026-09-28",
    date_to: "2026-10-04",
    thinking: "先核对候选。",
    ...overrides,
  });
}

async function parse(user: ReturnType<typeof userEvent.setup>, text = "请在三天内重排考研课程") {
  await user.type(await screen.findByLabelText("排课需求"), text);
  await user.click(screen.getByRole("button", { name: PARSE }));
}

/** 续办入口：任务已绑定，落在「接着办」上；点「让 AI 解析」用恢复出的需求重新解析。 */
async function resumeParse(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText(/已接着办这个任务/);
  await user.click(screen.getByRole("button", { name: PARSE }));
}

beforeEach(resetAssistantMocks);
afterEach(cleanup);

describe("AssistantPage task constraints & memory receipts (TC-6 §6.1)", () => {
  beforeEach(() => {
    mockAiConfigured();
  });

  it("renders task constraints with hard/soft badges and the scope-only note on the confirmation card", async () => {
    mocks.stream.mockResolvedValue(interpretationFixture({ task_constraints: taskConstraints }));
    const user = userEvent.setup();
    renderAssistant();
    await parse(user);
    const region = await screen.findByLabelText("本次任务要求");
    // 固定文案：仅作用于本次任务，不进入规则库。
    expect(within(region).getByText("仅作用于本次任务，不进入规则库")).toBeInTheDocument();
    // hard/soft 逐条展示：原话 + 徽标 + 主体×时段。
    expect(within(region).getByText("张老师周三晚上不能上")).toBeInTheDocument();
    expect(within(region).getByText("李老师周三晚尽量别排")).toBeInTheDocument();
    expect(within(region).getByText("硬约束")).toBeInTheDocument();
    expect(within(region).getByText("软约束")).toBeInTheDocument();
    expect(within(region).getByText("教师 T01 × 时段 S05、S06")).toBeInTheDocument();
    expect(within(region).getByText("教师 T02 × 时段 S05")).toBeInTheDocument();
    // 常驻的「本次要求」一句话由范围 + 任务约束推导。
    const summary = screen.getByLabelText("本次要求");
    expect(summary).toHaveTextContent("本次要求：");
    expect(summary).toHaveTextContent("张老师周三晚上不能上");
    expect(summary).toHaveTextContent("李老师周三晚尽量别排（尽量）");
    expect(summary).toHaveTextContent("先出草稿，不会自动发布");
  });

  it("renders memory action receipts: executed with the undo hint, pending with the inbox note", async () => {
    mocks.stream.mockResolvedValue(interpretationFixture({ memory_action_receipts: memoryReceipts }));
    const user = userEvent.setup();
    renderAssistant();
    await parse(user);
    const region = await screen.findByLabelText("记忆动作回执");
    // executed：徽标 + 回执文案（含「可在记忆页修改或撤销」的修改/撤销入口提示）。
    expect(within(region).getByText("已执行")).toBeInTheDocument();
    expect(within(region).getByText(/可在「记忆」页修改或撤销/)).toBeInTheDocument();
    // pending_confirmation：黄色徽标 + 收件箱待确认提示。
    expect(within(region).getByText("待确认")).toBeInTheDocument();
    expect(within(region).getByText("已放入记忆收件箱待确认")).toBeInTheDocument();
    // 后端回执里的「记忆」页在导航里叫「常用偏好」：每条回执旁边给出直达链接，有条目就直达该条。
    expect(within(region).getByRole("link", { name: "在常用偏好里修改或撤销" })).toHaveAttribute("href", "/memory?entry=pe-1");
    expect(within(region).getByRole("link", { name: "去常用偏好里确认" })).toHaveAttribute("href", "/memory");
  });

  it("keeps the confirmation card clean when no task constraints or receipts are present", async () => {
    mocks.stream.mockResolvedValue(interpretationFixture());
    const user = userEvent.setup();
    renderAssistant();
    await parse(user);
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    expect(screen.queryByLabelText("本次任务要求")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("记忆动作回执")).not.toBeInTheDocument();
  });
});

describe("AssistantPage solve request contract (TC-6 §6.5/§6.4)", () => {
  it("carries the full task_constraints list (hard + soft) in the /assistant/solve request body", async () => {
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture({ task_constraints: taskConstraints }));
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      const url = String(args[0]);
      if (url === "/api/v1/goals") return { data: { id: "goal-new-1", status: "open", checklist: [] } };
      return { data: { id: "run-tc", status: "queued", model_status: null } };
    });
    const user = userEvent.setup();
    renderAssistant();
    await parse(user);
    await user.click(await screen.findByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    const [solveUrl, solveBody] = mocks.post.mock.calls[1] as [string, Record<string, unknown>];
    expect(solveUrl).toBe("/api/v1/assistant/solve");
    // 软约束链前端填充（§6.5）：请求体带全量 task_constraints（hard+soft），与确认卡同源。
    expect(solveBody.task_constraints).toEqual(taskConstraints);
    expect(solveBody.goal_id).toBe("goal-new-1");
    // §4.5：新建目标成功即把 goal_id 同步进 URL（无残留 action 参数）。
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant\?goal=goal-new-1$/));
  });

  it("sends goal_id through the streaming channel when a goal is bound (§6.4)", async () => {
    mockAiConfigured();
    mocks.goalDetail = goalFixture({ context: goalContext });
    mocks.stream.mockResolvedValue(interpretationFixture());
    const user = userEvent.setup();
    renderAssistant("/assistant?goal=goal-77");
    await resumeParse(user);
    await waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(1));
    const streamArgs = mocks.stream.mock.calls[0] as unknown[];
    // 流式主路径第 4 个参数即 goalId（§6.4 必改点）。
    expect(streamArgs[3]).toBe("goal-77");
  });

  it("omits goal_id on the streaming channel when no goal is bound (§6.4)", async () => {
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture());
    const user = userEvent.setup();
    renderAssistant();
    await parse(user);
    await waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(1));
    const streamArgs = mocks.stream.mock.calls[0] as unknown[];
    expect(streamArgs[3]).toBeUndefined();
  });

  it("carries goal_id in the sync fallback body when streaming fails (§6.4)", async () => {
    mockAiConfigured();
    mocks.goalDetail = goalFixture({ context: goalContext });
    mocks.stream.mockRejectedValue(new Error("stream unavailable"));
    mocks.post.mockResolvedValue({ data: interpretationFixture() });
    const user = userEvent.setup();
    renderAssistant("/assistant?goal=goal-77");
    await resumeParse(user);
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    const fallbackCall = mocks.post.mock.calls.find((call) => call[0] === "/api/v1/assistant/interpret") as unknown[] | undefined;
    expect(fallbackCall).toBeDefined();
    // 同步回退与流式主路径口径一致（同样带 goal_id）。
    expect(fallbackCall![1]).toEqual({ instruction: expect.any(String), goal_id: "goal-77" });
  });
});

describe("AssistantPage resume restore (TC-6 §4.4/§4.5)", () => {
  it("restores instruction, context scope and the work-draft baseline when resuming ?goal=", async () => {
    mockAiConfigured();
    mocks.goalDetail = goalFixture({ context: goalContext });
    // 基准下拉需要能命中工作草稿选项；范围下拉需要命中业务线/班级候选。
    mocks.schedules = [scheduleFixture({ id: "sched-draft-9", solver_run_id: "run-draft", version_no: 2, name: "工作草稿", parent_id: null })];
    mocks.courses = [{ id: "cs-1", business_line: "考研", class_business_id: "B01", lesson_date: "2026-09-28" }];
    mocks.stream.mockResolvedValue(interpretationFixture());
    const user = userEvent.setup();
    renderAssistant("/assistant?goal=goal-77");
    // instruction 回填（不只绑 id），提示条说明上下文已恢复（解析后会换成沿用任务文案）。
    expect(await screen.findByText(/已接着办这个任务/)).toBeInTheDocument();
    expect(screen.getByLabelText("任务需求")).toHaveValue("重排 B01 班一周课表，避开周三晚间");
    expect(screen.getByText(/范围与需求已恢复/)).toBeInTheDocument();
    // 任务条上「你交代了什么」就是原始需求。
    expect(within(screen.getByLabelText("任务进展")).getByText("重排 B01 班一周课表，避开周三晚间")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: PARSE }));
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    // context.scope 回填共享参数草稿。
    await user.click(screen.getByRole("button", { name: "手动排课（自己设置参数）" }));
    expect(screen.getByLabelText("业务线")).toHaveValue("考研");
    expect(screen.getByLabelText("班级")).toHaveValue("B01");
    expect(screen.getByDisplayValue("2026-09-28")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2026-10-04")).toBeInTheDocument();
    // §4.6 前端口径：基准默认该目标正在调整的工作草稿。
    await user.click(screen.getByRole("button", { name: /更多选项/ }));
    expect(screen.getByLabelText("基准版本")).toHaveValue("sched-draft-9");
  });

  it("keeps old goals without context usable: only instruction is restored", async () => {
    mocks.goalDetail = goalFixture();
    const user = userEvent.setup();
    renderAssistant("/assistant?goal=goal-77");
    expect(await screen.findByText(/已接着办这个任务/)).toBeInTheDocument();
    expect(screen.getByLabelText("任务需求")).toHaveValue("重排 B01 班一周课表，避开周三晚间");
    // context 为空 → 范围留空照旧（AI 未配置时手动路径直接可用）。
    await user.click(screen.getByRole("button", { name: "手动排课（自己设置参数）" }));
    expect(await screen.findByRole("heading", { name: "手动排课参数" })).toBeInTheDocument();
    expect(screen.getByLabelText("业务线")).toHaveValue("");
    expect(screen.getByLabelText("班级")).toHaveValue("");
  });

  it("lands on the latest run of the goal, so resuming shows where the task got to", async () => {
    mocks.goalDetail = goalFixture({ latest_run_id: "run-9", run_count: 1 });
    mocks.runDetails["run-9"] = runFixture({ id: "run-9", model_status: "INFEASIBLE", goal_id: "goal-77" });
    renderAssistant("/assistant?goal=goal-77");
    expect(await screen.findByRole("heading", { name: "在现有的硬性要求下排不出全部课" })).toBeInTheDocument();
    expect(screen.queryByLabelText("接着办")).not.toBeInTheDocument();
  });

  it("removes the goal param from the URL when the association is cleared (§4.5)", async () => {
    mocks.goalDetail = goalFixture();
    const user = userEvent.setup();
    renderAssistant("/assistant?goal=goal-77");
    expect(await screen.findByText(/已接着办这个任务/)).toBeInTheDocument();
    expect(screen.getByTestId("location-probe")).toHaveTextContent("goal=goal-77");
    await user.click(screen.getByRole("button", { name: "手动排课（自己设置参数）" }));
    await user.click(await screen.findByRole("button", { name: "清除任务关联" }));
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant$/));
  });

  it("rebinds when the URL points at another goal", async () => {
    mocks.goalDetail = goalFixture({ id: "goal-A", instruction: "任务 A 的需求" });
    renderAssistant("/assistant?goal=goal-A");
    expect(await screen.findByText(/已接着办这个任务/)).toBeInTheDocument();
    expect(screen.getByLabelText("任务需求")).toHaveValue("任务 A 的需求");
  });
});

describe("AssistantPage budget retry and scope remedy (TC-6 §5.2)", () => {
  it("auto-submits a raised budget on mount when action=raise_budget, bypassing the interpretation guard", async () => {
    mocks.goalDetail = goalFixture({ context: goalContext });
    renderAssistant("/assistant?goal=goal-77&action=raise_budget");
    // 无任何解析状态（AI 未配置、从未解析）也能提交——不经过 solveFromInterpretation
    // 的守卫，直接复用手动求解提交路径（§5.2 评审指认的机制修正）。
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    expect(mocks.stream).not.toHaveBeenCalled();
    const submitted = mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> };
    // 时限 30 → max(30×3, 90) = 90；范围来自 context.scope 回填，未被重选。
    expect(submitted.data.time_limit_seconds).toBe(90);
    expect(submitted.data.goal_id).toBe("goal-77");
    expect(submitted.data.business_lines).toEqual(["考研"]);
    expect(submitted.data.class_business_ids).toEqual(["B01"]);
    expect(submitted.data.date_from).toBe("2026-09-28");
    expect(submitted.data.date_to).toBe("2026-10-04");
    expect(submitted.data.solver_rules).toEqual(expect.arrayContaining(["room_no_overlap", "teacher_no_overlap"]));
    // action 参数执行后摘除：刷新不会重复提交。
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant\?goal=goal-77$/));
    expect(mocks.submitMutate).toHaveBeenCalledTimes(1);
  });

  it("raises the budget from the gap remedy button and caps the limit at 900", async () => {
    const user = userEvent.setup();
    mocks.goalDetail = goalFixture({ id: "goal-9", latest_run_id: "run-goal", run_count: 1 });
    mocks.runDetails["run-goal"] = runFixture({
      id: "run-goal",
      goal_id: "goal-9",
      goal_report: {
        goal_id: "goal-9",
        instruction: "重排 B1 班三天课",
        all_passed: false,
        passed_count: 1,
        failed_count: 1,
        items: [{ key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", passed: false, detail: "缺 1 个课次" }],
        gaps: [{ key: "coverage", kind: "coverage", summary: "求解未安置全部课次", next_step: "加大时间预算后重跑", remedy: "raise_budget" }],
        decision: { status: "awaiting_decision", reason: "1 项未通过" },
      },
    });
    renderAssistant("/assistant?goal=goal-9");
    const remedyButton = await screen.findByRole("button", { name: "加大时间预算重跑" });
    // 先把时限改到 350：350×3=1050 → 上限 900。
    await user.click(screen.getByRole("button", { name: "手动排课（自己设置参数）" }));
    const timeInput = screen.getByLabelText("求解时限（秒）");
    await user.clear(timeInput);
    await user.type(timeInput, "350");
    await user.click(remedyButton);
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    const submitted = mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> };
    expect(submitted.data.time_limit_seconds).toBe(900);
    expect(submitted.data.goal_id).toBe("goal-9");
    // 只加预算不重选范围：范围字段保持原状。
    expect(submitted.data.business_lines).toEqual([]);
    expect(submitted.data.date_from).toBeNull();
  });

  it("expands the manual panel before scrolling to the scope area for action=resolve_scope", async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    try {
      mocks.goalDetail = goalFixture();
      renderAssistant("/assistant?goal=goal-77&action=resolve_scope");
      expect(await screen.findByRole("heading", { name: "手动排课参数" })).toBeInTheDocument();
      await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
      const target = scrollIntoView.mock.contexts[0] as HTMLElement;
      expect(target.id).toBe("assistant-scope-fieldset");
      await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant\?goal=goal-77$/));
    } finally {
      delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });

  it("runs the scope remedy button from a gap: expands the manual panel and focuses the scope area", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    try {
      mocks.goalDetail = goalFixture({ id: "goal-9", latest_run_id: "run-goal", run_count: 1 });
      mocks.runDetails["run-goal"] = runFixture({
        id: "run-goal",
        goal_id: "goal-9",
        goal_report: {
          goal_id: "goal-9",
          all_passed: false,
          passed_count: 0,
          failed_count: 1,
          items: [{ key: "coverage", kind: "coverage", requirement: "覆盖", passed: false, detail: "范围不一致" }],
          gaps: [{ key: "coverage", kind: "coverage", summary: "范围与清单不一致", next_step: "修正范围后重跑", remedy: "resolve_scope" }],
          decision: null,
        },
      });
      renderAssistant("/assistant?goal=goal-9");
      await user.click(await screen.findByRole("button", { name: "修正范围" }));
      expect(await screen.findByRole("heading", { name: "手动排课参数" })).toBeInTheDocument();
      await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
    } finally {
      delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });
});

describe("AssistantPage exit for requirements that could not be resolved", () => {
  it("goes straight to the in-card supplement for the bound goal instead of creating another one when resuming", async () => {
    mockAiConfigured();
    mocks.goalDetail = goalFixture({ context: goalContext, checklist: [PLACEHOLDER_ITEM] });
    mocks.stream.mockResolvedValue(
      interpretationFixture({
        unsupported_requirements: ["具体教师的禁排或请假要求"],
        goal_checklist_draft: [PLACEHOLDER_ITEM],
      }),
    );
    const user = userEvent.setup();
    renderAssistant("/assistant?goal=goal-77");
    await resumeParse(user);
    await screen.findByText(/已解析完成（用时/);
    // 不再有「前往目标跟踪补充」：补参表单就在确认卡内，任务复用已绑定的那个。
    expect(screen.getByRole("button", { name: CONFIRM })).toBeDisabled();
    expect(await screen.findByLabelText("禁排主体类型")).toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
    expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant\?goal=goal-77$/);
  });
});
