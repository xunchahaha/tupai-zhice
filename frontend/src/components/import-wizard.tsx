import { useCommitImportApiV1ImportsCommitPost, usePreviewImportApiV1ImportsPreviewPost } from "@/api/generated/client";
import { type ImportColumnMapping, type ImportCommitResponse, type ImportPreviewResponse } from "@/api/generated/models";
import { AlertCircle, Check, FileUp, Sparkles } from "lucide-react";
import { type DragEvent, Fragment, useRef, useState } from "react";
import { toast } from "sonner";

import { Skeleton } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Select } from "@/components/ui/select";
import { cn } from "@/lib/cn";
import { errorMessage } from "@/lib/format";

/**
 * 14 个规范字段（TEMPLATE_HEADERS）。唯一事实源在后端 converter_core.py，
 * 这里只为「目标字段」下拉列一份相同名单；后端仍会校验合法性。
 */
const TEMPLATE_FIELDS = [
  "标准业务线",
  "标准产品班型",
  "集训营班级标签",
  "教室标签",
  "课表编排来源",
  "编排阶段",
  "计划课次",
  "计划课时",
  "课次序号",
  "课节名称",
  "上课日期",
  "上课时段",
  "课节时长(小时)",
  "授课教师",
];

/** >= 0.85 视为高置信（后端 exact/alias/normalized 层），fuzzy 层 0.5-0.8 视为中置信。 */
const HIGH_CONFIDENCE = 0.85;

const MATCHED_BY_LABELS: Record<string, string> = {
  exact: "精确匹配",
  alias: "别名表",
  normalized: "归一化",
  fuzzy: "相似度",
  llm: "语义",
  historical: "上次导入",
  manual: "手动",
  unmatched: "未匹配",
};

type WizardStep = 1 | 2 | 3 | 4;
type IssueFilter = "errors" | "all";
type BadgeTone = "neutral" | "blue" | "green" | "yellow";

/** overrides 按列名记录用户决策（含「忽略该列」= ""），换文件重传时同名列自动沿用。 */
type ColumnOverrides = Record<string, string>;

/**
 * 行级单元格修复暂存：`{"7": {"上课日期": "2026-09-12"}}`，行号与 issues 报告同口径
 * （工作表内 1-based）。键用规范字段名（后端两者都收，规范名与源文件表头别名无关，
 * 换映射也不失效）；换文件/工作表/表头行后行号口径变了，必须整体清空。
 */
type CellOverrides = Record<string, Record<string, string>>;

interface CellEditDraft {
  row: string;
  field: string;
  value: string;
  /** 该行问题对应的可修复字段候选（「修复」下拉的内容）。 */
  candidates: string[];
  /** 问题报告里的原始值，切换修复字段时用于回填。 */
  original: string;
}

/**
 * 从跳过原因推断可修复的规范字段候选。「上课日期无法解析：X」钉死上课日期；
 * 「缺少上课日期或上课时段」等复合原因给候选清单让用户选。未知原因退回全字段。
 */
function issueFieldCandidates(reason: string): string[] {
  if (reason.includes("必须是数字")) return ["计划课次", "计划课时", "课次序号", "课节时长(小时)"];
  if (reason.includes("上课日期或上课时段")) return ["上课日期", "上课时段"];
  if (reason.includes("班级标签")) return ["班级标签"];
  if (reason.includes("上课时段")) return ["上课时段"];
  if (reason.includes("上课日期")) return ["上课日期"];
  return TEMPLATE_FIELDS;
}

interface ImportWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 提交成功后回调（父组件负责失效缓存、展示导入报告并关窗）。 */
  onCommitted: (result: ImportCommitResponse) => void;
}

function confidenceBadge(item: ImportColumnMapping, effective: string, overridden: boolean): { label: string; tone: BadgeTone } {
  if (effective === "") return { label: overridden ? "忽略" : "未匹配", tone: "neutral" };
  if (overridden && effective !== (item.target ?? "")) return { label: "手动指定", tone: "blue" };
  if (!item.target) return { label: "未匹配", tone: "neutral" };
  return item.confidence >= HIGH_CONFIDENCE ? { label: "高", tone: "green" } : { label: "中", tone: "yellow" };
}

/** 「上课日期无法解析：2026/13/1」→ 问题「上课日期无法解析」+ 原始值「2026/13/1」。 */
function splitIssueReason(reason: string): [string, string] {
  const index = reason.indexOf("：");
  return index === -1 ? [reason, ""] : [reason.slice(0, index), reason.slice(index + 1)];
}

/** 切换工作表/表头行走 mapping_json 覆写通道（后端约定 columns 至少一项，占位列即可）。 */
function reanalyzeMappingJson(patch: { sheet?: string; header_row_index?: number }): string {
  return JSON.stringify({ ...patch, columns: [{ column: "", column_index: 0, target: null }] });
}

function AnalysisPlaceholder({ label }: { label: string }) {
  return (
    <div className="space-y-2.5 rounded-lg border border-zinc-200 p-4 animate-fade-in" role="status" aria-label={label}>
      <Skeleton className="h-4 w-44" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-3/4" />
      <Skeleton className="h-9 w-1/2" />
    </div>
  );
}

function StepIndicator({ step }: { step: WizardStep }) {
  const labels = ["上传文件", "确认映射", "校验报告", "提交导入"];
  return (
    <ol className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs" aria-label={`导入进度：第 ${step} 步，共 4 步`}>
      {labels.map((label, index) => {
        const current = index + 1 === step;
        const done = index + 1 < step;
        return (
          <li key={label} className="flex items-center gap-1.5">
            <span
              className={cn(
                "grid size-5 place-items-center rounded-full text-[11px] font-medium transition-colors duration-150",
                current ? "bg-blue-600 text-white" : done ? "bg-zinc-200 text-zinc-600" : "bg-zinc-100 text-zinc-400",
              )}
            >
              {done ? <Check className="size-3" /> : index + 1}
            </span>
            <span className={cn(current ? "font-medium text-zinc-800" : "text-zinc-400")}>{label}</span>
            {index < labels.length - 1 ? <span className="mr-1 text-zinc-300">—</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

export function ImportWizard({ open, onOpenChange, onCommitted }: ImportWizardProps) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState<WizardStep>(1);
  const [file, setFile] = useState<File | null>(null);
  const [analysis, setAnalysis] = useState<ImportPreviewResponse | null>(null);
  const [overrides, setOverrides] = useState<ColumnOverrides>({});
  const [cellEdits, setCellEdits] = useState<CellOverrides>({});
  const [cellEditing, setCellEditing] = useState<CellEditDraft | null>(null);
  const [ackUnmatched, setAckUnmatched] = useState(false);
  const [mode, setMode] = useState<"upsert" | "insert">("upsert");
  const [issueFilter, setIssueFilter] = useState<IssueFilter>("errors");
  const [dragging, setDragging] = useState(false);
  // 切换过工作表/表头行后，本次分析走的是手动映射回放（无自动建议），映射表要提示用户逐列指定。
  const [manualAnalysis, setManualAnalysis] = useState(false);

  const preview = usePreviewImportApiV1ImportsPreviewPost({
    mutation: {
      onSuccess: (data) => setAnalysis(data),
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  const commit = useCommitImportApiV1ImportsCommitPost({
    mutation: { onError: (error) => toast.error(errorMessage(error)) },
  });

  // 关窗时回到初始状态（覆盖 Esc/遮罩关闭与提交成功两条路径）；
  // 同一次会话内重新上传文件时 overrides 保留（映射方案跟着列名走）。
  const resetWizard = () => {
    setStep(1);
    setFile(null);
    setAnalysis(null);
    setOverrides({});
    setCellEdits({});
    setCellEditing(null);
    setAckUnmatched(false);
    setMode("upsert");
    setIssueFilter("errors");
    setDragging(false);
    setManualAnalysis(false);
    preview.reset();
    commit.reset();
  };

  const requestClose = () => {
    if (busy) return;
    resetWizard();
    onOpenChange(false);
  };


  const startUpload = (selected: File) => {
    if (!/\.(xlsx|csv)$/i.test(selected.name)) {
      toast.error("仅支持 .xlsx 或 .csv 文件");
      return;
    }
    setFile(selected);
    setAnalysis(null);
    setManualAnalysis(false);
    // 行级修复按行号定位，换文件后行号口径变了，旧修复一律作废（列映射 overrides 保留）。
    setCellEdits({});
    setCellEditing(null);
    preview.mutate({ data: { file: selected as unknown as string } });
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) startUpload(dropped);
  };

  const effectiveTarget = (column: string): string => {
    if (overrides[column] !== undefined) return overrides[column];
    const suggested = analysis?.mapping.find((item) => item.column === column);
    return suggested?.target ?? "";
  };

  const buildMappingPayload = () => {
    if (!analysis) return null;
    return {
      sheet: analysis.selected_sheet,
      header_row_index: analysis.header_row_index,
      columns: analysis.mapping
        .filter((item) => item.column)
        .map((item) => ({ column: item.column, column_index: item.column_index, target: effectiveTarget(item.column) || null })),
    };
  };

  const reanalyze = (patch: { sheet?: string; header_row_index?: number }) => {
    if (!file || preview.isPending) return;
    // 切换工作表/表头行后数据行的行号整体位移，未提交的行级修复按新口径作废。
    setCellEdits({});
    setCellEditing(null);
    preview.mutate(
      { data: { file: file as unknown as string, mapping_json: reanalyzeMappingJson(patch) } },
      { onSuccess: () => setManualAnalysis(true) },
    );
  };

  const confirmMapping = () => {
    const payload = buildMappingPayload();
    if (!file || !payload) return;
    // 后端确认成功后清掉「手动解析」提示与未匹配勾选：回到本步时按新的校验结果重新判定。
    preview.mutate(
      {
        data: {
          file: file as unknown as string,
          mapping_json: JSON.stringify(payload),
          // 已暂存的行级修复随重跑一起生效，返回第 3 步时校验结果与提交口径一致。
          ...(Object.keys(cellEdits).length ? { cell_overrides: JSON.stringify(cellEdits) } : {}),
        },
      },
      { onSuccess: () => { setManualAnalysis(false); setAckUnmatched(false); setStep(3); } },
    );
  };

  // 第 3 步的「重新校验」：与确认映射同一条 preview 通道，但显式带着行级修复，
  // 错误清零（rows_skipped === 0）才放行进入提交步。
  const revalidateWithFixes = () => {
    const payload = buildMappingPayload();
    if (!file || !payload || !Object.keys(cellEdits).length) return;
    preview.mutate(
      {
        data: {
          file: file as unknown as string,
          mapping_json: JSON.stringify(payload),
          cell_overrides: JSON.stringify(cellEdits),
        },
      },
      { onSuccess: () => setStep(3) },
    );
  };

  const submitCommit = () => {
    const payload = buildMappingPayload();
    if (!file || !payload) return;
    commit.mutate(
      {
        data: {
          file: file as unknown as string,
          mapping_json: JSON.stringify(payload),
          mode,
          // 提交必须带上与最后校验同一份修复，否则报告与落库数据不一致。
          ...(Object.keys(cellEdits).length ? { cell_overrides: JSON.stringify(cellEdits) } : {}),
        },
      },
      { onSuccess: (result) => { resetWizard(); onCommitted(result); } },
    );
  };

  // 当前生效映射下每个规范字段的占用方，用于在下拉里禁掉已被别的列选走的字段。
  const targetOwners = new Map<string, string>();
  if (analysis) {
    for (const item of analysis.mapping) {
      const target = effectiveTarget(item.column);
      if (target && !targetOwners.has(target)) targetOwners.set(target, item.column);
    }
  }

  const mappingRows = analysis ? analysis.mapping.filter((item) => item.column) : [];
  const unmappedColumns = mappingRows.filter((item) => !effectiveTarget(item.column));
  const mappedCount = mappingRows.length - unmappedColumns.length;
  const missingFields = analysis?.missing_fields ?? [];
  const issues = analysis?.issues ?? [];
  const visibleIssues = issues.filter((item) => issueFilter === "all" || (typeof item["级别"] === "string" ? item["级别"] : "error") !== "warning");
  const stats = analysis?.stats;
  const busy = preview.isPending || commit.isPending;
  // 暂存的修复按「处」（单元格）计数展示；错误清零（rows_skipped === 0）才放行提交。
  const cellEditCount = Object.values(cellEdits).reduce((sum, cells) => sum + Object.keys(cells).length, 0);

  const startCellEdit = (item: Record<string, unknown>) => {
    const reason = typeof item["原因"] === "string" ? item["原因"] : "";
    const row = String(item["行号"] ?? "");
    const candidates = issueFieldCandidates(reason);
    const original = splitIssueReason(reason)[1];
    const field = candidates[0] ?? "";
    setCellEditing({ row, field, value: cellEdits[row]?.[field] ?? original, candidates, original });
  };

  const saveCellEdit = () => {
    if (!cellEditing || !cellEditing.field) return;
    const { row, field, value } = cellEditing;
    setCellEdits((current) => ({ ...current, [row]: { ...current[row], [field]: value } }));
    setCellEditing(null);
  };

  // 残留的 overrides（换文件后列名同名沿用）可能与新建议撞字段：提交前必须保证一个字段只对应一列。
  const targetClaimCounts = new Map<string, number>();
  for (const item of mappingRows) {
    const target = effectiveTarget(item.column);
    if (target) targetClaimCounts.set(target, (targetClaimCounts.get(target) ?? 0) + 1);
  }
  const hasDuplicateTargets = Array.from(targetClaimCounts.values()).some((count) => count > 1);

  const confirmDisabledReason = mappedCount === 0
    ? "请至少为一列选择目标字段后再校验"
    : hasDuplicateTargets
      ? "有规范字段被多列同时占用，请修正后再校验"
      : unmappedColumns.length > 0 && !ackUnmatched
        ? `还有 ${unmappedColumns.length} 列未映射且未勾选确认，无法继续`
        : "";

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) requestClose(); }}>
      <input ref={fileInput} type="file" accept=".xlsx,.csv" className="hidden" aria-label="选择导入文件" onChange={(event) => { const selected = event.target.files?.[0]; if (selected) startUpload(selected); event.target.value = ""; }} />
      <DialogContent className="max-w-4xl">
        <DialogTitle className="text-base font-semibold">智能导入</DialogTitle>
        <DialogDescription className="mt-1 text-sm text-zinc-500">
          上传任意教务导出的 Excel / CSV，自动识别表头并映射到 14 个规范字段；确认校验通过后才会写入基础资料。
        </DialogDescription>
        <StepIndicator step={step} />

        {step === 1 ? (
          <div className="mt-4 space-y-4 animate-fade-in">
            {!file ? (
              <div
                role="button"
                aria-label="上传导入文件"
                tabIndex={0}
                onClick={() => fileInput.current?.click()}
                onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") fileInput.current?.click(); }}
                onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
                className={cn(
                  "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-6 py-10 text-center transition-colors duration-150",
                  dragging ? "border-blue-500 bg-blue-50/60" : "border-zinc-300 bg-zinc-50/60 hover:border-zinc-400",
                )}
              >
                <FileUp className="size-6 text-zinc-400" />
                <p className="text-sm text-zinc-600">拖拽 .xlsx / .csv 文件到这里，或点击选择文件</p>
                <p className="text-xs text-zinc-400">系统只解析不落库；映射确认并校验通过后才写入</p>
              </div>
            ) : (
              <div className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-zinc-50/60 px-3 py-2.5 text-sm text-zinc-700">
                <FileUp className="size-4 shrink-0 text-blue-600" />
                <span className="min-w-0 truncate font-medium" title={file.name}>{file.name}</span>
                <span className="shrink-0 text-xs tabular-nums text-zinc-400">{Math.max(1, Math.round(file.size / 1024))} KB</span>
                <Button size="sm" variant="ghost" className="ml-auto" disabled={preview.isPending} onClick={() => fileInput.current?.click()}>重新选择</Button>
              </div>
            )}

            {preview.isPending ? <AnalysisPlaceholder label="正在解析文件" /> : null}

            {preview.isError ? (
              <div className="flex items-start gap-2 border border-red-200 bg-red-50 px-3 py-2.5 text-sm leading-5 text-red-800">
                <AlertCircle className="mt-0.5 size-4 shrink-0" />
                <span>文件解析失败：{errorMessage(preview.error)}</span>
              </div>
            ) : null}

            {analysis && !preview.isPending ? (
              <div className="space-y-4">
                <div>
                  <p className="text-xs font-medium text-zinc-500">工作表（{analysis.sheets.length}）</p>
                  <div className="mt-1.5 flex flex-wrap gap-2">
                    {analysis.sheets.map((sheet) => (
                      <button
                        key={sheet.name}
                        type="button"
                        disabled={preview.isPending}
                        onClick={() => { if (sheet.name !== analysis.selected_sheet) reanalyze({ sheet: sheet.name }); }}
                        className={cn(
                          "rounded-md border px-2.5 py-1.5 text-xs transition-colors duration-150 disabled:opacity-50",
                          sheet.name === analysis.selected_sheet ? "border-blue-600 bg-blue-50 text-blue-700" : "border-zinc-300 bg-white text-zinc-600 hover:border-zinc-400",
                        )}
                      >
                        <span className="font-medium">{sheet.name}</span>
                        <span className="ml-1.5 tabular-nums text-zinc-400">{sheet.row_count} 行 × {sheet.column_count} 列</span>
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="text-xs font-medium text-zinc-500">表头行候选</p>
                  <div className="mt-1.5 flex flex-wrap gap-2">
                    {analysis.header_candidates.slice(0, 5).map((candidate) => (
                      <button
                        key={candidate.row_index}
                        type="button"
                        disabled={preview.isPending}
                        onClick={() => { if (candidate.row_index !== analysis.header_row_index) reanalyze({ header_row_index: candidate.row_index }); }}
                        className={cn(
                          "rounded-md border px-2.5 py-1.5 text-xs transition-colors duration-150 disabled:opacity-50",
                          candidate.row_index === analysis.header_row_index ? "border-blue-600 bg-blue-50 text-blue-700" : "border-zinc-300 bg-white text-zinc-600 hover:border-zinc-400",
                        )}
                      >
                        <span className="font-medium">第 {candidate.row_index + 1} 行</span>
                        <span className="ml-1.5 tabular-nums text-zinc-400">匹配度 {Math.round(candidate.score * 100)}%</span>
                        {candidate.sample.length ? <span className="ml-1.5 text-zinc-400">{candidate.sample.slice(0, 4).join(" / ")}</span> : null}
                      </button>
                    ))}
                  </div>
                </div>
                <p className="text-xs leading-5 text-zinc-500">
                  共识别 {stats?.rows_total ?? 0} 行数据，已映射 {stats?.mapped_columns ?? 0}/{stats?.columns_total ?? 0} 列
                  {stats?.ai_mapping_used ? "（含 AI 语义映射）" : ""}。切换工作表或表头行会重新解析。
                </p>
              </div>
            ) : null}

            <div className="flex items-center justify-end gap-2 border-t border-zinc-100 pt-4">
              <Button variant="outline" disabled={busy} onClick={() => onOpenChange(false)}>取消</Button>
              <Button disabled={!analysis || busy} onClick={() => setStep(2)}>下一步：确认列映射</Button>
            </div>
          </div>
        ) : null}

        {step === 2 && analysis ? (
          <div className="mt-4 space-y-4 animate-fade-in">
            {missingFields.length ? (
              <div className="border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-5 text-amber-900">
                以下模板字段在文件里没有对应列，相关数据将按空值处理：{missingFields.join("、")}。
              </div>
            ) : null}
            {manualAnalysis ? (
              <p className="border border-zinc-200 bg-zinc-50 px-3 py-2.5 text-xs leading-5 text-zinc-600">
                已按你选择的工作表 / 表头行重新解析，自动映射建议不再适用，请逐列指定目标字段或忽略。
              </p>
            ) : null}
            {analysis.historical_match ? (
              <p className="border border-blue-200 bg-blue-50 px-3 py-2.5 text-xs leading-5 text-blue-800">
                表头与上次导入一致，已自动沿用上次确认的映射决策（含忽略的列），可逐列调整后再校验。
              </p>
            ) : null}
            <div className="overflow-x-auto rounded-lg border border-zinc-200">
              <table className="w-full min-w-[720px] border-collapse text-left text-sm">
                <thead className="bg-zinc-50/90 text-xs text-zinc-500">
                  <tr>
                    <th className="h-9 border-b border-zinc-200 px-3 font-medium">文件列</th>
                    <th className="h-9 border-b border-zinc-200 px-3 font-medium">样本值</th>
                    <th className="h-9 border-b border-zinc-200 px-3 font-medium">目标字段</th>
                    <th className="h-9 border-b border-zinc-200 px-3 font-medium">匹配置信度</th>
                  </tr>
                </thead>
                <tbody>
                  {mappingRows.map((item) => {
                    const effective = effectiveTarget(item.column);
                    const overridden = overrides[item.column] !== undefined;
                    const badge = confidenceBadge(item, effective, overridden && effective !== (item.target ?? ""));
                    const samples = Array.isArray(item.sample_values) ? item.sample_values : [];
                    return (
                      <tr key={item.column_index} className="border-b border-zinc-100 last:border-0 transition-colors duration-100 hover:bg-blue-50/20">
                        <td className="h-10 px-3 align-middle font-medium text-zinc-700">
                          <span className="block max-w-40 truncate" title={item.column}>{item.column}</span>
                        </td>
                        <td className="h-10 px-3 align-middle">
                          <span className="block max-w-52 truncate text-zinc-500" title={samples.join("、")}>{samples.length ? samples.slice(0, 3).join("、") : "—"}</span>
                        </td>
                        <td className="h-10 px-3 align-middle">
                          <Select
                            aria-label={`目标字段：${item.column}`}
                            selectSize="sm"
                            containerClassName="w-44"
                            value={effective}
                            onChange={(event) => setOverrides((current) => ({ ...current, [item.column]: event.target.value }))}
                          >
                            <option value="">忽略该列</option>
                            {TEMPLATE_FIELDS.map((field) => (
                              <option key={field} value={field} disabled={Boolean(targetOwners.get(field) && targetOwners.get(field) !== item.column)}>{field}</option>
                            ))}
                          </Select>
                        </td>
                        <td className="h-10 px-3 align-middle">
                          <div className="flex items-center gap-2">
                            <Badge tone={badge.tone}>{badge.label}</Badge>
                            <span className="max-w-64 truncate text-[11px] text-zinc-400" title={`${MATCHED_BY_LABELS[item.matched_by] ?? item.matched_by}：${item.rationale}`}>
                              {MATCHED_BY_LABELS[item.matched_by] ?? item.matched_by} · {item.rationale}
                            </span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="text-xs leading-5 text-zinc-500">
              目标字段决定该列写入哪个规范字段，选择「忽略该列」则不导入；一个规范字段只能对应一列，被占用的字段会在下拉中置灰。
            </p>
            {unmappedColumns.length ? (
              <div className="border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-5 text-amber-900">
                <p>
                  有 {unmappedColumns.length} 列未映射到规范字段（低置信度的列后端不会自动猜测）：{unmappedColumns.map((item) => item.column).join("、")}。未映射列不会导入。
                </p>
                <label className="mt-1.5 flex items-center gap-2">
                  <input type="checkbox" className="size-3.5 accent-blue-600" checked={ackUnmatched} onChange={(event) => setAckUnmatched(event.target.checked)} />
                  我已确认这些列的处理方式（忽略，或已手动指定目标字段）
                </label>
              </div>
            ) : null}
            {preview.isError ? (
              <div className="flex items-start gap-2 border border-red-200 bg-red-50 px-3 py-2.5 text-sm leading-5 text-red-800">
                <AlertCircle className="mt-0.5 size-4 shrink-0" />
                <span>校验失败：{errorMessage(preview.error)}</span>
              </div>
            ) : null}
            <div className="flex items-center justify-end gap-2 border-t border-zinc-100 pt-4">
              {confirmDisabledReason ? <p className="mr-auto text-xs text-amber-700">{confirmDisabledReason}</p> : null}
              <Button variant="outline" disabled={busy} onClick={() => setStep(1)}>上一步</Button>
              <Button disabled={Boolean(confirmDisabledReason) || preview.isPending} onClick={confirmMapping}>{preview.isPending ? "校验中…" : "确认映射并校验"}</Button>
            </div>
          </div>
        ) : null}

        {step === 3 && analysis && stats ? (
          <div className="mt-4 space-y-4 animate-fade-in">
            <dl className="grid gap-px overflow-hidden rounded-lg border border-zinc-200 bg-zinc-200 sm:grid-cols-4">
              {[
                { label: "总行数", value: stats.rows_total },
                { label: "校验通过", value: stats.rows_valid },
                { label: "校验跳过", value: stats.rows_skipped },
                { label: "忽略空行", value: stats.rows_ignored_blank ?? 0 },
              ].map((tile) => (
                <div key={tile.label} className="bg-white px-4 py-3">
                  <dt className="text-xs text-zinc-400">{tile.label}</dt>
                  <dd className="mt-1 text-lg font-semibold tabular-nums text-zinc-800">{tile.value}</dd>
                </div>
              ))}
            </dl>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-1 rounded-md border border-zinc-200 p-0.5">
                {(["errors", "all"] as IssueFilter[]).map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setIssueFilter(value)}
                    className={cn(
                      "rounded px-2.5 py-1 text-xs transition-colors duration-150",
                      issueFilter === value ? "bg-zinc-900 text-white" : "text-zinc-600 hover:bg-zinc-100",
                    )}
                  >
                    {value === "errors" ? "只看错误" : "全部"}
                  </button>
                ))}
              </div>
              <p className="text-xs tabular-nums text-zinc-500">
                {visibleIssues.length} 条问题记录{stats.rows_skipped > visibleIssues.length ? `（清单最多保留 200 条，实际跳过 ${stats.rows_skipped} 行）` : ""}
              </p>
            </div>
            <div className="overflow-x-auto rounded-lg border border-zinc-200">
              <table className="w-full min-w-[640px] border-collapse text-left text-sm">
                <thead className="bg-zinc-50/90 text-xs text-zinc-500">
                  <tr>
                    <th className="h-9 border-b border-zinc-200 px-3 font-medium">行号</th>
                    <th className="h-9 border-b border-zinc-200 px-3 font-medium">问题</th>
                    <th className="h-9 border-b border-zinc-200 px-3 font-medium">原始值</th>
                    <th className="h-9 border-b border-zinc-200 px-3 font-medium">修复</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleIssues.length ? visibleIssues.map((item, index) => {
                    const reason = typeof item["原因"] === "string" ? item["原因"] : JSON.stringify(item);
                    const [problem, raw] = splitIssueReason(reason);
                    const row = String(item["行号"] ?? "-");
                    return (
                      <Fragment key={index}>
                        <tr className="border-b border-zinc-100 transition-colors duration-100 hover:bg-blue-50/20">
                          <td className="h-10 px-3 align-middle tabular-nums text-zinc-700">{row}</td>
                          <td className="h-10 px-3 align-middle text-zinc-700">{problem}</td>
                          <td className="h-10 px-3 align-middle text-zinc-500">{raw || "—"}</td>
                          <td className="h-10 px-3 align-middle">
                            <Button size="sm" variant="ghost" disabled={busy} onClick={() => startCellEdit(item)}>修复</Button>
                          </td>
                        </tr>
                        {cellEditing?.row === row ? (
                          <tr className="border-b border-zinc-100 bg-blue-50/40">
                            <td colSpan={4} className="px-3 py-2.5">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="text-xs text-zinc-500">修复第 {cellEditing.row} 行</span>
                                <Select
                                  aria-label={`修复字段：第 ${cellEditing.row} 行`}
                                  selectSize="sm"
                                  containerClassName="w-40"
                                  value={cellEditing.field}
                                  onChange={(event) => {
                                    const field = event.target.value;
                                    setCellEditing((current) => current ? { ...current, field, value: cellEdits[current.row]?.[field] ?? current.original } : current);
                                  }}
                                >
                                  {cellEditing.candidates.map((field) => <option key={field} value={field}>{field}</option>)}
                                </Select>
                                <input
                                  type="text"
                                  aria-label={`修复值：第 ${cellEditing.row} 行`}
                                  value={cellEditing.value}
                                  onChange={(event) => setCellEditing((current) => current ? { ...current, value: event.target.value } : current)}
                                  className="h-8 w-44 rounded-md border border-zinc-300 bg-white px-2.5 text-xs shadow-2xs outline-none transition-all focus:border-blue-600 focus:ring-2 focus:ring-blue-500/20"
                                />
                                <Button size="sm" disabled={!cellEditing.field} onClick={saveCellEdit}>保存修改</Button>
                                <Button size="sm" variant="ghost" onClick={() => setCellEditing(null)}>取消</Button>
                              </div>
                            </td>
                          </tr>
                        ) : null}
                      </Fragment>
                    );
                  }) : (
                    <tr><td colSpan={4} className="h-24 px-3 text-center text-zinc-400">没有需要修复的行，全部数据校验通过</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            <p className="text-xs leading-5 text-zinc-500">
              在问题行点「修复」直接改单元格，再点「重新校验」确认错误清零后提交；也可以修改源文件后<a className="text-blue-600 hover:underline" href="#" onClick={(event) => { event.preventDefault(); setStep(1); }}>重新上传</a>，已确认的列映射方案会保留；还可以回到上一步继续调整映射。
            </p>
            <div className="flex items-center justify-end gap-2 border-t border-zinc-100 pt-4">
              {stats.rows_valid === 0 ? (
                <p className="mr-auto text-xs text-amber-700">没有校验通过的行，请调整映射或修改源文件后重新上传</p>
              ) : stats.rows_skipped > 0 ? (
                <p className="mr-auto text-xs text-amber-700">还有 {stats.rows_skipped} 行校验未通过，请修复问题行后再提交</p>
              ) : null}
              <Button variant="outline" disabled={busy} onClick={() => setStep(2)}>上一步</Button>
              {cellEditCount > 0 ? (
                <Button variant="outline" disabled={busy} onClick={revalidateWithFixes}>
                  {preview.isPending ? "校验中…" : `重新校验（${cellEditCount} 处修改）`}
                </Button>
              ) : null}
              <Button disabled={busy || stats.rows_valid === 0 || stats.rows_skipped > 0} onClick={() => setStep(4)}>下一步：确认提交</Button>
            </div>
          </div>
        ) : null}

        {step === 4 && analysis && stats ? (
          <div className="mt-4 space-y-4 animate-fade-in">
            <dl className="grid gap-px overflow-hidden rounded-lg border border-zinc-200 bg-zinc-200 sm:grid-cols-3">
              {[
                { label: "源文件", value: analysis.source },
                { label: "工作表", value: analysis.selected_sheet },
                { label: "可导入行", value: String(stats.rows_valid) },
              ].map((tile) => (
                <div key={tile.label} className="bg-white px-4 py-3">
                  <dt className="text-xs text-zinc-400">{tile.label}</dt>
                  <dd className="mt-1 truncate text-sm font-semibold text-zinc-800" title={tile.value}>{tile.value}</dd>
                </div>
              ))}
            </dl>
            <label className="block text-sm text-zinc-700">
              导入模式
              <Select selectSize="md" containerClassName="mt-1.5" value={mode} onChange={(event) => setMode(event.target.value === "insert" ? "insert" : "upsert")}>
                <option value="upsert">按业务键更新（推荐，重复导入即更新）</option>
                <option value="insert">全新追加（已存在的课次原样保留）</option>
              </Select>
            </label>
            <p className="text-xs leading-5 text-zinc-500">
              {mode === "upsert"
                ? "upsert 沿用业务键（班级 + 课次序号 + 课节名称 + 上课日期 + 上课时段），重复导入同一份文件不会产生重复课次。"
                : "insert 只新增不更新：与现有课次冲突的行会原样保留，已存在的数据不做改动。"}
            </p>
            <div className="border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900">
              即将把 {stats.rows_valid} 行数据写入基础资料，导入会生成新的草稿课表版本，不影响当前已发布版本。
            </div>
            {commit.isPending ? <AnalysisPlaceholder label="正在导入" /> : null}
            {commit.isError ? (
              <div className="flex items-start gap-2 border border-red-200 bg-red-50 px-3 py-2.5 text-sm leading-5 text-red-800">
                <AlertCircle className="mt-0.5 size-4 shrink-0" />
                <span>导入失败：{errorMessage(commit.error)}</span>
              </div>
            ) : null}
            <div className="flex items-center justify-end gap-2 border-t border-zinc-100 pt-4">
              <Button variant="outline" disabled={busy} onClick={() => setStep(3)}>上一步</Button>
              <Button disabled={commit.isPending} onClick={submitCommit}>
                <Sparkles className="size-3.5" />{commit.isPending ? "导入中…" : "确认导入"}
              </Button>
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
