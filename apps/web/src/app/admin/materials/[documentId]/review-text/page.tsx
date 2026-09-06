import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { AdminHeader } from "@/components/admin/admin-header";
import { SourcePageReviewWorkspace } from "@/components/admin/source-page-review-workspace";

export const dynamic = "force-dynamic";

export default async function MaterialTextReviewPage({
  params,
  searchParams,
}: {
  params: Promise<{ documentId: string }>;
  searchParams: Promise<{
    page_number?: string | string[];
    benchmark_id?: string | string[];
  }>;
}) {
  const cookieStore = await cookies();
  if (!cookieStore.get("exam_guru_admin_token")) {
    redirect("/admin/login");
  }
  const role =
    cookieStore.get("exam_guru_admin_role")?.value === "reviewer"
      ? "reviewer"
      : "admin";
  const { documentId } = await params;
  const query = await searchParams;
  const requestedPage =
    typeof query.page_number === "string" && /^\d+$/.test(query.page_number)
      ? Number(query.page_number)
      : 1;
  const initialPageNumber =
    Number.isSafeInteger(requestedPage) && requestedPage > 0
      ? requestedPage
      : 1;
  const benchmarkId =
    typeof query.benchmark_id === "string" ? query.benchmark_id : undefined;

  return (
    <main className="flex min-h-screen flex-col bg-[#f3f4ef] text-slate-950 lg:h-dvh lg:min-h-0 lg:overflow-hidden">
      <div className="shrink-0">
        <AdminHeader current="materials" role={role} />
      </div>
      <SourcePageReviewWorkspace
        benchmarkId={benchmarkId}
        documentId={documentId}
        initialPageNumber={initialPageNumber}
        role={role}
      />
    </main>
  );
}
