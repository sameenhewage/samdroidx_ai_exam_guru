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
    <main className="min-h-screen bg-[#f3f4ef] text-slate-950">
      <AdminHeader current="materials" role={role} />
      <SourceBenchmarkReview
        initialBenchmarkId={initialBenchmarkId}
        role={role}
      />
    </main>
  );
}
