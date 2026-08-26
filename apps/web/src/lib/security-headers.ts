import type { WebAppConfig } from "./web-app-config";

export const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "frame-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

type SecurityHeader = Readonly<{ key: string; value: string }>;

const BASE_SECURITY_HEADERS: readonly SecurityHeader[] = [
  { key: "Content-Security-Policy", value: CONTENT_SECURITY_POLICY },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  {
    key: "Permissions-Policy",
    value: "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

const SOURCE_CONTENT_SECURITY_HEADERS: readonly SecurityHeader[] = [
  {
    key: "Content-Security-Policy",
    value: "default-src 'none'; frame-ancestors 'self'; sandbox",
  },
  { key: "Cache-Control", value: "private, no-store" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "SAMEORIGIN" },
];

const HSTS_HEADER: SecurityHeader = {
  key: "Strict-Transport-Security",
  value: "max-age=63072000; includeSubDomains; preload",
};

export function securityHeaders(config: WebAppConfig): SecurityHeader[] {
  return config.environment === "production"
    ? [...BASE_SECURITY_HEADERS, HSTS_HEADER]
    : [...BASE_SECURITY_HEADERS];
}

export function securityHeaderRules(config: WebAppConfig) {
  return [
    { headers: securityHeaders(config), source: "/(.*)" },
    {
      headers: [...SOURCE_CONTENT_SECURITY_HEADERS],
      source: "/api/v1/admin/source-documents/:documentId/content",
    },
  ];
}
