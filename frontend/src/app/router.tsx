import { Navigate, createBrowserRouter, useLocation } from "react-router-dom";

import { AppShell } from "@/app/app-shell";
import { AuthBoundary } from "@/app/auth-boundary";
import { RouteErrorElement } from "@/app/error-boundary";
import { RoleRoute } from "@/app/role-route";
import { AccountsPage } from "@/pages/accounts-page";
import { DiagnosticsPage } from "@/pages/diagnostics-page";
import { GoalsPage } from "@/pages/goals-page";
import { LoginPage } from "@/pages/login-page";
import { MasterDataPage } from "@/pages/master-data-page";
import { MemoryPage } from "@/pages/memory-page";
import { OverviewPage } from "@/pages/overview-page";
import { PublicLinksPage } from "@/pages/public-links-page";
import { PublicSchedulePage } from "@/pages/public/public-schedule-page";
import { ReschedulePage } from "@/pages/reschedule-page";
import { RulesPage } from "@/pages/rules-page";
import { SchedulePage } from "@/pages/schedule-page";
import { SettingsPage } from "@/pages/settings-page";
import { SolverPage } from "@/pages/solver-page";
import { VersionsPage } from "@/pages/versions-page";

// /integrations 是历史路由；带 query（如 solver 页跳转的 ?section=ai）一并重定向到 /settings 保持兼容。
function IntegrationsRedirect() {
  const location = useLocation();
  return <Navigate to={{ pathname: "/settings", search: location.search, hash: location.hash }} replace />;
}

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage />, errorElement: <RouteErrorElement /> },
  // 公开课表 H5：免登录匿名路由，与 /login 平级、AuthBoundary 外（06 §3 B1）。
  { path: "/public/t/:token", element: <PublicSchedulePage />, errorElement: <RouteErrorElement /> },
  {
    element: <AuthBoundary />,
    errorElement: <RouteErrorElement />,
    children: [{ element: <AppShell />, errorElement: <RouteErrorElement />, children: [
      { path: "/overview", element: <OverviewPage /> },
    { path: "/master-data", element: <MasterDataPage /> },
    { path: "/rules", element: <RulesPage /> },
    { path: "/solver", element: <RoleRoute roles={["admin", "scheduler"]}><SolverPage /></RoleRoute> },
    { path: "/schedule", element: <SchedulePage /> },
    { path: "/diagnostics", element: <DiagnosticsPage /> },
    { path: "/reschedule", element: <RoleRoute roles={["admin", "scheduler"]}><ReschedulePage /></RoleRoute> },
    { path: "/memory", element: <RoleRoute roles={["admin", "scheduler"]}><MemoryPage /></RoleRoute> },
    { path: "/goals", element: <RoleRoute roles={["admin", "scheduler"]}><GoalsPage /></RoleRoute> },
    { path: "/versions", element: <VersionsPage /> },
    { path: "/public-links", element: <RoleRoute roles={["admin", "scheduler"]}><PublicLinksPage /></RoleRoute> },
    { path: "/settings", element: <RoleRoute roles={["admin", "scheduler"]}><SettingsPage /></RoleRoute> },
    { path: "/integrations", element: <IntegrationsRedirect /> },
    { path: "/accounts", element: <RoleRoute roles={["admin"]}><AccountsPage /></RoleRoute> },
    { path: "*", element: <Navigate to="/overview" replace /> },
  ] }] },
  { path: "*", element: <Navigate to="/overview" replace /> },
]);
