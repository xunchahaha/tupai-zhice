import { Link } from "react-router-dom";

import { type GoalDetailResponse } from "@/api/generated/models";
import { GoalSupplementPanel } from "@/components/goal/goal-supplement-panel";
import { Button } from "@/components/ui/button";
import { hasQuantizablePlaceholder } from "@/lib/assistant-task";
import { goalNeedsParams } from "@/lib/goal";
import { type Interpretation } from "@/lib/interpret-stream";
import { ROUTES } from "@/lib/routes";

/**
 * 未落实的要求（unsupported_requirements）：必须直接展示在确认卡上，不能折叠，更不能悄悄丢掉。
 * 存在可量化的占位项（缺教师/班级/教室与时段的禁排要求）时给出「还差一个条件」——先按需登记任务，
 * 再就地补齐并自动用同一任务重新解析；无法行内补充的，说明去向（改指令或去规则配置）。
 * 无论哪种，都不允许带着这些要求开始求解。
 */
export function UnsupportedBlock({
  interpretation,
  goal,
  goalId,
  supplementing,
  busy,
  onStartSupplement,
  onSaved,
}: {
  interpretation: Interpretation;
  goal: GoalDetailResponse | undefined;
  goalId: string;
  supplementing: boolean;
  busy: boolean;
  onStartSupplement: () => void;
  onSaved: () => void;
}) {
  const requirements = interpretation.unsupported_requirements ?? [];
  if (!requirements.length) return null;
  const quantizable = hasQuantizablePlaceholder(interpretation);
  const goalHasPlaceholder = goalNeedsParams(goal);
  const showPanel = quantizable && (supplementing || Boolean(goalId));
  return (
    <div role="alert" className="mt-3 border-l-2 border-amber-500 bg-amber-50 p-3 text-xs text-amber-900">
      <strong>以下要求尚未进入求解（待补充）：</strong>
      <ul className="mt-1 list-disc pl-5">
        {requirements.map((requirement) => <li key={requirement}>{requirement}</li>)}
      </ul>
      {quantizable ? (
        <div className="mt-2">
          <p className="font-medium">还差一个条件：补充具体的教师 / 班级 / 教室和时段后，这些要求才能真正参与排课。</p>
          <p className="mt-1">补充保存后会自动重新解析；在此之前不能开始求解，这些要求也不会被悄悄丢掉。也可以修改需求后重新解析。</p>
          {!showPanel ? (
            <Button className="mt-2" size="sm" variant="outline" onClick={onStartSupplement} disabled={busy}>补充条件</Button>
          ) : !goal ? (
            <p className="mt-2 text-zinc-600">正在读取任务…</p>
          ) : goalHasPlaceholder ? (
            <div className="mt-2 rounded-md border border-amber-200 bg-white/70 p-2">
              <GoalSupplementPanel goal={goal} autoOpen onSaved={onSaved} />
            </div>
          ) : (
            <p className="mt-2">任务里没有待补充的条件，请修改需求后重新解析。</p>
          )}
        </div>
      ) : (
        <p className="mt-2">
          不支持带着未实现的要求开始求解：请修改需求去掉这些要求后重新解析，或先在
          <Link className="mx-0.5 text-blue-700 underline-offset-2 hover:underline" to={ROUTES.rules}>学校通用规则</Link>
          里配置对应规则。
        </p>
      )}
    </div>
  );
}
