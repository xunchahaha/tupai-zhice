export type ScheduleCandidate = {
  id: string;
  status: string;
  parent_id?: string | null;
  name?: string;
  version_no?: number;
};

export function preferredSchedule<T extends ScheduleCandidate>(
  schedules: readonly T[] | undefined,
): T | undefined {
  if (!Array.isArray(schedules) || !schedules.length) return undefined;
  return (
    schedules.find((schedule) => schedule?.status === "published") ??
    schedules.find((schedule) => schedule?.status === "draft") ??
    schedules[0]
  );
}


export function latestDraftSchedule<T extends ScheduleCandidate>(
  schedules: readonly T[] | undefined,
): T | undefined {
  if (!Array.isArray(schedules)) return undefined;
  return schedules.find((schedule) => schedule?.status === "draft");
}
