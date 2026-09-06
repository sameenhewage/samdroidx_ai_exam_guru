import { request, type FullConfig } from "@playwright/test";

import { requireIsolatedE2ERuntime } from "./playwright-runtime";

export default async function globalSetup(config: FullConfig): Promise<void> {
  const runtime = requireIsolatedE2ERuntime(process.env);
  if (
    config.projects.length === 0 ||
    config.projects.some((project) => {
      try {
        const url = new URL(project.use.baseURL ?? "");
        return (
          url.origin !== runtime.baseURL ||
          url.pathname !== "/" ||
          !!url.username ||
          !!url.password ||
          !!url.search ||
          !!url.hash
        );
      } catch {
        return true;
      }
    })
  ) {
    throw new Error("Every Playwright project must use the same attested origin");
  }

  const context = await request.newContext({
    baseURL: runtime.baseURL,
    extraHTTPHeaders: { Origin: runtime.baseURL, "Sec-Fetch-Site": "same-origin" },
    timeout: 15_000,
  });
  try {
    const login = await context.post("/api/auth/development-login", {
      form: { role: "admin" },
      maxRedirects: 0,
    });
    const location = login.headers().location;
    let destination: URL | undefined;
    try {
      destination = location ? new URL(location, runtime.baseURL) : undefined;
    } catch {
      destination = undefined;
    }
    if (
      login.status() !== 303 ||
      !destination ||
      destination.origin !== runtime.baseURL ||
      destination.pathname !== "/admin/home" ||
      destination.username ||
      destination.password ||
      destination.search ||
      destination.hash
    ) {
      throw new Error("Isolated browser acceptance requires a same-origin development login");
    }

    const response = await context.get("/api/v1/admin/studio-safety/runtime-identity", {
      maxRedirects: 0,
    });
    if (response.status() !== 200) {
      throw new Error("Unable to verify the server runtime identity before browser fixtures");
    }
    const identity: unknown = await response.json();
    if (
      typeof identity !== "object" ||
      identity === null ||
      Array.isArray(identity) ||
      !("application_env" in identity) ||
      identity.application_env !== "test" ||
      !("test_runtime_id" in identity) ||
      identity.test_runtime_id !== runtime.composeProjectName
    ) {
      throw new Error("Browser fixtures require a matching server-attested test runtime identity");
    }
  } finally {
    await context.dispose();
  }
}
