import { NavLink, useLocation } from "react-router-dom";

import { cn } from "@/lib/cn";
import { SOP_STEPS } from "@/lib/sop";

/**
 * 横向 SOP 步骤条：当前步蓝底白字，已完成步蓝边，未到步灰显；步骤间细连线。
 * 位置由路由匹配决定；步骤本身是纯导航链接，只读成员照常显示。
 * 全部用现有 Tailwind token + transition，无新 keyframes。
 */
export function SopSteps() {
  const { pathname } = useLocation();
  const currentIndex = SOP_STEPS.findIndex((step) => step.to === pathname);
  return (
    <nav aria-label="排课流程" className="mt-3 flex items-center gap-1.5 overflow-x-auto whitespace-nowrap text-xs">
      {SOP_STEPS.map((step, index) => {
        const state = index < currentIndex ? "done" : index === currentIndex ? "current" : "todo";
        return (
          <div key={step.key} className="flex items-center gap-1.5">
            {index > 0 ? <span aria-hidden className="h-px w-5 shrink-0 border-t border-zinc-200 sm:w-8" /> : null}
            <NavLink
              to={step.to}
              className={cn(
                "group flex shrink-0 items-center gap-1.5 rounded-full py-0.5 pl-0.5 pr-1.5 transition-colors duration-150",
                state === "current" && "text-blue-700",
                state === "done" && "text-blue-600 hover:text-blue-700",
                state === "todo" && "text-zinc-500 hover:text-zinc-800",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "grid size-5 shrink-0 place-items-center rounded-full border tabular-nums transition-colors duration-150",
                  state === "current" && "border-blue-600 bg-blue-600 font-semibold text-white",
                  state === "done" && "border-blue-600 bg-white text-blue-600 group-hover:bg-blue-50",
                  state === "todo" && "border-zinc-300 bg-white text-zinc-400",
                )}
              >
                {index + 1}
              </span>
              <span className={cn(state === "current" && "font-medium")}>{step.label}</span>
            </NavLink>
          </div>
        );
      })}
    </nav>
  );
}
