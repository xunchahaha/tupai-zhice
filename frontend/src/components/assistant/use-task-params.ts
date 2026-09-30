import { useCallback, useEffect, useRef, useState } from "react";

import { type Interpretation } from "@/lib/interpret-stream";
import { defaultParams, type ScopeExpansion, type SolverParamValues } from "@/lib/solver-params";
import { type GoalTaskContext } from "@/lib/task-context";

type ManualField = "date_from" | "date_to" | "date_window_days" | "business_lines" | "class_business_ids" | "product_types";

const untouched = (): Record<ManualField, boolean> => ({
  date_from: false,
  date_to: false,
  date_window_days: false,
  business_lines: false,
  class_business_ids: false,
  product_types: false,
});

/**
 * 范围/日期/预算的**单一草稿**（问题2）：确认卡、手动排课面板、续办恢复读写同一份，
 * 手动改过的字段不被后续解析覆盖；范围从限定改成「全部」＝扩大，需单独确认。
 */
export function useTaskParams() {
  const [params, setParams] = useState<SolverParamValues>(defaultParams);
  // 解析结果回填手动参数草稿时，用户手动改过的字段不再被下一次解析覆盖（D6/问题2）。
  const manuallyEdited = useRef(untouched());
  // 解析写入草稿时的范围基线：判断「手动清空范围」是否属于扩大解析确认过的范围时以它为基准，
  // 而不是相邻两次编辑的差值——先改成别的限定值再清空，仍然算扩大。
  const parsedScopeBaseline = useRef({ business_lines: [] as string[], class_business_ids: [] as string[], course_business_ids: [] as string[] });
  const [scopeExpansion, setScopeExpansion] = useState<ScopeExpansion | null>(null);
  // 解析出的日期窗口是否已回填手动参数面板（来源徽标用，用户手动改动后即失效）。
  const [aiWindowFilled, setAiWindowFilled] = useState(false);
  // 解析是异步的：完成时要读「此刻」的草稿与待确认扩大，而不是点击时那一帧的闭包。
  const latest = useRef({ params, scopeExpansion });
  useEffect(() => { latest.current = { params, scopeExpansion }; });

  const markManualEdit = useCallback((field: "date_from" | "date_to" | "date_window_days") => {
    manuallyEdited.current[field] = true;
    if (field === "date_window_days") setAiWindowFilled(false);
  }, []);

  /**
   * 范围字段（业务线/班级）的手动编辑入口——打脏标记、写同一份草稿，并把「限定 → 全部」的
   * 扩大动作拦进单独确认（以解析写入草稿时的范围为基线）；改回限定值即视为放弃扩大。
   */
  const changeScopeField = useCallback((field: "business_lines" | "class_business_ids", value: string[]) => {
    manuallyEdited.current[field] = true;
    const previous = latest.current.params[field];
    setParams((current) => ({ ...current, [field]: value }));
    if (!value.length && previous.length && parsedScopeBaseline.current[field].length) {
      setScopeExpansion((current) => ({ ...current, [field]: previous }));
      return;
    }
    setScopeExpansion((current) => {
      if (!current || !(field in current)) return current;
      const next = { ...current };
      delete next[field];
      return Object.keys(next).length ? next : null;
    });
  }, []);

  /** 课表交接带来的课次限定写进草稿：此时还没提交，取消它不算扩大。 */
  const adoptLessons = useCallback((ids: string[]) => {
    setParams((current) => ({ ...current, course_business_ids: ids }));
  }, []);

  /** 课次限定随求解提交后成为已确认的范围：之后取消它就是扩大，要单独确认。 */
  const commitLessonScope = useCallback(() => {
    parsedScopeBaseline.current = { ...parsedScopeBaseline.current, course_business_ids: latest.current.params.course_business_ids };
  }, []);

  /** 取消课次限定：已提交过的限定被取消 = 扩大范围（返回 true，调用方据此保留交接状态直到确认）。 */
  const clearLessonScope = useCallback((): boolean => {
    const previous = latest.current.params.course_business_ids;
    setParams((current) => ({ ...current, course_business_ids: [] }));
    if (previous.length && parsedScopeBaseline.current.course_business_ids.length) {
      setScopeExpansion((current) => ({ ...current, course_business_ids: previous }));
      return true;
    }
    return false;
  }, []);

  /** 明确确认后扩大才生效：解除求解入口禁用，草稿保持用户改后的范围。 */
  const confirmScopeExpansion = useCallback(() => setScopeExpansion(null), []);

  const revertScopeExpansion = useCallback(() => {
    const pending = latest.current.scopeExpansion;
    if (pending) {
      setParams((current) => ({
        ...current,
        ...(pending.business_lines ? { business_lines: pending.business_lines } : {}),
        ...(pending.class_business_ids ? { class_business_ids: pending.class_business_ids } : {}),
        ...(pending.course_business_ids ? { course_business_ids: pending.course_business_ids } : {}),
      }));
    }
    setScopeExpansion(null);
  }, []);

  /**
   * 解析成功后把结果写入草稿（业务范围 + 日期三元组）。未确认的「扩大范围」随新解析回滚——
   * 确认动作没有完成，草稿不能带着扩大后的范围静默进入下一次确认。
   */
  const applyInterpreted = useCallback((data: Interpretation) => {
    const { params: base, scopeExpansion: pending } = latest.current;
    const draftBase = pending
      ? {
          ...base,
          ...(pending.business_lines ? { business_lines: pending.business_lines } : {}),
          ...(pending.class_business_ids ? { class_business_ids: pending.class_business_ids } : {}),
          ...(pending.course_business_ids ? { course_business_ids: pending.course_business_ids } : {}),
        }
      : base;
    if (pending) setScopeExpansion(null);
    const edited = manuallyEdited.current;
    const nextBusinessLines = edited.business_lines ? draftBase.business_lines : data.business_lines ?? [];
    const nextProductTypes = edited.product_types ? draftBase.product_types : data.product_types ?? [];
    const nextClassBusinessIds = edited.class_business_ids ? draftBase.class_business_ids : data.class_business_ids ?? [];
    setParams((current) => ({
      ...current,
      business_lines: nextBusinessLines,
      product_types: nextProductTypes,
      class_business_ids: nextClassBusinessIds,
      date_from: edited.date_from ? current.date_from : data.date_from ?? null,
      date_to: edited.date_to ? current.date_to : data.date_to ?? null,
      date_window_days: edited.date_window_days ? current.date_window_days : data.date_window_days ?? current.date_window_days,
    }));
    parsedScopeBaseline.current = { ...parsedScopeBaseline.current, business_lines: nextBusinessLines, class_business_ids: nextClassBusinessIds };
    setAiWindowFilled(!edited.date_window_days);
  }, []);

  /** 续办恢复：任务上下文里的范围/日期草稿回填，并成为「扩大范围」判定的基线（MEM-I2）。 */
  const restoreScope = useCallback((scope: NonNullable<GoalTaskContext["scope"]>) => {
    const restored = {
      business_lines: scope.business_lines ?? [],
      product_types: scope.product_types ?? [],
      class_business_ids: scope.class_business_ids ?? [],
      course_business_ids: scope.course_business_ids ?? [],
      date_from: scope.date_from ?? null,
      date_to: scope.date_to ?? null,
      date_window_days: scope.date_window_days ?? defaultParams.date_window_days,
    };
    setParams((current) => ({ ...current, ...restored }));
    parsedScopeBaseline.current = { business_lines: restored.business_lines, class_business_ids: restored.class_business_ids, course_business_ids: restored.course_business_ids };
  }, []);

  /** 回到首页：草稿、脏标记、范围基线全部复位，下一个任务从零开始。 */
  const reset = useCallback(() => {
    setParams(defaultParams);
    manuallyEdited.current = untouched();
    parsedScopeBaseline.current = { business_lines: [], class_business_ids: [], course_business_ids: [] };
    setScopeExpansion(null);
    setAiWindowFilled(false);
  }, []);

  return {
    params,
    setParams,
    scopeExpansion,
    aiWindowFilled,
    markManualEdit,
    changeScopeField,
    confirmScopeExpansion,
    revertScopeExpansion,
    adoptLessons,
    commitLessonScope,
    clearLessonScope,
    applyInterpreted,
    restoreScope,
    reset,
  };
}
