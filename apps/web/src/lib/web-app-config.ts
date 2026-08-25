const APP_ENVIRONMENTS = ["local", "test", "staging", "production"] as const;
const IDENTITY_PROVIDERS = ["deterministic", "oidc", "deny"] as const;
const DEFAULT_LOCAL_API_BASE_URL = "http://localhost:8000";
const DEFAULT_LOCAL_APP_BASE_URL = "http://localhost:3000";
const DEFAULT_HTTP_TIMEOUT_MS = 5_000;
const DEFAULT_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60;
const MAX_URL_LENGTH = 2_048;
const MAX_CLIENT_ID_LENGTH = 256;
const MAX_CLIENT_SECRET_LENGTH = 1_024;
const MAX_SCOPE_LENGTH = 64;
const MAX_SCOPES = 16;
const MAX_SCOPES_TEXT_LENGTH = 512;
const MIN_HTTP_TIMEOUT_MS = 100;
const MAX_HTTP_TIMEOUT_MS = 10_000;
const MIN_SESSION_MAX_AGE_SECONDS = 60;
const MAX_SESSION_MAX_AGE_SECONDS = 86_400;

export type AppEnvironment = (typeof APP_ENVIRONMENTS)[number];
export type IdentityProvider = (typeof IDENTITY_PROVIDERS)[number];
type EnvironmentSource = Readonly<Record<string, string | undefined>>;

export type WebAppConfig = Readonly<{
  adminCookieSecure: boolean;
  apiBaseUrl: URL;
  apiOrigin: string;
  appBaseUrl: URL;
  appOrigin: string;
  deterministicAdminToken: string | null;
  deterministicIdentityEnabled: boolean;
  deterministicReviewerToken: string | null;
  environment: AppEnvironment;
  httpTimeoutMs: number;
  identityProvider: IdentityProvider;
  oidcAuthorizationEndpoint: URL | null;
  oidcClientId: string | null;
  oidcClientSecret: string | null;
  oidcEndSessionEndpoint: URL | null;
  oidcIssuer: string | null;
  oidcScopes: readonly string[];
  oidcTokenEndpoint: URL | null;
  oidcTrustedTokenOrigin: string | null;
  sessionMaxAgeSeconds: number;
}>;

function parseEnvironment(value: string | undefined): AppEnvironment {
  const environment = value ?? "local";
  if (!APP_ENVIRONMENTS.includes(environment as AppEnvironment)) {
    throw new Error("APP_ENVIRONMENT must be one of local, test, staging, production");
  }
  return environment as AppEnvironment;
}

function parseIdentityProvider(value: string | undefined): IdentityProvider {
  const provider = value ?? "deny";
  if (!IDENTITY_PROVIDERS.includes(provider as IdentityProvider)) {
    throw new Error("WEB_IDENTITY_PROVIDER must be one of deterministic, oidc, deny");
  }
  return provider as IdentityProvider;
}

function parseBoolean(name: string, value: string | undefined, defaultValue: boolean): boolean {
  if (value === undefined) return defaultValue;
  if (value === "true") return true;
  if (value === "false") return false;
  throw new Error(`${name} must be either true or false`);
}

function parseBoundedInteger(
  name: string,
  value: string | undefined,
  defaultValue: number,
  minimum: number,
  maximum: number,
): number {
  if (value === undefined) return defaultValue;
  if (!/^\d+$/.test(value)) {
    throw new Error(`${name} must be an integer between ${minimum} and ${maximum}`);
  }
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${name} must be an integer between ${minimum} and ${maximum}`);
  }
  return parsed;
}

function parseHttpUrl(name: string, value: string, originOnly: boolean): URL {
  if (value !== value.trim()) {
    throw new Error(`${name} must not contain surrounding whitespace`);
  }
  if (!/^https?:\/\//i.test(value)) {
    throw new Error(`${name} must be an absolute http(s) URL`);
  }
  if (value.length > MAX_URL_LENGTH || !/^[\x21-\x7e]+$/.test(value)) {
    throw new Error(`${name} must be a bounded absolute HTTP(S) URL`);
  }
  if (originOnly && value.includes("\\")) {
    throw new Error(`${name} must not contain a path`);
  }
  if (value.includes("\\")) {
    throw new Error(`${name} must be a bounded absolute HTTP(S) URL`);
  }

  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${name} must be an absolute http(s) URL`);
  }

  if (parsed.username || parsed.password) {
    throw new Error(`${name} must not contain userinfo`);
  }
  if (value.includes("?") || value.includes("#") || parsed.search || parsed.hash) {
    throw new Error(`${name} must not contain a query or fragment`);
  }

  if (originOnly) {
    const authorityAndPath = value.slice(value.indexOf("://") + 3);
    const rawPathStart = authorityAndPath.search(/[\\/]/);
    const rawPath = rawPathStart === -1 ? "" : authorityAndPath.slice(rawPathStart);
    if (parsed.pathname !== "/" || (rawPath !== "" && rawPath !== "/")) {
      throw new Error(`${name} must not contain a path`);
    }
  }
  return parsed;
}

function parseBoundedToken(
  name: string,
  value: string | undefined,
  maximumLength: number,
  pattern: RegExp,
): string | null {
  if (value === undefined) return null;
  if (value.length === 0 || value.length > maximumLength || !pattern.test(value)) {
    throw new Error(`${name} must be a bounded printable token`);
  }
  return value;
}

function parseScopes(value: string | undefined): readonly string[] {
  const raw = value ?? "openid";
  if (raw.length === 0 || raw.length > MAX_SCOPES_TEXT_LENGTH || raw !== raw.trim()) {
    throw new Error("OIDC_SCOPES must be a bounded space-separated scope list containing openid");
  }
  const scopes = raw.split(" ");
  if (
    scopes.length > MAX_SCOPES ||
    scopes.some(
      (scope) =>
        scope.length === 0 ||
        scope.length > MAX_SCOPE_LENGTH ||
        !/^[\x21\x23-\x5b\x5d-\x7e]+$/.test(scope),
    ) ||
    new Set(scopes).size !== scopes.length ||
    !scopes.includes("openid")
  ) {
    throw new Error("OIDC_SCOPES must be a bounded unique scope list containing openid");
  }
  return Object.freeze(scopes);
}

function defineSecret(config: Omit<WebAppConfig, "oidcClientSecret">, secret: string | null): WebAppConfig {
  const withSecret = config as WebAppConfig;
  Object.defineProperty(withSecret, "oidcClientSecret", {
    configurable: false,
    enumerable: false,
    value: secret,
    writable: false,
  });
  return Object.freeze(withSecret);
}

export function parseWebAppConfig(source: EnvironmentSource = process.env): WebAppConfig {
  const environment = parseEnvironment(source.APP_ENVIRONMENT);
  if (environment === "production" && source.APP_BASE_URL === undefined) {
    throw new Error("APP_BASE_URL is required in production");
  }

  const appBaseUrl = parseHttpUrl(
    "APP_BASE_URL",
    source.APP_BASE_URL ?? DEFAULT_LOCAL_APP_BASE_URL,
    true,
  );
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

  const apiBaseUrl = parseHttpUrl(
    "API_BASE_URL",
    source.API_BASE_URL ?? DEFAULT_LOCAL_API_BASE_URL,
    true,
  );
  const identityProvider = parseIdentityProvider(source.WEB_IDENTITY_PROVIDER);
  const deterministicIdentityEnabled = parseBoolean(
    "ENABLE_DETERMINISTIC_IDENTITY",
    source.ENABLE_DETERMINISTIC_IDENTITY,
    false,
  );
  const deterministicAdminToken = parseBoundedToken(
    "DETERMINISTIC_ADMIN_TOKEN",
    source.DETERMINISTIC_ADMIN_TOKEN,
    8_192,
    /^[\x21-\x7e]+$/,
  );
  const deterministicReviewerToken = parseBoundedToken(
    "DETERMINISTIC_REVIEWER_TOKEN",
    source.DETERMINISTIC_REVIEWER_TOKEN,
    8_192,
    /^[\x21-\x7e]+$/,
  );

  if (
    (environment === "staging" || environment === "production") &&
    (identityProvider === "deterministic" ||
      deterministicIdentityEnabled ||
      deterministicAdminToken !== null ||
      deterministicReviewerToken !== null)
  ) {
    throw new Error("deterministic identity is restricted to local and test");
  }
  if (
    identityProvider !== "deterministic" &&
    (deterministicIdentityEnabled ||
      deterministicAdminToken !== null ||
      deterministicReviewerToken !== null)
  ) {
    throw new Error("deterministic identity settings require WEB_IDENTITY_PROVIDER=deterministic");
  }

  const httpTimeoutMs = parseBoundedInteger(
    "OIDC_HTTP_TIMEOUT_MS",
    source.OIDC_HTTP_TIMEOUT_MS,
    DEFAULT_HTTP_TIMEOUT_MS,
    MIN_HTTP_TIMEOUT_MS,
    MAX_HTTP_TIMEOUT_MS,
  );
  const sessionMaxAgeSeconds = parseBoundedInteger(
    "ADMIN_SESSION_MAX_AGE_SECONDS",
    source.ADMIN_SESSION_MAX_AGE_SECONDS,
    DEFAULT_SESSION_MAX_AGE_SECONDS,
    MIN_SESSION_MAX_AGE_SECONDS,
    MAX_SESSION_MAX_AGE_SECONDS,
  );

  const requiredOidcValues = [
    source.OIDC_ISSUER,
    source.OIDC_AUTHORIZATION_ENDPOINT,
    source.OIDC_TOKEN_ENDPOINT,
    source.OIDC_CLIENT_ID,
    source.OIDC_CLIENT_SECRET,
  ];
  const hasAnyOidcConfiguration =
    requiredOidcValues.some((value) => value !== undefined) ||
    source.OIDC_END_SESSION_ENDPOINT !== undefined ||
    source.OIDC_TRUSTED_TOKEN_ORIGIN !== undefined ||
    source.OIDC_SCOPES !== undefined;
  const hasCompleteOidcConfiguration = requiredOidcValues.every((value) => value !== undefined);
  if ((identityProvider === "oidc" || hasAnyOidcConfiguration) && !hasCompleteOidcConfiguration) {
    throw new Error("identity provider requires complete explicit OIDC configuration");
  }

  let oidcAuthorizationEndpoint: URL | null = null;
  let oidcTokenEndpoint: URL | null = null;
  let oidcEndSessionEndpoint: URL | null = null;
  let oidcIssuer: string | null = null;
  let oidcClientId: string | null = null;
  let oidcClientSecret: string | null = null;
  let oidcTrustedTokenOrigin: string | null = null;
  let oidcScopes: readonly string[] = Object.freeze(["openid"]);

  if (hasCompleteOidcConfiguration) {
    oidcIssuer = source.OIDC_ISSUER as string;
    const oidcIssuerUrl = parseHttpUrl("OIDC issuer", oidcIssuer, false);
    oidcAuthorizationEndpoint = parseHttpUrl(
      "OIDC authorization endpoint",
      source.OIDC_AUTHORIZATION_ENDPOINT as string,
      false,
    );
    oidcTokenEndpoint = parseHttpUrl(
      "OIDC token endpoint",
      source.OIDC_TOKEN_ENDPOINT as string,
      false,
    );
    oidcClientId = parseBoundedToken(
      "OIDC client ID",
      source.OIDC_CLIENT_ID,
      MAX_CLIENT_ID_LENGTH,
      /^[\x21-\x39\x3b-\x7e]+$/,
    );
    oidcClientSecret = parseBoundedToken(
      "OIDC client secret",
      source.OIDC_CLIENT_SECRET,
      MAX_CLIENT_SECRET_LENGTH,
      /^[\x21-\x7e]+$/,
    );
    oidcScopes = parseScopes(source.OIDC_SCOPES);

    if (source.OIDC_END_SESSION_ENDPOINT !== undefined) {
      oidcEndSessionEndpoint = parseHttpUrl(
        "OIDC end-session endpoint",
        source.OIDC_END_SESSION_ENDPOINT,
        false,
      );
    }
    if (source.OIDC_TRUSTED_TOKEN_ORIGIN !== undefined) {
      const trustedTokenUrl = parseHttpUrl(
        "OIDC trusted token origin",
        source.OIDC_TRUSTED_TOKEN_ORIGIN,
        true,
      );
      oidcTrustedTokenOrigin = trustedTokenUrl.origin;
    }

    if (
      environment === "production" &&
      (oidcIssuerUrl.protocol !== "https:" ||
        oidcAuthorizationEndpoint.protocol !== "https:" ||
        oidcTokenEndpoint.protocol !== "https:" ||
        oidcEndSessionEndpoint?.protocol === "http:")
    ) {
      throw new Error("production OIDC URLs must use HTTPS");
    }
    if (oidcAuthorizationEndpoint.origin !== oidcIssuerUrl.origin) {
      throw new Error("OIDC authorization endpoint origin must match the issuer origin");
    }
    if (oidcTokenEndpoint.origin !== oidcAuthorizationEndpoint.origin) {
      if (oidcTrustedTokenOrigin === null) {
        throw new Error(
          "OIDC token endpoint origin must match the authorization endpoint origin",
        );
      }
      if (oidcTrustedTokenOrigin !== oidcTokenEndpoint.origin) {
        throw new Error("OIDC trusted token origin must exactly match the token endpoint origin");
      }
    } else if (
      oidcTrustedTokenOrigin !== null &&
      oidcTrustedTokenOrigin !== oidcTokenEndpoint.origin
    ) {
      throw new Error("OIDC trusted token origin must exactly match the token endpoint origin");
    }
  }

  if (environment === "production" && identityProvider !== "oidc") {
    throw new Error("production WEB_IDENTITY_PROVIDER must be oidc");
  }

  return defineSecret(
    {
      adminCookieSecure,
      apiBaseUrl,
      apiOrigin: apiBaseUrl.origin,
      appBaseUrl,
      appOrigin: appBaseUrl.origin,
      deterministicAdminToken,
      deterministicIdentityEnabled:
        identityProvider === "deterministic" && deterministicIdentityEnabled,
      deterministicReviewerToken,
      environment,
      httpTimeoutMs,
      identityProvider,
      oidcAuthorizationEndpoint,
      oidcClientId,
      oidcEndSessionEndpoint,
      oidcIssuer,
      oidcScopes,
      oidcTokenEndpoint,
      oidcTrustedTokenOrigin,
      sessionMaxAgeSeconds,
    },
    oidcClientSecret,
  );
}
