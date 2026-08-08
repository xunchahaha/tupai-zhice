import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginPage } from "@/pages/login-page";

const { login, setToken } = vi.hoisted(() => ({
  login: vi.fn(),
  setToken: vi.fn(),
}));

vi.mock("@/api/generated/client", () => ({ loginApiV1AuthTokenPost: login }));
vi.mock("@/api/http", () => ({ authStore: { set: setToken } }));

describe("LoginPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    login.mockReset();
    setToken.mockReset();
  });

  it("submits the default demo credentials and stores the access token", async () => {
    login.mockResolvedValue({ access_token: "test-token" });
    const user = userEvent.setup();
    render(<MemoryRouter><LoginPage /></MemoryRouter>);

    expect(screen.getByLabelText("用户名")).toHaveValue("admin");
    expect(screen.getByLabelText("密码")).toHaveValue("tupai-demo");
    await user.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => expect(login).toHaveBeenCalledWith({ username: "admin", password: "tupai-demo" }));
    expect(setToken).toHaveBeenCalledWith("test-token");
  });

  it("shows validation feedback before an empty form is submitted", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><LoginPage /></MemoryRouter>);

    await user.clear(screen.getByLabelText("用户名"));
    await user.clear(screen.getByLabelText("密码"));
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("请输入用户名")).toBeVisible();
    expect(login).not.toHaveBeenCalled();
  });
});
