import { describe, expect, it } from "vitest";

import { plainReasons } from "@/lib/assistant-task";
import {
  distinctNameLabel,
  formatSlotLabel,
  formatTimeRange,
  humanizeIds,
  isAutoVersionName,
  isTechnicalNote,
} from "@/lib/humanize";

describe("formatTimeRange", () => {
  it("截掉秒，用半角连字符连接，和 formatSlot 一致", () => {
    expect(formatTimeRange("18:30:00", "21:30")).toBe("18:30-21:30");
    expect(formatTimeRange("08:30:00", "11:30:00")).toBe("08:30-11:30");
  });

  it("缺一端时只返回有值的那端，两端都缺返回空串", () => {
    expect(formatTimeRange("18:30:00", null)).toBe("18:30");
    expect(formatTimeRange(undefined, "21:30:00")).toBe("21:30");
    expect(formatTimeRange(null, undefined)).toBe("");
    expect(formatTimeRange("", "")).toBe("");
  });
});

describe("formatSlotLabel", () => {
  it("有星期和起止时间时返回「周三 18:30-21:30」", () => {
    expect(
      formatSlotLabel({ weekday: "周三", start_time: "18:30:00", end_time: "21:30:00", business_id: "S05" }),
    ).toBe("周三 18:30-21:30");
  });

  it("星期或时间缺失时退回 formatSlot(business_id)", () => {
    expect(formatSlotLabel({ weekday: null, start_time: "18:30", end_time: "21:30", business_id: "SLOT-周三-1830-2130" })).toBe(
      "周三 18:30-21:30",
    );
    expect(formatSlotLabel({ weekday: "周三", start_time: "18:30", end_time: null, business_id: "S05" })).toBe("S05");
  });

  it("只含 S05 这类业务标签时原样显示，不硬拼", () => {
    expect(formatSlotLabel({ business_id: "S05" })).toBe("S05");
  });
});

describe("humanizeIds", () => {
  it("07b 截图：约束范围里的任务编号换成「本次任务要求」，系统约束编号换成中文", () => {
    const raw = "约束范围：TASK-2e60acb7-forbidden_slot_free-1；系统约束：SYSTEM-FIXED-TIME、SYSTEM-CLASS-NO-OVERLAP。";

    expect(humanizeIds(raw)).toBe("约束范围：本次任务要求；系统约束：固定时段锁定、班级时间不重叠。");
  });

  it("不同的任务约束各自编号，换完之后仍能分清建议保留哪一条", () => {
    const raw = "规则 TASK-2e60acb7-forbidden_slot_free-1 与 TASK-2e60acb7-forbidden_slot_free-2 的条件重复，建议优先保留 TASK-2e60acb7-forbidden_slot_free-1。";
    expect(humanizeIds(raw)).toBe("规则 本次任务要求 1 与 本次任务要求 2 的条件重复，建议优先保留 本次任务要求 1。");
    expect(humanizeIds("约束范围：TASK-2e60acb7-a、TASK-2e60acb7-b、R-001")).toBe("约束范围：本次任务要求 1、本次任务要求 2、R-001");
  });

  it("一组原因共用一套编号：同一条任务约束在每句里叫同一个名字", () => {
    expect(plainReasons([
      "约束范围：TASK-2e60acb7-a；系统约束：SYSTEM-FIXED-TIME",
      "规则 TASK-2e60acb7-b 与 TASK-2e60acb7-a 的条件重复，建议优先保留 TASK-2e60acb7-b。",
    ])).toEqual([
      "约束范围：本次任务要求 1；系统约束：固定时段锁定",
      "规则 本次任务要求 2 与 本次任务要求 1 的条件重复，建议优先保留 本次任务要求 2。",
    ]);
  });

  it("07b 截图：删掉「示例课次」子句，句子仍以句号收尾", () => {
    const raw =
      "班级时间不重叠：班级场景 ('考研', '考研·全年集训营标准编排', '半年营一班') / ('08:30', '11:30') 在 2026-11-28 至 2026-11-28 的候选窗口内有 3 场，最多容纳 1 场；示例课次：CAMPUS-ZZ-34FB7F8AE46BC344、CAMPUS-ZZ-4812C13751261323、CAMPUS-ZZ-DCA284F3D3C3E0CE。";

    const result = humanizeIds(raw);

    expect(result).toBe(
      "班级时间不重叠：班级场景 ('考研', '考研·全年集训营标准编排', '半年营一班') / ('08:30', '11:30') 在 2026-11-28 至 2026-11-28 的候选窗口内有 3 场，最多容纳 1 场。",
    );
    expect(result).not.toContain("CAMPUS-");
    expect(result).not.toContain("示例课次");
  });

  it("不在「示例课次」子句里的残留课次编号换成「某课次」", () => {
    expect(humanizeIds("CAMPUS-ZZ-34FB7F8AE46BC344 与其他课次冲突")).toBe("某课次 与其他课次冲突");
  });

  it("SLOT- 编号换成「周三 18:30-21:30」，括号和顿号不被吞进去", () => {
    expect(humanizeIds("禁排时段（SLOT-周三-1830-2130）仍被占用")).toBe("禁排时段（周三 18:30-21:30）仍被占用");
    expect(humanizeIds("SLOT-周三-1830-2130、SLOT-周四-0830-1130 都满了")).toBe("周三 18:30-21:30、周四 08:30-11:30 都满了");
  });

  it("只含 S05 的情况：SLOT-S05 去掉前缀，裸 S05 不动", () => {
    expect(humanizeIds("时段 SLOT-S05 已满")).toBe("时段 S05 已满");
    expect(humanizeIds("教师 T01 × 时段 S05、S06")).toBe("教师 T01 × 时段 S05、S06");
  });

  it("没有匹配时原样返回", () => {
    const plain = "已确认在当前硬性要求下无法排开全部课程；需要你调整范围或放宽条件后重新排。";

    expect(humanizeIds(plain)).toBe(plain);
    expect(humanizeIds("")).toBe("");
    expect(humanizeIds("TASK-xyz 不是任务编号")).toBe("TASK-xyz 不是任务编号");
  });
});

describe("isTechnicalNote", () => {
  it("含 CP-SAT 或以「日期感知模型」开头的求解引擎自述返回 true", () => {
    expect(isTechnicalNote("日期感知模型未使用可供 CP-SAT 提取的假设文字；本次无解按内置硬约束标记。")).toBe(true);
    expect(isTechnicalNote("日期感知模型按日期展开课次")).toBe(true);
    expect(isTechnicalNote("CP-SAT 求解超时")).toBe(true);
  });

  it("普通业务句子返回 false", () => {
    expect(isTechnicalNote("约束范围：本次任务要求；系统约束：固定时段锁定。")).toBe(false);
    expect(isTechnicalNote("")).toBe(false);
  });
});

describe("isAutoVersionName", () => {
  it("系统自动起的「课表版本 V4」返回 true", () => {
    expect(isAutoVersionName("课表版本 V4")).toBe(true);
    expect(isAutoVersionName("课表版本V12")).toBe(true);
    expect(isAutoVersionName("课表版本 v3")).toBe(true);
  });

  it("人起的名字或空值返回 false", () => {
    expect(isAutoVersionName("郑州校区官方原始课表")).toBe(false);
    expect(isAutoVersionName("课表版本 V4（调课后）")).toBe(false);
    expect(isAutoVersionName(null)).toBe(false);
    expect(isAutoVersionName(undefined)).toBe(false);
  });
});

describe("distinctNameLabel", () => {
  it("只写名称；同一列表里重名时补编号区分", () => {
    const rooms = [
      { name: "301", business_id: "ZZ-301" },
      { name: "301", business_id: "KF-301" },
      { name: "502", business_id: "ZZ-502" },
    ];
    const label = distinctNameLabel(rooms);
    expect(rooms.map(label)).toEqual(["301（ZZ-301）", "301（KF-301）", "502"]);
  });

  it("名称就是编号、或没有名称时不重复括号", () => {
    const teachers = [
      { name: "郑州考研数学教研组", business_id: "郑州考研数学教研组" },
      { name: "郑州考研数学教研组", business_id: "郑州考研数学教研组-2" },
      { name: "", business_id: "T09" },
    ];
    const label = distinctNameLabel(teachers);
    expect(teachers.map(label)).toEqual(["郑州考研数学教研组", "郑州考研数学教研组（郑州考研数学教研组-2）", "T09"]);
  });
});
