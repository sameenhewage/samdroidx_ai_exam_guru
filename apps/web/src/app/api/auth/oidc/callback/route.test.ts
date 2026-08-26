import { cookies } from "next/headers";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "./route";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));

const STATE = "s".repeat(43);
const VERIFIER = "v".repeat(43);
const CODE = "provider-authorization-code";
const ACCESS_TOKEN = "opaque-access-token";
const CLIENT_CREDENTIAL = ["client", "credential", "must-not-leak"].join("-");
const ISSUER = "https://identity.example/realms/exam-guru";
const PREEXISTING_ACCESS_TOKEN = "preexisting-session-token";
const SUBJECT_ID = "b53b84f8-a97b-5d84-b028-76f6f60539a5";

const cookieStore = {
  delete: vi.fn(),
  get: vi.fn((name: string) => {
    if (name === "exam_guru_oidc_state") return { name, value: STATE };
    if (name === "exam_guru_oidc_verifier") return { name, value: VERIFIER };
    if (name === "exam_guru_admin_token") return { name, value: PREEXISTING_ACCESS_TOKEN };
    if (name === "exam_guru_admin_role") return { name, value: "reviewer" };
    return undefined;
  }),
  set: vi.fn(),
};

function configureOidc() {
  vi.stubEnv("ADMIN_COOKIE_SECURE", "true");
  vi.stubEnv("ADMIN_SESSION_MAX_AGE_SECONDS", "3600");
  vi.stubEnv("API_BASE_URL", "https://api.exam-guru.example");
  vi.stubEnv("APP_BASE_URL", "https://admin.exam-guru.example");
  vi.stubEnv("APP_ENVIRONMENT", "test");
  vi.stubEnv("OIDC_AUTHORIZATION_ENDPOINT", "https://identity.example/oauth2/authorize");
  vi.stubEnv("OIDC_CLIENT_ID", "exam-guru-web");
  vi.stubEnv("OIDC_CLIENT_SECRET", CLIENT_CREDENTIAL);
  vi.stubEnv("OIDC_HTTP_TIMEOUT_MS", "2500");
  vi.stubEnv("OIDC_ISSUER", ISSUER);
  vi.stubEnv("OIDC_SCOPES", "openid profile email");
  vi.stubEnv("OIDC_TOKEN_ENDPOINT", "https://identity.example/oauth2/token");
  vi.stubEnv("WEB_IDENTITY_PROVIDER", "oidc");
}

function callbackRequest(query = `code=${CODE}&state=${STATE}`) {
  return new NextRequest(`https://admin.exam-guru.example/api/auth/oidc/callback?${query}`, {
    headers: {
      "Sec-Fetch-Mode": "navigate",
      "Sec-Fetch-Site": "cross-site",
    },
    method: "GET",
  });
}

function responseWithBody(
  body: string,
  options: {
    contentLength?: string | null;
    contentType?: string;
    status?: number;
  } = {},
) {
  const headers = new Headers();
  if (options.contentLength !== null) {
    headers.set(
      "Content-Length",
      options.contentLength ?? String(Buffer.byteLength(body, "utf8")),
    );
  }
  headers.set("Content-Type", options.contentType ?? "application/json; charset=utf-8");
  return new Response(body, { headers, status: options.status ?? 200 });
}

function jsonResponse(
  value: unknown,
  options: Parameters<typeof responseWithBody>[1] = {},
) {
  return responseWithBody(JSON.stringify(value), options);
}

function successfulTokenResponse(overrides: Record<string, unknown> = {}) {
  return jsonResponse({
    access_token: ACCESS_TOKEN,
    expires_in: 7200,
    id_token: "id-token-that-must-not-be-stored",
    refresh_token: "refresh-token-that-must-not-be-stored",
    token_type: "Bearer",
    ...overrides,
  });
}

function successfulSessionResponse(overrides: Record<string, unknown> = {}) {
  return jsonResponse({
    roles: ["reviewer", "admin"],
    subject_id: SUBJECT_ID,
    ...overrides,
  });
}

function expectTransientCookiesConsumed() {
  for (const name of ["exam_guru_oidc_state", "exam_guru_oidc_verifier"]) {
    expect(cookieStore.set).toHaveBeenCalledWith(
      name,
      "",
      expect.objectContaining({
        maxAge: 0,
        path: "/api/auth/oidc/callback",
        sameSite: "lax",
        secure: true,
      }),
    );
  }
}

function expectExistingSessionUntouched() {
  expect(
    cookieStore.set.mock.calls.some(
      ([name]) => name === "exam_guru_admin_token" || name === "exam_guru_admin_role",
    ),
  ).toBe(false);
}

async function expectSafeFailure(request = callbackRequest()) {
  const response = await GET(request);
  expect(response.status).toBe(303);
  expect(response.headers.get("Location")).toBe(
    "https://admin.exam-guru.example/admin/login?error=oidc_login_failed",
  );
  const serializedResponse = `${await response.text()}\n${JSON.stringify([
    ...response.headers.entries(),
  ])}`;
  for (const sensitiveValue of [
    CODE,
    STATE,
    ACCESS_TOKEN,
    PREEXISTING_ACCESS_TOKEN,
    CLIENT_CREDENTIAL,
  ]) {
    expect(serializedResponse).not.toContain(sensitiveValue);
  }
  expectTransientCookiesConsumed();
  expectExistingSessionUntouched();
}

beforeEach(() => {
  configureOidc();
  for (const method of Object.values(cookieStore)) method.mockClear();
  cookieStore.get.mockImplementation((name: string) => {
    if (name === "exam_guru_oidc_state") return { name, value: STATE };
    if (name === "exam_guru_oidc_verifier") return { name, value: VERIFIER };
    if (name === "exam_guru_admin_token") return { name, value: PREEXISTING_ACCESS_TOKEN };
    if (name === "exam_guru_admin_role") return { name, value: "reviewer" };
    return undefined;
  });
  vi.mocked(cookies).mockResolvedValue(cookieStore as never);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("OIDC callback", () => {
  it("accepts a cross-site top-level callback, exchanges once, validates backend authority, and sets bounded cookies", async () => {
    const upstreamFetch = vi.fn().mockImplementationOnce(async () => {
      expect(cookieStore.set).toHaveBeenCalledWith(
        "exam_guru_oidc_state",
        "",
        expect.objectContaining({ maxAge: 0 }),
      );
      expect(cookieStore.set).toHaveBeenCalledWith(
        "exam_guru_oidc_verifier",
        "",
        expect.objectContaining({ maxAge: 0 }),
      );
      return successfulTokenResponse();
    });
    upstreamFetch.mockResolvedValueOnce(successfulSessionResponse());
    vi.stubGlobal("fetch", upstreamFetch);

    const response = await GET(callbackRequest());

    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe(
      "https://admin.exam-guru.example/admin/home",
    );
    expect(upstreamFetch).toHaveBeenCalledTimes(2);

    const [tokenUrl, tokenOptions] = upstreamFetch.mock.calls[0] as [string | URL, RequestInit];
    expect(tokenUrl.toString()).toBe("https://identity.example/oauth2/token");
    expect(tokenOptions).toMatchObject({
      cache: "no-store",
      method: "POST",
      redirect: "error",
    });
    expect(tokenOptions.signal).toBeInstanceOf(AbortSignal);
    const tokenHeaders = new Headers(tokenOptions.headers);
    const encodedCredentials = Buffer.from(
      tokenHeaders.get("Authorization")?.replace(/^Basic /, "") ?? "",
      "base64",
    ).toString("utf8");
    expect(encodedCredentials).toBe(`exam-guru-web:${CLIENT_CREDENTIAL}`);
    expect(tokenHeaders.get("Content-Type")).toBe("application/x-www-form-urlencoded");
    const body = new URLSearchParams(tokenOptions.body as string);
    expect(Object.fromEntries(body)).toEqual({
      code: CODE,
      code_verifier: VERIFIER,
      grant_type: "authorization_code",
      redirect_uri: "https://admin.exam-guru.example/api/auth/oidc/callback",
    });
    expect(tokenOptions.body).not.toContain(CLIENT_CREDENTIAL);

    const [sessionUrl, sessionOptions] = upstreamFetch.mock.calls[1] as [string | URL, RequestInit];
    expect(sessionUrl.toString()).toBe("https://api.exam-guru.example/api/v1/auth/session");
    expect(sessionOptions).toMatchObject({ cache: "no-store", method: "GET", redirect: "error" });
    expect(new Headers(sessionOptions.headers).get("Authorization")).toBe(
      `Bearer ${ACCESS_TOKEN}`,
    );
    expect(sessionOptions.signal).toBeInstanceOf(AbortSignal);

    expect(cookieStore.set).toHaveBeenCalledWith("exam_guru_admin_token", ACCESS_TOKEN, {
      httpOnly: true,
      maxAge: 3600,
      path: "/",
      sameSite: "strict",
      secure: true,
    });
    expect(cookieStore.set).toHaveBeenCalledWith("exam_guru_admin_role", "admin", {
      httpOnly: true,
      maxAge: 3600,
      path: "/",
      sameSite: "strict",
      secure: true,
    });
    expect(
      cookieStore.set.mock.calls.some(
        ([name, value, options]) =>
          (name === "exam_guru_admin_token" || name === "exam_guru_admin_role") &&
          value === "" &&
          options.maxAge === 0,
      ),
    ).toBe(false);
    const serializedCookieCalls = JSON.stringify(cookieStore.set.mock.calls);
    expect(serializedCookieCalls).not.toContain("id-token-that-must-not-be-stored");
    expect(serializedCookieCalls).not.toContain("refresh-token-that-must-not-be-stored");
    expect(serializedCookieCalls).not.toContain(SUBJECT_ID);
  });

  it("uses the provider lifetime when it is shorter than configured session max age", async () => {
    const upstreamFetch = vi
      .fn()
      .mockResolvedValueOnce(successfulTokenResponse({ expires_in: 90 }))
      .mockResolvedValueOnce(successfulSessionResponse({ roles: ["reviewer"] }));
    vi.stubGlobal("fetch", upstreamFetch);

    const response = await GET(callbackRequest());

    expect(response.status).toBe(303);
    expect(cookieStore.set).toHaveBeenCalledWith(
      "exam_guru_admin_token",
      ACCESS_TOKEN,
      expect.objectContaining({ maxAge: 90 }),
    );
    expect(cookieStore.set).toHaveBeenCalledWith(
      "exam_guru_admin_role",
      "reviewer",
      expect.objectContaining({ maxAge: 90 }),
    );
  });

  it.each(["bearer", "BEARER", "BeArEr"])(
    "accepts RFC6749 case-insensitive bearer token type %s",
    async (tokenType) => {
      const upstreamFetch = vi
        .fn()
        .mockResolvedValueOnce(successfulTokenResponse({ token_type: tokenType }))
        .mockResolvedValueOnce(successfulSessionResponse());
      vi.stubGlobal("fetch", upstreamFetch);

      const response = await GET(callbackRequest());

      expect(response.headers.get("Location")).toBe(
        "https://admin.exam-guru.example/admin/home",
      );
      expect(cookieStore.set).toHaveBeenCalledWith(
        "exam_guru_admin_token",
        ACCESS_TOKEN,
        expect.objectContaining({ maxAge: 3600 }),
      );
    },
  );

  it("accepts one RFC9207 issuer parameter only when it exactly matches configuration", async () => {
    const upstreamFetch = vi
      .fn()
      .mockResolvedValueOnce(successfulTokenResponse())
      .mockResolvedValueOnce(successfulSessionResponse());
    vi.stubGlobal("fetch", upstreamFetch);
    const query = new URLSearchParams({ code: CODE, iss: ISSUER, state: STATE }).toString();

    const response = await GET(callbackRequest(query));

    expect(response.headers.get("Location")).toBe(
      "https://admin.exam-guru.example/admin/home",
    );
    expect(upstreamFetch).toHaveBeenCalledTimes(2);
  });

  it("accepts a canonical UUIDv7 backend subject without treating its version as authority", async () => {
    const upstreamFetch = vi
      .fn()
      .mockResolvedValueOnce(successfulTokenResponse())
      .mockResolvedValueOnce(
        successfulSessionResponse({ subject_id: "018f1f6a-7b2c-7def-8abc-0123456789ab" }),
      );
    vi.stubGlobal("fetch", upstreamFetch);

    const response = await GET(callbackRequest());

    expect(response.headers.get("Location")).toBe(
      "https://admin.exam-guru.example/admin/home",
    );
  });

  it.each([
    ["missing code", `state=${STATE}`],
    ["missing state", `code=${CODE}`],
    ["duplicate code", `code=${CODE}&code=second&state=${STATE}`],
    ["duplicate state", `code=${CODE}&state=${STATE}&state=second`],
    [
      "duplicate issuer",
      `code=${CODE}&state=${STATE}&iss=${encodeURIComponent(ISSUER)}&iss=${encodeURIComponent(ISSUER)}`,
    ],
    [
      "wrong issuer",
      new URLSearchParams({
        code: CODE,
        iss: "https://identity.example/realms/other-tenant",
        state: STATE,
      }).toString(),
    ],
    [
      "oversize issuer",
      new URLSearchParams({ code: CODE, iss: `https://identity.example/${"i".repeat(2_049)}`, state: STATE }).toString(),
    ],
    ["unknown parameter", `code=${CODE}&state=${STATE}&next=https://attacker.example`],
    ["provider error", `error=access_denied&error_description=secret-provider-text&state=${STATE}`],
    ["empty code", `code=&state=${STATE}`],
    ["oversize code", `code=${"c".repeat(8_193)}&state=${STATE}`],
    ["oversize state", `code=${CODE}&state=${"s".repeat(129)}`],
    ["wrong state", `code=${CODE}&state=${"x".repeat(43)}`],
    ["wrong-length state", `code=${CODE}&state=short`],
  ])("rejects %s with one fixed safe redirect before exchange", async (_label, query) => {
    const upstreamFetch = vi.fn();
    vi.stubGlobal("fetch", upstreamFetch);

    await expectSafeFailure(callbackRequest(query));

    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("fails closed without exchange when OIDC mode is disabled", async () => {
    vi.stubEnv("WEB_IDENTITY_PROVIDER", "deny");
    vi.stubEnv("OIDC_AUTHORIZATION_ENDPOINT", undefined);
    vi.stubEnv("OIDC_TOKEN_ENDPOINT", undefined);
    vi.stubEnv("OIDC_CLIENT_ID", undefined);
    vi.stubEnv("OIDC_CLIENT_SECRET", undefined);
    vi.stubEnv("OIDC_ISSUER", undefined);
    vi.stubEnv("OIDC_SCOPES", undefined);
    const upstreamFetch = vi.fn();
    vi.stubGlobal("fetch", upstreamFetch);

    await expectSafeFailure();

    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("does not delete a preexisting Strict session on an unsolicited callback without transient state", async () => {
    cookieStore.get.mockImplementation((name: string) => {
      if (name === "exam_guru_admin_token") return { name, value: PREEXISTING_ACCESS_TOKEN };
      if (name === "exam_guru_admin_role") return { name, value: "reviewer" };
      return undefined;
    });
    const upstreamFetch = vi.fn();
    vi.stubGlobal("fetch", upstreamFetch);

    await expectSafeFailure();

    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it.each(["exam_guru_oidc_state", "exam_guru_oidc_verifier"])(
    "rejects a replay missing the %s cookie",
    async (missingCookie) => {
      cookieStore.get.mockImplementation((name: string) => {
        if (name === missingCookie) return undefined;
        if (name === "exam_guru_oidc_state") return { name, value: STATE };
        if (name === "exam_guru_oidc_verifier") return { name, value: VERIFIER };
        return undefined;
      });
      const upstreamFetch = vi.fn();
      vi.stubGlobal("fetch", upstreamFetch);

      await expectSafeFailure();

      expect(upstreamFetch).not.toHaveBeenCalled();
    },
  );

  it.each([
    ["timeout", () => Promise.reject(new DOMException("timed out", "TimeoutError"))],
    ["redirect", () => Promise.resolve(responseWithBody("", { status: 302 }))],
    [
      "non-JSON content type",
      () => Promise.resolve(successfulTokenResponseWith({ contentType: "text/plain" })),
    ],
    [
      "missing Content-Length",
      () => Promise.resolve(successfulTokenResponseWith({ contentLength: null })),
    ],
    [
      "invalid Content-Length",
      () => Promise.resolve(successfulTokenResponseWith({ contentLength: "NaN" })),
    ],
    [
      "oversize declared body",
      () => Promise.resolve(successfulTokenResponseWith({ contentLength: "65537" })),
    ],
    [
      "oversize actual body",
      () => Promise.resolve(responseWithBody(`{"padding":"${"x".repeat(65_536)}"}`)),
    ],
    ["non-success status", () => Promise.resolve(successfulTokenResponseWith({ status: 503 }))],
    ["malformed JSON", () => Promise.resolve(responseWithBody("{not-json"))],
    [
      "mismatched actual length",
      () => Promise.resolve(successfulTokenResponseWith({ contentLength: "1" })),
    ],
  ])("fails safely for token endpoint %s without retrying", async (_label, createResponse) => {
    const upstreamFetch = vi.fn().mockImplementation(createResponse);
    vi.stubGlobal("fetch", upstreamFetch);

    await expectSafeFailure();

    expect(upstreamFetch).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["missing access token", { access_token: undefined }],
    ["non-string access token", { access_token: 123 }],
    ["whitespace access token", { access_token: "token with space" }],
    ["control access token", { access_token: "token\nvalue" }],
    ["non-ASCII access token", { access_token: "tökén" }],
    ["semicolon cookie injection", { access_token: "token;Path=/" }],
    ["comma cookie injection", { access_token: "token,second=value" }],
    ["backslash cookie injection", { access_token: "token\\value" }],
    ["quote cookie injection", { access_token: 'token"value' }],
    ["oversize access token", { access_token: "t".repeat(8_193) }],
    ["wrong token type", { token_type: "DPoP" }],
    ["non-string token type", { token_type: 123 }],
    ["leading-whitespace token type", { token_type: " Bearer" }],
    ["trailing-whitespace token type", { token_type: "Bearer " }],
    ["zero expiry", { expires_in: 0 }],
    ["negative expiry", { expires_in: -1 }],
    ["fractional expiry", { expires_in: 1.5 }],
    ["string expiry", { expires_in: "3600" }],
    ["unknown field", { provider_debug: "must-not-be-accepted" }],
  ])("rejects token response with %s", async (_label, overrides) => {
    const upstreamFetch = vi.fn().mockResolvedValue(successfulTokenResponse(overrides));
    vi.stubGlobal("fetch", upstreamFetch);

    await expectSafeFailure();

    expect(upstreamFetch).toHaveBeenCalledTimes(1);
  });

  it("never returns or logs provider-controlled secrets on exchange failure", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const upstreamFetch = vi.fn().mockRejectedValue(new Error(`${CODE} ${STATE} ${CLIENT_CREDENTIAL}`));
    vi.stubGlobal("fetch", upstreamFetch);

    await expectSafeFailure();

    expect(log).not.toHaveBeenCalled();
    expect(error).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
  });

  it.each([
    ["401", () => jsonResponse({ detail: { code: "invalid_access_token" } }, { status: 401 })],
    ["503", () => jsonResponse({ detail: { code: "identity_provider_unavailable" } }, { status: 503 })],
    ["timeout", () => Promise.reject(new DOMException("timed out", "TimeoutError"))],
    ["redirect", () => responseWithBody("", { status: 302 })],
    ["non-JSON", () => successfulSessionResponseWith({ contentType: "text/plain" })],
    ["malformed JSON", () => responseWithBody("{not-json")],
    ["oversize response", () => responseWithBody(`{"padding":"${"x".repeat(16_384)}"}`)],
    ["invalid UUID", () => successfulSessionResponse({ subject_id: "not-a-uuid" })],
    ["empty roles", () => successfulSessionResponse({ roles: [] })],
    ["duplicate roles", () => successfulSessionResponse({ roles: ["admin", "admin"] })],
    ["external role", () => successfulSessionResponse({ roles: ["owner"] })],
    ["non-array roles", () => successfulSessionResponse({ roles: "admin" })],
    ["extra field", () => successfulSessionResponse({ tenant: "external" })],
  ])("fails safely when backend session validation returns %s", async (_label, makeSessionResponse) => {
    const upstreamFetch = vi
      .fn()
      .mockResolvedValueOnce(successfulTokenResponse())
      .mockImplementationOnce(makeSessionResponse);
    vi.stubGlobal("fetch", upstreamFetch);

    await expectSafeFailure();

    expect(upstreamFetch).toHaveBeenCalledTimes(2);
    expect(
      cookieStore.set.mock.calls.some(
        ([name, value, options]) => name === "exam_guru_admin_token" && value && options.maxAge !== 0,
      ),
    ).toBe(false);
  });
});

function successfulTokenResponseWith(
  options: Parameters<typeof responseWithBody>[1],
): Response {
  const body = JSON.stringify({
    access_token: ACCESS_TOKEN,
    expires_in: 7200,
    token_type: "Bearer",
  });
  return responseWithBody(body, options);
}

function successfulSessionResponseWith(
  options: Parameters<typeof responseWithBody>[1],
): Response {
  const body = JSON.stringify({ roles: ["admin"], subject_id: SUBJECT_ID });
  return responseWithBody(body, options);
}
