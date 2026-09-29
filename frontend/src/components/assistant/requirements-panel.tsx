import { Link } from "react-router-dom";

import { type AssistantTask } from "@/components/assistant/use-assistant-task";
import { buildRequirementItems } from "@/lib/assistant-task";
import { type MemoryUsageSnapshot, memoryEntryPath, memoryOutcomeDescription, memoryOutcomeLabel, splitMemoryOutcomes } from "@/lib/memory-usage";
import { ROUTES } from "@/lib/routes";
import { type AssistantTaskConstraint, parseGoalContext } from "@/lib/task-context";

const linkClass = "text-blue-700 underline-offset-2 hover:underline";

/**
 * 「本次要求」常驻面板（任务条下方，业务语言）：本次要求由范围 + 任务级约束推导；
 * 「参考的常用偏好」来自这次求解冻结的 memory_usage，逐条带「查看/修改」直达记忆页。
 * 只展示实际随请求提交的任务约束：走手动排课路径时请求体不带解析出的任务约束，面板如实说明，不能写成已生效。
 * 编译失败、未采用的偏好直接可见，不折叠进详情；管理入口并列：常用要求（记忆）与学校通用规则。
 */
export function RequirementsPanel({ task }: { task: AssistantTask }) {
  const { interpretation, goal, params } = task;
  const memory = task.activeRun?.memory_usage as MemoryUsageSnapshot | null | undefined;
  const showRequirements = Boolean(interpretation || goal);
  const showMemory = Boolean(memory?.status);
  // 续办时没有新解析，软约束取任务上下文里记下的那份。
  const contextConstraints = parseGoalContext(goal?.context)?.soft_task_constraints;
  const withheld = task.constraintsWithheld && Boolean(interpretation?.task_constraints?.length);
  const constraints: AssistantTaskConstraint[] = withheld ? contextConstraints ?? [] : interpretation?.task_constraints ?? contextConstraints ?? [];
  const items = buildRequirementItems({ scope: params, constraints });
  const { compileFailed, applied, notApplied } = splitMemoryOutcomes(memory);
  return (
    <section aria-label="本次要求" className="rounded-lg border border-zinc-200 bg-white p-4 text-sm shadow-2xs">
      {showRequirements ? (
        <p className="leading-6 text-zinc-800">
          <span className="font-medium text-zinc-600">本次要求：</span>
          {items.join("；")}
        </p>
      ) : null}
      {withheld ? (
        <p role="status" className="mt-2 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
          这次是按参数手动排课，没有带上解析出的本次要求（{interpretation?.task_constraints?.map((item) => item.source_text).join("；")}）；确认上面的理解并开始求解后，这些要求才会生效。
        </p>
      ) : null}
      {showMemory ? (
        <div className={showRequirements ? "mt-3" : undefined}>
          <div className="text-xs font-medium text-zinc-500">参考的常用偏好</div>
          {compileFailed ? (
            <p role="alert" className="mt-1.5 border-l-2 border-red-400 bg-red-50 px-3 py-2 text-xs leading-5 text-red-900">
              本次没能参考常用偏好：编译失败{memory?.detail ? `（${memory.detail}）` : ""}。本次求解照常完成，但未参考任何常用偏好；修复后重新求解即可带上偏好。
            </p>
          ) : applied.length ? (
            <ul className="mt-1.5 space-y-1 text-xs leading-5 text-zinc-700">
              {applied.map((item) => (
                <li key={`${item.entry_id}-applied`} className="flex flex-wrap items-center gap-2">
                  <span>{memoryOutcomeDescription(item)}</span>
                  <Link className={linkClass} to={memoryEntryPath(item.entry_id)}>查看/修改</Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1.5 text-xs text-zinc-500">这次没有可参考的常用偏好。</p>
          )}
          {!compileFailed && notApplied.length ? (
            <div className="mt-2 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
              <div className="font-medium">这些偏好本次没有采用</div>
              <ul className="mt-1 space-y-0.5">
                {notApplied.map((item) => (
                  <li key={`${item.entry_id}-${item.outcome}`} className="flex flex-wrap items-center gap-2">
                    <span>
                      {memoryOutcomeDescription(item)}：{memoryOutcomeLabel(item.outcome)}
                      {item.detail ? `——${item.detail}` : ""}
                    </span>
                    <Link className={linkClass} to={memoryEntryPath(item.entry_id)}>查看/修改</Link>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
      <div className={(showRequirements || showMemory ? "mt-3 " : "") + "flex flex-wrap items-center gap-x-4 gap-y-1 text-xs"}>
        <Link className={linkClass} to={ROUTES.memory}>管理常用要求</Link>
        <Link className={linkClass} to={ROUTES.rules}>学校通用规则</Link>
        <span className="text-zinc-400">这里的要求只作用于本次任务；长期有效的要求请到上面两处维护。</span>
      </div>
    </section>
  );
}
