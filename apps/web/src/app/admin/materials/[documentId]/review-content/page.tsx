import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { AdminHeader } from "@/components/admin/admin-header";
import { SourceUnderstandingReview } from "@/components/admin/source-understanding-review";

export const dynamic = "force-dynamic";

export default async function MaterialContentReviewPage({
  params,
  searchParams,
}: {
  params: Promise<{ documentId: string }>;
  searchParams: Promise<{ page_number?: string | string[] }>;
}) {
  const cookieStore = await cookies();
  if (!cookieStore.get("exam_guru_admin_token")) redirect("/admin/login");
  const role =
    cookieStore.get("exam_guru_admin_role")?.value === "reviewer"
      ? "reviewer"
      : "admin";
  const { documentId } = await params;
  const query = await searchParams;
  const requested =
    typeof query.page_number === "string" && /^\d+$/.test(query.page_number)
      ? Number(query.page_number)
      : 1;
  const initialPageNumber =
    Number.isSafeInteger(requested) && requested > 0 ? requested : 1;
  return (
    <main className="flex min-h-screen flex-col bg-[#f3f4ef] text-slate-950 lg:h-dvh lg:min-h-0 lg:overflow-hidden">
      <div className="shrink-0">
        <AdminHeader current="materials" role={role} />
      </div>
      <SourceUnderstandingReview
        documentId={documentId}
        role={role}
        initialPageNumber={initialPageNumber}
      />
    </main>
  );
}
