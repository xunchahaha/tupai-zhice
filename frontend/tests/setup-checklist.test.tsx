import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { SetupChecklist } from "@/components/setup-checklist";
import { hasPendingSetup } from "@/lib/setup-state";

function renderUi(ui: ReactElement) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/assistant"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const readyState = {
  masterDataImported: true,
  activeRuleCount: 2,
  aiConfigured: true,
  publishedScheduleCount: 1,
};

const emptyState = {
  masterDataImported: false,
  activeRuleCount: 0,
  aiConfigured: false,
  publishedScheduleCount: 0,
};

describe("SetupChecklist", () => {
  afterEach(cleanup);
  it("完整版全部就绪时展示 4/4，不再出现去完成链接", () => {
    renderUi(<SetupChecklist {...readyState} />);
    expect(screen.getByText("4/4 已就绪")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "去配规则" })).not.toBeInTheDocument();
    expect(screen.queryByText("可选")).not.toBeInTheDocument();
  });

  it("完整版按数据驱动四项，未完成项带直达链接，AI 未配置只是可选项", () => {
    renderUi(<SetupChecklist {...emptyState} />);
    expect(screen.getByText("0/4 已就绪")).toBeInTheDocument();

    // 首次使用：资料没导入时明确提示先导入课程资料；发布去课表的历史版本。
    expect(screen.getByRole("link", { name: "先导入课程资料" })).toHaveAttribute("href", "/master-data");
    expect(screen.getByRole("link", { name: "去配规则" })).toHaveAttribute("href", "/rules");
    expect(screen.getByRole("link", { name: "去发布" })).toHaveAttribute("href", "/schedule?view=history");
    // AI 未配置不是错误态：标「可选」，链接去 AI 配置区。
    expect(screen.getByText("可选")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去配置" })).toHaveAttribute("href", "/settings?section=ai");
  });

  it("紧凑版单行呈现四项，完成项与可选项状态可区分", () => {
    renderUi(
      <SetupChecklist
        variant="compact"
        masterDataImported={true}
        activeRuleCount={0}
        aiConfigured={null}
        publishedScheduleCount={0}
      />,
    );
    const row = screen.getByLabelText("排课准备清单");
    const links = within(row).getAllByRole("link");
    expect(links).toHaveLength(4);
    expect(links[0]).toHaveAttribute("href", "/master-data");
    expect(links[3]).toHaveAttribute("href", "/schedule?view=history");
    // AI 探测中（null）也按可选项呈现，不算错误。
    expect(within(row).getByText("可选")).toBeInTheDocument();
    // 资料已导入，不再出现「先导入课程资料」的首次使用提示。
    expect(within(row).queryByText("先导入课程资料")).not.toBeInTheDocument();
  });

  it("紧凑版在资料未导入时高亮首次使用提示，文案是「基础资料已导入」", () => {
    renderUi(<SetupChecklist variant="compact" {...emptyState} aiConfigured={null} />);
    const row = screen.getByLabelText("排课准备清单");
    expect(within(row).getByText("基础资料已导入")).toBeInTheDocument();
    expect(within(row).getByText("先导入课程资料")).toBeInTheDocument();
  });

  it("hasPendingSetup 只在有未完成项时为真，AI 探测中不算未完成", () => {
    expect(hasPendingSetup(readyState)).toBe(false);
    expect(hasPendingSetup({ ...readyState, masterDataImported: false })).toBe(true);
    expect(hasPendingSetup({ ...readyState, activeRuleCount: 0 })).toBe(true);
    expect(hasPendingSetup({ ...readyState, publishedScheduleCount: 0 })).toBe(true);
    // AI 明确未配置才算未完成；探测中（null）不让清单在结果回来前闪一下。
    expect(hasPendingSetup({ ...readyState, aiConfigured: false })).toBe(true);
    expect(hasPendingSetup({ ...readyState, aiConfigured: null })).toBe(false);
  });
});
