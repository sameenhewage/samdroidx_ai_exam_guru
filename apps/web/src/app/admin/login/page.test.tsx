import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import AdminLoginPage from "./page";

function configureBase(provider: "deterministic" | "oidc" | "deny") {
  vi.stubEnv("ADMIN_COOKIE_SECURE", "false");
  vi.stubEnv("API_BASE_URL", "http://localhost:8000");
  vi.stubEnv("APP_BASE_URL", "http://localhost:3000");
  vi.stubEnv("APP_ENVIRONMENT", "test");
  vi.stubEnv("WEB_IDENTITY_PROVIDER", provider);
}

function configureOidc() {
  configureBase("oidc");
  vi.stubEnv("OIDC_AUTHORIZATION_ENDPOINT", "https://identity.example/oauth2/authorize");
  vi.stubEnv("OIDC_TOKEN_ENDPOINT", "https://identity.example/oauth2/token");
  vi.stubEnv("OIDC_CLIENT_ID", "exam-guru-web");
  vi.stubEnv("OIDC_CLIENT_SECRET", "secret-never-rendered");
  vi.stubEnv("OIDC_ISSUER", "https://identity.example/realms/exam-guru");
  vi.stubEnv("OIDC_SCOPES", "openid profile");
}

async function renderPage(error?: string) {
  const page = await AdminLoginPage({ searchParams: Promise.resolve(error ? { error } : {}) });
  return render(page);
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("AdminLoginPage", () => {
  it("shows only same-origin organization login in OIDC mode", async () => {
    configureOidc();

    const { container } = await renderPage();

    const button = screen.getByRole("button", { name: "Continue with organization login" });
    expect(button.closest("form")).toHaveAttribute("action", "/api/auth/oidc/login");
    expect(button.closest("form")).toHaveAttribute("method", "post");
    expect(screen.queryByRole("button", { name: /Continue as/ })).not.toBeInTheDocument();
    expect(container.textContent).not.toContain("secret-never-rendered");
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });

  it("shows only explicitly enabled and configured local deterministic identities", async () => {
    configureBase("deterministic");
    vi.stubEnv("ENABLE_DETERMINISTIC_IDENTITY", "true");
    vi.stubEnv("DETERMINISTIC_ADMIN_TOKEN", "local-admin-token");

    await renderPage();

    expect(screen.getByRole("button", { name: "Continue as admin" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue as reviewer" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Continue with organization login" }),
    ).not.toBeInTheDocument();
  });

  it("fails closed with a fixed unavailable state in deny mode", async () => {
    configureBase("deny");

    await renderPage();

    expect(screen.getByText("Admin sign-in is unavailable.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("fails closed when deterministic mode has no configured identity token", async () => {
    configureBase("deterministic");
    vi.stubEnv("ENABLE_DETERMINISTIC_IDENTITY", "true");

    await renderPage();

    expect(screen.getByText("Admin sign-in is unavailable.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders only the fixed safe OIDC failure message", async () => {
    configureOidc();

    const { rerender } = await renderPage("oidc_login_failed");
    expect(
      screen.getByText("Organization sign-in failed. Please try again."),
    ).toBeInTheDocument();

    const malicious = "<img src=x onerror=alert(1)>provider-secret";
    const page = await AdminLoginPage({ searchParams: Promise.resolve({ error: malicious }) });
    rerender(page);
    expect(screen.queryByText(malicious)).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("provider-secret");
    expect(
      screen.queryByText("Organization sign-in failed. Please try again."),
    ).not.toBeInTheDocument();
  });
});
