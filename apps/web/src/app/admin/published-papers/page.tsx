import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { AdminHeader } from "@/components/admin/admin-header";
import { PublishedPapersLibrary } from "@/components/admin/published-papers-library";

export const dynamic = "force-dynamic";

export default async function PublishedPapersAdminPage() {
  const cookieStore = await cookies();
  if (!cookieStore.get("exam_guru_admin_token")) {
    redirect("/admin/login");
  }
  const role =
    cookieStore.get("exam_guru_admin_role")?.value === "reviewer" ? "reviewer" : "admin";

  return (
    <main className="min-h-screen bg-[#f3f4ef] text-slate-950">
      <AdminHeader current="published-papers" role={role} />
      <PublishedPapersLibrary role={role} />
    </main>
  );
}
