import { cookies } from "next/headers";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { bodyLimitForRequest, POST, upstreamHeadersForRequest } from "./route";

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
    vi.stubEnv("ADMIN_COOKIE_SECURE", "false");
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
});
