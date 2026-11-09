import { cleanup, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary, RouteErrorElement } from "@/app/error-boundary";
import "./support/data-router-shim";

function Boom(): never {
  throw new Error("渲染炸了");
}

describe("异常兜底页文案", () => {
  afterEach(cleanup);

  it("渲染期异常兜底页引导回排课助手，而不是已不存在的总览", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByRole("button", { name: "回到排课助手" })).toBeVisible();
    expect(screen.getByText(/或回到排课助手重新进入/)).toBeVisible();
    expect(screen.queryByText(/总览/)).not.toBeInTheDocument();
  });

  it("原始异常信息默认收进「技术详情」，一键可展开；标题和两个按钮常驻", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByRole("heading", { name: "页面出错了" })).toBeVisible();
    expect(screen.getByRole("button", { name: "重试" })).toBeVisible();
    expect(screen.getByText("渲染炸了")).not.toBeVisible();
    screen.getByText("技术详情").click();
    expect(screen.getByText("渲染炸了")).toBeVisible();
  });

  it("路由层异常兜底页同样引导回排课助手", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const router = createMemoryRouter([{ path: "/", element: <Boom />, errorElement: <RouteErrorElement /> }]);
    render(<RouterProvider router={router} />);

    expect(await screen.findByRole("button", { name: "回到排课助手" })).toBeVisible();
    expect(screen.getByText(/或回到排课助手重新进入/)).toBeVisible();
    expect(screen.queryByText(/总览/)).not.toBeInTheDocument();
  });
});
