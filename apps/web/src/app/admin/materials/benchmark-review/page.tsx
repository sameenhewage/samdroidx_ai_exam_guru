import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { AdminHeader } from "@/components/admin/admin-header";
import { SourceBenchmarkReview } from "@/components/admin/source-benchmark-review";

export const dynamic = "force-dynamic";

export default async function SourceBenchmarkReviewPage({
  searchParams,
}: {
  searchParams: Promise<{ benchmark_id?: string | string[] }>;
}) {
  const cookieStore = await cookies();
  if (!cookieStore.get("exam_guru_admin_token")) {
    redirect("/admin/login");
  }
  const role =
    cookieStore.get("exam_guru_admin_role")?.value === "reviewer"
      ? "reviewer"
      : "admin";
  const query = await searchParams;
  const initialBenchmarkId =
    typeof query.benchmark_id === "string" ? query.benchmark_id : undefined;

  return (
    <main className="min-h-screen bg-[#f3f4ef] text-slate-950 lg:[&:has([data-source-review])]:flex lg:[&:has([data-source-review])]:h-dvh lg:[&:has([data-source-review])]:min-h-0 lg:[&:has([data-source-review])]:flex-col lg:[&:has([data-source-review])]:overflow-hidden">
      <div className="shrink-0">
        <AdminHeader current="materials" role={role} />
      </div>
      <SourceBenchmarkReview
        initialBenchmarkId={initialBenchmarkId}
        role={role}
      />
    </main>
  );
}
