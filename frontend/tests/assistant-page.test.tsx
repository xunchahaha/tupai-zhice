import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderAssistant } from "./support/assistant-render";
import {
  assistantMocks as mocks,
  goalFixture,
  interpretationFixture,
  mockAiConfigured,
  PLACEHOLDER_ITEM,
  resetAssistantMocks,
  runFixture,
  scheduleFixture,
} from "./support/assistant-mocks";

vi.mock("@/api/generated/client", async () => (await import("./support/assistant-mocks")).clientMock);
vi.mock("@/api/http", async () => (await import("./support/assistant-mocks")).httpMock);
// 流式通道单独 mock：页面默认走 stream，失败时才回退 http.post。
vi.mock("@/lib/interpret-stream", async () => (await import("./support/assistant-mocks")).interpretStreamMock);

const PARSE = "让 AI 解析";
const CONFIRM = /确认并开始求解/;

async function parse(user: ReturnType<typeof userEvent.setup>, text = "请在三天内重排考研课程") {
  await user.type(await screen.findByLabelText("排课需求"), text);
  await user.click(screen.getByRole("button", { name: PARSE }));
}

async function openManualFromTaskView(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "手动排课（自己设置参数）" }));
}

beforeEach(resetAssistantMocks);
afterEach(cleanup);

describe("AssistantPage home", () => {
  it("leads with the request card, a always-available manual entry, the task list and folded insights", async () => {
    mockAiConfigured();
    mocks.rules = [{ id: "r1" }];
    renderAssistant();
    expect(await screen.findByRole("heading", { name: "排课助手" })).toBeInTheDocument();
    expect(screen.getByLabelText("排课需求")).toHaveAttribute("placeholder", expect.stringContaining("张老师周三晚上不能上"));
    expect(screen.getByRole("button", { name: "手动排课（自己设置参数）" })).toBeInTheDocument();
    expect(screen.getByLabelText("正在处理的任务")).toBeInTheDocument();
    // 折叠区默认收起，数据概览展开前不发任何概览请求。
    expect(screen.getByRole("button", { name: /数据概览/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: /求解记录/ })).toHaveAttribute("aria-expanded", "false");
    expect(mocks.overviewHook).not.toHaveBeenCalled();
    // 手动排课参数不再是「先解析成功才出现」的唯一路径，但默认也不占版面。
    expect(screen.queryByRole("heading", { name: "手动排课参数" })).not.toBeInTheDocument();
  });

  it("mounts the data overview only when it is expanded, and no longer carries the old quick-entry grid", async () => {
    const user = userEvent.setup();
    renderAssistant();
    await user.click(await screen.findByRole("button", { name: /数据概览/ }));
    await waitFor(() => expect(mocks.overviewHook).toHaveBeenCalled());
    expect(await screen.findByText("教师总数")).toBeInTheDocument();
    expect(screen.queryByText("排课工作台快捷入口")).not.toBeInTheDocument();
  });

  it("shows the compact setup checklist only while something is unfinished, with the first-use hint", async () => {
    renderAssistant();
    const row = await screen.findByLabelText("排课准备清单");
    expect(within(row).getByText("先导入课程资料")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: /基础资料已导入/ })).toHaveAttribute("href", "/master-data");
    expect(within(row).getByRole("link", { name: /已确认排课规则/ })).toHaveAttribute("href", "/rules");
    expect(within(row).getByRole("link", { name: /已发布课表版本/ })).toHaveAttribute("href", "/schedule?view=history");
    cleanup();
    // 全部就绪（含 AI 已配置）后清单不再出现。
    mockAiConfigured();
    mocks.rules = [{ id: "r1" }];
    mocks.courses = [{ id: "c1", class_business_id: "A班" }];
    mocks.schedules = [{ id: "pub", status: "published", version_no: 1, name: "已发布", solver_run_id: "r0" }];
    renderAssistant();
    await screen.findByLabelText("排课需求");
    await waitFor(() => expect(screen.getByText("text-model 已接入")).toBeInTheDocument());
    expect(screen.queryByLabelText("排课准备清单")).not.toBeInTheDocument();
  });

  it("prefills the request from ?prompt= without submitting it", async () => {
    mockAiConfigured();
    renderAssistant("/assistant?prompt=" + encodeURIComponent("下周 A 班调课，先出草稿"));
    expect(await screen.findByLabelText("排课需求")).toHaveValue("下周 A 班调课，先出草稿");
    expect(mocks.stream).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant$/));
  });

  it("expands the manual panel straight away for ?manual=1", async () => {
    renderAssistant("/assistant?manual=1");
    expect(await screen.findByRole("heading", { name: "手动排课参数" })).toBeInTheDocument();
  });

  it("toggles the manual panel from the always-available text button, without any parse or AI", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    renderAssistant();
    const entry = await screen.findByRole("button", { name: "手动排课（自己设置参数）" });
    await user.click(entry);
    expect(screen.getByRole("heading", { name: "手动排课参数" })).toBeInTheDocument();
    await user.click(entry);
    expect(screen.queryByRole("heading", { name: "手动排课参数" })).not.toBeInTheDocument();
  });

  it("promotes the manual path and offers 去配置 when AI is not configured", async () => {
    const user = userEvent.setup();
    renderAssistant();
    expect(await screen.findByRole("heading", { name: "手动排课参数" })).toBeInTheDocument();
    expect(screen.getByText(/还没有配置 AI/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去配置" })).toHaveAttribute("href", "/settings?section=ai");
    expect(screen.getByRole("button", { name: PARSE })).toBeDisabled();
    // 手动求解不依赖 AI：没有关联任务时 goal_id 为 null（后端 goal_id 可选，等价于未关联）。
    expect(screen.queryByText(/本次求解关联任务/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /按参数开始求解/ }));
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    const submitted = mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> };
    expect(submitted.data.goal_id).toBeNull();
    expect(submitted.data.solver_rules).toEqual(expect.arrayContaining(["room_no_overlap", "teacher_no_overlap"]));
  });
});

describe("AssistantPage interpret phases", () => {
  it("moves to the task view after a parse: confirmation card first, manual params on request with the AI backfill", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture());
    renderAssistant();
    await parse(user);
    expect(await screen.findByRole("heading", { name: "我理解的是……" })).toBeInTheDocument();
    // 思考区完成后折叠为「已解析完成（用时 N 秒）」，可展开回看。
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    // 流式成功不应触发同步回退。
    expect(mocks.post).not.toHaveBeenCalled();
    // 需求输入卡让位给任务视图；范围回填已经显示在确认卡上。
    expect(screen.queryByLabelText("排课需求")).not.toBeInTheDocument();
    expect(screen.getByText("2026-08-17")).toBeInTheDocument();
    expect(screen.getByText("3 天")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /展开回看思考过程/ }));
    expect(screen.getAllByText("用户要求 3 天窗口，先核对候选业务线。").length).toBeGreaterThan(0);
    // D6：日期三元组回填进手动参数，窗口 ≠ 默认 7 时带「来自 AI 解析」徽标。
    await openManualFromTaskView(user);
    expect(screen.getByRole("heading", { name: "手动排课参数" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("3")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2026-08-17")).toBeInTheDocument();
    expect(screen.getByText("来自 AI 解析")).toBeInTheDocument();
  });

  it("appends streaming thinking deltas live and keeps the stage list as fallback", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    let handlers: { onThinking?: (delta: string, elapsed: number) => void } | null = null;
    mocks.stream.mockImplementation((...args: unknown[]) => {
      handlers = (args[2] as { onThinking?: (delta: string, elapsed: number) => void } | undefined) ?? null;
      return new Promise(() => { /* 挂起，模拟流式在途 */ });
    });
    renderAssistant();
    await parse(user);
    expect(await screen.findByText("正在连接 AI 模型…")).toBeInTheDocument();
    // 无增量的空窗期：只有阶段文案，没有实时思考块。
    expect(screen.queryByText(/先核对候选业务线/)).not.toBeInTheDocument();
    await act(async () => {
      handlers?.onThinking?.("先核对候选业务线。", 0.4);
      handlers?.onThinking?.("再把三天换算成窗口。", 0.8);
    });
    expect(await screen.findByText(/先核对候选业务线。再把三天换算成窗口。/)).toBeInTheDocument();
    expect(screen.getByText("正在连接 AI 模型…")).toBeInTheDocument();
  });

  it("shows stage progress and cancel while parsing, and returns to idle on cancel", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockImplementation((...args: unknown[]) => new Promise((_resolve, reject) => {
      (args[1] as AbortSignal).addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }));
    renderAssistant();
    await parse(user);
    expect(await screen.findByText("正在连接 AI 模型…")).toBeInTheDocument();
    expect(screen.getByText(/已用时/)).toBeInTheDocument();
    expect(screen.getByLabelText("排课需求")).toBeDisabled();
    expect(screen.queryByRole("heading", { name: "手动排课参数" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "取消解析" }));
    await waitFor(() => expect(screen.getByLabelText("排课需求")).toBeEnabled());
    expect(screen.queryByText("正在连接 AI 模型…")).not.toBeInTheDocument();
  });

  it("renders a persistent error bar with retry when stream and fallback both fail", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.post.mockRejectedValue(new Error("AI 模型请求失败：连接超时"));
    renderAssistant();
    await parse(user);
    expect(await screen.findByText(/AI 解析失败：AI 模型请求失败：连接超时/)).toBeInTheDocument();
    // 流式失败后必须先尝试同步回退，再展示最终错误。
    expect(mocks.post).toHaveBeenCalledWith("/api/v1/assistant/interpret", { instruction: "请在三天内重排考研课程" }, expect.anything());
    expect(screen.getByRole("button", { name: /重试解析/ })).toBeInTheDocument();
    // 失败不进任务视图：需求还在，可以直接改了再试。
    expect(screen.getByLabelText("排课需求")).toHaveValue("请在三天内重排考研课程");
    expect(screen.queryByRole("heading", { name: "我理解的是……" })).not.toBeInTheDocument();
  });

  it("falls back to the sync endpoint and completes the parse when streaming fails", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockRejectedValue(new Error("网关不支持流式响应"));
    mocks.post.mockResolvedValue({ data: interpretationFixture() });
    renderAssistant();
    await parse(user);
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "我理解的是……" })).toBeInTheDocument();
    await openManualFromTaskView(user);
    expect(screen.getByDisplayValue("3")).toBeInTheDocument();
  });

  it("keeps the AI entry usable when only the feishu configuration read fails (问题6)", async () => {
    // 飞书配置读取失败不得把已配置好的通用 AI 入口一起打成不可用（可选集成解耦）。
    mocks.get.mockImplementation(async (...args: unknown[]) => {
      const url = String(args[0]);
      if (url === "/api/v1/integrations/feishu/connection") throw new Error("feishu 配置读取失败");
      return { data: { configured: true, model: null, app_configuration: { aily_configured: false } } };
    });
    renderAssistant();
    expect(await screen.findByText("通用 AI 模型 已接入")).toBeInTheDocument();
    expect(screen.queryByText("AI 配置读取失败")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    await userEvent.setup().type(screen.getByLabelText("排课需求"), "重排考研课程");
    expect(screen.getByRole("button", { name: PARSE })).toBeEnabled();
  });

  it("still shows the probe error with retry when both AI and feishu reads fail (问题6)", async () => {
    mocks.get.mockRejectedValue(new Error("网络中断"));
    renderAssistant();
    expect(await screen.findByText("AI 配置读取失败")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: PARSE })).toBeDisabled();
    // 探测失败时手动路径是唯一入口，直接展示。
    expect(screen.getByRole("heading", { name: "手动排课参数" })).toBeInTheDocument();
  });
});

describe("AssistantPage goal acceptance loop (MEM-C3)", () => {
  beforeEach(() => {
    mocks.schedules = [scheduleFixture({ id: "v1", status: "published", version_no: 1, name: "已发布课表", parent_id: null, solver_run_id: "run-0" })];
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      const url = String(args[0]);
      if (url === "/api/v1/goals") return { data: { id: "goal-1", status: "open", checklist: [] } };
      return { data: { id: "run-1", status: "queued", model_status: null } };
    });
  });

  it("creates a tracking goal with the prefilled checklist, then solves with goal_id and syncs it into the URL", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue({
      ...interpretationFixture(),
      goal_checklist_draft: [
        { key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", params: {} },
        { key: "draft_only", requirement: "只交付草稿", kind: "draft_only", params: {} },
      ],
    });
    renderAssistant();
    await parse(user);
    await user.click(await screen.findByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    const [goalUrl, goalBody] = mocks.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(goalUrl).toBe("/api/v1/goals");
    expect(goalBody.instruction).toBe("请在三天内重排考研课程");
    expect((goalBody.checklist as unknown[]).length).toBe(2);
    // 发布永远不由生成流程自动触发。
    expect(goalBody.forbid_publish).toBe(true);
    const [solveUrl, solveBody] = mocks.post.mock.calls[1] as [string, Record<string, unknown>];
    expect(solveUrl).toBe("/api/v1/assistant/solve");
    expect(solveBody.goal_id).toBe("goal-1");
    // 确认卡让位给进行中卡；新任务同步进 URL，run 参数不残留。
    expect(await screen.findByText("正在排课…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: CONFIRM })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant\?goal=goal-1$/));
    expect(mocks.publishMutate).not.toHaveBeenCalled();
  });

  it("skips goal creation when tracking is switched off under 更多选项 and sends goal_id null", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture());
    mocks.post.mockResolvedValue({ data: { id: "run-3", status: "queued" } });
    renderAssistant();
    await parse(user);
    // 目标跟踪默认开启并直接生效，选项收在「更多选项」里。
    expect(screen.queryByLabelText("以此为目标跟踪")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /更多选项/ }));
    expect(screen.getByLabelText("以此为目标跟踪")).toBeChecked();
    await user.click(screen.getByLabelText("以此为目标跟踪"));
    expect(screen.queryByLabelText("基准版本")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    const [solveUrl, solveBody] = mocks.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(solveUrl).toBe("/api/v1/assistant/solve");
    expect(solveBody.goal_id).toBeNull();
  });

  it("records only the baseline without a change limit unless the limit toggle is enabled (问题4)", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue({
      ...interpretationFixture(),
      goal_checklist_draft: [{ key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", params: {} }],
    });
    renderAssistant();
    await parse(user);
    await user.click(await screen.findByRole("button", { name: /更多选项/ }));
    await user.selectOptions(screen.getByLabelText("基准版本"), "v1");
    // 默认不设上限：确认卡如实说明「未设变更上限」，上限输入也不出现。
    expect(screen.getByText("已选基准：仅记录基准用于变更对比，未设变更上限")).toBeInTheDocument();
    expect(screen.queryByLabelText("变更数验收上限")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    const [goalUrl, goalBody] = mocks.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(goalUrl).toBe("/api/v1/goals");
    // 选基准不再静默附带 max_changes=50：清单里没有任何 max_changes 项。
    expect((goalBody.checklist as Array<{ kind: string }>).some((item) => item.kind === "max_changes")).toBe(false);
    // 基准本身仍被记录（用于变更明细/数量对比与优化配置）。
    expect(goalBody.baseline_schedule_version_id).toBe("v1");
  });

  it("attaches the max_changes checklist item only after 设置变更上限 is enabled (问题4)", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue({
      ...interpretationFixture(),
      goal_checklist_draft: [{ key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", params: {} }],
    });
    renderAssistant();
    await parse(user);
    await user.click(await screen.findByRole("button", { name: /更多选项/ }));
    await user.selectOptions(screen.getByLabelText("基准版本"), "v1");
    // 明确开启「设置变更上限」后上限输入才出现，此时确认卡说明将附带验收项。
    await user.click(screen.getByLabelText("设置变更上限"));
    expect(screen.getByText("已选基准：清单将附带变更数验收项")).toBeInTheDocument();
    const limitInput = screen.getByLabelText("变更数验收上限");
    await user.clear(limitInput);
    await user.type(limitInput, "3");
    await user.click(screen.getByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    const [goalUrl, goalBody] = mocks.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(goalUrl).toBe("/api/v1/goals");
    const maxChanges = (goalBody.checklist as Array<{ kind: string; params: Record<string, unknown> }>).find((item) => item.kind === "max_changes");
    expect(maxChanges).toBeDefined();
    expect(maxChanges!.params.max_changes).toBe(3);
    expect(maxChanges!.params.baseline_schedule_version_id).toBe("v1");
    expect(goalBody.baseline_schedule_version_id).toBe("v1");
  });

  it("keeps the parsed scope intact when only the time budget is adjusted before a manual retry (问题2)", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue({
      ...interpretationFixture(),
      business_lines: ["考研"],
      product_types: ["暑期集训"],
      class_business_ids: ["C-001"],
    });
    renderAssistant();
    await parse(user);
    await user.click(await screen.findByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    const [solveUrl, solveBody] = mocks.post.mock.calls[1] as [string, Record<string, unknown>];
    expect(solveUrl).toBe("/api/v1/assistant/solve");
    expect(solveBody.business_lines).toEqual(["考研"]);
    expect(solveBody.product_types).toEqual(["暑期集训"]);
    expect(solveBody.class_business_ids).toEqual(["C-001"]);
    expect(solveBody.date_from).toBe("2026-08-17");
    expect(solveBody.date_to).toBe("2026-08-19");
    expect(solveBody.goal_id).toBe("goal-1");

    // 只调时间预算（30 → 60），不动任何范围字段，然后走手动入口重试。
    await openManualFromTaskView(user);
    const timeInput = screen.getByLabelText("求解时限（秒）");
    await user.clear(timeInput);
    await user.type(timeInput, "60");
    await user.click(screen.getByRole("button", { name: /按参数开始求解/ }));
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    const submitted = mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> };
    expect(submitted.data.time_limit_seconds).toBe(60);
    // 除预算外，范围字段与第一次请求完全一致（含 goal_id 关联场景）。
    expect(submitted.data.business_lines).toEqual(["考研"]);
    expect(submitted.data.product_types).toEqual(["暑期集训"]);
    expect(submitted.data.class_business_ids).toEqual(["C-001"]);
    expect(submitted.data.date_from).toBe("2026-08-17");
    expect(submitted.data.date_to).toBe("2026-08-19");
    expect(submitted.data.date_window_days).toBe(3);
    expect(submitted.data.goal_id).toBe("goal-1");
  });

  it("gates a widened scope behind an explicit confirmation shown on the confirmation card (问题2)", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.courses = [{ id: "cs-1", business_line: "考研", class_business_id: "C-001", lesson_date: "2026-08-17" }];
    mocks.stream.mockResolvedValue({ ...interpretationFixture(), class_business_ids: ["C-001"] });
    mocks.post.mockResolvedValue({ data: { id: "run-wide", status: "queued", model_status: null } });
    renderAssistant();
    await parse(user);
    expect(await screen.findByText(/已解析完成（用时/)).toBeInTheDocument();
    await openManualFromTaskView(user);
    // 把班级从解析出的 C-001 改成「全部班级」＝扩大范围，需要单独确认。
    await user.selectOptions(screen.getByLabelText("班级"), "");
    // 二次确认直接展示在主流程上，不藏在手动面板里。
    const alert = screen.getByText(/扩大范围需要单独确认/).closest("[role=alert]") as HTMLElement;
    expect(within(alert).getByRole("button", { name: "确认扩大范围" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /按参数开始求解/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: CONFIRM })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "确认扩大范围" }));
    expect(screen.queryByText(/扩大范围需要单独确认/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /按参数开始求解/ }));
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    const submitted = mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> };
    expect(submitted.data.class_business_ids).toEqual([]);
  });

  it("restores the original scope when the pending widening is reverted", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.courses = [{ id: "cs-1", business_line: "考研", class_business_id: "C-001", lesson_date: "2026-08-17" }];
    mocks.stream.mockResolvedValue({ ...interpretationFixture(), class_business_ids: ["C-001"] });
    renderAssistant();
    await parse(user);
    await screen.findByText(/已解析完成（用时/);
    await openManualFromTaskView(user);
    await user.selectOptions(screen.getByLabelText("班级"), "");
    await user.click(screen.getByRole("button", { name: "恢复原范围" }));
    expect(screen.queryByText(/扩大范围需要单独确认/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("班级")).toHaveValue("C-001");
    expect(screen.getByRole("button", { name: CONFIRM })).toBeEnabled();
  });

  it("keeps manually edited scope fields across a re-parse (问题2)", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.courses = [
      { id: "cs-1", business_line: "考研", class_business_id: "C-001", lesson_date: "2026-08-17" },
      { id: "cs-2", business_line: "考研", class_business_id: "C-002", lesson_date: "2026-08-18" },
    ];
    mocks.stream.mockResolvedValue({ ...interpretationFixture(), class_business_ids: ["C-001"] });
    renderAssistant();
    await parse(user);
    await screen.findByText(/已解析完成（用时/);
    await openManualFromTaskView(user);
    await user.selectOptions(screen.getByLabelText("班级"), "C-002");
    // 重新解析同一指令：用户手动改过的范围字段不被解析结果覆盖。
    await user.click(screen.getByRole("button", { name: "重新解析" }));
    await waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(2));
    await screen.findByText(/已解析完成（用时/);
    expect(screen.getByLabelText("班级")).toHaveValue("C-002");
  });
});

describe("AssistantPage continue-adjusting a draft", () => {
  function completedRun() {
    return runFixture({ id: "run-1", goal_id: "goal-1" });
  }

  beforeEach(() => {
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture());
    mocks.schedules = [
      scheduleFixture({ id: "pub-1", status: "published", version_no: 1, name: "已发布", parent_id: null, solver_run_id: "run-0" }),
      scheduleFixture({ id: "draft-1", solver_run_id: "run-1" }),
    ];
    mocks.runDetails["run-1"] = completedRun();
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      const url = String(args[0]);
      if (url === "/api/v1/goals") return { data: { id: "goal-1", status: "open", checklist: [] } };
      if (url.endsWith("/explanation")) return { data: { headline: "已排好", explanation: [], next_actions: [], source: "system" } };
      return { data: { id: "run-1", status: "queued", model_status: null, goal_id: "goal-1" } };
    });
  });

  it("re-parses with the same goal on the draft baseline, then reuses that goal for the next solve", async () => {
    const user = userEvent.setup();
    mocks.goalDetail = goalFixture({ id: "goal-1", instruction: "请在三天内重排考研课程" });
    renderAssistant();
    await parse(user);
    await user.click(await screen.findByRole("button", { name: CONFIRM }));
    await screen.findByRole("heading", { name: /已生成草稿/ });
    mocks.post.mockClear();

    await user.click(screen.getByRole("button", { name: "继续调整" }));
    await user.type(screen.getByLabelText("还要改什么？"), "张老师周三晚上也不能上");
    await user.click(screen.getByRole("button", { name: "重新解析" }));
    await waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(2));
    const streamArgs = mocks.stream.mock.calls[1] as unknown[];
    // 有任务时只发追加的话，并带 goal_id（后端据任务上下文增量解析）。
    expect(streamArgs[0]).toBe("张老师周三晚上也不能上");
    expect(streamArgs[3]).toBe("goal-1");
    // 解析回来先回到确认卡（不自动求解）；基准默认是刚才这份草稿。
    expect(await screen.findByRole("heading", { name: "我理解的是……" })).toBeInTheDocument();
    expect(screen.getByText(/本次解析的.*与原任务不一致——仍按这个任务排课/)).toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /更多选项/ }));
    expect(screen.getByLabelText("基准版本")).toHaveValue("draft-1");
    await user.click(screen.getByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    // 不再新建任务，直接复用已绑定的任务求解。
    const [solveUrl, solveBody] = mocks.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(solveUrl).toBe("/api/v1/assistant/solve");
    expect(solveBody.goal_id).toBe("goal-1");
  });

  it("appends the extra request to the original one when there is no tracked goal", async () => {
    const user = userEvent.setup();
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      if (String(args[0]).endsWith("/explanation")) return { data: { headline: "已排好", explanation: [], next_actions: [], source: "system" } };
      return { data: { id: "run-1", status: "queued", model_status: null } };
    });
    mocks.runDetails["run-1"] = runFixture({ id: "run-1" });
    renderAssistant();
    await parse(user);
    await user.click(await screen.findByRole("button", { name: /更多选项/ }));
    await user.click(screen.getByLabelText("以此为目标跟踪"));
    await user.click(screen.getByRole("button", { name: CONFIRM }));
    await screen.findByRole("heading", { name: /已生成草稿/ });
    await user.click(screen.getByRole("button", { name: "继续调整" }));
    await user.type(screen.getByLabelText("还要改什么？"), "再避开周五");
    await user.click(screen.getByRole("button", { name: "重新解析" }));
    await waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(2));
    const streamArgs = mocks.stream.mock.calls[1] as unknown[];
    expect(streamArgs[0]).toBe("请在三天内重排考研课程；再避开周五");
    expect(streamArgs[3]).toBeUndefined();
  });

  it("returns to the home page from the task bar and forgets the session task", async () => {
    const user = userEvent.setup();
    mocks.goalDetail = goalFixture({ id: "goal-1", instruction: "请在三天内重排考研课程" });
    renderAssistant();
    await parse(user);
    await user.click(await screen.findByRole("button", { name: CONFIRM }));
    await screen.findByRole("heading", { name: /已生成草稿/ });
    await user.click(screen.getByRole("link", { name: /返回排课助手首页/ }));
    expect(await screen.findByLabelText("排课需求")).toHaveValue("");
    expect(screen.queryByRole("heading", { name: /已生成草稿/ })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant$/));
  });
});

describe("AssistantPage permissions", () => {
  it("read-only members see the explanation and the data overview instead of the request input and tasks", async () => {
    mocks.goals = [goalFixture()];
    renderAssistant("/assistant", "viewer");
    expect(await screen.findByText("当前账号为只读，可在「课表」查看课表与版本。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去课表" })).toHaveAttribute("href", "/schedule");
    expect(screen.queryByLabelText("排课需求")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("正在处理的任务")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("待发布的草稿")).not.toBeInTheDocument();
    // 数据概览直接展示，不用展开。
    expect(await screen.findByText("教师总数")).toBeInTheDocument();
    expect(mocks.stream).not.toHaveBeenCalled();
  });

  it("an approver without scheduling rights can review and publish drafts but cannot start a request", async () => {
    const user = userEvent.setup();
    mocks.schedules = [scheduleFixture()];
    renderAssistant("/assistant", "approver");
    expect(await screen.findByText(/没有排课权限/)).toBeInTheDocument();
    expect(screen.queryByLabelText("排课需求")).not.toBeInTheDocument();
    const drafts = screen.getByLabelText("待发布的草稿");
    expect(within(drafts).getByText(/v2 9 月排课草稿/)).toBeInTheDocument();
    await user.click(within(drafts).getByRole("button", { name: /审批发布/ }));
    expect(mocks.publishMutate).not.toHaveBeenCalled();
    await user.click(await screen.findByRole("button", { name: "确认发布" }));
    expect(mocks.publishMutate).toHaveBeenCalledWith({ scheduleId: "draft-1" });
  });

  it("lists drafts awaiting publish for publishers with a view link, and hides the block from schedulers", async () => {
    const user = userEvent.setup();
    mocks.schedules = [scheduleFixture(), scheduleFixture({ id: "pub-1", status: "published", version_no: 1, name: "已发布", parent_id: null })];
    renderAssistant();
    const drafts = await screen.findByLabelText("待发布的草稿");
    expect(within(drafts).getAllByRole("listitem")).toHaveLength(1);
    await user.click(within(drafts).getByRole("button", { name: /查看课表/ }));
    expect(screen.getByTestId("location-probe")).toHaveTextContent("/schedule?version=draft-1");
    cleanup();
    renderAssistant("/assistant", "scheduler");
    await screen.findByLabelText("排课需求");
    expect(screen.queryByLabelText("待发布的草稿")).not.toBeInTheDocument();
  });

  it("shows only the most recent few drafts by default and folds the rest behind 查看全部草稿", async () => {
    const user = userEvent.setup();
    mocks.schedules = Array.from({ length: 6 }, (_, index) =>
      scheduleFixture({ id: `draft-${index + 1}`, version_no: index + 2, name: `重试草稿 ${index + 1}`, created_at: `2026-09-2${index}T09:00:00+08:00` }),
    );
    renderAssistant();
    const drafts = await screen.findByLabelText("待发布的草稿");
    // 标题仍如实给出总数；列表只列最近 3 份（按生成时间倒序）。
    expect(within(drafts).getByRole("heading", { name: "待发布的草稿（6）" })).toBeInTheDocument();
    expect(within(drafts).getAllByRole("listitem")).toHaveLength(3);
    expect(within(drafts).getByText(/重试草稿 6/)).toBeInTheDocument();
    expect(within(drafts).queryByText(/重试草稿 1$/)).not.toBeInTheDocument();
    await user.click(within(drafts).getByRole("button", { name: "查看全部草稿（6）" }));
    expect(within(drafts).getAllByRole("listitem")).toHaveLength(6);
    await user.click(within(drafts).getByRole("button", { name: "只看最近 3 份" }));
    expect(within(drafts).getAllByRole("listitem")).toHaveLength(3);
  });

  it("publishing from the pending list needs a confirmation that spells out the consequences", async () => {
    const user = userEvent.setup();
    mocks.schedules = [scheduleFixture()];
    renderAssistant();
    const drafts = await screen.findByLabelText("待发布的草稿");
    await user.click(within(drafts).getByRole("button", { name: /审批发布/ }));
    expect(await screen.findByText(/发布后成为当前课表，并同步到已启用的外部集成；不会自动下发日历。/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(mocks.publishMutate).not.toHaveBeenCalled();
  });
});

describe("AssistantPage register-for-later becomes an in-card supplement", () => {
  const unsupported = () => ({
    ...interpretationFixture(),
    unsupported_requirements: ["具体教师的禁排或请假要求"],
    goal_checklist_draft: [{ key: "coverage", kind: "coverage", requirement: "覆盖全部目标课次", params: {} }, PLACEHOLDER_ITEM],
  });

  it("shows unresolved requirements directly, registers the goal on demand, supplements in place and re-parses with the same goal", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValueOnce(unsupported());
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      if (String(args[0]) === "/api/v1/goals") return { data: { id: "goal-later", status: "open", checklist: [PLACEHOLDER_ITEM] } };
      return { data: {} };
    });
    renderAssistant();
    await parse(user);
    // 未落实的要求直接展示，仍然拦住求解。
    expect(await screen.findByText("以下要求尚未进入求解（待补充）：")).toBeInTheDocument();
    expect(screen.getByText("具体教师的禁排或请假要求")).toBeInTheDocument();
    expect(screen.getByText(/还差一个条件/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: CONFIRM })).toBeDisabled();

    // 补充条件：先登记任务（清单里带待量化占位项），再行内出现补参表单，不跳走、不求解。
    mocks.goalDetail = goalFixture({ id: "goal-later", checklist: [PLACEHOLDER_ITEM] });
    await user.click(screen.getByRole("button", { name: "补充条件" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    const [url, body] = mocks.post.mock.calls[0] as [string, { checklist: Array<{ key: string }>; instruction: string }];
    expect(url).toBe("/api/v1/goals");
    expect(body.instruction).toBe("请在三天内重排考研课程");
    expect(body.checklist.map((item) => item.key)).toEqual(["coverage", "forbidden_slot_free-draft"]);
    expect(mocks.post.mock.calls.some((call) => String(call[0]) === "/api/v1/assistant/solve")).toBe(false);
    await user.selectOptions(await screen.findByLabelText("禁排主体类型"), "teacher");
    await user.selectOptions(screen.getByLabelText("禁排主体"), "T9");
    await user.selectOptions(screen.getByLabelText("添加禁排时段"), "S1");

    // 保存后自动用同一个任务重新解析；这次后端把补齐的要求编译进去了，确认按钮解除禁用。
    mocks.stream.mockResolvedValueOnce({ ...interpretationFixture(), unsupported_requirements: [] });
    await user.click(screen.getByRole("button", { name: "保存参数" }));
    await waitFor(() => expect(mocks.checklistPatch).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(2));
    expect((mocks.stream.mock.calls[1] as unknown[])[3]).toBe("goal-later");
    await waitFor(() => expect(screen.queryByText("以下要求尚未进入求解（待补充）：")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: CONFIRM })).toBeEnabled();
  });

  it("does not offer a fake exit for requirements that cannot be quantized (room / consecutive)", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue({
      ...interpretationFixture(),
      unsupported_requirements: ["指定教室要求"],
      goal_checklist_draft: [{ key: "coverage", kind: "coverage", requirement: "覆盖全部目标课次", params: {} }],
    });
    renderAssistant();
    await parse(user);
    await screen.findByText(/已解析完成（用时/);
    expect(screen.getByRole("button", { name: CONFIRM })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "补充条件" })).not.toBeInTheDocument();
    expect(screen.getByText(/请修改需求去掉这些要求后重新解析/)).toBeInTheDocument();
    const alert = screen.getByText("以下要求尚未进入求解（待补充）：").closest("[role=alert]") as HTMLElement;
    expect(within(alert).getByRole("link", { name: "学校通用规则" })).toHaveAttribute("href", "/rules");
  });

  it("shows coverage warnings directly on the confirmation card", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue({ ...interpretationFixture(), coverage_warnings: ["日期范围内没有 A 班的课次"] });
    renderAssistant();
    await parse(user);
    expect(await screen.findByText("日期范围内没有 A 班的课次")).toBeInTheDocument();
  });
});


describe("AssistantPage handoff from the schedule page (review #5 / follow-up)", () => {
  const draftDetail = {
    id: "draft-3",
    version_no: 3,
    name: "10 月调整草稿",
    status: "draft",
    assignments: [
      { course_business_id: "COURSE-9", class_business_id: "CLASS-B", teacher_business_id: "T-002", lesson_date: "2026-10-12", slot_business_id: "SLOT-周三-1900-2030", room_business_id: "R1" },
    ],
  };
  const entry = "/assistant?prompt=" + encodeURIComponent("调整 B班 2026-10-12 周三晚 的课（教师 李老师）：") + "&base=draft-3&lesson=COURSE-9";
  const EXPLAINED = { headline: "已解释", explanation: [], next_actions: [], source: "system" };
  const solveCalls = () => mocks.post.mock.calls.filter((call) => String(call[0]) === "/api/v1/assistant/solve");
  const manualBodies = () => mocks.submitMutate.mock.calls.map((call) => (call[0] as { data: Record<string, unknown> }).data);
  const mockGoalAndRun = () => {
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      if (String(args[0]) === "/api/v1/goals") return { data: { id: "goal-h", status: "open", checklist: [] } };
      return { data: { id: "run-h", status: "queued", goal_id: "goal-h" } };
    });
    // 第一次求解时间用完，没有产出草稿。
    mocks.runDetails["run-h"] = runFixture({ id: "run-h", goal_id: "goal-h", model_status: "UNKNOWN", explanation: EXPLAINED });
  };

  it("resolves the handed-over lesson from the chosen version and states it; the ids stay in the address bar until the task is registered", async () => {
    mockAiConfigured();
    mocks.scheduleDetails["draft-3"] = draftDetail;
    renderAssistant(entry);

    const notice = await screen.findByLabelText("调整对象");
    expect(notice).toHaveTextContent("基于课表 v3（草稿）的这一节课：2026-10-12");
    expect(notice).toHaveTextContent("班级 CLASS-B");
    expect(notice).toHaveTextContent("教师 T-002");
    expect(await screen.findByLabelText("排课需求")).toHaveValue("调整 B班 2026-10-12 周三晚 的课（教师 李老师）：");
    // 提示语只消费一次；版本与课次留在地址栏里——登记任务之前刷新页面还能恢复。
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant\?base=draft-3&lesson=COURSE-9$/));
  });

  it("re-adopts the handed-over lesson after a reload before the task exists", async () => {
    mockAiConfigured();
    mocks.scheduleDetails["draft-3"] = draftDetail;
    renderAssistant("/assistant?base=draft-3&lesson=COURSE-9");
    expect(await screen.findByLabelText("调整对象")).toHaveTextContent("基于课表 v3（草稿）的这一节课");
  });

  it("carries the lesson scope and the original base through the first solve AND a raised-budget retry that produced no draft", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.scheduleDetails["draft-3"] = draftDetail;
    mocks.stream.mockResolvedValue(interpretationFixture());
    mockGoalAndRun();
    renderAssistant(entry);
    await screen.findByLabelText("调整对象");
    await user.click(screen.getByRole("button", { name: PARSE }));
    await user.click(await screen.findByRole("button", { name: CONFIRM }));

    await waitFor(() => expect(solveCalls()).toHaveLength(1));
    const [, first] = solveCalls()[0] as [string, Record<string, unknown>];
    expect(first.parent_schedule_id).toBe("draft-3");
    expect(first.course_business_ids).toEqual(["COURSE-9"]);
    const goalBody = mocks.post.mock.calls[0][1] as Record<string, unknown>;
    expect(goalBody.course_business_ids).toEqual(["COURSE-9"]);
    // 登记任务时就把实际执行基准落库（与「变更数对比」的 baseline_schedule_version_id 是两回事）。
    expect(goalBody.base_schedule_id).toBe("draft-3");
    // 任务登记后由任务上下文承载：地址栏里的交接参数摘掉，但交接说明仍在。
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant\?goal=goal-h$/));
    expect(screen.getByLabelText("调整对象")).toHaveTextContent("基于课表 v3（草稿）的这一节课");

    // 第一次求解没有产出草稿，教务只点「加大时间预算重跑」：范围和基准都不能因此改变。
    const raise = await screen.findByRole("button", { name: "加大时间预算重跑" });
    await waitFor(() => expect(raise).toBeEnabled());
    await user.click(raise);
    await waitFor(() => expect(manualBodies()).toHaveLength(1));
    expect(manualBodies()[0]).toMatchObject({
      time_limit_seconds: 90,
      goal_id: "goal-h",
      course_business_ids: ["COURSE-9"],
      parent_schedule_id: "draft-3",
    });
  });

  it("restores the lesson scope from the task on reload, keeps sending it, and does not resend the original base once a work draft exists", async () => {
    const user = userEvent.setup();
    mocks.goalDetail = goalFixture({
      id: "goal-h",
      latest_run_id: "run-h",
      run_count: 1,
      context: {
        schema_version: 1,
        scope: { class_business_ids: [], business_lines: [], product_types: [], course_business_ids: ["COURSE-9"], date_from: null, date_to: null, date_window_days: 7 },
        base_schedule_id: "draft-3",
        work_draft_schedule_id: "draft-6",
      },
    });
    mocks.runDetails["run-h"] = runFixture({ id: "run-h", goal_id: "goal-h", model_status: "UNKNOWN", explanation: EXPLAINED });
    renderAssistant("/assistant?goal=goal-h");

    expect(await screen.findByLabelText("调整对象")).toHaveTextContent("本任务只针对课表里选中的 1 节课：COURSE-9");
    const raise = await screen.findByRole("button", { name: "加大时间预算重跑" });
    await waitFor(() => expect(raise).toBeEnabled());
    await user.click(raise);
    await waitFor(() => expect(manualBodies()).toHaveLength(1));
    expect(manualBodies()[0]).toMatchObject({ goal_id: "goal-h", course_business_ids: ["COURSE-9"] });
    // 基准由后端按「显式 > 任务工作草稿 > 记下的原始基准」选择，前端没有原始交接对象可发。
    expect(manualBodies()[0]).not.toHaveProperty("parent_schedule_id");
  });

  it("falls back to the checklist coverage for a task written before the lesson scope was stored in its context", async () => {
    mocks.goalDetail = goalFixture({
      id: "goal-h",
      latest_run_id: "run-h",
      run_count: 1,
      checklist: [{ key: "coverage", kind: "coverage", requirement: "覆盖所选课次", params: { course_business_ids: ["COURSE-9"] } }],
      context: { schema_version: 1, scope: { class_business_ids: [], business_lines: [], product_types: [], date_from: null, date_to: null, date_window_days: 7 } },
    });
    mocks.runDetails["run-h"] = runFixture({ id: "run-h", goal_id: "goal-h", model_status: "UNKNOWN", explanation: EXPLAINED });
    renderAssistant("/assistant?goal=goal-h");
    expect(await screen.findByLabelText("调整对象")).toHaveTextContent("COURSE-9");
  });

  it("makes dropping a submitted lesson scope an explicit expansion that must be confirmed", async () => {
    const user = userEvent.setup();
    mocks.goalDetail = goalFixture({
      id: "goal-h",
      latest_run_id: "run-h",
      run_count: 1,
      context: { schema_version: 1, scope: { class_business_ids: [], business_lines: [], product_types: [], course_business_ids: ["COURSE-9"], date_from: null, date_to: null, date_window_days: 7 } },
    });
    mocks.runDetails["run-h"] = runFixture({ id: "run-h", goal_id: "goal-h", model_status: "UNKNOWN", explanation: EXPLAINED });
    renderAssistant("/assistant?goal=goal-h");
    await user.click(await screen.findByRole("button", { name: "不限定课次" }));

    // 范围扩大要单独确认；确认之前所有求解入口都被拦下。
    expect(await screen.findByText(/课次范围（原来只调整选中的课次）将从限定范围改为「全部」/)).toBeInTheDocument();
    const raise = screen.getByRole("button", { name: "加大时间预算重跑" });
    expect(raise).toBeDisabled();
    // 恢复原范围：课次限定回来。
    await user.click(screen.getByRole("button", { name: "恢复原范围" }));
    expect(screen.getByLabelText("调整对象")).toHaveTextContent("本任务只针对课表里选中的 1 节课");

    await user.click(screen.getByRole("button", { name: "不限定课次" }));
    await user.click(await screen.findByRole("button", { name: "确认扩大范围" }));
    await waitFor(() => expect(screen.queryByLabelText("调整对象")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: "加大时间预算重跑" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "加大时间预算重跑" }));
    await waitFor(() => expect(manualBodies()).toHaveLength(1));
    expect(manualBodies()[0].course_business_ids).toEqual([]);
  });

  it("does not silently fall back to the published version when the chosen lesson is not in that version", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.scheduleDetails["draft-3"] = { ...draftDetail, assignments: [] };
    mocks.stream.mockResolvedValue(interpretationFixture());
    renderAssistant(entry);

    expect(await screen.findByText(/所选课次不在课表 v3（草稿）里，无法作为调整对象/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: PARSE }));
    await user.click(await screen.findByRole("button", { name: CONFIRM }));
    expect(solveCalls()).toHaveLength(0);

    // 用户明确取消限定后，才按需求整体排课，且请求里不再带基准与课次。
    mocks.post.mockResolvedValue({ data: { id: "run-h", status: "queued" } });
    await user.click(screen.getAllByRole("button", { name: "取消限定" })[0]);
    await user.click(await screen.findByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(solveCalls()).toHaveLength(1));
    const [, body] = solveCalls()[0] as [string, Record<string, unknown>];
    expect(body).not.toHaveProperty("parent_schedule_id");
    expect(body.course_business_ids).toEqual([]);
  });

  // 复审 R3 #1：任务已登记、首次求解还没开始，此时刷新——执行基准不能只活在页面状态里。
  it("registers the execution base together with the goal, and drops the ids from the URL only after the server accepted it", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.scheduleDetails["draft-3"] = draftDetail;
    mocks.stream.mockResolvedValueOnce({
      ...interpretationFixture(),
      unsupported_requirements: ["具体教师的禁排或请假要求"],
      goal_checklist_draft: [{ key: "coverage", kind: "coverage", requirement: "覆盖全部目标课次", params: {} }, PLACEHOLDER_ITEM],
    });
    let goalCalls = 0;
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      if (String(args[0]) !== "/api/v1/goals") return { data: {} };
      goalCalls += 1;
      if (goalCalls === 1) throw new Error("boom");
      return { data: { id: "goal-later", status: "open", checklist: [PLACEHOLDER_ITEM] } };
    });
    mocks.goalDetail = goalFixture({ id: "goal-later", checklist: [PLACEHOLDER_ITEM] });
    renderAssistant(entry);
    await screen.findByLabelText("调整对象");
    await user.click(screen.getByRole("button", { name: PARSE }));
    await user.click(await screen.findByRole("button", { name: "补充条件" }));

    // 登记失败：交接参数还在地址栏里，没有丢。
    await waitFor(() => expect(goalCalls).toBe(1));
    expect(screen.getByTestId("location-probe")).toHaveTextContent(/base=draft-3&lesson=COURSE-9/);

    await user.click(await screen.findByRole("button", { name: "补充条件" }));
    await waitFor(() => expect(goalCalls).toBe(2));
    const [, body] = mocks.post.mock.calls[1] as [string, Record<string, unknown>];
    expect(body.base_schedule_id).toBe("draft-3");
    expect(body.course_business_ids).toEqual(["COURSE-9"]);
    // 服务端确认保存之后才摘掉交接参数；没有发出任何求解请求。
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant\?goal=goal-later$/));
    expect(solveCalls()).toHaveLength(0);
  });

  it("does not register the task while the handed-over lesson is not resolved", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.scheduleDetails["draft-3"] = { ...draftDetail, assignments: [] };
    mocks.stream.mockResolvedValueOnce({
      ...interpretationFixture(),
      unsupported_requirements: ["具体教师的禁排或请假要求"],
      goal_checklist_draft: [{ key: "coverage", kind: "coverage", requirement: "覆盖全部目标课次", params: {} }, PLACEHOLDER_ITEM],
    });
    renderAssistant(entry);
    await screen.findByText(/所选课次不在课表 v3（草稿）里/);
    await user.click(screen.getByRole("button", { name: PARSE }));
    await user.click(await screen.findByRole("button", { name: "补充条件" }));
    expect(mocks.post.mock.calls.some((call) => String(call[0]) === "/api/v1/goals")).toBe(false);
  });

  // 复审 R3 #2：解析回包不能把「待确认的扩大范围」清掉、却把扩大后的范围留下。
  it("rolls a pending lesson-scope expansion back when an in-flight parse returns", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.goalDetail = goalFixture({
      id: "goal-h",
      latest_run_id: "run-x",
      run_count: 1,
      context: { schema_version: 1, scope: { class_business_ids: [], business_lines: [], product_types: [], course_business_ids: ["COURSE-9"], date_from: null, date_to: null, date_window_days: 7 } },
    });
    mocks.runDetails["run-x"] = runFixture({ id: "run-x", goal_id: "goal-h", model_status: "INFEASIBLE", explanation: EXPLAINED });
    let release: () => void = () => undefined;
    mocks.stream.mockImplementation(() => new Promise((resolve) => { release = () => resolve(interpretationFixture()); }));
    renderAssistant("/assistant?goal=goal-h");
    await screen.findByLabelText("调整对象");
    await user.click(await screen.findByRole("button", { name: "修改要求后重新解析" }));
    await user.type(screen.getByLabelText("修改后的要求"), "放宽周三晚");
    await user.click(screen.getByRole("button", { name: "重新解析" }));

    // 模型还在返回时取消已提交过的课次限定：需要单独确认。
    await user.click(screen.getByRole("button", { name: "不限定课次" }));
    expect(await screen.findByText(/课次范围（原来只调整选中的课次）将从限定范围改为「全部」/)).toBeInTheDocument();
    release();

    // 回包按「回滚未确认的扩大」处理：确认提示与范围必须一起恢复，不能出现「范围空了、确认也没了」。
    await waitFor(() => expect(screen.queryByText(/范围将要扩大/)).not.toBeInTheDocument());
    expect(screen.getByLabelText("调整对象")).toHaveTextContent("本任务只针对课表里选中的 1 节课");
    mocks.post.mockResolvedValue({ data: { id: "run-n", status: "queued", goal_id: "goal-h" } });
    await user.click(await screen.findByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(solveCalls()).toHaveLength(1));
    expect((solveCalls()[0] as [string, Record<string, unknown>])[1].course_business_ids).toEqual(["COURSE-9"]);
  });

  it("only promises the work-draft base when the task is tracked", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.scheduleDetails["draft-3"] = draftDetail;
    mocks.stream.mockResolvedValue(interpretationFixture());
    renderAssistant(entry);
    const notice = await screen.findByLabelText("调整对象");
    expect(notice).toHaveTextContent("继续调整以这个任务自己的工作草稿为基准");
    await user.click(screen.getByRole("button", { name: PARSE }));
    await user.click(await screen.findByRole("button", { name: /更多选项/ }));
    await user.click(screen.getByLabelText("以此为目标跟踪"));
    expect(screen.getByLabelText("调整对象")).toHaveTextContent("没有跟踪成任务时，产出草稿后继续调整会以当前已发布版本为基准");
    expect(screen.getByLabelText("调整对象")).not.toHaveTextContent("不会退回当前已发布版本");
  });
});
