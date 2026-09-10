"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Image from "next/image";
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
import {
  Button,
  Dialog,
  Form,
  Heading,
  Modal,
  ModalOverlay,
} from "react-aria-components";

import { cn } from "@/lib/utils";

import type { AdminRole } from "./admin-header";

type Workspace = components["schemas"]["PageReviewWorkspaceResponse"];
type Page = components["schemas"]["PageReviewView"];
type ConfirmRequest = components["schemas"]["PageConfirmRequest"];
type EditRequest = components["schemas"]["PageEditRequest"];
type ExcludeRequest = components["schemas"]["PageExcludeRequest"];
type RereadRequest = components["schemas"]["PageRereadRequest"];
type ReadJob = components["schemas"]["SourceReadJobResponse"];
type Action = "confirm" | "edit" | "exclude" | "reread" | "read";
type Draft = { text: string; reason: string; expectedVersion: number };
type Problem = { status: number; code: string };
type Props = {
  documentId: string;
  role: AdminRole;
  initialPageNumber?: number;
  benchmarkId?: string;
};

const buttonClass =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-950 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const primaryButton = cn(
  buttonClass,
  "border-slate-950 bg-slate-950 text-white hover:bg-slate-800 disabled:border-slate-300 disabled:bg-slate-200 disabled:text-slate-600 disabled:opacity-100",
);
const inputClass =
  "rounded-lg border border-slate-400 bg-white px-3 py-2 text-slate-950 outline-none focus-visible:ring-2 focus-visible:ring-amber-600 disabled:opacity-60";
const alertClass =
  "rounded-lg border border-amber-400 bg-amber-50 p-3 text-sm text-amber-950";

const english = {
  title: "Check the system-read text",
  instruction:
    "Compare the original page on the left with the system-read text on the right.",
  original: "Original page",
  systemText: "System-read text",
  suspicious: "The text on this page appears to have been read incorrectly.",
  confirmAction: "Text is correct",
  rereadAction: "Read again",
  editAction: "Correct the text",
  excludeAction: "Do not use this page",
  technical: "Technical details",
  back: "Back to material",
  backQueue: "Back to review queue",
  reviewer: "Reviewer access is read-only.",
  progress: "Review progress",
  total: "Total pages",
  processed: "Processed pages",
  verified: "Verified pages",
  excluded: "Excluded pages",
  flagged: "Flagged pages",
  remaining: "Remaining pages",
  ready: "Ready for AI",
  notReady: "Needs review before AI use",
  metadata:
    "Material details still need review. Page confirmation alone does not make the material ready for use.",
  inactive:
    "This material is removed from use. Reviewing a page does not restore it.",
  empty: "No pages are available for comparison yet.",
  readDocument: "Read document",
  readingDocument: "Reading the document…",
  documentReadFailed:
    "The document could not be fully read. Refresh the status or try reading it again. No page has been automatically confirmed.",
  loading: "Loading page…",
  loadAgain: "Try loading again",
  comparison: "Original and system text comparison",
  nav: "Page navigation",
  first: "First page",
  previous: "Previous page",
  next: "Next page",
  last: "Last page",
  previousFlagged: "Previous flagged page",
  nextFlagged: "Next flagged page",
  pageNumber: "Page number",
  go: "Go to page",
  of: "of",
  invalidPage: "Choose a whole page number from 1 to {total}.",
  refresh: "Refresh page status",
  actions: "Page actions",
  save: "Save correction",
  cancelEditing: "Cancel editing",
  correction: "Correction",
  correctionReason: "Reason for correction",
  notVerified: "Not yet checked against the original.",
  verifiedState: "Confirmed against the original.",
  excludedState: "Excluded from use. Its history is preserved.",
  processing: "Reading this page…",
  failed:
    "The page could not be read. Try again or correct the text against the original.",
  unconfirmable:
    "Read the page again or correct the text against the original. The unsuccessful reading is kept in Technical details and cannot be confirmed.",
  unsuccessfulText: "Unsuccessful reading (not verified)",
  recoveredText: "Re-read text — not ready for confirmation",
  recoveryWarning:
    "This recovered text is readable, but it has not passed all checks against the original. Numbers, symbols and tables may still be incorrect. Read again or correct the text.",
  previewLoading: "Loading original page…",
  previewError:
    "The original page could not be loaded. Try the image again before confirming the text.",
  imageRetry: "Try image again",
  zoom: "Page zoom",
  zoomIn: "Zoom in",
  zoomOut: "Zoom out",
  zoomReset: "Reset zoom",
  openOriginal: "Open original page",
  confirmTitle: "Confirm this page",
  confirmDescription:
    "Only confirm after comparing every line, number and symbol with the original. This records a human-confirmed reference, not an automatic reading score.",
  compared: "I compared this text with the original page.",
  confirmReason: "Compared this text with the original page.",
  confirmSubmit: "Confirm compared text",
  excludeTitle: "Exclude this page from use?",
  excludeDescription:
    "The original page, past readings and review history are kept. The page is not deleted.",
  exclusionAgreement: "I understand this page will not be used.",
  excludeReason: "Reason for not using this page",
  excludeSubmit: "Exclude page",
  cancel: "Cancel",
  saved: "Correction saved. Compare it with the original before confirming.",
  leave:
    "Discard this unsaved correction and leave the page? Choose Cancel to keep it.",
  conflict:
    "This page changed in another session. Your correction and reason have been kept. Reload the latest page before trying again.",
  blocked:
    "This page cannot be confirmed in its current state. Reload the page and compare it again. Your work has been kept.",
  networkError:
    "The connection was interrupted. Your work has been kept. Reload the page status before retrying; the request may already have reached the server.",
  requestError:
    "The request could not be completed. Your work has been kept. Refresh the page status before trying again.",
  expired: "Your session has expired. Sign in again before retrying.",
  denied: "Your account does not have permission for this action.",
  notFound:
    "This material or page could not be found. Check the selected material and try again.",
  invalidRequest:
    "The request was not accepted. Check the text and the required reason before retrying.",
  reloadKeeping: "Reload latest page, keeping correction",
  latest: "Latest system text",
  useLatest: "Use this version for my correction",
  working: "Sending request…",
  history: "Reading history",
  provenance: "Source provenance",
  diagnostics: "Reading diagnostics",
};

type Copy = Record<keyof typeof english, string>;

const sinhala: Copy = {
  title: "පද්ධතිය කියවූ පෙළ පරීක්ෂා කරන්න",
  instruction:
    "වම් පැත්තේ මුල් පිටුවත්, දකුණු පැත්තේ පද්ධතිය කියවූ පෙළත් සසඳන්න.",
  original: "මුල් පිටුව",
  systemText: "පද්ධතිය කියවූ පෙළ",
  suspicious: "මෙම පිටුවේ පෙළ නිවැරදිව කියවී නොමැති බව පෙනේ.",
  confirmAction: "පෙළ නිවැරදියි",
  rereadAction: "නැවත කියවන්න",
  editAction: "පෙළ නිවැරදි කරන්න",
  excludeAction: "මෙම පිටුව භාවිත නොකරන්න",
  technical: "තාක්ෂණික විස්තර",
  back: "මූලාශ්‍රය වෙත ආපසු",
  backQueue: "පරීක්ෂා කළ යුතු පිටු වෙත ආපසු",
  reviewer: "ඔබට මෙම පිටුව බැලිය හැකි නමුත් වෙනස් කළ නොහැක.",
  progress: "පරීක්ෂාවේ ප්‍රගතිය",
  total: "මුළු පිටු",
  processed: "කියවූ පිටු",
  verified: "තහවුරු කළ පිටු",
  excluded: "භාවිතයෙන් ඉවත් කළ පිටු",
  flagged: "අවධානය අවශ්‍ය පිටු",
  remaining: "ඉතිරි පිටු",
  ready: "AI භාවිතයට සූදානම්",
  notReady: "AI භාවිතයට පෙර පරීක්ෂා කළ යුතුයි",
  metadata:
    "මූලාශ්‍රයේ විස්තර තවමත් පරීක්ෂා කළ යුතුයි. පිටුවක් තහවුරු කිරීම පමණක් ප්‍රමාණවත් නොවේ.",
  inactive:
    "මෙම මූලාශ්‍රය භාවිතයෙන් ඉවත් කර ඇත. පිටුවක් පරීක්ෂා කිරීමෙන් එය නැවත භාවිතයට එක් නොවේ.",
  empty: "සැසඳීමට පිටු තවමත් නොමැත.",
  readDocument: "මූලාශ්‍රය කියවන්න",
  readingDocument: "මූලාශ්‍රය කියවමින් පවතී…",
  documentReadFailed:
    "මූලාශ්‍රය සම්පූර්ණයෙන් කියවීමට නොහැකි විය. තත්ත්වය යාවත්කාලීන කරන්න හෝ නැවත කියවන්න. කිසිදු පිටුවක් ස්වයංක්‍රීයව තහවුරු කර නැත.",
  loading: "පිටුව පූරණය වෙමින් පවතී…",
  loadAgain: "නැවත පූරණය කරන්න",
  comparison: "මුල් පිටුව සහ පද්ධතිය කියවූ පෙළ සැසඳීම",
  nav: "පිටු අතර ගමන් කිරීම",
  first: "පළමු පිටුව",
  previous: "පෙර පිටුව",
  next: "ඊළඟ පිටුව",
  last: "අවසාන පිටුව",
  previousFlagged: "අවධානය අවශ්‍ය පෙර පිටුව",
  nextFlagged: "අවධානය අවශ්‍ය ඊළඟ පිටුව",
  pageNumber: "පිටු අංකය",
  go: "පිටුවට යන්න",
  of: "/",
  invalidPage: "1 සිට {total} දක්වා සම්පූර්ණ පිටු අංකයක් තෝරන්න.",
  refresh: "පිටුවේ තත්ත්වය යාවත්කාලීන කරන්න",
  actions: "පිටුව සඳහා ක්‍රියා",
  save: "නිවැරදි කළ පෙළ සුරකින්න",
  cancelEditing: "සංස්කරණය අවලංගු කරන්න",
  correction: "නිවැරදි කළ පෙළ",
  correctionReason: "නිවැරදි කිරීමට හේතුව",
  notVerified: "මුල් පිටුව සමඟ සසඳා තවමත් තහවුරු කර නැත.",
  verifiedState: "මුල් පිටුව සමඟ සසඳා තහවුරු කර ඇත.",
  excludedState: "භාවිතයෙන් ඉවත් කර ඇත. ඉතිහාසය සුරැකී ඇත.",
  processing: "මෙම පිටුව කියවමින් පවතී…",
  failed: "මෙම පිටුවේ පෙළ නිවැරදිව කියවී නොමැත.",
  unconfirmable:
    "පිටුව නැවත කියවන්න හෝ මුල් පිටුව සමඟ සසඳා පෙළ නිවැරදි කරන්න. සාර්ථක නොවූ කියවීම තාක්ෂණික විස්තර යටතේ සුරැකී ඇත. එය තහවුරු කළ නොහැක.",
  unsuccessfulText: "සාර්ථක නොවූ කියවීම (තහවුරු කර නැත)",
  recoveredText: "නැවත කියවූ පෙළ — තහවුරු කිරීමට සූදානම් නැත",
  recoveryWarning:
    "නැවත ලබාගත් මෙම පෙළ කියවිය හැකි නමුත් මුල් පිටුව සමඟ කළ සියලු පරීක්ෂා සමත් වී නැත. අංක, සංකේත සහ වගු තවමත් වැරදි විය හැක. නැවත කියවන්න හෝ පෙළ නිවැරදි කරන්න.",
  previewLoading: "මුල් පිටුව පූරණය වෙමින් පවතී…",
  previewError:
    "මුල් පිටුව පූරණය කළ නොහැකි විය. පෙළ තහවුරු කිරීමට පෙර පිටුව නැවත පූරණය කරන්න.",
  imageRetry: "පිටුවේ රූපය නැවත පූරණය කරන්න",
  zoom: "පිටුවේ විශාලත්වය",
  zoomIn: "විශාල කරන්න",
  zoomOut: "කුඩා කරන්න",
  zoomReset: "මුල් විශාලත්වයට යන්න",
  openOriginal: "මුල් පිටුව විවෘත කරන්න",
  confirmTitle: "මෙම පිටුව තහවුරු කරන්න",
  confirmDescription:
    "සෑම පේළියක්ම, අංකයක්ම සහ සංකේතයක්ම මුල් පිටුව සමඟ සසඳා පමණක් තහවුරු කරන්න. මෙය ඔබ පරීක්ෂා කළ මූලාශ්‍ර පෙළ ලෙස සුරැකේ.",
  compared: "මම මෙම පෙළ මුල් පිටුව සමඟ සැසඳුවෙමි.",
  confirmReason: "මෙම පෙළ මුල් පිටුව සමඟ සැසඳුවෙමි.",
  confirmSubmit: "සැසඳූ පෙළ තහවුරු කරන්න",
  excludeTitle: "මෙම පිටුව භාවිතයෙන් ඉවත් කරන්නද?",
  excludeDescription:
    "මුල් පිටුව, පෙර කියවීම් සහ පරීක්ෂා ඉතිහාසය සුරැකේ. පිටුව මකා නොදමයි.",
  exclusionAgreement: "මෙම පිටුව භාවිත නොකරන බව මට වැටහේ.",
  excludeReason: "මෙම පිටුව භාවිත නොකිරීමට හේතුව",
  excludeSubmit: "පිටුව භාවිතයෙන් ඉවත් කරන්න",
  cancel: "අවලංගු කරන්න",
  saved: "නිවැරදි කළ පෙළ සුරැකුණි. තහවුරු කිරීමට පෙර එය මුල් පිටුව සමඟ සසඳන්න.",
  leave:
    "සුරැකී නැති නිවැරදි කිරීම ඉවත් කර මෙම පිටුවෙන් පිටවන්නද? එය තබා ගැනීමට අවලංගු කරන්න.",
  conflict:
    "වෙනත් සැසියක මෙම පිටුව වෙනස් වී ඇත. ඔබේ පෙළ සහ හේතුව සුරැකී ඇත. නැවත උත්සාහ කිරීමට පෙර නවතම පිටුව පූරණය කරන්න.",
  blocked:
    "මෙම තත්ත්වයේදී පිටුව තහවුරු කළ නොහැක. පිටුව නැවත පූරණය කර සසඳන්න. ඔබේ වැඩ මෙහි තබා ඇත.",
  networkError:
    "සම්බන්ධතාව බිඳ වැටුණි. ඔබේ වැඩ මෙහි තබා ඇත. ඉල්ලීම දැනටමත් ලැබී තිබිය හැකි බැවින් නැවත උත්සාහ කිරීමට පෙර පිටුවේ තත්ත්වය යාවත්කාලීන කරන්න.",
  requestError:
    "ඉල්ලීම සම්පූර්ණ කළ නොහැකි විය. ඔබේ වැඩ මෙහි තබා ඇත. නැවත උත්සාහ කිරීමට පෙර පිටුවේ තත්ත්වය යාවත්කාලීන කරන්න.",
  expired: "ඔබේ සැසිය අවසන් වී ඇත. නැවත ඇතුළු වී උත්සාහ කරන්න.",
  denied: "මෙම ක්‍රියාව කිරීමට ඔබේ ගිණුමට අවසර නැත.",
  notFound:
    "මෙම මූලාශ්‍රය හෝ පිටුව සොයාගත නොහැකි විය. තෝරාගත් මූලාශ්‍රය පරීක්ෂා කරන්න.",
  invalidRequest:
    "ඉල්ලීම පිළිගත්තේ නැත. පෙළ සහ අවශ්‍ය හේතුව පරීක්ෂා කර නැවත උත්සාහ කරන්න.",
  reloadKeeping: "නිවැරදි කිරීම තබාගෙන නවතම පිටුව පූරණය කරන්න",
  latest: "පද්ධතියේ නවතම පෙළ",
  useLatest: "මගේ නිවැරදි කිරීම සඳහා මෙම අනුවාදය භාවිත කරන්න",
  working: "ඉල්ලීම යවමින් පවතී…",
  history: "කියවීම් ඉතිහාසය",
  provenance: "මූලාශ්‍ර විස්තර",
  diagnostics: "කියවීමේ තාක්ෂණික විස්තර",
};

type ReviewLanguage = "en" | "si";
const reviewLanguageKey = "exam-guru:review-language:v1";

function savedReviewLanguage(): ReviewLanguage | null {
  try {
    const language = window.localStorage.getItem(reviewLanguageKey);
    return language === "si" || language === "en" ? language : null;
  } catch {
    return null;
  }
}

function subscribeReviewLanguage(onChange: () => void) {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

function textLanguage(language: string): "si" | "ta" | "en" {
  const code = language.toLowerCase().split(/[-_]/)[0];
  if (["si", "sin", "sinhala"].includes(code)) return "si";
  if (["ta", "tam", "tamil"].includes(code)) return "ta";
  return "en";
}

function detectedReviewLanguage(workspace?: Workspace): ReviewLanguage {
  const diagnostics = workspace?.page?.diagnostics;
  const languages = [
    workspace?.language,
    workspace?.page?.language,
    diagnostics?.detected_language,
    ...(Array.isArray(diagnostics?.languages) ? diagnostics.languages : []),
  ];
  return languages.some(
    (language) =>
      typeof language === "string" && textLanguage(language) === "si",
  )
    ? "si"
    : "en";
}

function problem(error: unknown, status: number): Problem {
  let code = "request_failed";
  if (error && typeof error === "object" && "detail" in error) {
    const detail = error.detail;
    if (
      detail &&
      typeof detail === "object" &&
      "code" in detail &&
      typeof detail.code === "string"
    )
      code = detail.code;
  }
  return { status, code };
}

function problemMessage(value: Problem, copy: Copy): string {
  if (value.status === 401) return copy.expired;
  if (value.status === 403) return copy.denied;
  if (value.status === 404) return copy.notFound;
  if (value.code === "source_page_verification_blocked") return copy.blocked;
  if (value.status === 409) return copy.conflict;
  if (value.status === 422) return copy.invalidRequest;
  if (value.status === 0) return copy.networkError;
  return copy.requestError;
}

function NavigationIcon({ name }: { name: "first" | "last" | "refresh" }) {
  return (
    <svg
      aria-hidden="true"
      className="h-4 w-4"
      fill="none"
      focusable="false"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
    >
      <path
        d={
          name === "first"
            ? "m11 6-6 6 6 6m8-12-6 6 6 6"
            : name === "last"
              ? "m5 6 6 6-6 6m8-12 6 6-6 6"
              : "M4 10a8 8 0 1 1 2 8M4 4v6h6"
        }
      />
    </svg>
  );
}

function RecoveredText({ page, copy }: { page: Page; copy: Copy }) {
  return (
    <section aria-label={copy.recoveredText} className={alertClass}>
      <h3 className="font-semibold">{copy.recoveredText}</h3>
      <p className="mt-2 leading-7">{copy.recoveryWarning}</p>
      <pre
        className="mt-3 whitespace-pre-wrap break-words font-sans text-base leading-8"
        data-testid="recovered-page-text"
        dir="auto"
        lang={textLanguage(page.language)}
      >
        {page.system_text}
      </pre>
    </section>
  );
}

function OriginalPage({
  page,
  copy,
  onReady,
}: {
  page: Page;
  copy: Copy;
  onReady: (ready: boolean) => void;
}) {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  const [zoom, setZoom] = useState(100);
  return (
    <section
      aria-label={copy.original}
      className="min-h-0 overflow-auto overscroll-contain rounded-lg border border-slate-300 bg-slate-100"
      tabIndex={0}
    >
      <header className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b border-slate-300 bg-white p-2">
        <h2 className="mr-auto font-semibold">{copy.original}</h2>
        <Button
          aria-label={copy.zoomOut}
          className={buttonClass}
          isDisabled={zoom <= 50}
          onPress={() => setZoom((value) => Math.max(50, value - 25))}
        >
          −
        </Button>
        <output aria-label={copy.zoom} className="min-w-12 text-center text-sm">
          {zoom}%
        </output>
        <Button
          aria-label={copy.zoomIn}
          className={buttonClass}
          isDisabled={zoom >= 200}
          onPress={() => setZoom((value) => Math.min(200, value + 25))}
        >
          +
        </Button>
        <Button
          aria-label={copy.zoomReset}
          className={cn(buttonClass, "w-10 shrink-0 px-0")}
          onPress={() => setZoom(100)}
        >
          <NavigationIcon name="refresh" />
        </Button>
        <a
          className="rounded p-2 text-sm underline focus-visible:ring-2 focus-visible:ring-amber-600"
          href={page.preview_url}
          rel="noreferrer noopener"
          target="_blank"
        >
          {copy.openOriginal}
        </a>
      </header>
      {state === "loading" && (
        <p className="p-3 text-sm" role="status">
          {copy.previewLoading}
        </p>
      )}
      {state === "error" && (
        <div className={`${alertClass} m-3`} role="alert">
          <p>{copy.previewError}</p>
          <Button
            className={`${buttonClass} mt-2`}
            onPress={() => {
              setState("loading");
              onReady(false);
              setAttempt((value) => value + 1);
            }}
          >
            {copy.imageRetry}
          </Button>
        </div>
      )}
      <div className="p-2" style={{ width: `${zoom}%` }}>
        <Image
          alt={`${copy.original} ${page.page_number}`}
          className="block h-auto w-full max-w-none bg-white"
          height={1414}
          key={attempt}
          loading="eager"
          onError={() => {
            setState("error");
            onReady(false);
          }}
          onLoad={() => {
            setState("ready");
            onReady(true);
          }}
          referrerPolicy="no-referrer"
          src={page.preview_url}
          unoptimized
          width={1000}
        />
      </div>
    </section>
  );
}

export function SourcePageReviewWorkspace(props: Props) {
  return (
    <ReviewSession
      key={`${props.documentId}:${props.initialPageNumber ?? 1}`}
      {...props}
    />
  );
}

function ReviewSession({
  documentId,
  role,
  initialPageNumber = 1,
  benchmarkId,
}: Props) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const startPage =
    Number.isSafeInteger(initialPageNumber) && initialPageNumber > 0
      ? initialPageNumber
      : 1;
  const [requestedPage, setRequestedPage] = useState(startPage);
  const [pageInput, setPageInput] = useState(String(startPage));
  const [loaded, setLoaded] = useState<{
    data: Workspace;
    requestId: number;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<Problem | null>(null);
  const [actionError, setActionError] = useState<Problem | null>(null);
  const [invalidPage, setInvalidPage] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [conflict, setConflict] = useState(false);
  const [latestLoaded, setLatestLoaded] = useState(false);
  const [saved, setSaved] = useState(false);
  const [previewReady, setPreviewReady] = useState(false);
  const [busy, setBusy] = useState<Action | null>(null);
  const [documentReadJob, setDocumentReadJob] = useState<ReadJob | null>(null);
  const [readJobError, setReadJobError] = useState<Problem | null>(null);
  const [readJobLoading, setReadJobLoading] = useState(false);
  const readJobRequest = useRef(0);
  const readJobController = useRef<AbortController | null>(null);
  const [decision, setDecision] = useState<{
    action: "confirm" | "exclude";
    page: Page;
  } | null>(null);
  const [decisionReason, setDecisionReason] = useState("");
  const [decisionChecked, setDecisionChecked] = useState(false);
  const requestId = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const alive = useRef(true);
  const mutationLock = useRef(false);
  const correctionFormId = useId();
  const pageInputId = useId();
  const storedLanguage = useSyncExternalStore<ReviewLanguage | null>(
    subscribeReviewLanguage,
    savedReviewLanguage,
    () => null,
  );
  const [languageChoice, setLanguageChoice] = useState<ReviewLanguage | null>(
    null,
  );
  const workspace = loaded?.data;
  const language =
    languageChoice ?? storedLanguage ?? detectedReviewLanguage(workspace);
  const copy: Copy = language === "si" ? sinhala : english;
  const page =
    workspace?.page?.page_number === requestedPage ? workspace.page : null;
  const total = workspace?.progress.total_pages ?? 0;
  const pageAvailable = Boolean(page && !loading && !loadError);
  const readingDocument = Boolean(
    documentReadJob && ["queued", "running"].includes(documentReadJob.status),
  );
  const documentReadFailed = Boolean(
    documentReadJob &&
    ["failed", "superseded"].includes(documentReadJob.status),
  );
  const canReadDocument = Boolean(
    role === "admin" &&
    workspace?.source_active &&
    !loading &&
    !loadError &&
    !busy &&
    !draft &&
    !decision &&
    !readingDocument &&
    !readJobLoading,
  );
  const canWrite = role === "admin" && pageAvailable && !busy && !conflict;
  const reviewableCandidate = Boolean(
    page?.can_confirm && page.candidate_id && page.system_text.trim(),
  );
  const readingFailed =
    page?.state === "failed" ||
    (page?.state === "needs_review" && !reviewableCandidate);
  const readableRecovery = Boolean(
    readingFailed &&
    page?.candidate_id &&
    page.system_text.trim() &&
    page.diagnostics.text_readable === true,
  );
  const canConfirm = Boolean(
    canWrite &&
    !draft &&
    reviewableCandidate &&
    page?.state === "needs_review" &&
    workspace?.source_active &&
    previewReady,
  );
  const canEdit = canWrite && page?.state !== "processing";
  const canReread =
    canWrite &&
    !draft &&
    page?.state !== "processing" &&
    Boolean(workspace?.source_active);
  const canExclude =
    canWrite &&
    !draft &&
    page?.state !== "excluded" &&
    page?.state !== "processing";
  const ready = Boolean(
    pageAvailable &&
    (page?.state === "verified" || page?.state === "excluded") &&
    workspace?.ready_for_ai &&
    workspace.source_active &&
    !workspace.metadata_review_required &&
    total > 0 &&
    workspace.progress.remaining_pages === 0 &&
    workspace.progress.verified_pages > 0,
  );
  const validDraft = Boolean(
    draft &&
    draft.text.trim().length &&
    draft.text.length <= 100000 &&
    draft.reason.trim().length &&
    draft.reason.length <= 2000 &&
    draft.expectedVersion === page?.version,
  );

  const loadPage = useCallback(
    async (pageNumber: number) => {
      const currentRequest = ++requestId.current;
      requestController.current?.abort();
      const controller = new AbortController();
      requestController.current = controller;
      setLoading(true);
      setLoadError(null);
      setPreviewReady(false);
      try {
        const result = await api.GET(
          "/api/v1/admin/materials/{document_id}/review-workspace",
          {
            params: {
              path: { document_id: documentId },
              query: { page_number: pageNumber },
            },
            cache: "no-store",
            signal: controller.signal,
          },
        );
        if (!alive.current || currentRequest !== requestId.current) return;
        if (!result.response.ok || result.error || !result.data) {
          setLoadError(problem(result.error, result.response.status));
          return;
        }
        if (
          result.data.document_id !== documentId ||
          (result.data.page !== null &&
            result.data.page.page_number !== pageNumber)
        ) {
          setLoadError({ status: 502, code: "unexpected_page" });
          return;
        }
        setLoaded({ data: result.data, requestId: currentRequest });
        return result.data;
      } catch {
        if (
          alive.current &&
          currentRequest === requestId.current &&
          !controller.signal.aborted
        )
          setLoadError({ status: 0, code: "network_error" });
      } finally {
        if (alive.current && currentRequest === requestId.current)
          setLoading(false);
      }
    },
    [api, documentId],
  );

  const loadReadJob = useCallback(
    async (jobId: string) => {
      const currentRequest = ++readJobRequest.current;
      readJobController.current?.abort();
      const controller = new AbortController();
      readJobController.current = controller;
      setReadJobLoading(true);
      setReadJobError(null);
      try {
        const result = await api.GET(
          "/api/v1/admin/source-read-jobs/{job_id}",
          {
            params: { path: { job_id: jobId } },
            cache: "no-store",
            signal: controller.signal,
          },
        );
        if (!alive.current || currentRequest !== readJobRequest.current) return;
        if (!result.response.ok || result.error || !result.data) {
          setReadJobError(problem(result.error, result.response.status));
          return;
        }
        if (
          result.data.document_id !== documentId ||
          result.data.id !== jobId ||
          result.data.page_number !== null
        ) {
          setReadJobError({ status: 502, code: "unexpected_reading" });
          return;
        }
        setDocumentReadJob(result.data);
      } catch {
        if (
          alive.current &&
          currentRequest === readJobRequest.current &&
          !controller.signal.aborted
        )
          setReadJobError({ status: 0, code: "network_error" });
      } finally {
        if (alive.current && currentRequest === readJobRequest.current)
          setReadJobLoading(false);
      }
    },
    [api, documentId],
  );

  useEffect(() => {
    alive.current = true;
    const timer = window.setTimeout(() => void loadPage(startPage), 0);
    return () => {
      window.clearTimeout(timer);
      alive.current = false;
      requestId.current += 1;
      requestController.current?.abort();
      readJobRequest.current += 1;
      readJobController.current?.abort();
    };
  }, [loadPage, startPage]);

  useEffect(() => {
    if (!draft && !busy) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const guardNavigation = (event: MouseEvent | SubmitEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const link = target.closest("a[href]");
      const form = target.closest("form[action]");
      if (link instanceof HTMLAnchorElement) {
        if (
          link.target === "_blank" ||
          link.hasAttribute("download") ||
          link.getAttribute("href")?.startsWith("#")
        )
          return;
      } else if (!form) return;
      if (mutationLock.current || !window.confirm(copy.leave)) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", guardNavigation, true);
    document.addEventListener("submit", guardNavigation, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", guardNavigation, true);
      document.removeEventListener("submit", guardNavigation, true);
    };
  }, [draft, busy, copy.leave]);

  useEffect(() => {
    if (
      (page?.state !== "processing" && !readingDocument) ||
      loading ||
      loadError ||
      readJobLoading ||
      readJobError ||
      busy ||
      draft ||
      decision
    )
      return;
    const timer = window.setTimeout(() => {
      if (readingDocument && documentReadJob)
        void loadReadJob(documentReadJob.id);
      void loadPage(requestedPage);
    }, 5000);
    return () => window.clearTimeout(timer);
  }, [
    page?.state,
    readingDocument,
    documentReadJob,
    readJobLoading,
    readJobError,
    loading,
    loadError,
    busy,
    draft,
    decision,
    loadReadJob,
    loadPage,
    requestedPage,
  ]);

  function navigate(pageNumber: number) {
    if (mutationLock.current) return;
    if (
      !Number.isSafeInteger(pageNumber) ||
      pageNumber < 1 ||
      pageNumber > total
    ) {
      setInvalidPage(true);
      return;
    }
    if (pageNumber === requestedPage && !loadError) return;
    if (draft && !window.confirm(copy.leave)) return;
    setDraft(null);
    setDecision(null);
    setDecisionReason("");
    setDecisionChecked(false);
    setConflict(false);
    setLatestLoaded(false);
    setSaved(false);
    setActionError(null);
    setInvalidPage(false);
    setRequestedPage(pageNumber);
    setPageInput(String(pageNumber));
    void loadPage(pageNumber);
  }

  function jump(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const pageNumber = pageInput.trim() ? Number(pageInput) : NaN;
    navigate(pageNumber);
  }

  async function readDocument() {
    if (mutationLock.current || !canReadDocument) return;
    mutationLock.current = true;
    setBusy("read");
    setReadJobError(null);
    try {
      const result = await api.POST(
        "/api/v1/admin/source-documents/{document_id}/read",
        {
          params: { path: { document_id: documentId } },
        },
      );
      if (!alive.current) return;
      if (!result.response.ok || result.error || !result.data) {
        setReadJobError(problem(result.error, result.response.status));
        return;
      }
      if (
        result.data.document_id !== documentId ||
        result.data.page_number !== null
      ) {
        setReadJobError({ status: 502, code: "unexpected_reading" });
        return;
      }
      setDocumentReadJob(result.data);
      await loadPage(requestedPage);
    } catch {
      if (alive.current) setReadJobError({ status: 0, code: "network_error" });
    } finally {
      mutationLock.current = false;
      if (alive.current) setBusy(null);
    }
  }

  async function refresh() {
    if (mutationLock.current) return;
    if (documentReadJob) void loadReadJob(documentReadJob.id);
    const fresh = await loadPage(requestedPage);
    if (!fresh?.page) return;
    if (draft) {
      setLatestLoaded(true);
    } else {
      setConflict(false);
      setActionError(null);
      setDecisionChecked(false);
      if (decision) setDecision({ action: decision.action, page: fresh.page });
    }
  }

  async function mutate(
    action: Action,
    send: () => Promise<{ response: Response; error?: unknown }>,
  ) {
    if (mutationLock.current || !canWrite || !page) return;
    mutationLock.current = true;
    setBusy(action);
    setActionError(null);
    setSaved(false);
    const mutationRequest = requestId.current;
    try {
      const result = await send();
      if (!alive.current || mutationRequest !== requestId.current) return;
      if (!result.response.ok || result.error) {
        setActionError(problem(result.error, result.response.status));
        setDecisionChecked(false);
        if (result.response.status === 409 || result.response.status >= 500) {
          setConflict(true);
          setLatestLoaded(false);
        }
        return;
      }
      if (action === "edit") setDraft(null);
      setDecision(null);
      setDecisionReason("");
      setDecisionChecked(false);
      setConflict(false);
      setLatestLoaded(false);
      const fresh = await loadPage(page.page_number);
      if (fresh && action === "edit") setSaved(true);
    } catch {
      if (alive.current && mutationRequest === requestId.current) {
        setActionError({ status: 0, code: "network_error" });
        setDecisionChecked(false);
        setConflict(true);
        setLatestLoaded(false);
      }
    } finally {
      mutationLock.current = false;
      if (alive.current) setBusy(null);
    }
  }

  function saveCorrection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft || !page || !validDraft || !canEdit) return;
    const body: EditRequest = {
      expected_version: draft.expectedVersion,
      text: draft.text,
      reason: draft.reason.trim(),
    };
    void mutate("edit", () =>
      api.POST(
        "/api/v1/admin/materials/{document_id}/pages/{page_number}/edit",
        {
          params: {
            path: { document_id: documentId, page_number: page.page_number },
          },
          body,
        },
      ),
    );
  }

  function reread() {
    if (!canReread || !page) return;
    const body: RereadRequest = { expected_version: page.version };
    void mutate("reread", () =>
      api.POST(
        "/api/v1/admin/materials/{document_id}/pages/{page_number}/reread",
        {
          params: {
            path: { document_id: documentId, page_number: page.page_number },
          },
          body,
        },
      ),
    );
  }

  function submitDecision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !decision ||
      !decisionChecked ||
      !page ||
      decision.page.version !== page.version ||
      decision.page.candidate_id !== page.candidate_id
    )
      return;
    if (decision.action === "confirm") {
      if (!canConfirm || !decision.page.candidate_id) return;
      const body: ConfirmRequest = {
        expected_version: decision.page.version,
        candidate_id: decision.page.candidate_id,
        compared_with_original: true,
        reason: copy.confirmReason,
      };
      void mutate("confirm", () =>
        api.POST(
          "/api/v1/admin/materials/{document_id}/pages/{page_number}/confirm",
          {
            params: {
              path: {
                document_id: documentId,
                page_number: decision.page.page_number,
              },
            },
            body,
          },
        ),
      );
    } else {
      if (!canExclude || !decisionReason.trim() || decisionReason.length > 2000)
        return;
      const body: ExcludeRequest = {
        expected_version: decision.page.version,
        confirm_exclusion: true,
        reason: decisionReason.trim(),
      };
      void mutate("exclude", () =>
        api.POST(
          "/api/v1/admin/materials/{document_id}/pages/{page_number}/exclude",
          {
            params: {
              path: {
                document_id: documentId,
                page_number: decision.page.page_number,
              },
            },
            body,
          },
        ),
      );
    }
  }

  function openDecision(action: "confirm" | "exclude") {
    if (!page || (action === "confirm" ? !canConfirm : !canExclude)) return;
    setDecision({ action, page });
    setDecisionChecked(false);
    setDecisionReason("");
    setActionError(null);
  }

  function cancelEdit() {
    if (mutationLock.current) return;
    setDraft(null);
    setLatestLoaded(false);
    if (!conflict) setActionError(null);
  }

  function changeLanguage(language: ReviewLanguage) {
    setLanguageChoice(language);
    try {
      window.localStorage.setItem(reviewLanguageKey, language);
    } catch {
      return;
    }
  }

  const progress = workspace?.progress;
  const status =
    page?.state === "verified"
      ? copy.verifiedState
      : page?.state === "excluded"
        ? copy.excludedState
        : page?.state === "processing"
          ? copy.processing
          : readingFailed
            ? copy.failed
            : copy.notVerified;
  const confirmControl = (
    <Button
      className={readingFailed ? buttonClass : primaryButton}
      isDisabled={!canConfirm}
      onPress={() => openDecision("confirm")}
    >
      {copy.confirmAction}
    </Button>
  );

  return (
    <section
      className="mx-auto flex min-h-0 w-full max-w-[100rem] flex-col gap-2 p-3 font-sans lg:flex-1 lg:overflow-hidden"
      data-source-review=""
      data-testid="source-page-workspace"
      lang={language}
    >
      <header className="shrink-0">
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <Link
            className="rounded underline focus-visible:ring-2 focus-visible:ring-amber-600"
            href={`/admin/materials/${encodeURIComponent(documentId)}`}
          >
            {copy.back}
          </Link>
          {benchmarkId && (
            <Link
              className="rounded underline focus-visible:ring-2 focus-visible:ring-amber-600"
              href={`/admin/materials/benchmark-review?benchmark_id=${encodeURIComponent(benchmarkId)}`}
            >
              {copy.backQueue}
            </Link>
          )}
          {workspace && (
            <p
              className="max-w-xl truncate font-semibold"
              title={workspace.document_title}
            >
              {workspace.document_title}
            </p>
          )}
        </div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-semibold sm:text-2xl">{copy.title}</h1>
          <label className="flex items-center gap-2 text-sm font-semibold">
            <span>
              <span lang="en">Review language</span>
              {" / "}
              <span lang="si">භාෂාව</span>
            </span>
            <select
              className={cn(inputClass, "min-h-10 py-1 text-sm")}
              onChange={(event) =>
                changeLanguage(event.currentTarget.value === "si" ? "si" : "en")
              }
              value={language}
            >
              <option lang="en" value="en">
                English
              </option>
              <option lang="si" value="si">
                සිංහල
              </option>
            </select>
          </label>
        </div>
        <p className="mt-1 text-sm leading-6 text-slate-700">
          {copy.instruction}
        </p>
        {role === "reviewer" && (
          <p className="mt-1 text-sm font-semibold">{copy.reviewer}</p>
        )}
      </header>

      {progress && (
        <section
          aria-label={copy.progress}
          className="shrink-0 rounded-lg border border-slate-300 bg-white px-3 py-2"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <dl className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
              {(
                [
                  [copy.total, progress.total_pages],
                  [copy.processed, progress.processed_pages],
                  [copy.verified, progress.verified_pages],
                  [copy.excluded, progress.excluded_pages],
                  [copy.flagged, progress.flagged_pages],
                  [copy.remaining, progress.remaining_pages],
                ] as const
              ).map(([label, value]) => (
                <div className="flex gap-2" key={label}>
                  <dt className="text-slate-600">{label}</dt>
                  <dd className="font-semibold tabular-nums">{value}</dd>
                </div>
              ))}
            </dl>
            <p className="text-sm font-semibold">
              {ready ? copy.ready : copy.notReady}
            </p>
          </div>
          {workspace.metadata_review_required && (
            <p className="mt-1 text-sm text-amber-950">{copy.metadata}</p>
          )}
          {!workspace.source_active && (
            <p className="mt-1 text-sm text-amber-950">{copy.inactive}</p>
          )}
        </section>
      )}

      <nav
        aria-label={copy.nav}
        className="sticky top-0 z-20 flex shrink-0 flex-wrap items-center gap-2 rounded-lg border border-slate-300 bg-white p-2"
      >
        <Button
          aria-label={copy.first}
          className={cn(buttonClass, "w-10 shrink-0 px-0")}
          isDisabled={Boolean(busy) || !total || requestedPage <= 1}
          onPress={() => navigate(1)}
        >
          <NavigationIcon name="first" />
        </Button>
        <Button
          className={buttonClass}
          isDisabled={Boolean(busy) || !total || requestedPage <= 1}
          onPress={() => navigate(requestedPage - 1)}
        >
          {copy.previous}
        </Button>
        <Form
          className="flex items-center gap-2"
          onSubmit={jump}
          validationBehavior="aria"
        >
          <label className="sr-only" htmlFor={pageInputId}>
            {copy.pageNumber}
          </label>
          <input
            aria-invalid={invalidPage || undefined}
            className={`${inputClass} w-20 text-sm`}
            disabled={Boolean(busy) || !total}
            id={pageInputId}
            inputMode="numeric"
            max={total || 1}
            min={1}
            onChange={(event) => {
              setPageInput(event.target.value);
              setInvalidPage(false);
            }}
            step={1}
            type="number"
            value={pageInput}
          />
          <span className="whitespace-nowrap text-sm">
            {copy.of} {total}
          </span>
          <Button
            className={buttonClass}
            isDisabled={Boolean(busy) || !total}
            type="submit"
          >
            {copy.go}
          </Button>
        </Form>
        <Button
          className={buttonClass}
          isDisabled={Boolean(busy) || !total || requestedPage >= total}
          onPress={() => navigate(requestedPage + 1)}
        >
          {copy.next}
        </Button>
        <Button
          aria-label={copy.last}
          className={cn(buttonClass, "w-10 shrink-0 px-0")}
          isDisabled={Boolean(busy) || !total || requestedPage >= total}
          onPress={() => navigate(total)}
        >
          <NavigationIcon name="last" />
        </Button>
        <Button
          className={buttonClass}
          isDisabled={
            Boolean(busy) || !pageAvailable || !workspace?.previous_flagged_page
          }
          onPress={() =>
            workspace?.previous_flagged_page &&
            navigate(workspace.previous_flagged_page)
          }
        >
          {copy.previousFlagged}
        </Button>
        <Button
          className={buttonClass}
          isDisabled={
            Boolean(busy) || !pageAvailable || !workspace?.next_flagged_page
          }
          onPress={() =>
            workspace?.next_flagged_page &&
            navigate(workspace.next_flagged_page)
          }
        >
          {copy.nextFlagged}
        </Button>
        <Button
          aria-label={copy.refresh}
          className={cn(buttonClass, "ml-auto w-10 shrink-0 px-0")}
          isDisabled={Boolean(busy) || loading || Boolean(draft)}
          onPress={() => void refresh()}
        >
          <NavigationIcon name="refresh" />
        </Button>
      </nav>
      {invalidPage && (
        <p className={alertClass} role="alert">
          {copy.invalidPage.replace("{total}", String(total))}
        </p>
      )}
      {loadError && (
        <div className={alertClass} role="alert">
          <p>{problemMessage(loadError, copy)}</p>
          <Button
            className={`${buttonClass} mt-2`}
            isDisabled={loading || Boolean(busy)}
            onPress={() => void refresh()}
          >
            {copy.loadAgain}
          </Button>
        </div>
      )}
      {actionError && !decision && (
        <div className={`${alertClass} shrink-0`} role="alert">
          <p>{problemMessage(actionError, copy)}</p>
          <Button
            className={`${buttonClass} mt-2`}
            isDisabled={loading || Boolean(busy)}
            onPress={() => void refresh()}
          >
            {draft ? copy.reloadKeeping : copy.refresh}
          </Button>
        </div>
      )}
      {saved && (
        <p className="shrink-0 text-sm font-semibold" role="status">
          {copy.saved}
        </p>
      )}
      {loading && (
        <p className="shrink-0 text-sm" role="status">
          {copy.loading}
        </p>
      )}
      {readingDocument && (
        <p className="shrink-0 text-sm" role="status">
          {copy.readingDocument}
        </p>
      )}
      {(readJobError || documentReadFailed) && (
        <div className={`${alertClass} shrink-0`} role="alert">
          <p>
            {readJobError
              ? problemMessage(readJobError, copy)
              : copy.documentReadFailed}
          </p>
          {documentReadJob && (
            <Button
              className={`${buttonClass} mt-2`}
              isDisabled={loading || readJobLoading || Boolean(busy)}
              onPress={() => void refresh()}
            >
              {copy.refresh}
            </Button>
          )}
          {documentReadFailed && total > 0 && (
            <Button
              className={`${buttonClass} mt-2`}
              isDisabled={!canReadDocument}
              onPress={() => void readDocument()}
            >
              {copy.readDocument}
            </Button>
          )}
        </div>
      )}
      {!loading && !loadError && workspace && !workspace.page && (
        <div className="shrink-0 rounded-lg border border-slate-300 bg-white p-4">
          <p role="status">{copy.empty}</p>
          <Button
            className={`${buttonClass} mt-3`}
            isDisabled={!canReadDocument}
            onPress={() => void readDocument()}
          >
            {copy.readDocument}
          </Button>
        </div>
      )}

      {page && (!loading || draft) && (!loadError || draft) ? (
        <section
          aria-label={copy.comparison}
          className="grid h-[75dvh] min-h-0 grid-rows-2 gap-3 overflow-hidden lg:h-auto lg:flex-1 lg:grid-cols-2 lg:grid-rows-1"
        >
          <OriginalPage
            copy={copy}
            key={loaded?.requestId}
            onReady={setPreviewReady}
            page={page}
          />
          <section
            aria-label={copy.systemText}
            className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-slate-300 bg-white"
          >
            <header className="shrink-0 border-b border-slate-300 px-3 py-2">
              <h2 className="font-semibold">{copy.systemText}</h2>
              <p
                className="mt-1 text-sm"
                role={readingFailed ? "alert" : "status"}
              >
                {status}
              </p>
              {!readingFailed && page.risk_codes.length > 0 && (
                <p className="mt-1 text-sm text-amber-950">{copy.suspicious}</p>
              )}
            </header>
            <div
              className="min-h-0 flex-1 overflow-auto overscroll-contain p-3"
              data-testid="text-scroll-panel"
              tabIndex={draft ? undefined : 0}
            >
              {draft ? (
                <Form
                  className="flex min-h-full flex-col gap-3"
                  id={correctionFormId}
                  onSubmit={saveCorrection}
                >
                  <label className="flex flex-1 flex-col gap-1 text-sm font-semibold">
                    {copy.correction}
                    <textarea
                      className={`${inputClass} min-h-48 flex-1 whitespace-pre-wrap font-sans text-base font-normal leading-8`}
                      dir="auto"
                      disabled={Boolean(busy) || loading}
                      lang={textLanguage(page.language)}
                      maxLength={100000}
                      onChange={(event) =>
                        setDraft({ ...draft, text: event.target.value })
                      }
                      required
                      value={draft.text}
                    />
                  </label>
                  <label className="grid gap-1 text-sm font-semibold">
                    {copy.correctionReason}
                    <input
                      className={`${inputClass} font-normal`}
                      disabled={Boolean(busy) || loading}
                      maxLength={2000}
                      onChange={(event) =>
                        setDraft({ ...draft, reason: event.target.value })
                      }
                      required
                      value={draft.reason}
                    />
                  </label>
                  {latestLoaded && (
                    <section
                      aria-label={copy.latest}
                      className="rounded-lg border border-amber-400 bg-amber-50 p-3"
                    >
                      <h3 className="font-semibold">{copy.latest}</h3>
                      {readableRecovery ? (
                        <RecoveredText page={page} copy={copy} />
                      ) : readingFailed ? (
                        <p className="mt-2 text-sm">{copy.unconfirmable}</p>
                      ) : (
                        <pre
                          className="mt-2 whitespace-pre-wrap break-words font-sans text-base leading-8"
                          dir="auto"
                          lang={textLanguage(page.language)}
                        >
                          {page.system_text}
                        </pre>
                      )}
                      <Button
                        className={`${buttonClass} mt-3`}
                        isDisabled={loading || Boolean(busy)}
                        onPress={() => {
                          setDraft({ ...draft, expectedVersion: page.version });
                          setConflict(false);
                          setLatestLoaded(false);
                          setActionError(null);
                        }}
                      >
                        {copy.useLatest}
                      </Button>
                    </section>
                  )}
                </Form>
              ) : readableRecovery ? (
                <RecoveredText page={page} copy={copy} />
              ) : readingFailed ? (
                <p className={alertClass}>{copy.unconfirmable}</p>
              ) : (
                <pre
                  className="whitespace-pre-wrap break-words font-sans text-base leading-8"
                  data-testid="system-page-text"
                  dir="auto"
                  lang={textLanguage(page.language)}
                >
                  {page.system_text}
                </pre>
              )}
              <details className="mt-4 rounded-lg border border-slate-300 p-3">
                <summary className="cursor-pointer rounded font-semibold focus-visible:ring-2 focus-visible:ring-amber-600">
                  {copy.technical}
                </summary>
                <div className="mt-3 space-y-3 text-sm" lang="en">
                  {readingFailed && !readableRecovery && (
                    <section aria-label={copy.unsuccessfulText} lang={language}>
                      <h3 className="font-semibold">{copy.unsuccessfulText}</h3>
                      <pre
                        className="mt-2 whitespace-pre-wrap break-words font-sans text-base leading-8"
                        data-testid="failed-page-text"
                        dir="auto"
                        lang={textLanguage(page.language)}
                      >
                        {page.system_text}
                      </pre>
                    </section>
                  )}
                  <p>Page version: {page.version}</p>
                  <p className="break-all">
                    Current candidate: {page.candidate_id ?? "None"}
                  </p>
                  <h3 className="font-semibold">{copy.provenance}</h3>
                  <pre className="whitespace-pre-wrap break-words font-sans">
                    {JSON.stringify(page.provenance, null, 2)}
                  </pre>
                  <h3 className="font-semibold">{copy.diagnostics}</h3>
                  <pre className="whitespace-pre-wrap break-words font-sans">
                    {JSON.stringify(
                      { risk_codes: page.risk_codes, ...page.diagnostics },
                      null,
                      2,
                    )}
                  </pre>
                  <h3 className="font-semibold">{copy.history}</h3>
                  <ul className="space-y-2">
                    {page.history.map((candidate) => (
                      <li
                        className="break-words rounded border border-slate-200 p-2"
                        key={candidate.id}
                      >
                        <p>
                          {candidate.method} · {candidate.created_at} ·{" "}
                          {candidate.is_current
                            ? "Current reading"
                            : "Past reading"}
                        </p>
                        <p className="break-all">{candidate.id}</p>
                        <p className="break-all">{candidate.text_sha256}</p>
                      </li>
                    ))}
                  </ul>
                </div>
              </details>
            </div>
          </section>
        </section>
      ) : (
        <div className="min-h-0 flex-1" />
      )}

      <div
        aria-label={copy.actions}
        className="sticky bottom-0 z-20 flex shrink-0 flex-wrap items-center gap-2 rounded-lg border border-slate-300 bg-white p-2"
        role="group"
      >
        {draft ? (
          <>
            <Button
              className={primaryButton}
              form={correctionFormId}
              isDisabled={!canEdit || !validDraft}
              type="submit"
            >
              {copy.save}
            </Button>
            <Button
              className={buttonClass}
              isDisabled={Boolean(busy) || loading}
              onPress={cancelEdit}
            >
              {copy.cancelEditing}
            </Button>
          </>
        ) : (
          <>
            {!readingFailed && confirmControl}
            <Button
              className={readingFailed ? primaryButton : buttonClass}
              isDisabled={!canReread}
              onPress={reread}
            >
              {copy.rereadAction}
            </Button>
            <Button
              className={buttonClass}
              isDisabled={!canEdit}
              onPress={() => {
                if (!page || !canEdit) return;
                setDraft({
                  text: page.system_text,
                  reason: "",
                  expectedVersion: page.version,
                });
                setSaved(false);
                setLatestLoaded(false);
                setActionError(null);
              }}
            >
              {copy.editAction}
            </Button>
            <Button
              className={buttonClass}
              isDisabled={!canExclude}
              onPress={() => openDecision("exclude")}
            >
              {copy.excludeAction}
            </Button>
            {readingFailed && confirmControl}
          </>
        )}
        {busy && (
          <p className="text-sm" role="status">
            {copy.working}
          </p>
        )}
      </div>

      <ModalOverlay
        className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4"
        isKeyboardDismissDisabled={Boolean(busy)}
        isOpen={Boolean(decision)}
        onOpenChange={(open) => {
          if (!open && !mutationLock.current) {
            setDecision(null);
            setDecisionChecked(false);
            setDecisionReason("");
          }
        }}
      >
        <Modal className="max-h-[90dvh] w-full max-w-xl overflow-auto rounded-xl bg-white p-5 shadow-xl">
          <Dialog className="font-sans outline-none" lang={language}>
            {decision && (
              <Form className="space-y-4" onSubmit={submitDecision}>
                <Heading className="text-xl font-semibold" slot="title">
                  {decision.action === "confirm"
                    ? copy.confirmTitle
                    : copy.excludeTitle}
                </Heading>
                <p className="text-sm leading-7">
                  {decision.action === "confirm"
                    ? copy.confirmDescription
                    : copy.excludeDescription}
                </p>
                <p className="text-sm font-semibold">
                  {workspace?.document_title} · {copy.pageNumber}{" "}
                  {decision.page.page_number}
                </p>
                {decision.action === "exclude" && (
                  <label className="grid gap-1 text-sm font-semibold">
                    {copy.excludeReason}
                    <textarea
                      className={`${inputClass} min-h-20 font-sans font-normal`}
                      disabled={Boolean(busy)}
                      maxLength={2000}
                      onChange={(event) =>
                        setDecisionReason(event.target.value)
                      }
                      required
                      value={decisionReason}
                    />
                  </label>
                )}
                <label className="flex items-start gap-3 text-sm leading-7">
                  <input
                    checked={decisionChecked}
                    className="mt-2 h-4 w-4 shrink-0 accent-slate-950"
                    disabled={Boolean(busy) || loading || conflict}
                    onChange={(event) =>
                      setDecisionChecked(event.target.checked)
                    }
                    type="checkbox"
                  />
                  {decision.action === "confirm"
                    ? copy.compared
                    : copy.exclusionAgreement}
                </label>
                {actionError && (
                  <div className={alertClass} role="alert">
                    <p>{problemMessage(actionError, copy)}</p>
                    <Button
                      className={`${buttonClass} mt-2`}
                      isDisabled={Boolean(busy) || loading}
                      onPress={() => void refresh()}
                    >
                      {copy.refresh}
                    </Button>
                  </div>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button
                    className={primaryButton}
                    isDisabled={
                      !decisionChecked ||
                      (decision.action === "confirm"
                        ? !canConfirm
                        : !canExclude ||
                          !decisionReason.trim() ||
                          decisionReason.length > 2000)
                    }
                    type="submit"
                  >
                    {decision.action === "confirm"
                      ? copy.confirmSubmit
                      : copy.excludeSubmit}
                  </Button>
                  <Button
                    className={buttonClass}
                    isDisabled={Boolean(busy)}
                    onPress={() => {
                      setDecision(null);
                      setDecisionChecked(false);
                      setDecisionReason("");
                    }}
                  >
                    {copy.cancel}
                  </Button>
                </div>
                {busy && <p role="status">{copy.working}</p>}
              </Form>
            )}
          </Dialog>
        </Modal>
      </ModalOverlay>
    </section>
  );
}
