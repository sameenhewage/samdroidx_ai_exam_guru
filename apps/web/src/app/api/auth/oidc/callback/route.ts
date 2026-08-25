import { createHash, timingSafeEqual } from "node:crypto";

import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import { parseWebAppConfig } from "@/lib/web-app-config";

import {
  AUTHENTICATED_PATH,
  clearTransientOidcCookies,
  exchangeAuthorizationCode,
  OIDC_FAILURE_PATH,
  OIDC_STATE_COOKIE,
  OIDC_VERIFIER_COOKIE,
  setSessionCookies,
  type AuthCookieStore,
  validateAccessTokenWithBackend,
} from "../../auth-utils";

export const runtime = "nodejs";

function safeFailure(appBaseUrl: URL): NextResponse {
  return NextResponse.redirect(new URL(OIDC_FAILURE_PATH, appBaseUrl), 303);
}

function hasExactCallbackQuery(request: NextRequest): boolean {
  const parameters = request.nextUrl.searchParams;
  const keys = [...parameters.keys()];
  return (
    keys.length >= 2 &&
    keys.length <= 3 &&
    keys.every((key) => key === "code" || key === "state" || key === "iss") &&
    parameters.getAll("code").length === 1 &&
    parameters.getAll("state").length === 1 &&
    parameters.getAll("iss").length <= 1
  );
}

function exactValueMatches(expected: string, provided: string): boolean {
  const expectedDigest = createHash("sha256").update(expected, "ascii").digest();
  const providedDigest = createHash("sha256").update(provided, "ascii").digest();
  return timingSafeEqual(expectedDigest, providedDigest) && expected.length === provided.length;
}

export async function GET(request: NextRequest) {
  const config = parseWebAppConfig();
  const cookieStore = (await cookies()) as AuthCookieStore;
  const expectedState = cookieStore.get(OIDC_STATE_COOKIE)?.value;
  const verifier = cookieStore.get(OIDC_VERIFIER_COOKIE)?.value;

  // Consume only callback state before any network operation. Existing Strict sessions survive
  // unsolicited, malformed, mismatched, and upstream-failed callbacks.
  clearTransientOidcCookies(cookieStore, config.adminCookieSecure);

  if (config.identityProvider !== "oidc" || !hasExactCallbackQuery(request)) {
    return safeFailure(config.appBaseUrl);
  }

  const code = request.nextUrl.searchParams.get("code") ?? "";
  const providedState = request.nextUrl.searchParams.get("state") ?? "";
  const providedIssuer = request.nextUrl.searchParams.get("iss");
  if (
    code.length === 0 ||
    code.length > 8_192 ||
    !/^[\x21-\x7e]+$/.test(code) ||
    providedState.length < 43 ||
    providedState.length > 128 ||
    !/^[A-Za-z0-9_-]+$/.test(providedState) ||
    (providedIssuer !== null &&
      (providedIssuer.length === 0 ||
        providedIssuer.length > 2_048 ||
        !/^[\x21-\x7e]+$/.test(providedIssuer) ||
        config.oidcIssuer === null ||
        !exactValueMatches(config.oidcIssuer, providedIssuer))) ||
    expectedState === undefined ||
    expectedState.length < 43 ||
    expectedState.length > 128 ||
    !/^[A-Za-z0-9_-]+$/.test(expectedState) ||
    verifier === undefined ||
    verifier.length < 43 ||
    verifier.length > 128 ||
    !/^[A-Za-z0-9._~-]+$/.test(verifier) ||
    !exactValueMatches(expectedState, providedState)
  ) {
    return safeFailure(config.appBaseUrl);
  }

  const token = await exchangeAuthorizationCode(code, verifier, config);
  if (!token) return safeFailure(config.appBaseUrl);

  const session = await validateAccessTokenWithBackend(token.accessToken, config);
  if (!session) return safeFailure(config.appBaseUrl);

  const sessionStored = setSessionCookies(
    cookieStore,
    config.adminCookieSecure,
    token.accessToken,
    session.role,
    Math.min(token.expiresIn, config.sessionMaxAgeSeconds),
  );
  return sessionStored
    ? NextResponse.redirect(new URL(AUTHENTICATED_PATH, config.appBaseUrl), 303)
    : safeFailure(config.appBaseUrl);
}
