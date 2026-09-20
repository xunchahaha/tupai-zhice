import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ShareDialog } from "@/components/public/share-dialog";

vi.mock("@/api/http", () => ({
  API_BASE_URL: "http://127.0.0.1:8000",
  customInstance: vi.fn(),
  http: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  authStore: { get: vi.fn(), set: vi.fn(), clear: vi.fn() },
  scheduleSetStore: { get: vi.fn(), set: vi.fn(), clear: vi.fn() },
}));

vi.mock("sonner", () => {
  const toast = { success: vi.fn(), error: vi.fn(), warning: vi.fn() };
  return { toast };
});

import { toast } from "sonner";

// public_url 里的 token 只有创建/轮换响应能拿到一次，这里用固定样例验证分享面。
const LINK = {
  displayName: "三年二班",
  publicUrl: "http://school.example.com/public/t/abc123DEF",
};

function renderDialog(link: typeof LINK | null = LINK) {
  return render(<ShareDialog open onOpenChange={() => {}} link={link} />);
}

describe("ShareDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(cleanup);

  it("renders a QR code for the H5 url plus all shareable links", () => {
    renderDialog();

    const qr = screen.getByTestId("public-share-qr");
    expect(qr.querySelector("svg")).not.toBeNull();
    expect(screen.getByLabelText("H5 页面链接")).toHaveValue(LINK.publicUrl);
    // webcal 与 https 指向同一 token 路径，只换 scheme。
    expect(screen.getByLabelText("日历订阅（webcal）")).toHaveValue("webcal://127.0.0.1:8000/api/v1/public/links/abc123DEF/calendar.ics");
    expect(screen.getByLabelText("ICS 文件直链（https）")).toHaveValue("http://127.0.0.1:8000/api/v1/public/links/abc123DEF/calendar.ics");
  });

  it("copies each link through the clipboard", async () => {
    // userEvent.setup() 会把 navigator.clipboard 换成它自己的 stub，spy 只能打在 stub 上。
    const user = userEvent.setup();
    const writeText = vi.spyOn(window.navigator.clipboard, "writeText");
    renderDialog();

    await user.click(screen.getByRole("button", { name: "复制 H5 链接" }));
    await user.click(screen.getByRole("button", { name: "复制订阅链接" }));
    await user.click(screen.getByRole("button", { name: "复制 ICS 链接" }));

    expect(writeText).toHaveBeenNthCalledWith(1, LINK.publicUrl);
    expect(writeText).toHaveBeenNthCalledWith(2, "webcal://127.0.0.1:8000/api/v1/public/links/abc123DEF/calendar.ics");
    expect(writeText).toHaveBeenNthCalledWith(3, "http://127.0.0.1:8000/api/v1/public/links/abc123DEF/calendar.ics");
    expect(toast.success).toHaveBeenCalledTimes(3);
  });

  it("degrades to an error toast when the clipboard is unavailable", async () => {
    const user = userEvent.setup();
    vi.spyOn(window.navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    renderDialog();

    await user.click(screen.getByRole("button", { name: "复制 H5 链接" }));
    expect(toast.error).toHaveBeenCalled();
  });

  it("renders nothing without a shareable link", () => {
    const { container } = renderDialog(null);
    expect(container).toBeEmptyDOMElement();
  });
});
