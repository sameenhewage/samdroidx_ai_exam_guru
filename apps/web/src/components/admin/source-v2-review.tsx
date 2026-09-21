"use client";

/**
 * Source V2 page review: the original page beside what the machine read.
 *
 * The teacher is not a typist. Every region arrives with one proposed reading
 * already in it; the teacher's job is to look at the page and decide. The
 * editor only opens when they choose to correct something.
 *
 * For a figure, three different things used to arrive as one block of text:
 * the words printed inside the picture, a machine's description of the
 * picture, and the validator's diagnostics. A reviewer could not tell which
 * was which — page 186's `p186-r002` showed an English sentence about a
 * line-art figure in the field that means "the exact Sinhala text printed
 * here". They are now three labelled sections with the diagnostics collapsed,
 * and each says plainly whether it is source or machine-generated.
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

type TechnicalEvidence = {
  reason: string;
  findings: string[];
  uncertainty: string[];
  abstained: boolean;
  proposed_source_kind: SourceKind | null;
  origin: "machine" | "human-correction";
  revision: number;
  crop_sha256: string | null;
};

type Region = {
  region_id: string;
  region_type: string;
  candidate_id: string;
  revision: number;
  origin: "machine" | "human-correction";
  /** The text printed *inside the crop*. Source, and only source. */
  text: string;
  abstained: boolean;
  reason: string;
  state: RegionState;
  bbox: number[] | null;
  verified_text: string | null;
  source_kind: SourceKind;
  proposed_source_kind: SourceKind | null;
  crop_sha256: string | null;
  /** Derived knowledge about the picture. Never source. */
  visual_description: string | null;
  detected_labels: string[];
  crop_url: string | null;
  technical_evidence: TechnicalEvidence;
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
    editAgain: "නැවත සංස්කරණය කරන්න",
    reverifyNote: "සංස්කරණය කළ විට නැවත තහවුරු කළ යුතු ය.",
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
    confirmVisual: "රූපය තහවුරු කරන්න",
    textPresent: "මෙහි පෙළ ඇත",
    needsDecision: "මෙම කොටස කුමක්දි යන්න තීරණය කරන්න.",
    reason: "හේතුව",
    // --- decorative page furniture (D18) ---
    decorativeNote:
      "මෙය පිටුවේ අලංකරණ කොටසකි (ශීර්ෂකය, පිටු අංකය, අලංකරණ ඉරි). " +
      "එය මූලාශ්‍ර අන්තර්ගතයක් ලෙස තහවුරු කළ නොහැක. " +
      "එකඟ නම් එය භාවිත නොකරන්න; එකඟ නොවේ නම් පළමුව වර්ගය වෙනස් කරන්න.",
    decorativeReason: "අලංකරණ කොටසකි; මූලාශ්‍ර අන්තර්ගතයක් නොවේ.",
    chooseKind: "මෙම කොටස කුමක්ද?",
    // --- the separated visual sections ---
    originalCrop: "මුල් රූපය",
    cropMissing: "මුල් රූපය නොලැබේ.",
    textInImage: "රූපයේ ඇති පෙළ",
    noTextInImage: "රූපයේ ඇති පෙළ: නොමැත",
    visualDescription: "රූප විස්තරය",
    noDescriptionYet: "රූප විස්තරයක් තවම ලියා නැත.",
    detectedLabels: "හඳුනාගත් ලේබල්",
    technical: "තාක්ෂණික විස්තර",
    fromSource: "මූලාශ්‍රයෙන්",
    machineGenerated: "යන්ත්‍රයෙන් සාදන ලදි",
    edit: "සංස්කරණය කරන්න",
    editDescription: "රූප විස්තරය සංස්කරණය කරන්න",
    saveDescription: "රූප විස්තරය සුරකින්න",
    reclassify: "වර්ගය වෙනස් කරන්න",
    descriptionNotVerification: "විස්තරය සුරැකීම තහවුරු කිරීමක් නොවේ.",
    evidenceReason: "හේතුව",
    evidenceFindings: "නිර්ණායක සොයාගැනීම්",
    evidenceUncertainty: "අවිනිශ්චිතතා",
    evidenceOrigin: "මූලය",
    evidenceRevision: "සංශෝධනය",
    evidenceCrop: "රූප පිටපතේ හැෂ්",
    evidenceProposedKind: "යන්ත්‍රය යෝජනා කළ වර්ගය",
    evidenceAbstained: "පෙළක් හමු නොවීය",
  },
  english: {
    heading: "Source page review",
    original: "Original page",
    candidate: "Machine reading",
    confirm: "Text is correct",
    locate: "Locate on page",
    correct: "Correct the text",
    editAgain: "Edit again",
    reverifyNote: "Editing withdraws verification; it must be confirmed again.",
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
    confirmVisual: "Confirm visual",
    textPresent: "Text is present",
    needsDecision: "Decide what this region is.",
    reason: "Reason",
    // --- decorative page furniture (D18) ---
    decorativeNote:
      "This is page furniture (running header, page number, ornamental rule). " +
      "It cannot be verified as source content. " +
      "If you agree, do not use it; if you disagree, change its kind first.",
    decorativeReason: "Decorative page furniture, not source content.",
    chooseKind: "What is this region?",
    // --- the separated visual sections ---
    originalCrop: "Original image",
    cropMissing: "The original image is unavailable.",
    textInImage: "Text in the image",
    noTextInImage: "Text in the image: none",
    visualDescription: "Image description",
    noDescriptionYet: "No image description has been written yet.",
    detectedLabels: "Detected labels",
    technical: "Technical details",
    fromSource: "From the source",
    machineGenerated: "Machine-generated",
    edit: "Edit",
    editDescription: "Edit image description",
    saveDescription: "Save image description",
    reclassify: "Change the kind",
    descriptionNotVerification: "Saving a description is not a verification.",
    evidenceReason: "Reason",
    evidenceFindings: "Deterministic findings",
    evidenceUncertainty: "Declared uncertainty",
    evidenceOrigin: "Origin",
    evidenceRevision: "Revision",
    evidenceCrop: "Crop checksum",
    evidenceProposedKind: "Kind proposed by the machine",
    evidenceAbstained: "No printed text was found",
  },
} as const;

type Labels = (typeof TEXT)["english"] | (typeof TEXT)["sinhala"];

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

/** Every kind a reviewer may move a region to, in the order they are offered.
 *  `undecided` is last because it is a retreat, not a decision. */
const KIND_CHOICES: readonly SourceKind[] = [
  "text_only",
  "visual_only",
  "visual_with_text",
  "decorative",
  "undecided",
];

function kindLabel(labels: Labels, kind: SourceKind): string {
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

/**
 * A section heading that says where its content came from.
 *
 * The badge is the whole point. Two sections of a figure card look alike and
 * mean opposite things: one is the source, one is a machine's opinion about
 * the source. Labelling only the machine one would leave the reader guessing
 * about the other, so both are marked.
 */
function ProvenanceHeading({
  title,
  provenance,
  regionId,
  slot,
  action,
}: {
  title: string;
  provenance: { text: string; machine: boolean };
  regionId: string;
  slot: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 pb-1 pt-3">
      <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
      <span
        data-testid={`${slot}-provenance-${regionId}`}
        data-provenance={provenance.machine ? "machine" : "source"}
        className={cn(
          "rounded border px-1.5 py-0.5 text-[11px] font-medium leading-4",
          provenance.machine
            ? "border-amber-400 bg-amber-50 text-amber-900"
            : "border-emerald-400 bg-emerald-50 text-emerald-900",
        )}
      >
        {provenance.text}
      </span>
      {action}
    </div>
  );
}

/**
 * Everything the machine noticed, closed by default.
 *
 * Provenance strings, `N deterministic finding(s)`, `spacing-doubt`,
 * `no-text`, the crop checksum and the revision are all real and all
 * auditable. None of them is what the teacher opened this card to look at,
 * and above the picture they drown it.
 */
function TechnicalDetails({
  labels,
  region,
}: {
  labels: Labels;
  region: Region;
}) {
  const evidence = region.technical_evidence;
  return (
    <details
      className="mt-3 rounded border border-slate-300 bg-slate-50"
      data-testid={`technical-${region.region_id}`}
    >
      <summary className="cursor-pointer px-2 py-1 text-xs font-medium text-slate-700">
        ▸ {labels.technical}
      </summary>
      <dl className="space-y-1 px-3 pb-2 pt-1 text-xs text-slate-700">
        <div>
          <dt className="inline font-medium">{labels.evidenceReason}: </dt>
          <dd className="inline break-words">{evidence.reason}</dd>
        </div>
        {evidence.findings.length > 0 ? (
          <div>
            <dt className="font-medium">{labels.evidenceFindings}</dt>
            <dd>
              <ul className="list-disc pl-5">
                {evidence.findings.map((finding) => (
                  <li key={finding} className="break-words">
                    {finding}
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        ) : null}
        {evidence.uncertainty.length > 0 ? (
          <div>
            <dt className="inline font-medium">{labels.evidenceUncertainty}: </dt>
            <dd className="inline font-mono">{evidence.uncertainty.join(", ")}</dd>
          </div>
        ) : null}
        {evidence.abstained ? (
          <div>
            <dt className="inline font-medium">{labels.evidenceAbstained}</dt>
            <dd className="inline" />
          </div>
        ) : null}
        {evidence.proposed_source_kind ? (
          <div>
            <dt className="inline font-medium">{labels.evidenceProposedKind}: </dt>
            <dd className="inline">
              {kindLabel(labels, evidence.proposed_source_kind)}
            </dd>
          </div>
        ) : null}
        <div>
          <dt className="inline font-medium">{labels.evidenceOrigin}: </dt>
          <dd className="inline font-mono">{evidence.origin}</dd>
        </div>
        <div>
          <dt className="inline font-medium">{labels.evidenceRevision}: </dt>
          <dd className="inline font-mono">r{evidence.revision}</dd>
        </div>
        {evidence.crop_sha256 ? (
          <div>
            <dt className="inline font-medium">{labels.evidenceCrop}: </dt>
            <dd className="inline break-all font-mono">{evidence.crop_sha256}</dd>
          </div>
        ) : null}
      </dl>
    </details>
  );
}

export function SourceV2Review({ pageId }: { pageId: string }) {
  const [page, setPage] = useState<PageView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  // The description editor is separate state from the text editor. Sharing
  // one draft would let a half-typed description be saved as source text.
  const [describing, setDescribing] = useState<string | null>(null);
  const [descriptionDraft, setDescriptionDraft] = useState("");
  // What the reviewer has *picked* in a Change-kind control, per region, and
  // has not yet applied. Kept separate from `region.source_kind` so choosing
  // an option changes nothing on the server until they press the button:
  // reclassification is always an explicit act (D18).
  const [kindDraft, setKindDraft] = useState<Record<string, SourceKind>>({});
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
    // Deferred by a zero timeout, as the other admin studios do: the first
    // load is a synchronisation with the server, not a render-time state
    // update, and running it inside the effect body cascades renders.
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  const act = useCallback(
    async (
      region: Region,
      action:
        | "confirm"
        | "correct"
        | "exclude"
        | "confirm-visual"
        | "reclassify"
        // Saving a description is an ordinary edit, so it goes through the
        // same request path as the others — and records no review event.
        | "describe",
      body: object,
    ) => {
      setBusy(`${region.region_id}:${action}`);
      setError(null);
      try {
        const response = await fetch(
          api(`/source-v2/pages/${pageId}/regions/${region.region_id}/${action}`),
          {
            method: action === "describe" ? "PUT" : "POST",
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
            // D18: page furniture. Non-educational by definition, so it can
            // never become Verified Source Content and must never be offered
            // the ordinary text-confirm button.
            const isDecorative = region.source_kind === "decorative";
            const needsDecision = region.source_kind === "undecided";
            // A figure with no printed text is not unreadable; it is a figure.
            // Neither is an ornamental rule that prints nothing: emptiness is
            // the correct reading of both, and the decorative note below says
            // so far more usefully than a reading-failure banner.
            const unreadable =
              !isVisualOnly &&
              !isVisualWithText &&
              !isDecorative &&
              (region.abstained || region.text.trim().length === 0);
            const isEditing = editing === region.region_id;
            // The three concepts only need separating where there is a
            // picture. Prose keeps the layout it already had.
            const isVisual =
              isVisualOnly || isVisualWithText || region.region_type === "figure";
            const isDescribing = describing === region.region_id;
            const openTextEditor = () => {
              setEditing(region.region_id);
              // Start from the latest *human-verified* text where one exists.
              // Falling back to the machine candidate would silently discard
              // the reviewer's own correction and invite them to redo it.
              setDraft(region.verified_text ?? region.text);
            };
            const openDescriptionEditor = () => {
              setDescribing(region.region_id);
              // Same rule for derived knowledge: resume from the description
              // that is actually saved, never from a blank box.
              setDescriptionDraft(region.visual_description ?? "");
            };
            // The kind shown in the Change-kind control: what the reviewer
            // picked if they picked anything, otherwise what the region is.
            const chosenKind = kindDraft[region.region_id] ?? region.source_kind;

            /* Exclude and Correct are offered to every kind; only their
               prominence changes. Building them once keeps the decorative
               card's ordering a layout decision rather than a second copy of
               the same two buttons drifting out of step. */
            const excludeAction = (emphasis: string) => (
              <button
                type="button"
                disabled={busy !== null || region.state === "excluded"}
                onClick={() =>
                  act(region, "exclude", {
                    candidate_id: region.candidate_id,
                    revision: region.revision,
                    // The exclusion reason is permanent. "This region was not
                    // read" is simply untrue of a running header the machine
                    // read perfectly well, so decorative carries its own.
                    note:
                      note.trim() ||
                      (isDecorative ? labels.decorativeReason : labels.unreadable),
                  })
                }
                className={cn(
                  "rounded border px-3 py-1 text-sm disabled:cursor-not-allowed",
                  emphasis,
                )}
                data-testid={`exclude-${region.region_id}`}
              >
                {labels.exclude}
              </button>
            );
            const correctAction = (
              <button
                type="button"
                disabled={busy !== null}
                title={region.state === "verified" ? labels.reverifyNote : undefined}
                onClick={openTextEditor}
                className="rounded border border-slate-500 px-3 py-1 text-sm"
                data-testid={`correct-${region.region_id}`}
              >
                {region.state === "verified" ? labels.editAgain : labels.correct}
              </button>
            );
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
                  // Key events bubble, so Space typed into the correction
                  // textarea arrived here and got preventDefault()-ed: the
                  // teacher could not type a space, and Enter could not make
                  // a newline. The card only activates on keys it owns, the
                  // same ownership rule onFocus above already uses. Stopping
                  // propagation inside every child control would fix the
                  // symptom and leave the next nested control broken.
                  if (event.target !== event.currentTarget) return;
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

                {isDecorative ? (
                  /* Says what the card is, and therefore why the ordinary
                     confirm button is absent rather than merely greyed out.
                     A disabled button with no explanation reads as a bug. */
                  <p
                    data-testid={`decorative-note-${region.region_id}`}
                    className="mb-2 rounded border border-slate-400 bg-slate-100 p-2 text-sm text-slate-800"
                  >
                    {labels.decorativeNote}
                  </p>
                ) : null}

                {isVisual ? (
                  <div data-testid={`visual-${region.region_id}`}>
                    {/* 1. The picture itself. It is the evidence; everything
                        below is either read off it or written about it. */}
                    {region.crop_url ? (
                      <figure className="rounded border border-violet-300 bg-violet-50 p-2">
                        <figcaption className="pb-1 text-center text-xs font-medium text-violet-900">
                          {labels.originalCrop}
                        </figcaption>
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={region.crop_url}
                          alt={`${labels.originalCrop}: ${region.region_id}`}
                          className="mx-auto block max-h-72 w-auto max-w-full bg-white"
                          data-testid={`crop-${region.region_id}`}
                        />
                      </figure>
                    ) : (
                      <p
                        data-testid={`crop-missing-${region.region_id}`}
                        className="rounded border border-slate-300 bg-slate-50 p-2 text-sm text-slate-700"
                      >
                        {labels.cropMissing}
                      </p>
                    )}

                    {needsDecision ? (
                      <p
                        data-testid={`undecided-note-${region.region_id}`}
                        className="mt-2 rounded border border-amber-400 bg-amber-50 p-2 text-sm text-amber-900"
                      >
                        {labels.needsDecision}
                      </p>
                    ) : null}

                    {/* 2. Text printed inside the crop. Source. */}
                    {isEditing ? (
                      <>
                        <ProvenanceHeading
                          title={labels.textInImage}
                          provenance={{ text: labels.fromSource, machine: false }}
                          regionId={region.region_id}
                          slot="text-in-image"
                        />
                        <textarea
                          value={draft}
                          onChange={(event) => setDraft(event.target.value)}
                          rows={4}
                          className="w-full rounded border border-slate-400 p-2 font-sans text-sm"
                          data-testid={`editor-${region.region_id}`}
                        />
                      </>
                    ) : isVisualOnly ? (
                      /* Emptiness is the right answer for a drawing, so this
                         states the fact rather than reporting a failure. */
                      <ProvenanceHeading
                        title={labels.noTextInImage}
                        provenance={{ text: labels.fromSource, machine: false }}
                        regionId={region.region_id}
                        slot="text-in-image"
                      />
                    ) : (
                      <>
                        <ProvenanceHeading
                          title={labels.textInImage}
                          provenance={{ text: labels.fromSource, machine: false }}
                          regionId={region.region_id}
                          slot="text-in-image"
                          action={
                            <button
                              type="button"
                              disabled={busy !== null}
                              onClick={(event) => {
                                event.stopPropagation();
                                openTextEditor();
                              }}
                              className="rounded border border-slate-400 px-1.5 py-0.5 text-xs text-slate-700 hover:border-sky-600 hover:text-sky-700"
                              data-testid={`edit-text-${region.region_id}`}
                            >
                              ✎ {labels.edit}
                            </button>
                          }
                        />
                        <pre
                          className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-2 text-sm"
                          data-testid={`text-${region.region_id}`}
                        >
                          {region.verified_text ?? region.text}
                        </pre>
                      </>
                    )}

                    {/* 3. What the picture shows. Derived knowledge (D18) —
                        badged as machine-generated so it can never be read as
                        something printed on the page. */}
                    <ProvenanceHeading
                      title={labels.visualDescription}
                      provenance={{ text: labels.machineGenerated, machine: true }}
                      regionId={region.region_id}
                      slot="description"
                      action={
                        isDescribing ? null : (
                          <button
                            type="button"
                            disabled={busy !== null}
                            onClick={(event) => {
                              event.stopPropagation();
                              openDescriptionEditor();
                            }}
                            className="rounded border border-slate-400 px-1.5 py-0.5 text-xs text-slate-700 hover:border-sky-600 hover:text-sky-700"
                            data-testid={`edit-description-${region.region_id}`}
                          >
                            ✎ {labels.edit}
                          </button>
                        )
                      }
                    />
                    {isDescribing ? (
                      <div className="space-y-2">
                        <textarea
                          value={descriptionDraft}
                          onChange={(event) => setDescriptionDraft(event.target.value)}
                          rows={4}
                          className="w-full rounded border border-amber-400 p-2 font-sans text-sm"
                          data-testid={`description-editor-${region.region_id}`}
                        />
                        <p className="text-xs text-slate-600">
                          {labels.descriptionNotVerification}
                        </p>
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            disabled={
                              busy !== null || descriptionDraft.trim().length === 0
                            }
                            onClick={async (event) => {
                              event.stopPropagation();
                              const saved = await act(region, "describe", {
                                candidate_id: region.candidate_id,
                                revision: region.revision,
                                visual_description: descriptionDraft,
                                detected_labels: region.detected_labels,
                              });
                              if (saved) {
                                setDescribing(null);
                                setDescriptionDraft("");
                              }
                            }}
                            className="rounded bg-amber-700 px-3 py-1 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-400"
                            data-testid={`save-description-${region.region_id}`}
                          >
                            {labels.saveDescription}
                          </button>
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              setDescribing(null);
                              setDescriptionDraft("");
                            }}
                            className="rounded border border-slate-400 px-3 py-1 text-sm"
                            data-testid={`cancel-description-${region.region_id}`}
                          >
                            {labels.cancel}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <p
                        className="whitespace-pre-wrap break-words rounded border border-amber-200 bg-amber-50/60 p-2 text-sm text-slate-800"
                        data-testid={`description-${region.region_id}`}
                      >
                        {region.visual_description ?? labels.noDescriptionYet}
                      </p>
                    )}

                    {/* 4. Labels legible inside the crop, only when there are
                        any. An empty heading asserts nothing and just adds
                        another thing to read. */}
                    {region.detected_labels.length > 0 ? (
                      <>
                        <ProvenanceHeading
                          title={labels.detectedLabels}
                          provenance={{ text: labels.fromSource, machine: false }}
                          regionId={region.region_id}
                          slot="labels"
                        />
                        <ul
                          className="list-disc pl-6 text-sm text-slate-800"
                          data-testid={`labels-${region.region_id}`}
                        >
                          {region.detected_labels.map((label) => (
                            <li key={label}>{label}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}

                    {/* 5. Diagnostics, closed. */}
                    <TechnicalDetails labels={labels} region={region} />
                  </div>
                ) : (
                  <>
                    {needsDecision ? (
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
                  </>
                )}

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
                  ) : isDecorative ? (
                    /* D18. Decorative content can never become Verified
                       Source Content, so the ordinary confirm button is not
                       rendered here at all — a control whose only possible
                       outcome is a 422 is worse than no control. The two real
                       decisions are offered instead: agree and take it out of
                       use, or disagree and say what it actually is. */
                    <>
                      {excludeAction(
                        "border-slate-900 bg-slate-900 font-medium text-white hover:bg-slate-800 disabled:border-slate-400 disabled:bg-slate-400 disabled:text-white",
                      )}
                      <span className="inline-flex flex-wrap items-center gap-2 rounded border border-sky-300 bg-sky-50 px-2 py-1">
                        <label
                          htmlFor={`kind-select-${region.region_id}`}
                          className="text-xs font-medium text-slate-700"
                        >
                          {labels.chooseKind}
                        </label>
                        <select
                          id={`kind-select-${region.region_id}`}
                          data-testid={`kind-select-${region.region_id}`}
                          value={chosenKind}
                          disabled={busy !== null}
                          onChange={(event) =>
                            // Picking is not deciding. This only moves the
                            // draft; nothing reaches the server until the
                            // button beside it is pressed.
                            setKindDraft((current) => ({
                              ...current,
                              [region.region_id]: event.target.value as SourceKind,
                            }))
                          }
                          className="rounded border border-slate-400 bg-white px-2 py-1 text-sm text-slate-900"
                        >
                          {KIND_CHOICES.map((kind) => (
                            <option key={kind} value={kind}>
                              {kindLabel(labels, kind)}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          disabled={busy !== null || chosenKind === region.source_kind}
                          onClick={async () => {
                            const changed = await act(region, "reclassify", {
                              candidate_id: region.candidate_id,
                              revision: region.revision,
                              source_kind: chosenKind,
                              note:
                                note.trim() ||
                                `reviewer reclassified this region as ${chosenKind}`,
                            });
                            if (changed) {
                              setKindDraft((current) => {
                                const next = { ...current };
                                delete next[region.region_id];
                                return next;
                              });
                            }
                          }}
                          className="rounded border border-sky-700 bg-white px-3 py-1 text-sm font-medium text-sky-900 disabled:cursor-not-allowed disabled:border-slate-300 disabled:text-slate-400"
                          data-testid={`reclassify-${region.region_id}`}
                        >
                          {labels.reclassify}
                        </button>
                      </span>
                      {correctAction}
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
                      {isVisualWithText ? (
                        /* The mirror of `text-present`: a reviewer who looks
                           and sees no printed label says so, rather than the
                           machine's proposal quietly standing. */
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={() =>
                            act(region, "reclassify", {
                              candidate_id: region.candidate_id,
                              revision: region.revision,
                              source_kind: "visual_only",
                              note:
                                note.trim() ||
                                "reviewer sees no printed text in this figure",
                            })
                          }
                          className="rounded border border-violet-600 px-3 py-1 text-sm text-violet-800"
                          data-testid={`reclassify-${region.region_id}`}
                        >
                          {labels.reclassify}
                        </button>
                      ) : null}
                      {correctAction}
                      {isVisual ? (
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={openDescriptionEditor}
                          className="rounded border border-amber-600 px-3 py-1 text-sm text-amber-900"
                          data-testid={`describe-${region.region_id}`}
                        >
                          {labels.editDescription}
                        </button>
                      ) : null}
                      {excludeAction("border-slate-500 disabled:text-slate-400")}
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
