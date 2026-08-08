import { describe, expect, it } from "vitest";

import { preferredSchedule } from "@/lib/schedule";

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
});
