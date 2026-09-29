import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type UserResponse } from "@/api/generated/models";
import { type ScheduleAccessRole } from "@/api/schedule-sets";
import { routeObjects } from "@/app/router";
import "./support/data-router-shim";

// 只验证路由表本身：认证壳、侧栏壳和各页面都换成轻量占位，避免拉起整套数据请求。
const { state, stubPage } = vi.hoisted(() => ({
  state: {
    user: { id: "u1", username: "demo", role: "admin", is_active: true, created_at: "2026-01-01T00:00:00Z" } as UserResponse,
    accessRole: "approver" as ScheduleAccessRole,
    runs: { isPending: false, isError: false, data: [] as unknown[] },
    runsOptions: undefined as unknown,
  },
  stubPage: async (name: string) => {
    const { createElement } = await import("react");
    return () => createElement("div", { "data-testid": "page" }, name);
  },
}));

vi.mock("@/api/generated/client", () => ({
  useListSolverRunsApiV1SolverRunsGet: (options: unknown) => {
    state.runsOptions = options;
    return state.runs;
  },
}));
vi.mock("@/app/auth-boundary", async () => {
  const { createElement } = await import("react");
  const { Outlet } = await import("react-router-dom");
  return { AuthBoundary: () => createElement(Outlet, { context: { user: state.user } }) };
});
vi.mock("@/app/app-shell", async () => {
  const { createElement } = await import("react");
  const { Outlet } = await import("react-router-dom");
  return { AppShell: () => createElement(Outlet, { context: { user: state.user, scheduleAccessRole: state.accessRole } }) };
});
vi.mock("@/pages/accounts-page", async () => ({ AccountsPage: await stubPage("accounts") }));
vi.mock("@/pages/assistant-page", async () => ({ AssistantPage: await stubPage("assistant") }));
vi.mock("@/pages/login-page", async () => ({ LoginPage: await stubPage("login") }));
vi.mock("@/pages/master-data-page", async () => ({ MasterDataPage: await stubPage("master-data") }));
vi.mock("@/pages/memory-page", async () => ({ MemoryPage: await stubPage("memory") }));
vi.mock("@/pages/public/public-schedule-page", async () => ({ PublicSchedulePage: await stubPage("public") }));
vi.mock("@/pages/rules-page", async () => ({ RulesPage: await stubPage("rules") }));
vi.mock("@/pages/schedule-page", async () => ({ SchedulePage: await stubPage("schedule") }));
vi.mock("@/pages/settings-page", async () => ({ SettingsPage: await stubPage("settings") }));

function renderAt(url: string) {
  const router = createMemoryRouter(routeObjects, { initialEntries: [url] });
  render(<RouterProvider router={router} />);
  return router;
}

/** query 顺序不属于契约，按键值对比较。 */
function landed(router: ReturnType<typeof renderAt>) {
  const { pathname, search, hash } = router.state.location;
  return { pathname, params: Object.fromEntries(new URLSearchParams(search)), hash };
}

async function expectLanding(router: ReturnType<typeof renderAt>, pathname: string, params: Record<string, string> = {}, hash = "") {
  await waitFor(() => expect(router.state.location.pathname).toBe(pathname));
  expect(landed(router)).toEqual({ pathname, params, hash });
}

function setRole(role: UserResponse["role"], accessRole: ScheduleAccessRole = "approver") {
  state.user = { ...state.user, role };
  state.accessRole = accessRole;
}

describe("旧地址重定向到新入口", () => {
  beforeEach(() => {
    setRole("admin");
    state.runs = { isPending: false, isError: false, data: [] };
    state.runsOptions = undefined;
  });
  afterEach(cleanup);

  it.each([
    ["/overview", "/assistant", {}],
    ["/solver", "/assistant", {}],
    ["/goals", "/assistant", {}],
    ["/reschedule", "/schedule", { view: "adjust" }],
    ["/versions", "/schedule", { view: "history" }],
    ["/public-links", "/schedule", { view: "share" }],
    ["/integrations", "/settings", {}],
  ])("%s → %s", async (from, pathname, params) => {
    const router = renderAt(from);
    await expectLanding(router, pathname, params);
  });

  it("/solver?goal=x&action=raise_budget 原样带到助手，续办与一键补救不丢", async () => {
    const router = renderAt("/solver?goal=x&action=raise_budget");
    await expectLanding(router, "/assistant", { goal: "x", action: "raise_budget" });
    expect(await screen.findByTestId("page")).toHaveTextContent("assistant");
    // 重定向不应在历史里留下死路，否则「返回」会再弹回旧地址。
    expect(router.state.historyAction).toBe("REPLACE");
  });

  it("/goals?goal=x → /assistant?goal=x", async () => {
    const router = renderAt("/goals?goal=x");
    await expectLanding(router, "/assistant", { goal: "x" });
  });

  it("/solver 上其它 query 与 hash 一并保留", async () => {
    const router = renderAt("/solver?goal=g1&action=resolve_scope&prompt=%E6%8E%92%E8%AF%BE#result");
    await expectLanding(router, "/assistant", { goal: "g1", action: "resolve_scope", prompt: "排课" }, "#result");
  });

  it("旧调课/版本/公开链接地址保留原 query，并补上目标视图", async () => {
    let router = renderAt("/reschedule?lesson=L-101");
    await expectLanding(router, "/schedule", { view: "adjust", lesson: "L-101" });
    cleanup();

    router = renderAt("/versions?version=v-2#top");
    await expectLanding(router, "/schedule", { view: "history", version: "v-2" }, "#top");
    cleanup();

    router = renderAt("/public-links?version=v-3");
    await expectLanding(router, "/schedule", { view: "share", version: "v-3" });
  });

  it("/integrations?section=ai 保留设置页锚点", async () => {
    const router = renderAt("/integrations?section=ai");
    await expectLanding(router, "/settings", { section: "ai" });
  });

  it("重定向不再受角色限制：只读成员打开旧 /solver?goal=x 也带着 query 落到助手", async () => {
    setRole("viewer", "viewer");
    const router = renderAt("/solver?goal=x&action=raise_budget");
    await expectLanding(router, "/assistant", { goal: "x", action: "raise_budget" });
  });

  it.each(["/", "/no/such/page", "/overview/extra"])("未知路径 %s → /assistant", async (from) => {
    const router = renderAt(from);
    await expectLanding(router, "/assistant");
    expect(await screen.findByTestId("page")).toHaveTextContent("assistant");
  });
});

describe("/diagnostics 找到最新一次无解任务", () => {
  const run = (id: string, modelStatus: string, createdAt: string) => ({ id, model_status: modelStatus, created_at: createdAt });

  beforeEach(() => {
    setRole("admin");
    state.runsOptions = undefined;
  });
  afterEach(cleanup);

  it("有无解任务时跳到最新那一次的结果与诊断，忽略可行任务和列表顺序", async () => {
    state.runs = {
      isPending: false,
      isError: false,
      data: [
        run("r-old", "INFEASIBLE", "2026-01-01T08:00:00Z"),
        run("r-ok", "OPTIMAL", "2026-03-01T08:00:00Z"),
        run("r-new", "INFEASIBLE", "2026-02-01T08:00:00Z"),
      ],
    };
    const router = renderAt("/diagnostics");
    await expectLanding(router, "/assistant", { run: "r-new" });
    expect(router.state.historyAction).toBe("REPLACE");
  });

  it("没有无解任务时回落到助手首页", async () => {
    state.runs = { isPending: false, isError: false, data: [run("r-ok", "OPTIMAL", "2026-03-01T08:00:00Z"), run("r-feasible", "FEASIBLE", "2026-03-02T08:00:00Z")] };
    const router = renderAt("/diagnostics");
    await expectLanding(router, "/assistant");
  });

  it("任务列表为空时回落到助手首页", async () => {
    state.runs = { isPending: false, isError: false, data: [] };
    const router = renderAt("/diagnostics");
    await expectLanding(router, "/assistant");
  });

  it("请求失败也回落到助手首页，而不是停在空白页", async () => {
    state.runs = { isPending: false, isError: true, data: undefined as unknown as unknown[] };
    const router = renderAt("/diagnostics");
    await expectLanding(router, "/assistant");
  });

  it("加载中显示加载态且不提前跳转", async () => {
    state.runs = { isPending: true, isError: false, data: undefined as unknown as unknown[] };
    const router = renderAt("/diagnostics");
    expect(await screen.findByText("加载数据中…")).toBeVisible();
    expect(router.state.location.pathname).toBe("/diagnostics");
  });

  it("保留 hash 和无关 query", async () => {
    state.runs = { isPending: false, isError: false, data: [run("r-1", "INFEASIBLE", "2026-01-01T08:00:00Z")] };
    const router = renderAt("/diagnostics?from=mail#detail");
    await expectLanding(router, "/assistant", { run: "r-1", from: "mail" }, "#detail");
  });

  it("地址里已带 run 时直接沿用，不再去查任务列表", async () => {
    state.runs = { isPending: false, isError: false, data: [run("r-1", "INFEASIBLE", "2026-01-01T08:00:00Z")] };
    const router = renderAt("/diagnostics?run=r-explicit");
    await expectLanding(router, "/assistant", { run: "r-explicit" });
    expect(state.runsOptions).toEqual({ query: { enabled: false } });
  });
});

describe("新入口的访问口径与旧规则一致", () => {
  beforeEach(() => {
    setRole("admin");
  });
  afterEach(cleanup);

  it.each(["viewer", "approver", "scheduler", "admin"] as const)("%s 可以打开助手、课表、基础资料", async (role) => {
    setRole(role, role === "viewer" ? "viewer" : "approver");
    for (const [path, page] of [["/assistant", "assistant"], ["/schedule", "schedule"], ["/master-data", "master-data"]]) {
      const router = renderAt(`${path}?view=history`);
      expect(await screen.findByTestId("page")).toHaveTextContent(page);
      expect(router.state.location.pathname).toBe(path);
      cleanup();
    }
  });

  it.each([
    ["admin", "approver", "settings"],
    ["scheduler", "scheduler", "settings"],
    ["approver", "approver", "assistant"],
    ["viewer", "viewer", "assistant"],
    ["scheduler", "viewer", "assistant"],
  ] as const)("/settings：%s（本课表 %s）→ %s", async (role, accessRole, expected) => {
    setRole(role, accessRole);
    const router = renderAt("/settings?section=ai");
    expect(await screen.findByTestId("page")).toHaveTextContent(expected);
    expect(router.state.location.pathname).toBe(expected === "settings" ? "/settings" : "/assistant");
  });

  it.each([
    ["admin", "approver", "memory"],
    ["scheduler", "scheduler", "memory"],
    ["approver", "approver", "assistant"],
    ["viewer", "viewer", "assistant"],
  ] as const)("/memory：%s（本课表 %s）→ %s", async (role, accessRole, expected) => {
    setRole(role, accessRole);
    const router = renderAt("/memory?entry=m1");
    expect(await screen.findByTestId("page")).toHaveTextContent(expected);
    expect(router.state.location.pathname).toBe(expected === "memory" ? "/memory" : "/assistant");
  });

  it.each([
    ["admin", "accounts"],
    ["scheduler", "assistant"],
    ["approver", "assistant"],
    ["viewer", "assistant"],
  ] as const)("/accounts：%s → %s", async (role, expected) => {
    setRole(role, role === "viewer" ? "viewer" : "approver");
    const router = renderAt("/accounts");
    expect(await screen.findByTestId("page")).toHaveTextContent(expected);
    expect(router.state.location.pathname).toBe(expected === "accounts" ? "/accounts" : "/assistant");
  });

  it.each(["admin", "scheduler", "approver", "viewer"] as const)("/rules 沿用旧口径：%s 都能打开，query 保留", async (role) => {
    setRole(role, role === "viewer" ? "viewer" : "approver");
    const router = renderAt("/rules?rule=R-1");
    expect(await screen.findByTestId("page")).toHaveTextContent("rules");
    expect(landed(router)).toEqual({ pathname: "/rules", params: { rule: "R-1" }, hash: "" });
  });

  it("登录页与公开课表仍在认证壳之外", async () => {
    let router = renderAt("/login");
    expect(await screen.findByTestId("page")).toHaveTextContent("login");
    expect(router.state.location.pathname).toBe("/login");
    cleanup();

    router = renderAt("/public/t/abc123");
    expect(await screen.findByTestId("page")).toHaveTextContent("public");
    expect(router.state.location.pathname).toBe("/public/t/abc123");
  });
});
