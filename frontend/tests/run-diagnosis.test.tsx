import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RunDiagnosis } from "@/components/diagnosis/run-diagnosis";

const mocks = vi.hoisted(() => ({
  runs: [] as Array<Record<string, unknown>>,
  rules: [] as Array<Record<string, unknown>>,
}));

vi.mock("@/api/generated/client", () => ({
  useListSolverRunsApiV1SolverRunsGet: () => ({ data: mocks.runs, isPending: false, isError: false, refetch: vi.fn() }),
  useListRulesApiV1RulesGet: () => ({ data: mocks.rules, isPending: false, isError: false, refetch: vi.fn() }),
}));

function ruleFixture(businessId: string, sourceText: string) {
  return {
    id: `id-${businessId}`,
    business_id: businessId,
    source_text: sourceText,
    actor_type: "teacher",
    actor_ids: ["T9"],
    constraint_type: "forbidden_slot",
    status: "active",
  };
}

function runFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: "run-infeasible-0001",
    status: "completed",
    model_status: "INFEASIBLE",
    conflict_rule_ids: ["R-1", "R-2"],
    priority_rule_ids: ["R-2"],
    priority_explanations: ["建议优先保留教师禁排"],
    created_at: "2026-09-20T09:00:00+08:00",
    ...overrides,
  };
}

const RULES = [ruleFixture("R-1", "T9 周一上午不排课"), ruleFixture("R-2", "B1 班每天不超过 6 节"), ruleFixture("R-3", "无关规则")];

afterEach(cleanup);

describe("RunDiagnosis", () => {
  it("shows the hint, the priority advice and only the conflicting rules with a deep link to each rule", () => {
    render(
      <MemoryRouter>
        <RunDiagnosis run={runFixture() as never} rules={RULES as never} />
      </MemoryRouter>,
    );
    expect(screen.getByText("冲突核心分析")).toBeInTheDocument();
    expect(screen.getByText(/求解器在数学层面已证明/)).toBeInTheDocument();
    expect(screen.getByText("建议调整优先级")).toBeInTheDocument();
    expect(screen.getByText("建议优先保留教师禁排")).toBeInTheDocument();
    // 只读视图保留状态徽标；完整任务 ID 不再外露。
    expect(screen.getByText("已证明无解")).toBeInTheDocument();
    expect(screen.queryByText(/run-infeasible-0001/)).not.toBeInTheDocument();
    // 涉事规则只列冲突的两条，以规则原文为标题，编号降为小字；无关规则不出现。
    const articles = screen.getAllByRole("article");
    expect(articles).toHaveLength(2);
    expect(within(articles[0]).getByText("T9 周一上午不排课")).toBeInTheDocument();
    expect(within(articles[0]).getByText("R-1")).toHaveClass("text-zinc-400");
    expect(within(articles[1]).getByText("B1 班每天不超过 6 节")).toBeInTheDocument();
    expect(screen.queryByText("无关规则")).not.toBeInTheDocument();
    // 建议优先保留的规则也写原文，不写编号。
    expect(screen.getAllByText("B1 班每天不超过 6 节")).toHaveLength(2);
    // 规则页按 ?rule= 定位高亮。
    const links = screen.getAllByRole("link", { name: "查看规则" });
    expect(links.map((link) => link.getAttribute("href"))).toEqual(["/rules?rule=R-1", "/rules?rule=R-2"]);
  });

  it("falls back to the built-in hard-constraint notice when no user rule is involved", () => {
    render(
      <MemoryRouter>
        <RunDiagnosis run={runFixture({ conflict_rule_ids: [], priority_rule_ids: [], priority_explanations: [] }) as never} rules={RULES as never} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/系统内置硬约束生效提示/)).toBeInTheDocument();
    expect(screen.queryByText("建议调整优先级")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "查看规则" })).not.toBeInTheDocument();
  });

  it("embedded in the stuck card: no repeated hint or status badge, ids and solver notes turned into plain words", () => {
    render(
      <MemoryRouter>
        <RunDiagnosis
          embedded
          run={runFixture({
            priority_rule_ids: ["R-2", "R-1"],
            priority_explanations: [
              "约束范围：TASK-2e60acb7-forbidden_slot_free-1；系统约束：SYSTEM-TEACHER-NO-OVERLAP",
              "日期感知模型未使用可供 CP-SAT 提取的假设文字；本次无解按内置硬约束标记。",
              "时段 SLOT-周三-1830-2130 最多容纳 1 场；示例课次：CAMPUS-ZZ-34FB7F8AE46BC344。",
            ],
          }) as never}
          rules={[ruleFixture("R-1", "T9 周一上午不排课"), ruleFixture("R-2", "B1 班每天不超过 6 节，周末两天合计也不能超过十节课")] as never}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("冲突核心分析")).toBeInTheDocument();
    // 宿主红框已说明「已证明无解」：诊断提示和红色状态徽标不再重复，也没有任务 ID。
    expect(screen.queryByText(/求解器在数学层面已证明/)).not.toBeInTheDocument();
    expect(screen.queryByText("已证明无解")).not.toBeInTheDocument();
    expect(screen.queryByText(/任务 ID/)).not.toBeInTheDocument();
    // 正文：编号换成人话，引擎自述不列出。
    expect(screen.getByText(/约束范围：本次任务要求/)).toBeVisible();
    expect(screen.getByText("时段 周三 18:30-21:30 最多容纳 1 场。")).toBeVisible();
    // 原文收进默认收起的「技术细节」。
    expect(screen.getByText("技术细节")).toBeInTheDocument();
    expect(screen.getByText(/示例课次：CAMPUS-ZZ-34FB7F8AE46BC344/)).not.toBeVisible();
    expect(screen.getByText(/日期感知模型未使用/)).not.toBeVisible();
    // 规则以原文为标题；建议保留的规则徽标超过 20 字截断，悬停看全文。
    const long = "B1 班每天不超过 6 节，周末两天合计也不能超过十节课";
    expect(screen.getByTitle(long)).toHaveTextContent(`${long.slice(0, 20)}…`);
    expect(screen.getAllByRole("article")[1]).toHaveTextContent(long);
  });
});
