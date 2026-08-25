import { createHash } from "node:crypto";

import { cookies } from "next/headers";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as loginRoute from "./route";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));

const cookieStore = {
  delete: vi.fn(),
  get: vi.fn(),
  set: vi.fn(),
};

function configureOidc() {
  vi.stubEnv("ADMIN_COOKIE_SECURE", "true");
  vi.stubEnv("API_BASE_URL", "https://api.exam-guru.example");
  vi.stubEnv("APP_BASE_URL", "https://admin.exam-guru.example");
  vi.stubEnv("APP_ENVIRONMENT", "test");
  vi.stubEnv("OIDC_AUTHORIZATION_ENDPOINT", "https://identity.example/oauth2/authorize");
  vi.stubEnv("OIDC_CLIENT_ID", "exam-guru-web");
  vi.stubEnv("OIDC_CLIENT_SECRET", "client-secret-must-not-leak");
  vi.stubEnv("OIDC_ISSUER", "https://identity.example/realms/exam-guru");
  vi.stubEnv("OIDC_SCOPES", "openid profile email");
  vi.stubEnv("OIDC_TOKEN_ENDPOINT", "https://identity.example/oauth2/token");
  vi.stubEnv("WEB_IDENTITY_PROVIDER", "oidc");
}

function request(headers: HeadersInit = {}) {
  const requestHeaders = new Headers({
    "Content-Type": "application/x-www-form-urlencoded",
    Origin: "https://admin.exam-guru.example",
    "Sec-Fetch-Site": "same-origin",
  });
  new Headers(headers).forEach((value, name) => requestHeaders.set(name, value));
  return new NextRequest("https://admin.exam-guru.example/api/auth/oidc/login", {
    body: new URLSearchParams({ redirect: "https://attacker.example/steal" }),
    headers: requestHeaders,
    method: "POST",
  });
}

function latestCookie(name: string): [string, string, Record<string, unknown>] {
  const call = [...cookieStore.set.mock.calls]
    .reverse()
    .find(([cookieName]) => cookieName === name);
  if (!call) throw new Error(`missing ${name} cookie`);
  return call as [string, string, Record<string, unknown>];
}

beforeEach(() => {
  configureOidc();
  for (const method of Object.values(cookieStore)) method.mockReset();
  vi.mocked(cookies).mockReset();
  vi.mocked(cookies).mockResolvedValue(cookieStore as never);
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("OIDC login initiation", () => {
  it("is POST-only and rejects cross-site login CSRF before cookie access", async () => {
    expect("GET" in loginRoute).toBe(false);

    const response = await loginRoute.POST(
      request({ Origin: "https://attacker.example", "Sec-Fetch-Site": "cross-site" }),
    );

    expect(response.status).toBe(403);
    expect(cookies).not.toHaveBeenCalled();
  });

  it("rotates sessions and creates a fixed PKCE authorization request", async () => {
    const response = await loginRoute.POST(request());

    expect(response.status).toBe(303);
    const redirect = new URL(response.headers.get("Location") as string);
    expect(redirect.origin + redirect.pathname).toBe(
      "https://identity.example/oauth2/authorize",
    );
    expect([...redirect.searchParams.keys()].sort()).toEqual(
      [
        "client_id",
        "code_challenge",
        "code_challenge_method",
        "redirect_uri",
        "response_type",
        "scope",
        "state",
      ].sort(),
    );
    expect(redirect.searchParams.get("response_type")).toBe("code");
    expect(redirect.searchParams.get("client_id")).toBe("exam-guru-web");
    expect(redirect.searchParams.get("redirect_uri")).toBe(
      "https://admin.exam-guru.example/api/auth/oidc/callback",
    );
    expect(redirect.searchParams.get("scope")).toBe("openid profile email");
    expect(redirect.searchParams.get("code_challenge_method")).toBe("S256");
    expect(redirect.href).not.toContain("attacker.example");
    expect(redirect.href).not.toContain("client-secret-must-not-leak");

    const [, state, stateOptions] = latestCookie("exam_guru_oidc_state");
    const [, verifier, verifierOptions] = latestCookie("exam_guru_oidc_verifier");
    expect(state).toMatch(/^[A-Za-z0-9_-]{43,128}$/);
    expect(verifier).toMatch(/^[A-Za-z0-9._~-]{43,128}$/);
    expect(state).not.toBe(verifier);
    const expectedChallenge = createHash("sha256").update(verifier, "ascii").digest("base64url");
    expect(redirect.searchParams.get("state")).toBe(state);
    expect(redirect.searchParams.get("code_challenge")).toBe(expectedChallenge);
    for (const options of [stateOptions, verifierOptions]) {
      expect(options).toEqual({
        httpOnly: true,
        maxAge: 600,
        path: "/api/auth/oidc/callback",
        sameSite: "lax",
        secure: true,
      });
    }

    for (const name of ["exam_guru_admin_token", "exam_guru_admin_role"]) {
      expect(cookieStore.set).toHaveBeenCalledWith(
        name,
        "",
        expect.objectContaining({ maxAge: 0, path: "/", sameSite: "strict", secure: true }),
      );
    }
    expect(cookieStore.set.mock.calls.filter(([name]) => name === "exam_guru_oidc_state")).toHaveLength(
      2,
    );
    expect(
      cookieStore.set.mock.calls.filter(([name]) => name === "exam_guru_oidc_verifier"),
    ).toHaveLength(2);
  });

  it("uses non-secure transient cookies only for an explicitly configured local HTTP app", async () => {
    vi.stubEnv("ADMIN_COOKIE_SECURE", "false");
    vi.stubEnv("APP_BASE_URL", "http://localhost:3000");

    const response = await loginRoute.POST(
      new NextRequest("http://localhost:3000/api/auth/oidc/login", {
        headers: { Origin: "http://localhost:3000" },
        method: "POST",
      }),
    );

    expect(new URL(response.headers.get("Location") as string).searchParams.get("redirect_uri")).toBe(
      "http://localhost:3000/api/auth/oidc/callback",
    );
    expect(latestCookie("exam_guru_oidc_state")[2]).toEqual(
      expect.objectContaining({ secure: false }),
    );
    expect(latestCookie("exam_guru_oidc_verifier")[2]).toEqual(
      expect.objectContaining({ secure: false }),
    );
  });

  it("fails closed outside OIDC mode", async () => {
    vi.stubEnv("WEB_IDENTITY_PROVIDER", "deny");
    vi.stubEnv("OIDC_AUTHORIZATION_ENDPOINT", undefined);
    vi.stubEnv("OIDC_TOKEN_ENDPOINT", undefined);
    vi.stubEnv("OIDC_CLIENT_ID", undefined);
    vi.stubEnv("OIDC_CLIENT_SECRET", undefined);
    vi.stubEnv("OIDC_ISSUER", undefined);
    vi.stubEnv("OIDC_SCOPES", undefined);

    const response = await loginRoute.POST(request());

    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({ detail: { code: "oidc_login_disabled" } });
    expect(cookies).not.toHaveBeenCalled();
  });
});
