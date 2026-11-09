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
  is_group: true,
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

function renderPage(context: { user: { id: string; username: string; role: "admin" | "scheduler" | "approver" | "viewer" }; scheduleAccessRole?: "viewer" | "scheduler" | "approver" } = { user: admin }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Routes>
          <Route element={<Outlet context={context} />}>
            <Route path="/" element={<MasterDataPage />} />
            <Route path="/assistant" element={<div>排课助手页面</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openTab(name: string) {
  await userEvent.click(screen.getByRole("tab", { name }));
}

describe("基础资料页", () => {
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

  it("页面标题叫「基础资料」，不再叫「主数据」", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "基础资料" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1, name: "主数据" })).not.toBeInTheDocument();
  });

  it("已有课次时「去排课」直达排课助手；原来的「去配规则」入口不再出现", async () => {
    renderPage();
    const goSchedule = await screen.findByRole("button", { name: "去排课" });
    expect(screen.queryByRole("button", { name: "去配规则" })).not.toBeInTheDocument();
    await userEvent.click(goSchedule);
    expect(await screen.findByText("排课助手页面")).toBeInTheDocument();
  });

  it("还没有课次时不展示「去排课」", async () => {
    mocks.request.mockImplementation(({ url }: { url: string }) => {
      if (url === "/api/v1/campuses") return Promise.resolve(campuses);
      if (url === "/api/v1/teachers") return Promise.resolve(teachers);
      if (url === "/api/v1/rooms") return Promise.resolve(rooms);
      if (url === "/api/v1/time-slots") return Promise.resolve(slots);
      return Promise.resolve([]);
    });
    renderPage();
    await waitFor(() => expect(screen.getByRole("tab", { name: "教师" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "去排课" })).not.toBeInTheDocument();
  });

  it("排课员（基础资料只读）仍能去排课；只读成员不展示「去排课」，并看到只读提示", async () => {
    const { unmount } = renderPage({ user: { id: "s1", username: "sched", role: "scheduler" }, scheduleAccessRole: "scheduler" });
    expect(await screen.findByRole("button", { name: "去排课" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "导入" })).not.toBeInTheDocument();
    unmount();

    renderPage({ user: { id: "v1", username: "viewer", role: "viewer" }, scheduleAccessRole: "viewer" });
    await waitFor(() => expect(screen.getByRole("tab", { name: "班级" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "去排课" })).not.toBeInTheDocument();
    await openTab("课程场次");
    expect(await screen.findByText("共享基础资料由管理员维护")).toBeInTheDocument();
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
    expect(screen.getByRole("columnheader", { name: "教师" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "教师类型" })).toBeInTheDocument();
  });

  it("管理员可以把教师在单体教师和教研组之间切换", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("columnheader", { name: "教师类型" })).toBeInTheDocument());

    const row = (await screen.findByText("郑州考研数学教研组")).closest("tr") as HTMLTableRowElement;
    expect(within(row).getByText("教研组")).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: "编辑教师" }));

    const dialog = await screen.findByRole("dialog");
    const type = within(dialog).getByLabelText("教师类型");
    expect(type).toHaveValue("group");
    await userEvent.selectOptions(type, "person");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));

    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith(expect.objectContaining({
      url: "/api/v1/teachers/teacher-0",
      method: "PUT",
      data: expect.objectContaining({ is_group: false }),
    })));
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
    // 选中集合清空后批量按钮整组消失，「全选筛选结果」仍可用。
    expect(screen.queryByText(/^已选/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "批量删除" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "批量改日期" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "清除选择" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: `全选筛选结果（${COURSE_COUNT} 条）` })).toBeEnabled();
  });

  it("页头只有「导入」和「去排课」；模板下载与直传挪进向导第 1 步", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: "去排课" })).toBeInTheDocument();
    for (const removed of ["刷新", "下载官方模板", "导入 XLSX", "智能导入"]) {
      expect(screen.queryByRole("button", { name: removed })).not.toBeInTheDocument();
    }

    const clicked: string[] = [];
    const clickSpy = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(function (this: HTMLInputElement) {
      clicked.push(this.getAttribute("aria-label") ?? "");
    });
    try {
      await userEvent.click(screen.getByRole("button", { name: "导入" }));
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByRole("button", { name: "下载官方模板" })).toBeInTheDocument();
      await userEvent.click(within(dialog).getByRole("button", { name: "已是官方模板？跳过映射直接导入" }));
      // 直传仍走页面原来的隐藏文件框（/imports/xlsx），不经过映射。
      expect(clicked).toContain("选择官方模板文件");
    } finally {
      clickSpy.mockRestore();
    }
  });

  it("没选中记录时教师工具栏只有搜索、全选、新增；选中后才出现批量按钮", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("columnheader", { name: "教师类型" })).toBeInTheDocument());

    expect(screen.getByRole("textbox", { name: "搜索教师" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全选筛选结果（4 条）" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "新增教师" })).toBeInTheDocument();
    for (const hidden of ["批量改教师类型", "批量改学科", "批量改日历账号", "批量删除", "清除选择"]) {
      expect(screen.queryByRole("button", { name: hidden })).not.toBeInTheDocument();
    }
    // 没有筛选条件时不显示「筛选 x / y 条」。
    expect(screen.queryByText(/^筛选 \d+ \/ \d+ 条$/)).not.toBeInTheDocument();

    await userEvent.click(screen.getAllByRole("checkbox", { name: "选择记录" })[0]);
    expect(screen.getByText("已选 1 条")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批量改日历账号" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "批量删除" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "清除选择" })).toBeInTheDocument();

    await userEvent.type(screen.getByRole("textbox", { name: "搜索教师" }), "数学");
    expect(screen.getByText("筛选 1 / 4 条")).toBeInTheDocument();
  });

  it("时段表用「周三 18:30-21:30」这样的人话，内部标签只在悬停时出现、仍可搜索", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("tab", { name: "时段" })).toBeInTheDocument());
    await openTab("时段");

    expect(screen.getByRole("columnheader", { name: "时段" })).toBeInTheDocument();
    for (const removed of ["时段 ID", "星期", "开始", "结束", "序号"]) {
      expect(screen.queryByRole("columnheader", { name: removed })).not.toBeInTheDocument();
    }
    const label = screen.getByText("周一 09:00-12:00");
    expect(label).toHaveAttribute("title", "S01");
    expect(screen.queryByText("S01")).not.toBeInTheDocument();

    await userEvent.type(screen.getByRole("textbox", { name: "搜索时段" }), "S01");
    expect(screen.getByText("周一 09:00-12:00")).toBeInTheDocument();

    const row = screen.getByText("周一 09:00-12:00").closest("tr") as HTMLTableRowElement;
    await userEvent.click(within(row).getByRole("button", { name: "删除" }));
    expect(await screen.findByText(/确认删除“周一 09:00-12:00”/)).toBeInTheDocument();
  });

  it("导入报告默认只露关键数字和下一步；整行丢弃警告常显，其余明细默认收起", async () => {
    const report = {
      source: "官方模板.xlsx",
      campuses: 1,
      teachers: 4,
      class_groups: 2,
      rooms: 1,
      time_slots: 1,
      course_sessions: 120,
      rules: 0,
      rows_total: 130,
      rows_dropped_placeholder_room: 6,
      dropped_lesson_groups: 2,
      dropped_classes: ["半年营一班"],
      rows_kept: 124,
      rows_deduped: 122,
      preprocessed_demands: 120,
      multi_product_demands: 3,
      multi_lesson_name_demands: 1,
      multi_slot_demands: 2,
      orphans_retained: 0,
      schedule_version_no: 5,
    };
    const listRequest = mocks.request.getMockImplementation()!;
    mocks.request.mockImplementation((config: { url: string }) => (config.url === "/api/v1/imports/xlsx" ? Promise.resolve(report) : listRequest(config)));
    renderPage();
    await waitFor(() => expect(screen.getByRole("tab", { name: "教师" })).toBeInTheDocument());

    await userEvent.upload(screen.getByLabelText("选择官方模板文件"), new File(["xlsx"], "官方模板.xlsx"));

    expect(await screen.findByText(/「教室-待校区确认」的 6 行已整行丢弃/)).toBeVisible();
    expect(screen.getByText("教室待确认丢弃")).toBeVisible();
    expect(screen.getByText("本次新建课次")).toBeVisible();
    expect(screen.getByText(/已生成草稿 V5，当前发布版本不受影响/)).toBeVisible();
    expect(screen.getByRole("link", { name: "去课表 › 历史版本审核发布" })).toHaveAttribute("href", "/schedule?view=history");
    expect(screen.queryByText(/版本管理/)).not.toBeInTheDocument();

    // 明细在 DOM 里但默认收起，点开才看得到。
    expect(screen.getByText("参与排课行数")).not.toBeVisible();
    await userEvent.click(screen.getByText("导入明细"));
    expect(screen.getByText("参与排课行数")).toBeVisible();
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
