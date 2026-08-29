type Environment = Readonly<Record<string, string | undefined>>;

type IsolatedE2ERuntime = Readonly<{
  baseURL: string;
  composeProjectName: string;
}>;

const isolatedProjectPattern = /^ai-exam-guru-e2e-[a-z0-9][a-z0-9-]{0,47}$/;
const loopbackHosts = new Set(["127.0.0.1", "[::1]", "localhost"]);

export function requireIsolatedE2ERuntime(environment: Environment): IsolatedE2ERuntime {
  if (environment.E2E_RUNTIME_ISOLATED !== "true") {
    throw new Error("Browser acceptance requires E2E_RUNTIME_ISOLATED=true");
  }

  const composeProjectName = environment.E2E_COMPOSE_PROJECT_NAME ?? "";
  if (!isolatedProjectPattern.test(composeProjectName)) {
    throw new Error("Browser acceptance requires a unique throwaway Compose project");
  }

  let url: URL;
  try {
    url = new URL(environment.E2E_BASE_URL ?? "");
  } catch {
    throw new Error("Browser acceptance requires an explicit loopback HTTP origin");
  }
  if (url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("E2E_BASE_URL must contain an origin only");
  }
  if (url.protocol !== "http:" || !loopbackHosts.has(url.hostname) || !url.port) {
    throw new Error("Browser acceptance requires an explicit loopback HTTP origin");
  }
  if (url.port === "3000") {
    throw new Error("Browser acceptance must not target the normal Studio origin");
  }

  return { baseURL: url.origin, composeProjectName };
}
