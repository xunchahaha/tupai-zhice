import { useOverviewApiV1OverviewGet } from "@/api/generated/client";
import { Badge } from "@/components/ui/badge";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { datetime, percent } from "@/lib/format";
import { modelStatusLabel, statusLabel } from "@/lib/labels";
import { statusTone } from "@/lib/status";
import { Activity, BookOpenCheck, CalendarClock, CircleAlert, DoorOpen, UsersRound } from "lucide-react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const stats = [
  { key: "teachers", label: "教师", icon: UsersRound }, { key: "class_groups", label: "班级", icon: UsersRound }, { key: "rooms", label: "教室", icon: DoorOpen }, { key: "course_sessions", label: "课次", icon: CalendarClock },
] as const;

export function OverviewPage() {
  const overview = useOverviewApiV1OverviewGet({ query: { refetchInterval: 15_000 } });
  if (overview.isPending) return <LoadingState />;
  if (overview.isError || !overview.data) return <ErrorState retry={() => void overview.refetch()} />;
  const data = overview.data;
  const metrics = data.latest_schedule?.metrics ?? {};
  const metricData = [
    { label: "教室占用", value: Number(metrics.room_slot_occupancy ?? 0) * 100 },
  ];
  return <div className="space-y-5"><PageHeader title="总览" actions={<Badge tone={statusTone(data.latest_run?.model_status)}>{data.latest_run ? modelStatusLabel(data.latest_run.model_status) : "尚未求解"}</Badge>} /><section className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">{stats.map(({ key, label, icon: Icon }) => <div key={key} className="border border-zinc-200 bg-white p-4"><div className="flex items-center justify-between text-zinc-500"><span className="text-sm">{label}</span><Icon className="size-4" /></div><div className="mt-4 text-2xl font-semibold tabular-nums">{data.counts[key] ?? 0}</div></div>)}</section><section className="grid gap-2 xl:grid-cols-[minmax(0,1.5fr)_minmax(320px,1fr)]"><div className="min-h-[300px] border border-zinc-200 bg-white p-4"><div className="mb-4 flex items-center justify-between"><h2 className="text-sm font-semibold">排课指标</h2><span className="text-xs text-zinc-400">版本 {data.latest_schedule?.version_no ?? "-"}</span></div><ResponsiveContainer width="100%" height={230}><BarChart data={metricData} margin={{ left: -20, right: 6 }}><XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: "#71717a" }} /><YAxis domain={[0, 100]} tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: "#a1a1aa" }} /><Tooltip cursor={{ fill: "#f4f4f5" }} formatter={(value) => `${Number(value).toFixed(1)}%`} /><Bar dataKey="value" fill="#2563eb" radius={[3, 3, 0, 0]} /></BarChart></ResponsiveContainer></div><div className="border border-zinc-200 bg-white p-4"><h2 className="text-sm font-semibold">待处理</h2><div className="mt-4 space-y-3"><div className="flex items-center justify-between text-sm"><span className="flex items-center gap-2 text-zinc-600"><BookOpenCheck className="size-4 text-amber-600" />待确认规则</span><strong>{data.pending_rules}</strong></div><div className="flex items-center justify-between text-sm"><span className="flex items-center gap-2 text-zinc-600"><CircleAlert className="size-4 text-amber-600" />调课事件</span><strong>{data.pending_reschedules}</strong></div><div className="flex items-center justify-between text-sm"><span className="flex items-center gap-2 text-zinc-600"><Activity className="size-4 text-blue-600" />飞书同步</span><Badge tone={statusTone(data.latest_sync_status)}>{statusLabel(data.latest_sync_status) === "未设置" ? "未执行" : statusLabel(data.latest_sync_status)}</Badge></div></div></div></section><section className="grid gap-2 md:grid-cols-3"><Metric label="硬冲突" value={String(metrics.hard_conflicts ?? "-")} /><Metric label="教室占用率" value={percent(Number(metrics.room_slot_occupancy))} /><Metric label="最近求解" value={datetime(data.latest_run?.created_at)} /></section></div>;
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="border-l-2 border-blue-600 bg-white px-4 py-3"><div className="text-xs text-zinc-400">{label}</div><div className="mt-1 text-sm font-semibold text-zinc-800">{value}</div></div>; }
