import { Link } from "react-router-dom";

import { InfoTooltip } from "@/components/assistant/info-tooltip";
import { Badge } from "@/components/ui/badge";
import { formatSlot } from "@/lib/format";
import { goalSubjectTypeLabel } from "@/lib/goal";
import { memoryEntryPath } from "@/lib/memory-usage";
import { ROUTES } from "@/lib/routes";
import {
  type AssistantMemoryActionReceipt,
  type AssistantTaskConstraint,
  memoryReceiptStatusLabel,
  STATIC_COVERAGE_NOTE_PLAIN,
  TASK_CONSTRAINT_SOURCE_NOTE,
  taskConstraintHardnessLabel,
  taskConstraintOpLabel,
} from "@/lib/task-context";

const linkClass = "text-blue-700 underline-offset-2 hover:underline";

/**
 * 确认卡「本次任务要求」区（07 §6.1）：解析产出的任务级约束逐条展示
 * （主体×时段 + 必须/尽量徽标），固定文案声明作用域——仅本次任务、不进规则库。
 * 与 solveFromInterpretation 请求体的 task_constraints 同源（同一 interpretation 状态）。
 * coverageNote：解析接口的固定说明句（哪些内容进入排课）以 tooltip 挂在标题旁。
 * quoteSource=false：原话已在宿主卡的「本次要求」一句里逐条写过，这里只列徽标和主体×时段（同一顺序），
 * 不再复述原话；「移除」项没有主体×时段可列，仍写原话。
 */
export function TaskConstraintsPanel({
  constraints,
  coverageNote = false,
  quoteSource = true,
}: {
  constraints: AssistantTaskConstraint[];
  coverageNote?: boolean;
  quoteSource?: boolean;
}) {
  return (
    <div aria-label="本次任务要求" className="mt-4 border-t border-blue-200 pt-4">
      <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-zinc-500">
        本次任务要求
        {coverageNote ? <InfoTooltip label="进入排课的内容">{STATIC_COVERAGE_NOTE_PLAIN}</InfoTooltip> : null}
        <Badge tone="neutral">{TASK_CONSTRAINT_SOURCE_NOTE}</Badge>
      </div>
      <ul className="mt-2 space-y-1.5">
        {constraints.map((item, index) => (
          <li key={item.id || `${item.source_text}-${index}`} className="flex flex-wrap items-center gap-2 border-l-2 border-zinc-200 pl-3 text-xs leading-5 text-zinc-700">
            {taskConstraintOpLabel(item.op) ? <Badge tone="yellow">{taskConstraintOpLabel(item.op)}</Badge> : null}
            {item.op === "remove" ? null : (
              <Badge tone={item.hardness === "soft" ? "neutral" : "blue"}>{taskConstraintHardnessLabel(item.hardness)}</Badge>
            )}
            {quoteSource || item.op === "remove" ? <span>{item.source_text}</span> : null}
            {item.op === "remove" ? null : (
              <span className="text-zinc-400">
                {goalSubjectTypeLabel(item.subject_type)} {item.subject_ids.join("、")}
                {item.slot_business_ids.length ? ` × 时段 ${item.slot_business_ids.map((slot) => formatSlot(slot)).join("、")}` : " × 时段待补充"}
              </span>
            )}
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
