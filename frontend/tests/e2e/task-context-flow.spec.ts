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

// 登录后直接落在排课助手：一句话需求、确认、求解、验收都在这一页。
async function loginAndOpenAssistant(page: Page) {
  await page.goto("/login");
  await page.getByLabel("用户名").fill("admin");
  await page.getByLabel("密码").fill(adminPassword);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/assistant");
  await expect(page.getByRole("heading", { name: "排课助手", exact: true })).toBeVisible();
}

async function parseInstruction(page: Page, instruction: string) {
  const box = page.getByLabel("排课需求");
  await expect(page.getByText("已接入", { exact: false }).first()).toBeVisible();
  await box.fill(instruction);
  await page.getByRole("button", { name: "让 AI 解析", exact: true }).click();
}

test("核心示例句：教师本次禁排结构化后可直接求解，禁排进入求解并通过验收，刷新后续办", async ({ page }) => {
  test.setTimeout(120_000);
  const api = await adminApi();
  await pointBackendAtFakeModel(api);

  await loginAndOpenAssistant(page);
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

  // 结果卡直接给出要求核对结论（不必进「目标」页）：所有要求已落实。
  const report = page.getByLabel("要求核对");
  await expect(report).toBeVisible({ timeout: 30_000 });
  await expect(report).toContainText("要求已落实");

  // 续办：goal_id 已写入 URL；刷新后你交代的需求与任务上下文恢复，不是一个空白页面。
  await expect(page).toHaveURL(new RegExp(`[?&]goal=${run.goal_id}`));
  await page.reload();
  await expect(page.getByLabel("任务进展")).toContainText(FLAGSHIP_INSTRUCTION, { timeout: 20_000 });

  await api.dispose();
});

test("显式「记住」：长期偏好直接生效并回执，不混进本次求解的禁排要求", async ({ page }) => {
  test.setTimeout(90_000);
  const api = await adminApi();
  await pointBackendAtFakeModel(api);

  await loginAndOpenAssistant(page);
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

  // 常用偏好页可见，可从回执旁的链接直达该条修改或撤销；来源如实标注为一句话排课。
  await receipts.getByRole("link", { name: "在常用偏好里修改或撤销" }).click();
  await expect(page).toHaveURL(/\/memory\?.*entry=/);
  await expect(page.getByRole("heading", { name: "常用偏好", exact: true })).toBeVisible();
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

  await loginAndOpenAssistant(page);
  await parseInstruction(page, "旧的教师乙周三晚偏好不要用了");

  const receipts = page.getByLabel("记忆动作回执");
  await expect(receipts).toBeVisible();
  await expect(receipts).toContainText("失效");
  // 失效的偏好在「常用偏好」里仍可查看历史：回执旁给出直达入口。
  await expect(receipts.getByRole("link", { name: "在常用偏好里修改或撤销" })).toBeVisible();

  // 条目状态被真实推进到 expired，撤销来自用户原话并带回链。
  const listed = await (await api.get("/api/v1/memory/preferences")).json();
  const entry = listed.find((item: { id: string }) => item.id === seeded.id);
  expect(entry, JSON.stringify(listed)).toBeTruthy();
  expect(entry.status).toBe("expired");
  expect(entry.provenance.via).toBe("assistant_interpret");
  expect(entry.provenance.instruction).toContain("不要用了");

  await api.dispose();
});

test("主体无法确认：补充条件（自动登记任务）→ 补参后自动重新解析 → 刷新续办再解析同一句话 → 求解并通过验收", async ({ page }) => {
  test.setTimeout(150_000);
  const api = await adminApi();
  await pointBackendAtFakeModel(api);

  await loginAndOpenAssistant(page);
  await parseInstruction(page, "重排初二物理A班，丙老师周五晚上不能上");

  // 模型没能唯一确定「丙老师」：要求没有进入求解，确认求解被拦住——但不是死路，
  // 直接在确认卡上「补充条件」：先登记任务，再就地补齐待补参数的清单项。
  await expect(page.getByText("以下要求尚未进入求解")).toBeVisible();
  await expect(page.getByRole("button", { name: "确认并开始求解" })).toBeDisabled();
  await page.getByRole("button", { name: "补充条件", exact: true }).click();

  // 任务已登记并写进 URL；补参表单直接展开（不用去别的页面）。
  await expect(page).toHaveURL(/\/assistant\?.*goal=/);
  const goalId = new URL(page.url()).searchParams.get("goal");
  expect(goalId).toBeTruthy();
  await expect(page.getByLabel("任务进展")).toContainText("重排初二物理A班，丙老师周五晚上不能上");
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

  // 补充保存后助手自动用同一任务重新解析：补全的禁排已在任务里 → 不再被拦。
  await expect(page.getByRole("button", { name: "确认并开始求解" })).toBeEnabled({ timeout: 30_000 });
  await expect(page.getByText("以下要求尚未进入求解")).toHaveCount(0);

  // 续办：刷新后任务仍在（goal 在 URL 里），原指令已恢复；再解析同一句话，同样不被拦。
  await page.reload();
  await expect(page.getByLabel("任务需求")).toHaveValue("重排初二物理A班，丙老师周五晚上不能上", {
    timeout: 20_000,
  });
  await page.getByRole("button", { name: "让 AI 解析", exact: true }).click();
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

test("课表交接：只调整选中的那一节课、基于所选草稿——范围与基准跨首次求解、刷新续办和手动重跑都不丢", async ({ page }) => {
  test.setTimeout(150_000);
  const api = await adminApi();
  await pointBackendAtFakeModel(api);

  // 造一份草稿，并从中选一节 B01 的课：它就是课表里「交给助手继续处理」带来的调整对象。
  const base = await api.post("/api/v1/solver-runs", { data: { class_business_ids: ["B01"], time_limit_seconds: 30, wait: true } });
  expect(base.ok(), await base.text()).toBeTruthy();
  const baseRun = await base.json();
  const summaries = await (await api.get("/api/v1/schedules")).json();
  const draft = summaries.find((item: { solver_run_id: string }) => item.solver_run_id === baseRun.id);
  expect(draft, "求解应产出一份草稿").toBeTruthy();
  const detail = await (await api.get(`/api/v1/schedules/${draft.id}`)).json();
  const lesson = detail.assignments.find((item: { class_business_id: string }) => item.class_business_id === "B01");
  expect(lesson).toBeTruthy();

  await loginAndOpenAssistant(page);
  const handoffUrl = `/assistant?prompt=${encodeURIComponent(FLAGSHIP_INSTRUCTION)}&base=${draft.id}&lesson=${encodeURIComponent(lesson.course_business_id)}`;
  await page.goto(handoffUrl);
  const notice = page.getByLabel("调整对象");
  await expect(notice).toContainText(`v${draft.version_no}`);
  if (lesson.lesson_date) await expect(notice).toContainText(lesson.lesson_date);
  await page.getByRole("button", { name: "让 AI 解析", exact: true }).click();

  const confirm = page.getByRole("button", { name: "确认并开始求解" });
  await expect(confirm).toBeEnabled();
  const solveResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/v1/assistant/solve") && response.request().method() === "POST",
  );
  await confirm.click();
  const solved = await solveResponse;
  expect(solved.ok(), await solved.text()).toBeTruthy();
  const run = await solved.json();
  // 首次请求：基准是所选草稿，目标是这一节课。
  const firstBody = solved.request().postDataJSON();
  expect(firstBody.parent_schedule_id).toBe(draft.id);
  expect(firstBody.course_business_ids).toEqual([lesson.course_business_id]);

  // 任务约定落在后端：课次限定 + 原始基准，刷新/重跑/续办从这里恢复。
  const goalContext = async () => (await (await api.get(`/api/v1/goals/${run.goal_id}`)).json()).context;
  await expect.poll(async () => (await goalContext())?.scope?.course_business_ids).toEqual([lesson.course_business_id]);
  expect((await goalContext()).base_schedule_id).toBe(draft.id);
  await expect
    .poll(async () => (await (await api.get(`/api/v1/solver-runs/${run.id}`)).json()).status, { timeout: 60_000, intervals: [500, 1000, 2000] })
    .toBe("completed");

  // 刷新续办：地址栏只剩 goal，课次限定从任务里恢复，说明还在。
  await expect(page).toHaveURL(new RegExp(`[?&]goal=${run.goal_id}`));
  await page.reload();
  await expect(page.getByLabel("调整对象")).toContainText("本任务只针对课表里选中的 1 节课", { timeout: 20_000 });

  // 手动重跑（与加预算重跑同一条提交路径）：课次范围原样沿用，不会因为「只是重跑」而放大成整批。
  const manualToggle = page.getByRole("button", { name: "手动排课（自己设置参数）" });
  if ((await manualToggle.getAttribute("aria-expanded")) !== "true") await manualToggle.click();
  const rerunResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/v1/solver-runs") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "按参数开始求解" }).click();
  const rerun = await rerunResponse;
  expect(rerun.ok(), await rerun.text()).toBeTruthy();
  const rerunBody = rerun.request().postDataJSON();
  expect(rerunBody.goal_id).toBe(run.goal_id);
  expect(rerunBody.course_business_ids).toEqual([lesson.course_business_id]);
  expect((await goalContext()).scope.course_business_ids).toEqual([lesson.course_business_id]);

  await api.dispose();
});
