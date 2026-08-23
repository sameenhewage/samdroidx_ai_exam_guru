import { defineConfig, devices } from "@playwright/test";

const channel = process.env.PLAYWRIGHT_CHANNEL;

export default defineConfig({
  expect: { timeout: 10_000 },
  fullyParallel: false,
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(channel ? { channel: channel as "chrome" } : {}),
      },
    },
  ],
  reporter: [["list"]],
  testDir: "./e2e",
  timeout: 60_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  workers: 1,
});
