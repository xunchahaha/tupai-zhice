import { useOutletContext } from "react-router-dom";

import { type UserResponse } from "@/api/generated/models";

export interface AppOutletContext {
  user: UserResponse;
}

export function useAppUser(): UserResponse {
  return useOutletContext<AppOutletContext>().user;
}

export function isReadOnlyMember(user: UserResponse): boolean {
  return user.role === "viewer";
}
