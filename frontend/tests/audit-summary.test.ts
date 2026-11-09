import { describe, expect, it } from "vitest";

import { auditSummary } from "@/lib/audit-summary";

describe("auditSummary", () => {
  it("tells a single-lesson reschedule event in one line", () => {
    const summary = auditSummary({
      action: "create",
      resource_type: "reschedule_event",
      detail: {
        event_type: "teacher_leave",
        declared_reason: null,
        teacher_business_id: "郑州考研数学教研组",
        room_business_id: null,
        slot_business_ids: ["SLOT-周四-1830-2130"],
        date_from: "2026-11-26",
        date_to: "2026-11-26",
        course_business_id: "CAMPUS-ZZ-CAC20DD83E1657DC",
        include_neighbors: false,
        neighborhood_days: 7,
      },
    });
    expect(summary).toEqual({ text: "教师请假 · 郑州考研数学教研组 · 2026-11-26（只调这一节）" });
  });

  it("writes a date range for a multi-day event and no single-lesson note when neighbours may move", () => {
    const summary = auditSummary({
      action: "create",
      resource_type: "reschedule_event",
      detail: { event_type: "room_outage", room_business_id: "503教室", date_from: "2026-11-23", date_to: "2026-11-29", include_neighbors: true },
    });
    expect(summary.text).toBe("教室停用 · 503教室 · 2026-11-23 ~ 2026-11-29");
  });

  it("keeps a sync failure reason apart from the summary so it can be shown on its own", () => {
    const summary = auditSummary({
      action: "sync",
      resource_type: "feishu",
      detail: { trigger: "version_publish", schedule_set_id: "default", duration_ms: 0.41, retry_count: 0, error: "飞书令牌密文校验失败，请重新授权" },
    });
    expect(summary).toEqual({ text: "发布后同步", error: "飞书令牌密文校验失败，请重新授权" });
  });

  it("names the sync by its trigger instead of always saying 发布后同步", () => {
    const sync = (trigger: string) => auditSummary({ action: "sync", resource_type: "feishu", detail: { trigger, schedule_set_id: "default" } }).text;
    expect(sync("version_publish")).toBe("发布后同步");
    expect(sync("version_rollback")).toBe("回滚后同步");
    expect(sync("manual_batch")).toBe("手动重新同步");
    expect(sync("single_resource")).toBe("单表同步");
    expect(sync("something_new")).toBe("同步");
  });

  it("says how many rows a batch delete removed, not how many fields the detail has", () => {
    expect(auditSummary({ action: "batch_delete", resource_type: "teacher", detail: { count: 500 } })).toEqual({ text: "删除 500 条" });
    expect(auditSummary({ action: "batch_delete", resource_type: "course_session", detail: { count: 3, selection: "filter", filter: { business_line: "考研" } } }).text).toBe("删除 3 条");
  });

  it("keeps calendar conflicts out of the collapsed raw record", () => {
    expect(auditSummary({ action: "calendar_publish", resource_type: "schedule", detail: { published: 12, conflicts: 2 } })).toEqual({ text: "下发 12 条", warning: "冲突 2 条" });
    expect(auditSummary({ action: "calendar_publish", resource_type: "schedule", detail: { published: 12, conflicts: 0 } })).toEqual({ text: "下发 12 条" });
  });

  it("keeps rows dropped on import out of the collapsed raw record", () => {
    const report = { rows_total: 30, rows_dropped_placeholder_room: 4, course_sessions_created: 26, course_sessions_updated: 0, warnings: {} };
    expect(auditSummary({ action: "import_xlsx", resource_type: "workbook", detail: report })).toEqual({ text: "新建 26 课次", warning: "整行丢弃 4 行（占位教室）" });
    expect(auditSummary({ action: "import_commit", resource_type: "workbook", detail: { ...report, rows_dropped_placeholder_room: 0 } })).toEqual({ text: "新建 26 课次" });
  });

  it("points at the raw record for an unknown action instead of counting its fields", () => {
    expect(auditSummary({ action: "explain", resource_type: "solver_run", detail: { source: "ai", refresh: false } })).toEqual({ text: "见原始记录" });
    expect(auditSummary({ action: "delete", resource_type: "schedule", detail: {} })).toEqual({ text: "-" });
    expect(auditSummary({ action: "delete", resource_type: "schedule", detail: null })).toEqual({ text: "-" });
  });

  it("quotes the first 40 characters of an assistant instruction", () => {
    const instruction = "11月23日那周，半年营一班的课日期一天都不能动，但政治老师周三晚上来不了，先试试能不能排。";
    const { text } = auditSummary({ action: "assistant_solve", resource_type: "solver_run", detail: { instruction, goal_id: "g", unsupported_count: 0 } });
    expect(text).toBe(`「${Array.from(instruction).slice(0, 40).join("")}…」`);
    expect(auditSummary({ action: "assistant_interpret", resource_type: "instruction", detail: { instruction: "短指令" } }).text).toBe("「短指令」");
  });

  it("summarises a task-context update by its date range and task requirements, without internal ids", () => {
    const { text } = auditSummary({
      action: "update_context",
      resource_type: "solve_goal",
      detail: {
        run_baseline_source: "latest_published",
        scope: { class_business_ids: ["半年营一班"], date_from: "2026-11-23", date_to: "2026-11-29" },
        task_constraint_rule_ids: ["TASK-2e60acb7-forbidden_slot_free-1"],
      },
    });
    expect(text).toBe("范围 2026-11-23 ~ 2026-11-29 · 1 条本次要求");
    expect(text).not.toMatch(/TASK-/);
  });

  it("names publish and rollback in Chinese", () => {
    expect(auditSummary({ action: "publish", resource_type: "schedule", detail: { replaced_published_schedule_id: "s1" } }).text).toBe("发布为当前课表");
    expect(auditSummary({ action: "rollback", resource_type: "schedule", detail: { replaced_published_schedule_id: "s2" } }).text).toBe("回滚为当前课表");
  });
});
