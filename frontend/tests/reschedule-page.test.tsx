import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReschedulePage } from "@/pages/reschedule-page";

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

const schedules = [{ id: "s1", version_no: 1, name: "基线课表", status: "published", parent_id: null, solver_run_id: "run-1", metrics: {}, assignment_count: 10, published_at: "2026-08-01T02:00:00Z", created_at: "2026-08-01T01:00:00Z" }];
const teachers = [{ id: "t1", business_id: "T-001", name: "张老师" }];
const rooms = [{ id: "r1", business_id: "教室-301", name: "301 教室" }];
const slot = { id: "slot-1", business_id: "SLOT-周三-1900-2030", weekday: "周三", start_time: "19:00", end_time: "20:30" };

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/reschedule"]}>
        <Routes>
          <Route element={<Outlet context={{ user: admin, scheduleAccessRole: "approver" }} />}>
            <Route path="/reschedule" element={<ReschedulePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function createCalls() {
  return mocks.request.mock.calls.filter(([config]) => config.url === "/api/v1/reschedule-events" && config.method === "POST");
}

describe("ReschedulePage declared reason chips", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.request.mockReset();
    mocks.success.mockReset();
    mocks.error.mockReset();
    mocks.request.mockImplementation(async (config: { url: string; method?: string }) => {
      const method = (config.method ?? "GET").toUpperCase();
      if (config.url === "/api/v1/reschedule-events" && method === "POST") return { id: "evt-1", event_type: "teacher_leave", status: "pending" };
      if (config.url === "/api/v1/reschedule-events") return [];
      if (config.url === "/api/v1/schedules") return schedules;
      if (config.url === "/api/v1/teachers") return teachers;
      if (config.url === "/api/v1/rooms") return rooms;
      if (config.url === "/api/v1/time-slots") return [slot];
      return [];
    });
  });

  it("submits without a declared reason when no chip is selected", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "生成候选方案" }));

    await waitFor(() => expect(createCalls()).toHaveLength(1));
    const [config] = createCalls()[0];
    expect(config.data).toMatchObject({ event_type: "teacher_leave", declared_reason: null });
  });

  it("writes the selected chip label into declared_reason and clears it after submit", async () => {
    const user = userEvent.setup();
    renderPage();

    const chip = await screen.findByRole("button", { name: "教室冲突" });
    await user.click(chip);
    expect(chip).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "生成候选方案" }));

    await waitFor(() => expect(createCalls()).toHaveLength(1));
    const [config] = createCalls()[0];
    expect(config.data).toMatchObject({ declared_reason: "教室冲突" });
    // 提交成功后原因行复位，避免下一次调课无意沿用上一次的归因。
    await waitFor(() => expect(screen.getByRole("button", { name: "教室冲突" })).toHaveAttribute("aria-pressed", "false"));
  });

  it("deselects a chip on second click so the reason is optional and reversible", async () => {
    const user = userEvent.setup();
    renderPage();

    const chip = await screen.findByRole("button", { name: "教师要求" });
    await user.click(chip);
    await user.click(chip);
    expect(chip).toHaveAttribute("aria-pressed", "false");

    await user.click(screen.getByRole("button", { name: "生成候选方案" }));

    await waitFor(() => expect(createCalls()).toHaveLength(1));
    const [config] = createCalls()[0];
    expect(config.data).toMatchObject({ declared_reason: null });
  });

  it("uses the typed text as declared reason when the other chip is selected", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "其他" }));
    const input = screen.getByRole("textbox", { name: "其他调课原因" });
    await user.type(input, "投影仪检修");

    await user.click(screen.getByRole("button", { name: "生成候选方案" }));

    await waitFor(() => expect(createCalls()).toHaveLength(1));
    const [config] = createCalls()[0];
    expect(config.data).toMatchObject({ declared_reason: "投影仪检修" });
  });
});
