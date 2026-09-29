import { cleanup, render, screen } from "@testing-library/react";
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
    // 涉事规则只列冲突的两条，无关规则不出现。
    expect(screen.getByText("T9 周一上午不排课")).toBeInTheDocument();
    expect(screen.getByText("B1 班每天不超过 6 节")).toBeInTheDocument();
    expect(screen.queryByText("无关规则")).not.toBeInTheDocument();
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
});
