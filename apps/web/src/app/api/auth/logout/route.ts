import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import { guardBrowserRequest } from "@/lib/browser-request-guard";
import { parseWebAppConfig } from "@/lib/web-app-config";

import {
  clearAuthCookies,
  LOGIN_PATH,
  type AuthCookieStore,
} from "../auth-utils";

export async function POST(request: NextRequest) {
  const config = parseWebAppConfig();
  const rejection = guardBrowserRequest(request, config);
  if (rejection) return rejection;

  const cookieStore = (await cookies()) as AuthCookieStore;
  clearAuthCookies(cookieStore, config.adminCookieSecure);

  const localLoginUrl = new URL(LOGIN_PATH, config.appBaseUrl);
  if (
    config.identityProvider !== "oidc" ||
    !config.oidcEndSessionEndpoint ||
    !config.oidcClientId
  ) {
    return NextResponse.redirect(localLoginUrl, 303);
  }

  const endSessionUrl = new URL(config.oidcEndSessionEndpoint);
  endSessionUrl.searchParams.set("post_logout_redirect_uri", localLoginUrl.toString());
  endSessionUrl.searchParams.set("client_id", config.oidcClientId);
  return NextResponse.redirect(endSessionUrl, 303);
}
