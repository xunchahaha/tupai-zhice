import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { SopSteps } from "@/components/sop-steps";
import { SOP_STEPS } from "@/lib/sop";

function renderSteps(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SopSteps />
    </MemoryRouter>,
  );
}

function stepLink(label: string) {
  return screen.getByText(label).closest("a") as HTMLAnchorElement;
}

/** 步骤圆点是链接里第一个带边框色 token 的 span，用它区分当前/已完成/未到三种状态。 */
function stepDot(label: string) {
  return stepLink(label).querySelector("span") as HTMLSpanElement;
}

describe("SopSteps", () => {
  afterEach(cleanup);

  it("暴露全部 SOP 步骤为导航链接，诊断与课表视图不是步骤", () => {
    renderSteps("/rules");
    const nav = screen.getByRole("navigation", { name: "排课流程" });
    expect(within(nav).getAllByRole("link")).toHaveLength(SOP_STEPS.length);
    expect(SOP_STEPS.map((step) => step.to)).toEqual([
      "/master-data",
      "/rules",
      "/solver",
      "/reschedule",
      "/versions",
    ]);
    expect(SOP_STEPS.map((step) => step.to)).not.toContain("/diagnostics");
    expect(SOP_STEPS.map((step) => step.to)).not.toContain("/schedule");
  });

  it("当前步蓝底白字，之前为蓝边已完成态，之后为灰显未到态", () => {
    renderSteps("/reschedule");

    const done = stepDot("规则");
    expect(done.className).toContain("border-blue-600");
    expect(done.className).not.toContain("bg-blue-600");

    const current = stepDot("调课");
    expect(current.className).toContain("bg-blue-600");
    expect(current.className).toContain("text-white");

    const todo = stepDot("发布");
    expect(todo.className).toContain("border-zinc-300");
    expect(todo.className).not.toContain("blue-600");
  });

  it("不在流程页上时没有当前步，全部按未到态显示", () => {
    renderSteps("/overview");
    for (const step of SOP_STEPS) {
      expect(stepDot(step.label).className).toContain("border-zinc-300");
    }
  });

  it("步骤链接指向对应页面", () => {
    renderSteps("/rules");
    expect(stepLink("主数据").getAttribute("href")).toBe("/master-data");
    expect(stepLink("发布").getAttribute("href")).toBe("/versions");
  });
});
