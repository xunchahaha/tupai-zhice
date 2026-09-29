import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { SopSteps } from "@/components/sop-steps";
import { SOP_STEPS } from "@/lib/sop";
import { ROUTES } from "@/lib/routes";

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

  it("三步流程与左侧三个业务入口一一对应，且都是导航链接", () => {
    renderSteps(ROUTES.masterData);
    const nav = screen.getByRole("navigation", { name: "排课流程" });
    expect(within(nav).getAllByRole("link")).toHaveLength(3);
    expect(SOP_STEPS.map((step) => [step.label, step.to])).toEqual([
      ["基础资料", ROUTES.masterData],
      ["排课助手", ROUTES.assistant],
      ["课表", ROUTES.schedule],
    ]);
  });

  it("规则、调课、发布等不再是独立步骤，旧入口地址也不出现在流程条里", () => {
    const targets = SOP_STEPS.map((step) => step.to);
    for (const legacy of ["/rules", "/solver", "/reschedule", "/versions", "/diagnostics", "/overview"]) {
      expect(targets).not.toContain(legacy);
    }
  });

  it("当前步蓝底白字，之前为蓝边已完成态，之后为灰显未到态", () => {
    renderSteps(ROUTES.assistant);

    const done = stepDot("基础资料");
    expect(done.className).toContain("border-blue-600");
    expect(done.className).not.toContain("bg-blue-600");

    const current = stepDot("排课助手");
    expect(current.className).toContain("bg-blue-600");
    expect(current.className).toContain("text-white");

    const todo = stepDot("课表");
    expect(todo.className).toContain("border-zinc-300");
    expect(todo.className).not.toContain("blue-600");
  });

  it("助手页带续办 query 时仍算当前步", () => {
    renderSteps("/assistant?goal=g1&action=raise_budget");
    expect(stepDot("排课助手").className).toContain("bg-blue-600");
  });

  it("不在流程页上时没有当前步，全部按未到态显示", () => {
    renderSteps(ROUTES.settings);
    for (const step of SOP_STEPS) {
      expect(stepDot(step.label).className).toContain("border-zinc-300");
    }
  });

  it("步骤链接指向对应页面", () => {
    renderSteps(ROUTES.settings);
    expect(stepLink("基础资料").getAttribute("href")).toBe("/master-data");
    expect(stepLink("排课助手").getAttribute("href")).toBe("/assistant");
    expect(stepLink("课表").getAttribute("href")).toBe("/schedule");
  });
});
