import { describe, expect, it } from "vitest";

import { parseWebAppConfig } from "./web-app-config";

const TEST_CLIENT_CREDENTIAL = ["never", "render", "this", "credential"].join("-");
const COMPLETE_OIDC_ENV = {
  ADMIN_COOKIE_SECURE: "true",
  API_BASE_URL: "https://api.exam-guru.example",
  APP_BASE_URL: "https://admin.exam-guru.example",
  APP_ENVIRONMENT: "production",
  OIDC_AUTHORIZATION_ENDPOINT: "https://identity.example/oauth2/authorize",
  OIDC_CLIENT_ID: "exam-guru-web",
  OIDC_CLIENT_SECRET: TEST_CLIENT_CREDENTIAL,
  OIDC_HTTP_TIMEOUT_MS: "2500",
  OIDC_ISSUER: "https://identity.example/realms/exam-guru",
  OIDC_SCOPES: "openid profile email",
  OIDC_TOKEN_ENDPOINT: "https://identity.example/oauth2/token",
  ADMIN_SESSION_MAX_AGE_SECONDS: "7200",
  WEB_IDENTITY_PROVIDER: "oidc",
} as const;

describe("web application configuration", () => {
  it("uses an explicit local HTTP default without secure cookies", () => {
    const config = parseWebAppConfig({});

    expect(config).toMatchObject({
      adminCookieSecure: false,
      apiOrigin: "http://localhost:8000",
      appOrigin: "http://localhost:3000",
      environment: "local",
      httpTimeoutMs: 5_000,
      identityProvider: "deny",
      sessionMaxAgeSeconds: 8 * 60 * 60,
    });
    expect(config.apiBaseUrl.href).toBe("http://localhost:8000/");
    expect(config.appBaseUrl.href).toBe("http://localhost:3000/");
  });

  it.each(["local", "test", "staging"] as const)(
    "accepts a validated HTTP base URL in the %s environment",
    (environment) => {
      const config = parseWebAppConfig({
        ADMIN_COOKIE_SECURE: "false",
        APP_BASE_URL: "http://127.0.0.1:3100/",
        APP_ENVIRONMENT: environment,
      });

      expect(config.environment).toBe(environment);
      expect(config.appOrigin).toBe("http://127.0.0.1:3100");
    },
  );

  it("accepts a standards-valid case-insensitive HTTPS scheme", () => {
    expect(parseWebAppConfig({ APP_BASE_URL: "HTTPS://EXAM-GURU.EXAMPLE" }).appOrigin).toBe(
      "https://exam-guru.example",
    );
  });

  it.each([
    ["not a URL", "absolute http(s) URL"],
    ["http://[::1", "absolute http(s) URL"],
    ["https:example.com", "absolute http(s) URL"],
    ["ftp://example.com", "absolute http(s) URL"],
    ["https://admin:" + "secret" + "@example.com", "must not contain userinfo"],
    ["https://example.com/admin", "must not contain a path"],
    ["https://example.com/%2e%2e", "must not contain a path"],
    ["https://example.com\\.", "must not contain a path"],
    ["https://example.com?redirect=evil", "must not contain a query or fragment"],
    ["https://example.com#evil", "must not contain a query or fragment"],
    [" https://example.com", "must not contain surrounding whitespace"],
  ])("rejects unsafe APP_BASE_URL %s", (appBaseUrl, expectedMessage) => {
    expect(() => parseWebAppConfig({ APP_BASE_URL: appBaseUrl })).toThrow(expectedMessage);
  });

  it("rejects unsupported application environments", () => {
    expect(() => parseWebAppConfig({ APP_ENVIRONMENT: "prod" })).toThrow(
      "APP_ENVIRONMENT must be one of local, test, staging, production",
    );
  });

  it("rejects ambiguous secure-cookie values", () => {
    expect(() => parseWebAppConfig({ ADMIN_COOKIE_SECURE: "yes" })).toThrow(
      "ADMIN_COOKIE_SECURE must be either true or false",
    );
  });

  it("requires an explicitly configured base URL in production", () => {
    expect(() =>
      parseWebAppConfig({
        ADMIN_COOKIE_SECURE: "true",
        APP_ENVIRONMENT: "production",
      }),
    ).toThrow("APP_BASE_URL is required in production");
  });

  it("requires HTTPS in production", () => {
    expect(() =>
      parseWebAppConfig({
        ADMIN_COOKIE_SECURE: "true",
        APP_BASE_URL: "http://exam-guru.example",
        APP_ENVIRONMENT: "production",
      }),
    ).toThrow("production APP_BASE_URL must use HTTPS");
  });

  it.each([undefined, "false"])("requires secure cookies in production (%s)", (secure) => {
    expect(() =>
      parseWebAppConfig({
        ADMIN_COOKIE_SECURE: secure,
        APP_BASE_URL: "https://exam-guru.example",
        APP_ENVIRONMENT: "production",
      }),
    ).toThrow("production ADMIN_COOKIE_SECURE must be true");
  });

  it("accepts a complete HTTPS production OIDC configuration", () => {
    const config = parseWebAppConfig({
      ...COMPLETE_OIDC_ENV,
      APP_BASE_URL: "https://exam-guru.example:8443/",
    });

    expect(config).toMatchObject({
      adminCookieSecure: true,
      appOrigin: "https://exam-guru.example:8443",
      environment: "production",
      httpTimeoutMs: 2_500,
      identityProvider: "oidc",
      oidcClientId: "exam-guru-web",
      oidcIssuer: "https://identity.example/realms/exam-guru",
      oidcScopes: ["openid", "profile", "email"],
      sessionMaxAgeSeconds: 7_200,
    });
    expect(config.oidcAuthorizationEndpoint?.href).toBe(
      "https://identity.example/oauth2/authorize",
    );
    expect(config.oidcTokenEndpoint?.href).toBe("https://identity.example/oauth2/token");
  });

  it.each(["invalid", "OIDC", ""])("rejects unsupported identity provider %s", (provider) => {
    expect(() => parseWebAppConfig({ WEB_IDENTITY_PROVIDER: provider })).toThrow(
      "WEB_IDENTITY_PROVIDER must be one of deterministic, oidc, deny",
    );
  });

  it("requires OIDC mode in production", () => {
    expect(() =>
      parseWebAppConfig({
        ...COMPLETE_OIDC_ENV,
        WEB_IDENTITY_PROVIDER: "deny",
      }),
    ).toThrow("production WEB_IDENTITY_PROVIDER must be oidc");
  });

  it.each([
    "OIDC_ISSUER",
    "OIDC_AUTHORIZATION_ENDPOINT",
    "OIDC_TOKEN_ENDPOINT",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
  ])(
    "rejects partial OIDC configuration missing %s",
    (missing) => {
      const source: Record<string, string | undefined> = { ...COMPLETE_OIDC_ENV };
      source.APP_ENVIRONMENT = "test";
      source.ADMIN_COOKIE_SECURE = "false";
      delete source[missing];

      expect(() => parseWebAppConfig(source)).toThrow(
        "identity provider requires complete explicit OIDC configuration",
      );
    },
  );

  it.each([
    "http://identity.example/oauth2/authorize",
    "https://identity.example/oauth2/authorize?tenant=evil",
    "https://identity.example/oauth2/authorize#fragment",
  ])("rejects unsafe production authorization endpoint %s", (endpoint) => {
    expect(() =>
      parseWebAppConfig({ ...COMPLETE_OIDC_ENV, OIDC_AUTHORIZATION_ENDPOINT: endpoint }),
    ).toThrow();
  });

  it("accepts a bounded issuer path and preserves it for exact RFC9207 comparison", () => {
    const issuer = "https://identity.example/realms/exam-guru/";

    expect(parseWebAppConfig({ ...COMPLETE_OIDC_ENV, OIDC_ISSUER: issuer }).oidcIssuer).toBe(
      issuer,
    );
  });

  it.each([
    "relative-issuer",
    "https://user:" + "password" + "@identity.example/realms/exam-guru",
    "https://identity.example/realms/exam-guru?tenant=evil",
    "https://identity.example/realms/exam-guru#fragment",
    "https://identity.example\\realms/exam-guru",
    `https://identity.example/${"i".repeat(2_049)}`,
  ])("rejects unsafe or unbounded OIDC issuer %s", (issuer) => {
    expect(() => parseWebAppConfig({ ...COMPLETE_OIDC_ENV, OIDC_ISSUER: issuer })).toThrow(
      "OIDC issuer",
    );
  });

  it("requires the authorization endpoint to share the issuer origin", () => {
    expect(() =>
      parseWebAppConfig({
        ...COMPLETE_OIDC_ENV,
        OIDC_AUTHORIZATION_ENDPOINT: "https://login.example/oauth2/authorize",
        OIDC_TOKEN_ENDPOINT: "https://login.example/oauth2/token",
      }),
    ).toThrow("OIDC authorization endpoint origin must match the issuer origin");
  });

  it("requires a production issuer to use HTTPS", () => {
    expect(() =>
      parseWebAppConfig({
        ...COMPLETE_OIDC_ENV,
        OIDC_ISSUER: "http://identity.example/realms/exam-guru",
      }),
    ).toThrow("production OIDC URLs must use HTTPS");
  });

  it.each([
    "relative",
    "ftp://api.example",
    "https://user:" + "password" + "@api.example",
    "https://api.example/v1",
    "https://api.example?query=true",
    "https://api.example#fragment",
    `https://${"a".repeat(2_048)}.example`,
  ])("rejects unsafe or unbounded API_BASE_URL %s", (apiBaseUrl) => {
    expect(() => parseWebAppConfig({ API_BASE_URL: apiBaseUrl })).toThrow("API_BASE_URL");
  });

  it("requires authorization and token endpoints to share an exact origin by default", () => {
    expect(() =>
      parseWebAppConfig({
        ...COMPLETE_OIDC_ENV,
        OIDC_TOKEN_ENDPOINT: "https://tokens.example/oauth2/token",
      }),
    ).toThrow("OIDC token endpoint origin must match the authorization endpoint origin");
  });

  it("allows one separately validated exact trusted token origin", () => {
    const config = parseWebAppConfig({
      ...COMPLETE_OIDC_ENV,
      OIDC_TOKEN_ENDPOINT: "https://tokens.example:8443/oauth2/token",
      OIDC_TRUSTED_TOKEN_ORIGIN: "https://tokens.example:8443",
    });

    expect(config.oidcTokenEndpoint?.origin).toBe("https://tokens.example:8443");
    expect(config.oidcTrustedTokenOrigin).toBe("https://tokens.example:8443");
  });

  it.each([
    "https://tokens.example/path",
    "https://tokens.example?query=true",
    "https://elsewhere.example",
  ])("rejects an invalid or mismatched trusted token origin %s", (trustedOrigin) => {
    expect(() =>
      parseWebAppConfig({
        ...COMPLETE_OIDC_ENV,
        OIDC_TOKEN_ENDPOINT: "https://tokens.example/oauth2/token",
        OIDC_TRUSTED_TOKEN_ORIGIN: trustedOrigin,
      }),
    ).toThrow("trusted token origin");
  });

  it.each(["client:identifier", " client", "client id", "", "x".repeat(257)])(
    "rejects unsafe or unbounded OIDC client ID %s",
    (clientId) => {
      expect(() => parseWebAppConfig({ ...COMPLETE_OIDC_ENV, OIDC_CLIENT_ID: clientId })).toThrow(
        "OIDC client ID",
      );
    },
  );

  it("keeps the client secret out of serialized or enumerable configuration", () => {
    const config = parseWebAppConfig(COMPLETE_OIDC_ENV);

    expect(config.oidcClientSecret).toBe(COMPLETE_OIDC_ENV.OIDC_CLIENT_SECRET);
    expect(Object.keys(config)).not.toContain("oidcClientSecret");
    expect(JSON.stringify(config)).not.toContain(COMPLETE_OIDC_ENV.OIDC_CLIENT_SECRET);
  });

  it("does not include the client secret in validation errors", () => {
    const secret = `secret-${"x".repeat(1_025)}`;
    expect(() =>
      parseWebAppConfig({ ...COMPLETE_OIDC_ENV, OIDC_CLIENT_SECRET: secret }),
    ).toThrowError(expect.not.objectContaining({ message: expect.stringContaining(secret) }));
  });

  it.each([
    "profile email",
    "openid openid",
    `openid ${"scope".repeat(17)}`,
    `openid ${Array.from({ length: 16 }, (_, index) => `scope-${index}`).join(" ")}`,
    "openid invalid\\scope",
  ])("rejects missing, duplicate, or unbounded OIDC scopes %s", (scopes) => {
    expect(() => parseWebAppConfig({ ...COMPLETE_OIDC_ENV, OIDC_SCOPES: scopes })).toThrow(
      "OIDC_SCOPES",
    );
  });

  it.each(["0", "99", "10001", "1.5", "infinite"])(
    "rejects an out-of-range OIDC HTTP timeout %s",
    (timeout) => {
      expect(() =>
        parseWebAppConfig({ ...COMPLETE_OIDC_ENV, OIDC_HTTP_TIMEOUT_MS: timeout }),
      ).toThrow("OIDC_HTTP_TIMEOUT_MS");
    },
  );

  it.each(["0", "59", "86401", "1.5"])(
    "rejects an out-of-range admin session age %s",
    (maxAge) => {
      expect(() =>
        parseWebAppConfig({ ...COMPLETE_OIDC_ENV, ADMIN_SESSION_MAX_AGE_SECONDS: maxAge }),
      ).toThrow("ADMIN_SESSION_MAX_AGE_SECONDS");
    },
  );

  it.each(["staging", "production"])(
    "rejects deterministic identity flags in %s",
    (environment) => {
      expect(() =>
        parseWebAppConfig({
          ...COMPLETE_OIDC_ENV,
          APP_ENVIRONMENT: environment,
          DETERMINISTIC_ADMIN_TOKEN: "local-admin-token",
          ENABLE_DETERMINISTIC_IDENTITY: "true",
          WEB_IDENTITY_PROVIDER: environment === "production" ? "oidc" : "deterministic",
        }),
      ).toThrow("deterministic identity is restricted to local and test");
    },
  );

  it.each([
    { ENABLE_DETERMINISTIC_IDENTITY: "true" },
    { DETERMINISTIC_ADMIN_TOKEN: "local-admin-token" },
    { DETERMINISTIC_REVIEWER_TOKEN: "local-reviewer-token" },
  ])("rejects deterministic setting outside deterministic mode: %o", (setting) => {
    expect(() => parseWebAppConfig({ ...setting, WEB_IDENTITY_PROVIDER: "deny" })).toThrow(
      "deterministic identity settings require WEB_IDENTITY_PROVIDER=deterministic",
    );
  });

  it.each(["", " openid", "openid ", `openid ${"x".repeat(506)}`])(
    "rejects empty, padded, or oversized raw OIDC scope text %s",
    (scopes) => {
      expect(() => parseWebAppConfig({ ...COMPLETE_OIDC_ENV, OIDC_SCOPES: scopes })).toThrow(
        "OIDC_SCOPES",
      );
    },
  );

  it("rejects backslashes in an OIDC endpoint before URL normalization", () => {
    expect(() =>
      parseWebAppConfig({
        ...COMPLETE_OIDC_ENV,
        OIDC_AUTHORIZATION_ENDPOINT: "https://identity.example\\oauth2/authorize",
      }),
    ).toThrow("OIDC authorization endpoint must be a bounded absolute HTTP(S) URL");
  });

  it("accepts a bounded optional end-session endpoint", () => {
    const config = parseWebAppConfig({
      ...COMPLETE_OIDC_ENV,
      OIDC_END_SESSION_ENDPOINT: "https://identity.example/oauth2/logout",
    });

    expect(config.oidcEndSessionEndpoint?.href).toBe(
      "https://identity.example/oauth2/logout",
    );
  });

  it("defaults a complete OIDC configuration to the required openid scope", () => {
    expect(parseWebAppConfig({ ...COMPLETE_OIDC_ENV, OIDC_SCOPES: undefined }).oidcScopes).toEqual([
      "openid",
    ]);
  });

  it.each([
    ["false", false],
    ["true", true],
  ])("derives deterministic enablement from the explicit flag %s", (enabled, expected) => {
    expect(
      parseWebAppConfig({
        ENABLE_DETERMINISTIC_IDENTITY: enabled,
        WEB_IDENTITY_PROVIDER: "deterministic",
      }).deterministicIdentityEnabled,
    ).toBe(expected);
  });

  it("rejects a mismatched trusted origin even when token and authorization origins match", () => {
    expect(() =>
      parseWebAppConfig({
        ...COMPLETE_OIDC_ENV,
        OIDC_TRUSTED_TOKEN_ORIGIN: "https://elsewhere.example",
      }),
    ).toThrow("OIDC trusted token origin must exactly match the token endpoint origin");
  });

  it.each([
    {
      OIDC_AUTHORIZATION_ENDPOINT: "http://identity.example/oauth2/authorize",
      OIDC_TOKEN_ENDPOINT: "http://identity.example/oauth2/token",
    },
    { OIDC_TOKEN_ENDPOINT: "http://identity.example/oauth2/token" },
    { OIDC_END_SESSION_ENDPOINT: "http://identity.example/oauth2/logout" },
  ])("rejects HTTP OIDC endpoints in production: %o", (endpoints) => {
    expect(() => parseWebAppConfig({ ...COMPLETE_OIDC_ENV, ...endpoints })).toThrow(
      "production OIDC URLs must use HTTPS",
    );
  });
});
