import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginPage } from "@/pages/login-page";

const { login, setToken } = vi.hoisted(() => ({
  login: vi.fn(),
  setToken: vi.fn(),
}));

vi.mock("@/api/generated/client", () => ({ loginApiV1AuthTokenPost: login }));
vi.mock("@/api/http", () => ({ authStore: { set: setToken } }));

describe("LoginPage", () => {
  let queryClient: QueryClient;

  afterEach(cleanup);

  beforeEach(() => {
    queryClient = new QueryClient();
    login.mockReset();
    setToken.mockReset();
  });

  it("submits the default demo credentials and stores the access token", async () => {
    login.mockResolvedValue({ access_token: "test-token" });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <LoginPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByLabelText("用户名")).toHaveValue("admin");
    expect(screen.getByLabelText("密码")).toHaveValue("tupai-demo-admin-2026!");
    await user.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => expect(login).toHaveBeenCalledWith({ username: "admin", password: "tupai-demo-admin-2026!" }));
    expect(setToken).toHaveBeenCalledWith("test-token");
  });

  it("lands on the assistant after a successful sign-in", async () => {
    login.mockResolvedValue({ access_token: "test-token" });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/login"]}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/assistant" element={<div>排课助手落点</div>} />
            <Route path="/overview" element={<div>旧总览落点</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("排课助手落点")).toBeVisible();
    expect(screen.queryByText("旧总览落点")).not.toBeInTheDocument();
  });

  it("shows validation feedback before an empty form is submitted", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <LoginPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await user.clear(screen.getByLabelText("用户名"));
    await user.clear(screen.getByLabelText("密码"));
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("请输入用户名")).toBeVisible();
    expect(login).not.toHaveBeenCalled();
  });
});
