import { Navigate, useLocation, type Location, type To } from "react-router-dom";

import { useListSolverRunsApiV1SolverRunsGet } from "@/api/generated/client";
import { type SolverRunResponse } from "@/api/generated/models";
import { LoadingState } from "@/components/page";
import { asArray } from "@/lib/format";
import { assistantPath } from "@/lib/routes";

/**
 * 旧地址 → 新入口：目标自带的 query 优先，原地址上的其余 query 与 hash 原样带过去。
 * 书签、聊天里的旧链接（如 /solver?goal=…&action=raise_budget）靠这一步继续可用。
 */
function carryOver(target: string, location: Location): To {
  const [pathname, targetSearch = ""] = target.split("?");
  const params = new URLSearchParams(targetSearch);
  for (const [key, value] of new URLSearchParams(location.search)) {
    if (!params.has(key)) params.append(key, value);
  }
  const search = params.toString();
  return { pathname, search: search ? `?${search}` : "", hash: location.hash };
}

export function LegacyRedirect({ to }: { to: string }) {
  const location = useLocation();
  return <Navigate to={carryOver(to, location)} replace />;
}

function latestInfeasibleRun(runs: SolverRunResponse[]): SolverRunResponse | undefined {
  let latest: SolverRunResponse | undefined;
  let latestAt = Number.NEGATIVE_INFINITY;
  for (const run of runs) {
    if (run.model_status !== "INFEASIBLE") continue;
    const at = Date.parse(run.created_at);
    // created_at 解析失败的任务只在没有其它候选时才会被选中，避免脏数据顶掉真正最新的任务。
    if (!latest || at > latestAt) {
      latest = run;
      latestAt = Number.isNaN(at) ? Number.NEGATIVE_INFINITY : at;
    }
  }
  return latest;
}

// 旧「无解诊断」是全局无解任务列表；现在诊断挂在某次求解结果上，所以先找到最新一次无解任务再跳。
// 请求失败或没有无解任务都回落到助手首页，不让旧链接停在空白页。
export function DiagnosticsRedirect() {
  const location = useLocation();
  const hasRun = new URLSearchParams(location.search).has("run");
  const runs = useListSolverRunsApiV1SolverRunsGet({ query: { enabled: !hasRun } });

  if (hasRun) return <LegacyRedirect to={assistantPath()} />;
  if (runs.isPending) return <LoadingState />;
  const latest = runs.isError ? undefined : latestInfeasibleRun(asArray<SolverRunResponse>(runs.data));
  return <LegacyRedirect to={latest ? assistantPath({ run: latest.id }) : assistantPath()} />;
}
