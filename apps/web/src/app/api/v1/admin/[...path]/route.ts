import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import { isTimeoutError } from "@/app/api/auth/auth-utils";
import { guardBrowserRequest } from "@/lib/browser-request-guard";
import { parseWebAppConfig } from "@/lib/web-app-config";

const MAX_BODY_BYTES = 64 * 1024;
const MAX_UPLOAD_BODY_BYTES = 26 * 1024 * 1024;
const MAX_CHUNK_BODY_BYTES = 4_194_304;
const checksumPattern = /^[0-9a-f]{64}$/;

function isChunkUpload(method: string, path: readonly string[]): boolean {
  return (
    method === "PUT" &&
    path.length === 3 &&
    path[0] === "source-uploads" &&
    path[2] === "chunks"
  );
}

export function bodyLimitForRequest(
  method: string,
  path: string[],
  contentType: string,
): number {
  if (
    isChunkUpload(method, path) &&
    contentType.toLowerCase().split(";", 1)[0].trim() ===
      "application/octet-stream"
  )
    return MAX_CHUNK_BODY_BYTES;
  return method === "POST" &&
    path.length === 1 &&
    path[0] === "source-documents" &&
    contentType.toLowerCase().startsWith("multipart/form-data;")
    ? MAX_UPLOAD_BODY_BYTES
    : MAX_BODY_BYTES;
}

function safeRange(value: string): boolean {
  const match = /^bytes=([0-9]{0,16})-([0-9]{0,16})$/.exec(value);
  if (!match || (!match[1] && !match[2])) return false;
  if (!match[1])
    return Number.isSafeInteger(Number(match[2])) && Number(match[2]) > 0;
  return (
    Number.isSafeInteger(Number(match[1])) &&
    (!match[2] ||
      (Number.isSafeInteger(Number(match[2])) &&
        Number(match[2]) >= Number(match[1])))
  );
}

function safeEtag(value: string): boolean {
  return /^"[\x21\x23-\x7e]{1,128}"$/.test(value);
}

function safeHttpDate(value: string): boolean {
  return (
    /^[A-Za-z]{3}, [0-9]{2} [A-Za-z]{3} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT$/.test(
      value,
    ) && Number.isFinite(Date.parse(value))
  );
}

export function upstreamHeadersForRequest(
  requestHeaders: Headers,
  token: string,
  method = "GET",
  path: readonly string[] = [],
): Record<string, string> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    "Content-Type": requestHeaders.get("Content-Type") ?? "application/json",
  };
  const idempotencyKey = requestHeaders.get("Idempotency-Key");
  if (idempotencyKey && /^\S{1,128}$/.test(idempotencyKey)) {
    headers["Idempotency-Key"] = idempotencyKey;
  }
  const checksum = requestHeaders.get("X-Chunk-SHA256");
  if (isChunkUpload(method, path) && checksum && checksumPattern.test(checksum))
    headers["X-Chunk-SHA256"] = checksum;
  if ((method === "GET" || method === "HEAD") && isSourceContentPath(path)) {
    const range = requestHeaders.get("Range");
    const condition = requestHeaders.get("If-Range");
    if (range && safeRange(range)) headers.Range = range;
    if (condition && (safeEtag(condition) || safeHttpDate(condition)))
      headers["If-Range"] = condition;
  }
  return headers;
}

type RouteContext = { params: Promise<{ path: string[] }> };

function isSourceContentPath(path: readonly string[]): boolean {
  return (
    path.length === 3 &&
    ((path[0] === "source-documents" && path[2] === "content") ||
      (path[0] === "materials" && path[2] === "original"))
  );
}

function isSourceImagePath(path: readonly string[]): boolean {
  return (
    path.length === 5 &&
    path[2] === "pages" &&
    ((path[0] === "materials" && path[4] === "image") ||
      (path[0] === "source-documents" && path[4] === "preview"))
  );
}

class InvalidRequestBody extends Error {
  constructor(
    public readonly code: string,
    public readonly status: number,
  ) {
    super(code);
  }
}

function errorResponse(code: string, status: number) {
  return NextResponse.json(
    { detail: { code } },
    { status, headers: { "Cache-Control": "private, no-store" } },
  );
}

async function boundedRequestBody(
  request: NextRequest,
  limit: number,
  timeoutMs: number,
): Promise<ArrayBuffer | undefined> {
  if (request.method === "GET" || request.method === "HEAD") return undefined;
  const rawLength = request.headers.get("Content-Length");
  let length: number | undefined;
  if (rawLength !== null) {
    if (
      !/^[0-9]{1,16}$/.test(rawLength) ||
      !Number.isSafeInteger(Number(rawLength))
    ) {
      await request.body?.cancel();
      throw new InvalidRequestBody("invalid_content_length", 400);
    }
    length = Number(rawLength);
    if (length > limit) {
      await request.body?.cancel();
      throw new InvalidRequestBody("request_too_large", 413);
    }
  }
  if (!request.body) {
    if (length) throw new InvalidRequestBody("request_body_truncated", 400);
    return undefined;
  }
  const reader = request.body.getReader();
  const bytes = new Uint8Array(length ?? limit);
  let offset = 0;
  let expired = false;
  const cancel = () => {
    void reader.cancel().catch(() => undefined);
  };
  const timer = setTimeout(() => {
    expired = true;
    cancel();
  }, timeoutMs);
  request.signal.addEventListener("abort", cancel, { once: true });
  try {
    while (true) {
      if (request.signal.aborted)
        throw new InvalidRequestBody("request_body_interrupted", 400);
      const next = await reader.read();
      if (expired) throw new InvalidRequestBody("request_body_timeout", 408);
      if (request.signal.aborted)
        throw new InvalidRequestBody("request_body_interrupted", 400);
      if (next.done) break;
      if (next.value.byteLength > limit - offset)
        throw new InvalidRequestBody("request_too_large", 413);
      if (length !== undefined && next.value.byteLength > length - offset)
        throw new InvalidRequestBody("request_body_length_mismatch", 400);
      bytes.set(next.value, offset);
      offset += next.value.byteLength;
    }
    if (length !== undefined && offset !== length)
      throw new InvalidRequestBody("request_body_truncated", 400);
    return offset === bytes.byteLength
      ? bytes.buffer
      : bytes.buffer.slice(0, offset);
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    if (error instanceof InvalidRequestBody) throw error;
    throw new InvalidRequestBody("request_body_interrupted", 400);
  } finally {
    clearTimeout(timer);
    request.signal.removeEventListener("abort", cancel);
    reader.releaseLock();
  }
}

function safeContentRange(value: string | null): boolean {
  if (!value) return false;
  const match = /^bytes (?:(\d{1,16})-(\d{1,16})|\*)\/(\d{1,16})$/.exec(value);
  if (!match || !Number.isSafeInteger(Number(match[3]))) return false;
  if (match[1] === undefined) return true;
  const [start, end, total] = match.slice(1).map(Number);
  return (
    [start, end, total].every(Number.isSafeInteger) &&
    start <= end &&
    end < total
  );
}

function safeInlineDisposition(value: string | null): string | null {
  return value !== null &&
    value.length <= 1_024 &&
    /^inline; filename="[A-Za-z0-9._ -]+[.]pdf"; filename\*=UTF-8''/i.test(
      value,
    ) &&
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
    return errorResponse("authentication_required", 401);
  }

  const { path } = await context.params;
  if (path.some((segment) => !/^[A-Za-z0-9._-]+$/.test(segment))) {
    return errorResponse("invalid_proxy_path", 400);
  }

  const sourceContentRequest = isSourceContentPath(path);
  const sourceImageRequest = isSourceImagePath(path);
  const checksum = request.headers.get("X-Chunk-SHA256");
  if (
    isChunkUpload(request.method, path) &&
    checksum !== null &&
    !checksumPattern.test(checksum)
  )
    return errorResponse("invalid_upload_checksum", 400);
  if (sourceContentRequest) {
    const range = request.headers.get("Range");
    const condition = request.headers.get("If-Range");
    if (
      (range !== null && !safeRange(range)) ||
      (condition !== null && !safeEtag(condition) && !safeHttpDate(condition))
    )
      return errorResponse("invalid_range_header", 400);
  }
  const bodyLimit = bodyLimitForRequest(
    request.method,
    path,
    request.headers.get("Content-Type") ?? "",
  );
  let body: ArrayBuffer | undefined;
  try {
    body = await boundedRequestBody(request, bodyLimit, config.httpTimeoutMs);
  } catch (error) {
    return error instanceof InvalidRequestBody
      ? errorResponse(error.code, error.status)
      : errorResponse("request_body_interrupted", 400);
  }

  const upstream = new URL(
    `/api/v1/admin/${path.map(encodeURIComponent).join("/")}`,
    config.apiBaseUrl,
  );
  upstream.search = request.nextUrl.search;

  const upstreamHeaders = upstreamHeadersForRequest(
    request.headers,
    token,
    request.method,
    path,
  );
  if (sourceContentRequest) {
    upstreamHeaders.Accept = "application/pdf";
    upstreamHeaders["Accept-Encoding"] = "identity";
  }

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
      signal: AbortSignal.any([
        request.signal,
        AbortSignal.timeout(config.httpTimeoutMs),
      ]),
    });
  } catch (error) {
    if (isTimeoutError(error)) {
      return errorResponse("upstream_timeout", 504);
    }
    return errorResponse("upstream_unavailable", 502);
  }
  if (response.status >= 300 && response.status < 400) {
    return errorResponse("upstream_unavailable", 502);
  }

  const responseContentType =
    response.headers.get("Content-Type") ?? "application/json";
  const mediaType = responseContentType.toLowerCase().split(";", 1)[0].trim();
  const disposition = safeInlineDisposition(
    response.headers.get("Content-Disposition"),
  );
  const contentRange = response.headers.get("Content-Range");
  const encoding = response.headers.get("Content-Encoding");
  const invalidPdf =
    sourceContentRequest &&
    (mediaType !== "application/pdf" ||
      !disposition ||
      (encoding !== null && encoding !== "identity") ||
      (response.status === 206 &&
        (!safeContentRange(contentRange) ||
          contentRange?.startsWith("bytes */"))));
  const invalidImage =
    sourceImageRequest &&
    !["image/png", "image/jpeg", "image/webp"].includes(mediaType);
  if (response.ok && (invalidPdf || invalidImage)) {
    await response.body?.cancel().catch(() => undefined);
    return errorResponse("upstream_unavailable", 502);
  }
  const responseHeaders: Record<string, string> = {
    "Cache-Control": "private, no-store",
    "Content-Type": responseContentType,
    "X-Content-Type-Options": "nosniff",
  };
  const retryAfter = response.headers.get("Retry-After");
  if (retryAfter && /^[0-9]{1,6}$/.test(retryAfter))
    responseHeaders["Retry-After"] = retryAfter;
  if (sourceContentRequest || sourceImageRequest) {
    responseHeaders["Content-Security-Policy"] =
      "default-src 'none'; frame-ancestors 'self'; sandbox";
    responseHeaders["Cross-Origin-Resource-Policy"] = "same-origin";
    responseHeaders["X-Frame-Options"] = "SAMEORIGIN";
  }
  if (sourceContentRequest) {
    if (disposition) responseHeaders["Content-Disposition"] = disposition;
    if (safeContentRange(contentRange))
      responseHeaders["Content-Range"] = contentRange!;
    const contentLength = response.headers.get("Content-Length");
    if (
      contentLength &&
      /^[0-9]{1,16}$/.test(contentLength) &&
      Number.isSafeInteger(Number(contentLength))
    )
      responseHeaders["Content-Length"] = contentLength;
    if (response.headers.get("Accept-Ranges") === "bytes")
      responseHeaders["Accept-Ranges"] = "bytes";
    const etag = response.headers.get("ETag");
    if (
      etag &&
      (safeEtag(etag) || (etag.startsWith("W/") && safeEtag(etag.slice(2))))
    )
      responseHeaders.ETag = etag;
    const modified = response.headers.get("Last-Modified");
    if (modified && safeHttpDate(modified))
      responseHeaders["Last-Modified"] = modified;
  }
  // Original PDFs and image bodies remain streams; never buffer a complete source here.
  return new NextResponse(request.method === "HEAD" ? null : response.body, {
    headers: responseHeaders,
    status: response.status,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
