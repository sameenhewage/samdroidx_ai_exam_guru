"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import { Badge } from "@/components/ui/badge";

type Role = "admin" | "reviewer";
type ReviewSummary = components["schemas"]["ReviewPaperSummaryResponse"];
type ReviewPaper = components["schemas"]["ReviewPaperDetailResponse"];
type ReviewQuestion = components["schemas"]["ReviewQuestionResponse"];
type QuestionContent = components["schemas"]["QuestionContentRequest"];
type TechnicalFinding = components["schemas"]["TechnicalValidationFindingResponse"];
type SemanticVerification = components["schemas"]["SemanticVerificationDetailsResponse"];
type SemanticClaim = components["schemas"]["SemanticClaimEvidenceResponse"];
type DraftCreated = components["schemas"]["ReviewPaperDraftCreatedResponse"];
type ReviewReasonCode = components["schemas"]["CorrectionReasonCode"];
type DefectCategory = components["schemas"]["DefectCategory"];
type FindingStatus = components["schemas"]["FindingStatus"];
type EvalCase = components["schemas"]["SubjectQualityEvalCaseResponse"];
type UiError = {
  code: string;
  message: string;
  preserveDraft?: boolean;
  retryable: boolean;
  title: string;
};
type ReviewReasonDraft = {
  note: string;
  reasonCode: ReviewReasonCode | "";
};
type EditDraft = ReviewReasonDraft & {
  answer: string;
  explanation: string;
  markingGuide: string;
  marks: string;
  options: Array<{ option_id: string; text: string }>;
  questionType: QuestionContent["question_type"];
  stem: string;
};
type RegenerationAttempt = {
  idempotencyKey: string;
  note: string | null;
  questionId: string;
  reasonCode: ReviewReasonCode;
  version: number;
};
type QualityEvidence = {
  expectedFindingCodes: string[];
  feedbackId: string;
  reasonCode: ReviewReasonCode;
  suggestedStatus: FindingStatus;
};
type PromotionDraft = QualityEvidence & {
  defectCategory: DefectCategory;
  expectedStatus: FindingStatus;
  idempotencyKey: string;
};
type JsonObject = Record<string, unknown>;

const LIST_LIMIT = 100;
const MAX_TEXT_LENGTH = 4_096;
const MAX_REVIEW_NOTE_LENGTH = 768;
const REGENERATION_POLL_DELAYS_MS = [0, 200, 400, 800, 1_500, 2_500] as const;
const REVIEW_REASON_CHOICES: ReadonlyArray<{ label: string; value: ReviewReasonCode }> = [
  { label: "The answer is incorrect", value: "answer_incorrect" },
  { label: "The wording is unclear or ambiguous", value: "ambiguous_wording" },
  { label: "It is outside the selected lessons", value: "outside_scope" },
  { label: "The reviewed sources do not support it", value: "source_not_supported" },
  { label: "The answer and marking do not agree", value: "marking_inconsistent" },
  { label: "The language is not suitable for these learners", value: "language_quality" },
  { label: "The answer choices are not suitable", value: "distractor_quality" },
  { label: "It is too similar to another question", value: "duplicate_content" },
  { label: "The content is unsafe or inappropriate", value: "unsafe_content" },
  { label: "Another educational quality issue", value: "other_quality_issue" },
];
const REVIEW_REASON_FINDING_CODES: Readonly<Record<ReviewReasonCode, string>> = {
  ambiguous_wording: "subject.language.ambiguous_wording",
  answer_incorrect: "subject.answer.incorrect",
  distractor_quality: "subject.assessment.distractor_quality",
  duplicate_content: "duplicate.lexical_similarity_indicator",
  language_quality: "subject.language.quality_issue",
  marking_inconsistent: "subject.marking.answer_inconsistent",
  other_quality_issue: "subject.review.other_quality_issue",
  outside_scope: "subject.scope.outside_selected_lesson",
  source_not_supported: "subject.factual.unsupported_claim",
  unsafe_content: "security.unsafe_content",
};
const FAILURE_REASONS: ReadonlySet<ReviewReasonCode> = new Set([
  "answer_incorrect",
  "marking_inconsistent",
  "outside_scope",
  "source_not_supported",
  "unsafe_content",
]);
const DEFECT_CHOICES: ReadonlyArray<{ label: string; value: DefectCategory }> = [
  { label: "No known defect (confirmed good example)", value: "no_defect" },
  { label: "Answer correctness", value: "answer_correctness" },
  { label: "More than one correct answer", value: "multiple_correct_answers" },
  { label: "Marking consistency", value: "marking_consistency" },
  { label: "Curriculum scope", value: "scope_alignment" },
  { label: "Source support", value: "source_grounding" },
  { label: "Language clarity", value: "language_clarity" },
  { label: "Answer-choice quality", value: "distractor_quality" },
  { label: "Duplicate content", value: "duplicate_content" },
  { label: "Unsafe instruction residue", value: "security_residue" },
  { label: "Other", value: "other" },
];

const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none transition hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-500 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const dangerButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-red-700 bg-red-700 px-4 py-2 text-sm font-semibold text-white outline-none transition hover:bg-red-800 focus-visible:ring-2 focus-visible:ring-red-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const fieldClass = "grid gap-1.5 text-sm font-semibold text-slate-800";
const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 outline-none transition focus:border-amber-600 focus:ring-2 focus:ring-amber-200 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function detailCode(error: unknown): string {
  const detail = asObject(asObject(error)?.detail);
  return typeof detail?.code === "string" ? detail.code : "request_failed";
}

function safeText(value: unknown, fallback = "Not recorded"): string {
  if (value === null || value === undefined || value === "") return fallback;
  const text =
    typeof value === "string" || typeof value === "number" || typeof value === "boolean"
      ? String(value)
      : fallback;
  const cleaned = text.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "�");
  return cleaned.length > MAX_TEXT_LENGTH ? `${cleaned.slice(0, MAX_TEXT_LENGTH)}…` : cleaned;
}

function titleCase(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function suggestedDefect(reasonCode: ReviewReasonCode): DefectCategory {
  const mapped: Partial<Record<ReviewReasonCode, DefectCategory>> = {
    ambiguous_wording: "language_clarity",
    answer_incorrect: "answer_correctness",
    distractor_quality: "distractor_quality",
    duplicate_content: "duplicate_content",
    language_quality: "language_clarity",
    marking_inconsistent: "marking_consistency",
    outside_scope: "scope_alignment",
    source_not_supported: "source_grounding",
    unsafe_content: "security_residue",
  };
  return mapped[reasonCode] ?? "other";
}

function suggestedFindingStatus(
  question: ReviewQuestion,
  reasonCode: ReviewReasonCode,
): FindingStatus {
  if (question.validation.status === "failed_check" || FAILURE_REASONS.has(reasonCode)) {
    return "fail";
  }
  return "warn";
}

function reviewError(error: unknown, response: Response, surface: "queue" | "paper" | "command" | "regenerate" | "draft"): UiError {
  const code = detailCode(error);
  const status = response.status;
  if (status === 401) {
    return {
      code: "authentication_required",
      message: "Your review session has expired. Sign in again before continuing.",
      retryable: false,
      title: "Sign in again",
    };
  }
  if (status === 403) {
    return {
      code: "permission_denied",
      message: "This account cannot review and approve generated questions.",
      retryable: false,
      title: "Review permission required",
    };
  }
  if (status === 404) {
    return {
      code,
      message:
        surface === "queue"
          ? "The review queue is unavailable. Reload the queue."
          : "This paper or question is no longer available. Reload the latest review queue.",
      retryable: true,
      title: "Review item not found",
    };
  }
  if (status === 409) {
    if (code === "review_question_version_conflict") {
      return {
        code,
        message:
          "Another reviewer changed this question. Your edits are still here. Compare or copy them before explicitly loading the latest version.",
        preserveDraft: true,
        retryable: true,
        title: "Another reviewer changed this question",
      };
    }
    if (code === "review_question_revalidation_required") {
      return {
        code,
        message:
          "This question changed after its previous check. Prepare and validate a replacement before approving it.",
        retryable: false,
        title: "Fresh validation required",
      };
    }
    if (code === "review_question_regeneration_limit_exceeded") {
      return {
        code,
        message:
          "This question has reached the safe regeneration limit. Reject it or ask an administrator to start a new paper.",
        retryable: false,
        title: "Regeneration limit reached",
      };
    }
    if (code === "review_question_cost_limit_exceeded") {
      return {
        code,
        message: "This paper reached its configured generation limit. Reject the question or start a new paper.",
        retryable: false,
        title: "Paper generation limit reached",
      };
    }
    if (code === "review_paper_state_conflict") {
      return {
        code,
        message:
          "The paper is no longer in the state required for this action. Reload the latest paper before continuing.",
        retryable: true,
        title: "Paper state changed",
      };
    }
    return {
      code,
      message: "Review state changed while the action was being processed. Reload the latest paper.",
      retryable: true,
      title: "Review state changed",
    };
  }
  if (status === 422) {
    return {
      code,
      message:
        surface === "draft"
          ? "A draft can be created only after every question is approved and the paper version is current."
          : "Check the question content and required reason, then try again.",
      retryable: false,
      title: surface === "draft" ? "Paper is not ready for a draft" : "Check the review action",
    };
  }
  if (status === 429 || code === "rate_limit_exceeded") {
    const retryAfter = response.headers.get("Retry-After");
    return {
      code,
      message: retryAfter
        ? `Several replacement requests were made recently. Try again in ${retryAfter} seconds.`
        : "Several replacement requests were made recently. Wait a short time before trying again.",
      retryable: true,
      title: "Please wait before regenerating",
    };
  }
  if (status === 503) {
    return {
      code,
      message:
        "A replacement cannot be prepared safely right now. Your reason and current review are unchanged; try again later.",
      retryable: true,
      title: "Replacement service unavailable",
    };
  }
  return {
    code,
    message: "The review action could not be completed. No approval was assumed.",
    retryable: true,
    title: "Review action failed",
  };
}

function networkError(surface: "queue" | "paper" | "command" | "regenerate" | "draft"): UiError {
  return {
    code: "network_error",
    message:
      surface === "command" || surface === "regenerate"
        ? "The service could not be reached. Your local input is still here; do not assume the action succeeded."
        : "The review service could not be reached. Check the connection and try again.",
    preserveDraft: surface === "command" || surface === "regenerate",
    retryable: true,
    title: "Connection unavailable",
  };
}

function secureRegenerationKey(): string {
  const cryptoObject = globalThis.crypto;
  let random: string;
  if (typeof cryptoObject?.randomUUID === "function") {
    random = cryptoObject.randomUUID();
  } else if (typeof cryptoObject?.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    cryptoObject.getRandomValues(bytes);
    random = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  } else {
    throw new Error("Secure browser randomness is unavailable");
  }
  const key = `review-regenerate-${random}`;
  if (key.length > 128 || /\s/.test(key)) throw new Error("Unsafe regeneration key");
  return key;
}

function securePromotionKey(): string {
  const cryptoObject = globalThis.crypto;
  const random =
    typeof cryptoObject?.randomUUID === "function"
      ? cryptoObject.randomUUID()
      : (() => {
          if (typeof cryptoObject?.getRandomValues !== "function") {
            throw new Error("Secure browser randomness is unavailable");
          }
          const bytes = new Uint8Array(16);
          cryptoObject.getRandomValues(bytes);
          return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
        })();
  const key = `quality-promotion-${random}`;
  if (key.length > 128 || /\s/.test(key)) throw new Error("Unsafe promotion key");
  return key;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds));
}

function Panel({ children, description, title }: { children: ReactNode; description?: string; title: string }) {
  return (
    <section className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6">
      <header className="border-b border-slate-200 pb-4">
        <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        {description ? <p className="mt-1 text-sm leading-6 text-slate-600">{description}</p> : null}
      </header>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function ErrorPanel({ error, action }: { error: UiError; action?: ReactNode }) {
  return (
    <section className="rounded-xl border border-red-300 bg-red-50 p-4 text-red-950" role="alert">
      <h3 className="font-semibold">{error.title}</h3>
      <p className="mt-1 text-sm leading-6">{error.message}</p>
      {action ? <div className="mt-3">{action}</div> : null}
    </section>
  );
}

function Modal({ children, labelledBy }: { children: ReactNode; labelledBy: string }) {
  return (
    <div
      aria-labelledby={labelledBy}
      aria-modal="true"
      className="fixed inset-0 z-50 grid items-start overflow-y-auto bg-slate-950/70 p-4 sm:items-center sm:p-8"
      role="dialog"
    >
      <div className="mx-auto w-full max-w-3xl rounded-2xl bg-[#f8f8f4] p-5 shadow-2xl sm:p-7">
        {children}
      </div>
    </div>
  );
}

function ValidationBadge({
  semantic = true,
  status,
}: {
  semantic?: boolean;
  status: ReviewQuestion["validation"]["status"];
}) {
  const label =
    status === "ready" ? "Ready" : status === "needs_attention" ? "Needs attention" : "Failed check";
  const classes =
    status === "ready"
      ? "border-emerald-300 bg-emerald-50 text-emerald-950"
      : status === "needs_attention"
        ? "border-amber-300 bg-amber-50 text-amber-950"
        : "border-red-300 bg-red-50 text-red-950";
  const badge = <Badge className={classes}>{label}</Badge>;
  return semantic ? (
    <span aria-label="Validation status" role="status">
      {badge}
    </span>
  ) : (
    badge
  );
}

function findingCategory(code: string): "Answer check" | "Calculation check" | "Source check" | "Language check" | "Scope check" | "Quality check" {
  const normalized = code.toLowerCase();
  if (normalized.includes("scope") || normalized.includes("lesson") || normalized.includes("curriculum")) {
    return "Scope check";
  }
  if (
    normalized.includes("math") ||
    normalized.includes("calculation") ||
    normalized.includes("numeric") ||
    normalized.includes("equation") ||
    normalized.includes("unit_mismatch") ||
    normalized.includes("multiple_correct")
  ) {
    return "Calculation check";
  }
  if (
    normalized.includes("source") ||
    normalized.includes("ground") ||
    normalized.includes("factual") ||
    normalized.includes("unsupported_claim") ||
    normalized.includes("evidence")
  ) {
    return "Source check";
  }
  if (
    normalized.includes("language") ||
    normalized.includes("script") ||
    normalized.includes("wording") ||
    normalized.includes("ambiguous")
  ) {
    return "Language check";
  }
  if (normalized.includes("answer") || normalized.includes("marking")) return "Answer check";
  return "Quality check";
}

function findingStatus(status: TechnicalFinding["status"]): "Passed" | "Needs attention" | "Failed" {
  if (status === "pass") return "Passed";
  if (status === "warn") return "Needs attention";
  return "Failed";
}

function contentFromQuestion(question: ReviewQuestion): QuestionContent {
  if (question.content) {
    return {
      answer: question.content.answer,
      explanation: question.content.explanation,
      marking_guide: [...question.content.marking_guide],
      marks: question.content.marks,
      options: question.content.options.map((option) => ({
        option_id: option.option_id,
        text: option.text,
      })),
      question_type: question.content.question_type,
      stem: question.content.stem,
    };
  }
  return {
    answer: question.answer,
    explanation: question.explanation,
    marking_guide: [...question.marking_scheme.criteria],
    marks: question.marking_scheme.total_marks,
    options: question.options.map((option) => ({ option_id: option.label, text: option.text })),
    question_type: question.options.length ? "multiple_choice" : "structured_response",
    stem: question.stem,
  };
}

function editDraftFromQuestion(question: ReviewQuestion): EditDraft {
  const content = contentFromQuestion(question);
  return {
    answer: content.answer,
    explanation: content.explanation,
    markingGuide: content.marking_guide.join("\n"),
    marks: String(content.marks),
    options: content.options.map((option) => ({ ...option })),
    note: "",
    questionType: content.question_type,
    reasonCode: "",
    stem: content.stem,
  };
}

function scopeSummary(question: ReviewQuestion): string {
  const topic = question.scope.taxonomy || question.scope.lesson || question.scope.unit;
  return [`Grade ${question.scope.grade} ${safeText(question.scope.subject)}`, safeText(question.scope.lessons), safeText(topic)]
    .filter(Boolean)
    .join(" · ");
}

function semanticClaimLabel(claim: SemanticClaim): string {
  if (claim.claim_type === "answer") return "Answer";
  if (claim.claim_type === "explanation") return "Explanation";
  return "Marking guidance";
}

function semanticClaimStatus(status: SemanticClaim["status"]): string {
  if (status === "supported") return "Supported";
  if (status === "contradicted") return "Conflicts with source";
  if (status === "insufficient_evidence") return "Needs more evidence";
  return "Manual review needed";
}

function AnswerEvidence({ verification }: { verification: SemanticVerification }) {
  return (
    <section className="mt-3 rounded-lg border border-sky-200 bg-sky-50 p-3">
      <h5 className="font-semibold text-sky-950">Answer evidence</h5>
      <p className="mt-1 text-sm leading-6 text-sky-900">{safeText(verification.summary)}</p>
      {verification.claims.length ? (
        <ul className="mt-3 space-y-2">
          {verification.claims.slice(0, 32).map((claim) => (
            <li className="rounded-md border border-sky-200 bg-white p-3" key={claim.claim_id}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-semibold">{semanticClaimLabel(claim)}</p>
                <Badge className="border-sky-300 bg-sky-50 text-sky-950">
                  {semanticClaimStatus(claim.status)}
                </Badge>
              </div>
              <p className="mt-1 text-sm leading-6">{safeText(claim.summary)}</p>
              {claim.evidence_refs.length ? (
                <ul className="mt-2 space-y-1 text-xs text-slate-700">
                  {claim.evidence_refs.map((reference, index) => (
                    <li key={`${claim.claim_id}-${index}`}>
                      Reviewed source · page {reference.page_number}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-xs font-semibold text-amber-900">
                  No reviewed source citation was returned for this claim.
                </p>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm font-semibold">Review this answer manually.</p>
      )}
    </section>
  );
}

function QuestionTechnicalDetails({ question }: { question: ReviewQuestion }) {
  const technical = question.technical_details;
  return (
    <details className="mt-5 rounded-xl border border-slate-300 bg-slate-50 p-4">
      <summary className="cursor-pointer rounded-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-amber-500">
        Technical details
      </summary>
      <div className="mt-4 space-y-4 text-sm">
        <dl className="grid gap-3 sm:grid-cols-2">
          {[
            ["Generation run", technical.generation_run_id],
            ["Validation run", technical.validation_run_id ?? "Not created"],
            ["Review candidate", technical.candidate_id ?? "Not created"],
            ["Blueprint slot", technical.blueprint_slot_id],
            ["Provider", technical.provider],
            ["Model version", technical.model_version],
          ].map(([label, value]) => (
            <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-3" key={label}>
              <dt className="text-xs font-semibold text-slate-500">{label}</dt>
              <dd className="mt-1 break-all font-mono text-xs">{safeText(value)}</dd>
            </div>
          ))}
        </dl>
        <section>
          <h4 className="font-semibold">Context references</h4>
          {technical.context_ids.length ? (
            <ul className="mt-2 space-y-2">
              {technical.context_ids.slice(0, 50).map((contextId) => (
                <li className="break-all rounded-lg border border-slate-200 bg-white p-3 font-mono text-xs" key={contextId}>
                  {safeText(contextId)}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-slate-600">No context references recorded.</p>
          )}
        </section>
        <section>
          <h4 className="font-semibold">Validator records</h4>
          {technical.validator_findings.length ? (
            <ul className="mt-2 space-y-2">
              {technical.validator_findings.slice(0, 100).map((finding, index) => (
                <li className="rounded-lg border border-slate-200 bg-white p-3" key={`${finding.code}-${index}`}>
                  <p className="break-all font-mono text-xs">{safeText(finding.code)}</p>
                  <p className="mt-1 text-xs font-semibold uppercase">{finding.status}</p>
                  <p className="mt-1 leading-6">{safeText(finding.message)}</p>
                  {finding.evidence.length ? (
                    <dl className="mt-2 space-y-1 border-l border-slate-300 pl-3 text-xs">
                      {finding.evidence.slice(0, 20).flatMap((record, recordIndex) =>
                        Object.entries(record)
                          .filter(([key]) => key !== "details")
                          .slice(0, 20)
                          .map(([key, value]) => (
                            <div key={`${recordIndex}-${key}`}>
                              <dt className="inline font-semibold">{titleCase(key)}: </dt>
                              <dd className="inline break-words">{safeText(value)}</dd>
                            </div>
                          )),
                      )}
                    </dl>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-slate-600">No validator records returned.</p>
          )}
        </section>
      </div>
    </details>
  );
}

function PaperQueue({
  items,
  onSelect,
  selectedId,
}: {
  items: ReviewSummary[];
  onSelect: (paperId: string) => void;
  selectedId: string;
}) {
  return (
    <section aria-label="Review queue" className="space-y-4">
      <label className={fieldClass}>
        Paper to review
        <select className={inputClass} onChange={(event) => onSelect(event.target.value)} value={selectedId}>
          {items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title} — {item.scope_summary} — {item.approved_count}/{item.question_count} approved
            </option>
          ))}
        </select>
      </label>
      <ol className="grid gap-3 md:grid-cols-2">
        {items.map((item) => (
          <li key={item.id}>
            <button
              aria-pressed={selectedId === item.id}
              className={`w-full rounded-xl border p-4 text-left outline-none transition focus-visible:ring-2 focus-visible:ring-amber-500 ${
                selectedId === item.id
                  ? "border-slate-950 bg-slate-50"
                  : "border-slate-200 bg-white hover:border-slate-400"
              }`}
              onClick={() => onSelect(item.id)}
              type="button"
            >
              <span className="block font-semibold">{safeText(item.title)}</span>
              <span className="mt-1 block text-sm text-slate-600">
                Grade {item.grade} {safeText(item.subject)} · {safeText(item.scope_summary)}
              </span>
              <span className="mt-2 block text-xs font-semibold text-slate-600">
                {item.approved_count} of {item.question_count} approved · {titleCase(item.status)}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ReviewApproveStudio({ role }: { role: Role }) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [queue, setQueue] = useState<ReviewSummary[]>([]);
  const [selectedPaperId, setSelectedPaperId] = useState("");
  const [paper, setPaper] = useState<ReviewPaper | null>(null);
  const [questionIndex, setQuestionIndex] = useState(0);
  const [queueLoading, setQueueLoading] = useState(true);
  const [paperLoading, setPaperLoading] = useState(false);
  const [queueError, setQueueError] = useState<UiError | null>(null);
  const [paperError, setPaperError] = useState<UiError | null>(null);
  const [commandError, setCommandError] = useState<UiError | null>(null);
  const [busy, setBusy] = useState<
    "start" | "approve" | "edit" | "reject" | "regenerate" | "draft" | "promote" | "approve-example" | ""
  >("");
  const [notice, setNotice] = useState("");
  const [editDraft, setEditDraft] = useState<EditDraft | null>(null);
  const [editError, setEditError] = useState<UiError | null>(null);
  const [rejectReason, setRejectReason] = useState<ReviewReasonDraft | null>(null);
  const [regenerateReason, setRegenerateReason] = useState<ReviewReasonDraft | null>(null);
  const [draftCreated, setDraftCreated] = useState<DraftCreated | null>(null);
  const [qualityEvidence, setQualityEvidence] = useState<QualityEvidence | null>(null);
  const [promotionDraft, setPromotionDraft] = useState<PromotionDraft | null>(null);
  const [evalCase, setEvalCase] = useState<EvalCase | null>(null);
  const [qualityNotice, setQualityNotice] = useState("");

  const queueRequest = useRef(0);
  const paperRequest = useRef(0);
  const regenerationPoll = useRef(0);
  const regenerationAttempt = useRef<RegenerationAttempt | null>(null);
  const initialRequestedPaper = useRef(
    new URLSearchParams(globalThis.location?.search ?? "").get("paper") ?? "",
  );

  const currentQuestion = paper?.questions[questionIndex] ?? null;
  const allApproved = Boolean(
    paper?.questions.length && paper.questions.every((question) => question.review_state === "approved"),
  );

  const loadQueue = useCallback(async () => {
    const requestId = ++queueRequest.current;
    setQueueLoading(true);
    setQueueError(null);
    try {
      const outcome = await api.GET("/api/v1/admin/review-papers", {
        params: { query: { limit: LIST_LIMIT, offset: 0 } },
      });
      if (requestId !== queueRequest.current) return;
      if (outcome.error !== undefined) {
        setQueueError(reviewError(outcome.error, outcome.response, "queue"));
        return;
      }
      const items = outcome.data?.items ?? [];
      setQueue(items);
      const requestedPaper = initialRequestedPaper.current;
      initialRequestedPaper.current = "";
      setSelectedPaperId((current) => {
        if (requestedPaper) return requestedPaper;
        if (items.some((item) => item.id === current)) return current;
        return items[0]?.id ?? "";
      });
    } catch {
      if (requestId === queueRequest.current) setQueueError(networkError("queue"));
    } finally {
      if (requestId === queueRequest.current) setQueueLoading(false);
    }
  }, [api]);

  const fetchPaper = useCallback(
    async (paperId: string, options: { loading?: boolean; preferredQuestionId?: string } = {}) => {
      const requestId = ++paperRequest.current;
      if (options.loading !== false) setPaperLoading(true);
      setPaperError(null);
      try {
        const outcome = await api.GET("/api/v1/admin/review-papers/{paper_job_id}", {
          params: { path: { paper_job_id: paperId } },
        });
        if (requestId !== paperRequest.current) return null;
        if (outcome.error !== undefined) {
          setPaperError(reviewError(outcome.error, outcome.response, "paper"));
          return null;
        }
        if (!outcome.data || outcome.data.id !== paperId) {
          setPaperError({
            code: "review_paper_scope_mismatch",
            message: "The returned paper does not match the selected review item. Reload the queue.",
            retryable: true,
            title: "Paper mismatch",
          });
          return null;
        }
        const nextPaper = outcome.data;
        setPaper(nextPaper);
        setQuestionIndex((current) => {
          if (options.preferredQuestionId) {
            const preferred = nextPaper.questions.findIndex(
              (question) => question.id === options.preferredQuestionId,
            );
            if (preferred >= 0) return preferred;
          }
          return Math.min(current, Math.max(0, nextPaper.questions.length - 1));
        });
        return nextPaper;
      } catch {
        if (requestId === paperRequest.current) setPaperError(networkError("paper"));
        return null;
      } finally {
        if (requestId === paperRequest.current && options.loading !== false) setPaperLoading(false);
      }
    },
    [api],
  );

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadQueue(), 0);
    return () => {
      window.clearTimeout(timeout);
      queueRequest.current += 1;
      paperRequest.current += 1;
      regenerationPoll.current += 1;
    };
  }, [loadQueue]);

  useEffect(() => {
    regenerationPoll.current += 1;
    if (!selectedPaperId) return;
    const timeout = window.setTimeout(() => {
      setQuestionIndex(0);
      setNotice("");
      setCommandError(null);
      setDraftCreated(null);
      setQualityEvidence(null);
      setPromotionDraft(null);
      setEvalCase(null);
      setQualityNotice("");
      void fetchPaper(selectedPaperId);
    }, 0);
    return () => window.clearTimeout(timeout);
  }, [fetchPaper, selectedPaperId]);

  function updateQuestion(question: ReviewQuestion) {
    setPaper((current) =>
      current
        ? {
            ...current,
            questions: current.questions.map((candidate) =>
              candidate.id === question.id ? question : candidate,
            ),
          }
        : current,
    );
  }

  function rememberQualityEvidence(
    feedbackId: string | null | undefined,
    question: ReviewQuestion,
    reasonCode: ReviewReasonCode,
  ) {
    if (!feedbackId) return;
    const expectedFindingCodes = new Set(
      question.technical_details.validator_findings
        .filter((finding) => finding.status !== "pass")
        .map((finding) => finding.code),
    );
    expectedFindingCodes.add(REVIEW_REASON_FINDING_CODES[reasonCode]);
    setQualityEvidence({
      expectedFindingCodes: [...expectedFindingCodes].sort(),
      feedbackId,
      reasonCode,
      suggestedStatus: suggestedFindingStatus(question, reasonCode),
    });
    setEvalCase(null);
    setQualityNotice("");
  }

  async function runQuestionCommand(
    action: "start" | "approve",
    question: ReviewQuestion,
  ) {
    if (!paper || busy) return;
    setBusy(action);
    setCommandError(null);
    setNotice("");
    try {
      const path = { paper_job_id: paper.id, question_id: question.id };
      const outcome =
        action === "start"
          ? await api.POST(
              "/api/v1/admin/review-papers/{paper_job_id}/questions/{question_id}/start",
              { body: { expected_version: question.version }, params: { path } },
            )
          : await api.POST(
              "/api/v1/admin/review-papers/{paper_job_id}/questions/{question_id}/approve",
              {
                body: { expected_version: question.version, note: null },
                params: { path },
              },
            );
      if (outcome.error !== undefined) {
        setCommandError(reviewError(outcome.error, outcome.response, "command"));
        return;
      }
      if (!outcome.data) return;
      updateQuestion(outcome.data);
      setNotice(action === "start" ? "Review started." : "Question approved.");
      await fetchPaper(paper.id, { loading: false, preferredQuestionId: outcome.data.id });
      await loadQueue();
    } catch {
      setCommandError(networkError("command"));
    } finally {
      setBusy("");
    }
  }

  async function saveEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!paper || !currentQuestion || !editDraft || busy) return;
    const marks = Number(editDraft.marks);
    const reasonCode = editDraft.reasonCode;
    const note = editDraft.note.trim() || null;
    const markingGuide = editDraft.markingGuide
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);
    if (!editDraft.stem.trim() || !editDraft.answer.trim() || !editDraft.explanation.trim()) {
      setEditError({
        code: "review_content_incomplete",
        message: "Question, proposed answer, and explanation are required.",
        preserveDraft: true,
        retryable: false,
        title: "Complete the question",
      });
      return;
    }
    if (!Number.isInteger(marks) || marks < 1) {
      setEditError({
        code: "review_marks_invalid",
        message: "Marks must be a whole number greater than zero.",
        preserveDraft: true,
        retryable: false,
        title: "Check the marks",
      });
      return;
    }
    if (!reasonCode || (note?.length ?? 0) > MAX_REVIEW_NOTE_LENGTH) {
      setEditError({
        code: "review_reason_invalid",
        message: "Choose the educational reason. Keep the optional note concise.",
        preserveDraft: true,
        retryable: false,
        title: "Reason required",
      });
      return;
    }
    const content: QuestionContent = {
      answer: editDraft.answer.trim(),
      explanation: editDraft.explanation.trim(),
      marking_guide: markingGuide,
      marks,
      options: editDraft.options.map((option) => ({
        option_id: option.option_id,
        text: option.text.trim(),
      })),
      question_type: editDraft.questionType,
      stem: editDraft.stem.trim(),
    };
    setBusy("edit");
    setEditError(null);
    setNotice("");
    try {
      const outcome = await api.PATCH(
        "/api/v1/admin/review-papers/{paper_job_id}/questions/{question_id}",
        {
          body: {
            content,
            expected_version: currentQuestion.version,
            note,
            reason_code: reasonCode,
          },
          params: {
            path: { paper_job_id: paper.id, question_id: currentQuestion.id },
          },
        },
      );
      if (outcome.error !== undefined) {
        setEditError(reviewError(outcome.error, outcome.response, "command"));
        return;
      }
      if (!outcome.data) return;
      updateQuestion(outcome.data);
      rememberQualityEvidence(outcome.data.quality_feedback_id, outcome.data, reasonCode);
      setEditDraft(null);
      setNotice("Question changes saved. A fresh check is required.");
      await fetchPaper(paper.id, { loading: false, preferredQuestionId: outcome.data.id });
      await loadQueue();
    } catch {
      setEditError(networkError("command"));
    } finally {
      setBusy("");
    }
  }

  async function rejectQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!paper || !currentQuestion || rejectReason === null || busy) return;
    const reasonCode = rejectReason.reasonCode;
    const note = rejectReason.note.trim() || null;
    if (!reasonCode || (note?.length ?? 0) > MAX_REVIEW_NOTE_LENGTH) {
      setCommandError({
        code: "review_reason_invalid",
        message: "Choose why this question should be rejected. The note is optional.",
        retryable: false,
        title: "Rejection reason required",
      });
      return;
    }
    setBusy("reject");
    setCommandError(null);
    setNotice("");
    try {
      const outcome = await api.POST(
        "/api/v1/admin/review-papers/{paper_job_id}/questions/{question_id}/reject",
        {
          body: {
            expected_version: currentQuestion.version,
            note,
            reason_code: reasonCode,
          },
          params: {
            path: { paper_job_id: paper.id, question_id: currentQuestion.id },
          },
        },
      );
      if (outcome.error !== undefined) {
        setCommandError(reviewError(outcome.error, outcome.response, "command"));
        return;
      }
      if (!outcome.data) return;
      updateQuestion(outcome.data);
      rememberQualityEvidence(outcome.data.quality_feedback_id, outcome.data, reasonCode);
      setRejectReason(null);
      setNotice("Question rejected.");
      await fetchPaper(paper.id, { loading: false, preferredQuestionId: outcome.data.id });
      await loadQueue();
    } catch {
      setCommandError(networkError("command"));
    } finally {
      setBusy("");
    }
  }

  const pollRegeneration = useCallback(
    async (paperId: string, originalQuestionId: string, replacementQuestionId: string) => {
      const pollId = ++regenerationPoll.current;
      for (const wait of REGENERATION_POLL_DELAYS_MS) {
        if (wait) await delay(wait);
        if (pollId !== regenerationPoll.current) return;
        const refreshed = await fetchPaper(paperId, {
          loading: false,
          preferredQuestionId: replacementQuestionId,
        });
        if (!refreshed) return;
        const replacement = refreshed.questions.find(
          (question) =>
            question.id === replacementQuestionId ||
            (question.id !== originalQuestionId && question.number === currentQuestion?.number),
        );
        if (replacement && !replacement.requires_revalidation) {
          await loadQueue();
          return;
        }
      }
    },
    [currentQuestion?.number, fetchPaper, loadQueue],
  );

  async function regenerateQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!paper || !currentQuestion || regenerateReason === null || busy) return;
    const reasonCode = regenerateReason.reasonCode;
    const note = regenerateReason.note.trim() || null;
    if (!reasonCode || (note?.length ?? 0) > MAX_REVIEW_NOTE_LENGTH) {
      setCommandError({
        code: "review_reason_invalid",
        message: "Choose why a replacement is needed. The note is optional.",
        retryable: false,
        title: "Regeneration reason required",
      });
      return;
    }
    let stored = regenerationAttempt.current;
    if (
      !stored ||
      stored.questionId !== currentQuestion.id ||
      stored.version !== currentQuestion.aggregate_slot_version ||
      stored.reasonCode !== reasonCode ||
      stored.note !== note
    ) {
      try {
        stored = {
          idempotencyKey: secureRegenerationKey(),
          note,
          questionId: currentQuestion.id,
          reasonCode,
          version: currentQuestion.aggregate_slot_version,
        };
      } catch {
        setCommandError({
          code: "secure_randomness_unavailable",
          message: "This browser cannot create a safe regeneration request.",
          retryable: false,
          title: "Safe regeneration unavailable",
        });
        return;
      }
      regenerationAttempt.current = stored;
    }
    setBusy("regenerate");
    setCommandError(null);
    setNotice("");
    try {
      const outcome = await api.POST(
        "/api/v1/admin/review-papers/{paper_job_id}/questions/{question_id}/regenerate",
        {
          body: {
            expected_version: stored.version,
            note: stored.note,
            reason_code: stored.reasonCode,
          },
          params: {
            header: { "Idempotency-Key": stored.idempotencyKey },
            path: { paper_job_id: paper.id, question_id: stored.questionId },
          },
        },
      );
      if (outcome.error !== undefined) {
        setCommandError(reviewError(outcome.error, outcome.response, "regenerate"));
        return;
      }
      if (!outcome.data) return;
      rememberQualityEvidence(
        outcome.data.quality_feedback_id,
        currentQuestion,
        stored.reasonCode,
      );
      setRegenerateReason(null);
      setNotice("A replacement question is being prepared and checked.");
      setBusy("");
      void pollRegeneration(paper.id, stored.questionId, outcome.data.question_id);
    } catch {
      setCommandError(networkError("regenerate"));
    } finally {
      setBusy((current) => (current === "regenerate" ? "" : current));
    }
  }

  function openPromotion() {
    if (!qualityEvidence || busy) return;
    try {
      setPromotionDraft({
        ...qualityEvidence,
        defectCategory:
          qualityEvidence.suggestedStatus === "pass"
            ? "no_defect"
            : suggestedDefect(qualityEvidence.reasonCode),
        expectedStatus: qualityEvidence.suggestedStatus,
        idempotencyKey: securePromotionKey(),
      });
      setCommandError(null);
    } catch {
      setCommandError({
        code: "secure_randomness_unavailable",
        message: "This browser cannot create a safe quality-example request.",
        retryable: false,
        title: "Quality example unavailable",
      });
    }
  }

  async function promoteQualityEvidence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!promotionDraft || busy) return;
    setBusy("promote");
    setCommandError(null);
    try {
      const expectedFindingCodes =
        promotionDraft.expectedStatus === "pass"
          ? []
          : promotionDraft.expectedFindingCodes;
      const outcome = await api.POST(
        "/api/v1/admin/subject-quality/feedback/{feedback_id}/promote",
        {
          body: {
            defect_category: promotionDraft.defectCategory,
            expected_finding_codes: expectedFindingCodes,
            expected_status: promotionDraft.expectedStatus,
          },
          params: {
            header: { "Idempotency-Key": promotionDraft.idempotencyKey },
            path: { feedback_id: promotionDraft.feedbackId },
          },
        },
      );
      if (outcome.error !== undefined) {
        setCommandError(reviewError(outcome.error, outcome.response, "command"));
        return;
      }
      if (!outcome.data) return;
      setEvalCase(outcome.data);
      setPromotionDraft(null);
      setQualityNotice(
        "Draft quality example created. A second reviewer or administrator must approve it.",
      );
    } catch {
      setCommandError(networkError("command"));
    } finally {
      setBusy("");
    }
  }

  async function approveQualityExample() {
    if (!evalCase?.can_approve || busy) return;
    setBusy("approve-example");
    setCommandError(null);
    try {
      const outcome = await api.POST(
        "/api/v1/admin/subject-quality/eval-cases/{eval_case_id}/approve",
        {
          body: { expected_version: evalCase.version },
          params: { path: { eval_case_id: evalCase.eval_case_id } },
        },
      );
      if (outcome.error !== undefined) {
        setCommandError(reviewError(outcome.error, outcome.response, "command"));
        return;
      }
      if (!outcome.data) return;
      setEvalCase(outcome.data);
      setQualityNotice("Quality example approved for offline evaluation.");
    } catch {
      setCommandError(networkError("command"));
    } finally {
      setBusy("");
    }
  }

  async function createDraft() {
    if (!paper || !allApproved || busy) return;
    setBusy("draft");
    setCommandError(null);
    setNotice("");
    try {
      const outcome = await api.POST("/api/v1/admin/review-papers/{paper_job_id}/create-draft", {
        body: { expected_version: paper.version },
        params: { path: { paper_job_id: paper.id } },
      });
      if (outcome.error !== undefined) {
        setCommandError(reviewError(outcome.error, outcome.response, "draft"));
        return;
      }
      if (!outcome.data) return;
      setDraftCreated(outcome.data);
      setNotice("Draft created. It is ready in Published Papers.");
      await fetchPaper(paper.id, { loading: false });
      await loadQueue();
    } catch {
      setCommandError(networkError("draft"));
    } finally {
      setBusy("");
    }
  }

  const startRequired = Boolean(
    currentQuestion && ["validated", "awaiting_review"].includes(currentQuestion.review_state),
  );
  const questionLocked = Boolean(
    currentQuestion && ["approved", "rejected"].includes(currentQuestion.review_state),
  );
  const approvalAllowed = Boolean(
    currentQuestion &&
      currentQuestion.review_state === "in_review" &&
      currentQuestion.validation.status !== "failed_check" &&
      !currentQuestion.requires_revalidation &&
      currentQuestion.technical_details.candidate_id,
  );

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-5 py-8 sm:px-8 sm:py-10">
      <header className="rounded-3xl bg-slate-900 p-6 text-white shadow-lg sm:p-8">
        <p className="text-xs font-semibold tracking-[0.18em] text-amber-300 uppercase">
          Subject-quality review
        </p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Review &amp; Approve</h1>
        <p className="mt-3 max-w-4xl text-sm leading-6 text-slate-200 sm:text-base">
          Review each question with its proposed answer, explanation, marking, curriculum scope,
          sources, and quality checks. A warning needs human judgement; a failed check can never be
          approved.
        </p>
      </header>

      {queueLoading ? (
        <section className="rounded-2xl border border-slate-300 bg-white p-6" aria-live="polite">
          Loading the review queue…
        </section>
      ) : queueError ? (
        <ErrorPanel
          error={queueError}
          action={
            queueError.retryable ? (
              <button className={secondaryButton} onClick={() => void loadQueue()} type="button">
                Reload review queue
              </button>
            ) : undefined
          }
        />
      ) : !queue.length && !selectedPaperId ? (
        <Panel description="Generated papers will appear here after answer checks finish." title="No papers waiting for review">
          <Link className={primaryButton} href="/admin/generate-papers">
            Generate a paper
          </Link>
        </Panel>
      ) : (
        <Panel
          description="Choose a paper by its readable title and curriculum scope."
          title="Review queue"
        >
          {queue.length ? (
            <PaperQueue
              items={queue}
              onSelect={(paperId) => setSelectedPaperId(paperId)}
              selectedId={selectedPaperId}
            />
          ) : (
            <p className="text-sm text-slate-600">Opening the paper linked from generation…</p>
          )}
        </Panel>
      )}

      {paperLoading ? (
        <section className="rounded-2xl border border-slate-300 bg-white p-6" aria-live="polite">
          Loading the paper and answer checks…
        </section>
      ) : paperError ? (
        <ErrorPanel
          error={paperError}
          action={
            paperError.retryable && selectedPaperId ? (
              <button className={secondaryButton} onClick={() => void fetchPaper(selectedPaperId)} type="button">
                Reload paper
              </button>
            ) : undefined
          }
        />
      ) : paper ? (
        <section className="space-y-6">
          <header className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                  {safeText(paper.paper_reference)}
                </p>
                <h2 className="mt-1 text-2xl font-semibold">{safeText(paper.title)}</h2>
                <p className="mt-2 text-sm text-slate-600">
                  Grade {paper.grade} {safeText(paper.subject)} · {safeText(paper.medium)} · {safeText(paper.scope_summary)}
                </p>
              </div>
              <Badge className="border-blue-300 bg-blue-50 text-blue-950">{titleCase(paper.status)}</Badge>
            </div>
            <details className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
              <summary className="cursor-pointer text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-amber-500">
                Paper technical details
              </summary>
              <dl className="mt-3 grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
                {[
                  ["Curriculum", paper.technical_details.curriculum_version_id],
                  ["Paper blueprint", paper.technical_details.paper_blueprint_id],
                  ["Request fingerprint", paper.technical_details.request_fingerprint],
                  ["Aggregate version", paper.version],
                  ["Cost (microusd)", paper.technical_details.cost_microusd],
                  ["Total tokens", paper.technical_details.total_tokens],
                ].map(([label, value]) => (
                  <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-3" key={label}>
                    <dt className="font-semibold text-slate-500">{label}</dt>
                    <dd className="mt-1 break-all font-mono">{safeText(value)}</dd>
                  </div>
                ))}
              </dl>
            </details>
          </header>

          {notice ? (
            <p
              className="rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-sm font-semibold text-emerald-950"
              role="status"
            >
              {notice}
            </p>
          ) : null}
          {commandError ? (
            <ErrorPanel
              error={commandError}
              action={
                commandError.retryable && selectedPaperId ? (
                  <button
                    className={secondaryButton}
                    onClick={() => {
                      setCommandError(null);
                      void fetchPaper(selectedPaperId, { preferredQuestionId: currentQuestion?.id });
                    }}
                    type="button"
                  >
                    Load latest paper
                  </button>
                ) : undefined
              }
            />
          ) : null}

          {qualityEvidence ? (
            <section className="rounded-xl border border-blue-300 bg-blue-50 p-4 text-blue-950">
              <h3 className="font-semibold">Optional quality evidence</h3>
              <p className="mt-1 text-sm leading-6">
                Save this reviewed action as a candidate for the private offline quality checks.
                This is review evidence; it does not train or automatically change the model.
              </p>
              {qualityNotice ? (
                <p className="mt-3 rounded-lg border border-blue-300 bg-white p-3 text-sm font-semibold" role="status">
                  {qualityNotice}
                </p>
              ) : null}
              <div className="mt-3 flex flex-wrap gap-3">
                {!evalCase ? (
                  <button className={secondaryButton} disabled={Boolean(busy)} onClick={openPromotion} type="button">
                    Add to quality examples
                  </button>
                ) : null}
                {evalCase?.can_approve ? (
                  <button
                    className={primaryButton}
                    disabled={Boolean(busy)}
                    onClick={() => void approveQualityExample()}
                    type="button"
                  >
                    {busy === "approve-example" ? "Approving…" : "Approve quality example"}
                  </button>
                ) : null}
              </div>
              <details className="mt-3 rounded-lg border border-blue-200 bg-white p-3 text-xs">
                <summary className="cursor-pointer font-semibold">Quality evidence technical details</summary>
                <dl className="mt-2 grid gap-2 sm:grid-cols-2">
                  <div>
                    <dt>Feedback ID</dt>
                    <dd className="break-all font-mono">{qualityEvidence.feedbackId}</dd>
                  </div>
                  <div>
                    <dt>Eval case</dt>
                    <dd className="break-all font-mono">{evalCase?.eval_case_id ?? "Not promoted"}</dd>
                  </div>
                </dl>
              </details>
            </section>
          ) : null}

          {currentQuestion ? (
            <article
              aria-label={`Question ${questionIndex + 1} of ${paper.questions.length}`}
              className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-7"
              role="region"
            >
              <header className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 pb-5">
                <div>
                  <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                    Question {questionIndex + 1} of {paper.questions.length}
                  </p>
                  <h3 className="mt-2 max-w-4xl text-xl font-semibold leading-8">
                    {safeText(currentQuestion.stem)}
                  </h3>
                  <p className="mt-2 text-sm text-slate-600">{scopeSummary(currentQuestion)}</p>
                </div>
                <ValidationBadge status={currentQuestion.validation.status} />
              </header>

              <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(18rem,0.8fr)]">
                <div className="space-y-5">
                  <section>
                    <h4 className="font-semibold">Answer options</h4>
                    {currentQuestion.options.length ? (
                      <ol aria-label="Answer options" className="mt-3 space-y-2">
                        {currentQuestion.options.map((option) => (
                          <li className="rounded-xl border border-slate-200 bg-slate-50 p-3" key={option.label}>
                            <span className="font-semibold">{safeText(option.label)}</span>{" "}
                            <span>{safeText(option.text)}</span>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p className="mt-2 text-sm text-slate-600">This question does not use answer options.</p>
                    )}
                  </section>

                  <section className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4">
                    <h4 className="font-semibold text-emerald-950">Proposed answer</h4>
                    <p className="mt-2 whitespace-pre-wrap leading-7 text-emerald-950">
                      {safeText(currentQuestion.answer)}
                    </p>
                  </section>

                  <section className="rounded-xl border border-slate-200 p-4">
                    <h4 className="font-semibold">Explanation</h4>
                    <p className="mt-2 whitespace-pre-wrap leading-7 text-slate-700">
                      {safeText(currentQuestion.explanation)}
                    </p>
                  </section>
                </div>

                <aside className="space-y-5">
                  <section className="rounded-xl border border-slate-200 p-4">
                    <h4 className="font-semibold">
                      Marking · {currentQuestion.marking_scheme.total_marks}{" "}
                      {currentQuestion.marking_scheme.total_marks === 1 ? "mark" : "marks"}
                    </h4>
                    {currentQuestion.marking_scheme.criteria.length ? (
                      <ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-6 text-slate-700">
                        {currentQuestion.marking_scheme.criteria.map((criterion, index) => (
                          <li key={index}>{safeText(criterion)}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="mt-2 text-sm text-slate-600">No marking criteria were supplied.</p>
                    )}
                  </section>

                  <section className="rounded-xl border border-slate-200 p-4">
                    <h4 className="font-semibold">Sources used</h4>
                    {currentQuestion.sources.length ? (
                      <ul className="mt-3 space-y-2 text-sm">
                        {currentQuestion.sources.map((source, index) => (
                          <li className="rounded-lg bg-slate-50 p-3" key={`${source.filename}-${source.page}-${index}`}>
                            {safeText(source.title, safeText(source.filename))} — page {source.page}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="mt-2 text-sm text-red-800">No readable source reference was returned.</p>
                    )}
                  </section>
                </aside>
              </div>

              <section className="mt-6 rounded-xl border border-slate-300 bg-slate-50 p-4 sm:p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h4 className="font-semibold">Quality checks</h4>
                    <p className="mt-1 text-sm leading-6 text-slate-700">
                      {safeText(currentQuestion.validation.summary)}
                    </p>
                  </div>
                  <ValidationBadge semantic={false} status={currentQuestion.validation.status} />
                </div>

                {currentQuestion.validation.status === "needs_attention" ? (
                  <p className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm font-semibold text-amber-950">
                    Human judgement required. A warning is not a passed check; inspect the evidence before deciding.
                  </p>
                ) : null}
                {currentQuestion.validation.status === "failed_check" ? (
                  <p className="mt-3 rounded-lg border border-red-300 bg-red-50 p-3 text-sm font-semibold text-red-950">
                    Approval is blocked. Edit, reject, or regenerate this question and require fresh validation.
                  </p>
                ) : null}
                {currentQuestion.requires_revalidation ? (
                  <p className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm font-semibold text-amber-950">
                    Fresh validation required before approval.
                  </p>
                ) : null}

                {currentQuestion.technical_details.validator_findings.length ? (
                  <ul className="mt-4 grid gap-3 md:grid-cols-2">
                    {currentQuestion.technical_details.validator_findings.map((finding, index) => {
                      const status = findingStatus(finding.status);
                      const classes =
                        finding.status === "pass"
                          ? "border-emerald-200 bg-emerald-50"
                          : finding.status === "warn"
                            ? "border-amber-300 bg-amber-50"
                            : "border-red-300 bg-red-50";
                      return (
                        <li className={`rounded-lg border p-3 ${classes}`} key={`${finding.code}-${index}`}>
                          <p className="font-semibold">
                            {findingCategory(finding.code)}: {status}
                          </p>
                          <p className="mt-1 text-sm leading-6">{safeText(finding.message)}</p>
                          {finding.semantic_verification ? (
                            <AnswerEvidence verification={finding.semantic_verification} />
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                ) : currentQuestion.validation.findings.length ? (
                  <ul className="mt-4 list-disc space-y-2 pl-5 text-sm leading-6">
                    {currentQuestion.validation.findings.map((finding, index) => (
                      <li key={index}>{safeText(finding)}</li>
                    ))}
                  </ul>
                ) : null}

                <QuestionTechnicalDetails question={currentQuestion} />
              </section>

              <footer className="mt-6 flex flex-wrap items-center justify-between gap-4 border-t border-slate-200 pt-5">
                <div className="flex flex-wrap gap-2">
                  {startRequired ? (
                    <button
                      className={secondaryButton}
                      disabled={Boolean(busy) || currentQuestion.validation.status === "failed_check"}
                      onClick={() => void runQuestionCommand("start", currentQuestion)}
                      type="button"
                    >
                      {busy === "start" ? "Starting…" : "Start review"}
                    </button>
                  ) : (
                    <button className={secondaryButton} disabled type="button">
                      Start review
                    </button>
                  )}
                  <button
                    className={primaryButton}
                    disabled={!approvalAllowed || Boolean(busy)}
                    onClick={() => void runQuestionCommand("approve", currentQuestion)}
                    type="button"
                  >
                    {busy === "approve" ? "Approving…" : "Approve"}
                  </button>
                  <button
                    className={secondaryButton}
                    disabled={Boolean(busy) || startRequired || questionLocked || !currentQuestion.content}
                    onClick={() => {
                      setEditError(null);
                      setEditDraft(editDraftFromQuestion(currentQuestion));
                    }}
                    type="button"
                  >
                    Edit
                  </button>
                  <button
                    className={dangerButton}
                    disabled={Boolean(busy) || startRequired || questionLocked}
                    onClick={() => {
                      setCommandError(null);
                      setRejectReason({ note: "", reasonCode: "" });
                    }}
                    type="button"
                  >
                    Reject
                  </button>
                  <button
                    className={secondaryButton}
                    disabled={Boolean(busy) || questionLocked}
                    onClick={() => {
                      setCommandError(null);
                      setRegenerateReason({ note: "", reasonCode: "" });
                    }}
                    type="button"
                  >
                    Regenerate question
                  </button>
                </div>
                <div className="flex gap-2">
                  <button
                    className={secondaryButton}
                    disabled={questionIndex === 0 || Boolean(busy)}
                    onClick={() => {
                      setQuestionIndex((index) => Math.max(0, index - 1));
                      setNotice("");
                      setCommandError(null);
                      setQualityEvidence(null);
                      setEvalCase(null);
                      setQualityNotice("");
                    }}
                    type="button"
                  >
                    Previous
                  </button>
                  <button
                    className={secondaryButton}
                    disabled={questionIndex >= paper.questions.length - 1 || Boolean(busy)}
                    onClick={() => {
                      setQuestionIndex((index) => Math.min(paper.questions.length - 1, index + 1));
                      setNotice("");
                      setCommandError(null);
                      setQualityEvidence(null);
                      setEvalCase(null);
                      setQualityNotice("");
                    }}
                    type="button"
                  >
                    Next
                  </button>
                </div>
              </footer>
            </article>
          ) : (
            <Panel title="No questions returned">
              <p className="text-sm text-slate-600">
                This paper has no reviewable questions. Return to Generate Papers and inspect its safe failure state.
              </p>
            </Panel>
          )}

          {allApproved ? (
            <section
              aria-label="Paper ready for draft"
              className="rounded-2xl border-2 border-emerald-600 bg-emerald-50 p-5 sm:p-6"
            >
              <h2 className="text-xl font-semibold text-emerald-950">Every question is approved</h2>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-emerald-950">
                Create an explicit immutable draft. This does not silently publish the paper to learners.
              </p>
              {draftCreated || paper.draft ? (
                <div className="mt-4 space-y-3">
                  <Link
                    className={primaryButton}
                    href={`/admin/published-papers?paper=${draftCreated?.draft_id ?? paper.draft?.draft_id}`}
                  >
                    Go to Published Papers
                  </Link>
                  {draftCreated ? (
                    <details className="rounded-lg border border-emerald-300 bg-white p-3 text-sm">
                      <summary className="cursor-pointer font-semibold">Returned publication path</summary>
                      <p className="mt-2 break-all font-mono text-xs">{safeText(draftCreated.publication_path)}</p>
                    </details>
                  ) : null}
                </div>
              ) : (
                <button
                  className={`${primaryButton} mt-4`}
                  disabled={Boolean(busy)}
                  onClick={() => void createDraft()}
                  type="button"
                >
                  {busy === "draft" ? "Creating draft…" : "Create draft"}
                </button>
              )}
            </section>
          ) : null}
        </section>
      ) : null}

      {editDraft && currentQuestion ? (
        <Modal labelledBy="edit-question-heading">
          <form className="space-y-4" onSubmit={(event) => void saveEdit(event)}>
            <div>
              <p className="text-xs font-semibold tracking-wide text-amber-800 uppercase">Teacher edit</p>
              <h2 className="mt-1 text-2xl font-semibold" id="edit-question-heading">
                Edit question {questionIndex + 1}
              </h2>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                Saving an edit invalidates the previous check. Approval remains blocked until a fresh replacement is generated and validated.
              </p>
            </div>
            {editError ? (
              <ErrorPanel
                error={editError}
                action={
                  editError.retryable ? (
                    <button
                      className={secondaryButton}
                      onClick={() => {
                        if (paper) void fetchPaper(paper.id, { preferredQuestionId: currentQuestion.id });
                      }}
                      type="button"
                    >
                      Load latest question separately
                    </button>
                  ) : undefined
                }
              />
            ) : null}
            <label className={fieldClass}>
              Question
              <textarea
                className={`${inputClass} min-h-28`}
                maxLength={MAX_TEXT_LENGTH}
                onChange={(event) => setEditDraft({ ...editDraft, stem: event.target.value })}
                required
                value={editDraft.stem}
              />
            </label>
            {editDraft.options.length ? (
              <fieldset className="space-y-3">
                <legend className="text-sm font-semibold">Answer options</legend>
                {editDraft.options.map((option, index) => (
                  <label className={fieldClass} key={`${option.option_id}-${index}`}>
                    Option {option.option_id}
                    <input
                      className={inputClass}
                      maxLength={MAX_TEXT_LENGTH}
                      onChange={(event) =>
                        setEditDraft({
                          ...editDraft,
                          options: editDraft.options.map((candidate, candidateIndex) =>
                            candidateIndex === index
                              ? { ...candidate, text: event.target.value }
                              : candidate,
                          ),
                        })
                      }
                      required
                      value={option.text}
                    />
                  </label>
                ))}
              </fieldset>
            ) : null}
            <div className="grid gap-4 md:grid-cols-2">
              <label className={fieldClass}>
                Proposed answer
                <textarea
                  className={`${inputClass} min-h-24`}
                  maxLength={MAX_TEXT_LENGTH}
                  onChange={(event) => setEditDraft({ ...editDraft, answer: event.target.value })}
                  required
                  value={editDraft.answer}
                />
              </label>
              <label className={fieldClass}>
                Explanation
                <textarea
                  className={`${inputClass} min-h-24`}
                  maxLength={MAX_TEXT_LENGTH}
                  onChange={(event) => setEditDraft({ ...editDraft, explanation: event.target.value })}
                  required
                  value={editDraft.explanation}
                />
              </label>
            </div>
            <div className="grid gap-4 md:grid-cols-[10rem_minmax(0,1fr)]">
              <label className={fieldClass}>
                Marks
                <input
                  className={inputClass}
                  min={1}
                  onChange={(event) => setEditDraft({ ...editDraft, marks: event.target.value })}
                  required
                  type="number"
                  value={editDraft.marks}
                />
              </label>
              <label className={fieldClass}>
                Marking guide (one criterion per line)
                <textarea
                  className={`${inputClass} min-h-24`}
                  maxLength={MAX_TEXT_LENGTH}
                  onChange={(event) => setEditDraft({ ...editDraft, markingGuide: event.target.value })}
                  value={editDraft.markingGuide}
                />
              </label>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <label className={fieldClass}>
                Why are you changing this question?
                <select
                  className={inputClass}
                  onChange={(event) =>
                    setEditDraft({
                      ...editDraft,
                      reasonCode: event.target.value as ReviewReasonCode | "",
                    })
                  }
                  required
                  value={editDraft.reasonCode}
                >
                  <option value="">Choose a reason</option>
                  {REVIEW_REASON_CHOICES.map((choice) => (
                    <option key={choice.value} value={choice.value}>
                      {choice.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className={fieldClass}>
                Optional note
                <textarea
                  className={`${inputClass} min-h-20`}
                  maxLength={MAX_REVIEW_NOTE_LENGTH}
                  onChange={(event) => setEditDraft({ ...editDraft, note: event.target.value })}
                  value={editDraft.note}
                />
              </label>
            </div>
            <div className="flex flex-wrap justify-end gap-3">
              <button
                className={secondaryButton}
                disabled={busy === "edit"}
                onClick={() => {
                  setEditDraft(null);
                  setEditError(null);
                }}
                type="button"
              >
                Cancel
              </button>
              <button className={primaryButton} disabled={busy === "edit"} type="submit">
                {busy === "edit" ? "Saving…" : "Save changes"}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {rejectReason !== null && currentQuestion ? (
        <Modal labelledBy="reject-question-heading">
          <form className="space-y-4" onSubmit={(event) => void rejectQuestion(event)}>
            <h2 className="text-2xl font-semibold" id="reject-question-heading">
              Reject question {questionIndex + 1}
            </h2>
            <p className="text-sm leading-6 text-slate-600">
              Record a clear educational reason. Rejection is audited and never counts as approval.
            </p>
            <label className={fieldClass}>
              Why are you rejecting this question?
              <select
                className={inputClass}
                onChange={(event) =>
                  setRejectReason({
                    ...rejectReason,
                    reasonCode: event.target.value as ReviewReasonCode | "",
                  })
                }
                required
                value={rejectReason.reasonCode}
              >
                <option value="">Choose a reason</option>
                {REVIEW_REASON_CHOICES.map((choice) => (
                  <option key={choice.value} value={choice.value}>
                    {choice.label}
                  </option>
                ))}
              </select>
            </label>
            <label className={fieldClass}>
              Optional note
              <textarea
                className={`${inputClass} min-h-24`}
                maxLength={MAX_REVIEW_NOTE_LENGTH}
                onChange={(event) =>
                  setRejectReason({ ...rejectReason, note: event.target.value })
                }
                value={rejectReason.note}
              />
            </label>
            <div className="flex flex-wrap justify-end gap-3">
              <button
                className={secondaryButton}
                disabled={busy === "reject"}
                onClick={() => setRejectReason(null)}
                type="button"
              >
                Cancel
              </button>
              <button className={dangerButton} disabled={busy === "reject"} type="submit">
                {busy === "reject" ? "Rejecting…" : "Reject question"}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {regenerateReason !== null && currentQuestion ? (
        <Modal labelledBy="regenerate-question-heading">
          <form className="space-y-4" onSubmit={(event) => void regenerateQuestion(event)}>
            <h2 className="text-2xl font-semibold" id="regenerate-question-heading">
              Regenerate question {questionIndex + 1}
            </h2>
            <p className="text-sm leading-6 text-slate-600">
              A bounded replacement will use the same trusted subject and curriculum scope, then run the full answer and source checks again.
            </p>
            {commandError ? <ErrorPanel error={commandError} /> : null}
            <label className={fieldClass}>
              Why should this question be replaced?
              <select
                className={inputClass}
                onChange={(event) => {
                  setRegenerateReason({
                    ...regenerateReason,
                    reasonCode: event.target.value as ReviewReasonCode | "",
                  });
                  regenerationAttempt.current = null;
                }}
                required
                value={regenerateReason.reasonCode}
              >
                <option value="">Choose a reason</option>
                {REVIEW_REASON_CHOICES.map((choice) => (
                  <option key={choice.value} value={choice.value}>
                    {choice.label}
                  </option>
                ))}
              </select>
            </label>
            <label className={fieldClass}>
              Optional note
              <textarea
                className={`${inputClass} min-h-24`}
                maxLength={MAX_REVIEW_NOTE_LENGTH}
                onChange={(event) => {
                  setRegenerateReason({ ...regenerateReason, note: event.target.value });
                  regenerationAttempt.current = null;
                }}
                value={regenerateReason.note}
              />
            </label>
            <div className="flex flex-wrap justify-end gap-3">
              <button
                className={secondaryButton}
                disabled={busy === "regenerate"}
                onClick={() => setRegenerateReason(null)}
                type="button"
              >
                Cancel
              </button>
              <button className={primaryButton} disabled={busy === "regenerate"} type="submit">
                {busy === "regenerate" ? "Starting…" : "Regenerate"}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {promotionDraft ? (
        <Modal labelledBy="promote-quality-heading">
          <form className="space-y-4" onSubmit={(event) => void promoteQualityEvidence(event)}>
            <div>
              <p className="text-xs font-semibold tracking-wide text-blue-800 uppercase">
                Private quality evidence
              </p>
              <h2 className="mt-1 text-2xl font-semibold" id="promote-quality-heading">
                Add review evidence to quality examples
              </h2>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                This creates a draft example for repeatable offline validation checks. It does not
                train or automatically change the model, prompts, or checking thresholds.
              </p>
            </div>
            {commandError ? <ErrorPanel error={commandError} /> : null}
            <div className="grid gap-4 md:grid-cols-2">
              <label className={fieldClass}>
                Expected check result
                <select
                  className={inputClass}
                  onChange={(event) => {
                    const expectedStatus = event.target.value as FindingStatus;
                    setPromotionDraft({
                      ...promotionDraft,
                      defectCategory:
                        expectedStatus === "pass"
                          ? "no_defect"
                          : promotionDraft.defectCategory,
                      expectedStatus,
                    });
                  }}
                  value={promotionDraft.expectedStatus}
                >
                  <option value="pass">Passes all expected checks</option>
                  <option value="warn">Needs human attention</option>
                  <option value="fail">Fails a required check</option>
                </select>
              </label>
              <label className={fieldClass}>
                Defect category
                <select
                  className={inputClass}
                  disabled={promotionDraft.expectedStatus === "pass"}
                  onChange={(event) =>
                    setPromotionDraft({
                      ...promotionDraft,
                      defectCategory: event.target.value as DefectCategory,
                    })
                  }
                  value={promotionDraft.defectCategory}
                >
                  {DEFECT_CHOICES.map((choice) => (
                    <option key={choice.value} value={choice.value}>
                      {choice.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <details className="rounded-lg border border-slate-300 bg-white p-3 text-xs">
              <summary className="cursor-pointer font-semibold">Technical eval details</summary>
              <p className="mt-2 break-all font-mono">Feedback {promotionDraft.feedbackId}</p>
              <p className="mt-2">
                Expected finding codes: {promotionDraft.expectedFindingCodes.join(", ") || "None"}
              </p>
            </details>
            <div className="flex flex-wrap justify-end gap-3">
              <button
                className={secondaryButton}
                disabled={busy === "promote"}
                onClick={() => setPromotionDraft(null)}
                type="button"
              >
                Cancel
              </button>
              <button className={primaryButton} disabled={busy === "promote"} type="submit">
                {busy === "promote" ? "Creating…" : "Create draft quality example"}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      <footer className="rounded-2xl border border-slate-300 bg-white p-5 text-sm text-slate-600">
        Signed in as <span className="font-semibold capitalize text-slate-900">{role}</span>. Generated content is never published automatically; a human-approved draft remains required.
      </footer>
    </div>
  );
}
