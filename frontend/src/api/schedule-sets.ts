import { http } from "@/api/http";

export type ScheduleAccessRole = "viewer" | "scheduler" | "approver";

export interface ScheduleSet {
  id: string;
  code: string;
  name: string;
  display_order: number;
  is_active: boolean;
  access_role: ScheduleAccessRole;
  current_version_id?: string | null;
  current_version_name?: string | null;
  current_version_no?: number | null;
}

export interface ScheduleSetMember {
  id: string;
  schedule_set_id: string;
  user_id: string;
  username: string;
  user_role: "admin" | "scheduler" | "approver" | "viewer";
  access_role: ScheduleAccessRole;
  is_active: boolean;
  granted_by?: string | null;
  created_at: string;
}

export const scheduleSetApi = {
  async list(): Promise<ScheduleSet[]> {
    const response = await http.get<ScheduleSet[]>("/api/v1/schedule-sets");
    return response.data;
  },
  async create(name: string): Promise<ScheduleSet> {
    const response = await http.post<ScheduleSet>("/api/v1/schedule-sets", { name });
    return response.data;
  },
  async rename(scheduleSetId: string, name: string): Promise<ScheduleSet> {
    const response = await http.patch<ScheduleSet>(`/api/v1/schedule-sets/${scheduleSetId}`, { name });
    return response.data;
  },
  async listMembers(scheduleSetId: string): Promise<ScheduleSetMember[]> {
    const response = await http.get<ScheduleSetMember[]>(`/api/v1/schedule-sets/${scheduleSetId}/members`);
    return response.data;
  },
  async setMember(
    scheduleSetId: string,
    userId: string,
    accessRole: ScheduleAccessRole,
  ): Promise<ScheduleSetMember> {
    const response = await http.put<ScheduleSetMember>(
      `/api/v1/schedule-sets/${scheduleSetId}/members/${userId}`,
      { user_id: userId, access_role: accessRole },
    );
    return response.data;
  },
  async revokeMember(scheduleSetId: string, userId: string): Promise<void> {
    await http.delete(`/api/v1/schedule-sets/${scheduleSetId}/members/${userId}`);
  },
};
