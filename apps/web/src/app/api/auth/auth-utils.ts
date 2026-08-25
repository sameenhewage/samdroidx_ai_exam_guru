import { Buffer } from "node:buffer";

import type { WebAppConfig } from "@/lib/web-app-config";

export const ACCESS_TOKEN_COOKIE = "exam_guru_admin_token";
export const ROLE_COOKIE = "exam_guru_admin_role";
export const OIDC_STATE_COOKIE = "exam_guru_oidc_state";
export const OIDC_VERIFIER_COOKIE = "exam_guru_oidc_verifier";
export const OIDC_CALLBACK_PATH = "/api/auth/oidc/callback";
export const OIDC_FAILURE_PATH = "/admin/login?error=oidc_login_failed";
export const LOGIN_PATH = "/admin/login";
export const AUTHENTICATED_PATH = "/admin/curriculum";
export const TRANSIENT_COOKIE_MAX_AGE_SECONDS = 10 * 60;

const MAX_TOKEN_RESPONSE_BYTES = 64 * 1024;
const MAX_SESSION_RESPONSE_BYTES = 16 * 1024;
const MAX_ACCESS_TOKEN_LENGTH = 8_192;
const ALLOWED_TOKEN_RESPONSE_FIELDS = new Set([
  "access_token",
  "expires_in",
  "id_token",
  "refresh_token",
  "scope",
  "token_type",
]);
const SESSION_RESPONSE_FIELDS = new Set(["roles", "subject_id"]);
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type CookieSameSite = "lax" | "strict";
type CookieOptions = {
  httpOnly: true;
  maxAge: number;
  path: string;
  sameSite: CookieSameSite;
  secure: boolean;
};

export type AuthCookieStore = {
  get(name: string): { value: string } | undefined;
  set(name: string, value: string, options: CookieOptions): unknown;
};

export type InternalRole = "admin" | "reviewer";
export type ValidatedSession = Readonly<{
  role: InternalRole;
  roles: readonly InternalRole[];
  subjectId: string;
}>;

export type ExchangedAccessToken = Readonly<{
  accessToken: string;
  expiresIn: number;
}>;

function cookieOptions(
  secure: boolean,
  maxAge: number,
  path: string,
  sameSite: CookieSameSite,
): CookieOptions {
  return { httpOnly: true, maxAge, path, sameSite, secure };
}

export function clearTransientOidcCookies(
  cookieStore: AuthCookieStore,
  secure: boolean,
): void {
  cookieStore.set(
    OIDC_STATE_COOKIE,
    "",
    cookieOptions(secure, 0, OIDC_CALLBACK_PATH, "lax"),
  );
  cookieStore.set(
    OIDC_VERIFIER_COOKIE,
    "",
    cookieOptions(secure, 0, OIDC_CALLBACK_PATH, "lax"),
  );
}

export function clearAuthCookies(cookieStore: AuthCookieStore, secure: boolean): void {
  cookieStore.set(ACCESS_TOKEN_COOKIE, "", cookieOptions(secure, 0, "/", "strict"));
  cookieStore.set(ROLE_COOKIE, "", cookieOptions(secure, 0, "/", "strict"));
  clearTransientOidcCookies(cookieStore, secure);
}

export function setOidcTransientCookies(
  cookieStore: AuthCookieStore,
  secure: boolean,
  state: string,
  verifier: string,
): void {
  const options = cookieOptions(
    secure,
    TRANSIENT_COOKIE_MAX_AGE_SECONDS,
    OIDC_CALLBACK_PATH,
    "lax",
  );
  cookieStore.set(OIDC_STATE_COOKIE, state, options);
  cookieStore.set(OIDC_VERIFIER_COOKIE, verifier, options);
}

function isCookieSafeAccessToken(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= MAX_ACCESS_TOKEN_LENGTH &&
    /^[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]+$/.test(value)
  );
}

export function setSessionCookies(
  cookieStore: AuthCookieStore,
  secure: boolean,
  accessToken: string,
  role: InternalRole,
  maxAge: number,
): boolean {
  if (!isCookieSafeAccessToken(accessToken)) return false;
  const options = cookieOptions(secure, maxAge, "/", "strict");
  cookieStore.set(ACCESS_TOKEN_COOKIE, accessToken, options);
  cookieStore.set(ROLE_COOKIE, role, options);
  return true;
}

function isJsonContentType(response: Response): boolean {
  return response.headers.get("Content-Type")?.split(";", 1)[0]?.trim().toLowerCase() ===
    "application/json";
}

async function readBoundedBody(
  response: Response,
  maximumBytes: number,
  requireContentLength: boolean,
): Promise<string | null> {
  const contentLengthHeader = response.headers.get("Content-Length");
  if (requireContentLength && contentLengthHeader === null) return null;

  let declaredLength: number | null = null;
  if (contentLengthHeader !== null) {
    if (!/^\d+$/.test(contentLengthHeader)) return null;
    declaredLength = Number(contentLengthHeader);
    if (!Number.isSafeInteger(declaredLength) || declaredLength > maximumBytes) return null;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    if (declaredLength !== null && declaredLength !== 0) return null;
    return "";
  }

  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > maximumBytes) {
        await reader.cancel().catch(() => undefined);
        return null;
      }
      chunks.push(value);
    }
  } catch {
    return null;
  }

  if (declaredLength !== null && declaredLength !== totalBytes) return null;
  const body = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(body);
  } catch {
    return null;
  }
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyFields(value: Record<string, unknown>, fields: ReadonlySet<string>): boolean {
  return Object.keys(value).every((field) => fields.has(field));
}

function formEncode(value: string): string {
  const encoded = new URLSearchParams({ value }).toString();
  return encoded.slice("value=".length);
}

export async function exchangeAuthorizationCode(
  code: string,
  verifier: string,
  config: WebAppConfig,
): Promise<ExchangedAccessToken | null> {
  const tokenEndpoint = config.oidcTokenEndpoint;
  const clientId = config.oidcClientId;
  const clientSecret = config.oidcClientSecret;
  if (!tokenEndpoint || !clientId || !clientSecret) return null;

  const credentials = Buffer.from(
    `${formEncode(clientId)}:${formEncode(clientSecret)}`,
    "utf8",
  ).toString("base64");
  const body = new URLSearchParams({
    code,
    code_verifier: verifier,
    grant_type: "authorization_code",
    redirect_uri: new URL(OIDC_CALLBACK_PATH, config.appBaseUrl).toString(),
  }).toString();

  let response: Response;
  try {
    response = await fetch(tokenEndpoint, {
      body,
      cache: "no-store",
      headers: {
        Accept: "application/json",
        Authorization: `Basic ${credentials}`,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      method: "POST",
      redirect: "error",
      signal: AbortSignal.timeout(config.httpTimeoutMs),
    });
  } catch {
    return null;
  }

  if (response.status !== 200 || !isJsonContentType(response)) return null;
  const responseBody = await readBoundedBody(response, MAX_TOKEN_RESPONSE_BYTES, true);
  if (responseBody === null) return null;

  let payload: unknown;
  try {
    payload = JSON.parse(responseBody) as unknown;
  } catch {
    return null;
  }
  if (!isPlainRecord(payload) || !hasOnlyFields(payload, ALLOWED_TOKEN_RESPONSE_FIELDS)) {
    return null;
  }

  const accessToken = payload.access_token;
  const tokenType = payload.token_type;
  const expiresIn = payload.expires_in;
  if (
    !isCookieSafeAccessToken(accessToken) ||
    typeof tokenType !== "string" ||
    tokenType.toLowerCase() !== "bearer" ||
    typeof expiresIn !== "number" ||
    !Number.isSafeInteger(expiresIn) ||
    expiresIn <= 0
  ) {
    return null;
  }
  return Object.freeze({ accessToken, expiresIn });
}

export async function validateAccessTokenWithBackend(
  accessToken: string,
  config: WebAppConfig,
): Promise<ValidatedSession | null> {
  let response: Response;
  try {
    response = await fetch(new URL("/api/v1/auth/session", config.apiBaseUrl), {
      cache: "no-store",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${accessToken}`,
      },
      method: "GET",
      redirect: "error",
      signal: AbortSignal.timeout(config.httpTimeoutMs),
    });
  } catch {
    return null;
  }

  if (response.status !== 200 || !isJsonContentType(response)) return null;
  const responseBody = await readBoundedBody(response, MAX_SESSION_RESPONSE_BYTES, false);
  if (responseBody === null) return null;

  let payload: unknown;
  try {
    payload = JSON.parse(responseBody) as unknown;
  } catch {
    return null;
  }
  if (
    !isPlainRecord(payload) ||
    !hasOnlyFields(payload, SESSION_RESPONSE_FIELDS) ||
    Object.keys(payload).length !== SESSION_RESPONSE_FIELDS.size ||
    typeof payload.subject_id !== "string" ||
    !UUID_PATTERN.test(payload.subject_id) ||
    !Array.isArray(payload.roles) ||
    payload.roles.length === 0 ||
    payload.roles.length > 2 ||
    payload.roles.some((role) => role !== "admin" && role !== "reviewer") ||
    new Set(payload.roles).size !== payload.roles.length
  ) {
    return null;
  }

  const roles = Object.freeze([...payload.roles] as InternalRole[]);
  return Object.freeze({
    role: roles.includes("admin") ? "admin" : "reviewer",
    roles,
    subjectId: payload.subject_id,
  });
}

export function isTimeoutError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    (error as { name: unknown }).name === "TimeoutError"
  );
}
