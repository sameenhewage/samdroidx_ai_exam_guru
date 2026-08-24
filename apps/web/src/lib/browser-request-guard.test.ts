import { describe, expect, it } from "vitest";

import { guardBrowserRequest } from "./browser-request-guard";
import { parseWebAppConfig } from "./web-app-config";

const config = parseWebAppConfig({
  ADMIN_COOKIE_SECURE: "false",
  APP_BASE_URL: "http://localhost:3000",
  APP_ENVIRONMENT: "test",
});

function request(method: string, headers: HeadersInit = {}): Pick<Request, "headers" | "method"> {
  return { headers: new Headers(headers), method };
}

describe("cookie-authenticated browser request guard", () => {
  it("does not apply to safe methods", () => {
    expect(
      guardBrowserRequest(
        request("GET", {
          Origin: "https://attacker.example",
          "Sec-Fetch-Site": "cross-site",
        }),
        config,
      ),
    ).toBeNull();
  });

  it.each(["POST", "PATCH", "PUT", "DELETE"])(
    "allows %s without browser-owned headers for server and APIRequestContext callers",
    (method) => {
      expect(guardBrowserRequest(request(method), config)).toBeNull();
    },
  );

  it("allows an exact configured Origin from a same-origin browser", () => {
    expect(
      guardBrowserRequest(
        request("POST", {
          Origin: "http://localhost:3000",
          "Sec-Fetch-Site": "same-origin",
        }),
        config,
      ),
    ).toBeNull();
  });

  it("rejects a browser-declared cross-site request even with the configured Origin", async () => {
    const response = guardBrowserRequest(
      request("PATCH", {
        Origin: "http://localhost:3000",
        "Sec-Fetch-Site": "cross-site",
      }),
      config,
    );

    expect(response).not.toBeNull();
    expect(response?.status).toBe(403);
    expect(await response?.text()).toBe(
      '{"detail":{"code":"cross_site_request_rejected"}}',
    );
  });

  it("rejects every supplied Origin that is not the exact configured origin", async () => {
    const attackerOrigin = "https://attacker.example";
    const response = guardBrowserRequest(
      request("DELETE", {
        Origin: attackerOrigin,
        "Sec-Fetch-Site": "same-site",
      }),
      config,
    );

    expect(response).not.toBeNull();
    expect(response?.status).toBe(403);
    expect(response?.headers.get("Access-Control-Allow-Origin")).toBeNull();
    expect(await response?.json()).toEqual({
      detail: { code: "cross_site_request_rejected" },
    });
  });
});
