import { Menu, MoreHorizontal, PanelLeftClose, PanelLeftOpen, ShieldCheck } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { NavLink, Outlet, useNavigate, useOutletContext } from "react-router-dom";

import { type UserResponse } from "@/api/generated/models";
import { scheduleSetApi, type ScheduleAccessRole, type ScheduleSet } from "@/api/schedule-sets";
import { authStore, scheduleSetStore } from "@/api/http";
import { type AppOutletContext, isReadOnlyMember } from "@/app/user-context";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Select } from "@/components/ui/select";
import { cn } from "@/lib/cn";
import { errorMessage } from "@/lib/format";
import { roleLabel } from "@/lib/labels";
import { toast } from "sonner";

const navigation = [
  { to: "/overview", label: "总览", group: "工作台", memberVisible: true },
  { to: "/master-data", label: "主数据", group: "工作台", memberVisible: true },
  { to: "/rules", label: "规则工作台", group: "排课", memberVisible: true },
  { to: "/solver", label: "排课求解", group: "排课", memberVisible: false, roles: ["admin", "scheduler"] },
  { to: "/schedule", label: "课表视图", group: "排课", memberVisible: true },
  { to: "/diagnostics", label: "无解诊断", group: "排课", memberVisible: true },
  { to: "/reschedule", label: "局部调课", group: "变更", memberVisible: false, roles: ["admin", "scheduler"] },
  { to: "/versions", label: "版本与回滚", group: "变更", memberVisible: true },
  { to: "/integrations", label: "飞书集成", group: "集成", memberVisible: false, roles: ["admin", "scheduler"] },
  { to: "/accounts", label: "账号管理", group: "账号", memberVisible: false, adminOnly: true },
];

function Nav({ user, scheduleAccessRole, close, collapsed }: { user: UserResponse; scheduleAccessRole?: ScheduleAccessRole; close?: () => void; collapsed?: boolean }) {
  const visibleNavigation = navigation.filter((item) => {
    if (item.adminOnly && user.role !== "admin") return false;
    if (item.roles && !item.roles.includes(user.role)) return false;
    return !isReadOnlyMember(user, scheduleAccessRole) || item.memberVisible;
  });

  const groups: { name: string; items: typeof visibleNavigation }[] = [];
  for (const item of visibleNavigation) {
    const lastGroup = groups[groups.length - 1];
    if (lastGroup && lastGroup.name === item.group) {
      lastGroup.items.push(item);
    } else {
      groups.push({ name: item.group, items: [item] });
    }
  }

  return (
    <nav className={cn("flex-1 overflow-y-auto pr-1", collapsed ? "mt-4 space-y-3" : "mt-6 space-y-4")}>
      {groups.map((group, groupIndex) => (
        <div key={group.name} className={groupIndex === 0 ? "" : "pt-1"}>
          {!collapsed ? (
            <div className="px-2.5 pb-1 text-[11px] font-semibold text-zinc-400 tracking-wider">
              {group.name}
            </div>
          ) : (
            groupIndex > 0 && <div className="my-1.5 text-center text-xs text-zinc-300">·</div>
          )}
          <div className="space-y-0.5">
            {group.items.map((item) => (
              <NavLink
                key={item.to}
                title={collapsed ? item.label : undefined}
                to={item.to}
                onClick={close}
                className={({ isActive }) =>
                  cn(
                    "flex h-8 items-center rounded-md text-sm text-zinc-600 transition-all duration-150 hover:bg-zinc-100 hover:text-zinc-950 active:scale-[0.98]",
                    collapsed ? "justify-center px-0 text-xs font-semibold" : "px-2.5",
                    isActive && "bg-zinc-100/90 text-blue-700 font-medium shadow-2xs",
                  )
                }
              >
                {collapsed ? item.label.slice(0, 1) : item.label}
              </NavLink>
            ))}
          </div>
        </div>
      ))}
    </nav>
  );
}

export function AppShell() {
  const { user } = useOutletContext<AppOutletContext>();
  const queryClient = useQueryClient();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [accountDialogOpen, setAccountDialogOpen] = useState(false);
  const [scheduleSets, setScheduleSets] = useState<ScheduleSet[]>([]);
  const [scheduleSetId, setScheduleSetId] = useState<string | null>(scheduleSetStore.get());
  const [scheduleSetsLoading, setScheduleSetsLoading] = useState(true);
  const [scheduleSetsError, setScheduleSetsError] = useState<string | null>(null);
  const [scheduleSetDialog, setScheduleSetDialog] = useState<"create" | "rename" | null>(null);
  const [scheduleSetName, setScheduleSetName] = useState("");
  const [scheduleSetSaving, setScheduleSetSaving] = useState(false);
  const navigate = useNavigate();
  const signOut = () => { authStore.clear(); window.dispatchEvent(new Event("tupai:unauthorized")); navigate("/login"); };

  const refreshScheduleSets = useCallback(async () => {
    setScheduleSetsLoading(true);
    setScheduleSetsError(null);
    try {
      const rawRows = await scheduleSetApi.list();
      const rows = Array.isArray(rawRows) ? rawRows : [];
      setScheduleSets(rows);
      if (!rows.length) {
        scheduleSetStore.clear();
        setScheduleSetId(null);
        return;
      }
      const persisted = scheduleSetStore.get();
      const selected = rows.find((item) => item && item.id === persisted) ?? rows[0];
      if (selected && selected.id !== persisted) {
        scheduleSetStore.set(selected.id);
        // Child pages may have mounted while the first scope was still
        // unresolved (the API returns 409 when multiple sets are available).
        // Refetch them now that a concrete scope is selected.
        void queryClient.invalidateQueries();
      }
      if (selected) {
        setScheduleSetId(selected.id);
      }
    } catch (error) {
      setScheduleSetsError(errorMessage(error));
      // A removed or revoked set can remain in the browser between sessions.
      // Drop it so the next request can resolve the server's default scope.
      if (scheduleSetStore.get()) {
        scheduleSetStore.clear();
        setScheduleSetId(null);
      }
    } finally {
      setScheduleSetsLoading(false);
    }
  }, [queryClient]);

  useEffect(() => {
    void refreshScheduleSets();
  }, [refreshScheduleSets, user.id]);

  useEffect(() => {
    const handleChange = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null;
      setScheduleSetId(typeof detail === "string" ? detail : scheduleSetStore.get());
    };
    window.addEventListener("tupai:schedule-set-changed", handleChange);
    return () => window.removeEventListener("tupai:schedule-set-changed", handleChange);
  }, []);

  const selectScheduleSet = (id: string) => {
    if (id === scheduleSetId) return;
    scheduleSetStore.set(id);
    setScheduleSetId(id);
    // Existing queries do not include the scope in their generated keys. A
    // switch must therefore invalidate the cached data before rendering it.
    void queryClient.invalidateQueries();
  };

  const submitScheduleSetDialog = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = scheduleSetName.trim();
    if (name.length < 2) {
      toast.error("课表名称至少需要 2 个字符");
      return;
    }
    setScheduleSetSaving(true);
    try {
      const result = scheduleSetDialog === "create"
        ? await scheduleSetApi.create(name)
        : scheduleSetId
          ? await scheduleSetApi.rename(scheduleSetId, name)
          : null;
      if (result) {
        await refreshScheduleSets();
        selectScheduleSet(result.id);
        toast.success(scheduleSetDialog === "create" ? "课表方案已创建" : "课表方案名称已更新");
      }
      setScheduleSetDialog(null);
      setScheduleSetName("");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setScheduleSetSaving(false);
    }
  };

  const validScheduleSets = Array.isArray(scheduleSets) ? scheduleSets : [];
  const selectedScheduleSet = validScheduleSets.find((item) => item && item.id === scheduleSetId);
  const identity = <div className={cn("relative border-t border-zinc-200 pt-3", sidebarCollapsed ? "flex justify-center" : "flex items-center justify-between")}>
    {!sidebarCollapsed ? <div className="min-w-0"><div className="truncate text-xs font-medium text-zinc-800">{user.username}</div><div className="text-[11px] text-zinc-400">{roleLabel(user.role)}</div></div> : null}
    <Button size="icon" variant="ghost" title="账户菜单" aria-label="账户菜单" onClick={() => setAccountMenuOpen((value) => !value)}><MoreHorizontal className="size-4" /></Button>
    {accountMenuOpen ? <div className={cn("absolute bottom-11 z-50 w-52 rounded-lg border border-zinc-200 bg-white p-1.5 text-sm shadow-lg animate-slide-up duration-150", sidebarCollapsed ? "left-0" : "right-0")}><div className="border-b border-zinc-100 px-2 py-2"><div className="truncate text-xs font-medium text-zinc-800">{user.username}</div><div className="mt-0.5 text-[11px] text-zinc-400">{roleLabel(user.role)}</div></div><button className="mt-1 w-full rounded px-2 py-1.5 text-left text-zinc-700 hover:bg-zinc-100 transition-colors" onClick={() => { setAccountMenuOpen(false); setAccountDialogOpen(true); }}>账户信息</button><button className="w-full rounded px-2 py-1.5 text-left text-red-600 hover:bg-red-50 transition-colors" onClick={signOut}>退出登录</button></div> : null}
  </div>;
  return <div className="min-h-screen bg-zinc-50/60">
    <aside className={cn("fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-zinc-200 bg-white px-3 py-5 transition-all duration-300 ease-in-out lg:flex shadow-2xs", sidebarCollapsed ? "w-16" : "w-64")}>
      <div className={cn("flex h-8 items-center", sidebarCollapsed ? "justify-center" : "justify-between px-2.5")}>
        <NavLink to="/overview" title="途排智策" className="flex items-center gap-2 text-base font-semibold text-zinc-950 hover:opacity-90 transition-opacity"><ShieldCheck className="size-4 text-blue-600" />{sidebarCollapsed ? null : "途排智策"}</NavLink>
        <Button size="icon" variant="ghost" title={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"} aria-label={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"} onClick={() => { setAccountMenuOpen(false); setSidebarCollapsed((value) => !value); }}>{sidebarCollapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}</Button>
      </div>
      <Nav user={user} scheduleAccessRole={selectedScheduleSet?.access_role} collapsed={sidebarCollapsed} />{identity}
    </aside>
    <header className="sticky top-0 z-20 flex h-12 items-center justify-between border-b border-zinc-200 bg-white/95 backdrop-blur-xs px-4 lg:hidden"><Button size="icon" variant="ghost" title="打开导航" onClick={() => setMobileOpen(true)}><Menu className="size-4" /></Button><div className="flex min-w-0 items-center gap-2"><span className="font-semibold text-zinc-900">途排智策</span>{validScheduleSets.length ? <Select aria-label="当前课表方案" selectSize="sm" containerClassName="w-36" value={scheduleSetId ?? ""} onChange={(event) => selectScheduleSet(event.target.value)} disabled={scheduleSetsLoading}><option value="" disabled>选择课表</option>{validScheduleSets.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</Select> : null}</div><span className="w-8" /></header>
    {mobileOpen ? <div className="fixed inset-0 z-40 bg-zinc-950/25 backdrop-blur-[2px] animate-fade-in lg:hidden" onClick={() => setMobileOpen(false)}><aside className="h-full w-72 bg-white px-3 py-5 shadow-xl animate-fade-in" onClick={(event) => event.stopPropagation()}><div className="flex h-8 items-center px-2.5 font-semibold">途排智策</div><Nav user={user} scheduleAccessRole={selectedScheduleSet?.access_role} close={() => setMobileOpen(false)} />{identity}</aside></div> : null}
    <div className={cn("min-h-screen transition-all duration-300 ease-in-out", sidebarCollapsed ? "lg:pl-16" : "lg:pl-64")}><div className="sticky top-0 z-10 hidden h-12 items-center justify-between border-b border-zinc-200/80 bg-white/95 backdrop-blur-xs px-6 lg:flex"><div className="flex min-w-0 items-center gap-2.5"><span className="text-xs font-medium text-zinc-400">当前课表</span>{validScheduleSets.length ? <Select aria-label="当前课表方案" selectSize="sm" containerClassName="w-56" value={scheduleSetId ?? ""} onChange={(event) => selectScheduleSet(event.target.value)} disabled={scheduleSetsLoading}><option value="" disabled>选择课表</option>{validScheduleSets.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</Select> : <span className="text-xs text-zinc-500">{scheduleSetsLoading ? "加载中…" : scheduleSetsError ? "课表方案加载失败" : "暂无可见课表"}</span>}{user.role === "admin" ? <><Button size="sm" variant="ghost" onClick={() => { setScheduleSetName(""); setScheduleSetDialog("create"); }}>新建</Button><Button size="sm" variant="ghost" disabled={!selectedScheduleSet} onClick={() => { setScheduleSetName(selectedScheduleSet?.name ?? ""); setScheduleSetDialog("rename"); }}>重命名</Button></> : null}</div><span className="text-xs text-zinc-500">当前角色：{roleLabel(user.role)}{selectedScheduleSet ? ` · 本课表${selectedScheduleSet.access_role === "viewer" ? "只读" : selectedScheduleSet.access_role === "scheduler" ? "排课" : "审批"}` : ""}</span></div><main className="mx-auto w-full max-w-[1500px] px-4 py-5 sm:px-6 lg:px-8"><Outlet context={{ user, scheduleAccessRole: selectedScheduleSet?.access_role, scheduleSet: selectedScheduleSet }} /></main></div>
    <Dialog open={scheduleSetDialog !== null} onOpenChange={(open) => { if (!scheduleSetSaving && !open) setScheduleSetDialog(null); }}><DialogContent className="max-w-md"><DialogTitle className="text-base font-semibold">{scheduleSetDialog === "create" ? "新建课表方案" : "重命名课表方案"}</DialogTitle><DialogDescription className="mt-1 text-sm text-zinc-500">每套课表方案拥有独立的规则、求解、版本和飞书同步目标；基础主数据可复用。</DialogDescription><form className="mt-5 space-y-4" onSubmit={submitScheduleSetDialog}><label className="block text-sm text-zinc-700">课表名称<input autoFocus className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2.5 text-sm outline-none transition-all duration-150 focus:border-blue-600 focus:ring-2 focus:ring-blue-500/20" value={scheduleSetName} onChange={(event) => setScheduleSetName(event.target.value)} placeholder="例如：郑州校区师范课表" /></label><div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={() => setScheduleSetDialog(null)} disabled={scheduleSetSaving}>取消</Button><Button type="submit" disabled={scheduleSetSaving}>{scheduleSetSaving ? "保存中" : "保存"}</Button></div></form></DialogContent></Dialog>
    <Dialog open={accountDialogOpen} onOpenChange={setAccountDialogOpen}><DialogContent><DialogTitle className="text-base font-semibold">账户信息</DialogTitle><DialogDescription className="mt-1 text-sm text-zinc-500">当前登录账户与权限身份。</DialogDescription><div className="mt-5 divide-y divide-zinc-100 border-y border-zinc-200 text-sm"><AccountField label="用户名" value={user.username} /><AccountField label="角色" value={roleLabel(user.role)} /><AccountField label="账户 ID" value={user.id} mono /></div><div className="mt-5 flex justify-end"><Button variant="outline" onClick={() => setAccountDialogOpen(false)}>关闭</Button></div></DialogContent></Dialog>
  </div>;
}

function AccountField({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="grid grid-cols-[96px_1fr] gap-3 py-3"><span className="text-zinc-400">{label}</span><span className={cn("break-all text-zinc-700", mono && "font-mono text-xs")}>{value}</span></div>;
}
