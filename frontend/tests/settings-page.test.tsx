import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "@/pages/settings-page";

const mocks = vi.hoisted(() => ({
  connection: { current: {} as Record<string, unknown> },
  currentUser: { current: {} as Record<string, unknown> },
  createWorkspace: vi.fn(),
  configureApp: vi.fn(),
  disconnect: vi.fn(),
  startAuthorization: vi.fn(),
  sync: vi.fn(),
  batchSync: vi.fn(),
  aiGet: vi.fn(),
  aiPost: vi.fn(),
  changePassword: vi.fn(),
  authClear: vi.fn(),
  refetchConnection: vi.fn(),
  refetchSyncs: vi.fn(),
  refetchCurrentUser: vi.fn(),
}));

vi.mock("@/api/http", () => ({
  API_BASE_URL: "http://127.0.0.1:8000",
  http: {
    get: mocks.aiGet,
    post: mocks.aiPost,
  },
  authStore: {
    clear: mocks.authClear,
  },
}));

vi.mock("@/api/generated/client", () => ({
  getFeishuConnectionApiV1IntegrationsFeishuConnectionGetQueryKey: () => ["飞书连接"],
  getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey: () => ["飞书同步"],
  useConfigureFeishuAppApiV1IntegrationsFeishuAppConfigurationPost: () => ({
    mutate: mocks.configureApp,
    isPending: false,
  }),
  useCurrentUserApiV1AuthMeGet: () => ({
    data: mocks.currentUser.current,
    isPending: false,
    isError: false,
    refetch: mocks.refetchCurrentUser,
  }),
  useFeishuConnectionApiV1IntegrationsFeishuConnectionGet: () => ({
    data: mocks.connection.current,
    isPending: false,
    isError: false,
    refetch: mocks.refetchConnection,
  }),
  useListFeishuSyncsApiV1IntegrationsFeishuSyncsGet: () => ({
    data: [],
    isPending: false,
    isError: false,
    refetch: mocks.refetchSyncs,
  }),
  useCreateFeishuWorkspaceApiV1IntegrationsFeishuWorkspacesPost: () => ({
    mutate: mocks.createWorkspace,
    isPending: false,
  }),
  useDisconnectFeishuApiV1IntegrationsFeishuConnectionDelete: () => ({
    mutate: mocks.disconnect,
    isPending: false,
  }),
  useStartFeishuOauthApiV1IntegrationsFeishuOauthStartPost: () => ({
    mutate: mocks.startAuthorization,
    isPending: false,
  }),
  useFeishuSyncApiV1IntegrationsFeishuSyncPost: () => ({
    mutate: mocks.sync,
    isPending: false,
  }),
  useFeishuSyncBatchApiV1IntegrationsFeishuSyncBatchPost: () => ({
    mutate: mocks.batchSync,
    isPending: false,
  }),
  useChangeOwnPasswordApiV1AuthChangePasswordPost: (options?: { mutation?: { onSuccess?: (data: unknown, variables: { data: unknown }, context: unknown) => void } }) => ({
    // 模拟真实 mutation 的成功路径，便于断言组件在 onSuccess 里的登出行为。
    mutate: (variables: { data: unknown }) => {
      mocks.changePassword(variables);
      options?.mutation?.onSuccess?.(undefined, variables, undefined);
    },
    isPending: false,
  }),
}));

const baseConnection = {
  status: "not_authorized",
  app_configured: true,
  authorized: false,
  missing_fields: [],
  granted_scopes: [],
  missing_scopes: [],
  access_expires_at: null,
  message: "应用配置已就绪，请授权飞书管理员账号。",
  console_url: "https://open.feishu.cn/app/",
  docs_url: "https://open.feishu.cn/document/authentication-management/access-token/obtain-oauth-code",
  app_configuration: {
    configured: true,
    source: "frontend",
    app_id: "cli_test",
    secret_configured: true,
    oauth_redirect_uri: "http://127.0.0.1:8002/api/v1/integrations/feishu/oauth/callback",
    frontend_url: "http://127.0.0.1:5175",
    aily_configured: false,
    aily_app_id: null,
    aily_skill_id: null,
  },
  workspace: null,
};

const baseUser = {
  id: "user-1",
  username: "admin",
  role: "admin",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SettingsPage />
    </QueryClientProvider>,
  );
}

describe("设置页", () => {
  afterEach(cleanup);

  beforeEach(() => {
    window.localStorage.setItem("tupai:feishu-guide-completed", "1");
    for (const mock of [
      mocks.createWorkspace,
      mocks.configureApp,
      mocks.disconnect,
      mocks.startAuthorization,
      mocks.sync,
      mocks.batchSync,
      mocks.changePassword,
      mocks.authClear,
      mocks.refetchConnection,
      mocks.refetchSyncs,
      mocks.refetchCurrentUser,
      mocks.aiGet,
      mocks.aiPost,
    ]) {
      mock.mockReset();
    }
    mocks.aiGet.mockResolvedValue({ data: { configured: true, source: "frontend", provider: "openai_compatible", base_url: "https://model.example/v1", api_key_configured: true, model: "scheduling-model" } });
    mocks.aiPost.mockResolvedValue({ data: { configured: true, source: "frontend", provider: "openai_compatible", base_url: "https://model.example/v1", api_key_configured: true, model: "scheduling-model" } });
    mocks.connection.current = { ...baseConnection };
    mocks.currentUser.current = { ...baseUser };
  });

  it("可在前端一次保存应用凭据，不再要求手工填写环境变量", async () => {
    mocks.connection.current = {
      ...baseConnection,
      status: "unconfigured",
      app_configured: false,
      missing_fields: ["应用编号", "应用密钥"],
      message: "请在当前页面填写飞书应用编号和应用密钥。",
      app_configuration: {
        ...baseConnection.app_configuration,
        configured: false,
        source: "none",
        app_id: null,
        secret_configured: false,
        oauth_redirect_uri: "http://stale.invalid/api/v1/integrations/feishu/oauth/callback",
      },
    };
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("需要申请的用户身份权限")).toBeVisible();
    expect(screen.getByText("base:view:write_only")).toBeVisible();
    await user.clear(screen.getByLabelText("飞书应用编号"));
    await user.type(screen.getByLabelText("飞书应用编号"), "cli_frontend_test");
    await user.clear(screen.getByLabelText("飞书应用密钥"));
    await user.type(screen.getByLabelText("飞书应用密钥"), "frontend-secret");
    await user.click(screen.getByRole("button", { name: "保存应用配置" }));
    expect(mocks.configureApp).toHaveBeenCalledWith({
      data: expect.objectContaining({
        app_id: "cli_frontend_test",
        app_secret: "frontend-secret",
        aily_app_id: "",
        aily_skill_id: "",
        oauth_redirect_uri:
          "http://127.0.0.1:8000/api/v1/integrations/feishu/oauth/callback",
      }),
    });
    expect(screen.queryByText("FEISHU_BITABLE_APP_TOKEN", { exact: false })).not.toBeInTheDocument();
    expect(screen.queryByText("FEISHU_TABLE_MAP", { exact: false })).not.toBeInTheDocument();
  });

  it("可配置独立 AI 模型完成一句话排课解析", async () => {
    mocks.aiGet.mockResolvedValueOnce({ data: { configured: false, source: "none", provider: null, base_url: null, api_key_configured: false, model: null } });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Aily 标识已从必填项移除");
    await user.type(screen.getByLabelText("AI 接口地址"), "https://model.example/v1");
    await user.type(screen.getByLabelText("AI 模型名称"), "scheduling-model");
    await user.type(screen.getByLabelText("AI API Key"), "secret-ai-key");
    await user.click(screen.getByRole("button", { name: "保存 AI 配置" }));

    expect(mocks.aiPost).toHaveBeenCalledWith("/api/v1/integrations/ai/configuration", {
      provider: "openai_compatible",
      base_url: "https://model.example/v1",
      api_key: "secret-ai-key",
      model: "scheduling-model",
    });
  });

  it("展示当前账户信息并提供修改密码表单", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("admin")).toBeVisible();
    expect(screen.getByText("user-1")).toBeVisible();

    // 两次输入的新密码不一致时按钮保持禁用。
    await user.type(screen.getByLabelText("当前密码"), "old-password-1");
    await user.type(screen.getByLabelText("新密码"), "new-password-1");
    await user.type(screen.getByLabelText("确认新密码"), "different-pass");
    expect(screen.getByRole("button", { name: "更新密码" })).toBeDisabled();

    await user.clear(screen.getByLabelText("确认新密码"));
    await user.type(screen.getByLabelText("确认新密码"), "new-password-1");
    await user.click(screen.getByRole("button", { name: "更新密码" }));
    expect(mocks.changePassword).toHaveBeenCalledWith({
      data: { current_password: "old-password-1", new_password: "new-password-1" },
    });
    // 改密会递增 token_version，旧令牌立即失效，因此必须清理本地凭据要求重新登录。
    expect(mocks.authClear).toHaveBeenCalled();
  });

  it("管理员授权后可以直接创建排课多维表格", async () => {
    mocks.connection.current = {
      ...baseConnection,
      status: "connected",
      authorized: true,
      granted_scopes: ["offline_access"],
      access_expires_at: "2026-08-10T12:00:00Z",
      message: "飞书管理员账号已授权。",
      app_configuration: {
        ...baseConnection.app_configuration,
        configured: true,
        app_id: "cli_test",
      },
    };
    const user = userEvent.setup();
    renderPage();

    const input = await screen.findByLabelText("多维表格基础名称");
    await user.clear(input);
    await user.type(input, "途排智策 - 2026 秋季学期");
    await user.click(screen.getByRole("button", { name: "自动创建排课表格" }));

    expect(mocks.createWorkspace).toHaveBeenCalledWith({
      data: { name: "途排智策 - 2026 秋季学期" },
    });
  });

  it("建表完成后可一键同步当前方案，失败时仍能重试单表", async () => {
    const tables = [
      ["teachers", "教师"],
      ["class_groups", "班级"],
      ["rooms", "教室"],
      ["time_slots", "时段"],
      ["course_sessions", "课程场次"],
      ["rules", "规则"],
      ["schedule", "课表"],
      ["public_summary", "公开展示汇总"],
      ["public_adjustment_notice", "公开调课通知"],
      ["public_class_links", "班级链接索引"],
    ].map(([resource, table_name], index) => ({ resource, table_name, table_id: `tbl-${index}` }));
    // Older installations may still expose the retired projection binding.
    // It must not make the current ten-table workspace fail readiness checks.
    tables.push({ resource: "public_class_schedule", table_name: "班级公开课表", table_id: "tbl-legacy" });
    mocks.connection.current = {
      ...baseConnection,
      status: "connected",
      authorized: true,
      granted_scopes: [
        "offline_access",
        "base:table:read",
        "base:field:read",
        "base:field:create",
        "bitable:app:readonly",
        "base:record:create",
        "base:record:retrieve",
        "base:record:update",
      ],
      missing_scopes: ["calendar:calendar.event:create"],
      access_expires_at: "2026-08-10T12:00:00Z",
      message: "飞书管理员账号已授权。",
      workspace: {
        id: "workspace-1",
        name: "途排智策 - 2026 秋季学期",
        url: "https://example.feishu.cn/base/test",
        status: "active",
        last_error: null,
        tables,
        created_at: "2026-08-10T10:00:00Z",
      },
    };
    const user = userEvent.setup();
    renderPage();

    // 连接完全就绪时飞书卡片默认收起，先展开再操作同步区。
    await user.click(await screen.findByRole("button", { name: "展开配置" }));
    expect(await screen.findByRole("link", { name: "打开多维表格" })).toHaveAttribute(
      "href",
      "https://example.feishu.cn/base/test",
    );
    await user.click(screen.getByRole("button", { name: "一键同步当前方案" }));
    expect(mocks.batchSync).toHaveBeenCalledWith({ data: {} });
    expect(screen.queryByText("在飞书内创建妙搭应用")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "同步展示数据与班级链接目录" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("同步资源"), "teachers");
    await user.click(screen.getByRole("button", { name: "重试单表" }));
    expect(mocks.sync).toHaveBeenCalledWith({ data: { resource: "teachers" } });
  });

  it("旧用户令牌缺少新增权限时显示重新授权动作", async () => {
    mocks.connection.current = {
      ...baseConnection,
      status: "reauthorization_required",
      authorized: false,
      missing_scopes: ["base:field:read", "base:field:create", "bitable:app:readonly"],
      message: "当前飞书用户令牌缺少同步所需权限，请重新授权管理员账号。",
    };
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText(/飞书用户授权已过期或缺少当前同步所需权限/)).toBeVisible();
    const reauthorizeButtons = screen.getAllByRole("button", { name: "重新授权管理员账号" });
    expect(reauthorizeButtons.length).toBeGreaterThanOrEqual(1);
    await user.click(reauthorizeButtons[0]);
    expect(mocks.startAuthorization).toHaveBeenCalledTimes(1);
  });
});
