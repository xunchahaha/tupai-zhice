import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountsPage } from "@/pages/accounts-page";
import { RulesPage } from "@/pages/rules-page";
import { SettingsPage } from "@/pages/settings-page";

// 设置页、规则页、账号页都走 orval 生成的客户端（统一经过 customInstance）；
// 设置页的 AI 配置与账号页的课表方案走裸 http.get。
const mocks = vi.hoisted(() => ({ request: vi.fn(), httpGet: vi.fn() }));

vi.mock("@/api/http", () => ({
  customInstance: mocks.request,
  http: { get: mocks.httpGet, post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn() },
  authStore: { get: vi.fn(), set: vi.fn(), clear: vi.fn() },
  API_BASE_URL: "http://127.0.0.1:8000",
}));

type Role = "admin" | "scheduler" | "approver" | "viewer";

function userOf(role: Role) {
  return { id: `${role}-id`, username: role, role, is_active: true, created_at: "2026-01-01T00:00:00Z" };
}

const connection = {
  status: "not_authorized",
  app_configured: true,
  authorized: false,
  missing_fields: [],
  granted_scopes: [],
  missing_scopes: [],
  access_expires_at: null,
  message: "应用配置已就绪，请授权飞书管理员账号。",
  console_url: "https://open.feishu.cn/app/",
  docs_url: "https://open.feishu.cn/document/authentication-management/access-token/obtain-oauth-code",
  app_configuration: {
    configured: true,
    source: "frontend",
    app_id: "cli_test",
    secret_configured: true,
    oauth_redirect_uri: "http://127.0.0.1:8002/api/v1/integrations/feishu/oauth/callback",
    frontend_url: "http://127.0.0.1:5175",
    aily_configured: false,
    aily_app_id: null,
    aily_skill_id: null,
  },
  workspace: null,
};

const catalog = [
  {
    type: "declared_constraint",
    label: "声明性约束",
    scope: [],
    scope_fields: [],
    hardness: ["hard", "soft"],
    solver_paths: { hard: ["date"], soft: ["date"] },
  },
];

function ruleOf(id: string, businessId: string, sourceText: string, status: string) {
  return {
    id,
    business_id: businessId,
    source_text: sourceText,
    status,
    constraint_type: "declared_constraint",
    actor_type: "system",
    actor_ids: [],
    scope: {},
    hardness: "hard",
    weight: null,
    version: 1,
    source_doc: null,
    confidence: 1,
    structured_expression: {},
  };
}

const activeRule = ruleOf("rule-uuid-1", "R-001", "教师甲周三不排晚课", "active");
const pendingRule = ruleOf("rule-uuid-2", "R-002", "每个班每天最多上三节课", "awaiting_confirmation");

let currentRole: Role = "admin";
let ruleRows: unknown[] = [activeRule, pendingRule];

function renderAt(path: string, role: Role = "admin", scheduleAccessRole: "viewer" | "scheduler" | "approver" = "approver") {
  currentRole = role;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<Outlet context={{ user: userOf(role), scheduleAccessRole }} />}>
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/rules" element={<RulesPage />} />
            <Route path="/accounts" element={<AccountsPage />} />
            <Route path="/memory" element={<div>常用偏好页面</div>} />
            <Route path="/assistant" element={<div>排课助手页面</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function highlightedRule(container: HTMLElement) {
  return container.querySelector("button[id^='rule-item-'][aria-current='true']");
}

describe("设置页「高级管理」", () => {
  afterEach(cleanup);

  beforeEach(() => {
    window.localStorage.setItem("tupai:feishu-guide-completed", "1");
    ruleRows = [activeRule, pendingRule];
    mocks.request.mockReset();
    mocks.httpGet.mockReset();
    mocks.request.mockImplementation(async (config: { url: string }) => {
      if (config.url === "/api/v1/auth/me") return userOf(currentRole);
      if (config.url === "/api/v1/integrations/feishu/connection") return connection;
      if (config.url === "/api/v1/integrations/feishu/syncs") return [];
      if (config.url === "/api/v1/rules") return ruleRows;
      if (config.url === "/api/v1/rules/constraint-catalog") return catalog;
      if (config.url === "/api/v1/users") return [userOf("admin")];
      return [];
    });
    mocks.httpGet.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/ai/configuration") {
        return { data: { configured: true, source: "frontend", provider: "openai_compatible", base_url: "https://model.example/v1", api_key_configured: true, model: "scheduling-model" } };
      }
      return { data: [] };
    });
  });

  it("管理员能看到全部三个管理入口，并带着业务语言的说明", async () => {
    renderAt("/settings", "admin");

    expect(await screen.findByRole("heading", { level: 2, name: "高级管理" })).toBeVisible();
    const rules = screen.getByRole("link", { name: /学校通用规则/ });
    const memory = screen.getByRole("link", { name: /常用偏好/ });
    const accounts = screen.getByRole("link", { name: /账号管理/ });
    expect(rules).toHaveAttribute("href", "/rules");
    expect(memory).toHaveAttribute("href", "/memory");
    expect(accounts).toHaveAttribute("href", "/accounts");
    expect(within(memory).getByText(/系统从调课习惯里学到的偏好/)).toBeVisible();
    expect(within(rules).getByText(/长期有效的排课规则/)).toBeVisible();
  });

  it("排课员能看到学校通用规则和常用偏好，看不到只属于管理员的账号管理", async () => {
    renderAt("/settings", "scheduler", "scheduler");

    expect(await screen.findByRole("link", { name: /学校通用规则/ })).toBeVisible();
    expect(screen.getByRole("link", { name: /常用偏好/ })).toBeVisible();
    expect(screen.queryByRole("link", { name: /账号管理/ })).not.toBeInTheDocument();
  });

  it("审批人与只读成员没有常用偏好和账号管理入口，仍可查看学校通用规则", async () => {
    for (const role of ["approver", "viewer"] as const) {
      const { unmount } = renderAt("/settings", role, role === "viewer" ? "viewer" : "approver");
      expect(await screen.findByRole("link", { name: /学校通用规则/ })).toBeVisible();
      expect(screen.queryByRole("link", { name: /常用偏好/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: /账号管理/ })).not.toBeInTheDocument();
      unmount();
    }
  });

  it("进入学校通用规则后可以一步返回设置", async () => {
    const user = userEvent.setup();
    renderAt("/settings", "admin");

    await user.click(await screen.findByRole("link", { name: /学校通用规则/ }));
    expect(await screen.findByRole("heading", { level: 1, name: "学校通用规则" })).toBeVisible();
    await user.click(screen.getByRole("link", { name: "返回设置" }));
    expect(await screen.findByRole("heading", { level: 1, name: "设置" })).toBeVisible();
  });

  it("高级管理排在「通用」之后、「AI 模型」之前，不必滚过整页接入向导", async () => {
    renderAt("/settings", "admin");

    await screen.findByRole("heading", { level: 2, name: "高级管理" });
    const titles = screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent ?? "");
    const at = (prefix: string) => titles.findIndex((title) => title.startsWith(prefix));
    expect(at("通用")).toBeGreaterThanOrEqual(0);
    expect(at("高级管理")).toBe(at("通用") + 1);
    expect(at("AI 模型")).toBe(at("高级管理") + 1);
    expect(at("外部集成")).toBeGreaterThan(at("高级管理"));
  });

  it("进不了设置的账号在规则页看到的是返回排课助手，而不是会被弹回的返回设置", async () => {
    const cases = [
      { role: "approver", access: "approver" },
      { role: "viewer", access: "viewer" },
      // 全局角色是排课员，但在当前课表里只有只读权限：RoleRoute 同样不放行设置。
      { role: "scheduler", access: "viewer" },
    ] as const;
    for (const { role, access } of cases) {
      const { unmount } = renderAt("/rules", role, access);
      expect(await screen.findByRole("heading", { level: 1, name: "学校通用规则" })).toBeVisible();
      expect(screen.queryByRole("link", { name: "返回设置" })).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "返回排课助手" })).toHaveAttribute("href", "/assistant");
      unmount();
    }
  });

  it("能进设置的排课员在规则页仍是返回设置", async () => {
    renderAt("/rules", "scheduler", "scheduler");

    expect(await screen.findByRole("link", { name: "返回设置" })).toHaveAttribute("href", "/settings");
    expect(screen.queryByRole("link", { name: "返回排课助手" })).not.toBeInTheDocument();
  });

  it("账号管理页头同样提供返回设置", async () => {
    const user = userEvent.setup();
    renderAt("/accounts", "admin");

    expect(await screen.findByRole("heading", { level: 1, name: "账号管理" })).toBeVisible();
    const back = screen.getByRole("link", { name: "返回设置" });
    expect(back).toHaveAttribute("href", "/settings");
    await user.click(back);
    expect(await screen.findByRole("heading", { level: 1, name: "设置" })).toBeVisible();
  });
});

describe("学校通用规则页", () => {
  afterEach(() => {
    cleanup();
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  });

  beforeEach(() => {
    ruleRows = [activeRule, pendingRule];
    mocks.request.mockReset();
    mocks.request.mockImplementation(async (config: { url: string }) => {
      if (config.url === "/api/v1/rules") return ruleRows;
      if (config.url === "/api/v1/rules/constraint-catalog") return catalog;
      return [];
    });
  });

  it("页头有返回设置，不再渲染排课流程步骤条", async () => {
    renderAt("/rules", "admin");

    expect(await screen.findByRole("heading", { level: 1, name: "学校通用规则" })).toBeVisible();
    expect(screen.getByRole("link", { name: "返回设置" })).toHaveAttribute("href", "/settings");
    expect(screen.queryByRole("navigation", { name: "排课流程" })).not.toBeInTheDocument();
  });

  it("已有生效规则时「去排课助手」直达助手；旧的「去求解」不再出现", async () => {
    const user = userEvent.setup();
    renderAt("/rules", "admin");

    const go = await screen.findByRole("button", { name: "去排课助手" });
    expect(screen.queryByRole("button", { name: "去求解" })).not.toBeInTheDocument();
    await user.click(go);
    expect(await screen.findByText("排课助手页面")).toBeVisible();
  });

  it("没有生效规则或只读成员时不展示「去排课助手」", async () => {
    ruleRows = [pendingRule];
    const first = renderAt("/rules", "admin");
    await screen.findByText("每个班每天最多上三节课");
    expect(screen.queryByRole("button", { name: "去排课助手" })).not.toBeInTheDocument();
    first.unmount();

    ruleRows = [activeRule];
    renderAt("/rules", "viewer", "viewer");
    await screen.findByText("教师甲周三不排晚课");
    expect(screen.queryByRole("button", { name: "去排课助手" })).not.toBeInTheDocument();
  });

  it("?rule= 直达某条规则：选中并高亮它，右侧显示详情，并滚动到该条", async () => {
    const scrollIntoView = vi.fn();
    (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView = scrollIntoView;
    const { container } = renderAt("/rules?rule=R-002", "admin");

    await waitFor(() => expect(highlightedRule(container)).not.toBeNull());
    const selected = highlightedRule(container) as HTMLElement;
    expect(within(selected).getByText("R-002")).toBeVisible();
    expect(screen.getByText("R-002 / 第 1 版")).toBeVisible();
    expect(container.querySelector("#rule-item-rule-uuid-1")).not.toHaveAttribute("aria-current");
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));
    expect(scrollIntoView.mock.contexts[0]).toBe(selected);
  });

  it("?rule= 之后手动点另一条规则，高亮随之移动，不会被链接参数拽回去", async () => {
    const user = userEvent.setup();
    const { container } = renderAt("/rules?rule=R-002", "admin");

    await waitFor(() => expect(highlightedRule(container)).not.toBeNull());
    await user.click(container.querySelector("#rule-item-rule-uuid-1") as HTMLElement);
    expect(container.querySelector("#rule-item-rule-uuid-1")).toHaveAttribute("aria-current", "true");
    expect(container.querySelector("#rule-item-rule-uuid-2")).not.toHaveAttribute("aria-current");
    expect(screen.getByText("R-001 / 第 1 版")).toBeVisible();
  });

  it("运行环境没有 scrollIntoView 时不报错，选中态照常生效", async () => {
    const { container } = renderAt("/rules?rule=R-001", "admin");

    await waitFor(() => expect(highlightedRule(container)).not.toBeNull());
    expect(screen.getByText("R-001 / 第 1 版")).toBeVisible();
  });

  it("?rule= 找不到对应规则时给出提示，列表仍可正常使用", async () => {
    const { container } = renderAt("/rules?rule=R-404", "admin");

    expect(await screen.findByText(/没有找到规则 R-404/)).toBeVisible();
    expect(highlightedRule(container)).toBeNull();
    expect(screen.getByText("教师甲周三不排晚课")).toBeVisible();
  });
});
