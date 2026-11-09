import { describe, expect, it } from "vitest";

import { latestDraftSchedule, preferredSchedule, scheduleForRun, versionStatus } from "@/lib/schedule";

describe("versionStatus", () => {
  it("calls the published version 当前使用中", () => {
    expect(versionStatus("published")).toEqual({ label: "当前使用中", tone: "green" });
  });
  it("calls a draft 草稿", () => {
    expect(versionStatus("draft")).toEqual({ label: "草稿", tone: "yellow" });
  });
  it("calls an archived version 已归档", () => {
    expect(versionStatus("archived")).toEqual({ label: "已归档", tone: "neutral" });
  });
  it("falls back to the generic status label", () => {
    expect(versionStatus("rolled_back")).toEqual({ label: "已回滚", tone: "neutral" });
  });
});

const schedules = [
  { id: "v4", status: "rolled_back" },
  { id: "v3", status: "published" },
  { id: "v2", status: "draft" },
];

describe("preferredSchedule", () => {
  it("prefers the published version over a newer rolled-back version", () => {
    expect(preferredSchedule(schedules)?.id).toBe("v3");
  });

  it("uses the newest draft when no published version exists", () => {
    expect(preferredSchedule(schedules.filter((item) => item.status !== "published"))?.id).toBe("v2");
  });

  it("can select the latest draft after a completed solve", () => {
    expect(latestDraftSchedule(schedules)?.id).toBe("v2");
  });
});


describe("scheduleForRun", () => {
  const drafts = [
    { id: "old", status: "draft", solver_run_id: "old-run" },
    { id: "new", status: "draft", solver_run_id: "new-run" },
  ];
  it("matches the exact successful run rather than the first draft", () => {
    expect(scheduleForRun(drafts, { id: "new-run", status: "completed", model_status: "OPTIMAL" })?.id).toBe("new");
  });
  it.each(["INFEASIBLE", "UNKNOWN", "MODEL_INVALID", null])("never reuses an older candidate for %s", (model_status) => {
    expect(scheduleForRun(drafts, { id: "old-run", status: "completed", model_status })).toBeUndefined();
  });
  it("waits for the task's own version after successful completion", () => {
    expect(scheduleForRun(drafts, { id: "missing", status: "completed", model_status: "FEASIBLE" })).toBeUndefined();
    expect(scheduleForRun(drafts, { id: "old-run", status: "running", model_status: "OPTIMAL" })).toBeUndefined();
  });
});
