"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Button, Form } from "react-aria-components";

import { Badge } from "@/components/ui/badge";

type HistoricalQuestion = components["schemas"]["HistoricalQuestionResponse"];
type KnowledgeChunk = components["schemas"]["KnowledgeChunkResponse"];
type KnowledgeRecord = HistoricalQuestion | KnowledgeChunk;
type EmbeddingConfiguration =
  components["schemas"]["EmbeddingConfigurationMetadataResponse"];
type EmbeddingJob = components["schemas"]["EmbeddingJobResponse"];
type EmbeddingJobCreateRequest = components["schemas"]["EmbeddingJobCreateRequest"];
type Role = "admin" | "reviewer";
type RecordKind = "questions" | "chunks";

type Candidate = {
  kind: RecordKind;
  record: KnowledgeRecord;
};

type UiError = {
  code: string;
  message: string;
  title: string;
};

type PollTarget = {
  curriculumId: string;
  job: EmbeddingJob;
  token: number;
};

type PollError = {
  error: UiError;
  jobId: string;
};

type PendingIdempotency = {
  fingerprint: string;
  key: string;
};

const RECORD_LIST_LIMIT = 100;
const JOB_LIST_LIMIT = 50;
const MAX_SELECTION = 100;
const POLL_DELAYS_MS = [250, 500, 1_000, 2_000, 4_000] as const;
const POLL_MAX_DURATION_MS = 120_000;

const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none transition hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-400 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";

const knownWorkerFailureCodes = new Set([
  "embedding_config_conflict",
  "embedding_config_unavailable",
  "embedding_contract_error",
  "embedding_internal_error",
  "embedding_persistence_conflict",
  "embedding_provider_unavailable",
  "embedding_source_conflict",
  "embedding_source_invalid",
  "embedding_source_not_found",
  "worker_lease_expired",
]);

function detailCode(error: unknown): string {
  if (!error || typeof error !== "object" || !("detail" in error)) return "request_failed";
  const detail = (error as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail) || !("code" in detail)) {
    return "request_failed";
  }
  const code = (detail as { code?: unknown }).code;
  return typeof code === "string" ? code : "request_failed";
}

function apiError(error: unknown, status: number, phase: "create" | "load" | "poll"): UiError {
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
        phase === "create"
          ? "This account cannot queue embedding work. Record and job inspection remain read-only."
          : "This account cannot inspect embedding records or jobs in the selected curriculum.",
      title:
        phase === "create" ? "Embedding permission required" : "Embedding read permission required",
    };
  }

  const code = detailCode(error);
  const mapped: Record<string, UiError> = {
    embedding_config_conflict: {
      code,
      message:
        "The selected records conflict with the active persisted embedding space. Review the existing configuration metadata before retrying.",
      title: "Embedding configuration conflict",
    },
    embedding_config_unavailable: {
      code,
      message:
        "No active server-owned embedding configuration is available. Retry after an administrator restores the configured embedding space.",
      title: "Embedding configuration unavailable",
    },
    embedding_curriculum_not_found: {
      code,
      message: "The selected active curriculum is no longer available. Reload Knowledge Studio.",
      title: "Embedding curriculum not found",
    },
    embedding_idempotency_conflict: {
      code,
      message:
        "The operation identity is already bound to a different immutable selection. Reload jobs and begin a new explicit operation.",
      title: "Embedding idempotency conflict",
    },
    embedding_job_not_found: {
      code,
      message: "The durable embedding job is no longer available in this curriculum.",
      title: "Embedding job not found",
    },
    embedding_persistence_conflict: {
      code,
      message:
        "The embedding operation conflicted with persisted state. Reload reviewed records and job metadata before retrying.",
      title: "Embedding persistence conflict",
    },
    embedding_provider_unavailable: {
      code,
      message:
        "The configured server-side embedding provider is temporarily unavailable. No browser vector or provider override was accepted.",
      title: "Embedding provider unavailable",
    },
    embedding_queue_unavailable: {
      code,
      message:
        "The durable job could not be dispatched to the queue. Retry the unchanged selection to recover the same idempotent operation.",
      title: "Embedding queue unavailable",
    },
    embedding_source_conflict: {
      code,
      message:
        "A selected record conflicts with previously persisted source identity. Do not overwrite it; investigate the reviewed source and existing embedding.",
      title: "Embedding source conflict",
    },
    embedding_source_not_found: {
      code,
      message:
        "A selected record no longer exists in this curriculum. Reload the reviewed record inventory.",
      title: "Embedding source not found",
    },
    embedding_source_not_reviewed: {
      code,
      message:
        "At least one selected record is no longer reviewed. The inventory is being refreshed; select only currently reviewed records.",
      title: "Selection is no longer reviewed",
    },
  };
  if (mapped[code]) return mapped[code];

  return {
    code: "request_failed",
    message:
      phase === "poll"
        ? "The durable embedding job could not be read. Its server-side work may continue; resume monitoring after checking the connection."
        : phase === "load"
          ? "Embedding jobs and reviewed records could not be loaded. Retry or contact an administrator if the failure persists."
          : "The embedding operation could not be completed. Reload persisted metadata before retrying.",
    title:
      phase === "poll"
        ? "Embedding job monitoring failed"
        : phase === "load"
          ? "Embedding data could not be loaded"
          : "Embedding request failed",
  };
}

function networkError(phase: "create" | "load" | "poll"): UiError {
  if (phase === "create") {
    return {
      code: "network_error",
      message:
        "The embedding service could not be reached. The durable request outcome is unknown; retry the unchanged selection with the preserved operation identity.",
      title: "Embedding service connection failed",
    };
  }
  if (phase === "poll") {
    return {
      code: "network_error",
      message:
        "Automatic monitoring lost its connection. The durable job continues independently; resume monitoring when the service is reachable.",
      title: "Embedding job monitoring failed",
    };
  }
  return {
    code: "network_error",
    message: "Embedding jobs and reviewed records could not be reached. Check the connection and retry.",
    title: "Embedding data could not be loaded",
  };
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function generatedIdempotencyKey(): string {
  const cryptoObject = globalThis.crypto;
  let random: string;
  if (typeof cryptoObject?.randomUUID === "function") {
    random = cryptoObject.randomUUID();
  } else {
    if (typeof cryptoObject?.getRandomValues !== "function") {
      throw new Error("secure random source unavailable");
    }
    const bytes = new Uint8Array(16);
    cryptoObject.getRandomValues(bytes);
    random = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }
  const key = `embedding-${random}`;
  if (key.length > 128 || /\s/.test(key)) {
    throw new Error("generated embedding idempotency key is outside the client boundary");
  }
  return key;
}

function isQuestion(record: KnowledgeRecord): record is HistoricalQuestion {
  return "question_number" in record;
}

function recordLabel(candidate: Candidate): string {
  const { record } = candidate;
  return isQuestion(record)
    ? `Historical question ${record.paper_code} / ${record.question_number}`
    : `Knowledge chunk ${record.educational_boundary} / Sequence ${record.sequence}`;
}

function embeddingLabel(configuration: EmbeddingConfiguration): string {
  return `${configuration.provider} / ${configuration.model} / ${configuration.version} / ${configuration.dimension}d`;
}

function displayStatus(status: EmbeddingJob["status"]): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function statusProgress(status: EmbeddingJob["status"]): number {
  if (status === "queued") return 0;
  if (status === "claimed") return 1;
  return 2;
}

function preferJob(current: EmbeddingJob | undefined, candidate: EmbeddingJob): EmbeddingJob {
  if (!current || candidate.version > current.version) return candidate;
  if (candidate.version < current.version) return current;
  return statusProgress(candidate.status) > statusProgress(current.status) ? candidate : current;
}

function mergeJobs(
  current: EmbeddingJob[],
  incoming: EmbeddingJob[],
  curriculumVersionId: string,
): EmbeddingJob[] {
  const byId = new Map<string, EmbeddingJob>();
  for (const item of [...current, ...incoming]) {
    if (item.curriculum_version_id !== curriculumVersionId) continue;
    byId.set(item.id, preferJob(byId.get(item.id), item));
  }
  return [...byId.values()].sort((left, right) => {
    const timeOrder = right.created_at.localeCompare(left.created_at);
    return timeOrder || right.id.localeCompare(left.id);
  });
}

function reviewedCandidates(
  questions: HistoricalQuestion[],
  chunks: KnowledgeChunk[],
  curriculumVersionId: string,
): Candidate[] {
  const seen = new Set<string>();
  const candidates: Candidate[] = [];
  const append = (kind: RecordKind, record: KnowledgeRecord) => {
    if (
      record.curriculum_version_id !== curriculumVersionId ||
      record.review_state !== "reviewed" ||
      seen.has(record.id)
    ) {
      return;
    }
    seen.add(record.id);
    candidates.push({ kind, record });
  };
  questions.forEach((record) => append("questions", record));
  chunks.forEach((record) => append("chunks", record));
  return candidates;
}

function safeWorkerFailureCode(value: string | null): string {
  if (value === null) return "None";
  return knownWorkerFailureCodes.has(value) ? value : "unrecognized_failure";
}

function ErrorPanel({ error }: { error: UiError }) {
  return (
    <div className="rounded-xl border border-red-300 bg-red-50 p-4 text-red-950" role="alert">
      <h3 className="font-semibold">{error.title}</h3>
      <p className="mt-1 text-sm leading-6 text-red-900">{error.message}</p>
      <p className="mt-2 font-mono text-xs">Error code: {error.code}</p>
    </div>
  );
}

function Definition({
  label,
  mono = false,
  value,
}: {
  label: string;
  mono?: boolean;
  value: number | string;
}) {
  return (
    <div>
      <dt className="text-xs font-semibold text-slate-500">{label}</dt>
      <dd className={`mt-1 break-all text-sm text-slate-950 ${mono ? "font-mono" : ""}`}>
        {value}
      </dd>
    </div>
  );
}

function Timestamp({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-xs font-semibold text-slate-500">{label}</dt>
      <dd className="mt-1 break-all font-mono text-xs text-slate-950">
        {value ? <time dateTime={value}>{value}</time> : "Not yet"}
      </dd>
    </div>
  );
}

function CandidateRow({
  candidate,
  disabled,
  onToggle,
  role,
  selected,
}: {
  candidate: Candidate;
  disabled: boolean;
  onToggle: (candidate: Candidate) => void;
  role: Role;
  selected: boolean;
}) {
  const label = recordLabel(candidate);
  const { record } = candidate;
  return (
    <li
      aria-label={label}
      className="rounded-lg border border-slate-300 bg-white p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {role === "admin" ? (
            <label className="flex cursor-pointer items-start gap-3 text-sm font-semibold text-slate-900">
              <input
                aria-label={`Select ${label.toLowerCase()}`}
                checked={selected}
                className="mt-1 size-4 accent-slate-950"
                disabled={disabled}
                onChange={() => onToggle(candidate)}
                type="checkbox"
              />
              <span>{label}</span>
            </label>
          ) : (
            <p className="text-sm font-semibold text-slate-900">{label}</p>
          )}
          <p className="mt-2 break-all font-mono text-xs text-slate-500">{record.id}</p>
        </div>
        <Badge
          className={
            record.embedding_status === "embedded"
              ? "border-emerald-300 bg-emerald-50 text-emerald-900"
              : "border-violet-300 bg-violet-50 text-violet-900"
          }
        >
          {record.embedding_status === "embedded" ? "Embedded" : "Not embedded"}
        </Badge>
      </div>
      {record.embedding_configurations.length ? (
        <ul aria-label={`Embedding configurations for ${label}`} className="mt-3 grid gap-2">
          {record.embedding_configurations.map((configuration) => (
            <li
              className="rounded-md border border-violet-200 bg-violet-50 p-3 text-xs"
              key={configuration.id}
            >
              <p className="font-semibold text-violet-950">{embeddingLabel(configuration)}</p>
              <p className="mt-1 break-all font-mono text-violet-800">
                {configuration.config_fingerprint}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-xs text-slate-500">No persisted embedding configuration.</p>
      )}
    </li>
  );
}

function JobCard({ item }: { item: EmbeddingJob }) {
  const configuration = item.configuration;
  return (
    <article
      aria-label={`Embedding job ${item.id}`}
      className="rounded-xl border border-slate-300 bg-white p-5 shadow-sm"
      role="region"
    >
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 pb-4">
        <div>
          <p className="break-all font-mono text-xs text-slate-500">{item.id}</p>
          <p className="mt-2 text-sm font-semibold text-slate-700">Version {item.version}</p>
        </div>
        <Badge
          className={
            item.status === "succeeded"
              ? "border-emerald-300 bg-emerald-50 text-emerald-900"
              : item.status === "failed"
                ? "border-red-300 bg-red-50 text-red-900"
                : item.status === "claimed"
                  ? "border-sky-300 bg-sky-50 text-sky-900"
                  : "border-amber-300 bg-amber-50 text-amber-900"
          }
        >
          {displayStatus(item.status)}
        </Badge>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <section aria-label={`Configuration for embedding job ${item.id}`} className="rounded-lg bg-violet-50 p-4">
          <h4 className="text-sm font-semibold text-violet-950">Configuration</h4>
          <dl className="mt-3 grid gap-3">
            <Definition label="Provider" value={configuration.provider} />
            <Definition label="Model" value={configuration.model} />
            <Definition label="Dimension" value={configuration.dimension} />
            <Definition label="Version" value={configuration.version} />
            <Definition
              label="Configuration fingerprint"
              mono
              value={configuration.config_fingerprint}
            />
          </dl>
        </section>

        <section aria-label={`Counts for embedding job ${item.id}`} className="rounded-lg bg-slate-100 p-4">
          <h4 className="text-sm font-semibold text-slate-900">Record counts</h4>
          <dl className="mt-3 grid grid-cols-3 gap-3">
            <Definition label="Requested" value={item.counts.requested} />
            <Definition label="Embedded" value={item.counts.embedded} />
            <Definition label="Deduplicated" value={item.counts.deduplicated} />
          </dl>
          <p className="mt-4 text-xs font-semibold text-slate-700">
            Submission deduplicated: {item.deduplicated ? "Yes" : "No"}
          </p>
        </section>

        <section aria-label={`Lifecycle for embedding job ${item.id}`} className="rounded-lg bg-amber-50 p-4">
          <h4 className="text-sm font-semibold text-amber-950">Lifecycle</h4>
          <dl className="mt-3 grid gap-3">
            <Timestamp label="Queued at" value={item.created_at} />
            <Timestamp label="Claimed at" value={item.claimed_at} />
            <Timestamp label="Completed at" value={item.completed_at} />
            <Definition
              label="Retry of job"
              mono
              value={item.retry_of_job_id ?? "Original attempt"}
            />
            <Definition
              label="Sanitized failure code"
              mono
              value={safeWorkerFailureCode(item.failure_code)}
            />
          </dl>
        </section>
      </div>
    </article>
  );
}

export function EmbeddingIngestion({
  curriculumVersionId,
  onRecordsEmbedded,
  role,
}: {
  curriculumVersionId: string;
  onRecordsEmbedded: () => Promise<void> | void;
  role: Role;
}) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [questions, setQuestions] = useState<HistoricalQuestion[]>([]);
  const [chunks, setChunks] = useState<KnowledgeChunk[]>([]);
  const [jobs, setJobs] = useState<EmbeddingJob[]>([]);
  const [selected, setSelected] = useState<Map<string, RecordKind>>(new Map());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<UiError | null>(null);
  const [createError, setCreateError] = useState<UiError | null>(null);
  const [createBusy, setCreateBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [pollTarget, setPollTarget] = useState<PollTarget | null>(null);
  const [pollError, setPollError] = useState<PollError | null>(null);
  const [refreshSequence, setRefreshSequence] = useState(0);

  const loadRequestId = useRef(0);
  const operationRequestId = useRef(0);
  const createInFlight = useRef(false);
  const createController = useRef<AbortController | null>(null);
  const pendingIdempotency = useRef<PendingIdempotency | null>(null);
  const pollToken = useRef(0);
  const completedJobs = useRef(new Set<string>());
  const loadedCurriculum = useRef<string | null>(null);
  const callbackRef = useRef(onRecordsEmbedded);

  useEffect(() => {
    callbackRef.current = onRecordsEmbedded;
  }, [onRecordsEmbedded]);

  const candidates = useMemo(
    () => reviewedCandidates(questions, chunks, curriculumVersionId),
    [chunks, curriculumVersionId, questions],
  );
  const candidatesById = useMemo(
    () => new Map(candidates.map((candidate) => [candidate.record.id, candidate])),
    [candidates],
  );
  const selectedCount = selected.size;
  const selectionLocked = createBusy || pollTarget !== null;

  useEffect(() => {
    const requestId = ++loadRequestId.current;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      const scopeChanged = loadedCurriculum.current !== curriculumVersionId;
      if (scopeChanged) {
        loadedCurriculum.current = curriculumVersionId;
        operationRequestId.current += 1;
        createController.current?.abort();
        createController.current = null;
        createInFlight.current = false;
        pendingIdempotency.current = null;
        completedJobs.current.clear();
        setCreateBusy(false);
        setCreateError(null);
        setPollTarget(null);
        setPollError(null);
        setNotice("");
        setSelected(new Map());
        setQuestions([]);
        setChunks([]);
        setJobs([]);
      }
      setLoadError(null);

      if (!curriculumVersionId) {
        setLoading(false);
        return;
      }

      setLoading(scopeChanged);
      const load = async () => {
        try {
          const path = { curriculum_version_id: curriculumVersionId };
          const [jobResult, questionResult, chunkResult] = await Promise.all([
            api.GET("/api/v1/admin/curricula/{curriculum_version_id}/embedding-jobs", {
              params: { path, query: { limit: JOB_LIST_LIMIT, offset: 0 } },
              signal: controller.signal,
            }),
            api.GET("/api/v1/admin/curricula/{curriculum_version_id}/knowledge/questions", {
              params: {
                path,
                query: { limit: RECORD_LIST_LIMIT, offset: 0, review_state: "reviewed" },
              },
              signal: controller.signal,
            }),
            api.GET("/api/v1/admin/curricula/{curriculum_version_id}/knowledge/chunks", {
              params: {
                path,
                query: { limit: RECORD_LIST_LIMIT, offset: 0, review_state: "reviewed" },
              },
              signal: controller.signal,
            }),
          ]);
          if (requestId !== loadRequestId.current || controller.signal.aborted) return;
          const failure = [jobResult, questionResult, chunkResult].find(
            (result) => !result.response.ok,
          );
          if (failure) {
            setLoadError(apiError(failure.error, failure.response.status, "load"));
            return;
          }
          const nextQuestions = (questionResult.data ?? []).filter(
            (record) =>
              record.curriculum_version_id === curriculumVersionId &&
              record.review_state === "reviewed",
          );
          const nextChunks = (chunkResult.data ?? []).filter(
            (record) =>
              record.curriculum_version_id === curriculumVersionId &&
              record.review_state === "reviewed",
          );
          const nextCandidates = reviewedCandidates(
            nextQuestions,
            nextChunks,
            curriculumVersionId,
          );
          const nextKindsById = new Map(
            nextCandidates.map((candidate) => [candidate.record.id, candidate.kind]),
          );
          setQuestions(nextQuestions);
          setChunks(nextChunks);
          setSelected((current) =>
            new Map(
              [...current].filter(
                ([id, kind]) => nextKindsById.get(id) === kind,
              ),
            ),
          );
          setJobs((current) =>
            mergeJobs(scopeChanged ? [] : current, jobResult.data ?? [], curriculumVersionId),
          );
        } catch (error) {
          if (
            requestId === loadRequestId.current &&
            !controller.signal.aborted &&
            !isAbortError(error)
          ) {
            setLoadError(networkError("load"));
          }
        } finally {
          if (requestId === loadRequestId.current && !controller.signal.aborted) {
            setLoading(false);
          }
        }
      };
      void load();
    }, 0);

    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [api, curriculumVersionId, refreshSequence]);

  useEffect(
    () => () => {
      loadRequestId.current += 1;
      operationRequestId.current += 1;
      createController.current?.abort();
      createInFlight.current = false;
    },
    [],
  );

  const completeSucceededJob = useCallback((item: EmbeddingJob) => {
    if (completedJobs.current.has(item.id)) return;
    completedJobs.current.add(item.id);
    pendingIdempotency.current = null;
    setSelected(new Map());
    setNotice("Embedding job succeeded.");
    setRefreshSequence((current) => current + 1);
    void Promise.resolve(callbackRef.current()).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!pollTarget || pollTarget.curriculumId !== curriculumVersionId) return;
    const target = pollTarget;
    const controller = new AbortController();
    const startedAt = Date.now();
    let latest = target.job;
    let timeout: number | undefined;
    let delayIndex = 0;

    const stop = () => {
      setPollTarget((current) => (current?.token === target.token ? null : current));
    };
    const pauseForTimeout = () => {
      setPollError({
        error: {
          code: "embedding_poll_timeout",
          message:
            "Automatic monitoring reached its two-minute bound. The durable job continues independently; resume monitoring to inspect it again.",
          title: "Automatic job monitoring paused",
        },
        jobId: target.job.id,
      });
      stop();
    };
    const schedule = () => {
      const elapsed = Date.now() - startedAt;
      const remaining = POLL_MAX_DURATION_MS - elapsed;
      if (remaining <= 0) {
        pauseForTimeout();
        return;
      }
      const requestedDelay =
        POLL_DELAYS_MS[Math.min(delayIndex, POLL_DELAYS_MS.length - 1)];
      delayIndex += 1;
      timeout = window.setTimeout(() => void poll(), Math.min(requestedDelay, remaining));
    };
    const poll = async () => {
      if (Date.now() - startedAt >= POLL_MAX_DURATION_MS) {
        pauseForTimeout();
        return;
      }
      try {
        const response = await api.GET(
          "/api/v1/admin/curricula/{curriculum_version_id}/embedding-jobs/{embedding_job_id}",
          {
            params: {
              path: {
                curriculum_version_id: target.curriculumId,
                embedding_job_id: target.job.id,
              },
            },
            signal: controller.signal,
          },
        );
        if (controller.signal.aborted) return;
        if (!response.response.ok || !response.data) {
          setPollError({
            error: apiError(response.error, response.response.status, "poll"),
            jobId: target.job.id,
          });
          stop();
          return;
        }
        const next = response.data;
        if (
          next.id !== target.job.id ||
          next.curriculum_version_id !== target.curriculumId
        ) {
          setPollError({
            error: {
              code: "embedding_job_scope_mismatch",
              message:
                "Polling returned a job outside the selected curriculum or operation. Monitoring was stopped without accepting the response.",
              title: "Embedding job monitoring stopped",
            },
            jobId: target.job.id,
          });
          stop();
          return;
        }
        const preferred = preferJob(latest, next);
        if (preferred === latest && next !== latest) {
          schedule();
          return;
        }
        latest = preferred;
        setJobs((current) => mergeJobs(current, [preferred], target.curriculumId));
        setPollError(null);
        if (preferred.status === "succeeded") {
          stop();
          completeSucceededJob(preferred);
          return;
        }
        if (preferred.status === "failed") {
          pendingIdempotency.current = null;
          setNotice("Embedding job failed.");
          stop();
          return;
        }
        schedule();
      } catch (error) {
        if (controller.signal.aborted || isAbortError(error)) return;
        setPollError({ error: networkError("poll"), jobId: target.job.id });
        stop();
      }
    };

    schedule();
    return () => {
      controller.abort();
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [api, completeSucceededJob, curriculumVersionId, pollTarget]);

  function resetPendingOperationIdentity() {
    pendingIdempotency.current = null;
  }

  function toggleCandidate(candidate: Candidate) {
    if (selectionLocked) return;
    resetPendingOperationIdentity();
    setCreateError(null);
    setNotice("");
    setSelected((current) => {
      const next = new Map(current);
      if (next.has(candidate.record.id)) {
        next.delete(candidate.record.id);
      } else if (next.size < MAX_SELECTION) {
        next.set(candidate.record.id, candidate.kind);
      }
      return next;
    });
  }

  function selectMaximum() {
    if (selectionLocked) return;
    resetPendingOperationIdentity();
    setCreateError(null);
    setNotice("");
    setSelected(
      new Map(
        candidates
          .slice(0, MAX_SELECTION)
          .map((candidate) => [candidate.record.id, candidate.kind]),
      ),
    );
  }

  function clearSelection() {
    if (selectionLocked) return;
    resetPendingOperationIdentity();
    setCreateError(null);
    setNotice("");
    setSelected(new Map());
  }

  async function queueEmbeddingJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (role !== "admin" || createInFlight.current || pollTarget) return;

    const selectedCandidates = [...selected.entries()].flatMap(([id, kind]) => {
      const candidate = candidatesById.get(id);
      return candidate && candidate.kind === kind ? [candidate] : [];
    });
    if (selectedCandidates.length < 1 || selectedCandidates.length > MAX_SELECTION) {
      setCreateError({
        code: "invalid_embedding_selection",
        message: "Select 1 through 100 unique reviewed records before queueing an embedding job.",
        title: "Choose reviewed records",
      });
      return;
    }

    const body: EmbeddingJobCreateRequest = {
      historical_question_ids: selectedCandidates
        .filter((candidate) => candidate.kind === "questions")
        .map((candidate) => candidate.record.id),
      knowledge_chunk_ids: selectedCandidates
        .filter((candidate) => candidate.kind === "chunks")
        .map((candidate) => candidate.record.id),
    };
    const requestFingerprint = JSON.stringify(body);
    let idempotency = pendingIdempotency.current;
    try {
      if (!idempotency || idempotency.fingerprint !== requestFingerprint) {
        idempotency = { fingerprint: requestFingerprint, key: generatedIdempotencyKey() };
        pendingIdempotency.current = idempotency;
      }
    } catch {
      setCreateError({
        code: "secure_random_unavailable",
        message:
          "A secure bounded operation identity could not be generated. No embedding request was sent.",
        title: "Embedding operation identity unavailable",
      });
      return;
    }

    const operationId = ++operationRequestId.current;
    const controller = new AbortController();
    createController.current?.abort();
    createController.current = controller;
    createInFlight.current = true;
    setCreateBusy(true);
    setCreateError(null);
    setPollError(null);
    setNotice("");
    try {
      const response = await api.POST(
        "/api/v1/admin/curricula/{curriculum_version_id}/embedding-jobs",
        {
          body,
          params: {
            header: { "Idempotency-Key": idempotency.key },
            path: { curriculum_version_id: curriculumVersionId },
          },
          signal: controller.signal,
        },
      );
      if (operationId !== operationRequestId.current || controller.signal.aborted) return;
      if (!response.response.ok || !response.data) {
        const code = detailCode(response.error);
        if (response.response.status === 409) pendingIdempotency.current = null;
        if (response.response.status === 422) {
          pendingIdempotency.current = null;
          setSelected(new Map());
          setRefreshSequence((current) => current + 1);
        }
        setCreateError(apiError(response.error, response.response.status, "create"));
        if (code === "embedding_source_not_found") {
          setRefreshSequence((current) => current + 1);
        }
        return;
      }
      const created = response.data;
      if (created.curriculum_version_id !== curriculumVersionId) {
        pendingIdempotency.current = null;
        setCreateError({
          code: "embedding_job_scope_mismatch",
          message:
            "The create response was outside the selected curriculum. It was not accepted or monitored.",
          title: "Embedding response scope mismatch",
        });
        return;
      }
      setJobs((current) => mergeJobs(current, [created], curriculumVersionId));
      if (created.status === "succeeded") {
        completeSucceededJob(created);
      } else if (created.status === "failed") {
        pendingIdempotency.current = null;
        setNotice("Embedding job failed.");
      } else {
        setNotice(
          created.deduplicated
            ? "Existing embedding operation recovered; monitoring durable job."
            : "Embedding job queued; monitoring durable job.",
        );
        setPollTarget({
          curriculumId: curriculumVersionId,
          job: created,
          token: ++pollToken.current,
        });
      }
    } catch (error) {
      if (
        operationId === operationRequestId.current &&
        !controller.signal.aborted &&
        !isAbortError(error)
      ) {
        setCreateError(networkError("create"));
      }
    } finally {
      if (operationId === operationRequestId.current) {
        createInFlight.current = false;
        createController.current = null;
        setCreateBusy(false);
      }
    }
  }

  function resumePolling() {
    if (!pollError) return;
    const item = jobs.find((jobItem) => jobItem.id === pollError.jobId);
    if (!item || item.status === "succeeded" || item.status === "failed") return;
    setPollError(null);
    setPollTarget({
      curriculumId: curriculumVersionId,
      job: item,
      token: ++pollToken.current,
    });
  }

  return (
    <section
      aria-labelledby="embedding-ingestion-heading"
      className="mt-8 rounded-2xl border border-slate-300 bg-slate-100/70 p-5 shadow-sm sm:p-6"
    >
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-300 pb-5">
        <div>
          <p className="font-mono text-xs font-semibold tracking-[0.16em] text-violet-700 uppercase">
            Server-owned vector ingestion
          </p>
          <h2 className="mt-1 text-2xl font-semibold" id="embedding-ingestion-heading">
            Embedding ingestion
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
            Queue only reviewed record identifiers. Source text, vectors, review state, and embedding
            configuration remain server-owned and are never submitted by this browser workflow.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Badge className="border-violet-300 bg-violet-50 text-violet-950">
            {role === "admin" ? "Admin create access" : "Reviewer read-only access"}
          </Badge>
          <Button
            className={secondaryButton}
            isDisabled={loading}
            onPress={() => setRefreshSequence((current) => current + 1)}
          >
            Refresh embedding data
          </Button>
        </div>
      </header>

      {loading ? (
        <p
          className="mt-5 flex items-center gap-3 rounded-xl border border-slate-300 bg-white p-5 text-sm text-slate-600"
          role="status"
        >
          <span aria-hidden="true" className="size-2 animate-pulse rounded-full bg-violet-500" />
          Loading embedding data…
        </p>
      ) : null}

      {!loading && loadError ? (
        <div className="mt-5 grid gap-3">
          <ErrorPanel error={loadError} />
          <Button
            className={`${secondaryButton} justify-self-start`}
            onPress={() => setRefreshSequence((current) => current + 1)}
          >
            Retry embedding data
          </Button>
        </div>
      ) : null}

      {!loading && !loadError ? (
        <div className="mt-6 grid gap-8">
          <section aria-labelledby="reviewed-embedding-records-heading">
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <h3 className="text-xl font-semibold" id="reviewed-embedding-records-heading">
                  Reviewed record inventory
                </h3>
                <p className="mt-1 text-sm text-slate-600">
                  {candidates.length.toLocaleString()} unique reviewed question/chunk records loaded
                  from API-bounded lists.
                </p>
              </div>
              {role === "admin" ? (
                <p aria-live="polite" className="text-sm font-semibold text-slate-700">
                  {selectedCount} of {MAX_SELECTION} records selected
                </p>
              ) : null}
            </div>

            {!candidates.length ? (
              <div className="mt-4 rounded-xl border border-dashed border-amber-400 bg-amber-50 p-5">
                <h4 className="font-semibold text-amber-950">No reviewed records available</h4>
                <p className="mt-1 text-sm leading-6 text-amber-900">
                  Classify and mark at least one historical question or knowledge chunk reviewed
                  before queueing server-owned embeddings.
                </p>
              </div>
            ) : (
              <>
                {role === "admin" ? (
                  <div className="mt-4 flex flex-wrap gap-3">
                    <Button
                      className={secondaryButton}
                      isDisabled={selectionLocked}
                      onPress={selectMaximum}
                    >
                      Select up to 100 reviewed records
                    </Button>
                    <Button
                      className={secondaryButton}
                      isDisabled={selectionLocked || selectedCount === 0}
                      onPress={clearSelection}
                    >
                      Clear selection
                    </Button>
                  </div>
                ) : null}
                <ul className="mt-4 grid gap-3 lg:grid-cols-2">
                  {candidates.map((candidate) => (
                    <CandidateRow
                      candidate={candidate}
                      disabled={
                        selectionLocked ||
                        (!selected.has(candidate.record.id) && selectedCount >= MAX_SELECTION)
                      }
                      key={`${candidate.kind}-${candidate.record.id}`}
                      onToggle={toggleCandidate}
                      role={role}
                      selected={selected.has(candidate.record.id)}
                    />
                  ))}
                </ul>
              </>
            )}

            {role === "admin" ? (
              <Form className="mt-5 grid gap-3" onSubmit={queueEmbeddingJob}>
                {createError ? <ErrorPanel error={createError} /> : null}
                {pollError ? (
                  <div className="grid gap-3">
                    <ErrorPanel error={pollError.error} />
                    <Button
                      className={`${secondaryButton} justify-self-start`}
                      onPress={resumePolling}
                    >
                      Resume job monitoring
                    </Button>
                  </div>
                ) : null}
                {notice ? (
                  <p
                    className={
                      notice === "Embedding job failed."
                        ? "rounded-lg border border-red-300 bg-red-50 p-3 text-sm font-semibold text-red-950"
                        : "rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm font-semibold text-emerald-950"
                    }
                    role="status"
                  >
                    {notice}
                  </p>
                ) : null}
                {pollTarget ? (
                  <p className="text-sm font-medium text-slate-600" role="status">
                    Monitoring embedding job with bounded 250–4000 ms backoff for at most two
                    minutes…
                  </p>
                ) : null}
                <div className="flex flex-wrap items-center gap-3">
                  <Button
                    className={primaryButton}
                    isDisabled={
                      createBusy || pollTarget !== null || selectedCount < 1 || selectedCount > MAX_SELECTION
                    }
                    type="submit"
                  >
                    {createBusy ? "Queueing embedding job…" : "Queue selected records"}
                  </Button>
                  <p className="text-xs leading-5 text-slate-500">
                    A fresh bounded idempotency key is generated for an explicit selection and
                    preserved only while its outcome is uncertain.
                  </p>
                </div>
              </Form>
            ) : null}
          </section>

          <section aria-labelledby="embedding-jobs-heading">
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <h3 className="text-xl font-semibold" id="embedding-jobs-heading">
                  Embedding jobs
                </h3>
                <p className="mt-1 text-sm text-slate-600">
                  Durable lifecycle, active configuration, bounded counts, retry lineage, and
                  sanitized failure metadata.
                </p>
              </div>
              <p className="font-mono text-xs text-slate-500">
                Latest {JOB_LIST_LIMIT} jobs requested
              </p>
            </div>
            {!jobs.length ? (
              <p className="mt-4 rounded-xl border border-dashed border-slate-400 bg-white p-5 text-sm text-slate-600">
                No embedding jobs have been recorded for this curriculum.
              </p>
            ) : (
              <div className="mt-4 grid gap-4">
                {jobs.map((item) => (
                  <JobCard item={item} key={item.id} />
                ))}
              </div>
            )}
          </section>
        </div>
      ) : null}
    </section>
  );
}
