import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";

const apiBaseURL = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:8001";

async function createAdminApi(): Promise<APIRequestContext> {
  const anonymousApi = await request.newContext({ baseURL: apiBaseURL });
  const login = await anonymousApi.post("/api/v1/auth/token", {
    form: { username: "admin", password: "tupai-demo-admin-2026!" },
  });
  expect(login.ok()).toBeTruthy();
  const { access_token: accessToken } = await login.json();
  await anonymousApi.dispose();
  return request.newContext({
    baseURL: apiBaseURL,
    extraHTTPHeaders: { Authorization: `Bearer ${accessToken}` },
  });
}

async function ensureSchedule(api: APIRequestContext) {
  const existing = await api.get("/api/v1/schedules");
  const schedules = await existing.json();
  if (schedules.length) return;
  const created = await api.post("/api/v1/solver-runs", {
    data: {
      preference_weight: 100,
      seat_waste_weight: 1,
      change_weight: 100000,
      time_limit_seconds: 30,
      wait: false,
    },
  });
  expect(created.ok()).toBeTruthy();
  const run = await created.json();
  await expect.poll(async () => {
    const status = await api.get(`/api/v1/solver-runs/${run.id}`);
    return (await status.json()).model_status;
  }, { timeout: 30_000 }).toBe("OPTIMAL");
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("用户名").fill("admin");
  await page.getByLabel("密码").fill("tupai-demo-admin-2026!");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/overview");
}

test.beforeEach(async () => {
  const api = await createAdminApi();
  await ensureSchedule(api);
  await api.dispose();
});

test("renders the schedule grid at a desktop viewport", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await login(page);
  await page.getByRole("link", { name: "课表视图", exact: true }).click();
  await expect(page.getByText(/^已安排:\s*[1-9]\d*\s*节课次$/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("schedule-desktop.png"), fullPage: true });
});

test("keeps navigation and the schedule usable at a mobile viewport", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.getByTitle("打开导航").click();
  await expect(page.getByRole("link", { name: "课表视图", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "课表视图", exact: true }).click();
  await expect(page.getByText(/^已安排:\s*[1-9]\d*\s*节课次$/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("schedule-mobile.png"), fullPage: true });
});
