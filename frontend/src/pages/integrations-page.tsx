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
  useFeishuSyncBatchApiV1IntegrationsFeishuSyncBatchPost,
  useFeishuConnectionApiV1IntegrationsFeishuConnectionGet,
  useFeishuSyncApiV1IntegrationsFeishuSyncPost,
  useListFeishuSyncsApiV1IntegrationsFeishuSyncsGet,
  useStartFeishuOauthApiV1IntegrationsFeishuOauthStartPost,
} from "@/api/generated/client";
import type { FeishuBatchSyncResponse, FeishuConnectionResponse } from "@/api/generated/models";
import { API_BASE_URL, http } from "@/api/http";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
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
  "public_adjustment_notice",
  "public_class_links",
] as const;

const publicDisplayResources = [
  "public_summary",
  "public_adjustment_notice",
  "public_class_links",
] as const;

// 第 5 步只写飞书多维表格；日历和 Aily 权限缺失不应把同步按钮置灰。
const bitableSyncScopes = [
  "base:table:read",
  "base:field:read",
  "base:field:create",
  "bitable:app:readonly",
  "base:record:create",
  "base:record:retrieve",
  "base:record:update",
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
  "base:field:read": "读取数据表字段",
  "base:field:create": "新增数据表字段",
  "bitable:app": "管理多维表格应用与字段（可替代细分权限）",
  "bitable:app:readonly": "读取多维表格应用与字段",
  "base:view:write_only": "创建和更新班级筛选视图（可选）",
  "base:record:create": "新增记录",
  "base:record:retrieve": "根据条件搜索记录",
  "base:record:update": "更新记录",
  "base:record:delete": "清理系统识别出的重复生成记录",
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
  const [batchResult, setBatchResult] = useState<FeishuBatchSyncResponse | null>(null);
  const [syncReauthorizationPrompt, setSyncReauthorizationPrompt] = useState(false);

  const refresh = async () => {
    await Promise.all([connection.refetch(), syncs.refetch(), aiConfiguration.refetch()]);
  };
  const refreshConnection = useCallback(async () => {
    await queryClient.invalidateQueries({
      queryKey: getFeishuConnectionApiV1IntegrationsFeishuConnectionGetQueryKey(),
    });
  }, [queryClient]);
  const handleFeishuActionError = useCallback(async (error: unknown) => {
    const message = errorMessage(error);
    toast.error(message);
    if (requiresFeishuReauthorization(message)) {
      setSyncReauthorizationPrompt(true);
    }
    await refreshConnection();
  }, [refreshConnection]);

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
      onError: handleFeishuActionError,
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
        const skipped = detailNumberValue(data.detail, "records_skipped");
        toast.success(skipped
          ? `已写入 ${data.records_written} 条，跳过 ${skipped} 条未变更记录`
          : `已同步 ${data.records_written} 条记录到飞书`);
        await queryClient.invalidateQueries({
          queryKey: getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey(),
        });
      },
      onError: async (error) => {
        await handleFeishuActionError(error);
        await queryClient.invalidateQueries({
          queryKey: getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey(),
        });
      },
    },
  });
  const batchSync = useFeishuSyncBatchApiV1IntegrationsFeishuSyncBatchPost({
    mutation: {
      onSuccess: async (data) => {
        setBatchResult(data);
        if (batchRequiresFeishuReauthorization(data)) {
          setSyncReauthorizationPrompt(true);
        }
        const skipped = batchSkippedCount(data);
        if (data.failed_count === 0) {
          toast.success(skipped
            ? `当前方案已写入 ${data.records_written} 条，跳过 ${skipped} 条未变更记录`
            : `当前方案的 ${data.completed_count} 类数据已同步（${data.records_written} 条）`);
        } else {
          toast.warning(`一键同步完成：${data.completed_count} 类成功，${data.failed_count} 类待重试`);
        }
        await queryClient.invalidateQueries({
          queryKey: getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey(),
        });
        await refreshConnection();
      },
      onError: async (error) => {
        await handleFeishuActionError(error);
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
    if (result === "reauthorization_required") toast.warning("飞书授权未包含最新权限，请在开放平台发布版本后重新授权");
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
    if (connection.data.workspace?.name) setWorkspaceName(connection.data.workspace.name);
    if (configured?.configured) {
      if (configured.app_id) setAppId(configured.app_id);
      if (configured.aily_app_id) setAilyAppId(configured.aily_app_id);
      if (configured.aily_skill_id) setAilySkillId(configured.aily_skill_id);
      setRedirectUri(configured.oauth_redirect_uri || "");
      setFrontendUrl(configured.frontend_url || "");
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

  // 顶栏切换课表方案会重新获取连接状态；不要把上一套方案的一键结果留在当前页。
  useEffect(() => {
    setBatchResult(null);
    setSyncReauthorizationPrompt(false);
  }, [connection.data?.workspace?.id]);

  useEffect(() => {
    if (connection.data?.status !== "reauthorization_required") {
      setSyncReauthorizationPrompt(false);
    }
  }, [connection.data?.status]);

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
  const grantedScopes = Array.isArray(status?.granted_scopes) ? status.granted_scopes : [];
  const missingScopes = Array.isArray(status?.missing_scopes) ? status.missing_scopes : [];
  const workspaceReady = Boolean(
    status?.workspace?.status === "active" &&
      Array.isArray(status.workspace.tables) &&
      status.workspace.tables.length === resources.length,
  );
  const hasFullBitableAppScope = grantedScopes.includes("bitable:app");
  const missingBitableSyncScopes = bitableSyncScopes.filter(
    (scope) => hasFullBitableAppScope
      ? false
      : scope === "bitable:app:readonly"
      ? !grantedScopes.some((item) => item === "bitable:app" || item === "bitable:app:readonly")
      : !grantedScopes.includes(scope),
  );
  const ready = Boolean(
    status?.app_configured &&
      status?.authorized &&
      missingBitableSyncScopes.length === 0 &&
      workspaceReady,
  );
  const hasPublicCleanupScope = grantedScopes.includes("base:record:delete");
  const syncBlockers = [
    !status?.app_configured ? "还没有保存企业自建应用配置" : null,
    !status?.authorized ? "还没有授权飞书管理员账号" : null,
    missingBitableSyncScopes.length > 0 ? `还缺少 ${missingBitableSyncScopes.length} 项多维表格写入权限（请在第 3 步查看）` : null,
    !workspaceReady
      ? status?.workspace?.status === "error"
        ? `排课多维表格创建失败：${status.workspace.last_error ?? "请重试第 4 步"}`
        : `排课多维表格尚未完成（当前 ${status?.workspace?.tables?.length ?? 0} / ${resources.length} 张业务表）`
      : null,
  ].filter((item): item is string => Boolean(item));
  const currentStep = !status?.app_configured
    ? 0
    : !aiConfiguration.data?.configured
      ? 1
    : !status?.authorized
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
    <div className="space-y-5 animate-fade-in">
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
                  {!hasPublicCleanupScope ? (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => authorize.mutate()}
                      disabled={authorize.isPending}
                    >
                      <ShieldCheck className="size-3.5" />补充公开表清理权限
                    </Button>
                  ) : null}
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setDisconnectConfirmOpen(true)}
                    disabled={disconnect.isPending}
                  >
                    <LogOut className="size-3.5" />解除连接
                  </Button>
                </div>
                {missingScopes.length > 0 ? (
                  <PermissionWarning scopes={missingScopes} />
                ) : null}
                {!hasPublicCleanupScope ? (
                  <p className="border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                    已有同步仍可使用；点击“补充公开表清理权限”重新授权一次后，系统会在同步“公开展示汇总”时自动删除历史重复记录。
                  </p>
                ) : null}
              </div>
            ) : (
              <div className="space-y-3">
                {status.status === "reauthorization_required" ? (
                  <div className="border-l-2 border-red-500 bg-red-50 px-3 py-2 text-xs leading-5 text-red-900">
                    飞书用户授权已过期或缺少当前同步所需权限。请先在飞书开放平台发布最新权限版本，再点击下方按钮重新授权管理员账号。
                  </div>
                ) : null}
                <Button
                  onClick={() => authorize.mutate()}
                  disabled={!status.app_configured || authorize.isPending}
                >
                  <ShieldCheck className="size-4" />
                  {authorize.isPending
                    ? "正在跳转"
                    : status.status === "reauthorization_required"
                      ? "重新授权管理员账号"
                      : "授权飞书管理员账号"}
                </Button>
                {missingScopes.length > 0 ? (
                  <PermissionWarning scopes={missingScopes} />
                ) : null}
              </div>
            )}
          </FlowStep>

          <FlowStep
            number={4}
            title="创建排课多维表格"
            description="系统会为当前课表方案创建独立的内部业务表，以及领导、班级入口目录和调课通知展示数据，并保存全部表格标识。"
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
                  {createWorkspace.isPending
                    ? `正在补齐 ${resources.length} 张表`
                    : status.workspace
                      ? "补齐缺失业务表"
                      : "自动创建排课表格"}
                </Button>
                <p className="text-xs leading-5 text-zinc-500 sm:col-span-2">{status.workspace ? `当前空间已有 ${status.workspace.tables?.length ?? 0} / ${resources.length} 张业务表；用同名空间补齐新增展示表，不会新建另一套课表。` : "创建时会自动加上当前课表方案名称，因此第 1、2、3……N 套课表会绑定到不同的飞书多维表格。"}</p>
              </div>
            )}
          </FlowStep>

          <FlowStep
            number={5}
            title="同步业务数据"
            description="一键同步当前发布版本的业务数据、展示汇总和班级链接目录；发布或回滚后会自动更新当前版本。所有生成表按稳定业务标识覆盖更新并清理旧版本行；此按钮只同步，不会启动求解。"
            state={ready ? "current" : "pending"}
            icon={CloudUpload}
          >
            <div className="flex max-w-3xl flex-col gap-2 sm:flex-row">
              <Button
                onClick={() => batchSync.mutate({ data: {} })}
                disabled={!ready || batchSync.isPending || sync.isPending}
              >
                <CloudUpload className="size-4" />
                {batchSync.isPending ? `正在同步 ${resources.length} 类数据` : "一键同步当前方案"}
              </Button>
              <Select aria-label="同步资源" selectSize="sm" containerClassName="w-44" value={resource} onChange={(event) => setResource(event.target.value as typeof resource)}
                className="h-9 min-w-0 flex-1 rounded-md border border-zinc-300 bg-white px-3 text-sm"
                disabled={!ready || batchSync.isPending}
              >
                {resources.map((item) => (
                  <option key={item} value={item}>
                    {resourceLabel(item)}
                  </option>
                ))}
              </Select>
              <Button
                onClick={() => sync.mutate({ data: { resource } })}
                disabled={!ready || sync.isPending || batchSync.isPending}
              >
                <SendHorizontal className="size-4" />
                {sync.isPending ? "正在同步单表" : "重试单表"}
              </Button>
              {status.workspace?.url ? <a href={status.workspace.url} target="_blank" rel="noreferrer"><Button type="button" variant="outline"><ExternalLink className="size-4" />打开当前多维表格</Button></a> : null}
            </div>
            {!ready ? (
              <div className="mt-3 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                <div className="font-medium">同步暂不可用，原因是：</div>
                <ul className="mt-1 list-disc pl-4">{syncBlockers.map((item) => <li key={item}>{item}</li>)}</ul>
              </div>
            ) : <p className="mt-2 text-xs text-emerald-700">连接已就绪：一键同步只写入顶栏当前所选方案绑定的独立多维表格；求解必须在“排课求解”页单独点击“开始求解”。</p>}
            {(syncReauthorizationPrompt || status.status === "reauthorization_required") ? (
              <FeishuReauthorizationAction
                authorize={() => authorize.mutate()}
                authorizing={authorize.isPending}
              />
            ) : null}
            {batchResult ? (
              <BatchSyncResult
                result={batchResult}
                authorize={() => authorize.mutate()}
                authorizing={authorize.isPending}
              />
            ) : null}
          </FlowStep>

          <FlowStep
            number={6}
            title="在飞书内创建妙搭应用"
            description="三张展示投影表和班级链接目录会随一键同步及版本发布/回滚更新；在飞书内分别绑定到妙搭的领导、班级和通知页面。"
            state={workspaceReady ? "current" : "pending"}
            icon={ExternalLink}
          >
            <div className="space-y-3 text-sm text-zinc-700">
              <p>每套课表方案都有独立的飞书多维表格。妙搭应从当前方案的展示数据和“班级链接索引”取数：领导页使用“公开展示汇总”，班级服务页使用“课表”并按班级筛选，入口目录使用“班级链接索引”，变更页使用“公开调课通知”。</p>
              <div className="grid gap-2 text-xs text-zinc-600 md:grid-cols-4">
                <div className="border border-zinc-200 bg-zinc-50 p-3"><div className="font-medium text-zinc-800">领导驾驶舱</div><p className="mt-1 leading-5">当前版本、发布时间、排课覆盖日期、覆盖班级、调整课次、总课次、教室利用率和月度趋势。</p></div>
                <div className="border border-zinc-200 bg-zinc-50 p-3"><div className="font-medium text-zinc-800">学生 / 家长课表</div><p className="mt-1 leading-5">从“课表”读取明细，按班级标识筛选；不把全校课表直接暴露给学生。</p></div>
                <div className="border border-zinc-200 bg-zinc-50 p-3"><div className="font-medium text-zinc-800">班级入口目录</div><p className="mt-1 leading-5">每个班级一行，登记学生/家长妙搭链接、公开视图链接、访问模式和链接状态。</p></div>
                <div className="border border-zinc-200 bg-zinc-50 p-3"><div className="font-medium text-zinc-800">调课通知</div><p className="mt-1 leading-5">只展示已生效的时间、地点、课程变更；不包含教师账号、联系方式和内部调课原因。</p></div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  onClick={() => batchSync.mutate({ data: { resources: [...publicDisplayResources] } })}
                  disabled={!ready || sync.isPending || batchSync.isPending}
                >
                  同步展示数据与班级链接目录
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
  const configured = Boolean(status?.app_configuration?.configured);
  const environmentManaged = status?.app_configuration?.source === "environment";
  if (configured && !editing) {
    return (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm font-medium text-emerald-700">飞书应用已配置</span>
          <Badge tone="green">应用密钥已加密</Badge>
          <span className="font-mono text-xs text-zinc-500">
            {status?.app_configuration?.app_id}
          </span>
        </div>
        <div className="text-xs leading-5 text-zinc-500">
          授权回调地址：{status?.app_configuration?.oauth_redirect_uri}
        </div>
        <div className="text-xs leading-5 text-zinc-500">
          Aily Workflow 高级通道：{status?.app_configuration?.aily_configured
            ? `${status?.app_configuration?.aily_app_id} / ${status?.app_configuration?.aily_skill_id}`
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

function PermissionWarning({ scopes }: { scopes?: unknown }) {
  const list = Array.isArray(scopes) ? scopes : [];
  if (list.length === 0) return null;
  return (
    <div className="border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
      尚缺权限：{list.map((scope) => permissionLabels[scope] ?? scope).join("、")}。请在开放平台补充权限、发布最新应用版本，再重新授权管理员账号。
    </div>
  );
}

function WorkspaceDetails({ workspace }: { workspace?: FeishuConnectionResponse["workspace"] }) {
  if (!workspace) return null;
  const tables = Array.isArray(workspace.tables) ? workspace.tables : [];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium text-zinc-900">{workspace.name}</span>
        <Badge tone="green">已创建</Badge>
        {workspace.url ? (
          <a href={workspace.url} target="_blank" rel="noreferrer">
            <Button size="sm" variant="outline"><ExternalLink className="size-3.5" />打开多维表格</Button>
          </a>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-2">
        {tables.map((table) => (
          <span key={table.resource} className="inline-flex items-center gap-1.5 rounded border border-zinc-200 bg-zinc-50 px-2 py-1 text-xs text-zinc-700">
            <CheckCircle2 className="size-3 text-emerald-600" />{table.table_name}
          </span>
        ))}
      </div>
    </div>
  );
}

function SyncHistory({ syncs }: { syncs?: unknown }) {
  // The API returns newest-first. A later successful reconciliation makes an
  // older transient cleanup failure historical, not an outstanding task.
  // Keep the row for auditability, but only show cleanup warnings on the
  // current row for each resource so users are not asked to clean an already
  // repaired table themselves.
  const list = Array.isArray(syncs) ? syncs : [];
  const latestByResource = new Map<string, string>();
  for (const item of list) {
    if (item && item.resource && !latestByResource.has(item.resource)) {
      latestByResource.set(item.resource, item.id);
    }
  }
  return (
    <section className="border border-zinc-200 bg-white">
      <div className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold">飞书同步记录</div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[930px] text-left text-sm">
          <thead className="bg-zinc-50 text-xs text-zinc-500"><tr><th className="h-9 px-4">时间</th><th>来源</th><th>业务数据</th><th>结果</th><th>飞书已有</th><th>新增</th><th>更新</th><th>跳过</th><th>本次写入</th></tr></thead>
          <tbody>
            {list.map((item) => (
              <tr key={item.id} className="border-t border-zinc-100">
                <td className="h-10 px-4 text-zinc-500">{datetime(item.created_at)}</td>
                <td className="text-xs text-zinc-500">{syncTriggerLabel(item.detail?.trigger)}</td>
                <td>{resourceLabel(item.resource)}</td>
                <td><Badge tone={statusTone(item.status)}>{statusLabel(item.status)}</Badge>{detailError(item.detail) ? <div className="mt-1 max-w-60 truncate text-xs text-red-600" title={detailError(item.detail) ?? undefined}>{detailError(item.detail)}</div> : null}{latestByResource.get(item.resource) === item.id && duplicateCleanupMessage(item.detail) ? <div className="mt-1 max-w-64 text-xs text-amber-700">{duplicateCleanupMessage(item.detail)}</div> : null}</td>
                <td>{item.records_read}</td>
                <td>{detailNumber(item.detail, "records_created")}</td>
                <td>{detailNumber(item.detail, "records_updated")}</td>
                <td>{detailNumber(item.detail, "records_skipped")}</td>
                <td>{item.records_written}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {list.length === 0 ? <div className="p-6 text-center text-sm text-zinc-400">暂无飞书同步记录</div> : null}
    </section>
  );
}

function FeishuReauthorizationAction({
  authorize,
  authorizing,
}: {
  authorize: () => void;
  authorizing: boolean;
}) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-3 border-l-2 border-red-500 bg-red-50 px-3 py-2 text-xs leading-5 text-red-900">
      <span>同步未开始或被飞书拒绝：当前用户授权已过期或缺少权限。发布应用权限后，请重新授权。</span>
      <Button size="sm" variant="outline" onClick={authorize} disabled={authorizing}>
        <ShieldCheck className="size-3.5" />{authorizing ? "正在跳转" : "重新授权管理员账号"}
      </Button>
    </div>
  );
}

function BatchSyncResult({
  result,
  authorize,
  authorizing,
}: {
  result: FeishuBatchSyncResponse;
  authorize: () => void;
  authorizing: boolean;
}) {
  const items = Array.isArray(result?.results) ? result.results : [];
  const skipped = batchSkippedCount(result);
  const reauthorizationRequired = batchRequiresFeishuReauthorization(result);
  const summary = result.failed_count === 0
    ? `已完成 ${result.completed_count} 类数据同步，共写入 ${result.records_written} 条记录${skipped ? `，跳过 ${skipped} 条未变更记录` : ""}。`
    : reauthorizationRequired
      ? `飞书拒绝了同步所需的用户授权；重新授权后再执行同步。`
      : `${result.completed_count} 类已完成，${result.failed_count} 类待处理；可用下方“重试单表”处理失败项。`;
  return (
    <div aria-live="polite" className="mt-3 border border-zinc-200 bg-zinc-50 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2"><span className="font-medium">本次一键同步</span><Badge tone={statusTone(result.status)}>{statusLabel(result.status)}</Badge><span className="text-xs text-zinc-500">{summary}</span></div>
      <div className="mt-2 grid gap-1 sm:grid-cols-2 xl:grid-cols-4">
        {items.map((item) => (
          <div key={item.id} className="border border-zinc-200 bg-white px-2.5 py-2 text-xs">
            <div className="flex items-center justify-between gap-2"><span className="font-medium">{resourceLabel(item.resource)}</span><Badge tone={statusTone(item.status)}>{statusLabel(item.status)}</Badge></div>
            <div className="mt-1 text-zinc-500">写入 {item.records_written} 条{detailNumberValue(item.detail, "records_skipped") ? `，跳过 ${detailNumberValue(item.detail, "records_skipped")} 条` : ""}</div>
            {detailError(item.detail) ? <div className="mt-1 text-red-600">{detailError(item.detail)}</div> : null}
            {duplicateCleanupMessage(item.detail) ? <div className="mt-1 text-amber-700">{duplicateCleanupMessage(item.detail)}</div> : null}
            {classViewSyncMessage(item.resource, item.detail) ? <div className="mt-1 text-blue-700">{classViewSyncMessage(item.resource, item.detail)}</div> : null}
          </div>
        ))}
      </div>
      {reauthorizationRequired ? <FeishuReauthorizationAction authorize={authorize} authorizing={authorizing} /> : null}
    </div>
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
      <p className="text-sm leading-6 text-zinc-700">{status.status === "reauthorization_required" ? "当前用户授权已过期或缺少权限。请先在飞书开放平台发布最新权限版本，再重新授权。" : "点击后将前往飞书授权页。确认权限后，飞书会自动返回途排智策。"}</p>
      <Button onClick={authorize} disabled={!status.app_configured || authorizing}>
        <ShieldCheck className="size-4" />{authorizing ? "正在跳转" : status.status === "reauthorization_required" ? "重新授权管理员账号" : "授权飞书管理员账号"}
      </Button>
      {Array.isArray(status.missing_scopes) && status.missing_scopes.length > 0 ? <PermissionWarning scopes={status.missing_scopes} /> : null}
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
      {Array.isArray(status.missing_scopes) && status.missing_scopes.length > 0 ? <PermissionWarning scopes={status.missing_scopes} /> : null}
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

function detailNumber(detail: Record<string, unknown> | undefined | null, key: string): number | string {
  if (!detail || typeof detail !== "object") return "-";
  const value = detail[key];
  return typeof value === "number" ? value : "-";
}

function detailNumberValue(detail: Record<string, unknown> | undefined | null, key: string): number {
  if (!detail || typeof detail !== "object") return 0;
  const value = detail[key];
  return typeof value === "number" ? value : 0;
}

function batchSkippedCount(result: FeishuBatchSyncResponse | null | undefined): number {
  const items = Array.isArray(result?.results) ? result.results : [];
  return items.reduce(
    (total, item) => total + detailNumberValue(item.detail, "records_skipped"),
    0,
  );
}

function detailError(detail: Record<string, unknown> | undefined | null): string | null {
  if (!detail || typeof detail !== "object") return null;
  const value = detail.error;
  return typeof value === "string" && value.trim() ? value : null;
}

function requiresFeishuReauthorization(message: string): boolean {
  return message.includes("99991679")
    || message.includes("重新授权")
    || message.includes("授权缺少权限")
    || message.includes("授权已经失效");
}

function batchRequiresFeishuReauthorization(result: FeishuBatchSyncResponse | null | undefined): boolean {
  const items = Array.isArray(result?.results) ? result.results : [];
  return items.some((item) => {
    if (item.detail?.reauthorization_required === true) return true;
    const error = detailError(item.detail);
    return error ? requiresFeishuReauthorization(error) : false;
  });
}

function duplicateCleanupMessage(detail: Record<string, unknown> | undefined | null): string | null {
  if (!detail || typeof detail !== "object") return null;
  const raw = detail.duplicate_cleanup;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const cleanup = raw as Record<string, unknown>;
  const status = typeof cleanup.status === "string" ? cleanup.status : "";
  const deleted = typeof cleanup.deleted === "number" ? cleanup.deleted : 0;
  const candidates = typeof cleanup.managed_candidates === "number" ? cleanup.managed_candidates : 0;
  const failed = typeof cleanup.failed === "number" ? cleanup.failed : 0;
  if (status === "completed" && deleted) return `已清理 ${deleted} 条历史重复记录`;
  if (status === "skipped_missing_delete_scope" && candidates) return `发现 ${candidates} 条历史重复记录；补充清理权限后可自动删除`;
  if (status === "failed" && (failed || candidates)) return `重复记录清理未完成（${failed || candidates} 条待处理）`;
  return null;
}

function classViewSyncMessage(resource: string, detail: Record<string, unknown>): string | null {
  if (resource !== "schedule") return null;
  const raw = detail.view_sync;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const sync = raw as Record<string, unknown>;
  const status = typeof sync.status === "string" ? sync.status : "";
  const created = typeof sync.created === "number" ? sync.created : 0;
  const deleted = typeof sync.deleted === "number" ? sync.deleted : 0;
  const classes = typeof sync.classes === "number" ? sync.classes : 0;
  if (status === "completed") return `班级视图 ${classes} 个（新增 ${created}，清理 ${deleted}）`;
  if (status === "skipped_missing_scope") return "班级视图未创建：重新授权后会自动补齐";
  if (status === "failed") return "班级视图同步未完成，可重试班级链接目录";
  return null;
}

function syncTriggerLabel(value: unknown): string {
  if (value === "manual_batch") return "一键同步";
  if (value === "single_resource") return "单表重试";
  if (value === "version_publish") return "发布后自动";
  if (value === "version_rollback") return "回滚后自动";
  return "单表同步";
}
