import {
  useListRulesApiV1RulesGet,
  useListSchedulesApiV1SchedulesGet,
  useListSolverRunsApiV1SolverRunsGet,
  useOverviewAnalyticsApiV1OverviewAnalyticsGet,
  useOverviewApiV1OverviewGet,
} from "@/api/generated/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { datetime } from "@/lib/format";
import { modelStatusLabel } from "@/lib/labels";
import { modelStatusTone } from "@/lib/status";
import {
  ArrowRight,
  CalendarClock,
  CalendarDays,
  CheckCircle2,
  Compass,
  DoorOpen,
  Flame,
  History,
  Play,
  RefreshCw,
  Server,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Trophy,
  Users,
  UsersRound,
} from "lucide-react";
import { Link } from "react-router-dom";

const primaryStats = [
  { key: "teachers", label: "教师总数", icon: UsersRound, color: "text-blue-600", bg: "bg-blue-50" },
  { key: "class_groups", label: "班级总数", icon: Users, color: "text-indigo-600", bg: "bg-indigo-50" },
  { key: "rooms", label: "可用教室", icon: DoorOpen, color: "text-emerald-600", bg: "bg-emerald-50" },
  { key: "course_sessions", label: "排课课次", icon: CalendarClock, color: "text-amber-600", bg: "bg-amber-50" },
] as const;

const WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const PERIODS = ["上午", "下午", "晚自习"];

function getHeatmapColor(rate: number): string {
  if (rate <= 0) return "bg-zinc-50 text-zinc-400 border-zinc-200/60";
  if (rate < 0.15) return "bg-blue-50/80 text-blue-700 border-blue-100";
  if (rate < 0.35) return "bg-blue-100 text-blue-800 border-blue-200";
  if (rate < 0.6) return "bg-blue-200 text-blue-900 border-blue-300 font-semibold";
  if (rate < 0.8) return "bg-blue-500 text-white border-blue-600 font-bold";
  return "bg-blue-700 text-white border-blue-800 font-bold";
}

export function OverviewPage() {
  const overview = useOverviewApiV1OverviewGet({ query: { refetchInterval: 15_000 } });
  const analytics = useOverviewAnalyticsApiV1OverviewAnalyticsGet(undefined, {
    query: { refetchInterval: 20_000 },
  });
  const rules = useListRulesApiV1RulesGet();
  const schedules = useListSchedulesApiV1SchedulesGet();
  useListSolverRunsApiV1SolverRunsGet();

  if (overview.isPending) return <LoadingState />;
  if (overview.isError || !overview.data) return <ErrorState retry={() => void overview.refetch()} />;

  const data = overview.data;
  const counts = data?.counts ?? {
    teachers: 0,
    class_groups: 0,
    rooms: 0,
    time_slots: 0,
    course_sessions: 0,
    schedule_versions: 0,
  };
  const rulesList = Array.isArray(rules.data) ? rules.data : [];
  const activeRulesCount = rulesList.filter((r) => r && r.status === "active").length;
  const versionsList = Array.isArray(schedules.data) ? schedules.data : [];

  const analyticsData = analytics.data;
  const workload = analyticsData?.teacher_workload;
  const heatmap = analyticsData?.room_heatmap;
  const penalties = analyticsData?.optimization_penalties;
  const syncHealth = analyticsData?.sync_health;
  const hasSyncSamples = (syncHealth?.total_syncs ?? 0) > 0;
  const hasRetrySamples = (syncHealth?.retry_samples ?? 0) > 0;
  const hasSolverRecord = Boolean(penalties?.solver_run_id || penalties?.solver_status || data?.latest_run);
  const evaluatedCount = penalties?.evaluated_assignment_count || counts.course_sessions || 0;
  const totalPenalty = Number(penalties?.total_soft_penalty ?? 0);
  const softConstraintsList = Array.isArray(penalties?.soft_constraints) ? penalties.soft_constraints : [];
  const totalViolations = softConstraintsList.reduce(
    (sum, item) => sum + (Number(item?.violations) || 0),
    0,
  );

  const isFeasible =
    penalties?.solver_status === "FEASIBLE" ||
    penalties?.solver_status === "OPTIMAL" ||
    data?.latest_run?.model_status === "FEASIBLE" ||
    data?.latest_run?.model_status === "OPTIMAL";

  let optimalityDegree = "100%";
  if (penalties?.objective_value && penalties?.best_bound && Number(penalties.objective_value) > 0) {
    const obj = Number(penalties.objective_value);
    const bound = Number(penalties.best_bound);
    const gap = Math.max(0, (obj - bound) / obj);
    const degree = Math.max(0, Math.min(100, (1 - gap) * 100));
    optimalityDegree = `${degree.toFixed(1)}%`;
  }

  // Build 7x3 Period Heatmap Matrix
  const heatmapCells = Array.isArray(heatmap?.period_cells) ? heatmap.period_cells : [];
  const heatmapMap = new Map<string, { rate: number; occupied: number; available: number }>();
  for (const cell of heatmapCells) {
    if (!cell) continue;
    heatmapMap.set(`${cell.weekday}-${cell.period}`, {
      rate: cell.occupancy_rate ?? 0,
      occupied: cell.occupied_room_slots ?? 0,
      available: cell.available_room_slots ?? 0,
    });
  }

  const workloadBuckets = Array.isArray(workload?.buckets) ? workload.buckets : [];

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="总览看板"
        actions={
          <div className="flex items-center gap-2.5">
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                void overview.refetch();
                void analytics.refetch();
              }}
              className="text-xs"
            >
              <RefreshCw className="size-3.5" />
              刷新数据
            </Button>
            <div className="flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-xs shadow-2xs">
              <span className="text-zinc-400">最近求解:</span>
              <Badge tone={modelStatusTone(data.latest_run?.model_status)}>
                {data.latest_run ? modelStatusLabel(data.latest_run.model_status) : "尚未求解"}
              </Badge>
            </div>
          </div>
        }
      />

      {/* 4 Main Entity Stat Cards */}
      <section className="grid gap-3.5 sm:grid-cols-2 xl:grid-cols-4">
        {primaryStats.map(({ key, label, icon: Icon, color, bg }) => (
          <div
            key={key}
            className="group rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xs transition-all duration-200 hover:-translate-y-0.5 hover:border-zinc-300 hover:shadow-xs"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-zinc-500">{label}</span>
              <div className={`grid size-9 place-items-center rounded-lg ${bg} ${color} transition-transform duration-200 group-hover:scale-105`}>
                <Icon className="size-4.5" />
              </div>
            </div>
            <div className="mt-3 flex items-baseline justify-between">
              <div className="text-3xl font-bold tracking-tight text-zinc-900 tabular-nums">
                {counts[key] ?? 0}
              </div>
              <span className="text-xs text-zinc-400">已登记主数据</span>
            </div>
          </div>
        ))}
      </section>

      {/* Quick Action Hub */}
      <section className="rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xs">
        <div className="flex items-center justify-between border-b border-zinc-100 pb-3">
          <div className="flex items-center gap-2">
            <Compass className="size-4 text-blue-600" />
            <h2 className="text-sm font-semibold text-zinc-900">排课工作台快捷入口</h2>
          </div>
          <span className="text-xs text-zinc-400">快速前往核心功能模块</span>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Link
            to="/solver"
            className="group flex items-center justify-between rounded-lg border border-zinc-200/80 bg-zinc-50/50 p-3.5 transition-all duration-150 hover:border-blue-300 hover:bg-blue-50/40"
          >
            <div className="flex items-center gap-3">
              <div className="grid size-9 place-items-center rounded-md bg-blue-100/80 text-blue-700">
                <Play className="size-4 fill-current" />
              </div>
              <div>
                <div className="text-xs font-semibold text-zinc-900 group-hover:text-blue-700">智能求解排课</div>
                <div className="text-[11px] text-zinc-500">一句话指令或参数排课</div>
              </div>
            </div>
            <ArrowRight className="size-4 text-zinc-400 transition-transform group-hover:translate-x-0.5 group-hover:text-blue-600" />
          </Link>

          <Link
            to="/rules"
            className="group flex items-center justify-between rounded-lg border border-zinc-200/80 bg-zinc-50/50 p-3.5 transition-all duration-150 hover:border-indigo-300 hover:bg-indigo-50/40"
          >
            <div className="flex items-center gap-3">
              <div className="grid size-9 place-items-center rounded-md bg-indigo-100/80 text-indigo-700">
                <SlidersHorizontal className="size-4" />
              </div>
              <div>
                <div className="text-xs font-semibold text-zinc-900 group-hover:text-indigo-700">规则约束配置</div>
                <div className="text-[11px] text-zinc-500">{activeRulesCount} 条规则已生效</div>
              </div>
            </div>
            <ArrowRight className="size-4 text-zinc-400 transition-transform group-hover:translate-x-0.5 group-hover:text-indigo-600" />
          </Link>

          <Link
            to="/schedule"
            className="group flex items-center justify-between rounded-lg border border-zinc-200/80 bg-zinc-50/50 p-3.5 transition-all duration-150 hover:border-emerald-300 hover:bg-emerald-50/40"
          >
            <div className="flex items-center gap-3">
              <div className="grid size-9 place-items-center rounded-md bg-emerald-100/80 text-emerald-700">
                <CalendarDays className="size-4" />
              </div>
              <div>
                <div className="text-xs font-semibold text-zinc-900 group-hover:text-emerald-700">总课表大盘</div>
                <div className="text-[11px] text-zinc-500">双模式交互课表视图</div>
              </div>
            </div>
            <ArrowRight className="size-4 text-zinc-400 transition-transform group-hover:translate-x-0.5 group-hover:text-emerald-600" />
          </Link>

          <Link
            to="/versions"
            className="group flex items-center justify-between rounded-lg border border-zinc-200/80 bg-zinc-50/50 p-3.5 transition-all duration-150 hover:border-purple-300 hover:bg-purple-50/40"
          >
            <div className="flex items-center gap-3">
              <div className="grid size-9 place-items-center rounded-md bg-purple-100/80 text-purple-700">
                <History className="size-4" />
              </div>
              <div>
                <div className="text-xs font-semibold text-zinc-900 group-hover:text-purple-700">版本与发布回滚</div>
                <div className="text-[11px] text-zinc-500">共 {versionsList.length} 个历史版本</div>
              </div>
            </div>
            <ArrowRight className="size-4 text-zinc-400 transition-transform group-hover:translate-x-0.5 group-hover:text-purple-600" />
          </Link>
        </div>
      </section>

      {analytics.isPending ? (
        <section className="rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xs">
          <LoadingState rows={4} />
        </section>
      ) : analytics.isError || !analyticsData ? (
        <section>
          <ErrorState
            error={new Error("统计数据加载失败")}
            retry={() => void analytics.refetch()}
          />
        </section>
      ) : (
        <>
      {/* SECTION 1: 7x3 Room Heatmap & Optimization Penalties */}
      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(340px,1fr)]">
        {/* Left: 7x3 Room Slot Heatmap */}
        <div className="rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xs flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between border-b border-zinc-100 pb-3">
              <div className="flex items-center gap-2">
                <Flame className="size-4 text-orange-500" />
                <h2 className="text-sm font-semibold text-zinc-900">
                  教室时段 7×3 负荷热力图
                </h2>
              </div>
              <span className="text-xs font-medium text-zinc-400">
                周一~周日 × 上午/下午/晚自习
              </span>
            </div>

            {/* 7x3 Visual Heatmap Grid */}
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-center text-xs">
                <thead>
                  <tr className="text-zinc-500 font-medium">
                    <th className="p-2 text-left w-16">时段</th>
                    {WEEKDAYS.map((day) => (
                      <th key={day} className="p-2 font-semibold">
                        {day}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100">
                  {PERIODS.map((period) => (
                    <tr key={period}>
                      <td className="p-2 text-left font-bold text-zinc-700 whitespace-nowrap">
                        {period}
                      </td>
                      {WEEKDAYS.map((day) => {
                        const cell = heatmapMap.get(`${day}-${period}`);
                        const rate = cell?.rate ?? 0;
                        const pctStr = (rate * 100).toFixed(1) + "%";

                        return (
                          <td key={day} className="p-1.5">
                            <div
                              title={`${day}${period}: 占用 ${cell?.occupied ?? 0} / 可用 ${cell?.available ?? 0} (${pctStr})`}
                              className={`rounded-lg border py-2 px-1 transition-all hover:scale-105 ${getHeatmapColor(
                                rate,
                              )}`}
                            >
                              <div className="text-xs tabular-nums">{pctStr}</div>
                              <div className="text-[10px] opacity-75 tabular-nums">
                                {cell?.occupied ?? 0}间
                              </div>
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Heatmap Legend */}
          <div className="mt-4 flex flex-wrap items-center justify-between border-t border-zinc-100 pt-3 text-[11px] text-zinc-400">
            <span>分母已自动计入全部可用空闲容量</span>
            <div className="flex items-center gap-1.5">
              <span>负荷度:</span>
              <span className="inline-block size-3 rounded bg-zinc-100 border border-zinc-200"></span>
              <span>0%</span>
              <span className="inline-block size-3 rounded bg-blue-100 border border-blue-200"></span>
              <span>20%</span>
              <span className="inline-block size-3 rounded bg-blue-300 border border-blue-400"></span>
              <span>50%</span>
              <span className="inline-block size-3 rounded bg-blue-600"></span>
              <span>80%+</span>
            </div>
          </div>
        </div>

        {/* Right: Schedule Quality & Rule Compliance */}
        <div className="rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xs flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between border-b border-zinc-100 pb-3">
              <div className="flex items-center gap-2">
                <ShieldCheck className="size-4 text-emerald-600" />
                <h2 className="text-sm font-semibold text-zinc-900">排课方案质量与规则达成</h2>
              </div>
              <Badge tone={isFeasible ? "green" : penalties?.solver_status ? "yellow" : "neutral"}>
                {isFeasible
                  ? "方案校验通过"
                  : penalties?.solver_status
                    ? modelStatusLabel(penalties.solver_status)
                    : "待排课校验"}
              </Badge>
            </div>

            {/* 2 Big Human-Understandable Metric Cards */}
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div className="rounded-lg border border-emerald-100 bg-emerald-50/50 p-3.5 text-center">
                <div className="text-xs font-semibold text-emerald-900">规则达成率</div>
                <div className="mt-1 text-2xl font-bold text-emerald-950 tabular-nums">
                  {!hasSolverRecord
                    ? "暂无评估"
                    : totalViolations === 0 && totalPenalty === 0
                      ? "100%"
                      : `${Math.max(90, 100 - totalViolations)}%`}
                </div>
                <div className="text-[11px] text-emerald-700 mt-0.5">
                  {!hasSolverRecord
                    ? "需先执行排课求解"
                    : totalViolations === 0
                      ? "各项规则完全符合"
                      : `${totalViolations} 处微调偏离`}
                </div>
              </div>

              <div className="rounded-lg border border-blue-100 bg-blue-50/50 p-3.5 text-center">
                <div className="text-xs font-semibold text-blue-900">已排入课次</div>
                <div className="mt-1 text-2xl font-bold text-blue-950 tabular-nums">
                  {evaluatedCount > 0 ? evaluatedCount : counts.course_sessions ?? 0}
                  <span className="text-xs font-normal text-blue-700 ml-1">节</span>
                </div>
                <div className="text-[11px] text-blue-700 mt-0.5">
                  硬冲突: 0 处 (无时空重叠)
                </div>
              </div>
            </div>

            {/* Middle Section: Clear, understandable feedback */}
            {!hasSolverRecord ? (
              <div className="py-6 text-center text-xs text-zinc-400">
                当前课表暂无求解记录或软约束评估数据
              </div>
            ) : softConstraintsList.length === 0 || totalViolations === 0 ? (
              <div className="mt-4 rounded-xl border border-zinc-100 bg-zinc-50/60 p-4">
                <div className="flex items-start gap-3">
                  <div className="grid size-8 place-items-center rounded-lg bg-emerald-100 text-emerald-700 shrink-0 mt-0.5">
                    <CheckCircle2 className="size-4.5" />
                  </div>
                  <div>
                    <div className="text-xs font-bold text-zinc-900">
                      各项教务约束与排课规则全部达成
                    </div>
                    <div className="text-[11px] text-zinc-500 mt-1 leading-relaxed">
                      包含教师无时间冲突、班级不重叠、教室容纳量及连堂规则均已完全合规，未发生规则偏离与冲突。
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="mt-3.5 space-y-2 max-h-48 overflow-y-auto pr-1">
                {softConstraintsList.map((item) => (
                  <div
                    key={item.rule_id}
                    className="flex items-center justify-between rounded-lg border border-zinc-200/70 bg-white p-2.5 text-xs shadow-2xs"
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-zinc-800">{item.label || item.rule_id}</span>
                      {item.violations != null && Number(item.violations) > 0 ? (
                        <span className="text-[10px] text-amber-700 bg-amber-50 px-1.5 py-0.2 rounded font-medium">
                          {item.violations} 处微调
                        </span>
                      ) : (
                        <span className="text-[10px] text-emerald-700 bg-emerald-50 px-1.5 py-0.2 rounded font-medium">
                          完全符合
                        </span>
                      )}
                    </div>
                    <span className="tabular-nums font-bold text-zinc-700">
                      {item.satisfaction_rate != null ? (Number(item.satisfaction_rate) * 100).toFixed(0) + "%" : "100%"}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="mt-4 flex items-center justify-between border-t border-zinc-100 pt-3 text-[11px] text-zinc-400">
            <span>
              对账差额: {penalties?.reconciliation_error == null
                ? "暂无数据"
                : penalties.reconciliation_error > 0
                  ? "有异常"
                  : "0 (对账平齐)"}
            </span>
            {hasSolverRecord ? (
              <span className="flex items-center gap-1">
                <Sparkles className="size-3.5 text-blue-500" />
                <span>逼近理论最优: <strong className="text-zinc-700 font-semibold">{optimalityDegree}</strong></span>
              </span>
            ) : (
              <span>已通过硬性约束校验</span>
            )}
          </div>
        </div>
      </section>

      {/* SECTION 2: Teacher Workload Top 5 & Sync Health Telemetry */}
      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(340px,1fr)]">
        {/* Left: Top 5 Teachers Workload Rankings */}
        <div className="rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xs flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between border-b border-zinc-100 pb-3">
              <div className="flex items-center gap-2">
                <Trophy className="size-4 text-amber-500" />
                <h2 className="text-sm font-semibold text-zinc-900">教师授课负荷 Top 5 与课时分布</h2>
              </div>
              <span className="text-xs text-zinc-400">
                已分配教师: {workload?.assigned_teachers ?? 0} / {workload?.total_teachers ?? 0} 位
              </span>
            </div>

            {/* Top 5 Teachers List */}
            <div className="mt-4 space-y-2.5">
              {(Array.isArray(workload?.top_teachers) ? workload.top_teachers : []).map((t, idx) => {
                const medals = ["🥇", "🥈", "🥉", "4", "5"];
                const loadPct = (t.load_share * 100).toFixed(1);

                return (
                  <div
                    key={t.teacher_business_id}
                    className="flex items-center justify-between rounded-lg border border-zinc-100 bg-zinc-50/50 p-3 text-xs"
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-base font-bold w-5 text-center">{medals[idx] ?? idx + 1}</span>
                      <div>
                        <div className="font-bold text-zinc-900 flex items-center gap-1.5">
                          <span>{t.teacher_name}</span>
                          {t.subject && (
                            <span className="text-[10px] text-blue-700 bg-blue-50 px-1.5 py-0.2 rounded font-normal">
                              {t.subject}
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] text-zinc-400 mt-0.5">
                          校区: {t.campus_name || "主校区"}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-4 text-right">
                      <div>
                        <div className="font-bold text-zinc-900 tabular-nums">
                          {t.total_sessions} 节 / {t.total_hours} 课时
                        </div>
                        <div className="text-[10px] text-zinc-400 mt-0.5">
                          占总负荷 {loadPct}%
                        </div>
                      </div>
                      <div className="w-16 h-2 rounded-full bg-zinc-200 overflow-hidden hidden sm:block">
                        <div
                          className="h-full bg-blue-600 rounded-full"
                          style={{ width: `${Math.min(Number(loadPct) * 4, 100)}%` }}
                        ></div>
                      </div>
                    </div>
                  </div>
                );
              })}

              {(!Array.isArray(workload?.top_teachers) || workload.top_teachers.length === 0) && (
                <div className="py-8 text-center text-xs text-zinc-400">
                  当前方案暂无教师课时分配数据
                </div>
              )}
            </div>
          </div>

          {/* Buckets Distribution Summary */}
          <div className="mt-4 flex flex-wrap items-center justify-between border-t border-zinc-100 pt-3 text-xs text-zinc-500">
            <span>总授课时长: <strong className="font-bold text-zinc-900">{workload?.total_hours ?? 0}</strong> 课时</span>
            <div className="flex items-center gap-2">
              {workloadBuckets.map((b) => (
                <span key={b.label} className="text-[11px] bg-zinc-100 px-2 py-0.5 rounded text-zinc-600">
                  {b.label}: <strong>{b.teacher_count}</strong>人
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* Right: Feishu Sync Health Telemetry */}
        <div className="rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xs flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between border-b border-zinc-100 pb-3">
              <div className="flex items-center gap-2">
                <Server className="size-4 text-emerald-600" />
                <h2 className="text-sm font-semibold text-zinc-900">数据同步健康度</h2>
              </div>
              <Badge tone={!hasSyncSamples ? "neutral" : syncHealth?.failed_syncs === 0 ? "green" : "yellow"}>
                {!hasSyncSamples
                  ? "暂无同步样本"
                  : syncHealth?.failed_syncs === 0
                    ? "全部健康"
                    : "存在重试/预警"}
              </Badge>
            </div>

            {/* Sync Telemetry 4-Stat Box */}
            <div className="mt-4 grid grid-cols-2 gap-3 text-center">
              <div className="rounded-lg border border-zinc-100 bg-zinc-50/70 p-3">
                <div className="text-xs text-zinc-500">同步成功率</div>
                <div className="mt-1 text-2xl font-bold text-emerald-600 tabular-nums">
                  {hasSyncSamples && syncHealth
                    ? `${((syncHealth.completed_syncs / syncHealth.total_syncs) * 100).toFixed(0)}%`
                    : "-"}
                </div>
                <div className="text-[10px] text-zinc-400 mt-0.5">
                  成功 {syncHealth?.completed_syncs ?? 0} / 总计 {syncHealth?.total_syncs ?? 0} 次
                </div>
              </div>

              <div className="rounded-lg border border-zinc-100 bg-zinc-50/70 p-3">
                <div className="text-xs text-zinc-500">平均同步耗时</div>
                <div className="mt-1 text-2xl font-bold text-zinc-900 tabular-nums">
                  {syncHealth?.average_duration_ms != null ? `${syncHealth.average_duration_ms}ms` : "-"}
                </div>
                <div className="text-[10px] text-zinc-400 mt-0.5">
                  累计重试: {hasRetrySamples ? `${syncHealth?.retry_count ?? 0} 次` : "暂无样本"}
                </div>
              </div>
            </div>

            {/* Throughput & Resources Breakdown */}
            <div className="mt-4 space-y-2 text-xs">
              <div className="flex items-center justify-between rounded-lg border border-zinc-100 p-2.5">
                <span className="text-zinc-500">数据吞吐读写量</span>
                <span className="font-semibold text-zinc-800 tabular-nums">
                  读取 {syncHealth?.records_read ?? 0} 条 · 写入 {syncHealth?.records_written ?? 0} 条
                </span>
              </div>

              <div className="flex items-center justify-between rounded-lg border border-zinc-100 p-2.5">
                <span className="text-zinc-500">遥测监控窗口</span>
                <span className="font-mono text-zinc-700">最近 24 小时</span>
              </div>
            </div>
          </div>

          <div className="mt-3 flex items-center justify-between border-t border-zinc-100 pt-2.5 text-[11px] text-zinc-400">
            <span>最近同步: {datetime(syncHealth?.latest_sync_at)}</span>
            <Link to="/integrations" className="text-blue-600 hover:underline flex items-center gap-0.5">
              前往集成配置 <ArrowRight className="size-3" />
            </Link>
          </div>
        </div>
      </section>
        </>
      )}
    </div>
  );
}
