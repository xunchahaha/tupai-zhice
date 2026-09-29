import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

import { type GoalDetailResponse } from "@/api/generated/models";
import { GoalChecklistParamForm } from "@/components/goal/goal-supplement-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { datetime } from "@/lib/format";
import {
  goalKindLabel,
  isBottomLineItem,
  isNeedsParamsItem,
  type GoalChecklistEntry,
  type GoalChecklistSnapshot,
} from "@/lib/goal";

/**
 * 验收清单（含底线徽标）与只读的历史版本折叠。
 *
 * 参数待补的禁排占位项：传了 onQuantizeRequest 才出「补齐参数」按钮，点击只上报清单项 key，
 * 由宿主决定怎么处理；宿主再把要展开的 key 放进 paramFormKeys，清单就在该项下就地展开补参表单，
 * 取消/保存成功后回调 onParamFormClose。都不传则是纯只读清单。
 * 历史折叠状态是组件内部的，换目标时请用 key={goal.id} 重置。
 */
export function GoalChecklist({
  goal,
  onQuantizeRequest,
  paramFormKeys,
  onParamFormClose,
}: {
  goal: GoalDetailResponse;
  onQuantizeRequest?: (entryKey: string) => void;
  paramFormKeys?: string[];
  onParamFormClose?: (entryKey: string) => void;
}) {
  const [showHistory, setShowHistory] = useState(false);
  const entries = (goal.checklist ?? []) as GoalChecklistEntry[];
  const checklistHistory = (goal.checklist_history ?? []) as GoalChecklistSnapshot[];
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-zinc-500">
        <span>验收清单 v{goal.checklist_version ?? 1}（底线项不可删除，与附加项并列验收）</span>
        {checklistHistory.length ? (
          <button
            type="button"
            aria-expanded={showHistory}
            className="inline-flex items-center gap-0.5 text-blue-600 transition-colors hover:underline"
            onClick={() => setShowHistory((value) => !value)}
          >
            {showHistory ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
            {showHistory ? "收起历史版本" : `历史版本（${checklistHistory.length}）`}
          </button>
        ) : null}
      </div>
      {showHistory ? (
        <ul className="mt-2 space-y-2">
          {[...checklistHistory].reverse().map((snapshot, snapshotIndex) => (
            <li key={String(snapshot.version ?? snapshotIndex)} className="rounded border border-zinc-100 bg-zinc-50/60 px-3 py-2">
              <div className="text-xs text-zinc-500">
                v{snapshot.version ?? "?"} · 保存于 {snapshot.saved_at ? datetime(snapshot.saved_at) : "—"}
              </div>
              <ul className="mt-1 space-y-0.5">
                {(snapshot.items ?? []).map((item, index) => (
                  <li key={String(item.key ?? index)} className="text-xs leading-5 text-zinc-500">
                    {goalKindLabel(item.kind)} · {item.requirement ?? ""}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      ) : null}
      <ul className="mt-2 space-y-1.5">
        {entries.map((entry, index) => {
          const key = String(entry.key ?? `${entry.kind}-${index}`);
          const needsParams = isNeedsParamsItem(entry);
          const paramFormOpen = Boolean(paramFormKeys?.includes(key));
          return (
            <li key={key} className="rounded border border-zinc-100 px-3 py-2 text-xs leading-5 text-zinc-700">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium">{goalKindLabel(entry.kind)}</span>
                {isBottomLineItem({ params: entry.params }) ? <Badge tone="blue">底线</Badge> : null}
                <span>· {entry.requirement ?? ""}</span>
                {needsParams && onQuantizeRequest ? (
                  <Button size="sm" variant="outline" aria-expanded={paramFormOpen} onClick={() => onQuantizeRequest(key)}>
                    补齐参数
                  </Button>
                ) : null}
              </div>
              {needsParams && paramFormOpen ? (
                <GoalChecklistParamForm
                  goal={goal}
                  entry={entry}
                  onCancel={() => onParamFormClose?.(key)}
                  onSaved={() => onParamFormClose?.(key)}
                />
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
