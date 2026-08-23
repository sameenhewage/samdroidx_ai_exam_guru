import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

const MAX_BODY_BYTES = 64 * 1024;

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: RouteContext) {
  const token = (await cookies()).get("exam_guru_admin_token")?.value;
  if (!token) {
    return NextResponse.json({ detail: { code: "authentication_required" } }, { status: 401 });
  }

  const { path } = await context.params;
  if (path.some((segment) => !/^[A-Za-z0-9._-]+$/.test(segment))) {
    return NextResponse.json({ detail: { code: "invalid_proxy_path" } }, { status: 400 });
  }

  const body = request.method === "GET" ? undefined : await request.arrayBuffer();
  if (body && body.byteLength > MAX_BODY_BYTES) {
    return NextResponse.json({ detail: { code: "request_too_large" } }, { status: 413 });
  }

  const baseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";
  const upstream = new URL(`/api/v1/admin/${path.map(encodeURIComponent).join("/")}`, baseUrl);
  upstream.search = request.nextUrl.search;
  const response = await fetch(upstream, {
    body,
    cache: "no-store",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": request.headers.get("Content-Type") ?? "application/json",
    },
    method: request.method,
  });

  return new NextResponse(response.body, {
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": response.headers.get("Content-Type") ?? "application/json",
    },
    status: response.status,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
