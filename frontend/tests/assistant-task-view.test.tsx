import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderAssistant } from "./support/assistant-render";
import {
  assistantMocks as mocks,
  goalFixture,
  mockAiConfigured,
  resetAssistantMocks,
  runFixture,
  scheduleFixture,
} from "./support/assistant-mocks";

vi.mock("@/api/generated/client", async () => (await import("./support/assistant-mocks")).clientMock);
vi.mock("@/api/http", async () => (await import("./support/assistant-mocks")).httpMock);
vi.mock("@/lib/interpret-stream", async () => (await import("./support/assistant-mocks")).interpretStreamMock);

const EXPLAINED = { headline: "已排好", explanation: [], next_actions: [], source: "system" };

function diffItem(id: string, over: Record<string, unknown> = {}) {
  return {
    course_business_id: id,
    class_business_id: `班-${id}`,
    teacher_business_id: "T9",
    before_lesson_date: "2026-09-28",
    before_slot_id: "SLOT-周一-0830-1130",
    before_room_id: "教室-101",
    after_lesson_date: "2026-09-29",
    after_slot_id: "SLOT-周一-0830-1130",
    after_room_id: "教室-102",
    change_kind: "moved",
    ...over,
  };
}

/** run-1 完成并生成了草稿 draft-1（父版本 pub-1 已发布）。 */
function withDraft(draftOver: Record<string, unknown> = {}, runOver: Record<string, unknown> = {}) {
  mocks.schedules = [
    scheduleFixture({ id: "pub-1", status: "published", version_no: 1, name: "已发布课表", parent_id: null, solver_run_id: "run-0" }),
    scheduleFixture({ id: "draft-1", solver_run_id: "run-1", ...draftOver }),
  ];
  mocks.runDetails["run-1"] = runFixture({ id: "run-1", explanation: EXPLAINED, ...runOver });
}

beforeEach(resetAssistantMocks);
afterEach(cleanup);

describe("AssistantPage result card", () => {
  it("summarises the draft as 调整了 N 节课 with change counts and the first-30 detail table open by default", async () => {
    withDraft();
    mocks.diff.mockReturnValue({
      data: {
        changed_count: 2,
        unchanged_count: 5,
        items: [
          diffItem("C1"),
          diffItem("C2", { after_slot_id: "SLOT-周二-0830-1130", change_kind: "moved" }),
          diffItem("C3", { before_lesson_date: "2026-09-30", after_lesson_date: "2026-09-30", after_room_id: "教室-101", change_kind: "unchanged" }),
        ],
      },
    });
    renderAssistant("/assistant?run=run-1");
    expect(await screen.findByRole("heading", { name: "已生成草稿，调整了 2 节课" })).toBeInTheDocument();
    expect(screen.getByText("草稿 · 未发布")).toBeInTheDocument();
    const card = screen.getByLabelText("排课结果");
    // 日期 / 时段 / 教室变化数。
    expect(within(card).getByText("日期变化").nextSibling).toHaveTextContent("2");
    expect(within(card).getByText("时段变化").nextSibling).toHaveTextContent("1");
    expect(within(card).getByText("教室变化").nextSibling).toHaveTextContent("2");
    // 明细默认展开，只列真正变化的课次。
    expect(within(card).getByText("班-C1")).toBeInTheDocument();
    expect(within(card).getByText("班-C2")).toBeInTheDocument();
    expect(within(card).queryByText("班-C3")).not.toBeInTheDocument();
    expect(screen.getByText(/v1「已发布课表」→ v2「9 月排课草稿」/)).toBeInTheDocument();
    // 现在办到哪里：草稿已生成，等待发布。
    expect(within(screen.getByLabelText("办理进展")).getByText("待发布").closest("li")).toHaveAttribute("aria-current", "step");
  });

  it("truncates the change table at 30 rows and points to the full comparison", async () => {
    withDraft();
    mocks.diff.mockReturnValue({ data: { changed_count: 35, unchanged_count: 0, items: Array.from({ length: 35 }, (_, index) => diffItem(`K${index}`)) } });
    renderAssistant("/assistant?run=run-1");
    expect(await screen.findByText("仅展示前 30 条，完整列表请打开版本对比。")).toBeInTheDocument();
    expect(screen.getAllByText(/^班-K/)).toHaveLength(30);
    expect(screen.getByRole("link", { name: "查看完整版本对比" })).toHaveAttribute("href", "/schedule?view=history&version=draft-1");
  });

  it("says so when nothing changed relative to the baseline, and when there is no baseline to compare with", async () => {
    withDraft();
    mocks.diff.mockReturnValue({ data: { changed_count: 0, unchanged_count: 1, items: [diffItem("C3", { change_kind: "unchanged" })] } });
    renderAssistant("/assistant?run=run-1");
    expect(await screen.findByText("本次没有相对基准版本发生变化。")).toBeInTheDocument();
    cleanup();
    withDraft({ parent_id: null });
    mocks.schedules = mocks.schedules.filter((item) => item.id !== "pub-1");
    renderAssistant("/assistant?run=run-1");
    expect(await screen.findByText(/本次已生成 v2 草稿，但还没有可比较的基准版本/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "已生成草稿 v2" })).toBeInTheDocument();
  });

  it("offers view / continue / manual-adjust as separate actions and opens the draft in the schedule page", async () => {
    const user = userEvent.setup();
    withDraft();
    renderAssistant("/assistant?run=run-1");
    await user.click(await screen.findByRole("button", { name: "查看课表草稿" }));
    expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/schedule\?version=draft-1$/);
    cleanup();
    withDraft();
    renderAssistant("/assistant?run=run-1");
    await user.click(await screen.findByRole("button", { name: "在课表里手动调整" }));
    expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/schedule\?view=adjust&version=draft-1$/);
  });

  it("publishes only after an explicit confirmation, and generating a draft never publishes by itself", async () => {
    const user = userEvent.setup();
    withDraft();
    renderAssistant("/assistant?run=run-1");
    const publishButton = await screen.findByRole("button", { name: /审批发布/ });
    expect(mocks.publishMutate).not.toHaveBeenCalled();
    await user.click(publishButton);
    expect(await screen.findByText("发布后成为当前课表，并同步到已启用的外部集成；不会自动下发日历。")).toBeInTheDocument();
    expect(mocks.publishMutate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "确认发布" }));
    expect(mocks.publishMutate).toHaveBeenCalledTimes(1);
    expect(mocks.publishMutate).toHaveBeenCalledWith({ scheduleId: "draft-1" });
  });

  it("lets a scheduler without approval rights see the draft but not publish it", async () => {
    withDraft();
    renderAssistant("/assistant?run=run-1", "scheduler");
    expect(await screen.findByText("草稿已生成，等待有审批权限的人发布。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /审批发布/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看课表草稿" })).toBeInTheDocument();
  });

  it("shows the published state once the draft has been published and offers no second publish", async () => {
    withDraft({ status: "published" });
    renderAssistant("/assistant?run=run-1");
    expect(await screen.findByText("已发布为当前课表")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /审批发布/ })).not.toBeInTheDocument();
    expect(within(screen.getByLabelText("办理进展")).getByText("已发布")).toBeInTheDocument();
    // 草稿已经是当前课表：按钮不再叫「草稿」。
    expect(screen.getByRole("button", { name: "查看课表" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看课表草稿" })).not.toBeInTheDocument();
  });

  it("does not carry calendar dispatch: that lives in the schedule page's share view", async () => {
    withDraft();
    renderAssistant("/assistant?run=run-1");
    await screen.findByLabelText("排课结果");
    expect(screen.queryByText("教师日历下发")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认下发" })).not.toBeInTheDocument();
  });
});

describe("AssistantPage stuck card", () => {
  it("does not inherit an old candidate draft after a completed INFEASIBLE / UNKNOWN run (candidate ownership)", async () => {
    for (const model_status of ["INFEASIBLE", "UNKNOWN"]) {
      cleanup();
      resetAssistantMocks();
      // 旧草稿属于 old-run；当前查看的 new-run 无解/超时：不能把旧候选挂到新任务名下。
      mocks.schedules = [scheduleFixture({ id: "old", status: "draft", solver_run_id: "old-run", version_no: 1, name: "旧候选", parent_id: null })];
      mocks.runDetails["new-run"] = runFixture({ id: "new-run", model_status, explanation: EXPLAINED });
      renderAssistant("/assistant?run=new-run");
      await screen.findByLabelText("排课卡住了");
      expect(screen.queryByText(/本次已生成 v1 草稿/)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "查看课表草稿" })).not.toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: /已生成草稿/ })).not.toBeInTheDocument();
      if (model_status === "UNKNOWN") expect(screen.getByText(/尚未证明无解；请增加时限/)).toBeInTheDocument();
      // 归属旧任务的候选课表不会触发差异对比请求。
      expect(mocks.diff).not.toHaveBeenCalled();
    }
  });

  it("explains an INFEASIBLE run in plain words, lists next steps, and folds the conflict analysis behind 展开详细诊断", async () => {
    const user = userEvent.setup();
    mocks.rules = [{ id: "id-R-1", business_id: "R-1", source_text: "T9 周一上午不排课", actor_type: "teacher", actor_ids: ["T9"], constraint_type: "forbidden_slot", status: "active" }];
    mocks.goalDetail = goalFixture({ latest_run_id: "run-x", run_count: 1 });
    mocks.runDetails["run-x"] = runFixture({
      id: "run-x",
      goal_id: "goal-77",
      model_status: "INFEASIBLE",
      conflict_rule_ids: ["R-1"],
      priority_explanations: ["SYSTEM-TEACHER-NO-OVERLAP 与教师禁排冲突"],
      explanation: {
        headline: "排不开",
        explanation: [],
        next_actions: ["先放宽 T9 周一上午的禁排"],
        source: "ai",
        suggested_instruction: "重排 B1 班，允许 T9 周一上午上课",
      },
    });
    renderAssistant("/assistant?goal=goal-77");
    expect(await screen.findByRole("heading", { name: "在现有的硬性要求下排不出全部课" })).toBeInTheDocument();
    expect(screen.getByText("教师时间不重叠 与教师禁排冲突")).toBeInTheDocument();
    expect(screen.getByText("先放宽 T9 周一上午的禁排")).toBeInTheDocument();
    expect(within(screen.getByLabelText("办理进展")).getByText("没能排出").closest("li")).toBeInTheDocument();
    // 冲突核心分析默认折叠，展开后是涉事规则，可点开对应规则。
    expect(screen.queryByText("冲突核心分析")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "展开详细诊断" }));
    expect(await screen.findByText("冲突核心分析")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看规则" })).toHaveAttribute("href", "/rules?rule=R-1");
    // 建议指令：填入指令框 = 填进「修改后的要求」，不覆盖已有结果。
    await user.click(screen.getByRole("button", { name: "填入指令框" }));
    expect(screen.getByLabelText("修改后的要求")).toHaveValue("重排 B1 班，允许 T9 周一上午上课");
  });

  it("opens the conflict analysis straight away when arriving from a run link (former 无解诊断)", async () => {
    mocks.rules = [{ id: "id-R-1", business_id: "R-1", source_text: "T9 周一上午不排课", actor_type: "teacher", actor_ids: ["T9"], constraint_type: "forbidden_slot", status: "active" }];
    mocks.runDetails["run-x"] = runFixture({ id: "run-x", model_status: "INFEASIBLE", conflict_rule_ids: ["R-1"], explanation: EXPLAINED });
    renderAssistant("/assistant?run=run-x");
    expect(await screen.findByText("冲突核心分析")).toBeInTheDocument();
    expect(screen.getByText("T9 周一上午不排课")).toBeInTheDocument();
  });

  it("keeps the presolve wording: never says proven infeasible, and offers no conflict analysis", async () => {
    mocks.runDetails["run-p"] = runFixture({ id: "run-p", model_status: "INFEASIBLE", presolve_infeasible: true, explanation: EXPLAINED });
    renderAssistant("/assistant?run=run-p");
    expect(await screen.findByText(/不能表述为「已证明无解」/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /详细诊断/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/已确认在当前硬性要求下无法排开/)).not.toBeInTheDocument();
  });

  it("treats UNKNOWN as not-yet-solved and retries with a raised budget in one click", async () => {
    const user = userEvent.setup();
    mocks.goalDetail = goalFixture({ id: "goal-77", latest_run_id: "run-u", run_count: 1 });
    mocks.runDetails["run-u"] = runFixture({ id: "run-u", goal_id: "goal-77", model_status: "UNKNOWN", explanation: EXPLAINED });
    renderAssistant("/assistant?run=run-u");
    expect(await screen.findByRole("heading", { name: "时间用完了，还没找到可用的排法" })).toBeInTheDocument();
    expect(screen.queryByText(/已确认在当前硬性要求下无法排开/)).not.toBeInTheDocument();
    // 任务的范围草稿恢复之前不能重跑，恢复后才可点。
    const raise = screen.getByRole("button", { name: "加大时间预算重跑" });
    await waitFor(() => expect(raise).toBeEnabled());
    await user.click(raise);
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    const submitted = (mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> }).data;
    expect(submitted.time_limit_seconds).toBe(90);
    expect(submitted.goal_id).toBe("goal-77");
  });

  it("does not offer a raised-budget rerun for a run without a task: it names the scope that a rerun would use", async () => {
    const user = userEvent.setup();
    mocks.runDetails["run-u"] = runFixture({ id: "run-u", model_status: "UNKNOWN", explanation: EXPLAINED });
    renderAssistant("/assistant?run=run-u");
    expect(await screen.findByRole("heading", { name: "时间用完了，还没找到可用的排法" })).toBeInTheDocument();
    // 没有任务就不知道原来的范围，绝不能悄悄拿默认的全范围提交。
    expect(screen.queryByRole("button", { name: "加大时间预算重跑" })).not.toBeInTheDocument();
    expect(screen.getByText(/没有关联任务，没法确认它原来的排课范围/)).toBeInTheDocument();
    expect(screen.getByText(/将使用的范围：全部课次；求解时限 30 秒/)).toBeInTheDocument();
    expect(mocks.submitMutate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "按当前范围重新排课" }));
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(1));
    const submitted = (mocks.submitMutate.mock.calls[0][0] as { data: Record<string, unknown> }).data;
    expect(submitted.time_limit_seconds).toBe(30);
    expect(submitted.goal_id).toBeNull();
  });

  it("lets a run started in this session raise the budget even without a task, since the draft holds its scope", async () => {
    const user = userEvent.setup();
    mockAiConfigured(); // AI 已配置时手动面板默认收起，需要点开；未配置时会自动展开
    mocks.submitResult = runFixture({ id: "run-m", model_status: "UNKNOWN", explanation: EXPLAINED });
    mocks.runDetails["run-m"] = runFixture({ id: "run-m", model_status: "UNKNOWN", explanation: EXPLAINED });
    renderAssistant();
    await user.click(await screen.findByRole("button", { name: "手动排课（自己设置参数）" }));
    await user.click(await screen.findByRole("button", { name: /按参数开始求解/ }));
    expect(await screen.findByRole("heading", { name: "时间用完了，还没找到可用的排法" })).toBeInTheDocument();
    // 范围草稿就是这次提交用的那份，不存在「悄悄换成默认全范围」的风险。
    expect(screen.queryByText(/没有关联任务，没法确认它原来的排课范围/)).not.toBeInTheDocument();
    const raise = screen.getByRole("button", { name: "加大时间预算重跑" });
    expect(raise).toBeEnabled();
    await user.click(raise);
    await waitFor(() => expect(mocks.submitMutate).toHaveBeenCalledTimes(2));
    const submitted = (mocks.submitMutate.mock.calls[1][0] as { data: Record<string, unknown> }).data;
    expect(submitted.time_limit_seconds).toBe(90);
    expect(submitted.goal_id).toBeNull();
  });

  it("shows the error message of a failed run", async () => {
    mocks.runDetails["run-f"] = runFixture({ id: "run-f", status: "failed", model_status: null, error_message: "求解服务超时" });
    renderAssistant("/assistant?run=run-f");
    expect(await screen.findByRole("heading", { name: "这次求解没有完成" })).toBeInTheDocument();
    expect(screen.getByText("求解服务超时")).toBeInTheDocument();
  });

  it("requests the result explanation once per run even when the details are opened and closed", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: { headline: "已解读", explanation: [], next_actions: ["缩小范围"], source: "ai" } });
    mocks.runDetails["run-e"] = runFixture({ id: "run-e", model_status: "UNKNOWN" });
    renderAssistant("/assistant?run=run-e");
    expect(await screen.findByText("缩小范围")).toBeInTheDocument();
    const detailsButton = screen.getByRole("button", { name: /查看详情/ });
    await user.click(detailsButton);
    await user.click(detailsButton);
    expect(mocks.post.mock.calls.filter((call) => String(call[0]).endsWith("/explanation"))).toHaveLength(1);
  });

  it("re-asks for the parse from the stuck card with a free-text change, and needs AI for that", async () => {
    const user = userEvent.setup();
    mocks.runDetails["run-x"] = runFixture({ id: "run-x", model_status: "INFEASIBLE", explanation: EXPLAINED });
    renderAssistant("/assistant?run=run-x");
    await user.click(await screen.findByRole("button", { name: "修改要求后重新解析" }));
    expect(screen.getByText(/AI 尚未接入，无法重新解析/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("修改后的要求"), "放宽周三晚");
    expect(screen.getByRole("button", { name: "重新解析" })).toBeDisabled();
  });
});

describe("AssistantPage progress card and acceptance states", () => {
  it("shows a plain solving card while the run is in flight", async () => {
    mocks.runDetails["run-q"] = runFixture({ id: "run-q", status: "running", model_status: null });
    renderAssistant("/assistant?run=run-q");
    expect(await screen.findByRole("heading", { name: "正在排课…" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("办理进展")).getByText("求解中").closest("li")).toHaveAttribute("aria-current", "step");
    // 技术指标不在主卡里。
    expect(screen.queryByText("目标函数值")).not.toBeInTheDocument();
  });

  it("shows 核对中 while the acceptance report is still being written, and never a pass", async () => {
    withDraft({}, { goal_id: "goal-9", goal_report: null });
    mocks.goalDetail = goalFixture({ id: "goal-9", latest_run_id: "run-1", run_count: 1 });
    renderAssistant("/assistant?run=run-1");
    expect(await screen.findByText("核对中…")).toBeInTheDocument();
    expect(screen.queryByText(/要求已落实/)).not.toBeInTheDocument();
  });

  it("shows 核对失败 with the reason when the acceptance run itself failed", async () => {
    withDraft({}, { goal_id: "goal-9", goal_report: { goal_id: "goal-9", items: [], gaps: [], all_passed: false, passed_count: 0, failed_count: 0, acceptance_status: "failed", acceptance_error: "验收器异常" } });
    mocks.goalDetail = goalFixture({ id: "goal-9", latest_run_id: "run-1", run_count: 1 });
    renderAssistant("/assistant?run=run-1");
    expect(await screen.findByText("核对失败")).toBeInTheDocument();
    expect(screen.getByText(/核对失败：验收器异常/)).toBeInTheDocument();
  });

  it("lists gaps with their next step directly, keeps unverifiable apart from passed, and folds the item-by-item report into details", async () => {
    const user = userEvent.setup();
    const report = {
      goal_id: "goal-9",
      instruction: "重排 B1 班三天课",
      all_passed: false,
      passed_count: 2,
      failed_count: 1,
      unverifiable_count: 1,
      items: [
        { key: "coverage", requirement: "覆盖全部目标课次", kind: "coverage", passed: true, detail: "目标课次 3 个，结果命中 3 个" },
        { key: "draft_only", requirement: "只交付草稿", kind: "draft_only", passed: true, detail: "目标期间无发布动作" },
        { key: "forbidden_slot_free-1", requirement: "T9 不占 S1", kind: "forbidden_slot_free", passed: false, detail: "禁排时段仍被占用 1 处：C24" },
        { key: "date_range_match-1", requirement: "日期范围", kind: "date_range_match", passed: false, verdict: "unverifiable", detail: "缺日期，无法验证" },
      ],
      gaps: [{ key: "forbidden_slot_free-1", kind: "forbidden_slot_free", summary: "禁排时段仍被占用", next_step: "等待教务放宽或调整排课", remedy: "await_admin" }],
      decision: { status: "awaiting_decision", reason: "1 项未通过；其中存在必须由教务放宽或裁决的缺口" },
    };
    withDraft({}, { goal_id: "goal-9", goal_report: report });
    mocks.goalDetail = goalFixture({ id: "goal-9", latest_run_id: "run-1", run_count: 1, latest_report: report, runs: [runFixture({ id: "run-1", goal_id: "goal-9", goal_report: report })] });
    renderAssistant("/assistant?run=run-1");
    const summary = await screen.findByLabelText("要求核对");
    expect(within(summary).getByText("还有 1 项要求没落实")).toBeInTheDocument();
    expect(within(summary).getByText("1 项暂时无法验证")).toBeInTheDocument();
    // await_admin 是人的裁决：只有文字，没有按钮。
    expect(within(summary).getByText("等待教务放宽或调整排课")).toBeInTheDocument();
    // 需要教务裁决的结论完整展示，不截断。
    expect(within(summary).getByText(/1 项未通过；其中存在必须由教务放宽或裁决的缺口/)).toBeInTheDocument();
    expect(within(summary).queryByRole("button")).not.toBeInTheDocument();
    // 逐项报告全文在「查看详情」里：✓ / ✗ / ? 各自标注，无法验证不算通过。
    expect(screen.queryByText(/禁排时段仍被占用 1 处/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /查看详情/ }));
    expect(await screen.findByText("最新验收")).toBeInTheDocument();
    expect(screen.getByText(/禁排时段仍被占用 1 处：C24/)).toBeInTheDocument();
    expect(screen.getAllByText("✓")).toHaveLength(2);
    expect(screen.getAllByText("✗")).toHaveLength(1);
    expect(screen.getAllByText("?")).toHaveLength(1);
    expect(screen.getByText("无法验证")).toBeInTheDocument();
  });

  it("renders an all-passed report as fulfilled without the gap block", async () => {
    withDraft({}, {
      goal_id: "goal-8",
      goal_report: { goal_id: "goal-8", all_passed: true, passed_count: 2, failed_count: 0, items: [], gaps: [], decision: { status: "achieved", reason: "全部验收项通过" } },
    });
    mocks.goalDetail = goalFixture({ id: "goal-8", latest_run_id: "run-1", run_count: 1 });
    renderAssistant("/assistant?run=run-1");
    expect(await screen.findByText("全部 2 项要求已落实")).toBeInTheDocument();
    expect(screen.queryByText("建议的下一步")).not.toBeInTheDocument();
  });

  // 审查 #2：v1 已通过 → 修订为 v2 → 还没重新验收，回来打开旧结果，不能看到绿色「全部已落实」。
  it("shows a v1 all-passed report as history once the task was revised to v2 and is awaiting acceptance", async () => {
    withDraft({}, {
      goal_id: "goal-8",
      goal_report: { goal_id: "goal-8", all_passed: true, passed_count: 2, failed_count: 0, items: [], gaps: [], decision: { status: "achieved", reason: "全部验收项通过" }, meta: { checklist_version: 1 } },
    });
    mocks.goalDetail = goalFixture({ id: "goal-8", latest_run_id: "run-1", run_count: 1, checklist_version: 2, acceptance_status: "pending", status: "open" });
    renderAssistant("/assistant?run=run-1");
    const summary = await screen.findByLabelText("要求核对");
    expect(within(summary).getByText("等待新验收（v2）")).toBeInTheDocument();
    expect(within(summary).getByText(/历史 v1 已通过；当前 v2 尚待核对/)).toBeInTheDocument();
    expect(screen.queryByText(/要求已落实/)).not.toBeInTheDocument();
  });

  it("fixes a pending checklist placeholder in place from the 补充条件 remedy", async () => {
    const user = userEvent.setup();
    const placeholder = {
      key: "forbidden_slot_free-draft",
      kind: "forbidden_slot_free",
      requirement: "禁排要求待量化",
      params: { subject_type: "teacher", subject_ids: [], slot_business_ids: [], needs_params: true },
    };
    withDraft({}, {
      goal_id: "goal-9",
      goal_report: {
        goal_id: "goal-9",
        all_passed: false,
        passed_count: 0,
        failed_count: 1,
        items: [{ key: "forbidden_slot_free-draft", kind: "forbidden_slot_free", requirement: "禁排", passed: false, detail: "参数缺失" }],
        gaps: [{ key: "forbidden_slot_free-draft", kind: "forbidden_slot_free", summary: "禁排参数缺失", next_step: "在清单中补齐参数后重跑", remedy: "fix_checklist" }],
        decision: null,
      },
    });
    mocks.goalDetail = goalFixture({ id: "goal-9", latest_run_id: "run-1", run_count: 1, checklist: [placeholder] });
    renderAssistant("/assistant?run=run-1");
    await user.click(await screen.findByRole("button", { name: "补充条件" }));
    await user.selectOptions(await screen.findByLabelText("禁排主体类型"), "teacher");
    await user.selectOptions(screen.getByLabelText("禁排主体"), "T9");
    await user.selectOptions(screen.getByLabelText("添加禁排时段"), "S1");
    await user.click(screen.getByRole("button", { name: "保存参数" }));
    await waitFor(() => expect(mocks.checklistPatch).toHaveBeenCalledTimes(1));
    const call = mocks.checklistPatch.mock.calls[0][0] as { goalId: string; data: { checklist: Array<Record<string, unknown>> } };
    expect(call.goalId).toBe("goal-9");
    expect(call.data.checklist[0]).toMatchObject({ key: "forbidden_slot_free-draft", params: { subject_ids: ["T9"], slot_business_ids: ["S1"] } });
  });
});

describe("AssistantPage 本次要求 panel", () => {
  const memoryUsage = {
    status: "ok",
    summary: { considered: 2, applied: 1, unused: 1 },
    outcomes: [
      { entry_id: "e1", subject_type: "teacher", subject_id: "T9", predicate: "avoid_slot", outcome: "applied", detail: "以权重 30 参与求解" },
      { entry_id: "e2", subject_type: "teacher", subject_id: "T8", predicate: "prefer_slot", outcome: "not_authorized", detail: "待确认候选未经采纳或授权试用，不进入求解输入" },
    ],
  };

  it("lists the preferences that took part with a 查看/修改 link each, and shows the ones that did not right away", async () => {
    mocks.runDetails["run-mem"] = runFixture({ id: "run-mem", model_status: "UNKNOWN", memory_usage: memoryUsage, explanation: EXPLAINED });
    renderAssistant("/assistant?run=run-mem");
    const panel = await screen.findByLabelText("本次要求");
    expect(within(panel).getByText("参考的常用偏好")).toBeInTheDocument();
    expect(within(panel).getByText("教师 T9 · 避开时段")).toBeInTheDocument();
    const links = within(panel).getAllByRole("link", { name: "查看/修改" });
    expect(links.map((link) => link.getAttribute("href"))).toEqual(["/memory?entry=e1", "/memory?entry=e2"]);
    // 未采用的偏好不折叠：标出原因。
    expect(within(panel).getByText("这些偏好本次没有采用")).toBeInTheDocument();
    expect(within(panel).getByText(/教师 T8 · 偏好时段：未授权试用——待确认候选未经采纳或授权试用/)).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "管理常用要求" })).toHaveAttribute("href", "/memory");
    expect(within(panel).getByRole("link", { name: "学校通用规则" })).toHaveAttribute("href", "/rules");
  });

  it("keeps the full compile-basis memory summary under 查看详情", async () => {
    const user = userEvent.setup();
    mocks.runDetails["run-mem"] = runFixture({ id: "run-mem", model_status: "UNKNOWN", memory_usage: memoryUsage, explanation: EXPLAINED });
    renderAssistant("/assistant?run=run-mem");
    await screen.findByLabelText("本次要求");
    expect(screen.queryByText("偏好记忆（创建时编译口径）")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /查看详情/ }));
    expect(await screen.findByText("偏好记忆（创建时编译口径）")).toBeInTheDocument();
    expect(screen.getByText(/创建任务时 2 条偏好记忆获准编译（编译进求解输入 1 \/ 未编译 1）/)).toBeInTheDocument();
    // 未采用原因在「本次要求」面板与详情的编译口径里各出现一次。
    expect(screen.getAllByText(/待确认候选未经采纳或授权试用，不进入求解输入/)).toHaveLength(2);
  });

  it("states explicitly, and visibly, that preferences were not used when compilation failed", async () => {
    mocks.runDetails["run-fail"] = runFixture({ id: "run-fail", model_status: "UNKNOWN", memory_usage: { status: "compile_failed", detail: "模拟编译崩溃" }, explanation: EXPLAINED });
    renderAssistant("/assistant?run=run-fail");
    expect(await screen.findByText(/本次没能参考常用偏好：编译失败（模拟编译崩溃）/)).toBeInTheDocument();
    expect(screen.getByLabelText("本次要求")).toHaveTextContent("未参考任何常用偏好");
  });
});

describe("AssistantPage run records and deep links", () => {
  it("opens the result and diagnosis of any run from the records list (?run=)", async () => {
    const user = userEvent.setup();
    mocks.runs = [
      runFixture({ id: "run-aaaaaaaa1", model_status: "INFEASIBLE" }),
      runFixture({ id: "run-bbbbbbbb2", model_status: "OPTIMAL" }),
    ];
    mocks.runDetails["run-aaaaaaaa1"] = runFixture({ id: "run-aaaaaaaa1", model_status: "INFEASIBLE", explanation: EXPLAINED });
    renderAssistant();
    await user.click(await screen.findByRole("button", { name: /求解记录/ }));
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(3);
    await user.click(within(rows[1]).getByText("run-aaaa"));
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant\?run=run-aaaaaaaa1$/));
    expect(await screen.findByRole("heading", { name: "在现有的硬性要求下排不出全部课" })).toBeInTheDocument();
  });

  it("binds the goal behind a run link so remedies can be fed back to the same task", async () => {
    mocks.goalDetail = goalFixture({ latest_run_id: "run-g", run_count: 1 });
    mocks.runDetails["run-g"] = runFixture({ id: "run-g", goal_id: "goal-77", model_status: "UNKNOWN", explanation: EXPLAINED });
    renderAssistant("/assistant?run=run-g");
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/goal=goal-77/));
    expect(screen.getByTestId("location-probe")).toHaveTextContent(/run=run-g/);
    expect(await screen.findByText(/已接着办这个任务/)).toBeInTheDocument();
    expect(within(screen.getByLabelText("任务进展")).getByText("重排 B01 班一周课表，避开周三晚间")).toBeInTheDocument();
  });

  it("says a run link with no tracked request is a manual solve", async () => {
    mocks.runDetails["run-m"] = runFixture({ id: "run-m", model_status: "UNKNOWN", explanation: EXPLAINED });
    renderAssistant("/assistant?run=run-m");
    expect(await screen.findByText(/手动排课：按你设置的参数直接求解/)).toBeInTheDocument();
  });

  it("shows technical metrics only under 查看详情", async () => {
    const user = userEvent.setup();
    mocks.runDetails["run-m"] = runFixture({ id: "run-m", model_status: "UNKNOWN", explanation: EXPLAINED, wall_time_seconds: 3.5 });
    renderAssistant("/assistant?run=run-m");
    await screen.findByLabelText("排课卡住了");
    expect(screen.queryByText("最佳界")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /查看详情/ }));
    expect(await screen.findByText("最佳界")).toBeInTheDocument();
    expect(screen.getByText("3.50 秒")).toBeInTheDocument();
    expect(screen.getByText("超时未定")).toBeInTheDocument();
  });
});

describe("AssistantPage home task list", () => {
  const updated = (day: number) => `2026-09-${String(day).padStart(2, "0")}T09:00:00+08:00`;

  it("shows at most three unfinished tasks, newest first, each with a one-line progress and a continue button", async () => {
    const user = userEvent.setup();
    mocks.goals = [
      goalFixture({ id: "g1", instruction: "任务一", updated_at: updated(1), latest_run_id: null, run_count: 0 }),
      goalFixture({ id: "g2", instruction: "任务二", updated_at: updated(2), latest_run_id: "r2", run_count: 1 }),
      goalFixture({ id: "g3", instruction: "任务三", updated_at: updated(3), status: "awaiting_decision", latest_run_id: "r3", run_count: 1 }),
      goalFixture({ id: "g4", instruction: "任务四", updated_at: updated(4), latest_run_id: "r4", run_count: 1 }),
      goalFixture({ id: "g5", instruction: "已达成的任务", status: "achieved", updated_at: updated(5), run_count: 1 }),
    ];
    mocks.runs = [
      runFixture({ id: "r2", status: "running", model_status: null }),
      runFixture({ id: "r3", model_status: "INFEASIBLE" }),
      runFixture({ id: "r4", goal_report: { all_passed: false, passed_count: 1, failed_count: 1, items: [], gaps: [] } }),
    ];
    renderAssistant();
    const section = await screen.findByLabelText("正在处理的任务");
    const items = within(section).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("任务四");
    expect(items[0]).toHaveTextContent("已生成草稿，还有 1 项要求需要确认");
    expect(items[1]).toHaveTextContent("任务三");
    expect(items[1]).toHaveTextContent("没能排出来，需要你调整范围");
    expect(items[2]).toHaveTextContent("任务二");
    expect(items[2]).toHaveTextContent("求解中");
    expect(within(section).queryByText("已达成的任务")).not.toBeInTheDocument();
    await user.click(within(items[1]).getByRole("button", { name: "继续处理" }));
    expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant\?goal=g3$/);
  });

  it("expands to every task including finished ones, and keeps 不再跟进 behind a confirmation", async () => {
    const user = userEvent.setup();
    mocks.goals = [
      goalFixture({ id: "g1", instruction: "进行中的任务", updated_at: "2026-09-03T09:00:00+08:00" }),
      goalFixture({ id: "g2", instruction: "已达成的任务", status: "achieved", updated_at: "2026-09-02T09:00:00+08:00", run_count: 1 }),
      goalFixture({ id: "g3", instruction: "已放弃的任务", status: "abandoned", updated_at: "2026-09-01T09:00:00+08:00" }),
    ];
    renderAssistant();
    const section = await screen.findByLabelText("正在处理的任务");
    expect(within(section).getAllByRole("listitem")).toHaveLength(1);
    await user.click(within(section).getByRole("button", { name: "查看全部任务（3）" }));
    expect(within(section).getAllByRole("listitem")).toHaveLength(3);
    expect(within(section).getByText("已达成")).toBeInTheDocument();
    expect(within(section).getByText("已放弃")).toBeInTheDocument();
    // 已放弃的没有「不再跟进」按钮，其余都有。
    const abandonButtons = within(section).getAllByRole("button", { name: "不再跟进" });
    expect(abandonButtons).toHaveLength(2);
    await user.click(abandonButtons[0]);
    expect(await screen.findByText("不再跟进这个任务？")).toBeInTheDocument();
    expect(mocks.abandonMutate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "确认不再跟进" }));
    await waitFor(() => expect(mocks.abandonMutate).toHaveBeenCalledWith({ goalId: "g1" }));
  });

  it("gives a one-line hint when there are no tasks yet", async () => {
    renderAssistant();
    expect(await screen.findByText(/还没有任务/)).toBeInTheDocument();
  });
});

describe("AssistantPage resumed task without a result", () => {
  const placeholder = {
    key: "forbidden_slot_free-draft",
    kind: "forbidden_slot_free",
    requirement: "禁排要求待量化：补充主体与具体时段后才能独立复核",
    params: { subject_type: "teacher", subject_ids: [], slot_business_ids: [], needs_params: true },
  };

  it("lands on 接着办 with the missing condition offered in place (formerly 目标跟踪 › 补齐禁排参数)", async () => {
    const user = userEvent.setup();
    mocks.goalDetail = goalFixture({ id: "goal-later", checklist: [placeholder] });
    renderAssistant("/assistant?goal=goal-later");
    expect(await screen.findByRole("heading", { name: "接着办这个任务" })).toBeInTheDocument();
    expect(screen.getByText(/还差一个条件/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "补齐参数" }));
    await user.selectOptions(screen.getByLabelText("禁排主体类型"), "teacher");
    await user.selectOptions(screen.getByLabelText("禁排主体"), "T9");
    await user.selectOptions(screen.getByLabelText("添加禁排时段"), "S1");
    await user.click(screen.getByRole("button", { name: "保存参数" }));
    await waitFor(() => expect(mocks.checklistPatch).toHaveBeenCalledTimes(1));
    const call = mocks.checklistPatch.mock.calls[0][0] as { goalId: string; data: { checklist: Array<Record<string, unknown>> } };
    expect(call.goalId).toBe("goal-later");
    // PATCH body 是完整清单（量化后的禁排项替换占位项）。
    expect(call.data.checklist).toHaveLength(1);
    expect(call.data.checklist[0]).toMatchObject({ key: "forbidden_slot_free-draft", kind: "forbidden_slot_free", params: { subject_type: "teacher", subject_ids: ["T9"], slot_business_ids: ["S1"] } });
    expect(call.data.checklist[0].params).not.toHaveProperty("needs_params", true);
  });

  it("offers the abandon-goal action from the task bar with the same confirmation", async () => {
    const user = userEvent.setup();
    mocks.goalDetail = goalFixture({ id: "goal-77" });
    renderAssistant("/assistant?goal=goal-77");
    await user.click(await screen.findByRole("button", { name: "不再跟进这个任务" }));
    expect(await screen.findByText("不再跟进这个任务？")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认不再跟进" }));
    await waitFor(() => expect(mocks.abandonMutate).toHaveBeenCalledWith({ goalId: "goal-77" }));
    // 放弃的正是本页绑定的任务：解除绑定并回首页，不留下一个已结束任务的续办页。
    expect(await screen.findByLabelText("排课需求")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/assistant$/));
  });

  it("keeps the checklist with its version history and the goal's run list under 查看详情", async () => {
    const user = userEvent.setup();
    mocks.goalDetail = goalFixture({
      id: "goal-77",
      checklist_version: 3,
      checklist: [{ key: "coverage", requirement: "覆盖目标课次", kind: "coverage", params: { bottom_line: true } }],
      checklist_history: [
        { version: 1, saved_at: "2026-09-20T08:00:00+08:00", saved_by: null, items: [{ key: "a", kind: "draft_only", requirement: "只交付草稿" }] },
        { version: 2, saved_at: "2026-09-21T08:00:00+08:00", saved_by: null, items: [{ key: "a", kind: "draft_only", requirement: "改口径：仍只出草稿" }] },
      ],
      runs: [runFixture({ id: "run-old", goal_id: "goal-77" })],
    });
    renderAssistant("/assistant?goal=goal-77");
    await screen.findByRole("heading", { name: "接着办这个任务" });
    expect(screen.queryByText(/验收清单 v3/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /查看详情/ }));
    expect(await screen.findByText(/验收清单 v3/)).toBeInTheDocument();
    expect(screen.getByText("底线")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /历史版本（2）/ }));
    expect(await screen.findByText(/v2 · 保存于/)).toBeInTheDocument();
    expect(screen.getByText(/改口径：仍只出草稿/)).toBeInTheDocument();
    expect(screen.getByText("求解记录（1）")).toBeInTheDocument();
  });
});
