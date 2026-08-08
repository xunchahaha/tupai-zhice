import { Navigate, createBrowserRouter } from "react-router-dom";

import { AppShell } from "@/app/app-shell";
import { AuthBoundary } from "@/app/auth-boundary";
import { DiagnosticsPage } from "@/pages/diagnostics-page";
import { IntegrationsPage } from "@/pages/integrations-page";
import { LoginPage } from "@/pages/login-page";
import { MasterDataPage } from "@/pages/master-data-page";
import { OverviewPage } from "@/pages/overview-page";
import { ReschedulePage } from "@/pages/reschedule-page";
import { RulesPage } from "@/pages/rules-page";
import { SchedulePage } from "@/pages/schedule-page";
import { SolverPage } from "@/pages/solver-page";
import { VersionsPage } from "@/pages/versions-page";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { element: <AuthBoundary />, children: [{ element: <AppShell />, children: [
    { path: "/overview", element: <OverviewPage /> }, { path: "/master-data", element: <MasterDataPage /> }, { path: "/rules", element: <RulesPage /> }, { path: "/solver", element: <SolverPage /> }, { path: "/schedule", element: <SchedulePage /> }, { path: "/diagnostics", element: <DiagnosticsPage /> }, { path: "/reschedule", element: <ReschedulePage /> }, { path: "/versions", element: <VersionsPage /> }, { path: "/integrations", element: <IntegrationsPage /> }, { path: "*", element: <Navigate to="/overview" replace /> },
  ] }] },
  { path: "*", element: <Navigate to="/overview" replace /> },
]);
