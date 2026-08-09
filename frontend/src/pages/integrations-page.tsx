import { useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Clipboard,
  CloudUpload,
  Database,
  ExternalLink,
  KeyRound,
  Link2,
  LogOut,
  RefreshCw,
  SendHorizontal,
  Settings2,
  ShieldCheck,
  TableProperties,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  getFeishuConnectionApiV1IntegrationsFeishuConnectionGetQueryKey,
  getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey,
  useCreateFeishuWorkspaceApiV1IntegrationsFeishuWorkspacesPost,
  useDisconnectFeishuApiV1IntegrationsFeishuConnectionDelete,
  useFeishuConnectionApiV1IntegrationsFeishuConnectionGet,
  useFeishuSyncApiV1IntegrationsFeishuSyncPost,
  useListFeishuSyncsApiV1IntegrationsFeishuSyncsGet,
  useStartFeishuOauthApiV1IntegrationsFeishuOauthStartPost,
} from "@/api/generated/client";
import type { FeishuConnectionResponse } from "@/api/generated/models";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { datetime, errorMessage } from "@/lib/format";
import { resourceLabel, statusLabel } from "@/lib/labels";
import { statusTone } from "@/lib/status";

const resources = [
  "teachers",
  "class_groups",
  "rooms",
  "time_slots",
  "course_sessions",
  "rules",
  "schedule",
] as const;

const permissionLabels: Record<string, string> = {
  offline_access: "后台持续同步",
  "base:app:create": "创建多维表格",
  "base:app:read": "读取多维表格信息",
  "base:table:create": "创建数据表",
  "base:table:read": "读取数据表信息",
  "base:table:update": "修改数据表",
  "base:record:create": "新增记录",
  "base:record:retrieve": "读取记录",
  "base:record:update": "更新记录",
};

const environmentTemplate = `FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=请填写应用密钥
FEISHU_TOKEN_ENCRYPTION_KEY=请填写服务端生成的加密密钥
FEISHU_OAUTH_REDIRECT_URI=https://你的后端域名/api/v1/integrations/feishu/oauth/callback
FRONTEND_URL=https://你的前端域名`;

export function IntegrationsPage() {
  const queryClient = useQueryClient();
  const connection = useFeishuConnectionApiV1IntegrationsFeishuConnectionGet();
  const syncs = useListFeishuSyncsApiV1IntegrationsFeishuSyncsGet();
  const [resource, setResource] = useState<(typeof resources)[number]>("schedule");
  const [workspaceName, setWorkspaceName] = useState("途排智策 - 排课空间");
  const [guideOpen, setGuideOpen] = useState(false);
  const [guideStep, setGuideStep] = useState(0);
  const [copied, setCopied] = useState(false);

  const refresh = async () => {
    await Promise.all([connection.refetch(), syncs.refetch()]);
  };
  const refreshConnection = useCallback(async () => {
    await queryClient.invalidateQueries({
      queryKey: getFeishuConnectionApiV1IntegrationsFeishuConnectionGetQueryKey(),
    });
  }, [queryClient]);

  const authorize = useStartFeishuOauthApiV1IntegrationsFeishuOauthStartPost({
    mutation: {
      onSuccess: (data) => window.location.assign(data.authorization_url),
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  const createWorkspace = useCreateFeishuWorkspaceApiV1IntegrationsFeishuWorkspacesPost({
    mutation: {
      onSuccess: async () => {
        toast.success("排课多维表格和 7 张业务表已创建");
        await refreshConnection();
        setGuideStep(4);
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  const disconnect = useDisconnectFeishuApiV1IntegrationsFeishuConnectionDelete({
    mutation: {
      onSuccess: async () => {
        toast.success("飞书账号连接已解除");
        await refreshConnection();
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  const sync = useFeishuSyncApiV1IntegrationsFeishuSyncPost({
    mutation: {
      onSuccess: async (data) => {
        toast.success(`已同步 ${data.records_written} 条记录到飞书`);
        await queryClient.invalidateQueries({
          queryKey: getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey(),
        });
      },
      onError: async (error) => {
        toast.error(errorMessage(error));
        await queryClient.invalidateQueries({
          queryKey: getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey(),
        });
      },
    },
  });

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const result = params.get("feishu");
    if (result === "connected") toast.success("飞书管理员账号授权成功");
    if (result === "error") toast.error("飞书授权未完成，请重新发起授权");
    if (result === "cancelled") toast.info("已取消飞书授权");
    if (result) {
      window.history.replaceState({}, "", window.location.pathname);
      void refreshConnection();
    }
  }, [refreshConnection]);

  useEffect(() => {
    if (window.localStorage.getItem("tupai:feishu-guide-completed") !== "1") {
      setGuideOpen(true);
    }
  }, []);

  if (connection.isPending || syncs.isPending) return <LoadingState />;
  if (connection.isError || syncs.isError) return <ErrorState retry={() => void refresh()} />;

  const status = connection.data;
  const workspaceReady = Boolean(
    status.workspace?.status === "active" && status.workspace.tables?.length === resources.length,
  );
  const ready = Boolean(
    status.app_configured &&
      status.authorized &&
      status.missing_scopes.length === 0 &&
      workspaceReady,
  );
  const currentStep = !status.app_configured
    ? 0
    : !status.authorized
      ? 1
      : !workspaceReady
        ? 2
        : 3;

  const copyEnvironment = async () => {
    await navigator.clipboard.writeText(environmentTemplate);
    setCopied(true);
    toast.success("服务端配置模板已复制");
    window.setTimeout(() => setCopied(false), 1600);
  };
  const closeGuide = () => {
    setGuideOpen(false);
    window.localStorage.setItem("tupai:feishu-guide-completed", "1");
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="飞书集成"
        actions={
          <>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setGuideStep(0);
                setGuideOpen(true);
              }}
            >
              <Link2 className="size-3.5" />接入向导
            </Button>
            <Button size="sm" variant="outline" onClick={() => void refresh()}>
              <RefreshCw className="size-3.5" />刷新状态
            </Button>
          </>
        }
      >
        <p className="mt-1 text-sm text-zinc-500">
          管理员授权后，系统直接创建并管理排课多维表格，不需要复制任何表格标识。
        </p>
      </PageHeader>

      <ConnectionSummary status={status} ready={ready} />

      <section className="border-y border-zinc-200 bg-white">
        <div className="border-b border-zinc-200 px-5 py-4">
          <h2 className="text-sm font-semibold text-zinc-950">生产接入步骤</h2>
          <p className="mt-1 text-xs text-zinc-500">当前需要完成第 {currentStep + 1} 步</p>
        </div>
        <div className="divide-y divide-zinc-100">
          <FlowStep
            number={1}
            title="配置企业自建应用"
            description="部署人员一次性配置应用凭据、回调地址和用户身份权限。"
            state={status.app_configured ? "completed" : "current"}
            icon={Settings2}
          >
            {!status.app_configured ? (
              <DeploymentConfiguration
                status={status}
                copied={copied}
                copyEnvironment={() => void copyEnvironment()}
              />
            ) : (
              <p className="text-sm text-emerald-700">服务端应用配置已就绪。</p>
            )}
          </FlowStep>

          <FlowStep
            number={2}
            title="授权飞书管理员账号"
            description="授权后，多维表格归属该管理员并出现在其飞书云空间。"
            state={status.authorized ? "completed" : status.app_configured ? "current" : "pending"}
            icon={KeyRound}
          >
            {status.authorized ? (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="text-sm text-emerald-700">管理员账号已授权</span>
                  <span className="text-xs text-zinc-400">
                    访问令牌到期：{datetime(status.access_expires_at)}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      if (window.confirm("解除后需要重新授权，已创建的飞书表格仍会保留。确认解除？")) {
                        disconnect.mutate();
                      }
                    }}
                    disabled={disconnect.isPending}
                  >
                    <LogOut className="size-3.5" />解除连接
                  </Button>
                </div>
                {status.missing_scopes.length > 0 ? (
                  <PermissionWarning scopes={status.missing_scopes} />
                ) : null}
              </div>
            ) : (
              <div className="space-y-3">
                <Button
                  onClick={() => authorize.mutate()}
                  disabled={!status.app_configured || authorize.isPending}
                >
                  <ShieldCheck className="size-4" />
                  {authorize.isPending ? "正在跳转" : "授权飞书管理员账号"}
                </Button>
                {status.missing_scopes.length > 0 ? (
                  <PermissionWarning scopes={status.missing_scopes} />
                ) : null}
              </div>
            )}
          </FlowStep>

          <FlowStep
            number={3}
            title="创建排课多维表格"
            description="系统自动创建接入说明和 7 张中文业务表，并保存全部表格标识。"
            state={workspaceReady ? "completed" : status.authorized ? "current" : "pending"}
            icon={Database}
          >
            {workspaceReady && status.workspace ? (
              <WorkspaceDetails workspace={status.workspace} />
            ) : (
              <div className="flex max-w-xl flex-col gap-2 sm:flex-row">
                <input
                  aria-label="排课空间名称"
                  value={workspaceName}
                  onChange={(event) => setWorkspaceName(event.target.value)}
                  className="h-9 min-w-0 flex-1 rounded-md border border-zinc-300 px-3 text-sm outline-none focus:border-blue-500"
                  placeholder="例如：途排智策 - 2026 秋季学期"
                />
                <Button
                  onClick={() => createWorkspace.mutate({ data: { name: workspaceName.trim() } })}
                  disabled={
                    !status.authorized || workspaceName.trim().length < 2 || createWorkspace.isPending
                  }
                >
                  <TableProperties className="size-4" />
                  {createWorkspace.isPending ? "正在创建 7 张表" : "自动创建排课表格"}
                </Button>
              </div>
            )}
          </FlowStep>

          <FlowStep
            number={4}
            title="同步业务数据"
            description="按业务标识新增或更新，重复执行不会产生重复记录。"
            state={ready ? "current" : "pending"}
            icon={CloudUpload}
          >
            <div className="flex max-w-xl flex-col gap-2 sm:flex-row">
              <select
                aria-label="同步资源"
                value={resource}
                onChange={(event) => setResource(event.target.value as typeof resource)}
                className="h-9 min-w-0 flex-1 rounded-md border border-zinc-300 bg-white px-3 text-sm"
                disabled={!ready}
              >
                {resources.map((item) => (
                  <option key={item} value={item}>
                    {resourceLabel(item)}
                  </option>
                ))}
              </select>
              <Button
                onClick={() => sync.mutate({ data: { resource } })}
                disabled={!ready || sync.isPending}
              >
                <SendHorizontal className="size-4" />
                {sync.isPending ? "正在同步" : "同步到飞书"}
              </Button>
            </div>
            {!ready ? (
              <p className="mt-2 text-xs text-amber-700">完成账号授权和自动建表后开放同步。</p>
            ) : null}
          </FlowStep>
        </div>
      </section>

      <SyncHistory syncs={syncs.data ?? []} />

      <OnboardingDialog
        open={guideOpen}
        step={guideStep}
        status={status}
        workspaceName={workspaceName}
        setWorkspaceName={setWorkspaceName}
        authorize={() => authorize.mutate()}
        authorizing={authorize.isPending}
        createWorkspace={() => createWorkspace.mutate({ data: { name: workspaceName.trim() } })}
        creatingWorkspace={createWorkspace.isPending}
        copyEnvironment={() => void copyEnvironment()}
        copied={copied}
        close={closeGuide}
        previous={() => setGuideStep((value) => Math.max(0, value - 1))}
        next={() => {
          if (guideStep === 4) closeGuide();
          else setGuideStep((value) => value + 1);
        }}
        onOpenChange={(open) => (open ? setGuideOpen(true) : closeGuide())}
      />
    </div>
  );
}

function ConnectionSummary({
  status,
  ready,
}: {
  status: FeishuConnectionResponse;
  ready: boolean;
}) {
  const tone: BadgeTone = ready ? "green" : status.status === "reauthorization_required" ? "red" : "yellow";
  const label = ready
    ? "生产连接已就绪"
    : status.status === "unconfigured"
      ? "等待部署配置"
      : status.status === "not_authorized"
        ? "等待管理员授权"
        : status.status === "reauthorization_required"
          ? "需要重新授权"
          : "等待自动建表";
  return (
    <section className={`border p-5 ${ready ? "border-emerald-200 bg-emerald-50/50" : "border-amber-200 bg-amber-50/50"}`}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className={`mt-0.5 rounded-full p-2 ${ready ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
            {ready ? <CheckCircle2 className="size-5" /> : <Link2 className="size-5" />}
          </div>
          <div>
            <h2 className="font-semibold text-zinc-950">飞书生产连接</h2>
            <p className="mt-1 text-sm text-zinc-700">{status.message}</p>
          </div>
        </div>
        <Badge tone={tone}>{label}</Badge>
      </div>
      <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryItem label="应用配置" value={status.app_configured ? "已就绪" : "待完成"} />
        <SummaryItem label="管理员账号" value={status.authorized ? "已授权" : "待授权"} />
        <SummaryItem
          label="排课多维表格"
          value={status.workspace?.status === "active" ? "已创建" : "待创建"}
        />
        <SummaryItem
          label="业务数据表"
          value={`${status.workspace?.tables?.length ?? 0} / ${resources.length} 张`}
        />
      </div>
    </section>
  );
}

function FlowStep({
  number,
  title,
  description,
  state,
  icon: Icon,
  children,
}: {
  number: number;
  title: string;
  description: string;
  state: "completed" | "current" | "pending";
  icon: typeof Settings2;
  children: React.ReactNode;
}) {
  return (
    <div className={`grid gap-4 px-5 py-5 lg:grid-cols-[260px_1fr] ${state === "pending" ? "opacity-60" : ""}`}>
      <div className="flex gap-3">
        <div className={`grid size-8 shrink-0 place-items-center rounded-full ${state === "completed" ? "bg-emerald-100 text-emerald-700" : state === "current" ? "bg-blue-600 text-white" : "bg-zinc-100 text-zinc-400"}`}>
          {state === "completed" ? <CheckCircle2 className="size-4" /> : <span className="text-xs font-semibold">{number}</span>}
        </div>
        <div>
          <div className="flex items-center gap-2">
            <Icon className="size-4 text-zinc-500" />
            <h3 className="text-sm font-semibold text-zinc-900">{title}</h3>
          </div>
          <p className="mt-1 text-xs leading-5 text-zinc-500">{description}</p>
        </div>
      </div>
      <div className="min-w-0 lg:pt-1">{children}</div>
    </div>
  );
}

function DeploymentConfiguration({
  status,
  copied,
  copyEnvironment,
}: {
  status: FeishuConnectionResponse;
  copied: boolean;
  copyEnvironment: () => void;
}) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <a href={status.console_url} target="_blank" rel="noreferrer">
          <Button size="sm" variant="outline"><ExternalLink className="size-3.5" />打开飞书开放平台</Button>
        </a>
        <a href={status.docs_url} target="_blank" rel="noreferrer">
          <Button size="sm" variant="ghost"><ExternalLink className="size-3.5" />查看官方授权文档</Button>
        </a>
      </div>
      <ol className="grid gap-2 text-xs leading-5 text-zinc-700 md:grid-cols-3">
        <li className="border border-zinc-200 p-3"><strong>一、创建应用</strong><p className="mt-1 text-zinc-500">创建企业自建应用并记录应用编号与应用密钥。</p></li>
        <li className="border border-zinc-200 p-3"><strong>二、设置回调</strong><p className="mt-1 text-zinc-500">在安全设置中添加服务端授权回调地址。</p></li>
        <li className="border border-zinc-200 p-3"><strong>三、申请并发布</strong><p className="mt-1 text-zinc-500">申请下列用户身份权限，发布应用版本并覆盖管理员。</p></li>
      </ol>
      <PermissionList />
      <div className="rounded-md bg-zinc-950 p-4 text-xs text-zinc-100">
        <div className="mb-2 flex items-center justify-between gap-3">
          <span className="text-zinc-400">服务端配置文件</span>
          <Button size="sm" variant="secondary" onClick={copyEnvironment}>
            <Clipboard className="size-3.5" />{copied ? "已复制" : "复制配置"}
          </Button>
        </div>
        <pre className="overflow-x-auto whitespace-pre-wrap leading-5">{environmentTemplate}</pre>
      </div>
      <p className="text-xs text-zinc-500">
        加密密钥生成命令：<code>uv run python -c &quot;from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())&quot;</code>
      </p>
      {status.missing_fields.length > 0 ? (
        <p className="text-xs text-amber-700">保存配置并重启后端，然后点击页面右上角“刷新状态”。</p>
      ) : null}
    </div>
  );
}

function PermissionList() {
  return (
    <div>
      <div className="mb-2 text-xs font-medium text-zinc-700">需要申请的用户身份权限</div>
      <div className="grid gap-1.5 sm:grid-cols-2 xl:grid-cols-3">
        {Object.entries(permissionLabels).map(([scope, label]) => (
          <div key={scope} className="flex items-center justify-between gap-3 border border-zinc-200 px-3 py-2 text-xs">
            <span>{label}</span><code className="text-zinc-400">{scope}</code>
          </div>
        ))}
      </div>
    </div>
  );
}

function PermissionWarning({ scopes }: { scopes: string[] }) {
  return (
    <div className="border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
      尚缺权限：{scopes.map((scope) => permissionLabels[scope] ?? scope).join("、")}。请在开放平台补充权限并重新发布应用版本。
    </div>
  );
}

function WorkspaceDetails({ workspace }: { workspace: NonNullable<FeishuConnectionResponse["workspace"]> }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium text-zinc-900">{workspace.name}</span>
        <Badge tone="green">已创建</Badge>
        <a href={workspace.url} target="_blank" rel="noreferrer">
          <Button size="sm" variant="outline"><ExternalLink className="size-3.5" />打开多维表格</Button>
        </a>
      </div>
      <div className="flex flex-wrap gap-2">
        {workspace.tables?.map((table) => (
          <span key={table.resource} className="inline-flex items-center gap-1.5 rounded border border-zinc-200 bg-zinc-50 px-2 py-1 text-xs text-zinc-700">
            <CheckCircle2 className="size-3 text-emerald-600" />{table.table_name}
          </span>
        ))}
      </div>
    </div>
  );
}

function SyncHistory({ syncs }: { syncs: Array<{ id: string; created_at: string; resource: string; status: string; records_read: number; records_written: number; detail: Record<string, unknown> }> }) {
  return (
    <section className="border border-zinc-200 bg-white">
      <div className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold">飞书同步记录</div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="bg-zinc-50 text-xs text-zinc-500"><tr><th className="h-9 px-4">时间</th><th>业务数据</th><th>结果</th><th>飞书已有</th><th>新增</th><th>更新</th><th>本次写入</th></tr></thead>
          <tbody>
            {syncs.map((item) => (
              <tr key={item.id} className="border-t border-zinc-100">
                <td className="h-10 px-4 text-zinc-500">{datetime(item.created_at)}</td>
                <td>{resourceLabel(item.resource)}</td>
                <td><Badge tone={statusTone(item.status)}>{statusLabel(item.status)}</Badge></td>
                <td>{item.records_read}</td>
                <td>{detailNumber(item.detail, "records_created")}</td>
                <td>{detailNumber(item.detail, "records_updated")}</td>
                <td>{item.records_written}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {syncs.length === 0 ? <div className="p-6 text-center text-sm text-zinc-400">暂无飞书同步记录</div> : null}
    </section>
  );
}

function OnboardingDialog({
  open,
  step,
  status,
  workspaceName,
  setWorkspaceName,
  authorize,
  authorizing,
  createWorkspace,
  creatingWorkspace,
  copyEnvironment,
  copied,
  close,
  previous,
  next,
  onOpenChange,
}: {
  open: boolean;
  step: number;
  status: FeishuConnectionResponse;
  workspaceName: string;
  setWorkspaceName: (value: string) => void;
  authorize: () => void;
  authorizing: boolean;
  createWorkspace: () => void;
  creatingWorkspace: boolean;
  copyEnvironment: () => void;
  copied: boolean;
  close: () => void;
  previous: () => void;
  next: () => void;
  onOpenChange: (open: boolean) => void;
}) {
  const steps = useMemo(
    () => [
      { title: "飞书生产接入向导", description: "完成应用配置、管理员授权、自动建表和首次同步。" },
      { title: "配置企业自建应用", description: "这一步由部署人员完成一次，管理员无需接触应用密钥。" },
      { title: "授权飞书管理员账号", description: "多维表格将创建在授权管理员的飞书云空间。" },
      { title: "自动创建排课多维表格", description: "途排智策会直接创建接入说明和 7 张中文业务表。" },
      { title: "开始发布和同步", description: "发布课表后按业务标识同步，重复同步只更新原记录。" },
    ],
    [],
  );
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto p-0">
        <div className="border-b border-zinc-200 px-6 py-5">
          <div className="text-xs text-blue-700">第 {step + 1} / {steps.length} 步</div>
          <DialogTitle className="mt-2 text-lg font-semibold">{steps[step].title}</DialogTitle>
          <DialogDescription className="mt-1 text-sm text-zinc-500">{steps[step].description}</DialogDescription>
          <div className="mt-4 flex gap-1">
            {steps.map((item, index) => <span key={item.title} className={`h-1.5 flex-1 rounded-full ${index <= step ? "bg-blue-600" : "bg-zinc-200"}`} />)}
          </div>
        </div>
        <div className="min-h-64 px-6 py-5">
          {step === 0 ? <GuideWelcome /> : null}
          {step === 1 ? <DeploymentConfiguration status={status} copied={copied} copyEnvironment={copyEnvironment} /> : null}
          {step === 2 ? (
            <GuideAuthorize status={status} authorize={authorize} authorizing={authorizing} />
          ) : null}
          {step === 3 ? (
            <GuideWorkspace
              status={status}
              workspaceName={workspaceName}
              setWorkspaceName={setWorkspaceName}
              createWorkspace={createWorkspace}
              creatingWorkspace={creatingWorkspace}
            />
          ) : null}
          {step === 4 ? <GuideFinish status={status} /> : null}
        </div>
        <div className="flex items-center justify-between border-t border-zinc-200 px-6 py-4">
          <Button variant="ghost" onClick={close}>稍后继续</Button>
          <div className="flex gap-2">
            {step > 0 ? <Button variant="outline" onClick={previous}>上一步</Button> : null}
            <Button onClick={next}>{step === steps.length - 1 ? "完成向导" : "下一步"}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function GuideWelcome() {
  return (
    <div className="space-y-5">
      <div className="bg-blue-50 p-5 text-blue-950">
        <div className="font-semibold">管理员不需要手工创建任何飞书数据表。</div>
        <p className="mt-2 text-sm leading-6">部署人员配置应用后，管理员在途排智策授权账号并点击创建，系统会完成建表、字段配置和绑定保存。</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <GuideFact icon={Settings2} title="部署人员" text="配置应用与回调" />
        <GuideFact icon={ShieldCheck} title="教务管理员" text="授权飞书账号" />
        <GuideFact icon={Database} title="途排智策" text="自动建表并同步" />
      </div>
    </div>
  );
}

function GuideAuthorize({ status, authorize, authorizing }: { status: FeishuConnectionResponse; authorize: () => void; authorizing: boolean }) {
  if (status.authorized) return <GuideCompleted title="管理员账号已经授权" text="可以进入下一步创建排课多维表格。" />;
  return (
    <div className="space-y-4">
      <p className="text-sm leading-6 text-zinc-700">点击后将前往飞书授权页。确认权限后，飞书会自动返回途排智策。</p>
      <Button onClick={authorize} disabled={!status.app_configured || authorizing}>
        <ShieldCheck className="size-4" />{authorizing ? "正在跳转" : "授权飞书管理员账号"}
      </Button>
      {!status.app_configured ? <p className="text-xs text-amber-700">请先完成上一页的应用配置。</p> : null}
    </div>
  );
}

function GuideWorkspace({
  status,
  workspaceName,
  setWorkspaceName,
  createWorkspace,
  creatingWorkspace,
}: {
  status: FeishuConnectionResponse;
  workspaceName: string;
  setWorkspaceName: (value: string) => void;
  createWorkspace: () => void;
  creatingWorkspace: boolean;
}) {
  if (status.workspace?.status === "active") return <WorkspaceDetails workspace={status.workspace} />;
  return (
    <div className="space-y-4">
      {status.missing_scopes.length > 0 ? <PermissionWarning scopes={status.missing_scopes} /> : null}
      <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
        {resources.map((item) => <div key={item} className="border border-zinc-200 px-3 py-2">{resourceLabel(item)}</div>)}
      </div>
      <input aria-label="向导中的排课空间名称" value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} className="h-9 w-full rounded-md border border-zinc-300 px-3 text-sm" />
      <Button onClick={createWorkspace} disabled={!status.authorized || workspaceName.trim().length < 2 || creatingWorkspace}>
        <TableProperties className="size-4" />{creatingWorkspace ? "正在创建 7 张表" : "自动创建排课表格"}
      </Button>
      {!status.authorized ? <p className="text-xs text-amber-700">请先完成管理员账号授权。</p> : null}
    </div>
  );
}

function GuideFinish({ status }: { status: FeishuConnectionResponse }) {
  const ready = status.workspace?.status === "active" && (status.workspace.tables?.length ?? 0) === resources.length;
  return (
    <div className="space-y-4">
      <GuideCompleted
        title={ready ? "飞书接入已经就绪" : "接入步骤还未完成"}
        text={ready ? "先在“版本与回滚”发布课表，再回到飞书集成同步课表；主数据和规则也可以分别同步。" : "返回前面的步骤完成账号授权和自动建表。"}
      />
      {status.workspace?.url ? <a href={status.workspace.url} target="_blank" rel="noreferrer"><Button variant="outline"><ExternalLink className="size-4" />打开排课多维表格</Button></a> : null}
    </div>
  );
}

function GuideCompleted({ title, text }: { title: string; text: string }) {
  return <div className="bg-emerald-50 p-5 text-emerald-950"><div className="flex items-center gap-2 font-semibold"><CheckCircle2 className="size-5" />{title}</div><p className="mt-2 text-sm leading-6">{text}</p></div>;
}

function GuideFact({ icon: Icon, title, text }: { icon: typeof Settings2; title: string; text: string }) {
  return <div className="border border-zinc-200 p-4"><Icon className="size-4 text-blue-600" /><div className="mt-3 text-sm font-medium">{title}</div><div className="mt-1 text-xs text-zinc-500">{text}</div></div>;
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return <div className="border border-black/5 bg-white/70 p-3"><div className="text-xs text-zinc-500">{label}</div><div className="mt-1 text-sm font-medium text-zinc-900">{value}</div></div>;
}

function detailNumber(detail: Record<string, unknown>, key: string): number | string {
  const value = detail[key];
  return typeof value === "number" ? value : "-";
}
