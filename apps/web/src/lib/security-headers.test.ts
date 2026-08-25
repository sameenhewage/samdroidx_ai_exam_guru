import { afterEach, describe, expect, it, vi } from "vitest";

import nextConfig from "../../next.config";
import { parseWebAppConfig } from "./web-app-config";
import {
  CONTENT_SECURITY_POLICY,
  securityHeaderRules,
  securityHeaders,
} from "./security-headers";

const TEST_CLIENT_CREDENTIAL = ["test", "client", "credential"].join("-");
const expectedContentSecurityPolicy = [
  "default-src 'self'",
  // Next emits inline React/flight bootstrap scripts; unsafe-eval remains deliberately forbidden.
  "script-src 'self' 'unsafe-inline'",
  // Next and the current UI render required framework/component styles inline.
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const expectedBaseHeaders = [
  { key: "Content-Security-Policy", value: expectedContentSecurityPolicy },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  {
    key: "Permissions-Policy",
    value: "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("web security response headers", () => {
  it("defines one deterministic CSP without unsafe-eval", () => {
    expect(CONTENT_SECURITY_POLICY).toBe(expectedContentSecurityPolicy);
    expect(CONTENT_SECURITY_POLICY).not.toContain("'unsafe-eval'");
  });

  it("sets the complete header policy and omits HSTS for local HTTP", () => {
    const config = parseWebAppConfig({
      ADMIN_COOKIE_SECURE: "false",
      APP_BASE_URL: "http://localhost:3000",
      APP_ENVIRONMENT: "local",
    });

    expect(securityHeaders(config)).toEqual(expectedBaseHeaders);
    expect(securityHeaderRules(config)).toEqual([
      { headers: expectedBaseHeaders, source: "/(.*)" },
    ]);
  });

  it("omits HSTS outside explicitly configured production even on HTTPS", () => {
    const config = parseWebAppConfig({
      ADMIN_COOKIE_SECURE: "true",
      APP_BASE_URL: "https://staging.exam-guru.example",
      APP_ENVIRONMENT: "staging",
    });

    expect(securityHeaders(config)).toEqual(expectedBaseHeaders);
  });

  it("adds HSTS only for validated HTTPS production configuration", () => {
    const config = parseWebAppConfig({
      ADMIN_COOKIE_SECURE: "true",
      APP_BASE_URL: "https://exam-guru.example",
      APP_ENVIRONMENT: "production",
      OIDC_AUTHORIZATION_ENDPOINT: "https://identity.example/oauth2/authorize",
      OIDC_CLIENT_ID: "exam-guru-web",
      OIDC_CLIENT_SECRET: TEST_CLIENT_CREDENTIAL,
      OIDC_ISSUER: "https://identity.example/realms/exam-guru",
      OIDC_TOKEN_ENDPOINT: "https://identity.example/oauth2/token",
      WEB_IDENTITY_PROVIDER: "oidc",
    });

    expect(securityHeaders(config)).toEqual([
      ...expectedBaseHeaders,
      {
        key: "Strict-Transport-Security",
        value: "max-age=63072000; includeSubDomains; preload",
      },
    ]);
  });

  it("wires the deterministic rules through Next for every route", async () => {
    vi.stubEnv("APP_ENVIRONMENT", "local");
    vi.stubEnv("APP_BASE_URL", "http://localhost:3000");
    vi.stubEnv("ADMIN_COOKIE_SECURE", "false");

    await expect(nextConfig.headers?.()).resolves.toEqual([
      { headers: expectedBaseHeaders, source: "/(.*)" },
    ]);
  });
});
