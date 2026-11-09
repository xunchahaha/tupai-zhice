import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, RouterProvider, createMemoryRouter, useOutletContext } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type UserResponse } from "@/api/generated/models";
import { type ScheduleAccessRole, type ScheduleSet } from "@/api/schedule-sets";
import { AppShell } from "@/app/app-shell";
import { type AppOutletContext } from "@/app/user-context";
import "./support/data-router-shim";

const { listScheduleSets } = vi.hoisted(() => ({ listScheduleSets: vi.fn() }));

vi.mock("@/api/schedule-sets", () => ({
  scheduleSetApi: { list: listScheduleSets, create: vi.fn(), rename: vi.fn() },
}));

/** 页面从 Outlet context 读到的课表方案加载状态；助手页据此区分「还在加载」和「确实无权限」。 */
function ContextProbe() {
  const { scheduleSetLoading } = useOutletContext<AppOutletContext>();
  return <div data-testid="page" data-loading={String(scheduleSetLoading)}>页面内容</div>;
}

function makeUser(role: UserResponse["role"]): UserResponse {
  return { id: `${role}-id`, username: `${role}_demo`, role, is_active: true, created_at: "2026-01-01T00:00:00Z" };
}

function makeSet(accessRole: ScheduleAccessRole): ScheduleSet {
  return { id: "set-1", code: "main", name: "主课表", display_order: 1, is_active: true, access_role: accessRole };
}

/** 与线上一致：AuthBoundary 提供 user，AppShell 再把当前课表方案的权限透传给页面。 */
async function renderShell(role: UserResponse["role"], path = "/assistant", accessRole: ScheduleAccessRole = "approver") {
  listScheduleSets.mockResolvedValue([makeSet(accessRole)]);
  const router = createMemoryRouter(
    [{ element: <Outlet context={{ user: makeUser(role) }} />, children: [{ element: <AppShell />, children: [{ path: "*", element: <ContextProbe /> }] }] }],
    { initialEntries: [path] },
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  // 课表方案的访问权限是异步加载的，权限相关断言必须等它落定。
  await screen.findAllByRole("combobox", { name: "当前课表方案" });
  return router;
}

const sidebar = () => screen.getByRole("navigation", { name: "主导航" });
const sidebarLinks = () => within(sidebar()).getAllByRole("link").map((link) => link.getAttribute("aria-label") ?? link.textContent);
const entry = (name: string) => within(sidebar()).getByRole("link", { name });
const queryEntry = (name: string) => within(sidebar()).queryByRole("link", { name });

describe("AppShell 侧栏", () => {
  beforeEach(() => {
    window.localStorage.clear();
    listScheduleSets.mockReset();
  });
  afterEach(cleanup);

  it("管理员只看到三个业务入口和底部设置，没有分组标题与旧模块入口", async () => {
    await renderShell("admin");

    expect(sidebarLinks()).toEqual(["排课助手", "课表", "基础资料", "设置"]);
    expect(entry("排课助手")).toHaveAttribute("href", "/assistant");
    expect(entry("课表")).toHaveAttribute("href", "/schedule");
    expect(entry("基础资料")).toHaveAttribute("href", "/master-data");
    expect(entry("设置")).toHaveAttribute("href", "/settings");

    for (const removed of ["总览", "主数据", "规则工作台", "排课求解", "课表视图", "无解诊断", "局部调课", "记忆与偏好", "目标跟踪", "版本与回滚", "公开链接", "账号管理"]) {
      expect(queryEntry(removed)).not.toBeInTheDocument();
    }
    for (const groupTitle of ["工作台", "排课流程", "变更"]) {
      expect(within(sidebar()).queryByText(groupTitle)).not.toBeInTheDocument();
    }
  });

  it("把课表方案是否仍在加载透传给页面：加载中为 true，落定后为 false", async () => {
    let resolveSets: (sets: ScheduleSet[]) => void = () => {};
    listScheduleSets.mockReturnValue(new Promise<ScheduleSet[]>((resolve) => { resolveSets = resolve; }));
    const router = createMemoryRouter(
      [{ element: <Outlet context={{ user: makeUser("scheduler") }} />, children: [{ element: <AppShell />, children: [{ path: "*", element: <ContextProbe /> }] }] }],
      { initialEntries: ["/assistant"] },
    );
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByTestId("page")).toHaveAttribute("data-loading", "true");
    resolveSets([makeSet("scheduler")]);
    await waitFor(() => expect(screen.getByTestId("page")).toHaveAttribute("data-loading", "false"));
  });

  it("设置与三个业务入口分处两个区块，每个入口都带图标", async () => {
    await renderShell("admin");

    expect(entry("设置").parentElement).not.toBe(entry("排课助手").parentElement);
    expect(entry("课表").parentElement).toBe(entry("排课助手").parentElement);
    for (const name of ["排课助手", "课表", "基础资料", "设置"]) {
      expect(entry(name).querySelector("svg")).not.toBeNull();
    }
  });

  it("排课员同样可见设置", async () => {
    await renderShell("scheduler", "/assistant", "scheduler");
    expect(sidebarLinks()).toEqual(["排课助手", "课表", "基础资料", "设置"]);
  });

  it.each([
    ["approver", "approver"],
    ["viewer", "viewer"],
  ] as const)("%s 看得到三个业务入口，看不到设置", async (role, accessRole) => {
    await renderShell(role, "/assistant", accessRole);
    expect(sidebarLinks()).toEqual(["排课助手", "课表", "基础资料"]);
  });

  it("在当前课表方案里是只读成员的排课员，同样看不到设置，业务入口保留", async () => {
    await renderShell("scheduler", "/assistant", "viewer");
    await waitFor(() => expect(queryEntry("设置")).not.toBeInTheDocument());
    expect(sidebarLinks()).toEqual(["排课助手", "课表", "基础资料"]);
  });

  it("只读成员在课表方案选择器旁看到「只读」徽标，侧栏写明本方案权限；顶栏不再有「当前角色」", async () => {
    await renderShell("scheduler", "/assistant", "viewer");

    expect(screen.getAllByText("只读", { exact: true }).length).toBeGreaterThan(0);
    expect(screen.getAllByText("排课员 · 本方案只读").length).toBeGreaterThan(0);
    expect(screen.queryByText(/当前角色/)).not.toBeInTheDocument();
    expect(screen.getByText("课表方案")).toBeInTheDocument();
    // 只有管理员才有新建 / 重命名课表方案的图标按钮。
    expect(screen.queryByRole("button", { name: "新建课表方案" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重命名课表方案" })).not.toBeInTheDocument();
  });

  it("有审批权限时侧栏写「本方案可审批」，不出现只读徽标；管理员的新建 / 重命名是图标按钮", async () => {
    await renderShell("admin", "/assistant", "approver");

    expect(screen.getAllByText("管理员 · 本方案可审批").length).toBeGreaterThan(0);
    expect(screen.queryByText("只读", { exact: true })).not.toBeInTheDocument();
    for (const name of ["新建课表方案", "重命名课表方案"]) {
      const button = screen.getByRole("button", { name });
      expect(button).toHaveAttribute("title", name);
      expect(button.textContent).toBe("");
    }
  });

  it("账户菜单不再重复用户名和角色，账户信息里没有账户 ID", async () => {
    const user = userEvent.setup();
    await renderShell("admin");

    const menuButtons = screen.getAllByRole("button", { name: "账户菜单" });
    expect(menuButtons[0]).toHaveAttribute("title", expect.stringContaining("admin_demo"));
    await user.click(menuButtons[0]);
    expect(screen.getByRole("button", { name: "账户信息" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "退出登录" })).toBeInTheDocument();
    // 侧栏身份区已经有一份用户名，菜单里不再重复。
    expect(screen.getAllByText("admin_demo")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "账户信息" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("admin_demo")).toBeInTheDocument();
    expect(within(dialog).queryByText("账户 ID")).not.toBeInTheDocument();
  });

  it.each([
    ["/assistant", "排课助手"],
    ["/assistant?goal=g1&action=raise_budget", "排课助手"],
    ["/assistant?run=run-1", "排课助手"],
    ["/schedule", "课表"],
    ["/schedule?view=history&version=v1", "课表"],
    ["/master-data", "基础资料"],
    ["/settings", "设置"],
    ["/rules", "设置"],
    ["/rules?rule=R-1", "设置"],
    ["/memory?entry=m1", "设置"],
    ["/accounts", "设置"],
  ])("在 %s 上只高亮「%s」", async (path, expected) => {
    await renderShell("admin", path);

    const current = within(sidebar()).getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveAccessibleName(expected);
  });

  it("无权进入设置的角色停在 /rules 时，侧栏没有任何入口被高亮", async () => {
    await renderShell("approver", "/rules", "approver");
    const current = within(sidebar()).getAllByRole("link").filter((link) => link.hasAttribute("aria-current"));
    expect(current).toHaveLength(0);
  });

  it("品牌链接回到排课助手", async () => {
    await renderShell("admin", "/schedule");
    expect(screen.getByRole("link", { name: "途排智策" })).toHaveAttribute("href", "/assistant");
  });

  it("折叠态只显示图标，用 title 与 aria-label 保留入口名称", async () => {
    const user = userEvent.setup();
    await renderShell("admin");

    await user.click(screen.getByRole("button", { name: "收起侧边栏" }));

    for (const name of ["排课助手", "课表", "基础资料", "设置"]) {
      const link = entry(name);
      expect(link).toHaveAttribute("title", name);
      expect(link).toHaveAttribute("aria-label", name);
      expect(link.textContent).toBe("");
      expect(link.querySelector("svg")).not.toBeNull();
    }

    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    expect(entry("课表")).not.toHaveAttribute("title");
    expect(entry("课表").textContent).toBe("课表");
  });

  it("移动端抽屉里是同一套入口，点击入口后自动收起", async () => {
    const user = userEvent.setup();
    await renderShell("admin");

    await user.click(screen.getByRole("button", { name: "打开导航" }));
    const navs = screen.getAllByRole("navigation", { name: "主导航" });
    expect(navs).toHaveLength(2);
    expect(within(navs[1]).getAllByRole("link").map((link) => link.textContent)).toEqual(["排课助手", "课表", "基础资料", "设置"]);

    await user.click(within(navs[1]).getByRole("link", { name: "课表" }));
    await waitFor(() => expect(screen.getAllByRole("navigation", { name: "主导航" })).toHaveLength(1));
  });
});
