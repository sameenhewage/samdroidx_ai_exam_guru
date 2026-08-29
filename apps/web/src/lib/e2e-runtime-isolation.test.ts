import { describe, expect, it } from "vitest";

import { requireIsolatedE2ERuntime } from "../../playwright-runtime";

const isolatedEnvironment = {
  E2E_BASE_URL: "http://127.0.0.1:43100",
  E2E_COMPOSE_PROJECT_NAME: "ai-exam-guru-e2e-1234",
  E2E_RUNTIME_ISOLATED: "true",
} as const;

describe("Playwright runtime isolation", () => {
  it("accepts an explicit loopback throwaway Compose runtime", () => {
    expect(requireIsolatedE2ERuntime(isolatedEnvironment)).toEqual({
      baseURL: "http://127.0.0.1:43100",
      composeProjectName: "ai-exam-guru-e2e-1234",
    });
  });

  it.each([
    [{ ...isolatedEnvironment, E2E_RUNTIME_ISOLATED: undefined }, "E2E_RUNTIME_ISOLATED=true"],
    [{ ...isolatedEnvironment, E2E_COMPOSE_PROJECT_NAME: "ai-exam-guru" }, "throwaway Compose project"],
    [{ ...isolatedEnvironment, E2E_COMPOSE_PROJECT_NAME: "unexpected-project" }, "throwaway Compose project"],
    [{ ...isolatedEnvironment, E2E_BASE_URL: "http://localhost:3000" }, "normal Studio origin"],
    [{ ...isolatedEnvironment, E2E_BASE_URL: "https://example.com" }, "loopback HTTP origin"],
    [
      {
        ...isolatedEnvironment,
        E2E_BASE_URL: ["http://", "user", ":", "password", "@127.0.0.1:43100"].join(""),
      },
      "origin only",
    ],
    [{ ...isolatedEnvironment, E2E_BASE_URL: "http://127.0.0.1:43100/admin" }, "origin only"],
  ] as const)("rejects a runtime that is not isolated: %o", (environment, message) => {
    expect(() => requireIsolatedE2ERuntime(environment)).toThrow(message);
  });
});
