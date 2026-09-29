import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { goalSubjectTypeLabel } from "@/lib/goal";
import { memoryEntryPath } from "@/lib/memory-usage";
import { ROUTES } from "@/lib/routes";
import {
  type AssistantMemoryActionReceipt,
  type AssistantTaskConstraint,
  memoryReceiptStatusLabel,
  TASK_CONSTRAINT_SOURCE_NOTE,
  taskConstraintHardnessLabel,
} from "@/lib/task-context";

const linkClass = "text-blue-700 underline-offset-2 hover:underline";

/** 范围里的一格：标签 + 取值徽标，没有限定就说「全部」。 */
export function ScopeItem({ label, values }: { label: string; values: string[] }) {
  return (
    <div>
      <div className="text-xs text-zinc-400">{label}</div>
      <div className="mt-1 flex flex-wrap gap-1">
        {values.length ? values.map((value) => <Badge key={value} tone="blue">{value}</Badge>) : <span className="text-xs text-zinc-500">全部</span>}
      </div>
    </div>
  );
}

/**
 * 确认卡「本次任务要求」区（07 §6.1）：解析产出的任务级约束逐条展示
 * （主体×时段 + hard/soft 徽标），固定文案声明作用域——仅本次任务、不进规则库。
 * 与 solveFromInterpretation 请求体的 task_constraints 同源（同一 interpretation 状态）。
 */
export function TaskConstraintsPanel({ constraints }: { constraints: AssistantTaskConstraint[] }) {
  return (
    <div aria-label="本次任务要求" className="mt-4 border-t border-blue-200 pt-4">
      <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-zinc-500">
        本次任务要求
        <Badge tone="neutral">{TASK_CONSTRAINT_SOURCE_NOTE}</Badge>
      </div>
      <ul className="mt-2 space-y-1.5">
        {constraints.map((item, index) => (
          <li key={item.id || `${item.source_text}-${index}`} className="flex flex-wrap items-center gap-2 border-l-2 border-zinc-200 pl-3 text-xs leading-5 text-zinc-700">
            <Badge tone={item.hardness === "soft" ? "neutral" : "blue"}>{taskConstraintHardnessLabel(item.hardness)}</Badge>
            <span>{item.source_text}</span>
            <span className="text-zinc-400">
              {goalSubjectTypeLabel(item.subject_type)} {item.subject_ids.join("、")}
              {item.slot_business_ids.length ? ` × 时段 ${item.slot_business_ids.join("、")}` : " × 时段待补充"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * 确认卡「记忆动作回执」区（07 §6.1）：explicit 动作已直接执行→绿色徽标 +
 * 文案（后端固定带「可在记忆页修改或撤销」）；推测或降级→
 * 黄色徽标 + 「已放入记忆收件箱待确认」。后端文案里的「记忆」页在导航里已改叫「常用偏好」，
 * 所以每条回执旁边补一个直达链接（有条目就直达该条），不依赖读者去猜入口。
 */
export function MemoryReceiptsPanel({ receipts }: { receipts: AssistantMemoryActionReceipt[] }) {
  return (
    <div aria-label="记忆动作回执" className="mt-4 border-t border-blue-200 pt-4">
      <div className="text-xs font-medium text-zinc-500">记忆动作回执</div>
      <ul className="mt-2 space-y-1.5">
        {receipts.map((item, index) => (
          <li key={item.action_id || `${item.receipt}-${index}`} className="flex flex-wrap items-center gap-2 border-l-2 border-zinc-200 pl-3 text-xs leading-5 text-zinc-700">
            <Badge tone={item.status === "executed" ? "green" : "yellow"}>{memoryReceiptStatusLabel(item.status)}</Badge>
            <span>{item.receipt}</span>
            {item.status !== "executed" ? <span className="text-amber-800">已放入记忆收件箱待确认</span> : null}
            <Link className={linkClass} to={item.entry_id ? memoryEntryPath(item.entry_id) : ROUTES.memory}>
              {item.status === "executed" ? "在常用偏好里修改或撤销" : "去常用偏好里确认"}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
