/* eslint-disable react-refresh/only-export-components -- 测试辅助文件，不参与热更新 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { type NavigateFunction, MemoryRouter, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { AssistantPage } from "@/pages/assistant-page";

export type TestRole = "admin" | "scheduler" | "approver" | "viewer";

/** 路由探针：断言 URL 同步（goal 写入/删除、action/run 摘除）与跳转目标。 */
function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{location.pathname}{location.search}</div>;
}

/** 测试里模拟浏览器后退 / 侧栏链接：在页面外发起导航（act 包一层）。 */
export const nav = { go: (() => undefined) as NavigateFunction };
function NavigationCapture() {
  nav.go = useNavigate();
  return null;
}

export interface RenderAssistantOptions {
  /** 课表方案的访问角色；传 null 模拟「方案还没加载出来」。默认按 role 推导。 */
  accessRole?: "approver" | "scheduler" | "viewer" | null;
  scheduleSetLoading?: boolean;
  /** 多条历史记录时，从最后一条开始（默认）。 */
  initialIndex?: number;
}

/**
 * 在与真实应用一致的 Outlet 上下文里渲染排课助手：
 * admin=管理员（排课+发布），scheduler=排课员（只能生成草稿），approver=审批人（只能发布），viewer=只读成员。
 */
export function renderAssistant(entry: string | string[] = "/assistant", role: TestRole = "admin", options: RenderAssistantOptions = {}) {
  const derived = role === "admin" ? "approver" : role;
  const accessRole = options.accessRole === null ? undefined : options.accessRole ?? derived;
  const user = { id: `${role}-id`, username: role, role } as never;
  const entries = Array.isArray(entry) ? entry : [entry];
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <MemoryRouter initialEntries={entries} initialIndex={options.initialIndex ?? entries.length - 1}>
        <NavigationCapture />
        <Routes>
          <Route element={<Outlet context={{ user, scheduleAccessRole: accessRole, scheduleSetLoading: options.scheduleSetLoading ?? false }} />}>
            <Route path="/assistant" element={<AssistantPage />} />
            <Route path="*" element={<div>其它页面</div>} />
          </Route>
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
