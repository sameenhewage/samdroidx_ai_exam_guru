"use client";

import {
  createApiClient,
  type components,
} from "@exam-guru/api-client";
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
import { Button, Form } from "react-aria-components";

import { Badge } from "@/components/ui/badge";

type Exam = components["schemas"]["ExamConfigurationResponse"];
type Medium = components["schemas"]["MediumResponse"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type Candidate = components["schemas"]["ReviewCandidateResponse"];
type CandidateSummary = components["schemas"]["ReviewCandidateSummaryResponse"];
type CandidateState = CandidateSummary["state"];
type QuestionContent = components["schemas"]["QuestionContentRequest"];
type QuestionOption = components["schemas"]["QuestionOptionRequest"];
type Generation = components["schemas"]["GenerationRunResponse"];
type Validation = components["schemas"]["ValidationRunResponse"];
type ValidationSummary = components["schemas"]["ValidationRunSummaryResponse"];
type Finding = components["schemas"]["ValidationFindingResponse"];
type SemanticVerification = components["schemas"]["SemanticVerificationDetailsResponse"];
type SemanticClaim = components["schemas"]["SemanticClaimEvidenceResponse"];
type Role = "admin" | "reviewer";
type JsonObject = Record<string, unknown>;
type ApiOutcome = { error?: unknown; response: Response };
type CandidateOutcome = ApiOutcome & { data?: Candidate };
type UiError = { code: string; message: string; title: string };
type QueueFilters = {
  blueprintId: string;
  slotId: string;
  state: CandidateState | "";
};
type DraftState = {
  baseVersion: number;
  candidateId: string;
  content: QuestionContent;
  dirty: boolean;
};

const LIST_LIMIT = 50;
const VALIDATION_LIMIT = 100;
const FINDING_LIMIT = 100;
const MAX_DISPLAY_TEXT = 4_096;
const MAX_DISPLAY_RECORDS = 48;
const MAX_DISPLAY_FIELDS = 24;
const EMPTY_FILTERS: QueueFilters = { blueprintId: "", slotId: "", state: "" };
const states: ReadonlyArray<{ label: string; value: CandidateState | "" }> = [
  { label: "All states", value: "" },
  { label: "Validated", value: "validated" },
  { label: "In review", value: "in_review" },
  { label: "Approved", value: "approved" },
  { label: "Rejected", value: "rejected" },
];

const fieldClass = "grid gap-1.5 text-sm font-semibold text-slate-700";
const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 outline-none transition focus:border-amber-500 focus:ring-2 focus:ring-amber-200 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none transition hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-400 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const dangerButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-red-700 bg-red-700 px-4 py-2 text-sm font-semibold text-white outline-none transition hover:bg-red-800 focus-visible:ring-2 focus-visible:ring-red-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function objectArray(value: unknown): JsonObject[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, MAX_DISPLAY_RECORDS).flatMap((item) => {
    const object = asObject(item);
    return object ? [object] : [];
  });
}

function safeText(value: unknown, fallback = "Not recorded"): string {
  let text: string;
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string") text = value;
  else if (typeof value === "number" || typeof value === "boolean") text = String(value);
  else {
    try {
      text = JSON.stringify(value);
    } catch {
      return "Structured value unavailable";
    }
  }
  const plain = text.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "�");
  return plain.length > MAX_DISPLAY_TEXT ? `${plain.slice(0, MAX_DISPLAY_TEXT)}…` : plain;
}

function titleCase(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function displayDate(value: string | null): string {
  if (!value) return "Not yet";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? safeText(value)
    : new Intl.DateTimeFormat("en", {
        dateStyle: "medium",
        timeStyle: "medium",
        timeZone: "UTC",
      }).format(date);
}

function detailCode(error: unknown): string {
  const detail = asObject(asObject(error)?.detail);
  return typeof detail?.code === "string" ? detail.code : "request_failed";
}

function firstFailure(outcomes: readonly ApiOutcome[]): ApiOutcome | undefined {
  return outcomes.find((outcome) => outcome.error !== undefined);
}

function apiError(
  error: unknown,
  status: number,
  surface: "workspace" | "queue" | "detail" | "evidence" | "create" | "command",
): UiError {
  const code = detailCode(error);
  if (status === 401) {
    return {
      code: "authentication_required",
      message: "Your content-review session expired. Sign in again before retrying.",
      title: "Authentication required",
    };
  }
  if (status === 403) {
    return {
      code: "permission_denied",
      message:
        surface === "workspace" || surface === "queue" || surface === "detail" || surface === "evidence"
          ? "This account cannot inspect review candidates. Ask an administrator to verify content:review access."
          : "This account cannot perform content-review commands. Ask an administrator to verify content:review access.",
      title:
        surface === "workspace"
          ? "Reviewer workspace permission required"
          : surface === "queue"
            ? "Review queue permission required"
            : surface === "detail" || surface === "evidence"
              ? "Review evidence permission required"
              : "Content review permission required",
    };
  }
  if (status === 404) {
    return {
      code,
      message:
        "The selected curriculum, candidate, generation run, or validation report is no longer available in this scope. Reload persisted records.",
      title: "Review resource not found",
    };
  }
  if (status === 409) {
    const conflicts: Record<string, UiError> = {
      review_candidate_idempotency_conflict: {
        code,
        message: "The validation report is already linked to inconsistent candidate data. Reload before retrying.",
        title: "Candidate identity conflict",
      },
      review_candidate_state_conflict: {
        code,
        message: "The candidate is no longer in a state that accepts this command. Reload authoritative history.",
        title: "Candidate state changed",
      },
      review_candidate_version_conflict: {
        code,
        message: "Another reviewer changed this candidate. Choose how to reconcile your local draft with the authoritative version.",
        title: "Authoritative version changed",
      },
      review_validation_not_passed: {
        code,
        message: "A failed validation report cannot create a review candidate.",
        title: "Non-failing validation required",
      },
      review_upstream_integrity_invalid: {
        code,
        message: "Persisted generation or validation lineage could not be reconstructed safely.",
        title: "Review lineage conflict",
      },
    };
    return (
      conflicts[code] ?? {
        code,
        message: "Authoritative review state changed. Reload persisted history before retrying.",
        title: "Review state conflict",
      }
    );
  }
  if (status === 422) {
    return {
      code,
      message:
        surface === "create"
          ? "The server rejected this validation selection. Select persisted PASS or WARN evidence from the active curriculum."
          : "The server rejected the bounded review content or command. Check required fields without changing locked type or marks.",
      title: surface === "create" ? "Candidate creation rejected" : "Candidate update rejected",
    };
  }
  return {
    code,
    message: "The review request could not be completed. Retry or contact an administrator if it persists.",
    title:
      surface === "workspace"
        ? "Reviewer workspace unavailable"
        : surface === "queue"
          ? "Review queue unavailable"
          : surface === "detail"
            ? "Candidate detail unavailable"
            : surface === "evidence"
              ? "Candidate evidence unavailable"
              : surface === "create"
                ? "Candidate creation failed"
                : "Review command failed",
  };
}

function networkError(
  surface: "workspace" | "queue" | "detail" | "evidence" | "create" | "command",
): UiError {
  return {
    code: "network_error",
    message: "The API could not be reached. Check the connection and retry without losing the local draft.",
    title:
      surface === "workspace"
        ? "Reviewer workspace unavailable"
        : surface === "queue"
          ? "Review queue unavailable"
          : surface === "detail"
            ? "Candidate detail unavailable"
            : surface === "evidence"
              ? "Candidate evidence unavailable"
              : surface === "create"
                ? "Candidate creation connection failed"
                : "Review command connection failed",
  };
}

function contentCopy(value: Candidate["current_content"]): QuestionContent {
  return {
    answer: value.answer,
    explanation: value.explanation,
    marking_guide: [...value.marking_guide],
    marks: value.marks,
    options: value.options.map((option) => ({ ...option })),
    question_type: value.question_type,
    stem: value.stem,
  };
}

function summaryFromCandidate(value: Candidate): CandidateSummary {
  return {
    blueprint_id: value.blueprint_id,
    blueprint_slot_id: value.blueprint_slot_id,
    blueprint_version: value.blueprint_version,
    created_at: value.created_at,
    created_by: value.created_by,
    current_revision: value.current_revision,
    current_revision_created_at:
      value.revisions[value.revisions.length - 1]?.created_at ?? value.created_at,
    curriculum_version_id: value.curriculum_version_id,
    generation_attempt_id: value.generation_attempt_id,
    generation_run_id: value.generation_run_id,
    id: value.id,
    marks: value.current_content.marks,
    paper_blueprint_id: value.paper_blueprint_id,
    question_type: value.current_content.question_type,
    state: value.state,
    stem_preview: value.current_content.stem.slice(0, 512),
    validation_run_id: value.validation_run_id,
    version: value.version,
  };
}

function Panel({
  ariaLabel,
  children,
  description,
  title,
}: {
  ariaLabel?: string;
  children: ReactNode;
  description?: string;
  title: string;
}) {
  return (
    <section
      aria-label={ariaLabel}
      className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6"
    >
      <header className="border-b border-slate-200 pb-4">
        <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        {description ? <p className="mt-1 text-sm leading-6 text-slate-600">{description}</p> : null}
      </header>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function ErrorPanel({
  error,
  onRetry,
  retryLabel,
}: {
  error: UiError;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <section className="rounded-xl border border-red-300 bg-red-50 p-4 text-red-950" role="alert">
      <h2 className="font-semibold">{error.title}</h2>
      <p className="mt-1 text-sm leading-6">{error.message}</p>
      <p className="mt-2 font-mono text-xs">Code: {error.code}</p>
      {onRetry && retryLabel ? (
        <Button className={`${secondaryButton} mt-3 border-red-300`} onPress={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
    </section>
  );
}

function StateBadge({ state }: { state: CandidateState }) {
  const classes =
    state === "approved"
      ? "border-emerald-300 bg-emerald-50 text-emerald-900"
      : state === "rejected"
        ? "border-red-300 bg-red-50 text-red-950"
        : state === "in_review"
          ? "border-blue-300 bg-blue-50 text-blue-950"
          : "border-amber-300 bg-amber-50 text-amber-950";
  return <Badge className={classes}>{titleCase(state)}</Badge>;
}

function ValidationBadge({ status }: { status: Validation["overall_status"] }) {
  const classes =
    status === "pass"
      ? "border-emerald-300 bg-emerald-50 text-emerald-900"
      : status === "warn"
        ? "border-amber-300 bg-amber-50 text-amber-950"
        : "border-red-300 bg-red-50 text-red-950";
  return <Badge className={classes}>{titleCase(status)}</Badge>;
}

function Definition({ label, mono = false, value }: { label: string; mono?: boolean; value: ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <dt className="text-xs font-semibold text-slate-500">{label}</dt>
      <dd className={`mt-1 break-words whitespace-pre-wrap text-sm ${mono ? "font-mono text-xs" : ""}`}>
        {value}
      </dd>
    </div>
  );
}

function PlainRecord({ record }: { record: JsonObject }) {
  return (
    <dl className="grid gap-2 sm:grid-cols-2">
      {Object.entries(record)
        .slice(0, MAX_DISPLAY_FIELDS)
        .map(([key, value]) => (
          <Definition key={key} label={titleCase(key)} mono value={safeText(value)} />
        ))}
    </dl>
  );
}

function CandidateQueue({
  candidates,
  onSelect,
  selectedId,
}: {
  candidates: CandidateSummary[];
  onSelect: (candidateId: string) => void;
  selectedId: string;
}) {
  if (!candidates.length) {
    return (
      <div className="rounded-xl border border-dashed border-slate-400 p-4">
        <h3 className="font-semibold">No review candidates match</h3>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          Change the state, blueprint, or slot filter, or create a candidate from persisted non-failing evidence.
        </p>
      </div>
    );
  }
  return (
    <ol className="space-y-3">
      {candidates.map((candidate) => (
        <li key={candidate.id}>
          <button
            aria-label={`Select review candidate ${candidate.id}`}
            aria-pressed={candidate.id === selectedId}
            className={`w-full rounded-xl border p-4 text-left outline-none transition focus-visible:ring-2 focus-visible:ring-amber-500 ${
              candidate.id === selectedId
                ? "border-slate-950 bg-slate-50"
                : "border-slate-200 bg-white hover:border-slate-400 hover:bg-slate-50"
            }`}
            onClick={() => onSelect(candidate.id)}
            type="button"
          >
            <span className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-mono text-xs text-slate-600">{safeText(candidate.blueprint_slot_id)}</span>
              <StateBadge state={candidate.state} />
            </span>
            <span className="mt-3 block break-words text-sm font-semibold">
              {safeText(candidate.stem_preview)}
            </span>
            <span className="mt-2 block text-xs text-slate-600">
              {titleCase(candidate.question_type)} · {candidate.marks} marks · revision {candidate.current_revision} · version {candidate.version}
            </span>
          </button>
        </li>
      ))}
    </ol>
  );
}

function GeneratedEvidence({ generation }: { generation: Generation }) {
  const generated = asObject(generation.candidate);
  const answer = asObject(generated?.answer);
  const options = objectArray(generated?.options);
  const marking = asObject(generated?.marking);
  return (
    <div className="space-y-5">
      <section
        aria-label="Generated revision 1 evidence"
        className="rounded-2xl border border-slate-300 bg-white p-5"
      >
        <h3 className="text-lg font-semibold">Generated revision 1 question and answer</h3>
        <p className="mt-1 text-sm leading-6 text-slate-600">
          This is the immutable generated output that automated validation evaluated, rendered as plain bounded text.
        </p>
        {generated ? (
          <div className="mt-4 space-y-4">
            <dl className="grid gap-3 sm:grid-cols-2">
              <Definition label="Question type" value={safeText(generated.question_type)} />
              <Definition label="Stem" value={safeText(generated.stem)} />
            </dl>
            <div>
              <h4 className="text-sm font-semibold">Generated options</h4>
              {options.length ? (
                <ol className="mt-2 space-y-2">
                  {options.map((option, index) => (
                    <li className="rounded-lg border border-slate-200 bg-slate-50 p-3" key={index}>
                      <span className="font-mono text-xs">{safeText(option.option_id, `Option ${index + 1}`)}</span>
                      <p className="mt-1 whitespace-pre-wrap break-words text-sm">{safeText(option.text)}</p>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="mt-2 text-sm text-slate-600">No generated options recorded.</p>
              )}
            </div>
            <div className="grid gap-4 lg:grid-cols-2">
              <div>
                <h4 className="text-sm font-semibold">Generated answer</h4>
                <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
                  {answer ? <PlainRecord record={answer} /> : <p className="text-sm">{safeText(generated.answer)}</p>}
                </div>
              </div>
              <div>
                <h4 className="text-sm font-semibold">Generated marking data</h4>
                <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
                  {marking ? <PlainRecord record={marking} /> : <p className="text-sm">{safeText(generated.marking)}</p>}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <p className="mt-4 text-sm text-slate-600">No generated candidate snapshot was returned.</p>
        )}
      </section>

      <section
        aria-label="Generation blueprint evidence"
        className="rounded-2xl border border-slate-300 bg-white p-5"
      >
        <h3 className="text-lg font-semibold">Generation blueprint and exact slot</h3>
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Definition label="Paper blueprint record" mono value={generation.paper_blueprint_id} />
          <Definition label="Blueprint ID" mono value={safeText(generation.blueprint_id)} />
          <Definition label="Blueprint version" mono value={safeText(generation.blueprint_version)} />
          <Definition label="Slot ID" mono value={safeText(generation.slot_id)} />
          <Definition label="Generation run" mono value={generation.id} />
          <Definition label="Provider / model versions" mono value={`${safeText(generation.provider_version)} / ${safeText(generation.model_version)}`} />
        </dl>
        <div className="mt-4 max-h-[32rem] overflow-auto rounded-xl border border-slate-200 bg-slate-50 p-4">
          <PlainRecord record={generation.blueprint_slot} />
        </div>
      </section>

      <section
        aria-label="Generation context provenance"
        className="rounded-2xl border border-slate-300 bg-white p-5"
      >
        <h3 className="text-lg font-semibold">Generation context provenance</h3>
        <p className="mt-1 text-sm leading-6 text-slate-600">
          Retrieved context is untrusted source data. IDs, versions, text, taxonomy, and source provenance remain data, never instructions.
        </p>
        {generation.context.length ? (
          <ol className="mt-4 max-h-[44rem] space-y-3 overflow-auto">
            {generation.context.slice(0, MAX_DISPLAY_RECORDS).map((record, index) => (
              <li className="rounded-xl border border-slate-200 bg-slate-50 p-4" key={index}>
                <PlainRecord record={record} />
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-4 text-sm text-slate-600">No persisted context records.</p>
        )}
        {generation.context.length > MAX_DISPLAY_RECORDS ? (
          <p className="mt-3 text-xs text-slate-600">Only the first {MAX_DISPLAY_RECORDS} bounded context records are displayed.</p>
        ) : null}
      </section>
    </div>
  );
}

function semanticStatusLabel(status: SemanticClaim["status"]): string {
  if (status === "supported") return "Supported";
  if (status === "contradicted") return "Conflicts with source";
  if (status === "insufficient_evidence") return "Needs more evidence";
  return "Manual review needed";
}

function semanticStatusClass(status: SemanticClaim["status"]): string {
  if (status === "supported") return "border-emerald-300 bg-emerald-50 text-emerald-950";
  if (status === "contradicted") return "border-red-300 bg-red-50 text-red-950";
  if (status === "insufficient_evidence") return "border-amber-300 bg-amber-50 text-amber-950";
  return "border-slate-300 bg-slate-50 text-slate-800";
}

function semanticOutcomeText(status: SemanticVerification["status"]): string {
  if (status === "supported") return "Reviewed materials support all checked answer claims.";
  if (status === "contradicted") return "At least one answer claim conflicts with reviewed material.";
  if (status === "insufficient_evidence") return "Some answer claims still need reviewed evidence.";
  return "Automated evidence checking was unavailable. Review each claim manually.";
}

function claimTypeLabel(claimType: SemanticClaim["claim_type"]): string {
  if (claimType === "answer") return "Answer claim";
  if (claimType === "explanation") return "Explanation claim";
  return "Marking guidance claim";
}

function SemanticClaimEvidence({ verification }: { verification: SemanticVerification }) {
  return (
    <section
      aria-labelledby="answer-evidence-check-heading"
      className="mt-4 rounded-xl border border-sky-300 bg-sky-50 p-4 text-sky-950"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h5 className="font-semibold" id="answer-evidence-check-heading">
          Answer evidence check
        </h5>
        <span
          className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${semanticStatusClass(verification.status)}`}
        >
          {semanticStatusLabel(verification.status)}
        </span>
      </div>
      <p className="mt-2 text-sm font-semibold leading-6">{semanticOutcomeText(verification.status)}</p>
      <p className="mt-1 text-sm leading-6 text-sky-900">{safeText(verification.summary)}</p>
      {verification.claims.length ? (
        <ol className="mt-4 space-y-3">
          {verification.claims.slice(0, MAX_DISPLAY_RECORDS).map((claim) => (
            <li className="rounded-lg border border-sky-200 bg-white p-3" key={claim.claim_id}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h6 className="font-semibold">{claimTypeLabel(claim.claim_type)}</h6>
                <span
                  className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${semanticStatusClass(claim.status)}`}
                >
                  {semanticStatusLabel(claim.status)}
                </span>
              </div>
              <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">
                {safeText(claim.summary)}
              </p>
              {claim.evidence_refs.length ? (
                <ul className="mt-2 space-y-2 text-sm">
                  {claim.evidence_refs.map((reference, index) => (
                    <li className="rounded-md bg-sky-50 px-3 py-2" key={`${claim.claim_id}-${index}`}>
                      <span className="font-semibold">Reviewed source · page {reference.page_number}</span>
                      <details className="mt-1 text-xs text-slate-600">
                        <summary className="cursor-pointer font-semibold">Source reference</summary>
                        <p className="mt-1 break-all font-mono">
                          {safeText(reference.source_document_id)} · {safeText(reference.context_id)}
                        </p>
                      </details>
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
        </ol>
      ) : (
        <p className="mt-3 text-sm font-semibold">No individual claims were available; review manually.</p>
      )}
      <details className="mt-4 border-t border-sky-200 pt-3 text-xs text-slate-600">
        <summary className="cursor-pointer font-semibold">Technical verification details</summary>
        <dl className="mt-2 grid gap-2 sm:grid-cols-2">
          <Definition label="Schema" mono value={safeText(verification.schema_version)} />
          <Definition
            label="Claim decomposition"
            mono
            value={safeText(verification.decomposition_version)}
          />
          <Definition label="Failure code" mono value={safeText(verification.failure_code, "None")} />
          <Definition
            label="Verifier"
            mono
            value={safeText(verification.lineage?.verifier_id, "Not configured")}
          />
          <Definition
            label="Model version"
            mono
            value={safeText(verification.lineage?.model_version, "Not configured")}
          />
        </dl>
      </details>
    </section>
  );
}

function TechnicalFindingEvidence({ evidence }: { evidence: Finding["evidence"] }) {
  return (
    <details className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <summary className="cursor-pointer text-sm font-semibold text-slate-700">
        Technical finding evidence
      </summary>
      <div className="mt-3 space-y-2">
        {evidence.slice(0, MAX_DISPLAY_RECORDS).map((record, index) => (
          <div className="rounded-lg bg-white p-3" key={index}>
            <PlainRecord
              record={{
                location: record.location,
                expected: record.expected,
                observed: record.observed,
              }}
            />
          </div>
        ))}
      </div>
    </details>
  );
}

function ValidationEvidence({ findings, validation }: { findings: Finding[]; validation: Validation }) {
  return (
    <section
      aria-label="P8 validation report and findings"
      className="rounded-2xl border border-slate-300 bg-white p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Immutable P8 report</p>
          <h3 className="mt-1 text-lg font-semibold">Automated validation report and findings</h3>
        </div>
        <ValidationBadge status={validation.overall_status} />
      </div>
      <p className="mt-3 text-sm leading-6 text-slate-600">
        The report is evidence for generated revision 1, not a human decision and not permission to publish.
      </p>
      <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Definition label="Validation run" mono value={validation.id} />
        <Definition label="Pipeline version" mono value={safeText(validation.pipeline_version)} />
        <Definition label="Report schema" mono value={safeText(validation.report_schema_version)} />
        <Definition label="Counts" value={`${validation.finding_count} findings · ${validation.validator_count} validators`} />
        <Definition label="Grounding sources" value={validation.grounding_source_count} />
        <Definition label="Created (UTC)" value={displayDate(validation.created_at)} />
        <Definition label="Candidate fingerprint" mono value={safeText(validation.candidate_fingerprint)} />
        <Definition label="Input fingerprint" mono value={safeText(validation.input_fingerprint)} />
        <Definition label="Report fingerprint" mono value={safeText(validation.report_fingerprint)} />
      </dl>
      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-950">
          <h4 className="font-semibold">Recorded limitations</h4>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6">
            {validation.limitations.slice(0, 16).map((limitation, index) => (
              <li key={index}>{safeText(limitation)}</li>
            ))}
          </ul>
        </div>
        <div className="rounded-xl border border-slate-200 p-4">
          <h4 className="font-semibold">Validator lineage</h4>
          <ul className="mt-2 space-y-2">
            {validation.validator_lineage.slice(0, 32).map((validator, index) => (
              <li className="break-words font-mono text-xs" key={index}>
                {safeText(validator.validator_id)} · {safeText(validator.validator_version)}
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="mt-6">
        <h4 className="font-semibold">Append-only findings</h4>
        {findings.length ? (
          <ol className="mt-3 max-h-[44rem] space-y-3 overflow-auto">
            {findings.slice(0, FINDING_LIMIT).map((item) => (
              <li className="rounded-xl border border-slate-200 p-4" key={item.id}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-xs">{item.ordinal}. {safeText(item.code)}</span>
                  <ValidationBadge status={item.status} />
                </div>
                <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">{safeText(item.message)}</p>
                <p className="mt-2 font-mono text-xs text-slate-600">
                  {safeText(item.validator_id)} · {safeText(item.validator_version)}
                </p>
                {item.semantic_verification ? (
                  <SemanticClaimEvidence verification={item.semantic_verification} />
                ) : null}
                <TechnicalFindingEvidence evidence={item.evidence} />
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-2 text-sm text-slate-600">No findings were returned for this report.</p>
        )}
        {validation.finding_count > findings.length ? (
          <p className="mt-3 text-xs text-slate-600">
            Displaying {findings.length} of {validation.finding_count} findings within the API display bound.
          </p>
        ) : null}
      </div>
    </section>
  );
}

function CandidateHistory({ candidate }: { candidate: Candidate }) {
  const decision = [...candidate.events]
    .reverse()
    .find((event) => event.action === "approved" || event.action === "rejected");
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(18rem,0.6fr)]">
      <section
        aria-label="Candidate revisions and events"
        className="rounded-2xl border border-slate-300 bg-white p-5"
      >
        <h3 className="text-lg font-semibold">Candidate revisions and events</h3>
        <div className="mt-4 grid gap-5 lg:grid-cols-2">
          <div>
            <h4 className="font-semibold">Immutable revisions</h4>
            <ol className="mt-3 max-h-[40rem] space-y-3 overflow-auto">
              {candidate.revisions.slice(0, MAX_DISPLAY_RECORDS).map((revision) => (
                <li className="rounded-xl border border-slate-200 bg-slate-50 p-4" key={revision.revision}>
                  <p className="font-semibold">Revision {revision.revision}</p>
                  <p className="mt-1 font-mono text-xs text-slate-600">
                    Candidate version {revision.candidate_version} · {displayDate(revision.created_at)}
                  </p>
                  <p className="mt-2 text-sm">{safeText(revision.reason, "Generated revision")}</p>
                  <dl className="mt-3 grid gap-2">
                    <Definition label="Stem" value={safeText(revision.content.stem)} />
                    <Definition label="Answer" value={safeText(revision.content.answer)} />
                    <Definition label="Explanation" value={safeText(revision.content.explanation)} />
                    <Definition label="Reviewer" mono value={safeText(revision.reviewer_id, "Generator")} />
                  </dl>
                </li>
              ))}
            </ol>
          </div>
          <div>
            <h4 className="font-semibold">Append-only review events</h4>
            {candidate.events.length ? (
              <ol className="mt-3 max-h-[40rem] space-y-3 overflow-auto">
                {candidate.events.slice(0, MAX_DISPLAY_RECORDS).map((event, index) => (
                  <li className="rounded-xl border border-slate-200 p-4" key={`${event.candidate_version}-${index}`}>
                    <div className="flex items-center justify-between gap-2">
                      <p className="font-semibold">{titleCase(event.action)}</p>
                      <span className="font-mono text-xs">v{event.candidate_version}</span>
                    </div>
                    <p className="mt-1 text-xs text-slate-600">{displayDate(event.created_at)}</p>
                    <p className="mt-2 whitespace-pre-wrap break-words text-sm">{safeText(event.reason, "No note recorded")}</p>
                    <p className="mt-2 break-words font-mono text-xs text-slate-600">Reviewer {event.reviewer_id}</p>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="mt-3 text-sm text-slate-600">No human review events yet.</p>
            )}
          </div>
        </div>
      </section>
      <section
        aria-label="Review decision"
        className="rounded-2xl border border-slate-300 bg-white p-5"
      >
        <h3 className="text-lg font-semibold">Review decision</h3>
        {decision ? (
          <div className="mt-4">
            <StateBadge state={decision.action === "approved" ? "approved" : "rejected"} />
            <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-6">
              {safeText(decision.reason, "No decision note recorded")}
            </p>
            <dl className="mt-4 grid gap-3">
              <Definition label="Decision version" value={decision.candidate_version} />
              <Definition label="Reviewer" mono value={decision.reviewer_id} />
              <Definition label="Recorded (UTC)" value={displayDate(decision.created_at)} />
            </dl>
          </div>
        ) : (
          <div className="mt-4 rounded-xl border border-dashed border-slate-400 p-4">
            <p className="font-semibold">No final decision recorded</p>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              Starting or editing review does not imply approval or publication.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

function editableContent(value: QuestionContent): QuestionContent {
  return {
    ...value,
    marking_guide: [...value.marking_guide],
    options: value.options.map((option) => ({ ...option })),
  };
}

function normalizedContent(value: QuestionContent): { content?: QuestionContent; error?: UiError } {
  const stem = value.stem.trim();
  const explanation = value.explanation.trim();
  const answer = value.answer.trim();
  const options = value.options.map((option) => ({
    option_id: option.option_id.trim(),
    text: option.text.trim(),
  }));
  const markingGuide = value.marking_guide.map((item) => item.trim()).filter(Boolean);
  const optionIds = options.map((option) => option.option_id);
  const invalid =
    !stem ||
    !explanation ||
    !answer ||
    !markingGuide.length ||
    markingGuide.length > 64 ||
    options.length > 16 ||
    options.some((option) => !option.option_id || !option.text) ||
    new Set(optionIds).size !== optionIds.length ||
    (value.question_type === "multiple_choice" &&
      (options.length < 2 || optionIds.filter((optionId) => optionId === answer).length !== 1));
  if (invalid) {
    return {
      error: {
        code: "local_content_invalid",
        message:
          "Complete the stem, answer, explanation, unique option IDs/text, and at least one marking-guide item. Multiple choice requires at least two options and one matching answer.",
        title: "Review content is incomplete",
      },
    };
  }
  return {
    content: {
      answer,
      explanation,
      marking_guide: markingGuide,
      marks: value.marks,
      options,
      question_type: value.question_type,
      stem,
    },
  };
}

function ReviewEditor({
  approvalNote,
  busy,
  candidate,
  draft,
  editReason,
  onApprovalNote,
  onApprove,
  onDiscardDraft,
  onDraft,
  onEditReason,
  onReject,
  onRejectReason,
  onSave,
  onStart,
  rejectReason,
}: {
  approvalNote: string;
  busy: boolean;
  candidate: Candidate;
  draft: DraftState;
  editReason: string;
  onApprovalNote: (value: string) => void;
  onApprove: () => void;
  onDiscardDraft: () => void;
  onDraft: (content: QuestionContent) => void;
  onEditReason: (value: string) => void;
  onReject: () => void;
  onRejectReason: (value: string) => void;
  onSave: () => void;
  onStart: () => void;
  rejectReason: string;
}) {
  const terminal = candidate.state === "approved" || candidate.state === "rejected";
  const editable = candidate.state === "in_review" && !terminal;
  const content = draft.content;

  function updateOption(index: number, next: QuestionOption) {
    const options = content.options.map((option, optionIndex) =>
      optionIndex === index ? next : option,
    );
    onDraft({ ...content, options });
  }

  function addOption() {
    if (content.options.length >= 16) return;
    const used = new Set(content.options.map((option) => option.option_id));
    let optionId = String.fromCharCode(65 + content.options.length);
    let suffix = content.options.length + 1;
    while (used.has(optionId)) {
      optionId = `O${suffix}`;
      suffix += 1;
    }
    onDraft({ ...content, options: [...content.options, { option_id: optionId, text: "" }] });
  }

  function removeOption(index: number) {
    const removed = content.options[index];
    const options = content.options.filter((_option, optionIndex) => optionIndex !== index);
    const answer = removed?.option_id === content.answer ? options[0]?.option_id ?? "" : content.answer;
    onDraft({ ...content, answer, options });
  }

  return (
    <section
      aria-label="Candidate review editor"
      className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 pb-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Human review workspace</p>
          <h2 className="mt-1 text-xl font-semibold">Current candidate revision</h2>
        </div>
        <StateBadge state={candidate.state} />
      </div>

      {terminal ? (
        <section className={`mt-5 rounded-xl border p-4 ${candidate.state === "approved" ? "border-emerald-300 bg-emerald-50 text-emerald-950" : "border-red-300 bg-red-50 text-red-950"}`}>
          <h3 className="font-semibold">{titleCase(candidate.state)} terminal state</h3>
          <p className="mt-1 text-sm leading-6">
            This candidate is locked. Terminal candidates cannot be edited or moved to another review decision.
          </p>
        </section>
      ) : null}

      {candidate.state === "validated" ? (
        <section className="mt-5 rounded-xl border border-blue-300 bg-blue-50 p-4 text-blue-950">
          <h3 className="font-semibold">Ready for human review</h3>
          <p className="mt-1 text-sm leading-6">
            Starting review is explicit and sends the current expected version. It does not approve or publish the candidate.
          </p>
          <Button className={`${primaryButton} mt-3`} isDisabled={busy} onPress={onStart}>
            {busy ? "Starting review…" : "Start review"}
          </Button>
        </section>
      ) : null}

      <Form
        className="mt-5 space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          onSave();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <label className={fieldClass}>
            Question type (locked)
            <input
              aria-readonly="true"
              className={inputClass}
              disabled
              value={titleCase(content.question_type)}
            />
          </label>
          <label className={fieldClass}>
            Marks (locked)
            <input aria-readonly="true" className={inputClass} disabled value={content.marks} />
          </label>
        </div>
        <label className={fieldClass}>
          Question stem
          <textarea
            className={`${inputClass} max-h-80 min-h-28 resize-y`}
            disabled={!editable || busy}
            maxLength={32_768}
            onChange={(event) => onDraft({ ...content, stem: event.target.value })}
            value={content.stem}
          />
        </label>
        <fieldset className="rounded-xl border border-slate-300 p-4" disabled={!editable || busy}>
          <legend className="px-1 text-sm font-semibold text-slate-700">Options</legend>
          <div className="space-y-3">
            {content.options.map((option, index) => (
              <div className="grid gap-3 rounded-lg bg-slate-50 p-3 sm:grid-cols-[8rem_minmax(0,1fr)_auto]" key={`${index}-${option.option_id}`}>
                <label className={fieldClass}>
                  Option {index + 1} ID
                  <input
                    className={inputClass}
                    maxLength={128}
                    onChange={(event) => updateOption(index, { ...option, option_id: event.target.value })}
                    value={option.option_id}
                  />
                </label>
                <label className={fieldClass}>
                  Option {option.option_id || index + 1} text
                  <input
                    className={inputClass}
                    maxLength={8_192}
                    onChange={(event) => updateOption(index, { ...option, text: event.target.value })}
                    value={option.text}
                  />
                </label>
                <Button
                  className={`${secondaryButton} self-end`}
                  isDisabled={content.question_type === "multiple_choice" && content.options.length <= 2}
                  onPress={() => removeOption(index)}
                >
                  Remove
                </Button>
              </div>
            ))}
          </div>
          {editable ? (
            <Button className={`${secondaryButton} mt-3`} isDisabled={content.options.length >= 16} onPress={addOption}>
              Add option
            </Button>
          ) : null}
        </fieldset>
        {content.question_type === "multiple_choice" ? (
          <label className={fieldClass}>
            Answer
            <select
              className={inputClass}
              disabled={!editable || busy}
              onChange={(event) => onDraft({ ...content, answer: event.target.value })}
              value={content.answer}
            >
              {content.options.map((option, index) => (
                <option key={`${index}-${option.option_id}`} value={option.option_id}>
                  {safeText(option.option_id, `Option ${index + 1}`)}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label className={fieldClass}>
            Answer
            <textarea
              className={`${inputClass} max-h-72 min-h-24 resize-y`}
              disabled={!editable || busy}
              maxLength={32_768}
              onChange={(event) => onDraft({ ...content, answer: event.target.value })}
              value={content.answer}
            />
          </label>
        )}
        <label className={fieldClass}>
          Explanation
          <textarea
            className={`${inputClass} max-h-80 min-h-28 resize-y`}
            disabled={!editable || busy}
            maxLength={32_768}
            onChange={(event) => onDraft({ ...content, explanation: event.target.value })}
            value={content.explanation}
          />
        </label>
        <label className={fieldClass}>
          Marking guide (one item per line)
          <textarea
            className={`${inputClass} max-h-80 min-h-28 resize-y`}
            disabled={!editable || busy}
            maxLength={32_768}
            onChange={(event) => onDraft({ ...content, marking_guide: event.target.value.split("\n") })}
            value={content.marking_guide.join("\n")}
          />
        </label>
        {editable ? (
          <>
            <label className={fieldClass}>
              Edit reason
              <textarea
                className={`${inputClass} max-h-52 min-h-20 resize-y`}
                maxLength={1_024}
                onChange={(event) => onEditReason(event.target.value)}
                required
                value={editReason}
              />
            </label>
            <Button
              className={primaryButton}
              isDisabled={busy || !draft.dirty || !editReason.trim()}
              type="submit"
            >
              {busy ? "Saving revision…" : "Save revision"}
            </Button>
          </>
        ) : null}
      </Form>

      {editable ? (
        <div className="mt-7 grid gap-5 border-t border-slate-200 pt-6 lg:grid-cols-2">
          <section className="rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-emerald-950">
            <h3 className="font-semibold">Approve after human review</h3>
            <p className="mt-1 text-sm leading-6">The note is optional. Approval is terminal but is not publication.</p>
            {draft.dirty ? (
              <div className="mt-3 rounded-lg border border-amber-400 bg-amber-50 p-3 text-amber-950">
                <p className="text-sm font-semibold">Save or discard the unsaved revision before approval.</p>
                <Button className={`${secondaryButton} mt-2`} isDisabled={busy} onPress={onDiscardDraft}>
                  Discard unsaved revision
                </Button>
              </div>
            ) : null}
            <label className={`${fieldClass} mt-3 text-emerald-950`}>
              Approval note (optional)
              <textarea
                className={`${inputClass} max-h-52 min-h-20 resize-y`}
                disabled={busy}
                maxLength={1_024}
                onChange={(event) => onApprovalNote(event.target.value)}
                value={approvalNote}
              />
            </label>
            <Button className={`${primaryButton} mt-3`} isDisabled={busy || draft.dirty} onPress={onApprove}>
              Approve candidate
            </Button>
          </section>
          <section className="rounded-xl border border-red-300 bg-red-50 p-4 text-red-950">
            <h3 className="font-semibold">Reject candidate</h3>
            <p className="mt-1 text-sm leading-6">A specific reason is required. Rejection is terminal.</p>
            <label className={`${fieldClass} mt-3 text-red-950`}>
              Rejection reason (required)
              <textarea
                className={`${inputClass} max-h-52 min-h-20 resize-y`}
                disabled={busy}
                maxLength={1_024}
                onChange={(event) => onRejectReason(event.target.value)}
                required
                value={rejectReason}
              />
            </label>
            <Button className={`${dangerButton} mt-3`} isDisabled={busy || !rejectReason.trim()} onPress={onReject}>
              Reject candidate
            </Button>
          </section>
        </div>
      ) : null}
    </section>
  );
}

export function ReviewerStudio({ role }: { role: Role }) {
  const api = useMemo(() => createApiClient(globalThis.location?.origin ?? "http://localhost"), []);
  const [exams, setExams] = useState<Exam[]>([]);
  const [media, setMedia] = useState<Medium[]>([]);
  const [curricula, setCurricula] = useState<Curriculum[]>([]);
  const [selectedCurriculumId, setSelectedCurriculumId] = useState("");
  const [candidates, setCandidates] = useState<CandidateSummary[]>([]);
  const [validationSummaries, setValidationSummaries] = useState<ValidationSummary[]>([]);
  const [selectedValidationId, setSelectedValidationId] = useState("");
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [generation, setGeneration] = useState<Generation | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [filterState, setFilterState] = useState<CandidateState | "">("");
  const [blueprintFilter, setBlueprintFilter] = useState("");
  const [slotFilter, setSlotFilter] = useState("");
  const [editReason, setEditReason] = useState("");
  const [approvalNote, setApprovalNote] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [workspaceLoading, setWorkspaceLoading] = useState(true);
  const [queueLoading, setQueueLoading] = useState(false);
  const [validationsLoading, setValidationsLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [busyAction, setBusyAction] = useState("");
  const [workspaceError, setWorkspaceError] = useState<UiError | null>(null);
  const [queueError, setQueueError] = useState<UiError | null>(null);
  const [validationListError, setValidationListError] = useState<UiError | null>(null);
  const [detailError, setDetailError] = useState<UiError | null>(null);
  const [evidenceError, setEvidenceError] = useState<UiError | null>(null);
  const [operationError, setOperationError] = useState<UiError | null>(null);
  const [versionConflict, setVersionConflict] = useState(false);
  const [notice, setNotice] = useState("");

  const workspaceRequestId = useRef(0);
  const queueRequestId = useRef(0);
  const validationRequestId = useRef(0);
  const detailRequestId = useRef(0);
  const evidenceRequestId = useRef(0);
  const operationInFlight = useRef(false);

  const curriculumChoices = useMemo(() => {
    const examsById = new Map(exams.map((item) => [item.id, item]));
    const mediaById = new Map(media.map((item) => [item.id, item]));
    return curricula.filter((item) => {
      const exam = examsById.get(item.exam_configuration_id);
      return item.active && exam?.active && exam.grade === 5 && mediaById.get(item.medium_id)?.active;
    });
  }, [curricula, exams, media]);

  const eligibleValidations = useMemo(
    () =>
      validationSummaries.filter(
        (item) =>
          item.curriculum_version_id === selectedCurriculumId && item.overall_status !== "fail",
      ),
    [selectedCurriculumId, validationSummaries],
  );

  const loadWorkspace = useCallback(async () => {
    const requestId = ++workspaceRequestId.current;
    setWorkspaceLoading(true);
    setWorkspaceError(null);
    try {
      const [examResponse, mediumResponse, curriculumResponse] = await Promise.all([
        api.GET("/api/v1/admin/exam-configurations"),
        api.GET("/api/v1/admin/media"),
        api.GET("/api/v1/admin/curriculum-versions"),
      ]);
      if (requestId !== workspaceRequestId.current) return;
      const failure = firstFailure([examResponse, mediumResponse, curriculumResponse]);
      if (failure?.error !== undefined) {
        setWorkspaceError(apiError(failure.error, failure.response.status, "workspace"));
        return;
      }
      const nextExams = examResponse.data ?? [];
      const nextMedia = mediumResponse.data ?? [];
      const nextCurricula = curriculumResponse.data ?? [];
      setExams(nextExams);
      setMedia(nextMedia);
      setCurricula(nextCurricula);
      const examsById = new Map(nextExams.map((item) => [item.id, item]));
      const mediaById = new Map(nextMedia.map((item) => [item.id, item]));
      const active = nextCurricula.filter((item) => {
        const exam = examsById.get(item.exam_configuration_id);
        return item.active && exam?.active && exam.grade === 5 && mediaById.get(item.medium_id)?.active;
      });
      setQueueLoading(active.length > 0);
      setValidationsLoading(active.length > 0);
      setSelectedCurriculumId((current) =>
        active.some((item) => item.id === current) ? current : active[0]?.id ?? "",
      );
    } catch {
      if (requestId === workspaceRequestId.current) setWorkspaceError(networkError("workspace"));
    } finally {
      if (requestId === workspaceRequestId.current) setWorkspaceLoading(false);
    }
  }, [api]);

  const loadQueue = useCallback(
    async (curriculumId: string, filters: QueueFilters) => {
      const requestId = ++queueRequestId.current;
      setQueueLoading(true);
      setQueueError(null);
      try {
        const query: {
          blueprint_slot_id?: string;
          limit: number;
          offset: number;
          paper_blueprint_id?: string;
          state?: CandidateState;
        } = { limit: LIST_LIMIT, offset: 0 };
        if (filters.state) query.state = filters.state;
        if (filters.blueprintId) query.paper_blueprint_id = filters.blueprintId;
        if (filters.slotId) query.blueprint_slot_id = filters.slotId;
        const response = await api.GET(
          "/api/v1/admin/curricula/{curriculum_version_id}/review-candidates",
          { params: { path: { curriculum_version_id: curriculumId }, query } },
        );
        if (requestId !== queueRequestId.current) return;
        if (response.error !== undefined) {
          setQueueError(apiError(response.error, response.response.status, "queue"));
          return;
        }
        setCandidates(
          (response.data ?? []).filter((item) => item.curriculum_version_id === curriculumId),
        );
      } catch {
        if (requestId === queueRequestId.current) setQueueError(networkError("queue"));
      } finally {
        if (requestId === queueRequestId.current) setQueueLoading(false);
      }
    },
    [api],
  );

  const loadValidationSummaries = useCallback(
    async (curriculumId: string) => {
      const requestId = ++validationRequestId.current;
      setValidationsLoading(true);
      setValidationListError(null);
      try {
        const response = await api.GET(
          "/api/v1/admin/curricula/{curriculum_version_id}/validation-runs",
          {
            params: {
              path: { curriculum_version_id: curriculumId },
              query: { limit: VALIDATION_LIMIT, offset: 0 },
            },
          },
        );
        if (requestId !== validationRequestId.current) return;
        if (response.error !== undefined) {
          setValidationListError(apiError(response.error, response.response.status, "queue"));
          return;
        }
        const next = (response.data ?? []).filter(
          (item) => item.curriculum_version_id === curriculumId,
        );
        setValidationSummaries(next);
        const eligible = next.filter((item) => item.overall_status !== "fail");
        setSelectedValidationId((current) =>
          eligible.some((item) => item.id === current) ? current : eligible[0]?.id ?? "",
        );
      } catch {
        if (requestId === validationRequestId.current) {
          setValidationListError(networkError("queue"));
        }
      } finally {
        if (requestId === validationRequestId.current) setValidationsLoading(false);
      }
    },
    [api],
  );

  const loadEvidence = useCallback(
    async (record: Candidate) => {
      const requestId = ++evidenceRequestId.current;
      setEvidenceLoading(true);
      setEvidenceError(null);
      try {
        const path = { curriculum_version_id: record.curriculum_version_id };
        const [generationResponse, validationResponse, findingsResponse] = await Promise.all([
          api.GET(
            "/api/v1/admin/curricula/{curriculum_version_id}/generation-runs/{generation_run_id}",
            {
              params: {
                path: { ...path, generation_run_id: record.generation_run_id },
              },
            },
          ),
          api.GET(
            "/api/v1/admin/curricula/{curriculum_version_id}/validation-runs/{validation_run_id}",
            {
              params: {
                path: { ...path, validation_run_id: record.validation_run_id },
              },
            },
          ),
          api.GET(
            "/api/v1/admin/curricula/{curriculum_version_id}/validation-runs/{validation_run_id}/findings",
            {
              params: {
                path: { ...path, validation_run_id: record.validation_run_id },
                query: { limit: FINDING_LIMIT, offset: 0 },
              },
            },
          ),
        ]);
        if (requestId !== evidenceRequestId.current) return;
        const failure = firstFailure([generationResponse, validationResponse, findingsResponse]);
        if (failure?.error !== undefined) {
          setEvidenceError(apiError(failure.error, failure.response.status, "evidence"));
          return;
        }
        setGeneration(generationResponse.data ?? null);
        setValidation(validationResponse.data ?? null);
        setFindings(findingsResponse.data ?? []);
      } catch {
        if (requestId === evidenceRequestId.current) setEvidenceError(networkError("evidence"));
      } finally {
        if (requestId === evidenceRequestId.current) setEvidenceLoading(false);
      }
    },
    [api],
  );

  const loadCandidate = useCallback(
    async (
      curriculumId: string,
      candidateId: string,
      options: { discardDraft?: boolean; preserveDraft?: boolean; rebaseDraft?: boolean } = {},
    ) => {
      const requestId = ++detailRequestId.current;
      setDetailLoading(true);
      setDetailError(null);
      try {
        const response = await api.GET(
          "/api/v1/admin/curricula/{curriculum_version_id}/review-candidates/{candidate_id}",
          {
            params: { path: { candidate_id: candidateId, curriculum_version_id: curriculumId } },
          },
        );
        if (requestId !== detailRequestId.current) return;
        if (response.error !== undefined) {
          setDetailError(apiError(response.error, response.response.status, "detail"));
          return;
        }
        const next = response.data;
        if (!next || next.curriculum_version_id !== curriculumId || next.id !== candidateId) {
          setDetailError({
            code: "review_scope_mismatch",
            message: "The returned candidate did not match the selected curriculum and identity.",
            title: "Candidate scope mismatch",
          });
          return;
        }
        setCandidate(next);
        setCandidates((current) => {
          const summary = summaryFromCandidate(next);
          return current.some((item) => item.id === next.id)
            ? current.map((item) => (item.id === next.id ? summary : item))
            : [summary, ...current];
        });
        setDraft((current) => {
          const keep =
            options.preserveDraft &&
            current?.candidateId === next.id &&
            current.dirty &&
            !options.discardDraft;
          if (!keep) {
            return {
              baseVersion: next.version,
              candidateId: next.id,
              content: contentCopy(next.current_content),
              dirty: false,
            };
          }
          return options.rebaseDraft ? { ...current, baseVersion: next.version } : current;
        });
        if (options.rebaseDraft) {
          setNotice(`Local draft rebased onto authoritative version ${next.version}. Review it before saving.`);
        } else if (options.preserveDraft) {
          setNotice("Candidate and evidence refreshed; any unsaved local draft was preserved.");
        }
        setVersionConflict(false);
        setOperationError(null);
        void loadEvidence(next);
      } catch {
        if (requestId === detailRequestId.current) setDetailError(networkError("detail"));
      } finally {
        if (requestId === detailRequestId.current) setDetailLoading(false);
      }
    },
    [api, loadEvidence],
  );

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadWorkspace(), 0);
    return () => {
      window.clearTimeout(timeout);
      workspaceRequestId.current += 1;
    };
  }, [loadWorkspace]);

  useEffect(() => {
    if (!selectedCurriculumId) return;
    const timeout = window.setTimeout(() => {
      void loadQueue(selectedCurriculumId, EMPTY_FILTERS);
      void loadValidationSummaries(selectedCurriculumId);
    }, 0);
    return () => window.clearTimeout(timeout);
  }, [loadQueue, loadValidationSummaries, selectedCurriculumId]);

  function clearSelection() {
    detailRequestId.current += 1;
    evidenceRequestId.current += 1;
    setSelectedCandidateId("");
    setCandidate(null);
    setGeneration(null);
    setValidation(null);
    setFindings([]);
    setDraft(null);
    setDetailError(null);
    setEvidenceError(null);
    setOperationError(null);
    setVersionConflict(false);
    setNotice("");
    setEditReason("");
    setApprovalNote("");
    setRejectReason("");
  }

  function selectCurriculum(curriculumId: string) {
    if (curriculumId === selectedCurriculumId) return;
    queueRequestId.current += 1;
    validationRequestId.current += 1;
    setSelectedCurriculumId(curriculumId);
    setQueueLoading(true);
    setValidationsLoading(true);
    setCandidates([]);
    setValidationSummaries([]);
    setSelectedValidationId("");
    setFilterState("");
    setBlueprintFilter("");
    setSlotFilter("");
    clearSelection();
  }

  function selectCandidate(candidateId: string) {
    if (candidateId === selectedCandidateId && candidate) return;
    setSelectedCandidateId(candidateId);
    setCandidate(null);
    setGeneration(null);
    setValidation(null);
    setFindings([]);
    setDraft(null);
    setOperationError(null);
    setVersionConflict(false);
    setNotice("");
    setEditReason("");
    setApprovalNote("");
    setRejectReason("");
    void loadCandidate(selectedCurriculumId, candidateId);
  }

  function applyQueueFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    clearSelection();
    void loadQueue(selectedCurriculumId, {
      blueprintId: blueprintFilter.trim(),
      slotId: slotFilter.trim(),
      state: filterState,
    });
  }

  function updateDraft(content: QuestionContent) {
    setDraft((current) =>
      current ? { ...current, content: editableContent(content), dirty: true } : current,
    );
    setOperationError(null);
    setVersionConflict(false);
    setNotice("");
  }

  function discardDraft() {
    if (!candidate) return;
    setDraft({
      baseVersion: candidate.version,
      candidateId: candidate.id,
      content: contentCopy(candidate.current_content),
      dirty: false,
    });
    setEditReason("");
    setOperationError(null);
    setVersionConflict(false);
    setNotice("Unsaved local revision discarded; authoritative content restored.");
  }

  function acceptCandidate(next: Candidate, message: string) {
    setCandidate(next);
    setSelectedCandidateId(next.id);
    setDraft({
      baseVersion: next.version,
      candidateId: next.id,
      content: contentCopy(next.current_content),
      dirty: false,
    });
    setCandidates((current) => {
      const summary = summaryFromCandidate(next);
      return current.some((item) => item.id === next.id)
        ? current.map((item) => (item.id === next.id ? summary : item))
        : [summary, ...current];
    });
    setEditReason("");
    setApprovalNote("");
    setRejectReason("");
    setVersionConflict(false);
    setOperationError(null);
    setNotice(message);
  }

  async function executeCandidateCommand(
    action: string,
    operation: () => Promise<CandidateOutcome>,
    success: (value: Candidate) => void,
    surface: "create" | "command" = "command",
  ) {
    if (operationInFlight.current) return;
    operationInFlight.current = true;
    setBusyAction(action);
    setOperationError(null);
    setVersionConflict(false);
    setNotice("");
    try {
      const response = await operation();
      if (response.error !== undefined) {
        const error = apiError(response.error, response.response.status, surface);
        if (error.code === "review_candidate_version_conflict") {
          setVersionConflict(true);
        }
        setOperationError(error);
        return;
      }
      if (!response.data) {
        setOperationError({
          code: "empty_response",
          message: "The server completed the command without returning the authoritative candidate.",
          title: "Candidate response missing",
        });
        return;
      }
      success(response.data);
    } catch {
      setOperationError(networkError(surface));
    } finally {
      operationInFlight.current = false;
      setBusyAction("");
    }
  }

  function createCandidate() {
    if (!selectedCurriculumId || !selectedValidationId) return;
    const selected = eligibleValidations.find((item) => item.id === selectedValidationId);
    if (!selected || selected.overall_status === "fail") {
      setOperationError({
        code: "non_failing_validation_required",
        message: "Select a persisted PASS or WARN validation report from this curriculum.",
        title: "Non-failing validation required",
      });
      return;
    }
    void executeCandidateCommand(
      "create",
      () =>
        api.POST("/api/v1/admin/curricula/{curriculum_version_id}/review-candidates", {
          params: { path: { curriculum_version_id: selectedCurriculumId } },
          body: { validation_run_id: selected.id },
        }),
      (next) => {
        acceptCandidate(
          next,
          "Review candidate created from persisted non-failing validation evidence.",
        );
        void loadEvidence(next);
      },
      "create",
    );
  }

  function startReview() {
    if (!candidate) return;
    const expectedVersion = candidate.version;
    void executeCandidateCommand(
      "start",
      () =>
        api.POST(
          "/api/v1/admin/curricula/{curriculum_version_id}/review-candidates/{candidate_id}/start-review",
          {
            params: {
              path: {
                candidate_id: candidate.id,
                curriculum_version_id: candidate.curriculum_version_id,
              },
            },
            body: { expected_version: expectedVersion },
          },
        ),
      (next) => acceptCandidate(next, "Human review started."),
    );
  }

  function saveRevision() {
    if (!candidate || !draft || !editReason.trim()) return;
    const normalized = normalizedContent(draft.content);
    if (!normalized.content) {
      setOperationError(normalized.error ?? networkError("command"));
      return;
    }
    const expectedVersion = draft.baseVersion;
    const reason = editReason.trim();
    void executeCandidateCommand(
      "edit",
      () =>
        api.PATCH(
          "/api/v1/admin/curricula/{curriculum_version_id}/review-candidates/{candidate_id}",
          {
            params: {
              path: {
                candidate_id: candidate.id,
                curriculum_version_id: candidate.curriculum_version_id,
              },
            },
            body: { content: normalized.content!, expected_version: expectedVersion, reason },
          },
        ),
      (next) =>
        acceptCandidate(
          next,
          `Revision ${next.current_revision} saved. Automated validation still applies only to revision 1.`,
        ),
    );
  }

  function approveCandidate() {
    if (!candidate) return;
    const expectedVersion = candidate.version;
    const note = approvalNote.trim() || null;
    void executeCandidateCommand(
      "approve",
      () =>
        api.POST(
          "/api/v1/admin/curricula/{curriculum_version_id}/review-candidates/{candidate_id}/approve",
          {
            params: {
              path: {
                candidate_id: candidate.id,
                curriculum_version_id: candidate.curriculum_version_id,
              },
            },
            body: { expected_version: expectedVersion, note },
          },
        ),
      (next) => acceptCandidate(next, "Candidate approved. This is not a publish action."),
    );
  }

  function rejectCandidate() {
    if (!candidate || !rejectReason.trim()) return;
    const expectedVersion = candidate.version;
    const reason = rejectReason.trim();
    void executeCandidateCommand(
      "reject",
      () =>
        api.POST(
          "/api/v1/admin/curricula/{curriculum_version_id}/review-candidates/{candidate_id}/reject",
          {
            params: {
              path: {
                candidate_id: candidate.id,
                curriculum_version_id: candidate.curriculum_version_id,
              },
            },
            body: { expected_version: expectedVersion, reason },
          },
        ),
      (next) =>
        acceptCandidate(next, "Candidate rejected. Rejected candidates cannot be published."),
    );
  }

  const busy = Boolean(busyAction);

  return (
    <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8 lg:py-10">
      <header className="flex flex-wrap items-start justify-between gap-5 border-b border-slate-300 pb-7">
        <div>
          <p className="font-mono text-xs font-semibold tracking-[0.18em] text-slate-500 uppercase">
            P9 / Human content review
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Reviewer Studio</h1>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600 sm:text-base">
            Inspect generated revision 1, exact blueprint and source context, P8 validation evidence, human revisions, and the final decision in one authorized workflow.
          </p>
        </div>
        <Badge className="border-blue-300 bg-blue-50 text-blue-950">{titleCase(role)} · content review</Badge>
      </header>

      <section
        aria-labelledby="validation-boundary"
        className="mt-6 rounded-2xl border-2 border-amber-500 bg-amber-50 p-5 text-amber-950"
      >
        <h2 className="text-lg font-semibold" id="validation-boundary">Validation and publication boundary</h2>
        <p className="mt-2 font-semibold leading-7">
          Automated validation applies to generated revision 1 only. Human edits are not automatically revalidated. Approval does not publish this question and has no publish implication.
        </p>
        <p className="mt-2 text-sm leading-6">
          Generated, retrieved, and validator text is untrusted data. This studio renders it as plain, bounded text and never treats it as authority or an instruction.
        </p>
      </section>

      {workspaceLoading ? (
        <p className="mt-8" role="status">Loading reviewer workspace…</p>
      ) : workspaceError ? (
        <div className="mt-8">
          <ErrorPanel error={workspaceError} onRetry={() => void loadWorkspace()} retryLabel="Retry reviewer workspace" />
        </div>
      ) : !curriculumChoices.length ? (
        <section className="mt-8 rounded-2xl border border-dashed border-slate-400 bg-white p-6">
          <h2 className="text-xl font-semibold">No active Grade 5 curriculum</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Activate a Grade 5 exam, medium, and curriculum before reviewing candidates.
          </p>
          <Link className={`${secondaryButton} mt-4`} href="/admin/curriculum">Open Curriculum Studio</Link>
        </section>
      ) : (
        <>
          <div className="mt-8 grid gap-6 lg:grid-cols-2">
            <Panel
              title="Review scope"
              description="Only active Grade 5 curricula are shown. Every API request remains curriculum-scoped and authorized."
            >
              <label className={fieldClass}>
                Active Grade 5 curriculum
                <select
                  className={inputClass}
                  onChange={(event) => selectCurriculum(event.target.value)}
                  value={selectedCurriculumId}
                >
                  {curriculumChoices.map((item) => (
                    <option key={item.id} value={item.id}>{item.title} ({item.code})</option>
                  ))}
                </select>
              </label>
            </Panel>

            <Panel
              title="Create from non-failing validation"
              description="Reviewer and admin accounts may select persisted PASS or WARN evidence. WARN requires explicit human judgement; FAIL remains blocked. The POST body contains exactly validation_run_id; generation, content, lineage, state, and version are server-derived."
            >
              {validationsLoading ? (
                <p role="status">Loading persisted validation reports…</p>
              ) : validationListError ? (
                <ErrorPanel
                  error={validationListError}
                  onRetry={() => void loadValidationSummaries(selectedCurriculumId)}
                  retryLabel="Retry validation reports"
                />
              ) : eligibleValidations.length ? (
                <div>
                  <label className={fieldClass}>
                    Eligible validation run
                    <select
                      className={inputClass}
                      disabled={busy}
                      onChange={(event) => setSelectedValidationId(event.target.value)}
                      value={selectedValidationId}
                    >
                      {eligibleValidations.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.overall_status.toUpperCase()} · {item.id} · generation {item.generation_run_id}
                        </option>
                      ))}
                    </select>
                  </label>
                  <p className="mt-3 text-xs leading-5 text-slate-600">
                    {validationSummaries.length - eligibleValidations.length} failed report(s) excluded. PASS and WARN evidence both require human review.
                  </p>
                  <Button
                    className={`${primaryButton} mt-4`}
                    isDisabled={busy || !selectedValidationId}
                    onPress={createCandidate}
                  >
                    {busyAction === "create" ? "Creating candidate…" : "Create review candidate"}
                  </Button>
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-400 p-4">
                  <h3 className="font-semibold">No persisted non-failing validation reports</h3>
                  <p className="mt-2 text-sm leading-6 text-slate-600">
                    Run validation and resolve failed checks before candidate creation.
                  </p>
                  <Link className={`${secondaryButton} mt-3`} href="/admin/validation">Open Validation Studio</Link>
                </div>
              )}
            </Panel>
          </div>

          <div className="mt-8 grid gap-6 lg:grid-cols-[22rem_minmax(0,1fr)]">
            <aside className="space-y-5">
              <Panel title="Review queue filters" description="Apply bounded server-side state, blueprint, and exact-slot filters.">
                <Form className="space-y-4" onSubmit={applyQueueFilters}>
                  <label className={fieldClass}>
                    Review state
                    <select
                      className={inputClass}
                      onChange={(event) => setFilterState(event.target.value as CandidateState | "")}
                      value={filterState}
                    >
                      {states.map((item) => <option key={item.value || "all"} value={item.value}>{item.label}</option>)}
                    </select>
                  </label>
                  <label className={fieldClass}>
                    Paper blueprint ID
                    <input
                      className={inputClass}
                      maxLength={36}
                      onChange={(event) => setBlueprintFilter(event.target.value)}
                      placeholder="Optional UUID"
                      value={blueprintFilter}
                    />
                  </label>
                  <label className={fieldClass}>
                    Blueprint slot ID
                    <input
                      className={inputClass}
                      maxLength={128}
                      onChange={(event) => setSlotFilter(event.target.value)}
                      placeholder="Optional exact slot"
                      value={slotFilter}
                    />
                  </label>
                  <Button className={secondaryButton} isDisabled={queueLoading} type="submit">Apply queue filters</Button>
                </Form>
              </Panel>

              <Panel title="Candidate queue" description={`Lightweight summaries only · first ${LIST_LIMIT} records`}>
                {queueLoading ? (
                  <p role="status">Loading review candidates…</p>
                ) : queueError ? (
                  <ErrorPanel
                    error={queueError}
                    onRetry={() =>
                      void loadQueue(selectedCurriculumId, {
                        blueprintId: blueprintFilter.trim(),
                        slotId: slotFilter.trim(),
                        state: filterState,
                      })
                    }
                    retryLabel="Retry review queue"
                  />
                ) : (
                  <CandidateQueue
                    candidates={candidates}
                    onSelect={selectCandidate}
                    selectedId={selectedCandidateId}
                  />
                )}
              </Panel>
            </aside>

            <div className="min-w-0 space-y-6">
              {notice ? (
                <p className="rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-sm font-semibold text-emerald-950" role="status">
                  {notice}
                </p>
              ) : null}

              {versionConflict && candidate && draft ? (
                <section className="rounded-xl border-2 border-red-500 bg-red-50 p-5 text-red-950" role="alert">
                  <h2 className="text-lg font-semibold">Authoritative version changed</h2>
                  <p className="mt-2 text-sm leading-6">
                    Another reviewer changed this candidate after local draft version {draft.baseVersion}. Reload the authoritative version before any retry. Keeping the draft explicitly rebases it; discarding replaces it with server content.
                  </p>
                  <div className="mt-4 flex flex-wrap gap-3">
                    <Button
                      className={secondaryButton}
                      isDisabled={detailLoading || busy}
                      onPress={() =>
                        void loadCandidate(candidate.curriculum_version_id, candidate.id, {
                          preserveDraft: true,
                          rebaseDraft: true,
                        })
                      }
                    >
                      Reload authoritative and keep draft
                    </Button>
                    <Button
                      className={dangerButton}
                      isDisabled={detailLoading || busy}
                      onPress={() =>
                        void loadCandidate(candidate.curriculum_version_id, candidate.id, {
                          discardDraft: true,
                        })
                      }
                    >
                      Discard draft and use authoritative
                    </Button>
                  </div>
                </section>
              ) : operationError ? (
                <ErrorPanel error={operationError} />
              ) : null}

              {detailLoading && !candidate ? (
                <p role="status">Loading candidate detail…</p>
              ) : detailError ? (
                <ErrorPanel
                  error={detailError}
                  onRetry={() =>
                    selectedCandidateId
                      ? void loadCandidate(selectedCurriculumId, selectedCandidateId, { preserveDraft: true })
                      : undefined
                  }
                  retryLabel="Retry candidate detail"
                />
              ) : candidate && draft ? (
                <>
                  <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-300 bg-white p-4">
                    <div>
                      <p className="font-mono text-xs text-slate-600">Candidate {candidate.id}</p>
                      <p className="mt-1 text-sm font-semibold">
                        Authoritative version {candidate.version} · revision {candidate.current_revision}
                      </p>
                      {draft.dirty && draft.baseVersion !== candidate.version ? (
                        <p className="mt-1 text-sm font-semibold text-amber-800">
                          Authoritative version {candidate.version}; local draft is based on version {draft.baseVersion}.
                        </p>
                      ) : draft.dirty ? (
                        <p className="mt-1 text-sm font-semibold text-amber-800">Unsaved local edit</p>
                      ) : null}
                    </div>
                    <Button
                      className={secondaryButton}
                      isDisabled={detailLoading || evidenceLoading || busy}
                      onPress={() =>
                        void loadCandidate(candidate.curriculum_version_id, candidate.id, {
                          preserveDraft: true,
                        })
                      }
                    >
                      {detailLoading || evidenceLoading ? "Refreshing…" : "Refresh candidate and evidence"}
                    </Button>
                  </div>

                  <ReviewEditor
                    approvalNote={approvalNote}
                    busy={busy}
                    candidate={candidate}
                    draft={draft}
                    editReason={editReason}
                    onApprovalNote={setApprovalNote}
                    onApprove={approveCandidate}
                    onDiscardDraft={discardDraft}
                    onDraft={updateDraft}
                    onEditReason={setEditReason}
                    onReject={rejectCandidate}
                    onRejectReason={setRejectReason}
                    onSave={saveRevision}
                    onStart={startReview}
                    rejectReason={rejectReason}
                  />

                  <section className="rounded-2xl border border-slate-300 bg-white p-5">
                    <h2 className="text-xl font-semibold">Immutable candidate lineage</h2>
                    <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                      <Definition label="Generation run" mono value={candidate.generation_run_id} />
                      <Definition label="Generation attempt" mono value={candidate.generation_attempt_id} />
                      <Definition label="Validation run" mono value={candidate.validation_run_id} />
                      <Definition label="Paper blueprint" mono value={candidate.paper_blueprint_id} />
                      <Definition label="Blueprint ID / version" mono value={`${safeText(candidate.blueprint_id)} / ${safeText(candidate.blueprint_version)}`} />
                      <Definition label="Exact slot" mono value={safeText(candidate.blueprint_slot_id)} />
                      <Definition label="Prompt version" mono value={safeText(candidate.lineage.prompt_version)} />
                      <Definition label="Provider / model" mono value={`${safeText(candidate.lineage.provider)} / ${safeText(candidate.lineage.model_version)}`} />
                      <Definition label="Retrieval / schema" mono value={`${safeText(candidate.lineage.retrieval_version)} / ${safeText(candidate.lineage.schema_version)}`} />
                      <Definition label="Validated revision" value={candidate.validation.validated_revision} />
                      <Definition label="Validator version" mono value={safeText(candidate.validation.validator_version)} />
                      <Definition label="Validation passed" value={candidate.validation.passed ? "Yes" : "No"} />
                    </dl>
                    <h3 className="mt-5 font-semibold">Candidate source provenance</h3>
                    <ol className="mt-3 grid gap-3 lg:grid-cols-2">
                      {candidate.lineage.provenance.slice(0, MAX_DISPLAY_RECORDS).map((record, index) => (
                        <li className="rounded-xl border border-slate-200 bg-slate-50 p-3" key={index}>
                          <PlainRecord record={record} />
                        </li>
                      ))}
                    </ol>
                  </section>

                  <CandidateHistory candidate={candidate} />

                  {evidenceLoading ? (
                    <p role="status">Loading generation and validation evidence by ID…</p>
                  ) : evidenceError ? (
                    <ErrorPanel
                      error={evidenceError}
                      onRetry={() => void loadEvidence(candidate)}
                      retryLabel="Retry candidate evidence"
                    />
                  ) : generation && validation ? (
                    <>
                      <GeneratedEvidence generation={generation} />
                      <ValidationEvidence findings={findings} validation={validation} />
                    </>
                  ) : (
                    <p className="text-sm text-slate-600">Generation or validation evidence is unavailable.</p>
                  )}
                </>
              ) : candidates.length ? (
                <section className="rounded-2xl border border-dashed border-slate-400 bg-white p-6">
                  <h2 className="text-xl font-semibold">Select a review candidate</h2>
                  <p className="mt-2 text-sm leading-6 text-slate-600">
                    Detail, generation, validation, revisions, and events load only after an explicit queue selection.
                  </p>
                </section>
              ) : null}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
