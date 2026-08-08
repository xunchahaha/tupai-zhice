import { useQueryClient } from "@tanstack/react-query";
import { Bot, Check, CheckCircle2, Clipboard, CloudCog, ExternalLink, Link2, RefreshCw, SendHorizontal, TableProperties } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey, useFeishuConnectionApiV1IntegrationsFeishuConnectionGet, useFeishuSyncApiV1IntegrationsFeishuSyncPost, useListFeishuSyncsApiV1IntegrationsFeishuSyncsGet } from "@/api/generated/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { datetime, errorMessage } from "@/lib/format";
import { integrationModeLabel, resourceLabel, statusLabel, syncDirectionLabel } from "@/lib/labels";
import { statusTone } from "@/lib/status";

const resources = ["teachers", "class_groups", "rooms", "time_slots", "course_sessions", "rules", "schedule"] as const;
const envTemplate = `FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=请填写应用密钥
FEISHU_BITABLE_APP_TOKEN=bascnxxx
FEISHU_TABLE_MAP={"teachers":"tblxxx","class_groups":"tblxxx","rooms":"tblxxx","time_slots":"tblxxx","course_sessions":"tblxxx","rules":"tblxxx","schedule":"tblxxx"}`;

export function IntegrationsPage() {
  const client = useQueryClient();
  const connection = useFeishuConnectionApiV1IntegrationsFeishuConnectionGet();
  const syncs = useListFeishuSyncsApiV1IntegrationsFeishuSyncsGet();
  const [resource, setResource] = useState<(typeof resources)[number]>("teachers");
  const [direction, setDirection] = useState<"import" | "export">("export");
  const [copied, setCopied] = useState(false);
  const refresh = () => { void connection.refetch(); void syncs.refetch(); };
  const sync = useFeishuSyncApiV1IntegrationsFeishuSyncPost({ mutation: { onSuccess: () => { toast.success("真实飞书同步已完成"); void client.invalidateQueries({ queryKey: getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey() }); }, onError: (error) => toast.error(errorMessage(error)) } });
  if (connection.isPending || syncs.isPending) return <LoadingState />;
  if (connection.isError || syncs.isError) return <ErrorState retry={refresh} />;
  const status = connection.data;
  const ready = Boolean(status?.configured && status.connected && status.table_mapping_configured);
  const copyTemplate = async () => { await navigator.clipboard.writeText(envTemplate); setCopied(true); toast.success("配置模板已复制"); window.setTimeout(() => setCopied(false), 1800); };
  return <div className="space-y-5">
    <PageHeader title="飞书集成" actions={<Button size="sm" variant="outline" onClick={refresh}><RefreshCw className="size-3.5" />检查连接</Button>}>
      <p className="mt-1 text-sm text-zinc-500">这里连接真实飞书生产环境。未完成配置时，同步入口保持禁用。</p>
    </PageHeader>
    <section className={`border p-5 ${ready ? "border-emerald-200 bg-emerald-50/50" : "border-amber-200 bg-amber-50/50"}`}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3"><div className={`mt-0.5 rounded-full p-2 ${ready ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>{ready ? <CheckCircle2 className="size-5" /> : <Link2 className="size-5" />}</div><div><h2 className="font-semibold text-zinc-950">飞书生产连接</h2><p className="mt-1 text-sm text-zinc-700">{status?.message ?? "尚未返回连接信息"}</p></div></div>
        <Badge tone={ready ? "green" : "yellow"}>{status ? integrationModeLabel(status.mode) : "未检查"}</Badge>
      </div>
      <div className="mt-5 grid gap-3 sm:grid-cols-4"><ConnectionItem label="应用凭据" value={status?.configured ? "已配置" : "未配置"} /><ConnectionItem label="应用鉴权" value={status?.connected ? "已验证" : "未验证"} /><ConnectionItem label="数据表映射" value={status?.table_mapping_configured ? "已完成" : `${status?.missing_resources?.length ?? 0} 项待配置`} /><ConnectionItem label="同步权限" value={ready ? "已开放" : "未开放"} /></div>
      {status?.missing_fields?.length ? <div className="mt-4 border-t border-amber-200 pt-4 text-xs leading-5 text-amber-900"><strong>待填写环境变量：</strong>{status.missing_fields.join("、")}</div> : null}
      {status?.missing_resources?.length ? <div className="mt-2 text-xs leading-5 text-amber-900"><strong>待填写数据表：</strong>{status.missing_resources.map(resourceLabel).join("、")}</div> : null}
    </section>
    {!ready ? <section className="border border-zinc-200 bg-white p-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold">生产连接引导</h2><p className="mt-1 text-sm text-zinc-500">按以下顺序配置，完成后重启后端并点击“检查连接”。</p></div><div className="flex gap-2"><a href={status?.console_url ?? "https://open.feishu.cn/app/"} target="_blank" rel="noreferrer"><Button size="sm" variant="outline"><ExternalLink className="size-3.5" />打开飞书开放平台</Button></a><a href={status?.docs_url ?? "https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal"} target="_blank" rel="noreferrer"><Button size="sm" variant="ghost"><ExternalLink className="size-3.5" />查看鉴权文档</Button></a></div></div><ol className="mt-5 grid gap-3 text-sm text-zinc-700 md:grid-cols-2"><GuideStep index="1" title="创建企业自建应用" body="在飞书开放平台创建应用，记录 App ID、App Secret，申请多维表格记录读取和写入权限并发布应用版本。" /><GuideStep index="2" title="准备多维表格" body="创建教师、班级、教室、时段、课程场次、规则、课表数据表，分别复制每张表的 table_id。" /><GuideStep index="3" title="填写后端环境变量" body="将凭据、Bitable app_token 和资源到 table_id 的映射写入 backend/.env，只保存在服务端。" /><GuideStep index="4" title="检查并开始同步" body="重启后端，点击“检查连接”；应用鉴权和全部数据表映射完成后，执行同步按钮自动开放。" /></ol><div className="mt-5 rounded-md border border-zinc-200 bg-zinc-950 p-4 text-xs text-zinc-100"><div className="mb-2 flex items-center justify-between"><span className="text-zinc-400">backend/.env 配置模板</span><Button size="sm" variant="secondary" onClick={() => void copyTemplate()}><Clipboard className="size-3.5" />{copied ? <><Check className="size-3.5" />已复制</> : "复制模板"}</Button></div><pre className="overflow-x-auto whitespace-pre-wrap leading-5">{envTemplate}</pre></div></section> : null}
    <div className="grid gap-2 xl:grid-cols-3"><section className="border border-zinc-200 bg-white p-5"><div className="flex items-center justify-between"><TableProperties className="size-4 text-blue-600" /><Badge tone={ready ? "green" : "yellow"}>{ready ? "可同步" : "待配置"}</Badge></div><h2 className="mt-5 font-semibold">多维表格</h2><div className="mt-2 text-sm text-zinc-600">资源字段按照导出模板写入真实飞书数据表；缺少映射时接口会直接阻断。</div><div className="mt-4 text-xs text-zinc-400">同步前请确认应用已发布且管理员已批准权限。</div></section><section className="border border-zinc-200 bg-white p-5"><Bot className="size-4 text-blue-600" /><h2 className="mt-5 font-semibold">Aily 规则入口</h2><div className="mt-2 text-sm text-zinc-600">候选规则由 Aily Skill 提交，后端校验后进入人工确认。</div><a className="mt-4 inline-flex text-xs text-blue-700 hover:underline" href="https://aily.feishu.cn/ai/agents/" target="_blank" rel="noreferrer">打开 Aily 控制台</a></section><section className="border border-zinc-200 bg-white p-5"><CloudCog className="size-4 text-blue-600" /><h2 className="mt-5 font-semibold">执行真实同步</h2><div className="mt-4 flex gap-2"><select aria-label="同步资源" className="h-8 min-w-0 flex-1 rounded-md border border-zinc-300 px-2 text-xs" value={resource} onChange={(event) => setResource(event.target.value as typeof resource)}>{resources.map((item) => <option key={item} value={item}>{resourceLabel(item)}</option>)}</select><select aria-label="同步方向" className="h-8 rounded-md border border-zinc-300 px-2 text-xs" value={direction} onChange={(event) => setDirection(event.target.value as "import" | "export")}><option value="export">导出</option><option value="import">导入</option></select></div><Button className="mt-3 w-full" size="sm" onClick={() => sync.mutate({ data: { resource, direction } })} disabled={!ready || sync.isPending}><SendHorizontal className="size-3.5" />{sync.isPending ? "同步中" : ready ? "执行同步" : "完成配置后启用"}</Button>{!ready ? <p className="mt-2 text-xs text-amber-700">请先完成应用鉴权和数据表映射。</p> : null}</section></div>
    <section className="border border-zinc-200 bg-white"><div className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold">真实同步记录</div><div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-sm"><thead className="bg-zinc-50 text-xs text-zinc-500"><tr><th className="h-9 px-4">时间</th><th>方向</th><th>资源</th><th>连接模式</th><th>状态</th><th>读取</th><th>写入</th></tr></thead><tbody>{syncs.data?.filter((item) => item.mode === "live").map((item) => <tr key={item.id} className="border-t border-zinc-100"><td className="h-10 px-4 text-zinc-500">{datetime(item.created_at)}</td><td>{syncDirectionLabel(item.direction)}</td><td>{resourceLabel(item.resource)}</td><td>{integrationModeLabel(item.mode)}</td><td><Badge tone={statusTone(item.status)}>{statusLabel(item.status)}</Badge></td><td>{item.records_read}</td><td>{item.records_written}</td></tr>)}</tbody></table></div>{!syncs.data?.some((item) => item.mode === "live") ? <div className="p-6 text-center text-sm text-zinc-400">暂无真实飞书同步记录</div> : null}</section>
  </div>;
}

function ConnectionItem({ label, value }: { label: string; value: string }) { return <div className="border border-black/5 bg-white/70 p-3"><div className="text-xs text-zinc-500">{label}</div><div className="mt-1 text-sm font-medium text-zinc-900">{value}</div></div>; }
function GuideStep({ index, title, body }: { index: string; title: string; body: string }) { return <li className="flex gap-3 rounded-md border border-zinc-200 p-3"><span className="grid size-6 shrink-0 place-items-center rounded-full bg-blue-600 text-xs font-semibold text-white">{index}</span><div><div className="font-medium text-zinc-900">{title}</div><div className="mt-1 leading-5 text-zinc-600">{body}</div></div></li>; }
