import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RoleRoute } from "@/app/role-route";
import { AccountsPage } from "@/pages/accounts-page";

// 页面走 orval 生成的客户端，统一经过 customInstance，因此在这一层拦截。
const mocks = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("@/api/http", () => ({
  customInstance: mocks.request,
  http: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  authStore: { get: vi.fn(), set: vi.fn(), clear: vi.fn() },
  API_BASE_URL: "http://127.0.0.1:8000",
}));

const admin = { id: "admin-id", username: "admin", role: "admin" as const };
const accounts = [
  {
    id: "admin-id",
    username: "admin",
    role: "admin",
    is_active: true,
    created_at: "2026-08-01T08:00:00Z",
    last_login_at: "2026-08-14T08:00:00Z",
    created_by: null,
  },
  {
    id: "member-id",
    username: "member_demo",
    role: "viewer",
    is_active: true,
    created_at: "2026-08-02T08:00:00Z",
    last_login_at: null,
    created_by: "admin-id",
  },
];

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/accounts"]}>
        <Routes>
          <Route element={<Outlet context={{ user: admin }} />}>
            <Route path="/accounts" element={<AccountsPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AccountsPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.request.mockReset();
    mocks.request.mockImplementation(async (config: { url: string; method: string }) => {
      const method = config.method.toUpperCase();
      if (method === "GET" && config.url === "/api/v1/users") return accounts;
      if (config.url === "/api/v1/users/member-id/status") return { ...accounts[1], is_active: false };
      if (config.url === "/api/v1/users/member-id/role") return { ...accounts[1], role: "scheduler" };
      return {};
    });
  });

  it("lists administrator and member accounts", async () => {
    renderPage();
    expect(await screen.findByText("member_demo")).toBeVisible();
    expect(screen.getByText("当前账号")).toBeVisible();
  });

  it("uses the application dialog before disabling a member", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "停用" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("停用成员账号");
    expect(
      mocks.request.mock.calls.filter(([config]) => config.method.toUpperCase() === "PATCH"),
    ).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "确认停用" }));
    await waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith(
        expect.objectContaining({
          url: "/api/v1/users/member-id/status",
          method: "PATCH",
          data: { is_active: false },
        }),
      ),
    );
  });

  it("assigns a role to a member", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.selectOptions(
      await screen.findByRole("combobox", { name: "member_demo 的角色" }),
      "scheduler",
    );

    await waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith(
        expect.objectContaining({
          url: "/api/v1/users/member-id/role",
          method: "PATCH",
          data: { role: "scheduler" },
        }),
      ),
    );
  });

  it("never offers a role selector for the signed-in administrator", async () => {
    renderPage();
    await screen.findByText("member_demo");
    expect(screen.queryByRole("combobox", { name: "admin 的角色" })).not.toBeInTheDocument();
  });

  it("redirects a member away from an administrator route", async () => {
    const member = { id: "member-id", username: "member_demo", role: "viewer" as const };
    render(
      <MemoryRouter initialEntries={["/accounts"]}>
        <Routes>
          <Route element={<Outlet context={{ user: member }} />}>
            <Route
              path="/accounts"
              element={
                <RoleRoute roles={["admin"]}>
                  <div>管理员页面</div>
                </RoleRoute>
              }
            />
            <Route path="/overview" element={<div>成员总览</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("成员总览")).toBeVisible();
    expect(screen.queryByText("管理员页面")).not.toBeInTheDocument();
  });
});
