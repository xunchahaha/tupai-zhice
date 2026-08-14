import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, RefreshCw, ShieldCheck, UserCheck, UserMinus, Users } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { http } from "@/api/http";
import { useAppUser } from "@/app/user-context";
import { datetime, errorMessage } from "@/lib/format";
import { roleLabel } from "@/lib/labels";

type AccountRole = "admin" | "scheduler" | "approver" | "viewer";

interface AccountRecord {
  id: string;
  username: string;
  role: AccountRole;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
  created_by: string | null;
}

interface CreateAccountDraft {
  username: string;
  password: string;
}

interface ResetTarget {
  id: string;
  username: string;
}

const queryKey = ["accounts"];
const inputClass = "mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2.5 text-sm outline-none focus:border-blue-500";

export function AccountsPage() {
  const currentUser = useAppUser();
  const client = useQueryClient();
  const accounts = useQuery({
    queryKey,
    queryFn: async () => (await http.get<AccountRecord[]>("/api/v1/users")).data,
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [createDraft, setCreateDraft] = useState<CreateAccountDraft>({ username: "", password: "" });
  const [resetTarget, setResetTarget] = useState<ResetTarget | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [statusTarget, setStatusTarget] = useState<AccountRecord | null>(null);

  const invalidate = () => void client.invalidateQueries({ queryKey });
  const create = useMutation({
    mutationFn: async (payload: CreateAccountDraft) => (await http.post<AccountRecord>("/api/v1/users", payload)).data,
    onSuccess: () => {
      setCreateOpen(false);
      setCreateDraft({ username: "", password: "" });
      invalidate();
      toast.success("成员账号已创建");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const updateStatus = useMutation({
    mutationFn: async ({ id, is_active }: { id: string; is_active: boolean }) => (await http.patch<AccountRecord>(`/api/v1/users/${id}/status`, { is_active })).data,
    onSuccess: (account) => {
      setStatusTarget(null);
      invalidate();
      toast.success(account.is_active ? "账号已启用" : "账号已停用");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const reset = useMutation({
    mutationFn: async ({ id, password }: { id: string; password: string }) => http.post(`/api/v1/users/${id}/reset-password`, { password }),
    onSuccess: () => {
      setResetTarget(null);
      setResetPassword("");
      toast.success("密码已重置");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  if (accounts.isPending) return <LoadingState />;
  if (accounts.isError) return <ErrorState error={accounts.error} retry={() => void accounts.refetch()} />;
  const rows = accounts.data ?? [];
  return <div className="space-y-5">
    <PageHeader title="账号管理" actions={<><Button variant="outline" size="sm" onClick={() => void accounts.refetch()} disabled={accounts.isFetching}><RefreshCw className={accounts.isFetching ? "size-3.5 animate-spin" : "size-3.5"} />刷新</Button><Button size="sm" onClick={() => setCreateOpen(true)}><Plus className="size-3.5" />新增成员</Button></>}>
      <p className="mt-1 text-sm text-zinc-500">管理员可创建和维护成员账号；成员仅能查看业务数据。</p>
    </PageHeader>
    <section className="grid gap-2 sm:grid-cols-3">
      <Summary icon={<Users className="size-4" />} label="账号总数" value={String(rows.length)} />
      <Summary icon={<UserCheck className="size-4" />} label="启用中" value={String(rows.filter((item) => item.is_active).length)} />
      <Summary icon={<UserMinus className="size-4" />} label="已停用" value={String(rows.filter((item) => !item.is_active).length)} />
    </section>
    <section className="border border-zinc-200 bg-white">
      <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3"><div className="flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="size-4 text-blue-600" />账号列表</div><span className="text-xs text-zinc-400">{rows.length} 个账号</span></div>
      <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-sm"><thead className="bg-zinc-50 text-xs text-zinc-500"><tr><th className="h-10 px-4 font-medium">用户名</th><th className="px-4 font-medium">角色</th><th className="px-4 font-medium">状态</th><th className="px-4 font-medium">创建时间</th><th className="px-4 font-medium">最近登录</th><th className="px-4 text-right font-medium">操作</th></tr></thead><tbody>{rows.map((account) => {
        const self = account.id === currentUser.id;
        return <tr key={account.id} className="border-t border-zinc-100"><td className="px-4 py-3"><div className="font-medium text-zinc-800">{account.username}{self ? <span className="ml-2 text-[11px] text-blue-600">当前账号</span> : null}</div><div className="mt-0.5 font-mono text-[11px] text-zinc-400">{account.id.slice(0, 8)}</div></td><td className="px-4 py-3"><Badge tone={account.role === "admin" ? "blue" : "neutral"}>{roleLabel(account.role)}</Badge></td><td className="px-4 py-3"><Badge tone={account.is_active ? "green" : "red"}>{account.is_active ? "启用中" : "已停用"}</Badge></td><td className="px-4 py-3 text-zinc-500">{datetime(account.created_at)}</td><td className="px-4 py-3 text-zinc-500">{datetime(account.last_login_at)}</td><td className="px-4 py-3"><div className="flex justify-end gap-1.5">{!self && account.is_active ? <Button size="sm" variant="ghost" className="text-red-600 hover:bg-red-50 hover:text-red-700" onClick={() => setStatusTarget(account)}><UserMinus className="size-3.5" />停用</Button> : null}{!self && !account.is_active ? <Button size="sm" variant="outline" onClick={() => updateStatus.mutate({ id: account.id, is_active: true })} disabled={updateStatus.isPending}><UserCheck className="size-3.5" />启用</Button> : null}{!self ? <Button size="sm" variant="ghost" onClick={() => { setResetPassword(""); setResetTarget({ id: account.id, username: account.username }); }}><KeyRound className="size-3.5" />重置密码</Button> : null}{self ? <span className="px-2 text-xs text-zinc-400">管理员账号</span> : null}</div></td></tr>;
      })}</tbody></table></div>
      {!rows.length ? <div className="grid min-h-40 place-items-center text-sm text-zinc-400">暂无账号</div> : null}
    </section>
    <Dialog open={createOpen} onOpenChange={(open) => { if (!create.isPending) setCreateOpen(open); }}><DialogContent className="max-w-md"><DialogTitle className="text-base font-semibold">新增成员账号</DialogTitle><DialogDescription className="mt-1 text-sm text-zinc-500">新账号默认拥有成员权限，只能查看排课数据。</DialogDescription><form className="mt-5 space-y-4" onSubmit={(event) => { event.preventDefault(); if (createDraft.username.trim().length < 3) { toast.error("用户名至少需要 3 个字符"); return; } if (createDraft.password.length < 8) { toast.error("初始密码至少需要 8 个字符"); return; } create.mutate({ username: createDraft.username.trim(), password: createDraft.password }); }}><label className="block text-sm text-zinc-700">用户名<input className={inputClass} autoComplete="off" value={createDraft.username} onChange={(event) => setCreateDraft((draft) => ({ ...draft, username: event.target.value }))} placeholder="例如：campus_member" /></label><label className="block text-sm text-zinc-700">初始密码<input className={inputClass} type="password" autoComplete="new-password" value={createDraft.password} onChange={(event) => setCreateDraft((draft) => ({ ...draft, password: event.target.value }))} placeholder="至少 8 个字符" /></label><div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={() => setCreateOpen(false)} disabled={create.isPending}>取消</Button><Button type="submit" disabled={create.isPending}>{create.isPending ? "创建中" : "创建成员"}</Button></div></form></DialogContent></Dialog>
    <Dialog open={Boolean(resetTarget)} onOpenChange={(open) => { if (!reset.isPending && !open) setResetTarget(null); }}><DialogContent className="max-w-md"><DialogTitle className="text-base font-semibold">重置成员密码</DialogTitle><DialogDescription className="mt-1 text-sm text-zinc-500">将为“{resetTarget?.username ?? ""}”设置新的登录密码，原密码会立即失效。</DialogDescription><form className="mt-5 space-y-4" onSubmit={(event) => { event.preventDefault(); if (!resetTarget) return; if (resetPassword.length < 8) { toast.error("新密码至少需要 8 个字符"); return; } reset.mutate({ id: resetTarget.id, password: resetPassword }); }}><label className="block text-sm text-zinc-700">新密码<input className={inputClass} type="password" autoComplete="new-password" value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} placeholder="至少 8 个字符" /></label><div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={() => setResetTarget(null)} disabled={reset.isPending}>取消</Button><Button type="submit" disabled={reset.isPending}>{reset.isPending ? "重置中" : "确认重置"}</Button></div></form></DialogContent></Dialog>
    <ConfirmDialog open={Boolean(statusTarget)} onOpenChange={(open) => { if (!open) setStatusTarget(null); }} title="停用成员账号" description={statusTarget ? `停用“${statusTarget.username}”后，该账号将立即退出当前会话，且无法再次登录，直到管理员重新启用。` : ""} confirmLabel="确认停用" danger pending={updateStatus.isPending} onConfirm={() => { if (statusTarget) updateStatus.mutate({ id: statusTarget.id, is_active: false }); }} />
  </div>;
}

function Summary({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return <div className="border border-zinc-200 bg-white p-4"><div className="flex items-center justify-between text-zinc-500"><span className="text-sm">{label}</span>{icon}</div><div className="mt-3 text-2xl font-semibold tabular-nums">{value}</div></div>;
}
