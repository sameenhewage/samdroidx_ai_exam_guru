import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { AdminHeader } from "@/components/admin/admin-header";
import { OperationsDashboard } from "@/components/admin/operations-dashboard";

export const dynamic = "force-dynamic";

export default async function OperationsAdminPage() {
  const cookieStore = await cookies();
  if (!cookieStore.get("exam_guru_admin_token")) {
    redirect("/admin/login");
  }
  const role = cookieStore.get("exam_guru_admin_role")?.value === "admin" ? "admin" : "reviewer";

  return (
    <main className="min-h-screen bg-[#f3f4ef] text-slate-950">
      <AdminHeader current="operations" role={role} />
      <OperationsDashboard />
    </main>
  );
}
