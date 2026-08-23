import Link from "next/link";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { DocumentsStudio } from "@/components/admin/documents-studio";

export const dynamic = "force-dynamic";

export default async function DocumentsAdminPage() {
  const cookieStore = await cookies();
  if (!cookieStore.get("exam_guru_admin_token")) {
    redirect("/admin/login");
  }
  const role = cookieStore.get("exam_guru_admin_role")?.value === "reviewer" ? "reviewer" : "admin";

  return (
    <main className="min-h-screen bg-[#f3f4ef] text-slate-950">
      <header className="border-b border-slate-300 bg-slate-950 px-5 py-5 text-white sm:px-8">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-5">
          <div>
            <p className="font-mono text-xs tracking-[0.2em] text-amber-300 uppercase">AI Exam Guru</p>
            <p className="mt-1 font-semibold">Admin Content Studio</p>
          </div>

          <nav aria-label="Admin content areas" className="order-3 flex w-full gap-1 border-t border-white/10 pt-4 sm:order-none sm:w-auto sm:border-0 sm:pt-0">
            <Link
              className="rounded-md px-3 py-2 text-sm text-slate-300 outline-none hover:bg-white/10 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-300"
              href="/admin/curriculum"
            >
              Curriculum
            </Link>
            <Link
              aria-current="page"
              className="rounded-md bg-white/10 px-3 py-2 text-sm font-semibold text-white outline-none focus-visible:ring-2 focus-visible:ring-amber-300"
              href="/admin/documents"
            >
              Documents
            </Link>
          </nav>

          <div className="flex items-center gap-4">
            <span className="rounded-full border border-white/20 px-3 py-1 text-xs capitalize">{role}</span>
            <form action="/api/auth/logout" method="post">
              <button className="text-sm text-slate-300 underline outline-none hover:text-white focus-visible:ring-2 focus-visible:ring-amber-300" type="submit">
                Sign out
              </button>
            </form>
          </div>
        </div>
      </header>
      <DocumentsStudio role={role} />
    </main>
  );
}
