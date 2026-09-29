import { Link } from "react-router-dom";

import { useGetSolverRunApiV1SolverRunsRunIdGet, useListRulesApiV1RulesGet } from "@/api/generated/client";
import { type RuleResponse, type SolverRunResponse } from "@/api/generated/models";
import { RunDiagnosis } from "@/components/diagnosis/run-diagnosis";
import { ErrorState, LoadingState } from "@/components/page";
import { stuckHeadline } from "@/lib/assistant-task";
import { asArray } from "@/lib/format";
import { translateSystemConstraints } from "@/lib/labels";
import { schedulePath } from "@/lib/routes";
import { classifyRun } from "@/lib/run-kind";

function ProvenInfeasibleDiagnosis({ run }: { run: SolverRunResponse }) {
  const rules = useListRulesApiV1RulesGet();
  return (
    <div className="mt-4 rounded-lg border border-zinc-200 bg-white">
      {rules.isPending ? <LoadingState rows={2} /> : <RunDiagnosis run={run} rules={asArray<RuleResponse>(rules.data)} />}
    </div>
  );
}

/**
 * 没有排课权限的账号（审批人、只读成员）打开 ?run= 时看到的只读诊断：
 * 为什么没排出来、涉事规则、建议调整优先级与下一步建议。这里没有任何提交类按钮——
 * 重新解析、加预算、修正范围都是排课员的动作。旧的「无解诊断」页对所有角色可读，这条路径把它留住。
 */
export function RestrictedRunView({ runId }: { runId: string }) {
  const run = useGetSolverRunApiV1SolverRunsRunIdGet(runId, {
    // 还没跑完就低频刷新，跑完即止；只读账号不需要 700ms 级的进度。
    query: { enabled: Boolean(runId), refetchInterval: (query) => (query.state.data && query.state.data.status !== "running" && query.state.data.status !== "queued" ? false : 3000) },
  });
  if (run.isPending) return <LoadingState rows={2} />;
  if (run.isError || !run.data) return <ErrorState retry={() => void run.refetch()} />;
  const data = run.data;
  const kind = classifyRun(data);
  const priorities = Array.isArray(data.priority_explanations) ? data.priority_explanations : [];
  const nextActions = Array.isArray(data.explanation?.next_actions) ? (data.explanation.next_actions as string[]) : [];
  const provenInfeasible = data.model_status === "INFEASIBLE" && !data.presolve_infeasible && data.status === "completed";
  return (
    <section aria-label="求解诊断（只读）" className="rounded-lg border border-amber-300 bg-amber-50/50 p-5">
      {kind === "solving" ? (
        <h2 className="font-semibold">这次求解还在进行中</h2>
      ) : kind === "result" ? (
        <>
          <h2 className="font-semibold">这次求解已经排出了草稿</h2>
          <p className="mt-1 text-xs text-zinc-600">
            草稿可以在「课表」里查看。
            <Link className="ml-1 text-blue-700 underline-offset-2 hover:underline" to={schedulePath({ view: "history" })}>去历史版本</Link>
          </p>
        </>
      ) : (
        <>
          <h2 className="font-semibold">{stuckHeadline(data)}</h2>
          <p className="mt-1 text-xs text-zinc-600">这是只读的诊断说明；重新排课需要有排课权限的同事来处理。</p>
        </>
      )}
      {data.presolve_infeasible ? (
        <p className="mt-3 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">本次结论来自求解前的数据预检，求解器未运行，因此不能表述为「已证明无解」。</p>
      ) : null}
      {data.model_status === "UNKNOWN" && data.status === "completed" && !data.presolve_infeasible ? (
        <p role="status" className="mt-3 text-sm text-amber-800">本次尚未找到可用解，尚未证明无解。本次没有候选课表。</p>
      ) : null}
      {data.error_message ? <p className="mt-3 text-sm text-red-700">{data.error_message}</p> : null}
      {priorities.length && !provenInfeasible ? (
        <div className="mt-3 space-y-2 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="font-medium">可能的原因</div>
          {priorities.map((item, index) => <div key={`${index}-${item}`}>{translateSystemConstraints(item)}</div>)}
        </div>
      ) : null}
      {nextActions.length ? (
        <div className="mt-3 border-l-2 border-blue-400 bg-blue-50/60 px-4 py-3 text-sm text-zinc-800">
          <div className="text-xs font-medium text-blue-800">下一步可以做什么</div>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {nextActions.map((item, index) => <li key={`${index}-${item.slice(0, 12)}`}>{item}</li>)}
          </ul>
        </div>
      ) : null}
      {provenInfeasible ? <ProvenInfeasibleDiagnosis run={data} /> : null}
    </section>
  );
}
