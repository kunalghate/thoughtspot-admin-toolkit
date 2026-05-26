import { defineConfig, devices } from "@playwright/test";

// First-time setup (run from frontend/):
//   npm install
//   npm run test:e2e:install   # downloads the Chromium browser
// Then run from frontend/ via:
//   npm run test:e2e

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // Spawn the Next.js dev server. The spec stubs every /api/v1/* call via
  // page.route(), so no FastAPI backend is required for these tests.
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
