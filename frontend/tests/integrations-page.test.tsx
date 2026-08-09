import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IntegrationsPage } from "@/pages/integrations-page";

const mocks = vi.hoisted(() => ({
  connection: { current: {} as Record<string, unknown> },
  createWorkspace: vi.fn(),
  disconnect: vi.fn(),
  startAuthorization: vi.fn(),
  sync: vi.fn(),
  refetchConnection: vi.fn(),
  refetchSyncs: vi.fn(),
}));

vi.mock("@/api/generated/client", () => ({
  getFeishuConnectionApiV1IntegrationsFeishuConnectionGetQueryKey: () => ["飞书连接"],
  getListFeishuSyncsApiV1IntegrationsFeishuSyncsGetQueryKey: () => ["飞书同步"],
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
  workspace: null,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <IntegrationsPage />
    </QueryClientProvider>,
  );
}

describe("飞书生产接入页", () => {
  afterEach(cleanup);

  beforeEach(() => {
    window.localStorage.setItem("tupai:feishu-guide-completed", "1");
    for (const mock of [
      mocks.createWorkspace,
      mocks.disconnect,
      mocks.startAuthorization,
      mocks.sync,
      mocks.refetchConnection,
      mocks.refetchSyncs,
    ]) {
      mock.mockReset();
    }
    mocks.connection.current = { ...baseConnection };
  });

  it("只要求部署应用配置，不再要求手工填写表格标识", () => {
    mocks.connection.current = {
      ...baseConnection,
      status: "unconfigured",
      app_configured: false,
      missing_fields: ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_TOKEN_ENCRYPTION_KEY"],
      message: "服务端尚未完成飞书应用配置。",
    };
    renderPage();

    expect(screen.getByText("需要申请的用户身份权限")).toBeVisible();
    expect(screen.getByText("FEISHU_TOKEN_ENCRYPTION_KEY", { exact: false })).toBeVisible();
    expect(screen.queryByText("FEISHU_BITABLE_APP_TOKEN", { exact: false })).not.toBeInTheDocument();
    expect(screen.queryByText("FEISHU_TABLE_MAP", { exact: false })).not.toBeInTheDocument();
  });

  it("管理员授权后可以直接创建排课多维表格", async () => {
    mocks.connection.current = {
      ...baseConnection,
      status: "connected",
      authorized: true,
      granted_scopes: ["offline_access"],
      access_expires_at: "2026-08-10T12:00:00Z",
      message: "飞书管理员账号已授权。",
    };
    const user = userEvent.setup();
    renderPage();

    const input = screen.getByLabelText("排课空间名称");
    await user.clear(input);
    await user.type(input, "途排智策 - 2026 秋季学期");
    await user.click(screen.getByRole("button", { name: "自动创建排课表格" }));

    expect(mocks.createWorkspace).toHaveBeenCalledWith({
      data: { name: "途排智策 - 2026 秋季学期" },
    });
  });

  it("后端滚动升级期间收到旧版连接字段仍可正常显示", () => {
    mocks.connection.current = {
      mode: "live",
      configured: true,
      connected: true,
      table_mapping_configured: true,
      missing_fields: [],
      missing_resources: [],
      message: "飞书生产连接已验证。",
      console_url: "https://open.feishu.cn/app/",
      docs_url: "https://open.feishu.cn/document/",
    };

    renderPage();

    expect(screen.getByText("飞书生产连接已验证。")).toBeVisible();
    expect(screen.getByText("管理员账号已授权")).toBeVisible();
    expect(screen.getByRole("button", { name: "自动创建排课表格" })).toBeEnabled();
  });

  it("建表完成后按中文资源执行真实幂等同步", async () => {
    const tables = [
      ["teachers", "教师"],
      ["class_groups", "班级"],
      ["rooms", "教室"],
      ["time_slots", "时段"],
      ["course_sessions", "课程场次"],
      ["rules", "规则"],
      ["schedule", "课表"],
    ].map(([resource, table_name], index) => ({ resource, table_name, table_id: `tbl-${index}` }));
    mocks.connection.current = {
      ...baseConnection,
      status: "connected",
      authorized: true,
      granted_scopes: Object.keys({ offline_access: true }),
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

    expect(screen.getByRole("link", { name: "打开多维表格" })).toHaveAttribute(
      "href",
      "https://example.feishu.cn/base/test",
    );
    await user.selectOptions(screen.getByLabelText("同步资源"), "teachers");
    await user.click(screen.getByRole("button", { name: "同步到飞书" }));
    expect(mocks.sync).toHaveBeenCalledWith({ data: { resource: "teachers" } });
  });
});
