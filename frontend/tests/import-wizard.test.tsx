import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ImportWizard } from "@/components/import-wizard";

// 向导走 orval 生成的 preview/commit hooks，统一经过 customInstance，拦它就拦住了全部请求。
const mocks = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("@/api/http", () => ({
  customInstance: mocks.request,
  http: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  authStore: { get: vi.fn(), set: vi.fn(), clear: vi.fn() },
  API_BASE_URL: "http://127.0.0.1:8000",
}));

const XLSX_FILE = new File(["fake-xlsx-bytes"], " 名单.xlsx".trim(), { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });

// 第一次 preview（不带 mapping）：自动建议。神秘列零置信未匹配，上课时段缺失。
const previewSuggestion = {
  source: "名单.xlsx",
  sheets: [
    { name: "课表数据源", row_count: 12, column_count: 3, nonempty_cells: 33 },
    { name: "填表说明", row_count: 3, column_count: 2, nonempty_cells: 4 },
  ],
  selected_sheet: "课表数据源",
  header_row_index: 0,
  header_candidates: [{ row_index: 0, score: 0.9, sample: ["班级标签", "上课日期", "神秘列"] }],
  mapping: [
    { column: "班级标签", column_index: 0, target: "集训营班级标签", confidence: 1, rationale: "表头与规范字段「集训营班级标签」完全一致", matched_by: "exact", sample_values: ["OMO4班", "OMO4班", "OMO5班"] },
    { column: "上课日期", column_index: 1, target: "上课日期", confidence: 0.95, rationale: "表头命中别名表：「上课日期」→「上课日期」", matched_by: "alias", sample_values: ["2026-08-20", "2026-08-21"] },
    { column: "神秘列", column_index: 2, target: null, confidence: 0, rationale: "表头没有命中任何规范字段、别名或相近文本", matched_by: "unmatched", sample_values: ["甲", "乙"] },
  ],
  unmatched_columns: ["神秘列"],
  missing_fields: ["上课时段"],
  issues: [],
  stats: { rows_total: 10, rows_valid: 0, rows_skipped: 0, rows_ignored_blank: 1, columns_total: 3, mapped_columns: 2, ai_mapping_used: false },
};

// 第二次 preview（带 mapping_json）：按确认后的映射重跑的行级校验。
const previewValidated = {
  ...previewSuggestion,
  issues: [
    { 行号: 4, 原因: "缺少上课日期或上课时段" },
    { 行号: 7, 原因: "上课日期无法解析：2026/13/01" },
  ],
  stats: { ...previewSuggestion.stats, rows_valid: 8, rows_skipped: 2 },
};

// 第三次 preview（带 cell_overrides）：行内修复生效，错误清零。
const previewFixed = {
  ...previewValidated,
  issues: [],
  stats: { ...previewValidated.stats, rows_valid: 10, rows_skipped: 0, overrides_applied: 2 },
  historical_match: false,
  ignored_overrides: 0,
};

const commitResult = {
  source: "名单.xlsx",
  campuses: 1,
  teachers: 2,
  class_groups: 1,
  rooms: 1,
  time_slots: 0,
  course_sessions: 10,
  rules: 0,
  mode: "upsert",
  course_sessions_updated: 3,
  course_sessions_skipped_existing: 0,
  overrides_applied: 2,
  ignored_overrides: 0,
};

function requestUrl(call: unknown[]): string {
  return (call[0] as { url: string }).url;
}

function formField(call: unknown[], field: string): string {
  return String((call[0] as { data: FormData }).data.get(field));
}

function renderWizard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onCommitted = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <ImportWizard open onOpenChange={onOpenChange} onCommitted={onCommitted} />
    </QueryClientProvider>,
  );
  return { onCommitted, onOpenChange };
}

// 文件选择框在真实 UI 里是隐藏的（由拖拽区和「重新选择」按钮触发），上传时跳过指针可见性检查。
const user = userEvent.setup({ pointerEventsCheck: 0 });

async function uploadAndOpenMapping() {
  await user.upload(screen.getByLabelText("选择导入文件"), XLSX_FILE);
  await screen.findByText("课表数据源");
  await user.click(screen.getByRole("button", { name: "下一步：确认列映射" }));
  await screen.findByText("目标字段");
}

describe("智能导入向导", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.request.mockReset();
    mocks.request.mockImplementation((config: { url: string; data: FormData }) => {
      if (config.url === "/api/v1/imports/preview") {
        // 带 cell_overrides 的是修复后的重新校验：错误清零。
        if (config.data.get("cell_overrides")) return Promise.resolve(previewFixed);
        const mappingJson = config.data.get("mapping_json");
        return Promise.resolve(mappingJson ? previewValidated : previewSuggestion);
      }
      if (config.url === "/api/v1/imports/commit") return Promise.resolve(commitResult);
      return Promise.resolve({});
    });
  });

  it("上传后展示工作表概览，进入映射表渲染建议、样本值与置信度", async () => {
    renderWizard();

    await user.upload(screen.getByLabelText("选择导入文件"), XLSX_FILE);
    // sheet 概览：两个工作表都在，当前选中的带出行列数
    expect(await screen.findByText("课表数据源")).toBeInTheDocument();
    expect(screen.getByText("填表说明")).toBeInTheDocument();
    expect(screen.getByText(/12 行 × 3 列/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "下一步：确认列映射" }));
    await screen.findByText("匹配置信度");

    // 逐列建议：目标字段下拉的当前值、样本值（前 3 个）、置信度徽章与说明
    expect(screen.getByLabelText("目标字段：班级标签")).toHaveValue("集训营班级标签");
    expect(screen.getByLabelText("目标字段：神秘列")).toHaveValue("");
    expect(screen.getByText("OMO4班、OMO4班、OMO5班")).toBeInTheDocument();
    expect(screen.getAllByText("高")).toHaveLength(2);
    expect(screen.getByText(/精确匹配 · 表头与规范字段/)).toBeInTheDocument();
    // missing_fields 醒目提示（「上课时段」同时是下拉选项，故在横幅容器上断言）
    const banner = screen.getByText(/以下模板字段在文件里没有对应列/);
    expect(banner.textContent).toContain("上课时段");
  });

  it("存在未映射列时校验按钮被门控，勾选确认后才能继续", async () => {
    renderWizard();
    await uploadAndOpenMapping();

    const confirm = screen.getByRole("button", { name: "确认映射并校验" });
    expect(confirm).toBeDisabled();
    expect(screen.getByText(/还有 1 列未映射且未勾选确认/)).toBeInTheDocument();

    await user.click(screen.getByLabelText(/我已确认这些列的处理方式/));
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    // 第二次 preview 必须带 mapping_json，且未匹配列的 target 为 null
    await waitFor(() => expect(mocks.request.mock.calls.filter((call) => requestUrl(call) === "/api/v1/imports/preview")).toHaveLength(2));
    const second = mocks.request.mock.calls.filter((call) => requestUrl(call) === "/api/v1/imports/preview")[1];
    const payload = JSON.parse(formField(second, "mapping_json"));
    expect(payload.sheet).toBe("课表数据源");
    expect(payload.header_row_index).toBe(0);
    expect(payload.columns).toEqual(expect.arrayContaining([
      expect.objectContaining({ column: "班级标签", target: "集训营班级标签" }),
      expect.objectContaining({ column: "神秘列", target: null }),
    ]));

    // 校验报告：统计 + 行级问题清单
    expect(await screen.findByText("校验通过")).toBeInTheDocument();
    expect(screen.getByText("缺少上课日期或上课时段")).toBeInTheDocument();
    expect(screen.getByText("2026/13/01")).toBeInTheDocument();
  });

  it("第三步内联修复问题行，重新校验错误清零后才放行提交，提交携带同一份修复", async () => {
    const { onCommitted } = renderWizard();
    await uploadAndOpenMapping();
    await user.click(screen.getByLabelText(/我已确认这些列的处理方式/));
    await user.click(screen.getByRole("button", { name: "确认映射并校验" }));
    await screen.findByText("校验通过");

    // 错误未清零：提交被门控，也还没有可点的重新校验（没有暂存修改）
    expect(screen.getByRole("button", { name: "下一步：确认提交" })).toBeDisabled();
    expect(screen.getByText(/还有 2 行校验未通过/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /重新校验/ })).not.toBeInTheDocument();

    // 行 7「上课日期无法解析：2026/13/01」：字段自动推断为上课日期，原值预填
    const row7 = screen.getByText("上课日期无法解析").closest("tr") as HTMLElement;
    await user.click(within(row7).getByRole("button", { name: "修复" }));
    expect(screen.getByLabelText("修复字段：第 7 行")).toHaveValue("上课日期");
    const value7 = screen.getByLabelText("修复值：第 7 行") as HTMLInputElement;
    expect(value7.value).toBe("2026/13/01");
    await user.clear(value7);
    await user.type(value7, "2026-09-12");
    await user.click(screen.getByRole("button", { name: "保存修改" }));

    // 行 4「缺少上课日期或上课时段」：复合原因给候选下拉，默认第一项上课日期
    const row4 = screen.getByText("缺少上课日期或上课时段").closest("tr") as HTMLElement;
    await user.click(within(row4).getByRole("button", { name: "修复" }));
    expect(screen.getByLabelText("修复字段：第 4 行")).toHaveValue("上课日期");
    await user.type(screen.getByLabelText("修复值：第 4 行"), "2026-08-20");
    await user.click(screen.getByRole("button", { name: "保存修改" }));

    // 重新校验必须带 mapping_json + cell_overrides，修复按行号 + 规范字段名提交
    await user.click(screen.getByRole("button", { name: "重新校验（2 处修改）" }));
    await waitFor(() => expect(mocks.request.mock.calls.filter((call) => requestUrl(call) === "/api/v1/imports/preview")).toHaveLength(3));
    const third = mocks.request.mock.calls.filter((call) => requestUrl(call) === "/api/v1/imports/preview")[2];
    expect(JSON.parse(formField(third, "cell_overrides"))).toEqual({
      "7": { 上课日期: "2026-09-12" },
      "4": { 上课日期: "2026-08-20" },
    });
    expect(JSON.parse(formField(third, "mapping_json")).columns.length).toBeGreaterThan(0);

    // 错误清零：门禁解除，保留的「改源文件重传」路径文案仍在
    expect(await screen.findByText(/没有需要修复的行/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一步：确认提交" })).toBeEnabled();
    expect(screen.getByText(/已确认的列映射方案会保留/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "下一步：确认提交" }));

    // 第四步确认提交：commit 带同一份 overrides
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/即将把 10 行数据写入主数据/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /确认导入/ }));

    await waitFor(() => expect(onCommitted).toHaveBeenCalledWith(commitResult));
    const commitCall = mocks.request.mock.calls.find((call) => requestUrl(call) === "/api/v1/imports/commit");
    expect(commitCall).toBeTruthy();
    expect(formField(commitCall!, "mode")).toBe("upsert");
    expect(JSON.parse(formField(commitCall!, "cell_overrides"))).toEqual({
      "7": { 上课日期: "2026-09-12" },
      "4": { 上课日期: "2026-08-20" },
    });
  });

  it("命中上次导入的映射记忆时，映射表提示沿用决策且匹配来源标注为「上次导入」", async () => {
    mocks.request.mockImplementation((config: { url: string }) => {
      if (config.url === "/api/v1/imports/preview") {
        return Promise.resolve({
          ...previewSuggestion,
          historical_match: true,
          mapping: previewSuggestion.mapping.map((item) => ({ ...item, matched_by: "historical" })),
        });
      }
      return Promise.resolve({});
    });
    renderWizard();
    await user.upload(screen.getByLabelText("选择导入文件"), XLSX_FILE);
    await user.click(screen.getByRole("button", { name: "下一步：确认列映射" }));
    await screen.findByText("目标字段");

    expect(screen.getByText(/已自动沿用上次确认的映射决策/)).toBeInTheDocument();
    expect(screen.getAllByText(/上次导入 · /).length).toBeGreaterThan(0);
  });

  it("用户可以在映射表里改目标字段，mapping_json 按修改后的值回传", async () => {
    renderWizard();
    await uploadAndOpenMapping();

    // 神秘列手动指定为「课表编排来源」：从「忽略该列」改为真实字段
    await user.selectOptions(screen.getByLabelText("目标字段：神秘列"), "课表编排来源");

    // 全部列映射后确认门控消失，无需再勾选
    expect(screen.queryByLabelText(/我已确认这些列的处理方式/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认映射并校验" }));
    await waitFor(() => expect(mocks.request.mock.calls.filter((call) => requestUrl(call) === "/api/v1/imports/preview")).toHaveLength(2));

    const second = mocks.request.mock.calls.filter((call) => requestUrl(call) === "/api/v1/imports/preview")[1];
    const payload = JSON.parse(formField(second, "mapping_json"));
    expect(payload.columns).toEqual(expect.arrayContaining([
      expect.objectContaining({ column: "神秘列", target: "课表编排来源" }),
    ]));
  });
});
