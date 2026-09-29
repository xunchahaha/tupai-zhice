import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";

/**
 * 信息架构收敛后的导航契约：旧地址仍可访问（重定向到新入口），
 * 没有排课权限的成员只看到自己用得上的入口。URL 契约见 src/lib/routes.ts。
 */

const apiBaseURL = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:8001";
const adminPassword = "tupai-demo-admin-2026!";
// 只读成员是本用例自建的测试账号，只存在于 e2e 独立库里。
const viewerPassword = "e2e-viewer-pass-2026!";

async function adminApi(): Promise<APIRequestContext> {
  const anonymous = await request.newContext({ baseURL: apiBaseURL });
  const login = await anonymous.post("/api/v1/auth/token", {
    form: { username: "admin", password: adminPassword },
  });
  expect(login.ok()).toBeTruthy();
  const { access_token: accessToken } = await login.json();
  await anonymous.dispose();
  return request.newContext({
    baseURL: apiBaseURL,
    extraHTTPHeaders: { Authorization: `Bearer ${accessToken}` },
  });
}

async function login(page: Page, username: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/assistant");
}

test("旧地址仍可访问：重定向到对应的新入口并保留 query", async ({ page }) => {
  await login(page, "admin", adminPassword);
  const sidebar = page.getByRole("navigation", { name: "主导航" });
  // 左侧只有三个业务入口和底部设置。
  await expect(sidebar.getByRole("link")).toHaveText(["排课助手", "课表", "基础资料", "设置"]);

  const redirects: Array<[from: string, to: RegExp]> = [
    ["/overview", /\/assistant$/],
    ["/solver", /\/assistant$/],
    ["/goals", /\/assistant$/],
    ["/reschedule", /\/schedule\?view=adjust$/],
    ["/versions", /\/schedule\?view=history$/],
    ["/public-links", /\/schedule\?view=share$/],
    ["/integrations?section=ai", /\/settings\?section=ai$/],
  ];
  for (const [from, to] of redirects) {
    await page.goto(from);
    await expect(page, `${from} 应重定向`).toHaveURL(to);
  }

  // 课表页按 ?view= 落在对应分段。
  await page.goto("/versions");
  await expect(page.getByRole("tab", { name: "历史版本" })).toHaveAttribute("aria-selected", "true");

  // 规则 / 常用偏好不在侧栏，但地址直达仍可用，且侧栏仍高亮「设置」。
  await page.goto("/rules");
  await expect(page.getByRole("heading", { name: "学校通用规则", exact: true })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "设置", exact: true })).toHaveAttribute("aria-current", "page");
  await page.goto("/memory");
  await expect(page.getByRole("heading", { name: "常用偏好", exact: true })).toBeVisible();
});

test("只读成员：助手页不提供排课入口，设置入口不可见且不能直达", async ({ page }) => {
  const api = await adminApi();
  const username = `e2e-viewer-${Date.now()}`;
  const created = await api.post("/api/v1/users", {
    data: { username, password: viewerPassword, role: "viewer" },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  await api.dispose();

  await login(page, username, viewerPassword);
  await expect(page.getByRole("heading", { name: "排课助手", exact: true })).toBeVisible();
  await expect(page.getByText("当前账号为只读")).toBeVisible();
  await expect(page.getByLabel("排课需求")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "手动排课（自己设置参数）" })).toHaveCount(0);

  const sidebar = page.getByRole("navigation", { name: "主导航" });
  await expect(sidebar.getByRole("link", { name: "课表", exact: true })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "设置", exact: true })).toHaveCount(0);

  // 设置与账号管理页对只读成员不开放：直达地址被退回，不是空白也不是报错。
  await page.goto("/settings");
  await expect(page).not.toHaveURL(/\/settings/);
  await page.goto("/accounts");
  await expect(page).not.toHaveURL(/\/accounts/);
});
