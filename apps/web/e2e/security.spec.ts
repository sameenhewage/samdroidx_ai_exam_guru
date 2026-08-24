import { expect, test } from "@playwright/test";

const contentSecurityPolicy = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const expectedHeaders = {
  "content-security-policy": contentSecurityPolicy,
  "cross-origin-opener-policy": "same-origin",
  "permissions-policy": "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
  "referrer-policy": "strict-origin-when-cross-origin",
  "x-content-type-options": "nosniff",
  "x-frame-options": "DENY",
};

test("browser responses carry hardening headers without breaking the admin app", async ({ page }) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  const loginResponse = await page.goto("/admin/login");
  expect(loginResponse).not.toBeNull();
  expect(loginResponse?.headers()).toMatchObject(expectedHeaders);
  expect(loginResponse?.headers()["strict-transport-security"]).toBeUndefined();

  await page.getByRole("button", { name: "Continue as admin" }).click();
  await expect(page).toHaveURL(/\/admin\/curriculum$/);
  await expect(page.getByRole("heading", { name: "Configuration & taxonomy" })).toBeVisible();

  const apiResponse = await page.request.get("/api/v1/admin/exam-configurations");
  expect(apiResponse.ok()).toBe(true);
  expect(apiResponse.headers()).toMatchObject(expectedHeaders);
  expect(browserErrors).toEqual([]);
});
