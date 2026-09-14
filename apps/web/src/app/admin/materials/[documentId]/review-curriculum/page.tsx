import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { AdminHeader } from "@/components/admin/admin-header";
import { MaterialCurriculumReview } from "@/components/admin/material-curriculum-review";

export const dynamic = "force-dynamic";

export default async function MaterialCurriculumReviewPage({
  params,
}: {
  params: Promise<{ documentId: string }>;
}) {
  const cookieStore = await cookies();
  if (!cookieStore.get("exam_guru_admin_token")) redirect("/admin/login");
  const role =
    cookieStore.get("exam_guru_admin_role")?.value === "reviewer"
      ? "reviewer"
      : "admin";
  const { documentId } = await params;
  return (
    <main className="flex min-h-screen flex-col bg-[#f3f4ef] text-slate-950 lg:h-dvh lg:min-h-0 lg:overflow-hidden">
      <div className="shrink-0">
        <AdminHeader current="materials" role={role} />
      </div>
      <MaterialCurriculumReview documentId={documentId} role={role} />
    </main>
  );
}
