import { CheckCircle2, Circle, ListChecks } from "lucide-react";
import { Link } from "react-router-dom";

import { cn } from "@/lib/cn";
import { ROUTES, schedulePath, settingsPath } from "@/lib/routes";
import { type SetupChecklistState } from "@/lib/setup-state";

interface ChecklistItem {
  key: string;
  label: string;
  to: string;
  actionLabel: string;
  done: boolean;
  /** 可选项未就绪不是错误态，只标注「可选」。 */
  optional?: boolean;
}

function checklistItems(state: SetupChecklistState): ChecklistItem[] {
  return [
    { key: "master-data", label: "基础资料已导入", to: ROUTES.masterData, actionLabel: "先导入课程资料", done: state.masterDataImported },
    { key: "rules", label: "已确认排课规则", to: ROUTES.rules, actionLabel: "去配规则", done: state.activeRuleCount > 0 },
    { key: "ai", label: "一句话排课 AI", to: settingsPath("ai"), actionLabel: "去配置", done: state.aiConfigured === true, optional: true },
    { key: "publish", label: "已发布课表版本", to: schedulePath({ view: "history" }), actionLabel: "去发布", done: state.publishedScheduleCount > 0 },
  ];
}

/**
 * 排课就绪度清单：完整版（独立卡片）+ 助手首页的紧凑版（单行四点，只在有未完成项时出现）。
 * 数据全部来自调用方已就绪的查询，组件自身不发列表请求。
 */
export function SetupChecklist({ variant = "full", ...state }: SetupChecklistState & { variant?: "full" | "compact" }) {
  const items = checklistItems(state);
  const doneCount = items.filter((item) => item.done).length;

  if (variant === "compact") {
    return (
      <div aria-label="排课准备清单" className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-500">
        <span className="inline-flex items-center gap-1 font-medium text-zinc-600">
          <ListChecks className="size-3.5 text-blue-600" />
          排课准备
        </span>
        {items.map((item) => (
          <Link
            key={item.key}
            to={item.to}
            className="inline-flex items-center gap-1 transition-colors duration-150 hover:text-zinc-800"
            title={item.done ? `${item.label}：已就绪` : item.optional ? `${item.label}：可选` : `${item.label}：待完成`}
          >
            {item.done ? (
              <CheckCircle2 className="size-3.5 shrink-0 text-emerald-600" />
            ) : (
              <Circle className="size-3.5 shrink-0 text-zinc-300" />
            )}
            <span className={cn(item.done && "text-zinc-600")}>{item.label}</span>
            {item.optional && !item.done ? <span className="text-zinc-400">可选</span> : null}
            {/* 首次使用：资料还没导入时明确提示先做这一步。 */}
            {item.key === "master-data" && !item.done ? <span className="font-medium text-amber-700">{item.actionLabel}</span> : null}
          </Link>
        ))}
      </div>
    );
  }

  return (
    <section className="animate-fade-in rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xs">
      <div className="flex items-center justify-between border-b border-zinc-100 pb-3">
        <div className="flex items-center gap-2">
          <ListChecks className="size-4 text-blue-600" />
          <h2 className="text-sm font-semibold text-zinc-900">排课准备清单</h2>
        </div>
        <span className="text-xs text-zinc-400 tabular-nums">{doneCount}/{items.length} 已就绪</span>
      </div>
      <ul className="mt-4 grid gap-2 sm:grid-cols-2">
        {items.map((item) => (
          <li
            key={item.key}
            className={cn(
              "flex items-center justify-between gap-2 rounded-lg border px-3 py-2.5",
              item.done ? "border-emerald-100 bg-emerald-50/40" : "border-zinc-200 bg-zinc-50/50",
            )}
          >
            <span className="flex min-w-0 items-center gap-2 text-xs">
              {item.done ? (
                <CheckCircle2 className="size-4 shrink-0 text-emerald-600" />
              ) : (
                <Circle className="size-4 shrink-0 text-zinc-300" />
              )}
              <span className={cn("truncate", item.done ? "font-medium text-zinc-800" : "text-zinc-600")}>{item.label}</span>
              {item.optional && !item.done ? (
                <span className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-500">可选</span>
              ) : null}
            </span>
            {item.done ? null : (
              <Link to={item.to} className="shrink-0 text-xs text-blue-600 transition-colors hover:text-blue-700 hover:underline">
                {item.actionLabel}
              </Link>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
