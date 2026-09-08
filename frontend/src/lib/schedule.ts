export type ScheduleCandidate = {
  id: string;
  status: string;
  parent_id?: string | null;
  name?: string;
  version_no?: number;
  solver_run_id?: string | null;
};

export function scheduleForRun<T extends ScheduleCandidate>(
  schedules: readonly T[] | undefined,
  run: { id: string; status: string; model_status?: string | null } | null | undefined,
): T | undefined {
  if (run?.status !== "completed" || !["OPTIMAL", "FEASIBLE"].includes(run.model_status ?? "")) return undefined;
  return schedules?.find((schedule) => schedule.solver_run_id === run.id);
}

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
