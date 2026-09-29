import { useOutletContext } from "react-router-dom";

import { type UserResponse } from "@/api/generated/models";
import { type ScheduleAccessRole, type ScheduleSet } from "@/api/schedule-sets";

export interface AppOutletContext {
  user: UserResponse;
  scheduleAccessRole?: ScheduleAccessRole;
  scheduleSet?: ScheduleSet;
  /** 课表方案列表是否仍在加载：加载期间 scheduleAccessRole 为空，不能据此判定「无权限」。 */
  scheduleSetLoading: boolean;
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

/**
 * 发布/回滚/删除版本的判定（原 versions-page 内联口径的单一来源）：
 * 全局角色须为管理员或审批人，且对当前课表方案持有审批权限。
 * 排课员生成草稿后只能等待有权限的人发布。
 */
export function canPublishCurrentSet(
  user: UserResponse,
  scheduleAccessRole?: ScheduleAccessRole,
): boolean {
  return (user.role === "admin" || user.role === "approver") && scheduleAccessRole === "approver";
}
