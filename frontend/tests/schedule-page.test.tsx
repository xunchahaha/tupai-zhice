import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SchedulePage } from "@/pages/schedule-page";

// 页面走 orval 生成的客户端（customInstance）+ 少量直接 http 调用（导出、日历下发），两处都拦截。
const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}));

vi.mock("@/api/http", () => ({
  customInstance: mocks.request,
  http: { get: mocks.get, post: mocks.post, patch: vi.fn() },
  authStore: { get: vi.fn(), set: vi.fn(), clear: vi.fn() },
  scheduleSetStore: { get: vi.fn(), set: vi.fn(), clear: vi.fn() },
  API_BASE_URL: "http://127.0.0.1:8000",
}));

vi.mock("sonner", () => ({ toast: { success: mocks.success, error: mocks.error, warning: mocks.warning } }));

const admin = { id: "admin-id", username: "admin", role: "admin" as const };
const scheduler = { id: "scheduler-id", username: "scheduler_demo", role: "scheduler" as const };
const approver = { id: "approver-id", username: "approver_demo", role: "approver" as const };
const viewer = { id: "viewer-id", username: "member_demo", role: "viewer" as const };

type TestUser = typeof admin | typeof scheduler | typeof approver | typeof viewer;

const membershipFor: Record<TestUser["role"], string> = {
  admin: "approver",
  scheduler: "scheduler",
  approver: "approver",
  viewer: "viewer",
};

const SLOT_WED = "SLOT-周三-1900-2030";
const SLOT_THU = "SLOT-周四-0900-1030";

const publishedSchedule = { id: "s1", version_no: 1, name: "基线课表", status: "published", parent_id: null, solver_run_id: "run-1", metrics: {}, assignment_count: 2, published_at: "2026-08-01T02:00:00Z", created_at: "2026-08-01T01:00:00Z" };
const draftSchedule = { id: "s2", version_no: 2, name: "求解版本 v2", status: "draft", parent_id: "s1", solver_run_id: "run-2", metrics: {}, assignment_count: 2, published_at: null, created_at: "2026-08-02T01:00:00Z" };

const classes = [
  { id: "cg1", campus_id: "c1", business_id: "CLASS-A", name: "A班" },
  { id: "cg2", campus_id: "c1", business_id: "CLASS-B", name: "B班" },
];
const teachers = [
  { id: "t1", campus_id: "c1", business_id: "T-001", name: "王老师" },
  { id: "t2", campus_id: "c1", business_id: "T-002", name: "李老师" },
];
const rooms = [{ id: "r1", business_id: "教室-301", name: "301 教室" }];
const slots = [
  { id: "slot-1", business_id: SLOT_WED, weekday: "周三", start_time: "19:00", end_time: "20:30" },
  { id: "slot-2", business_id: SLOT_THU, weekday: "周四", start_time: "09:00", end_time: "10:30" },
];
const courseSessions = [
  { business_id: "COURSE-1", class_business_id: "CLASS-A", teacher_business_id: "T-001", subject: "数学" },
  { business_id: "COURSE-2", class_business_id: "CLASS-B", teacher_business_id: "T-002", subject: "英语" },
];

// 2026-10-14 是周三、2026-10-15 是周四。
const assignments = [
  { course_session_id: "cs-1", course_business_id: "COURSE-1", class_business_id: "CLASS-A", teacher_business_id: "T-001", lesson_date: "2026-10-14", slot_business_id: SLOT_WED, room_business_id: "教室-301", change_kind: "unchanged" },
  { course_session_id: "cs-2", course_business_id: "COURSE-2", class_business_id: "CLASS-B", teacher_business_id: "T-002", lesson_date: "2026-10-15", slot_business_id: SLOT_THU, room_business_id: "教室-301", change_kind: "unchanged" },
];

let scheduleList: Array<Record<string, unknown>>;

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}{location.search}</div>;
}

function renderPage(entry = "/schedule", user: TestUser = admin, accessRole: string = membershipFor[user.role]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route element={<Outlet context={{ user, scheduleAccessRole: accessRole, scheduleSet: { id: "set-1", name: "主方案" } }} />}>
            <Route path="/schedule" element={<SchedulePage />} />
          </Route>
          <Route path="*" element={<div>其它页面</div>} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const location = () => screen.getByTestId("location").textContent ?? "";
const search = () => new URLSearchParams(location().split("?")[1] ?? "");
const tabNames = () => screen.getAllByRole("tab").map((tab) => tab.textContent);

describe("SchedulePage hub", () => {
  afterEach(cleanup);

  beforeEach(() => {
    scheduleList = [publishedSchedule, draftSchedule];
    Object.values(mocks).forEach((mock) => mock.mockReset());
    mocks.request.mockImplementation(async (config: { url: string; method?: string }) => {
      const method = (config.method ?? "GET").toUpperCase();
      const url = String(config.url);
      if (url === "/api/v1/schedules") return scheduleList;
      const detail = url.match(/^\/api\/v1\/schedules\/([^/]+)$/);
      if (detail && method === "GET") {
        const found = scheduleList.find((item) => item.id === detail[1]);
        return { ...found, assignments };
      }
      if (url.includes("/diff/")) return { changed_count: 0, unchanged_count: 2, items: [] };
      if (url === "/api/v1/reschedule-events" && method === "POST") return { id: "evt-1", event_type: "teacher_leave", status: "candidate_ready", candidate_schedule_id: "s2" };
      if (url === "/api/v1/class-groups") return classes;
      if (url === "/api/v1/teachers") return teachers;
      if (url === "/api/v1/rooms") return rooms;
      if (url === "/api/v1/time-slots") return slots;
      if (url === "/api/v1/course-sessions") return courseSessions;
      return [];
    });
  });

  describe("header and view switching", () => {
    it("titles the page 课表 and marks the status of the selected version", async () => {
      renderPage();

      expect(await screen.findByRole("heading", { name: "课表", level: 1 })).toBeVisible();
      // 默认落在当前使用中的已发布版本。
      expect(await screen.findByTestId("version-status")).toHaveTextContent("当前使用中");
      expect(screen.getByRole("combobox", { name: "课表版本" })).toHaveValue("s1");
    });

    it("switches the status label with the version and shows the draft banner", async () => {
      const user = userEvent.setup();
      renderPage();
      await screen.findByTestId("lesson-card-COURSE-1");

      await user.selectOptions(screen.getByRole("combobox", { name: "课表版本" }), "s2");

      expect(screen.getByTestId("version-status")).toHaveTextContent("草稿");
      expect(search().get("version")).toBe("s2");
      expect(screen.getByText(/它还没有生效，也不会对外分享或下发/)).toBeVisible();
    });

    it("preselects the version from ?version= and ignores unknown ids", async () => {
      renderPage("/schedule?version=s2");
      expect(await screen.findByTestId("version-status")).toHaveTextContent("草稿");
      expect(screen.getByRole("combobox", { name: "课表版本" })).toHaveValue("s2");
      await waitFor(() =>
        expect(mocks.request).toHaveBeenCalledWith(expect.objectContaining({ url: "/api/v1/schedules/s2", method: "GET" })),
      );

      cleanup();
      renderPage("/schedule?version=missing");
      expect(await screen.findByTestId("version-status")).toHaveTextContent("当前使用中");
      expect(screen.getByRole("combobox", { name: "课表版本" })).toHaveValue("s1");
    });

    it("offers the four views to a scheduler-capable user and follows ?view=", async () => {
      renderPage("/schedule?view=history");

      await screen.findByText("版本记录");
      expect(tabNames()).toEqual(["课表", "调整", "历史版本", "分享与订阅"]);
      expect(screen.getByRole("tab", { name: "历史版本" })).toHaveAttribute("aria-selected", "true");
      expect(screen.getByRole("tab", { name: "课表" })).toHaveAttribute("aria-selected", "false");
      expect(screen.getByRole("tabpanel")).toBeVisible();
    });

    it("keeps the other query params when switching views and drops view for 课表", async () => {
      const user = userEvent.setup();
      renderPage("/schedule?version=s2&lesson=COURSE-1");
      await screen.findByTestId("lesson-card-COURSE-1");

      await user.click(screen.getByRole("tab", { name: "分享与订阅" }));
      expect(search().get("view")).toBe("share");
      expect(search().get("version")).toBe("s2");
      expect(search().get("lesson")).toBe("COURSE-1");
      expect(await screen.findByText("教师日历下发")).toBeVisible();

      await user.click(screen.getByRole("tab", { name: "课表" }));
      expect(search().has("view")).toBe(false);
      expect(search().get("version")).toBe("s2");
      expect(await screen.findByTestId("lesson-card-COURSE-1")).toBeVisible();
    });

    it("moves between views with the arrow keys", async () => {
      const user = userEvent.setup();
      renderPage();
      await screen.findByTestId("lesson-card-COURSE-1");

      screen.getByRole("tab", { name: "课表" }).focus();
      await user.keyboard("{ArrowRight}");

      expect(search().get("view")).toBe("adjust");
      expect(screen.getByRole("tab", { name: "调整" })).toHaveFocus();
    });

    it("prints from the 课表 view only", async () => {
      const user = userEvent.setup();
      const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
      renderPage();

      await user.click(await screen.findByRole("button", { name: "打印" }));
      expect(print).toHaveBeenCalledTimes(1);

      await user.click(screen.getByRole("tab", { name: "历史版本" }));
      expect(screen.queryByRole("button", { name: "打印" })).not.toBeInTheDocument();
      print.mockRestore();
    });

    it("exports the selected version as XLSX", async () => {
      const user = userEvent.setup();
      const createObjectURL = vi.fn(() => "blob:schedule");
      const originalCreate = URL.createObjectURL;
      const originalRevoke = URL.revokeObjectURL;
      URL.createObjectURL = createObjectURL;
      URL.revokeObjectURL = vi.fn();
      const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
      mocks.get.mockResolvedValue({ data: new Blob(["x"]) });
      renderPage("/schedule?version=s2");

      await user.click(await screen.findByRole("button", { name: "导出 XLSX" }));

      await waitFor(() =>
        expect(mocks.get).toHaveBeenCalledWith("/api/v1/schedules/s2/export.xlsx", { responseType: "blob" }),
      );
      expect(click).toHaveBeenCalledTimes(1);
      click.mockRestore();
      URL.createObjectURL = originalCreate;
      URL.revokeObjectURL = originalRevoke;
    });
  });

  describe("role gating", () => {
    it("hides 调整 from read-only members and falls back to 课表 on ?view=adjust", async () => {
      renderPage("/schedule?view=adjust", viewer);

      expect(await screen.findByTestId("lesson-card-COURSE-1")).toBeVisible();
      expect(tabNames()).toEqual(["课表", "历史版本"]);
      expect(screen.getByRole("tab", { name: "课表" })).toHaveAttribute("aria-selected", "true");
      expect(screen.queryByText("创建变更事件")).not.toBeInTheDocument();
    });

    it("lets an approver review history but not adjust or distribute", async () => {
      renderPage("/schedule?view=share", approver);

      expect(await screen.findByTestId("lesson-card-COURSE-1")).toBeVisible();
      expect(tabNames()).toEqual(["课表", "历史版本"]);
      expect(screen.queryByText("教师日历下发")).not.toBeInTheDocument();
    });

    // 与旧 RoleRoute 同口径：全局角色是排课员，但在当前课表里只有只读权限，就不能签发公开链接或下发日历。
    it("hides 调整 and 分享与订阅 from a scheduler who is only a viewer of this timetable", async () => {
      renderPage("/schedule?view=share", scheduler, "viewer");

      expect(await screen.findByTestId("lesson-card-COURSE-1")).toBeVisible();
      expect(tabNames()).toEqual(["课表", "历史版本"]);
      expect(screen.getByRole("tab", { name: "课表" })).toHaveAttribute("aria-selected", "true");
      expect(screen.queryByText("教师日历下发")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "新建公开链接" })).not.toBeInTheDocument();
    });

    it("gives schedulers adjust and share but publishing stays an approver action", async () => {
      const user = userEvent.setup();
      renderPage("/schedule?version=s2", scheduler);

      await screen.findByTestId("lesson-card-COURSE-1");
      expect(tabNames()).toEqual(["课表", "调整", "历史版本", "分享与订阅"]);
      expect(screen.getByText(/需由有审批权限的人在/)).toBeVisible();

      await user.click(screen.getByRole("tab", { name: "历史版本" }));
      await screen.findByText("版本记录");
      expect(screen.queryByRole("button", { name: "发布版本 v2" })).not.toBeInTheDocument();
    });
  });

  describe("lesson selection", () => {
    it("selects a lesson and offers 调整这节课 and 交给助手继续处理 to schedulers", async () => {
      const user = userEvent.setup();
      renderPage();

      const card = await screen.findByTestId("lesson-card-COURSE-1");
      expect(card).toHaveAttribute("aria-pressed", "false");
      expect(screen.queryByRole("button", { name: "调整这节课" })).not.toBeInTheDocument();

      await user.click(card);

      expect(card).toHaveAttribute("aria-pressed", "true");
      expect(search().get("lesson")).toBe("COURSE-1");
      const bar = screen.getByRole("region", { name: "已选课次" });
      expect(bar).toHaveTextContent("数学");
      expect(bar).toHaveTextContent("A班");
      expect(bar).toHaveTextContent("10月14日 周三晚");
      expect(bar).toHaveTextContent("王老师");
      expect(within(bar).getByRole("button", { name: "调整这节课" })).toBeVisible();
      expect(within(bar).getByRole("button", { name: "交给助手继续处理" })).toBeVisible();

      await user.click(within(bar).getByRole("button", { name: "取消选择" }));
      expect(search().has("lesson")).toBe(false);
      expect(screen.queryByRole("region", { name: "已选课次" })).not.toBeInTheDocument();
    });

    it("hands the lesson to the assistant as a prefilled prompt without submitting", async () => {
      const user = userEvent.setup();
      renderPage("/schedule?lesson=COURSE-1");

      await user.click(await screen.findByRole("button", { name: "交给助手继续处理" }));

      expect(location().startsWith("/assistant?")).toBe(true);
      // 一句话给人读（带完整日期）；业务身份走结构化参数：所选版本 + 课次业务号。
      expect(search().get("prompt")).toBe("调整 A班 2026-10-14 周三晚 的课（教师 王老师）：");
      expect(search().get("base")).toBe("s1");
      expect(search().get("lesson")).toBe("COURSE-1");
      expect(search().has("goal")).toBe(false);
      expect(search().has("run")).toBe(false);
    });

    it("jumps to 调整 scoped to exactly that lesson: identity, date and version travel with the request", async () => {
      const user = userEvent.setup();
      renderPage("/schedule?lesson=COURSE-2");

      await user.click(await screen.findByRole("button", { name: "调整这节课" }));

      expect(await screen.findByText("创建变更事件")).toBeVisible();
      expect(search().get("view")).toBe("adjust");
      expect(search().get("lesson")).toBe("COURSE-2");
      expect(screen.getByText(/已按所选课次带入：B班 10月15日 周四上午/)).toBeVisible();
      // 选中课次的调整对象是一节具体的课：教师、日期、时段是事实，不再是可随手改的表单项。
      const facts = screen.getByLabelText("所选课次");
      expect(facts).toHaveTextContent("2026-10-15");
      expect(facts).toHaveTextContent("李老师");
      expect(screen.queryByRole("combobox", { name: "教师" })).not.toBeInTheDocument();
      expect(screen.getByRole("combobox", { name: "父课表" })).toHaveValue("s1");
      expect(screen.getByRole("combobox", { name: "父课表" })).toBeDisabled();

      await user.click(screen.getByRole("button", { name: "生成候选方案" }));
      await waitFor(() =>
        expect(mocks.request).toHaveBeenCalledWith(
          expect.objectContaining({
            url: "/api/v1/reschedule-events",
            method: "POST",
            data: expect.objectContaining({
              teacher_business_id: "T-002",
              slot_business_ids: [SLOT_THU],
              parent_schedule_id: "s1",
              course_business_id: "COURSE-2",
              date_from: "2026-10-15",
              date_to: "2026-10-15",
              include_neighbors: false,
            }),
          }),
        ),
      );
    });

    it("registering the lesson as an event leaves the teacher editable and drops the single-lesson scope", async () => {
      const user = userEvent.setup();
      renderPage("/schedule?lesson=COURSE-2&view=adjust");

      await user.click(await screen.findByRole("radio", { name: /登记为教师请假事件/ }));
      expect(screen.getByRole("combobox", { name: "教师" })).toHaveValue("T-002");
      await user.selectOptions(screen.getByRole("combobox", { name: "教师" }), "T-001");
      await user.click(screen.getByRole("button", { name: "生成候选方案" }));
      await waitFor(() =>
        expect(mocks.request).toHaveBeenCalledWith(
          expect.objectContaining({
            url: "/api/v1/reschedule-events",
            method: "POST",
            data: expect.objectContaining({ teacher_business_id: "T-001", slot_business_ids: [SLOT_THU], course_business_id: null }),
          }),
        ),
      );
      const posted = mocks.request.mock.calls.map(([config]) => config).find((config) => config.method === "POST" && config.url === "/api/v1/reschedule-events");
      expect(posted.data).not.toHaveProperty("date_from");
    });

    it("reveals a lesson from the link even when it belongs to another class", async () => {
      renderPage("/schedule?lesson=COURSE-2");

      // 默认对象是 A班，链接指向 B班的课次：页面切到 B班并选中它。
      const card = await screen.findByTestId("lesson-card-COURSE-2");
      expect(card).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByRole("combobox", { name: "排课对象" })).toHaveValue("CLASS-B");
      expect(screen.queryByTestId("lesson-card-COURSE-1")).not.toBeInTheDocument();
    });

    it("lets read-only members select a lesson but gives them no adjust actions", async () => {
      const user = userEvent.setup();
      renderPage("/schedule", viewer);

      await user.click(await screen.findByTestId("lesson-card-COURSE-1"));

      const bar = screen.getByRole("region", { name: "已选课次" });
      expect(within(bar).queryByRole("button", { name: "调整这节课" })).not.toBeInTheDocument();
      expect(within(bar).queryByRole("button", { name: "交给助手继续处理" })).not.toBeInTheDocument();
      expect(bar).toHaveTextContent("只读成员可以查看课次，不能调整");
    });

    it("selects a lesson from the keyboard", async () => {
      const user = userEvent.setup();
      renderPage();

      const card = await screen.findByTestId("lesson-card-COURSE-1");
      card.focus();
      await user.keyboard("{Enter}");

      expect(card).toHaveAttribute("aria-pressed", "true");
    });
  });

  describe("from candidate to history", () => {
    it("switches to 历史版本 and highlights the new draft after generating a candidate", async () => {
      const user = userEvent.setup();
      renderPage("/schedule?view=adjust");

      await user.click(await screen.findByRole("button", { name: "生成候选方案" }));
      await user.click(await screen.findByRole("button", { name: "在历史版本中查看新草稿" }));

      expect(location()).toBe("/schedule?view=history&version=s2");
      await screen.findByText("版本记录");
      expect(screen.getByRole("tab", { name: "历史版本" })).toHaveAttribute("aria-selected", "true");
      expect(screen.getByTestId("version-status")).toHaveTextContent("草稿");
      expect(screen.getByTestId("version-card-s2")).toHaveAttribute("data-selected", "true");
      expect(screen.getByTestId("version-card-s1")).not.toHaveAttribute("data-selected");
      // 新草稿默认拿它和来源版本对比。
      await waitFor(() => expect(screen.getByRole("combobox", { name: "目标版本" })).toHaveValue("s2"));
      expect(screen.getByRole("combobox", { name: "基准版本" })).toHaveValue("s1");
    });

    it("returns from 历史版本 to 课表 on the chosen version", async () => {
      const user = userEvent.setup();
      renderPage("/schedule?view=history&version=s2");
      await screen.findByText("版本记录");

      await user.click(screen.getByRole("button", { name: "查看课表" }));

      expect(location()).toBe("/schedule?version=s2");
      expect(await screen.findByTestId("lesson-card-COURSE-1")).toBeVisible();
    });

    it("embeds history without the standalone page header and publishes through the shared hook", async () => {
      const user = userEvent.setup();
      renderPage("/schedule?view=history");
      await screen.findByText("版本记录");

      expect(screen.queryByRole("heading", { name: "版本与回滚" })).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "发布版本 v2" }));
      expect(mocks.request).not.toHaveBeenCalledWith(expect.objectContaining({ url: "/api/v1/schedules/s2/publish" }));
      await user.click(await screen.findByRole("button", { name: "确认发布" }));
      await waitFor(() => expect(mocks.success).toHaveBeenCalledWith("版本已发布为当前课表，已触发发布数据同步"));
      expect(mocks.request).toHaveBeenCalledWith(expect.objectContaining({ url: "/api/v1/schedules/s2/publish", method: "POST" }));
    });
  });

  describe("share and subscribe view", () => {
    it("says drafts are not shared or dispatched and keeps formal dispatch off for a draft", async () => {
      const user = userEvent.setup();
      mocks.post.mockResolvedValue({ data: { dry_run: true, would_publish: 3, published: 0, existing: 0, skipped_unmapped: 0, conflict_count: 0, conflicts: [] } });
      renderPage("/schedule?view=share&version=s2");

      expect(await screen.findByText(/草稿不会对外分享或下发，需先由有审批权限的人发布/)).toBeVisible();
      expect(screen.getByText(/当前所选的 v2（草稿）不在对外范围内/)).toBeVisible();
      expect(await screen.findByRole("button", { name: "新建公开链接" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "确认下发" })).toBeDisabled();

      await user.click(screen.getByRole("button", { name: "忙闲预检" }));
      await waitFor(() =>
        expect(mocks.post).toHaveBeenCalledWith("/api/v1/schedules/s2/calendar-publish", expect.objectContaining({ dry_run: true })),
      );
    });

    it("drops the previous version's precheck result when the header switches version", async () => {
      const user = userEvent.setup();
      mocks.post.mockResolvedValue({ data: { dry_run: true, would_publish: 120, published: 0, existing: 0, skipped_unmapped: 0, conflict_count: 0, conflicts: [] } });
      renderPage("/schedule?view=share&version=s2");

      await user.click(await screen.findByRole("button", { name: "忙闲预检" }));
      expect(await screen.findByText("120")).toBeVisible();

      await user.selectOptions(screen.getByRole("combobox", { name: "课表版本" }), "s1");

      expect(await screen.findByText(/当前课表：基线课表/)).toBeVisible();
      expect(screen.queryByText("120")).not.toBeInTheDocument();
      expect(screen.queryByText("仅预检")).not.toBeInTheDocument();
    });

    it("opens formal dispatch for the in-use version", async () => {
      renderPage("/schedule?view=share");

      expect(await screen.findByText("教师日历下发")).toBeVisible();
      expect(screen.getByRole("button", { name: "确认下发" })).toBeEnabled();
      expect(screen.queryByText(/不在对外范围内/)).not.toBeInTheDocument();
    });

    it("blocks creating links and dispatching while nothing is published yet", async () => {
      scheduleList = [draftSchedule];
      renderPage("/schedule?view=share");

      expect(await screen.findByText(/还没有已发布的课表版本，暂时不能新建公开链接/)).toBeVisible();
      expect(await screen.findByRole("button", { name: "新建公开链接" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "确认下发" })).toBeDisabled();
    });
  });
});
