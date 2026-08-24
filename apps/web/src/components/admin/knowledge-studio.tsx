"use client";

import {
  createApiClient,
  type components,
  type operations,
} from "@exam-guru/api-client";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  Button,
  Form,
  Tab,
  TabList,
  TabPanel,
  Tabs,
} from "react-aria-components";

import { Badge } from "@/components/ui/badge";

type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type SourcePage = components["schemas"]["SourcePageResponse"];
type ExtractedBlock = components["schemas"]["ExtractedBlockResponse"];
type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
type HistoricalQuestion = components["schemas"]["HistoricalQuestionResponse"];
type KnowledgeChunk = components["schemas"]["KnowledgeChunkResponse"];
type KnowledgeRecord = HistoricalQuestion | KnowledgeChunk;
type Classification = components["schemas"]["KnowledgeClassificationRequest"];
type ReviewState = components["schemas"]["ReviewState"];
type QuestionType = components["schemas"]["QuestionType"];
type ChunkType = components["schemas"]["ChunkType"];
type QuestionQuery = NonNullable<
  operations["list_historical_questions"]["parameters"]["query"]
>;
type ChunkQuery = NonNullable<operations["list_knowledge_chunks"]["parameters"]["query"]>;
type Role = "admin" | "reviewer";
type RecordKind = "questions" | "chunks";

type UiError = {
  code: string;
  message: string;
};

type MutationOutcome = {
  error?: UiError;
  notice?: string;
};

const DEFAULT_LIMIT = 25;
const MAX_LIMIT = 100;
const MAX_OFFSET = 100_000;
const PAGE_SIZES = [10, 25, 50, 100] as const;

const questionTypes: ReadonlyArray<{ label: string; value: QuestionType }> = [
  { label: "Multiple choice", value: "multiple_choice" },
  { label: "Short answer", value: "short_answer" },
  { label: "Structured", value: "structured" },
];

const chunkTypes: ReadonlyArray<{ label: string; value: ChunkType }> = [
  { label: "Competency section", value: "competency_section" },
  { label: "Learning outcome", value: "learning_outcome" },
  { label: "Explanation", value: "explanation" },
  { label: "Example", value: "example" },
  { label: "Practice question", value: "practice_question" },
  { label: "Key term", value: "key_term" },
];

const reviewStates: ReadonlyArray<{ label: string; value: ReviewState }> = [
  { label: "Draft", value: "draft" },
  { label: "In review", value: "in_review" },
  { label: "Reviewed", value: "reviewed" },
  { label: "Rejected", value: "rejected" },
];

const errorMessages: Record<string, string> = {
  authentication_required: "Your admin session has expired. Sign in again before retrying.",
  concurrent_knowledge_modification:
    "Conflict detected: another reviewer changed this record. The latest version was loaded; review it before retrying.",
  curriculum_version_not_found: "The selected curriculum version no longer exists.",
  final_knowledge_record: "This reviewed or rejected record is final and read-only.",
  invalid_review_transition: "That review transition is not allowed from the current state.",
  invalid_taxonomy_classification:
    "The selected taxonomy path is not a valid hierarchy for this curriculum.",
  knowledge_record_not_found: "The knowledge record no longer exists.",
  knowledge_record_not_ready:
    "A reviewed record requires a competency classification and source-block provenance.",
  network_error: "The service could not be reached. Check the connection and retry.",
  permission_denied: "Your account does not have permission to complete this action.",
  request_failed: "The request could not be completed. Retry or contact an administrator.",
  service_unavailable: "The knowledge service is temporarily unavailable.",
  source_curriculum_mismatch: "The selected source belongs to a different curriculum.",
  source_document_not_found: "The selected source document no longer exists.",
  source_import_conflict:
    "This source location was already imported with different normalized content.",
  source_metadata_mismatch:
    "The past-paper year or paper code does not match the immutable source metadata.",
  trusted_source_required: "Knowledge can only be imported from a trusted source document.",
};

const fieldClass = "grid gap-1.5 text-sm font-medium text-slate-700";
const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 outline-none transition focus:border-amber-500 focus:ring-2 focus:ring-amber-200 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none transition hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-400 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const dangerButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-red-300 bg-red-50 px-4 py-2 text-sm font-semibold text-red-900 outline-none transition hover:bg-red-100 focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

function errorCode(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail) && "code" in detail) {
      return String((detail as { code: unknown }).code);
    }
  }
  return "request_failed";
}

function uiError(error: unknown, status?: number): UiError {
  const code = status === 401 ? "authentication_required" : status === 403 ? "permission_denied" : errorCode(error);
  return { code, message: errorMessages[code] ?? errorMessages.request_failed };
}

function networkError(): UiError {
  return { code: "network_error", message: errorMessages.network_error };
}

function pageSize(value: FormDataEntryValue | null): number {
  const parsed = Number(value);
  return PAGE_SIZES.includes(parsed as (typeof PAGE_SIZES)[number])
    ? parsed
    : DEFAULT_LIMIT;
}

function optionalString(value: FormDataEntryValue | null): string | undefined {
  const normalized = String(value ?? "").trim();
  return normalized || undefined;
}

function displayState(state: ReviewState): string {
  return state.replace("_", " ");
}

function displayEmbeddingStatus(status: components["schemas"]["EmbeddingStatus"]): string {
  return status === "embedded" ? "Embedded" : "Not embedded";
}

function documentLabel(document: SourceDocument): string {
  const paperMetadata =
    document.document_type === "past_paper"
      ? ` — ${document.year ?? "year missing"} / ${document.paper_code ?? "paper code missing"}`
      : "";
  return `${document.original_filename}${paperMetadata}`;
}

function isQuestion(record: KnowledgeRecord): record is HistoricalQuestion {
  return "question_number" in record;
}

export function KnowledgeStudio({ role }: { role: Role }) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [curricula, setCurricula] = useState<Curriculum[]>([]);
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [taxonomy, setTaxonomy] = useState<TaxonomyNode[]>([]);
  const [questions, setQuestions] = useState<HistoricalQuestion[]>([]);
  const [chunks, setChunks] = useState<KnowledgeChunk[]>([]);
  const [selectedCurriculumId, setSelectedCurriculumId] = useState("");
  const [activeKind, setActiveKind] = useState<RecordKind>("questions");
  const [questionQuery, setQuestionQuery] = useState<QuestionQuery>({
    limit: DEFAULT_LIMIT,
    offset: 0,
  });
  const [chunkQuery, setChunkQuery] = useState<ChunkQuery>({
    limit: DEFAULT_LIMIT,
    offset: 0,
  });
  const [workspaceLoading, setWorkspaceLoading] = useState(true);
  const [workspaceError, setWorkspaceError] = useState<UiError | null>(null);
  const [taxonomyLoading, setTaxonomyLoading] = useState(false);
  const [taxonomyError, setTaxonomyError] = useState<UiError | null>(null);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordsError, setRecordsError] = useState<UiError | null>(null);
  const [filterError, setFilterError] = useState("");
  const [importPermissionDenied, setImportPermissionDenied] = useState(false);
  const [reviewPermissionDenied, setReviewPermissionDenied] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [importNotice, setImportNotice] = useState("");
  const [importError, setImportError] = useState<UiError | null>(null);

  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [sourcePages, setSourcePages] = useState<SourcePage[]>([]);
  const [selectedPageNumber, setSelectedPageNumber] = useState("");
  const [sourceBlocks, setSourceBlocks] = useState<ExtractedBlock[]>([]);
  const [selectedBlockId, setSelectedBlockId] = useState("");
  const [normalizedText, setNormalizedText] = useState("");
  const [provenanceLoading, setProvenanceLoading] = useState(false);
  const [provenanceError, setProvenanceError] = useState<UiError | null>(null);

  const recordRequestId = useRef(0);
  const taxonomyRequestId = useRef(0);
  const provenanceRequestId = useRef(0);

  const activeCurricula = useMemo(
    () => curricula.filter((item) => item.active),
    [curricula],
  );
  const scopedDocuments = useMemo(
    () => documents.filter((document) => document.curriculum_version_id === selectedCurriculumId),
    [documents, selectedCurriculumId],
  );
  const trustedSources = useMemo(
    () =>
      scopedDocuments.filter(
        (document) =>
          document.extraction_status === "trusted" &&
          (activeKind === "chunks" ||
            (document.document_type === "past_paper" &&
              document.year !== null &&
              document.paper_code !== null)),
      ),
    [activeKind, scopedDocuments],
  );
  const selectedSource = trustedSources.find((document) => document.id === selectedSourceId);
  const selectedBlock = sourceBlocks.find((block) => block.id === selectedBlockId);

  const loadWorkspace = useCallback(async () => {
    setWorkspaceLoading(true);
    setWorkspaceError(null);
    try {
      const [curriculumResult, documentResult] = await Promise.all([
        api.GET("/api/v1/admin/curriculum-versions"),
        api.GET("/api/v1/admin/source-documents"),
      ]);
      if (!curriculumResult.response.ok || !documentResult.response.ok) {
        const failed = !curriculumResult.response.ok ? curriculumResult : documentResult;
        setWorkspaceError(uiError(failed.error, failed.response.status));
        return;
      }
      const nextCurricula = curriculumResult.data ?? [];
      const nextDocuments = documentResult.data ?? [];
      setCurricula(nextCurricula);
      setDocuments(nextDocuments);
      setSelectedCurriculumId((current) => {
        const currentIsActive = nextCurricula.some((item) => item.id === current && item.active);
        return currentIsActive ? current : (nextCurricula.find((item) => item.active)?.id ?? "");
      });
    } catch {
      setWorkspaceError(networkError());
    } finally {
      setWorkspaceLoading(false);
    }
  }, [api]);

  const loadTaxonomy = useCallback(
    async (curriculumVersionId: string) => {
      if (!curriculumVersionId) {
        setTaxonomy([]);
        return;
      }
      const requestId = ++taxonomyRequestId.current;
      setTaxonomyLoading(true);
      setTaxonomyError(null);
      try {
        const result = await api.GET(
          "/api/v1/admin/curricula/{curriculum_version_id}/taxonomy/nodes",
          { params: { path: { curriculum_version_id: curriculumVersionId } } },
        );
        if (requestId !== taxonomyRequestId.current) return;
        if (!result.response.ok) {
          setTaxonomyError(uiError(result.error, result.response.status));
          setTaxonomy([]);
        } else {
          setTaxonomy(result.data ?? []);
        }
      } catch {
        if (requestId === taxonomyRequestId.current) {
          setTaxonomyError(networkError());
          setTaxonomy([]);
        }
      } finally {
        if (requestId === taxonomyRequestId.current) setTaxonomyLoading(false);
      }
    },
    [api],
  );

  const loadRecords = useCallback(
    async (
      kind: RecordKind,
      curriculumVersionId: string,
      query: QuestionQuery | ChunkQuery,
    ) => {
      if (!curriculumVersionId) {
        setQuestions([]);
        setChunks([]);
        return;
      }
      const requestId = ++recordRequestId.current;
      setRecordsLoading(true);
      setRecordsError(null);
      try {
        if (kind === "questions") {
          const result = await api.GET(
            "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/questions",
            {
              params: {
                path: { curriculum_version_id: curriculumVersionId },
                query: query as QuestionQuery,
              },
            },
          );
          if (requestId !== recordRequestId.current) return;
          if (!result.response.ok) {
            setRecordsError(uiError(result.error, result.response.status));
            setQuestions([]);
          } else {
            setQuestions(result.data ?? []);
          }
        } else {
          const result = await api.GET(
            "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/chunks",
            {
              params: {
                path: { curriculum_version_id: curriculumVersionId },
                query: query as ChunkQuery,
              },
            },
          );
          if (requestId !== recordRequestId.current) return;
          if (!result.response.ok) {
            setRecordsError(uiError(result.error, result.response.status));
            setChunks([]);
          } else {
            setChunks(result.data ?? []);
          }
        }
      } catch {
        if (requestId === recordRequestId.current) {
          setRecordsError(networkError());
          if (kind === "questions") setQuestions([]);
          else setChunks([]);
        }
      } finally {
        if (requestId === recordRequestId.current) setRecordsLoading(false);
      }
    },
    [api],
  );

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadWorkspace(), 0);
    return () => window.clearTimeout(timeout);
  }, [loadWorkspace]);

  useEffect(() => {
    const timeout = window.setTimeout(
      () => void loadTaxonomy(selectedCurriculumId),
      0,
    );
    return () => window.clearTimeout(timeout);
  }, [loadTaxonomy, selectedCurriculumId]);

  useEffect(() => {
    const query = activeKind === "questions" ? questionQuery : chunkQuery;
    const timeout = window.setTimeout(
      () => void loadRecords(activeKind, selectedCurriculumId, query),
      0,
    );
    return () => window.clearTimeout(timeout);
  }, [activeKind, chunkQuery, loadRecords, questionQuery, selectedCurriculumId]);

  async function loadSourcePages(sourceDocumentId: string) {
    const requestId = ++provenanceRequestId.current;
    setProvenanceLoading(true);
    setProvenanceError(null);
    setSourcePages([]);
    setSelectedPageNumber("");
    setSourceBlocks([]);
    setSelectedBlockId("");
    setNormalizedText("");
    if (!sourceDocumentId) {
      setProvenanceLoading(false);
      return;
    }
    try {
      const result = await api.GET(
        "/api/v1/admin/source-documents/{document_id}/pages",
        { params: { path: { document_id: sourceDocumentId } } },
      );
      if (requestId !== provenanceRequestId.current) return;
      if (!result.response.ok) {
        setProvenanceError(uiError(result.error, result.response.status));
      } else {
        setSourcePages(result.data ?? []);
      }
    } catch {
      if (requestId === provenanceRequestId.current) setProvenanceError(networkError());
    } finally {
      if (requestId === provenanceRequestId.current) setProvenanceLoading(false);
    }
  }

  async function loadSourceBlocks(sourceDocumentId: string, pageNumber: number) {
    const requestId = ++provenanceRequestId.current;
    setProvenanceLoading(true);
    setProvenanceError(null);
    setSourceBlocks([]);
    setSelectedBlockId("");
    setNormalizedText("");
    try {
      const result = await api.GET(
        "/api/v1/admin/source-documents/{document_id}/pages/{page_number}/blocks",
        {
          params: {
            path: { document_id: sourceDocumentId, page_number: pageNumber },
          },
        },
      );
      if (requestId !== provenanceRequestId.current) return;
      if (!result.response.ok) {
        setProvenanceError(uiError(result.error, result.response.status));
      } else {
        setSourceBlocks((result.data as ExtractedBlock[] | undefined) ?? []);
      }
    } catch {
      if (requestId === provenanceRequestId.current) setProvenanceError(networkError());
    } finally {
      if (requestId === provenanceRequestId.current) setProvenanceLoading(false);
    }
  }

  function chooseSource(sourceDocumentId: string) {
    setSelectedSourceId(sourceDocumentId);
    setImportError(null);
    setImportNotice("");
    void loadSourcePages(sourceDocumentId);
  }

  function choosePage(pageNumber: string) {
    setSelectedPageNumber(pageNumber);
    setImportError(null);
    setImportNotice("");
    if (selectedSourceId && pageNumber) {
      void loadSourceBlocks(selectedSourceId, Number(pageNumber));
    } else {
      setSourceBlocks([]);
      setSelectedBlockId("");
      setNormalizedText("");
    }
  }

  function chooseBlock(blockId: string) {
    setSelectedBlockId(blockId);
    const block = sourceBlocks.find((item) => item.id === blockId);
    setNormalizedText(block ? (block.reviewed_text ?? block.raw_text) : "");
    setImportError(null);
    setImportNotice("");
  }

  function resetImportSelection() {
    provenanceRequestId.current += 1;
    setSelectedSourceId("");
    setSourcePages([]);
    setSelectedPageNumber("");
    setSourceBlocks([]);
    setSelectedBlockId("");
    setNormalizedText("");
    setProvenanceError(null);
    setImportError(null);
    setImportNotice("");
  }

  function changeRecordKind(kind: RecordKind) {
    if (kind === activeKind) return;
    resetImportSelection();
    setFilterError("");
    setActiveKind(kind);
  }

  function changeCurriculum(curriculumVersionId: string) {
    resetImportSelection();
    setSelectedCurriculumId(curriculumVersionId);
    setQuestionQuery({ limit: DEFAULT_LIMIT, offset: 0 });
    setChunkQuery({ limit: DEFAULT_LIMIT, offset: 0 });
    setFilterError("");
    setReviewPermissionDenied(false);
  }

  function applyQuestionFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const rawYear = optionalString(form.get("year"));
    const year = rawYear ? Number(rawYear) : undefined;
    if (
      year !== undefined &&
      (!Number.isInteger(year) || year < 1900 || year > 2100)
    ) {
      setFilterError("Enter a whole year from 1900 through 2100, or leave it blank.");
      return;
    }
    const paperCode = optionalString(form.get("paper_code"));
    if (paperCode && paperCode.length > 64) {
      setFilterError("Paper code filters are limited to 64 characters.");
      return;
    }
    setFilterError("");
    setQuestionQuery({
      competency_id: optionalString(form.get("competency_id")),
      limit: pageSize(form.get("limit")),
      offset: 0,
      paper_code: paperCode,
      question_type: optionalString(form.get("question_type")) as QuestionType | undefined,
      review_state: optionalString(form.get("review_state")) as ReviewState | undefined,
      source_document_id: optionalString(form.get("source_document_id")),
      year,
    });
  }

  function applyChunkFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setFilterError("");
    setChunkQuery({
      chunk_type: optionalString(form.get("chunk_type")) as ChunkType | undefined,
      competency_id: optionalString(form.get("competency_id")),
      limit: pageSize(form.get("limit")),
      offset: 0,
      review_state: optionalString(form.get("review_state")) as ReviewState | undefined,
      source_document_id: optionalString(form.get("source_document_id")),
    });
  }

  function paginate(direction: -1 | 1) {
    if (activeKind === "questions") {
      setQuestionQuery((current) => ({
        ...current,
        offset: Math.min(
          MAX_OFFSET,
          Math.max(0, (current.offset ?? 0) + direction * (current.limit ?? DEFAULT_LIMIT)),
        ),
      }));
    } else {
      setChunkQuery((current) => ({
        ...current,
        offset: Math.min(
          MAX_OFFSET,
          Math.max(0, (current.offset ?? 0) + direction * (current.limit ?? DEFAULT_LIMIT)),
        ),
      }));
    }
  }

  function importFailure(error: unknown, status: number) {
    const nextError = uiError(error, status);
    if (status === 403) setImportPermissionDenied(true);
    setImportError(nextError);
  }

  async function importQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !selectedSource ||
      selectedSource.year === null ||
      selectedSource.paper_code === null ||
      !selectedPageNumber ||
      !selectedBlock ||
      !normalizedText.trim()
    ) {
      setImportError({
        code: "provenance_required",
        message: "Choose a trusted source page and block, then provide normalized text.",
      });
      return;
    }
    const form = new FormData(event.currentTarget);
    const request: components["schemas"]["HistoricalQuestionImportRequest"] = {
      marks: Number(form.get("marks")),
      page_number: Number(selectedPageNumber),
      paper_code: selectedSource.paper_code,
      question_number: String(form.get("question_number")),
      question_type: String(form.get("question_type")) as QuestionType,
      source_block_id: selectedBlock.id,
      source_document_id: selectedSource.id,
      text: normalizedText,
      year: selectedSource.year,
    };
    setImportBusy(true);
    setImportError(null);
    setImportNotice("");
    try {
      const result = await api.POST(
        "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/questions",
        {
          body: request,
          params: { path: { curriculum_version_id: selectedCurriculumId } },
        },
      );
      if (!result.response.ok) {
        importFailure(result.error, result.response.status);
        return;
      }
      const imported = result.data as HistoricalQuestion | undefined;
      if (!imported) {
        setImportError({ code: "request_failed", message: errorMessages.request_failed });
        return;
      }
      setQuestions((current) => [
        imported,
        ...current.filter((record) => record.id !== imported.id),
      ]);
      setImportNotice(
        imported.deduplicated
          ? "Existing historical question reused."
          : "Historical question imported.",
      );
    } catch {
      setImportError(networkError());
    } finally {
      setImportBusy(false);
    }
  }

  async function importChunk(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !selectedSource ||
      !selectedPageNumber ||
      !selectedBlock ||
      !normalizedText.trim()
    ) {
      setImportError({
        code: "provenance_required",
        message: "Choose a trusted source page and block, then provide normalized text.",
      });
      return;
    }
    const form = new FormData(event.currentTarget);
    const request: components["schemas"]["KnowledgeChunkImportRequest"] = {
      chunk_type: String(form.get("chunk_type")) as ChunkType,
      educational_boundary: String(form.get("educational_boundary")),
      page_number: Number(selectedPageNumber),
      sequence: Number(form.get("sequence")),
      source_block_id: selectedBlock.id,
      source_document_id: selectedSource.id,
      text: normalizedText,
    };
    setImportBusy(true);
    setImportError(null);
    setImportNotice("");
    try {
      const result = await api.POST(
        "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/chunks",
        {
          body: request,
          params: { path: { curriculum_version_id: selectedCurriculumId } },
        },
      );
      if (!result.response.ok) {
        importFailure(result.error, result.response.status);
        return;
      }
      const imported = result.data as KnowledgeChunk | undefined;
      if (!imported) {
        setImportError({ code: "request_failed", message: errorMessages.request_failed });
        return;
      }
      setChunks((current) => [
        imported,
        ...current.filter((record) => record.id !== imported.id),
      ]);
      setImportNotice(
        imported.deduplicated ? "Existing knowledge chunk reused." : "Knowledge chunk imported.",
      );
    } catch {
      setImportError(networkError());
    } finally {
      setImportBusy(false);
    }
  }

  function updateRecord(kind: RecordKind, updated: KnowledgeRecord) {
    if (kind === "questions") {
      const question = updated as HistoricalQuestion;
      setQuestions((current) =>
        current.map((record) => (record.id === question.id ? question : record)),
      );
    } else {
      const chunk = updated as KnowledgeChunk;
      setChunks((current) =>
        current.map((record) => (record.id === chunk.id ? chunk : record)),
      );
    }
  }

  async function refreshRecord(kind: RecordKind, recordId: string): Promise<boolean> {
    try {
      if (kind === "questions") {
        const result = await api.GET(
          "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/questions/{question_id}",
          {
            params: {
              path: {
                curriculum_version_id: selectedCurriculumId,
                question_id: recordId,
              },
            },
          },
        );
        if (!result.response.ok || !result.data) return false;
        updateRecord(kind, result.data);
      } else {
        const result = await api.GET(
          "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/chunks/{chunk_id}",
          {
            params: {
              path: {
                chunk_id: recordId,
                curriculum_version_id: selectedCurriculumId,
              },
            },
          },
        );
        if (!result.response.ok || !result.data) return false;
        updateRecord(kind, result.data);
      }
      return true;
    } catch {
      return false;
    }
  }

  async function mutationFailure(
    kind: RecordKind,
    recordId: string,
    error: unknown,
    status: number,
  ): Promise<MutationOutcome> {
    if (status === 403) setReviewPermissionDenied(true);
    const nextError = uiError(error, status);
    if (status === 409) {
      await refreshRecord(kind, recordId);
      if (nextError.code === "concurrent_knowledge_modification") {
        return { error: nextError };
      }
      return {
        error: {
          code: nextError.code,
          message: `${nextError.message} The latest version was loaded.`,
        },
      };
    }
    return { error: nextError };
  }

  async function classifyRecord(
    kind: RecordKind,
    record: KnowledgeRecord,
    classification: Omit<Classification, "expected_version">,
  ): Promise<MutationOutcome> {
    const body: Classification = { ...classification, expected_version: record.version };
    try {
      if (kind === "questions") {
        const result = await api.PATCH(
          "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/questions/{question_id}/classification",
          {
            body,
            params: {
              path: {
                curriculum_version_id: selectedCurriculumId,
                question_id: record.id,
              },
            },
          },
        );
        if (!result.response.ok) {
          return mutationFailure(kind, record.id, result.error, result.response.status);
        }
        if (result.data) updateRecord(kind, result.data);
      } else {
        const result = await api.PATCH(
          "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/chunks/{chunk_id}/classification",
          {
            body,
            params: {
              path: {
                chunk_id: record.id,
                curriculum_version_id: selectedCurriculumId,
              },
            },
          },
        );
        if (!result.response.ok) {
          return mutationFailure(kind, record.id, result.error, result.response.status);
        }
        if (result.data) updateRecord(kind, result.data);
      }
      return { notice: "Classification saved." };
    } catch {
      return { error: networkError() };
    }
  }

  async function transitionRecord(
    kind: RecordKind,
    record: KnowledgeRecord,
    target: ReviewState,
  ): Promise<MutationOutcome> {
    const body: components["schemas"]["KnowledgeReviewTransitionRequest"] = {
      expected_version: record.version,
      target,
    };
    try {
      if (kind === "questions") {
        const result = await api.POST(
          "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/questions/{question_id}/review",
          {
            body,
            params: {
              path: {
                curriculum_version_id: selectedCurriculumId,
                question_id: record.id,
              },
            },
          },
        );
        if (!result.response.ok) {
          return mutationFailure(kind, record.id, result.error, result.response.status);
        }
        if (result.data) updateRecord(kind, result.data);
      } else {
        const result = await api.POST(
          "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/chunks/{chunk_id}/review",
          {
            body,
            params: {
              path: {
                chunk_id: record.id,
                curriculum_version_id: selectedCurriculumId,
              },
            },
          },
        );
        if (!result.response.ok) {
          return mutationFailure(kind, record.id, result.error, result.response.status);
        }
        if (result.data) updateRecord(kind, result.data);
      }
      const notice =
        target === "in_review"
          ? "Review started."
          : target === "reviewed"
            ? "Record marked reviewed."
            : "Record rejected.";
      return { notice };
    } catch {
      return { error: networkError() };
    }
  }

  if (workspaceLoading) {
    return (
      <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8">
        <div
          aria-live="polite"
          className="flex items-center gap-3 rounded-xl border border-slate-300 bg-white p-6 text-sm text-slate-600"
          role="status"
        >
          <span aria-hidden="true" className="size-2 animate-pulse rounded-full bg-amber-500" />
          Loading Knowledge Studio…
        </div>
      </div>
    );
  }

  if (workspaceError) {
    return (
      <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8">
        <div className="rounded-xl border border-red-300 bg-red-50 p-6 text-red-950" role="alert">
          <h1 className="text-2xl font-semibold">Knowledge Studio could not be loaded.</h1>
          <p className="mt-2">{workspaceError.message}</p>
          <p className="mt-2 font-mono text-xs">Error code: {workspaceError.code}</p>
          <Button className={`${secondaryButton} mt-5`} onPress={() => void loadWorkspace()}>
            Retry loading Knowledge Studio
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8 lg:py-10">
      <header className="border-b border-slate-300 pb-7">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div>
            <p className="font-mono text-xs font-semibold tracking-[0.18em] text-amber-700 uppercase">
              P3 / reviewed knowledge
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">
              Knowledge Studio
            </h1>
            <p className="mt-3 max-w-3xl leading-7 text-slate-600">
              Normalize trusted source blocks into historical questions and educational chunks,
              inspect immutable provenance and embedding versions, then apply reviewer-confirmed
              taxonomy before final review.
            </p>
          </div>
          <div className="min-w-72 rounded-xl border border-slate-300 bg-white p-4 shadow-sm">
            <label className={fieldClass} htmlFor="active-curriculum">
              Active curriculum
              <select
                className={inputClass}
                id="active-curriculum"
                onChange={(event) => changeCurriculum(event.target.value)}
                value={selectedCurriculumId}
              >
                {!activeCurricula.length && <option value="">No active curriculum</option>}
                {activeCurricula.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.code} — {item.title}
                  </option>
                ))}
              </select>
            </label>
            <p className="mt-2 text-xs text-slate-500">
              Lists, source choices, and taxonomy are always scoped to this version.
            </p>
          </div>
        </div>
      </header>

      {!activeCurricula.length ? (
        <section className="mt-8 rounded-xl border border-dashed border-slate-400 bg-white p-8 text-center">
          <h2 className="text-xl font-semibold">No active curriculum version</h2>
          <p className="mt-2 text-slate-600">
            Create or activate a curriculum before importing or reviewing knowledge records.
          </p>
          <Link className={`${secondaryButton} mt-5`} href="/admin/curriculum">
            Open Curriculum Studio
          </Link>
        </section>
      ) : (
        <Tabs
          className="mt-8"
          onSelectionChange={(key) => changeRecordKind(key === "chunks" ? "chunks" : "questions")}
          selectedKey={activeKind}
        >
          <TabList
            aria-label="Knowledge record type"
            className="flex gap-1 border-b border-slate-300"
          >
            <Tab
              className="cursor-pointer rounded-t-lg border border-transparent px-5 py-3 text-sm font-semibold text-slate-600 outline-none transition hover:text-slate-950 focus-visible:ring-2 focus-visible:ring-amber-500 selected:border-slate-300 selected:border-b-white selected:bg-white selected:text-slate-950"
              id="questions"
            >
              Historical questions ({questions.length})
            </Tab>
            <Tab
              className="cursor-pointer rounded-t-lg border border-transparent px-5 py-3 text-sm font-semibold text-slate-600 outline-none transition hover:text-slate-950 focus-visible:ring-2 focus-visible:ring-amber-500 selected:border-slate-300 selected:border-b-white selected:bg-white selected:text-slate-950"
              id="chunks"
            >
              Knowledge chunks ({chunks.length})
            </Tab>
          </TabList>

          <TabPanel className="outline-none" id="questions">
            {activeKind === "questions" && (
              <KnowledgeWorkspace
                activeKind="questions"
                currentQuery={questionQuery}
                documents={scopedDocuments}
                filterError={filterError}
                importArea={
                  <ImportArea
                    activeKind="questions"
                    blocks={sourceBlocks}
                    busy={importBusy}
                    error={importError}
                    importPermissionDenied={importPermissionDenied}
                    loading={provenanceLoading}
                    normalizedText={normalizedText}
                    notice={importNotice}
                    onBlockChange={chooseBlock}
                    onImport={importQuestion}
                    onNormalizedTextChange={setNormalizedText}
                    onPageChange={choosePage}
                    onRetry={() => {
                      if (selectedPageNumber && selectedSourceId) {
                        void loadSourceBlocks(selectedSourceId, Number(selectedPageNumber));
                      } else {
                        void loadSourcePages(selectedSourceId);
                      }
                    }}
                    onSourceChange={chooseSource}
                    pages={sourcePages}
                    provenanceError={provenanceError}
                    role={role}
                    selectedBlockId={selectedBlockId}
                    selectedPageNumber={selectedPageNumber}
                    selectedSource={selectedSource}
                    selectedSourceId={selectedSourceId}
                    sources={trustedSources}
                  />
                }
                loading={recordsLoading}
                onApplyFilters={applyQuestionFilters}
                onClassify={classifyRecord}
                onNext={() => paginate(1)}
                onPrevious={() => paginate(-1)}
                onRetry={() =>
                  void loadRecords("questions", selectedCurriculumId, questionQuery)
                }
                onTransition={transitionRecord}
                records={questions}
                recordsError={recordsError}
                reviewPermissionDenied={reviewPermissionDenied}
                taxonomy={taxonomy}
                taxonomyError={taxonomyError}
                taxonomyLoading={taxonomyLoading}
              />
            )}
          </TabPanel>

          <TabPanel className="outline-none" id="chunks">
            {activeKind === "chunks" && (
              <KnowledgeWorkspace
                activeKind="chunks"
                currentQuery={chunkQuery}
                documents={scopedDocuments}
                filterError={filterError}
                importArea={
                  <ImportArea
                    activeKind="chunks"
                    blocks={sourceBlocks}
                    busy={importBusy}
                    error={importError}
                    importPermissionDenied={importPermissionDenied}
                    loading={provenanceLoading}
                    normalizedText={normalizedText}
                    notice={importNotice}
                    onBlockChange={chooseBlock}
                    onImport={importChunk}
                    onNormalizedTextChange={setNormalizedText}
                    onPageChange={choosePage}
                    onRetry={() => {
                      if (selectedPageNumber && selectedSourceId) {
                        void loadSourceBlocks(selectedSourceId, Number(selectedPageNumber));
                      } else {
                        void loadSourcePages(selectedSourceId);
                      }
                    }}
                    onSourceChange={chooseSource}
                    pages={sourcePages}
                    provenanceError={provenanceError}
                    role={role}
                    selectedBlockId={selectedBlockId}
                    selectedPageNumber={selectedPageNumber}
                    selectedSource={selectedSource}
                    selectedSourceId={selectedSourceId}
                    sources={trustedSources}
                  />
                }
                loading={recordsLoading}
                onApplyFilters={applyChunkFilters}
                onClassify={classifyRecord}
                onNext={() => paginate(1)}
                onPrevious={() => paginate(-1)}
                onRetry={() => void loadRecords("chunks", selectedCurriculumId, chunkQuery)}
                onTransition={transitionRecord}
                records={chunks}
                recordsError={recordsError}
                reviewPermissionDenied={reviewPermissionDenied}
                taxonomy={taxonomy}
                taxonomyError={taxonomyError}
                taxonomyLoading={taxonomyLoading}
              />
            )}
          </TabPanel>
        </Tabs>
      )}
    </div>
  );
}

function ImportArea({
  activeKind,
  blocks,
  busy,
  error,
  importPermissionDenied,
  loading,
  normalizedText,
  notice,
  onBlockChange,
  onImport,
  onNormalizedTextChange,
  onPageChange,
  onRetry,
  onSourceChange,
  pages,
  provenanceError,
  role,
  selectedBlockId,
  selectedPageNumber,
  selectedSource,
  selectedSourceId,
  sources,
}: {
  activeKind: RecordKind;
  blocks: ExtractedBlock[];
  busy: boolean;
  error: UiError | null;
  importPermissionDenied: boolean;
  loading: boolean;
  normalizedText: string;
  notice: string;
  onBlockChange: (value: string) => void;
  onImport: (event: FormEvent<HTMLFormElement>) => void;
  onNormalizedTextChange: (value: string) => void;
  onPageChange: (value: string) => void;
  onRetry: () => void;
  onSourceChange: (value: string) => void;
  pages: SourcePage[];
  provenanceError: UiError | null;
  role: Role;
  selectedBlockId: string;
  selectedPageNumber: string;
  selectedSource: SourceDocument | undefined;
  selectedSourceId: string;
  sources: SourceDocument[];
}) {
  const denied = role === "reviewer" || importPermissionDenied;
  return (
    <section
      aria-labelledby="import-heading"
      className="rounded-xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="font-mono text-xs text-slate-500 uppercase">Admin-only trusted import</p>
          <h2 className="mt-1 text-2xl font-semibold" id="import-heading">
            Import trusted content
          </h2>
        </div>
        <Badge variant="foundation">Immutable provenance</Badge>
      </div>
      <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
        Source document, page, and block are selected from trusted extraction records. Provenance
        identifiers cannot be typed or changed after import.
      </p>

      {denied ? (
        <div className="mt-6 rounded-lg border border-amber-300 bg-amber-50 p-5 text-amber-950">
          <h3 className="font-semibold">Import permission required</h3>
          <p className="mt-2 text-sm leading-6">
            {role === "reviewer"
              ? "Reviewer access can inspect, classify, and review knowledge but cannot import records."
              : "The service denied knowledge import permission for this admin session."}
          </p>
        </div>
      ) : !sources.length ? (
        <div className="mt-6 rounded-lg border border-dashed border-slate-400 bg-slate-50 p-5">
          <h3 className="font-semibold">No eligible trusted sources</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            {activeKind === "questions"
              ? "A historical question requires a trusted past paper linked to this curriculum with year and paper code metadata."
              : "A knowledge chunk requires a trusted document linked to this curriculum."}
          </p>
          <Link className={`${secondaryButton} mt-4`} href="/admin/documents">
            Open Documents Studio
          </Link>
        </div>
      ) : (
        <Form
          className="mt-6 grid gap-5"
          onSubmit={onImport}
          validationBehavior="native"
        >
          <div className="grid gap-5 md:grid-cols-3">
            <label className={fieldClass} htmlFor={`trusted-source-${activeKind}`}>
              Trusted source document
              <select
                className={inputClass}
                id={`trusted-source-${activeKind}`}
                onChange={(event) => onSourceChange(event.target.value)}
                required
                value={selectedSourceId}
              >
                <option value="">Choose trusted source</option>
                {sources.map((source) => (
                  <option key={source.id} value={source.id}>
                    {documentLabel(source)}
                  </option>
                ))}
              </select>
            </label>

            <label className={fieldClass} htmlFor={`source-page-${activeKind}`}>
              Source page
              <select
                className={inputClass}
                disabled={!selectedSourceId || loading}
                id={`source-page-${activeKind}`}
                onChange={(event) => onPageChange(event.target.value)}
                required
                value={selectedPageNumber}
              >
                <option value="">Choose source page</option>
                {pages.map((page) => (
                  <option key={page.id} value={page.page_number}>
                    Page {page.page_number}
                  </option>
                ))}
              </select>
            </label>

            <label className={fieldClass} htmlFor={`source-block-${activeKind}`}>
              Source block
              <select
                className={inputClass}
                disabled={!selectedPageNumber || loading}
                id={`source-block-${activeKind}`}
                onChange={(event) => onBlockChange(event.target.value)}
                required
                value={selectedBlockId}
              >
                <option value="">Choose source block</option>
                {blocks.map((block) => (
                  <option key={block.id} value={block.id}>
                    Block {block.reading_order + 1} — {block.character_count} characters
                  </option>
                ))}
              </select>
            </label>
          </div>

          {loading && (
            <p className="rounded-lg bg-slate-100 p-3 text-sm text-slate-600" role="status">
              Loading trusted page and block provenance…
            </p>
          )}
          {provenanceError && (
            <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-950" role="alert">
              <p className="font-semibold">Trusted provenance could not be loaded.</p>
              <p className="mt-1">{provenanceError.message}</p>
              <p className="mt-2 font-mono text-xs">Error code: {provenanceError.code}</p>
              <Button className={`${secondaryButton} mt-4`} onPress={onRetry}>
                Retry loading provenance
              </Button>
            </div>
          )}
          {selectedSourceId && !loading && !provenanceError && !pages.length && (
            <p className="rounded-lg border border-dashed border-slate-400 p-4 text-sm text-slate-600">
              This trusted source has no extracted pages to select.
            </p>
          )}
          {selectedPageNumber && !loading && !provenanceError && !blocks.length && (
            <p className="rounded-lg border border-dashed border-slate-400 p-4 text-sm text-slate-600">
              This page has no extracted blocks. Import is unavailable because final review requires
              block-level provenance.
            </p>
          )}

          {activeKind === "questions" ? (
            <QuestionImportFields selectedSource={selectedSource} />
          ) : (
            <ChunkImportFields />
          )}

          <label className={fieldClass} htmlFor={`normalized-text-${activeKind}`}>
            {activeKind === "questions" ? "Normalized question text" : "Normalized chunk text"}
            <textarea
              className={`${inputClass} min-h-32 resize-y`}
              id={`normalized-text-${activeKind}`}
              maxLength={1_000_000}
              onChange={(event) => onNormalizedTextChange(event.target.value)}
              required
              value={normalizedText}
            />
            <span className="font-normal text-slate-500">
              Starts with reviewed block text; normalize conservatively without changing educational
              meaning.
            </span>
          </label>

          {error && (
            <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-950" role="alert">
              <p className="font-semibold">Import was not completed.</p>
              <p className="mt-1">{error.message}</p>
              <p className="mt-2 font-mono text-xs">Error code: {error.code}</p>
            </div>
          )}
          {notice && (
            <p
              className="rounded-lg border border-emerald-300 bg-emerald-50 p-4 text-sm font-semibold text-emerald-950"
              role="status"
            >
              {notice}
            </p>
          )}

          <div className="flex justify-end border-t border-slate-200 pt-5">
            <Button
              className={primaryButton}
              isDisabled={busy || !selectedBlockId || !normalizedText.trim()}
              type="submit"
            >
              {busy
                ? "Importing…"
                : activeKind === "questions"
                  ? "Import historical question"
                  : "Import knowledge chunk"}
            </Button>
          </div>
        </Form>
      )}
    </section>
  );
}

function QuestionImportFields({ selectedSource }: { selectedSource: SourceDocument | undefined }) {
  return (
    <>
      {selectedSource && (
        <dl className="grid gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs font-semibold text-amber-800 uppercase">Immutable year</dt>
            <dd className="mt-1 font-semibold">{selectedSource.year}</dd>
          </div>
          <div>
            <dt className="text-xs font-semibold text-amber-800 uppercase">Immutable paper code</dt>
            <dd className="mt-1 font-semibold">{selectedSource.paper_code}</dd>
          </div>
        </dl>
      )}
      <div className="grid gap-5 md:grid-cols-3">
        <label className={fieldClass} htmlFor="question-number">
          Question number
          <input className={inputClass} id="question-number" maxLength={64} name="question_number" required />
        </label>
        <label className={fieldClass} htmlFor="question-type">
          Question type
          <select className={inputClass} defaultValue="multiple_choice" id="question-type" name="question_type">
            {questionTypes.map((type) => (
              <option key={type.value} value={type.value}>
                {type.label}
              </option>
            ))}
          </select>
        </label>
        <label className={fieldClass} htmlFor="question-marks">
          Marks
          <input className={inputClass} id="question-marks" max={1_000} min={1} name="marks" required type="number" />
        </label>
      </div>
    </>
  );
}

function ChunkImportFields() {
  return (
    <div className="grid gap-5 md:grid-cols-3">
      <label className={fieldClass} htmlFor="chunk-type">
        Chunk type
        <select className={inputClass} defaultValue="explanation" id="chunk-type" name="chunk_type">
          {chunkTypes.map((type) => (
            <option key={type.value} value={type.value}>
              {type.label}
            </option>
          ))}
        </select>
      </label>
      <label className={fieldClass} htmlFor="educational-boundary">
        Educational boundary
        <input className={inputClass} id="educational-boundary" maxLength={512} name="educational_boundary" required />
      </label>
      <label className={fieldClass} htmlFor="chunk-sequence">
        Sequence
        <input className={inputClass} id="chunk-sequence" max={2_147_483_647} min={0} name="sequence" required type="number" />
      </label>
    </div>
  );
}

function KnowledgeWorkspace({
  activeKind,
  currentQuery,
  documents,
  filterError,
  importArea,
  loading,
  onApplyFilters,
  onClassify,
  onNext,
  onPrevious,
  onRetry,
  onTransition,
  records,
  recordsError,
  reviewPermissionDenied,
  taxonomy,
  taxonomyError,
  taxonomyLoading,
}: {
  activeKind: RecordKind;
  currentQuery: QuestionQuery | ChunkQuery;
  documents: SourceDocument[];
  filterError: string;
  importArea: React.ReactNode;
  loading: boolean;
  onApplyFilters: (event: FormEvent<HTMLFormElement>) => void;
  onClassify: (
    kind: RecordKind,
    record: KnowledgeRecord,
    classification: Omit<Classification, "expected_version">,
  ) => Promise<MutationOutcome>;
  onNext: () => void;
  onPrevious: () => void;
  onRetry: () => void;
  onTransition: (
    kind: RecordKind,
    record: KnowledgeRecord,
    target: ReviewState,
  ) => Promise<MutationOutcome>;
  records: KnowledgeRecord[];
  recordsError: UiError | null;
  reviewPermissionDenied: boolean;
  taxonomy: TaxonomyNode[];
  taxonomyError: UiError | null;
  taxonomyLoading: boolean;
}) {
  const limit = currentQuery.limit ?? DEFAULT_LIMIT;
  const offset = currentQuery.offset ?? 0;
  const hasNext = records.length === limit && offset + limit <= MAX_OFFSET;
  return (
    <div className="grid gap-8 pt-8">
      {importArea}

      <section aria-labelledby={`${activeKind}-list-heading`}>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="font-mono text-xs text-slate-500 uppercase">Bounded PostgreSQL list</p>
            <h2 className="mt-1 text-2xl font-semibold" id={`${activeKind}-list-heading`}>
              {activeKind === "questions" ? "Historical questions" : "Knowledge chunks"}
            </h2>
          </div>
          <p className="text-sm text-slate-500">
            Showing offset {offset} · maximum {MAX_LIMIT} records per request
          </p>
        </div>

        <FilterForm
          activeKind={activeKind}
          currentQuery={currentQuery}
          key={JSON.stringify(currentQuery)}
          documents={documents}
          error={filterError}
          onSubmit={onApplyFilters}
          taxonomy={taxonomy}
        />

        {taxonomyLoading && (
          <p className="mt-4 rounded-lg bg-slate-100 p-3 text-sm text-slate-600" role="status">
            Loading current curriculum taxonomy…
          </p>
        )}
        {taxonomyError && (
          <p className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950" role="alert">
            Taxonomy selectors are unavailable: {taxonomyError.message}
          </p>
        )}
        {reviewPermissionDenied && (
          <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-amber-950">
            <h3 className="font-semibold">Review permission required</h3>
            <p className="mt-1 text-sm">
              The service denied classification or review permission. Record inspection remains
              available.
            </p>
          </div>
        )}

        {loading && (
          <p
            className="mt-5 flex items-center gap-3 rounded-xl border border-slate-300 bg-white p-5 text-sm text-slate-600"
            role="status"
          >
            <span aria-hidden="true" className="size-2 animate-pulse rounded-full bg-amber-500" />
            Loading {activeKind === "questions" ? "historical questions" : "knowledge chunks"}…
          </p>
        )}

        {!loading && recordsError && (
          <div className="mt-5 rounded-xl border border-red-300 bg-red-50 p-5 text-sm text-red-950" role="alert">
            {recordsError.code === "permission_denied" ? (
              <>
                <h3 className="font-semibold">Knowledge read permission required</h3>
                <p className="mt-1">Your account cannot inspect records in this curriculum.</p>
              </>
            ) : (
              <>
                <h3 className="font-semibold">Knowledge records could not be loaded.</h3>
                <p className="mt-1">{recordsError.message}</p>
              </>
            )}
            <p className="mt-2 font-mono text-xs">Error code: {recordsError.code}</p>
            <Button className={`${secondaryButton} mt-4`} onPress={onRetry}>
              Retry loading records
            </Button>
          </div>
        )}

        {!loading && !recordsError && !records.length && (
          <div className="mt-5 rounded-xl border border-dashed border-slate-400 bg-white p-8 text-center">
            <p className="font-semibold text-slate-700">
              No {activeKind === "questions" ? "historical questions" : "knowledge chunks"} match
              these filters.
            </p>
            <p className="mt-2 text-sm text-slate-500">
              Change the bounded filters or import a trusted source block.
            </p>
          </div>
        )}

        {!loading && !recordsError && records.length > 0 && (
          <div className="mt-5 grid gap-5">
            {records.map((record) => (
              <KnowledgeRecordCard
                documents={documents}
                key={record.id}
                kind={activeKind}
                onClassify={onClassify}
                onTransition={onTransition}
                record={record}
                reviewPermissionDenied={reviewPermissionDenied}
                taxonomy={taxonomy}
              />
            ))}
          </div>
        )}

        {!loading && !recordsError && (
          <nav aria-label={`${activeKind} pagination`} className="mt-5 flex items-center justify-between gap-4">
            <Button className={secondaryButton} isDisabled={offset === 0} onPress={onPrevious}>
              Previous page
            </Button>
            <span className="text-sm text-slate-500">
              Records {records.length ? offset + 1 : 0}–{offset + records.length}
            </span>
            <Button className={secondaryButton} isDisabled={!hasNext} onPress={onNext}>
              Next page
            </Button>
          </nav>
        )}
      </section>
    </div>
  );
}

function FilterForm({
  activeKind,
  currentQuery,
  documents,
  error,
  onSubmit,
  taxonomy,
}: {
  activeKind: RecordKind;
  currentQuery: QuestionQuery | ChunkQuery;
  documents: SourceDocument[];
  error: string;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  taxonomy: TaxonomyNode[];
}) {
  const competencies = taxonomy.filter(
    (node) => node.level === "competency" && node.active && node.review_state === "reviewed",
  );
  return (
    <Form
      className="mt-5 grid gap-4 rounded-xl border border-slate-300 bg-slate-100/70 p-4 md:grid-cols-3 xl:grid-cols-6"
      onSubmit={onSubmit}
    >
      <label className={fieldClass}>
        Review state
        <select className={inputClass} defaultValue={currentQuery.review_state ?? ""} name="review_state">
          <option value="">All states</option>
          {reviewStates.map((state) => (
            <option key={state.value} value={state.value}>
              {state.label}
            </option>
          ))}
        </select>
      </label>
      <label className={fieldClass}>
        Source document
        <select className={inputClass} defaultValue={currentQuery.source_document_id ?? ""} name="source_document_id">
          <option value="">All sources</option>
          {documents.map((document) => (
            <option key={document.id} value={document.id}>
              {document.original_filename}
            </option>
          ))}
        </select>
      </label>
      <label className={fieldClass}>
        Competency filter
        <select className={inputClass} defaultValue={currentQuery.competency_id ?? ""} name="competency_id">
          <option value="">All competencies</option>
          {competencies.map((node) => (
            <option key={node.id} value={node.id}>
              {node.code} — {node.title}
            </option>
          ))}
        </select>
      </label>

      {activeKind === "questions" ? (
        <>
          <label className={fieldClass}>
            Question type
            <select className={inputClass} defaultValue={(currentQuery as QuestionQuery).question_type ?? ""} name="question_type">
              <option value="">All types</option>
              {questionTypes.map((type) => (
                <option key={type.value} value={type.value}>
                  {type.label}
                </option>
              ))}
            </select>
          </label>
          <label className={fieldClass}>
            Year
            <input className={inputClass} defaultValue={(currentQuery as QuestionQuery).year ?? ""} max={2100} min={1900} name="year" type="number" />
          </label>
          <label className={fieldClass}>
            Paper code
            <input className={inputClass} defaultValue={(currentQuery as QuestionQuery).paper_code ?? ""} maxLength={64} name="paper_code" />
          </label>
        </>
      ) : (
        <label className={fieldClass}>
          Chunk type
          <select className={inputClass} defaultValue={(currentQuery as ChunkQuery).chunk_type ?? ""} name="chunk_type">
            <option value="">All types</option>
            {chunkTypes.map((type) => (
              <option key={type.value} value={type.value}>
                {type.label}
              </option>
            ))}
          </select>
        </label>
      )}

      <label className={fieldClass}>
        Records per page
        <select className={inputClass} defaultValue={currentQuery.limit ?? DEFAULT_LIMIT} name="limit">
          {PAGE_SIZES.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </label>
      <div className="flex items-end">
        <Button className={`${secondaryButton} w-full`} type="submit">
          Apply filters
        </Button>
      </div>
      {error && (
        <p className="text-sm text-red-800 md:col-span-3 xl:col-span-6" role="alert">
          {error}
        </p>
      )}
    </Form>
  );
}

function KnowledgeRecordCard({
  documents,
  kind,
  onClassify,
  onTransition,
  record,
  reviewPermissionDenied,
  taxonomy,
}: {
  documents: SourceDocument[];
  kind: RecordKind;
  onClassify: (
    kind: RecordKind,
    record: KnowledgeRecord,
    classification: Omit<Classification, "expected_version">,
  ) => Promise<MutationOutcome>;
  onTransition: (
    kind: RecordKind,
    record: KnowledgeRecord,
    target: ReviewState,
  ) => Promise<MutationOutcome>;
  record: KnowledgeRecord;
  reviewPermissionDenied: boolean;
  taxonomy: TaxonomyNode[];
}) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<UiError | null>(null);
  const source = documents.find(
    (document) => document.id === record.provenance.source_document_id,
  );
  const final = record.review_state === "reviewed" || record.review_state === "rejected";
  const heading = isQuestion(record)
    ? `${record.paper_code} / Question ${record.question_number}`
    : `${record.educational_boundary} / Sequence ${record.sequence}`;

  async function run(action: () => Promise<MutationOutcome>) {
    setBusy(true);
    setNotice("");
    setError(null);
    const outcome = await action();
    setBusy(false);
    setNotice(outcome.notice ?? "");
    setError(outcome.error ?? null);
  }

  return (
    <article className="rounded-xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 pb-5">
        <div className="min-w-0">
          <p className="font-mono text-xs text-slate-500 uppercase">
            {isQuestion(record)
              ? questionTypes.find((type) => type.value === record.question_type)?.label
              : chunkTypes.find((type) => type.value === record.chunk_type)?.label}
          </p>
          <h3 className="mt-1 break-words text-xl font-semibold">{heading}</h3>
          <p className="mt-2 text-sm text-slate-500">Version {record.version}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`rounded-full px-3 py-1 text-xs font-semibold capitalize ${
              record.review_state === "reviewed"
                ? "bg-emerald-100 text-emerald-900"
                : record.review_state === "rejected"
                  ? "bg-red-100 text-red-900"
                  : record.review_state === "in_review"
                    ? "bg-sky-100 text-sky-900"
                    : "bg-slate-100 text-slate-800"
            }`}
          >
            {displayState(record.review_state)}
          </span>
          <span className="rounded-full bg-violet-100 px-3 py-1 text-xs font-semibold text-violet-900">
            {displayEmbeddingStatus(record.embedding_status)}
          </span>
        </div>
      </div>

      <section aria-label={`Normalized text for ${heading}`} className="mt-5">
        <h4 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
          Normalized content
        </h4>
        <p className="mt-2 whitespace-pre-wrap rounded-lg bg-slate-950 p-4 leading-7 text-white">
          {record.text}
        </p>
        {isQuestion(record) && (
          <dl className="mt-3 flex flex-wrap gap-x-8 gap-y-2 text-sm text-slate-600">
            <div>
              <dt className="inline font-semibold">Year: </dt>
              <dd className="inline">{record.year}</dd>
            </div>
            <div>
              <dt className="inline font-semibold">Marks: </dt>
              <dd className="inline">{record.marks}</dd>
            </div>
          </dl>
        )}
      </section>

      <div className="mt-6 grid gap-5 lg:grid-cols-2">
        <section className="rounded-lg border border-amber-200 bg-amber-50 p-4">
          <h4 className="font-semibold">Immutable provenance</h4>
          <dl className="mt-3 grid gap-3 text-sm">
            <div>
              <dt className="text-xs text-amber-800 uppercase">Trusted source</dt>
              <dd className="mt-1 break-words font-semibold">
                {source?.original_filename ?? "Source metadata unavailable"}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-amber-800 uppercase">Source document ID</dt>
              <dd className="mt-1 break-all font-mono text-xs">
                {record.provenance.source_document_id}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-amber-800 uppercase">Page</dt>
              <dd className="mt-1 font-semibold">{record.provenance.page_number}</dd>
            </div>
            <div>
              <dt className="text-xs text-amber-800 uppercase">Source block ID</dt>
              <dd className="mt-1 break-all font-mono text-xs">
                {record.provenance.source_block_id ?? "No block attached"}
              </dd>
            </div>
          </dl>
        </section>

        <section className="rounded-lg border border-violet-200 bg-violet-50 p-4">
          <h4 className="font-semibold">Embedding metadata</h4>
          <p className="mt-2 text-sm">
            Status: <strong>{displayEmbeddingStatus(record.embedding_status)}</strong>
          </p>
          {!record.embedding_configurations.length ? (
            <p className="mt-3 text-sm text-violet-800">
              No versioned embedding configuration is attached.
            </p>
          ) : (
            <ul className="mt-3 grid gap-3">
              {record.embedding_configurations.map((configuration) => (
                <li className="rounded-md border border-violet-200 bg-white p-3 text-sm" key={configuration.id}>
                  <p className="flex flex-wrap gap-1 font-semibold">
                    <span>{configuration.provider}</span>
                    <span aria-hidden="true">/</span>
                    <span>{configuration.model}</span>
                  </p>
                  <dl className="mt-2 grid gap-1 text-xs text-slate-600">
                    <div>
                      <dt className="inline font-semibold">Version: </dt>
                      <dd className="inline">{configuration.version}</dd>
                    </div>
                    <div>
                      <dt className="inline font-semibold">Dimension: </dt>
                      <dd className="inline">{configuration.dimension}</dd>
                    </div>
                    <div>
                      <dt className="inline font-semibold">Fingerprint: </dt>
                      <dd className="inline break-all font-mono">{configuration.config_fingerprint}</dd>
                    </div>
                  </dl>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="mt-6 border-t border-slate-200 pt-5" aria-label={`Classification and review for ${heading}`}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h4 className="font-semibold">Reviewer-confirmed classification</h4>
            <p className="mt-1 text-sm text-slate-500">
              Select a valid parent-to-child path from the current curriculum taxonomy.
            </p>
          </div>
          {final && (
            <span className="rounded-full bg-slate-950 px-3 py-1 text-xs font-semibold text-white">
              Final record — read-only
            </span>
          )}
        </div>

        {final || reviewPermissionDenied ? (
          <ClassificationSummary classification={record.classification} taxonomy={taxonomy} />
        ) : (
          <ClassificationEditor
            busy={busy}
            classification={record.classification}
            key={record.version}
            onSave={(classification) =>
              void run(() => onClassify(kind, record, classification))
            }
            taxonomy={taxonomy}
          />
        )}

        {!final && !reviewPermissionDenied && (
          <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-slate-200 pt-5">
            {record.review_state === "draft" && (
              <Button
                className={secondaryButton}
                isDisabled={busy}
                onPress={() => void run(() => onTransition(kind, record, "in_review"))}
              >
                Start review
              </Button>
            )}
            {record.review_state === "in_review" && (
              <>
                <Button
                  className={primaryButton}
                  isDisabled={
                    busy ||
                    !record.classification.competency_id ||
                    !record.provenance.source_block_id
                  }
                  onPress={() => void run(() => onTransition(kind, record, "reviewed"))}
                >
                  Mark reviewed
                </Button>
                <Button
                  className={dangerButton}
                  isDisabled={busy}
                  onPress={() => void run(() => onTransition(kind, record, "rejected"))}
                >
                  Reject record
                </Button>
              </>
            )}
            {record.review_state === "in_review" &&
              (!record.classification.competency_id || !record.provenance.source_block_id) && (
                <p className="text-sm text-amber-800">
                  Select a competency and retain source-block provenance before marking reviewed.
                </p>
              )}
          </div>
        )}

        {notice && (
          <p className="mt-4 rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm font-semibold text-emerald-950" role="status">
            {notice}
          </p>
        )}
        {error && (
          <div className="mt-4 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-950" role="alert">
            <p className="font-semibold">{error.message}</p>
            <p className="mt-1 font-mono text-xs">Error code: {error.code}</p>
          </div>
        )}
      </section>
    </article>
  );
}

function ClassificationEditor({
  busy,
  classification,
  onSave,
  taxonomy,
}: {
  busy: boolean;
  classification: components["schemas"]["KnowledgeClassificationResponse"];
  onSave: (classification: Omit<Classification, "expected_version">) => void;
  taxonomy: TaxonomyNode[];
}) {
  const [competencyId, setCompetencyId] = useState(classification.competency_id ?? "");
  const [skillId, setSkillId] = useState(classification.skill_id ?? "");
  const [subSkillId, setSubSkillId] = useState(classification.sub_skill_id ?? "");
  const [conceptId, setConceptId] = useState(classification.learning_concept_id ?? "");

  const nodes = taxonomy.filter((node) => node.active && node.review_state === "reviewed");
  const competencies = nodes.filter((node) => node.level === "competency");
  const skills = nodes.filter(
    (node) => node.level === "skill" && node.parent_id === competencyId,
  );
  const subSkills = nodes.filter(
    (node) => node.level === "sub_skill" && node.parent_id === skillId,
  );
  const concepts = nodes.filter(
    (node) => node.level === "learning_concept" && node.parent_id === subSkillId,
  );

  return (
    <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-5">
      <label className={fieldClass}>
        Competency
        <select
          className={inputClass}
          disabled={busy}
          onChange={(event) => {
            setCompetencyId(event.target.value);
            setSkillId("");
            setSubSkillId("");
            setConceptId("");
          }}
          value={competencyId}
        >
          <option value="">Not classified</option>
          {competencies.map((node) => (
            <option key={node.id} value={node.id}>
              {node.code} — {node.title}
            </option>
          ))}
        </select>
      </label>
      <label className={fieldClass}>
        Skill
        <select
          className={inputClass}
          disabled={busy || !competencyId}
          onChange={(event) => {
            setSkillId(event.target.value);
            setSubSkillId("");
            setConceptId("");
          }}
          value={skillId}
        >
          <option value="">Not classified</option>
          {skills.map((node) => (
            <option key={node.id} value={node.id}>
              {node.code} — {node.title}
            </option>
          ))}
        </select>
      </label>
      <label className={fieldClass}>
        Sub-skill
        <select
          className={inputClass}
          disabled={busy || !skillId}
          onChange={(event) => {
            setSubSkillId(event.target.value);
            setConceptId("");
          }}
          value={subSkillId}
        >
          <option value="">Not classified</option>
          {subSkills.map((node) => (
            <option key={node.id} value={node.id}>
              {node.code} — {node.title}
            </option>
          ))}
        </select>
      </label>
      <label className={fieldClass}>
        Learning concept
        <select
          className={inputClass}
          disabled={busy || !subSkillId}
          onChange={(event) => setConceptId(event.target.value)}
          value={conceptId}
        >
          <option value="">Not classified</option>
          {concepts.map((node) => (
            <option key={node.id} value={node.id}>
              {node.code} — {node.title}
            </option>
          ))}
        </select>
      </label>
      <Button
        className={`${secondaryButton} self-end`}
        isDisabled={busy || !competencies.length}
        onPress={() =>
          onSave({
            competency_id: competencyId || null,
            learning_concept_id: conceptId || null,
            skill_id: skillId || null,
            sub_skill_id: subSkillId || null,
          })
        }
      >
        Save classification
      </Button>
    </div>
  );
}

function ClassificationSummary({
  classification,
  taxonomy,
}: {
  classification: components["schemas"]["KnowledgeClassificationResponse"];
  taxonomy: TaxonomyNode[];
}) {
  const labels = [
    ["Competency", classification.competency_id],
    ["Skill", classification.skill_id],
    ["Sub-skill", classification.sub_skill_id],
    ["Learning concept", classification.learning_concept_id],
  ] as const;
  return (
    <dl className="mt-4 grid gap-3 rounded-lg bg-slate-100 p-4 sm:grid-cols-2 lg:grid-cols-4">
      {labels.map(([label, id]) => {
        const node = taxonomy.find((item) => item.id === id);
        return (
          <div key={label}>
            <dt className="text-xs font-semibold text-slate-500 uppercase">{label}</dt>
            <dd className="mt-1 text-sm font-semibold">
              {node ? `${node.code} — ${node.title}` : id ?? "Not classified"}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
