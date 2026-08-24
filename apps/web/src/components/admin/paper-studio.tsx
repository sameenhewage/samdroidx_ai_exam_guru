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
import { Button, Form } from "react-aria-components";

import { Badge } from "@/components/ui/badge";

type Exam = components["schemas"]["ExamConfigurationResponse"];
type Medium = components["schemas"]["MediumResponse"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type Blueprint = components["schemas"]["PaperBlueprintResponse"];
type BlueprintSummary = components["schemas"]["PaperBlueprintSummaryResponse"];
type BlueprintSlot = components["schemas"]["BlueprintSlotResponse"];
type CandidateSummary = components["schemas"]["ReviewCandidateSummaryResponse"];
type PaperSummary = components["schemas"]["PaperSummaryResponse"];
type PaperAggregate = components["schemas"]["PaperAggregateResponse"];
type PaperDraft = components["schemas"]["PaperDraftVersionResponse"];
type PublicationSummary = components["schemas"]["PublishedPaperVersionSummaryResponse"];
type Publication = components["schemas"]["PublishedPaperVersionResponse"];
type Archive = components["schemas"]["PaperArchiveResponse"];
type QuestionContent = components["schemas"]["QuestionContentResponse"];
type PaperDraftRequest = components["schemas"]["PaperDraftCreateRequest"];
type PaperRevisionRequest = components["schemas"]["PaperRevisionCreateRequest"];
type PaperPublishRequest = components["schemas"]["PaperPublishRequest"];
type PaperArchiveRequest = components["schemas"]["PaperArchiveRequest"];
type Role = "admin" | "reviewer";
type SelectionMap = Record<string, string>;
type ApiOutcome = { error?: unknown; response: Response };
type JsonObject = Record<string, unknown>;
type Surface = "workspace" | "curriculum" | "blueprint" | "paper" | "publication" | "command";
type Operation = "archive" | "create" | "publish" | "revise";
type UiError = {
  code: string;
  message: string;
  reload?: boolean;
  title: string;
};

const LIST_LIMIT = 100;
const MAX_DISPLAY_TEXT = 4_096;
const MAX_DISPLAY_ITEMS = 200;
const MAX_TITLE_CHARACTERS = 512;
const MAX_ARCHIVE_REASON_CHARACTERS = 1_024;

const fieldClass = "grid gap-1.5 text-sm font-semibold text-slate-700";
const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 outline-none transition focus:border-amber-500 focus:ring-2 focus:ring-amber-200 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none transition hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-400 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const dangerButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg border border-red-700 bg-red-700 px-5 py-2.5 text-sm font-semibold text-white outline-none transition hover:bg-red-800 focus-visible:ring-2 focus-visible:ring-red-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function safeText(value: unknown, fallback = "Not recorded"): string {
  if (value === null || value === undefined || value === "") return fallback;
  let text: string;
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

function apiError(error: unknown, status: number, surface: Surface): UiError {
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
        surface === "command"
          ? "This account cannot perform this paper lifecycle command. Publishing and archiving require administrator paper-publish permission."
          : "This account cannot read or assemble papers. Ask an administrator to verify content-review permission.",
      title: surface === "workspace" ? "Paper Studio permission required" : "Paper permission required",
    };
  }
  if (status === 404) {
    return {
      code,
      message:
        "The selected curriculum, blueprint, paper, draft, publication, or archive no longer exists in this scope. Reload authoritative records.",
      reload: true,
      title: "Paper resource not found",
    };
  }
  if (status === 409) {
    const conflicts: Record<string, UiError> = {
      paper_idempotency_conflict: {
        code,
        message:
          "This operation key is already bound to different immutable draft input. Reload the paper list and begin a new explicit assembly operation.",
        reload: true,
        title: "Draft identity conflict",
      },
      paper_integrity_invalid: {
        code,
        message:
          "Persisted paper, candidate, or publication integrity verification failed. No unsafe lifecycle transition was accepted.",
        reload: true,
        title: "Paper integrity conflict",
      },
      paper_persistence_conflict: {
        code,
        message: "A concurrent persistence conflict occurred. Reload authoritative paper state.",
        reload: true,
        title: "Paper persistence conflict",
      },
      paper_state_conflict: {
        code,
        message:
          "The paper is no longer in the lifecycle state required for this command. Reload authoritative paper state.",
        reload: true,
        title: "Paper state changed",
      },
      paper_version_conflict: {
        code,
        message:
          "Another reviewer changed this paper version. Reload the authoritative aggregate before publishing, revising, or archiving.",
        reload: true,
        title: "Paper version changed",
      },
    };
    return (
      conflicts[code] ?? {
        code,
        message: "Authoritative paper state changed. Reload before retrying.",
        reload: true,
        title: "Paper conflict",
      }
    );
  }
  if (status === 413) {
    return {
      code: "request_too_large",
      message: "The bounded web request was too large. Reduce the selection before retrying.",
      title: "Paper request is too large",
    };
  }
  if (status === 422) {
    if (code === "paper_candidate_selection_too_large") {
      return {
        code,
        message:
          "The authoritative candidate selection exceeds the bounded reconstruction limit. Reduce persisted candidate source size or split the blueprint before retrying.",
        title: "Candidate selection is too large",
      };
    }
    if (code === "paper_candidate_selection_invalid") {
      return {
        code,
        message:
          "The server rejected the candidate set. Select exactly one unique, approved, same-blueprint candidate for every exact slot.",
        title: "Candidate selection is invalid",
      };
    }
    return {
      code,
      message:
        "The server rejected this bounded paper command. Check the required title, exact candidate coverage, version, and archive reason.",
      title: "Paper command rejected",
    };
  }
  return {
    code,
    message: "The paper request could not be completed. Retry or contact an administrator if it persists.",
    title:
      surface === "workspace"
        ? "Paper Studio unavailable"
        : surface === "curriculum"
          ? "Paper curriculum data unavailable"
          : surface === "blueprint"
            ? "Blueprint assembly data unavailable"
            : surface === "paper"
              ? "Paper detail unavailable"
              : surface === "publication"
                ? "Publication snapshot unavailable"
                : "Paper command failed",
  };
}

function networkError(surface: Surface): UiError {
  return {
    code: "network_error",
    message: "The API could not be reached. Check the connection and retry the same explicit operation.",
    title:
      surface === "workspace"
        ? "Paper Studio unavailable"
        : surface === "curriculum"
          ? "Paper curriculum data unavailable"
          : surface === "blueprint"
            ? "Blueprint assembly data unavailable"
            : surface === "paper"
              ? "Paper detail unavailable"
              : surface === "publication"
                ? "Publication snapshot unavailable"
                : "Paper command connection failed",
  };
}

function generatedIdempotencyKey(): string {
  const cryptoObject = globalThis.crypto;
  let random: string;
  if (typeof cryptoObject?.randomUUID === "function") {
    random = cryptoObject.randomUUID();
  } else {
    const bytes = new Uint8Array(16);
    if (typeof cryptoObject?.getRandomValues !== "function") {
      throw new Error("secure browser randomness is unavailable");
    }
    cryptoObject.getRandomValues(bytes);
    random = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  }
  const key = `paper-draft-${random}`;
  if (key.length > 128 || /\s/.test(key)) {
    throw new Error("generated paper idempotency key is outside the client boundary");
  }
  return key;
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
      <p className="mt-2 break-all font-mono text-xs">Code: {safeText(error.code)}</p>
      {onRetry && retryLabel ? (
        <Button className={`${secondaryButton} mt-3 border-red-300`} onPress={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
    </section>
  );
}

function Definition({
  label,
  mono = false,
  value,
}: {
  label: string;
  mono?: boolean;
  value: ReactNode;
}) {
  return (
    <div className="min-w-0 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <dt className="text-xs font-semibold text-slate-500">{label}</dt>
      <dd
        className={`mt-1 break-words whitespace-pre-wrap text-sm ${mono ? "font-mono text-xs" : ""}`}
      >
        {value}
      </dd>
    </div>
  );
}

function StateBadge({ state }: { state: PaperSummary["state"] }) {
  const classes =
    state === "published"
      ? "border-emerald-300 bg-emerald-50 text-emerald-900"
      : state === "archived"
        ? "border-slate-400 bg-slate-100 text-slate-800"
        : "border-amber-300 bg-amber-50 text-amber-950";
  return <Badge className={classes}>{titleCase(state)}</Badge>;
}

function QuestionContentView({ content, heading }: { content: QuestionContent; heading: string }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4">
      <h5 className="font-semibold">{heading}</h5>
      <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Definition label="Question type" value={titleCase(content.question_type)} />
        <Definition label="Marks" value={content.marks} />
        <Definition label="Stem" value={safeText(content.stem)} />
        <Definition label="Answer" value={safeText(content.answer)} />
        <Definition label="Explanation" value={safeText(content.explanation)} />
      </dl>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <h6 className="text-sm font-semibold">Options</h6>
          {content.options.length ? (
            <ol className="mt-2 space-y-2">
              {content.options.slice(0, MAX_DISPLAY_ITEMS).map((option, index) => (
                <li className="rounded-lg border border-slate-200 bg-slate-50 p-3" key={`${option.option_id}-${index}`}>
                  <span className="font-mono text-xs">{safeText(option.option_id, `Option ${index + 1}`)}</span>
                  <p className="mt-1 break-words whitespace-pre-wrap text-sm">{safeText(option.text)}</p>
                </li>
              ))}
            </ol>
          ) : (
            <p className="mt-2 text-sm text-slate-600">No options for this question type.</p>
          )}
        </div>
        <div>
          <h6 className="text-sm font-semibold">Marking guide</h6>
          <ol className="mt-2 space-y-2">
            {content.marking_guide.slice(0, MAX_DISPLAY_ITEMS).map((item, index) => (
              <li className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm" key={index}>
                {index + 1}. {safeText(item)}
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}

function PublicationSnapshot({ publication }: { publication: Publication }) {
  const snapshot = publication.snapshot;
  return (
    <section
      aria-label="Verified immutable publication snapshot"
      className="rounded-2xl border-2 border-emerald-700 bg-emerald-50/40 p-5 sm:p-6"
    >
      <div className="rounded-xl bg-emerald-950 p-4 text-white">
        <p className="text-xs font-semibold tracking-[0.18em] text-emerald-200 uppercase">
          Verified student-servable publication
        </p>
        <h3 className="mt-2 text-xl font-semibold">Immutable, hash-verified snapshot</h3>
        <p className="mt-2 max-w-4xl text-sm leading-6 text-emerald-50">
          Student serving requires no live LLM or provider call. The API returned a fully materialized,
          immutable, hash-verified snapshot; inspecting it does not regenerate or mutate content.
        </p>
      </div>

      <dl className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Definition label="Snapshot title" value={safeText(snapshot.title)} />
        <Definition label="Content hash" mono value={safeText(publication.content_hash)} />
        <Definition label="Publication version" value={publication.version} />
        <Definition label="Snapshot paper version" value={snapshot.paper_version} />
        <Definition label="Previous version" value={publication.previous_version ?? "None"} />
        <Definition
          label="Supersedes hash"
          mono
          value={safeText(publication.supersedes_content_hash, "None")}
        />
        <Definition label="Snapshot schema" mono value={safeText(snapshot.schema)} />
        <Definition label="Published by" mono value={publication.published_by} />
        <Definition label="Published at" value={displayDate(publication.published_at)} />
        <Definition label="Persistent paper ID" mono value={publication.paper_id} />
        <Definition label="Blueprint record" mono value={snapshot.blueprint.paper_blueprint_id} />
        <Definition label="Blueprint ID" mono value={safeText(snapshot.blueprint.blueprint_id)} />
        <Definition label="Blueprint version" mono value={safeText(snapshot.blueprint.blueprint_version)} />
        <Definition
          label="Exact slot chain"
          mono
          value={snapshot.blueprint.slot_ids
            .slice(0, MAX_DISPLAY_ITEMS)
            .map((item) => safeText(item))
            .join("\n")}
        />
      </dl>

      <div className="mt-6 space-y-6">
        {snapshot.questions.slice(0, MAX_DISPLAY_ITEMS).map((question, questionIndex) => (
          <article className="rounded-2xl border border-slate-300 bg-white p-5" key={question.slot_id}>
            <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 pb-4">
              <div>
                <p className="font-mono text-xs text-slate-500">Question {questionIndex + 1}</p>
                <h4 className="mt-1 break-words font-semibold">{safeText(question.slot_id)}</h4>
              </div>
              <Badge className="border-emerald-300 bg-emerald-50 text-emerald-900">Approved</Badge>
            </header>

            <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Definition label="Candidate ID" mono value={question.candidate_id} />
              <Definition label="Candidate version" value={question.candidate_version} />
              <Definition label="Content revision" value={question.content_revision} />
              <Definition label="Blueprint slot" mono value={safeText(question.slot_id)} />
            </dl>
            <div className="mt-4">
              <QuestionContentView content={question.content} heading="Published question, answer, and marking" />
            </div>

            <section className="mt-5 rounded-xl border border-slate-200 p-4">
              <h5 className="font-semibold">Generation, blueprint, and source provenance</h5>
              <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <Definition label="Generation ID" mono value={question.lineage.generation_id} />
                <Definition label="Generation attempt" mono value={question.lineage.generation_attempt_id} />
                <Definition label="Provider" mono value={safeText(question.lineage.provider)} />
                <Definition label="Model version" mono value={safeText(question.lineage.model_version)} />
                <Definition label="Prompt version" mono value={safeText(question.lineage.prompt_version)} />
                <Definition label="Retrieval version" mono value={safeText(question.lineage.retrieval_version)} />
                <Definition label="Question schema" mono value={safeText(question.lineage.schema_version)} />
                <Definition label="Blueprint ID" mono value={safeText(question.lineage.blueprint_id)} />
                <Definition label="Blueprint version" mono value={safeText(question.lineage.blueprint_version)} />
                <Definition label="Blueprint slot" mono value={safeText(question.lineage.blueprint_slot_id)} />
              </dl>
              <h6 className="mt-4 text-sm font-semibold">Immutable provenance references</h6>
              <ol className="mt-2 grid gap-3 lg:grid-cols-2">
                {question.lineage.provenance.slice(0, MAX_DISPLAY_ITEMS).map((source, sourceIndex) => (
                  <li className="rounded-lg border border-slate-200 bg-slate-50 p-3" key={`${source.chunk_id}-${sourceIndex}`}>
                    <dl className="grid gap-2 sm:grid-cols-2">
                      <Definition label="Source document" mono value={safeText(source.source_document_id)} />
                      <Definition label="Source version" mono value={safeText(source.source_version)} />
                      <Definition label="Page" value={source.page_number} />
                      <Definition label="Chunk" mono value={safeText(source.chunk_id)} />
                    </dl>
                  </li>
                ))}
              </ol>
            </section>

            <section className="mt-5 rounded-xl border border-slate-200 p-4">
              <h5 className="font-semibold">Validation evidence</h5>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                Automated validation is immutable evidence for generated revision 1; reviewer edits and the
                approval decision remain visible separately.
              </p>
              <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <Definition label="Passed" value={question.validation.passed ? "Yes" : "No"} />
                <Definition label="Validated revision" value={question.validation.validated_revision} />
                <Definition label="Validation run" mono value={question.validation.validation_run_id} />
                <Definition label="Validator version" mono value={safeText(question.validation.validator_version)} />
                <Definition
                  label="Finding references"
                  mono
                  value={question.validation.finding_refs
                    .slice(0, MAX_DISPLAY_ITEMS)
                    .map((item) => safeText(item))
                    .join("\n")}
                />
              </dl>
            </section>

            <section className="mt-5 rounded-xl border border-slate-200 p-4">
              <h5 className="font-semibold">Reviewer revisions</h5>
              <div className="mt-3 space-y-4">
                {question.revisions.slice(0, MAX_DISPLAY_ITEMS).map((revision) => (
                  <article className="rounded-xl border border-slate-200 bg-slate-50 p-4" key={revision.revision}>
                    <div className="flex flex-wrap justify-between gap-2">
                      <h6 className="font-semibold">Revision {revision.revision}</h6>
                      <span className="break-all font-mono text-xs text-slate-600">
                        {revision.reviewer_id ? `Reviewer ${revision.reviewer_id}` : "Generated revision"}
                      </span>
                    </div>
                    <p className="mt-2 break-words whitespace-pre-wrap text-sm text-slate-700">
                      {safeText(revision.reason, "No revision reason")}
                    </p>
                    <div className="mt-3">
                      <QuestionContentView
                        content={revision.content}
                        heading={`Revision ${revision.revision} content`}
                      />
                    </div>
                  </article>
                ))}
              </div>
            </section>

            <section className="mt-5 grid gap-4 lg:grid-cols-2">
              <div className="rounded-xl border border-slate-200 p-4">
                <h5 className="font-semibold">Review history</h5>
                <ol className="mt-3 space-y-3">
                  {question.review_history.slice(0, MAX_DISPLAY_ITEMS).map((event, eventIndex) => (
                    <li className="rounded-lg border border-slate-200 bg-slate-50 p-3" key={`${event.action}-${event.candidate_version}-${eventIndex}`}>
                      <div className="flex flex-wrap justify-between gap-2">
                        <span className="font-semibold">{titleCase(event.action)}</span>
                        <span className="font-mono text-xs">v{event.candidate_version}</span>
                      </div>
                      <p className="mt-2 break-words whitespace-pre-wrap text-sm">
                        {safeText(event.reason, "No note recorded")}
                      </p>
                      <p className="mt-2 break-all font-mono text-xs text-slate-600">
                        Reviewer {event.reviewer_id}
                      </p>
                    </li>
                  ))}
                </ol>
              </div>
              <div className="rounded-xl border border-emerald-300 bg-emerald-50 p-4">
                <h5 className="font-semibold">Review decision</h5>
                <Badge className="mt-3 border-emerald-300 bg-white text-emerald-900">
                  {titleCase(question.decision.state)}
                </Badge>
                <dl className="mt-3 grid gap-3">
                  <Definition label="Decision version" value={question.decision.candidate_version} />
                  <Definition label="Reviewer" mono value={question.decision.reviewer_id} />
                  <Definition
                    label="Decision note"
                    value={safeText(question.decision.reason, "No decision note recorded")}
                  />
                </dl>
              </div>
            </section>
          </article>
        ))}
      </div>
    </section>
  );
}

function uniqueCandidatesForSlot(candidates: CandidateSummary[], slotId: string): CandidateSummary[] {
  const unique = new Map<string, CandidateSummary>();
  for (const candidate of candidates) {
    if (candidate.blueprint_slot_id === slotId && !unique.has(candidate.id)) {
      unique.set(candidate.id, candidate);
    }
  }
  return [...unique.values()];
}

function uncoveredExactSlots(slots: BlueprintSlot[], candidates: CandidateSummary[]): string[] {
  const ownerByCandidate = new Map<string, string>();
  const slotById = new Map(slots.map((slot) => [slot.slot_id, slot]));

  function assign(slotId: string, visited: Set<string>): boolean {
    for (const candidate of uniqueCandidatesForSlot(candidates, slotId)) {
      if (visited.has(candidate.id)) continue;
      visited.add(candidate.id);
      const previousSlot = ownerByCandidate.get(candidate.id);
      if (!previousSlot || (slotById.has(previousSlot) && assign(previousSlot, visited))) {
        ownerByCandidate.set(candidate.id, slotId);
        return true;
      }
    }
    return false;
  }

  const uncovered: string[] = [];
  for (const slot of slots) {
    if (!assign(slot.slot_id, new Set())) uncovered.push(slot.slot_id);
  }
  return uncovered;
}

function hasCompleteSelection(
  slots: BlueprintSlot[],
  candidates: CandidateSummary[],
  selections: SelectionMap,
): boolean {
  const selected = slots.map((slot) => selections[slot.slot_id] ?? "");
  if (selected.some((candidateId) => !candidateId) || new Set(selected).size !== slots.length) {
    return false;
  }
  return slots.every((slot) =>
    uniqueCandidatesForSlot(candidates, slot.slot_id).some(
      (candidate) => candidate.id === selections[slot.slot_id],
    ),
  );
}

function ExactSlotSelections({
  blueprint,
  candidates,
  labelPrefix,
  onChange,
  selections,
  testIds = false,
}: {
  blueprint: Blueprint;
  candidates: CandidateSummary[];
  labelPrefix: string;
  onChange: (slotId: string, candidateId: string) => void;
  selections: SelectionMap;
  testIds?: boolean;
}) {
  const slots = blueprint.blueprint.slots;
  const selectedByOtherSlots = (slotId: string) =>
    new Set(
      Object.entries(selections)
        .filter(([selectedSlot]) => selectedSlot !== slotId)
        .map(([, candidateId]) => candidateId)
        .filter(Boolean),
    );

  return (
    <ol className="space-y-4">
      {slots.map((slot) => {
        const available = uniqueCandidatesForSlot(candidates, slot.slot_id);
        const usedElsewhere = selectedByOtherSlots(slot.slot_id);
        return (
          <li
            className="rounded-xl border border-slate-300 bg-slate-50 p-4"
            data-testid={testIds ? "exact-blueprint-slot" : undefined}
            key={slot.slot_id}
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <span className="font-mono text-xs text-slate-500">{slot.ordinal}</span>
                <code className="ml-2 break-all text-xs font-semibold">{safeText(slot.slot_id)}</code>
                <p className="mt-2 text-sm font-semibold">
                  {safeText(slot.section_title)} · {titleCase(slot.question_type)} · {slot.marks} marks
                </p>
                <p className="mt-1 text-xs text-slate-600">
                  {titleCase(slot.difficulty)} · {safeText(slot.archetype)}
                </p>
              </div>
              <Badge>{available.length} approved</Badge>
            </div>
            <label className={`${fieldClass} mt-4`}>
              {labelPrefix} {slot.slot_id}
              <select
                className={inputClass}
                onChange={(event) => onChange(slot.slot_id, event.target.value)}
                required
                value={selections[slot.slot_id] ?? ""}
              >
                <option value="">Select one approved candidate</option>
                {available.map((candidate) => (
                  <option
                    disabled={usedElsewhere.has(candidate.id)}
                    key={candidate.id}
                    value={candidate.id}
                  >
                    {safeText(candidate.stem_preview)} · revision {candidate.current_revision} · {candidate.id}
                  </option>
                ))}
              </select>
            </label>
          </li>
        );
      })}
    </ol>
  );
}

export function PaperStudio({ role }: { role: Role }) {
  const api = useMemo(() => createApiClient(globalThis.location?.origin ?? "http://localhost"), []);
  const [exams, setExams] = useState<Exam[]>([]);
  const [media, setMedia] = useState<Medium[]>([]);
  const [curricula, setCurricula] = useState<Curriculum[]>([]);
  const [selectedCurriculumId, setSelectedCurriculumId] = useState("");
  const [blueprints, setBlueprints] = useState<BlueprintSummary[]>([]);
  const [selectedBlueprintId, setSelectedBlueprintId] = useState("");
  const [blueprint, setBlueprint] = useState<Blueprint | null>(null);
  const [approvedCandidates, setApprovedCandidates] = useState<CandidateSummary[]>([]);
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [selectedPaperId, setSelectedPaperId] = useState("");
  const [paper, setPaper] = useState<PaperAggregate | null>(null);
  const [drafts, setDrafts] = useState<PaperDraft[]>([]);
  const [publicationVersions, setPublicationVersions] = useState<PublicationSummary[]>([]);
  const [selectedPublicationVersion, setSelectedPublicationVersion] = useState<number | null>(null);
  const [publication, setPublication] = useState<Publication | null>(null);
  const [archive, setArchive] = useState<Archive | null>(null);
  const [paperTitle, setPaperTitle] = useState("");
  const [assemblySelections, setAssemblySelections] = useState<SelectionMap>({});
  const [revisionSelections, setRevisionSelections] = useState<SelectionMap>({});
  const [revisionTitle, setRevisionTitle] = useState("");
  const [archiveReason, setArchiveReason] = useState("");
  const [workspaceLoading, setWorkspaceLoading] = useState(true);
  const [curriculumLoading, setCurriculumLoading] = useState(false);
  const [blueprintLoading, setBlueprintLoading] = useState(false);
  const [paperLoading, setPaperLoading] = useState(false);
  const [publicationLoading, setPublicationLoading] = useState(false);
  const [busyAction, setBusyAction] = useState<Operation | "">("");
  const [workspaceError, setWorkspaceError] = useState<UiError | null>(null);
  const [curriculumError, setCurriculumError] = useState<UiError | null>(null);
  const [blueprintError, setBlueprintError] = useState<UiError | null>(null);
  const [paperError, setPaperError] = useState<UiError | null>(null);
  const [publicationError, setPublicationError] = useState<UiError | null>(null);
  const [operationError, setOperationError] = useState<UiError | null>(null);
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");

  const workspaceRequestId = useRef(0);
  const curriculumRequestId = useRef(0);
  const blueprintRequestId = useRef(0);
  const paperRequestId = useRef(0);
  const publicationRequestId = useRef(0);
  const operationRequestId = useRef(0);
  const operationInFlight = useRef(false);

  const curriculumChoices = useMemo(() => {
    const examsById = new Map(exams.map((exam) => [exam.id, exam]));
    const mediaById = new Map(media.map((item) => [item.id, item]));
    return curricula.filter((curriculum) => {
      const exam = examsById.get(curriculum.exam_configuration_id);
      return curriculum.active && exam?.active && exam.grade === 5 && mediaById.get(curriculum.medium_id)?.active;
    });
  }, [curricula, exams, media]);

  const selectedPaperSummary = useMemo(
    () => papers.find((item) => item.id === selectedPaperId) ?? null,
    [papers, selectedPaperId],
  );

  const uncoveredSlots = useMemo(
    () => (blueprint ? uncoveredExactSlots(blueprint.blueprint.slots, approvedCandidates) : []),
    [approvedCandidates, blueprint],
  );
  const assemblyComplete = useMemo(
    () =>
      blueprint
        ? hasCompleteSelection(blueprint.blueprint.slots, approvedCandidates, assemblySelections)
        : false,
    [approvedCandidates, assemblySelections, blueprint],
  );
  const revisionComplete = useMemo(
    () =>
      blueprint
        ? hasCompleteSelection(blueprint.blueprint.slots, approvedCandidates, revisionSelections)
        : false,
    [approvedCandidates, blueprint, revisionSelections],
  );

  const loadWorkspace = useCallback(async () => {
    const requestId = ++workspaceRequestId.current;
    setWorkspaceLoading(true);
    setWorkspaceError(null);
    try {
      const responses = await Promise.all([
        api.GET("/api/v1/admin/exam-configurations"),
        api.GET("/api/v1/admin/media"),
        api.GET("/api/v1/admin/curriculum-versions"),
      ]);
      if (requestId !== workspaceRequestId.current) return;
      const failed = firstFailure(responses);
      if (failed?.error !== undefined) {
        setWorkspaceError(apiError(failed.error, failed.response.status, "workspace"));
        return;
      }
      const nextExams = (responses[0].data ?? []) as Exam[];
      const nextMedia = (responses[1].data ?? []) as Medium[];
      const nextCurricula = (responses[2].data ?? []) as Curriculum[];
      const examsById = new Map(nextExams.map((exam) => [exam.id, exam]));
      const mediaById = new Map(nextMedia.map((item) => [item.id, item]));
      const active = nextCurricula.filter((curriculum) => {
        const exam = examsById.get(curriculum.exam_configuration_id);
        return curriculum.active && exam?.active && exam.grade === 5 && mediaById.get(curriculum.medium_id)?.active;
      });
      setExams(nextExams);
      setMedia(nextMedia);
      setCurricula(nextCurricula);
      setSelectedCurriculumId((current) =>
        active.some((item) => item.id === current) ? current : (active[0]?.id ?? ""),
      );
    } catch {
      if (requestId === workspaceRequestId.current) setWorkspaceError(networkError("workspace"));
    } finally {
      if (requestId === workspaceRequestId.current) setWorkspaceLoading(false);
    }
  }, [api]);

  const loadCurriculumData = useCallback(
    async (curriculumId: string, preferredPaperId?: string) => {
      const requestId = ++curriculumRequestId.current;
      setCurriculumLoading(true);
      setCurriculumError(null);
      try {
        const path = { curriculum_version_id: curriculumId };
        const responses = await Promise.all([
          api.GET("/api/v1/admin/curricula/{curriculum_version_id}/blueprints", {
            params: { path, query: { limit: LIST_LIMIT, offset: 0 } },
          }),
          api.GET("/api/v1/admin/curricula/{curriculum_version_id}/papers", {
            params: { path, query: { limit: LIST_LIMIT, offset: 0 } },
          }),
        ]);
        if (requestId !== curriculumRequestId.current) return;
        const failed = firstFailure(responses);
        if (failed?.error !== undefined) {
          setCurriculumError(apiError(failed.error, failed.response.status, "curriculum"));
          return;
        }
        const nextBlueprints = ((responses[0].data ?? []) as BlueprintSummary[]).filter(
          (item) => item.curriculum_version_id === curriculumId,
        );
        const nextPapers = ((responses[1].data ?? []) as PaperSummary[]).filter(
          (item) => item.curriculum_version_id === curriculumId,
        );
        setBlueprints(nextBlueprints);
        setPapers(nextPapers);
        setSelectedBlueprintId((current) =>
          nextBlueprints.some((item) => item.id === current) ? current : (nextBlueprints[0]?.id ?? ""),
        );
        setSelectedPaperId((current) => {
          const desired = preferredPaperId ?? current;
          return nextPapers.some((item) => item.id === desired) ? desired : (nextPapers[0]?.id ?? "");
        });
        if (!nextBlueprints.length) {
          setBlueprint(null);
          setApprovedCandidates([]);
        }
        if (!nextPapers.length) {
          setPaper(null);
          setDrafts([]);
          setPublicationVersions([]);
          setSelectedPublicationVersion(null);
          setPublication(null);
          setArchive(null);
        }
      } catch {
        if (requestId === curriculumRequestId.current) setCurriculumError(networkError("curriculum"));
      } finally {
        if (requestId === curriculumRequestId.current) setCurriculumLoading(false);
      }
    },
    [api],
  );

  const loadBlueprint = useCallback(
    async (curriculumId: string, blueprintId: string) => {
      const requestId = ++blueprintRequestId.current;
      setBlueprintLoading(true);
      setBlueprintError(null);
      try {
        const path = {
          curriculum_version_id: curriculumId,
          paper_blueprint_id: blueprintId,
        };
        const responses = await Promise.all([
          api.GET(
            "/api/v1/admin/curricula/{curriculum_version_id}/blueprints/{paper_blueprint_id}",
            { params: { path } },
          ),
          api.GET("/api/v1/admin/curricula/{curriculum_version_id}/review-candidates", {
            params: {
              path: { curriculum_version_id: curriculumId },
              query: {
                limit: LIST_LIMIT,
                offset: 0,
                paper_blueprint_id: blueprintId,
                state: "approved",
              },
            },
          }),
        ]);
        if (requestId !== blueprintRequestId.current) return;
        const failed = firstFailure(responses);
        if (failed?.error !== undefined) {
          setBlueprintError(apiError(failed.error, failed.response.status, "blueprint"));
          setBlueprint(null);
          setApprovedCandidates([]);
          return;
        }
        const detail = responses[0].data as Blueprint | undefined;
        if (!detail || detail.id !== blueprintId || detail.curriculum_version_id !== curriculumId) {
          setBlueprintError({
            code: "paper_blueprint_scope_mismatch",
            message: "The returned immutable blueprint does not match the selected curriculum and record.",
            title: "Blueprint scope mismatch",
          });
          setBlueprint(null);
          setApprovedCandidates([]);
          return;
        }
        const candidates = ((responses[1].data ?? []) as CandidateSummary[]).filter(
          (candidate) =>
            candidate.curriculum_version_id === curriculumId &&
            candidate.state === "approved" &&
            candidate.paper_blueprint_id === detail.id &&
            candidate.blueprint_id === detail.blueprint_id &&
            candidate.blueprint_version === detail.blueprint_id,
        );
        setBlueprint(detail);
        setApprovedCandidates(candidates);
        setAssemblySelections({});
        setPaperTitle(detail.blueprint.title.slice(0, MAX_TITLE_CHARACTERS));
      } catch {
        if (requestId === blueprintRequestId.current) {
          setBlueprintError(networkError("blueprint"));
          setBlueprint(null);
          setApprovedCandidates([]);
        }
      } finally {
        if (requestId === blueprintRequestId.current) setBlueprintLoading(false);
      }
    },
    [api],
  );

  const loadPaper = useCallback(
    async (curriculumId: string, paperId: string, state: PaperSummary["state"]) => {
      const requestId = ++paperRequestId.current;
      setPaperLoading(true);
      setPaperError(null);
      try {
        const path = { curriculum_version_id: curriculumId, paper_id: paperId };
        const responses = await Promise.all([
          api.GET("/api/v1/admin/curricula/{curriculum_version_id}/papers/{paper_id}", {
            params: { path },
          }),
          api.GET(
            "/api/v1/admin/curricula/{curriculum_version_id}/papers/{paper_id}/draft-versions",
            { params: { path, query: { limit: LIST_LIMIT, offset: 0 } } },
          ),
          api.GET(
            "/api/v1/admin/curricula/{curriculum_version_id}/papers/{paper_id}/publication-versions",
            { params: { path, query: { limit: LIST_LIMIT, offset: 0 } } },
          ),
          ...(state === "archived"
            ? [
                api.GET(
                  "/api/v1/admin/curricula/{curriculum_version_id}/papers/{paper_id}/archive",
                  { params: { path } },
                ),
              ]
            : []),
        ]);
        if (requestId !== paperRequestId.current) return;
        const failed = firstFailure(responses);
        if (failed?.error !== undefined) {
          setPaperError(apiError(failed.error, failed.response.status, "paper"));
          return;
        }
        const aggregate = responses[0].data as PaperAggregate | undefined;
        if (
          !aggregate ||
          aggregate.id !== paperId ||
          aggregate.curriculum_version_id !== curriculumId
        ) {
          setPaperError({
            code: "paper_scope_mismatch",
            message: "The returned paper aggregate does not match the selected curriculum and paper.",
            title: "Paper scope mismatch",
          });
          return;
        }
        const nextDrafts = ((responses[1].data ?? []) as PaperDraft[]).filter(
          (item) => item.paper_id === paperId && item.curriculum_version_id === curriculumId,
        );
        const nextPublications = ((responses[2].data ?? []) as PublicationSummary[]).filter(
          (item) => item.paper_id === paperId && item.curriculum_version_id === curriculumId,
        );
        const nextArchive = state === "archived" ? (responses[3]?.data as Archive | undefined) : undefined;
        if (
          nextArchive &&
          (nextArchive.paper_id !== paperId || nextArchive.curriculum_version_id !== curriculumId)
        ) {
          setPaperError({
            code: "paper_archive_scope_mismatch",
            message: "The terminal archive event does not match the selected paper scope.",
            title: "Archive scope mismatch",
          });
          return;
        }
        setPaper(aggregate);
        setDrafts(nextDrafts);
        setPublicationVersions(nextPublications);
        setArchive(nextArchive ?? null);
        setSelectedBlueprintId(aggregate.paper_blueprint_id);
        setSelectedPublicationVersion((current) => {
          if (current !== null && nextPublications.some((item) => item.version === current)) return current;
          return nextPublications.reduce<number | null>(
            (latest, item) => (latest === null || item.version > latest ? item.version : latest),
            null,
          );
        });
        if (!nextPublications.length) setPublication(null);
      } catch {
        if (requestId === paperRequestId.current) setPaperError(networkError("paper"));
      } finally {
        if (requestId === paperRequestId.current) setPaperLoading(false);
      }
    },
    [api],
  );

  const loadPublication = useCallback(
    async (curriculumId: string, paperId: string, version: number) => {
      const requestId = ++publicationRequestId.current;
      setPublicationLoading(true);
      setPublicationError(null);
      try {
        const response = await api.GET(
          "/api/v1/admin/curricula/{curriculum_version_id}/papers/{paper_id}/publication-versions/{version}",
          {
            params: {
              path: {
                curriculum_version_id: curriculumId,
                paper_id: paperId,
                version,
              },
            },
          },
        );
        if (requestId !== publicationRequestId.current) return;
        if (response.error !== undefined) {
          setPublicationError(apiError(response.error, response.response.status, "publication"));
          setPublication(null);
          return;
        }
        const detail = response.data as Publication | undefined;
        if (
          !detail ||
          detail.paper_id !== paperId ||
          detail.curriculum_version_id !== curriculumId ||
          detail.version !== version ||
          detail.snapshot.paper_id !== paperId ||
          detail.snapshot.paper_version !== version
        ) {
          setPublicationError({
            code: "publication_scope_mismatch",
            message: "The returned verified snapshot does not match the selected paper version.",
            title: "Publication scope mismatch",
          });
          setPublication(null);
          return;
        }
        setPublication(detail);
      } catch {
        if (requestId === publicationRequestId.current) {
          setPublicationError(networkError("publication"));
          setPublication(null);
        }
      } finally {
        if (requestId === publicationRequestId.current) setPublicationLoading(false);
      }
    },
    [api],
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
    const timeout = window.setTimeout(() => void loadCurriculumData(selectedCurriculumId), 0);
    return () => {
      window.clearTimeout(timeout);
      curriculumRequestId.current += 1;
    };
  }, [loadCurriculumData, selectedCurriculumId]);

  useEffect(() => {
    if (!selectedCurriculumId || !selectedBlueprintId) return;
    const timeout = window.setTimeout(
      () => void loadBlueprint(selectedCurriculumId, selectedBlueprintId),
      0,
    );
    return () => {
      window.clearTimeout(timeout);
      blueprintRequestId.current += 1;
    };
  }, [loadBlueprint, selectedBlueprintId, selectedCurriculumId]);

  useEffect(() => {
    if (!selectedCurriculumId || !selectedPaperSummary) return;
    const timeout = window.setTimeout(
      () =>
        void loadPaper(
          selectedCurriculumId,
          selectedPaperSummary.id,
          selectedPaperSummary.state,
        ),
      0,
    );
    return () => {
      window.clearTimeout(timeout);
      paperRequestId.current += 1;
    };
  }, [loadPaper, selectedCurriculumId, selectedPaperSummary]);

  useEffect(() => {
    if (!selectedCurriculumId || !selectedPaperId || selectedPublicationVersion === null) return;
    const timeout = window.setTimeout(
      () =>
        void loadPublication(
          selectedCurriculumId,
          selectedPaperId,
          selectedPublicationVersion,
        ),
      0,
    );
    return () => {
      window.clearTimeout(timeout);
      publicationRequestId.current += 1;
    };
  }, [loadPublication, selectedCurriculumId, selectedPaperId, selectedPublicationVersion]);

  useEffect(() => {
    if (!paper || paper.state !== "published" || !publication || !blueprint) return;
    if (publication.paper_id !== paper.id || publication.version !== paper.current_version) return;
    const next: SelectionMap = {};
    for (const question of publication.snapshot.questions) next[question.slot_id] = question.candidate_id;
    const timeout = window.setTimeout(() => {
      setRevisionSelections(next);
      setRevisionTitle("");
    }, 0);
    return () => window.clearTimeout(timeout);
  }, [blueprint, paper, publication]);

  useEffect(
    () => () => {
      operationRequestId.current += 1;
      operationInFlight.current = false;
    },
    [],
  );

  function changeSelection(
    setter: (value: SelectionMap | ((current: SelectionMap) => SelectionMap)) => void,
    slotId: string,
    candidateId: string,
  ) {
    setter((current) => {
      if (candidateId && Object.entries(current).some(([key, value]) => key !== slotId && value === candidateId)) {
        setFormError("A candidate can be selected only once in a paper.");
        return current;
      }
      setFormError("");
      return { ...current, [slotId]: candidateId };
    });
  }

  const reloadAuthoritativePaper = useCallback(() => {
    setOperationError(null);
    if (selectedCurriculumId && selectedPaperSummary) {
      void loadPaper(selectedCurriculumId, selectedPaperSummary.id, selectedPaperSummary.state);
    } else if (selectedCurriculumId) {
      void loadCurriculumData(selectedCurriculumId);
    }
  }, [loadCurriculumData, loadPaper, selectedCurriculumId, selectedPaperSummary]);

  async function createDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (operationInFlight.current || !selectedCurriculumId || !blueprint) return;
    const title = paperTitle.trim();
    if (!title || title.length > MAX_TITLE_CHARACTERS) {
      setFormError(`Enter a paper title of at most ${MAX_TITLE_CHARACTERS} characters.`);
      return;
    }
    if (!hasCompleteSelection(blueprint.blueprint.slots, approvedCandidates, assemblySelections)) {
      setFormError("Select exactly one unique approved candidate for every exact blueprint slot.");
      return;
    }
    const body: PaperDraftRequest = {
      candidate_ids: blueprint.blueprint.slots.map((slot) => assemblySelections[slot.slot_id]),
      paper_blueprint_id: blueprint.id,
      title,
    };
    const requestId = ++operationRequestId.current;
    operationInFlight.current = true;
    setBusyAction("create");
    setOperationError(null);
    setFormError("");
    setNotice("");
    try {
      const key = generatedIdempotencyKey();
      const response = await api.POST(
        "/api/v1/admin/curricula/{curriculum_version_id}/paper-drafts",
        {
          body,
          params: {
            header: { "Idempotency-Key": key },
            path: { curriculum_version_id: selectedCurriculumId },
          },
        },
      );
      if (requestId !== operationRequestId.current) return;
      if (response.error !== undefined) {
        setOperationError(apiError(response.error, response.response.status, "command"));
        return;
      }
      const result = response.data as PaperDraft | undefined;
      if (!result || result.curriculum_version_id !== selectedCurriculumId) {
        setOperationError({
          code: "paper_draft_scope_mismatch",
          message: "The returned immutable draft does not match the selected curriculum.",
          title: "Draft scope mismatch",
        });
        return;
      }
      setNotice(
        result.deduplicated
          ? `Matching immutable draft version ${result.version} already existed; it was reused.`
          : `Immutable draft version ${result.version} created.`,
      );
      await loadCurriculumData(selectedCurriculumId, result.paper_id);
    } catch {
      if (requestId === operationRequestId.current) setOperationError(networkError("command"));
    } finally {
      if (requestId === operationRequestId.current) {
        operationInFlight.current = false;
        setBusyAction("");
      }
    }
  }

  async function revisePaper(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      operationInFlight.current ||
      !selectedCurriculumId ||
      !paper ||
      paper.state !== "published" ||
      !blueprint
    ) {
      return;
    }
    if (!hasCompleteSelection(blueprint.blueprint.slots, approvedCandidates, revisionSelections)) {
      setFormError("Select exactly one unique approved candidate for every revision slot.");
      return;
    }
    const optionalTitle = revisionTitle.trim();
    if (optionalTitle.length > MAX_TITLE_CHARACTERS) {
      setFormError(`The optional revised title cannot exceed ${MAX_TITLE_CHARACTERS} characters.`);
      return;
    }
    const expectedVersion = paper.current_version;
    const body: PaperRevisionRequest = {
      candidate_ids: blueprint.blueprint.slots.map((slot) => revisionSelections[slot.slot_id]),
      expected_version: expectedVersion,
      ...(optionalTitle ? { title: optionalTitle } : {}),
    };
    const requestId = ++operationRequestId.current;
    operationInFlight.current = true;
    setBusyAction("revise");
    setOperationError(null);
    setFormError("");
    setNotice("");
    try {
      const response = await api.POST(
        "/api/v1/admin/curricula/{curriculum_version_id}/papers/{paper_id}/revisions",
        {
          body,
          params: {
            path: {
              curriculum_version_id: selectedCurriculumId,
              paper_id: paper.id,
            },
          },
        },
      );
      if (requestId !== operationRequestId.current) return;
      if (response.error !== undefined) {
        setOperationError(apiError(response.error, response.response.status, "command"));
        return;
      }
      const result = response.data as PaperDraft | undefined;
      if (!result || result.paper_id !== paper.id || result.curriculum_version_id !== selectedCurriculumId) {
        setOperationError({
          code: "paper_revision_scope_mismatch",
          message: "The returned revision draft does not match the selected paper.",
          title: "Revision scope mismatch",
        });
        return;
      }
      setNotice(
        `Revision draft version ${result.version} created from publication version ${expectedVersion}.`,
      );
      await loadCurriculumData(selectedCurriculumId, paper.id);
    } catch {
      if (requestId === operationRequestId.current) setOperationError(networkError("command"));
    } finally {
      if (requestId === operationRequestId.current) {
        operationInFlight.current = false;
        setBusyAction("");
      }
    }
  }

  async function publishPaper() {
    if (
      operationInFlight.current ||
      role !== "admin" ||
      !selectedCurriculumId ||
      !paper ||
      paper.state !== "draft"
    ) {
      return;
    }
    const body: PaperPublishRequest = { expected_version: paper.current_version };
    const requestId = ++operationRequestId.current;
    operationInFlight.current = true;
    setBusyAction("publish");
    setOperationError(null);
    setNotice("");
    try {
      const response = await api.POST(
        "/api/v1/admin/curricula/{curriculum_version_id}/papers/{paper_id}/publish",
        {
          body,
          params: {
            path: {
              curriculum_version_id: selectedCurriculumId,
              paper_id: paper.id,
            },
          },
        },
      );
      if (requestId !== operationRequestId.current) return;
      if (response.error !== undefined) {
        setOperationError(apiError(response.error, response.response.status, "command"));
        return;
      }
      const result = response.data as Publication | undefined;
      if (!result || result.paper_id !== paper.id || result.curriculum_version_id !== selectedCurriculumId) {
        setOperationError({
          code: "paper_publication_scope_mismatch",
          message: "The returned publication does not match the selected paper.",
          title: "Publication scope mismatch",
        });
        return;
      }
      setPublication(result);
      setSelectedPublicationVersion(result.version);
      setNotice(
        result.deduplicated
          ? `Matching publication version ${result.version} already existed; the immutable snapshot was reused.`
          : `Publication version ${result.version} created as an immutable verified snapshot.`,
      );
      await loadCurriculumData(selectedCurriculumId, paper.id);
    } catch {
      if (requestId === operationRequestId.current) setOperationError(networkError("command"));
    } finally {
      if (requestId === operationRequestId.current) {
        operationInFlight.current = false;
        setBusyAction("");
      }
    }
  }

  async function archivePaper(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      operationInFlight.current ||
      role !== "admin" ||
      !selectedCurriculumId ||
      !paper ||
      paper.state !== "published"
    ) {
      return;
    }
    const reason = archiveReason.trim();
    if (!reason || reason.length > MAX_ARCHIVE_REASON_CHARACTERS) {
      setFormError(`Enter an archive reason of at most ${MAX_ARCHIVE_REASON_CHARACTERS} characters.`);
      return;
    }
    const body: PaperArchiveRequest = {
      expected_version: paper.current_version,
      reason,
    };
    const requestId = ++operationRequestId.current;
    operationInFlight.current = true;
    setBusyAction("archive");
    setOperationError(null);
    setFormError("");
    setNotice("");
    try {
      const response = await api.POST(
        "/api/v1/admin/curricula/{curriculum_version_id}/papers/{paper_id}/archive",
        {
          body,
          params: {
            path: {
              curriculum_version_id: selectedCurriculumId,
              paper_id: paper.id,
            },
          },
        },
      );
      if (requestId !== operationRequestId.current) return;
      if (response.error !== undefined) {
        setOperationError(apiError(response.error, response.response.status, "command"));
        return;
      }
      const result = response.data as Archive | undefined;
      if (!result || result.paper_id !== paper.id || result.curriculum_version_id !== selectedCurriculumId) {
        setOperationError({
          code: "paper_archive_scope_mismatch",
          message: "The returned archive event does not match the selected paper.",
          title: "Archive scope mismatch",
        });
        return;
      }
      setArchiveReason("");
      setNotice("Paper archived terminally. Existing immutable publication snapshots remain inspectable.");
      await loadCurriculumData(selectedCurriculumId, paper.id);
    } catch {
      if (requestId === operationRequestId.current) setOperationError(networkError("command"));
    } finally {
      if (requestId === operationRequestId.current) {
        operationInFlight.current = false;
        setBusyAction("");
      }
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8 lg:py-10">
      <header className="border-b border-slate-300 pb-7">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div>
            <p className="font-mono text-xs font-semibold tracking-[0.18em] text-slate-500 uppercase">
              P9 / approved assembly / publication
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Paper Studio</h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600 sm:text-base">
              Assemble exact immutable-blueprint slots from approved candidates, inspect every draft and
              verified publication version, and enforce role-separated lifecycle transitions.
            </p>
          </div>
          <Badge className="border-slate-400 bg-white text-slate-900">
            {role === "admin" ? "Administrator · publish controls" : "Reviewer · content review"}
          </Badge>
        </div>
        <div className="mt-5 rounded-xl border border-emerald-300 bg-emerald-50 p-4">
          <p className="font-semibold text-emerald-950">Immutable serving boundary</p>
          <p className="mt-1 text-sm leading-6 text-emerald-900">
            Student serving requires no live LLM or provider call. Published versions are complete,
            immutable snapshots verified by the API before they are returned.
          </p>
        </div>
      </header>

      {notice ? (
        <p className="mt-6 rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-sm text-emerald-950" role="status">
          {notice}
        </p>
      ) : null}
      {formError ? (
        <p className="mt-6 rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-950" role="alert">
          {formError}
        </p>
      ) : null}
      {operationError ? (
        <div className="mt-6">
          <ErrorPanel
            error={operationError}
            onRetry={operationError.reload ? reloadAuthoritativePaper : undefined}
            retryLabel={operationError.reload ? "Reload authoritative paper" : undefined}
          />
        </div>
      ) : null}

      {workspaceLoading ? (
        <p className="mt-8" role="status">Loading Paper Studio workspace…</p>
      ) : workspaceError ? (
        <div className="mt-8">
          <ErrorPanel
            error={workspaceError}
            onRetry={() => void loadWorkspace()}
            retryLabel="Retry Paper Studio workspace"
          />
        </div>
      ) : !curriculumChoices.length ? (
        <section className="mt-8 rounded-2xl border border-dashed border-slate-400 bg-white p-6">
          <h2 className="text-xl font-semibold">No active Grade 5 curriculum</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Activate a Grade 5 exam, medium, and curriculum before assembling papers.
          </p>
          <Link className={`${secondaryButton} mt-4`} href="/admin/curriculum">
            Open Curriculum Studio
          </Link>
        </section>
      ) : (
        <>
          <div className="mt-8">
            <Panel
              title="Paper scope"
              description="Only active Grade 5 curricula are available. Every read and command remains curriculum-scoped."
            >
              <label className={fieldClass}>
                Active Grade 5 curriculum
                <select
                  className={inputClass}
                  onChange={(event) => {
                    setSelectedCurriculumId(event.target.value);
                    setSelectedBlueprintId("");
                    setSelectedPaperId("");
                    setNotice("");
                    setOperationError(null);
                  }}
                  value={selectedCurriculumId}
                >
                  {curriculumChoices.map((curriculum) => (
                    <option key={curriculum.id} value={curriculum.id}>
                      {safeText(curriculum.title)} · {safeText(curriculum.code)}
                    </option>
                  ))}
                </select>
              </label>
            </Panel>
          </div>

          {curriculumLoading ? (
            <p className="mt-6" role="status">Loading immutable blueprints and paper summaries…</p>
          ) : curriculumError ? (
            <div className="mt-6">
              <ErrorPanel
                error={curriculumError}
                onRetry={() => void loadCurriculumData(selectedCurriculumId)}
                retryLabel="Retry curriculum paper data"
              />
            </div>
          ) : (
            <div className="mt-6 grid items-start gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(20rem,0.75fr)]">
              <Panel
                title="Assemble an immutable draft"
                description="Select one immutable blueprint. Every exact slot stays in blueprint order and accepts one unique approved candidate from that same blueprint."
              >
                {!blueprints.length ? (
                  <div className="rounded-xl border border-dashed border-slate-400 p-5">
                    <h3 className="font-semibold">No immutable blueprints</h3>
                    <p className="mt-2 text-sm text-slate-600">
                      Create a deterministic immutable blueprint before assembling a paper.
                    </p>
                    <Link className={`${secondaryButton} mt-4`} href="/admin/blueprints">
                      Open Blueprint Studio
                    </Link>
                  </div>
                ) : (
                  <>
                    <label className={fieldClass}>
                      Immutable paper blueprint
                      <select
                        className={inputClass}
                        onChange={(event) => {
                          setSelectedBlueprintId(event.target.value);
                          setNotice("");
                          setOperationError(null);
                        }}
                        value={selectedBlueprintId}
                      >
                        {blueprints.map((item) => (
                          <option key={item.id} value={item.id}>
                            {safeText(item.title)} · {item.slot_count} slots · {item.total_marks} marks
                          </option>
                        ))}
                      </select>
                    </label>

                    {blueprintLoading ? (
                      <p className="mt-5" role="status">Loading immutable blueprint and approved queue…</p>
                    ) : blueprintError ? (
                      <div className="mt-5">
                        <ErrorPanel
                          error={blueprintError}
                          onRetry={() => void loadBlueprint(selectedCurriculumId, selectedBlueprintId)}
                          retryLabel="Retry blueprint assembly data"
                        />
                      </div>
                    ) : blueprint ? (
                      <Form className="mt-5" onSubmit={createDraft}>
                        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                          <Definition label="Blueprint ID" mono value={safeText(blueprint.blueprint_id)} />
                          <Definition label="Schema version" mono value={safeText(blueprint.schema_version)} />
                          <Definition label="Exact slots" value={blueprint.slot_count} />
                          <Definition label="Total marks" value={blueprint.total_marks} />
                        </dl>
                        <p className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm leading-6 text-slate-700">
                          Approved candidate queue: lightweight summaries only, bounded to the first {LIST_LIMIT}
                          same-blueprint records. Full immutable content is materialized only in verified publication
                          snapshots.
                        </p>

                        {uncoveredSlots.length ? (
                          <section className="mt-4 rounded-xl border border-amber-400 bg-amber-50 p-4">
                            <h3 className="font-semibold text-amber-950">No exact approved coverage</h3>
                            <p className="mt-1 text-sm leading-6 text-amber-900">
                              Generate, validate, and approve exactly one same-blueprint candidate for each uncovered
                              slot before creating a draft. Uncovered: {uncoveredSlots.map((item) => safeText(item)).join(", ")}.
                            </p>
                          </section>
                        ) : null}

                        <label className={`${fieldClass} mt-5`}>
                          Paper title
                          <input
                            className={inputClass}
                            maxLength={MAX_TITLE_CHARACTERS}
                            onChange={(event) => setPaperTitle(event.target.value)}
                            required
                            value={paperTitle}
                          />
                        </label>

                        <fieldset className="mt-5">
                          <legend className="text-base font-semibold">Exact blueprint slots</legend>
                          <p className="mt-1 text-sm text-slate-600">
                            Slot order is immutable. Duplicate candidate identifiers are disabled.
                          </p>
                          <div className="mt-4">
                            <ExactSlotSelections
                              blueprint={blueprint}
                              candidates={approvedCandidates}
                              labelPrefix="Candidate for exact slot"
                              onChange={(slotId, candidateId) =>
                                changeSelection(setAssemblySelections, slotId, candidateId)
                              }
                              selections={assemblySelections}
                              testIds
                            />
                          </div>
                        </fieldset>

                        <p className="mt-4 text-sm leading-6 text-slate-600">
                          The request body contains exactly the blueprint record ID, trimmed title, and candidate IDs
                          in blueprint slot order. A fresh bounded idempotency key is generated for this explicit
                          operation.
                        </p>
                        <Button
                          className={`${primaryButton} mt-4`}
                          isDisabled={
                            busyAction !== "" ||
                            !assemblyComplete ||
                            !paperTitle.trim() ||
                            uncoveredSlots.length > 0
                          }
                          type="submit"
                        >
                          {busyAction === "create" ? "Creating immutable draft…" : "Create immutable draft"}
                        </Button>
                      </Form>
                    ) : null}
                  </>
                )}
              </Panel>

              <Panel
                title="Paper aggregates"
                description={`Bounded curriculum list · first ${LIST_LIMIT} records · draft, published, and archived states`}
              >
                {!papers.length ? (
                  <p className="text-sm text-slate-600">No papers exist in this curriculum yet.</p>
                ) : (
                  <ol className="space-y-3">
                    {papers.map((item) => (
                      <li key={item.id}>
                        <Button
                          aria-label={`Select paper ${item.id}`}
                          className={`w-full rounded-xl border p-4 text-left outline-none transition focus-visible:ring-2 focus-visible:ring-amber-500 ${
                            selectedPaperId === item.id
                              ? "border-slate-950 bg-slate-50"
                              : "border-slate-300 bg-white hover:bg-slate-50"
                          }`}
                          onPress={() => {
                            setSelectedPaperId(item.id);
                            setNotice("");
                            setOperationError(null);
                          }}
                        >
                          <span className="flex flex-wrap items-start justify-between gap-2">
                            <span className="break-words font-semibold">{safeText(item.title)}</span>
                            <StateBadge state={item.state} />
                          </span>
                          <span className="mt-2 block break-all font-mono text-xs text-slate-600">
                            {item.id}
                          </span>
                          <span className="mt-2 block text-xs text-slate-600">
                            Version {item.current_version} · {item.latest_publication_hash ? "Published hash available" : "No publication hash"}
                          </span>
                        </Button>
                      </li>
                    ))}
                  </ol>
                )}
              </Panel>
            </div>
          )}

          {selectedPaperId ? (
            <div className="mt-6 space-y-6">
              {paperLoading ? (
                <p role="status">Loading paper aggregate and immutable versions…</p>
              ) : paperError ? (
                <ErrorPanel
                  error={paperError}
                  onRetry={reloadAuthoritativePaper}
                  retryLabel="Reload selected paper"
                />
              ) : paper ? (
                <>
                  <section
                    aria-label="Selected paper lifecycle"
                    className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6"
                  >
                    <header className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 pb-4">
                      <div>
                        <p className="font-mono text-xs text-slate-500">{paper.id}</p>
                        <h2 className="mt-1 text-xl font-semibold">Selected paper lifecycle</h2>
                      </div>
                      <StateBadge state={paper.state} />
                    </header>
                    <dl className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                      <Definition label="State" value={titleCase(paper.state)} />
                      <Definition label="Current version" value={paper.current_version} />
                      <Definition label="Blueprint record" mono value={paper.paper_blueprint_id} />
                      <Definition label="Blueprint ID" mono value={safeText(paper.blueprint_id)} />
                      <Definition label="Blueprint version" mono value={safeText(paper.blueprint_version)} />
                      <Definition label="Created by" mono value={paper.created_by} />
                      <Definition label="Created at" value={displayDate(paper.created_at)} />
                      <Definition label="Updated at" value={displayDate(paper.updated_at)} />
                    </dl>

                    {role === "admin" && paper.state === "draft" ? (
                      <section className="mt-5 rounded-xl border border-emerald-300 bg-emerald-50 p-4">
                        <h3 className="font-semibold">Administrator publication control</h3>
                        <p className="mt-1 text-sm leading-6 text-emerald-900">
                          Publishing sends only the current expected version. The server reconstructs and verifies the
                          full immutable snapshot from persisted approved candidates.
                        </p>
                        <Button
                          className={`${primaryButton} mt-4`}
                          isDisabled={busyAction !== ""}
                          onPress={() => void publishPaper()}
                        >
                          {busyAction === "publish" ? "Publishing current draft…" : "Publish current draft"}
                        </Button>
                      </section>
                    ) : null}

                    {paper.state === "published" ? (
                      <section className="mt-5 rounded-xl border border-slate-300 p-4">
                        <h3 className="text-lg font-semibold">Revise current publication</h3>
                        <p className="mt-1 text-sm leading-6 text-slate-600">
                          Admins and reviewers with content-review permission may create the next draft only from the
                          current published version. Every exact slot remains required; the title override is optional.
                        </p>
                        {blueprintLoading || !blueprint ? (
                          <p className="mt-4" role="status">Loading exact revision slots…</p>
                        ) : (
                          <Form className="mt-4" onSubmit={revisePaper}>
                            <label className={fieldClass}>
                              Revised title (optional)
                              <input
                                className={inputClass}
                                maxLength={MAX_TITLE_CHARACTERS}
                                onChange={(event) => setRevisionTitle(event.target.value)}
                                placeholder="Leave blank to retain the published title"
                                value={revisionTitle}
                              />
                            </label>
                            <fieldset className="mt-4">
                              <legend className="font-semibold">Exact revision slots</legend>
                              <div className="mt-3">
                                <ExactSlotSelections
                                  blueprint={blueprint}
                                  candidates={approvedCandidates}
                                  labelPrefix="Revision candidate for exact slot"
                                  onChange={(slotId, candidateId) =>
                                    changeSelection(setRevisionSelections, slotId, candidateId)
                                  }
                                  selections={revisionSelections}
                                />
                              </div>
                            </fieldset>
                            <Button
                              className={`${primaryButton} mt-4`}
                              isDisabled={busyAction !== "" || !revisionComplete || uncoveredSlots.length > 0}
                              type="submit"
                            >
                              {busyAction === "revise" ? "Creating revision draft…" : "Create revision draft"}
                            </Button>
                          </Form>
                        )}
                      </section>
                    ) : null}

                    {role === "admin" && paper.state === "published" ? (
                      <Form
                        className="mt-5 rounded-xl border-2 border-red-400 bg-red-50 p-4"
                        onSubmit={archivePaper}
                      >
                        <h3 className="font-semibold text-red-950">Terminal archive control</h3>
                        <p className="mt-1 text-sm leading-6 text-red-900">
                          Archiving is terminal. The paper can never return to draft or published state, although every
                          immutable publication snapshot remains inspectable.
                        </p>
                        <label className={`${fieldClass} mt-4`}>
                          Archive reason (required)
                          <textarea
                            className={inputClass}
                            maxLength={MAX_ARCHIVE_REASON_CHARACTERS}
                            onChange={(event) => setArchiveReason(event.target.value)}
                            required
                            rows={3}
                            value={archiveReason}
                          />
                        </label>
                        <Button
                          className={`${dangerButton} mt-4`}
                          isDisabled={busyAction !== "" || !archiveReason.trim()}
                          type="submit"
                        >
                          {busyAction === "archive" ? "Archiving paper…" : "Archive paper terminally"}
                        </Button>
                      </Form>
                    ) : null}

                    {paper.state === "archived" ? (
                      <section className="mt-5 rounded-xl border border-slate-400 bg-slate-100 p-4">
                        <h3 className="font-semibold">Archived terminal state</h3>
                        {archive ? (
                          <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                            <Definition label="Archive version" value={archive.version} />
                            <Definition label="Reason" value={safeText(archive.reason)} />
                            <Definition label="Archived by" mono value={archive.archived_by} />
                            <Definition label="Archived at" value={displayDate(archive.archived_at)} />
                            <Definition label="Publication hash" mono value={archive.content_hash} />
                          </dl>
                        ) : (
                          <p className="mt-2 text-sm">Loading terminal archive event…</p>
                        )}
                      </section>
                    ) : null}
                  </section>

                  <section
                    aria-label="Immutable draft versions"
                    className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6"
                  >
                    <header className="border-b border-slate-200 pb-4">
                      <h2 className="text-xl font-semibold">Immutable draft versions</h2>
                      <p className="mt-1 text-sm text-slate-600">
                        Candidate references are frozen with their exact version and content revision.
                      </p>
                    </header>
                    {!drafts.length ? (
                      <p className="mt-5 text-sm text-slate-600">No immutable draft versions were returned.</p>
                    ) : (
                      <ol className="mt-5 space-y-4">
                        {[...drafts]
                          .sort((left, right) => right.version - left.version)
                          .map((item) => (
                            <li className="rounded-xl border border-slate-300 p-4" key={item.version}>
                              <div className="flex flex-wrap justify-between gap-3">
                                <h3 className="font-semibold">Draft version {item.version}</h3>
                                <span className="text-xs text-slate-600">{displayDate(item.created_at)}</span>
                              </div>
                              <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                                <Definition label="Title" value={safeText(item.title)} />
                                <Definition
                                  label="Supersedes hash"
                                  mono
                                  value={safeText(item.supersedes_content_hash, "None")}
                                />
                                <Definition label="Created by" mono value={item.created_by} />
                                <Definition label="Candidate count" value={item.candidates.length} />
                              </dl>
                              <ol className="mt-4 grid gap-3 lg:grid-cols-2">
                                {item.candidates.slice(0, MAX_DISPLAY_ITEMS).map((reference) => (
                                  <li className="rounded-lg border border-slate-200 bg-slate-50 p-3" key={`${reference.ordinal}-${reference.candidate_id}`}>
                                    <p className="font-semibold">Slot {reference.ordinal}</p>
                                    <dl className="mt-2 grid gap-2 sm:grid-cols-2">
                                      <Definition label="Blueprint slot" mono value={safeText(reference.blueprint_slot_id)} />
                                      <Definition label="Candidate ID" mono value={reference.candidate_id} />
                                      <Definition label="Candidate version" value={reference.candidate_version} />
                                      <Definition label="Content revision" value={reference.candidate_revision} />
                                    </dl>
                                  </li>
                                ))}
                              </ol>
                            </li>
                          ))}
                      </ol>
                    )}
                  </section>

                  <section className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6">
                    <header className="border-b border-slate-200 pb-4">
                      <h2 className="text-xl font-semibold">Immutable publication versions</h2>
                      <p className="mt-1 text-sm text-slate-600">
                        Select metadata to fetch and verify the complete materialized snapshot.
                      </p>
                    </header>
                    {!publicationVersions.length ? (
                      <p className="mt-5 text-sm text-slate-600">This paper has not been published yet.</p>
                    ) : (
                      <ol className="mt-5 grid gap-3 lg:grid-cols-2">
                        {[...publicationVersions]
                          .sort((left, right) => right.version - left.version)
                          .map((item) => (
                            <li key={item.version}>
                              <Button
                                aria-label={`Inspect publication version ${item.version}`}
                                className={`w-full rounded-xl border p-4 text-left outline-none focus-visible:ring-2 focus-visible:ring-amber-500 ${
                                  selectedPublicationVersion === item.version
                                    ? "border-slate-950 bg-slate-50"
                                    : "border-slate-300 bg-white"
                                }`}
                                onPress={() => setSelectedPublicationVersion(item.version)}
                              >
                                <span className="block font-semibold">Publication version {item.version}</span>
                                <span className="mt-2 block break-all font-mono text-xs">{item.content_hash}</span>
                                <span className="mt-2 block text-xs text-slate-600">
                                  {displayDate(item.published_at)} · {item.published_by}
                                </span>
                              </Button>
                            </li>
                          ))}
                      </ol>
                    )}
                  </section>

                  {selectedPublicationVersion !== null ? (
                    publicationLoading ? (
                      <p role="status">Loading and verifying full immutable publication snapshot…</p>
                    ) : publicationError ? (
                      <ErrorPanel
                        error={publicationError}
                        onRetry={() =>
                          void loadPublication(
                            selectedCurriculumId,
                            selectedPaperId,
                            selectedPublicationVersion,
                          )
                        }
                        retryLabel="Retry verified publication snapshot"
                      />
                    ) : publication ? (
                      <PublicationSnapshot publication={publication} />
                    ) : null
                  ) : null}
                </>
              ) : null}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
