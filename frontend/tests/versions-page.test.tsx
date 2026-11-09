import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VersionsPage, type VersionsPageProps } from "@/pages/versions-page";

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
const approver = { id: "approver-id", username: "approver_demo", role: "approver" as const };

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

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}{location.search}</div>;
}

function renderPage(user: typeof admin | typeof viewer | typeof scheduler | typeof approver = admin, props: VersionsPageProps = {}) {
  const scheduleAccessRole = user.role === "viewer"
    ? "viewer"
    : user.role === "scheduler"
      ? "scheduler"
      : "approver";
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/versions"]}>
        <Routes>
          <Route element={<Outlet context={{ user, scheduleAccessRole }} />}>
            <Route path="/versions" element={<VersionsPage {...props} />} />
          </Route>
          <Route path="*" element={<div>其它页面</div>} />
        </Routes>
        <LocationProbe />
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

describe("VersionsPage publishing and hub embedding", () => {
  afterEach(cleanup);

  const location = () => screen.getByTestId("location").textContent;
  const publishCalls = () =>
    mocks.request.mock.calls.filter(([config]) => config.method.toUpperCase() === "POST" && String(config.url).endsWith("/publish"));
  const scheduleListCalls = () => mocks.request.mock.calls.filter(([config]) => config.url === "/api/v1/schedules").length;

  beforeEach(() => {
    deleted = new Set();
    deleteRejection = undefined;
    mocks.request.mockReset();
    mocks.success.mockReset();
    mocks.error.mockReset();
    mocks.request.mockImplementation(async (config: { url: string; method: string }) => {
      const method = config.method.toUpperCase();
      if (method === "POST" || method === "DELETE") return undefined;
      if (config.url === "/api/v1/schedules") return schedules;
      if (config.url === "/api/v1/audit-logs") return auditLogs;
      if (config.url.includes("/diff/")) return diffPayload;
      return {};
    });
  });

  it("publishes a draft through the shared hook with its usual toast and cache refresh", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "发布版本 v2" }));
    // 点「发布此版本」只是弹出确认，不能直接发布。
    expect(publishCalls()).toHaveLength(0);
    expect(await screen.findByText("发布草稿 v2？")).toBeVisible();
    expect(screen.getByText("发布后成为当前课表，并同步到已启用的外部集成；不会自动下发日历。")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "确认发布" }));

    await waitFor(() => expect(publishCalls()).toHaveLength(1));
    expect(publishCalls()[0][0].url).toBe("/api/v1/schedules/s2/publish");
    await waitFor(() => expect(mocks.success).toHaveBeenCalledWith("版本已发布为当前课表，已触发发布数据同步"));
    // 发布后版本列表要重新拉取，页面才能看到新的当前版本。
    await waitFor(() => expect(scheduleListCalls()).toBeGreaterThan(1));
  });

  it("does not publish when the confirmation is cancelled", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "发布版本 v2" }));
    await user.click(await screen.findByRole("button", { name: "取消" }));

    await waitFor(() => expect(screen.queryByText("发布草稿 v2？")).not.toBeInTheDocument());
    expect(publishCalls()).toHaveLength(0);
  });

  it("asks before rolling back and says it replaces the current timetable", async () => {
    const user = userEvent.setup();
    const rollbackCalls = () =>
      mocks.request.mock.calls.filter(([config]) => config.method.toUpperCase() === "POST" && String(config.url).endsWith("/rollback"));
    renderPage(approver);

    await user.click(await screen.findByRole("button", { name: "回滚到版本 v3" }));
    expect(rollbackCalls()).toHaveLength(0);
    expect(await screen.findByText("回滚到版本 v3？")).toBeVisible();
    expect(screen.getByText(/替换现在使用中的课表.*不会自动下发日历/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "确认回滚" }));

    await waitFor(() => expect(rollbackCalls()).toHaveLength(1));
    expect(rollbackCalls()[0][0].url).toBe("/api/v1/schedules/s3/rollback");
  });

  it("reports a failed publish with the message from the server", async () => {
    const user = userEvent.setup();
    mocks.request.mockImplementation(async (config: { url: string; method: string }) => {
      if (config.method.toUpperCase() === "POST") return Promise.reject({ response: { data: { detail: "没有审批权限" } } });
      if (config.url === "/api/v1/schedules") return schedules;
      if (config.url === "/api/v1/audit-logs") return auditLogs;
      return diffPayload;
    });
    renderPage();

    await user.click(await screen.findByRole("button", { name: "发布版本 v2" }));
    await user.click(await screen.findByRole("button", { name: "确认发布" }));

    await waitFor(() => expect(mocks.error).toHaveBeenCalledWith("没有审批权限"));
    expect(mocks.success).not.toHaveBeenCalled();
  });

  it("lets an approver publish and roll back, but a scheduler only look", async () => {
    renderPage(approver);
    expect(await screen.findByRole("button", { name: "发布版本 v2" })).toBeVisible();
    expect(screen.getByRole("button", { name: "回滚到版本 v3" })).toBeVisible();

    cleanup();
    renderPage(scheduler);
    await screen.findByText("版本记录");
    expect(screen.queryByRole("button", { name: /^发布版本/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^回滚到版本/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新同步发布数据" })).not.toBeInTheDocument();
  });

  it("drops the page header, the process steps and the duplicate 查看课表 when embedded, keeping re-sync", async () => {
    renderPage(admin, { embedded: true });
    await screen.findByText("版本记录");

    expect(screen.queryByRole("heading", { name: "版本与回滚" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "排课流程" })).not.toBeInTheDocument();
    // 课表 tab 和版本卡上的「看课表」已经覆盖回课表，内嵌时不再单独放一个。
    expect(screen.queryByRole("button", { name: "查看课表" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新同步发布数据" })).toBeVisible();
    // 审计日志随内嵌保留。
    expect(screen.getByText("审计日志")).toBeVisible();
  });

  it("keeps the standalone header by default", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "版本与回滚", level: 1 })).toBeVisible();
    expect(screen.getByRole("button", { name: "查看课表" })).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "排课流程" })).not.toBeInTheDocument();
  });

  it("re-syncs the published data from the embedded toolbar", async () => {
    const user = userEvent.setup();
    renderPage(admin, { embedded: true });

    await user.click(await screen.findByRole("button", { name: "重新同步发布数据" }));

    await waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith(expect.objectContaining({ url: "/api/v1/integrations/feishu/sync-batch", method: "POST" })),
    );
  });

  it("highlights the selected version and compares it with its source version by default", async () => {
    renderPage(admin, { embedded: true, highlightId: "s4" });

    await screen.findByText("版本记录");
    expect(screen.getByTestId("version-card-s4")).toHaveAttribute("data-selected", "true");
    expect(screen.getByTestId("version-card-s2")).not.toHaveAttribute("data-selected");
    // s4 派生自 s3：对比默认是「s3 到 s4」，而不是随便一个别的版本。
    await waitFor(() => expect(screen.getByRole("combobox", { name: "目标版本" })).toHaveValue("s4"));
    expect(screen.getByRole("combobox", { name: "基准版本" })).toHaveValue("s3");
  });

  it("leaves the comparison alone once the user picked their own", async () => {
    const user = userEvent.setup();
    renderPage(admin, { embedded: true, highlightId: "s4" });
    const base = await screen.findByRole("combobox", { name: "基准版本" });
    await waitFor(() => expect(base).toHaveValue("s3"));

    await user.selectOptions(base, "s2");
    // 版本列表重新拉取（例如发布后）也不会把手动选的基准改回去。
    await user.click(screen.getByRole("button", { name: "发布版本 v2" }));
    await user.click(await screen.findByRole("button", { name: "确认发布" }));
    await waitFor(() => expect(scheduleListCalls()).toBeGreaterThan(1));
    expect(base).toHaveValue("s2");
  });

  it("goes back to the schedule view on the highlighted version", async () => {
    const user = userEvent.setup();
    renderPage(admin, { embedded: true, highlightId: "s4" });

    await user.click(await screen.findByRole("button", { name: "查看版本 v4 的课表" }));

    expect(location()).toBe("/schedule?version=s4");
  });

  it("opens any version's timetable straight from its card", async () => {
    const user = userEvent.setup();
    renderPage(viewer);

    await user.click(await screen.findByRole("button", { name: "查看版本 v3 的课表" }));

    expect(location()).toBe("/schedule?version=s3");
  });
});

describe("VersionsPage says each fact once", () => {
  afterEach(cleanup);

  let listed: Array<Record<string, unknown>>;
  let logs: Array<Record<string, unknown>>;
  let diff: Record<string, unknown>;

  const diffItem = (index: number, kind = "moved") => ({
    course_business_id: `COURSE-${index}`,
    class_business_id: `${index}班`,
    teacher_business_id: "王老师",
    before_lesson_date: "2026-11-25",
    after_lesson_date: "2026-11-24",
    before_slot_id: "SLOT-周三-1830-2130",
    after_slot_id: "SLOT-周二-1830-2130",
    before_room_id: "510教室",
    after_room_id: "503教室",
    change_kind: kind,
  });

  beforeEach(() => {
    listed = [
      { ...schedules[0], id: "s1", version_no: 1, name: "郑州校区官方原始课表", status: "archived" },
      { ...schedules[1], id: "s3", version_no: 3, name: "课表版本 V3", status: "published", parent_id: "s1" },
      { ...schedules[1], id: "s4", version_no: 4, name: "课表版本 V4", status: "draft", parent_id: "s3" },
    ];
    logs = auditLogs;
    diff = { changed_count: 1, unchanged_count: 4543, items: [diffItem(1)] };
    mocks.request.mockReset();
    mocks.success.mockReset();
    mocks.error.mockReset();
    mocks.request.mockImplementation(async (config: { url: string; method: string }) => {
      if (config.method.toUpperCase() !== "GET") return undefined;
      if (config.url === "/api/v1/schedules") return listed;
      if (config.url === "/api/v1/audit-logs") return logs;
      if (config.url.includes("/diff/")) return diff;
      return {};
    });
  });

  it("hides auto-generated version names, keeps custom ones and marks the in-use version once", async () => {
    renderPage(admin, { embedded: true });
    await screen.findByText("版本记录");

    expect(screen.queryByText("课表版本 V3")).not.toBeInTheDocument();
    expect(screen.queryByText("课表版本 V4")).not.toBeInTheDocument();
    expect(screen.getByText("郑州校区官方原始课表")).toBeVisible();
    // 版本卡上只剩右上角一个「当前使用中」，不再另挂「当前生效版本」。
    expect(within(screen.getByTestId("version-card-s3")).getAllByText("当前使用中")).toHaveLength(1);
    expect(screen.queryByText("当前生效版本")).not.toBeInTheDocument();
    // 删除是一个图标按钮，名字仍说清删哪个版本。
    const remove = screen.getByRole("button", { name: "删除版本 v4" });
    expect(remove).toHaveAttribute("title", "删除");
    expect(remove).not.toHaveTextContent("删除");
  });

  it("keeps the sync note to one line for managers and shows non-managers no permission cards", async () => {
    renderPage(admin, { embedded: true });
    expect(await screen.findByText(/发布或回滚后会自动同步到已启用的外部集成；同步失败不影响本地版本/)).toBeVisible();

    cleanup();
    renderPage(viewer, { embedded: true });
    await screen.findByText("版本记录");
    expect(screen.queryByText(/自动同步到已启用的外部集成/)).not.toBeInTheDocument();
    expect(screen.queryByText(/变更操作仅由具备相应权限的账号执行/)).not.toBeInTheDocument();
    expect(screen.queryByText(/审计日志仅管理员可见/)).not.toBeInTheDocument();
    expect(screen.queryByText("审计日志")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新同步发布数据" })).not.toBeInTheDocument();
  });

  it("only offers diff search and type filter for more than 20 changes", async () => {
    const user = userEvent.setup();
    renderPage(admin, { embedded: true, highlightId: "s4" });
    expect(await screen.findByText(/^变更:/)).toBeInTheDocument();
    await screen.findByText("1班");
    expect(screen.queryByRole("textbox", { name: "搜索差异" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "变更类型" })).not.toBeInTheDocument();

    cleanup();
    diff = { changed_count: 21, unchanged_count: 4523, items: Array.from({ length: 21 }, (_, index) => diffItem(index + 1, index === 0 ? "room_changed" : "moved")) };
    renderPage(admin, { embedded: true, highlightId: "s4" });
    const search = await screen.findByRole("textbox", { name: "搜索差异" });
    expect(screen.queryByText(/条差异/)).not.toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "变更类型" }), "room_changed");
    expect(screen.getByText("共 1 条差异")).toBeVisible();
    expect(search).toBeVisible();
  });

  it("says one line per audit entry, shows a failure on its own in red and keeps the raw record collapsed", async () => {
    logs = [
      {
        id: "log-sync",
        action: "sync",
        resource_type: "feishu",
        resource_id: "ec777cc9-4eac-46a9-9099-4ff07f3e6fe9",
        user_id: "admin-id",
        detail: { trigger: "version_publish", retry_count: 0, error: "飞书令牌密文校验失败，请重新授权" },
        created_at: "2026-11-09T13:03:00Z",
      },
      {
        id: "log-context",
        action: "update_context",
        resource_type: "solve_goal",
        resource_id: "2e60acb7-4026-47a0-862f-b86a3a71e0a6",
        user_id: "admin-id",
        detail: { scope: { date_from: "2026-11-23", date_to: "2026-11-29" }, task_constraint_rule_ids: ["TASK-2e60acb7-forbidden_slot_free-1"] },
        created_at: "2026-11-09T13:04:00Z",
      },
    ];
    renderPage(admin, { embedded: true });

    expect(await screen.findByText("发布后同步")).toBeVisible();
    const failure = screen.getByText("飞书令牌密文校验失败，请重新授权");
    expect(failure).toBeVisible();
    expect(failure).toHaveClass("text-red-700");
    expect(screen.getByText("范围 2026-11-23 ~ 2026-11-29 · 1 条本次要求")).toBeVisible();
    expect(screen.getByText("更新任务范围")).toBeVisible();
    expect(screen.getByText("排课任务")).toBeVisible();
    // 资源列只显示首尾片段，完整 ID 可复制。
    expect(screen.getByRole("button", { name: "复制完整 ID 2e60acb7-4026-47a0-862f-b86a3a71e0a6" })).toBeInTheDocument();
    // 原始 JSON 收进默认收起的「原始记录」里。
    expect(screen.getAllByText("原始记录")).toHaveLength(2);
    expect(screen.getByText(/"task_constraint_rule_ids"/)).not.toBeVisible();
    expect(screen.queryByText("update_context")).not.toBeInTheDocument();
  });

  it("keeps calendar conflicts and dropped import rows visible next to the summary, with Chinese action names", async () => {
    logs = [
      {
        id: "log-calendar",
        action: "calendar_publish",
        resource_type: "schedule",
        resource_id: "s4",
        user_id: "admin-id",
        detail: { published: 12, conflicts: 2 },
        created_at: "2026-11-09T13:03:00Z",
      },
      {
        id: "log-import",
        action: "import_xlsx",
        resource_type: "workbook",
        resource_id: "排课.xlsx",
        user_id: "admin-id",
        detail: { rows_total: 30, rows_dropped_placeholder_room: 4, course_sessions_created: 26 },
        created_at: "2026-11-09T13:04:00Z",
      },
      {
        id: "log-delete",
        action: "batch_delete",
        resource_type: "teacher",
        resource_id: null,
        user_id: "admin-id",
        detail: { count: 500 },
        created_at: "2026-11-09T13:05:00Z",
      },
    ];
    renderPage(admin, { embedded: true });

    expect(await screen.findByText("下发 12 条")).toBeVisible();
    expect(screen.getByText("冲突 2 条")).toHaveClass("text-amber-700");
    expect(screen.getByText("新建 26 课次")).toBeVisible();
    expect(screen.getByText("整行丢弃 4 行（占位教室）")).toHaveClass("text-amber-700");
    expect(screen.getByText("删除 500 条")).toBeVisible();
    for (const label of ["下发日历", "导入工作簿", "批量删除"]) expect(screen.getByText(label)).toBeVisible();
    for (const code of ["calendar_publish", "import_xlsx", "batch_delete"]) expect(screen.queryByText(code)).not.toBeInTheDocument();
  });
});
