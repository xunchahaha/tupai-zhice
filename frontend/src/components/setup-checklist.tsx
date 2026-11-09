import { ListChecks } from "lucide-react";
import { Link } from "react-router-dom";

import { cn } from "@/lib/cn";
import { ROUTES, schedulePath } from "@/lib/routes";
import { type SetupChecklistState } from "@/lib/setup-state";

interface ChecklistItem {
  key: string;
  label: string;
  to: string;
  actionLabel: string;
  done: boolean;
}

/**
 * 还没完成的必需准备项，按要做的顺序。AI 是可选项、不算「还差」：
 * 没配置时同一屏的需求卡已经醒目提示并给出「去配置」，这里不再说第二遍。
 */
function pendingItems(state: SetupChecklistState): ChecklistItem[] {
  const items: ChecklistItem[] = [
    { key: "master-data", label: "基础资料已导入", to: ROUTES.masterData, actionLabel: "先导入课程资料", done: state.masterDataImported },
    { key: "rules", label: "已确认排课规则", to: ROUTES.rules, actionLabel: "去配规则", done: state.activeRuleCount > 0 },
    { key: "publish", label: "已发布课表版本", to: schedulePath({ view: "history" }), actionLabel: "去发布", done: state.publishedScheduleCount > 0 },
  ];
  return items.filter((item) => !item.done);
}

/**
 * 排课准备：助手首页标题下的一行，只列还没做完的项，并直接用动作措辞（「去配规则」）。
 * 全部就绪时不渲染。数据全部来自调用方已就绪的查询，组件自身不发列表请求。
 */
export function SetupChecklist(state: SetupChecklistState) {
  const items = pendingItems(state);
  if (!items.length) return null;
  return (
    <div aria-label="排课准备清单" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
      <span className="inline-flex items-center gap-1 font-medium text-zinc-600">
        <ListChecks className="size-3.5 text-blue-600" />
        排课准备还差 {items.length} 项：
      </span>
      {items.map((item) => (
        <Link
          key={item.key}
          to={item.to}
          title={item.label}
          className={cn(
            "underline-offset-2 transition-colors duration-150 hover:underline",
            // 首次使用：资料还没导入时明确提示先做这一步。
            item.key === "master-data" ? "font-medium text-amber-700" : "text-blue-700",
          )}
        >
          {item.actionLabel}
        </Link>
      ))}
    </div>
  );
}
