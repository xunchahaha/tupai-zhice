import { Menu, MoreHorizontal, PanelLeftClose, ShieldCheck } from "lucide-react";
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

function Nav({ close }: { close?: () => void }) {
  let currentGroup = "";
  return <nav className="mt-7 flex-1 overflow-y-auto pr-1">{navigation.map((item) => {
    const showGroup = item.group !== currentGroup;
    currentGroup = item.group;
    return <div key={item.to}>{showGroup ? <div className="mb-1 mt-5 px-2.5 text-[11px] font-medium text-zinc-400 first:mt-0">{item.group}</div> : null}<NavLink to={item.to} onClick={close} className={({ isActive }) => cn("flex h-8 items-center rounded-md px-2.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-950", isActive && "bg-zinc-100 text-zinc-950")}>{item.label}</NavLink></div>;
  })}</nav>;
}

export function AppShell() {
  const { user } = useOutletContext<{ user: UserResponse }>();
  const [mobileOpen, setMobileOpen] = useState(false);
  const navigate = useNavigate();
  const signOut = () => { authStore.clear(); window.dispatchEvent(new Event("tupai:unauthorized")); navigate("/login"); };
  const identity = <div className="flex items-center justify-between border-t border-zinc-200 pt-3"><div className="min-w-0"><div className="truncate text-xs font-medium text-zinc-700">{user.username}</div><div className="text-[11px] text-zinc-400">{roleLabel(user.role)}</div></div><Button size="icon" variant="ghost" title="退出登录" onClick={signOut}><MoreHorizontal className="size-4" /></Button></div>;
  return <div className="min-h-screen bg-zinc-50">
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-zinc-200 bg-white px-3 py-5 lg:flex"><div className="flex h-8 items-center justify-between px-2.5"><NavLink to="/overview" className="flex items-center gap-2 text-base font-semibold text-zinc-950"><ShieldCheck className="size-4 text-blue-600" />途排智策</NavLink><PanelLeftClose className="size-4 text-zinc-400" /></div><Nav />{identity}</aside>
    <header className="sticky top-0 z-20 flex h-12 items-center justify-between border-b border-zinc-200 bg-white px-4 lg:hidden"><Button size="icon" variant="ghost" title="打开导航" onClick={() => setMobileOpen(true)}><Menu className="size-4" /></Button><span className="font-semibold">途排智策</span><span className="w-8" /></header>
    {mobileOpen ? <div className="fixed inset-0 z-40 bg-zinc-950/25 lg:hidden" onClick={() => setMobileOpen(false)}><aside className="h-full w-72 bg-white px-3 py-5 shadow-xl" onClick={(event) => event.stopPropagation()}><div className="flex h-8 items-center px-2.5 font-semibold">途排智策</div><Nav close={() => setMobileOpen(false)} />{identity}</aside></div> : null}
    <div className="min-h-screen lg:pl-64"><div className="sticky top-0 z-10 hidden h-12 items-center justify-between border-b border-zinc-200 bg-white px-6 lg:flex"><span className="text-xs text-zinc-400">示范校区 / 2026 秋季排课</span><span className="text-xs text-zinc-500">当前角色：{roleLabel(user.role)}</span></div><main className="mx-auto w-full max-w-[1500px] px-4 py-5 sm:px-6 lg:px-8"><Outlet /></main></div>
  </div>;
}
