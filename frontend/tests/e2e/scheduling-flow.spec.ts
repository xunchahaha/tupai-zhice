import { expect, request, test } from "@playwright/test";

const apiBaseURL = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:8001";

test("管理员完成排课、调课、回滚和飞书生产接入引导流程", async ({ page }, testInfo) => {
  test.setTimeout(75_000);
  const anonymousApi = await request.newContext({ baseURL: apiBaseURL });
  const login = await anonymousApi.post("/api/v1/auth/token", {
    form: { username: "admin", password: "tupai-demo" },
  });
  expect(login.ok()).toBeTruthy();
  const { access_token: accessToken } = await login.json();
  await anonymousApi.dispose();

  const api = await request.newContext({
    baseURL: apiBaseURL,
    extraHTTPHeaders: { Authorization: `Bearer ${accessToken}` },
  });
  const proposalId = `E2E-AILY-${testInfo.parallelIndex}-${Date.now()}`;
  const proposal = await api.post("/api/v1/aily/rule-proposals", {
    headers: { "X-Aily-Key": "aily-e2e-key" },
    data: {
      source_text: "测试教师上午优先排课",
      source_doc: "Playwright E2E",
      proposals: [{
        business_id: proposalId,
        source_text: "教师甲优先安排在 S01",
        actor_type: "teacher",
        actor_ids: ["T01"],
        constraint_type: "preferred_slot",
        scope: { slot_ids: ["S01"] },
        hardness: "soft",
        weight: 10,
        structured_expression: { preference: "S01" },
        confidence: 0.95,
      }],
    },
  });
  expect(proposal.ok()).toBeTruthy();

  await page.goto("/login");
  await page.getByLabel("用户名").fill("admin");
  await page.getByLabel("密码").fill("tupai-demo");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/overview");
  await expect(page.getByRole("heading", { name: "总览" })).toBeVisible();

  await page.getByRole("link", { name: "规则工作台" }).click();
  await expect(page.getByText(proposalId, { exact: true })).toBeVisible();
  await page.getByText(proposalId, { exact: true }).click();
  await page.getByRole("button", { name: "确认生效" }).click();
  await expect(page.getByText("规则状态已更新")).toBeVisible();
  await expect.poll(async () => {
    const response = await api.get(`/api/v1/rules?status=active`);
    const rules = await response.json();
    return rules.some((rule: { business_id: string }) => rule.business_id === proposalId);
  }).toBeTruthy();

  await page.getByRole("link", { name: "飞书集成" }).click();
  await expect(page.getByRole("heading", { name: "飞书生产连接" })).toBeVisible();
  await expect(page.getByText("应用凭据", { exact: true })).toBeVisible();
  await expect(page.getByText("生产连接引导", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "完成配置后启用" })).toBeDisabled();

  await page.getByRole("link", { name: "排课求解" }).click();
  const solverResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/solver-runs") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "开始求解" }).click();
  const solverRun = await (await solverResponse).json();
  await expect(page.getByText("求解任务已创建")).toBeVisible();
  await expect.poll(async () => {
    const response = await api.get(`/api/v1/solver-runs/${solverRun.id}`);
    return (await response.json()).model_status;
  }, { timeout: 30_000 }).toBe("OPTIMAL");
  await expect(page.getByText("最优解", { exact: true }).first()).toBeVisible();

  await page.getByRole("link", { name: "课表视图" }).click();
  await expect(page.getByRole("heading", { name: "课表视图" })).toBeVisible();
  await expect(page.getByText(/已显示 \d+ 个课次/)).toBeVisible();
  await expect(page.getByText("B01-1", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "局部调课" }).click();
  await page.getByLabel("影响时段").selectOption("S03");
  await page.getByLabel("说明").fill("E2E 教师请假");
  const adjustmentResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/reschedule-events") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "生成候选方案" }).click();
  const adjustment = await (await adjustmentResponse).json();
  await expect(page.getByText("局部调课任务已创建")).toBeVisible();
  let candidateScheduleId = "";
  await expect.poll(async () => {
    const response = await api.get("/api/v1/reschedule-events");
    const events = await response.json();
    candidateScheduleId = events.find((event: { id: string }) => event.id === adjustment.id)?.candidate_schedule_id ?? "";
    return candidateScheduleId;
  }, { timeout: 30_000 }).not.toBe("");

  const eventResponse = await api.get("/api/v1/reschedule-events");
  const event = (await eventResponse.json()).find((item: { id: string }) => item.id === adjustment.id);
  expect(event.status).toBe("candidate_ready");
  expect(candidateScheduleId).toBeTruthy();

  const candidateResponse = await api.get(`/api/v1/schedules/${candidateScheduleId}`);
  const candidate = await candidateResponse.json();
  const diffResponse = await api.get(
    `/api/v1/schedules/${event.parent_schedule_id}/diff/${candidateScheduleId}`,
  );
  const diff = await diffResponse.json();
  expect(diff.changed_count).toBeGreaterThan(0);

  await page.getByRole("link", { name: "版本与回滚" }).click();
  await page.getByLabel("基准版本").selectOption(event.parent_schedule_id);
  await page.getByLabel("目标版本").selectOption(candidateScheduleId);
  await expect(page.getByText("变更课次")).toBeVisible();
  await expect(page.getByText(String(diff.changed_count), { exact: true })).toBeVisible();

  const candidateCard = page.getByTestId(`version-card-${candidate.id}`);
  await candidateCard.locator("button").click();
  await expect(page.getByText("版本已发布")).toBeVisible();
  const parentResponse = await api.get(`/api/v1/schedules/${event.parent_schedule_id}`);
  const parent = await parentResponse.json();
  const parentCard = page.getByTestId(`version-card-${parent.id}`);
  await parentCard.locator("button").click();
  await expect.poll(async () => {
    const response = await api.get(`/api/v1/schedules/${event.parent_schedule_id}`);
    return (await response.json()).status;
  }).toBe("published");

  await api.dispose();
});
