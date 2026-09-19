"use client";

/**
 * Source V2 page review: the original page beside what the machine read.
 *
 * The teacher is not an OCR typist. Every region arrives with a proposed
 * reading already in it; the teacher's job is to look at the page and decide.
 * The editor only opens when they choose to correct something.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { cn } from "@/lib/utils";

type RegionState = "unverified" | "verified" | "excluded";

type ReaderEvidence = {
  reader: string;
  text: string;
  abstained: boolean;
  failure: string | null;
  seconds: number;
};

type Region = {
  region_id: string;
  region_type: string;
  candidate_id: string;
  revision: number;
  origin: "machine" | "human-correction";
  text: string;
  abstained: boolean;
  chosen_reader: string | null;
  reason: string;
  critical_conflict: boolean;
  agreement_ratio: number;
  disagreement: { cells?: { kind: string; variants: { value: string; readers: string[] }[] }[] };
  state: RegionState;
  bbox: number[] | null;
  verified_text: string | null;
  readers: ReaderEvidence[];
};

type Progress = {
  total: number;
  unverified: number;
  verified: number;
  excluded: number;
  resolved: number;
};

type PageView = {
  page_id: string;
  document_id: string;
  page_number: number;
  image_sha256: string;
  width: number;
  height: number;
  dpi: number;
  language: string;
  detector_version: string;
  progress: Progress;
  regions: Region[];
};

const TEXT = {
  sinhala: {
    heading: "මූලාශ්‍ර පිටුව සමාලෝචනය",
    original: "මුල් පිටුව",
    candidate: "යන්ත්‍රය කියවූ පෙළ",
    confirm: "පෙළ නිවැරදියි",
    correct: "පෙළ නිවැරදි කරන්න",
    saveCorrection: "නිවැරදි කළ පෙළ සුරකින්න",
    cancel: "අවලංගු කරන්න",
    exclude: "මෙම කොටස භාවිත නොකරන්න",
    unreadable: "මෙම කොටස කියවී නොමැත",
    verified: "තහවුරු කර ඇත",
    excluded: "භාවිතයෙන් ඉවත් කර ඇත",
    unverified: "තහවුරු කර නොමැත",
    conflict: "කියවීම් අතර නොගැලපීමක්",
    notRead: "මෙම කොටසේ පෙළ නිවැරදිව කියවී නොමැත.",
    reason: "හේතුව",
  },
  english: {
    heading: "Source page review",
    original: "Original page",
    candidate: "Machine reading",
    confirm: "Text is correct",
    correct: "Correct the text",
    saveCorrection: "Save corrected text",
    cancel: "Cancel",
    exclude: "Do not use this region",
    unreadable: "This region was not read",
    verified: "Verified",
    excluded: "Removed from use",
    unverified: "Not verified",
    conflict: "Readers disagree",
    notRead: "This region was not read correctly.",
    reason: "Reason",
  },
} as const;

const STATE_STYLES: Record<RegionState, string> = {
  verified: "bg-emerald-100 text-emerald-900 border-emerald-300",
  excluded: "bg-slate-200 text-slate-700 border-slate-300",
  unverified: "bg-amber-100 text-amber-900 border-amber-300",
};

function api(path: string): string {
  return `/api/v1/admin${path}`;
}

export function SourceV2Review({ pageId }: { pageId: string }) {
  const [page, setPage] = useState<PageView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState("");

  const labels = useMemo(
    () => (page?.language === "sinhala" ? TEXT.sinhala : TEXT.english),
    [page?.language],
  );

  const load = useCallback(async () => {
    setError(null);
    const response = await fetch(api(`/source-v2/pages/${pageId}`), {
      cache: "no-store",
    });
    if (!response.ok) {
      setError(`${response.status} ${await response.text()}`);
      return;
    }
    setPage((await response.json()) as PageView);
  }, [pageId]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = useCallback(
    async (region: Region, action: "confirm" | "correct" | "exclude", body: object) => {
      setBusy(`${region.region_id}:${action}`);
      setError(null);
      try {
        const response = await fetch(
          api(`/source-v2/pages/${pageId}/regions/${region.region_id}/${action}`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          },
        );
        if (!response.ok) {
          setError(`${action} failed: ${response.status} ${await response.text()}`);
          return false;
        }
        await load();
        return true;
      } finally {
        setBusy(null);
      }
    },
    [load, pageId],
  );

  if (error && !page) {
    return (
      <p className="p-6 text-red-700" data-testid="source-v2-error">
        {error}
      </p>
    );
  }
  if (!page) {
    return <p className="p-6 text-slate-600">Loading…</p>;
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-4 p-4" data-source-v2-review>
      <header className="flex flex-wrap items-baseline gap-4">
        <h1 className="text-xl font-semibold">
          {labels.heading} — {labels.original} {page.page_number}
        </h1>
        <p className="text-sm text-slate-700" data-testid="source-v2-progress">
          {labels.verified}: {page.progress.verified} · {labels.excluded}:{" "}
          {page.progress.excluded} · {labels.unverified}: {page.progress.unverified} /{" "}
          {page.progress.total}
        </p>
      </header>

      {error ? (
        <p className="rounded border border-red-300 bg-red-50 p-2 text-sm text-red-800">
          {error}
        </p>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-2">
        <figure className="min-h-0 overflow-auto rounded border border-slate-300 bg-white p-2">
          <figcaption className="pb-2 text-sm font-medium text-slate-700">
            {labels.original}
          </figcaption>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={api(`/source-v2/pages/${page.page_id}/render`)}
            alt={`${labels.original} ${page.page_number}`}
            className="w-full"
            data-testid="source-v2-original"
          />
        </figure>

        <ol className="min-h-0 space-y-3 overflow-auto" data-testid="source-v2-regions">
          {page.regions.map((region) => {
            const unreadable = region.abstained || region.text.trim().length === 0;
            const isEditing = editing === region.region_id;
            return (
              <li
                key={region.region_id}
                data-testid={`region-${region.region_id}`}
                data-region-state={region.state}
                className="rounded border border-slate-300 bg-white p-3"
              >
                <div className="flex flex-wrap items-center gap-2 pb-2">
                  <span className="font-mono text-xs text-slate-600">
                    {region.region_id} · {region.region_type}
                  </span>
                  <span
                    className={cn(
                      "rounded border px-2 py-0.5 text-xs font-medium",
                      STATE_STYLES[region.state],
                    )}
                  >
                    {labels[region.state]}
                  </span>
                  {region.critical_conflict ? (
                    <span className="rounded border border-orange-300 bg-orange-100 px-2 py-0.5 text-xs text-orange-900">
                      {labels.conflict}
                    </span>
                  ) : null}
                  {region.origin === "human-correction" ? (
                    <span className="rounded border border-sky-300 bg-sky-100 px-2 py-0.5 text-xs text-sky-900">
                      r{region.revision}
                    </span>
                  ) : null}
                </div>

                {unreadable ? (
                  <p className="rounded border border-red-300 bg-red-50 p-2 text-sm text-red-800">
                    {labels.notRead}
                  </p>
                ) : isEditing ? (
                  <textarea
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    rows={6}
                    className="w-full rounded border border-slate-400 p-2 font-sans text-sm"
                    data-testid={`editor-${region.region_id}`}
                  />
                ) : (
                  <pre
                    className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-2 text-sm"
                    data-testid={`text-${region.region_id}`}
                  >
                    {region.verified_text ?? region.text}
                  </pre>
                )}

                <p className="pt-1 text-xs text-slate-600">
                  {labels.reason}: {region.reason}
                </p>

                <div className="flex flex-wrap gap-2 pt-2">
                  {isEditing ? (
                    <>
                      <button
                        type="button"
                        disabled={busy !== null || draft.trim().length === 0}
                        onClick={async () => {
                          const saved = await act(region, "correct", {
                            candidate_id: region.candidate_id,
                            revision: region.revision,
                            corrected_text: draft,
                          });
                          if (saved) {
                            setEditing(null);
                            setDraft("");
                          }
                        }}
                        className="rounded bg-sky-700 px-3 py-1 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-400"
                        data-testid={`save-${region.region_id}`}
                      >
                        {labels.saveCorrection}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setEditing(null);
                          setDraft("");
                        }}
                        className="rounded border border-slate-400 px-3 py-1 text-sm"
                      >
                        {labels.cancel}
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        disabled={unreadable || busy !== null || region.state === "verified"}
                        onClick={() =>
                          act(region, "confirm", {
                            candidate_id: region.candidate_id,
                            revision: region.revision,
                            compared_with_image_sha256: page.image_sha256,
                          })
                        }
                        className="rounded bg-emerald-700 px-3 py-1 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-400"
                        data-testid={`confirm-${region.region_id}`}
                      >
                        {labels.confirm}
                      </button>
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() => {
                          setEditing(region.region_id);
                          setDraft(region.verified_text ?? region.text);
                        }}
                        className="rounded border border-slate-500 px-3 py-1 text-sm"
                        data-testid={`correct-${region.region_id}`}
                      >
                        {labels.correct}
                      </button>
                      <button
                        type="button"
                        disabled={busy !== null || region.state === "excluded"}
                        onClick={() =>
                          act(region, "exclude", {
                            candidate_id: region.candidate_id,
                            revision: region.revision,
                            note: note.trim() || labels.unreadable,
                          })
                        }
                        className="rounded border border-slate-500 px-3 py-1 text-sm disabled:cursor-not-allowed disabled:text-slate-400"
                        data-testid={`exclude-${region.region_id}`}
                      >
                        {labels.exclude}
                      </button>
                    </>
                  )}
                </div>

                {region.readers.length > 0 ? (
                  <details className="pt-2 text-xs text-slate-700">
                    <summary className="cursor-pointer">
                      Technical details ({region.readers.length} readers,{" "}
                      {region.disagreement.cells?.length ?? 0} conflicts)
                    </summary>
                    <ul className="space-y-1 pt-1">
                      {region.readers.map((reader) => (
                        <li key={reader.reader}>
                          <span className="font-mono">{reader.reader}</span> ·{" "}
                          {reader.seconds.toFixed(1)}s · {reader.text.length} chars
                          {reader.failure ? ` · ${reader.failure}` : ""}
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </li>
            );
          })}
        </ol>
      </div>

      <label className="text-xs text-slate-600">
        Exclusion reason
        <input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          className="ml-2 w-96 rounded border border-slate-400 px-2 py-1 text-sm"
          data-testid="exclusion-note"
          placeholder={labels.unreadable}
        />
      </label>
    </section>
  );
}
