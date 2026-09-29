export interface SetupChecklistState {
  /** course-sessions 总数大于 0 即视为主数据已导入。 */
  masterDataImported: boolean;
  /** status=active 的规则数量。 */
  activeRuleCount: number;
  /** AI 配置探测结果（助手页的双通道探测）；null 表示探测中或探测失败。 */
  aiConfigured: boolean | null;
  /** status=published 的课表版本数量。 */
  publishedScheduleCount: number;
}

/**
 * 还有没做完的准备项吗（助手首页据此决定要不要露出紧凑清单）。
 * AI 还在探测（null）不算未完成，避免清单在探测结果回来前闪一下。
 */
export function hasPendingSetup(state: SetupChecklistState): boolean {
  return !state.masterDataImported || state.activeRuleCount === 0 || state.aiConfigured === false || state.publishedScheduleCount === 0;
}
