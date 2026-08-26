"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { AdminRole } from "./admin-header";

type Material = components["schemas"]["MaterialListItemResponse"];
type MaterialStatus = components["schemas"]["MaterialStatus"];
type MaterialType = components["schemas"]["SourceDocumentType"];
type SourceDocument = components["schemas"]["SourceDocumentResponse"];

const materialTypeLabels: Record<MaterialType, string> = {
  evaluation_report: "Evaluation / Examiner Report",
  marking_scheme: "Marking Scheme",
  other_approved: "Other approved material",
  past_paper: "Past Paper",
  syllabus: "Syllabus",
  teacher_guide: "Teacher Guide",
};

const statusLabels: Record<MaterialStatus, string> = {
  needs_review: "Needs review",
  processing: "Processing",
  ready_for_ai: "Ready for AI",
  removed: "Removed",
};

const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none hover:border-slate-500 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-600 focus-visible:ring-offset-2";

function errorCode(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail) && "code" in detail) {
      return String((detail as { code: unknown }).code);
    }
  }
  return "request_failed";
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Date unavailable";
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
    year: "numeric",
  }).format(date);
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <dt className="text-xs font-semibold tracking-wide text-slate-500 uppercase">{label}</dt>
      <dd className="mt-2 break-words text-sm text-slate-950">{value}</dd>
    </div>
  );
}

export function MaterialDetails({
  documentId,
  role,
}: {
  documentId: string;
  role: AdminRole;
}) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [material, setMaterial] = useState<Material | null>(null);
  const [source, setSource] = useState<SourceDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [permissionDenied, setPermissionDenied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    setPermissionDenied(false);
    try {
      const [materialResult, sourceResult] = await Promise.all([
        api.GET("/api/v1/admin/materials", {
          params: { query: { limit: 100, offset: 0 } },
        }),
        api.GET("/api/v1/admin/source-documents"),
      ]);
      if (materialResult.error || sourceResult.error) {
        const failed = materialResult.error ? materialResult : sourceResult;
        setPermissionDenied(failed.response.status === 403);
        setError(errorCode(failed.error));
        return;
      }
      const nextMaterial =
        materialResult.data?.find((candidate) => candidate.id === documentId) ?? null;
      const nextSource = sourceResult.data?.find((candidate) => candidate.id === documentId) ?? null;
      if (!nextMaterial || !nextSource) {
        setError("source_document_not_found");
        return;
      }
      setMaterial(nextMaterial);
      setSource(nextSource);
    } catch {
      setError("network_error");
    } finally {
      setLoading(false);
    }
  }, [api, documentId]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  if (loading) {
    return (
      <p className="mx-auto max-w-5xl p-8 text-slate-600" role="status">
        Loading material…
      </p>
    );
  }

  if (error || !material || !source) {
    return (
      <section className="mx-auto max-w-5xl px-5 py-10 sm:px-8" role="alert">
        <div className="rounded-xl border border-red-300 bg-red-50 p-5 text-red-950">
          <h1 className="text-2xl font-semibold">
            {permissionDenied ? "Materials access required" : "Material could not be opened"}
          </h1>
          <p className="mt-2 text-sm leading-6">
            {permissionDenied
              ? "Your account does not have permission to view this material."
              : error === "source_document_not_found"
                ? "This material was not found or is outside the bounded Materials catalog."
                : "The connection or Materials service failed. Return to Materials and try again."}
          </p>
          <Link className={`${secondaryButton} mt-4`} href="/admin/materials">
            Back to Materials
          </Link>
        </div>
      </section>
    );
  }

  const canReviewText = ["extracted", "in_review", "trusted"].includes(
    source.extraction_status,
  );

  return (
    <article className="mx-auto max-w-5xl px-5 py-8 sm:px-8 lg:py-12">
      <Link className="text-sm font-semibold underline decoration-amber-500 underline-offset-4" href="/admin/materials">
        Back to Materials
      </Link>
      <header className="mt-6 border-b border-slate-300 pb-7">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-xs font-semibold tracking-wider text-amber-800 uppercase">
              {materialTypeLabels[material.material_type]}
            </p>
            <h1 className="mt-2 break-words text-3xl font-semibold sm:text-4xl">
              {material.title}
            </h1>
          </div>
          <span className="rounded-full border border-slate-300 bg-white px-3 py-1 text-sm font-semibold">
            {statusLabels[material.status]}
          </span>
        </div>
        <p className="mt-4 max-w-2xl text-slate-600">
          {material.status === "removed"
            ? "This source is preserved for history but excluded from future AI use."
            : material.status === "ready_for_ai"
              ? "This reviewed source is available for scoped paper generation."
              : material.status === "needs_review"
                ? "Check the extracted text before allowing this source into AI use."
                : "The PDF is being read. Return later to review the extracted text."}
        </p>
        <div className="mt-5 flex flex-wrap gap-3">
          {canReviewText && (
            <Link className={secondaryButton} href={`/admin/materials/${documentId}/review-text`}>
              Review text
            </Link>
          )}
          {role === "reviewer" && (
            <span className="self-center text-sm text-slate-600">Reviewer access is read-only.</span>
          )}
        </div>
      </header>

      <section aria-labelledby="material-details-heading" className="mt-8">
        <h2 className="text-2xl font-semibold" id="material-details-heading">
          Material details
        </h2>
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Detail label="Grade" value={material.grade === null ? "Not assigned" : `Grade ${material.grade}`} />
          <Detail label="Subject" value={material.subject ?? "Not assigned"} />
          <Detail label="Medium" value={material.medium ?? "Not assigned"} />
          <Detail label="Material type" value={materialTypeLabels[material.material_type]} />
          <Detail label="Year" value={material.year === null ? "Not recorded" : String(material.year)} />
          <Detail label="Curriculum" value={material.curriculum ?? "Not assigned"} />
          <Detail label="Unit" value={material.unit ?? "Whole curriculum"} />
          <Detail label="Lesson" value={material.lesson ?? "All lessons in scope"} />
          <Detail
            label="Pages"
            value={material.page_count === null ? "Reading in progress" : String(material.page_count)}
          />
          <Detail label="Uploaded" value={formatDate(material.uploaded_at)} />
          {source.removal_reason && <Detail label="Removal reason" value={source.removal_reason} />}
        </dl>
      </section>

      <details className="mt-8 rounded-xl border border-slate-300 bg-white p-5">
        <summary className="w-fit cursor-pointer rounded text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-amber-600">
          Technical details
        </summary>
        <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
          <Detail label="Source document ID" value={source.id} />
          <Detail label="Checksum" value={source.checksum_sha256} />
          <Detail label="Extraction state" value={source.extraction_status} />
          <Detail label="Metadata scope version" value={String(source.metadata_scope_version)} />
        </dl>
      </details>
    </article>
  );
}
