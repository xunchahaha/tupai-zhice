import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";

import { isReadOnlyMember, useAppUser, useScheduleAccessRole } from "@/app/user-context";
import { cn } from "@/lib/cn";
import { ROUTES } from "@/lib/routes";

/**
 * 学校通用规则 / 常用偏好 / 账号管理三个完整管理页不在左侧导航里，
 * 只能从「设置」进入；页头放一个回到设置的出口，避免用户进去后找不到路。
 * 外观与 Button(outline, sm) 保持一致，但语义是链接。
 *
 * 规则页对所有角色开放，而设置页只给管理员/排课员（且非只读成员，与 RoleRoute 同口径）：
 * 进不了设置的账号点「返回设置」会被静默弹回，所以改成回到排课助手。
 */
export function SettingsBackLink({ className }: { className?: string }) {
  const user = useAppUser();
  const scheduleAccessRole = useScheduleAccessRole();
  const canOpenSettings =
    (user.role === "admin" || user.role === "scheduler") && !isReadOnlyMember(user, scheduleAccessRole);
  return (
    <Link
      to={canOpenSettings ? ROUTES.settings : ROUTES.assistant}
      className={cn(
        "inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-md border border-zinc-300 bg-white px-2.5 text-xs font-medium text-zinc-700 shadow-2xs transition-all duration-150 hover:border-zinc-400 hover:bg-zinc-50/80 active:scale-[0.98]",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600",
        className,
      )}
    >
      <ArrowLeft className="size-3.5" />
      {canOpenSettings ? "返回设置" : "返回排课助手"}
    </Link>
  );
}
