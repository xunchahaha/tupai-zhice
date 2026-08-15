import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MasterDataPage } from "@/pages/master-data-page";

// 页面的读取走 orval 生成的客户端（统一经过 customInstance），批量写入走裸 http.post，两条都要拦。
const mocks = vi.hoisted(() => ({ request: vi.fn(), post: vi.fn() }));

vi.mock("@/api/http", () => ({
  customInstance: mocks.request,
  http: { get: vi.fn(), post: mocks.post, patch: vi.fn() },
  authStore: { get: vi.fn(), set: vi.fn(), clear: vi.fn() },
  API_BASE_URL: "http://127.0.0.1:8000",
}));

const admin = { id: "admin-id", username: "admin", role: "admin" as const };
const campuses = [{ id: "campus-1", business_id: "ZZ", name: "郑州校区" }];
const teachers = ["数学", "英语", "政治", "专业课"].map((subject, index) => ({
  id: `teacher-${index}`,
  campus_id: "campus-1",
  business_id: `郑州考研${subject}教研组`,
  name: `郑州考研${subject}教研组`,
  subject,
  calendar_user_id: null,
}));
const rooms = [{ id: "room-1", campus_id: "campus-1", business_id: "教室-211", name: "教室-211", is_active: true }];
const slots = [{ id: "slot-1", campus_id: "campus-1", business_id: "S01", weekday: "周一", start_time: "09:00", end_time: "12:00", kind: "上午", sequence: 1, is_open: true }];

// OMO4班 就是用户抱怨的那个班：同时有 含数学 / 无数学 两个班型、四个教研组。
const omo4 = {
  id: "class-omo4",
  campus_id: "campus-1",
  business_id: "暑期集训营OMO4班",
  name: "暑期集训营OMO4班",
  business_lines: ["考研"],
  product_types: ["考研·暑期强化（含数学）", "考研·暑期强化（无数学）"],
  subjects: ["数学", "政治", "英语"],
  teacher_business_ids: teachers.map((item) => item.business_id),
  session_count: 140,
  tracks: [
    { business_line: "考研", product_type: "考研·暑期强化（含数学）", subject: "数学", teacher_business_id: "郑州考研数学教研组", session_count: 48 },
    { business_line: "考研", product_type: "考研·暑期强化（无数学）", subject: "政治", teacher_business_id: "郑州考研政治教研组", session_count: 45 },
    { business_line: "考研", product_type: "考研·暑期强化（无数学）", subject: "英语", teacher_business_id: "郑州考研英语教研组", session_count: 47 },
  ],
};
// 手工新建、还没有课次的班：聚合结果全空，UI 必须说「暂无课次」而不是留白。
const emptyClass = {
  id: "class-empty",
  campus_id: "campus-1",
  business_id: "新建待排班",
  name: "新建待排班",
  business_lines: [],
  product_types: [],
  subjects: [],
  teacher_business_ids: [],
  session_count: 0,
  tracks: [],
};

const COURSE_COUNT = 1200;
const courses = Array.from({ length: COURSE_COUNT }, (_, index) => ({
  id: `course-${index}`,
  campus_id: "campus-1",
  business_id: `KC-${index}`,
  business_line: "考研",
  product_type: index % 2 === 0 ? "考研·暑期强化（含数学）" : "考研·暑期强化（无数学）",
  class_business_id: "暑期集训营OMO4班",
  teacher_business_id: index % 2 === 0 ? "郑州考研数学教研组" : "郑州考研英语教研组",
  subject: index % 2 === 0 ? "数学" : "英语",
  lesson_name: "第一讲",
  lesson_date: "2026-08-20",
  session_no: index + 1,
  fixed_start_time: "09:00",
  fixed_end_time: "12:00",
  original_room_business_id: "教室-211",
  is_locked: false,
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Routes>
          <Route element={<Outlet context={{ user: admin }} />}>
            <Route path="/" element={<MasterDataPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openTab(name: string) {
  await userEvent.click(screen.getByRole("tab", { name }));
}

describe("主数据页", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.post.mockReset();
    mocks.post.mockResolvedValue({ data: { affected_count: COURSE_COUNT } });
    mocks.request.mockReset();
    mocks.request.mockImplementation(({ url }: { url: string }) => {
      if (url === "/api/v1/campuses") return Promise.resolve(campuses);
      if (url === "/api/v1/teachers") return Promise.resolve(teachers);
      if (url === "/api/v1/class-groups") return Promise.resolve([omo4, emptyClass]);
      if (url === "/api/v1/rooms") return Promise.resolve(rooms);
      if (url === "/api/v1/time-slots") return Promise.resolve(slots);
      if (url === "/api/v1/course-sessions") return Promise.resolve(courses);
      return Promise.resolve([]);
    });
  });

  it("班级列出全部班型和全部教师，多值折叠后可以展开看全量", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("tab", { name: "班级" })).toBeInTheDocument());
    await openTab("班级");

    const row = (await screen.findByText("暑期集训营OMO4班")).closest("tr") as HTMLTableRowElement;
    // 两个班型都要在，不能只显示胜出的那一个。
    expect(within(row).getByText("考研·暑期强化（含数学）")).toBeInTheDocument();
    expect(within(row).getByText("考研·暑期强化（无数学）")).toBeInTheDocument();
    // 四个教研组：默认显示 2 个 + 「+2」，展开后四个都在。
    expect(within(row).getByText("郑州考研数学教研组")).toBeInTheDocument();
    expect(within(row).queryByText("郑州考研专业课教研组")).not.toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: "+2" }));
    expect(within(row).getByText("郑州考研专业课教研组")).toBeInTheDocument();
  });

  it("没有课次的班级显式显示「暂无课次」，不是留白", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("tab", { name: "班级" })).toBeInTheDocument());
    await openTab("班级");

    const row = (await screen.findByText("新建待排班")).closest("tr") as HTMLTableRowElement;
    expect(within(row).getAllByText("暂无课次").length).toBeGreaterThan(0);
  });

  it("走班明细按「班型 × 科目」列出真实授课教师", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("tab", { name: "班级" })).toBeInTheDocument());
    await openTab("班级");

    const row = (await screen.findByText("暑期集训营OMO4班")).closest("tr") as HTMLTableRowElement;
    await userEvent.click(within(row).getByRole("button", { name: "3 条轨道" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("郑州考研政治教研组")).toBeInTheDocument();
    expect(within(dialog).getByText("48")).toBeInTheDocument();
  });

  it("列名与表单文案里不再出现「教师（教研组）」", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("tab", { name: "教师" })).toBeInTheDocument());
    expect(screen.queryByText("教师（教研组）")).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /^教师/ })).toBeInTheDocument();
  });

  it("课程场次可以全选超过 1000 条，并按筛选条件整批删除", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("tab", { name: "课程场次" })).toBeInTheDocument());
    await openTab("课程场次");

    await userEvent.click(await screen.findByRole("button", { name: `全选筛选结果（${COURSE_COUNT} 条）` }));
    expect(screen.getByText(`已选 全部 ${COURSE_COUNT} 条`)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "批量删除" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/当前筛选结果的全部/)).toBeInTheDocument();
    // 影响面超过阈值时必须再打一次勾，一次点击清空全库的操作不能只有一层确认。
    const confirm = within(dialog).getByRole("button", { name: `确认删除 ${COURSE_COUNT} 条` });
    expect(confirm).toBeDisabled();
    await userEvent.click(within(dialog).getByRole("checkbox", { name: /我确认删除这 1200 条课程/ }));
    await userEvent.click(confirm);

    expect(mocks.post).toHaveBeenCalledWith("/api/v1/course-sessions/batch-delete", {
      filter: {},
      expected_count: COURSE_COUNT,
    });
  });

  it("筛选后全选，发出的 filter 与页面上的筛选条件一致", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("tab", { name: "课程场次" })).toBeInTheDocument());
    await openTab("课程场次");

    await userEvent.selectOptions(screen.getByLabelText("全部教师"), "郑州考研英语教研组");
    const half = COURSE_COUNT / 2;
    await userEvent.click(await screen.findByRole("button", { name: `全选筛选结果（${half} 条）` }));
    await userEvent.click(screen.getByRole("button", { name: "批量改教室" }));

    const dialog = await screen.findByRole("dialog");
    await userEvent.selectOptions(within(dialog).getByLabelText("新的授课教室"), "教室-211");
    await userEvent.click(within(dialog).getByRole("button", { name: `确认修改 ${half} 条` }));

    expect(mocks.post).toHaveBeenCalledWith("/api/v1/course-sessions/batch-update", {
      filter: { teacher_business_id: "郑州考研英语教研组" },
      expected_count: half,
      original_room_business_id: "教室-211",
    });
  });

  it("表头复选框与「全选筛选结果」同语义：取消一次清空整个选中集合", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("tab", { name: "课程场次" })).toBeInTheDocument());
    await openTab("课程场次");

    await userEvent.click(await screen.findByRole("button", { name: `全选筛选结果（${COURSE_COUNT} 条）` }));
    const header = screen.getByRole("checkbox", { name: "选择全部筛选结果" });
    expect(header).toBeChecked();

    await userEvent.click(header);
    expect(screen.getByText("已选 0 条")).toBeInTheDocument();
  });

  it("逐条选择超过 1000 条时禁用批量操作并指路「全选筛选结果」", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("tab", { name: "课程场次" })).toBeInTheDocument());
    await openTab("课程场次");

    await userEvent.click(await screen.findByRole("button", { name: `全选筛选结果（${COURSE_COUNT} 条）` }));
    // 从全选里剔掉一条：filter 表达不了「全选后再排除」，object_ids 又超了 1000。
    await userEvent.click(screen.getAllByRole("checkbox", { name: "选择记录" })[0]);

    expect(screen.getByText(`已选 ${COURSE_COUNT - 1} 条`)).toBeInTheDocument();
    expect(screen.getByText(/逐条选择单次上限 1000 条/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批量删除" })).toBeDisabled();
  });
});
