import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, RefreshCw, ShieldCheck, UserCheck, UserMinus, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  getListUsersApiV1UsersGetQueryKey,
  useCreateUserApiV1UsersPost,
  useListUsersApiV1UsersGet,
  useResetUserPasswordApiV1UsersUserIdResetPasswordPost,
  useUpdateUserRoleApiV1UsersUserIdRolePatch,
  useUpdateUserStatusApiV1UsersUserIdStatusPatch,
} from "@/api/generated/client";
import type { UserResponse, UserResponseRole } from "@/api/generated/models";
import {
  scheduleSetApi,
  type ScheduleAccessRole,
  type ScheduleSet,
  type ScheduleSetMember,
} from "@/api/schedule-sets";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Select } from "@/components/ui/select";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { useAppUser } from "@/app/user-context";
import { datetime, errorMessage } from "@/lib/format";
import { roleLabel } from "@/lib/labels";

const ROLES: Array<{ value: UserResponseRole; hint: string }> = [
  { value: "admin", hint: "全部权限，含账号与集成配置" },
  { value: "scheduler", hint: "在获授权课表中录入规则、发起求解和局部调课" },
  { value: "approver", hint: "在获审批权限的课表中发布、回滚与删除版本" },
  { value: "viewer", hint: "只读查看业务数据" },
];

const inputClass =
  "mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2.5 text-sm outline-none focus:border-blue-500";

const ACCESS_OPTIONS: Array<{ value: "none" | ScheduleAccessRole; label: string }> = [
  { value: "none", label: "无权限" },
  { value: "viewer", label: "只读" },
  { value: "scheduler", label: "排课" },
  { value: "approver", label: "审批" },
];

function accessAllowed(role: UserResponseRole, access: "none" | ScheduleAccessRole): boolean {
  if (access === "none") return true;
  if (role === "admin") return true;
  if (role === "scheduler") return access === "viewer" || access === "scheduler";
  if (role === "approver") return access === "viewer" || access === "approver";
  return access === "viewer";
}

function accessLabel(access: ScheduleAccessRole): string {
  return { viewer: "只读", scheduler: "排课", approver: "审批" }[access];
}

function accessTone(access: ScheduleAccessRole): "neutral" | "blue" | "green" {
  return access === "viewer" ? "neutral" : access === "scheduler" ? "blue" : "green";
}

function ScheduleAccessSummary({
  account,
  scheduleSets,
  membersBySet,
  loading,
  error,
}: {
  account: UserResponse;
  scheduleSets: ScheduleSet[];
  membersBySet: Record<string, ScheduleSetMember[]>;
  loading: boolean;
  error: string | null;
}) {
  if (account.role === "admin") {
    return <span className="text-xs text-blue-700">全部课表（管理员）</span>;
  }
  if (loading) return <span className="text-xs text-zinc-400">读取中…</span>;
  if (error) return <span className="text-xs text-red-600">读取失败</span>;
  const grants = scheduleSets.flatMap((scheduleSet) => {
    const member = membersBySet[scheduleSet.id]?.find(
      (item) => item.user_id === account.id && item.is_active,
    );
    return member ? [{ scheduleSet, access: member.access_role }] : [];
  });
  if (!grants.length) return <span className="text-xs text-zinc-400">未分配课表</span>;
  return (
    <div className="flex max-w-72 flex-wrap gap-1">
      {grants.slice(0, 2).map(({ scheduleSet, access }) => (
        <Badge key={scheduleSet.id} tone={accessTone(access)}>{scheduleSet.name} · {accessLabel(access)}</Badge>
      ))}
      {grants.length > 2 ? <Badge tone="neutral">+{grants.length - 2}</Badge> : null}
    </div>
  );
}

export function AccountsPage() {
  const currentUser = useAppUser();
  const client = useQueryClient();
  const accounts = useListUsersApiV1UsersGet();
  const [createOpen, setCreateOpen] = useState(false);
  const [draft, setDraft] = useState<{ username: string; password: string; role: UserResponseRole }>(
    { username: "", password: "", role: "viewer" },
  );
  const [resetTarget, setResetTarget] = useState<UserResponse | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [statusTarget, setStatusTarget] = useState<UserResponse | null>(null);
  const [scheduleSets, setScheduleSets] = useState<ScheduleSet[]>([]);
  const [membersBySet, setMembersBySet] = useState<Record<string, ScheduleSetMember[]>>({});
  const [scheduleSetsLoading, setScheduleSetsLoading] = useState(true);
  const [scheduleSetsError, setScheduleSetsError] = useState<string | null>(null);
  const [memberSavingKey, setMemberSavingKey] = useState<string | null>(null);
  const [accessTarget, setAccessTarget] = useState<UserResponse | null>(null);

  const invalidate = () =>
    void client.invalidateQueries({ queryKey: getListUsersApiV1UsersGetQueryKey() });
  const onError = (error: unknown) => toast.error(errorMessage(error));

  const refreshScheduleAccess = async () => {
    setScheduleSetsLoading(true);
    setScheduleSetsError(null);
    try {
      const sets = await scheduleSetApi.list();
      const memberEntries = await Promise.all(
        sets.map(async (item) => [item.id, await scheduleSetApi.listMembers(item.id)] as const),
      );
      setScheduleSets(sets);
      setMembersBySet(Object.fromEntries(memberEntries));
    } catch (error) {
      setScheduleSetsError(errorMessage(error));
    } finally {
      setScheduleSetsLoading(false);
    }
  };

  useEffect(() => {
    void refreshScheduleAccess();
  }, []);

  const updateScheduleAccess = async (
    account: UserResponse,
    scheduleSet: ScheduleSet,
    value: "none" | ScheduleAccessRole,
  ) => {
    const key = `${scheduleSet.id}:${account.id}`;
    setMemberSavingKey(key);
    try {
      if (value === "none") {
        await scheduleSetApi.revokeMember(scheduleSet.id, account.id);
      } else {
        await scheduleSetApi.setMember(scheduleSet.id, account.id, value);
      }
      const members = await scheduleSetApi.listMembers(scheduleSet.id);
      setMembersBySet((current) => ({ ...current, [scheduleSet.id]: members }));
      toast.success(`已更新 ${account.username} 的「${scheduleSet.name}」权限`);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setMemberSavingKey(null);
    }
  };

  const create = useCreateUserApiV1UsersPost({
    mutation: {
      onSuccess: () => {
        setCreateOpen(false);
        setDraft({ username: "", password: "", role: "viewer" });
        invalidate();
        void refreshScheduleAccess();
        toast.success("成员账号已创建");
      },
      onError,
    },
  });
  const updateStatus = useUpdateUserStatusApiV1UsersUserIdStatusPatch({
    mutation: {
      onSuccess: (account) => {
        setStatusTarget(null);
        invalidate();
        toast.success(account.is_active ? "账号已启用" : "账号已停用");
      },
      onError,
    },
  });
  const updateRole = useUpdateUserRoleApiV1UsersUserIdRolePatch({
    mutation: {
      onSuccess: (account) => {
        invalidate();
        toast.success(`已将 ${account.username} 设为${roleLabel(account.role)}`);
      },
      onError,
    },
  });
  const reset = useResetUserPasswordApiV1UsersUserIdResetPasswordPost({
    mutation: {
      onSuccess: () => {
        setResetTarget(null);
        setResetPassword("");
        toast.success("密码已重置，该账号的旧登录状态立即失效");
      },
      onError,
    },
  });

  if (accounts.isPending) return <LoadingState />;
  if (accounts.isError) {
    return <ErrorState error={accounts.error} retry={() => void accounts.refetch()} />;
  }
  const rows = accounts.data ?? [];

  return (
    <div className="space-y-5 animate-fade-in">
      <PageHeader
        title="账号管理"
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void accounts.refetch()}
              disabled={accounts.isFetching}
            >
              <RefreshCw className={accounts.isFetching ? "size-3.5 animate-spin" : "size-3.5"} />
              刷新
            </Button>
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <Plus className="size-3.5" />
              新增成员
            </Button>
          </>
        }
      >
        <p className="mt-1 text-sm text-zinc-500">
          管理员可创建成员并分配角色。角色决定后端权限，不只是界面显示。
        </p>
      </PageHeader>

      <section className="grid gap-2 sm:grid-cols-3">
        <Summary icon={<Users className="size-4" />} label="账号总数" value={String(rows.length)} />
        <Summary
          icon={<UserCheck className="size-4" />}
          label="启用中"
          value={String(rows.filter((item) => item.is_active).length)}
        />
        <Summary
          icon={<UserMinus className="size-4" />}
          label="已停用"
          value={String(rows.filter((item) => !item.is_active).length)}
        />
      </section>

      <section className="border border-zinc-200 bg-white">
        <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <ShieldCheck className="size-4 text-blue-600" />
            账号列表
          </div>
          <span className="text-xs text-zinc-400">{rows.length} 个账号</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1040px] text-left text-sm">
            <thead className="bg-zinc-50 text-xs text-zinc-500">
              <tr>
                <th className="h-10 px-4 font-medium">用户名</th>
                <th className="px-4 font-medium">角色</th>
                <th className="px-4 font-medium">状态</th>
                <th className="px-4 font-medium">可访问课表</th>
                <th className="px-4 font-medium">创建时间</th>
                <th className="px-4 font-medium">最近登录</th>
                <th className="px-4 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((account) => {
                const self = account.id === currentUser.id;
                return (
                  <tr key={account.id} className="border-t border-zinc-100">
                    <td className="px-4 py-3">
                      <div className="font-medium text-zinc-800">
                        {account.username}
                        {self ? <span className="ml-2 text-[11px] text-blue-600">当前账号</span> : null}
                      </div>
                      <div className="mt-0.5 font-mono text-[11px] text-zinc-400">
                        {account.id.slice(0, 8)}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      {self ? (
                        <Badge tone="blue">{roleLabel(account.role)}</Badge>
                      ) : (
                        <Select aria-label={`${account.username} 的角色`} selectSize="sm" containerClassName="w-32" value={account.role} disabled={updateRole.isPending} onChange={(event) =>
                            updateRole.mutate({
                              userId: account.id,
                              data: { role: event.target.value as UserResponseRole },
                            })
                          }
                        >
                          {ROLES.map((role) => (
                            <option key={role.value} value={role.value}>
                              {roleLabel(role.value)}
                            </option>
                          ))}
                        </Select>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={account.is_active ? "green" : "red"}>
                        {account.is_active ? "启用中" : "已停用"}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      <ScheduleAccessSummary
                        account={account}
                        scheduleSets={scheduleSets}
                        membersBySet={membersBySet}
                        loading={scheduleSetsLoading}
                        error={scheduleSetsError}
                      />
                    </td>
                    <td className="px-4 py-3 text-zinc-500">{datetime(account.created_at)}</td>
                    <td className="px-4 py-3 text-zinc-500">{datetime(account.last_login_at)}</td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-1.5">
                        {account.role !== "admin" ? (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setAccessTarget(account);
                              if (scheduleSetsError) void refreshScheduleAccess();
                            }}
                            disabled={!account.is_active}
                          >
                            课表权限
                          </Button>
                        ) : null}
                        {!self && account.is_active ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-red-600 hover:bg-red-50 hover:text-red-700"
                            onClick={() => setStatusTarget(account)}
                          >
                            <UserMinus className="size-3.5" />
                            停用
                          </Button>
                        ) : null}
                        {!self && !account.is_active ? (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() =>
                              updateStatus.mutate({ userId: account.id, data: { is_active: true } })
                            }
                            disabled={updateStatus.isPending}
                          >
                            <UserCheck className="size-3.5" />
                            启用
                          </Button>
                        ) : null}
                        {!self ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              setResetPassword("");
                              setResetTarget(account);
                            }}
                          >
                            <KeyRound className="size-3.5" />
                            重置密码
                          </Button>
                        ) : (
                          <span className="px-2 text-xs text-zinc-400">在账户信息里修改自己的密码</span>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {!rows.length ? (
          <div className="grid min-h-40 place-items-center text-sm text-zinc-400">暂无账号</div>
        ) : null}
      </section>

      <Dialog
        open={Boolean(accessTarget)}
        onOpenChange={(open) => {
          if (!open && !memberSavingKey) setAccessTarget(null);
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogTitle className="text-base font-semibold">课表访问权限</DialogTitle>
          <DialogDescription className="mt-1 text-sm text-zinc-500">
            为“{accessTarget?.username ?? ""}”配置可见课表。全局角色决定该成员可被授予的最高课表权限。
          </DialogDescription>
          {accessTarget ? (
            <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
              <span>全局角色</span>
              <Badge tone="blue">{roleLabel(accessTarget.role)}</Badge>
              <span>权限：只读可查看；排课可维护数据并发起求解；审批可发布与回滚。</span>
            </div>
          ) : null}
          {scheduleSetsLoading ? (
            <div className="grid min-h-36 place-items-center text-sm text-zinc-400">正在读取课表方案…</div>
          ) : scheduleSetsError ? (
            <div className="mt-5 flex min-h-32 items-center justify-center gap-3 text-sm text-red-600">
              <span>{scheduleSetsError}</span>
              <Button size="sm" variant="outline" onClick={() => void refreshScheduleAccess()}>重试</Button>
            </div>
          ) : !scheduleSets.length ? (
            <div className="grid min-h-32 place-items-center text-sm text-zinc-400">请先在顶栏新建课表方案</div>
          ) : (
            <div className="mt-5 divide-y divide-zinc-100 border-y border-zinc-200">
              {scheduleSets.map((scheduleSet) => {
                const member = membersBySet[scheduleSet.id]?.find(
                  (item) => item.user_id === accessTarget?.id && item.is_active,
                );
                const value: "none" | ScheduleAccessRole = member?.access_role ?? "none";
                const saving = memberSavingKey === `${scheduleSet.id}:${accessTarget?.id ?? ""}`;
                return (
                  <div key={scheduleSet.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-zinc-800">{scheduleSet.name}</div>
                      <div className="mt-0.5 font-mono text-[11px] text-zinc-400">{scheduleSet.code}</div>
                    </div>
                    <Select aria-label={`${accessTarget?.username ?? "成员"} 在 ${scheduleSet.name} 的课表权限`} selectSize="sm" containerClassName="w-full sm:w-32" value={value} disabled={saving || !accessTarget?.is_active} onChange={(event) => {
                        if (!accessTarget) return;
                        const next = event.target.value as "none" | ScheduleAccessRole;
                        if (accessAllowed(accessTarget.role, next)) {
                          void updateScheduleAccess(accessTarget, scheduleSet, next);
                        } else {
                          toast.error("课表权限不能高于成员的全局角色");
                        }
                      }}
                    >
                      {ACCESS_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value} disabled={!accessAllowed(accessTarget?.role ?? "viewer", option.value)}>
                          {option.label}
                        </option>
                      ))}
                    </Select>
                  </div>
                );
              })}
            </div>
          )}
          <div className="mt-5 flex justify-end">
            <Button variant="outline" onClick={() => setAccessTarget(null)} disabled={Boolean(memberSavingKey)}>关闭</Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          if (!create.isPending) setCreateOpen(open);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogTitle className="text-base font-semibold">新增成员账号</DialogTitle>
          <DialogDescription className="mt-1 text-sm text-zinc-500">
            角色可以随时调整，但至少要保留一个启用中的管理员。
          </DialogDescription>
          <form
            className="mt-5 space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (draft.username.trim().length < 3) {
                toast.error("用户名至少需要 3 个字符");
                return;
              }
              if (draft.password.length < 8) {
                toast.error("初始密码至少需要 8 个字符");
                return;
              }
              create.mutate({ data: { ...draft, username: draft.username.trim() } });
            }}
          >
            <label className="block text-sm text-zinc-700">
              用户名
              <input
                className={inputClass}
                autoComplete="off"
                value={draft.username}
                onChange={(event) => setDraft((current) => ({ ...current, username: event.target.value }))}
                placeholder="例如：campus_member"
              />
            </label>
            <label className="block text-sm text-zinc-700">
              初始密码
              <input
                className={inputClass}
                type="password"
                autoComplete="new-password"
                value={draft.password}
                onChange={(event) => setDraft((current) => ({ ...current, password: event.target.value }))}
                placeholder="至少 8 个字符"
              />
            </label>
            <label className="block text-sm text-zinc-700">
              角色
              <Select selectSize="md" containerClassName="mt-1.5" value={draft.role} onChange={(event) =>
                  setDraft((current) => ({ ...current, role: event.target.value as UserResponseRole }))
                }
              >
                {ROLES.map((role) => (
                  <option key={role.value} value={role.value}>
                    {roleLabel(role.value)} · {role.hint}
                  </option>
                ))}
              </Select>
            </label>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setCreateOpen(false)} disabled={create.isPending}>
                取消
              </Button>
              <Button type="submit" disabled={create.isPending}>
                {create.isPending ? "创建中" : "创建成员"}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(resetTarget)}
        onOpenChange={(open) => {
          if (!reset.isPending && !open) setResetTarget(null);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogTitle className="text-base font-semibold">重置成员密码</DialogTitle>
          <DialogDescription className="mt-1 text-sm text-zinc-500">
            将为“{resetTarget?.username ?? ""}”设置新密码。该账号已登录的会话会立即失效。
          </DialogDescription>
          <form
            className="mt-5 space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (!resetTarget) return;
              if (resetPassword.length < 8) {
                toast.error("新密码至少需要 8 个字符");
                return;
              }
              reset.mutate({ userId: resetTarget.id, data: { password: resetPassword } });
            }}
          >
            <label className="block text-sm text-zinc-700">
              新密码
              <input
                className={inputClass}
                type="password"
                autoComplete="new-password"
                value={resetPassword}
                onChange={(event) => setResetPassword(event.target.value)}
                placeholder="至少 8 个字符"
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setResetTarget(null)} disabled={reset.isPending}>
                取消
              </Button>
              <Button type="submit" disabled={reset.isPending}>
                {reset.isPending ? "重置中" : "确认重置"}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={Boolean(statusTarget)}
        onOpenChange={(open) => {
          if (!open) setStatusTarget(null);
        }}
        title="停用成员账号"
        description={
          statusTarget
            ? `停用“${statusTarget.username}”后，该账号将立即退出当前会话，且无法再次登录，直到管理员重新启用。`
            : ""
        }
        confirmLabel="确认停用"
        danger
        pending={updateStatus.isPending}
        onConfirm={() => {
          if (statusTarget) {
            updateStatus.mutate({ userId: statusTarget.id, data: { is_active: false } });
          }
        }}
      />
    </div>
  );
}

function Summary({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="border border-zinc-200 bg-white p-4">
      <div className="flex items-center justify-between text-zinc-500">
        <span className="text-sm">{label}</span>
        {icon}
      </div>
      <div className="mt-3 text-2xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}
