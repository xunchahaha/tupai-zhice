import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReschedulePage, type ReschedulePageProps } from "@/pages/reschedule-page";

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
const teachers = [{ id: "t1", business_id: "T-001", name: "张老师" }, { id: "t2", business_id: "T-002", name: "李老师" }];
const rooms = [{ id: "r1", business_id: "教室-301", name: "301 教室" }, { id: "r2", business_id: "教室-302", name: "302 教室" }];
const slot = { id: "slot-1", business_id: "SLOT-周三-1900-2030", weekday: "周三", start_time: "19:00", end_time: "20:30" };
const slotThursday = { id: "slot-2", business_id: "SLOT-周四-0900-1030", weekday: "周四", start_time: "09:00", end_time: "10:30" };
const draft = { ...schedules[0], id: "s2", version_no: 2, name: "求解版本 v2", status: "draft", parent_id: "s1", published_at: null };

// 生成候选方案时后端返回的草稿 id；null 表示这次没有产出候选。
let candidateScheduleId: string | null;
let events: Array<Record<string, unknown>>;
// 课表详情里的课次：登记成事件时用来数「会有多少课次被卷进来」。
let scheduleAssignments: Array<Record<string, unknown>>;
// 创建接口返回的事件：默认是后台已算完的候选事件；慢求解/无解/失败用例改成 pending。
let createdEvent: Record<string, unknown> | null;

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}{location.search}</div>;
}

function tree(client: QueryClient, props: ReschedulePageProps) {
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/reschedule"]}>
        <Routes>
          <Route element={<Outlet context={{ user: admin, scheduleAccessRole: "approver" }} />}>
            <Route path="/reschedule" element={<ReschedulePage {...props} />} />
          </Route>
          <Route path="*" element={<div>其它页面</div>} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function renderPage(props: ReschedulePageProps = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(tree(client, props));
  return { ...view, rerenderWith: (next: ReschedulePageProps) => view.rerender(tree(client, next)) };
}

function mockBackend() {
  candidateScheduleId = null;
  events = [];
  scheduleAssignments = [];
  createdEvent = null;
  mocks.request.mockReset();
  mocks.success.mockReset();
  mocks.error.mockReset();
  mocks.request.mockImplementation(async (config: { url: string; method?: string }) => {
    const method = (config.method ?? "GET").toUpperCase();
    if (config.url === "/api/v1/reschedule-events" && method === "POST") return createdEvent ?? { id: "evt-1", event_type: "teacher_leave", status: "candidate_ready", candidate_schedule_id: candidateScheduleId };
    const detail = /^\/api\/v1\/schedules\/([^/]+)$/.exec(config.url);
    if (detail && method === "GET") return { ...[...schedules, draft].find((item) => item.id === detail[1]), assignments: scheduleAssignments };
    if (config.url === "/api/v1/reschedule-events") return events;
    if (config.url === "/api/v1/schedules") return [...schedules, draft];
    if (config.url === "/api/v1/teachers") return teachers;
    if (config.url === "/api/v1/rooms") return rooms;
    if (config.url === "/api/v1/time-slots") return [slot, slotThursday];
    return [];
  });
}

function createCalls() {
  return mocks.request.mock.calls.filter(([config]) => config.url === "/api/v1/reschedule-events" && config.method === "POST");
}

describe("ReschedulePage declared reason chips", () => {
  afterEach(cleanup);

  beforeEach(mockBackend);

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

describe("ReschedulePage inside the schedule hub", () => {
  afterEach(cleanup);
  beforeEach(mockBackend);

  const location = () => screen.getByTestId("location").textContent;
  const prefill = { lessonId: "COURSE-2", label: "B班 10月15日 周四上午，教师 李老师", teacherId: "T-002", roomId: "教室-302", slotId: slotThursday.business_id, lessonDate: "2026-10-15", scheduleId: "s2" };

  it("keeps its own page title when standalone and drops it when embedded", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "局部调课", level: 1 })).toBeVisible();

    cleanup();
    renderPage({ embedded: true });
    await screen.findByText("创建变更事件");
    expect(screen.queryByRole("heading", { name: "局部调课" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "排课流程" })).not.toBeInTheDocument();
  });

  it("scopes the request to the selected lesson: id, full date, version and no neighbours by default", async () => {
    const user = userEvent.setup();
    renderPage({ embedded: true, parentScheduleId: "s2", prefill });

    expect(await screen.findByText(/已按所选课次带入：B班 10月15日 周四上午，教师 李老师/)).toBeVisible();
    expect(screen.getByRole("radio", { name: /只调整这一节课/ })).toBeChecked();
    // 课次身份只在选中它的那一版里成立：基准版本锁定，不能改成别的版本。
    expect(screen.getByRole("combobox", { name: "父课表" })).toHaveValue("s2");
    expect(screen.getByRole("combobox", { name: "父课表" })).toBeDisabled();
    expect(screen.queryByRole("combobox", { name: "教师" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "生成候选方案" }));
    await waitFor(() => expect(createCalls()).toHaveLength(1));
    expect(createCalls()[0][0].data).toMatchObject({
      event_type: "teacher_leave",
      parent_schedule_id: "s2",
      teacher_business_id: "T-002",
      room_business_id: null,
      slot_business_ids: [slotThursday.business_id],
      course_business_id: "COURSE-2",
      date_from: "2026-10-15",
      date_to: "2026-10-15",
      include_neighbors: false,
    });
  });

  it("uses the version the lesson was selected in even if the header default differs", async () => {
    const user = userEvent.setup();
    renderPage({ embedded: true, parentScheduleId: "s1", prefill });
    await user.click(await screen.findByRole("button", { name: "生成候选方案" }));
    await waitFor(() => expect(createCalls()).toHaveLength(1));
    expect(createCalls()[0][0].data).toMatchObject({ parent_schedule_id: "s2", course_business_id: "COURSE-2" });
  });

  it("only widens to neighbouring lessons after an explicit opt-in, with the number of days", async () => {
    const user = userEvent.setup();
    renderPage({ embedded: true, prefill });

    await user.click(await screen.findByRole("checkbox", { name: /允许连带调整同班级、同教室的邻近课次/ }));
    const days = screen.getByRole("spinbutton", { name: "连带调整的天数" });
    await user.clear(days);
    await user.type(days, "3");
    await user.click(screen.getByRole("button", { name: "生成候选方案" }));

    await waitFor(() => expect(createCalls()).toHaveLength(1));
    expect(createCalls()[0][0].data).toMatchObject({ course_business_id: "COURSE-2", include_neighbors: true, neighborhood_days: 3 });
  });

  it("uses the lesson's room for a room outage and keeps it fixed to that lesson", async () => {
    const user = userEvent.setup();
    renderPage({ embedded: true, prefill });

    await user.selectOptions(await screen.findByRole("combobox", { name: "事件类型" }), "room_outage");
    expect(screen.queryByRole("combobox", { name: "教室" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("所选课次")).toHaveTextContent("302 教室");

    await user.click(screen.getByRole("button", { name: "生成候选方案" }));
    await waitFor(() => expect(createCalls()).toHaveLength(1));
    expect(createCalls()[0][0].data).toMatchObject({ event_type: "room_outage", room_business_id: "教室-302", teacher_business_id: null, course_business_id: "COURSE-2" });
  });

  it("registering an event instead keeps the form editable, sends no lesson scope and shows how many lessons it pulls in", async () => {
    const user = userEvent.setup();
    scheduleAssignments = [
      { course_business_id: "COURSE-2", teacher_business_id: "T-002", room_business_id: "教室-302", slot_business_id: slotThursday.business_id, lesson_date: "2026-10-15" },
      { course_business_id: "COURSE-3", teacher_business_id: "T-002", room_business_id: "教室-302", slot_business_id: slotThursday.business_id, lesson_date: "2026-10-22" },
      { course_business_id: "COURSE-4", teacher_business_id: "T-002", room_business_id: "教室-302", slot_business_id: slot.business_id, lesson_date: "2026-10-28" },
      { course_business_id: "COURSE-5", teacher_business_id: "T-001", room_business_id: "教室-301", slot_business_id: slot.business_id, lesson_date: "2026-10-29" },
    ];
    renderPage({ embedded: true, parentScheduleId: "s2", prefill });

    await user.click(await screen.findByRole("radio", { name: /登记为教师请假事件/ }));
    expect(await screen.findByText(/李老师在这份课表里共有 3 节课会进入调整范围（2026-10-15 ~ 2026-10-28），其中 2 节在所选时段；不限于某一天/)).toBeVisible();
    // 整批事件的表单仍然可以改：换成另一位老师，影响范围跟着重算。
    await user.selectOptions(screen.getByRole("combobox", { name: "教师" }), "T-001");
    expect(await screen.findByText(/张老师在这份课表里共有 1 节课会进入调整范围/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "生成候选方案" }));

    await waitFor(() => expect(createCalls()).toHaveLength(1));
    expect(createCalls()[0][0].data).toMatchObject({ teacher_business_id: "T-001", course_business_id: null });
    expect(createCalls()[0][0].data).not.toHaveProperty("date_from");
  });

  it("does not overwrite the event form when the same lesson stays selected, but re-prefills for another lesson", async () => {
    const user = userEvent.setup();
    const view = renderPage({ embedded: true, prefill });
    await user.click(await screen.findByRole("radio", { name: /登记为教师请假事件/ }));
    await user.selectOptions(await screen.findByRole("combobox", { name: "教师" }), "T-001");

    // 同一节课重新渲染（例如课表数据刷新）不应把用户刚改的老师刷回去，也不该把范围弹回去。
    view.rerenderWith({ embedded: true, prefill: { ...prefill } });
    expect(screen.getByRole("combobox", { name: "教师" })).toHaveValue("T-001");

    // 换选另一节课：预填与范围都回到默认的「只调整这一节课」。
    view.rerenderWith({ embedded: true, prefill: { ...prefill, lessonId: "COURSE-3", teacherId: "T-002" } });
    await waitFor(() => expect(screen.getByRole("radio", { name: /只调整这一节课/ })).toBeChecked());
    expect(screen.queryByRole("combobox", { name: "教师" })).not.toBeInTheDocument();
  });

  it("follows the version chosen in the schedule header", async () => {
    const view = renderPage({ embedded: true, parentScheduleId: "s1" });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "父课表" })).toHaveValue("s1"));

    view.rerenderWith({ embedded: true, parentScheduleId: "s2" });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "父课表" })).toHaveValue("s2"));
  });

  it("points at the new draft in 历史版本 after a candidate is generated", async () => {
    const user = userEvent.setup();
    candidateScheduleId = "s2";
    renderPage({ embedded: true });

    await user.click(await screen.findByRole("button", { name: "生成候选方案" }));

    expect(await screen.findByText(/候选方案已生成为草稿，尚未生效/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "查看版本" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "在历史版本中查看新草稿" }));
    expect(location()).toBe("/schedule?view=history&version=s2");
  });

  it("refreshes the version list so the new draft can be found", async () => {
    const user = userEvent.setup();
    candidateScheduleId = "s2";
    renderPage({ embedded: true });
    await screen.findByRole("button", { name: "生成候选方案" });
    const listCalls = () => mocks.request.mock.calls.filter(([config]) => config.url === "/api/v1/schedules").length;
    const before = listCalls();

    await user.click(screen.getByRole("button", { name: "生成候选方案" }));

    await waitFor(() => expect(listCalls()).toBeGreaterThan(before));
  });

  // 审查 #4：创建成功只代表任务入队，不代表候选草稿已生成——要一直跟踪到事件自己给出结果。
  const pendingEvent = { id: "evt-1", event_type: "teacher_leave", description: "教师请假", status: "pending", declared_reason: null, candidate_schedule_id: null, created_at: "2026-08-02T01:00:00Z" };

  it("says the candidate is still being generated while the solve runs, and offers no draft link yet", async () => {
    const user = userEvent.setup();
    createdEvent = pendingEvent;
    events = [pendingEvent];
    renderPage({ embedded: true });

    await user.click(await screen.findByRole("button", { name: "生成候选方案" }));

    expect(await screen.findByText(/正在生成候选方案/)).toBeVisible();
    expect(screen.queryByText(/候选方案已生成为草稿/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "在历史版本中查看新草稿" })).not.toBeInTheDocument();
  });

  it("follows the event until the candidate exists, then the button opens exactly that candidate", async () => {
    const user = userEvent.setup();
    createdEvent = pendingEvent;
    events = [pendingEvent];
    renderPage({ embedded: true });
    await user.click(await screen.findByRole("button", { name: "生成候选方案" }));
    expect(await screen.findByText(/正在生成候选方案/)).toBeVisible();

    // 后台求解完成：事件带上了候选课表编号，页面自己轮询到，不需要任何人手动刷新。
    events = [{ ...pendingEvent, status: "candidate_ready", candidate_schedule_id: "s2" }];
    const open = await screen.findByRole("button", { name: "在历史版本中查看新草稿" }, { timeout: 6000 });
    expect(screen.getByText(/候选方案已生成为草稿，尚未生效/)).toBeVisible();
    await user.click(open);
    expect(location()).toBe("/schedule?view=history&version=s2");
  });

  it("tells the user when the solve found no feasible candidate instead of promising a draft", async () => {
    const user = userEvent.setup();
    createdEvent = pendingEvent;
    events = [pendingEvent];
    renderPage({ embedded: true });
    await user.click(await screen.findByRole("button", { name: "生成候选方案" }));
    expect(await screen.findByText(/正在生成候选方案/)).toBeVisible();

    events = [{ ...pendingEvent, status: "no_candidate" }];
    expect(await screen.findByText(/这次没有找到可行的候选方案，没有生成草稿/, undefined, { timeout: 6000 })).toBeVisible();
    expect(screen.queryByRole("button", { name: "在历史版本中查看新草稿" })).not.toBeInTheDocument();
    expect(screen.queryByText(/正在生成候选方案/)).not.toBeInTheDocument();
  });

  it("tells the user when the solve failed", async () => {
    const user = userEvent.setup();
    createdEvent = pendingEvent;
    events = [pendingEvent];
    renderPage({ embedded: true });
    await user.click(await screen.findByRole("button", { name: "生成候选方案" }));
    expect(await screen.findByText(/正在生成候选方案/)).toBeVisible();

    events = [{ ...pendingEvent, status: "failed" }];
    expect(await screen.findByText(/求解没有完成，没有生成候选方案/, undefined, { timeout: 6000 })).toBeVisible();
    expect(screen.queryByRole("button", { name: "在历史版本中查看新草稿" })).not.toBeInTheDocument();
  });

  it("links an event to its candidate draft", async () => {
    const user = userEvent.setup();
    events = [{ id: "evt-9-aaaa-bbbb", event_type: "teacher_leave", description: "张老师请假", status: "candidate_ready", declared_reason: null, candidate_schedule_id: "s2", created_at: "2026-08-02T01:00:00Z" }];
    renderPage({ embedded: true });

    await user.click(await screen.findByRole("button", { name: "查看候选草稿" }));

    expect(location()).toBe("/schedule?view=history&version=s2");
  });
});
