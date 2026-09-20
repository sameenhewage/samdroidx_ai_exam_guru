"use client";

/**
 * Source V2 page review: the original page beside what the machine read.
 *
 * The teacher is not a typist. Every region arrives with one proposed reading
 * already in it; the teacher's job is to look at the page and decide. The
 * editor only opens when they choose to correct something.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";

type RegionState = "unverified" | "verified" | "excluded";
type SourceKind =
  | "text_only"
  | "visual_only"
  | "visual_with_text"
  | "decorative"
  | "undecided";

type Region = {
  region_id: string;
  region_type: string;
  candidate_id: string;
  revision: number;
  origin: "machine" | "human-correction";
  text: string;
  abstained: boolean;
  reason: string;
  state: RegionState;
  bbox: number[] | null;
  verified_text: string | null;
  source_kind: SourceKind;
  proposed_source_kind: SourceKind | null;
  crop_sha256: string | null;
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
    locate: "පිටුවේ පෙන්වන්න",
    correct: "පෙළ නිවැරදි කරන්න",
    saveCorrection: "නිවැරදි කළ පෙළ සුරකින්න",
    cancel: "අවලංගු කරන්න",
    exclude: "මෙම කොටස භාවිත නොකරන්න",
    unreadable: "මෙම කොටස කියවී නොමැත",
    verified: "තහවුරු කර ඇත",
    excluded: "භාවිතයෙන් ඉවත් කර ඇත",
    unverified: "තහවුරු කර නොමැත",
    notRead: "මෙම කොටසේ පෙළ නිවැරදිව කියවී නොමැත.",
    kindText: "පෙළ",
    kindVisual: "රූපය පමණි",
    kindVisualText: "රූපය + පෙළ",
    kindDecorative: "අලංකරණ",
    kindUndecided: "තීරණය අවස්ථා",
    visualOnlyBody: "මෙය අධ්‍යාපනික රූපයකි. මුද්‍රිත පෙළක් නොමැත.",
    confirmVisual: "රූපය තහවුරු කරන්න",
    textPresent: "මෙහි පෙළ ඇත",
    needsDecision: "මෙම කොටස කුමක්දි යන්න තීරණය කරන්න.",
    reason: "හේතුව",
  },
  english: {
    heading: "Source page review",
    original: "Original page",
    candidate: "Machine reading",
    confirm: "Text is correct",
    locate: "Locate on page",
    correct: "Correct the text",
    saveCorrection: "Save corrected text",
    cancel: "Cancel",
    exclude: "Do not use this region",
    unreadable: "This region was not read",
    verified: "Verified",
    excluded: "Removed from use",
    unverified: "Not verified",
    notRead: "This region was not read correctly.",
    kindText: "Text",
    kindVisual: "Visual only",
    kindVisualText: "Visual + text",
    kindDecorative: "Decorative",
    kindUndecided: "Needs decision",
    visualOnlyBody: "This is an educational figure. It contains no printed text.",
    confirmVisual: "Confirm visual",
    textPresent: "Text is present",
    needsDecision: "Decide what this region is.",
    reason: "Reason",
  },
} as const;

/** Each kind gets its own wording *and* its own shape of border, so the
 *  distinction never depends on colour alone. */
const KIND_STYLES: Record<SourceKind, string> = {
  text_only: "border-slate-400 bg-slate-50 text-slate-800",
  visual_only: "border-violet-500 border-dashed bg-violet-50 text-violet-900",
  visual_with_text: "border-violet-500 bg-violet-50 text-violet-900",
  decorative: "border-slate-300 bg-white text-slate-500 italic",
  undecided: "border-amber-500 border-dotted bg-amber-50 text-amber-900",
};

const STATE_STYLES: Record<RegionState, string> = {
  verified: "bg-emerald-100 text-emerald-900 border-emerald-300",
  excluded: "bg-slate-200 text-slate-700 border-slate-300",
  unverified: "bg-amber-100 text-amber-900 border-amber-300",
};

function kindLabel(
  labels: (typeof TEXT)["english"] | (typeof TEXT)["sinhala"],
  kind: SourceKind,
): string {
  switch (kind) {
    case "visual_only":
      return labels.kindVisual;
    case "visual_with_text":
      return labels.kindVisualText;
    case "decorative":
      return labels.kindDecorative;
    case "undecided":
      return labels.kindUndecided;
    default:
      return labels.kindText;
  }
}

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
  const [selectedRegionId, setSelectedRegionId] = useState<string | null>(null);
  const [pulse, setPulse] = useState(false);
  const selectedRegionIdRef = useRef<string | null>(null);
  const viewerRef = useRef<HTMLElement | null>(null);
  const listRef = useRef<HTMLOListElement | null>(null);

  /**
   * One selection drives both panes.
   *
   * `origin` says which pane the teacher acted in, so the *other* pane is the
   * one that scrolls. Scrolling the pane they just clicked in would move the
   * thing under their cursor.
   */
  const select = useCallback(
    (regionId: string, origin: "card" | "overlay") => {
      // Re-selecting what is already selected must not scroll anything; a
      // teacher clicking inside the card they are reading should not have the
      // page move.
      if (regionId === selectedRegionIdRef.current) return;
      selectedRegionIdRef.current = regionId;
      setSelectedRegionId(regionId);
      setPulse(true);
      window.setTimeout(() => setPulse(false), 900);

      if (origin === "card") {
        const overlay = document.querySelector<HTMLElement>(
          `[data-testid="overlay-${regionId}"]`,
        );
        const viewer = viewerRef.current;
        if (overlay && viewer) {
          // Centre the region in the viewer without scrolling the whole page.
          const top =
            overlay.offsetTop - viewer.clientHeight / 2 + overlay.offsetHeight / 2;
          viewer.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
        }
        return;
      }

      const card = document.querySelector<HTMLElement>(`[data-testid="region-${regionId}"]`);
      const list = listRef.current;
      if (card && list) {
        const top = card.offsetTop - list.clientHeight / 2 + card.offsetHeight / 2;
        list.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
      }
    },
    [],
  );

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
    async (region: Region, action: "confirm" | "correct" | "exclude" | "confirm-visual" | "reclassify", body: object) => {
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
        <figure
          ref={viewerRef}
          className="min-h-0 overflow-auto rounded border border-slate-300 bg-white p-2"
        >
          <figcaption className="pb-2 text-sm font-medium text-slate-700">
            {labels.original}
          </figcaption>
          {/* The overlays are positioned as percentages of this wrapper, which
              is exactly the rendered image box. That keeps them aligned under
              any scaling - window resize, split-pane drag, zoom - with no
              recalculation. */}
          <div className="relative w-full">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={api(`/source-v2/pages/${page.page_id}/render`)}
              alt={`${labels.original} ${page.page_number}`}
              className="block w-full"
              data-testid="source-v2-original"
            />
            {page.regions.map((region) => {
              if (!region.bbox || region.bbox.length !== 4) return null;
              const [x0, y0, x1, y1] = region.bbox;
              const selected = region.region_id === selectedRegionId;
              return (
                <button
                  key={region.region_id}
                  type="button"
                  aria-label={`${labels.locate}: ${region.region_id}`}
                  aria-pressed={selected}
                  data-testid={`overlay-${region.region_id}`}
                  data-selected={selected}
                  onClick={() => select(region.region_id, "overlay")}
                  style={{
                    left: `${(x0 / page.width) * 100}%`,
                    top: `${(y0 / page.height) * 100}%`,
                    width: `${((x1 - x0) / page.width) * 100}%`,
                    height: `${((y1 - y0) / page.height) * 100}%`,
                  }}
                  className={cn(
                    "absolute cursor-pointer rounded-[2px] transition-colors",
                    selected
                      ? "z-10 border-[3px] border-sky-600 bg-sky-400/15 ring-2 ring-sky-300/70"
                      : "border border-transparent hover:border-2 hover:border-sky-400/70 hover:bg-sky-300/10",
                    selected && pulse ? "animate-pulse" : "",
                  )}
                >
                  {selected ? (
                    <span className="absolute -top-px left-0 -translate-y-full rounded-t bg-sky-600 px-1 text-[10px] font-semibold leading-4 text-white">
                      {region.region_id.split("-").pop()}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
        </figure>

        <ol
          ref={listRef}
          className="min-h-0 space-y-3 overflow-auto"
          data-testid="source-v2-regions"
        >
          {page.regions.map((region) => {
            const isVisualOnly = region.source_kind === "visual_only";
            const isVisualWithText = region.source_kind === "visual_with_text";
            const needsDecision = region.source_kind === "undecided";
            // A figure with no printed text is not unreadable; it is a figure.
            const unreadable =
              !isVisualOnly && (region.abstained || region.text.trim().length === 0);
            const isEditing = editing === region.region_id;
            return (
              <li
                key={region.region_id}
                data-testid={`region-${region.region_id}`}
                data-region-state={region.state}
                data-selected={region.region_id === selectedRegionId}
                tabIndex={0}
                onClick={() => select(region.region_id, "card")}
                onFocus={(event) => {
                  // onFocus bubbles, so focusing the correction textarea would
                  // otherwise re-select and scroll the page out from under
                  // someone who is mid-sentence. Only the card itself selects.
                  if (event.target === event.currentTarget) {
                    select(region.region_id, "card");
                  }
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    select(region.region_id, "card");
                  }
                }}
                className={cn(
                  "cursor-pointer rounded border bg-white p-3 outline-none transition-shadow",
                  region.region_id === selectedRegionId
                    ? "border-sky-600 border-l-4 shadow-md ring-1 ring-sky-300"
                    : "border-slate-300 hover:border-slate-400",
                )}
              >
                <div className="flex flex-wrap items-center gap-2 pb-2">
                  <span className="font-mono text-xs text-slate-600">
                    {region.region_id} · {region.region_type}
                  </span>
                  <button
                    type="button"
                    data-testid={`locate-${region.region_id}`}
                    title={labels.locate}
                    aria-label={`${labels.locate}: ${region.region_id}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      select(region.region_id, "card");
                    }}
                    className="rounded border border-slate-400 px-1.5 py-0.5 text-xs text-slate-700 hover:border-sky-600 hover:text-sky-700"
                  >
                    ⌖ {labels.locate}
                  </button>
                  <span
                    className={cn(
                      "rounded border px-2 py-0.5 text-xs font-medium",
                      STATE_STYLES[region.state],
                    )}
                  >
                    {labels[region.state]}
                  </span>
                  <span
                    data-testid={`kind-${region.region_id}`}
                    data-source-kind={region.source_kind}
                    className={cn(
                      "rounded border px-2 py-0.5 text-xs font-medium",
                      KIND_STYLES[region.source_kind],
                    )}
                  >
                    {kindLabel(labels, region.source_kind)}
                  </span>
                  {region.origin === "human-correction" ? (
                    <span className="rounded border border-sky-300 bg-sky-100 px-2 py-0.5 text-xs text-sky-900">
                      r{region.revision}
                    </span>
                  ) : null}
                </div>

                {isVisualOnly ? (
                  /* Emptiness is the right answer here, so this is
                     informational rather than an error. */
                  <p
                    data-testid={`visual-note-${region.region_id}`}
                    className="rounded border border-violet-300 bg-violet-50 p-2 text-sm text-violet-900"
                  >
                    {labels.visualOnlyBody}
                  </p>
                ) : needsDecision ? (
                  <p
                    data-testid={`undecided-note-${region.region_id}`}
                    className="rounded border border-amber-400 bg-amber-50 p-2 text-sm text-amber-900"
                  >
                    {labels.needsDecision}
                  </p>
                ) : unreadable ? (
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
                        disabled={
                          unreadable ||
                          needsDecision ||
                          busy !== null ||
                          region.state === "verified"
                        }
                        onClick={() =>
                          act(
                            region,
                            isVisualOnly || isVisualWithText ? "confirm-visual" : "confirm",
                            isVisualOnly
                              ? {
                                  candidate_id: region.candidate_id,
                                  revision: region.revision,
                                  compared_with_image_sha256: page.image_sha256,
                                  source_kind: "visual_only",
                                }
                              : isVisualWithText
                                ? {
                                    candidate_id: region.candidate_id,
                                    revision: region.revision,
                                    compared_with_image_sha256: page.image_sha256,
                                    source_kind: "visual_with_text",
                                    text: region.verified_text ?? region.text,
                                  }
                                : {
                                    candidate_id: region.candidate_id,
                                    revision: region.revision,
                                    compared_with_image_sha256: page.image_sha256,
                                  },
                          )
                        }
                        className="rounded bg-emerald-700 px-3 py-1 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-400"
                        data-testid={`confirm-${region.region_id}`}
                      >
                        {isVisualOnly ? labels.confirmVisual : labels.confirm}
                      </button>
                      {isVisualOnly ? (
                        /* The machine saw no text. If the reviewer can see
                           labels, reclassifying is the honest route - never
                           silently changing the kind behind them. */
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={() =>
                            act(region, "reclassify", {
                              candidate_id: region.candidate_id,
                              revision: region.revision,
                              source_kind: "visual_with_text",
                              note: note.trim() || "reviewer sees printed text in this figure",
                            })
                          }
                          className="rounded border border-violet-600 px-3 py-1 text-sm text-violet-800"
                          data-testid={`text-present-${region.region_id}`}
                        >
                          {labels.textPresent}
                        </button>
                      ) : null}
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
