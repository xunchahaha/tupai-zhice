import { Navigate, createBrowserRouter, type RouteObject } from "react-router-dom";

import { AppShell } from "@/app/app-shell";
import { AuthBoundary } from "@/app/auth-boundary";
import { RouteErrorElement } from "@/app/error-boundary";
import { DiagnosticsRedirect, LegacyRedirect } from "@/app/legacy-redirects";
import { RoleRoute } from "@/app/role-route";
import { ROUTES, assistantPath, schedulePath, settingsPath } from "@/lib/routes";
import { AccountsPage } from "@/pages/accounts-page";
import { AssistantPage } from "@/pages/assistant-page";
import { LoginPage } from "@/pages/login-page";
import { MasterDataPage } from "@/pages/master-data-page";
import { MemoryPage } from "@/pages/memory-page";
import { PublicSchedulePage } from "@/pages/public/public-schedule-page";
import { RulesPage } from "@/pages/rules-page";
import { SchedulePage } from "@/pages/schedule-page";
import { SettingsPage } from "@/pages/settings-page";

const legacyRedirects: RouteObject[] = [
  { path: "/overview", element: <LegacyRedirect to={assistantPath()} /> },
  { path: "/solver", element: <LegacyRedirect to={assistantPath()} /> },
  { path: "/goals", element: <LegacyRedirect to={assistantPath()} /> },
  { path: "/diagnostics", element: <DiagnosticsRedirect /> },
  { path: "/reschedule", element: <LegacyRedirect to={schedulePath({ view: "adjust" })} /> },
  { path: "/versions", element: <LegacyRedirect to={schedulePath({ view: "history" })} /> },
  { path: "/public-links", element: <LegacyRedirect to={schedulePath({ view: "share" })} /> },
  // 历史路由；带 query（如 ?section=ai）一并保留。
  { path: "/integrations", element: <LegacyRedirect to={settingsPath()} /> },
];

export const routeObjects: RouteObject[] = [
  { path: "/login", element: <LoginPage />, errorElement: <RouteErrorElement /> },
  // 公开课表 H5：免登录匿名路由，与 /login 平级、AuthBoundary 外（06 §3 B1）。
  { path: "/public/t/:token", element: <PublicSchedulePage />, errorElement: <RouteErrorElement /> },
  {
    element: <AuthBoundary />,
    errorElement: <RouteErrorElement />,
    children: [{ element: <AppShell />, errorElement: <RouteErrorElement />, children: [
      // 助手/课表/基础资料对所有角色开放：只读成员进得来，页面自己按能力降级。
      { path: ROUTES.assistant, element: <AssistantPage /> },
      { path: ROUTES.schedule, element: <SchedulePage /> },
      { path: ROUTES.masterData, element: <MasterDataPage /> },
      { path: ROUTES.settings, element: <RoleRoute roles={["admin", "scheduler"]}><SettingsPage /></RoleRoute> },
      // 规则页沿用旧口径：所有角色可看（写操作由页面自己按能力限制）。
      { path: ROUTES.rules, element: <RulesPage /> },
      { path: ROUTES.memory, element: <RoleRoute roles={["admin", "scheduler"]}><MemoryPage /></RoleRoute> },
      { path: ROUTES.accounts, element: <RoleRoute roles={["admin"]}><AccountsPage /></RoleRoute> },
      ...legacyRedirects,
      { path: "*", element: <Navigate to={ROUTES.assistant} replace /> },
    ] }],
  },
  { path: "*", element: <Navigate to={ROUTES.assistant} replace /> },
];

export const router = createBrowserRouter(routeObjects);
