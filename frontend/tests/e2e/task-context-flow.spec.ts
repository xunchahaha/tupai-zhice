import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";

/**
 * 任务上下文主线的端到端业务场景（docs/roadmap/07-task-context.md）。
 *
 * 与 vitest 的组件级回归不同，这里走「真实前端 → 真实后端解析收口 → 真实 CP-SAT →
 * 真实验收」整条链路，只把模型输出固定成假模型（backend/scripts/fake_model_server.py，
 * 由 playwright.config.ts 的 webServer 起在 8002）。stub 若放在浏览器里会绕过后端的
 * `_finalize_assistant_interpret`，而「已结构化的要求被旧正则再次判为不支持、求解按钮
 * 被永久禁用」这类缺陷恰好出在那一层。
 *
 * 场景原话与假模型的场景标记逐字对应，业务标识来自 seed_demo_data（B01/T01/S05…）。
 */

const apiBaseURL = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:8001";
const modelBaseURL = process.env.E2E_MODEL_BASE_URL ?? "http://127.0.0.1:8002";
const adminPassword = "tupai-demo-admin-2026!";

// 核心示例句：只排一个班；教师本次不能上周三晚；尽量少动其他课程；先出草稿。
const FLAGSHIP_INSTRUCTION = "把初一数学A班重新排一下。教师甲周三晚上不能上，尽量少动其他课程，先给我草稿。";
const MEMORY_INSTRUCTION = "记住，这学期教师乙周三晚尽量别排，先看看初一英语A班";

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

async function pointBackendAtFakeModel(api: APIRequestContext) {
  const response = await api.post("/api/v1/integrations/ai/configuration", {
    data: {
      provider: "openai_compatible",
      base_url: `${modelBaseURL}/v1`,
      api_key: "e2e-fake-model-key",
      model: "e2e-fake-model",
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function loginAndOpenSolver(page: Page) {
  await page.goto("/login");
  await page.getByLabel("用户名").fill("admin");
  await page.getByLabel("密码").fill(adminPassword);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/overview");
  await page.getByRole("link", { name: "排课求解" }).click();
  await expect(page.getByRole("heading", { name: "排课求解" })).toBeVisible();
}

async function parseInstruction(page: Page, instruction: string) {
  const box = page.getByLabel("一句话排课指令");
  await expect(page.getByText("已接入", { exact: false }).first()).toBeVisible();
  await box.fill(instruction);
  await page.getByRole("button", { name: "让 AI 解析排课指令" }).click();
}

test("核心示例句：教师本次禁排结构化后可直接求解，禁排进入求解并通过验收，刷新后续办", async ({ page }) => {
  test.setTimeout(120_000);
  const api = await adminApi();
  await pointBackendAtFakeModel(api);

  await loginAndOpenSolver(page);
  await parseInstruction(page, FLAGSHIP_INSTRUCTION);

  // 确认卡：范围、本次任务要求逐条展示；模型把同一句话又抄进 unsupported_requirements，
  // 也不得把已结构化的要求判成「尚未进入求解」。
  const taskPanel = page.getByLabel("本次任务要求");
  await expect(taskPanel).toBeVisible();
  await expect(taskPanel).toContainText("教师甲周三晚上不能上");
  await expect(taskPanel).toContainText("T01");
  await expect(taskPanel).toContainText("S05");
  await expect(page.getByText("以下要求尚未进入求解")).toHaveCount(0);
  const confirm = page.getByRole("button", { name: "确认并开始求解" });
  await expect(confirm).toBeEnabled();

  const solveResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/assistant/solve") && response.request().method() === "POST",
  );
  await confirm.click();
  const solved = await solveResponse;
  expect(solved.ok(), await solved.text()).toBeTruthy();
  const run = await solved.json();
  expect(run.goal_id).toBeTruthy();

  // 请求体：禁排作为任务约束随请求带回后端。
  const requestBody = solved.request().postDataJSON();
  expect(requestBody.class_business_ids).toEqual(["B01"]);
  expect(requestBody.task_constraints).toHaveLength(1);
  expect(requestBody.task_constraints[0]).toMatchObject({
    subject_type: "teacher",
    subject_ids: ["T01"],
    slot_business_ids: ["S05", "S06"],
    hardness: "hard",
  });

  // 求解完成后验收报告落库：禁排复核项通过——说明该要求真的编译进了求解输入。
  await expect
    .poll(
      async () => {
        const detail = await (await api.get(`/api/v1/solver-runs/${run.id}`)).json();
        return detail.status === "completed" && detail.goal_report ? "ready" : detail.status;
      },
      { timeout: 60_000, intervals: [500, 1000, 2000] },
    )
    .toBe("ready");
  const finished = await (await api.get(`/api/v1/solver-runs/${run.id}`)).json();
  const forbidden = finished.goal_report.items.find(
    (item: { kind: string }) => item.kind === "forbidden_slot_free",
  );
  expect(forbidden, JSON.stringify(finished.goal_report)).toBeTruthy();
  expect(forbidden.passed, JSON.stringify(forbidden)).toBe(true);

  const report = page.getByLabel("目标验收报告");
  await expect(report).toBeVisible({ timeout: 30_000 });

  // 续办：goal_id 已写入 URL；刷新后原指令与任务上下文恢复，不是一个空白页面。
  await expect(page).toHaveURL(new RegExp(`goal=${run.goal_id}`));
  await page.reload();
  await expect(page.getByLabel("一句话排课指令")).toHaveValue(FLAGSHIP_INSTRUCTION, {
    timeout: 20_000,
  });

  await api.dispose();
});

test("显式「记住」：长期偏好直接生效并回执，不混进本次求解的禁排要求", async ({ page }) => {
  test.setTimeout(90_000);
  const api = await adminApi();
  await pointBackendAtFakeModel(api);

  await loginAndOpenSolver(page);
  await parseInstruction(page, MEMORY_INSTRUCTION);

  const receipts = page.getByLabel("记忆动作回执");
  await expect(receipts).toBeVisible();
  await expect(receipts).toContainText("已记住");
  await expect(receipts).toContainText("T02");
  await expect(page.getByText("以下要求尚未进入求解")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "确认并开始求解" })).toBeEnabled();

  // 偏好落成 confirmed 的长期条目，来源标注为用户明确声明并带原话回链。
  const listed = await (
    await api.get("/api/v1/memory/preferences", { params: { subject_id: "T02" } })
  ).json();
  const entry = listed.find(
    (item: { predicate: string; constraint: { slot_ids?: string[] } }) =>
      item.predicate === "avoid_slot" &&
      JSON.stringify(item.constraint.slot_ids) === JSON.stringify(["S05", "S06"]),
  );
  expect(entry, JSON.stringify(listed)).toBeTruthy();
  expect(entry.status).toBe("confirmed");
  expect(entry.source).toBe("explicit_stated");
  expect(entry.provenance.via).toBe("assistant_interpret");
  expect(entry.provenance.instruction).toContain("记住");

  // 记忆页可见，可从那里修改或撤销（回执里的固定提示）；来源如实标注为一句话排课。
  await page.getByRole("link", { name: "记忆与偏好" }).click();
  await expect(page.getByRole("heading", { name: "记忆与偏好" })).toBeVisible();
  await expect(page.getByText("教师乙").first()).toBeVisible();
  await expect(page.getByText("一句话排课（明确声明）").first()).toBeVisible();

  await api.dispose();
});

test("显式「不要用了」：撤销既有偏好，目标只能是上下文里真实存在的条目", async ({ page }) => {
  test.setTimeout(90_000);
  const api = await adminApi();
  await pointBackendAtFakeModel(api);

  // 自备一条待撤销的长期偏好（不依赖别的用例先跑）：教师乙避开周三晚。
  const created = await api.post("/api/v1/memory/preferences", {
    data: {
      subject_type: "teacher",
      subject_id: "T02",
      predicate: "avoid_slot",
      constraint: { slot_ids: ["S05", "S06"] },
      source: "explicit_stated",
      evidence: ["e2e 预置"],
      weight: 50,
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const seeded = await created.json();

  await loginAndOpenSolver(page);
  await parseInstruction(page, "旧的教师乙周三晚偏好不要用了");

  const receipts = page.getByLabel("记忆动作回执");
  await expect(receipts).toBeVisible();
  await expect(receipts).toContainText("失效");
  await expect(receipts).toContainText("记忆」页查看历史");

  // 条目状态被真实推进到 expired，撤销来自用户原话并带回链。
  const listed = await (await api.get("/api/v1/memory/preferences")).json();
  const entry = listed.find((item: { id: string }) => item.id === seeded.id);
  expect(entry, JSON.stringify(listed)).toBeTruthy();
  expect(entry.status).toBe("expired");
  expect(entry.provenance.via).toBe("assistant_interpret");
  expect(entry.provenance.instruction).toContain("不要用了");

  await api.dispose();
});

test("主体无法确认：登记目标 → 目标跟踪补参 → 回求解页再解析同一句话 → 求解并通过验收", async ({ page }) => {
  test.setTimeout(150_000);
  const api = await adminApi();
  await pointBackendAtFakeModel(api);

  await loginAndOpenSolver(page);
  await parseInstruction(page, "重排初二物理A班，丙老师周五晚上不能上");

  // 模型没能唯一确定「丙老师」：要求没有进入求解，确认求解被拦住——但不是死路，
  // 可以先登记目标，带着待补参的清单项去补。
  await expect(page.getByText("以下要求尚未进入求解")).toBeVisible();
  await expect(page.getByRole("button", { name: "确认并开始求解" })).toBeDisabled();
  await page.getByRole("button", { name: "登记为目标，稍后补充" }).click();

  // 落地在目标详情：待量化项 + 「补齐禁排参数」入口。
  await expect(page).toHaveURL(/\/goals\?goal=/);
  const goalId = new URL(page.url()).searchParams.get("goal");
  expect(goalId).toBeTruthy();
  await expect(page.getByText("目标详情")).toBeVisible();
  await page.getByRole("button", { name: "补齐禁排参数" }).click();
  await page.getByLabel("禁排主体", { exact: true }).selectOption({ label: "教师丙" });
  await page.getByLabel("添加禁排时段").selectOption({ label: "周五 18:30-20:00" });
  await page.getByLabel("添加禁排时段").selectOption({ label: "周五 20:10-21:40" });
  await page.getByRole("button", { name: "保存参数" }).click();
  await expect
    .poll(async () => {
      const goal = await (await api.get(`/api/v1/goals/${goalId}`)).json();
      const item = goal.checklist.find((entry: { kind: string }) => entry.kind === "forbidden_slot_free");
      return JSON.stringify([item?.params?.subject_ids, item?.params?.slot_business_ids, Boolean(item?.params?.needs_params)]);
    })
    .toBe(JSON.stringify([["T03"], ["S09", "S10"], false]));

  // 回求解页：原指令已恢复；重新解析同一句话，补全的禁排已在目标里 → 不再被拦。
  await page.getByRole("button", { name: "修正范围后重新求解" }).click();
  await expect(page).toHaveURL(new RegExp(`/solver[?]goal=${goalId}`));
  await expect(page.getByLabel("一句话排课指令")).toHaveValue("重排初二物理A班，丙老师周五晚上不能上", {
    timeout: 20_000,
  });
  await page.getByRole("button", { name: "让 AI 解析排课指令" }).click();
  const confirm = page.getByRole("button", { name: "确认并开始求解" });
  await expect(confirm).toBeEnabled();
  await expect(page.getByText("以下要求尚未进入求解")).toHaveCount(0);

  const solveResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/assistant/solve") && response.request().method() === "POST",
  );
  await confirm.click();
  const solved = await solveResponse;
  expect(solved.ok(), await solved.text()).toBeTruthy();
  const run = await solved.json();
  expect(run.goal_id).toBe(goalId);

  // 补参后的禁排进入了求解并通过独立验收（验收器不信任求解器自报）。
  await expect
    .poll(
      async () => {
        const detail = await (await api.get(`/api/v1/solver-runs/${run.id}`)).json();
        return detail.status === "completed" && detail.goal_report ? "ready" : detail.status;
      },
      { timeout: 60_000, intervals: [500, 1000, 2000] },
    )
    .toBe("ready");
  const finished = await (await api.get(`/api/v1/solver-runs/${run.id}`)).json();
  const forbidden = finished.goal_report.items.find(
    (item: { kind: string }) => item.kind === "forbidden_slot_free",
  );
  expect(forbidden, JSON.stringify(finished.goal_report)).toBeTruthy();
  expect(forbidden.passed, JSON.stringify(forbidden)).toBe(true);
  expect(forbidden.verdict).not.toBe("unverifiable");

  await api.dispose();
});
