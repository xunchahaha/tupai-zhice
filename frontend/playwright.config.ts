import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

const frontendDir = path.dirname(fileURLToPath(import.meta.url));
const projectDir = path.resolve(frontendDir, "..");
const backendDir = path.join(projectDir, "backend");
const e2eDatabase = path.join(projectDir, "data", "e2e.db").replaceAll("\\", "/");
const apiBaseURL = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:8001";
const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5174";
const useExternalServers = process.env.E2E_EXTERNAL_SERVERS === "1";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: "list",
  outputDir: "test-results",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: useExternalServers
    ? undefined
    : [
        {
          command: "uv run python scripts/reset_e2e_db.py && uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8001",
          cwd: backendDir,
          url: `${apiBaseURL}/api/v1/health/ready`,
          reuseExistingServer: false,
          env: {
            ...process.env,
            DATABASE_URL: `sqlite:///${e2eDatabase}`,
            JWT_SECRET: "e2e-secret-with-at-least-32-characters",
            AILY_SKILL_API_KEY: "aily-e2e-key",
            CORS_ORIGINS: baseURL,
          },
        },
        {
          command: "pnpm dev --host 127.0.0.1 --port 5174",
          cwd: frontendDir,
          url: baseURL,
          reuseExistingServer: false,
          env: { ...process.env, VITE_API_BASE_URL: apiBaseURL },
        },
      ],
});
