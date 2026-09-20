import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SetupChecklist, useAiConfigurationProbe } from "@/components/setup-checklist";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("@/api/http", () => ({ http: { get: mocks.get } }));

function renderUi(ui: ReactElement) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/overview"]}>{ui}</MemoryRouter>
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

/** useAiConfigurationProbe 的最小消费组件，把探测结果翻译成可查询文本。 */
function ProbeConsumer() {
  const aiConfigured = useAiConfigurationProbe();
  if (aiConfigured === null) return <p>探测中</p>;
  return <p>{aiConfigured ? "AI 已配置" : "AI 未配置"}</p>;
}

describe("SetupChecklist", () => {
  afterEach(cleanup);
  beforeEach(() => {
    mocks.get.mockReset();
  });

  it("完整版全部就绪时展示 4/4，不再出现去完成链接", () => {
    renderUi(<SetupChecklist {...readyState} />);
    expect(screen.getByText("4/4 已就绪")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "去配规则" })).not.toBeInTheDocument();
    expect(screen.queryByText("可选")).not.toBeInTheDocument();
  });

  it("完整版按数据驱动四项，未完成项带直达链接，AI 未配置只是可选项", () => {
    renderUi(<SetupChecklist {...emptyState} />);
    expect(screen.getByText("0/4 已就绪")).toBeInTheDocument();

    expect(screen.getByRole("link", { name: "去导入" })).toHaveAttribute("href", "/master-data");
    expect(screen.getByRole("link", { name: "去配规则" })).toHaveAttribute("href", "/rules");
    expect(screen.getByRole("link", { name: "去发布" })).toHaveAttribute("href", "/versions");
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
    // AI 探测中（null）也按可选项呈现，不算错误。
    expect(within(row).getByText("可选")).toBeInTheDocument();
  });

  it("AI 探测成功时该项按已就绪显示", async () => {
    mocks.get.mockResolvedValue({ data: { configured: true } });
    renderUi(<ProbeConsumer />);
    expect(await screen.findByText("AI 已配置")).toBeInTheDocument();
  });

  it("AI 探测失败不算错误，回落为未配置（可选）", async () => {
    mocks.get.mockRejectedValue(new Error("network down"));
    renderUi(<ProbeConsumer />);
    await waitFor(() => expect(screen.getByText("AI 未配置")).toBeInTheDocument());
  });
});
