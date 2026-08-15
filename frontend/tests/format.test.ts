import { describe, expect, it } from "vitest";

import { datetime } from "@/lib/format";

describe("UTC+8 时间显示", () => {
  it("在浏览器时区不同于中国时仍按 Asia/Shanghai 展示", () => {
    const rendered = datetime("2026-08-01T00:00:00Z");

    expect(rendered).toContain("08:00");
  });
});
