import { cookies } from "next/headers";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));

const cookieStore = { delete: vi.fn(), get: vi.fn(), set: vi.fn() };

function configureBase() {
  vi.stubEnv("APP_ENVIRONMENT", "test");
  vi.stubEnv("APP_BASE_URL", "https://admin.exam-guru.example");
  vi.stubEnv("API_BASE_URL", "https://api.exam-guru.example");
  vi.stubEnv("ADMIN_COOKIE_SECURE", "true");
  vi.stubEnv("WEB_IDENTITY_PROVIDER", "deny");
}

function configureOidc(endSession = true) {
  vi.stubEnv("WEB_IDENTITY_PROVIDER", "oidc");
  vi.stubEnv("OIDC_AUTHORIZATION_ENDPOINT", "https://identity.example/oauth2/authorize");
  vi.stubEnv("OIDC_TOKEN_ENDPOINT", "https://identity.example/oauth2/token");
  vi.stubEnv("OIDC_CLIENT_ID", "exam-guru-web");
  vi.stubEnv("OIDC_CLIENT_SECRET", "client-secret");
  vi.stubEnv("OIDC_ISSUER", "https://identity.example/realms/exam-guru");
  vi.stubEnv("OIDC_SCOPES", "openid profile");
  if (endSession) {
    vi.stubEnv("OIDC_END_SESSION_ENDPOINT", "https://identity.example/oauth2/logout");
  }
}

function logoutRequest(headers: HeadersInit = {}) {
  const requestHeaders = new Headers({
    "Content-Type": "application/x-www-form-urlencoded",
    Origin: "https://admin.exam-guru.example",
  });
  new Headers(headers).forEach((value, name) => requestHeaders.set(name, value));
  return new NextRequest("https://admin.exam-guru.example/api/auth/logout", {
    body: new URLSearchParams({ id_token_hint: "attacker-token", target: "https://attacker.example" }),
    headers: requestHeaders,
    method: "POST",
  });
}

function expectCookiesCleared() {
  for (const [name, path, sameSite] of [
    ["exam_guru_admin_token", "/", "strict"],
    ["exam_guru_admin_role", "/", "strict"],
    ["exam_guru_oidc_state", "/api/auth/oidc/callback", "lax"],
    ["exam_guru_oidc_verifier", "/api/auth/oidc/callback", "lax"],
  ] as const) {
    expect(cookieStore.set).toHaveBeenCalledWith(
      name,
      "",
      expect.objectContaining({
        httpOnly: true,
        maxAge: 0,
        path,
        sameSite,
        secure: true,
      }),
    );
  }
}

beforeEach(() => {
  configureBase();
  for (const method of Object.values(cookieStore)) method.mockReset();
  vi.mocked(cookies).mockResolvedValue(cookieStore as never);
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

  it("clears session and transient cookies before a fixed local redirect", async () => {
    const response = await POST(logoutRequest());

    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe("https://admin.exam-guru.example/admin/login");
    expectCookiesCleared();
  });

  it("uses only fixed RP-initiated logout parameters when configured", async () => {
    configureOidc();

    const response = await POST(logoutRequest());

    expect(response.status).toBe(303);
    const redirect = new URL(response.headers.get("Location") as string);
    expect(redirect.origin + redirect.pathname).toBe("https://identity.example/oauth2/logout");
    expect(Object.fromEntries(redirect.searchParams)).toEqual({
      client_id: "exam-guru-web",
      post_logout_redirect_uri: "https://admin.exam-guru.example/admin/login",
    });
    expect(redirect.href).not.toContain("attacker.example");
    expect(redirect.href).not.toContain("attacker-token");
    expectCookiesCleared();
  });

  it("uses the local redirect when OIDC has no end-session endpoint", async () => {
    configureOidc(false);

    const response = await POST(logoutRequest());

    expect(response.headers.get("Location")).toBe("https://admin.exam-guru.example/admin/login");
    expectCookiesCleared();
  });
});
