"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Link from "next/link";
import { useEffect, useId, useMemo, useState } from "react";
import { Button } from "react-aria-components";

import type { ReviewLanguage } from "@/lib/review-language";

import { viewerButtonClass } from "./original-page-viewer";

type Snapshot = components["schemas"]["MaterialKnowledgePreparationResponse"];
type Problem = "expired" | "denied" | "missing" | "failed";
type State = {
  documentId: string;
  revision: number;
  data?: Snapshot;
  problem?: Problem;
};

const english = {
  title: "Content preparation",
  refresh: "Refresh status",
  loading: "Checking preparation…",
  not_requested: "Automatic preparation not started",
  waiting: "Waiting for material review",
  preparing: "Preparing checked pages…",
  prepared: "Checked content prepared",
  needs_attention: "Preparation needs attention",
  removed: "Removed from AI use",
  source: "Finish checking the original pages.",
  scope: "Confirm the material details and curriculum assignment.",
  reviewPages: "Review pages",
  openMaterials: "Open Materials",
  separate:
    "Curriculum mappings and final AI readiness still need their own checks.",
  historical:
    "New page-review decisions request preparation automatically once page and material checks pass. Historical material is not started silently.",
  failure:
    "Source reviews have been kept. Ask an administrator to inspect the preparation failure.",
  noContent:
    "No content from these pages is available for question generation.",
  expired: "Your session has expired. Sign in again to check preparation.",
  denied: "Your account cannot view this material's preparation.",
  missing: "Preparation details are not available for this material.",
  failed: "Preparation status could not be loaded. Try refreshing it.",
};

const sinhala: Record<keyof typeof english, string> = {
  title: "අන්තර්ගතය සකස් කිරීම",
  refresh: "තත්ත්වය නැවත බලන්න",
  loading: "තත්ත්වය පරීක්ෂා කරමින් පවතී…",
  not_requested: "ස්වයංක්‍රීයව සකස් කිරීම තවම ආරම්භ කර නැත",
  waiting: "මූලාශ්‍ර පරීක්ෂාව සම්පූර්ණ කළ යුතුය",
  preparing: "පරීක්ෂා කළ පිටු සකස් කරමින් පවතී…",
  prepared: "පරීක්ෂා කළ අන්තර්ගතය සකස් කර ඇත",
  needs_attention: "සකස් කිරීමේ ගැටලුවක් පවතී",
  removed: "AI භාවිතයෙන් ඉවත් කර ඇත",
  source: "මුල් පිටු පරීක්ෂා කර අවසන් කරන්න.",
  scope: "මූලාශ්‍ර තොරතුරු සහ විෂයමාලා වෙන් කිරීම තහවුරු කරන්න.",
  reviewPages: "පිටු පරීක්ෂා කරන්න",
  openMaterials: "මූලාශ්‍ර වෙත යන්න",
  separate: "විෂයමාලා ගැළපීම් සහ AI භාවිතයට සූදානම වෙන වෙනම පරීක්ෂා කළ යුතුය.",
  historical:
    "පිටු සහ මූලාශ්‍ර තොරතුරු පරීක්ෂා කළ පසු, නව පිටු පරීක්ෂණ තීරණ මගින් අන්තර්ගතය ස්වයංක්‍රීයව සකස් කෙරේ. පැරණි මූලාශ්‍ර නොදැනුවත්ව සකස් කිරීම ආරම්භ නොකෙරේ.",
  failure:
    "පිටු පරීක්ෂණ තීරණ සුරැකී ඇත. සකස් කිරීමේ ගැටලුව පරීක්ෂා කිරීමට පරිපාලකවරයෙකුගෙන් විමසන්න.",
  noContent: "මෙම පිටුවලින් ප්‍රශ්න සෑදීමට භාවිත කළ හැකි අන්තර්ගතයක් නොමැත.",
  expired: "ඔබේ සැසිය අවසන් වී ඇත. තත්ත්වය පරීක්ෂා කිරීමට නැවත පිවිසෙන්න.",
  denied: "මෙම මූලාශ්‍රයේ සකස් කිරීමේ තත්ත්වය බැලීමට ඔබට අවසර නැත.",
  missing: "මෙම මූලාශ්‍රයේ සකස් කිරීමේ තොරතුරු ලබා ගත නොහැක.",
  failed: "සකස් කිරීමේ තත්ත්වය ලබා ගත නොහැකි විය. නැවත බලන්න.",
};

const statuses = new Set([
  "not_requested",
  "waiting",
  "preparing",
  "prepared",
  "needs_attention",
  "removed",
]);
const countFields = [
  "verified_pages",
  "prepared_pages",
  "unit_count",
  "projection_count",
  "pending_pages",
  "failed_pages",
] as const;

function validSnapshot(
  data: Snapshot | undefined,
  documentId: string,
): data is Snapshot {
  return Boolean(
    data &&
    data.document_id === documentId &&
    statuses.has(data.status) &&
    [data.requested, data.source_ready, data.scope_ready].every(
      (value) => typeof value === "boolean",
    ) &&
    countFields.every(
      (field) => Number.isSafeInteger(data[field]) && data[field] >= 0,
    ) &&
    data.prepared_pages <= data.verified_pages &&
    (data.status !== "prepared" ||
      (data.source_ready &&
        data.scope_ready &&
        data.prepared_pages === data.verified_pages &&
        data.pending_pages === 0 &&
        data.failed_pages === 0)),
  );
}

export function MaterialKnowledgePreparation({
  documentId,
  language,
}: {
  documentId: string;
  language: ReviewLanguage;
}) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const headingId = useId();
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<State | null>(null);
  const copy = language === "si" ? sinhala : english;
  const current =
    state?.documentId === documentId && state.revision === revision
      ? state
      : null;

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    async function load() {
      try {
        const result = await api.GET(
          "/api/v1/admin/materials/{document_id}/knowledge-preparation",
          {
            params: { path: { document_id: documentId } },
            cache: "no-store",
            signal: controller.signal,
          },
        );
        if (!active) return;
        if (
          !result.response.ok ||
          result.error ||
          !validSnapshot(result.data, documentId)
        ) {
          const status = result.response.status;
          const problem: Problem =
            status === 401
              ? "expired"
              : status === 403
                ? "denied"
                : status === 404
                  ? "missing"
                  : "failed";
          setState({ documentId, revision, problem });
          return;
        }
        setState({ documentId, revision, data: result.data });
        if (result.data.status === "preparing")
          timer = setTimeout(() => void load(), 5000);
      } catch {
        if (active) setState({ documentId, revision, problem: "failed" });
      }
    }
    void load();
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [api, documentId, revision]);

  const data = current?.data;
  return (
    <section
      aria-labelledby={headingId}
      className="mt-6 rounded-xl border border-slate-300 bg-white p-5"
      lang={language}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-semibold" id={headingId}>
          {copy.title}
        </h2>
        <Button
          className={viewerButtonClass}
          isDisabled={!current}
          onPress={() => setRevision((value) => value + 1)}
        >
          {copy.refresh}
        </Button>
      </div>
      {!current && (
        <p className="mt-3 text-sm text-slate-600" role="status">
          {copy.loading}
        </p>
      )}
      {current?.problem && (
        <p className="mt-3 text-sm text-red-900" role="alert">
          {copy[current.problem]}
        </p>
      )}
      {data && (
        <div className="mt-3 space-y-3 text-sm text-slate-700">
          <p className="font-semibold text-slate-950" role="status">
            {copy[data.status]}
          </p>
          {data.status === "not_requested" && <p>{copy.historical}</p>}
          {data.status === "needs_attention" && <p>{copy.failure}</p>}
          {data.status !== "removed" && (
            <>
              {!data.source_ready && (
                <p>
                  {copy.source}{" "}
                  <Link
                    className="font-semibold underline underline-offset-4"
                    href={`/admin/materials/${documentId}/review-content`}
                    prefetch={false}
                  >
                    {copy.reviewPages}
                  </Link>
                </p>
              )}
              {!data.scope_ready && (
                <p>
                  {copy.scope}{" "}
                  <Link
                    className="font-semibold underline underline-offset-4"
                    href="/admin/materials"
                  >
                    {copy.openMaterials}
                  </Link>
                </p>
              )}
              <p>
                {language === "si"
                  ? `පරීක්ෂා කළ පිටු ${data.verified_pages}න් ${data.prepared_pages}ක් සකස් කර ඇත`
                  : `${data.prepared_pages} of ${data.verified_pages} checked pages prepared`}
              </p>
              <p>
                {language === "si"
                  ? `සකස් කළ අන්තර්ගත කොටස්: ${data.unit_count}`
                  : `Content sections prepared: ${data.unit_count}`}
              </p>
              {data.status === "prepared" && data.projection_count === 0 && (
                <p>{copy.noContent}</p>
              )}
              {data.status === "prepared" && <p>{copy.separate}</p>}
            </>
          )}
        </div>
      )}
    </section>
  );
}
