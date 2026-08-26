import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import { isTimeoutError } from "@/app/api/auth/auth-utils";
import { guardBrowserRequest } from "@/lib/browser-request-guard";
import { parseWebAppConfig } from "@/lib/web-app-config";

const MAX_BODY_BYTES = 64 * 1024;
const MAX_UPLOAD_BODY_BYTES = 26 * 1024 * 1024;

export function bodyLimitForRequest(method: string, path: string[], contentType: string): number {
  return method === "POST" &&
    path.length === 1 &&
    path[0] === "source-documents" &&
    contentType.toLowerCase().startsWith("multipart/form-data;")
    ? MAX_UPLOAD_BODY_BYTES
    : MAX_BODY_BYTES;
}

export function upstreamHeadersForRequest(
  requestHeaders: Headers,
  token: string,
): Record<string, string> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    "Content-Type": requestHeaders.get("Content-Type") ?? "application/json",
  };
  const idempotencyKey = requestHeaders.get("Idempotency-Key");
  if (idempotencyKey && /^\S{1,128}$/.test(idempotencyKey)) {
    headers["Idempotency-Key"] = idempotencyKey;
  }
  return headers;
}

type RouteContext = { params: Promise<{ path: string[] }> };

function isSourceContentPath(path: string[]): boolean {
  return path.length === 3 && path[0] === "source-documents" && path[2] === "content";
}

function safeInlineDisposition(value: string | null): string | null {
  return value !== null &&
    value.length <= 1_024 &&
    /^inline; filename="[A-Za-z0-9._ -]+[.]pdf"; filename\*=UTF-8''/i.test(value) &&
    !/[\u0000-\u001F\u007F/\\]/.test(value) &&
    !/%(?:0a|0d|2f|5c)/i.test(value)
    ? value
    : null;
}

async function proxy(request: NextRequest, context: RouteContext) {
  const config = parseWebAppConfig();
  const rejection = guardBrowserRequest(request, config);
  if (rejection) return rejection;

  const token = (await cookies()).get("exam_guru_admin_token")?.value;
  if (!token) {
    return NextResponse.json({ detail: { code: "authentication_required" } }, { status: 401 });
  }

  const { path } = await context.params;
  if (path.some((segment) => !/^[A-Za-z0-9._-]+$/.test(segment))) {
    return NextResponse.json({ detail: { code: "invalid_proxy_path" } }, { status: 400 });
  }

  const sourceContentRequest = isSourceContentPath(path);
  const body = request.method === "GET" ? undefined : await request.arrayBuffer();
  const bodyLimit = bodyLimitForRequest(
    request.method,
    path,
    request.headers.get("Content-Type") ?? "",
  );
  if (body && body.byteLength > bodyLimit) {
    return NextResponse.json({ detail: { code: "request_too_large" } }, { status: 413 });
  }

  const upstream = new URL(
    `/api/v1/admin/${path.map(encodeURIComponent).join("/")}`,
    config.apiBaseUrl,
  );
  upstream.search = request.nextUrl.search;

  const upstreamHeaders = upstreamHeadersForRequest(request.headers, token);
  if (sourceContentRequest) upstreamHeaders.Accept = "application/pdf";

  let response: Response;
  try {
    // The display-only role cookie is deliberately ignored. Every target API endpoint receives
    // the bearer token and revalidates identity and permissions at the backend boundary.
    response = await fetch(upstream, {
      body,
      cache: "no-store",
      headers: upstreamHeaders,
      method: request.method,
      redirect: "error",
      signal: AbortSignal.timeout(config.httpTimeoutMs),
    });
  } catch (error) {
    if (isTimeoutError(error)) {
      return NextResponse.json({ detail: { code: "upstream_timeout" } }, { status: 504 });
    }
    return NextResponse.json({ detail: { code: "upstream_unavailable" } }, { status: 502 });
  }
  if (response.status >= 300 && response.status < 400) {
    return NextResponse.json({ detail: { code: "upstream_unavailable" } }, { status: 502 });
  }

  const responseContentType = response.headers.get("Content-Type") ?? "application/json";
  const disposition = safeInlineDisposition(response.headers.get("Content-Disposition"));
  if (
    sourceContentRequest &&
    response.ok &&
    (responseContentType.toLowerCase().split(";", 1)[0] !== "application/pdf" || !disposition)
  ) {
    return NextResponse.json({ detail: { code: "upstream_unavailable" } }, { status: 502 });
  }
  const responseHeaders: Record<string, string> = {
    "Cache-Control": sourceContentRequest ? "private, no-store" : "no-store",
    "Content-Type": responseContentType,
  };
  if (sourceContentRequest) {
    responseHeaders["Content-Security-Policy"] =
      "default-src 'none'; frame-ancestors 'self'; sandbox";
    responseHeaders["Cross-Origin-Resource-Policy"] = "same-origin";
    responseHeaders["X-Content-Type-Options"] = "nosniff";
    responseHeaders["X-Frame-Options"] = "SAMEORIGIN";
    if (disposition) responseHeaders["Content-Disposition"] = disposition;
  }

  return new NextResponse(response.body, {
    headers: responseHeaders,
    status: response.status,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
