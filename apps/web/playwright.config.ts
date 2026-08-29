import { defineConfig, devices } from "@playwright/test";

import { requireIsolatedE2ERuntime } from "./playwright-runtime";

const channel = process.env.PLAYWRIGHT_CHANNEL;
const runtime = requireIsolatedE2ERuntime(process.env);

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
    baseURL: runtime.baseURL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  workers: 1,
});
