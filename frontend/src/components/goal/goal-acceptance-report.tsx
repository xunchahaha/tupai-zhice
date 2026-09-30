import { type GoalDetailResponse } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { goalChecklistVersion, goalKindLabel, goalNeedsParams, type GoalReport, reportChecklistVersion } from "@/lib/goal";

/**
 * 「当前结论 / 最新验收」：报告绑定清单版本（MEM-E2/E2a）。验收 pending（清单修订后未重新验收）时，
 * 旧报告是「历史版本结论」，不得当成当前口径展示，改显示「等待新验收（v{n}）」。
 *
 * 缺口的 remedy 动作化（07 §5.2）：raise_budget / resolve_scope / fix_checklist 各出一个按钮，
 * 点击只回调、不在这里跳转，由宿主决定去哪儿办；没传对应回调就不出按钮。
 * await_admin 是人的裁决，保持文本、无按钮。
 */
export function GoalAcceptanceReport({
  goal,
  report,
  onRaiseBudget,
  raiseBudgetBlockedReason,
  onResolveScope,
  onFixChecklist,
}: {
  goal: GoalDetailResponse;
  report: GoalReport | null;
  onRaiseBudget?: () => void;
  /** 宿主此刻不允许加预算重跑的原因（扩大范围待确认、任务范围未恢复……）：给了就禁用按钮并说明。 */
  raiseBudgetBlockedReason?: string | null;
  onResolveScope?: () => void;
  onFixChecklist?: () => void;
}) {
  if (!report) return null;
  const goalVersion = goalChecklistVersion(goal);
  const reportVersion = reportChecklistVersion(report);
  const staleReport = goal.acceptance_status === "pending" && reportVersion < goalVersion;
  if (staleReport) {
    return (
      <div>
        <div className="flex items-center gap-2 text-xs font-medium text-zinc-500">
          当前结论
          <Badge tone="blue">等待新验收（v{goalVersion}）</Badge>
        </div>
        <p className="mt-1 text-xs leading-5 text-zinc-600">
          清单已修订至 v{goalVersion}，旧验收结论按当时口径保留在下方求解记录中；
          重新关联求解后按 v{goalVersion} 出具新结论。
        </p>
      </div>
    );
  }
  // 只有清单里真有待补的禁排占位项，「修订目标清单」才有地方可去。
  const hasNeedsParams = goalNeedsParams(goal);
  return (
    <div>
      <div className="flex items-center gap-2 text-xs font-medium text-zinc-500">
        最新验收
        {report.acceptance_status === "failed" ? (
          <Badge tone="yellow">验收失败</Badge>
        ) : (
          <Badge tone={report.all_passed ? "green" : "yellow"}>
            {report.all_passed ? `全部 ${report.passed_count} 项通过` : `${report.failed_count} 项缺口`}
          </Badge>
        )}
        {reportVersion < goalVersion ? (
          <Badge tone="neutral">历史版本 v{reportVersion} 的结论</Badge>
        ) : null}
      </div>
      {report.meta?.version_note ? (
        <p className="mt-1 text-xs text-zinc-500">{report.meta.version_note}</p>
      ) : null}
      {report.acceptance_status === "failed" ? (
        <p className="mt-1 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          验收失败：{report.acceptance_error ?? "验收器执行时发生异常，未能生成报告"}
        </p>
      ) : null}
      <ul className="mt-2 space-y-1 text-xs">
        {report.items.map((item) => (
          <li key={item.key} className="flex gap-2">
            <span aria-hidden className={item.passed ? "text-emerald-600" : item.verdict === "unverifiable" ? "text-amber-600" : "text-red-600"}>{item.passed ? "✓" : item.verdict === "unverifiable" ? "?" : "✗"}</span>
            <span className="text-zinc-700">
              {goalKindLabel(item.kind)}
              {item.bottom_line ? <Badge tone="blue">底线</Badge> : null}
              {item.verdict === "unverifiable" ? <Badge tone="yellow">无法验证</Badge> : null}
              ——{item.detail}
            </span>
          </li>
        ))}
      </ul>
      {report.gaps.length ? (
        <div className="mt-2 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <ul className="list-disc space-y-0.5 pl-4">
            {report.gaps.map((gap) => (
              <li key={gap.key} className="flex flex-wrap items-center gap-2">
                <span>{gap.next_step}</span>
                {gap.remedy === "raise_budget" && onRaiseBudget ? (
                  <Button size="sm" variant="outline" disabled={Boolean(raiseBudgetBlockedReason)} title={raiseBudgetBlockedReason ?? undefined} onClick={onRaiseBudget}>加大时间预算重跑</Button>
                ) : null}
                {gap.remedy === "fix_checklist" && hasNeedsParams && onFixChecklist ? (
                  <Button size="sm" variant="outline" onClick={onFixChecklist}>补充条件</Button>
                ) : null}
                {gap.remedy === "resolve_scope" && onResolveScope ? (
                  <Button size="sm" variant="outline" onClick={onResolveScope}>修正范围</Button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
