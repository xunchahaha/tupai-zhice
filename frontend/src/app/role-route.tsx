import { type ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { type UserResponseRole } from "@/api/generated/models";
import { isReadOnlyMember, useAppUser, useScheduleAccessRole } from "@/app/user-context";
import { ROUTES } from "@/lib/routes";

export function RoleRoute({ roles, children }: { roles: readonly UserResponseRole[]; children: ReactNode }) {
  const user = useAppUser();
  const scheduleAccessRole = useScheduleAccessRole();
  const requiresWriteAccess = roles.some((role) => role !== "viewer");
  const allowed = roles.includes(user.role)
    && !(requiresWriteAccess && isReadOnlyMember(user, scheduleAccessRole));
  return allowed ? children : <Navigate to={ROUTES.assistant} replace />;
}
