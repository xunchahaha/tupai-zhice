import { expect, request, test, type Page } from "@playwright/test";

const apiBaseURL = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:8001";

// 页头「排课流程」里也有同名链接，侧栏入口必须限定在主导航内。
const sidebar = (page: Page) => page.getByRole("navigation", { name: "主导航" });

test("管理员完成排课、调课、回滚和集成接入引导流程", async ({ page }, testInfo) => {
  test.setTimeout(75_000);
  const anonymousApi = await request.newContext({ baseURL: apiBaseURL });
  const login = await anonymousApi.post("/api/v1/auth/token", {
    form: { username: "admin", password: "tupai-demo-admin-2026!" },
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
  await page.getByLabel("密码").fill("tupai-demo-admin-2026!");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  // 登录后落在排课助手；侧栏只有 排课助手 / 课表 / 基础资料 与底部的 设置。
  await page.waitForURL("**/assistant");
  await expect(page.getByRole("heading", { name: "排课助手", exact: true })).toBeVisible();

  // 设置里先完成飞书接入引导，再从「高级管理」进入学校通用规则（规则页不在侧栏，这是真实的点击路径）。
  await sidebar(page).getByRole("link", { name: "设置", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "飞书生产接入向导" })).toBeVisible();
  await expect(page.getByText("管理员不需要手工创建任何飞书数据表。")).toBeVisible();
  await page.getByRole("button", { name: "稍后继续" }).click();
  await expect(page.getByRole("heading", { name: "飞书生产连接" })).toBeVisible();
  await expect(page.getByText("应用配置", { exact: true })).toBeVisible();
  await expect(page.getByText("生产接入步骤", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "一键同步当前方案" })).toBeDisabled();

  await page.getByRole("link", { name: /学校通用规则/ }).click();
  await expect(page).toHaveURL(/\/rules$/);
  await expect(page.getByRole("heading", { name: "学校通用规则", exact: true })).toBeVisible();
  // 规则页仍归属「设置」，侧栏的设置入口保持高亮。
  await expect(sidebar(page).getByRole("link", { name: "设置", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(page.getByText(proposalId, { exact: true })).toBeVisible();
  await page.getByText(proposalId, { exact: true }).click();
  await page.getByRole("button", { name: "确认生效" }).click();
  await expect(page.getByText("规则状态已更新")).toBeVisible();
  await expect.poll(async () => {
    const response = await api.get(`/api/v1/rules?status=active`);
    const rules = await response.json();
    return rules.some((rule: { business_id: string }) => rule.business_id === proposalId);
  }).toBeTruthy();

  // 手动排课路径随时可用：助手首页展开「手动排课（自己设置参数）」，按参数提交，请求仍是 POST /api/v1/solver-runs。
  await sidebar(page).getByRole("link", { name: "排课助手", exact: true }).click();
  await expect(page).toHaveURL(/\/assistant$/);
  // AI 还没配置时助手会自动展开手动排课面板（唯一入口），已配置时默认收起：先等 AI 状态读出来，按当前状态决定要不要点。
  await expect(page.getByText(/已接入|AI 模型待配置/).first()).toBeVisible();
  const manualToggle = page.getByRole("button", { name: "手动排课（自己设置参数）" });
  if ((await manualToggle.getAttribute("aria-expanded")) !== "true") await manualToggle.click();
  await expect(page.getByLabel("手动排课参数")).toBeVisible();
  const solverResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/solver-runs") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "按参数开始求解" }).click();
  const solverRun = await (await solverResponse).json();
  await expect(page.getByText("求解任务已创建")).toBeVisible();
  await expect.poll(async () => {
    const response = await api.get(`/api/v1/solver-runs/${solverRun.id}`);
    return (await response.json()).model_status;
  }, { timeout: 30_000 }).toBe("OPTIMAL");
  // 结果卡直接给出草稿；求解结论「已证明最优」是技术指标，收在「查看详情」里，与任务状态区分开。
  await expect(page.getByRole("heading", { name: /^已生成草稿/ })).toBeVisible();
  await expect(page.getByText("草稿 · 未发布")).toBeVisible();
  await page.getByRole("button", { name: /^查看详情/ }).click();
  await expect(page.getByText("已证明最优", { exact: true }).first()).toBeVisible();

  await sidebar(page).getByRole("link", { name: "课表", exact: true }).click();
  await expect(page.getByRole("heading", { name: "课表", exact: true })).toBeVisible();
  await expect(page.getByText(/^已安排:\s*[1-9]\d*\s*节课次$/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "全部课次安排" })).toBeVisible();

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

  // 局部调课在课表页的「调整」视图；先把课表切到刚求解出的那一版，调课的父课表随之带入。
  await page.getByLabel("课表版本").selectOption(parentSummary.id);
  await page.getByRole("tab", { name: "调整" }).click();
  await expect(page).toHaveURL(/view=adjust/);
  await expect(page.getByLabel("父课表")).toHaveValue(parentSummary.id);
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

  // 生成候选只是草稿；发布/回滚在同一页的「历史版本」里由有审批权限的人决定。
  await page.getByRole("tab", { name: "历史版本" }).click();
  await expect(page).toHaveURL(/view=history/);
  await page.getByLabel("基准版本").selectOption(event.parent_schedule_id);
  await page.getByLabel("目标版本").selectOption(candidateScheduleId);
  const changedSummary = page.getByText(/^变更:\s*\d+\s*节$/);
  await expect(changedSummary).toBeVisible();
  await expect(changedSummary.locator("strong")).toHaveText(String(diff.changed_count));

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
  // 发布/回滚会改变对外的当前课表，先弹二次确认，点了确认才提交。
  await parentCard.getByRole("button", { name: `发布版本 v${parent.version_no}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "确认发布" }).click();
  await expect(page.getByText("版本已发布")).toBeVisible();

  const candidateCard = page.getByTestId(`version-card-${candidate.id}`);
  await candidateCard.getByRole("button", { name: `发布版本 v${candidate.version_no}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "确认发布" }).click();
  await expect.poll(async () => {
    const response = await api.get(`/api/v1/schedules/${event.parent_schedule_id}`);
    return (await response.json()).status;
  }).toBe("archived");

  await parentCard.getByRole("button", { name: `回滚到版本 v${parent.version_no}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "确认回滚" }).click();
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
