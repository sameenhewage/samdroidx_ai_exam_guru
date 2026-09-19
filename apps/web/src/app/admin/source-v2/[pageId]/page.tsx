import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { AdminHeader } from "@/components/admin/admin-header";
import { SourceV2Review } from "@/components/admin/source-v2-review";

export const dynamic = "force-dynamic";

export default async function SourceV2ReviewPage({
  params,
}: {
  params: Promise<{ pageId: string }>;
}) {
  const cookieStore = await cookies();
  if (!cookieStore.get("exam_guru_admin_token")) {
    redirect("/admin/login");
  }
  const role =
    cookieStore.get("exam_guru_admin_role")?.value === "reviewer"
      ? "reviewer"
      : "admin";
  const { pageId } = await params;

  return (
    <main className="flex h-dvh min-h-0 flex-col overflow-hidden bg-[#f3f4ef] text-slate-950">
      <div className="shrink-0">
        <AdminHeader current="materials" role={role} />
      </div>
      <SourceV2Review pageId={pageId} />
    </main>
  );
}
