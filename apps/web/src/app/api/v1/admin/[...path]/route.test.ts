import { describe, expect, it } from "vitest";

import { bodyLimitForRequest, upstreamHeadersForRequest } from "./route";

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
