"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Button, Form } from "react-aria-components";

import { Badge } from "@/components/ui/badge";

type Exam = components["schemas"]["ExamConfigurationResponse"];
type Medium = components["schemas"]["MediumResponse"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type Generation = components["schemas"]["GenerationRunSummaryResponse"];
type Report = components["schemas"]["ValidationRunResponse"];
type ReportSummary = components["schemas"]["ValidationRunSummaryResponse"];
type Finding = components["schemas"]["ValidationFindingResponse"];
type ValidationRequest = components["schemas"]["ValidationRunCreateRequest"];
type Role = "admin" | "reviewer";
type JsonObject = Record<string, unknown>;
type UiError = { code: string; message: string; title: string };
type ApiOutcome = { error?: unknown; response: Response };

const LIST_LIMIT = 100;
const FINDINGS_PAGE_SIZE = 10;
const MAX_DISPLAY_TEXT = 1_024;
const MAX_DISPLAY_RECORDS = 64;
const fieldClass = "grid gap-1.5 text-sm font-semibold text-slate-700";
const inputClass = "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 outline-none focus:border-amber-500 focus:ring-2 focus:ring-amber-200 disabled:bg-slate-100";
const primaryButton = "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton = "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function objectArray(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.slice(0, MAX_DISPLAY_RECORDS).flatMap((item) => {
        const object = asObject(item);
        return object ? [object] : [];
      })
    : [];
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
  const sanitized = text.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "�");
  return sanitized.length > MAX_DISPLAY_TEXT
    ? `${sanitized.slice(0, MAX_DISPLAY_TEXT)}…`
    : sanitized;
}

function displayDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? safeText(value)
    : new Intl.DateTimeFormat("en", {
        dateStyle: "medium",
        timeStyle: "medium",
        timeZone: "UTC",
      }).format(date);
}

function titleCase(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function detailCode(error: unknown): string {
  const detail = asObject(asObject(error)?.detail);
  return typeof detail?.code === "string" ? detail.code : "request_failed";
}

function apiError(error: unknown, status: number, action: "workspace" | "data" | "report" | "create"): UiError {
  const code = detailCode(error);
  if (status === 401) {
    return { code: "authentication_required", message: "Your session expired. Sign in again before retrying.", title: "Authentication required" };
  }
  if (status === 403) {
    return {
      code: "permission_denied",
      message: action === "create" ? "Only an administrator with validation:run permission can start validation." : "This account cannot read validation records. Ask an administrator to verify validation:read access.",
      title: action === "workspace" ? "Validation workspace permission required" : action === "create" ? "Validation run permission required" : "Validation read permission required",
    };
  }
  if (status === 409) {
    const messages: Record<string, string> = {
      validation_generation_integrity_invalid: "The persisted generation could not be reconstructed canonically. No report was created.",
      validation_generation_not_succeeded: "The selected generation is not currently succeeded. Reload authoritative generation state.",
      validation_idempotency_conflict: "An immutable validation identity conflicts with persisted data. Reload before retrying.",
      validation_pipeline_version_conflict: "The canonical pipeline version conflicts with the stored validation identity.",
      validation_persistence_conflict: "The immutable report could not be persisted consistently. Reload before retrying.",
    };
    return { code, message: messages[code] ?? "Authoritative validation state changed. Reload before retrying.", title: "Validation state conflict" };
  }
  if (status === 404) {
    return { code, message: "The selected curriculum, generation, or immutable report is no longer available in this scope.", title: "Validation resource not found" };
  }
  if (status === 422) {
    return { code, message: "The server rejected validation because a deterministic resource bound was exceeded.", title: "Validation resource limit reached" };
  }
  return { code, message: "The validation request could not be completed. Retry or contact an administrator if it persists.", title: action === "create" ? "Validation request failed" : "Validation data unavailable" };
}

function networkError(action: "workspace" | "data" | "report" | "create"): UiError {
  return { code: "network_error", message: "The API could not be reached. Check the connection and retry.", title: action === "create" ? "Validation connection failed" : "Validation data unavailable" };
}

function reportSummary(value: Report): ReportSummary {
  return {
    candidate_fingerprint: value.candidate_fingerprint,
    created_at: value.created_at,
    created_by: value.created_by,
    curriculum_version_id: value.curriculum_version_id,
    deduplicated: value.deduplicated,
    duplicate_reference_count: value.duplicate_reference_count,
    finding_count: value.finding_count,
    generation_attempt_id: value.generation_attempt_id,
    generation_result_fingerprint: value.generation_result_fingerprint,
    generation_run_id: value.generation_run_id,
    grounding_source_count: value.grounding_source_count,
    id: value.id,
    input_fingerprint: value.input_fingerprint,
    overall_status: value.overall_status,
    pipeline_fingerprint: value.pipeline_fingerprint,
    pipeline_version: value.pipeline_version,
    report_fingerprint: value.report_fingerprint,
    validator_count: value.validator_count,
  };
}

function eligibleGeneration(value: Generation): boolean {
  return value.status === "succeeded" && value.disposition === "requires_validation";
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

function ErrorPanel({ error, onRetry }: { error: UiError; onRetry?: () => void }) {
  return (
    <section className="rounded-xl border border-red-300 bg-red-50 p-4 text-red-950" role="alert">
      <h2 className="font-semibold">{error.title}</h2>
      <p className="mt-1 text-sm leading-6">{error.message}</p>
      <p className="mt-2 font-mono text-xs">Code: {error.code}</p>
      {onRetry ? <Button className={`${secondaryButton} mt-3`} onPress={onRetry}>Retry</Button> : null}
    </section>
  );
}

function StatusBadge({ status }: { status: "pass" | "warn" | "fail" }) {
  const classes = status === "pass" ? "border-emerald-300 bg-emerald-50 text-emerald-900" : status === "warn" ? "border-amber-300 bg-amber-50 text-amber-950" : "border-red-300 bg-red-50 text-red-950";
  return <Badge className={classes}>{titleCase(status)}</Badge>;
}

function Definition({ label, value, mono = false }: { label: string; mono?: boolean; value: ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <dt className="text-xs font-semibold text-slate-500">{label}</dt>
      <dd className={`mt-1 break-words text-sm ${mono ? "font-mono text-xs" : ""}`}>{value}</dd>
    </div>
  );
}

function SnapshotRecords({ label, records }: { label: string; records: JsonObject[] }) {
  return (
    <section aria-label={label} className="rounded-2xl border border-slate-300 bg-white p-5">
      <h3 className="font-semibold">{label}</h3>
      {records.length ? (
        <ol className="mt-3 space-y-3">
          {records.map((record, index) => (
            <li className="rounded-lg border border-slate-200 bg-slate-50 p-3" key={`${label}-${index}`}>
              <dl className="grid gap-2 sm:grid-cols-2">
                {Object.entries(record).slice(0, 24).map(([key, value]) => (
                  <div className="min-w-0" key={key}>
                    <dt className="text-xs font-semibold text-slate-500">{titleCase(key)}</dt>
                    <dd className="mt-0.5 break-words whitespace-pre-wrap font-mono text-xs">{safeText(value)}</dd>
                  </div>
                ))}
              </dl>
            </li>
          ))}
        </ol>
      ) : <p className="mt-2 text-sm text-slate-600">No records captured.</p>}
    </section>
  );
}

function ReportView({ detail, findingPage, findings, findingsLoading, onPage }: { detail: Report; findingPage: number; findings: Finding[]; findingsLoading: boolean; onPage: (page: number) => void }) {
  const snapshot = asObject(detail.input_snapshot) ?? {};
  const generation = asObject(snapshot.generation) ?? {};
  const grounding = objectArray(snapshot.grounding_sources);
  const duplicates = objectArray(snapshot.duplicate_references);
  const start = findingPage * FINDINGS_PAGE_SIZE;
  const shownStart = detail.finding_count ? start + 1 : 0;
  const shownEnd = Math.min(start + findings.length, detail.finding_count);
  return (
    <div className="space-y-6">
      <section className="rounded-2xl border-2 border-amber-400 bg-amber-50 p-5" aria-labelledby="validation-limits">
        <h2 className="text-lg font-semibold text-amber-950" id="validation-limits">Deterministic validation is limited</h2>
        <p className="mt-2 font-medium leading-6 text-amber-950">A deterministic PASS does not establish factual correctness, semantic quality, curriculum approval, or language approval. Human review is still required.</p>
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-amber-950">
          {detail.limitations.slice(0, 16).map((item, index) => <li key={index}>{safeText(item, "Unspecified limitation")}</li>)}
        </ul>
      </section>

      <section aria-label="Validation report metadata" className="rounded-2xl border border-slate-300 bg-white p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Immutable automated report</p>
            <h2 className="mt-1 text-2xl font-semibold">Deterministic result: {titleCase(detail.overall_status)}</h2>
          </div>
          <StatusBadge status={detail.overall_status} />
        </div>
        <p className="mt-3 text-sm leading-6 text-slate-600">This status describes only the recorded validator outcomes. It is not an approval or publish decision.</p>
        <dl className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <Definition label="Report ID" mono value={detail.id} />
          <Definition label="Generation run ID" mono value={detail.generation_run_id} />
          <Definition label="Generation attempt ID" mono value={detail.generation_attempt_id} />
          <Definition label="Pipeline version" mono value={safeText(detail.pipeline_version)} />
          <Definition label="Input schema version" mono value={safeText(detail.input_schema_version)} />
          <Definition label="Report schema version" mono value={safeText(detail.report_schema_version)} />
          <Definition label="Generation provider / version" mono value={`${safeText(generation.provider)} / ${safeText(generation.provider_version)}`} />
          <Definition label="Generation model / version" mono value={`${safeText(generation.model)} / ${safeText(generation.model_version)}`} />
          <Definition label="Prompt / retrieval versions" mono value={`${safeText(generation.prompt_version)} / ${safeText(generation.retrieval_version)}`} />
          <Definition label="Generation schema version" mono value={safeText(generation.generation_schema_version)} />
          <Definition label="Pipeline fingerprint" mono value={detail.pipeline_fingerprint} />
          <Definition label="Generation result fingerprint" mono value={detail.generation_result_fingerprint} />
          <Definition label="Input fingerprint" mono value={detail.input_fingerprint} />
          <Definition label="Candidate fingerprint" mono value={detail.candidate_fingerprint} />
          <Definition label="Report fingerprint" mono value={detail.report_fingerprint} />
          <Definition label="Counts" value={`${detail.finding_count} findings · ${detail.validator_count} validators`} />
          <Definition label="Provenance counts" value={`${detail.grounding_source_count} grounding · ${detail.duplicate_reference_count} duplicate references`} />
          <Definition label="Created (UTC)" value={displayDate(detail.created_at)} />
        </dl>
        <h3 className="mt-6 font-semibold">Validator lineage</h3>
        <ul className="mt-2 grid gap-2 sm:grid-cols-2">
          {detail.validator_lineage.slice(0, 32).map((validator, index) => <li className="rounded-lg border border-slate-200 p-3 font-mono text-xs" key={index}>{safeText(validator.validator_id)} · {safeText(validator.validator_version)}</li>)}
        </ul>
      </section>

      <div className="grid gap-5 lg:grid-cols-2">
        <SnapshotRecords label="Grounding provenance" records={grounding} />
        <SnapshotRecords label="Duplicate provenance" records={duplicates} />
      </div>

      <section aria-label="Validation findings" className="rounded-2xl border border-slate-300 bg-white p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="text-xl font-semibold">Findings</h2><p className="mt-1 text-sm text-slate-600">Sanitized, bounded evidence from immutable append-only records.</p></div>
          <p aria-live="polite" className="text-sm font-semibold">Findings {shownStart}–{shownEnd} of {detail.finding_count}</p>
        </div>
        {findingsLoading ? <p className="mt-5" role="status">Loading findings…</p> : (
          <ol className="mt-5 space-y-4" start={start + 1}>
            {findings.map((item) => (
              <li className="rounded-xl border border-slate-200 p-4" key={item.id}>
                <div className="flex flex-wrap items-center justify-between gap-2"><p className="font-mono text-xs text-slate-500">{item.ordinal}. {safeText(item.code)}</p><StatusBadge status={item.status} /></div>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-6">{safeText(item.message)}</p>
                <p className="mt-2 font-mono text-xs text-slate-600">{safeText(item.validator_id)} · {safeText(item.validator_version)} · {item.evidence_count} evidence</p>
                <div className="mt-3 space-y-2">
                  {item.evidence.slice(0, MAX_DISPLAY_RECORDS).map((evidence, evidenceIndex) => <dl className="rounded-lg bg-slate-50 p-3" key={evidenceIndex}>{Object.entries(evidence).slice(0, 16).map(([key, value]) => <div className="grid gap-1 sm:grid-cols-[10rem_1fr]" key={key}><dt className="text-xs font-semibold text-slate-500">{titleCase(key)}</dt><dd className="break-words whitespace-pre-wrap font-mono text-xs">{safeText(value)}</dd></div>)}</dl>)}
                </div>
              </li>
            ))}
          </ol>
        )}
        <div className="mt-5 flex justify-between gap-3">
          <Button className={secondaryButton} isDisabled={findingPage === 0 || findingsLoading} onPress={() => onPage(findingPage - 1)}>Previous findings page</Button>
          <Button className={secondaryButton} isDisabled={shownEnd >= detail.finding_count || findingsLoading} onPress={() => onPage(findingPage + 1)}>Next findings page</Button>
        </div>
      </section>
    </div>
  );
}

export function ValidationStudio({ role }: { role: Role }) {
  const api = useMemo(() => createApiClient(globalThis.location?.origin ?? "http://localhost"), []);
  const [exams, setExams] = useState<Exam[]>([]);
  const [media, setMedia] = useState<Medium[]>([]);
  const [curricula, setCurricula] = useState<Curriculum[]>([]);
  const [selectedCurriculumId, setSelectedCurriculumId] = useState("");
  const [generations, setGenerations] = useState<Generation[]>([]);
  const [selectedGenerationId, setSelectedGenerationId] = useState("");
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [selectedReportId, setSelectedReportId] = useState("");
  const [detail, setDetail] = useState<Report | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [findingPage, setFindingPage] = useState(0);
  const [workspaceLoading, setWorkspaceLoading] = useState(true);
  const [dataLoading, setDataLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [findingsLoading, setFindingsLoading] = useState(false);
  const [createLoading, setCreateLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<UiError | null>(null);
  const [dataError, setDataError] = useState<UiError | null>(null);
  const [detailError, setDetailError] = useState<UiError | null>(null);
  const [operationError, setOperationError] = useState<UiError | null>(null);
  const [notice, setNotice] = useState("");

  const dataRequestId = useRef(0);
  const detailRequestId = useRef(0);
  const findingsRequestId = useRef(0);

  const curriculumChoices = useMemo(() => {
    const examsById = new Map(exams.map((item) => [item.id, item]));
    const mediaById = new Map(media.map((item) => [item.id, item]));
    return curricula.filter((item) => {
      const exam = examsById.get(item.exam_configuration_id);
      return item.active && exam?.active && exam.grade === 5 && mediaById.get(item.medium_id)?.active;
    });
  }, [curricula, exams, media]);

  const loadWorkspace = useCallback(async () => {
    setWorkspaceLoading(true);
    setWorkspaceError(null);
    try {
      const [examResponse, mediumResponse, curriculumResponse] = await Promise.all([
        api.GET("/api/v1/admin/exam-configurations"),
        api.GET("/api/v1/admin/media"),
        api.GET("/api/v1/admin/curriculum-versions"),
      ]);
      const outcomes: ApiOutcome[] = [examResponse, mediumResponse, curriculumResponse];
      const failure = outcomes.find((item) => item.error !== undefined);
      if (failure?.error !== undefined) {
        setWorkspaceError(apiError(failure.error, failure.response.status, "workspace"));
        return;
      }
      const nextExams = examResponse.data ?? [];
      const nextMedia = mediumResponse.data ?? [];
      const nextCurricula = curriculumResponse.data ?? [];
      const examIds = new Set(nextExams.filter((item) => item.active && item.grade === 5).map((item) => item.id));
      const mediumIds = new Set(nextMedia.filter((item) => item.active).map((item) => item.id));
      const active = nextCurricula.filter((item) => item.active && examIds.has(item.exam_configuration_id) && mediumIds.has(item.medium_id));
      setExams(nextExams);
      setMedia(nextMedia);
      setCurricula(nextCurricula);
      setSelectedCurriculumId((current) => active.some((item) => item.id === current) ? current : (active[0]?.id ?? ""));
    } catch {
      setWorkspaceError(networkError("workspace"));
    } finally {
      setWorkspaceLoading(false);
    }
  }, [api]);

  const loadData = useCallback(async (curriculumId: string) => {
    const requestId = ++dataRequestId.current;
    setDataLoading(true);
    setDataError(null);
    setNotice("");
    try {
      const path = { curriculum_version_id: curriculumId };
      const [generationResponse, reportResponse] = await Promise.all([
        api.GET("/api/v1/admin/curricula/{curriculum_version_id}/generation-runs", { params: { path, query: { limit: LIST_LIMIT, offset: 0 } } }),
        api.GET("/api/v1/admin/curricula/{curriculum_version_id}/validation-runs", { params: { path, query: { limit: LIST_LIMIT, offset: 0 } } }),
      ]);
      if (requestId !== dataRequestId.current) return;
      const failure = generationResponse.error !== undefined ? generationResponse : reportResponse.error !== undefined ? reportResponse : null;
      if (failure?.error !== undefined) {
        setDataError(apiError(failure.error, failure.response.status, "data"));
        return;
      }
      const nextGenerations = [...(generationResponse.data ?? [])].sort((left, right) => Number(eligibleGeneration(right)) - Number(eligibleGeneration(left)) || right.created_at.localeCompare(left.created_at));
      const nextReports = reportResponse.data ?? [];
      setGenerations(nextGenerations);
      setReports(nextReports);
      setSelectedGenerationId((current) => nextGenerations.some((item) => item.id === current) ? current : (nextGenerations[0]?.id ?? ""));
      setSelectedReportId((current) => nextReports.some((item) => item.id === current) ? current : (nextReports[0]?.id ?? ""));
      if (!nextReports.length) {
        setDetail(null);
        setFindings([]);
      }
    } catch {
      if (requestId === dataRequestId.current) setDataError(networkError("data"));
    } finally {
      if (requestId === dataRequestId.current) setDataLoading(false);
    }
  }, [api]);

  const loadReport = useCallback(async (curriculumId: string, reportId: string) => {
    const requestId = ++detailRequestId.current;
    setDetailLoading(true);
    setDetailError(null);
    try {
      const response = await api.GET("/api/v1/admin/curricula/{curriculum_version_id}/validation-runs/{validation_run_id}", { params: { path: { curriculum_version_id: curriculumId, validation_run_id: reportId } } });
      if (requestId !== detailRequestId.current) return;
      if (response.error !== undefined) setDetailError(apiError(response.error, response.response.status, "report"));
      else setDetail(response.data ?? null);
    } catch {
      if (requestId === detailRequestId.current) setDetailError(networkError("report"));
    } finally {
      if (requestId === detailRequestId.current) setDetailLoading(false);
    }
  }, [api]);

  const loadFindings = useCallback(async (curriculumId: string, reportId: string, page: number) => {
    const requestId = ++findingsRequestId.current;
    setFindingsLoading(true);
    setDetailError(null);
    try {
      const response = await api.GET("/api/v1/admin/curricula/{curriculum_version_id}/validation-runs/{validation_run_id}/findings", { params: { path: { curriculum_version_id: curriculumId, validation_run_id: reportId }, query: { limit: FINDINGS_PAGE_SIZE, offset: page * FINDINGS_PAGE_SIZE } } });
      if (requestId !== findingsRequestId.current) return;
      if (response.error !== undefined) setDetailError(apiError(response.error, response.response.status, "report"));
      else setFindings(response.data ?? []);
    } catch {
      if (requestId === findingsRequestId.current) setDetailError(networkError("report"));
    } finally {
      if (requestId === findingsRequestId.current) setFindingsLoading(false);
    }
  }, [api]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadWorkspace(), 0);
    return () => window.clearTimeout(timeout);
  }, [loadWorkspace]);
  useEffect(() => {
    if (!selectedCurriculumId) return;
    const timeout = window.setTimeout(() => void loadData(selectedCurriculumId), 0);
    return () => window.clearTimeout(timeout);
  }, [loadData, selectedCurriculumId]);
  useEffect(() => {
    if (!selectedCurriculumId || !selectedReportId) return;
    const timeout = window.setTimeout(() => void loadReport(selectedCurriculumId, selectedReportId), 0);
    return () => window.clearTimeout(timeout);
  }, [loadReport, selectedCurriculumId, selectedReportId]);
  useEffect(() => {
    if (!selectedCurriculumId || !selectedReportId) return;
    const timeout = window.setTimeout(
      () => void loadFindings(selectedCurriculumId, selectedReportId, findingPage),
      0,
    );
    return () => window.clearTimeout(timeout);
  }, [findingPage, loadFindings, selectedCurriculumId, selectedReportId]);

  function selectCurriculum(curriculumId: string) {
    if (curriculumId === selectedCurriculumId) return;
    dataRequestId.current += 1;
    detailRequestId.current += 1;
    findingsRequestId.current += 1;
    setDataLoading(true);
    setDetailLoading(false);
    setFindingsLoading(false);
    setSelectedCurriculumId(curriculumId);
    setSelectedGenerationId("");
    setSelectedReportId("");
    setGenerations([]);
    setReports([]);
    setDetail(null);
    setFindings([]);
    setFindingPage(0);
  }

  function selectReport(reportId: string) {
    if (reportId === selectedReportId) return;
    detailRequestId.current += 1;
    findingsRequestId.current += 1;
    setSelectedReportId(reportId);
    setDetail(null);
    setFindings([]);
    setFindingPage(0);
  }

  function selectFindingPage(page: number) {
    findingsRequestId.current += 1;
    setFindings([]);
    setFindingsLoading(true);
    setFindingPage(page);
  }

  async function createValidation() {
    if (!selectedCurriculumId || !selectedGenerationId || role !== "admin" || createLoading) return;
    setCreateLoading(true);
    setOperationError(null);
    setNotice("");
    const body: ValidationRequest = { generation_run_id: selectedGenerationId };
    try {
      const response = await api.POST("/api/v1/admin/curricula/{curriculum_version_id}/validation-runs", { body, params: { path: { curriculum_version_id: selectedCurriculumId } } });
      if (response.error !== undefined) {
        setOperationError(apiError(response.error, response.response.status, "create"));
        return;
      }
      if (!response.data) return;
      const created = response.data;
      setReports((current) => [reportSummary(created), ...current.filter((item) => item.id !== created.id)]);
      setDetail(created);
      setSelectedReportId(created.id);
      setFindingPage(0);
      setNotice(created.deduplicated ? "Existing immutable report reused; no duplicate report was created." : "Immutable validation report created. Human review is still required.");
    } catch {
      setOperationError(networkError("create"));
    } finally {
      setCreateLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8 lg:py-10">
      <header className="flex flex-wrap items-start justify-between gap-5 border-b border-slate-300 pb-7">
        <div><p className="font-mono text-xs tracking-[0.18em] text-slate-500 uppercase">P8 / automated evidence</p><h1 className="mt-2 text-4xl font-semibold tracking-tight">Validation Studio</h1><p className="mt-3 max-w-3xl leading-7 text-slate-600">Run the server-owned deterministic pipeline and inspect immutable, bounded evidence before mandatory human review.</p></div>
        <Badge className="border-slate-300 bg-white text-slate-700">{role === "reviewer" ? "Reviewer read access" : "Admin validation access"}</Badge>
      </header>

      <section className="mt-6 rounded-2xl border-2 border-amber-400 bg-amber-50 p-5">
        <h2 className="font-semibold text-amber-950">No automated approval</h2>
        <p className="mt-2 text-sm leading-6 text-amber-950">A deterministic PASS does not establish factual correctness, semantic quality, curriculum approval, or language approval. Human review is still required before any approval or publishing workflow.</p>
      </section>

      {workspaceLoading ? <p className="mt-8" role="status">Loading validation workspace…</p> : workspaceError ? <div className="mt-8"><ErrorPanel error={workspaceError} onRetry={() => void loadWorkspace()} /></div> : !curriculumChoices.length ? (
        <section className="mt-8 rounded-2xl border border-dashed border-slate-400 bg-white p-6"><h2 className="text-xl font-semibold">No active Grade 5 curriculum</h2><p className="mt-2 text-sm text-slate-600">Activate a Grade 5 exam, medium, and curriculum before validation.</p><Link className={`${secondaryButton} mt-4`} href="/admin/curriculum">Open Curriculum Studio</Link></section>
      ) : (
        <>
          <div className="mt-8 grid gap-6 lg:grid-cols-2">
            <Panel title="Validation scope" description="Only active Grade 5 curricula are shown; the API remains authoritative.">
              <label className={fieldClass}>Active Grade 5 curriculum<select className={inputClass} onChange={(event) => selectCurriculum(event.target.value)} value={selectedCurriculumId}>{curriculumChoices.map((item) => <option key={item.id} value={item.id}>{item.title} ({item.code})</option>)}</select></label>
            </Panel>
            {role === "reviewer" ? <section className="rounded-2xl border border-blue-300 bg-blue-50 p-5"><h2 className="text-xl font-semibold text-blue-950">Reviewer read-only mode</h2><p className="mt-2 text-sm leading-6 text-blue-950">You can inspect immutable reports and findings. Validation creation is not available to this role.</p></section> : <Panel title="Run canonical validation" description="The request contains only the selected generation_run_id. Pipeline rules and inputs are reconstructed by the server."><Form onSubmit={(event) => { event.preventDefault(); void createValidation(); }}><label className={fieldClass}>Generation run<select className={inputClass} disabled={!generations.length || createLoading} onChange={(event) => setSelectedGenerationId(event.target.value)} value={selectedGenerationId}>{generations.map((item) => <option key={item.id} value={item.id}>{eligibleGeneration(item) ? "Preferred: succeeded and requires validation" : `${titleCase(item.status)}${item.disposition ? ` / ${titleCase(item.disposition)}` : ""}`} — {item.id}</option>)}</select></label>{generations.length ? <p className="mt-3 text-xs leading-5 text-slate-600">Succeeded runs marked requires validation are preferred. Other states remain visible; server authorization and persisted state are authoritative.</p> : null}<Button className={`${primaryButton} mt-4`} isDisabled={!selectedGenerationId || createLoading} type="submit">{createLoading ? "Running validation…" : "Run deterministic validation"}</Button></Form></Panel>}
          </div>

          {dataLoading ? <p className="mt-8" role="status">Loading generation runs and validation reports…</p> : dataError ? <div className="mt-8"><ErrorPanel error={dataError} onRetry={() => void loadData(selectedCurriculumId)} /></div> : (
            <div className="mt-8 grid gap-6 lg:grid-cols-[22rem_minmax(0,1fr)]">
              <div className="space-y-6">
                {!generations.length ? <section className="rounded-2xl border border-dashed border-slate-400 bg-white p-5"><h2 className="text-lg font-semibold">No generation runs yet</h2><p className="mt-2 text-sm text-slate-600">Create a grounded generation before validation.</p><Link className={`${secondaryButton} mt-4`} href="/admin/generation">Open Generation Studio</Link></section> : !generations.some(eligibleGeneration) ? <section className="rounded-xl border border-amber-300 bg-amber-50 p-4"><h2 className="font-semibold">No preferred generation is ready</h2><p className="mt-1 text-sm leading-6">Pending generations are listed for context. The server will reject any state that is not eligible when an administrator requests validation.</p></section> : null}
                <Panel title="Immutable reports" description="Select a persisted report; reports cannot be edited here.">
                  {!reports.length ? <div><h3 className="font-semibold">No validation reports yet</h3><p className="mt-2 text-sm leading-6 text-slate-600">A generation can be pending or succeeded without a report until an administrator explicitly runs validation.</p></div> : <ol className="space-y-3">{reports.map((item) => <li key={item.id}><button aria-pressed={item.id === selectedReportId} className={`w-full rounded-xl border p-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-amber-500 ${item.id === selectedReportId ? "border-slate-950 bg-slate-50" : "border-slate-200 bg-white hover:bg-slate-50"}`} onClick={() => selectReport(item.id)} type="button" aria-label={`Select validation report ${item.id}`}><span className="flex items-center justify-between gap-2"><span className="font-mono text-xs">{item.id}</span><StatusBadge status={item.overall_status} /></span><span className="mt-2 block text-xs text-slate-600">{item.finding_count} findings · {displayDate(item.created_at)}</span></button></li>)}</ol>}
                </Panel>
              </div>
              <div>{notice ? <p className="mb-5 rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-sm font-semibold text-emerald-950" role="status">{notice}</p> : null}{operationError ? <div className="mb-5"><ErrorPanel error={operationError} /></div> : null}{detailLoading ? <p role="status">Loading immutable validation report…</p> : detailError ? <ErrorPanel error={detailError} onRetry={() => { if (selectedReportId) { void loadReport(selectedCurriculumId, selectedReportId); void loadFindings(selectedCurriculumId, selectedReportId, findingPage); } }} /> : detail ? <ReportView detail={detail} findingPage={findingPage} findings={findings} findingsLoading={findingsLoading} onPage={selectFindingPage} /> : reports.length ? <p>Select an immutable report.</p> : null}</div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
