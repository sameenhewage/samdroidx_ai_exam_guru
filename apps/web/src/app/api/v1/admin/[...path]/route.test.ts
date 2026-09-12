import { cookies } from "next/headers";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  bodyLimitForRequest,
  GET,
  POST,
  PUT,
  upstreamHeadersForRequest,
} from "./route";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));

const uploadId = "00000000-0000-0000-0000-000000000981";
const uploadPath = ["source-uploads", uploadId, "chunks"];
const chunkBytes = 4_194_304;
const originalPath = ["materials", uploadId, "original"];
const pdfHeaders = {
  "Content-Type": "application/pdf",
  "Content-Disposition":
    "inline; filename=\"source.pdf\"; filename*=UTF-8''source.pdf",
};

function originalRequest(signal?: AbortSignal) {
  return new NextRequest(
    `http://localhost:3000/api/v1/admin/materials/${uploadId}/original`,
    { signal },
  );
}

function adminSession() {
  vi.mocked(cookies).mockResolvedValue({
    get: (name: string) =>
      name === "exam_guru_admin_token"
        ? { name, value: "server-session-token" }
        : undefined,
  } as never);
}

describe("admin API proxy body limits", () => {
  it("allows only the real raw resumable chunk route a 4 MiB bound without enlarging legacy or other requests", () => {
    expect(
      bodyLimitForRequest("PUT", uploadPath, "application/octet-stream"),
    ).toBe(chunkBytes);
    expect(
      bodyLimitForRequest("POST", uploadPath, "application/octet-stream"),
    ).toBe(64 * 1024);
    expect(
      bodyLimitForRequest(
        "PUT",
        ["exam-configurations"],
        "application/octet-stream",
      ),
    ).toBe(64 * 1024);
    expect(bodyLimitForRequest("PUT", uploadPath, "application/json")).toBe(
      64 * 1024,
    );
  });

  it("forwards the actual X-Chunk-SHA256 contract only on upload chunks, never browser authorization or invented Upload headers", () => {
    const incoming = new Headers({
      Authorization: "Bearer attacker",
      "Content-Type": "application/octet-stream",
      "X-Chunk-SHA256": "a".repeat(64),
      "Upload-Offset": "9",
      "Upload-Version": "12",
      Cookie: "secret=local",
      "X-Admin-Role": "admin",
    });
    expect(
      upstreamHeadersForRequest(
        incoming,
        "server-session-token",
        "PUT",
        uploadPath,
      ),
    ).toEqual({
      Authorization: "Bearer server-session-token",
      "Content-Type": "application/octet-stream",
      "X-Chunk-SHA256": "a".repeat(64),
    });
    expect(
      upstreamHeadersForRequest(incoming, "server-session-token", "POST", [
        "exam-configurations",
      ]),
    ).not.toHaveProperty("X-Chunk-SHA256");
  });

  it("allows bounded PDF multipart uploads", () => {
    expect(
      bodyLimitForRequest(
        "POST",
        ["source-documents"],
        "multipart/form-data; boundary=fixture",
      ),
    ).toBe(26 * 1024 * 1024);
  });

  it("keeps non-upload admin requests tightly bounded", () => {
    expect(
      bodyLimitForRequest("POST", ["exam-configurations"], "application/json"),
    ).toBe(64 * 1024);
    expect(
      bodyLimitForRequest("PATCH", ["source-documents"], "application/json"),
    ).toBe(64 * 1024);
  });

  it("forwards only a bounded idempotency key alongside server-owned authorization", () => {
    const incoming = new Headers({
      Authorization: "Bearer attacker-controlled",
      "Content-Type": "application/json",
      "Idempotency-Key": "generation-12345678-1234-1234-1234-123456789012",
      "X-Provider": "client-provider",
    });

    expect(upstreamHeadersForRequest(incoming, "server-session-token")).toEqual(
      {
        Authorization: "Bearer server-session-token",
        "Content-Type": "application/json",
        "Idempotency-Key": "generation-12345678-1234-1234-1234-123456789012",
      },
    );
    expect(
      upstreamHeadersForRequest(
        new Headers({ "Idempotency-Key": `generation-${"x".repeat(129)}` }),
        "server-session-token",
      ),
    ).toEqual({
      Authorization: "Bearer server-session-token",
      "Content-Type": "application/json",
    });
  });
});

describe("admin API proxy browser request boundary", () => {
  beforeEach(() => {
    vi.stubEnv("APP_ENVIRONMENT", "test");
    vi.stubEnv("APP_BASE_URL", "http://localhost:3000");
    vi.stubEnv("API_BASE_URL", "http://api.test:8000");
    vi.stubEnv("ADMIN_COOKIE_SECURE", "false");
    vi.stubEnv("OIDC_HTTP_TIMEOUT_MS", "100");
    vi.stubEnv("WEB_IDENTITY_PROVIDER", "deny");
    vi.mocked(cookies).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it.each([
    new Headers({ Origin: "https://attacker.example" }),
    new Headers({ "Sec-Fetch-Site": "cross-site" }),
  ])(
    "rejects malicious browser headers before session access or an upstream call",
    async (headers) => {
      const upstreamFetch = vi.fn();
      vi.stubGlobal("fetch", upstreamFetch);
      const request = new NextRequest(
        "http://localhost:3000/api/v1/admin/exam-configurations",
        {
          headers,
          method: "POST",
        },
      );

      const response = await POST(request, {
        params: Promise.resolve({ path: ["exam-configurations"] }),
      });

      expect(response.status).toBe(403);
      await expect(response.json()).resolves.toEqual({
        detail: { code: "cross_site_request_rejected" },
      });
      expect(cookies).not.toHaveBeenCalled();
      expect(upstreamFetch).not.toHaveBeenCalled();
    },
  );

  it("forwards bounded raw chunks and exact offsets without whole-request arrayBuffer buffering", async () => {
    adminSession();
    const upstream = vi.fn(async (_url: URL, options: RequestInit) => {
      expect(new Uint8Array(options.body as ArrayBuffer).byteLength).toBe(
        chunkBytes,
      );
      return Response.json({ next_offset: chunkBytes * 2, version: 2 });
    });
    vi.stubGlobal("fetch", upstream);
    const request = new NextRequest(
      `http://localhost:3000/api/v1/admin/source-uploads/${uploadId}/chunks?offset=${chunkBytes}`,
      {
        method: "PUT",
        headers: {
          Origin: "http://localhost:3000",
          "Content-Type": "application/octet-stream",
          "X-Chunk-SHA256": "b".repeat(64),
        },
        body: new Uint8Array(chunkBytes),
      },
    );
    const wholeBody = vi
      .spyOn(request, "arrayBuffer")
      .mockRejectedValue(new Error("Do not fully buffer arbitrary requests"));
    const response = await PUT(request, {
      params: Promise.resolve({ path: uploadPath }),
    });
    expect(response.status).toBe(200);
    expect(wholeBody).not.toHaveBeenCalled();
    expect(upstream.mock.calls[0][0].searchParams.get("offset")).toBe(
      String(chunkBytes),
    );
    expect(
      new Headers(upstream.mock.calls[0][1].headers).get("X-Chunk-SHA256"),
    ).toBe("b".repeat(64));
  });

  it.each([true, false])(
    "rejects oversized chunk streams early and cancels the unread body (declared length: %s)",
    async (declared) => {
      adminSession();
      let cancelled = false;
      let emitted = 0;
      const stream = new ReadableStream<Uint8Array>({
        pull(controller) {
          controller.enqueue(new Uint8Array(emitted++ === 0 ? chunkBytes : 1));
        },
        cancel() {
          cancelled = true;
        },
      });
      const request = new NextRequest(
        `http://localhost:3000/api/v1/admin/source-uploads/${uploadId}/chunks?offset=0`,
        {
          method: "PUT",
          body: stream,
          duplex: "half",
          headers: {
            Origin: "http://localhost:3000",
            "Content-Type": "application/octet-stream",
            ...(declared ? { "Content-Length": String(chunkBytes + 1) } : {}),
          },
        } as ConstructorParameters<typeof NextRequest>[1] & { duplex: "half" },
      );
      const wholeBody = vi
        .spyOn(request, "arrayBuffer")
        .mockRejectedValue(new Error("Unbounded buffering"));
      const upstream = vi.fn();
      vi.stubGlobal("fetch", upstream);
      const response = await PUT(request, {
        params: Promise.resolve({ path: uploadPath }),
      });
      expect(response.status).toBe(413);
      expect(cancelled).toBe(true);
      expect(wholeBody).not.toHaveBeenCalled();
      expect(upstream).not.toHaveBeenCalled();
    },
  );

  it("bounds a stalled incoming chunk by the configured request deadline", async () => {
    adminSession();
    let cancelled = false;
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
      cancel() {
        cancelled = true;
      },
    });
    const request = new NextRequest(
      `http://localhost:3000/api/v1/admin/source-uploads/${uploadId}/chunks?offset=0`,
      {
        method: "PUT",
        body: stream,
        duplex: "half",
        headers: { "Content-Type": "application/octet-stream" },
      } as ConstructorParameters<typeof NextRequest>[1] & { duplex: "half" },
    );
    const upstream = vi.fn();
    vi.stubGlobal("fetch", upstream);
    const pending = PUT(request, {
      params: Promise.resolve({ path: uploadPath }),
    });
    const result = await Promise.race([
      pending,
      new Promise<null>((resolve) => setTimeout(() => resolve(null), 250)),
    ]);
    if (!result) {
      streamController.error(new Error("Test closes stalled stream"));
      await pending;
    }
    expect(result?.status).toBe(408);
    expect(cancelled).toBe(true);
    expect(upstream).not.toHaveBeenCalled();
  });

  it("rejects malformed chunk checksums instead of silently dropping an integrity header", async () => {
    adminSession();
    const upstream = vi.fn();
    vi.stubGlobal("fetch", upstream);
    const response = await PUT(
      new NextRequest(
        `http://localhost:3000/api/v1/admin/source-uploads/${uploadId}/chunks?offset=0`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/octet-stream",
            "X-Chunk-SHA256": "not-a-checksum",
          },
          body: new Uint8Array(5),
        },
      ),
      { params: Promise.resolve({ path: uploadPath }) },
    );
    expect(response.status).toBe(400);
    expect(upstream).not.toHaveBeenCalled();
  });

  it("preserves private quota/rate error bodies and Retry-After without propagating cookies", async () => {
    adminSession();
    const body = { detail: { code: "rate_limit_exceeded" } };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(body, {
          status: 429,
          headers: {
            "Retry-After": "17",
            "Set-Cookie": "upstream=secret",
            "Cache-Control": "public",
          },
        }),
      ),
    );
    const response = await POST(
      new NextRequest("http://localhost:3000/api/v1/admin/source-uploads", {
        method: "POST",
        body: "{}",
        headers: { "Content-Type": "application/json" },
      }),
      { params: Promise.resolve({ path: ["source-uploads"] }) },
    );
    expect(response.status).toBe(429);
    expect(await response.json()).toEqual(body);
    expect(response.headers.get("Retry-After")).toBe("17");
    expect(response.headers.get("Cache-Control")).toContain("no-store");
    expect(response.headers.has("Set-Cookie")).toBe(false);
  });

  it("keeps a progressing original-PDF stream alive after the ordinary HTTP deadline", async () => {
    adminSession();
    vi.useFakeTimers();
    const timeout = vi
      .spyOn(AbortSignal, "timeout")
      .mockImplementation((milliseconds) => {
        const controller = new AbortController();
        setTimeout(
          () =>
            controller.abort(
              new DOMException("Request timed out", "TimeoutError"),
            ),
          milliseconds,
        );
        return controller.signal;
      });
    let signal: AbortSignal | null | undefined;
    let upstreamResponse: Response | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: URL, options: RequestInit) => {
        signal = options.signal;
        const chunks = ["%PDF-", "source", "-end"];
        upstreamResponse = new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              signal?.addEventListener(
                "abort",
                () => controller.error(signal?.reason),
                { once: true },
              );
            },
            async pull(controller) {
              await new Promise((resolve) => setTimeout(resolve, 80));
              if (signal?.aborted) return;
              const chunk = chunks.shift();
              if (chunk === undefined) controller.close();
              else controller.enqueue(new TextEncoder().encode(chunk));
            },
          }),
          {
            headers: {
              "Content-Type": "application/pdf",
              "Content-Disposition":
                "inline; filename=\"source.pdf\"; filename*=UTF-8''source.pdf",
            },
          },
        );
        return upstreamResponse;
      }),
    );
    try {
      const response = await GET(
        new NextRequest(
          `http://localhost:3000/api/v1/admin/materials/${uploadId}/original`,
        ),
        {
          params: Promise.resolve({
            path: ["materials", uploadId, "original"],
          }),
        },
      );
      const buffering = vi.spyOn(upstreamResponse!, "arrayBuffer");
      const received = response.text().then(
        (value) => ({ value }),
        (error) => ({ error }),
      );
      await vi.advanceTimersByTimeAsync(400);
      expect(await received).toEqual({ value: "%PDF-source-end" });
      expect(signal?.aborted).toBe(false);
      expect(buffering).not.toHaveBeenCalled();
    } finally {
      timeout.mockRestore();
      vi.useRealTimers();
    }
  });

  it("retains a finite source-header deadline without applying the ordinary timeout to integrity checking", async () => {
    adminSession();
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: URL, options: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            options.signal?.addEventListener(
              "abort",
              () => reject(options.signal?.reason),
              { once: true },
            );
          }),
      ),
    );
    let finished = false;
    const pending = GET(originalRequest(), {
      params: Promise.resolve({ path: originalPath }),
    }).then((response) => {
      finished = true;
      return response;
    });
    await vi.advanceTimersByTimeAsync(29_999);
    expect(finished).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const response = await pending;
    expect(response.status).toBe(504);
    await expect(response.json()).resolves.toEqual({
      detail: { code: "upstream_timeout" },
    });
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each(["idle", "client", "consumer", "failure"])(
    "releases the source stream and timers on %s termination",
    async (mode) => {
      adminSession();
      vi.useFakeTimers();
      const client = new AbortController();
      let signal: AbortSignal | null | undefined;
      let upstreamController!: ReadableStreamDefaultController<Uint8Array>;
      const cancelled = vi.fn();
      vi.stubGlobal(
        "fetch",
        vi.fn(async (_url: URL, options: RequestInit) => {
          signal = options.signal;
          return new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                upstreamController = controller;
              },
              cancel: cancelled,
            }),
            { headers: pdfHeaders },
          );
        }),
      );
      const response = await GET(originalRequest(client.signal), {
        params: Promise.resolve({ path: originalPath }),
      });
      if (mode === "consumer") await response.body!.cancel();
      else {
        const received = response.text().then(
          () => "completed",
          (error) => error.name,
        );
        if (mode === "client") client.abort();
        else if (mode === "failure")
          upstreamController.error(new Error("Source disconnected"));
        else await vi.advanceTimersByTimeAsync(30_001);
        expect(await received).not.toBe("completed");
      }
      await vi.advanceTimersByTimeAsync(0);
      expect(signal?.aborted).toBe(true);
      if (mode !== "failure") expect(cancelled).toHaveBeenCalledOnce();
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("keeps a total source-transfer bound even while bytes continue to arrive", async () => {
    adminSession();
    vi.useFakeTimers();
    let signal: AbortSignal | null | undefined;
    let chunks = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: URL, options: RequestInit) => {
        signal = options.signal;
        return new Response(
          new ReadableStream<Uint8Array>({
            async pull(controller) {
              await new Promise((resolve) => setTimeout(resolve, 10_000));
              if (!signal?.aborted) {
                chunks += 1;
                controller.enqueue(new Uint8Array([1]));
              }
            },
          }),
          { headers: pdfHeaders },
        );
      }),
    );
    const response = await GET(originalRequest(), {
      params: Promise.resolve({ path: originalPath }),
    });
    const received = response.text().then(
      () => "completed",
      (error) => error.name,
    );
    await vi.advanceTimersByTimeAsync(899_999);
    expect(signal?.aborted).toBe(false);
    expect(chunks).toBeGreaterThan(80);
    await vi.advanceTimersByTimeAsync(1);
    expect(await received).toBe("TimeoutError");
    expect(signal?.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not treat empty upstream chunks as download progress", async () => {
    adminSession();
    vi.useFakeTimers();
    const client = new AbortController();
    let signal: AbortSignal | null | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: URL, options: RequestInit) => {
        signal = options.signal;
        return new Response(
          new ReadableStream<Uint8Array>({
            async pull(controller) {
              await new Promise((resolve) => setTimeout(resolve, 10_000));
              if (!signal?.aborted) controller.enqueue(new Uint8Array());
            },
          }),
          { headers: pdfHeaders },
        );
      }),
    );
    const response = await GET(originalRequest(client.signal), {
      params: Promise.resolve({ path: originalPath }),
    });
    const received = response.text().catch(() => null);
    try {
      await vi.advanceTimersByTimeAsync(30_001);
      expect(signal?.aborted).toBe(true);
    } finally {
      client.abort();
      await received;
      await vi.advanceTimersByTimeAsync(10_000);
    }
  });

  it("streams new original PDF ranges with safe conditional headers and private PDF sandboxing", async () => {
    adminSession();
    const data = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([37, 80, 68, 70]));
        controller.close();
      },
    });
    const upstreamResponse = new Response(data, {
      status: 206,
      headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition":
          "inline; filename=\"source.pdf\"; filename*=UTF-8''source.pdf",
        "Content-Range": "bytes 0-3/314572800",
        "Content-Length": "4",
        "Accept-Ranges": "bytes",
        ETag: '"source-sha256"',
      },
    });
    const buffer = vi.spyOn(upstreamResponse, "arrayBuffer");
    const upstream = vi.fn(async () => upstreamResponse);
    vi.stubGlobal("fetch", upstream);
    const response = await GET(
      new NextRequest(
        `http://localhost:3000/api/v1/admin/materials/${uploadId}/original`,
        {
          headers: {
            Range: "bytes=0-3",
            "If-Range": '"source-sha256"',
            Authorization: "Bearer spoofed",
          },
        },
      ),
      {
        params: Promise.resolve({ path: ["materials", uploadId, "original"] }),
      },
    );
    expect(response.status).toBe(206);
    expect(response.headers.get("Content-Range")).toBe("bytes 0-3/314572800");
    expect(response.headers.get("Content-Length")).toBe("4");
    expect(response.headers.get("Accept-Ranges")).toBe("bytes");
    expect(response.headers.get("ETag")).toBe('"source-sha256"');
    expect(response.headers.get("Content-Security-Policy")).toContain(
      "sandbox",
    );
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(buffer).not.toHaveBeenCalled();
    expect(await response.arrayBuffer()).toHaveProperty("byteLength", 4);
    const forwarded = new Headers(
      (upstream.mock.calls[0] as unknown as [URL, RequestInit])[1].headers,
    );
    expect(forwarded.get("Range")).toBe("bytes=0-3");
    expect(forwarded.get("If-Range")).toBe('"source-sha256"');
    expect(forwarded.get("Authorization")).toBe("Bearer server-session-token");
  });

  it.each([
    { path: ["materials", uploadId, "pages", "1", "image"] },
    {
      path: [
        "materials",
        uploadId,
        "pages",
        "1",
        "understanding",
        "candidates",
        uploadId,
        "image",
      ],
    },
    {
      path: [
        "source-benchmarks",
        uploadId,
        "evaluation-previews",
        uploadId,
        "image",
      ],
    },
  ])(
    "rejects active image content on each source-comparison path: $path",
    async ({ path }) => {
      adminSession();
      vi.stubGlobal(
        "fetch",
        vi.fn(
          async () =>
            new Response("<script>bad</script>", {
              headers: { "Content-Type": "text/html" },
            }),
        ),
      );
      const response = await GET(
        new NextRequest(`http://localhost:3000/api/v1/admin/${path.join("/")}`),
        { params: Promise.resolve({ path }) },
      );
      expect(response.status).toBe(502);
    },
  );

  it.each([
    {
      path: [
        "materials",
        uploadId,
        "pages",
        "1",
        "understanding",
        "candidates",
        uploadId,
        "image",
      ],
    },
    {
      path: [
        "source-benchmarks",
        uploadId,
        "evaluation-previews",
        uploadId,
        "image",
      ],
    },
  ])(
    "keeps comparison image responses private and sandboxed: $path",
    async ({ path }) => {
      adminSession();
      vi.stubGlobal(
        "fetch",
        vi.fn(
          async () =>
            new Response("image fixture", {
              headers: { "Content-Type": "image/png" },
            }),
        ),
      );
      const response = await GET(
        new NextRequest(`http://localhost:3000/api/v1/admin/${path.join("/")}`),
        { params: Promise.resolve({ path }) },
      );
      expect(response.status).toBe(200);
      expect(response.headers.get("Cross-Origin-Resource-Policy")).toBe(
        "same-origin",
      );
      expect(response.headers.get("Content-Security-Policy")).toContain(
        "sandbox",
      );
      expect(response.headers.get("X-Frame-Options")).toBe("SAMEORIGIN");
      expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    },
  );

  it("uses validated configuration, a bounded signal, and disabled redirects", async () => {
    vi.mocked(cookies).mockResolvedValue({
      get: (name: string) =>
        name === "exam_guru_admin_token"
          ? { name, value: "server-session-token" }
          : undefined,
    } as never);
    const upstreamFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), {
        headers: { "Content-Type": "application/json" },
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", upstreamFetch);
    const request = new NextRequest(
      "http://localhost:3000/api/v1/admin/exam-configurations?limit=10",
    );

    const response = await GET(request, {
      params: Promise.resolve({ path: ["exam-configurations"] }),
    });

    expect(response.status).toBe(200);
    expect(upstreamFetch).toHaveBeenCalledTimes(1);
    const [url, options] = upstreamFetch.mock.calls[0] as [URL, RequestInit];
    expect(url.toString()).toBe(
      "http://api.test:8000/api/v1/admin/exam-configurations?limit=10",
    );
    expect(options.redirect).toBe("error");
    expect(options.signal).toBeInstanceOf(AbortSignal);
    expect(options.cache).toBe("no-store");
  });

  it("streams only authorized original PDFs with private same-origin framing headers", async () => {
    vi.mocked(cookies).mockResolvedValue({
      get: (name: string) =>
        name === "exam_guru_admin_token"
          ? { name, value: "server-session-token" }
          : undefined,
    } as never);
    const disposition =
      "inline; filename=\"source.pdf\"; filename*=UTF-8''Scholarship%20source.pdf";
    const upstreamFetch = vi.fn().mockResolvedValue(
      new Response(new Uint8Array([0x25, 0x50, 0x44, 0x46]), {
        headers: {
          "Cache-Control": "public, max-age=3600",
          "Content-Disposition": disposition,
          "Content-Type": "application/pdf",
          "X-Content-Type-Options": "nosniff",
        },
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", upstreamFetch);
    const documentId = "00000000-0000-0000-0000-000000000901";
    const request = new NextRequest(
      `http://localhost:3000/api/v1/admin/source-documents/${documentId}/content`,
    );

    const response = await GET(request, {
      params: Promise.resolve({
        path: ["source-documents", documentId, "content"],
      }),
    });

    expect(response.status).toBe(200);
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(
      new Uint8Array([0x25, 0x50, 0x44, 0x46]),
    );
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(response.headers.get("Content-Disposition")).toBe(disposition);
    expect(response.headers.get("Content-Type")).toBe("application/pdf");
    expect(response.headers.get("X-Content-Type-Options")).toBe("nosniff");
    expect(response.headers.get("X-Frame-Options")).toBe("SAMEORIGIN");
    expect(response.headers.get("Content-Security-Policy")).toBe(
      "default-src 'none'; frame-ancestors 'self'; sandbox",
    );
    const options = upstreamFetch.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(options.headers).get("Authorization")).toBe(
      "Bearer server-session-token",
    );
  });

  it("rejects unsafe upstream PDF disposition metadata instead of relaying it", async () => {
    vi.mocked(cookies).mockResolvedValue({
      get: () => ({
        name: "exam_guru_admin_token",
        value: "server-session-token",
      }),
    } as never);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(new Uint8Array([0x25, 0x50, 0x44, 0x46]), {
          headers: {
            "Content-Disposition":
              "inline; filename=\"../private.pdf\"; filename*=UTF-8''..%2Fprivate.pdf",
            "Content-Type": "application/pdf",
          },
          status: 200,
        }),
      ),
    );
    const documentId = "00000000-0000-0000-0000-000000000901";
    const request = new NextRequest(
      `http://localhost:3000/api/v1/admin/source-documents/${documentId}/content`,
    );

    const response = await GET(request, {
      params: Promise.resolve({
        path: ["source-documents", documentId, "content"],
      }),
    });

    expect(response.status).toBe(502);
    expect(response.headers.has("Content-Disposition")).toBe(false);
    await expect(response.json()).resolves.toEqual({
      detail: { code: "upstream_unavailable" },
    });
  });

  it("never turns a spoofed display-role cookie into API privilege", async () => {
    vi.mocked(cookies).mockResolvedValue({
      get: (name: string) => {
        if (name === "exam_guru_admin_token")
          return { name, value: "reviewer-token" };
        if (name === "exam_guru_admin_role") return { name, value: "admin" };
        return undefined;
      },
    } as never);
    const upstreamFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: { code: "permission_denied" } }), {
        headers: { "Content-Type": "application/json" },
        status: 403,
      }),
    );
    vi.stubGlobal("fetch", upstreamFetch);
    const request = new NextRequest(
      "http://localhost:3000/api/v1/admin/exam-configurations",
      {
        headers: { Cookie: "exam_guru_admin_role=admin" },
      },
    );

    const response = await GET(request, {
      params: Promise.resolve({ path: ["exam-configurations"] }),
    });

    expect(response.status).toBe(403);
    const options = upstreamFetch.mock.calls[0]?.[1] as RequestInit;
    const headers = new Headers(options.headers);
    expect(headers.get("Authorization")).toBe("Bearer reviewer-token");
    expect(headers.has("Cookie")).toBe(false);
    expect(headers.has("X-Admin-Role")).toBe(false);
  });

  it("returns a fixed timeout response without leaking the session token", async () => {
    vi.mocked(cookies).mockResolvedValue({
      get: () => ({
        name: "exam_guru_admin_token",
        value: "secret-session-token",
      }),
    } as never);
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockRejectedValue(
          new DOMException("request timed out", "TimeoutError"),
        ),
    );
    const request = new NextRequest(
      "http://localhost:3000/api/v1/admin/exam-configurations",
    );

    const response = await GET(request, {
      params: Promise.resolve({ path: ["exam-configurations"] }),
    });

    expect(response.status).toBe(504);
    const responseCopy = response.clone();
    expect(await response.json()).toEqual({
      detail: { code: "upstream_timeout" },
    });
    expect(await responseCopy.text()).not.toContain("secret-session-token");
  });

  it("never relays an upstream redirect even if a fetch implementation returns one", async () => {
    vi.mocked(cookies).mockResolvedValue({
      get: () => ({
        name: "exam_guru_admin_token",
        value: "server-session-token",
      }),
    } as never);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(null, {
          headers: { Location: "https://attacker.example/steal" },
          status: 302,
        }),
      ),
    );
    const request = new NextRequest(
      "http://localhost:3000/api/v1/admin/exam-configurations",
    );

    const response = await GET(request, {
      params: Promise.resolve({ path: ["exam-configurations"] }),
    });

    expect(response.status).toBe(502);
    expect(response.headers.has("Location")).toBe(false);
    await expect(response.json()).resolves.toEqual({
      detail: { code: "upstream_unavailable" },
    });
  });

  it("rejects an unsafe API base URL before an upstream request", async () => {
    vi.stubEnv("API_BASE_URL", "https://api.example/internal");
    vi.mocked(cookies).mockResolvedValue({
      get: () => ({
        name: "exam_guru_admin_token",
        value: "server-session-token",
      }),
    } as never);
    const upstreamFetch = vi.fn();
    vi.stubGlobal("fetch", upstreamFetch);
    const request = new NextRequest(
      "http://localhost:3000/api/v1/admin/exam-configurations",
    );

    await expect(
      GET(request, {
        params: Promise.resolve({ path: ["exam-configurations"] }),
      }),
    ).rejects.toThrow("API_BASE_URL must not contain a path");
    expect(upstreamFetch).not.toHaveBeenCalled();
  });
});
