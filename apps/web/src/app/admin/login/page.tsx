export const dynamic = "force-dynamic";

export default function AdminLoginPage() {
  const enabled =
    process.env.ENABLE_DETERMINISTIC_IDENTITY === "true" &&
    Boolean(process.env.DETERMINISTIC_ADMIN_TOKEN || process.env.DETERMINISTIC_REVIEWER_TOKEN);

  return (
    <main className="grid min-h-screen place-items-center bg-slate-950 px-5 py-12 text-white">
      <section className="w-full max-w-lg border border-white/15 bg-white/5 p-8 shadow-2xl">
        <p className="font-mono text-xs tracking-[0.2em] text-amber-300 uppercase">AI Exam Guru</p>
        <h1 className="mt-4 text-3xl font-semibold tracking-tight">Admin Content Studio</h1>
        <p className="mt-3 leading-7 text-slate-300">
          Select a deterministic development identity to exercise the same authorization port used
          by the production integration at P10.
        </p>
        {enabled ? (
          <div className="mt-8 grid gap-3 sm:grid-cols-2">
            <form action="/api/auth/development-login" method="post">
              <input name="role" type="hidden" value="admin" />
              <button className="w-full bg-amber-300 px-4 py-3 font-semibold text-slate-950" type="submit">
                Continue as admin
              </button>
            </form>
            <form action="/api/auth/development-login" method="post">
              <input name="role" type="hidden" value="reviewer" />
              <button className="w-full border border-white/25 px-4 py-3 font-semibold" type="submit">
                Continue as reviewer
              </button>
            </form>
          </div>
        ) : (
          <p className="mt-8 border border-amber-300/40 bg-amber-300/10 p-4 text-amber-100">
            Deterministic identity is not configured. Production identity integration is deferred to
            P10.
          </p>
        )}
      </section>
    </main>
  );
}
