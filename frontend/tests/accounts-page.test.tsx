import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RoleRoute } from "@/app/role-route";
import { AccountsPage } from "@/pages/accounts-page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
}));

vi.mock("@/api/http", () => ({ http: mocks }));

const admin = { id: "admin-id", username: "admin", role: "admin" as const };
const accounts = [
  { id: "admin-id", username: "admin", role: "admin", is_active: true, created_at: "2026-08-01T08:00:00Z", last_login_at: "2026-08-14T08:00:00Z", created_by: null },
  { id: "member-id", username: "member_demo", role: "viewer", is_active: true, created_at: "2026-08-02T08:00:00Z", last_login_at: null, created_by: "admin-id" },
];

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/accounts"]}><Routes><Route element={<Outlet context={{ user: admin }} />}><Route path="/accounts" element={<AccountsPage />} /></Route></Routes></MemoryRouter></QueryClientProvider>);
}

describe("AccountsPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.get.mockReset();
    mocks.post.mockReset();
    mocks.patch.mockReset();
    mocks.get.mockResolvedValue({ data: accounts });
  });

  it("lists administrator and member accounts", async () => {
    renderPage();
    expect(await screen.findByText("member_demo")).toBeVisible();
    expect(screen.getByText("成员")).toBeVisible();
    expect(screen.getByText("当前账号")).toBeVisible();
  });

  it("uses the application dialog before disabling a member", async () => {
    const user = userEvent.setup();
    mocks.patch.mockResolvedValue({ data: { ...accounts[1], is_active: false } });
    renderPage();

    await user.click(await screen.findByRole("button", { name: "停用" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("停用成员账号");
    expect(mocks.patch).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "确认停用" }));
    await waitFor(() => expect(mocks.patch).toHaveBeenCalledWith("/api/v1/users/member-id/status", { is_active: false }));
  });

  it("redirects a member away from an administrator route", async () => {
    const member = { id: "member-id", username: "member_demo", role: "viewer" as const };
    render(<MemoryRouter initialEntries={["/accounts"]}><Routes><Route element={<Outlet context={{ user: member }} />}><Route path="/accounts" element={<RoleRoute roles={["admin"]}><div>管理员页面</div></RoleRoute>} /><Route path="/overview" element={<div>成员总览</div>} /></Route></Routes></MemoryRouter>);
    expect(await screen.findByText("成员总览")).toBeVisible();
    expect(screen.queryByText("管理员页面")).not.toBeInTheDocument();
  });
});
