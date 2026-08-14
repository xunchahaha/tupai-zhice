import { type ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { type UserResponseRole } from "@/api/generated/models";
import { useAppUser } from "@/app/user-context";

export function RoleRoute({ roles, children }: { roles: readonly UserResponseRole[]; children: ReactNode }) {
  const user = useAppUser();
  return roles.includes(user.role) ? children : <Navigate to="/overview" replace />;
}
