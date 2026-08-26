import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { AdminHeader } from "@/components/admin/admin-header";
import { GeneratePapersWizard } from "@/components/admin/generate-papers-wizard";

export const dynamic = "force-dynamic";

export default async function GeneratePapersAdminPage() {
  const cookieStore = await cookies();
  if (!cookieStore.get("exam_guru_admin_token")) {
    redirect("/admin/login");
  }
  const role =
    cookieStore.get("exam_guru_admin_role")?.value === "reviewer" ? "reviewer" : "admin";

  return (
    <main className="min-h-screen bg-[#f3f4ef] text-slate-950">
      <AdminHeader current="generate-papers" role={role} />
      <GeneratePapersWizard role={role} />
    </main>
  );
}
