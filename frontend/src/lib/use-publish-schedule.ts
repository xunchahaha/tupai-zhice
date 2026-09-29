import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey,
  getListSchedulesApiV1SchedulesGetQueryKey,
  usePublishScheduleApiV1SchedulesScheduleIdPublishPost,
} from "@/api/generated/client";
import { errorMessage } from "@/lib/format";

/**
 * 发布草稿为当前课表：助手结果卡与课表「历史版本」共用同一份提交与副作用，
 * 避免两个入口各自维护提示与缓存失效。发布始终是一个独立、明确的操作，
 * 调用方负责在点击前给出确认，不要把「生成 / 发布 / 下发」合并成一步。
 */
export function usePublishSchedule(options: { onPublished?: () => void } = {}) {
  const client = useQueryClient();
  return usePublishScheduleApiV1SchedulesScheduleIdPublishPost({
    mutation: {
      onSuccess: () => {
        toast.success("版本已发布为当前课表，已触发发布数据同步");
        void client.invalidateQueries({ queryKey: getListSchedulesApiV1SchedulesGetQueryKey() });
        void client.invalidateQueries({ queryKey: getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey() });
        options.onPublished?.();
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}
