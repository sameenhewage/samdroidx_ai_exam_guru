import { describe, expect, it } from "vitest";

import { bodyLimitForRequest } from "./route";

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
});
