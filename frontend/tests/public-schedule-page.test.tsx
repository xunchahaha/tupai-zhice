import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  addDaysIso,
  PublicLinkUnavailableError,
  toIsoDate,
  type PublicLinkPayload,
} from "@/lib/public-api";
import type { PublicLinkSchedulePayload } from "@/api/generated/models";
import { PublicSchedulePage } from "@/pages/public/public-schedule-page";

const mocks = vi.hoisted(() => ({
  fetchPublicLink: vi.fn(),
}));

// 公开页只经 lib/public-api 的裸 fetch 拉数据，这里拦截 fetchPublicLink，
// URL 拼接等纯函数保持真实实现参与断言。
vi.mock("@/lib/public-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/public-api")>()),
  fetchPublicLink: mocks.fetchPublicLink,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

// 用真实时钟构造数据：今天恒定存在的 00:00-23:59 行保证「进行中」可断言，
// 其余行落在明天，避免 fake timers 让 waitFor 失去推进的问题。
const TODAY = toIsoDate(new Date());

const ROW_TODAY_ONGOING = { date: TODAY, weekday: "周三", start: "00:00", end: "23:59", class_name: "三年二班", subject: "语文", lesson_name: "阅读", teacher_names: ["李老师"], location: "301教室" };
const ROW_TODAY_MORNING = { date: TODAY, weekday: "周三", start: "01:00", end: "01:45", class_name: "三年二班", subject: "数学", lesson_name: "数学思维", teacher_names: ["王老师"], location: "301教室" };
const ROW_TOMORROW = { date: addDaysIso(TODAY, 1), weekday: "周四", start: "08:00", end: "08:40", class_name: "三年二班", subject: "英语", lesson_name: "英语", teacher_names: [], location: "302教室" };

function schedulePayload(overrides: Partial<PublicLinkSchedulePayload> = {}): PublicLinkPayload {
  return {
    scope: "class",
    display_name: "三年二班",
    show_teacher_names: true,
    version_no: 7,
    published_at: "2026-09-14T08:00:00Z",
    first_date: "2026-09-14",
    last_date: "2026-12-30",
    rows: [ROW_TODAY_MORNING, ROW_TODAY_ONGOING, ROW_TOMORROW],
    adjustments: [],
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/public/t/tok-123"]}>
      <Routes>
        <Route path="/public/t/:token" element={<PublicSchedulePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function renderReady(payload: PublicLinkPayload = schedulePayload()) {
  mocks.fetchPublicLink.mockResolvedValue(payload);
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("三年二班");
  return user;
}

afterEach(() => {
  cleanup();
  mocks.fetchPublicLink.mockReset();
});

describe("PublicSchedulePage", () => {
  it("renders header, grouped course cards and the ongoing-course highlight", async () => {
    await renderReady();

    expect(screen.getByText("V7 · 更新于 2026/9/14")).toBeInTheDocument();
    expect(screen.getByText("覆盖日期 2026-09-14 ~ 2026-12-30")).toBeInTheDocument();

    // 今天分组置顶：数学与语文（全天行，恒为进行中）都在今天卡片流里。
    expect(screen.getByText("01:00-01:45")).toBeInTheDocument();
    const ongoingCard = screen.getByText("00:00-23:59").closest("div.flex");
    expect(ongoingCard).toHaveClass("ring-2", "ring-blue-500");
    expect(screen.getByText("进行中")).toBeInTheDocument();

    // 白名单字段：教师姓名显示、教室在右侧。
    expect(screen.getByText("教师：王老师")).toBeInTheDocument();
    expect(screen.getAllByText("301教室").length).toBeGreaterThan(0);
  });

  it("hides teacher rows when the link disables show_teacher_names", async () => {
    await renderReady(schedulePayload({ show_teacher_names: false }));
    expect(screen.getByText("01:00-01:45")).toBeInTheDocument();
    expect(screen.queryByText(/教师：/)).not.toBeInTheDocument();
  });

  it("shows the sticky adjustment banner and expands the full before-after list on click", async () => {
    const user = await renderReady(schedulePayload({
      adjustments: [
        { type: "时间调整", class_name: "三年二班", course_name: "数学", before_time: "9月17日 09:00-09:45", after_time: "9月18日 10:00-10:45", before_location: "301教室", after_location: "301教室" },
        { type: "地点调整", class_name: "三年二班", course_name: "英语", before_time: "9月17日 10:00-10:40", after_time: "9月17日 10:00-10:40", before_location: "302教室", after_location: "405教室" },
      ],
    }));

    expect(screen.getByRole("button", { name: /本周有 2 条调课/ })).toBeInTheDocument();
    expect(screen.queryByText(/9月17日 09:00-09:45/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /本周有 2 条调课/ }));
    expect(screen.getByText(/9月18日 10:00-10:45/)).toBeInTheDocument();
    expect(screen.getByText(/地点 302教室 → 405教室/)).toBeInTheDocument();
  });

  it("shows the empty state for a selected day without courses", async () => {
    const user = await renderReady();
    const emptyDay = addDaysIso(TODAY, 3);
    await user.click(screen.getByRole("button", { name: new RegExp(emptyDay.slice(5).replace("-", "/")) }));
    expect(screen.getByText("该日无课")).toBeInTheDocument();
  });

  it("keeps the selected day pinned first when browsing across weeks", async () => {
    const user = await renderReady();
    const nextWeekDay = addDaysIso(TODAY, 9);
    await user.click(screen.getByRole("button", { name: "下一周" }));
    await user.click(screen.getByRole("button", { name: new RegExp(nextWeekDay.slice(5).replace("-", "/")) }));
    await user.click(screen.getByRole("button", { name: /上一周/ }));
    // 选中日被翻出当前窗口后仍置顶渲染（组标题「09/29 周X」）。
    expect(screen.getAllByText(new RegExp(nextWeekDay.slice(5).replace("-", "/"))).length).toBeGreaterThan(0);
  });

  it("opens the subscribe sheet with a Google Calendar link and copies the ICS URL", async () => {
    // userEvent.setup() 会把 navigator.clipboard 换成它自己的 stub，spy 只能打在 stub 上。
    const user = await renderReady();
    const writeText = vi.spyOn(window.navigator.clipboard, "writeText");

    // jsdom UA 不是 iOS/macOS，走弹层而不是 webcal 直跳。
    expect(mocks.fetchPublicLink).toHaveBeenCalledWith("tok-123");
    await user.click(screen.getByRole("button", { name: "订阅到日历" }));

    const googleLink = screen.getByRole("link", { name: "添加到 Google 日历" });
    const ics = "http://127.0.0.1:8000/api/v1/public/links/tok-123/calendar.ics";
    expect(googleLink).toHaveAttribute("href", `https://calendar.google.com/calendar/render?cid=${encodeURIComponent(ics)}`);
    expect(screen.getByText(/订阅后由日历应用定期刷新，Google 约 12-24 小时/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "复制 ICS 订阅链接" }));
    expect(writeText).toHaveBeenCalledWith(ics);
  });

  it("renders the school directory as display-only without the subscribe bar", async () => {
    mocks.fetchPublicLink.mockResolvedValue({
      scope: "school",
      display_name: "阳光中学课程公示",
      version_no: 3,
      published_at: "2026-09-14T08:00:00Z",
      classes: [
        { class_business_id: "CLASS-3-2", class_name: "三年级二班", session_count: 128, first_date: "2026-09-01", last_date: "2027-01-30" },
        { class_business_id: "CLASS-4-1", class_name: "四年级一班", session_count: 132, first_date: "2026-09-01", last_date: "2027-01-30" },
      ],
    });
    renderPage();
    expect(await screen.findByText("阳光中学课程公示")).toBeInTheDocument();
    expect(screen.getByText("三年级二班")).toBeInTheDocument();
    expect(screen.getByText(/128 节课/)).toBeInTheDocument();
    expect(screen.getByText(/单个班级的课表链接由学校另行发放/)).toBeInTheDocument();
    // 后端没有单班公开端点：目录项不可点，也不提供全校日历订阅入口。
    expect(screen.queryByRole("button", { name: "订阅到日历" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /三年级二班/ })).not.toBeInTheDocument();
  });

  it("explains an invalid token instead of leaking link existence", async () => {
    mocks.fetchPublicLink.mockRejectedValue(new PublicLinkUnavailableError(404));
    renderPage();
    expect(await screen.findByText("链接不存在或已失效")).toBeInTheDocument();
    expect(screen.getByText(/链接可能已被停用、轮换或过期/)).toBeInTheDocument();
  });

  it("offers a retry after a network failure", async () => {
    mocks.fetchPublicLink.mockRejectedValueOnce(new Error("fetch failed"));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("课表加载失败");
    mocks.fetchPublicLink.mockResolvedValue(schedulePayload());
    await user.click(screen.getByRole("button", { name: /重新加载/ }));
    expect(await screen.findByText("三年二班")).toBeInTheDocument();
  });
});
