import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));

function logoutRequest(headers: HeadersInit = {}) {
  return {
    headers: new Headers(headers),
    method: "POST",
    url: "http://localhost:3000/api/auth/logout",
  } as unknown as NextRequest;
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

describe("logout request boundary", () => {
  it.each([
    new Headers({ Origin: "https://attacker.example" }),
    new Headers({ "Sec-Fetch-Site": "cross-site" }),
  ])("rejects malicious browser headers before deleting cookies", async (headers) => {
    const response = await POST(logoutRequest(headers));

    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({
      detail: { code: "cross_site_request_rejected" },
    });
    expect(cookies).not.toHaveBeenCalled();
  });
});
