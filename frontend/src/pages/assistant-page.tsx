import { Link, useOutletContext, useSearchParams } from "react-router-dom";

import {
  useGetScheduleApiV1SchedulesScheduleIdGet,
  useListCourseSessionsApiV1CourseSessionsGet,
  useListRulesApiV1RulesGet,
  useListSchedulesApiV1SchedulesGet,
  useListSolverRunsApiV1SolverRunsGet,
} from "@/api/generated/client";
import { type CourseSessionResponse, type ScheduleSummaryResponse, type SolverRunResponse } from "@/api/generated/models";
import { type AppOutletContext, canPublishCurrentSet, canScheduleCurrentSet, isReadOnlyMember } from "@/app/user-context";
import { CollapsibleSection } from "@/components/assistant/collapsible-section";
import { DetailsSection } from "@/components/assistant/details-section";
import { HomeTasks } from "@/components/assistant/home-tasks";
import { ManualSolvePanel } from "@/components/assistant/manual-solve-panel";
import { NextStep } from "@/components/assistant/next-step";
import { PendingDrafts } from "@/components/assistant/pending-drafts";
import { RequestCard } from "@/components/assistant/request-card";
import { RequirementsPanel } from "@/components/assistant/requirements-panel";
import { RestrictedRunView } from "@/components/assistant/restricted-run-view";
import { RunRecords } from "@/components/assistant/run-records";
import { TaskBar } from "@/components/assistant/task-bar";
import { useAssistantTask } from "@/components/assistant/use-assistant-task";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { SetupChecklist } from "@/components/setup-checklist";
import { SopSteps } from "@/components/sop-steps";
import { buildTaskSteps } from "@/lib/assistant-task";
import { asArray } from "@/lib/format";
import { ROUTES } from "@/lib/routes";
import { scheduleForRun } from "@/lib/schedule";
import { hasPendingSetup } from "@/lib/setup-state";
import { defaultParams } from "@/lib/solver-params";
import { OverviewInsights } from "@/pages/overview-page";

/**
 * 排课助手：交代需求、接着办、看结果。默认只突出三件事——你交代了什么、现在办到哪里、下一步需要你做什么。
 * 目标跟踪、无解诊断、总览、规则与记忆不再是独立入口，而是在对应的时刻出现在这里。
 */
export function AssistantPage() {
  const { user, scheduleAccessRole, scheduleSetLoading } = useOutletContext<AppOutletContext>();
  const canPublish = canPublishCurrentSet(user, scheduleAccessRole);
  // 设置页入口对管理员/排课员开放，只读成员即使角色是排课员也进不去。
  const canOpenSettings = (user.role === "admin" || user.role === "scheduler") && !isReadOnlyMember(user, scheduleAccessRole);
  if (!canScheduleCurrentSet(user, scheduleAccessRole)) {
    // 课表方案还在加载时 scheduleAccessRole 为空，不能据此判定「没有排课权限」，否则排课员会先闪一下受限页。
    if (scheduleSetLoading ?? false) return <LoadingState />;
    return <RestrictedAssistant readOnly={isReadOnlyMember(user, scheduleAccessRole)} canPublish={canPublish} canOpenSettings={canOpenSettings} />;
  }
  return <SchedulerAssistant canPublish={canPublish} canOpenSettings={canOpenSettings} />;
}

/**
 * 没有排课权限的账号：不显示需求输入与任务，保留说明、（有权限时）待发布草稿、数据概览，
 * 以及只读的求解诊断（?run=，旧的无解诊断页对所有角色可读）和学校通用规则入口。
 */
function RestrictedAssistant({ readOnly, canPublish, canOpenSettings }: { readOnly: boolean; canPublish: boolean; canOpenSettings: boolean }) {
  const runParam = useSearchParams()[0].get("run") ?? "";
  return (
    <div className="space-y-5 animate-fade-in">
      <PageHeader title="排课助手"><SopSteps /></PageHeader>
      <section className="rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-3 text-sm text-zinc-700">
        {readOnly
          ? "当前账号为只读，可在「课表」查看课表与版本。"
          : "当前账号没有排课权限，不能在这里交代需求或排课；可以审批发布草稿，或在「课表」查看课表与版本。"}
        <Link className="ml-2 text-blue-700 underline-offset-2 hover:underline" to={ROUTES.schedule}>去课表</Link>
        <Link className="ml-3 text-blue-700 underline-offset-2 hover:underline" to={ROUTES.rules}>查看学校通用规则</Link>
      </section>
      {runParam ? <RestrictedRunView runId={runParam} /> : null}
      {canPublish ? <PendingDrafts /> : null}
      <OverviewInsights canOpenSettings={canOpenSettings} />
    </div>
  );
}

function SchedulerAssistant({ canPublish, canOpenSettings }: { canPublish: boolean; canOpenSettings: boolean }) {
  const task = useAssistantTask();
  const rules = useListRulesApiV1RulesGet({ status: "active" });
  const courses = useListCourseSessionsApiV1CourseSessionsGet();
  const runs = useListSolverRunsApiV1SolverRunsGet({ query: { refetchInterval: 5000 } });
  const schedules = useListSchedulesApiV1SchedulesGet();
  const scheduleList = asArray<ScheduleSummaryResponse>(schedules.data);
  const publishedVersion = scheduleList.find((item) => item.status === "published");
  const publishedDetail = useGetScheduleApiV1SchedulesScheduleIdGet(publishedVersion?.id ?? "", { query: { enabled: Boolean(publishedVersion) } });
  if (rules.isPending || runs.isPending || schedules.isPending) return <LoadingState />;
  if (rules.isError || runs.isError || schedules.isError) {
    return <ErrorState retry={() => { void rules.refetch(); void runs.refetch(); void schedules.refetch(); }} />;
  }

  const { params } = task;
  const courseRows = asArray<CourseSessionResponse>(courses.data);
  const runList = asArray<SolverRunResponse>(runs.data);
  const scopeOptions = {
    businessLines: [...new Set(courseRows.map((item) => item.business_line ?? "").filter(Boolean))].sort(),
    classes: [...new Set(courseRows.map((item) => item.class_business_id))].sort(),
  };
  // 手动排课面板「当前范围命中 N 个课次」：以已发布课表里的日期为准，没有再用课次自带日期。
  const parentDates = new Map(publishedDetail.data?.assignments?.map((item) => [item.course_session_id, item.lesson_date]));
  const selectedCount = courseRows.filter((item) => {
    if (params.business_lines.length && !params.business_lines.includes(item.business_line ?? "")) return false;
    if (params.class_business_ids.length && !params.class_business_ids.includes(item.class_business_id)) return false;
    const lessonDate = parentDates.get(item.id) ?? item.lesson_date ?? "";
    if (params.date_from && lessonDate < params.date_from) return false;
    if (params.date_to && lessonDate > params.date_to) return false;
    return true;
  }).length;

  const manualPanel = (
    <ManualSolvePanel
      params={params}
      setParams={task.setParams}
      scope={scopeOptions}
      selectedCount={selectedCount}
      pending={task.pending}
      aiWindowBadge={task.aiWindowFilled && params.date_window_days !== defaultParams.date_window_days}
      onManualEdit={task.markManualEdit}
      // 已结束的任务不会随求解提交 goal_id，面板也就不该声称这次求解关联了它。
      linkedGoalId={task.goalClosed ? "" : task.goalId}
      onClearGoal={task.clearGoal}
      scopeExpansionPending={Boolean(task.scopeExpansion)}
      onScopeFieldChange={task.changeScopeField}
      focusScope={task.focusScope}
      onScopeFocused={task.clearFocusScope}
      onSubmit={task.submitManual}
    />
  );

  if (!task.taskMode) {
    const setup = {
      masterDataImported: courseRows.length > 0,
      activeRuleCount: asArray(rules.data).length,
      aiConfigured: task.probe.ready,
      publishedScheduleCount: scheduleList.filter((item) => item.status === "published").length,
    };
    return (
      <div className="space-y-5 animate-fade-in">
        <PageHeader title="排课助手"><SopSteps /></PageHeader>
        {hasPendingSetup(setup) ? <SetupChecklist variant="compact" {...setup} /> : null}
        <RequestCard task={task} />
        {task.manualOpen ? <div className="animate-fade-in">{manualPanel}</div> : null}
        {canPublish ? <PendingDrafts /> : null}
        <HomeTasks runs={runList} />
        <CollapsibleSection title="数据概览" hint="统计、教师负荷、教室热力、同步健康度">
          <OverviewInsights canOpenSettings={canOpenSettings} />
        </CollapsibleSection>
        <RunRecords runs={runList} />
      </div>
    );
  }

  const draft = scheduleForRun(scheduleList, task.activeRun);
  const steps = buildTaskSteps({
    understood: Boolean(task.interpretation) || Boolean(task.goalId),
    confirmed: task.confirmed,
    runKind: task.runKind,
    draftStatus: draft?.status,
    pendingConfirmation: task.awaitingReconfirmation,
  });
  return (
    <div className="space-y-5 animate-fade-in">
      <PageHeader title="排课助手"><SopSteps /></PageHeader>
      <TaskBar task={task} steps={steps} />
      {task.goalId && task.goalNotice ? (
        <p role="status" className="border-l-2 border-blue-400 bg-blue-50/60 px-3 py-2 text-xs text-zinc-700">{task.goalNotice}</p>
      ) : null}
      <RequirementsPanel task={task} />
      <NextStep task={task} scheduleList={scheduleList} canPublish={canPublish} defaultDiagnosisOpen={Boolean(task.runParam)} />
      <div>
        <button
          type="button"
          aria-expanded={task.manualOpen && task.detailsOpen}
          className="text-sm text-zinc-500 underline-offset-2 transition-colors hover:text-zinc-900 hover:underline"
          onClick={task.toggleManual}
        >
          手动排课（自己设置参数）
        </button>
      </div>
      <DetailsSection task={task} manualPanel={manualPanel} />
    </div>
  );
}
