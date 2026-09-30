import { Button } from "@/components/ui/button";
import { type ScopeExpansion } from "@/lib/solver-params";

/**
 * 范围将要扩大：把范围从限定改成「全部」超出了解析确认过的范围，需要单独确认。
 * 这条提示必须直接展示（不能折叠进手动排课面板），确认前所有求解入口都被禁用。
 */
export function ScopeExpansionAlert({ expansion, onConfirm, onRevert }: { expansion: ScopeExpansion; onConfirm: () => void; onRevert: () => void }) {
  const fields = [
    expansion.business_lines ? "业务线" : null,
    expansion.class_business_ids ? "班级范围" : null,
    expansion.course_business_ids ? "课次范围（原来只调整选中的课次）" : null,
  ].filter(Boolean).join("、");
  return (
    <div role="alert" className="border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <div>
        范围将要扩大：{fields}将从限定范围改为「全部」，超出解析确认的范围。扩大范围需要单独确认，确认前不能开始求解。
      </div>
      <div className="mt-2 flex gap-2">
        <Button size="sm" variant="outline" onClick={onConfirm}>确认扩大范围</Button>
        <Button size="sm" variant="outline" onClick={onRevert}>恢复原范围</Button>
      </div>
    </div>
  );
}
