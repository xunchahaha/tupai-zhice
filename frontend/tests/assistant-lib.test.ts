import { describe, expect, it } from "vitest";

import {
  buildRefineInstruction,
  buildRequirementItems,
  buildTaskSteps,
  describeScope,
  goalScopeConflicts,
  hasQuantizablePlaceholder,
  plainInterpretSummary,
  truncateText,
} from "@/lib/assistant-task";
import { goalNeedsParams, goalProgress, isActiveGoal, isClosedGoal, needsParamsItems, parseGoalReport, reportStanding, requirementsNote } from "@/lib/goal";
import { type Interpretation } from "@/lib/interpret-stream";
import { memoryEntryPath, memoryOutcomeDescription, splitMemoryOutcomes } from "@/lib/memory-usage";
import { classifyRun } from "@/lib/run-kind";
import { withSystemRules } from "@/lib/solver-params";

describe("classifyRun", () => {
  it("maps a run to the card that should explain it", () => {
    expect(classifyRun(null)).toBeNull();
    expect(classifyRun({ status: "queued" })).toBe("solving");
    expect(classifyRun({ status: "running" })).toBe("solving");
    expect(classifyRun({ status: "failed" })).toBe("failed");
    expect(classifyRun({ status: "completed", model_status: "OPTIMAL" })).toBe("result");
    expect(classifyRun({ status: "completed", model_status: "FEASIBLE" })).toBe("result");
    expect(classifyRun({ status: "completed", model_status: "INFEASIBLE" })).toBe("stuck");
    expect(classifyRun({ status: "completed", model_status: "UNKNOWN" })).toBe("stuck");
    expect(classifyRun({ status: "completed", model_status: null })).toBe("stuck");
  });

  it("never treats a presolve verdict as a draft, even when the model status reads OPTIMAL (empty scope)", () => {
    expect(classifyRun({ status: "completed", model_status: "OPTIMAL", presolve_infeasible: true })).toBe("stuck");
    expect(classifyRun({ status: "completed", model_status: "INFEASIBLE", presolve_infeasible: true })).toBe("stuck");
  });
});

describe("goalProgress (one-line business progress)", () => {
  const okRun = { status: "completed", model_status: "OPTIMAL" };
  const placeholder = { key: "p", kind: "forbidden_slot_free", params: { needs_params: true } };
  const report = (over: Record<string, unknown> = {}) => ({
    all_passed: false,
    passed_count: 1,
    failed_count: 1,
    items: [],
    gaps: [],
    ...over,
  });

  it("recognises unfinished tasks only", () => {
    expect(isActiveGoal("open")).toBe(true);
    expect(isActiveGoal("awaiting_decision")).toBe(true);
    expect(isActiveGoal("achieved")).toBe(false);
    expect(isActiveGoal("abandoned")).toBe(false);
    // 只有人工放弃后端才拒绝新求解；已达成仍可继续调整。
    expect(isClosedGoal("abandoned")).toBe(true);
    expect(isClosedGoal("achieved")).toBe(false);
    expect(isClosedGoal("open")).toBe(false);
    expect(isClosedGoal(undefined)).toBe(false);
  });

  it("says abandoned goals are no longer followed", () => {
    expect(goalProgress({ status: "abandoned", run_count: 2 }, okRun).text).toBe("已放弃，不再跟进");
  });

  it("before any solve: asks for the missing condition when a placeholder is pending, otherwise says nothing started", () => {
    expect(goalProgress({ status: "open", run_count: 0, checklist: [placeholder] })).toMatchObject({ text: "还差一个条件，补充后才能开始排课", tone: "yellow" });
    expect(goalProgress({ status: "open", run_count: 0, checklist: [] })).toMatchObject({ text: "已登记，还没有开始排课", tone: "blue" });
  });

  it("reports solving, failed and the three stuck flavours in plain language", () => {
    expect(goalProgress({ status: "open", run_count: 1 }, { status: "running" }).text).toBe("求解中，完成后会自动核对要求");
    expect(goalProgress({ status: "open", run_count: 1 }, { status: "failed" }).text).toBe("求解没有完成，可以重新处理");
    expect(goalProgress({ status: "open", run_count: 1 }, { status: "completed", model_status: "INFEASIBLE" }).text).toBe("没能排出来，需要你调整范围");
    expect(goalProgress({ status: "open", run_count: 1 }, { status: "completed", model_status: "UNKNOWN" }).text).toBe("时间用完还没排出来，可以加大时间预算再试");
    expect(goalProgress({ status: "open", run_count: 1 }, { status: "completed", model_status: "INFEASIBLE", presolve_infeasible: true }).text).toBe("求解前的数据检查没通过，需要先修正输入");
  });

  it("derives the draft sentence from the latest acceptance report", () => {
    expect(goalProgress({ status: "awaiting_decision", run_count: 1 }, { ...okRun, goal_report: report({ failed_count: 1 }) })).toMatchObject({
      text: "已生成草稿，还有 1 项要求需要确认",
      tone: "yellow",
    });
    expect(goalProgress({ status: "open", run_count: 1 }, { ...okRun, goal_report: report({ all_passed: true, failed_count: 0 }) })).toMatchObject({
      text: "已生成草稿，全部要求已落实",
      tone: "green",
    });
    // 报告还没落库：验收在异步事务里，不能先宣布通过。
    expect(goalProgress({ status: "open", run_count: 1, acceptance_status: "pending" }, okRun).text).toBe("已生成草稿，正在核对要求");
    expect(goalProgress({ status: "open", run_count: 1, acceptance_status: "failed" }, okRun).text).toBe("已生成草稿，但要求核对没有完成，可以重新核对");
    expect(goalProgress({ status: "open", run_count: 1 }, { ...okRun, goal_report: report({ acceptance_status: "failed" }) }).text).toBe(
      "已生成草稿，但要求核对没有完成，可以重新核对",
    );
  });

  it("counts unverifiable-only reports as still needing confirmation, never as done", () => {
    const result = goalProgress({ status: "open", run_count: 1 }, { ...okRun, goal_report: report({ failed_count: 0, unverifiable_count: 2 }) });
    expect(result.text).toBe("已生成草稿，还有 2 项要求需要确认");
  });

  it("falls back to a neutral sentence when the latest run is not in the list", () => {
    expect(goalProgress({ status: "open", run_count: 3 }).text).toBe("已有排课记录，继续处理可查看最新进展");
    expect(goalProgress({ status: "achieved", run_count: 3 }).text).toBe("已生成草稿，全部要求已落实");
  });
});

describe("report currency: an older checklist version never passes for the current one (review #2/#3)", () => {
  const okRun = { status: "completed", model_status: "OPTIMAL" };
  const passed = (version: number) => ({ goal_id: "g1", all_passed: true, passed_count: 3, failed_count: 0, items: [], gaps: [], meta: { checklist_version: version } });

  it("goalProgress: v1 all-passed report on a v2 goal awaiting acceptance is history, not 全部要求已落实", () => {
    const result = goalProgress({ id: "g1", status: "open", checklist_version: 2, acceptance_status: "pending", run_count: 1 }, { ...okRun, goal_report: passed(1) });
    expect(result).toMatchObject({ tone: "blue" });
    expect(result.text).toContain("历史 v1 已通过；当前 v2 尚待核对");
    expect(result.text).not.toContain("全部要求已落实");
  });

  it("goalProgress: the same report still counts once the goal is at that version", () => {
    expect(goalProgress({ id: "g1", status: "achieved", checklist_version: 1, acceptance_status: "completed", run_count: 1 }, { ...okRun, goal_report: passed(1) }).text).toBe("已生成草稿，全部要求已落实");
  });

  it("goalProgress: an achieved-looking goal whose acceptance is pending is not reported as done without a run", () => {
    expect(goalProgress({ status: "achieved", acceptance_status: "pending", run_count: 2 }).text).toBe("已有排课记录，继续处理可查看最新进展");
  });

  it("reads the version of a failure fallback report from its top level and treats it as version 1 when absent", () => {
    expect(reportStanding(parseGoalReport({ items: [], acceptance_status: "failed", checklist_version: 1 })!, { checklist_version: 2 })).toBe("historical");
    expect(reportStanding(parseGoalReport({ items: [] })!, { checklist_version: 1 })).toBe("current");
    expect(reportStanding(parseGoalReport({ items: [] })!, undefined)).toBe("unknown");
    expect(reportStanding(parseGoalReport({ items: [], goal_id: "other" })!, { id: "g1", checklist_version: 1 })).toBe("historical");
  });

  it("requirementsNote: a failed report with zero counts is a failure, never 全部 0 项", () => {
    const failed = parseGoalReport({ goal_id: "g1", acceptance_status: "failed", acceptance_error: "boom", all_passed: false, items: [], gaps: [] })!;
    expect(requirementsNote(failed, { id: "g1", checklist_version: 1 })).toBe("它关联的求解要求核对失败（boom），尚不能确认要求已落实。");
  });

  it("requirementsNote: only a current, explicitly all-passed report is called fulfilled", () => {
    const ok = parseGoalReport(passed(2))!;
    expect(requirementsNote(ok, { id: "g1", checklist_version: 2 })).toBe("它关联的求解已落实全部 3 项要求。");
    expect(requirementsNote(ok, { id: "g1", checklist_version: 3 })).toContain("历史 v2 已通过；当前 v3 尚待核对");
    expect(requirementsNote(ok, undefined)).toContain("无法确认");
    // 没有失败项也没有 all_passed（例如空清单）：不能凭「0 项未通过」说成功。
    const empty = parseGoalReport({ goal_id: "g1", all_passed: false, passed_count: 0, failed_count: 0, items: [], gaps: [] })!;
    expect(requirementsNote(empty, { id: "g1", checklist_version: 1 })).toBe("它关联的求解要求落实情况尚未确认。");
  });
});

describe("buildTaskSteps", () => {
  const states = (input: Parameters<typeof buildTaskSteps>[0]) => buildTaskSteps(input).map((step) => `${step.label}:${step.state}`);

  it("highlights the first unfinished step as the next thing to do", () => {
    expect(states({ understood: false, confirmed: false, runKind: null })).toEqual([
      "已理解:current", "已确认:todo", "求解中:todo", "草稿已生成:todo", "待发布:todo",
    ]);
    expect(states({ understood: true, confirmed: false, runKind: null })).toEqual([
      "已理解:done", "已确认:current", "求解中:todo", "草稿已生成:todo", "待发布:todo",
    ]);
  });

  it("shows solving as current and treats an existing run as understood and confirmed", () => {
    expect(states({ understood: false, confirmed: false, runKind: "solving" })).toEqual([
      "已理解:done", "已确认:done", "求解中:current", "草稿已生成:todo", "待发布:todo",
    ]);
  });

  it("waits on 待发布 after a draft exists, and flips to 已发布 once published", () => {
    expect(states({ understood: true, confirmed: true, runKind: "result", draftStatus: "draft" })).toEqual([
      "已理解:done", "已确认:done", "求解中:done", "草稿已生成:done", "待发布:current",
    ]);
    expect(states({ understood: true, confirmed: true, runKind: "result", draftStatus: "published" })).toEqual([
      "已理解:done", "已确认:done", "求解中:done", "草稿已生成:done", "已发布:done",
    ]);
  });

  it("marks the solving step as blocked when nothing could be scheduled", () => {
    for (const runKind of ["stuck", "failed"] as const) {
      expect(states({ understood: true, confirmed: true, runKind })).toEqual([
        "已理解:done", "已确认:done", "没能排出:blocked", "草稿已生成:todo", "待发布:todo",
      ]);
    }
  });
});

describe("requirement summary helpers", () => {
  const scope = { business_lines: ["考研"], product_types: [], class_business_ids: ["A班"], date_from: "2026-09-28", date_to: null };

  it("describes only the scope fields that are actually limited", () => {
    expect(describeScope(scope)).toEqual(["业务线 考研", "班级 A班", "日期 2026-09-28 至 不限"]);
    expect(describeScope({ business_lines: [], product_types: [], class_business_ids: [], date_from: null, date_to: null })).toEqual([]);
  });

  it("builds the one-line 本次要求 from scope + task constraints + draft-only", () => {
    const items = buildRequirementItems({
      scope,
      constraints: [
        { id: "1", source_text: "张老师周三晚不能上", subject_type: "teacher", subject_ids: [], slot_business_ids: [], hardness: "hard" },
        { id: "2", source_text: "李老师周三晚别排", subject_type: "teacher", subject_ids: [], slot_business_ids: [], hardness: "soft" },
      ],
    });
    expect(items).toEqual([
      "只调整业务线 考研，班级 A班，日期 2026-09-28 至 不限",
      "张老师周三晚不能上",
      "李老师周三晚别排（尽量）",
      "先出草稿，不会自动发布",
    ]);
    expect(buildRequirementItems({ scope: { ...scope, business_lines: [], class_business_ids: [], date_from: null }, constraints: [] })[0]).toBe("调整范围：全部课次");
  });

  it("truncates long requirement text", () => {
    expect(truncateText("abcdef", 4)).toBe("abcd…");
    expect(truncateText("abc", 4)).toBe("abc");
  });
});

describe("goalScopeConflicts (goal continuity notice)", () => {
  const goal = {
    checklist: [{ key: "coverage", kind: "coverage", requirement: "覆盖", params: { business_lines: ["考研"], class_business_ids: ["B1"], date_from: "2026-09-28T00:00:00", date_to: "2026-10-04" } }],
  } as never;
  const parsed = (over: Record<string, unknown> = {}) =>
    ({ business_lines: ["考研"], product_types: [], class_business_ids: ["B1"], date_from: "2026-09-28", date_to: "2026-10-04", ...over }) as unknown as Interpretation;

  it("is silent when the parse matches the goal scope", () => {
    expect(goalScopeConflicts(goal, parsed())).toEqual([]);
  });

  it("names every field that drifted", () => {
    expect(goalScopeConflicts(goal, parsed({ business_lines: ["高考"], class_business_ids: [], date_to: "2026-10-05" }))).toEqual(["业务线", "班级范围", "日期范围"]);
  });
});

describe("hasQuantizablePlaceholder / withSystemRules", () => {
  it("only counts forbidden-slot placeholders that still need params", () => {
    const item = (kind: string, needs: boolean) => ({ key: kind, kind, requirement: "", params: { needs_params: needs } });
    expect(hasQuantizablePlaceholder({ goal_checklist_draft: [item("forbidden_slot_free", true)] } as unknown as Interpretation)).toBe(true);
    expect(hasQuantizablePlaceholder({ goal_checklist_draft: [item("forbidden_slot_free", false), item("coverage", true)] } as unknown as Interpretation)).toBe(false);
    expect(hasQuantizablePlaceholder({} as Interpretation)).toBe(false);
  });

  it("always includes the two system hard rules exactly once", () => {
    expect(withSystemRules(["fixed_time", "room_no_overlap"]).sort()).toEqual(["fixed_time", "room_no_overlap", "teacher_no_overlap"]);
  });
});

describe("memory usage helpers", () => {
  it("describes an entry in business words and links to it on the memory page", () => {
    expect(memoryOutcomeDescription({ entry_id: "e1", subject_type: "teacher", subject_id: "T9", predicate: "avoid_slot", outcome: "applied" })).toBe("教师 T9 · 避开时段");
    expect(memoryOutcomeDescription({ entry_id: "e2", outcome: "applied" })).toBe("一条常用偏好");
    expect(memoryEntryPath("pe 1")).toBe("/memory?entry=pe+1");
  });

  it("splits applied from not-applied outcomes and flags compile failure", () => {
    const split = splitMemoryOutcomes({
      status: "ok",
      outcomes: [
        { entry_id: "e1", outcome: "applied" },
        { entry_id: "e2", outcome: "not_authorized" },
      ],
    });
    expect(split.applied.map((item) => item.entry_id)).toEqual(["e1"]);
    expect(split.notApplied.map((item) => item.entry_id)).toEqual(["e2"]);
    expect(split.compileFailed).toBe(false);
    expect(splitMemoryOutcomes({ status: "compile_failed" }).compileFailed).toBe(true);
    expect(splitMemoryOutcomes(null)).toEqual({ compileFailed: false, applied: [], notApplied: [] });
  });
});

describe("plainInterpretSummary (确认卡里不出现技术黑话，也不出现「。；」)", () => {
  it("turns the backend's CP-SAT closing sentence into plain words and drops the trailing punctuation", () => {
    const plain = plainInterpretSummary("已解析为考研业务线 8 月 17 日至 19 日的重排，请教务确认后启动 CP-SAT 求解。");
    expect(plain).toBe("已解析为考研业务线 8 月 17 日至 19 日的重排，请教务确认后开始排课");
    expect(plain).not.toMatch(/CP-SAT/i);
    // 卡片会在后面接「；解析来源」，摘要自己不能以句号收尾。
    expect(`${plain}；解析来源：通用 AI 模型。`).not.toContain("。；");
  });

  it("replaces any other mention of the solver and leaves ordinary summaries alone", () => {
    expect(plainInterpretSummary("将用 CP-SAT 求解器重排；")).toBe("将用 排课重排");
    expect(plainInterpretSummary("已解析排课范围")).toBe("已解析排课范围");
    expect(plainInterpretSummary("")).toBe("");
  });
});

describe("buildRefineInstruction (继续调整发给解析的话)", () => {
  it("sends only the extra sentence when a task is bound: the backend merges it with the task context", () => {
    expect(buildRefineInstruction({ goalBound: true, base: "重排 B01 班", extra: " 张老师周三晚上也不能上 " })).toBe("张老师周三晚上也不能上");
  });

  it("appends to the original request without a task", () => {
    expect(buildRefineInstruction({ goalBound: false, base: "重排 B01 班", extra: "张老师周三晚上也不能上" })).toBe("重排 B01 班；张老师周三晚上也不能上");
  });

  it("never starts with a full-width semicolon when there is no request to inherit", () => {
    const text = buildRefineInstruction({ goalBound: false, base: "", extra: "张老师周三晚上也不能上" });
    expect(text).toBe("张老师周三晚上也不能上");
    expect(text.startsWith("；")).toBe(false);
  });
});

describe("needsParamsItems / goalNeedsParams (one definition of 「还差一个条件」)", () => {
  const placeholder = { key: "f1", kind: "forbidden_slot_free", params: { needs_params: true } };
  it("counts only forbidden-slot items still waiting for parameters", () => {
    const goal = { checklist: [placeholder, { key: "f2", kind: "forbidden_slot_free", params: { needs_params: false } }, { key: "c", kind: "coverage", params: { needs_params: true } }] };
    expect(needsParamsItems(goal).map((item) => item.key)).toEqual(["f1"]);
    expect(goalNeedsParams(goal)).toBe(true);
  });

  it("is false for missing goals and empty checklists", () => {
    expect(goalNeedsParams(undefined)).toBe(false);
    expect(goalNeedsParams(null)).toBe(false);
    expect(goalNeedsParams({ checklist: null })).toBe(false);
    expect(needsParamsItems({ checklist: [] })).toEqual([]);
  });
});
