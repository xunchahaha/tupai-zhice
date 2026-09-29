/**
 * 前端信息架构的路由契约（单一事实源）。
 *
 * 左侧只有三个业务入口 + 底部设置：
 *   排课助手 /assistant · 课表 /schedule · 基础资料 /master-data · 设置 /settings
 * 其余能力（目标、诊断、调课、版本、公开链接、规则、记忆、账号）不再是独立入口，
 * 而是挂在对应业务入口内的就近操作；旧地址由 router 重定向到新入口（保留 query）。
 * 各页面互相跳转一律经这里的构造函数，避免路径与参数名散落各处。
 */

export const ROUTES = {
  assistant: "/assistant",
  schedule: "/schedule",
  masterData: "/master-data",
  settings: "/settings",
  /** 以下三个是「设置」下的完整管理页，只从设置/助手内的「管理常用要求」进入，不进左侧导航。 */
  rules: "/rules",
  memory: "/memory",
  accounts: "/accounts",
} as const;

/** 课表页的内部视图：课表本体 / 调整 / 历史版本 / 分享与订阅。 */
export const SCHEDULE_VIEWS = ["schedule", "adjust", "history", "share"] as const;
export type ScheduleView = (typeof SCHEDULE_VIEWS)[number];

export const SCHEDULE_VIEW_LABELS: Record<ScheduleView, string> = {
  schedule: "课表",
  adjust: "调整",
  history: "历史版本",
  share: "分享与订阅",
};

export function parseScheduleView(value: string | null | undefined): ScheduleView {
  return (SCHEDULE_VIEWS as readonly string[]).includes(value ?? "") ? (value as ScheduleView) : "schedule";
}

function withQuery(pathname: string, params: Record<string, string | undefined | null | false>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === false || value === "") continue;
    search.set(key, value);
  }
  const text = search.toString();
  return text ? `${pathname}?${text}` : pathname;
}

export interface AssistantPathParams {
  /** 续办一个持久目标（原「目标跟踪」详情/「排课求解?goal=」）。 */
  goal?: string;
  /** 打开某次求解的结果与诊断（原「无解诊断」按任务查看）。 */
  run?: string;
  /** 续办时的一键补救动作（07 §5.2）。 */
  action?: "raise_budget" | "resolve_scope";
  /** 预填需求输入框（不自动提交），例如课表里「交给助手继续处理」。 */
  prompt?: string;
  /** 直接展开「手动排课」参数面板。 */
  manual?: boolean;
}

export function assistantPath(params: AssistantPathParams = {}): string {
  return withQuery(ROUTES.assistant, {
    goal: params.goal,
    run: params.run,
    action: params.action,
    prompt: params.prompt,
    manual: params.manual ? "1" : undefined,
  });
}

export interface SchedulePathParams {
  view?: ScheduleView;
  /** 预选某个课表版本（例如助手结果卡「查看课表草稿」）。 */
  version?: string;
  /** 选中的课次业务号（例如「调整」视图预填该课）。 */
  lesson?: string;
}

export function schedulePath(params: SchedulePathParams = {}): string {
  return withQuery(ROUTES.schedule, {
    view: params.view && params.view !== "schedule" ? params.view : undefined,
    version: params.version,
    lesson: params.lesson,
  });
}

/** `section` 沿用设置页既有的 ai / aily 锚点。 */
export function settingsPath(section?: "ai" | "aily"): string {
  return withQuery(ROUTES.settings, { section });
}

/**
 * 旧地址 → 新业务入口。router 用它做重定向（保留原 query），
 * 侧栏用它判断「当前页归属哪个入口」以便高亮。
 */
export const NAV_OWNER: ReadonlyArray<{ match: (pathname: string) => boolean; owner: keyof Pick<typeof ROUTES, "assistant" | "schedule" | "masterData" | "settings"> }> = [
  { match: (p) => p === ROUTES.assistant, owner: "assistant" },
  { match: (p) => p === ROUTES.schedule, owner: "schedule" },
  { match: (p) => p === ROUTES.masterData, owner: "masterData" },
  { match: (p) => p === ROUTES.settings || p === ROUTES.rules || p === ROUTES.memory || p === ROUTES.accounts, owner: "settings" },
];
