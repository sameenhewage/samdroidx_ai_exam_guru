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

type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type Exam = components["schemas"]["ExamConfigurationResponse"];
type Medium = components["schemas"]["MediumResponse"];
type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
type HistoricalQuestion = components["schemas"]["HistoricalQuestionResponse"];
type KnowledgeChunk = components["schemas"]["KnowledgeChunkResponse"];
type EmbeddingConfiguration =
  components["schemas"]["EmbeddingConfigurationMetadataResponse"];
type RetrievalRequest = components["schemas"]["RetrievalExploreRequest"];
type RetrievalResult = components["schemas"]["RetrievalExploreResponse"];
type RetrievalScope = components["schemas"]["RetrievalScopeResponse"];
type Provenance = components["schemas"]["RetrievalProvenanceResponse"];
type ChannelCandidate = components["schemas"]["RetrievalChannelCandidateResponse"];
type Role = "admin" | "reviewer";

type UiError = {
  code: string;
  message: string;
  title: string;
};

type EligibleCurriculum = {
  curriculum: Curriculum;
  exam: Exam;
  medium: Medium;
};

type ApiOutcome = {
  error?: unknown;
  response: Response;
};

type KnowledgeDiscovery<RecordType> = {
  capped: boolean;
  failure?: ApiOutcome;
  records: RecordType[];
};

const REVIEWED_LIST_LIMIT = 100;
const MAX_REVIEWED_DISCOVERY_RECORDS = 5_000;
const MAX_QUERY_CHARACTERS = 4_096;
const limits = {
  candidateLimit: { maximum: 100, minimum: 1 },
  contextCharacters: { maximum: 100_000, minimum: 1 },
  contextItemCharacters: { maximum: 20_000, minimum: 1 },
  contextItems: { maximum: 100, minimum: 1 },
  topK: { maximum: 100, minimum: 1 },
} as const;

const fieldClass = "grid gap-1.5 text-sm font-semibold text-slate-700";
const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 outline-none transition focus:border-amber-500 focus:ring-2 focus:ring-amber-200 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none transition hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-400 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";

function detailCode(error: unknown): string {
  if (!error || typeof error !== "object" || !("detail" in error)) return "request_failed";
  const detail = (error as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail) || !("code" in detail)) {
    return "request_failed";
  }
  return String((detail as { code: unknown }).code);
}

function uiError(error: unknown, status?: number): UiError {
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
      message: "This account cannot inspect retrieval evidence. Ask an administrator to verify retrieval:read access.",
      title: "Retrieval permission required",
    };
  }
  if (status === 503) {
    return {
      code: "embedding_provider_unavailable",
      message: "The configured server-side embedding provider is unavailable. No client vector was accepted or stored. Retry when the provider recovers.",
      title: "Embedding provider temporarily unavailable",
    };
  }

  const code = detailCode(error);
  if (code === "embedding_configuration_not_found") {
    return {
      code,
      message: "That persisted embedding configuration is no longer available. Reload reviewed knowledge metadata and choose another configuration.",
      title: "Embedding configuration not found",
    };
  }
  if (code === "retrieval_scope_not_found") {
    return {
      code,
      message: "The selected active curriculum and reviewed taxonomy path no longer form a valid retrieval scope. Reload the scope before retrying.",
      title: "Retrieval scope changed",
    };
  }
  if (code === "invalid_retrieval_request" || status === 422) {
    return {
      code: "invalid_retrieval_request",
      message: "The retrieval request was rejected as invalid. Review the taxonomy hierarchy and bounded limits.",
      title: "Invalid retrieval request",
    };
  }
  return {
    code,
    message: "The retrieval request could not be completed. Retry or contact an administrator if the failure persists.",
    title: "Retrieval request failed",
  };
}

function networkError(): UiError {
  return {
    code: "network_error",
    message: "The retrieval service could not be reached. Check the connection and retry.",
    title: "Service connection failed",
  };
}

function firstApiFailure(outcomes: readonly ApiOutcome[]): ApiOutcome | undefined {
  return outcomes.find((outcome) => outcome.error !== undefined);
}

function eligibleCurricula(
  curricula: Curriculum[],
  exams: Exam[],
  media: Medium[],
): EligibleCurriculum[] {
  const examById = new Map(exams.map((exam) => [exam.id, exam]));
  const mediumById = new Map(media.map((item) => [item.id, item]));
  return curricula.flatMap((curriculum) => {
    const exam = examById.get(curriculum.exam_configuration_id);
    const medium = mediumById.get(curriculum.medium_id);
    return curriculum.active && exam?.active && exam.grade === 5 && medium?.active
      ? [{ curriculum, exam, medium }]
      : [];
  });
}

function collectEmbeddingConfigurations(
  questions: HistoricalQuestion[],
  chunks: KnowledgeChunk[],
): EmbeddingConfiguration[] {
  const configurations = new Map<string, EmbeddingConfiguration>();
  for (const record of [...questions, ...chunks]) {
    if (record.review_state !== "reviewed" || record.embedding_status !== "embedded") continue;
    for (const configuration of record.embedding_configurations) {
      configurations.set(configuration.config_fingerprint, configuration);
    }
  }
  return [...configurations.values()].sort((left, right) =>
    `${left.provider}/${left.model}/${left.version}/${left.dimension}`.localeCompare(
      `${right.provider}/${right.model}/${right.version}/${right.dimension}`,
    ),
  );
}

async function discoverReviewedQuestions(
  api: ApiClient,
  curriculumVersionId: string,
): Promise<KnowledgeDiscovery<HistoricalQuestion>> {
  const records: HistoricalQuestion[] = [];
  for (
    let offset = 0;
    offset < MAX_REVIEWED_DISCOVERY_RECORDS;
    offset += REVIEWED_LIST_LIMIT
  ) {
    const response = await api.GET(
      "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/questions",
      {
        params: {
          path: { curriculum_version_id: curriculumVersionId },
          query: { limit: REVIEWED_LIST_LIMIT, offset, review_state: "reviewed" },
        },
      },
    );
    const failure = firstApiFailure([response]);
    if (failure) return { capped: false, failure, records };
    const batch = response.data ?? [];
    records.push(...batch);
    if (batch.length < REVIEWED_LIST_LIMIT) return { capped: false, records };
  }
  return { capped: true, records };
}

async function discoverReviewedChunks(
  api: ApiClient,
  curriculumVersionId: string,
): Promise<KnowledgeDiscovery<KnowledgeChunk>> {
  const records: KnowledgeChunk[] = [];
  for (
    let offset = 0;
    offset < MAX_REVIEWED_DISCOVERY_RECORDS;
    offset += REVIEWED_LIST_LIMIT
  ) {
    const response = await api.GET(
      "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/chunks",
      {
        params: {
          path: { curriculum_version_id: curriculumVersionId },
          query: { limit: REVIEWED_LIST_LIMIT, offset, review_state: "reviewed" },
        },
      },
    );
    const failure = firstApiFailure([response]);
    if (failure) return { capped: false, failure, records };
    const batch = response.data ?? [];
    records.push(...batch);
    if (batch.length < REVIEWED_LIST_LIMIT) return { capped: false, records };
  }
  return { capped: true, records };
}

function boundedInteger(
  value: string,
  label: string,
  minimum: number,
  maximum: number,
): { error?: string; value?: number } {
  if (!/^\d+$/.test(value.trim())) {
    return { error: `${label} must be a whole number from ${minimum} through ${maximum}.` };
  }
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    return { error: `${label} must be a whole number from ${minimum} through ${maximum}.` };
  }
  return { value: parsed };
}

function taxonomyLabel(node: TaxonomyNode): string {
  return `${node.code} — ${node.title}`;
}

function embeddingLabel(configuration: EmbeddingConfiguration): string {
  return `${configuration.provider} / ${configuration.model} / ${configuration.version} / ${configuration.dimension}d`;
}

function score(value: number): string {
  return value.toFixed(6);
}

function milliseconds(value: number): string {
  return `${value.toFixed(3)} ms`;
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
    <div className="rounded-xl border border-red-300 bg-red-50 p-4 text-red-950" role="alert">
      <p className="font-semibold">{error.title}</p>
      <p className="mt-1 text-sm leading-6 text-red-900">{error.message}</p>
      {onRetry && retryLabel ? (
        <Button className={`${secondaryButton} mt-3 border-red-300 bg-white`} onPress={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
    </div>
  );
}

function Section({
  children,
  description,
  title,
}: {
  children: ReactNode;
  description?: string;
  title: string;
}) {
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

function TrustBadge() {
  return (
    <Badge className="border-amber-300 bg-amber-50 text-amber-950">
      Untrusted source data
    </Badge>
  );
}

function ScopeIds({ scope }: { scope: RetrievalScope }) {
  const taxonomy = scope.taxonomy;
  return (
    <details className="mt-3 text-xs text-slate-600">
      <summary className="cursor-pointer font-semibold text-slate-700">Returned scope IDs</summary>
      <dl className="mt-2 grid gap-2 rounded-lg bg-slate-50 p-3 sm:grid-cols-2">
        <IdTerm label="Curriculum" value={scope.curriculum_version_id} />
        <IdTerm label="Exam" value={scope.exam_id} />
        <IdTerm label="Medium" value={scope.medium_id} />
        <IdTerm label="Competency" value={taxonomy.competency_id} />
        {taxonomy.skill_id ? <IdTerm label="Skill" value={taxonomy.skill_id} /> : null}
        {taxonomy.sub_skill_id ? <IdTerm label="Sub-skill" value={taxonomy.sub_skill_id} /> : null}
        {taxonomy.learning_concept_id ? (
          <IdTerm label="Learning concept" value={taxonomy.learning_concept_id} />
        ) : null}
      </dl>
    </details>
  );
}

function IdTerm({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="font-semibold text-slate-500">{label}</dt>
      <dd className="mt-0.5 break-all font-mono text-slate-800">{value}</dd>
    </div>
  );
}

function ProvenanceList({ items }: { items: Provenance[] }) {
  return (
    <ul aria-label="Source provenance" className="mt-3 grid gap-2">
      {items.map((item, index) => (
        <li
          className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700"
          key={`${item.source_document_id}-${item.page_number}-${item.source_block_id ?? "page"}-${index}`}
        >
          <p>
            <span className="font-semibold">Source</span>{" "}
            <span className="break-all font-mono">{item.source_document_id}</span>
          </p>
          <p className="mt-1">
            <span className="font-semibold">Page</span> {item.page_number}
            <span aria-hidden="true"> · </span>
            <span className="font-semibold">Block</span>{" "}
            {item.source_block_id ? (
              <span className="break-all font-mono">{item.source_block_id}</span>
            ) : (
              "Page-level provenance"
            )}
          </p>
        </li>
      ))}
    </ul>
  );
}

function ChannelSection({
  candidates,
  title,
}: {
  candidates: ChannelCandidate[];
  title: "Lexical channel" | "Vector channel";
}) {
  return (
    <section aria-labelledby={`${title.split(" ")[0].toLowerCase()}-heading`}>
      <div className="flex items-center justify-between gap-4">
        <h2
          className="text-lg font-semibold"
          id={`${title.split(" ")[0].toLowerCase()}-heading`}
        >
          {title}
        </h2>
        <span className="font-mono text-xs text-slate-500">{candidates.length} candidates</span>
      </div>
      {candidates.length ? (
        <ol className="mt-3 grid gap-3">
          {candidates.map((candidate) => (
            <li
              className="rounded-xl border border-slate-300 bg-white p-4 shadow-sm"
              key={`${title}-${candidate.rank}-${candidate.chunk_id}`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-mono text-xs font-semibold text-slate-600">
                  Rank {candidate.rank} · score {score(candidate.score)}
                </p>
                <TrustBadge />
              </div>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-900">
                {candidate.text}
              </p>
              <p className="mt-3 break-all font-mono text-xs text-slate-500">
                Chunk {candidate.chunk_id}
              </p>
              <ProvenanceList items={[candidate.provenance]} />
              <ScopeIds scope={candidate.scope} />
            </li>
          ))}
        </ol>
      ) : (
        <p className="mt-3 rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-600">
          No candidates returned by this channel.
        </p>
      )}
    </section>
  );
}

function Results({ result }: { result: RetrievalResult }) {
  const diagnostics = result.diagnostics;
  const latency = result.latency_ms;
  return (
    <div className="mt-8 grid gap-6">
      <section
        aria-label="Retrieval result summary"
        className="rounded-2xl border border-slate-800 bg-slate-950 p-5 text-white shadow-sm sm:p-6"
      >
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="font-mono text-xs tracking-[0.16em] text-amber-300 uppercase">
              Grounding result
            </p>
            <h2 className="mt-2 text-2xl font-semibold">{result.query}</h2>
          </div>
          <TrustBadge />
        </div>
        <dl className="mt-5 grid gap-3 sm:grid-cols-3">
          <SummaryMetric label="Fused candidates" value={String(result.fused_candidates.length)} />
          <SummaryMetric
            label="Channel candidates"
            value={`${result.channels.lexical.length} / ${result.channels.vector.length}`}
          />
          <SummaryMetric
            label="Bounded context"
            value={`${result.context.character_count} chars`}
          />
        </dl>
        {!result.fused_candidates.length ? (
          <p className="mt-5 rounded-lg border border-white/20 bg-white/5 p-4 text-sm text-slate-200">
            No matching reviewed evidence was found.
          </p>
        ) : null}
      </section>

      <div className="grid gap-6 xl:grid-cols-2">
        <ChannelSection candidates={result.channels.lexical} title="Lexical channel" />
        <ChannelSection candidates={result.channels.vector} title="Vector channel" />
      </div>

      <Section
        description="Weighted reciprocal-rank fusion with channel ranks and deduplicated source identities."
        title="Fused ranking"
      >
        {result.fused_candidates.length ? (
          <ol className="grid gap-4">
            {result.fused_candidates.map((candidate) => (
              <li
                className="rounded-xl border border-slate-300 bg-slate-50 p-4"
                key={`${candidate.rank}-${candidate.chunk_id}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="font-mono text-xs font-semibold text-slate-500">
                      Fused rank {candidate.rank} · score {score(candidate.score)}
                    </p>
                    <p className="mt-1 text-sm text-slate-700">
                      Lexical rank {candidate.lexical_rank ?? "not returned"} · Vector rank{" "}
                      {candidate.vector_rank ?? "not returned"}
                    </p>
                  </div>
                  <TrustBadge />
                </div>
                <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-950">
                  {candidate.text}
                </p>
                <div className="mt-4 rounded-lg border border-slate-200 bg-white p-3">
                  <p className="text-xs font-semibold text-slate-600">
                    {candidate.source_chunk_ids.length}{" "}
                    {candidate.source_chunk_ids.length === 1
                      ? "source chunk ID"
                      : "source chunk IDs"}
                  </p>
                  <ul className="mt-2 grid gap-1 font-mono text-xs text-slate-700">
                    {candidate.source_chunk_ids.map((id) => (
                      <li className="break-all" key={id}>
                        {id}
                      </li>
                    ))}
                  </ul>
                </div>
                <ProvenanceList items={candidate.provenances} />
                <ScopeIds scope={candidate.scope} />
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-slate-600">No candidates were eligible for fusion.</p>
        )}
      </Section>

      <Section
        description={`Server-enforced limit: ${result.context.limits.max_items} items, ${result.context.limits.max_total_characters.toLocaleString()} total characters, ${result.context.limits.max_item_characters.toLocaleString()} per item.`}
        title="Bounded context"
      >
        <div className="mb-4 flex flex-wrap gap-3 text-sm text-slate-600">
          <span>{result.context.character_count.toLocaleString()} characters returned</span>
          <span aria-hidden="true">·</span>
          <span>{result.context.omitted_candidate_count} candidates omitted by bounds</span>
        </div>
        {result.context.items.length ? (
          <ol className="grid gap-4">
            {result.context.items.map((item) => (
              <li
                className="rounded-xl border border-amber-200 bg-amber-50/50 p-4"
                key={`${item.rank}-${item.source_chunk_ids.join("-")}`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs font-semibold text-slate-600">
                    Context rank {item.rank} · fusion {score(item.fusion_score)}
                  </span>
                  <TrustBadge />
                  {item.truncated ? (
                    <Badge className="border-red-300 bg-red-50 text-red-900">Truncated</Badge>
                  ) : (
                    <Badge>Complete text</Badge>
                  )}
                </div>
                <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-950">
                  {item.text}
                </p>
                <p className="mt-3 text-xs text-slate-600">
                  Original source length: {item.original_character_count.toLocaleString()} characters
                </p>
                <ul className="mt-2 grid gap-1 font-mono text-xs text-slate-600">
                  {item.source_chunk_ids.map((id) => (
                    <li className="break-all" key={id}>
                      {id}
                    </li>
                  ))}
                </ul>
                <ProvenanceList items={item.provenances} />
                <ScopeIds scope={item.scope} />
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-slate-600">No source text entered the bounded context.</p>
        )}
      </Section>

      <div className="grid gap-6 xl:grid-cols-2">
        <Section title="Retrieval diagnostics">
          <dl className="grid gap-3 sm:grid-cols-2">
            <Diagnostic
              label="Hard scope filter applied"
              value={diagnostics.hard_scope_filter_applied ? "Yes" : "No"}
            />
            <Diagnostic label="Lexical candidates" value={diagnostics.lexical_candidate_count} />
            <Diagnostic label="Vector candidates" value={diagnostics.vector_candidate_count} />
            <Diagnostic label="Filtered out" value={diagnostics.filtered_out_candidate_count} />
            <Diagnostic label="Fused candidates" value={diagnostics.fused_candidate_count} />
            <Diagnostic
              label="Deduplicated sources"
              value={diagnostics.deduplicated_source_count}
            />
            <Diagnostic label="Context items" value={diagnostics.context_item_count} />
            <Diagnostic
              label="Context characters"
              value={diagnostics.context_character_count.toLocaleString()}
            />
            <Diagnostic
              label="Omitted fused candidates"
              value={diagnostics.omitted_fused_candidate_count}
            />
          </dl>
        </Section>

        <Section title="Phase latency">
          <dl className="grid gap-3 sm:grid-cols-2">
            <Diagnostic label="Validation" value={milliseconds(latency.validation_ms)} />
            <Diagnostic label="Server-side embedding" value={milliseconds(latency.embedding_ms)} />
            <Diagnostic
              label="Candidate retrieval"
              value={milliseconds(latency.candidate_retrieval_ms)}
            />
            <Diagnostic label="Fusion" value={milliseconds(latency.fusion_ms)} />
            <Diagnostic
              label="Context building"
              value={milliseconds(latency.context_building_ms)}
            />
            <Diagnostic label="Total" value={milliseconds(latency.total_ms)} />
          </dl>
        </Section>
      </div>
    </div>
  );
}

function SummaryMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/15 bg-white/5 p-3">
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className="mt-1 font-mono text-lg font-semibold text-white">{value}</dd>
    </div>
  );
}

function Diagnostic({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
      <dt className="text-xs font-semibold text-slate-500">{label}</dt>
      <dd className="mt-1 font-mono text-sm text-slate-950">{value}</dd>
    </div>
  );
}

export function RetrievalExplorer({ role }: { role: Role }) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [exams, setExams] = useState<Exam[]>([]);
  const [media, setMedia] = useState<Medium[]>([]);
  const [curricula, setCurricula] = useState<Curriculum[]>([]);
  const [selectedCurriculumId, setSelectedCurriculumId] = useState("");
  const [taxonomy, setTaxonomy] = useState<TaxonomyNode[]>([]);
  const [embeddingConfigurations, setEmbeddingConfigurations] = useState<
    EmbeddingConfiguration[]
  >([]);
  const [selectedEmbeddingFingerprint, setSelectedEmbeddingFingerprint] = useState("");
  const [reviewedRecordCount, setReviewedRecordCount] = useState(0);
  const [embeddingDiscoveryCapped, setEmbeddingDiscoveryCapped] = useState(false);
  const [competencyId, setCompetencyId] = useState("");
  const [skillId, setSkillId] = useState("");
  const [subSkillId, setSubSkillId] = useState("");
  const [learningConceptId, setLearningConceptId] = useState("");
  const [query, setQuery] = useState("");
  const [candidateLimit, setCandidateLimit] = useState("20");
  const [topK, setTopK] = useState("10");
  const [contextItems, setContextItems] = useState("5");
  const [contextCharacters, setContextCharacters] = useState("6000");
  const [contextItemCharacters, setContextItemCharacters] = useState("1800");
  const [workspaceLoading, setWorkspaceLoading] = useState(true);
  const [scopeLoading, setScopeLoading] = useState(false);
  const [retrievalLoading, setRetrievalLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<UiError | null>(null);
  const [scopeError, setScopeError] = useState<UiError | null>(null);
  const [retrievalError, setRetrievalError] = useState<UiError | null>(null);
  const [formError, setFormError] = useState("");
  const [result, setResult] = useState<RetrievalResult | null>(null);
  const [lastRequest, setLastRequest] = useState<RetrievalRequest | null>(null);
  const workspaceRequestId = useRef(0);
  const scopeRequestId = useRef(0);
  const retrievalRequestId = useRef(0);

  const eligible = useMemo(
    () => eligibleCurricula(curricula, exams, media),
    [curricula, exams, media],
  );
  const selectedScope = useMemo(
    () => eligible.find((item) => item.curriculum.id === selectedCurriculumId) ?? null,
    [eligible, selectedCurriculumId],
  );
  const reviewedTaxonomy = useMemo(
    () => taxonomy.filter((node) => node.active && node.review_state === "reviewed"),
    [taxonomy],
  );
  const competencies = useMemo(
    () =>
      reviewedTaxonomy.filter(
        (node) => node.level === "competency" && node.parent_id === null,
      ),
    [reviewedTaxonomy],
  );
  const skills = useMemo(
    () =>
      reviewedTaxonomy.filter(
        (node) => node.level === "skill" && node.parent_id === competencyId,
      ),
    [reviewedTaxonomy, competencyId],
  );
  const subSkills = useMemo(
    () =>
      reviewedTaxonomy.filter(
        (node) => node.level === "sub_skill" && node.parent_id === skillId,
      ),
    [reviewedTaxonomy, skillId],
  );
  const learningConcepts = useMemo(
    () =>
      reviewedTaxonomy.filter(
        (node) => node.level === "learning_concept" && node.parent_id === subSkillId,
      ),
    [reviewedTaxonomy, subSkillId],
  );
  const selectedEmbedding = useMemo(
    () =>
      embeddingConfigurations.find(
        (configuration) =>
          configuration.config_fingerprint === selectedEmbeddingFingerprint,
      ) ?? null,
    [embeddingConfigurations, selectedEmbeddingFingerprint],
  );

  const loadWorkspace = useCallback(async () => {
    const requestId = ++workspaceRequestId.current;
    setWorkspaceLoading(true);
    setWorkspaceError(null);
    try {
      const [examResult, mediumResult, curriculumResult] = await Promise.all([
        api.GET("/api/v1/admin/exam-configurations"),
        api.GET("/api/v1/admin/media"),
        api.GET("/api/v1/admin/curriculum-versions"),
      ]);
      if (requestId !== workspaceRequestId.current) return;
      const failed = firstApiFailure([examResult, mediumResult, curriculumResult]);
      if (failed?.error) {
        setWorkspaceError(uiError(failed.error, failed.response.status));
        return;
      }
      const nextExams = examResult.data ?? [];
      const nextMedia = mediumResult.data ?? [];
      const nextCurricula = curriculumResult.data ?? [];
      const nextEligible = eligibleCurricula(nextCurricula, nextExams, nextMedia);
      setExams(nextExams);
      setMedia(nextMedia);
      setCurricula(nextCurricula);
      setTaxonomy([]);
      setEmbeddingConfigurations([]);
      setEmbeddingDiscoveryCapped(false);
      setScopeLoading(nextEligible.length > 0);
      setSelectedCurriculumId((current) =>
        nextEligible.some((item) => item.curriculum.id === current)
          ? current
          : (nextEligible[0]?.curriculum.id ?? ""),
      );
    } catch {
      if (requestId === workspaceRequestId.current) setWorkspaceError(networkError());
    } finally {
      if (requestId === workspaceRequestId.current) setWorkspaceLoading(false);
    }
  }, [api]);

  const loadScope = useCallback(
    async (curriculumVersionId: string) => {
      const requestId = ++scopeRequestId.current;
      setScopeLoading(true);
      setScopeError(null);
      setResult(null);
      setRetrievalError(null);
      try {
        const path = { curriculum_version_id: curriculumVersionId };
        const [taxonomyResult, questionDiscovery, chunkDiscovery] = await Promise.all([
          api.GET("/api/v1/admin/curricula/{curriculum_version_id}/taxonomy/nodes", {
            params: { path },
          }),
          discoverReviewedQuestions(api, curriculumVersionId),
          discoverReviewedChunks(api, curriculumVersionId),
        ]);
        if (requestId !== scopeRequestId.current) return;
        const failed =
          firstApiFailure([taxonomyResult]) ??
          questionDiscovery.failure ??
          chunkDiscovery.failure;
        if (failed?.error) {
          setScopeError(uiError(failed.error, failed.response.status));
          return;
        }
        const nextTaxonomy = taxonomyResult.data ?? [];
        const nextQuestions = questionDiscovery.records;
        const nextChunks = chunkDiscovery.records;
        const nextConfigurations = collectEmbeddingConfigurations(nextQuestions, nextChunks);
        const firstCompetency = nextTaxonomy.find(
          (node) =>
            node.active &&
            node.review_state === "reviewed" &&
            node.level === "competency" &&
            node.parent_id === null,
        );
        setTaxonomy(nextTaxonomy);
        setEmbeddingConfigurations(nextConfigurations);
        setSelectedEmbeddingFingerprint(
          (current) =>
            nextConfigurations.find(
              (configuration) => configuration.config_fingerprint === current,
            )?.config_fingerprint ?? nextConfigurations[0]?.config_fingerprint ?? "",
        );
        setReviewedRecordCount(nextQuestions.length + nextChunks.length);
        setEmbeddingDiscoveryCapped(questionDiscovery.capped || chunkDiscovery.capped);
        setCompetencyId(firstCompetency?.id ?? "");
        setSkillId("");
        setSubSkillId("");
        setLearningConceptId("");
      } catch {
        if (requestId === scopeRequestId.current) setScopeError(networkError());
      } finally {
        if (requestId === scopeRequestId.current) setScopeLoading(false);
      }
    },
    [api],
  );

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadWorkspace(), 0);
    return () => window.clearTimeout(timeout);
  }, [loadWorkspace]);

  useEffect(() => {
    if (!selectedCurriculumId) return;
    const timeout = window.setTimeout(() => void loadScope(selectedCurriculumId), 0);
    return () => window.clearTimeout(timeout);
  }, [loadScope, selectedCurriculumId]);

  const executeRetrieval = useCallback(
    async (request: RetrievalRequest) => {
      const requestId = ++retrievalRequestId.current;
      setRetrievalLoading(true);
      setRetrievalError(null);
      setFormError("");
      setLastRequest(request);
      try {
        const response = await api.POST("/api/v1/admin/retrieval/explore", {
          body: request,
        });
        if (requestId !== retrievalRequestId.current) return;
        if (response.error) {
          setRetrievalError(uiError(response.error, response.response.status));
          return;
        }
        setResult(response.data ?? null);
      } catch {
        if (requestId === retrievalRequestId.current) setRetrievalError(networkError());
      } finally {
        if (requestId === retrievalRequestId.current) setRetrievalLoading(false);
      }
    },
    [api],
  );

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedScope || !competencyId || !selectedEmbedding) {
      setFormError("Choose a complete active retrieval scope and persisted embedding configuration.");
      return;
    }
    const normalizedQuery = query.trim();
    if (!normalizedQuery || Array.from(normalizedQuery).length > MAX_QUERY_CHARACTERS) {
      setFormError(`Retrieval query must contain 1 through ${MAX_QUERY_CHARACTERS.toLocaleString()} characters.`);
      return;
    }
    const parsedCandidateLimit = boundedInteger(
      candidateLimit,
      "Candidate limit",
      limits.candidateLimit.minimum,
      limits.candidateLimit.maximum,
    );
    const parsedTopK = boundedInteger(
      topK,
      "Fused result limit",
      limits.topK.minimum,
      limits.topK.maximum,
    );
    const parsedContextItems = boundedInteger(
      contextItems,
      "Context item limit",
      limits.contextItems.minimum,
      limits.contextItems.maximum,
    );
    const parsedContextCharacters = boundedInteger(
      contextCharacters,
      "Total context character limit",
      limits.contextCharacters.minimum,
      limits.contextCharacters.maximum,
    );
    const parsedContextItemCharacters = boundedInteger(
      contextItemCharacters,
      "Per-item character limit",
      limits.contextItemCharacters.minimum,
      limits.contextItemCharacters.maximum,
    );
    const firstError = [
      parsedCandidateLimit,
      parsedTopK,
      parsedContextItems,
      parsedContextCharacters,
      parsedContextItemCharacters,
    ].find((item) => item.error)?.error;
    if (firstError) {
      setFormError(firstError);
      return;
    }
    if ((parsedTopK.value ?? 0) > (parsedCandidateLimit.value ?? 0)) {
      setFormError("Fused result limit cannot exceed candidate limit.");
      return;
    }
    if ((parsedContextItems.value ?? 0) > (parsedTopK.value ?? 0)) {
      setFormError("Context item limit cannot exceed fused result limit.");
      return;
    }
    if ((parsedContextItemCharacters.value ?? 0) > (parsedContextCharacters.value ?? 0)) {
      setFormError("Per-item character limit cannot exceed total context character limit.");
      return;
    }

    void executeRetrieval({
      embedding_config: {
        config_fingerprint: selectedEmbedding.config_fingerprint,
        dimension: selectedEmbedding.dimension,
        model: selectedEmbedding.model,
        provider: selectedEmbedding.provider,
        version: selectedEmbedding.version,
      },
      limits: {
        candidate_limit: parsedCandidateLimit.value as number,
        max_context_characters: parsedContextCharacters.value as number,
        max_context_item_characters: parsedContextItemCharacters.value as number,
        max_context_items: parsedContextItems.value as number,
        top_k: parsedTopK.value as number,
      },
      query: normalizedQuery,
      scope: {
        curriculum_version_id: selectedScope.curriculum.id,
        exam_id: selectedScope.exam.id,
        grade: 5,
        medium_id: selectedScope.medium.id,
        taxonomy: {
          competency_id: competencyId,
          learning_concept_id: learningConceptId || null,
          skill_id: skillId || null,
          sub_skill_id: subSkillId || null,
        },
      },
    });
  }

  if (workspaceLoading) {
    return (
      <div className="mx-auto max-w-7xl px-5 py-12 sm:px-8" role="status">
        <p className="font-semibold">Loading RAG Explorer…</p>
        <p className="mt-2 text-sm text-slate-600">
          Resolving active Grade 5 curriculum, exam, medium, and reviewed knowledge metadata.
        </p>
      </div>
    );
  }

  if (workspaceError) {
    return (
      <div className="mx-auto max-w-3xl px-5 py-12 sm:px-8">
        <ErrorPanel
          error={workspaceError}
          onRetry={() => void loadWorkspace()}
          retryLabel="Retry workspace"
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8">
      <header className="border-b border-slate-300 pb-7">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div>
            <p className="font-mono text-xs tracking-[0.18em] text-slate-500 uppercase">
              P4 / Grounding inspection
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">RAG Explorer</h1>
            <p className="mt-3 max-w-3xl leading-7 text-slate-600">
              Inspect hard-scoped lexical and semantic retrieval, deterministic fusion, bounded
              untrusted context, source provenance, and phase latency. Query embeddings are created
              only by the configured server-side provider.
            </p>
          </div>
          <Badge className="border-slate-300 bg-white text-slate-700">
            {role === "reviewer" ? "Reviewer read access" : "Admin read access"}
          </Badge>
        </div>
      </header>

      {!eligible.length ? (
        <section className="mt-8 rounded-2xl border border-amber-300 bg-amber-50 p-6">
          <h2 className="text-xl font-semibold text-amber-950">No active retrieval scope</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-amber-900">
            Retrieval requires an active Grade 5 exam, active medium, and active curriculum version.
            Configure that scope before exploring reviewed evidence.
          </p>
          <Link
            className={`${secondaryButton} mt-4 border-amber-300`}
            href="/admin/curriculum"
          >
            Configure curriculum scope
          </Link>
        </section>
      ) : (
        <div className="mt-8 grid gap-6 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)]">
          <aside className="grid content-start gap-6">
            <Section
              description="Only active Grade 5 curricula linked to an active exam and medium are selectable."
              title="Hard metadata scope"
            >
              <label className={fieldClass}>
                Active retrieval curriculum
                <select
                  className={inputClass}
                  onChange={(event) => {
                    setTaxonomy([]);
                    setEmbeddingConfigurations([]);
                    setEmbeddingDiscoveryCapped(false);
                    setResult(null);
                    setScopeLoading(true);
                    setSelectedCurriculumId(event.target.value);
                  }}
                  value={selectedCurriculumId}
                >
                  {eligible.map((item) => (
                    <option key={item.curriculum.id} value={item.curriculum.id}>
                      {item.curriculum.title}
                    </option>
                  ))}
                </select>
              </label>
              {selectedScope ? (
                <dl className="mt-4 grid gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                  <div>
                    <dt className="text-xs font-semibold text-slate-500">Exam</dt>
                    <dd className="mt-1 text-sm font-medium">{selectedScope.exam.name}</dd>
                    <dd className="mt-0.5 font-mono text-xs text-slate-500">
                      {selectedScope.exam.code} · Grade {selectedScope.exam.grade}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs font-semibold text-slate-500">Medium</dt>
                    <dd className="mt-1 text-sm font-medium">{selectedScope.medium.name}</dd>
                    <dd className="mt-0.5 font-mono text-xs text-slate-500">
                      {selectedScope.medium.code}
                    </dd>
                  </div>
                </dl>
              ) : null}

              {scopeLoading ? (
                <p className="mt-5 text-sm font-medium text-slate-600" role="status">
                  Loading reviewed taxonomy and embedding metadata…
                </p>
              ) : null}
              {scopeError ? (
                <div className="mt-5">
                  <ErrorPanel
                    error={scopeError}
                    onRetry={() => void loadScope(selectedCurriculumId)}
                    retryLabel="Retry scope data"
                  />
                </div>
              ) : null}

              {!scopeLoading && !scopeError && !competencies.length ? (
                <div className="mt-5 rounded-xl border border-amber-300 bg-amber-50 p-4">
                  <h3 className="font-semibold text-amber-950">No reviewed competency available</h3>
                  <p className="mt-1 text-sm leading-6 text-amber-900">
                    Review and activate at least one competency before running hard-scoped retrieval.
                  </p>
                  <Link
                    className="mt-2 inline-flex text-sm font-semibold text-amber-950 underline"
                    href="/admin/curriculum"
                  >
                    Review taxonomy
                  </Link>
                </div>
              ) : null}

              {!scopeLoading && !scopeError && competencies.length ? (
                <div className="mt-5 grid gap-4">
                  <label className={fieldClass}>
                    Competency
                    <select
                      className={inputClass}
                      onChange={(event) => {
                        setCompetencyId(event.target.value);
                        setSkillId("");
                        setSubSkillId("");
                        setLearningConceptId("");
                      }}
                      value={competencyId}
                    >
                      {competencies.map((node) => (
                        <option key={node.id} value={node.id}>
                          {taxonomyLabel(node)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className={fieldClass}>
                    Skill (optional)
                    <select
                      className={inputClass}
                      onChange={(event) => {
                        setSkillId(event.target.value);
                        setSubSkillId("");
                        setLearningConceptId("");
                      }}
                      value={skillId}
                    >
                      <option value="">All reviewed skills in competency</option>
                      {skills.map((node) => (
                        <option key={node.id} value={node.id}>
                          {taxonomyLabel(node)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className={fieldClass}>
                    Sub-skill (optional)
                    <select
                      className={inputClass}
                      disabled={!skillId}
                      onChange={(event) => {
                        setSubSkillId(event.target.value);
                        setLearningConceptId("");
                      }}
                      value={subSkillId}
                    >
                      <option value="">All reviewed sub-skills in skill</option>
                      {subSkills.map((node) => (
                        <option key={node.id} value={node.id}>
                          {taxonomyLabel(node)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className={fieldClass}>
                    Learning concept (optional)
                    <select
                      className={inputClass}
                      disabled={!subSkillId}
                      onChange={(event) => setLearningConceptId(event.target.value)}
                      value={learningConceptId}
                    >
                      <option value="">All reviewed concepts in sub-skill</option>
                      {learningConcepts.map((node) => (
                        <option key={node.id} value={node.id}>
                          {taxonomyLabel(node)}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              ) : null}
            </Section>

            <Section
              description={`Scanned ${reviewedRecordCount.toLocaleString()} reviewed question/chunk records in API-bounded pages of ${REVIEWED_LIST_LIMIT}.`}
              title="Persisted embedding space"
            >
              {embeddingDiscoveryCapped ? (
                <p className="mb-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm leading-6 text-amber-900">
                  Metadata discovery reached the safety cap of{" "}
                  {MAX_REVIEWED_DISCOVERY_RECORDS.toLocaleString()} records per knowledge list.
                  Configurations found within that bounded scan remain selectable.
                </p>
              ) : null}
              {!scopeLoading && !scopeError && !embeddingConfigurations.length ? (
                <div className="rounded-xl border border-amber-300 bg-amber-50 p-4">
                  <h3 className="font-semibold text-amber-950">
                    No persisted embeddings available
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-amber-900">
                    Review knowledge records and run the configured embedding ingestion workflow.
                    The explorer accepts persisted configuration metadata only; it never accepts a
                    vector from the browser.
                  </p>
                  <Link
                    className="mt-3 inline-flex text-sm font-semibold text-amber-950 underline"
                    href="/admin/knowledge"
                  >
                    Review knowledge records
                  </Link>
                </div>
              ) : null}
              {embeddingConfigurations.length ? (
                <>
                  <label className={fieldClass}>
                    Embedding configuration
                    <select
                      className={inputClass}
                      onChange={(event) => setSelectedEmbeddingFingerprint(event.target.value)}
                      value={selectedEmbeddingFingerprint}
                    >
                      {embeddingConfigurations.map((configuration) => (
                        <option
                          key={configuration.id}
                          value={configuration.config_fingerprint}
                        >
                          {embeddingLabel(configuration)}
                        </option>
                      ))}
                    </select>
                  </label>
                  {selectedEmbedding ? (
                    <dl className="mt-4 grid gap-2 rounded-xl border border-slate-200 bg-slate-50 p-4 text-xs">
                      <IdTerm label="Fingerprint" value={selectedEmbedding.config_fingerprint} />
                      <IdTerm label="Persisted configuration ID" value={selectedEmbedding.id} />
                    </dl>
                  ) : null}
                </>
              ) : null}
            </Section>
          </aside>

          <div>
            <Section
              description="Limits are enforced in the browser for feedback and again by the API. Source text remains opaque untrusted data."
              title="Retrieval request"
            >
              <Form className="grid gap-5" onSubmit={submit}>
                <label className={fieldClass}>
                  Retrieval query
                  <textarea
                    aria-label="Retrieval query"
                    className={`${inputClass} min-h-28 resize-y`}
                    maxLength={MAX_QUERY_CHARACTERS}
                    name="query"
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Describe the curriculum evidence needed for a blueprint slot."
                    required
                    value={query}
                  />
                  <span className="font-normal text-slate-500">
                    {Array.from(query).length.toLocaleString()} /{" "}
                    {MAX_QUERY_CHARACTERS.toLocaleString()} characters
                  </span>
                </label>

                <fieldset className="grid gap-4 rounded-xl border border-slate-300 p-4 sm:grid-cols-2">
                  <legend className="px-2 text-sm font-semibold text-slate-800">
                    Candidate and context bounds
                  </legend>
                  <NumberField
                    label="Candidate limit"
                    maximum={limits.candidateLimit.maximum}
                    minimum={limits.candidateLimit.minimum}
                    onChange={setCandidateLimit}
                    value={candidateLimit}
                  />
                  <NumberField
                    label="Fused result limit"
                    maximum={limits.topK.maximum}
                    minimum={limits.topK.minimum}
                    onChange={setTopK}
                    value={topK}
                  />
                  <NumberField
                    label="Context item limit"
                    maximum={limits.contextItems.maximum}
                    minimum={limits.contextItems.minimum}
                    onChange={setContextItems}
                    value={contextItems}
                  />
                  <NumberField
                    label="Total context character limit"
                    maximum={limits.contextCharacters.maximum}
                    minimum={limits.contextCharacters.minimum}
                    onChange={setContextCharacters}
                    value={contextCharacters}
                  />
                  <NumberField
                    label="Per-item character limit"
                    maximum={limits.contextItemCharacters.maximum}
                    minimum={limits.contextItemCharacters.minimum}
                    onChange={setContextItemCharacters}
                    value={contextItemCharacters}
                  />
                </fieldset>

                {formError ? (
                  <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900" role="alert">
                    {formError}
                  </p>
                ) : null}
                {retrievalError ? (
                  <ErrorPanel
                    error={retrievalError}
                    onRetry={
                      lastRequest ? () => void executeRetrieval(lastRequest) : undefined
                    }
                    retryLabel={lastRequest ? "Retry retrieval" : undefined}
                  />
                ) : null}
                {retrievalLoading ? (
                  <p className="text-sm font-medium text-slate-600" role="status">
                    Running hard-scoped hybrid retrieval…
                  </p>
                ) : null}

                <div className="flex flex-wrap items-center gap-3">
                  <Button
                    className={primaryButton}
                    isDisabled={
                      retrievalLoading ||
                      scopeLoading ||
                      !competencyId ||
                      !selectedEmbedding
                    }
                    type="submit"
                  >
                    {retrievalLoading ? "Running retrieval…" : "Run retrieval"}
                  </Button>
                  <p className="text-xs leading-5 text-slate-500">
                    The request contains query text, IDs, persisted embedding metadata, and limits—
                    never a client-supplied vector.
                  </p>
                </div>
              </Form>
            </Section>
          </div>
        </div>
      )}

      {result ? <Results result={result} /> : null}
    </div>
  );
}

function NumberField({
  label,
  maximum,
  minimum,
  onChange,
  value,
}: {
  label: string;
  maximum: number;
  minimum: number;
  onChange: (value: string) => void;
  value: string;
}) {
  return (
    <label className={fieldClass}>
      {label}
      <input
        className={inputClass}
        inputMode="numeric"
        max={maximum}
        min={minimum}
        onChange={(event) => onChange(event.target.value)}
        required
        step={1}
        type="number"
        value={value}
      />
    </label>
  );
}
