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
  it("全部就绪时不渲染", () => {
    renderUi(<SetupChecklist {...readyState} />);
    expect(screen.queryByLabelText("排课准备清单")).not.toBeInTheDocument();
  });

  it("只列未完成的必需项，用动作措辞直达；AI 是可选项，不算「还差」", () => {
    renderUi(<SetupChecklist {...emptyState} />);
    const row = screen.getByLabelText("排课准备清单");
    expect(row).toHaveTextContent("排课准备还差 3 项：");
    const links = within(row).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["先导入课程资料", "去配规则", "去发布"]);
    // 首次使用：资料没导入时明确提示先导入课程资料；发布去课表的历史版本。
    expect(within(row).getByRole("link", { name: "先导入课程资料" })).toHaveAttribute("href", "/master-data");
    expect(within(row).getByRole("link", { name: "先导入课程资料" })).toHaveClass("text-amber-700");
    expect(within(row).getByRole("link", { name: "去配规则" })).toHaveAttribute("href", "/rules");
    expect(within(row).getByRole("link", { name: "去发布" })).toHaveAttribute("href", "/schedule?view=history");
    // AI 未配置由同屏需求卡提示（含「去配置」），清单不说第二遍。
    expect(within(row).queryByText(/AI/)).not.toBeInTheDocument();
  });

  it("其它都就绪、只是没配 AI 时不渲染（不出现「还差 1 项：去配置 AI（可选）」）", () => {
    renderUi(<SetupChecklist {...readyState} aiConfigured={false} />);
    expect(screen.queryByLabelText("排课准备清单")).not.toBeInTheDocument();
  });

  it("已完成的项不再出现；AI 探测中（null）不列", () => {
    renderUi(
      <SetupChecklist
        masterDataImported={true}
        activeRuleCount={0}
        aiConfigured={null}
        publishedScheduleCount={0}
      />,
    );
    const row = screen.getByLabelText("排课准备清单");
    expect(row).toHaveTextContent("排课准备还差 2 项：");
    const links = within(row).getAllByRole("link");
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute("href", "/rules");
    expect(links[1]).toHaveAttribute("href", "/schedule?view=history");
    expect(within(row).queryByText(/AI/)).not.toBeInTheDocument();
    // 资料已导入，不再出现「先导入课程资料」的首次使用提示。
    expect(within(row).queryByText("先导入课程资料")).not.toBeInTheDocument();
  });

  it("只差规则时只剩一项「去配规则」", () => {
    renderUi(<SetupChecklist {...readyState} activeRuleCount={0} />);
    const row = screen.getByLabelText("排课准备清单");
    expect(row).toHaveTextContent("排课准备还差 1 项：去配规则");
    expect(within(row).getAllByRole("link")).toHaveLength(1);
  });

  it("hasPendingSetup 只在有未完成项时为真，AI 探测中不算未完成", () => {
    expect(hasPendingSetup(readyState)).toBe(false);
    expect(hasPendingSetup({ ...readyState, masterDataImported: false })).toBe(true);
    expect(hasPendingSetup({ ...readyState, activeRuleCount: 0 })).toBe(true);
    expect(hasPendingSetup({ ...readyState, publishedScheduleCount: 0 })).toBe(true);
    // AI 是可选项：未配置或探测中都不算未完成。
    expect(hasPendingSetup({ ...readyState, aiConfigured: false })).toBe(false);
    expect(hasPendingSetup({ ...readyState, aiConfigured: null })).toBe(false);
  });
});
