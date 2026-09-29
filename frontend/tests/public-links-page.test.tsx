import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PublicLinksPage, type PublicLinksPageProps } from "@/pages/public-links-page";

// 页面走 orval 生成的客户端，统一经过 customInstance，因此在这一层拦截。
const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}));

vi.mock("@/api/http", () => ({
  customInstance: mocks.request,
  http: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  authStore: { get: vi.fn(), set: vi.fn(), clear: vi.fn() },
  API_BASE_URL: "http://127.0.0.1:8000",
  scheduleSetStore: { get: vi.fn(), set: vi.fn(), clear: vi.fn() },
}));

vi.mock("sonner", () => ({ toast: { success: mocks.success, error: mocks.error, warning: mocks.warning } }));

const admin = { id: "admin-id", username: "admin", role: "admin" as const };

const campuses = [{ id: "c1", business_id: "CAMPUS-1", name: "郑州校区" }];
const classGroups = [{ id: "cg1", campus_id: "c1", business_id: "CLASS-3-2", name: "三年二班", session_count: 120 }];
const teachers = [{ id: "t1", campus_id: "c1", business_id: "T-001", name: "王老师" }];

const existingLink = {
  id: "lk-1",
  schedule_set_id: "set-1",
  scope: "class",
  campus_id: "c1",
  resource_business_id: "CLASS-3-2",
  display_name: "三年二班",
  show_teacher_names: true,
  token_hint: "x9Yz",
  status: "active",
  expires_at: "2027-03-01T23:59:59+08:00",
  last_seen_at: "2026-09-19T10:00:00Z",
  access_count: 42,
  note: "发到家长群",
  created_at: "2026-09-01T08:00:00Z",
  created_by: "admin-id",
};

let links: Array<Record<string, unknown>>;
let createdBodies: Array<Record<string, unknown>>;
let revoked: Set<string>;

function spyClipboard() {
  // userEvent.setup() 会把 navigator.clipboard 换成它自己的 stub，spy 只能打在 stub 上。
  return vi.spyOn(window.navigator.clipboard, "writeText");
}

function secretResponse(overrides: Record<string, unknown> = {}) {
  return {
    ...existingLink,
    ...overrides,
    token: "plaintext-token",
    public_url: `http://school.example.com/public/t/${overrides.token_hint ?? "x9Yz"}-secret`,
  };
}

function renderPage(props: PublicLinksPageProps = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/public-links"]}>
        <Routes>
          <Route
            element={<Outlet context={{ user: admin, scheduleAccessRole: "approver", scheduleSet: { id: "set-1", name: "主方案" } }} />}
          >
            <Route path="/public-links" element={<PublicLinksPage {...props} />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function fillCreateDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /新建公开链接/ }));
  const dialog = screen.getByRole("dialog");
  await user.selectOptions(within(dialog).getByLabelText("校区"), "c1");
  await user.selectOptions(within(dialog).getByLabelText("选择班级"), "CLASS-3-2");
  return dialog;
}

describe("PublicLinksPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    links = [{ ...existingLink }];
    createdBodies = [];
    revoked = new Set();
    mocks.request.mockReset();
    mocks.success.mockReset();
    mocks.error.mockReset();
    mocks.warning.mockReset();
    mocks.request.mockImplementation(async (config: { url: string; method: string; data?: unknown }) => {
      const method = config.method.toUpperCase();
      if (config.url === "/api/v1/campuses") return campuses;
      if (config.url === "/api/v1/class-groups") return classGroups;
      if (config.url === "/api/v1/teachers") return teachers;
      if (config.url === "/api/v1/schedule-sets/set-1/public-links") {
        if (method === "GET") return links.filter((item) => !revoked.has(item.id as string));
        if (method === "POST") {
          createdBodies.push(config.data as Record<string, unknown>);
          const created = secretResponse({ id: `lk-new-${createdBodies.length}`, token_hint: `nw${createdBodies.length}` });
          links = [...links, created];
          return created;
        }
      }
      if (config.url === "/api/v1/public-links/lk-1/rotate" && method === "POST") {
        // 与后端一致：旧链接立即 revoked，轮换出的新链接置于列表最前。
        links = [
          secretResponse({ id: "lk-rotated", token_hint: "ro1t" }),
          ...links.map((item) => (item.id === "lk-1" ? { ...item, status: "revoked" } : item)),
        ];
        return links[0];
      }
      if (config.url.startsWith("/api/v1/public-links/") && method === "DELETE") {
        revoked.add(config.url.replace("/api/v1/public-links/", ""));
        return undefined;
      }
      return {};
    });
  });

  it("lists links with scope, status and access stats", async () => {
    renderPage();
    expect(await screen.findByText("三年二班")).toBeInTheDocument();
    expect(screen.getByText("班级")).toBeInTheDocument();
    expect(screen.getByText("生效")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    // 明文链接无法回查：未在本次会话创建/轮换过的行不提供分享入口。
    expect(screen.getByRole("button", { name: /分享/ })).toBeDisabled();
  });

  it("creates a link and shows the plaintext url exactly once", async () => {
    const user = userEvent.setup();
    const writeText = spyClipboard();
    renderPage();
    await fillCreateDialog(user);

    await user.click(screen.getByRole("button", { name: "创建" }));

    expect(createdBodies).toHaveLength(1);
    expect(createdBodies[0]).toMatchObject({
      scope: "class",
      campus_id: "c1",
      resource_business_id: "CLASS-3-2",
      show_teacher_names: true,
    });
    expect(String(createdBodies[0].expires_at)).toContain("+08:00");

    const secretDialog = await screen.findByRole("dialog");
    const secretUrl = within(secretDialog).getByTestId("public-secret-url");
    expect(secretUrl).toHaveTextContent("http://school.example.com/public/t/nw1-secret");
    expect(screen.getByText(/关闭后无法再次查看/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /复制链接/ }));
    expect(writeText).toHaveBeenCalledWith("http://school.example.com/public/t/nw1-secret");

    // 关闭后明文不再出现在页面任何位置。
    await user.click(screen.getByRole("button", { name: "我已保存，关闭" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.queryByTestId("public-secret-url")).not.toBeInTheDocument();
    expect(screen.queryByText(/nw1-secret/)).not.toBeInTheDocument();
  });

  it("shares a fresh secret through the QR dialog", async () => {
    const user = userEvent.setup();
    renderPage();
    await fillCreateDialog(user);

    await user.click(screen.getByRole("button", { name: "创建" }));
    await user.click(await screen.findByRole("button", { name: "分享（二维码）" }));

    expect(screen.getByTestId("public-share-qr").querySelector("svg")).not.toBeNull();
    expect(screen.getByLabelText("H5 页面链接")).toHaveValue("http://school.example.com/public/t/nw1-secret");
    expect(screen.getByLabelText("日历订阅（webcal）")).toHaveValue(
      "webcal://127.0.0.1:8000/api/v1/public/links/nw1-secret/calendar.ics",
    );
  });

  it("rotates a link after confirming the old one dies immediately", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /轮换/ }));
    expect(screen.getByRole("dialog")).toHaveTextContent("旧链接立即失效");

    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "轮换" }));
    expect(await screen.findByTestId("public-secret-url")).toHaveTextContent("http://school.example.com/public/t/ro1t-secret");
    expect(screen.getByText("链接已轮换")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "我已保存，关闭" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // 旧行回到已停用；只有本次会话拿到明文的新行可分享。
    await screen.findByText("已停用");
    const shareButtons = screen.getAllByRole("button", { name: /分享/ });
    expect(shareButtons.filter((button) => !button.hasAttribute("disabled"))).toHaveLength(1);
  });

  it("revokes a link after confirmation", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /停用/ }));
    expect(screen.getByRole("dialog")).toHaveTextContent("立即返回 404");

    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "停用" }));
    await waitFor(() => expect(revoked.has("lk-1")).toBe(true));
    await waitFor(() => expect(screen.queryByText("生效")).not.toBeInTheDocument());
    expect(screen.getByText(/还没有公开链接/)).toBeInTheDocument();
  });

  it("requires campus and resource before creating", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /新建公开链接/ }));
    expect(screen.getByRole("button", { name: "创建" })).toBeDisabled();
    expect(createdBodies).toHaveLength(0);
  });

  it("keeps its own page title when standalone and drops it when embedded", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "公开链接", level: 1 })).toBeInTheDocument();

    cleanup();
    renderPage({ embedded: true });
    expect(await screen.findByText("三年二班")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "公开链接" })).not.toBeInTheDocument();
    // 创建入口和说明搬进了内嵌工具条，功能不减。
    expect(screen.getByText(/免登录课表页与日历订阅的凭证链接/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新建公开链接/ })).toBeEnabled();
  });

  it("creates a link from the embedded toolbar exactly like the standalone page", async () => {
    const user = userEvent.setup();
    renderPage({ embedded: true });
    await fillCreateDialog(user);

    await user.click(screen.getByRole("button", { name: "创建" }));

    expect(createdBodies).toHaveLength(1);
    expect(createdBodies[0]).toMatchObject({ scope: "class", campus_id: "c1", resource_business_id: "CLASS-3-2" });
    expect(await screen.findByTestId("public-secret-url")).toHaveTextContent("http://school.example.com/public/t/nw1-secret");
  });

  it("disables link creation with the given reason, e.g. before anything is published", async () => {
    const user = userEvent.setup();
    renderPage({ embedded: true, createDisabledReason: "还没有已发布的课表版本，先发布后再创建公开链接" });

    const create = await screen.findByRole("button", { name: /新建公开链接/ });
    expect(create).toBeDisabled();
    expect(create).toHaveAttribute("title", "还没有已发布的课表版本，先发布后再创建公开链接");
    // 已有链接的轮换/停用不受影响。
    await user.click(screen.getByRole("button", { name: /轮换/ }));
    expect(screen.getByRole("dialog")).toHaveTextContent("旧链接立即失效");
  });
});
