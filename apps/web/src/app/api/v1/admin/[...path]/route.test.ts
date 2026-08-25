import { cookies } from "next/headers";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { bodyLimitForRequest, GET, POST, upstreamHeadersForRequest } from "./route";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));

describe("admin API proxy body limits", () => {
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
    expect(bodyLimitForRequest("POST", ["exam-configurations"], "application/json")).toBe(
      64 * 1024,
    );
    expect(bodyLimitForRequest("PATCH", ["source-documents"], "application/json")).toBe(
      64 * 1024,
    );
  });

  it("forwards only a bounded idempotency key alongside server-owned authorization", () => {
    const incoming = new Headers({
      Authorization: "Bearer attacker-controlled",
      "Content-Type": "application/json",
      "Idempotency-Key": "generation-12345678-1234-1234-1234-123456789012",
      "X-Provider": "client-provider",
    });

    expect(upstreamHeadersForRequest(incoming, "server-session-token")).toEqual({
      Authorization: "Bearer server-session-token",
      "Content-Type": "application/json",
      "Idempotency-Key": "generation-12345678-1234-1234-1234-123456789012",
    });
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
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it.each([
    new Headers({ Origin: "https://attacker.example" }),
    new Headers({ "Sec-Fetch-Site": "cross-site" }),
  ])("rejects malicious browser headers before session access or an upstream call", async (headers) => {
    const upstreamFetch = vi.fn();
    vi.stubGlobal("fetch", upstreamFetch);
    const request = new NextRequest("http://localhost:3000/api/v1/admin/exam-configurations", {
      headers,
      method: "POST",
    });

    const response = await POST(request, {
      params: Promise.resolve({ path: ["exam-configurations"] }),
    });

    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({
      detail: { code: "cross_site_request_rejected" },
    });
    expect(cookies).not.toHaveBeenCalled();
    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("uses validated configuration, a bounded signal, and disabled redirects", async () => {
    vi.mocked(cookies).mockResolvedValue({
      get: (name: string) =>
        name === "exam_guru_admin_token" ? { name, value: "server-session-token" } : undefined,
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
    expect(url.toString()).toBe("http://api.test:8000/api/v1/admin/exam-configurations?limit=10");
    expect(options.redirect).toBe("error");
    expect(options.signal).toBeInstanceOf(AbortSignal);
    expect(options.cache).toBe("no-store");
  });

  it("never turns a spoofed display-role cookie into API privilege", async () => {
    vi.mocked(cookies).mockResolvedValue({
      get: (name: string) => {
        if (name === "exam_guru_admin_token") return { name, value: "reviewer-token" };
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
    const request = new NextRequest("http://localhost:3000/api/v1/admin/exam-configurations", {
      headers: { Cookie: "exam_guru_admin_role=admin" },
    });

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
      get: () => ({ name: "exam_guru_admin_token", value: "secret-session-token" }),
    } as never);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("request timed out", "TimeoutError")),
    );
    const request = new NextRequest("http://localhost:3000/api/v1/admin/exam-configurations");

    const response = await GET(request, {
      params: Promise.resolve({ path: ["exam-configurations"] }),
    });

    expect(response.status).toBe(504);
    const responseCopy = response.clone();
    expect(await response.json()).toEqual({ detail: { code: "upstream_timeout" } });
    expect(await responseCopy.text()).not.toContain("secret-session-token");
  });

  it("never relays an upstream redirect even if a fetch implementation returns one", async () => {
    vi.mocked(cookies).mockResolvedValue({
      get: () => ({ name: "exam_guru_admin_token", value: "server-session-token" }),
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
    const request = new NextRequest("http://localhost:3000/api/v1/admin/exam-configurations");

    const response = await GET(request, {
      params: Promise.resolve({ path: ["exam-configurations"] }),
    });

    expect(response.status).toBe(502);
    expect(response.headers.has("Location")).toBe(false);
    await expect(response.json()).resolves.toEqual({ detail: { code: "upstream_unavailable" } });
  });

  it("rejects an unsafe API base URL before an upstream request", async () => {
    vi.stubEnv("API_BASE_URL", "https://api.example/internal");
    vi.mocked(cookies).mockResolvedValue({
      get: () => ({ name: "exam_guru_admin_token", value: "server-session-token" }),
    } as never);
    const upstreamFetch = vi.fn();
    vi.stubGlobal("fetch", upstreamFetch);
    const request = new NextRequest("http://localhost:3000/api/v1/admin/exam-configurations");

    await expect(
      GET(request, { params: Promise.resolve({ path: ["exam-configurations"] }) }),
    ).rejects.toThrow("API_BASE_URL must not contain a path");
    expect(upstreamFetch).not.toHaveBeenCalled();
  });
});
