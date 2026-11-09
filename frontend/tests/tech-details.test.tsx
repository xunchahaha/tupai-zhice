import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TechDetails } from "@/components/tech-details";

describe("TechDetails", () => {
  afterEach(cleanup);

  it("默认收起：摘要默认是「技术细节」，内容在 DOM 中但不可见", () => {
    render(<TechDetails>求解编号 run-7f3a</TechDetails>);

    const summary = screen.getByText("技术细节");
    const body = screen.getByText("求解编号 run-7f3a");
    expect(summary).toBeVisible();
    expect(body).toBeInTheDocument();
    expect(body).not.toBeVisible();
    expect(summary.closest("details")).not.toHaveAttribute("open");
  });

  it("点击摘要后内容可见", () => {
    render(<TechDetails summary="原始记录">{'{"action":"publish"}'}</TechDetails>);

    const summary = screen.getByText("原始记录");
    fireEvent.click(summary);

    expect(summary.closest("details")).toHaveAttribute("open");
    expect(screen.getByText('{"action":"publish"}')).toBeVisible();
  });

  it("className 落在外层 details 上", () => {
    render(<TechDetails className="mt-2">内容</TechDetails>);

    const details = screen.getByText("技术细节").closest("details");
    expect(details).toHaveClass("mt-2");
    expect(details).toHaveClass("group");
  });
});
