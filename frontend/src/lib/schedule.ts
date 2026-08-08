type ScheduleCandidate = {
  id: string;
  status: string;
};

export function preferredSchedule<T extends ScheduleCandidate>(
  schedules: readonly T[] | undefined,
): T | undefined {
  if (!schedules?.length) return undefined;
  return (
    schedules.find((schedule) => schedule.status === "published") ??
    schedules.find((schedule) => schedule.status === "draft") ??
    schedules[0]
  );
}
