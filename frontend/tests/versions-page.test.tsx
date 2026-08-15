import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VersionsPage } from "@/pages/versions-page";

// 页面走 orval 生成的客户端，统一经过 customInstance，因此在这一层拦截。
const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("@/api/http", () => ({
  customInstance: mocks.request,
  http: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  authStore: { get: vi.fn(), set: vi.fn(), clear: vi.fn() },
  API_BASE_URL: "http://127.0.0.1:8000",
}));

vi.mock("sonner", () => ({ toast: { success: mocks.success, error: mocks.error } }));

const admin = { id: "admin-id", username: "admin", role: "admin" as const };
const viewer = { id: "viewer-id", username: "member_demo", role: "viewer" as const };
const scheduler = { id: "scheduler-id", username: "scheduler_demo", role: "scheduler" as const };

// v1 是导入基线（published + 官方后缀）；v3 派生出 v4，删它服务端会 409。
const schedules = [
  { id: "s1", version_no: 1, name: "郑州考研官方原始课表", status: "published", parent_id: null, solver_run_id: "run-1", metrics: {}, assignment_count: 1200, published_at: "2026-08-01T02:00:00Z", created_at: "2026-08-01T01:00:00Z" },
  { id: "s2", version_no: 2, name: "求解版本 v2", status: "draft", parent_id: "s1", solver_run_id: "run-2", metrics: {}, assignment_count: 640, published_at: null, created_at: "2026-08-02T01:00:00Z" },
  { id: "s3", version_no: 3, name: "求解版本 v3", status: "archived", parent_id: "s1", solver_run_id: "run-3", metrics: {}, assignment_count: 880, published_at: null, created_at: "2026-08-03T01:00:00Z" },
  { id: "s4", version_no: 4, name: "求解版本 v4", status: "draft", parent_id: "s3", solver_run_id: "run-4", metrics: {}, assignment_count: 900, published_at: null, created_at: "2026-08-04T01:00:00Z" },
];

const auditLogs = [
  { id: "log-1", action: "delete", resource_type: "schedule", resource_id: "s9", user_id: "admin-id", detail: {}, created_at: "2026-08-05T01:00:00Z" },
];

const diffPayload = { changed_count: 2, unchanged_count: 8, items: [] };

let deleted: Set<string>;
let deleteRejection: unknown;

function renderPage(user: typeof admin | typeof viewer | typeof scheduler = admin) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/versions"]}>
        <Routes>
          <Route element={<Outlet context={{ user }} />}>
            <Route path="/versions" element={<VersionsPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("VersionsPage delete", () => {
  afterEach(cleanup);

  beforeEach(() => {
    deleted = new Set();
    deleteRejection = undefined;
    mocks.request.mockReset();
    mocks.success.mockReset();
    mocks.error.mockReset();
    mocks.request.mockImplementation(async (config: { url: string; method: string }) => {
      const method = config.method.toUpperCase();
      if (method === "DELETE") {
        if (deleteRejection) return Promise.reject(deleteRejection);
        deleted.add(config.url.replace("/api/v1/schedules/", ""));
        return undefined;
      }
      if (config.url === "/api/v1/schedules") return schedules.filter((item) => !deleted.has(item.id));
      if (config.url === "/api/v1/audit-logs") return auditLogs;
      if (config.url.includes("/diff/")) return diffPayload;
      return {};
    });
  });

  it("hides the delete control from read-only members", async () => {
    renderPage(viewer);
    await screen.findByText("版本记录");
    expect(screen.queryByRole("button", { name: /^删除版本/ })).not.toBeInTheDocument();
  });

  it("hides the delete control from schedulers, who the server would reject anyway", async () => {
    renderPage(scheduler);
    await screen.findByText("版本记录");
    expect(screen.queryByRole("button", { name: /^删除版本/ })).not.toBeInTheDocument();
  });

  it("never offers to delete the published version or the import baseline", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: "删除版本 v2" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "删除版本 v1" })).not.toBeInTheDocument();
  });

  it("spells out the assignment count and the derived versions before deleting", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "删除版本 v3" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("880 条排课记录");
    expect(dialog).toHaveTextContent("该版本是 v4 的来源版本");
    expect(
      mocks.request.mock.calls.filter(([config]) => config.method.toUpperCase() === "DELETE"),
    ).toHaveLength(0);
  });

  it("says so when nothing derives from the version", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "删除版本 v2" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("目前没有别的版本以它为来源");
  });

  it("drops the deleted version from the diff selection instead of keeping a dead id", async () => {
    const user = userEvent.setup();
    renderPage();

    // 默认对比基准是 v2（列表里第一个不是当前版本的），删掉它选择必须自己挪走。
    const baseSelect = await screen.findByRole("combobox", { name: "基准版本" });
    await waitFor(() => expect(baseSelect).toHaveValue("s2"));

    await user.click(screen.getByRole("button", { name: "删除版本 v2" }));
    await user.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith(
        expect.objectContaining({ url: "/api/v1/schedules/s2", method: "DELETE" }),
      ),
    );
    expect(mocks.success).toHaveBeenCalledWith("版本已删除");
    await waitFor(() => expect(screen.queryByRole("button", { name: "删除版本 v2" })).not.toBeInTheDocument());
    await waitFor(() => expect(baseSelect).toHaveValue("s3"));

    // 删除之后不能再对着已经没了的 s2 打 diff，否则页面会卡在 404 的错误态上。
    const deleteAt = mocks.request.mock.calls.findIndex(([config]) => config.method.toUpperCase() === "DELETE");
    const afterDelete = mocks.request.mock.calls.slice(deleteAt + 1).map(([config]) => String(config.url));
    expect(afterDelete.some((url) => url.startsWith("/api/v1/schedules/s2/diff/"))).toBe(false);
    await waitFor(() => expect(mocks.request).toHaveBeenLastCalledWith(expect.objectContaining({ url: "/api/v1/schedules/s3/diff/s1" })));
  });

  it("surfaces the server's refusal verbatim", async () => {
    const user = userEvent.setup();
    deleteRejection = { response: { data: { detail: "该版本是 v4 的来源版本，请先删除这些版本" } } };
    renderPage();

    await user.click(await screen.findByRole("button", { name: "删除版本 v3" }));
    await user.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() => expect(mocks.error).toHaveBeenCalledWith("该版本是 v4 的来源版本，请先删除这些版本"));
    expect(mocks.success).not.toHaveBeenCalled();
    // 被拒绝时确认框留在原地，用户能读完话术再取消，版本卡片也不能提前消失。
    expect(screen.getByRole("dialog")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(await screen.findByRole("button", { name: "删除版本 v3" })).toBeVisible();
  });

  it("renders the delete audit action in Chinese", async () => {
    renderPage();
    const auditRow = (await screen.findByText("s9")).closest("div");
    expect(auditRow).toHaveTextContent("删除");
    expect(auditRow).toHaveTextContent("课表版本");
  });
});
