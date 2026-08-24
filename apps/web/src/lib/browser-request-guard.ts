import { NextResponse } from "next/server";

import { parseWebAppConfig, type WebAppConfig } from "./web-app-config";

const UNSAFE_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

type GuardedRequest = Pick<Request, "headers" | "method">;

// Browser cookies terminate at this same-origin Next boundary. The upstream API remains
// bearer-authenticated (not cookie-authenticated), so its requests are outside this CSRF surface.
export function guardBrowserRequest(
  request: GuardedRequest,
  config: WebAppConfig = parseWebAppConfig(),
): NextResponse | null {
  if (!UNSAFE_METHODS.has(request.method.toUpperCase())) return null;

  // Origin and Fetch Metadata are browser-owned headers. Their absence is intentional for
  // Playwright APIRequestContext and trusted server callers; SameSite=Strict remains defense-in-depth.
  const origin = request.headers.get("Origin");
  const fetchSite = request.headers.get("Sec-Fetch-Site");
  if (
    fetchSite?.toLowerCase() === "cross-site" ||
    (origin !== null && origin !== config.appOrigin)
  ) {
    return NextResponse.json(
      { detail: { code: "cross_site_request_rejected" } },
      { status: 403 },
    );
  }

  return null;
}
