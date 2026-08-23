import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { ExtractionReviewStudio } from "@/components/admin/extraction-review-studio";

export const dynamic = "force-dynamic";

export default async function ExtractionReviewPage({
  params,
}: {
  params: Promise<{ documentId: string }>;
}) {
  const cookieStore = await cookies();
  if (!cookieStore.get("exam_guru_admin_token")) redirect("/admin/login");
  const role = cookieStore.get("exam_guru_admin_role")?.value === "reviewer" ? "reviewer" : "admin";
  const { documentId } = await params;

  return (
    <main className="min-h-screen bg-[#f3f4ef] text-slate-950">
      <ExtractionReviewStudio documentId={documentId} role={role} />
    </main>
  );
}
