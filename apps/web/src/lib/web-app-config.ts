const APP_ENVIRONMENTS = ["local", "test", "staging", "production"] as const;
const DEFAULT_LOCAL_APP_BASE_URL = "http://localhost:3000";

type AppEnvironment = (typeof APP_ENVIRONMENTS)[number];
type EnvironmentSource = Readonly<Record<string, string | undefined>>;

export type WebAppConfig = Readonly<{
  adminCookieSecure: boolean;
  appBaseUrl: URL;
  appOrigin: string;
  environment: AppEnvironment;
}>;

function parseEnvironment(value: string | undefined): AppEnvironment {
  const environment = value ?? "local";
  if (!APP_ENVIRONMENTS.includes(environment as AppEnvironment)) {
    throw new Error("APP_ENVIRONMENT must be one of local, test, staging, production");
  }
  return environment as AppEnvironment;
}

function parseBoolean(name: string, value: string | undefined, defaultValue: boolean): boolean {
  if (value === undefined) return defaultValue;
  if (value === "true") return true;
  if (value === "false") return false;
  throw new Error(`${name} must be either true or false`);
}

function parseAppBaseUrl(value: string): URL {
  if (value !== value.trim()) {
    throw new Error("APP_BASE_URL must not contain surrounding whitespace");
  }
  if (!/^https?:\/\//i.test(value)) {
    throw new Error("APP_BASE_URL must be an absolute http(s) URL");
  }

  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("APP_BASE_URL must be an absolute http(s) URL");
  }

  if (parsed.username || parsed.password) {
    throw new Error("APP_BASE_URL must not contain userinfo");
  }
  if (value.includes("?") || value.includes("#")) {
    throw new Error("APP_BASE_URL must not contain a query or fragment");
  }

  const authorityAndPath = value.slice(value.indexOf("://") + 3);
  const rawPathStart = authorityAndPath.search(/[\\/]/);
  const rawPath = rawPathStart === -1 ? "" : authorityAndPath.slice(rawPathStart);
  if (parsed.pathname !== "/" || (rawPath !== "" && rawPath !== "/")) {
    throw new Error("APP_BASE_URL must not contain a path");
  }
  return parsed;
}

export function parseWebAppConfig(source: EnvironmentSource = process.env): WebAppConfig {
  const environment = parseEnvironment(source.APP_ENVIRONMENT);
  if (environment === "production" && source.APP_BASE_URL === undefined) {
    throw new Error("APP_BASE_URL is required in production");
  }

  const appBaseUrl = parseAppBaseUrl(source.APP_BASE_URL ?? DEFAULT_LOCAL_APP_BASE_URL);
  const adminCookieSecure = parseBoolean(
    "ADMIN_COOKIE_SECURE",
    source.ADMIN_COOKIE_SECURE,
    false,
  );

  if (environment === "production" && appBaseUrl.protocol !== "https:") {
    throw new Error("production APP_BASE_URL must use HTTPS");
  }
  if (environment === "production" && !adminCookieSecure) {
    throw new Error("production ADMIN_COOKIE_SECURE must be true");
  }

  return Object.freeze({
    adminCookieSecure,
    appBaseUrl,
    appOrigin: appBaseUrl.origin,
    environment,
  });
}
