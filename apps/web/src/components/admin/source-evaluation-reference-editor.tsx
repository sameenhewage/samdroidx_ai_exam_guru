"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type FormEvent,
} from "react";
import { Button, Form } from "react-aria-components";

import { cn } from "@/lib/utils";

import {
  reviewLanguageKey,
  savedReviewLanguage,
  subscribeReviewLanguage,
  type ReviewLanguage,
} from "@/lib/review-language";

import { OriginalPageViewer } from "./original-page-viewer";

type Preview = components["schemas"]["EvaluationPreviewResponse"];
type Reference = components["schemas"]["EvaluationReferenceResponse"];
const buttonClass =
  "inline-flex min-h-10 cursor-pointer items-center justify-center rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-950 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-700";
const primaryClass = cn(
  buttonClass,
  "border-slate-950 bg-slate-950 text-white hover:bg-slate-800",
);
const inputClass =
  "w-full rounded-lg border border-slate-400 bg-white px-3 py-2 text-slate-950 outline-none focus-visible:ring-2 focus-visible:ring-amber-600";
const english = {
  title: "Evaluation reference",
  back: "Back to review set",
  original: "Original comparison page",
  warning:
    "Evaluation only. Saving a reference does not confirm this source page, clear its reading failures, or make it ready for AI use.",
  text: "Reference text",
  reason: "Reason for reference",
  blank: "The original page has no visible text",
  check:
    "I compared this reference with the original image and manually reviewed all of its text.",
  save: "Save evaluation reference",
  saving: "Saving reference…",
  saved: "Reference saved for evaluation only.",
  retryImage: "Try image again",
  imageError:
    "The original image could not be verified. No reference can be saved until it is available.",
  error:
    "The reference was not saved. Your text is kept; check the connection or sign in again before retrying.",
  conflict:
    "Another reference was saved while you were editing. Your text is kept. Load the latest saved version before trying again.",
  loadLatest: "Load latest reference",
  latest: "Latest saved reference",
  keepMine: "Keep my text and use latest version",
  useLatest: "Use saved reference instead",
  discard: "Discard unsaved changes",
  keepEditing: "Keep editing",
  discardQuestion: "Leave without saving this reference?",
  zoomIn: "Zoom in",
  zoomOut: "Zoom out",
  reset: "Reset zoom",
  details: "Technical details",
  version: "Saved reference version",
  source: "Original source checksum",
  image: "Comparison image checksum",
  humanOnly:
    "Start with your own transcription. Do not submit an unreviewed machine reading as a human reference.",
};
const sinhala: typeof english = {
  title: "ඇගයීම සඳහා යොමු පෙළ",
  back: "සමාලෝචන කට්ටලයට ආපසු",
  original: "සැසඳීමට මුල් පිටුව",
  warning:
    "මෙය ඇගයීම සඳහා පමණි. යොමු පෙළ සුරැකීමෙන් මුල් පිටුව තහවුරු වන්නේ නැත; කියවීමේ දෝෂ ඉවත් නොවේ; AI භාවිතයට සූදානම් නොවේ.",
  text: "යොමු පෙළ",
  reason: "යොමු පෙළ සඳහා හේතුව",
  blank: "මුල් පිටුවේ දෘශ්‍ය පෙළ නොමැත",
  check:
    "මෙම යොමු පෙළ මුල් රූපය සමඟ සසඳා එහි සියලු පෙළ මා විසින් පරීක්ෂා කරන ලදී.",
  save: "ඇගයීම සඳහා යොමු පෙළ සුරකින්න",
  saving: "යොමු පෙළ සුරකිමින්…",
  saved: "ඇගයීම සඳහා පමණක් යොමු පෙළ සුරකින ලදී.",
  retryImage: "රූපය නැවත ලබාගන්න",
  imageError:
    "මුල් රූපය තහවුරු කළ නොහැකි විය. එය ලබාගන්නා තෙක් යොමු පෙළ සුරැකිය නොහැක.",
  error:
    "යොමු පෙළ සුරැකුණේ නැත. ඔබේ පෙළ මෙහි තබා ඇත. නැවත උත්සාහ කිරීමට පෙර සම්බන්ධතාව හෝ පිවිසුම පරීක්ෂා කරන්න.",
  conflict:
    "ඔබ සංස්කරණය කරන අතර වෙනත් යොමු පෙළක් සුරැකී ඇත. ඔබේ පෙළ මෙහි තබා ඇත. නවතම සුරැකි අනුවාදය ලබාගෙන සසඳන්න.",
  loadLatest: "නවතම යොමු පෙළ ලබාගන්න",
  latest: "නවතම සුරැකි යොමු පෙළ",
  keepMine: "මගේ පෙළ තබා නවතම අනුවාදය භාවිත කරන්න",
  useLatest: "සුරැකි යොමු පෙළ භාවිත කරන්න",
  discard: "නොසුරැකි වෙනස්කම් අත්හරින්න",
  keepEditing: "සංස්කරණය දිගටම කරන්න",
  discardQuestion: "මෙම යොමු පෙළ නොසුරැක පිටවන්නද?",
  zoomIn: "විශාල කරන්න",
  zoomOut: "කුඩා කරන්න",
  reset: "විශාලනය යළි සකසන්න",
  details: "තාක්ෂණික විස්තර",
  version: "සුරැකි යොමු පෙළ අනුවාදය",
  source: "මුල් මූලාශ්‍රයේ හඳුනාගැනීම",
  image: "සැසඳීමේ රූපයේ හඳුනාගැනීම",
  humanOnly:
    "ඔබේම පිටපත් කළ පෙළෙන් ආරම්භ කරන්න. පරීක්ෂා නොකළ යන්ත්‍ර කියවීමක් මානව යොමු පෙළක් ලෙස ඉදිරිපත් නොකරන්න.",
};

function matches(reference: Reference, preview: Preview) {
  return (
    reference.benchmark_id === preview.benchmark_id &&
    reference.document_id === preview.document_id &&
    reference.page_number === preview.page_number &&
    reference.evaluation_only === true &&
    Number.isSafeInteger(reference.version) &&
    reference.version > 0
  );
}

export function SourceEvaluationReferenceEditor({
  preview,
  onClose,
  onSaved,
}: {
  preview: Preview;
  onClose: () => void;
  onSaved: () => void;
}) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const storedLanguage = useSyncExternalStore(
    subscribeReviewLanguage,
    savedReviewLanguage,
    () => null,
  );
  const [languageChoice, setLanguageChoice] = useState<ReviewLanguage | null>(
    null,
  );
  const language =
    languageChoice ??
    storedLanguage ??
    (/^(si|sin|sinhala)(-|$)/i.test(preview.language) ? "si" : "en");
  const copy = language === "si" ? sinhala : english;
  const [baseline, setBaseline] = useState(preview.latest_reference);
  const [version, setVersion] = useState(preview.reference_version);
  const [text, setText] = useState(preview.latest_reference?.text ?? "");
  const [blank, setBlank] = useState(
    preview.latest_reference?.blank_reference ?? false,
  );
  const [reason, setReason] = useState("");
  const [checked, setChecked] = useState(false);
  const [imageReady, setImageReady] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadingLatest, setLoadingLatest] = useState(false);
  const [error, setError] = useState<number | null>(null);
  const [conflicted, setConflicted] = useState(false);
  const [latest, setLatest] = useState<Reference | null>(null);
  const [saved, setSaved] = useState(false);
  const [discard, setDiscard] = useState(false);
  const textId = useId();
  const reasonId = useId();
  const alive = useRef(true);
  const mutation = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const dirty =
    text !== (baseline?.text ?? "") ||
    blank !== (baseline?.blank_reference ?? false) ||
    Boolean(reason.trim());
  const canSave =
    imageReady &&
    !saving &&
    !loadingLatest &&
    !conflicted &&
    checked &&
    Boolean(reason.trim()) &&
    (blank ? text === "" : Boolean(text.trim()));

  useEffect(() => {
    alive.current = true;
    heading.current?.focus();
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    if (!dirty) return;
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  function changeLanguage(value: ReviewLanguage) {
    setLanguageChoice(value);
    try {
      window.localStorage.setItem(reviewLanguageKey, value);
    } catch {
      return;
    }
  }
  function changed() {
    setChecked(false);
    setSaved(false);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave || mutation.current) return;
    mutation.current = true;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const result = await api.POST(
        "/api/v1/admin/source-benchmarks/{benchmark_id}/pages/{document_id}/{page_number}/evaluation-references",
        {
          params: {
            path: {
              benchmark_id: preview.benchmark_id,
              document_id: preview.document_id,
              page_number: preview.page_number,
            },
          },
          body: {
            preview_id: preview.id,
            expected_version: version,
            text,
            blank_reference: blank,
            compared_with_original: true,
            human_reviewed: true,
            reason: reason.trim(),
          },
        },
      );
      if (!alive.current) return;
      if (result.error || !result.data) {
        setError(result.response.status);
        if (result.response.status === 409) setConflicted(true);
        return;
      }
      const reference = result.data;
      if (
        !matches(reference, preview) ||
        reference.preview_id !== preview.id ||
        reference.version !== version + 1 ||
        reference.text !== text ||
        reference.blank_reference !== blank
      ) {
        setError(502);
        return;
      }
      setBaseline(reference);
      setVersion(reference.version);
      setReason("");
      setChecked(false);
      setLatest(null);
      setSaved(true);
      onSaved();
    } catch {
      if (alive.current) setError(0);
    } finally {
      mutation.current = false;
      if (alive.current) setSaving(false);
    }
  }

  async function loadLatest() {
    setLoadingLatest(true);
    try {
      const result = await api.GET(
        "/api/v1/admin/source-benchmarks/{benchmark_id}/pages/{document_id}/{page_number}/evaluation-references",
        {
          params: {
            path: {
              benchmark_id: preview.benchmark_id,
              document_id: preview.document_id,
              page_number: preview.page_number,
            },
            query: { limit: 1, offset: 0 },
          },
          cache: "no-store",
        },
      );
      if (!alive.current) return;
      const reference = result.data?.[0];
      if (result.error || !reference || !matches(reference, preview)) {
        setError(result.response.status || 502);
        return;
      }
      setLatest(reference);
    } catch {
      if (alive.current) setError(0);
    } finally {
      if (alive.current) setLoadingLatest(false);
    }
  }
  function rebase(useSaved: boolean) {
    if (!latest || saving) return;
    setVersion(latest.version);
    setBaseline(latest);
    if (useSaved) {
      setText(latest.text);
      setBlank(latest.blank_reference);
      setReason("");
    }
    setLatest(null);
    setError(null);
    setConflicted(false);
    changed();
  }

  return (
    <section
      aria-label="Evaluation reference editor"
      data-source-review=""
      className="mx-auto flex min-h-0 w-full max-w-[100rem] flex-col gap-2 p-3 font-sans lg:flex-1 lg:overflow-hidden"
      lang={language}
    >
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h1
            ref={heading}
            tabIndex={-1}
            className="text-xl font-semibold outline-none"
          >
            {copy.title}
          </h1>
          <p
            className="mt-1 max-w-[70vw] truncate text-sm"
            title={preview.document_title}
          >
            {preview.document_title} · {preview.page_number}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            className={buttonClass}
            aria-pressed={language === "si"}
            onPress={() => changeLanguage("si")}
          >
            සිංහල
          </Button>
          <Button
            className={buttonClass}
            aria-pressed={language === "en"}
            onPress={() => changeLanguage("en")}
          >
            English
          </Button>
          <Button
            className={buttonClass}
            isDisabled={saving}
            onPress={() => (dirty ? setDiscard(true) : onClose())}
          >
            {copy.back}
          </Button>
        </div>
      </header>
      <p className="shrink-0 rounded-lg border border-amber-400 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-950">
        {copy.warning}
      </p>
      {discard && (
        <section
          role="alertdialog"
          aria-label={copy.discardQuestion}
          className="shrink-0 rounded-lg border border-amber-500 bg-amber-50 p-3"
        >
          <p>{copy.discardQuestion}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button className={buttonClass} onPress={() => setDiscard(false)}>
              {copy.keepEditing}
            </Button>
            <Button className={buttonClass} onPress={onClose}>
              {copy.discard}
            </Button>
          </div>
        </section>
      )}
      <Form
        onSubmit={save}
        className="flex min-h-0 flex-1 flex-col gap-2 lg:overflow-hidden"
      >
        <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-2">
          <OriginalPageViewer
            documentId={preview.document_id}
            pageNumber={preview.page_number}
            previewUrl={preview.preview_url}
            language={language}
            labels={{ original: copy.original, previewError: copy.imageError, imageRetry: copy.retryImage, zoomReset: copy.reset }}
            expectedDimensions={{ width: preview.image_width, height: preview.image_height }}
            className="h-[60dvh] min-h-64 lg:h-auto lg:min-h-0"
            onReady={ready => { setImageReady(ready); if (!ready) setChecked(false); }}
          />
          <section
            aria-label="Human reference entry"
            className="flex min-h-0 flex-col gap-2 rounded-lg border border-slate-300 bg-white p-3"
          >
            <p className="shrink-0 text-sm leading-6 text-slate-700">
              {copy.humanOnly}
            </p>
            <label className="shrink-0 font-semibold" htmlFor={textId}>
              {copy.text}
            </label>
            <textarea
              id={textId}
              className={cn(
                inputClass,
                "min-h-60 flex-1 resize-none whitespace-pre-wrap font-sans font-normal leading-7 lg:min-h-0",
              )}
              rows={12}
              dir="auto"
              lang={preview.language}
              maxLength={100000}
              disabled={saving || blank}
              value={text}
              onChange={(event) => {
                setText(event.currentTarget.value);
                changed();
              }}
            />
            <label className="flex shrink-0 items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={blank}
                disabled={saving || Boolean(text.trim())}
                onChange={(event) => {
                  setBlank(event.currentTarget.checked);
                  changed();
                }}
              />
              {copy.blank}
            </label>
            {(error !== null || latest) && (
              <div className="max-h-56 shrink-0 space-y-2 overflow-auto">
                {error !== null && (
                  <section
                    role="alert"
                    className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-950"
                  >
                    <p>{error === 409 ? copy.conflict : copy.error}</p>
                    <Button
                      className={cn(buttonClass, "mt-2")}
                      isDisabled={saving || loadingLatest}
                      onPress={() => void loadLatest()}
                    >
                      {copy.loadLatest}
                    </Button>
                  </section>
                )}
                {latest && (
                  <section
                    aria-label={copy.latest}
                    className="rounded-lg border border-amber-400 bg-amber-50 p-3"
                  >
                    <h2 className="font-semibold">
                      {copy.latest} · {latest.version}
                    </h2>
                    <p className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap font-sans">
                      {latest.text}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <Button
                        className={buttonClass}
                        onPress={() => rebase(false)}
                      >
                        {copy.keepMine}
                      </Button>
                      <Button
                        className={buttonClass}
                        onPress={() => rebase(true)}
                      >
                        {copy.useLatest}
                      </Button>
                    </div>
                  </section>
                )}
              </div>
            )}
          </section>
        </div>
        <div className="grid shrink-0 items-end gap-3 rounded-lg border border-slate-300 bg-white p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_auto]">
          <label
            className="grid gap-1 text-sm font-semibold"
            htmlFor={reasonId}
          >
            {copy.reason}
            <input
              id={reasonId}
              className={inputClass}
              maxLength={2000}
              required
              disabled={saving}
              value={reason}
              onChange={(event) => {
                setReason(event.currentTarget.value);
                changed();
              }}
            />
          </label>
          <label className="flex items-start gap-2 text-sm leading-6">
            <input
              className="mt-1"
              type="checkbox"
              checked={checked}
              disabled={saving || !imageReady}
              onChange={(event) => setChecked(event.currentTarget.checked)}
            />
            {copy.check}
          </label>
          <Button className={primaryClass} type="submit" isDisabled={!canSave}>
            {saving ? copy.saving : copy.save}
          </Button>
          {saved && (
            <p role="status" className="text-sm font-semibold sm:col-span-3">
              {copy.saved}
            </p>
          )}
        </div>
      </Form>
      <details className="relative shrink-0 text-sm">
        <summary className="w-fit cursor-pointer rounded px-2 py-1 font-semibold focus-visible:ring-2 focus-visible:ring-amber-600">
          {copy.details}
        </summary>
        <dl className="mt-2 grid max-h-48 gap-2 overflow-auto break-all rounded-lg border border-slate-300 bg-white p-3 lg:absolute lg:bottom-full lg:z-20 lg:w-full lg:shadow-lg">
          <dt>{copy.version}</dt>
          <dd>{version}</dd>
          <dt>{copy.source}</dt>
          <dd>{preview.source_checksum_sha256}</dd>
          <dt>{copy.image}</dt>
          <dd>{preview.image_sha256}</dd>
        </dl>
      </details>
    </section>
  );
}
