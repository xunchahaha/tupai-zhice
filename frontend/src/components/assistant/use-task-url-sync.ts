import { useCallback, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";

export interface TaskUrlHandlers {
  /** 完整重置任务状态（含放弃在途解析）；首次挂载没有旧任务，不会调用。 */
  reset: () => void;
  bindGoal: (goalId: string) => void;
  bindRun: (runId: string) => void;
}

/**
 * URL ↔ 任务状态。URL 里的 goal / run 是任务视图的事实来源：
 * 它们被外部导航改成另一个值或消失（浏览器后退、点侧栏「排课助手」/品牌链接、A→B 切换）时，
 * 先完整重置任务状态再绑定新目标——否则会出现 A 的任务条配 B 的结果卡，或后退也回不到首页。
 *
 * 本 hook 自己写入的 goal / run（新建任务后同步 goal、开始新一次求解后摘掉 run……）
 * 走 updateSearch，写入时同步前移基线，因此不会被当成外部导航而触发重置。
 */
export function useTaskUrlSync(handlers: TaskUrlHandlers) {
  const [searchParams, setSearchParams] = useSearchParams();
  const goalParam = searchParams.get("goal") ?? "";
  const runParam = searchParams.get("run") ?? "";
  const actionParam = searchParams.get("action") ?? "";
  const promptParam = searchParams.get("prompt") ?? "";
  const manualParam = searchParams.get("manual") === "1";

  // URL 更新走「最新 search + 变更函数」：解析/求解都是异步的，闭包里的 searchParams 早已过期，
  // 直接基于它 set 会把期间别处写入的参数（如新建任务同步进去的 goal）覆盖掉。
  const latestSearch = useRef(searchParams);
  useEffect(() => { latestSearch.current = searchParams; });
  // 任务状态当前对应的 goal / run；null = 还没有绑定过（首次挂载）。
  const synced = useRef<{ goal: string; run: string } | null>(null);
  const updateSearch = useCallback((mutate: (next: URLSearchParams) => void) => {
    const next = new URLSearchParams(latestSearch.current);
    mutate(next);
    if (next.toString() === latestSearch.current.toString()) return;
    latestSearch.current = next;
    synced.current = { goal: next.get("goal") ?? "", run: next.get("run") ?? "" };
    setSearchParams(next, { replace: true });
  }, [setSearchParams]);

  const latestHandlers = useRef(handlers);
  useEffect(() => { latestHandlers.current = handlers; });
  // 必须排在其它会调用 updateSearch 的 effect 之前（hook 调用顺序保证）。
  useEffect(() => {
    const previous = synced.current;
    if (previous && previous.goal === goalParam && previous.run === runParam) return;
    synced.current = { goal: goalParam, run: runParam };
    const { reset, bindGoal, bindRun } = latestHandlers.current;
    if (previous) reset();
    if (goalParam) bindGoal(goalParam);
    if (runParam) bindRun(runParam);
  }, [goalParam, runParam]);

  return { goalParam, runParam, actionParam, promptParam, manualParam, updateSearch };
}
