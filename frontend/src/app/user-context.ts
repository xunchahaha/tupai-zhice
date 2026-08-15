import { useOutletContext } from "react-router-dom";

import { type UserResponse } from "@/api/generated/models";
import { type ScheduleAccessRole, type ScheduleSet } from "@/api/schedule-sets";

export interface AppOutletContext {
  user: UserResponse;
  scheduleAccessRole?: ScheduleAccessRole;
  scheduleSet?: ScheduleSet;
}

export function useAppUser(): UserResponse {
  return useOutletContext<AppOutletContext>().user;
}

export function useScheduleAccessRole(): ScheduleAccessRole | undefined {
  return useOutletContext<AppOutletContext>().scheduleAccessRole;
}

export function isReadOnlyMember(
  user: UserResponse,
  scheduleAccessRole?: ScheduleAccessRole,
): boolean {
  return user.role === "viewer" || scheduleAccessRole === "viewer";
}

/**
 * The global account role defines the type of work a person may do; the
 * timetable membership grants that work for one specific plan.  An approver
 * can publish an assigned timetable but is not silently made a scheduler.
 */
export function canScheduleCurrentSet(
  user: UserResponse,
  scheduleAccessRole?: ScheduleAccessRole,
): boolean {
  return user.role === "admin" || (user.role === "scheduler" && scheduleAccessRole === "scheduler");
}
