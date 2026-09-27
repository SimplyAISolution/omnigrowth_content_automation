import { defineConfig } from "@playwright/test";

/**
 * Browser journeys against the REAL API on a throwaway, fixture-seeded database
 * (scripts/ui_env.py). Uses the installed Microsoft Edge (channel "msedge"), so no browser is
 * downloaded. Run with `npm run e2e` (prepares the environment first, drops it afterwards).
 */
const API_PORT = Number(process.env.OG_UI_API_PORT ?? 8012);
const WEB_PORT = 4173;

export default defineConfig({
  testDir: "e2e",
  outputDir: "test-results",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  fullyParallel: false,
  reporter: [["list"]],
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    channel: "msedge",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1280, height: 900 } }, testIgnore: /mobile\.spec/ },
    { name: "mobile", use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }, testMatch: /mobile\.spec/ },
  ],
  webServer: [
    {
      command: "uv run --project .. python ../scripts/ui_env.py serve",
      url: `http://127.0.0.1:${API_PORT}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { OG_UI_API_PORT: String(API_PORT) },
    },
    {
      command: `npx vite build && npx vite preview --port ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { OG_API_URL: `http://127.0.0.1:${API_PORT}` },
    },
  ],
});
