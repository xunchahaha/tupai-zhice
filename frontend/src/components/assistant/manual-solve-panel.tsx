import { LockKeyhole, Play, SlidersHorizontal, Target } from "lucide-react";
import { type Dispatch, type ReactNode, type SetStateAction, useEffect, useRef } from "react";

import { InfoTooltip } from "@/components/assistant/info-tooltip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { OPTIONAL_SOLVER_RULES, type SolverParamValues, type SolverRule, SYSTEM_SOLVER_RULES } from "@/lib/solver-params";

function NumberField({ label, hint, labelExtra, value, min, max, step, onChange }: { label: string; hint: string; labelExtra?: ReactNode; value: number; min: number; max: number; step: number; onChange: (value: number) => void }) {
  const id = `solver-${label}`;
  return (
    <div className="block text-sm text-zinc-700">
      <span className="flex items-center justify-between">
        <span className="inline-flex items-center gap-1.5">
          <label htmlFor={id} className="cursor-pointer font-medium text-zinc-800">{label}</label>
          {labelExtra}
        </span>
        <InfoTooltip label={label}>{hint}</InfoTooltip>
      </span>
      <input
        id={id}
        className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2.5 text-sm tabular-nums outline-none focus:border-blue-500"
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  );
}

/**
 * 手动排课参数：不依赖 AI 是否配置，也不需要先解析成功。范围/日期/预算与「我理解的是……」
 * 确认卡读写同一份草稿（use-task-params），所以两个入口发出的范围口径永远一致。
 * 「扩大范围需单独确认」的提示由页面在确认卡上直接展示，这里只负责在未确认时禁用提交。
 */
export function ManualSolvePanel({
  params,
  setParams,
  scope,
  selectedCount,
  pending,
  aiWindowBadge,
  onManualEdit,
  linkedGoalId,
  onClearGoal,
  scopeExpansionPending,
  onScopeFieldChange,
  focusScope,
  onScopeFocused,
  onSubmit,
}: {
  params: SolverParamValues;
  setParams: Dispatch<SetStateAction<SolverParamValues>>;
  scope: { businessLines: string[]; classes: string[] };
  selectedCount: number;
  pending: boolean;
  aiWindowBadge?: boolean;
  onManualEdit?: (field: "date_from" | "date_to" | "date_window_days") => void;
  linkedGoalId?: string;
  onClearGoal?: () => void;
  scopeExpansionPending?: boolean;
  onScopeFieldChange?: (field: "business_lines" | "class_business_ids", value: string[]) => void;
  /** 「修正范围」补救动作：范围区已展开后再滚动聚焦（未展开时滚动无目标）。 */
  focusScope?: boolean;
  onScopeFocused?: () => void;
  onSubmit: () => void;
}) {
  const scopeRef = useRef<HTMLFieldSetElement | null>(null);
  useEffect(() => {
    if (!focusScope) return;
    scopeRef.current?.scrollIntoView?.({ block: "start" });
    onScopeFocused?.();
  }, [focusScope, onScopeFocused]);
  const toggleRule = (key: SolverRule) =>
    setParams((current) => ({
      ...current,
      solver_rules: current.solver_rules.includes(key)
        ? current.solver_rules.filter((item) => item !== key)
        : [...current.solver_rules, key],
    }));
  return (
    <section aria-label="手动排课参数" className="rounded-lg border border-zinc-200 bg-white p-5">
      <div className="flex items-center gap-2">
        <SlidersHorizontal className="size-4 text-blue-600" />
        <h2 className="font-semibold">手动排课参数</h2>
      </div>
      <p className="mt-1 text-xs text-zinc-500">自己设置范围、日期和时限直接排课，不需要 AI 先解析，随时可用。</p>
      <fieldset ref={scopeRef} id="assistant-scope-fieldset" className="mt-5 grid gap-3 border-b border-zinc-100 pb-4">
        <legend className="sr-only">排课范围</legend>
        <div className="text-xs font-medium text-zinc-500">排课范围</div>
        <label className="block text-sm text-zinc-700">
          业务线
          <Select aria-label="业务线" selectSize="md" containerClassName="mt-1.5" value={params.business_lines[0] ?? ""} onChange={(event) =>
              onScopeFieldChange?.("business_lines", event.target.value ? [event.target.value] : [])
            }
          >
            <option value="">全部业务线</option>
            {scope.businessLines.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </Select>
        </label>
        <label className="block text-sm text-zinc-700">
          班级
          <Select aria-label="班级" selectSize="md" containerClassName="mt-1.5" value={params.class_business_ids[0] ?? ""} onChange={(event) =>
              onScopeFieldChange?.("class_business_ids", event.target.value ? [event.target.value] : [])
            }
          >
            <option value="">全部班级</option>
            {scope.classes.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </Select>
        </label>
        <div className="grid grid-cols-2 gap-3">
          {(["date_from", "date_to"] as const).map((key) => {
            const label = key === "date_from" ? "起始日期" : "结束日期";
            const id = `solver-${key}`;
            return (
              <div key={key} className="block text-sm text-zinc-700">
                <span className="inline-flex items-center gap-1.5">
                  <label htmlFor={id}>{label}</label>
                  <InfoTooltip label={label}>
                    {key === "date_from"
                      ? "只选择原课表日期不早于这一天的课次，并限制新日期不早于这一天；还会与日期调整窗口共同生效。留空不设下界。"
                      : "只选择原课表日期不晚于这一天的课次，并限制新日期不晚于这一天；还会与日期调整窗口共同生效。留空不设上界。"}
                  </InfoTooltip>
                </span>
                <input
                  id={id}
                  className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2 text-sm"
                  type="date"
                  value={params[key] ?? ""}
                  onChange={(event) => {
                    onManualEdit?.(key);
                    setParams((current) => ({ ...current, [key]: event.target.value || null }));
                  }}
                />
              </div>
            );
          })}
        </div>
        <p className={"text-xs leading-5 " + (selectedCount > 1500 ? "text-amber-700" : "text-zinc-400")}>
          当前范围命中 <span className="font-mono tabular-nums">{selectedCount}</span> 个课次。
          {selectedCount > 1500 ? "课次过多时求解会超时，建议按班级或按周分批。" : ""}
        </p>
      </fieldset>
      <div className="mt-4 grid gap-4">
        <NumberField label="求解时限（秒）" hint="求解最多运行多久。超时可能返回已有可行解，或还没找到解（不代表无解）；课次范围越大，通常需要越长时间。" value={params.time_limit_seconds} min={1} max={900} step={5} onChange={(value) => setParams((current) => ({ ...current, time_limit_seconds: value }))} />
        <NumberField label="日期调整窗口（天）" hint="每节课相对原日期最多可前后挪动几天。实际新日期还必须落在起始日期与结束日期设定的边界内；设为 0 表示不调日期。" labelExtra={aiWindowBadge ? <Badge tone="blue">来自 AI 解析</Badge> : undefined} value={params.date_window_days} min={0} max={31} step={1} onChange={(value) => { onManualEdit?.("date_window_days"); setParams((current) => ({ ...current, date_window_days: value })); }} />
        <NumberField label="变更权重" hint="每挪动一天的代价。数值越大，求解器越倾向保持原课表。" value={params.change_weight} min={0} max={1000000} step={1000} onChange={(value) => setParams((current) => ({ ...current, change_weight: value }))} />
      </div>
      <fieldset className="mt-5 border-t border-zinc-100 pt-4">
        <legend className="sr-only">硬性要求与可选策略</legend>
        <div className="text-xs font-medium text-zinc-500">系统硬性要求</div>
        <div className="mt-2 grid gap-2">
          {SYSTEM_SOLVER_RULES.map((rule) => (
            <div key={rule.key} className="flex items-start gap-2 rounded border border-emerald-100 bg-emerald-50/60 px-3 py-2 text-sm text-zinc-700">
              <LockKeyhole className="mt-0.5 size-3.5 shrink-0 text-emerald-700" />
              <span>
                <span className="inline-flex items-center gap-1.5">{rule.label}<InfoTooltip label={rule.label}>{rule.hint}</InfoTooltip><span className="text-xs text-emerald-700">始终生效</span></span>
              </span>
            </div>
          ))}
        </div>
        <div className="mt-4 text-xs font-medium text-zinc-500">可选策略</div>
        <div className="mt-2 grid gap-2">
          {OPTIONAL_SOLVER_RULES.map((rule) => (
            <div key={rule.key} className="flex items-start gap-2 text-sm text-zinc-700">
              <input id={`solver-rule-${rule.key}`} className="mt-1 accent-blue-600" type="checkbox" checked={params.solver_rules.includes(rule.key)} onChange={() => toggleRule(rule.key)} />
              <span className="inline-flex items-center gap-1.5">
                <label className="cursor-pointer" htmlFor={`solver-rule-${rule.key}`}>{rule.label}</label>
                <InfoTooltip label={rule.label}>{rule.hint}</InfoTooltip>
              </span>
            </div>
          ))}
        </div>
      </fieldset>
      {/* MEM-D3（目标连续性）：会话持有任务时手动排课自动带上同一任务，完成后纳入同一验收闭环；
          可显式清除回到「不关联任务」的原有行为。 */}
      {linkedGoalId ? (
        <div className="mt-5 flex flex-wrap items-center gap-2 rounded-md border border-blue-100 bg-blue-50/60 px-3 py-2 text-sm">
          <Target className="size-4 shrink-0 text-blue-600" />
          <span className="text-zinc-700">
            本次求解关联任务 <span className="font-mono text-blue-700">#{linkedGoalId.slice(0, 8)}</span>，完成后自动核对要求
          </span>
          <button
            type="button"
            aria-label="清除任务关联"
            className="ml-auto text-xs text-zinc-500 underline-offset-2 transition-colors hover:text-zinc-700 hover:underline"
            onClick={onClearGoal}
          >
            清除关联
          </button>
        </div>
      ) : null}
      <Button className="mt-6 w-full" onClick={onSubmit} disabled={pending || Boolean(scopeExpansionPending)}>
        <Play className="size-4" />
        按参数开始求解
      </Button>
    </section>
  );
}
