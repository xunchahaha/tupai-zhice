import { ChevronDown } from "lucide-react";
import { type ReactNode, useId, useState } from "react";

import { cn } from "@/lib/cn";

/**
 * 折叠区：默认收起，**展开时才挂载内容**（内容里的查询与轮询到展开才开始）。
 * 传 open/onOpenChange 为受控，页面可以从别处（如「手动排课」入口）把它展开。
 */
export function CollapsibleSection({
  title,
  hint,
  open: controlledOpen,
  onOpenChange,
  defaultOpen = false,
  className,
  children,
}: {
  title: string;
  hint?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  defaultOpen?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const [innerOpen, setInnerOpen] = useState(defaultOpen);
  const bodyId = useId();
  const open = controlledOpen ?? innerOpen;
  const toggle = () => {
    const next = !open;
    if (controlledOpen === undefined) setInnerOpen(next);
    onOpenChange?.(next);
  };
  return (
    <section className={cn("rounded-lg border border-zinc-200 bg-white shadow-2xs", className)}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-zinc-50/70"
        onClick={toggle}
      >
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
          <span className="text-sm font-semibold text-zinc-900">{title}</span>
          {hint ? <span className="text-xs text-zinc-400">{hint}</span> : null}
        </span>
        <ChevronDown className={cn("size-4 shrink-0 text-zinc-400 transition-transform duration-200", open && "rotate-180")} />
      </button>
      {open ? <div id={bodyId} className="border-t border-zinc-100 p-4">{children}</div> : null}
    </section>
  );
}
