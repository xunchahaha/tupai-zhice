import { useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";

import { type ScopeExpansion } from "@/lib/solver-params";

export const SCOPE_EXPANSION_BLOCK_REASON = "排课范围比确认过的更大，请先在页面上方确认扩大范围，或恢复原范围。";

/**
 * 提交闸门：所有会触发求解或重新解析的入口（确认卡按钮、手动提交、继续调整、加预算重跑、
 * ?action=raise_budget、验收缺口补救、补充条件后的自动重新解析）都必须经 gated 包一层，
 * 「扩大范围待确认」时一律拦下并给出可见原因。按钮的 disabled 读 blockedReason，与这里同源，
 * 不会出现「按钮可点、函数静默不干活」或反过来的分歧。
 */
export function useSubmitGate(scopeExpansion: ScopeExpansion | null) {
  const blockedReason = scopeExpansion ? SCOPE_EXPANSION_BLOCK_REASON : null;
  // 异步入口在 await 之后再判断时要读「此刻」的状态，而不是发起时那一帧的闭包。
  const latest = useRef(blockedReason);
  useEffect(() => { latest.current = blockedReason; });
  const gated = useCallback(
    <Args extends unknown[], Result>(action: (...args: Args) => Result) =>
      (...args: Args): Result | undefined => {
        if (latest.current) {
          toast.error(latest.current);
          return undefined;
        }
        return action(...args);
      },
    [],
  );
  return { blockedReason, gated };
}
