import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/app/app-shell";
import { AssistantPage } from "@/pages/assistant-page";
import "./support/data-router-shim";
import {
  assistantMocks as mocks,
  interpretationFixture,
  mockAiConfigured,
  resetAssistantMocks,
  runFixture,
} from "./support/assistant-mocks";

vi.mock("@/api/generated/client", async () => (await import("./support/assistant-mocks")).clientMock);
// 助手页只用 http.get/post（mock）；AppShell 还要真实的 scheduleSetStore 等导出。
vi.mock("@/api/http", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/http")>()),
  ...(await import("./support/assistant-mocks")).httpMock,
}));
vi.mock("@/lib/interpret-stream", async () => (await import("./support/assistant-mocks")).interpretStreamMock);

const { listScheduleSets } = vi.hoisted(() => ({ listScheduleSets: vi.fn() }));
vi.mock("@/api/schedule-sets", () => ({
  scheduleSetApi: { list: listScheduleSets, create: vi.fn(), rename: vi.fn() },
}));

const PARSE = "让 AI 解析";
const CONFIRM = /确认并开始求解/;

/**
 * 与线上一致的壳：AuthBoundary 提供 user，真实的 AppShell（侧栏、品牌链接）包着真实的助手页。
 * 「回到助手首页」要靠侧栏/品牌链接这条真实的导航，而不是测试里手动 navigate。
 */
async function renderInShell() {
  listScheduleSets.mockResolvedValue([{ id: "set-1", code: "main", name: "主课表", display_order: 1, is_active: true, access_role: "approver" }]);
  const user = { id: "admin-id", username: "admin", role: "admin", is_active: true, created_at: "2026-01-01T00:00:00Z" };
  const router = createMemoryRouter(
    [{ element: <Outlet context={{ user }} />, children: [{ element: <AppShell />, children: [{ path: "/assistant", element: <AssistantPage /> }, { path: "*", element: <div>其它页面</div> }] }] }],
    { initialEntries: ["/assistant"] },
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await screen.findAllByRole("combobox", { name: "当前课表方案" });
  return router;
}

const sidebarEntry = () => within(screen.getByRole("navigation", { name: "主导航" })).getByRole("link", { name: "排课助手" });
const brandLink = () => screen.getByRole("link", { name: "途排智策" });

beforeEach(() => {
  resetAssistantMocks();
  window.localStorage.clear();
  listScheduleSets.mockReset();
});
afterEach(cleanup);

// 审查 #6：确认卡（还没有持久任务）与没关联任务的手动结果，URL 里都没有 goal / run，
// 点侧栏「排课助手」或品牌链接地址不变，也必须回到空白的需求输入与任务列表。
describe("going back to the assistant home from the sidebar or the brand link", () => {
  it.each([
    ["the sidebar entry", sidebarEntry],
    ["the brand link", brandLink],
  ])("clears a parsed confirmation card that has no goal or run in the URL (%s)", async (_name, link) => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture());
    const router = await renderInShell();

    await user.type(await screen.findByLabelText("排课需求"), "请在三天内重排考研课程");
    await user.click(screen.getByRole("button", { name: PARSE }));
    expect(await screen.findByRole("button", { name: CONFIRM })).toBeInTheDocument();
    // 前提：确认卡阶段 URL 里确实什么参数都没有——单靠 goal/run 是否变化推断不出要回首页。
    expect(router.state.location.pathname + router.state.location.search).toBe("/assistant");

    await user.click(link());

    expect(await screen.findByLabelText("排课需求")).toHaveValue("");
    expect(screen.queryByRole("button", { name: CONFIRM })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("任务进展")).not.toBeInTheDocument();
    expect(screen.getByLabelText("正在处理的任务")).toBeInTheDocument();
  });

  it.each([
    ["the sidebar entry", sidebarEntry],
    ["the brand link", brandLink],
  ])("clears a manual solve result that is not linked to any task (%s)", async (_name, link) => {
    const user = userEvent.setup();
    mocks.submitResult = { id: "run-m", status: "completed", model_status: "OPTIMAL" };
    mocks.runDetails["run-m"] = runFixture({ id: "run-m" });
    await renderInShell();

    // AI 未配置时首页默认展开手动排课。
    await user.click(await screen.findByRole("button", { name: /按参数开始求解/ }));
    expect(await screen.findByRole("heading", { name: /已生成草稿/ })).toBeInTheDocument();
    expect(screen.getByLabelText("任务进展")).toBeInTheDocument();

    await user.click(link());

    await waitFor(() => expect(screen.queryByLabelText("任务进展")).not.toBeInTheDocument());
    expect(await screen.findByLabelText("排课需求")).toHaveValue("");
    expect(screen.queryByRole("heading", { name: /已生成草稿/ })).not.toBeInTheDocument();
  });

  it("does not wipe the task on ordinary URL updates, only on the explicit home navigation", async () => {
    const user = userEvent.setup();
    mockAiConfigured();
    mocks.stream.mockResolvedValue(interpretationFixture());
    mocks.post.mockImplementation(async (...args: unknown[]) => {
      if (String(args[0]) === "/api/v1/goals") return { data: { id: "goal-n", status: "open", checklist: [] } };
      return { data: { id: "run-n", status: "queued", model_status: null, goal_id: "goal-n" } };
    });
    const router = await renderInShell();

    await user.type(await screen.findByLabelText("排课需求"), "请在三天内重排考研课程");
    await user.click(screen.getByRole("button", { name: PARSE }));
    await user.click(await screen.findByRole("button", { name: CONFIRM }));
    // 新建任务后自动把 goal 写进 URL：这不是「回首页」，当前任务必须留着。
    await waitFor(() => expect(router.state.location.search).toBe("?goal=goal-n"));
    expect(await screen.findByLabelText("任务进展")).toBeInTheDocument();

    await user.click(sidebarEntry());

    await waitFor(() => expect(screen.queryByLabelText("任务进展")).not.toBeInTheDocument());
    expect(await screen.findByLabelText("排课需求")).toHaveValue("");
    expect(router.state.location.search).toBe("");
  });
});
