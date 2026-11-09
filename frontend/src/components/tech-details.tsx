import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * 「技术细节」折叠块：原生 <details>，默认收起，内容常驻 DOM（getByText 能找到，但不可见）。
 * 只装纯技术或说明性内容；风险、阻断、失败、「无法验证」、冲突、数据丢弃、
 * 依据或基准变化的提示不要放进来，它们必须常驻、醒目。
 */
export function TechDetails({
  summary = "技术细节",
  children,
  className,
}: {
  summary?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <details className={cn("group", className)}>
      <summary className="inline-flex cursor-pointer list-none select-none items-center gap-1 text-xs text-zinc-400 hover:text-zinc-700 [&::-webkit-details-marker]:hidden">
        <ChevronRight aria-hidden="true" className="size-3 transition-transform group-open:rotate-90" />
        {summary}
      </summary>
      <div className="mt-1.5 border-l-2 border-zinc-200 pl-3 text-xs leading-5 text-zinc-500">{children}</div>
    </details>
  );
}
