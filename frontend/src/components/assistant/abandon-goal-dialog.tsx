import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getGetGoalApiV1GoalsGoalIdGetQueryKey, useAbandonGoalApiV1GoalsGoalIdAbandonPost } from "@/api/generated/client";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { errorMessage } from "@/lib/format";

/**
 * 不再跟进这个任务（后端叫放弃目标）：ConfirmDialog 二次确认，历史核对报告保留可查。
 * onAbandoned 让宿主在成功后收尾——例如任务视图里放弃的正是当前绑定的任务，要解除绑定并回首页。
 */
export function AbandonGoalDialog({
  goal,
  onClose,
  onAbandoned,
}: {
  goal: { id: string; instruction: string } | null;
  onClose: () => void;
  onAbandoned?: (goalId: string) => void;
}) {
  const client = useQueryClient();
  const abandon = useAbandonGoalApiV1GoalsGoalIdAbandonPost({
    mutation: {
      onSuccess: () => {
        toast.success("已不再跟进这个任务");
        if (goal) void client.invalidateQueries({ queryKey: getGetGoalApiV1GoalsGoalIdGetQueryKey(goal.id) });
        void client.invalidateQueries();
        if (goal) onAbandoned?.(goal.id);
        onClose();
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  return (
    <ConfirmDialog
      open={goal !== null}
      title="不再跟进这个任务？"
      description={`不再跟进后，助手不再自动核对「${goal?.instruction ?? ""}」的要求，也不能再用它排新的课；历史核对报告保留可查。`}
      confirmLabel="确认不再跟进"
      danger
      pending={abandon.isPending}
      onOpenChange={(open) => { if (!open) onClose(); }}
      onConfirm={() => { if (goal) abandon.mutate({ goalId: goal.id }); }}
    />
  );
}
