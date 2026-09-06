import type { FullConfig } from "@playwright/test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import globalSetup from "../../e2e-global-setup";

const { newContext, post, get, dispose } = vi.hoisted(() => ({
  newContext: vi.fn(),
  post: vi.fn(),
  get: vi.fn(),
  dispose: vi.fn(),
}));

vi.mock("@playwright/test", () => ({ request: { newContext } }));

const baseURL = "http://127.0.0.1:43100";
const project = "ai-exam-guru-e2e-attestation-test";
const identityPath = "/api/v1/admin/studio-safety/runtime-identity";

function config(url = baseURL): FullConfig {
  return { projects: [{ use: { baseURL: url } }] } as unknown as FullConfig;
}

function identityResponse(body: unknown, status = 200) {
  return { status: () => status, json: vi.fn().mockResolvedValue(body) };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubEnv("E2E_BASE_URL", baseURL);
  vi.stubEnv("E2E_COMPOSE_PROJECT_NAME", project);
  vi.stubEnv("E2E_RUNTIME_ISOLATED", "true");
  newContext.mockResolvedValue({ post, get, dispose });
  post.mockResolvedValue({
    status: () => 303,
    headers: () => ({ location: `${baseURL}/admin/home` }),
  });
  get.mockResolvedValue(identityResponse({ application_env: "test", test_runtime_id: project }));
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("server-attested browser isolation", () => {
  it("authenticates on the fixture origin and checks the backend through its admin proxy", async () => {
    await globalSetup(config());

    expect(newContext).toHaveBeenCalledWith({
      baseURL,
      extraHTTPHeaders: { Origin: baseURL, "Sec-Fetch-Site": "same-origin" },
      timeout: 15_000,
    });
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/auth/development-login", {
      form: { role: "admin" },
      maxRedirects: 0,
    });
    expect(get).toHaveBeenCalledExactlyOnceWith(identityPath, { maxRedirects: 0 });
    expect(post.mock.invocationCallOrder[0]).toBeLessThan(get.mock.invocationCallOrder[0]);
    expect(dispose).toHaveBeenCalledExactlyOnceWith();
  });

  it.each([
    { application_env: "local", test_runtime_id: project },
    { application_env: "local" },
    { application_env: "staging", test_runtime_id: project },
    { application_env: "production", test_runtime_id: project },
    { application_env: "test", test_runtime_id: "ai-exam-guru-e2e-other-run" },
    { application_env: "test", test_runtime_id: null },
    { application_env: "test" },
    { environment: "test", test_runtime_id: project },
    null,
    [],
    "test",
  ])("rejects forged declarations or mismatched server identity before any fixture writes: %o", async (identity) => {
    get.mockResolvedValue(identityResponse(identity));
    const fixtureWrite = vi.fn();

    await expect(globalSetup(config()).then(fixtureWrite)).rejects.toThrow("server-attested");

    expect(fixtureWrite).not.toHaveBeenCalled();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0]?.[0]).toBe("/api/auth/development-login");
    expect(get).toHaveBeenCalledExactlyOnceWith(identityPath, { maxRedirects: 0 });
    expect(dispose).toHaveBeenCalledOnce();
  });

  it.each([301, 302, 401, 403, 404, 500, 503])("fails closed when the identity API returns %i", async (status) => {
    get.mockResolvedValue(identityResponse({ application_env: "test", test_runtime_id: project }, status));
    const fixtureWrite = vi.fn();

    await expect(globalSetup(config()).then(fixtureWrite)).rejects.toThrow("runtime identity");

    expect(fixtureWrite).not.toHaveBeenCalled();
    expect(dispose).toHaveBeenCalledOnce();
  });

  it("rejects non-JSON identity responses and disposes credentials", async () => {
    get.mockResolvedValue({ status: () => 200, json: vi.fn().mockRejectedValue(new Error("invalid JSON")) });

    await expect(globalSetup(config())).rejects.toThrow();

    expect(dispose).toHaveBeenCalledOnce();
  });

  it.each([
    [200, `${baseURL}/admin/home`],
    [401, `${baseURL}/admin/home`],
    [403, `${baseURL}/admin/home`],
    [303, "http://localhost:3000/admin/home"],
    [303, "http://127.0.0.1:49999/admin/home"],
    [303, "https://example.com/admin/home"],
    [303, ""],
  ])("does not follow failed or cross-origin login redirects: %i %s", async (status, location) => {
    post.mockResolvedValue({ status: () => status, headers: () => ({ location }) });

    await expect(globalSetup(config())).rejects.toThrow("development login");

    expect(get).not.toHaveBeenCalled();
    expect(dispose).toHaveBeenCalledOnce();
  });

  it("does not contact a server when an actual Playwright project uses a different origin", async () => {
    await expect(globalSetup(config("http://127.0.0.1:43101"))).rejects.toThrow("same attested origin");

    expect(newContext).not.toHaveBeenCalled();
  });

  it("checks every project rather than only the first", async () => {
    const mixed = config();
    mixed.projects.push({ use: { baseURL: "http://localhost:3000" } } as FullConfig["projects"][number]);

    await expect(globalSetup(mixed)).rejects.toThrow("same attested origin");

    expect(newContext).not.toHaveBeenCalled();
  });

  it("keeps the existing declaration and normal-port checks ahead of authentication", async () => {
    vi.stubEnv("E2E_BASE_URL", "http://localhost:3000");

    await expect(globalSetup(config())).rejects.toThrow("normal Studio origin");

    expect(newContext).not.toHaveBeenCalled();
  });

  it("rejects missing isolation declarations without even logging in", async () => {
    vi.stubEnv("E2E_RUNTIME_ISOLATED", "false");

    await expect(globalSetup(config())).rejects.toThrow("E2E_RUNTIME_ISOLATED=true");

    expect(newContext).not.toHaveBeenCalled();
  });

  it("disposes the authenticated context on network failure without running fixtures", async () => {
    get.mockRejectedValue(new Error("connection failed"));
    const fixtureWrite = vi.fn();

    await expect(globalSetup(config()).then(fixtureWrite)).rejects.toThrow("connection failed");

    expect(fixtureWrite).not.toHaveBeenCalled();
    expect(dispose).toHaveBeenCalledOnce();
  });
});
