import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryPage } from "@/pages/memory-page";

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

const slot = { id: "slot-1", business_id: "SLOT-周三-1900-2030", weekday: "周三", start_time: "19:00", end_time: "20:30", kind: "evening", is_open: true };

// 一条挖掘候选（probation）+ 一条已确认 + 一条已拒绝：覆盖收件箱与全部偏好表。
const entries = [
  {
    id: "pref-1",
    schedule_set_id: "set-1",
    subject_type: "teacher",
    subject_id: "T-001",
    predicate: "avoid_slot",
    constraint: { slot_ids: [slot.business_id] },
    modality: "soft",
    confidence: 0.5,
    source: "induced_from_adjustment",
    evidence: ["evt-1", "evt-2", "evt-3"],
    weight: 40,
    status: "probation",
    valid_from: "2026-09-01",
    valid_until: "2027-02-28",
    provenance: { origin: "mining", rationale: "该教师多次在周三晚间调课" },
    created_at: "2026-09-10T01:00:00Z",
    updated_at: "2026-09-10T01:00:00Z",
  },
  {
    id: "pref-2",
    schedule_set_id: "set-1",
    subject_type: "classroom",
    subject_id: "教室-301",
    predicate: "avoid_room",
    constraint: { room_ids: ["教室-301"] },
    modality: "soft",
    confidence: 0.9,
    source: "admin_directive",
    evidence: [],
    weight: 60,
    status: "confirmed",
    valid_from: "2026-09-01",
    valid_until: "2027-02-28",
    provenance: { origin: "api" },
    created_at: "2026-09-01T01:00:00Z",
    updated_at: "2026-09-02T01:00:00Z",
  },
  {
    id: "pref-3",
    schedule_set_id: "set-1",
    subject_type: "teacher",
    subject_id: "T-001",
    predicate: "prefer_slot",
    constraint: { slot_ids: [slot.business_id] },
    modality: "soft",
    confidence: 0.4,
    source: "induced_from_adjustment",
    evidence: ["evt-9"],
    weight: 30,
    status: "rejected",
    valid_from: "2026-09-01",
    valid_until: "2027-02-28",
    provenance: { origin: "mining" },
    created_at: "2026-08-30T01:00:00Z",
    updated_at: "2026-08-31T01:00:00Z",
  },
  // MEM-C1：hard 条目走「转为硬规则」；偏好库只管软偏好。
  {
    id: "pref-hard",
    schedule_set_id: "set-1",
    subject_type: "classroom",
    subject_id: "教室-301",
    predicate: "avoid_room",
    constraint: { room_ids: ["教室-301"] },
    modality: "hard",
    confidence: 0.9,
    source: "admin_directive",
    evidence: [],
    weight: 60,
    status: "confirmed",
    valid_from: "2026-09-01",
    valid_until: "2027-02-28",
    provenance: { origin: "api" },
    created_at: "2026-09-03T01:00:00Z",
    updated_at: "2026-09-03T01:00:00Z",
  },
];

const miningResponse = { engine: "deterministic", events_scanned: 4, created: [{ ...entries[0] }, { ...entries[0], id: "pref-new" }], skipped_existing: 0 };

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/memory"]}>
        <Routes>
          <Route element={<Outlet context={{ user: admin, scheduleAccessRole: "approver" }} />}>
            <Route path="/memory" element={<MemoryPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("MemoryPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.request.mockReset();
    mocks.success.mockReset();
    mocks.error.mockReset();
    mocks.request.mockImplementation(async (config: { url: string; method?: string }) => {
      const method = (config.method ?? "GET").toUpperCase();
      if (config.url === "/api/v1/memory/preferences" && method === "GET") return entries;
      if (config.url === "/api/v1/teachers") return [{ id: "t1", business_id: "T-001", name: "张老师", subject: "数学" }];
      if (config.url === "/api/v1/rooms") return [{ id: "r1", business_id: "教室-301", name: "301 教室" }];
      if (config.url === "/api/v1/class-groups") return [];
      if (config.url === "/api/v1/course-sessions") return [];
      if (config.url === "/api/v1/time-slots") return [slot];
      if (config.url === "/api/v1/solver-runs") {
        return [{
          id: "run-1",
          status: "completed",
          memory_usage: {
            status: "ok",
            summary: { considered: 1, applied: 1, unused: 0 },
            outcomes: [{ entry_id: "pref-2", outcome: "applied", detail: "以权重 54 参与求解" }],
          },
        }];
      }
      if (config.url === "/api/v1/memory/mining-runs") return miningResponse;
      if (config.url.startsWith("/api/v1/memory/preferences/")) return entries[0];
      return [];
    });
  });

  it("renders the probation inbox card with resolved names, summary and evidence count", async () => {
    renderPage();

    // 主体 business_id 被翻译成了主数据里的人名；时段 id 被翻译成可读时段。
    // 张老师同时出现在收件箱卡片与偏好表里，因此允许多个匹配。
    const teacherNames = await screen.findAllByText("张老师");
    expect(teacherNames.length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("避开时段").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("「周三 19:00-20:30」")).toBeVisible();
    expect(screen.getByText("来源：调课挖掘")).toBeVisible();
    expect(screen.getByText("置信度 50%")).toBeVisible();
    expect(screen.getByText("来自 3 次调课")).toBeVisible();
    expect(screen.getByText("该教师多次在周三晚间调课")).toBeVisible();
    // MEM-C1：候选在采纳或授权试用前不影响排课——页面文案必须如实承诺。
    expect(screen.getByText(/待确认候选在您采纳或授权试用前不会影响排课/)).toBeVisible();
    // 全部偏好表同时渲染：最近使用列来自最近求解任务的 memory_usage 反查。
    expect(screen.getAllByText("301 教室").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("button", { name: "停用" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("最近使用")).toBeVisible();
    expect(screen.getByText("已应用")).toBeVisible();
  });

  it("authorizes a trial with the default 30 days and keeps the entry on probation", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "授权试用" }));
    expect(screen.getByLabelText("试用天数")).toHaveValue(30);
    await user.click(screen.getByRole("button", { name: "确认授权" }));

    await waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith(
        expect.objectContaining({
          url: "/api/v1/memory/preferences/pref-1/transition",
          method: "POST",
          data: { action: "authorize_trial", trial_days: 30 },
        }),
      ),
    );
    expect(mocks.success).toHaveBeenCalledWith("已授权试用 30 天，试用期内以小权重参与排课");
  });

  it("converts a hard preference into a formal rule after explicit confirmation", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "转为硬规则" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("把这条硬偏好转成正式规则？");

    await user.click(screen.getByRole("button", { name: "确认转换" }));
    await waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith(
        expect.objectContaining({
          url: "/api/v1/memory/preferences/pref-hard/convert-to-rule",
          method: "POST",
          data: { confirmed_conversion: false },
        }),
      ),
    );
    expect(mocks.success).toHaveBeenCalledWith("已转为正式硬规则，原偏好条目归档为已失效");
  });

  it("adopts a candidate by calling the transition endpoint with confirmed", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "采纳" }));

    await waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith(
        expect.objectContaining({
          url: "/api/v1/memory/preferences/pref-1/transition",
          method: "POST",
          data: { target_status: "confirmed" },
        }),
      ),
    );
    expect(mocks.success).toHaveBeenCalledWith("已采纳该偏好，下次求解开始生效");
  });

  it("gates rejection behind a confirm dialog and does not transition until confirmed", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "拒绝" }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("拒绝这条候选偏好？");
    expect(
      mocks.request.mock.calls.filter(([config]) => String(config.url).includes("/transition")),
    ).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "确认拒绝" }));
    await waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith(
        expect.objectContaining({
          url: "/api/v1/memory/preferences/pref-1/transition",
          method: "POST",
          data: { target_status: "rejected" },
        }),
      ),
    );
  });

  it("runs preference mining and toasts the number of new candidates", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "回顾本学期调课" }));

    await waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith(
        expect.objectContaining({ url: "/api/v1/memory/mining-runs", method: "POST" }),
      ),
    );
    await waitFor(() => expect(mocks.success).toHaveBeenCalledWith("挖掘出 2 条候选偏好"));
  });
});
