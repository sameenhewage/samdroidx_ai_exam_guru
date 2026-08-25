import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import { guardBrowserRequest } from "@/lib/browser-request-guard";
import { parseWebAppConfig } from "@/lib/web-app-config";

import {
  AUTHENTICATED_PATH,
  clearAuthCookies,
  setSessionCookies,
  type AuthCookieStore,
  validateAccessTokenWithBackend,
} from "../auth-utils";

export async function POST(request: NextRequest) {
  const config = parseWebAppConfig();
  const rejection = guardBrowserRequest(request, config);
  if (rejection) return rejection;

  if (config.identityProvider !== "deterministic" || !config.deterministicIdentityEnabled) {
    return NextResponse.json({ detail: { code: "development_login_disabled" } }, { status: 404 });
  }

  const cookieStore = (await cookies()) as AuthCookieStore;
  clearAuthCookies(cookieStore, config.adminCookieSecure);

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json(
      { detail: { code: "development_identity_unavailable" } },
      { status: 503 },
    );
  }
  const requestedRole = form.get("role");
  const token =
    requestedRole === "admin"
      ? config.deterministicAdminToken
      : requestedRole === "reviewer"
        ? config.deterministicReviewerToken
        : null;
  if (!token) {
    return NextResponse.json(
      { detail: { code: "development_identity_unavailable" } },
      { status: 503 },
    );
  }

  const session = await validateAccessTokenWithBackend(token, config);
  if (!session) {
    return NextResponse.json(
      { detail: { code: "development_identity_unavailable" } },
      { status: 503 },
    );
  }

  const sessionStored = setSessionCookies(
    cookieStore,
    config.adminCookieSecure,
    token,
    session.role,
    config.sessionMaxAgeSeconds,
  );
  if (!sessionStored) {
    return NextResponse.json(
      { detail: { code: "development_identity_unavailable" } },
      { status: 503 },
    );
  }
  return NextResponse.redirect(new URL(AUTHENTICATED_PATH, config.appBaseUrl), 303);
}
