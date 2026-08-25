import { parseWebAppConfig } from "@/lib/web-app-config";

export const dynamic = "force-dynamic";

type LoginPageProps = {
  searchParams?: Promise<{ error?: string | string[] }>;
};

export default async function AdminLoginPage({ searchParams }: LoginPageProps = {}) {
  const config = parseWebAppConfig();
  const parameters = searchParams ? await searchParams : {};
  const oidcFailed = parameters.error === "oidc_login_failed";
  const deterministicEnabled =
    config.identityProvider === "deterministic" &&
    config.deterministicIdentityEnabled &&
    Boolean(config.deterministicAdminToken || config.deterministicReviewerToken);

  return (
    <main className="grid min-h-screen place-items-center bg-slate-950 px-5 py-12 text-white">
      <section
        aria-labelledby="admin-login-title"
        className="w-full max-w-lg border border-white/15 bg-white/5 p-8 shadow-2xl"
      >
        <p className="font-mono text-xs tracking-[0.2em] text-amber-300 uppercase">AI Exam Guru</p>
        <h1 className="mt-4 text-3xl font-semibold tracking-tight" id="admin-login-title">
          Admin Content Studio
        </h1>
        <p className="mt-3 leading-7 text-slate-300">
          Sign in with an authorized administrator or reviewer identity to continue.
        </p>

        {oidcFailed ? (
          <p
            className="mt-6 border border-red-300/40 bg-red-300/10 p-4 text-red-100"
            role="alert"
          >
            Organization sign-in failed. Please try again.
          </p>
        ) : null}

        {config.identityProvider === "oidc" ? (
          <form action="/api/auth/oidc/login" className="mt-8" method="post">
            <button
              className="w-full bg-amber-300 px-4 py-3 font-semibold text-slate-950"
              type="submit"
            >
              Continue with organization login
            </button>
          </form>
        ) : deterministicEnabled ? (
          <div className="mt-8 grid gap-3 sm:grid-cols-2">
            {config.deterministicAdminToken ? (
              <form action="/api/auth/development-login" method="post">
                <input name="role" type="hidden" value="admin" />
                <button
                  className="w-full bg-amber-300 px-4 py-3 font-semibold text-slate-950"
                  type="submit"
                >
                  Continue as admin
                </button>
              </form>
            ) : null}
            {config.deterministicReviewerToken ? (
              <form action="/api/auth/development-login" method="post">
                <input name="role" type="hidden" value="reviewer" />
                <button
                  className="w-full border border-white/25 px-4 py-3 font-semibold"
                  type="submit"
                >
                  Continue as reviewer
                </button>
              </form>
            ) : null}
          </div>
        ) : (
          <p className="mt-8 border border-amber-300/40 bg-amber-300/10 p-4 text-amber-100">
            Admin sign-in is unavailable.
          </p>
        )}
      </section>
    </main>
  );
}
