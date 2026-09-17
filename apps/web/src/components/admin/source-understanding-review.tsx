"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type FormEvent,
} from "react";
import { Button } from "react-aria-components";

import {
  reviewLanguageKey,
  savedReviewLanguage,
  subscribeReviewLanguage,
  type ReviewLanguage,
} from "@/lib/review-language";
import { cn } from "@/lib/utils";

import type { AdminRole } from "./admin-header";
import {
  OriginalPageViewer,
  sourceViewerCopy,
  viewerButtonClass,
} from "./original-page-viewer";
import { SourceUnderstandingContent } from "./source-understanding-content";
import {
  correctionIsComplete,
  SourceUnderstandingEditor,
} from "./source-understanding-editor";

type Snapshot = components["schemas"]["UnderstandingPageResponse"];
type Workspace = components["schemas"]["PageReviewWorkspaceResponse"];
type Candidate = components["schemas"]["ObservationCandidate"];
type CreateJob = components["schemas"]["UnderstandingJobCreateRequest"];
type Verify = components["schemas"]["UnderstandingVerifyRequest"];
type Correction = components["schemas"]["UnderstandingCorrectionRequest"];
type Content = components["schemas"]["PageUnderstanding"];
type VerifyDraft = {
  mode: "verify";
  candidate: Candidate;
  version: number;
  compared: boolean;
  allDetails: boolean;
  resolved: string[];
  reason: string;
};
type CorrectionDraft = {
  mode: "correct";
  candidate: Candidate;
  version: number;
  content: Content;
  reason: string;
  requestId: string;
};
type LifecycleDraft = {
  mode: "exclude" | "reopen";
  candidate: Candidate | null;
  version: number;
  reason: string;
  confirmed: boolean;
};
type Draft = VerifyDraft | CorrectionDraft | LifecycleDraft;
type Problem =
  | "loadError"
  | "requestError"
  | "conflict"
  | "denied"
  | "expired"
  | "limited"
  | "invalidPage";

const english = {
  title: "Review page content",
  back: "Back to material",
  loading: "Loading page…",
  intro:
    "Does the source text and structure on the right match the original page on the left? Verify the source before any educational analysis.",
  review: "Review this reading",
  confirm: "Confirm checked page",
  cancel: "Cancel review",
  cancelAction: "Cancel",
  correct: "Correct this reading",
  saveCorrection: "Save correction",
  correctionReason: "Reason for correction",
  rebaseCorrection: "Keep correction with latest reading",
  exclude: "Do not use this page",
  excludeSubmit: "Exclude page",
  excludeReason: "Reason for excluding this page",
  excludeConsent: "I understand this page will not be used for AI.",
  exclusionNotice:
    "Exclusion keeps the original and every earlier reading. It is not a source verification.",
  excluded: "This page is excluded from AI use. Its history is preserved.",
  reopen: "Review this page again",
  reopenSubmit: "Return page to review",
  reopenReason: "Reason for reopening this page",
  reopenConsent:
    "I want to return this page to review, without restoring trust.",
  reopenNotice:
    "Reopening does not restore an earlier verification. Check the page again before using it.",
  analyze: "Analyze page",
  analyzeAgain: "Read page again",
  analyzing: "Reading this page…",
  retryAnalysis: "Retry analysis request",
  refresh: "Reload latest reading",
  latest: "Review latest reading",
  checked: "Page checked against the original",
  unverified: "Needs comparison with the original",
  machineReady: "Machine reading ready for review",
  machineAttention: "This page needs attention. Re-read or correct the highlighted details.",
  recoveredSource: "Re-read source — not ready for confirmation",
  noReliableSource: "No reliable machine reading is available. Re-read or correct from the original.",
  unavailable:
    "Page analysis is not configured. Ask the administrator to enable it.",
  empty: "No page understanding has been proposed yet.",
  metadata:
    "Material details and other pages still have their own review requirements. Checking this page does not make the whole material ready for AI.",
  inactive:
    "This material is removed from use. This review does not restore it.",
  readonly: "Reviewer access is read-only.",
  compared: "I compared this reading with the original page.",
  allDetails: "I checked every visible source detail.",
  meaning: "Choose the teaching points supported by this page",
  uncertainty: "Check each uncertain detail against the original",
  reason: "Reason for accepting this reading",
  technical: "Analysis details",
  failedChecks:
    "Some source checks failed. Do not accept this reading until the problems are resolved.",
  failedAnalysis:
    "The latest analysis could not be completed. Its history has been kept; no page was automatically checked.",
  loadError:
    "This page could not be loaded. Your review choices have been kept.",
  requestError:
    "The request could not be completed. Reload the latest reading before trying again.",
  conflict: "This reading changed. Your review choices have been kept.",
  denied: "Your account does not have permission for this action.",
  expired: "Your session has expired. Sign in again before continuing.",
  limited:
    "Please wait before trying again. No automatic analysis retry will be sent.",
  invalidPage: "Choose a valid page number from this material.",
  leave:
    "Discard this unsaved review and leave the page? Cancel keeps your work.",
  working: "Saving review…",
  nav: "Page navigation",
  analyzeReason: "Requested a fresh reading of this original page.",
};
const sinhala: Record<keyof typeof english, string> = {
  title: "පිටුවේ අන්තර්ගතය පරීක්ෂා කරන්න",
  back: "මූලාශ්‍රය වෙත ආපසු",
  loading: "පිටුව පූරණය වෙමින් පවතී…",
  intro:
    "දකුණු පස පෙළ සහ ව්‍යුහය වම් පස මුල් පිටුවට ගැළපේද? අධ්‍යාපනික විශ්ලේෂණයට පෙර මූලාශ්‍රය තහවුරු කරන්න.",
  review: "මෙම කියවීම පරීක්ෂා කරන්න",
  confirm: "පරීක්ෂා කළ පිටුව තහවුරු කරන්න",
  cancel: "පරීක්ෂාව අවලංගු කරන්න",
  cancelAction: "අවලංගු කරන්න",
  correct: "මෙම කියවීම නිවැරදි කරන්න",
  saveCorrection: "නිවැරදි කිරීම සුරකින්න",
  correctionReason: "නිවැරදි කිරීමට හේතුව",
  rebaseCorrection: "නවතම කියවීම සමඟ නිවැරදි කිරීම රඳවා ගන්න",
  exclude: "මෙම පිටුව භාවිත නොකරන්න",
  excludeSubmit: "පිටුව භාවිතයෙන් ඉවත් කරන්න",
  excludeReason: "මෙම පිටුව භාවිත නොකිරීමට හේතුව",
  excludeConsent: "මෙම පිටුව AI සඳහා භාවිත නොවන බව මට වැටහේ.",
  exclusionNotice:
    "මුල් පිටුව සහ පෙර කියවීම් රඳවා ගනී. භාවිතයෙන් ඉවත් කිරීම මූලාශ්‍ර තහවුරු කිරීමක් නොවේ.",
  excluded: "මෙම පිටුව AI භාවිතයෙන් ඉවත් කර ඇත. එහි ඉතිහාසය රඳවා ඇත.",
  reopen: "මෙම පිටුව නැවත පරීක්ෂා කරන්න",
  reopenSubmit: "පිටුව නැවත පරීක්ෂාවට යොමු කරන්න",
  reopenReason: "මෙම පිටුව නැවත පරීක්ෂා කිරීමට හේතුව",
  reopenConsent:
    "පෙර තහවුරු කිරීම නැවත ලබා නොදී මෙම පිටුව පරීක්ෂාවට යොමු කිරීමට මට අවශ්‍යයි.",
  reopenNotice:
    "නැවත විවෘත කිරීමෙන් පෙර තහවුරු කිරීම නැවත නොලැබේ. භාවිතයට පෙර පිටුව නැවත පරීක්ෂා කරන්න.",
  analyze: "පිටුව කියවන්න",
  analyzeAgain: "පිටුව නැවත කියවන්න",
  analyzing: "මෙම පිටුව කියවමින් පවතී…",
  retryAnalysis: "කියවීමේ ඉල්ලීම නැවත යවන්න",
  refresh: "නවතම කියවීම පූරණය කරන්න",
  latest: "නවතම කියවීම පරීක්ෂා කරන්න",
  checked: "මුල් පිටුව සමඟ සසඳා තහවුරු කර ඇත",
  unverified: "මුල් පිටුව සමඟ සැසඳිය යුතුය",
  machineReady: "පද්ධතියේ කියවීම පරීක්ෂාවට සූදානම්",
  machineAttention: "මෙම පිටුවේ ගැටලු ඇත. සලකුණු කළ කරුණු නැවත කියවන්න හෝ නිවැරදි කරන්න.",
  recoveredSource: "නැවත කියවූ පෙළ — තහවුරු කිරීමට සූදානම් නැත",
  noReliableSource: "විශ්වාසදායක පද්ධති කියවීමක් නැත. මුල් පිටුව සමඟ සසඳා නැවත කියවන්න හෝ නිවැරදි කරන්න.",
  unavailable:
    "පිටු කියවීම තවම සකසා නැත. එය සක්‍රිය කිරීමට පරිපාලකවරයාගෙන් විමසන්න.",
  empty: "මෙම පිටුව සඳහා අවබෝධයක් තවම යෝජනා කර නැත.",
  metadata:
    "මූලාශ්‍රයේ විස්තර සහ අනෙක් පිටු වෙන වෙනම පරීක්ෂා කළ යුතුය. මෙම පිටුව තහවුරු කිරීමෙන් මුළු මූලාශ්‍රයම AI සඳහා සූදානම් නොවේ.",
  inactive:
    "මෙම මූලාශ්‍රය භාවිතයෙන් ඉවත් කර ඇත. මෙම පරීක්ෂාවෙන් එය නැවත සක්‍රිය නොවේ.",
  readonly: "ඔබට බැලිය හැකි නමුත් වෙනස් කළ නොහැක.",
  compared: "මම මෙම කියවීම මුල් පිටුව සමඟ සැසඳුවෙමි.",
  allDetails: "මම පෙනෙන සෑම මූලාශ්‍ර කරුණක්ම පරීක්ෂා කළෙමි.",
  meaning: "මෙම පිටුවෙන් සහාය ලැබෙන ඉගැන්වීමේ කරුණු තෝරන්න",
  uncertainty: "අවිනිශ්චිත සෑම කරුණක්ම මුල් පිටුව සමඟ පරීක්ෂා කරන්න",
  reason: "මෙම කියවීම පිළිගැනීමට හේතුව",
  technical: "කියවීමේ විස්තර",
  failedChecks:
    "මෙම පිටුවේ පෙළ නිවැරදිව කියවී නොමැත. ගැටලු විසඳන තුරු මෙම කියවීම පිළි නොගන්න.",
  failedAnalysis:
    "නවතම කියවීම සම්පූර්ණ කළ නොහැකි විය. ඉතිහාසය රඳවා ඇත; කිසිදු පිටුවක් ස්වයංක්‍රීයව තහවුරු කර නැත.",
  loadError: "මෙම පිටුව පූරණය කළ නොහැකි විය. ඔබගේ පරීක්ෂා තේරීම් රඳවා ඇත.",
  requestError:
    "ඉල්ලීම සම්පූර්ණ කළ නොහැකි විය. නැවත උත්සාහ කිරීමට පෙර නවතම කියවීම පූරණය කරන්න.",
  conflict: "මෙම කියවීම වෙනස් වී ඇත. ඔබගේ පරීක්ෂා තේරීම් රඳවා ඇත.",
  denied: "මෙම ක්‍රියාව කිරීමට ඔබගේ ගිණුමට අවසර නැත.",
  expired: "ඔබගේ සැසිය අවසන් වී ඇත. ඉදිරියට යාමට නැවත පිවිසෙන්න.",
  limited:
    "නැවත උත්සාහ කිරීමට පෙර රැඳී සිටින්න. කියවීමේ ඉල්ලීම ස්වයංක්‍රීයව නැවත යවන්නේ නැත.",
  invalidPage: "මෙම මූලාශ්‍රයේ වලංගු පිටු අංකයක් තෝරන්න.",
  leave:
    "සුරැකී නැති මෙම පරීක්ෂාව අත්හැර පිටුවෙන් ඉවත් වන්නද? අවලංගු කිරීමෙන් ඔබගේ වැඩ රඳවා ගනී.",
  working: "පරීක්ෂාව සුරකිමින්…",
  nav: "පිටු අතර ගමන් කිරීම",
  analyzeReason: "මෙම මුල් පිටුව නැවත කියවීමට ඉල්ලා සිටියේය.",
};
const primary = cn(
  viewerButtonClass,
  "border-slate-950 bg-slate-950 text-white hover:bg-slate-800 disabled:border-slate-300 disabled:bg-slate-200 disabled:text-slate-600",
);
const inputClass =
  "w-full rounded-lg border border-slate-400 bg-white p-2 text-slate-950 outline-none focus-visible:ring-2 focus-visible:ring-amber-600";

function problemFor(status: number): Problem {
  return status === 401
    ? "expired"
    : status === 403
      ? "denied"
      : status === 409
        ? "conflict"
        : status === 429
          ? "limited"
          : "requestError";
}
function blankDraft(
  candidate: Candidate,
  version: number,
  reason = "",
): VerifyDraft {
  return {
    mode: "verify",
    candidate,
    version,
    reason,
    compared: false,
    allDetails: false,
    resolved: [],
  };
}
function toggle(values: string[], key: string, checked: boolean) {
  return checked
    ? [...values.filter((value) => value !== key), key]
    : values.filter((value) => value !== key);
}

export function SourceUnderstandingReview({
  documentId,
  role,
  initialPageNumber = 1,
}: {
  documentId: string;
  role: AdminRole;
  initialPageNumber?: number;
}) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const savedLanguage = useSyncExternalStore(
    subscribeReviewLanguage,
    savedReviewLanguage,
    () => null,
  );
  const [chosenLanguage, setChosenLanguage] = useState<ReviewLanguage | null>(
    null,
  );
  const [pageNumber, setPageNumber] = useState(initialPageNumber);
  const [pageInput, setPageInput] = useState(String(initialPageNumber));
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [draft, setDraftState] = useState<Draft | null>(null);
  const draftRef = useRef<Draft | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [conflict, setConflict] = useState(false);
  const [loading, setLoading] = useState(true);
  const [fresh, setFresh] = useState(false);
  const [busy, setBusy] = useState<"analysis" | Draft["mode"] | null>(null);
  const formId = useId();
  const [imageStatus, setImageStatus] = useState<{
    url: string;
    ready: boolean;
  } | null>(null);
  const [pendingAnalysis, setPendingAnalysisState] = useState<CreateJob | null>(
    null,
  );
  const pendingAnalysisRef = useRef<CreateJob | null>(null);
  const setPendingAnalysis = useCallback((value: CreateJob | null) => {
    pendingAnalysisRef.current = value;
    setPendingAnalysisState(value);
  }, []);
  const loadSequence = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const setDraft = useCallback((value: Draft | null) => {
    draftRef.current = value;
    setDraftState(value);
  }, []);
  const earlierLanguage = workspace?.page?.language;
  const knownPageLanguage =
    earlierLanguage && ["si", "en", "ta"].includes(earlierLanguage)
      ? earlierLanguage
      : snapshot?.candidate?.content.observation.language;
  const mixedSinhala =
    (!knownPageLanguage ||
      ["mixed", "mul", "und"].includes(knownPageLanguage)) &&
    snapshot?.candidate?.content.observation.regions.some((region) =>
      /[\u0d80-\u0dff]/u.test(region.exact_text),
    );
  const automaticLanguage =
    knownPageLanguage === "si" || mixedSinhala
      ? "si"
      : knownPageLanguage === "en" || knownPageLanguage === "ta"
        ? "en"
        : workspace?.language === "si"
          ? "si"
          : "en";
  const language = chosenLanguage ?? savedLanguage ?? automaticLanguage;
  const copy = language === "si" ? sinhala : english;
  const navigation = sourceViewerCopy(language);

  const load = useCallback(
    async (page: number) => {
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      const sequence = ++loadSequence.current;
      try {
        const [original, understood] = await Promise.all([
          api.GET("/api/v1/admin/materials/{document_id}/review-workspace", {
            params: {
              path: { document_id: documentId },
              query: { page_number: page },
            },
            signal: controller.signal,
          }),
          api.GET(
            "/api/v1/admin/materials/{document_id}/pages/{page_number}/understanding",
            {
              params: { path: { document_id: documentId, page_number: page } },
              signal: controller.signal,
            },
          ),
        ]);
        if (sequence !== loadSequence.current || controller.signal.aborted)
          return;
        if (!original.data || !understood.data) {
          const status = !original.data
            ? original.response.status
            : understood.response.status;
          setProblem(
            [401, 403, 429].includes(status) ? problemFor(status) : "loadError",
          );
          if (draftRef.current) setConflict(true);
          return;
        }
        const incoming = understood.data;
        if (
          draftRef.current &&
          (draftRef.current.version !== incoming.version ||
            draftRef.current.candidate?.id !== incoming.candidate?.id)
        )
          setConflict(true);
        if (
          pendingAnalysisRef.current &&
          incoming.version > pendingAnalysisRef.current.expected_version
        )
          setPendingAnalysis(null);
        setWorkspace(original.data);
        setSnapshot(incoming);
        setProblem(null);
        setFresh(true);
      } catch {
        if (sequence !== loadSequence.current || controller.signal.aborted)
          return;
        setProblem("loadError");
        if (draftRef.current) setConflict(true);
      } finally {
        if (sequence === loadSequence.current && !controller.signal.aborted)
          setLoading(false);
      }
    },
    [api, documentId, setPendingAnalysis],
  );
  const refresh = useCallback(
    (page: number) => {
      setLoading(true);
      setFresh(false);
      return load(page);
    },
    [load],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(pageNumber), 0);
    return () => {
      window.clearTimeout(timer);
      abort.current?.abort();
    };
  }, [refresh, pageNumber]);
  useEffect(() => {
    if (!snapshot?.active_job_id || problem || busy) return;
    const timer = window.setTimeout(() => void refresh(pageNumber), 1500);
    return () => window.clearTimeout(timer);
  }, [snapshot, problem, busy, refresh, pageNumber]);
  useEffect(() => {
    if (!draft) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [draft]);

  const candidate = draft?.candidate ?? snapshot?.candidate ?? null;
  const shownPage = snapshot?.page_number ?? pageNumber;
  const shownDocument = snapshot?.document_id ?? documentId;
  const previewUrl = candidate
    ? `/api/v1/admin/materials/${shownDocument}/pages/${shownPage}/understanding/candidates/${candidate.id}/image`
    : `/api/v1/admin/materials/${shownDocument}/pages/${shownPage}/image`;
  const imageReady = imageStatus?.url === previewUrl && imageStatus.ready;
  const imageState = useCallback(
    (ready: boolean) => {
      setImageStatus({ url: previewUrl, ready });
      if (
        !ready &&
        draftRef.current?.mode === "verify" &&
        draftRef.current.compared
      )
        setDraft({ ...draftRef.current, compared: false });
    },
    [previewUrl, setDraft],
  );
  const blocked =
    !snapshot?.report ||
    snapshot.source_status === "source_fidelity_failed" ||
    snapshot.machine?.state === "needs_attention" ||
    snapshot.report.findings.some((finding) => finding.severity === "block");
  const recoveredReadable = fresh && snapshot?.machine?.text_readable === true;
  const showSourceCandidate = !blocked || recoveredReadable;
  const writable = role === "admin" && workspace?.source_active === true;
  const working = !!snapshot?.active_job_id;
  const failedFreshReading =
    snapshot?.state === "needs_reprocessing" &&
    !!snapshot.latest_job &&
    ["failed", "unknown"].includes(snapshot.latest_job.status);
  const mayReview =
    writable &&
    !!snapshot?.candidate &&
    !blocked &&
    !working &&
    !snapshot.verified_source &&
    snapshot.source_status === "source_fidelity_needs_review" &&
    ["needs_human_review", "corrected"].includes(snapshot.state);
  const mayCorrect =
    writable &&
    !!snapshot?.candidate &&
    !working &&
    snapshot.state !== "excluded";
  const canConfirm =
    draft?.mode === "verify" &&
    mayReview &&
    imageReady &&
    draft.compared &&
    draft.allDetails &&
    draft.reason.trim().length > 0 &&
    draft.candidate.content.uncertainties.every((item) =>
      draft.resolved.includes(item.key),
    ) &&
    !conflict &&
    fresh &&
    !loading &&
    !busy;

  const canSaveCorrection =
    draft?.mode === "correct" &&
    mayCorrect &&
    imageReady &&
    draft.reason.trim().length > 0 &&
    correctionIsComplete(draft.content) &&
    JSON.stringify(draft.content) !== JSON.stringify(draft.candidate.content) &&
    !conflict &&
    fresh &&
    !loading &&
    !busy;
  const canChangeLifecycle =
    !!draft &&
    (draft.mode === "exclude" || draft.mode === "reopen") &&
    writable &&
    !working &&
    draft.confirmed &&
    draft.reason.trim().length > 0 &&
    (draft.mode === "reopen") === (snapshot?.state === "excluded") &&
    !conflict &&
    fresh &&
    !loading &&
    !busy;

  function chooseLanguage(value: ReviewLanguage) {
    setChosenLanguage(value);
    try {
      window.localStorage.setItem(reviewLanguageKey, value);
    } catch {}
  }
  function navigate(target: number) {
    if (
      busy ||
      loading ||
      !Number.isSafeInteger(target) ||
      target === pageNumber ||
      target < 1 ||
      target > (workspace?.progress.total_pages ?? 1)
    )
      return;
    if (draft && !window.confirm(copy.leave)) return;
    setDraft(null);
    setConflict(false);
    setImageStatus(null);
    setLoading(true);
    setFresh(false);
    setPageInput(String(target));
    setPageNumber(target);
  }
  function jump(event: FormEvent) {
    event.preventDefault();
    const value = /^\d+$/.test(pageInput) ? Number(pageInput) : NaN;
    if (
      !Number.isSafeInteger(value) ||
      value < 1 ||
      value > (workspace?.progress.total_pages ?? 1)
    ) {
      setProblem("invalidPage");
      return;
    }
    navigate(value);
  }
  async function analyze() {
    if (
      !snapshot ||
      !writable ||
      !snapshot.provider_available ||
      working ||
      draft ||
      busy ||
      loading ||
      !fresh
    )
      return;
    const body = pendingAnalysisRef.current ?? {
      request_id: crypto.randomUUID(),
      expected_version: snapshot.version,
      reason: copy.analyzeReason,
    };
    setPendingAnalysis(body);
    setBusy("analysis");
    setProblem(null);
    try {
      const result = await api.POST(
        "/api/v1/admin/materials/{document_id}/pages/{page_number}/understanding/jobs",
        {
          params: {
            path: { document_id: documentId, page_number: pageNumber },
          },
          body,
        },
      );
      if (!result.response.ok || !result.data) {
        setProblem(problemFor(result.response.status));
        setFresh(false);
        return;
      }
      setPendingAnalysis(null);
      await refresh(pageNumber);
    } catch {
      setProblem("requestError");
      setFresh(false);
    } finally {
      setBusy(null);
    }
  }
  async function submitReview(
    mode: Draft["mode"],
    operation: () => Promise<{ response: Response; data?: unknown }>,
  ) {
    setBusy(mode);
    setProblem(null);
    try {
      const result = await operation();
      if (!result.response.ok || !result.data) {
        setProblem(problemFor(result.response.status));
        setConflict(true);
        setFresh(false);
        return;
      }
      setDraft(null);
      setConflict(false);
      await refresh(pageNumber);
    } catch {
      setProblem("requestError");
      setConflict(true);
      setFresh(false);
    } finally {
      setBusy(null);
    }
  }
  async function confirm() {
    if (!canConfirm || draft?.mode !== "verify") return;
    const body: Verify = {
      candidate_id: draft.candidate.id,
      expected_version: draft.version,
      compared_with_original: true,
      reviewed_region_keys: draft.candidate.content.observation.regions.map(
        (region) => region.key,
      ),
      resolved_uncertainty_keys: draft.candidate.content.uncertainties
        .filter((item) => draft.resolved.includes(item.key))
        .map((item) => item.key),
      reason: draft.reason,
    };
    await submitReview("verify", () =>
      api.POST(
        "/api/v1/admin/materials/{document_id}/pages/{page_number}/understanding/verify",
        {
          params: {
            path: { document_id: documentId, page_number: pageNumber },
          },
          body,
        },
      ),
    );
  }
  async function saveCorrection() {
    if (!canSaveCorrection || draft?.mode !== "correct") return;
    const body: Correction = {
      parent_candidate_id: draft.candidate.id,
      request_id: draft.requestId,
      expected_version: draft.version,
      content: {
        schema_version: "source-read-candidate.v1",
        observation: draft.content.observation,
        uncertainties: draft.content.uncertainties,
      },
      reason: draft.reason,
    };
    await submitReview("correct", () =>
      api.POST(
        "/api/v1/admin/materials/{document_id}/pages/{page_number}/understanding/corrections",
        {
          params: {
            path: { document_id: documentId, page_number: pageNumber },
          },
          body,
        },
      ),
    );
  }
  async function changeLifecycle() {
    if (
      !canChangeLifecycle ||
      !draft ||
      (draft.mode !== "exclude" && draft.mode !== "reopen")
    )
      return;
    const path = { document_id: documentId, page_number: pageNumber };
    if (draft.mode === "exclude") {
      const body = {
        expected_version: draft.version,
        reason: draft.reason,
        confirm_exclusion: true as const,
      };
      await submitReview("exclude", () =>
        api.POST(
          "/api/v1/admin/materials/{document_id}/pages/{page_number}/understanding/exclude",
          { params: { path }, body },
        ),
      );
    } else {
      const body = {
        expected_version: draft.version,
        reason: draft.reason,
        confirm_reopen: true as const,
      };
      await submitReview("reopen", () =>
        api.POST(
          "/api/v1/admin/materials/{document_id}/pages/{page_number}/understanding/reopen",
          { params: { path }, body },
        ),
      );
    }
  }
  function beginCorrection() {
    if (!snapshot?.candidate || !mayCorrect || !imageReady || loading || busy)
      return;
    setDraft({
      mode: "correct",
      candidate: snapshot.candidate,
      version: snapshot.version,
      content: { ...snapshot.candidate.content, education: { claims: [] } },
      reason: "",
      requestId: crypto.randomUUID(),
    });
  }
  function beginLifecycle(mode: "exclude" | "reopen") {
    if (!snapshot || !writable || working || loading || busy) return;
    setDraft({
      mode,
      candidate: snapshot.candidate,
      version: snapshot.version,
      reason: "",
      confirmed: false,
    });
  }
  function useLatest() {
    if (!fresh || !snapshot || loading || busy) return;
    if (draft?.mode === "correct") {
      if (!snapshot.candidate || snapshot.state === "excluded") return;
      setDraft({
        ...draft,
        candidate: snapshot.candidate,
        version: snapshot.version,
        requestId: crypto.randomUUID(),
      });
    } else if (draft?.mode === "exclude" || draft?.mode === "reopen") {
      setDraft(
        (draft.mode === "reopen") === (snapshot.state === "excluded")
          ? {
              ...draft,
              candidate: snapshot.candidate,
              version: snapshot.version,
              confirmed: false,
            }
          : null,
      );
    } else {
      setDraft(
        snapshot.verified_source != null ||
          snapshot.state === "verified" ||
          snapshot.state === "excluded" ||
          !snapshot.candidate
          ? null
          : blankDraft(snapshot.candidate, snapshot.version, draft?.reason),
      );
    }
    setConflict(false);
    setProblem(null);
    imageState(false);
  }

  return (
    <section
      lang={language}
      className="flex min-h-0 flex-1 flex-col px-4 pb-4 sm:px-6"
    >
      <header className="shrink-0 space-y-3 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <Link
            className="text-sm font-semibold underline"
            href={`/admin/materials/${documentId}`}
            onClick={(event) => {
              if (draft && !window.confirm(copy.leave)) event.preventDefault();
            }}
          >
            {copy.back}
          </Link>
          <div className="ml-auto flex gap-2">
            <Button
              className={viewerButtonClass}
              aria-pressed={language === "si"}
              onPress={() => chooseLanguage("si")}
            >
              සිංහල
            </Button>
            <Button
              className={viewerButtonClass}
              aria-pressed={language === "en"}
              onPress={() => chooseLanguage("en")}
            >
              English
            </Button>
          </div>
        </div>
        <h1 className="text-xl font-semibold">{copy.title}</h1>
        {workspace && (
          <p className="truncate font-semibold">{workspace.document_title}</p>
        )}
        <p className="text-sm text-slate-700">{copy.intro}</p>
        {role === "reviewer" && <p className="text-sm">{copy.readonly}</p>}
        {workspace && !workspace.source_active && (
          <p role="alert" className="text-sm text-amber-950">
            {copy.inactive}
          </p>
        )}
        <nav
          aria-label={copy.nav}
          className="flex flex-wrap items-center gap-2"
        >
          <Button
            className={viewerButtonClass}
            isDisabled={pageNumber <= 1 || loading || !!busy}
            onPress={() => navigate(1)}
          >
            {navigation.first}
          </Button>
          <Button
            className={viewerButtonClass}
            isDisabled={pageNumber <= 1 || loading || !!busy}
            onPress={() => navigate(pageNumber - 1)}
          >
            {navigation.previous}
          </Button>
          <form onSubmit={jump} className="flex items-center gap-2">
            <input
              aria-label={navigation.pageNumber}
              className={cn(inputClass, "w-20")}
              type="number"
              min={1}
              max={workspace?.progress.total_pages || 1}
              value={pageInput}
              onChange={(event) => setPageInput(event.target.value)}
            />
            <span className="text-sm">
              {navigation.of} {workspace?.progress.total_pages ?? "—"}
            </span>
            <Button
              type="submit"
              className={viewerButtonClass}
              isDisabled={loading || !!busy}
            >
              {navigation.go}
            </Button>
          </form>
          <Button
            className={viewerButtonClass}
            isDisabled={
              !workspace ||
              pageNumber >= workspace.progress.total_pages ||
              loading ||
              !!busy
            }
            onPress={() => navigate(pageNumber + 1)}
          >
            {navigation.next}
          </Button>
          <Button
            className={viewerButtonClass}
            isDisabled={
              !workspace ||
              pageNumber >= workspace.progress.total_pages ||
              loading ||
              !!busy
            }
            onPress={() => navigate(workspace?.progress.total_pages ?? 1)}
          >
            {navigation.last}
          </Button>
          <Button
            className={viewerButtonClass}
            isDisabled={loading || !!busy}
            onPress={() => void refresh(pageNumber)}
          >
            {copy.refresh}
          </Button>
        </nav>
        {loading && <p role="status">{copy.loading}</p>}
        {problem && (
          <p
            role="alert"
            className="rounded-lg bg-amber-50 p-3 text-sm text-amber-950"
          >
            {copy[problem]}
          </p>
        )}
        {conflict && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
            {problem !== "conflict" && <p>{copy.conflict}</p>}
            <Button
              className={viewerButtonClass}
              isDisabled={
                !fresh ||
                loading ||
                !!busy ||
                (draft?.mode === "correct" && snapshot?.state === "excluded")
              }
              onPress={useLatest}
            >
              {draft?.mode === "correct" ? copy.rebaseCorrection : copy.latest}
            </Button>
          </div>
        )}
      </header>
      {snapshot && workspace && (
        <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-2">
          <OriginalPageViewer
            documentId={shownDocument}
            pageNumber={shownPage}
            previewUrl={previewUrl}
            language={language}
            onReady={imageState}
            className="h-full min-h-64"
            initialFit
          />
          <div className="min-h-0 space-y-4 overflow-auto overscroll-contain rounded-lg border border-slate-300 bg-white p-4">
            {working && (
              <p role="status" className="font-semibold">
                {copy.analyzing}
              </p>
            )}
            {snapshot.state === "excluded" && (
              <div
                className="rounded-lg border border-amber-400 bg-amber-50 p-3 text-amber-950"
                role="status"
              >
                <p className="font-semibold">{copy.excluded}</p>
                {snapshot.exclusion && (
                  <p className="mt-2 text-sm">{snapshot.exclusion.reason}</p>
                )}
              </div>
            )}
            {!draft && snapshot.verified_source && (
              <p className="rounded-lg border border-emerald-400 bg-emerald-50 p-3 font-semibold">
                {copy.checked} · {language === "si" ? "අනුවාදය" : "Revision"}{" "}
                {snapshot.verified_source.revision}
              </p>
            )}
            {!draft && !working && !snapshot.verified_source && snapshot.machine && (
              <p role="status" className={cn("rounded-lg border p-3 font-semibold", snapshot.machine.state === "machine_ready" ? "border-emerald-400 bg-emerald-50 text-emerald-950" : "border-amber-400 bg-amber-50 text-amber-950")}>
                {snapshot.machine.state === "machine_ready" ? copy.machineReady : copy.machineAttention}
              </p>
            )}
            {blocked && recoveredReadable && !working && (
              <p className="font-semibold text-amber-950">{copy.recoveredSource}</p>
            )}
            {blocked && candidate && (
              <p
                role="alert"
                className="rounded-lg border border-red-300 bg-red-50 p-3 font-semibold text-red-950"
              >
                {copy.failedChecks}
              </p>
            )}
            {snapshot.latest_job &&
              ["failed", "unknown"].includes(snapshot.latest_job.status) && (
                <p
                  role="alert"
                  className="rounded-lg bg-amber-50 p-3 text-amber-950"
                >
                  {copy.failedAnalysis}
                </p>
              )}
            {draft?.mode === "correct" ||
            working ||
            (failedFreshReading && !draft) ? null : !draft &&
              snapshot.verified_source ? (
              <SourceUnderstandingContent
                understanding={{
                  schema_version: "page-understanding.v1",
                  observation: snapshot.verified_source.content.observation,
                  education: { claims: [] },
                  uncertainties: snapshot.verified_source.content.uncertainties,
                }}
                language={language}
                sourceOnly
                sourceVerified
              />
            ) : candidate && showSourceCandidate ? (
              <SourceUnderstandingContent
                understanding={candidate.content}
                language={language}
                sourceOnly
              />
            ) : (
              <p>{candidate && blocked ? copy.noReliableSource : copy.empty}</p>
            )}
            {!snapshot.provider_available && (
              <p className="text-sm text-slate-600">{copy.unavailable}</p>
            )}
            <p className="text-sm text-slate-600">{copy.metadata}</p>
            {draft?.mode === "correct" && (
              <form
                id={formId}
                className="space-y-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  void saveCorrection();
                }}
              >
                <SourceUnderstandingEditor
                  sourceOnly
                  value={draft.content}
                  language={language}
                  disabled={!!busy}
                  onChange={(content) =>
                    setDraft({
                      ...draft,
                      content,
                      requestId: crypto.randomUUID(),
                    })
                  }
                />
                <label className="block font-semibold">
                  {copy.correctionReason}
                  <textarea
                    className={cn(inputClass, "mt-2 min-h-24 font-normal")}
                    maxLength={2000}
                    value={draft.reason}
                    disabled={!!busy}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        reason: event.target.value,
                        requestId: crypto.randomUUID(),
                      })
                    }
                  />
                </label>
              </form>
            )}
            {(draft?.mode === "exclude" || draft?.mode === "reopen") && (
              <form
                id={formId}
                className="space-y-4 rounded-lg border border-amber-400 p-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  void changeLifecycle();
                }}
              >
                <p>
                  {draft.mode === "exclude"
                    ? copy.exclusionNotice
                    : copy.reopenNotice}
                </p>
                <label className="flex gap-2">
                  <input
                    type="checkbox"
                    checked={draft.confirmed}
                    disabled={!!busy}
                    onChange={(event) =>
                      setDraft({ ...draft, confirmed: event.target.checked })
                    }
                  />
                  {draft.mode === "exclude"
                    ? copy.excludeConsent
                    : copy.reopenConsent}
                </label>
                <label className="block font-semibold">
                  {draft.mode === "exclude"
                    ? copy.excludeReason
                    : copy.reopenReason}
                  <textarea
                    className={cn(inputClass, "mt-2 min-h-24 font-normal")}
                    maxLength={2000}
                    value={draft.reason}
                    disabled={!!busy}
                    onChange={(event) =>
                      setDraft({ ...draft, reason: event.target.value })
                    }
                  />
                </label>
              </form>
            )}
            {draft?.mode === "verify" && (
              <form
                id={formId}
                className="space-y-4 rounded-lg border border-slate-400 p-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  void confirm();
                }}
              >
                <label className="flex gap-2">
                  <input
                    type="checkbox"
                    checked={draft.compared}
                    disabled={!imageReady || !!busy}
                    onChange={(event) =>
                      setDraft({ ...draft, compared: event.target.checked })
                    }
                  />
                  {copy.compared}
                </label>
                <label className="flex gap-2">
                  <input
                    type="checkbox"
                    checked={draft.allDetails}
                    disabled={!imageReady || !!busy}
                    onChange={(event) =>
                      setDraft({ ...draft, allDetails: event.target.checked })
                    }
                  />
                  {copy.allDetails}
                </label>
                {!!draft.candidate.content.uncertainties.length && (
                  <fieldset className="space-y-2">
                    <legend className="font-semibold">
                      {copy.uncertainty}
                    </legend>
                    {draft.candidate.content.uncertainties.map((item) => (
                      <label className="flex gap-2" key={item.key}>
                        <input
                          type="checkbox"
                          checked={draft.resolved.includes(item.key)}
                          disabled={!!busy}
                          onChange={(event) =>
                            setDraft({
                              ...draft,
                              resolved: toggle(
                                draft.resolved,
                                item.key,
                                event.target.checked,
                              ),
                            })
                          }
                        />
                        {item.reason}
                      </label>
                    ))}
                  </fieldset>
                )}
                <label className="block font-semibold">
                  {copy.reason}
                  <textarea
                    className={cn(inputClass, "mt-2 min-h-24 font-normal")}
                    maxLength={2000}
                    value={draft.reason}
                    disabled={!!busy}
                    onChange={(event) =>
                      setDraft({ ...draft, reason: event.target.value })
                    }
                  />
                </label>
              </form>
            )}
            <details className="rounded-lg border border-slate-300 p-3 text-sm">
              <summary className="cursor-pointer font-semibold">
                {copy.technical}
              </summary>
              <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words">
                {JSON.stringify(
                  {
                    candidate: snapshot.candidate
                      ? {
                          ...snapshot.candidate,
                          content: {
                            ...snapshot.candidate.content,
                            education: { claims: [] },
                          },
                        }
                      : null,
                    report: snapshot.report,
                    job: snapshot.latest_job,
                    provider_completed: snapshot.provider_completed,
                    machine: snapshot.machine,
                    source_status: snapshot.source_status,
                    verified_source: snapshot.verified_source,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
          </div>
        </div>
      )}
      <footer className="flex shrink-0 flex-wrap items-center gap-3 border-t border-slate-300 py-3">
        {draft ? (
          <>
            <Button
              type="submit"
              form={formId}
              className={primary}
              isDisabled={
                !(draft.mode === "verify"
                  ? canConfirm
                  : draft.mode === "correct"
                    ? canSaveCorrection
                    : canChangeLifecycle)
              }
            >
              {busy
                ? copy.working
                : draft.mode === "verify"
                  ? copy.confirm
                  : draft.mode === "correct"
                    ? copy.saveCorrection
                    : draft.mode === "exclude"
                      ? copy.excludeSubmit
                      : copy.reopenSubmit}
            </Button>
            <Button
              className={viewerButtonClass}
              isDisabled={!!busy}
              onPress={() => {
                setDraft(null);
                setConflict(false);
              }}
            >
              {draft.mode === "verify" ? copy.cancel : copy.cancelAction}
            </Button>
          </>
        ) : (
          <>
            {snapshot?.candidate && (
              <Button
                className={blocked ? viewerButtonClass : primary}
                isDisabled={!mayReview || !imageReady || loading || !!busy}
                onPress={() => {
                  if (snapshot.candidate)
                    setDraft(blankDraft(snapshot.candidate, snapshot.version));
                }}
              >
                {copy.review}
              </Button>
            )}
            {snapshot?.candidate && snapshot.state !== "excluded" && (
              <Button
                className={viewerButtonClass}
                isDisabled={
                  !mayCorrect || !imageReady || loading || !!busy || !fresh
                }
                onPress={beginCorrection}
              >
                {copy.correct}
              </Button>
            )}
            {role === "admin" && snapshot && (
              <Button
                className={viewerButtonClass}
                isDisabled={!writable || working || loading || !!busy || !fresh}
                onPress={() =>
                  beginLifecycle(
                    snapshot.state === "excluded" ? "reopen" : "exclude",
                  )
                }
              >
                {snapshot.state === "excluded" ? copy.reopen : copy.exclude}
              </Button>
            )}
            {role === "admin" &&
              snapshot?.state !== "verified" &&
              snapshot?.state !== "excluded" && (
                <Button
                  className={
                    snapshot?.candidate && !blocked
                      ? viewerButtonClass
                      : primary
                  }
                  isDisabled={
                    !writable ||
                    !snapshot?.provider_available ||
                    working ||
                    loading ||
                    !!busy ||
                    !fresh
                  }
                  onPress={() => void analyze()}
                >
                  {pendingAnalysis
                    ? copy.retryAnalysis
                    : snapshot?.candidate
                      ? copy.analyzeAgain
                      : copy.analyze}
                </Button>
              )}
          </>
        )}
      </footer>
    </section>
  );
}
