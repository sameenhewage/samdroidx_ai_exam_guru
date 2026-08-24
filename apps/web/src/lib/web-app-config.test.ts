import { describe, expect, it } from "vitest";

import { parseWebAppConfig } from "./web-app-config";

describe("web application configuration", () => {
  it("uses an explicit local HTTP default without secure cookies", () => {
    const config = parseWebAppConfig({});

    expect(config).toMatchObject({
      adminCookieSecure: false,
      appOrigin: "http://localhost:3000",
      environment: "local",
    });
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

  it("accepts an HTTPS production origin with secure cookies", () => {
    expect(
      parseWebAppConfig({
        ADMIN_COOKIE_SECURE: "true",
        APP_BASE_URL: "https://exam-guru.example:8443/",
        APP_ENVIRONMENT: "production",
      }),
    ).toMatchObject({
      adminCookieSecure: true,
      appOrigin: "https://exam-guru.example:8443",
      environment: "production",
    });
  });
});
