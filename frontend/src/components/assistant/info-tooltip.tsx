import { CircleAlert } from "lucide-react";
import { type ReactNode, useId } from "react";

/** 字段旁的「i」说明气泡：悬停或聚焦时显示，键盘可达。 */
export function InfoTooltip({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  return (
    <span className="group relative inline-flex align-middle">
      <button
        type="button"
        aria-label={`${label}说明`}
        aria-describedby={id}
        className="inline-flex size-4 items-center justify-center rounded-full text-zinc-400 outline-none transition-colors hover:text-zinc-700 focus-visible:text-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
      >
        <CircleAlert className="size-3.5" aria-hidden="true" />
      </button>
      <span
        id={id}
        role="tooltip"
        className="pointer-events-none absolute left-0 top-[calc(100%+6px)] z-30 w-64 rounded-md bg-zinc-900 px-3 py-2 text-left text-xs leading-5 text-white opacity-0 shadow-lg transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
      >
        {children}
      </span>
    </span>
  );
}
