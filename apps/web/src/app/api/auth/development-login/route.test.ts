import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));

function loginRequest(headers: HeadersInit = {}) {
  const formData = vi.fn();
  const request = {
    formData,
    headers: new Headers(headers),
    method: "POST",
    url: "http://localhost:3000/api/auth/development-login",
  } as unknown as NextRequest;
  return { formData, request };
}

beforeEach(() => {
  vi.stubEnv("APP_ENVIRONMENT", "test");
  vi.stubEnv("APP_BASE_URL", "http://localhost:3000");
  vi.stubEnv("ADMIN_COOKIE_SECURE", "false");
  vi.mocked(cookies).mockReset();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("development login request boundary", () => {
  it.each([
    new Headers({ Origin: "https://attacker.example" }),
    new Headers({ "Sec-Fetch-Site": "cross-site" }),
  ])("rejects malicious browser headers before parsing or setting cookies", async (headers) => {
    vi.stubEnv("ENABLE_DETERMINISTIC_IDENTITY", "true");
    vi.stubEnv("DETERMINISTIC_ADMIN_TOKEN", "local-admin-token");
    const { formData, request } = loginRequest(headers);

    const response = await POST(request);

    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({
      detail: { code: "cross_site_request_rejected" },
    });
    expect(formData).not.toHaveBeenCalled();
    expect(cookies).not.toHaveBeenCalled();
  });

  it("stays disabled unless the deterministic identity flag is explicitly true", async () => {
    vi.stubEnv("ENABLE_DETERMINISTIC_IDENTITY", "false");
    const { formData, request } = loginRequest();

    const response = await POST(request);

    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({
      detail: { code: "development_login_disabled" },
    });
    expect(formData).not.toHaveBeenCalled();
    expect(cookies).not.toHaveBeenCalled();
  });
});
