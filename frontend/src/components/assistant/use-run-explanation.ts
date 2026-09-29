import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { getListSolverRunsApiV1SolverRunsGetQueryKey } from "@/api/generated/client";
import { type SolverRunExplanation, type SolverRunResponse } from "@/api/generated/models";
import { http } from "@/api/http";
import { errorMessage } from "@/lib/format";

/**
 * 排不出来时后端会多给一段可直接粘回需求输入框的指令草稿。
 * 这段文字由后端确定性拼出，不在 orval 生成的模型里，故在这里就地扩展类型。
 */
export type SolverRunExplanationDetail = SolverRunExplanation & { suggested_instruction?: string | null };

/**
 * 一次求解的结果解释（自动请求一次 + 手动重新解析）。放在页面级而不是详情面板里：
 * 「卡住了」卡要直接用它的下一步建议，而详情折叠时面板并不挂载，自动请求不能因此丢失。
 *
 * 这里刻意不用 orval 生成的 useMutation：自动解析是在 effect 里发起的，StrictMode 下
 * mutation observer 会在双次 effect 之间被摘掉，请求成功也回不到组件，加载态就永远停在
 * 「解析中」。改成直接调 http，finally 一定收尾。
 */
export function useRunExplanation(run: SolverRunResponse | null) {
  const client = useQueryClient();
  const runId = run?.id ?? "";
  // 请求得到的解释按 run 记账：换一次求解任务，上一条解释不会挂在新任务下面。
  const [fetched, setFetched] = useState<{ runId: string; value: SolverRunExplanationDetail } | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [failure, setFailure] = useState("");
  const explanation: SolverRunExplanationDetail | null =
    (fetched && fetched.runId === runId ? fetched.value : null) ?? (run?.explanation as SolverRunExplanationDetail | null | undefined) ?? null;
  const analyze = useCallback(
    async (targetRunId: string, options: { refresh: boolean; auto: boolean }) => {
      setAnalyzing(true);
      setFailure("");
      try {
        const { data } = await http.post<SolverRunExplanationDetail>(
          `/api/v1/solver-runs/${targetRunId}/explanation`,
          undefined,
          { params: { refresh: options.refresh } },
        );
        setFetched({ runId: targetRunId, value: data });
        void client.invalidateQueries({ queryKey: getListSolverRunsApiV1SolverRunsGetQueryKey() });
      } catch (error) {
        const message = errorMessage(error);
        setFailure(message);
        // 自动解析失败不弹 toast：用户没点过任何东西，弹窗只会让人以为求解本身出了问题。
        if (!options.auto) toast.error(message);
      } finally {
        setAnalyzing(false);
      }
    },
    [client],
  );
  // 换任务时清掉上一个任务的失败提示。
  useEffect(() => { setFailure(""); }, [runId]);
  const finished = run?.status === "completed";
  const explained = Boolean(explanation);
  // 求解一到终态就自动解析，用户不用再点一次；同一个任务只自动发一次，
  // 失败后由「重新解析」接管，避免解析接口不可用时无限重试。
  const autoRequested = useRef("");
  useEffect(() => {
    if (!finished || !runId || explained || autoRequested.current === runId) return;
    autoRequested.current = runId;
    void analyze(runId, { refresh: false, auto: true });
  }, [analyze, explained, finished, runId]);
  return { explanation, analyzing, failure, analyze };
}

export type RunExplanationState = ReturnType<typeof useRunExplanation>;
