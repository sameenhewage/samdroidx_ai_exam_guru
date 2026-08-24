"use client";

import {
  createApiClient,
  type ApiClient,
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
type Blueprint = components["schemas"]["PaperBlueprintResponse"];
type BlueprintSummary = components["schemas"]["PaperBlueprintSummaryResponse"];
type BlueprintSlot = components["schemas"]["BlueprintSlotResponse"];
type TaxonomyTarget = components["schemas"]["TaxonomyTargetResponse"];
type KnowledgeChunk = components["schemas"]["KnowledgeChunkResponse"];
type HistoricalQuestion = components["schemas"]["HistoricalQuestionResponse"];
type Classification = components["schemas"]["KnowledgeClassificationResponse"];
type GenerationRun = components["schemas"]["GenerationRunResponse"];
type GenerationRunSummary = components["schemas"]["GenerationRunSummaryResponse"];
type GenerationAttempt = components["schemas"]["GenerationAttemptResponse"];
type GenerationJob = components["schemas"]["GenerationJobResponse"];
type GenerationRequest = components["schemas"]["GenerationRunCreateRequest"];
type Role = "admin" | "reviewer";
type ContextKind = "knowledge_chunk" | "historical_question";

type ApiOutcome = { error?: unknown; response: Response };
type UiError = { code: string; message: string; title: string };
type CurriculumChoice = { curriculum: Curriculum; exam: Exam; medium: Medium };
type PollTarget = {
  curriculumId: string;
  jobId: string | null;
  runId: string;
  token: string;
};
type Discovery<RecordType> = {
  capped: boolean;
  failure?: ApiOutcome;
  records: RecordType[];
};
type JsonObject = Record<string, unknown>;

const LIST_LIMIT = 100;
const MAX_DISCOVERY_RECORDS = 5_000;
const MAX_CONTEXT_REFERENCES = 16;
const POLL_DELAYS_MS = [250, 500, 1_000, 2_000, 4_000] as const;
const POLL_MAX_DURATION_MS = 120_000;

const fieldClass = "grid gap-1.5 text-sm font-semibold text-slate-700";
const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 outline-none transition focus:border-amber-500 focus:ring-2 focus:ring-amber-200 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none transition hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-400 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function stringValue(value: unknown, fallback = "Not recorded"): string {
  return typeof value === "string" && value.length ? value : fallback;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function detailObject(error: unknown): JsonObject | null {
  const object = asObject(error);
  return object ? asObject(object.detail) : null;
}

function detailCode(error: unknown): string {
  const detail = detailObject(error);
  return detail && typeof detail.code === "string" ? detail.code : "request_failed";
}

function firstFailure(outcomes: readonly ApiOutcome[]): ApiOutcome | undefined {
  return outcomes.find((outcome) => outcome.error !== undefined);
}

function apiError(
  error: unknown,
  status: number,
  surface: "workspace" | "selection" | "runs" | "detail" | "create" | "retry" | "poll",
): UiError {
  const code = detailCode(error);
  if (status === 401) {
    return {
      code: "authentication_required",
      message: "Your admin session has expired. Sign in again before retrying.",
      title: "Authentication required",
    };
  }
  if (status === 403) {
    return {
      code: "permission_denied",
      message:
        surface === "create" || surface === "retry"
          ? "This account cannot create or retry generation runs. Sign in as an administrator or ask for generation:run access."
          : "This account cannot inspect generation records. Ask an administrator to verify generation:read access.",
      title:
        surface === "workspace"
          ? "Generation workspace permission required"
          : surface === "create" || surface === "retry"
            ? "Generation permission required"
            : "Generation read permission required",
    };
  }
  if (status === 503) {
    if (code === "generation_runtime_unavailable") {
      return {
        code,
        message:
          "No server-side generation configuration is available. No provider, model, prompt, pricing, or credential can be supplied by this client. Retry after an administrator configures the service.",
        title: "Generation configuration unavailable",
      };
    }
    if (code === "generation_queue_unavailable") {
      return {
        code,
        message:
          "The durable generation queue is unavailable. The request was not accepted for execution; retry this explicit operation after the queue recovers.",
        title: "Generation queue unavailable",
      };
    }
    return {
      code,
      message: "The generation service is temporarily unavailable. Retry without changing the intended immutable selection.",
      title: "Generation service unavailable",
    };
  }
  if (status === 409) {
    const conflicts: Record<string, UiError> = {
      generation_blueprint_snapshot_invalid: {
        code,
        message:
          "The persisted blueprint scope or immutable snapshot no longer validates. Reload the authoritative blueprint before creating another run.",
        title: "Immutable blueprint conflict",
      },
      generation_curriculum_inactive: {
        code,
        message:
          "The selected curriculum, exam, or medium is no longer active. Reload and select an active Grade 5 scope.",
        title: "Curriculum scope changed",
      },
      generation_idempotency_conflict: {
        code,
        message:
          "The generated operation identity conflicts with a different immutable request. Reload persisted runs and start a new explicit operation.",
        title: "Idempotency conflict",
      },
      generation_retry_state_invalid: {
        code,
        message:
          "Only a failed generation run can be retried. Reload the run because its durable state has changed.",
        title: "Run cannot be retried",
      },
    };
    return (
      conflicts[code] ?? {
        code,
        message:
          "A durable generation state conflict occurred. Reload the immutable run before retrying.",
        title: "Generation state conflict",
      }
    );
  }
  if (status === 404) {
    const titles: Record<string, string> = {
      generation_blueprint_not_found: "Immutable blueprint not found",
      generation_curriculum_not_found: "Curriculum not found",
      generation_job_not_found: "Generation job not found",
      generation_run_not_found: "Generation run not found",
    };
    return {
      code,
      message:
        "The selected durable resource is no longer available in this curriculum. Reload the workspace and choose a persisted record.",
      title: titles[code] ?? "Generation resource not found",
    };
  }
  if (status === 422) {
    const failures: Record<string, UiError> = {
      generation_context_cross_curriculum: {
        code,
        message:
          "A context reference belongs to another curriculum. Reload same-curriculum reviewed references before creating a run.",
        title: "Cross-curriculum context rejected",
      },
      generation_context_limit_exceeded: {
        code,
        message:
          "The selected context exceeds server bounds. Select between 1 and 16 smaller reviewed references.",
        title: "Context limit exceeded",
      },
      generation_context_not_found: {
        code,
        message:
          "A selected context record no longer exists. Reload reviewed references and select the exact records again.",
        title: "Context reference not found",
      },
      generation_context_not_reviewed: {
        code,
        message:
          "A selected record is no longer reviewed. Reload and choose only final reviewed context.",
        title: "Context review state changed",
      },
      generation_context_source_untrusted: {
        code,
        message:
          "A selected record no longer has trusted source-block provenance. Return to Knowledge Studio and complete source review.",
        title: "Context source is not trusted",
      },
      generation_context_taxonomy_mismatch: {
        code,
        message:
          "A selected context classification does not match the immutable exact slot taxonomy. Reload and use only enabled references.",
        title: "Context does not match slot",
      },
      generation_slot_not_found: {
        code,
        message:
          "The exact slot is absent from the immutable blueprint snapshot. Reload the blueprint and choose a persisted slot.",
        title: "Blueprint slot not found",
      },
    };
    return (
      failures[code] ?? {
        code,
        message:
          "The authoritative server rejected this bounded generation selection. Reload the scope, blueprint, slot, and reviewed context before retrying.",
        title: "Generation selection rejected",
      }
    );
  }
  return {
    code,
    message:
      "The generation request could not be completed. Retry or contact an administrator if the failure persists.",
    title:
      surface === "workspace"
        ? "Generation workspace unavailable"
        : surface === "selection"
          ? "Generation selection unavailable"
          : surface === "runs"
            ? "Generation run list unavailable"
            : surface === "detail" || surface === "poll"
              ? "Generation run unavailable"
              : surface === "retry"
                ? "Generation retry failed"
                : "Generation request failed",
  };
}

function networkError(
  surface: "workspace" | "selection" | "runs" | "detail" | "create" | "retry" | "poll",
): UiError {
  return {
    code: "network_error",
    message:
      "The API could not be reached. Check the connection and retry the same explicit operation.",
    title:
      surface === "workspace"
        ? "Generation workspace unavailable"
        : surface === "selection"
          ? "Generation selection unavailable"
          : surface === "runs"
            ? "Generation run list unavailable"
            : surface === "detail" || surface === "poll"
              ? "Generation polling paused"
              : surface === "retry"
                ? "Generation retry connection failed"
                : "Generation connection failed",
  };
}

function displayEnum(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function displayDate(value: string | null): string {
  if (!value) return "Not yet";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("en", {
        dateStyle: "medium",
        timeStyle: "medium",
        timeZone: "UTC",
      }).format(date);
}

function displayCost(value: number | null): string {
  return value === null ? "Accounting unavailable" : `${value.toLocaleString("en")} microusd`;
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? "Not recorded";
  } catch {
    return "Unable to display persisted structured data";
  }
}

function generatedIdempotencyKey(prefix: "generation" | "generation-retry"): string {
  const cryptoObject = globalThis.crypto;
  let random: string;
  if (typeof cryptoObject?.randomUUID === "function") {
    random = cryptoObject.randomUUID();
  } else {
    const bytes = new Uint8Array(16);
    cryptoObject.getRandomValues(bytes);
    random = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  }
  const key = `${prefix}-${random}`;
  if (key.length > 128 || /\s/.test(key)) {
    throw new Error("generated idempotency key is outside the client boundary");
  }
  return key;
}

function summaryFromRun(value: GenerationRun): GenerationRunSummary {
  return {
    attempt_count: value.attempt_count,
    completed_at: value.completed_at,
    cost_microusd: value.cost_microusd,
    created_at: value.created_at,
    created_by: value.created_by,
    curriculum_version_id: value.curriculum_version_id,
    disposition: value.disposition,
    failure_code: value.failure_code,
    id: value.id,
    latency_ms: value.latency_ms,
    model: value.model,
    paper_blueprint_id: value.paper_blueprint_id,
    prompt_version: value.prompt_version,
    provider: value.provider,
    request_fingerprint: value.request_fingerprint,
    retry_of_run_id: value.retry_of_run_id,
    slot_id: value.slot_id,
    started_at: value.started_at,
    status: value.status,
    total_tokens: value.total_tokens,
    version: value.version,
  };
}

function taxonomyMatches(classification: Classification, target: TaxonomyTarget): boolean {
  return (
    classification.competency_id === target.competency_id &&
    (target.skill_id === null || classification.skill_id === target.skill_id) &&
    (target.sub_skill_id === null || classification.sub_skill_id === target.sub_skill_id) &&
    (target.learning_concept_id === null ||
      classification.learning_concept_id === target.learning_concept_id)
  );
}

async function discoverReviewedChunks(
  api: ApiClient,
  curriculumId: string,
  signal?: AbortSignal,
): Promise<Discovery<KnowledgeChunk>> {
  const records: KnowledgeChunk[] = [];
  for (let offset = 0; offset < MAX_DISCOVERY_RECORDS; offset += LIST_LIMIT) {
    const response = await api.GET(
      "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/chunks",
      {
        params: {
          path: { curriculum_version_id: curriculumId },
          query: { limit: LIST_LIMIT, offset, review_state: "reviewed" },
        },
        signal,
      },
    );
    if (response.error !== undefined) return { capped: false, failure: response, records };
    const batch = response.data ?? [];
    records.push(...batch);
    if (batch.length < LIST_LIMIT) return { capped: false, records };
  }
  return { capped: true, records };
}

async function discoverReviewedQuestions(
  api: ApiClient,
  curriculumId: string,
  signal?: AbortSignal,
): Promise<Discovery<HistoricalQuestion>> {
  const records: HistoricalQuestion[] = [];
  for (let offset = 0; offset < MAX_DISCOVERY_RECORDS; offset += LIST_LIMIT) {
    const response = await api.GET(
      "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/questions",
      {
        params: {
          path: { curriculum_version_id: curriculumId },
          query: { limit: LIST_LIMIT, offset, review_state: "reviewed" },
        },
        signal,
      },
    );
    if (response.error !== undefined) return { capped: false, failure: response, records };
    const batch = response.data ?? [];
    records.push(...batch);
    if (batch.length < LIST_LIMIT) return { capped: false, records };
  }
  return { capped: true, records };
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
      <h3 className="font-semibold">{error.title}</h3>
      <p className="mt-1 text-sm leading-6 text-red-900">{error.message}</p>
      <p className="mt-2 font-mono text-xs text-red-800">Code: {error.code}</p>
      {onRetry && retryLabel ? (
        <Button className={`${secondaryButton} mt-3 border-red-300`} onPress={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
    </section>
  );
}

function StatusBadge({ status }: { status: GenerationRun["status"] }) {
  const className =
    status === "succeeded"
      ? "border-emerald-300 bg-emerald-50 text-emerald-900"
      : status === "failed"
        ? "border-red-300 bg-red-50 text-red-900"
        : status === "running"
          ? "border-blue-300 bg-blue-50 text-blue-900"
          : "border-amber-300 bg-amber-50 text-amber-950";
  return <Badge className={className}>{displayEnum(status)}</Badge>;
}

function Definition({ label, value, mono = false }: { label: string; mono?: boolean; value: ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <dt className="text-xs font-semibold text-slate-500">{label}</dt>
      <dd className={`mt-1 break-words text-sm text-slate-950 ${mono ? "font-mono text-xs" : ""}`}>
        {value}
      </dd>
    </div>
  );
}

function TaxonomyValues({ value }: { value: JsonObject | TaxonomyTarget | Classification }) {
  return (
    <dl className="grid gap-2 sm:grid-cols-2">
      <Definition label="Competency" mono value={stringValue(value.competency_id, "None")} />
      <Definition label="Skill" mono value={stringValue(value.skill_id, "None")} />
      <Definition label="Sub-skill" mono value={stringValue(value.sub_skill_id, "None")} />
      <Definition
        label="Learning concept"
        mono
        value={stringValue(value.learning_concept_id, "None")}
      />
    </dl>
  );
}

function BlueprintSelection({ blueprint, slot: selectedSlot }: { blueprint: Blueprint; slot: BlueprintSlot }) {
  return (
    <section className="mt-5 rounded-xl border border-slate-300 bg-slate-50 p-4">
      <h3 className="font-semibold">Immutable blueprint snapshot</h3>
      <p className="mt-1 text-sm text-slate-600">
        Persisted version and exact slot data are read-only. Generation resolves the authoritative snapshot again on the server.
      </p>
      <dl className="mt-4 grid gap-3 sm:grid-cols-2">
        <Definition label="Paper" value={`${blueprint.specification.paper_code} — ${blueprint.specification.title}`} />
        <Definition label="Blueprint record ID" mono value={blueprint.id} />
        <Definition label="Blueprint version" mono value={blueprint.blueprint_id} />
        <Definition label="Schema version" mono value={blueprint.schema_version} />
        <Definition label="Exact slot" mono value={selectedSlot.slot_id} />
        <Definition label="Section" value={`${selectedSlot.section_id} — ${selectedSlot.section_title}`} />
        <Definition label="Question type" value={displayEnum(selectedSlot.question_type)} />
        <Definition label="Archetype" value={selectedSlot.archetype} />
        <Definition label="Difficulty" value={displayEnum(selectedSlot.difficulty)} />
        <Definition label="Marks" value={selectedSlot.marks} />
      </dl>
      <div className="mt-4">
        <h4 className="text-sm font-semibold">Exact slot taxonomy</h4>
        <div className="mt-2">
          <TaxonomyValues value={selectedSlot.taxonomy_target} />
        </div>
      </div>
    </section>
  );
}

function ReferenceCard({
  checked,
  disabled,
  id,
  kind,
  matches,
  onChange,
  record,
}: {
  checked: boolean;
  disabled: boolean;
  id: string;
  kind: ContextKind;
  matches: boolean;
  onChange: (checked: boolean) => void;
  record: KnowledgeChunk | HistoricalQuestion;
}) {
  const isChunk = kind === "knowledge_chunk";
  const title = isChunk
    ? (record as KnowledgeChunk).educational_boundary
    : `${(record as HistoricalQuestion).paper_code} / Question ${(record as HistoricalQuestion).question_number}`;
  const provenance = record.provenance;
  return (
    <li className={`rounded-xl border p-4 ${matches ? "border-slate-300 bg-white" : "border-slate-200 bg-slate-100"}`}>
      <label className="flex items-start gap-3">
        <input
          aria-label={`Select ${isChunk ? "knowledge chunk" : "historical question"} ${id}`}
          checked={checked}
          className="mt-1 h-4 w-4 rounded border-slate-400 text-slate-950 outline-none focus-visible:ring-2 focus-visible:ring-amber-500"
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
          type="checkbox"
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-semibold text-slate-950">{title}</span>
            <span className="text-xs font-semibold text-slate-600">
              {matches ? "Matches exact slot" : "Taxonomy does not match the exact slot"}
            </span>
          </span>
          <span className="mt-2 block whitespace-pre-wrap text-sm leading-6 text-slate-700">
            {record.text}
          </span>
          <span className="mt-3 block break-all font-mono text-xs text-slate-500">ID {id}</span>
          <span className="mt-1 block break-all font-mono text-xs text-slate-500">
            Source {provenance.source_document_id} · page {provenance.page_number} · block {provenance.source_block_id ?? "none"}
          </span>
        </span>
      </label>
    </li>
  );
}

function RunList({
  loading,
  onRefresh,
  onSelect,
  runs,
  selectedRunId,
}: {
  loading: boolean;
  onRefresh: () => void;
  onSelect: (runId: string) => void;
  runs: GenerationRunSummary[];
  selectedRunId: string;
}) {
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-600">{runs.length} persisted run{runs.length === 1 ? "" : "s"}</p>
        <Button className={secondaryButton} isDisabled={loading} onPress={onRefresh}>
          {loading ? "Refreshing runs…" : "Refresh runs"}
        </Button>
      </div>
      {runs.length ? (
        <ol className="mt-4 grid gap-3">
          {runs.map((run) => {
            const selected = selectedRunId === run.id;
            return (
              <li key={run.id}>
                <button
                  aria-pressed={selected}
                  className={`w-full rounded-xl border p-4 text-left outline-none transition focus-visible:ring-2 focus-visible:ring-amber-500 ${
                    selected
                      ? "border-slate-950 bg-slate-950 text-white"
                      : "border-slate-300 bg-white hover:border-slate-500"
                  }`}
                  onClick={() => onSelect(run.id)}
                  type="button"
                >
                  <span className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold">{run.slot_id}</span>
                    <StatusBadge status={run.status} />
                  </span>
                  <span className={`mt-2 block font-mono text-xs ${selected ? "text-slate-300" : "text-slate-500"}`}>
                    {run.id}
                  </span>
                  <span className={`mt-2 block text-xs ${selected ? "text-slate-300" : "text-slate-600"}`}>
                    Version {run.version} · {run.attempt_count} attempt{run.attempt_count === 1 ? "" : "s"} · {run.total_tokens} tokens · {displayCost(run.cost_microusd)}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      ) : (
        <div className="mt-4 rounded-xl border border-dashed border-slate-300 p-5">
          <h3 className="font-semibold">No generation runs yet</h3>
          <p className="mt-1 text-sm text-slate-600">
            An administrator can create the first run after selecting one immutable slot and reviewed context.
          </p>
        </div>
      )}
    </div>
  );
}

function CandidateView({ candidate }: { candidate: JsonObject }) {
  const answer = asObject(candidate.answer) ?? {};
  const marking = asObject(candidate.marking) ?? {};
  const options = Array.isArray(candidate.options)
    ? candidate.options.map(asObject).filter((value): value is JsonObject => value !== null)
    : [];
  const criteria = Array.isArray(marking.criteria)
    ? marking.criteria.map(asObject).filter((value): value is JsonObject => value !== null)
    : [];
  const accepted = Array.isArray(answer.accepted_responses)
    ? answer.accepted_responses.filter((value): value is string => typeof value === "string")
    : [];
  return (
    <div>
      <dl className="grid gap-3 sm:grid-cols-2">
        <Definition label="Question type" value={displayEnum(stringValue(candidate.question_type))} />
        <Definition label="Total marks" value={stringValue(marking.total_marks, String(numberValue(marking.total_marks) ?? "Not recorded"))} />
      </dl>
      <section className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
        <h3 className="text-sm font-semibold">Question</h3>
        <p className="mt-2 whitespace-pre-wrap text-base leading-7">{stringValue(candidate.stem)}</p>
        {options.length ? (
          <ol className="mt-3 grid gap-2">
            {options.map((option, index) => (
              <li className="rounded-lg border border-slate-200 bg-white p-3 text-sm" key={`${stringValue(option.option_id, String(index))}-${index}`}>
                <span className="font-semibold">{stringValue(option.option_id, String(index + 1))}.</span>{" "}
                {stringValue(option.text)}
              </li>
            ))}
          </ol>
        ) : null}
      </section>
      <section className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
        <h3 className="text-sm font-semibold">Proposed answer</h3>
        {typeof answer.correct_option_id === "string" ? (
          <p className="mt-2 text-sm"><span className="font-semibold">Correct option:</span> {answer.correct_option_id}</p>
        ) : null}
        {accepted.length ? (
          <div className="mt-2 text-sm">
            <p className="font-semibold">Accepted responses</p>
            <ul className="mt-1 list-disc pl-5">{accepted.map((value) => <li key={value}>{value}</li>)}</ul>
          </div>
        ) : null}
        <p className="mt-2 whitespace-pre-wrap text-sm leading-6">{stringValue(answer.explanation)}</p>
      </section>
      <section className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
        <h3 className="text-sm font-semibold">Proposed marking scheme</h3>
        {criteria.length ? (
          <ol className="mt-2 grid gap-2">
            {criteria.map((criterion, index) => (
              <li className="rounded-lg border border-slate-200 bg-white p-3 text-sm" key={`${stringValue(criterion.criterion_id, String(index))}-${index}`}>
                <p className="font-semibold">{stringValue(criterion.criterion_id, `Criterion ${index + 1}`)} · {numberValue(criterion.marks) ?? "Unknown"} marks</p>
                <p className="mt-1 whitespace-pre-wrap leading-6">{stringValue(criterion.description)}</p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-2 text-sm text-slate-600">No marking criteria were recorded.</p>
        )}
      </section>
    </div>
  );
}

function ContextSnapshot({ context }: { context: JsonObject[] }) {
  return context.length ? (
    <ol className="grid gap-4">
      {context.map((item, index) => {
        const provenance = asObject(item.provenance) ?? {};
        const taxonomy = asObject(item.taxonomy) ?? {};
        return (
          <li className="rounded-xl border border-amber-300 bg-amber-50 p-4" key={`${stringValue(item.context_id, String(index))}-${index}`}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="font-semibold">Context {index + 1}</p>
                <p className="mt-1 break-all font-mono text-xs text-slate-600">{stringValue(item.context_id)}</p>
              </div>
              <Badge className="border-amber-400 bg-white text-amber-950">Untrusted source data</Badge>
            </div>
            <p className="mt-4 whitespace-pre-wrap rounded-lg border border-amber-200 bg-white p-4 text-sm leading-6 text-slate-950">
              {stringValue(item.text)}
            </p>
            <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Definition label="Record kind" value={displayEnum(stringValue(item.record_kind))} />
              <Definition label="Record ID" mono value={stringValue(item.record_id)} />
              <Definition label="Record version" value={numberValue(item.record_version) ?? "Not recorded"} />
              <Definition label="Source document" mono value={stringValue(provenance.source_document_id)} />
              <Definition label="source_version" mono value={stringValue(provenance.source_version)} />
              <Definition label="Page" value={numberValue(provenance.page_number) ?? "Not recorded"} />
              <Definition label="Chunk" mono value={stringValue(provenance.chunk_id)} />
              <Definition label="Source block" mono value={stringValue(provenance.source_block_id)} />
              <Definition label="Trust label" mono value={stringValue(item.trust)} />
            </dl>
            <div className="mt-4">
              <h3 className="text-sm font-semibold">Persisted taxonomy</h3>
              <div className="mt-2"><TaxonomyValues value={taxonomy} /></div>
            </div>
          </li>
        );
      })}
    </ol>
  ) : (
    <p className="rounded-xl border border-dashed border-slate-300 p-4 text-sm text-slate-600">
      No persisted context snapshot is available.
    </p>
  );
}

function AttemptsView({ attempts: values }: { attempts: GenerationAttempt[] }) {
  return values.length ? (
    <ol className="grid gap-4">
      {values.map((attempt) => (
        <li className="rounded-xl border border-slate-300 p-4" key={attempt.id}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="font-semibold">Attempt {attempt.attempt_number}</h3>
            <Badge className={attempt.status === "succeeded" ? "border-emerald-300 bg-emerald-50 text-emerald-900" : "border-red-300 bg-red-50 text-red-900"}>
              {displayEnum(attempt.status)}
            </Badge>
          </div>
          <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Definition label="Attempt ID" mono value={attempt.id} />
            <Definition label="Generation run ID" mono value={attempt.generation_run_id} />
            <Definition label="Retry of attempt" mono value={attempt.retry_of_attempt_id ?? "None"} />
            <Definition label="Provider idempotency key" mono value={attempt.provider_idempotency_key} />
            <Definition label="Sanitized failure code" mono value={attempt.failure_code ?? "None"} />
            <Definition label="Retry after" value={attempt.retry_after_ms === null ? "Not requested" : `${attempt.retry_after_ms} ms`} />
            <Definition label="Accounting known" value={attempt.accounting_known ? "Yes" : "No"} />
            <Definition label="Input tokens" value={attempt.input_tokens ?? "Unknown"} />
            <Definition label="Output tokens" value={attempt.output_tokens ?? "Unknown"} />
            <Definition label="Total tokens" value={attempt.total_tokens ?? "Unknown"} />
            <Definition label="Cost" value={displayCost(attempt.cost_microusd)} />
            <Definition label="Latency" value={`${attempt.latency_ms.toLocaleString("en")} ms`} />
            <Definition label="Started" value={displayDate(attempt.started_at)} />
            <Definition label="Completed" value={displayDate(attempt.completed_at)} />
            <Definition label="Disposition" mono value={attempt.disposition ?? "None"} />
          </dl>
          {attempt.candidate ? (
            <details className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3">
              <summary className="cursor-pointer text-sm font-semibold">Attempt candidate snapshot</summary>
              <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{safeJson(attempt.candidate)}</pre>
            </details>
          ) : null}
        </li>
      ))}
    </ol>
  ) : (
    <p className="rounded-xl border border-dashed border-slate-300 p-4 text-sm text-slate-600">
      No provider attempts have completed. Pending and newly running jobs can remain empty briefly.
    </p>
  );
}

function RunInspection({
  attempts: values,
  job,
  onRetry,
  polling,
  retrying,
  role,
  run,
}: {
  attempts: GenerationAttempt[];
  job: GenerationJob | null;
  onRetry: () => void;
  polling: boolean;
  retrying: boolean;
  role: Role;
  run: GenerationRun;
}) {
  const candidateObject = asObject(run.candidate);
  return (
    <article className="mt-8 grid gap-6">
      {run.disposition === "requires_validation" ? (
        <section className="rounded-2xl border-2 border-amber-500 bg-amber-50 p-5 text-amber-950 shadow-sm" role="status">
          <p className="font-mono text-sm font-bold tracking-[0.16em]">REQUIRES VALIDATION</p>
          <h2 className="mt-2 text-xl font-semibold">Generated content is not approved or publishable</h2>
          <p className="mt-2 max-w-4xl text-sm leading-6">
            This candidate remains untrusted model output. It must pass the separate validation and human review gates. No publish action is available in Generation Studio.
          </p>
        </section>
      ) : null}

      <Panel ariaLabel="Generation run overview" title="Generation run overview" description="Durable run identity, lifecycle state, accounting totals, and retry lineage.">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <StatusBadge status={run.status} />
            <span className="text-sm font-semibold">Version {run.version}</span>
            {polling ? <span className="text-sm text-blue-700">Polling durable state…</span> : null}
          </div>
          {role === "admin" && run.status === "failed" ? (
            <Button className={primaryButton} isDisabled={retrying} onPress={onRetry}>
              {retrying ? "Queuing failed-run retry…" : "Retry failed run"}
            </Button>
          ) : null}
        </div>
        <dl className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Definition label="Run ID" mono value={run.id} />
          <Definition label="Status" value={displayEnum(run.status)} />
          <Definition label="Version" value={run.version} />
          <Definition label="Request fingerprint" mono value={run.request_fingerprint} />
          <Definition label="Retry of run" mono value={run.retry_of_run_id ?? "None"} />
          <Definition label="Failure code" mono value={run.failure_code ?? "None"} />
          <Definition label="Disposition" mono value={run.disposition ?? "None"} />
          <Definition label="Created by" mono value={run.created_by} />
          <Definition label="Created" value={displayDate(run.created_at)} />
          <Definition label="Started" value={displayDate(run.started_at)} />
          <Definition label="Completed" value={displayDate(run.completed_at)} />
          <Definition label="Attempts" value={run.attempt_count} />
          <Definition label="Input tokens" value={run.input_tokens} />
          <Definition label="Output tokens" value={run.output_tokens} />
          <Definition label="Total tokens" value={run.total_tokens} />
          <Definition label="Cost" value={displayCost(run.cost_microusd)} />
          <Definition label="Latency" value={`${run.latency_ms.toLocaleString("en")} ms`} />
          <Definition label="Job status" value={job ? displayEnum(job.status) : "Not loaded for this selection"} />
          <Definition label="Job version" value={job?.version ?? "Not loaded"} />
          <Definition label="Job failure code" mono value={job?.failure_code ?? "None"} />
        </dl>
        {run.status === "pending" || run.status === "running" ? (
          <p className="mt-4 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-950" role="status">
            {run.status === "pending"
              ? "The durable run is pending queue claim. It has no candidate or provider attempt yet."
              : "The worker is running this durable request. Candidate output remains unavailable until the run succeeds."}
          </p>
        ) : null}
        {run.status === "failed" ? (
          <p className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-950" role="status">
            The run failed with sanitized code {run.failure_code}. It produced no publishable candidate. An administrator must explicitly start any retry.
          </p>
        ) : null}
      </Panel>

      <Panel ariaLabel="Immutable blueprint and slot snapshot" title="Immutable blueprint and slot snapshot" description="The exact slot was snapshotted when the run was created; this inspection cannot edit it.">
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Definition label="Paper blueprint record" mono value={run.paper_blueprint_id} />
          <Definition label="Blueprint ID" mono value={run.blueprint_id} />
          <Definition label="Blueprint version" mono value={run.blueprint_version} />
          <Definition label="Slot ID" mono value={run.slot_id} />
        </dl>
        <pre className="mt-4 max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-xl border border-slate-200 bg-slate-950 p-4 font-mono text-xs leading-5 text-slate-100">{safeJson(run.blueprint_slot)}</pre>
      </Panel>

      <Panel ariaLabel="Generation configuration versions" title="Generation configuration versions" description="All routing and contract versions were selected server-side and persisted with this run.">
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Definition label="Prompt ID" mono value={run.prompt_id} />
          <Definition label="Prompt version" mono value={run.prompt_version} />
          <Definition label="Provider" mono value={run.provider} />
          <Definition label="Provider version" mono value={run.provider_version} />
          <Definition label="Model" mono value={run.model} />
          <Definition label="Model version" mono value={run.model_version} />
          <Definition label="Retrieval version" mono value={run.retrieval_version} />
          <Definition label="Schema version" mono value={run.schema_version} />
          <Definition label="Pricing version" mono value={run.pricing_version} />
        </dl>
      </Panel>

      <Panel ariaLabel="Generation budgets and parameters" title="Generation budgets and parameters" description="Persisted server-owned bounds and provider-neutral parameters. They are inspectable, never client-editable.">
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <h3 className="text-sm font-semibold">Budgets</h3>
            <pre className="mt-2 overflow-auto whitespace-pre-wrap break-words rounded-xl border border-slate-200 bg-slate-50 p-4 font-mono text-xs">{safeJson(run.budgets)}</pre>
          </div>
          <div>
            <h3 className="text-sm font-semibold">Generation parameters</h3>
            <pre className="mt-2 overflow-auto whitespace-pre-wrap break-words rounded-xl border border-slate-200 bg-slate-50 p-4 font-mono text-xs">{safeJson(run.generation_parameters)}</pre>
          </div>
        </div>
      </Panel>

      <Panel ariaLabel="Persisted generation context" title="Persisted generation context" description="Exact reviewed source snapshots are persisted as untrusted data with IDs, versions, provenance, and taxonomy.">
        <ContextSnapshot context={run.context} />
      </Panel>

      <Panel ariaLabel="Provider attempts" title="Provider attempts" description="Append-only attempts show bounded retries, lineage, sanitized failures, retry hints, and per-attempt accounting.">
        <AttemptsView attempts={values} />
      </Panel>

      <Panel ariaLabel="Generated candidate" title="Generated candidate" description="Question, proposed answer, and marking data remain unvalidated model output.">
        {candidateObject ? (
          <CandidateView candidate={candidateObject} />
        ) : (
          <div className="rounded-xl border border-dashed border-slate-300 p-4">
            <h3 className="font-semibold">No candidate available</h3>
            <p className="mt-1 text-sm text-slate-600">
              Pending, running, and failed runs do not expose candidate content. Refresh durable state if work is still active.
            </p>
          </div>
        )}
      </Panel>
    </article>
  );
}

export function GenerationStudio({ role }: { role: Role }) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [exams, setExams] = useState<Exam[]>([]);
  const [media, setMedia] = useState<Medium[]>([]);
  const [curricula, setCurricula] = useState<Curriculum[]>([]);
  const [selectedCurriculumId, setSelectedCurriculumId] = useState("");
  const [blueprints, setBlueprints] = useState<BlueprintSummary[]>([]);
  const [selectedBlueprintId, setSelectedBlueprintId] = useState("");
  const [blueprintDetail, setBlueprintDetail] = useState<Blueprint | null>(null);
  const [selectedSlotId, setSelectedSlotId] = useState("");
  const [chunks, setChunks] = useState<KnowledgeChunk[]>([]);
  const [questions, setQuestions] = useState<HistoricalQuestion[]>([]);
  const [contextCapped, setContextCapped] = useState(false);
  const [selectedChunkIds, setSelectedChunkIds] = useState<Set<string>>(() => new Set());
  const [selectedQuestionIds, setSelectedQuestionIds] = useState<Set<string>>(() => new Set());
  const [runs, setRuns] = useState<GenerationRunSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [runDetail, setRunDetail] = useState<GenerationRun | null>(null);
  const [attempts, setAttempts] = useState<GenerationAttempt[]>([]);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [pollTarget, setPollTarget] = useState<PollTarget | null>(null);

  const [workspaceLoading, setWorkspaceLoading] = useState(true);
  const [dataLoading, setDataLoading] = useState(false);
  const [blueprintLoading, setBlueprintLoading] = useState(false);
  const [runsLoading, setRunsLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [createLoading, setCreateLoading] = useState(false);
  const [retryLoading, setRetryLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<UiError | null>(null);
  const [dataError, setDataError] = useState<UiError | null>(null);
  const [blueprintError, setBlueprintError] = useState<UiError | null>(null);
  const [runsError, setRunsError] = useState<UiError | null>(null);
  const [detailError, setDetailError] = useState<UiError | null>(null);
  const [operationError, setOperationError] = useState<UiError | null>(null);
  const [pollError, setPollError] = useState<UiError | null>(null);
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");

  const workspaceRequestId = useRef(0);
  const dataRequestId = useRef(0);
  const blueprintRequestId = useRef(0);
  const detailRequestId = useRef(0);
  const operationRequestId = useRef(0);
  const operationInFlight = useRef(false);
  const latestRunVersions = useRef(new Map<string, number>());

  const choices = useMemo(() => {
    const examsById = new Map(exams.map((value) => [value.id, value]));
    const mediaById = new Map(media.map((value) => [value.id, value]));
    return curricula.flatMap((value): CurriculumChoice[] => {
      const selectedExam = examsById.get(value.exam_configuration_id);
      const selectedMedium = mediaById.get(value.medium_id);
      return value.active && selectedExam?.active && selectedExam.grade === 5 && selectedMedium?.active
        ? [{ curriculum: value, exam: selectedExam, medium: selectedMedium }]
        : [];
    });
  }, [curricula, exams, media]);

  const selectedSlot = useMemo(
    () => blueprintDetail?.blueprint.slots.find((value) => value.slot_id === selectedSlotId) ?? null,
    [blueprintDetail, selectedSlotId],
  );
  const selectedCount = selectedChunkIds.size + selectedQuestionIds.size;
  const scopedChunks = useMemo(
    () => chunks.filter((value) => value.curriculum_version_id === selectedCurriculumId && value.review_state === "reviewed"),
    [chunks, selectedCurriculumId],
  );
  const scopedQuestions = useMemo(
    () => questions.filter((value) => value.curriculum_version_id === selectedCurriculumId && value.review_state === "reviewed"),
    [questions, selectedCurriculumId],
  );

  const acceptRunDetail = useCallback(
    (detail: GenerationRun, nextAttempts: GenerationAttempt[]) => {
      const latestVersion = latestRunVersions.current.get(detail.id) ?? -1;
      if (detail.version < latestVersion) return false;
      latestRunVersions.current.set(detail.id, detail.version);
      setRunDetail(detail);
      setAttempts(nextAttempts);
      setRuns((current) => [
        summaryFromRun(detail),
        ...current.filter((value) => value.id !== detail.id),
      ]);
      return true;
    },
    [],
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
      const nextExams = responses[0].data ?? [];
      const nextMedia = responses[1].data ?? [];
      const nextCurricula = responses[2].data ?? [];
      const examsById = new Map(nextExams.map((value) => [value.id, value]));
      const mediaById = new Map(nextMedia.map((value) => [value.id, value]));
      const available = nextCurricula.filter((value) => {
        const nextExam = examsById.get(value.exam_configuration_id);
        const nextMedium = mediaById.get(value.medium_id);
        return value.active && nextExam?.active && nextExam.grade === 5 && nextMedium?.active;
      });
      setExams(nextExams);
      setMedia(nextMedia);
      setCurricula(nextCurricula);
      setSelectedCurriculumId((current) =>
        available.some((value) => value.id === current) ? current : (available[0]?.id ?? ""),
      );
    } catch {
      if (requestId === workspaceRequestId.current) setWorkspaceError(networkError("workspace"));
    } finally {
      if (requestId === workspaceRequestId.current) setWorkspaceLoading(false);
    }
  }, [api]);

  const loadCurriculumData = useCallback(async (curriculumId: string) => {
    const requestId = ++dataRequestId.current;
    setDataLoading(true);
    setDataError(null);
    setRunsError(null);
    try {
      const path = { curriculum_version_id: curriculumId };
      const [blueprintResponse, runResponse, chunkDiscovery, questionDiscovery] = await Promise.all([
        api.GET("/api/v1/admin/curricula/{curriculum_version_id}/blueprints", {
          params: { path, query: { limit: LIST_LIMIT, offset: 0 } },
        }),
        api.GET("/api/v1/admin/curricula/{curriculum_version_id}/generation-runs", {
          params: { path, query: { limit: LIST_LIMIT, offset: 0 } },
        }),
        discoverReviewedChunks(api, curriculumId),
        discoverReviewedQuestions(api, curriculumId),
      ]);
      if (requestId !== dataRequestId.current) return;
      const failed = firstFailure([
        blueprintResponse,
        runResponse,
        ...(chunkDiscovery.failure ? [chunkDiscovery.failure] : []),
        ...(questionDiscovery.failure ? [questionDiscovery.failure] : []),
      ]);
      if (failed?.error !== undefined) {
        setDataError(apiError(failed.error, failed.response.status, "selection"));
        return;
      }
      const nextBlueprints = (blueprintResponse.data ?? []).filter(
        (value) => value.curriculum_version_id === curriculumId,
      );
      const nextRuns = (runResponse.data ?? []).filter(
        (value) => value.curriculum_version_id === curriculumId,
      );
      for (const run of nextRuns) {
        const latestVersion = latestRunVersions.current.get(run.id) ?? -1;
        if (run.version > latestVersion) latestRunVersions.current.set(run.id, run.version);
      }
      setBlueprints(nextBlueprints);
      setRuns(nextRuns);
      setChunks(
        chunkDiscovery.records.filter(
          (value) => value.curriculum_version_id === curriculumId && value.review_state === "reviewed",
        ),
      );
      setQuestions(
        questionDiscovery.records.filter(
          (value) => value.curriculum_version_id === curriculumId && value.review_state === "reviewed",
        ),
      );
      setContextCapped(chunkDiscovery.capped || questionDiscovery.capped);
      setSelectedBlueprintId((current) =>
        nextBlueprints.some((value) => value.id === current)
          ? current
          : (nextBlueprints[0]?.id ?? ""),
      );
      setSelectedRunId((current) =>
        nextRuns.some((value) => value.id === current) ? current : (nextRuns[0]?.id ?? ""),
      );
      if (!nextBlueprints.length) {
        setBlueprintDetail(null);
        setSelectedSlotId("");
      }
      if (!nextRuns.length) {
        setRunDetail(null);
        setAttempts([]);
      }
    } catch {
      if (requestId === dataRequestId.current) setDataError(networkError("selection"));
    } finally {
      if (requestId === dataRequestId.current) setDataLoading(false);
    }
  }, [api]);

  const loadBlueprint = useCallback(async (curriculumId: string, blueprintId: string) => {
    const requestId = ++blueprintRequestId.current;
    setBlueprintLoading(true);
    setBlueprintError(null);
    try {
      const response = await api.GET(
        "/api/v1/admin/curricula/{curriculum_version_id}/blueprints/{paper_blueprint_id}",
        {
          params: {
            path: {
              curriculum_version_id: curriculumId,
              paper_blueprint_id: blueprintId,
            },
          },
        },
      );
      if (requestId !== blueprintRequestId.current) return;
      if (response.error !== undefined) {
        setBlueprintError(apiError(response.error, response.response.status, "selection"));
        setBlueprintDetail(null);
        return;
      }
      const detail = response.data as Blueprint | undefined;
      if (!detail || detail.curriculum_version_id !== curriculumId) {
        setBlueprintError({
          code: "generation_blueprint_scope_mismatch",
          message: "The returned immutable blueprint does not belong to the selected curriculum.",
          title: "Immutable blueprint scope mismatch",
        });
        setBlueprintDetail(null);
        return;
      }
      setBlueprintDetail(detail);
      setSelectedSlotId((current) =>
        detail.blueprint.slots.some((value) => value.slot_id === current)
          ? current
          : (detail.blueprint.slots[0]?.slot_id ?? ""),
      );
    } catch {
      if (requestId === blueprintRequestId.current) setBlueprintError(networkError("selection"));
    } finally {
      if (requestId === blueprintRequestId.current) setBlueprintLoading(false);
    }
  }, [api]);

  const loadRunDetail = useCallback(async (curriculumId: string, runId: string) => {
    const requestId = ++detailRequestId.current;
    setDetailLoading(true);
    setDetailError(null);
    try {
      const path = { curriculum_version_id: curriculumId, generation_run_id: runId };
      const responses = await Promise.all([
        api.GET("/api/v1/admin/curricula/{curriculum_version_id}/generation-runs/{generation_run_id}", {
          params: { path },
        }),
        api.GET("/api/v1/admin/curricula/{curriculum_version_id}/generation-runs/{generation_run_id}/attempts", {
          params: { path, query: { limit: 10, offset: 0 } },
        }),
      ]);
      if (requestId !== detailRequestId.current) return;
      const failed = firstFailure(responses);
      if (failed?.error !== undefined) {
        setDetailError(apiError(failed.error, failed.response.status, "detail"));
        setRunDetail(null);
        setAttempts([]);
        return;
      }
      const detail = responses[0].data as GenerationRun | undefined;
      if (!detail || detail.curriculum_version_id !== curriculumId) {
        setDetailError({
          code: "generation_run_scope_mismatch",
          message: "The returned run does not belong to the selected curriculum.",
          title: "Generation run scope mismatch",
        });
        return;
      }
      acceptRunDetail(
        detail,
        (responses[1].data as GenerationAttempt[] | undefined) ?? [],
      );
    } catch {
      if (requestId === detailRequestId.current) {
        setDetailError(networkError("detail"));
        setRunDetail(null);
        setAttempts([]);
      }
    } finally {
      if (requestId === detailRequestId.current) setDetailLoading(false);
    }
  }, [acceptRunDetail, api]);

  const refreshRuns = useCallback(async () => {
    if (!selectedCurriculumId) return;
    const requestId = ++dataRequestId.current;
    setRunsLoading(true);
    setRunsError(null);
    try {
      const response = await api.GET(
        "/api/v1/admin/curricula/{curriculum_version_id}/generation-runs",
        {
          params: {
            path: { curriculum_version_id: selectedCurriculumId },
            query: { limit: LIST_LIMIT, offset: 0 },
          },
        },
      );
      if (requestId !== dataRequestId.current) return;
      if (response.error !== undefined) {
        setRunsError(apiError(response.error, response.response.status, "runs"));
        return;
      }
      const next = (response.data ?? []).filter(
        (value) => value.curriculum_version_id === selectedCurriculumId,
      );
      setRuns(next);
      setSelectedRunId((current) =>
        next.some((value) => value.id === current) ? current : (next[0]?.id ?? ""),
      );
      if (!next.length) {
        setRunDetail(null);
        setAttempts([]);
      }
    } catch {
      if (requestId === dataRequestId.current) setRunsError(networkError("runs"));
    } finally {
      if (requestId === dataRequestId.current) setRunsLoading(false);
    }
  }, [api, selectedCurriculumId]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadWorkspace(), 0);
    return () => window.clearTimeout(timeout);
  }, [loadWorkspace]);

  useEffect(() => {
    if (!selectedCurriculumId) return;
    const timeout = window.setTimeout(() => void loadCurriculumData(selectedCurriculumId), 0);
    return () => window.clearTimeout(timeout);
  }, [loadCurriculumData, selectedCurriculumId]);

  useEffect(() => {
    if (!selectedCurriculumId || !selectedBlueprintId) return;
    const timeout = window.setTimeout(
      () => void loadBlueprint(selectedCurriculumId, selectedBlueprintId),
      0,
    );
    return () => window.clearTimeout(timeout);
  }, [loadBlueprint, selectedBlueprintId, selectedCurriculumId]);

  useEffect(() => {
    if (!selectedCurriculumId || !selectedRunId) return;
    const timeout = window.setTimeout(
      () => void loadRunDetail(selectedCurriculumId, selectedRunId),
      0,
    );
    return () => window.clearTimeout(timeout);
  }, [loadRunDetail, selectedCurriculumId, selectedRunId]);

  useEffect(() => {
    if (!pollTarget) return;
    const target = pollTarget;
    const controller = new AbortController();
    const startedAt = Date.now();
    let timeout: number | undefined;
    let delayIndex = 1;

    const stop = () => {
      setPollTarget((current) => (current?.token === target.token ? null : current));
    };
    const poll = async () => {
      try {
        const path = {
          curriculum_version_id: target.curriculumId,
          generation_run_id: target.runId,
        };
        const responses = await Promise.all([
          ...(target.jobId
            ? [
                api.GET(
                  "/api/v1/admin/curricula/{curriculum_version_id}/generation-jobs/{generation_job_id}",
                  {
                    params: {
                      path: {
                        curriculum_version_id: target.curriculumId,
                        generation_job_id: target.jobId,
                      },
                    },
                    signal: controller.signal,
                  },
                ),
              ]
            : []),
          api.GET(
            "/api/v1/admin/curricula/{curriculum_version_id}/generation-runs/{generation_run_id}",
            { params: { path }, signal: controller.signal },
          ),
          api.GET(
            "/api/v1/admin/curricula/{curriculum_version_id}/generation-runs/{generation_run_id}/attempts",
            {
              params: { path, query: { limit: 10, offset: 0 } },
              signal: controller.signal,
            },
          ),
        ]);
        if (controller.signal.aborted) return;
        const failed = firstFailure(responses);
        if (failed?.error !== undefined) {
          setPollError(apiError(failed.error, failed.response.status, "poll"));
          stop();
          return;
        }
        let index = 0;
        const nextJob = target.jobId ? (responses[index++].data as GenerationJob | undefined) : undefined;
        const nextRun = responses[index++].data as GenerationRun | undefined;
        const nextAttempts = responses[index].data as GenerationAttempt[] | undefined;
        if (!nextRun || nextRun.curriculum_version_id !== target.curriculumId) {
          setPollError({
            code: "generation_run_scope_mismatch",
            message: "Polling returned a run outside the selected curriculum. Polling was stopped.",
            title: "Generation polling stopped",
          });
          stop();
          return;
        }
        if (nextJob) {
          setJob((current) =>
            current?.id === nextJob.id && current.version > nextJob.version ? current : nextJob,
          );
        }
        acceptRunDetail(nextRun, nextAttempts ?? []);
        setPollError(null);

        const runTerminal = nextRun.status === "succeeded" || nextRun.status === "failed";
        const jobTerminal = !nextJob || nextJob.status === "succeeded" || nextJob.status === "failed";
        if (runTerminal && jobTerminal) {
          stop();
          return;
        }
        if (nextJob?.status === "failed") {
          stop();
          return;
        }
        if (Date.now() - startedAt >= POLL_MAX_DURATION_MS) {
          setPollError({
            code: "generation_poll_timeout",
            message:
              "Automatic polling reached its two-minute bound. The durable job continues independently; use Refresh run to inspect it again.",
            title: "Generation polling paused",
          });
          stop();
          return;
        }
        const delay = POLL_DELAYS_MS[Math.min(delayIndex, POLL_DELAYS_MS.length - 1)];
        delayIndex += 1;
        timeout = window.setTimeout(() => void poll(), delay);
      } catch (error) {
        if (controller.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) return;
        setPollError(networkError("poll"));
        stop();
      }
    };

    timeout = window.setTimeout(() => void poll(), POLL_DELAYS_MS[0]);
    return () => {
      controller.abort();
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [acceptRunDetail, api, pollTarget]);

  function clearContextSelection() {
    setSelectedChunkIds(new Set());
    setSelectedQuestionIds(new Set());
    setFormError("");
  }

  function selectCurriculum(curriculumId: string) {
    dataRequestId.current += 1;
    blueprintRequestId.current += 1;
    detailRequestId.current += 1;
    operationRequestId.current += 1;
    operationInFlight.current = false;
    setCreateLoading(false);
    setRetryLoading(false);
    setPollTarget(null);
    setBlueprints([]);
    setSelectedBlueprintId("");
    setBlueprintDetail(null);
    setSelectedSlotId("");
    setChunks([]);
    setQuestions([]);
    setRuns([]);
    setSelectedRunId("");
    setRunDetail(null);
    setAttempts([]);
    setJob(null);
    setDataError(null);
    setBlueprintError(null);
    setRunsError(null);
    setDetailError(null);
    setOperationError(null);
    setPollError(null);
    setNotice("");
    clearContextSelection();
    setSelectedCurriculumId(curriculumId);
  }

  function selectBlueprint(blueprintId: string) {
    if (blueprintId === selectedBlueprintId) return;
    blueprintRequestId.current += 1;
    setBlueprintDetail(null);
    setSelectedSlotId("");
    setBlueprintError(null);
    setOperationError(null);
    setNotice("");
    clearContextSelection();
    setSelectedBlueprintId(blueprintId);
  }

  function selectSlot(slotId: string) {
    setOperationError(null);
    setNotice("");
    clearContextSelection();
    setSelectedSlotId(slotId);
  }

  function toggleReference(kind: ContextKind, id: string, checked: boolean) {
    setFormError("");
    const setValue = kind === "knowledge_chunk" ? setSelectedChunkIds : setSelectedQuestionIds;
    setValue((current) => {
      const next = new Set(current);
      if (checked) {
        if (selectedCount >= MAX_CONTEXT_REFERENCES) return current;
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  }

  function selectRun(runId: string) {
    detailRequestId.current += 1;
    setPollTarget(null);
    setJob(null);
    setRunDetail(null);
    setAttempts([]);
    setDetailError(null);
    setOperationError(null);
    setPollError(null);
    setNotice("");
    setSelectedRunId(runId);
    const summary = runs.find((value) => value.id === runId);
    if (summary?.status === "pending" || summary?.status === "running") {
      setPollTarget({
        curriculumId: selectedCurriculumId,
        jobId: null,
        runId,
        token: generatedIdempotencyKey("generation"),
      });
    }
  }

  async function createRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (role !== "admin" || operationInFlight.current) return;
    if (!selectedCurriculumId || !blueprintDetail || !selectedSlot) {
      setFormError("Select an active curriculum, immutable blueprint, and exact slot before creating a run.");
      return;
    }
    if (selectedCount < 1 || selectedCount > MAX_CONTEXT_REFERENCES) {
      setFormError("Select at least one reviewed reference and no more than 16 before creating a run.");
      return;
    }
    const request: GenerationRequest = {
      historical_question_ids: [...selectedQuestionIds],
      knowledge_chunk_ids: [...selectedChunkIds],
      paper_blueprint_id: blueprintDetail.id,
      slot_id: selectedSlot.slot_id,
    };
    const curriculumId = selectedCurriculumId;
    const requestId = ++operationRequestId.current;
    operationInFlight.current = true;
    setCreateLoading(true);
    setOperationError(null);
    setPollError(null);
    setFormError("");
    setNotice("");
    try {
      const key = generatedIdempotencyKey("generation");
      const response = await api.POST(
        "/api/v1/admin/curricula/{curriculum_version_id}/generation-runs",
        {
          body: request,
          params: {
            header: { "Idempotency-Key": key },
            path: { curriculum_version_id: curriculumId },
          },
        },
      );
      if (requestId !== operationRequestId.current || curriculumId !== selectedCurriculumId) return;
      if (response.error !== undefined) {
        setOperationError(apiError(response.error, response.response.status, "create"));
        return;
      }
      const nextJob = response.data as GenerationJob;
      setJob(nextJob);
      setSelectedRunId(nextJob.generation_run_id);
      setNotice(
        nextJob.deduplicated
          ? "Existing identical generation operation selected; no duplicate was queued."
          : "Generation run queued.",
      );
      setPollTarget({
        curriculumId,
        jobId: nextJob.id,
        runId: nextJob.generation_run_id,
        token: key,
      });
    } catch {
      if (requestId === operationRequestId.current) setOperationError(networkError("create"));
    } finally {
      if (requestId === operationRequestId.current) {
        operationInFlight.current = false;
        setCreateLoading(false);
      }
    }
  }

  async function retryRun() {
    if (
      role !== "admin" ||
      operationInFlight.current ||
      !selectedCurriculumId ||
      !runDetail ||
      runDetail.status !== "failed"
    ) return;
    const curriculumId = selectedCurriculumId;
    const failedRunId = runDetail.id;
    const requestId = ++operationRequestId.current;
    operationInFlight.current = true;
    setRetryLoading(true);
    setOperationError(null);
    setPollError(null);
    setNotice("");
    try {
      const key = generatedIdempotencyKey("generation-retry");
      const response = await api.POST(
        "/api/v1/admin/curricula/{curriculum_version_id}/generation-runs/{generation_run_id}/retry",
        {
          params: {
            header: { "Idempotency-Key": key },
            path: {
              curriculum_version_id: curriculumId,
              generation_run_id: failedRunId,
            },
          },
        },
      );
      if (requestId !== operationRequestId.current || curriculumId !== selectedCurriculumId) return;
      if (response.error !== undefined) {
        setOperationError(apiError(response.error, response.response.status, "retry"));
        return;
      }
      const nextJob = response.data as GenerationJob;
      setJob(nextJob);
      setSelectedRunId(nextJob.generation_run_id);
      setNotice(
        nextJob.deduplicated
          ? "Existing identical failed-run retry selected; no duplicate was queued."
          : "Failed run retry queued.",
      );
      setPollTarget({
        curriculumId,
        jobId: nextJob.id,
        runId: nextJob.generation_run_id,
        token: key,
      });
    } catch {
      if (requestId === operationRequestId.current) setOperationError(networkError("retry"));
    } finally {
      if (requestId === operationRequestId.current) {
        operationInFlight.current = false;
        setRetryLoading(false);
      }
    }
  }

  function refreshDetail() {
    if (selectedCurriculumId && selectedRunId) {
      setPollError(null);
      void loadRunDetail(selectedCurriculumId, selectedRunId);
      const current = runs.find((value) => value.id === selectedRunId);
      if (current?.status === "pending" || current?.status === "running") {
        setPollTarget({
          curriculumId: selectedCurriculumId,
          jobId: job?.generation_run_id === selectedRunId ? job.id : null,
          runId: selectedRunId,
          token: generatedIdempotencyKey("generation"),
        });
      }
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-5 py-10 sm:px-8">
      <header className="flex flex-wrap items-start justify-between gap-5 border-b border-slate-300 pb-8">
        <div className="max-w-3xl">
          <p className="font-mono text-xs tracking-[0.2em] text-amber-700 uppercase">P7 · Grounded generation</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Generation Studio</h1>
          <p className="mt-3 text-base leading-7 text-slate-600">
            Bind one immutable blueprint slot to 1–16 reviewed, same-curriculum references. The server owns prompts, providers, models, pricing, credentials, budgets, and durable execution.
          </p>
        </div>
        <div className="rounded-xl border border-slate-300 bg-white p-4 text-sm shadow-sm">
          <p className="font-semibold">{role === "admin" ? "Administrator run access" : "Reviewer read access"}</p>
          <p className="mt-1 max-w-sm text-slate-600">
            {role === "admin"
              ? "You may create runs and explicitly retry failures. Generation can never publish."
              : "You may inspect persisted runs and evidence. Create and retry controls are disabled."}
          </p>
        </div>
      </header>

      {role === "reviewer" ? (
        <section className="mt-6 rounded-xl border border-blue-300 bg-blue-50 p-4 text-blue-950">
          <h2 className="font-semibold">Reviewer read-only mode</h2>
          <p className="mt-1 text-sm leading-6">
            Blueprint, context, configuration, attempts, accounting, and candidate inspection remain available. Run and retry mutations require administrator permission.
          </p>
        </section>
      ) : null}

      <section className="mt-8" aria-label="Generation curriculum scope">
        {workspaceLoading ? (
          <p className="rounded-xl border border-slate-300 bg-white p-5 text-sm text-slate-600" role="status">
            Loading generation workspace…
          </p>
        ) : workspaceError ? (
          <ErrorPanel error={workspaceError} onRetry={() => void loadWorkspace()} retryLabel="Retry generation workspace" />
        ) : choices.length ? (
          <label className={`${fieldClass} max-w-2xl`}>
            Active Grade 5 curriculum
            <select
              className={inputClass}
              onChange={(event) => selectCurriculum(event.target.value)}
              value={selectedCurriculumId}
            >
              {choices.map((choice) => (
                <option key={choice.curriculum.id} value={choice.curriculum.id}>
                  {choice.curriculum.title} · {choice.exam.name} · {choice.medium.name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <div className="rounded-xl border border-dashed border-slate-400 p-5">
            <h2 className="font-semibold">No active Grade 5 curriculum</h2>
            <p className="mt-1 text-sm text-slate-600">Create and activate the curriculum, exam, and medium before generation.</p>
            <Link className={`${secondaryButton} mt-4`} href="/admin/curriculum">Open Curriculum Studio</Link>
          </div>
        )}
      </section>

      {selectedCurriculumId && !workspaceError ? (
        <>
          {dataLoading ? (
            <p className="mt-6 rounded-xl border border-slate-300 bg-white p-5 text-sm text-slate-600" role="status">
              Loading generation data…
            </p>
          ) : dataError ? (
            <div className="mt-6">
              <ErrorPanel error={dataError} onRetry={() => void loadCurriculumData(selectedCurriculumId)} retryLabel="Retry generation data" />
            </div>
          ) : (
            <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1.45fr)_minmax(20rem,0.75fr)]">
              <Panel title="Grounded run setup" description="Choose one authoritative blueprint and exact slot before selecting reviewed context.">
                {blueprints.length ? (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <label className={fieldClass}>
                      Immutable blueprint
                      <select className={inputClass} onChange={(event) => selectBlueprint(event.target.value)} value={selectedBlueprintId}>
                        {blueprints.map((value) => (
                          <option key={value.id} value={value.id}>{value.paper_code} — {value.title} · {value.slot_count} slots</option>
                        ))}
                      </select>
                    </label>
                    <label className={fieldClass}>
                      Exact blueprint slot
                      <select
                        className={inputClass}
                        disabled={!blueprintDetail || blueprintLoading}
                        onChange={(event) => selectSlot(event.target.value)}
                        value={selectedSlotId}
                      >
                        {(blueprintDetail?.blueprint.slots ?? []).map((value) => (
                          <option key={value.slot_id} value={value.slot_id}>
                            {value.slot_id} · {displayEnum(value.question_type)} · {value.marks} marks
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                ) : (
                  <div className="rounded-xl border border-dashed border-slate-300 p-5">
                    <h3 className="font-semibold">No immutable blueprints yet</h3>
                    <p className="mt-1 text-sm text-slate-600">Create and inspect a deterministic blueprint before generation.</p>
                    <Link className={`${secondaryButton} mt-4`} href="/admin/blueprints">Open Blueprint Studio</Link>
                  </div>
                )}
                {blueprintLoading ? <p className="mt-4 text-sm text-slate-600" role="status">Loading immutable blueprint…</p> : null}
                {blueprintError ? <div className="mt-4"><ErrorPanel error={blueprintError} onRetry={() => selectedBlueprintId && void loadBlueprint(selectedCurriculumId, selectedBlueprintId)} retryLabel="Retry blueprint" /></div> : null}
                {blueprintDetail && selectedSlot ? <BlueprintSelection blueprint={blueprintDetail} slot={selectedSlot} /> : null}

                <section className="mt-6 border-t border-slate-200 pt-5" aria-labelledby="context-selection-heading">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h3 className="font-semibold" id="context-selection-heading">Reviewed context references</h3>
                      <p className="mt-1 text-sm leading-6 text-slate-600">
                        Only reviewed records returned by this curriculum are loaded. Taxonomy-mismatched records remain visible but disabled.
                      </p>
                    </div>
                    <Badge className={selectedCount === MAX_CONTEXT_REFERENCES ? "border-amber-300 bg-amber-50 text-amber-950" : "border-slate-300 bg-slate-50 text-slate-800"}>
                      {selectedCount} of 16 references selected
                    </Badge>
                  </div>
                  {contextCapped ? (
                    <p className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950" role="status">
                      Reviewed context discovery reached its 5,000-record safety cap. Refine the knowledge base before relying on this list.
                    </p>
                  ) : null}
                  {selectedSlot ? (
                    scopedChunks.length || scopedQuestions.length ? (
                      <div className="mt-4 grid gap-5">
                        <section aria-labelledby="chunks-heading">
                          <h4 className="text-sm font-semibold" id="chunks-heading">Knowledge chunks</h4>
                          {scopedChunks.length ? (
                            <ul className="mt-2 grid gap-3">
                              {scopedChunks.map((record) => {
                                const matches = taxonomyMatches(record.classification, selectedSlot.taxonomy_target);
                                const checked = selectedChunkIds.has(record.id);
                                return (
                                  <ReferenceCard
                                    checked={checked}
                                    disabled={role !== "admin" || !matches || (!checked && selectedCount >= MAX_CONTEXT_REFERENCES)}
                                    id={record.id}
                                    key={record.id}
                                    kind="knowledge_chunk"
                                    matches={matches}
                                    onChange={(value) => toggleReference("knowledge_chunk", record.id, value)}
                                    record={record}
                                  />
                                );
                              })}
                            </ul>
                          ) : <p className="mt-2 text-sm text-slate-500">No reviewed knowledge chunks are available.</p>}
                        </section>
                        <section aria-labelledby="questions-heading">
                          <h4 className="text-sm font-semibold" id="questions-heading">Historical questions</h4>
                          {scopedQuestions.length ? (
                            <ul className="mt-2 grid gap-3">
                              {scopedQuestions.map((record) => {
                                const matches = taxonomyMatches(record.classification, selectedSlot.taxonomy_target);
                                const checked = selectedQuestionIds.has(record.id);
                                return (
                                  <ReferenceCard
                                    checked={checked}
                                    disabled={role !== "admin" || !matches || (!checked && selectedCount >= MAX_CONTEXT_REFERENCES)}
                                    id={record.id}
                                    key={record.id}
                                    kind="historical_question"
                                    matches={matches}
                                    onChange={(value) => toggleReference("historical_question", record.id, value)}
                                    record={record}
                                  />
                                );
                              })}
                            </ul>
                          ) : <p className="mt-2 text-sm text-slate-500">No reviewed historical questions are available.</p>}
                        </section>
                      </div>
                    ) : (
                      <div className="mt-4 rounded-xl border border-dashed border-slate-300 p-4">
                        <h4 className="font-semibold">No reviewed context available</h4>
                        <p className="mt-1 text-sm text-slate-600">Review and classify a trusted-source chunk or historical question in this curriculum first.</p>
                        <Link className={`${secondaryButton} mt-3`} href="/admin/knowledge">Open Knowledge Studio</Link>
                      </div>
                    )
                  ) : (
                    <p className="mt-4 rounded-xl border border-dashed border-slate-300 p-4 text-sm text-slate-600">Select an immutable blueprint and exact slot to apply taxonomy filtering.</p>
                  )}
                </section>

                {role === "admin" ? (
                  <Form className="mt-6 border-t border-slate-200 pt-5" onSubmit={createRun}>
                    <p className="text-sm leading-6 text-slate-600">
                      The request body contains only the blueprint record ID, exact slot ID, and selected context IDs. A fresh bounded idempotency key is generated for this explicit operation.
                    </p>
                    {formError ? <p className="mt-3 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-950" role="alert">{formError}</p> : null}
                    {operationError ? <div className="mt-3"><ErrorPanel error={operationError} /></div> : null}
                    {notice ? <p className="mt-3 rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-950" role="status">{notice}</p> : null}
                    <Button className={`${primaryButton} mt-4`} isDisabled={createLoading || retryLoading || !selectedSlot} type="submit">
                      {createLoading ? "Creating generation run…" : "Create generation run"}
                    </Button>
                  </Form>
                ) : null}
              </Panel>

              <Panel title="Persisted generation runs" description="Select any durable run in this curriculum for read-only inspection.">
                {runsError ? <div className="mb-4"><ErrorPanel error={runsError} onRetry={() => void refreshRuns()} retryLabel="Retry run list" /></div> : null}
                <RunList loading={runsLoading} onRefresh={() => void refreshRuns()} onSelect={selectRun} runs={runs} selectedRunId={selectedRunId} />
              </Panel>
            </div>
          )}
        </>
      ) : null}

      {pollError ? (
        <div className="mt-6"><ErrorPanel error={pollError} onRetry={refreshDetail} retryLabel="Refresh run" /></div>
      ) : null}
      {detailLoading ? (
        <p className="mt-6 rounded-xl border border-slate-300 bg-white p-5 text-sm text-slate-600" role="status">Loading generation run…</p>
      ) : null}
      {detailError ? (
        <div className="mt-6"><ErrorPanel error={detailError} onRetry={refreshDetail} retryLabel="Retry run detail" /></div>
      ) : null}
      {runDetail ? (
        <RunInspection
          attempts={attempts}
          job={job}
          onRetry={() => void retryRun()}
          polling={pollTarget?.runId === runDetail.id}
          retrying={retryLoading}
          role={role}
          run={runDetail}
        />
      ) : null}
      {retryLoading ? <p className="mt-4 text-sm text-slate-600" role="status">Queuing explicit failed-run retry…</p> : null}
      {operationError && !blueprints.length ? <div className="mt-6"><ErrorPanel error={operationError} /></div> : null}
    </div>
  );
}
