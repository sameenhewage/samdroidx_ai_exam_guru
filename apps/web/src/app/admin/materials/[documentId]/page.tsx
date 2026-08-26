import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { AdminHeader } from "@/components/admin/admin-header";
import { MaterialDetails } from "@/components/admin/material-details";

export const dynamic = "force-dynamic";

export default async function MaterialDetailsPage({
  params,
}: {
  params: Promise<{ documentId: string }>;
}) {
  const cookieStore = await cookies();
  if (!cookieStore.get("exam_guru_admin_token")) {
    redirect("/admin/login");
  }
  const role =
    cookieStore.get("exam_guru_admin_role")?.value === "reviewer" ? "reviewer" : "admin";
  const { documentId } = await params;

  return (
    <main className="min-h-screen bg-[#f3f4ef] text-slate-950">
      <AdminHeader current="materials" role={role} />
      <MaterialDetails documentId={documentId} role={role} />
    </main>
  );
}
