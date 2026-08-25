import { createHash, randomBytes } from "node:crypto";

import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import { guardBrowserRequest } from "@/lib/browser-request-guard";
import { parseWebAppConfig } from "@/lib/web-app-config";

import {
  clearAuthCookies,
  OIDC_CALLBACK_PATH,
  setOidcTransientCookies,
  type AuthCookieStore,
} from "../../auth-utils";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const config = parseWebAppConfig();
  const rejection = guardBrowserRequest(request, config);
  if (rejection) return rejection;

  if (
    config.identityProvider !== "oidc" ||
    !config.oidcAuthorizationEndpoint ||
    !config.oidcClientId
  ) {
    return NextResponse.json({ detail: { code: "oidc_login_disabled" } }, { status: 404 });
  }

  const cookieStore = (await cookies()) as AuthCookieStore;
  clearAuthCookies(cookieStore, config.adminCookieSecure);

  const state = randomBytes(32).toString("base64url");
  const verifier = randomBytes(32).toString("base64url");
  const challenge = createHash("sha256").update(verifier, "ascii").digest("base64url");
  setOidcTransientCookies(cookieStore, config.adminCookieSecure, state, verifier);

  const authorizationUrl = new URL(config.oidcAuthorizationEndpoint);
  authorizationUrl.searchParams.set("response_type", "code");
  authorizationUrl.searchParams.set("client_id", config.oidcClientId);
  authorizationUrl.searchParams.set(
    "redirect_uri",
    new URL(OIDC_CALLBACK_PATH, config.appBaseUrl).toString(),
  );
  authorizationUrl.searchParams.set("scope", config.oidcScopes.join(" "));
  authorizationUrl.searchParams.set("state", state);
  authorizationUrl.searchParams.set("code_challenge", challenge);
  authorizationUrl.searchParams.set("code_challenge_method", "S256");

  return NextResponse.redirect(authorizationUrl, 303);
}
