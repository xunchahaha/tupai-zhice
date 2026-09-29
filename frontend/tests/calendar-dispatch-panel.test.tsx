import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CalendarDispatchPanel } from "@/components/calendar-dispatch-panel";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("@/api/http", () => ({ http: { post: mocks.post } }));
vi.mock("sonner", () => ({ toast: { success: mocks.success, error: mocks.error } }));

const published = { id: "s1", name: "基线课表", status: "published" };
const draft = { id: "s2", name: "求解版本 v2", status: "draft" };

function result(overrides: Record<string, unknown> = {}) {
  return {
    dry_run: true,
    would_publish: 12,
    published: 0,
    existing: 3,
    skipped_unmapped: 0,
    conflict_count: 0,
    conflicts: [],
    ...overrides,
  };
}

describe("CalendarDispatchPanel", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.post.mockReset();
    mocks.success.mockReset();
    mocks.error.mockReset();
  });

  it("offers nothing to dispatch when there is no schedule", () => {
    render(<CalendarDispatchPanel schedule={undefined} />);

    expect(screen.getByText(/当前没有可下发课表/)).toBeVisible();
    expect(screen.getByRole("button", { name: "忙闲预检" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "确认下发" })).toBeDisabled();
  });

  it("runs a free/busy pre-check as a dry run and reports the counts", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: result({ conflict_count: 1, skipped_unmapped: 2, conflicts: [{ course_session_id: "12345678-abcd", calendar_user_id: "ou_teacher", lesson_date: "2026-10-14", start_time: "19:00", end_time: "20:30", source: "feishu_freebusy" }] }) });
    render(<CalendarDispatchPanel schedule={published} />);

    expect(screen.getByText(/当前课表：基线课表/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "忙闲预检" }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith("/api/v1/schedules/s1/calendar-publish", {
        calendar_id: "primary",
        need_notification: true,
        dry_run: true,
      }),
    );
    expect(mocks.success).toHaveBeenCalledWith("预检完成：预计下发 12 个日程，发现 1 个冲突");
    expect(await screen.findByText("仅预检")).toBeVisible();
    expect(screen.getByText("预计下发").nextSibling).toHaveTextContent("12");
    expect(screen.getByText("已存在").nextSibling).toHaveTextContent("3");
    expect(screen.getByText("待补账号").nextSibling).toHaveTextContent("2");
    expect(screen.getByText("冲突告警").nextSibling).toHaveTextContent("1");
    expect(screen.getByText(/未映射具体日历账号的课程已跳过/)).toBeVisible();
    expect(screen.getByText("冲突明细（正式下发仍会创建并标记冲突）")).toBeVisible();
    expect(screen.getByText(/2026-10-14 19:00-20:30 \/ ou_teacher \/ 课程 12345678 \/ 日历已有忙碌/)).toBeVisible();
  });

  it("dispatches for real only when confirmed on an in-use version", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: result({ dry_run: false, published: 12, conflicts: [] }) });
    render(<CalendarDispatchPanel schedule={published} />);

    const confirm = screen.getByRole("button", { name: "确认下发" });
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith("/api/v1/schedules/s1/calendar-publish", expect.objectContaining({ dry_run: false })),
    );
    expect(mocks.success).toHaveBeenCalledWith("已下发 12 个日程，发现 0 个冲突");
    expect(await screen.findByText("正式下发")).toBeVisible();
    expect(screen.getByText("本次发布").nextSibling).toHaveTextContent("12");
  });

  it("keeps formal dispatch off for a draft but still allows the pre-check", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: result() });
    render(<CalendarDispatchPanel schedule={draft} />);

    expect(screen.getByText(/草稿不会下发到教师日历，需先由有审批权限的人发布/)).toBeVisible();
    expect(screen.getByRole("button", { name: "确认下发" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "忙闲预检" }));
    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith("/api/v1/schedules/s2/calendar-publish", expect.objectContaining({ dry_run: true })),
    );
    // 草稿只做预检，没有任何一次正式下发请求。
    expect(mocks.post.mock.calls.filter(([, body]) => (body as { dry_run: boolean }).dry_run === false)).toHaveLength(0);
  });

  it("does not offer formal dispatch for an archived version either", () => {
    render(<CalendarDispatchPanel schedule={{ id: "s3", name: "旧版本", status: "archived" }} />);

    expect(screen.getByRole("button", { name: "确认下发" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "忙闲预检" })).toBeEnabled();
  });

  it("disables both actions while a request is in flight", async () => {
    const user = userEvent.setup();
    let release: (value: unknown) => void = () => undefined;
    mocks.post.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    render(<CalendarDispatchPanel schedule={published} />);

    await user.click(screen.getByRole("button", { name: "确认下发" }));

    expect(await screen.findByRole("button", { name: "正在下发" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "忙闲预检" })).toBeDisabled();
    release({ data: result({ dry_run: false }) });
    await waitFor(() => expect(screen.getByRole("button", { name: "确认下发" })).toBeEnabled());
  });

  it("surfaces the server's refusal and leaves the previous result alone", async () => {
    const user = userEvent.setup();
    mocks.post.mockRejectedValue({ response: { data: { detail: "飞书日历尚未授权" } } });
    render(<CalendarDispatchPanel schedule={published} />);

    await user.click(screen.getByRole("button", { name: "忙闲预检" }));

    await waitFor(() => expect(mocks.error).toHaveBeenCalledWith("飞书日历尚未授权"));
    expect(mocks.success).not.toHaveBeenCalled();
    expect(screen.queryByText("仅预检")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "忙闲预检" })).toBeEnabled();
  });
});
