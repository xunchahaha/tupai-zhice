import { useNavigate } from "react-router-dom";

import { type SolverRunResponse } from "@/api/generated/models";
import { CollapsibleSection } from "@/components/assistant/collapsible-section";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { datetime } from "@/lib/format";
import { modelStatusLabel, statusLabel } from "@/lib/labels";
import { assistantPath } from "@/lib/routes";
import { modelStatusTone, statusTone } from "@/lib/status";

/**
 * 「求解记录」折叠区：每一行都能点开看这次求解的结果与诊断（?run=<id>）。
 * 用来查看没有绑定任务的求解，也是旧「无解诊断」地址的落点。
 */
export function RunRecords({ runs }: { runs: SolverRunResponse[] }) {
  const navigate = useNavigate();
  return (
    <CollapsibleSection title="求解记录" hint={`共 ${runs.length} 次 · 点一行查看结果与诊断`}>
      {runs.length === 0 ? (
        <p className="text-sm text-zinc-500">还没有求解记录。</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="bg-zinc-50 text-xs text-zinc-500">
              <tr>
                <th className="h-9 px-4">任务</th>
                <th>状态</th>
                <th>结果</th>
                <th>目标值</th>
                <th>最佳界</th>
                <th>耗时</th>
                <th>创建时间</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr
                  key={run.id}
                  className="cursor-pointer border-t border-zinc-100 transition-colors hover:bg-blue-50/30"
                  onClick={() => navigate(assistantPath({ run: run.id }))}
                >
                  <td className="h-10 px-4">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="font-mono text-xs"
                      aria-label={`查看求解记录 ${run.id.slice(0, 8)}`}
                      onClick={(event) => { event.stopPropagation(); navigate(assistantPath({ run: run.id })); }}
                    >
                      {run.id.slice(0, 8)}
                    </Button>
                  </td>
                  <td><Badge tone={run.status === "completed" ? modelStatusTone(run.model_status) : statusTone(run.status)}>{statusLabel(run.status)}</Badge></td>
                  <td>{modelStatusLabel(run.model_status, run.presolve_infeasible)}</td>
                  <td>{run.objective_value?.toFixed(1) ?? "-"}</td>
                  <td>{run.best_bound?.toFixed(1) ?? "-"}</td>
                  <td>{run.wall_time_seconds?.toFixed(2) ?? "-"} 秒</td>
                  <td className="text-xs text-zinc-500">{datetime(run.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CollapsibleSection>
  );
}
