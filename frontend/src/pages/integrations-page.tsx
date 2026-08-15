import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
  useConfigureFeishuAppApiV1IntegrationsFeishuAppConfigurationPost,
  useCreateFeishuWorkspaceApiV1IntegrationsFeishuWorkspacesPost,
  useDisconnectFeishuApiV1IntegrationsFeishuConnectionDelete,
  useFeishuConnectionApiV1IntegrationsFeishuConnectionGet,
  useFeishuSyncApiV1IntegrationsFeishuSyncPost,
  useListFeishuSyncsApiV1IntegrationsFeishuSyncsGet,
  useStartFeishuOauthApiV1IntegrationsFeishuOauthStartPost,
} from "@/api/generated/client";
import type { FeishuConnectionResponse } from "@/api/generated/models";
import { API_BASE_URL, http } from "@/api/http";
import { ConfirmDialog } from "@/components/confirm-dialog";
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
  "public_summary",
] as const;

const AILY_SKILL_DOC_URL = "https://open.feishu.cn/document/aily-v1/app-skill/start?lang=zh-CN";

interface AIConfiguration {
  configured: boolean;
  source: "environment" | "frontend" | "none";
  provider: string | null;
  base_url: string | null;
  api_key_configured: boolean;
  model: string | null;
}

const permissionLabels: Record<string, string> = {
  offline_access: "持续访问已授权的数据",
  "base:app:create": "创建多维表格",
  "base:app:read": "获取多维表格信息",
  "base:table:create": "新增数据表",
  "base:table:read": "获取数据表信息",
  "base:table:update": "更新数据表",
  "base:record:create": "新增记录",
  "base:record:retrieve": "根据条件搜索记录",
  "base:record:update": "更新记录",
};

export function IntegrationsPage() {
  const queryClient = useQueryClient();
  const connection = useFeishuConnectionApiV1IntegrationsFeishuConnectionGet();
  const syncs = useListFeishuSyncsApiV1IntegrationsFeishuSyncsGet();
  const aiConfiguration = useQuery({
    queryKey: ["ai-provider-configuration"],
    queryFn: async () => (await http.get<AIConfiguration>("/api/v1/integrations/ai/configuration")).data,
  });
  const [resource, setResource] = useState<(typeof resources)[number]>("schedule");
  const [workspaceName, setWorkspaceName] = useState("途排智策 - 排课空间");
  const [guideOpen, setGuideOpen] = useState(false);
  const [guideStep, setGuideStep] = useState(0);
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [ailyAppId, setAilyAppId] = useState("");
  const [ailySkillId, setAilySkillId] = useState("");
  const [redirectUri, setRedirectUri] = useState(
    `${API_BASE_URL.replace(/\/$/, "")}/api/v1/integrations/feishu/oauth/callback`,
  );
  const [frontendUrl, setFrontendUrl] = useState(window.location.origin);
  const [editingApp, setEditingApp] = useState(false);
  const [copiedCallback, setCopiedCallback] = useState(false);
  const [disconnectConfirmOpen, setDisconnectConfirmOpen] = useState(false);
  const [aiBaseUrl, setAiBaseUrl] = useState("");
  const [aiApiKey, setAiApiKey] = useState("");
  const [aiModel, setAiModel] = useState("");
  const [editingAI, setEditingAI] = useState(false);

  const refresh = async () => {
    await Promise.all([connection.refetch(), syncs.refetch(), aiConfiguration.refetch()]);
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
  const configureApp = useConfigureFeishuAppApiV1IntegrationsFeishuAppConfigurationPost({
    mutation: {
      onSuccess: async () => {
        setAppSecret("");
        setEditingApp(false);
        toast.success("飞书应用配置已加密保存");
        await refreshConnection();
        setGuideStep(2);
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  const createWorkspace = useCreateFeishuWorkspaceApiV1IntegrationsFeishuWorkspacesPost({
    mutation: {
      onSuccess: async () => {
        toast.success(`排课多维表格和 ${resources.length} 张业务表已创建`);
        await refreshConnection();
        setGuideStep(4);
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  const disconnect = useDisconnectFeishuApiV1IntegrationsFeishuConnectionDelete({
    mutation: {
      onSuccess: async () => {
        setDisconnectConfirmOpen(false);
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
  const configureAI = useMutation({
    mutationFn: async () => (await http.post<AIConfiguration>("/api/v1/integrations/ai/configuration", {
      provider: "openai_compatible",
      base_url: aiBaseUrl.trim(),
      api_key: aiApiKey || null,
      model: aiModel.trim(),
    })).data,
    onSuccess: (configured) => {
      setAiApiKey("");
      setEditingAI(false);
      setAiBaseUrl(configured.base_url ?? "");
      setAiModel(configured.model ?? "");
      void queryClient.invalidateQueries({ queryKey: ["ai-provider-configuration"] });
      toast.success("一句话排课 AI 已配置");
    },
    onError: (error) => toast.error(errorMessage(error)),
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

  useEffect(() => {
    if (!connection.data) return;
    const configured = connection.data.app_configuration;
    if (configured.configured) {
      if (configured.app_id) setAppId(configured.app_id);
      if (configured.aily_app_id) setAilyAppId(configured.aily_app_id);
      if (configured.aily_skill_id) setAilySkillId(configured.aily_skill_id);
      setRedirectUri(configured.oauth_redirect_uri);
      setFrontendUrl(configured.frontend_url);
      if (new URLSearchParams(window.location.search).get("section") === "aily") {
        setEditingApp(true);
        window.requestAnimationFrame(() => {
          document.getElementById("aily-configuration")?.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      }
      return;
    }
    setRedirectUri(
      `${API_BASE_URL.replace(/\/$/, "")}/api/v1/integrations/feishu/oauth/callback`,
    );
    setFrontendUrl(window.location.origin);
  }, [connection.data]);

  useEffect(() => {
    if (!aiConfiguration.data) return;
    setAiBaseUrl(aiConfiguration.data.base_url ?? "");
    setAiModel(aiConfiguration.data.model ?? "");
    if (new URLSearchParams(window.location.search).get("section") === "ai") {
      setEditingAI(true);
      window.requestAnimationFrame(() => {
        document.getElementById("ai-configuration")?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
  }, [aiConfiguration.data]);

  if (connection.isPending || syncs.isPending || aiConfiguration.isPending) return <LoadingState />;
  if (connection.isError || syncs.isError || aiConfiguration.isError || !connection.data || !aiConfiguration.data) return <ErrorState retry={() => void refresh()} />;

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
  const syncBlockers = [
    !status.app_configured ? "还没有保存企业自建应用配置" : null,
    !status.authorized ? "还没有授权飞书管理员账号" : null,
    status.missing_scopes.length > 0 ? `还缺少 ${status.missing_scopes.length} 项飞书权限（请在第 3 步查看）` : null,
    !workspaceReady
      ? status.workspace?.status === "error"
        ? `排课多维表格创建失败：${status.workspace.last_error ?? "请重试第 4 步"}`
        : `排课多维表格尚未完成（当前 ${status.workspace?.tables?.length ?? 0} / ${resources.length} 张业务表）`
      : null,
  ].filter((item): item is string => Boolean(item));
  const currentStep = !status.app_configured
    ? 0
    : !aiConfiguration.data.configured
      ? 1
    : !status.authorized
      ? 2
      : !workspaceReady
        ? 3
        : 4;

  const copyCallback = async () => {
    await navigator.clipboard.writeText(redirectUri);
    setCopiedCallback(true);
    toast.success("授权回调地址已复制");
    window.setTimeout(() => setCopiedCallback(false), 1600);
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

      <ConnectionSummary status={status} ready={ready} ai={aiConfiguration.data} />

      <section className="border-y border-zinc-200 bg-white">
        <div className="border-b border-zinc-200 px-5 py-4">
          <h2 className="text-sm font-semibold text-zinc-950">生产接入步骤</h2>
          <p className="mt-1 text-xs text-zinc-500">当前需要完成第 {currentStep + 1} 步</p>
        </div>
        <div className="divide-y divide-zinc-100">
          <FlowStep
            number={1}
            title="配置企业自建应用"
            description="管理员填写飞书企业自建应用编号和应用密钥，后端自动加密保存。"
            state={status.app_configured ? "completed" : "current"}
            icon={Settings2}
          >
            <ApplicationConfiguration
              status={status}
              appId={appId}
              setAppId={setAppId}
              appSecret={appSecret}
              setAppSecret={setAppSecret}
              ailyAppId={ailyAppId}
              setAilyAppId={setAilyAppId}
              ailySkillId={ailySkillId}
              setAilySkillId={setAilySkillId}
              redirectUri={redirectUri}
              editing={editingApp}
              setEditing={setEditingApp}
              saving={configureApp.isPending}
              save={() =>
                configureApp.mutate({
                  data: {
                    app_id: appId.trim(),
                    app_secret: appSecret,
                    oauth_redirect_uri: redirectUri.trim(),
                    frontend_url: frontendUrl.trim(),
                    aily_app_id: ailyAppId.trim(),
                    aily_skill_id: ailySkillId.trim(),
                  },
                })
              }
              copiedCallback={copiedCallback}
              copyCallback={() => void copyCallback()}
            />
          </FlowStep>

          <FlowStep
            number={2}
            title="配置一句话排课 AI"
            description="AI 负责理解教务人员的自然语言，CP-SAT 负责执行确定性排课。"
            state={aiConfiguration.data.configured ? "completed" : "current"}
            icon={Settings2}
          >
            <AIConfigurationPanel
              configuration={aiConfiguration.data}
              baseUrl={aiBaseUrl}
              setBaseUrl={setAiBaseUrl}
              apiKey={aiApiKey}
              setApiKey={setAiApiKey}
              model={aiModel}
              setModel={setAiModel}
              editing={editingAI}
              setEditing={setEditingAI}
              saving={configureAI.isPending}
              save={() => configureAI.mutate()}
            />
          </FlowStep>

          <FlowStep
            number={3}
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
                    onClick={() => setDisconnectConfirmOpen(true)}
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
            number={4}
            title="创建排课多维表格"
            description="系统会为当前课表方案创建独立的内部业务表和脱敏公开汇总表，并保存全部表格标识。"
            state={workspaceReady ? "completed" : status.authorized ? "current" : "pending"}
            icon={Database}
          >
            {workspaceReady && status.workspace ? (
              <WorkspaceDetails workspace={status.workspace} />
            ) : (
              <div className="flex max-w-xl flex-col gap-2 sm:flex-row">
                <input
                  aria-label="多维表格基础名称"
                  value={workspaceName}
                  onChange={(event) => setWorkspaceName(event.target.value)}
                  className="h-9 min-w-0 flex-1 rounded-md border border-zinc-300 px-3 text-sm outline-none focus:border-blue-500"
                  placeholder="例如：排课协同"
                />
                <Button
                  onClick={() => createWorkspace.mutate({ data: { name: workspaceName.trim() } })}
                  disabled={
                    !status.authorized || workspaceName.trim().length < 2 || createWorkspace.isPending
                  }
                >
                  <TableProperties className="size-4" />
                  {createWorkspace.isPending ? `正在创建 ${resources.length} 张表` : "自动创建排课表格"}
                </Button>
                <p className="text-xs leading-5 text-zinc-500 sm:col-span-2">创建时会自动加上当前课表方案名称，因此第 1、2、3……N 套课表会绑定到不同的飞书多维表格。</p>
              </div>
            )}
          </FlowStep>

          <FlowStep
            number={5}
            title="同步业务数据"
            description="发布不会自动写入飞书；在这里选择资源后，按业务标识新增或更新，重复执行不会产生重复记录。"
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
              {status.workspace?.url ? <a href={status.workspace.url} target="_blank" rel="noreferrer"><Button type="button" variant="outline"><ExternalLink className="size-4" />打开当前多维表格</Button></a> : null}
            </div>
            {!ready ? (
              <div className="mt-3 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                <div className="font-medium">同步暂不可用，原因是：</div>
                <ul className="mt-1 list-disc pl-4">{syncBlockers.map((item) => <li key={item}>{item}</li>)}</ul>
              </div>
            ) : <p className="mt-2 text-xs text-emerald-700">连接已就绪，可以同步课表、主数据和规则。</p>}
          </FlowStep>

          <FlowStep
            number={6}
            title="在飞书内创建妙搭应用"
            description="先同步脱敏公开汇总，再打开飞书多维表格，在飞书内部使用妙搭搭建和发布页面。"
            state={workspaceReady ? "current" : "pending"}
            icon={ExternalLink}
          >
            <div className="space-y-3 text-sm text-zinc-700">
              <p>此处只负责把“公开展示汇总”写入飞书多维表格；妙搭的创建、搭建和公网发布均在飞书内部完成。</p>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  onClick={() => {
                    setResource("public_summary");
                    sync.mutate({ data: { resource: "public_summary" } });
                  }}
                  disabled={!ready || sync.isPending}
                >
                  同步公开展示汇总
                </Button>
                {status.workspace?.url ? <a href={status.workspace.url} target="_blank" rel="noreferrer"><Button variant="outline"><ExternalLink className="size-4" />打开飞书多维表格</Button></a> : null}
              </div>
            </div>
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
        appId={appId}
        setAppId={setAppId}
        appSecret={appSecret}
        setAppSecret={setAppSecret}
        ailyAppId={ailyAppId}
        setAilyAppId={setAilyAppId}
        ailySkillId={ailySkillId}
        setAilySkillId={setAilySkillId}
        redirectUri={redirectUri}
        editingApp={editingApp}
        setEditingApp={setEditingApp}
        savingApp={configureApp.isPending}
        saveApp={() =>
          configureApp.mutate({
            data: {
              app_id: appId.trim(),
              app_secret: appSecret,
              oauth_redirect_uri: redirectUri.trim(),
              frontend_url: frontendUrl.trim(),
              aily_app_id: ailyAppId.trim(),
              aily_skill_id: ailySkillId.trim(),
            },
          })
        }
        copiedCallback={copiedCallback}
        copyCallback={() => void copyCallback()}
        close={closeGuide}
        previous={() => setGuideStep((value) => Math.max(0, value - 1))}
        next={() => {
          if (guideStep === 4) closeGuide();
          else setGuideStep((value) => value + 1);
        }}
        onOpenChange={(open) => (open ? setGuideOpen(true) : closeGuide())}
      />
      <ConfirmDialog
        open={disconnectConfirmOpen}
        title="解除飞书连接"
        description="解除后需要重新授权；已创建的飞书多维表格和其中的数据仍会保留。"
        confirmLabel="确认解除"
        danger
        pending={disconnect.isPending}
        onOpenChange={setDisconnectConfirmOpen}
        onConfirm={() => disconnect.mutate()}
      />
    </div>
  );
}

function AIConfigurationPanel({
  configuration,
  baseUrl,
  setBaseUrl,
  apiKey,
  setApiKey,
  model,
  setModel,
  editing,
  setEditing,
  saving,
  save,
}: {
  configuration: AIConfiguration;
  baseUrl: string;
  setBaseUrl: (value: string) => void;
  apiKey: string;
  setApiKey: (value: string) => void;
  model: string;
  setModel: (value: string) => void;
  editing: boolean;
  setEditing: (value: boolean) => void;
  saving: boolean;
  save: () => void;
}) {
  const environmentManaged = configuration.source === "environment";
  if (configuration.configured && !editing) {
    return <div id="ai-configuration" className="space-y-3"><div className="flex flex-wrap items-center gap-3"><span className="text-sm font-medium text-emerald-700">自然语言 AI 已接入</span><Badge tone="green">API Key 已加密</Badge><span className="font-mono text-xs text-zinc-500">{configuration.model}</span></div><div className="break-all text-xs leading-5 text-zinc-500">OpenAI-compatible 接口：{configuration.base_url}</div><p className="text-xs leading-5 text-zinc-500">前端的一句话会先交给该模型解析为业务范围、日期窗口和约束，再由教务确认并启动 CP-SAT；普通飞书应用继续负责多维表格、日历和妙搭数据链路。</p>{environmentManaged ? <p className="text-xs text-zinc-500">当前配置由部署环境统一管理。</p> : <Button size="sm" variant="outline" onClick={() => setEditing(true)}><Settings2 className="size-3.5" />更新 AI 配置</Button>}</div>;
  }
  return <div id="ai-configuration" className="space-y-4"><div className="border border-blue-200 bg-blue-50/60 p-4 text-sm text-blue-950"><div className="font-semibold">Aily 标识已从必填项移除</div><p className="mt-2 text-xs leading-5 text-blue-900/75">这里使用标准 OpenAI-compatible 模型接口，可接入豆包 Ark、DeepSeek 或企业已有模型网关。仅需要接口地址、API Key 和模型名称；飞书自建应用的 <code>cli_...</code> 继续用于飞书数据和日历授权。</p></div><div className="grid gap-4 sm:grid-cols-2"><label className="text-sm text-zinc-700 sm:col-span-2">接口地址<input aria-label="AI 接口地址" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://provider.example/v1" className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-3 font-mono text-sm outline-none focus:border-blue-500" /><span className="mt-1 block text-xs leading-5 text-zinc-500">填写到版本根路径，系统会调用其 <code>/chat/completions</code>。</span></label><label className="text-sm text-zinc-700">模型名称或接入点 ID<input aria-label="AI 模型名称" value={model} onChange={(event) => setModel(event.target.value)} placeholder="例如：deepseek-chat 或 ep-..." className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-3 font-mono text-sm outline-none focus:border-blue-500" /></label><label className="text-sm text-zinc-700">API Key<input aria-label="AI API Key" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={configuration.api_key_configured ? "留空则继续使用已保存密钥" : "填写模型平台 API Key"} autoComplete="new-password" className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-3 text-sm outline-none focus:border-blue-500" /></label></div><div className="flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-4"><Button onClick={save} disabled={saving || !/^https?:\/\//.test(baseUrl.trim()) || !model.trim() || (!configuration.api_key_configured && apiKey.length < 8) || (apiKey.length > 0 && apiKey.length < 8)}><ShieldCheck className="size-4" />{saving ? "正在加密保存" : "保存 AI 配置"}</Button>{configuration.configured ? <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>取消</Button> : null}<span className="text-xs text-zinc-500">API Key 只提交给本地后端并加密保存，页面不会回显。</span></div></div>;
}

function ConnectionSummary({
  status,
  ready,
  ai,
}: {
  status: FeishuConnectionResponse;
  ready: boolean;
  ai: AIConfiguration;
}) {
  const tone: BadgeTone = ready ? "green" : status.status === "reauthorization_required" ? "red" : "yellow";
  const label = ready
    ? "生产连接已就绪"
    : status.status === "unconfigured"
      ? "等待应用配置"
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
      <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <SummaryItem label="应用配置" value={status.app_configured ? "已就绪" : "待完成"} />
        <SummaryItem label="一句话排课 AI" value={ai.configured ? ai.model ?? "已配置" : "待配置"} />
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

function ApplicationConfiguration({
  status,
  appId,
  setAppId,
  appSecret,
  setAppSecret,
  ailyAppId,
  setAilyAppId,
  ailySkillId,
  setAilySkillId,
  redirectUri,
  editing,
  setEditing,
  saving,
  save,
  copiedCallback,
  copyCallback,
}: {
  status: FeishuConnectionResponse;
  appId: string;
  setAppId: (value: string) => void;
  appSecret: string;
  setAppSecret: (value: string) => void;
  ailyAppId: string;
  setAilyAppId: (value: string) => void;
  ailySkillId: string;
  setAilySkillId: (value: string) => void;
  redirectUri: string;
  editing: boolean;
  setEditing: (value: boolean) => void;
  saving: boolean;
  save: () => void;
  copiedCallback: boolean;
  copyCallback: () => void;
}) {
  const configured = status.app_configuration.configured;
  const environmentManaged = status.app_configuration.source === "environment";
  if (configured && !editing) {
    return (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm font-medium text-emerald-700">飞书应用已配置</span>
          <Badge tone="green">应用密钥已加密</Badge>
          <span className="font-mono text-xs text-zinc-500">
            {status.app_configuration.app_id}
          </span>
        </div>
        <div className="text-xs leading-5 text-zinc-500">
          授权回调地址：{status.app_configuration.oauth_redirect_uri}
        </div>
        <div className="text-xs leading-5 text-zinc-500">
          Aily Workflow 高级通道：{status.app_configuration.aily_configured
            ? `${status.app_configuration.aily_app_id} / ${status.app_configuration.aily_skill_id}`
            : "未启用（不影响一句话排课）"}
        </div>
        {environmentManaged ? (
          <p className="text-xs text-zinc-500">当前配置由部署环境统一管理。</p>
        ) : (
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
            <Settings2 className="size-3.5" />更新应用配置
          </Button>
        )}
      </div>
    );
  }
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
        <li className="border border-zinc-200 p-3"><strong>二、设置回调</strong><p className="mt-1 text-zinc-500">复制下方回调地址，添加到飞书应用安全设置。</p></li>
        <li className="border border-zinc-200 p-3"><strong>三、申请并发布</strong><p className="mt-1 text-zinc-500">申请下列用户身份权限，发布版本并覆盖管理员。</p></li>
      </ol>
      <p className="text-sm text-zinc-700">
        在途排智策填写普通飞书企业自建应用凭据。该应用负责多维表格、日历和 OAuth 授权；AI 模型在下一步单独配置。
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="text-sm text-zinc-700">
          应用编号
          <input
            aria-label="飞书应用编号"
            value={appId}
            onChange={(event) => setAppId(event.target.value)}
            placeholder="cli_xxxxxxxxxx"
            autoComplete="off"
            className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-3 font-mono text-sm outline-none focus:border-blue-500"
          />
        </label>
        <label className="text-sm text-zinc-700">
          应用密钥
          <input
            aria-label="飞书应用密钥"
            type="password"
            value={appSecret}
            onChange={(event) => setAppSecret(event.target.value)}
            placeholder={configured ? "重新输入应用密钥" : "从飞书开放平台复制"}
            autoComplete="new-password"
            className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-3 text-sm outline-none focus:border-blue-500"
          />
        </label>
      </div>
      <details className="border border-zinc-200 bg-zinc-50 p-4">
        <summary className="cursor-pointer text-sm font-medium text-zinc-700">Aily Workflow 高级接入（可选）</summary>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <label className="text-sm text-zinc-700">Aily 应用标识<input aria-label="飞书 Aily 应用标识" value={ailyAppId} onChange={(event) => setAilyAppId(event.target.value)} placeholder="spring_xxxxxxxxxx" className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-3 font-mono text-sm outline-none focus:border-blue-500" /></label>
          <label className="text-sm text-zinc-700">Aily 技能标识<input aria-label="飞书 Aily 技能标识" value={ailySkillId} onChange={(event) => setAilySkillId(event.target.value)} placeholder="skill_xxxxxxxxxx" className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-3 font-mono text-sm outline-none focus:border-blue-500" /></label>
        </div>
        <AilyConfigurationGuide />
      </details>
      <label className="block text-sm text-zinc-700">
        授权回调地址
        <div className="mt-1.5 flex gap-2">
          <input
            aria-label="飞书授权回调地址"
            value={redirectUri}
            readOnly
            className="h-9 min-w-0 flex-1 rounded-md border border-zinc-300 bg-zinc-50 px-3 font-mono text-xs text-zinc-600"
          />
          <Button size="sm" variant="outline" onClick={copyCallback}>
            <Clipboard className="size-3.5" />{copiedCallback ? "已复制" : "复制"}
          </Button>
        </div>
      </label>
      <PermissionList />
      <div className="flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-4">
        <Button
          onClick={save}
          disabled={
            saving ||
            !appId.trim().startsWith("cli_") ||
            (!configured && appSecret.length < 8) ||
            (configured && appSecret.length > 0 && appSecret.length < 8) ||
            (Boolean(ailyAppId.trim() || ailySkillId.trim()) &&
              (!ailyAppId.trim().startsWith("spring_") || !ailySkillId.trim().startsWith("skill_"))) ||
            !redirectUri.trim()
          }
        >
          <ShieldCheck className="size-4" />
          {saving ? "正在加密保存" : "保存应用配置"}
        </Button>
        {configured ? (
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
            取消
          </Button>
        ) : null}
        <span className="text-xs text-zinc-500">
          应用密钥与用户令牌由后端自动加密，保存后页面不再显示明文。
        </span>
      </div>
    </div>
  );
}

function AilyConfigurationGuide() {
  return (
    <div className="mt-4 border border-blue-200 bg-blue-50/60 p-4 text-sm text-blue-950">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="font-semibold">普通应用 ID 与 Aily App ID 的区别</div>
        <a href={AILY_SKILL_DOC_URL} target="_blank" rel="noreferrer">
          <Button size="sm" variant="outline"><ExternalLink className="size-3.5" />打开 Aily 技能调用官方文档</Button>
        </a>
      </div>
      <div className="mt-3 space-y-2 text-xs leading-5 text-blue-900/80"><p><code>cli_...</code> 是飞书开放平台自建应用，用来换取访问凭证。用户提供的 Session / Message / Run 文档里，真正触发 AI 的 Run 仍要绑定 Aily App，文档中的 <code>app_id</code> 指 <code>spring_...__c</code>，并非 <code>cli_...</code>。</p><p>该文档允许省略 <code>skill_id</code>，由 Aily 自己选择技能；它并未省略 Aily App ID。本项目现在用独立 AI 模型完成一句话理解，因此这里可以保持为空。</p></div>
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
  appId,
  setAppId,
  appSecret,
  setAppSecret,
  ailyAppId,
  setAilyAppId,
  ailySkillId,
  setAilySkillId,
  redirectUri,
  editingApp,
  setEditingApp,
  savingApp,
  saveApp,
  copiedCallback,
  copyCallback,
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
  appId: string;
  setAppId: (value: string) => void;
  appSecret: string;
  setAppSecret: (value: string) => void;
  ailyAppId: string;
  setAilyAppId: (value: string) => void;
  ailySkillId: string;
  setAilySkillId: (value: string) => void;
  redirectUri: string;
  editingApp: boolean;
  setEditingApp: (value: boolean) => void;
  savingApp: boolean;
  saveApp: () => void;
  copiedCallback: boolean;
  copyCallback: () => void;
  close: () => void;
  previous: () => void;
  next: () => void;
  onOpenChange: (open: boolean) => void;
}) {
  const steps = useMemo(
    () => [
      { title: "飞书生产接入向导", description: "完成应用配置、管理员授权、自动建表和首次同步。" },
      { title: "配置企业自建应用", description: "管理员在这里一次填写应用编号和应用密钥。" },
      { title: "授权飞书管理员账号", description: "多维表格将创建在授权管理员的飞书云空间。" },
      { title: "自动创建排课多维表格", description: `途排智策会直接创建接入说明和 ${resources.length} 张中文业务表。` },
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
          {step === 1 ? (
            <ApplicationConfiguration
              status={status}
              appId={appId}
              setAppId={setAppId}
              appSecret={appSecret}
              setAppSecret={setAppSecret}
              ailyAppId={ailyAppId}
              setAilyAppId={setAilyAppId}
              ailySkillId={ailySkillId}
              setAilySkillId={setAilySkillId}
              redirectUri={redirectUri}
              editing={editingApp}
              setEditing={setEditingApp}
              saving={savingApp}
              save={saveApp}
              copiedCallback={copiedCallback}
              copyCallback={copyCallback}
            />
          ) : null}
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
        <p className="mt-2 text-sm leading-6">管理员先在途排智策填写一次飞书应用编号和应用密钥，再授权账号并点击创建，系统会完成建表、字段配置和绑定保存。</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <GuideFact icon={Settings2} title="应用配置" text="前端一次填写凭据" />
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
        <TableProperties className="size-4" />{creatingWorkspace ? `正在创建 ${resources.length} 张表` : "自动创建排课表格"}
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
