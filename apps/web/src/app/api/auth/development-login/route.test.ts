import { cookies } from "next/headers";
import { NextRequest, type NextRequest as NextRequestType } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));

const SUBJECT_ID = "b53b84f8-a97b-5d84-b028-76f6f60539a5";
const cookieStore = { delete: vi.fn(), get: vi.fn(), set: vi.fn() };

function configureDeterministic() {
  vi.stubEnv("ADMIN_COOKIE_SECURE", "false");
  vi.stubEnv("API_BASE_URL", "http://api.test:8000");
  vi.stubEnv("APP_BASE_URL", "http://localhost:3000");
  vi.stubEnv("APP_ENVIRONMENT", "test");
  vi.stubEnv("DETERMINISTIC_ADMIN_TOKEN", "local-admin-token");
  vi.stubEnv("DETERMINISTIC_REVIEWER_TOKEN", "local-reviewer-token");
  vi.stubEnv("ENABLE_DETERMINISTIC_IDENTITY", "true");
  vi.stubEnv("WEB_IDENTITY_PROVIDER", "deterministic");
}

function loginRequest(headers: HeadersInit = {}, role = "admin") {
  return new NextRequest("http://localhost:3000/api/auth/development-login", {
    body: new URLSearchParams({ role, token: "attacker-supplied-token" }),
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Origin: "http://localhost:3000",
      ...headers,
    },
    method: "POST",
  });
}

function mockedLoginRequest(headers: HeadersInit = {}) {
  const formData = vi.fn();
  const request = {
    formData,
    headers: new Headers(headers),
    method: "POST",
    url: "http://localhost:3000/api/auth/development-login",
  } as unknown as NextRequestType;
  return { formData, request };
}

function sessionResponse(value: unknown, status = 200) {
  const body = JSON.stringify(value);
  return new Response(body, {
    headers: {
      "Content-Length": String(Buffer.byteLength(body)),
      "Content-Type": "application/json",
    },
    status,
  });
}

beforeEach(() => {
  configureDeterministic();
  for (const method of Object.values(cookieStore)) method.mockReset();
  vi.mocked(cookies).mockResolvedValue(cookieStore as never);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("development login request boundary", () => {
  it.each([
    new Headers({ Origin: "https://attacker.example" }),
    new Headers({ "Sec-Fetch-Site": "cross-site" }),
  ])("rejects malicious browser headers before parsing or setting cookies", async (headers) => {
    const { formData, request } = mockedLoginRequest(headers);

    const response = await POST(request);

    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({
      detail: { code: "cross_site_request_rejected" },
    });
    expect(formData).not.toHaveBeenCalled();
    expect(cookies).not.toHaveBeenCalled();
  });

  it("stays disabled unless deterministic identity is explicitly selected and enabled", async () => {
    vi.stubEnv("ENABLE_DETERMINISTIC_IDENTITY", "false");
    const { formData, request } = mockedLoginRequest();

    const response = await POST(request);

    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({
      detail: { code: "development_login_disabled" },
    });
    expect(formData).not.toHaveBeenCalled();
    expect(cookies).not.toHaveBeenCalled();
  });

  it("remains disabled under required production OIDC configuration", async () => {
    vi.stubEnv("ADMIN_COOKIE_SECURE", "true");
    vi.stubEnv("APP_BASE_URL", "https://admin.exam-guru.example");
    vi.stubEnv("APP_ENVIRONMENT", "production");
    vi.stubEnv("DETERMINISTIC_ADMIN_TOKEN", undefined);
    vi.stubEnv("DETERMINISTIC_REVIEWER_TOKEN", undefined);
    vi.stubEnv("ENABLE_DETERMINISTIC_IDENTITY", undefined);
    vi.stubEnv("OIDC_AUTHORIZATION_ENDPOINT", "https://identity.example/oauth2/authorize");
    vi.stubEnv("OIDC_CLIENT_ID", "exam-guru-web");
    vi.stubEnv("OIDC_CLIENT_SECRET", "production-client-secret");
    vi.stubEnv("OIDC_ISSUER", "https://identity.example/realms/exam-guru");
    vi.stubEnv("OIDC_TOKEN_ENDPOINT", "https://identity.example/oauth2/token");
    vi.stubEnv("WEB_IDENTITY_PROVIDER", "oidc");
    const { formData, request } = mockedLoginRequest({
      Origin: "https://admin.exam-guru.example",
    });

    const response = await POST(request);

    expect(response.status).toBe(404);
    expect(formData).not.toHaveBeenCalled();
    expect(cookies).not.toHaveBeenCalled();
  });

  it("validates the selected server-owned token and derives the role only from backend session", async () => {
    const upstreamFetch = vi.fn().mockResolvedValue(
      sessionResponse({ roles: ["reviewer"], subject_id: SUBJECT_ID }),
    );
    vi.stubGlobal("fetch", upstreamFetch);

    const response = await POST(loginRequest({}, "admin"));

    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe("http://localhost:3000/admin/curriculum");
    expect(upstreamFetch).toHaveBeenCalledTimes(1);
    const [url, options] = upstreamFetch.mock.calls[0] as [URL, RequestInit];
    expect(url.toString()).toBe("http://api.test:8000/api/v1/auth/session");
    expect(new Headers(options.headers).get("Authorization")).toBe("Bearer local-admin-token");
    expect(options).toMatchObject({ cache: "no-store", method: "GET", redirect: "error" });
    expect(cookieStore.set).toHaveBeenCalledWith(
      "exam_guru_admin_role",
      "reviewer",
      expect.objectContaining({ httpOnly: true, sameSite: "strict" }),
    );
    expect(cookieStore.set).toHaveBeenCalledWith(
      "exam_guru_admin_token",
      "local-admin-token",
      expect.objectContaining({ httpOnly: true, sameSite: "strict" }),
    );
  });

  it("fails safely when an otherwise valid backend bearer token cannot be stored as a cookie", async () => {
    vi.stubEnv("DETERMINISTIC_ADMIN_TOKEN", "local;Path=/;token");
    const upstreamFetch = vi.fn().mockResolvedValue(
      sessionResponse({ roles: ["admin"], subject_id: SUBJECT_ID }),
    );
    vi.stubGlobal("fetch", upstreamFetch);

    const response = await POST(loginRequest());

    expect(upstreamFetch).toHaveBeenCalledTimes(1);
    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({
      detail: { code: "development_identity_unavailable" },
    });
    expect(cookieStore.set).not.toHaveBeenCalledWith(
      "exam_guru_admin_token",
      "local;Path=/;token",
      expect.anything(),
    );
  });

  it.each(["owner", "admin%00", "", "reviewer-admin"])(
    "cannot mint an unconfigured role from form value %s",
    async (role) => {
      const upstreamFetch = vi.fn();
      vi.stubGlobal("fetch", upstreamFetch);

      const response = await POST(loginRequest({}, role));

      expect(response.status).toBe(503);
      await expect(response.json()).resolves.toEqual({
        detail: { code: "development_identity_unavailable" },
      });
      expect(upstreamFetch).not.toHaveBeenCalled();
      expect(
        cookieStore.set.mock.calls.some(
          ([name, value, options]) =>
            name === "exam_guru_admin_token" && value !== "" && Number(options.maxAge) > 0,
        ),
      ).toBe(false);
    },
  );

  it.each([
    ["backend 401", sessionResponse({ detail: { code: "invalid_access_token" } }, 401)],
    ["backend 503", sessionResponse({ detail: { code: "identity_provider_unavailable" } }, 503)],
    ["malformed session", sessionResponse({ roles: ["owner"], subject_id: SUBJECT_ID })],
  ])("does not create a deterministic session for %s", async (_label, backendResponse) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(backendResponse));

    const response = await POST(loginRequest());

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({
      detail: { code: "development_identity_unavailable" },
    });
    expect(cookieStore.set).not.toHaveBeenCalledWith(
      "exam_guru_admin_token",
      "local-admin-token",
      expect.objectContaining({ maxAge: expect.any(Number) }),
    );
  });
});
