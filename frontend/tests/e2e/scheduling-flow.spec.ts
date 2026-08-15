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
  await expect(page.getByRole("dialog", { name: "飞书生产接入向导" })).toBeVisible();
  await expect(page.getByText("管理员不需要手工创建任何飞书数据表。")).toBeVisible();
  await page.getByRole("button", { name: "稍后继续" }).click();
  await expect(page.getByRole("heading", { name: "飞书生产连接" })).toBeVisible();
  await expect(page.getByText("应用配置", { exact: true })).toBeVisible();
  await expect(page.getByText("生产接入步骤", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "同步到飞书" })).toBeDisabled();

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
  // 求解结论的文案在 f076b9f 已改成「已证明最优」，与任务状态区分开。
  await expect(page.getByText("已证明最优", { exact: true }).first()).toBeVisible();

  await page.getByRole("link", { name: "课表视图" }).click();
  await expect(page.getByRole("heading", { name: "课表视图" })).toBeVisible();
  await expect(page.getByText(/已显示 \d+ 个课次/)).toBeVisible();
  await expect(page.getByText("B01-1", { exact: true })).toBeVisible();

  // 请假事件必须命中该教师真实占用的时段，否则它不与任何课冲突，零变更才是最优解。
  // 原来写死的 S03 只在求解器单线程时成立：36e55d0 起 num_search_workers=8，
  // 目标值相同的最优解之间会随机换一个，T01 不一定还落在 S03 上，断言随机失败。
  const scheduleSummaries = await (await api.get("/api/v1/schedules")).json();
  const parentSummary = scheduleSummaries.find(
    (item: { solver_run_id: string }) => item.solver_run_id === solverRun.id,
  );
  expect(parentSummary).toBeTruthy();
  const parentDetail = await (await api.get(`/api/v1/schedules/${parentSummary.id}`)).json();
  // 行序没有 ORDER BY，按业务标识排序取第一条，保证同一份课表每次选中同一个课次。
  const [blocked] = [...parentDetail.assignments].sort(
    (left: { course_business_id: string }, right: { course_business_id: string }) =>
      left.course_business_id.localeCompare(right.course_business_id),
  );
  expect(blocked).toBeTruthy();

  await page.getByRole("link", { name: "局部调课" }).click();
  await page.getByLabel("教师").selectOption(blocked.teacher_business_id);
  await page.getByLabel("影响时段").selectOption(blocked.slot_business_id);
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
  // 聚合计数只是必要条件，真正要证明的是被请假挡住的那节课自己挪走了。
  expect(diff.changed_count).toBeGreaterThan(0);
  const movedItem = diff.items.find(
    (item: { course_business_id: string }) =>
      item.course_business_id === blocked.course_business_id,
  );
  expect(movedItem.change_kind).toBe("moved");
  expect(movedItem.before_slot_id).toBe(blocked.slot_business_id);
  expect(movedItem.after_slot_id).not.toBe(blocked.slot_business_id);

  await page.getByRole("link", { name: "版本与回滚" }).click();
  await page.getByLabel("基准版本").selectOption(event.parent_schedule_id);
  await page.getByLabel("目标版本").selectOption(candidateScheduleId);
  await expect(page.getByText("变更课次")).toBeVisible();
  // 变更课次现在稳定是个位数，全页搜 "1" 会撞上别的文本，改成只在这块指标里断言。
  await expect(page.getByText("变更课次").locator("..")).toContainText(String(diff.changed_count));

  // 卡片上现在不止一个按钮（草稿有“发布”和“删除”，归档版本有“回滚”和“删除”），按 aria-label 点。
  //
  // 顺序必须是「先发父版本 → 再发候选 → 回滚到父版本」。回滚只接受 archived/rolled_back
  // 的版本（api.py rollback_schedule），而父版本只有在自己发布过、又被候选顶下去之后
  // 才会变成 archived。此前这里用 locator("button") 点“卡片上唯一那个按钮”，父版本一直
  // 停在 draft，实际点到的是“发布”——所以这个叫“回滚”的用例从来没验证过回滚，
  // 末尾断言 status === "published" 是被发布而不是被回滚满足的。
  const parentResponse = await api.get(`/api/v1/schedules/${event.parent_schedule_id}`);
  const parent = await parentResponse.json();
  const parentCard = page.getByTestId(`version-card-${parent.id}`);
  await parentCard.getByRole("button", { name: `发布版本 v${parent.version_no}` }).click();
  await expect(page.getByText("版本已发布")).toBeVisible();

  const candidateCard = page.getByTestId(`version-card-${candidate.id}`);
  await candidateCard.getByRole("button", { name: `发布版本 v${candidate.version_no}` }).click();
  await expect.poll(async () => {
    const response = await api.get(`/api/v1/schedules/${event.parent_schedule_id}`);
    return (await response.json()).status;
  }).toBe("archived");

  await parentCard.getByRole("button", { name: `回滚到版本 v${parent.version_no}` }).click();
  await expect.poll(async () => {
    const response = await api.get(`/api/v1/schedules/${candidate.id}`);
    return (await response.json()).status;
  }).toBe("rolled_back");
  await expect.poll(async () => {
    const response = await api.get(`/api/v1/schedules/${event.parent_schedule_id}`);
    return (await response.json()).status;
  }).toBe("published");

  await api.dispose();
});
