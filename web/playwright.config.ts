import { defineConfig, devices } from "@playwright/test";

// Runs against the production build (npm run build) served by vite preview.
export default defineConfig({
  testDir: "e2e",
  timeout: 180_000,
  expect: { timeout: 60_000 },
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:4173", trace: "retain-on-failure", viewport: { width: 1440, height: 900 } },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, channel: process.env.PW_CHANNEL } }],
  webServer: { command: "npm run preview", url: "http://localhost:4173", reuseExistingServer: true, timeout: 60_000 },
});
