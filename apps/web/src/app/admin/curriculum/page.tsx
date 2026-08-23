import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { CurriculumStudio } from "@/components/admin/curriculum-studio";

export const dynamic = "force-dynamic";

export default async function CurriculumAdminPage() {
  const cookieStore = await cookies();
  if (!cookieStore.get("exam_guru_admin_token")) {
    redirect("/admin/login");
  }
  const role = cookieStore.get("exam_guru_admin_role")?.value === "reviewer" ? "reviewer" : "admin";

  return (
    <main className="min-h-screen bg-[#f3f4ef] text-slate-950">
      <header className="border-b border-slate-300 bg-slate-950 px-5 py-5 text-white sm:px-8">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4">
          <div>
            <p className="font-mono text-xs tracking-[0.2em] text-amber-300 uppercase">AI Exam Guru</p>
            <p className="mt-1 font-semibold">Admin Content Studio</p>
          </div>
          <div className="flex items-center gap-4">
            <span className="rounded-full border border-white/20 px-3 py-1 text-xs capitalize">{role}</span>
            <form action="/api/auth/logout" method="post">
              <button className="text-sm text-slate-300 underline" type="submit">
                Sign out
              </button>
            </form>
          </div>
        </div>
      </header>
      <CurriculumStudio role={role} />
    </main>
  );
}
