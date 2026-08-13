import { Menu, MoreHorizontal, PanelLeftClose, PanelLeftOpen, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useNavigate, useOutletContext } from "react-router-dom";

import { type UserResponse } from "@/api/generated/models";
import { authStore } from "@/api/http";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { roleLabel } from "@/lib/labels";

const navigation = [
  { to: "/overview", label: "总览", group: "工作台" },
  { to: "/master-data", label: "主数据", group: "工作台" },
  { to: "/rules", label: "规则工作台", group: "排课" },
  { to: "/solver", label: "排课求解", group: "排课" },
  { to: "/schedule", label: "课表视图", group: "排课" },
  { to: "/diagnostics", label: "无解诊断", group: "排课" },
  { to: "/reschedule", label: "局部调课", group: "变更" },
  { to: "/versions", label: "版本与回滚", group: "变更" },
  { to: "/integrations", label: "飞书集成", group: "集成" },
];

function Nav({ close, collapsed }: { close?: () => void; collapsed?: boolean }) {
  let currentGroup = "";
  return <nav className={cn("flex-1 overflow-y-auto pr-1", collapsed ? "mt-5" : "mt-8")}>
    {navigation.map((item) => {
      const showGroup = item.group !== currentGroup;
      currentGroup = item.group;
      return <div key={item.to}>
        {showGroup ? <div className={cn("px-2.5 text-[11px] font-medium text-zinc-400", collapsed ? "mb-2 mt-7 h-1 px-0 text-center text-[0px]" : "mb-2 mt-7 first:mt-0")}>{collapsed ? "·" : item.group}</div> : null}
        <NavLink title={collapsed ? item.label : undefined} to={item.to} onClick={close} className={({ isActive }) => cn("flex h-8 items-center rounded-md text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-950", collapsed ? "justify-center px-0 text-xs font-semibold" : "px-2.5", isActive && "bg-zinc-100 text-zinc-950")}>
          {collapsed ? item.label.slice(0, 1) : item.label}
        </NavLink>
      </div>;
    })}
  </nav>;
}

export function AppShell() {
  const { user } = useOutletContext<{ user: UserResponse }>();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const navigate = useNavigate();
  const signOut = () => { authStore.clear(); window.dispatchEvent(new Event("tupai:unauthorized")); navigate("/login"); };
  const identity = <div className={cn("relative border-t border-zinc-200 pt-3", sidebarCollapsed ? "flex justify-center" : "flex items-center justify-between")}>
    {!sidebarCollapsed ? <div className="min-w-0"><div className="truncate text-xs font-medium text-zinc-700">{user.username}</div><div className="text-[11px] text-zinc-400">{roleLabel(user.role)}</div></div> : null}
    <Button size="icon" variant="ghost" title="账户菜单" aria-label="账户菜单" onClick={() => setAccountMenuOpen((value) => !value)}><MoreHorizontal className="size-4" /></Button>
    {accountMenuOpen ? <div className="absolute bottom-11 left-0 z-50 w-48 rounded-md border border-zinc-200 bg-white p-1.5 text-sm shadow-lg"><div className="px-2 py-1.5 text-xs text-zinc-500">{user.username} · {roleLabel(user.role)}</div><button className="w-full rounded px-2 py-1.5 text-left hover:bg-zinc-100" onClick={() => setAccountMenuOpen(false)}>账户信息</button><button className="w-full rounded px-2 py-1.5 text-left text-red-600 hover:bg-red-50" onClick={signOut}>退出登录</button></div> : null}
  </div>;
  return <div className="min-h-screen bg-zinc-50">
    <aside className={cn("fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-zinc-200 bg-white px-3 py-5 transition-[width] lg:flex", sidebarCollapsed ? "w-16" : "w-64")}>
      <div className={cn("flex h-8 items-center", sidebarCollapsed ? "justify-center" : "justify-between px-2.5")}>
        <NavLink to="/overview" title="途排智策" className="flex items-center gap-2 text-base font-semibold text-zinc-950"><ShieldCheck className="size-4 text-blue-600" />{sidebarCollapsed ? null : "途排智策"}</NavLink>
        <Button size="icon" variant="ghost" title={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"} aria-label={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"} onClick={() => setSidebarCollapsed((value) => !value)}>{sidebarCollapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}</Button>
      </div>
      <Nav collapsed={sidebarCollapsed} />{identity}
    </aside>
    <header className="sticky top-0 z-20 flex h-12 items-center justify-between border-b border-zinc-200 bg-white px-4 lg:hidden"><Button size="icon" variant="ghost" title="打开导航" onClick={() => setMobileOpen(true)}><Menu className="size-4" /></Button><span className="font-semibold">途排智策</span><span className="w-8" /></header>
    {mobileOpen ? <div className="fixed inset-0 z-40 bg-zinc-950/25 lg:hidden" onClick={() => setMobileOpen(false)}><aside className="h-full w-72 bg-white px-3 py-5 shadow-xl" onClick={(event) => event.stopPropagation()}><div className="flex h-8 items-center px-2.5 font-semibold">途排智策</div><Nav close={() => setMobileOpen(false)} />{identity}</aside></div> : null}
    <div className={cn("min-h-screen transition-[padding]", sidebarCollapsed ? "lg:pl-16" : "lg:pl-64")}><div className="sticky top-0 z-10 hidden h-12 items-center justify-between border-b border-zinc-200 bg-white px-6 lg:flex"><span className="text-xs text-zinc-400">示范校区 / 2026 秋季排课</span><span className="text-xs text-zinc-500">当前角色：{roleLabel(user.role)}</span></div><main className="mx-auto w-full max-w-[1500px] px-4 py-5 sm:px-6 lg:px-8"><Outlet /></main></div>
  </div>;
}
