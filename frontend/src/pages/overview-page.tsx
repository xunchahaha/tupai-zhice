import {
  useListRulesApiV1RulesGet,
  useListSchedulesApiV1SchedulesGet,
  useListSolverRunsApiV1SolverRunsGet,
  useOverviewApiV1OverviewGet,
} from "@/api/generated/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { datetime, percent } from "@/lib/format";
import { modelStatusLabel, statusLabel } from "@/lib/labels";
import { modelStatusTone, statusTone } from "@/lib/status";
import {
  Activity,
  ArrowRight,
  BookOpenCheck,
  CalendarCheck,
  CalendarClock,
  CalendarDays,
  CheckCircle2,
  CircleAlert,
  Clock,
  Compass,
  DoorOpen,
  History,
  Layers,
  Play,
  RotateCcw,
  Scale,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Users,
  UsersRound,
  Zap,
} from "lucide-react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const primaryStats = [
  { key: "teachers", label: "教师总数", icon: UsersRound, color: "text-blue-600", bg: "bg-blue-50" },
  { key: "class_groups", label: "班级总数", icon: Users, color: "text-indigo-600", bg: "bg-indigo-50" },
  { key: "rooms", label: "可用教室", icon: DoorOpen, color: "text-emerald-600", bg: "bg-emerald-50" },
  { key: "course_sessions", label: "排课课次", icon: CalendarClock, color: "text-amber-600", bg: "bg-amber-50" },
] as const;

export function OverviewPage() {
  const overview = useOverviewApiV1OverviewGet({ query: { refetchInterval: 15_000 } });
  const rules = useListRulesApiV1RulesGet();
  const schedules = useListSchedulesApiV1SchedulesGet();
  const solverRuns = useListSolverRunsApiV1SolverRunsGet();

  if (overview.isPending) return <LoadingState />;
  if (overview.isError || !overview.data) return <ErrorState retry={() => void overview.refetch()} />;

  const data = overview.data;
  const metrics = data.latest_schedule?.metrics ?? {};
  const metricData = [
    { label: "教室占用", value: Number(metrics.room_slot_occupancy ?? 0) * 100 },
  ];

  const rulesList = rules.data ?? [];
  const activeRulesCount = rulesList.filter((r) => r.status === "active").length;
  const hardRulesCount = rulesList.filter((r) => r.hardness === "hard").length;
  const softRulesCount = rulesList.filter((r) => r.hardness === "soft").length;

  const versionsList = schedules.data ?? [];
  const publishedVersion = versionsList.find((s) => s.status === "published");
  const recentRuns = (solverRuns.data ?? []).slice(0, 4);

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="总览看板"
        actions={
          <div className="flex items-center gap-2">
            <span className="text-xs text-zinc-500">最近求解状态:</span>
            <Badge tone={modelStatusTone(data.latest_run?.model_status)}>
              {data.latest_run ? modelStatusLabel(data.latest_run.model_status) : "尚未求解"}
            </Badge>
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
                {data.counts[key] ?? 0}
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
                <div className="text-[11px] text-zinc-500">多维度交互课表视图</div>
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

      {/* Middle Section: Chart & Pending Todos */}
      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(340px,1fr)]">
        {/* Left: Occupancy Chart */}
        <div className="rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xs">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Activity className="size-4 text-blue-600" />
              <h2 className="text-sm font-semibold text-zinc-900">核心排课指标</h2>
            </div>
            <span className="text-xs font-mono text-zinc-400">
              当前版本: {publishedVersion ? `v${publishedVersion.version_no}` : (data.latest_schedule?.version_no ? `v${data.latest_schedule.version_no}` : "无")}
            </span>
          </div>

          <ResponsiveContainer width="100%" height={230}>
            <BarChart data={metricData} margin={{ left: -20, right: 6, top: 10 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f4f4f5" />
              <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: "#71717a" }} />
              <YAxis domain={[0, 100]} tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: "#a1a1aa" }} />
              <Tooltip
                cursor={{ fill: "#f4f4f5" }}
                contentStyle={{ borderRadius: "8px", border: "1px solid #e4e4e7", boxShadow: "0 4px 12px rgba(0,0,0,0.05)" }}
                formatter={(value) => `${Number(value).toFixed(1)}%`}
              />
              <Bar dataKey="value" fill="#2563eb" radius={[6, 6, 0, 0]} animationDuration={800} />
            </BarChart>
          </ResponsiveContainer>

          <div className="mt-4 grid grid-cols-3 gap-2 border-t border-zinc-100 pt-3 text-center">
            <div>
              <div className="text-xs text-zinc-400">硬冲突</div>
              <div className="mt-0.5 font-semibold text-emerald-600 tabular-nums">
                {typeof metrics.hard_conflicts === "number"
                  ? (metrics.hard_conflicts === 0 ? "0 (无冲突)" : metrics.hard_conflicts)
                  : String(metrics.hard_conflicts ?? "0")}
              </div>
            </div>
            <div>
              <div className="text-xs text-zinc-400">教室占用率</div>
              <div className="mt-0.5 font-semibold text-blue-600 tabular-nums">
                {percent(Number(metrics.room_slot_occupancy))}
              </div>
            </div>
            <div>
              <div className="text-xs text-zinc-400">生效状态</div>
              <div className="mt-0.5 font-semibold text-zinc-700">
                {publishedVersion ? "已发布上线" : "草稿中"}
              </div>
            </div>
          </div>
        </div>

        {/* Right: Pending Matters & Sync Status */}
        <div className="rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xs flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between border-b border-zinc-100 pb-3">
              <div className="flex items-center gap-2">
                <Clock className="size-4 text-amber-600" />
                <h2 className="text-sm font-semibold text-zinc-900">教务待办与同步状态</h2>
              </div>
              <span className="text-xs font-normal text-zinc-400">实时监控</span>
            </div>

            <div className="mt-4 space-y-2.5">
              <Link
                to="/rules"
                className="flex items-center justify-between rounded-lg border border-zinc-100 bg-zinc-50/60 p-3 text-xs transition-colors hover:border-zinc-200 hover:bg-zinc-100/70"
              >
                <span className="flex items-center gap-2.5 text-zinc-700 font-medium">
                  <BookOpenCheck className="size-4 text-amber-600" />
                  待确认排课规则
                </span>
                <div className="flex items-center gap-1.5">
                  <strong className="font-semibold text-zinc-900 tabular-nums">{data.pending_rules}</strong>
                  <span className="text-zinc-400">条</span>
                </div>
              </Link>

              <Link
                to="/reschedule"
                className="flex items-center justify-between rounded-lg border border-zinc-100 bg-zinc-50/60 p-3 text-xs transition-colors hover:border-zinc-200 hover:bg-zinc-100/70"
              >
                <span className="flex items-center gap-2.5 text-zinc-700 font-medium">
                  <CircleAlert className="size-4 text-amber-600" />
                  调课换课待审事件
                </span>
                <div className="flex items-center gap-1.5">
                  <strong className="font-semibold text-zinc-900 tabular-nums">{data.pending_reschedules}</strong>
                  <span className="text-zinc-400">件</span>
                </div>
              </Link>

              <Link
                to="/integrations"
                className="flex items-center justify-between rounded-lg border border-zinc-100 bg-zinc-50/60 p-3 text-xs transition-colors hover:border-zinc-200 hover:bg-zinc-100/70"
              >
                <span className="flex items-center gap-2.5 text-zinc-700 font-medium">
                  <Zap className="size-4 text-blue-600" />
                  飞书生产多维表格同步
                </span>
                <Badge tone={statusTone(data.latest_sync_status)}>
                  {statusLabel(data.latest_sync_status) === "未设置" ? "未执行" : statusLabel(data.latest_sync_status)}
                </Badge>
              </Link>
            </div>
          </div>

          {/* Rules Breakdown Mini Pill */}
          <div className="mt-4 rounded-lg bg-zinc-50 p-3 text-xs text-zinc-600">
            <div className="flex items-center justify-between mb-1.5">
              <span className="font-medium text-zinc-700">规则库体系概况</span>
              <span className="text-zinc-400 font-mono">共 {rulesList.length} 条</span>
            </div>
            <div className="flex items-center gap-3 text-[11px] text-zinc-500">
              <span>硬约束: <strong className="text-zinc-800">{hardRulesCount}</strong></span>
              <span>软约束: <strong className="text-zinc-800">{softRulesCount}</strong></span>
              <span>已激活: <strong className="text-emerald-700">{activeRulesCount}</strong></span>
            </div>
          </div>
        </div>
      </section>

      {/* Bottom Section: Recent Solver Runs List */}
      <section className="rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xs">
        <div className="flex items-center justify-between border-b border-zinc-100 pb-3">
          <div className="flex items-center gap-2">
            <History className="size-4 text-blue-600" />
            <h2 className="text-sm font-semibold text-zinc-900">近期求解任务动态</h2>
          </div>
          <Link to="/solver" className="text-xs text-blue-600 hover:underline">
            进入排课求解器 →
          </Link>
        </div>

        <div className="mt-4">
          {recentRuns.length === 0 ? (
            <div className="py-8 text-center text-xs text-zinc-400">暂无历史求解任务</div>
          ) : (
            <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
              {recentRuns.map((run) => (
                <div
                  key={run.id}
                  className="rounded-lg border border-zinc-100 bg-zinc-50/50 p-3 transition-all hover:bg-zinc-100/60"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs font-semibold text-zinc-800">
                      任务 #{run.id.slice(0, 8)}
                    </span>
                    <Badge tone={modelStatusTone(run.model_status)}>
                      {modelStatusLabel(run.model_status)}
                    </Badge>
                  </div>
                  <div className="mt-2 text-[11px] text-zinc-500 flex items-center justify-between">
                    <span>{datetime(run.created_at)}</span>
                    <span className="font-mono">{typeof run.wall_time_seconds === "number" ? `${run.wall_time_seconds.toFixed(1)}s` : "-"}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
