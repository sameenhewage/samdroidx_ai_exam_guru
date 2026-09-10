"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type ReactNode,
} from "react";

import {
  ResumableSourceUpload,
  recoverUploadCheckpoints,
  UploadFailure,
  uploadFailureMessage,
  type UploadMetadata,
  type UploadProgress,
  type UploadSession,
} from "@/lib/source-upload";
import {
  finishUploadCheckpoint,
  readUploadCheckpoints,
  recordUploadCheckpoint,
  type UploadCheckpoints,
} from "@/lib/source-upload-checkpoint";

import type { AdminRole } from "./admin-header";
import { MaterialIntakeMetadata } from "./material-details";

type CatalogueEntry = components["schemas"]["MaterialCatalogueEntry"];
type GradeSummary = components["schemas"]["MaterialGradeSummaryResponse"];
type Lesson = components["schemas"]["CurriculumLessonResponse"];
type Material = components["schemas"]["MaterialListItemResponse"];
type MaterialStatus = components["schemas"]["MaterialStatus"];
type MaterialType = components["schemas"]["SourceDocumentType"];
type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type Unit = components["schemas"]["CurriculumUnitResponse"];
type RemoveBody = components["schemas"]["MaterialRemoveRequest"];
type RestoreBody = components["schemas"]["MaterialRestoreRequest"];
type ScopeBody = components["schemas"]["MaterialScopeCorrectionRequest"];
type MetadataCandidateBody =
  components["schemas"]["MaterialMetadataCandidateRequest"];
type MetadataDraft = {
  scopeVersion: number;
  candidateVersion: number;
  grade: string;
  medium: string;
  subject: string;
  type: string;
  year: string;
  reason: string;
};

type UiError = Readonly<{
  code: string;
  message: string;
  status?: number;
}>;

type WizardStep = 0 | 1 | 2 | 3 | 4 | 5 | 6;

const grades = Array.from({ length: 13 }, (_, index) => index + 1);
const intakeMedia = [
  "Sinhala",
  "Tamil",
  "English",
  "Mixed / other",
  "Not sure",
];
const materialTypesWithYear: readonly MaterialType[] = [
  "past_paper",
  "marking_scheme",
  "evaluation_report",
];
const wizardStepLabels = [
  "Grade",
  "Medium",
  "Subject",
  "Material type",
  "Year or curriculum",
  "PDF",
  "Review",
] as const;

const materialTypes: ReadonlyArray<{ label: string; value: MaterialType }> = [
  { label: "Syllabus", value: "syllabus" },
  { label: "Teacher Guide", value: "teacher_guide" },
  { label: "Past Paper", value: "past_paper" },
  { label: "Marking Scheme", value: "marking_scheme" },
  { label: "Evaluation / Examiner Report", value: "evaluation_report" },
  { label: "Other approved material", value: "other_approved" },
];

const candidateMaterialTypes: readonly {
  label: string;
  value: MaterialType;
  sinhala: string;
}[] = [
  { label: "Syllabus", value: "syllabus", sinhala: "විෂය නිර්දේශය" },
  {
    label: "Teacher Guide",
    value: "teacher_guide",
    sinhala: "ගුරු මාර්ගෝපදේශය",
  },
  {
    label: "Past Paper",
    value: "past_paper",
    sinhala: "පසුගිය ප්‍රශ්න පත්‍රය",
  },
  {
    label: "Marking Scheme",
    value: "marking_scheme",
    sinhala: "ලකුණු දීමේ පටිපාටිය",
  },
  {
    label: "Evaluation / Examiner Report",
    value: "evaluation_report",
    sinhala: "විභාග ඇගයීම් වාර්තාව",
  },
  {
    label: "Worksheet",
    value: "other_approved",
    sinhala: "ක්‍රියාකාරකම් පත්‍රිකාව",
  },
  { label: "Workbook", value: "other_approved", sinhala: "වැඩ පොත" },
  {
    label: "Other material",
    value: "other_approved",
    sinhala: "වෙනත් ද්‍රව්‍ය",
  },
];

const materialTypeLabels: Record<MaterialType, string> = Object.fromEntries(
  materialTypes.map(({ label, value }) => [value, label]),
) as Record<MaterialType, string>;

const statusLabels: Record<MaterialStatus, string> = {
  needs_review: "Needs review",
  processing: "Processing",
  ready_for_ai: "Ready for AI",
  removed: "Removed",
};

const statusClasses: Record<MaterialStatus, string> = {
  needs_review: "border-amber-300 bg-amber-50 text-amber-950",
  processing: "border-sky-300 bg-sky-50 text-sky-950",
  ready_for_ai: "border-emerald-300 bg-emerald-50 text-emerald-950",
  removed: "border-slate-300 bg-slate-100 text-slate-700",
};

const fieldClass = "grid gap-1.5 text-sm font-semibold text-slate-800";
const inputClass =
  "min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-slate-950 outline-none focus:border-amber-600 focus:ring-2 focus:ring-amber-200 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none hover:border-slate-500 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const dangerButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-red-800 px-5 py-2.5 text-sm font-semibold text-white outline-none hover:bg-red-700 focus-visible:ring-2 focus-visible:ring-red-700 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

const MATERIAL_PAGE_LIMIT = 100;

type MaterialDiscovery = {
  error?: UiError;
  hasNext: boolean;
  items: Material[];
};

const errorMessages: Record<string, string> = {
  authentication_required:
    "Your session has expired. Sign in again before retrying.",
  concurrent_material_scope_modification:
    "This material changed in another session. Close this window, refresh Materials, and try again.",
  metadata_candidate_changed:
    "The descriptions changed after you opened this material. Reopen it and compare the current details before confirming.",
  curriculum_version_inactive:
    "That curriculum is no longer active. Choose another curriculum.",
  empty_file: "The selected PDF is empty.",
  file_too_large:
    "The selected PDF is larger than the configured upload limit.",
  invalid_pdf_signature: "The selected file is not a valid PDF.",
  invalid_removal_reason: "Enter a reason using 1–512 printable characters.",
  learning_scope_inactive:
    "That unit or lesson is no longer active. Choose an active option.",
  learning_scope_mismatch:
    "The unit or lesson does not belong to the selected curriculum.",
  material_scope_inactive:
    "That curriculum, unit, or lesson is no longer active.",
  material_scope_mismatch:
    "The selected unit or lesson does not belong to that curriculum.",
  material_scope_not_found:
    "That curriculum, unit, or lesson could not be found.",
  network_error:
    "The connection was interrupted. Your choices are still here; try again.",
  permission_denied:
    "Your account does not have permission to make this change.",
  request_too_large: "The upload is larger than the same-origin request limit.",
  service_unavailable:
    "Materials are temporarily unavailable. Try again shortly.",
  trusted_material_scope_immutable_remove_from_use:
    "This material already has trusted or downstream content. Remove it from use instead, then upload a correctly scoped version so provenance remains intact.",
  unsafe_filename:
    "Rename the PDF to remove path or control characters, then try again.",
  unsupported_media_type: "Only PDF files can be uploaded.",
};

function errorCode(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail;
    if (
      detail &&
      typeof detail === "object" &&
      !Array.isArray(detail) &&
      "code" in detail
    ) {
      return String((detail as { code: unknown }).code);
    }
  }
  return "request_failed";
}

function uiError(error: unknown, status?: number): UiError {
  const code = errorCode(error);
  return {
    code,
    message:
      errorMessages[code] ??
      "The request could not be completed. Your choices have been kept so you can try again.",
    status,
  };
}

function networkError(): UiError {
  return { code: "network_error", message: errorMessages.network_error };
}

function catalogueChoices(
  entries: readonly CatalogueEntry[],
  kind: "medium" | "subject",
) {
  return [
    ...new Map(
      entries.map((entry) => [
        entry[`${kind}_id`],
        { id: entry[`${kind}_id`], name: entry[`${kind}_name`] },
      ]),
    ).values(),
  ];
}

function emptyGradeSummary(grade: number | null): GradeSummary {
  return {
    grade,
    material_count: 0,
    needs_review_count: 0,
    processing_count: 0,
    ready_count: 0,
    removed_count: 0,
    subject_count: 0,
  };
}

function completeGradeSummaries(
  summaries: readonly GradeSummary[],
): GradeSummary[] {
  const byGrade = new Map(summaries.map((summary) => [summary.grade, summary]));
  return [...grades, null].map(
    (grade) => byGrade.get(grade) ?? emptyGradeSummary(grade),
  );
}

function examBadge(grade: number | null): string | null {
  if (grade === 5) return "Scholarship";
  if (grade === 11) return "O/L";
  if (grade === 13) return "A/L";
  return null;
}

function plural(
  count: number,
  singular: string,
  pluralForm = `${singular}s`,
): string {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Date unavailable";
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
    year: "numeric",
  }).format(date);
}

function formatBytes(bytes: number): string {
  if (bytes < 1_024) return `${bytes} B`;
  if (bytes < 1_024 * 1_024) return `${(bytes / 1_024).toFixed(1)} KB`;
  return `${(bytes / (1_024 * 1_024)).toFixed(1)} MB`;
}

function intakeLabelError(value: string, label: string): UiError | null {
  const text = value.trim();
  return Array.from(text).length > 200 || /(?! )[\p{C}\p{Z}]/u.test(text)
    ? {
        code: "intake_label_invalid",
        message: `Use up to 200 characters for the ${label}, without hidden formatting.`,
      }
    : null;
}

function validatePdf(file: File | null): UiError | null {
  if (!file)
    return { code: "pdf_required", message: "Choose a PDF file to continue." };
  if (file.size === 0)
    return { code: "empty_file", message: errorMessages.empty_file };
  if (
    file.type !== "application/pdf" ||
    !file.name.toLocaleLowerCase().endsWith(".pdf")
  ) {
    return {
      code: "invalid_pdf_selection",
      message: "Choose a PDF file with a .pdf name.",
    };
  }
  if (
    file.name !== file.name.normalize("NFC") ||
    file.name.includes("/") ||
    file.name.includes("\\") ||
    [...file.name].some((character) => {
      const point = character.codePointAt(0) ?? 0;
      return point < 32 || point === 127;
    })
  ) {
    return { code: "unsafe_filename", message: errorMessages.unsafe_filename };
  }
  return null;
}

function Modal({
  children,
  labelledBy,
  onClose,
}: {
  children: ReactNode;
  labelledBy: string;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    const previousFocus =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const dialog = dialogRef.current;
    const focusable = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );
    focusable()[0]?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previousFocus?.focus();
    };
  }, []);

  return (
    <div
      aria-labelledby={labelledBy}
      aria-modal="true"
      className="fixed inset-0 z-50 grid items-start overflow-y-auto bg-slate-950/65 p-4 sm:items-center sm:p-8"
      ref={dialogRef}
      role="dialog"
    >
      <div className="mx-auto w-full max-w-3xl rounded-2xl bg-[#f8f8f4] p-5 shadow-2xl sm:p-7">
        {children}
      </div>
    </div>
  );
}

function InlineError({ error, title }: { error: UiError; title: string }) {
  return (
    <div
      className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-950"
      role="alert"
    >
      <p className="font-semibold">{title}</p>
      <p className="mt-1 leading-6">{error.message}</p>
    </div>
  );
}

export function MaterialsLibrary({ role }: { role: AdminRole }) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [summaries, setSummaries] = useState<GradeSummary[]>([]);
  const [catalogue, setCatalogue] = useState<CatalogueEntry[]>([]);
  const [sources, setSources] = useState<SourceDocument[]>([]);
  const [materials, setMaterials] = useState<Material[]>([]);
  const [selectedGrade, setSelectedGrade] = useState<number | null>(5);
  const [selectedSubject, setSelectedSubject] = useState("");
  const [materialSearch, setMaterialSearch] = useState("");
  const [selectedMedium, setSelectedMedium] = useState("");
  const [selectedMaterialType, setSelectedMaterialType] = useState<
    MaterialType | ""
  >("");
  const [selectedMaterialStatus, setSelectedMaterialStatus] = useState<
    MaterialStatus | ""
  >("");
  const [selectedYear, setSelectedYear] = useState("");
  const [workspaceLoading, setWorkspaceLoading] = useState(true);
  const [workspaceError, setWorkspaceError] = useState<UiError | null>(null);
  const [materialsLoading, setMaterialsLoading] = useState(false);
  const [materialsError, setMaterialsError] = useState<UiError | null>(null);
  const [materialsOffset, setMaterialsOffset] = useState(0);
  const [materialsHasNext, setMaterialsHasNext] = useState(false);
  const materialsRequestId = useRef(0);
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState<UiError | null>(null);

  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardStep, setWizardStep] = useState<WizardStep>(0);
  const [wizardGrade, setWizardGrade] = useState("");
  const [wizardMediumId, setWizardMediumId] = useState("");
  const [wizardSubjectId, setWizardSubjectId] = useState("");
  const [wizardManualDetails, setWizardManualDetails] = useState(false);
  const [wizardIntakeMedium, setWizardIntakeMedium] = useState("");
  const [wizardIntakeSubject, setWizardIntakeSubject] = useState("");
  const [wizardIntakeCurriculum, setWizardIntakeCurriculum] = useState("");
  const [wizardMaterialType, setWizardMaterialType] =
    useState<MaterialType>("syllabus");
  const [wizardYear, setWizardYear] = useState("");
  const [wizardCurriculumId, setWizardCurriculumId] = useState("");
  const [wizardUnitId, setWizardUnitId] = useState("");
  const [wizardLessonId, setWizardLessonId] = useState("");
  const [wizardUnits, setWizardUnits] = useState<Unit[]>([]);
  const [wizardLessons, setWizardLessons] = useState<Lesson[]>([]);
  const [wizardScopeLoading, setWizardScopeLoading] = useState(false);
  const [wizardFile, setWizardFile] = useState<File | null>(null);
  const [wizardError, setWizardError] = useState<UiError | null>(null);
  const [uploading, setUploading] = useState(false);
  const [duplicate, setDuplicate] = useState<Pick<
    SourceDocument,
    "id" | "original_filename"
  > | null>(null);
  const [checkpoints, setCheckpoints] = useState<UploadCheckpoints>({
    uploadIds: [],
    requestIds: [],
    creationUncertain: false,
  });
  const [checkpointError, setCheckpointError] = useState("");
  const [checkpointLoading, setCheckpointLoading] = useState(true);
  const [uploadRequestId, setUploadRequestId] = useState("");
  const [continuingRequest, setContinuingRequest] = useState(false);
  const recoveryRequest = useRef(0);
  const recoveryController = useRef<AbortController | null>(null);
  const [activeUpload, setActiveUpload] = useState<UploadSession | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(
    null,
  );
  const [resumeId, setResumeId] = useState("");
  const [resumeLoading, setResumeLoading] = useState(false);
  const [uploadRetryBlocked, setUploadRetryBlocked] = useState(false);
  const [retryDelayMs, setRetryDelayMs] = useState(0);
  const [uploadedDocumentId, setUploadedDocumentId] = useState("");
  const uploadTask = useRef<ResumableSourceUpload | null>(null);
  const uploadController = useRef<AbortController | null>(null);
  const uploadRunning = useRef(false);
  const uploadViewRequest = useRef(0);

  const [removeTarget, setRemoveTarget] = useState<Material | null>(null);
  const [removeReason, setRemoveReason] = useState("");
  const [removeError, setRemoveError] = useState<UiError | null>(null);
  const [removing, setRemoving] = useState(false);
  const [restoringId, setRestoringId] = useState<string | null>(null);

  const [scopeTarget, setScopeTarget] = useState<Material | null>(null);
  const [scopeEditing, setScopeEditing] = useState(false);
  const [scopeCandidateId, setScopeCandidateId] = useState<string | null>(null);
  const [metadataDraft, setMetadataDraft] = useState<MetadataDraft | null>(
    null,
  );
  const [scopeGrade, setScopeGrade] = useState("");
  const [scopeMediumId, setScopeMediumId] = useState("");
  const [scopeSubjectId, setScopeSubjectId] = useState("");
  const [scopeCurriculumId, setScopeCurriculumId] = useState("");
  const [scopeUnitId, setScopeUnitId] = useState("");
  const [scopeLessonId, setScopeLessonId] = useState("");
  const [scopeUnits, setScopeUnits] = useState<Unit[]>([]);
  const [scopeLessons, setScopeLessons] = useState<Lesson[]>([]);
  const [scopeLoading, setScopeLoading] = useState(false);
  const [scopeSaving, setScopeSaving] = useState(false);
  const [confirmIntakeMetadata, setConfirmIntakeMetadata] = useState(false);
  const [scopeError, setScopeError] = useState<UiError | null>(null);

  const sourceById = useMemo(
    () => new Map(sources.map((source) => [source.id, source])),
    [sources],
  );
  const curriculumById = useMemo(
    () =>
      new Map(catalogue.map((entry) => [entry.curriculum_version_id, entry])),
    [catalogue],
  );
  const gradeCatalogue = catalogue.filter(
    (entry) => selectedGrade === null || entry.grade === selectedGrade,
  );
  const filterMedia = catalogueChoices(gradeCatalogue, "medium");
  const filterSubjects = catalogueChoices(
    gradeCatalogue.filter(
      (entry) => !selectedMedium || entry.medium_id === selectedMedium,
    ),
    "subject",
  );
  const wizardGradeCatalogue = catalogue.filter(
    (entry) => entry.grade === Number(wizardGrade),
  );
  const wizardUsesIntake =
    wizardManualDetails || wizardGradeCatalogue.length === 0;
  const wizardUsesYear = materialTypesWithYear.includes(wizardMaterialType);
  const wizardMedia = catalogueChoices(wizardGradeCatalogue, "medium");
  const wizardSubjects = catalogueChoices(
    wizardGradeCatalogue.filter((entry) => entry.medium_id === wizardMediumId),
    "subject",
  );
  const scopeGradeCatalogue = catalogue.filter(
    (entry) => entry.grade === Number(scopeGrade),
  );
  const scopeMedia = catalogueChoices(scopeGradeCatalogue, "medium");
  const scopeSubjects = catalogueChoices(
    scopeGradeCatalogue.filter((entry) => entry.medium_id === scopeMediumId),
    "subject",
  );
  const scopeCurricula = scopeGradeCatalogue.filter(
    (entry) =>
      entry.medium_id === scopeMediumId && entry.subject_id === scopeSubjectId,
  );

  const loadWorkspace = useCallback(async () => {
    setWorkspaceLoading(true);
    setWorkspaceError(null);
    try {
      const [summaryResult, catalogueResult, sourceResult] = await Promise.all([
        api.GET("/api/v1/admin/materials/grade-summary", { cache: "no-store" }),
        api.GET("/api/v1/admin/material-catalogue", {
          params: { query: { limit: 1000 } },
          cache: "no-store",
        }),
        api.GET("/api/v1/admin/source-documents", { cache: "no-store" }),
      ]);
      const workspaceResults: ReadonlyArray<{
        error?: unknown;
        response: Response;
      }> = [summaryResult, catalogueResult, sourceResult];
      const failure = workspaceResults.find(
        (result) => !result.response.ok || result.error,
      );
      if (failure) {
        setWorkspaceError(uiError(failure.error, failure.response.status));
        return;
      }
      setSummaries(completeGradeSummaries(summaryResult.data ?? []));
      setCatalogue(catalogueResult.data ?? []);
      setSources(sourceResult.data ?? []);
    } catch {
      setWorkspaceError(networkError());
    } finally {
      setWorkspaceLoading(false);
    }
  }, [api]);

  const discoverMaterials = useCallback(
    async (
      grade: number | null,
      subjectId: string,
      offset: number,
      ignoreFilters = false,
    ): Promise<MaterialDiscovery> => {
      const result = await api.GET("/api/v1/admin/materials", {
        params: {
          query: {
            ...(grade === null ? { unassigned_only: true } : { grade }),
            limit: MATERIAL_PAGE_LIMIT,
            material_type: ignoreFilters ? null : selectedMaterialType || null,
            medium_id: ignoreFilters ? null : selectedMedium || null,
            offset,
            search: ignoreFilters ? null : materialSearch.trim() || null,
            status: ignoreFilters ? null : selectedMaterialStatus || null,
            subject_id: subjectId || null,
            year:
              !ignoreFilters &&
              Number(selectedYear) >= 1900 &&
              Number(selectedYear) <= 2100
                ? Number(selectedYear)
                : null,
          },
        },
      });
      if (result.error) {
        return {
          error: uiError(result.error, result.response.status),
          hasNext: false,
          items: [],
        };
      }
      const items = result.data ?? [];
      return { hasNext: items.length === MATERIAL_PAGE_LIMIT, items };
    },
    [
      api,
      materialSearch,
      selectedMaterialStatus,
      selectedMaterialType,
      selectedMedium,
      selectedYear,
    ],
  );

  const loadMaterials = useCallback(
    async (grade: number | null, subjectId: string, offset = 0) => {
      const requestId = ++materialsRequestId.current;
      setMaterialsLoading(true);
      setMaterialsError(null);
      try {
        const result = await discoverMaterials(grade, subjectId, offset);
        if (requestId !== materialsRequestId.current) return;
        if (result.error) {
          setMaterialsError(result.error);
        } else {
          setMaterials(result.items);
          setMaterialsOffset(offset);
          setMaterialsHasNext(result.hasNext);
        }
      } catch {
        if (requestId === materialsRequestId.current)
          setMaterialsError(networkError());
      } finally {
        if (requestId === materialsRequestId.current)
          setMaterialsLoading(false);
      }
    },
    [discoverMaterials],
  );

  const refreshCatalog = useCallback(
    async (
      grade: number | null = selectedGrade,
      subjectId: string = selectedSubject,
      ignoreFilters = false,
    ) => {
      try {
        const [summaryResult, sourceResult, materialResult] = await Promise.all(
          [
            api.GET("/api/v1/admin/materials/grade-summary"),
            api.GET("/api/v1/admin/source-documents"),
            discoverMaterials(grade, subjectId, 0, ignoreFilters),
          ],
        );
        if (!summaryResult.error)
          setSummaries(completeGradeSummaries(summaryResult.data ?? []));
        if (!sourceResult.error) setSources(sourceResult.data ?? []);
        if (materialResult && !materialResult.error) {
          setMaterials(materialResult.items);
          setMaterialsOffset(0);
          setMaterialsHasNext(materialResult.hasNext);
        }
      } catch {
        // The completed mutation remains authoritative. A later manual retry can refresh the catalog.
      }
    },
    [api, discoverMaterials, selectedGrade, selectedSubject],
  );

  const refreshUploadRecovery = useCallback(async () => {
    const request = ++recoveryRequest.current;
    recoveryController.current?.abort();
    const controller = new AbortController();
    recoveryController.current = controller;
    setCheckpointLoading(true);
    setCheckpointError("");
    try {
      setCheckpoints({ requestIds: [], ...readUploadCheckpoints() });
      const recovered =
        role === "admin"
          ? await recoverUploadCheckpoints(api, controller.signal)
          : readUploadCheckpoints();
      if (request === recoveryRequest.current)
        setCheckpoints({ requestIds: [], ...recovered });
    } catch (error) {
      if (request === recoveryRequest.current && !controller.signal.aborted) {
        setCheckpointError(uploadFailureMessage(error));
        try {
          setCheckpoints({ requestIds: [], ...readUploadCheckpoints() });
        } catch {
          /* Keep the last known recovery links. */
        }
      }
    } finally {
      if (request === recoveryRequest.current) setCheckpointLoading(false);
    }
  }, [api, role]);

  useEffect(() => {
    const update = () => {
      void refreshUploadRecovery();
    };
    const timer = window.setTimeout(update, 0);
    window.addEventListener("storage", update);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("storage", update);
      recoveryRequest.current += 1;
      recoveryController.current?.abort();
      uploadViewRequest.current += 1;
      uploadController.current?.abort();
    };
  }, [refreshUploadRecovery]);

  useEffect(() => {
    if (!retryDelayMs) return;
    const timer = window.setTimeout(() => setRetryDelayMs(0), retryDelayMs);
    return () => window.clearTimeout(timer);
  }, [retryDelayMs]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadWorkspace(), 0);
    return () => window.clearTimeout(timeout);
  }, [loadWorkspace]);

  useEffect(() => {
    if (workspaceError) return;
    const timeout = window.setTimeout(
      () => void loadMaterials(selectedGrade, selectedSubject),
      0,
    );
    return () => window.clearTimeout(timeout);
  }, [loadMaterials, selectedGrade, selectedSubject, workspaceError]);

  const wizardCurricula = catalogue.filter(
    (entry) =>
      entry.grade === Number(wizardGrade) &&
      entry.medium_id === wizardMediumId &&
      entry.subject_id === wizardSubjectId,
  );

  const activeWizardUnits = wizardUnits.filter((unit) => unit.active);
  const activeWizardLessons = wizardLessons.filter(
    (lesson) =>
      lesson.active && (!wizardUnitId || lesson.unit_id === wizardUnitId),
  );
  const activeScopeUnits = scopeUnits.filter((unit) => unit.active);
  const activeScopeLessons = scopeLessons.filter(
    (lesson) =>
      lesson.active && (!scopeUnitId || lesson.unit_id === scopeUnitId),
  );

  const loadWizardScope = useCallback(
    async (curriculumId: string) => {
      setWizardUnits([]);
      setWizardLessons([]);
      setWizardUnitId("");
      setWizardLessonId("");
      if (!curriculumId) return;
      setWizardScopeLoading(true);
      try {
        const [unitResult, lessonResult] = await Promise.all([
          api.GET(
            "/api/v1/admin/curriculum-versions/{curriculum_version_id}/units",
            {
              params: { path: { curriculum_version_id: curriculumId } },
            },
          ),
          api.GET(
            "/api/v1/admin/curriculum-versions/{curriculum_version_id}/lessons",
            {
              params: { path: { curriculum_version_id: curriculumId } },
            },
          ),
        ]);
        if (unitResult.error || lessonResult.error) {
          setWizardError(
            uiError(
              unitResult.error ?? lessonResult.error,
              unitResult.error
                ? unitResult.response.status
                : lessonResult.response.status,
            ),
          );
        } else {
          setWizardUnits(unitResult.data ?? []);
          setWizardLessons(lessonResult.data ?? []);
        }
      } catch {
        setWizardError(networkError());
      } finally {
        setWizardScopeLoading(false);
      }
    },
    [api],
  );

  const loadScopeChoices = useCallback(
    async (curriculumId: string, preserveSelection = false) => {
      if (!preserveSelection) {
        setScopeUnitId("");
        setScopeLessonId("");
      }
      setScopeUnits([]);
      setScopeLessons([]);
      if (!curriculumId) return;
      setScopeLoading(true);
      try {
        const [unitResult, lessonResult] = await Promise.all([
          api.GET(
            "/api/v1/admin/curriculum-versions/{curriculum_version_id}/units",
            {
              params: { path: { curriculum_version_id: curriculumId } },
            },
          ),
          api.GET(
            "/api/v1/admin/curriculum-versions/{curriculum_version_id}/lessons",
            {
              params: { path: { curriculum_version_id: curriculumId } },
            },
          ),
        ]);
        if (unitResult.error || lessonResult.error) {
          setScopeError(
            uiError(
              unitResult.error ?? lessonResult.error,
              unitResult.error
                ? unitResult.response.status
                : lessonResult.response.status,
            ),
          );
        } else {
          setScopeUnits(unitResult.data ?? []);
          setScopeLessons(lessonResult.data ?? []);
        }
      } catch {
        setScopeError(networkError());
      } finally {
        setScopeLoading(false);
      }
    },
    [api],
  );

  function clearMaterialFilters() {
    setMaterialSearch("");
    setSelectedSubject("");
    setSelectedMedium("");
    setSelectedMaterialType("");
    setSelectedMaterialStatus("");
    setSelectedYear("");
  }

  function resetWizardAssignment() {
    setWizardMediumId("");
    setWizardSubjectId("");
    setWizardIntakeMedium("");
    setWizardIntakeSubject("");
    setWizardIntakeCurriculum("");
    setWizardCurriculumId("");
    setWizardUnitId("");
    setWizardLessonId("");
    setWizardUnits([]);
    setWizardLessons([]);
    setWizardScopeLoading(false);
  }

  function chooseUploadDetailsMode(manual: boolean) {
    if (
      uploadRunning.current ||
      activeUpload ||
      uploadProgress ||
      uploadRequestId
    )
      return;
    resetWizardAssignment();
    setWizardManualDetails(manual);
    setWizardStep(1);
    setWizardError(null);
  }

  function resetWizard() {
    uploadViewRequest.current += 1;
    uploadTask.current = null;
    setActiveUpload(null);
    setUploadProgress(null);
    setResumeId("");
    setUploadRequestId("");
    setContinuingRequest(false);
    setResumeLoading(false);
    setUploadRetryBlocked(false);
    setRetryDelayMs(0);
    setWizardStep(0);
    setWizardGrade("");
    setWizardManualDetails(false);
    resetWizardAssignment();
    setWizardMaterialType("syllabus");
    setWizardYear("");
    setWizardFile(null);
    setWizardError(null);
    setDuplicate(null);
  }

  function closeWizard() {
    if (uploading) return;
    setWizardOpen(false);
    resetWizard();
  }

  async function continueWizard() {
    setWizardError(null);
    if (wizardStep === 0 && !wizardGrade) {
      setWizardError({
        code: "grade_required",
        message: "Choose a grade to continue.",
      });
      return;
    }
    if (
      wizardStep === 1 &&
      (wizardUsesIntake
        ? !intakeMedia.includes(wizardIntakeMedium)
        : !wizardMediumId)
    ) {
      setWizardError({
        code: "medium_required",
        message: "Choose a medium to continue.",
      });
      return;
    }
    if (wizardStep === 2 && !wizardUsesIntake && !wizardSubjectId) {
      setWizardError({
        code: "subject_required",
        message: "Choose a subject to continue.",
      });
      return;
    }
    if (wizardUsesIntake && (wizardStep === 2 || wizardStep === 4)) {
      const labelError =
        wizardStep === 2
          ? intakeLabelError(wizardIntakeSubject, "subject")
          : intakeLabelError(wizardIntakeCurriculum, "curriculum / edition");
      if (labelError) {
        setWizardError(labelError);
        return;
      }
    }
    if (wizardStep === 3 && !wizardUsesIntake) {
      const defaultCurriculum =
        wizardCurricula.find(
          (curriculum) =>
            curriculum.curriculum_version_id === wizardCurriculumId,
        ) ?? (wizardCurricula.length === 1 ? wizardCurricula[0] : undefined);
      if (!wizardCurricula.length) {
        setWizardError({
          code: "curriculum_required",
          message: "විෂයමාලා තොරතුරු තහවුරු කිරීමට අවශ්‍යයි",
        });
        return;
      }
      if (
        defaultCurriculum &&
        defaultCurriculum.curriculum_version_id !== wizardCurriculumId
      ) {
        setWizardCurriculumId(defaultCurriculum.curriculum_version_id);
        void loadWizardScope(defaultCurriculum.curriculum_version_id);
      }
    }
    if (wizardStep === 4) {
      if (!wizardUsesIntake && !wizardCurriculumId) {
        setWizardError({
          code: "curriculum_required",
          message: "Choose the curriculum this material belongs to.",
        });
        return;
      }
      const year = Number(wizardYear);
      if (
        wizardUsesYear &&
        (!wizardUsesIntake || wizardYear !== "") &&
        (!Number.isInteger(year) || year < 1900 || year > 2100)
      ) {
        setWizardError({
          code: "year_required",
          message: "Enter a whole year from 1900 through 2100.",
        });
        return;
      }
    }
    if (wizardStep === 5) {
      const fileError = validatePdf(wizardFile);
      if (fileError) {
        setWizardError(fileError);
        return;
      }
    }
    if (wizardStep < 6) setWizardStep((wizardStep + 1) as WizardStep);
  }

  function previousWizardStep() {
    if (uploadRunning.current || activeUpload || uploadProgress) return;
    uploadTask.current = null;
    setWizardError(null);
    setWizardStep(Math.max(0, wizardStep - 1) as WizardStep);
  }

  function submitUploadWizard(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
  }

  function uploadProblem(error: unknown) {
    const value =
      error instanceof UploadFailure
        ? error
        : new UploadFailure("upload_interrupted");
    setWizardError({
      code: value.code,
      message: uploadFailureMessage(value),
      status: value.status,
    });
    setUploadRetryBlocked(!value.safeToRetry || value.code === "upload_failed");
    if (value.retryAfterSeconds)
      setRetryDelayMs(value.retryAfterSeconds * 1000);
  }

  function openInterruptedUpload(requestId: string) {
    if (uploadRunning.current || checkpointLoading || role !== "admin") return;
    resetWizard();
    setUploadRequestId(requestId);
    setContinuingRequest(true);
    setWizardOpen(true);
  }

  async function openSavedUpload(id: string) {
    if (uploadRunning.current || role !== "admin") return;
    resetWizard();
    setResumeId(id);
    setWizardOpen(true);
    setResumeLoading(true);
    const view = uploadViewRequest.current;
    try {
      const result = await api.GET("/api/v1/admin/source-uploads/{upload_id}", {
        params: { path: { upload_id: id } },
        cache: "no-store",
      });
      if (view !== uploadViewRequest.current) return;
      if (result.error || !result.response.ok || !result.data)
        throw new UploadFailure(
          errorCode(result.error),
          result.response.status,
        );
      if (result.data.id !== id)
        throw new UploadFailure("upload_response_invalid");
      setActiveUpload(result.data);
    } catch (error) {
      if (view === uploadViewRequest.current) uploadProblem(error);
    } finally {
      if (view === uploadViewRequest.current) setResumeLoading(false);
    }
  }

  async function uploadMaterial() {
    if (
      uploadRunning.current ||
      role !== "admin" ||
      uploadRetryBlocked ||
      retryDelayMs
    )
      return;
    const file = wizardFile;
    const needsFile = !resumeId || activeUpload?.status === "uploading";
    if (needsFile) {
      const fileError = validatePdf(file);
      if (fileError) {
        setWizardError(fileError);
        return;
      }
    }
    const view = uploadViewRequest.current;
    uploadRunning.current = true;
    setUploading(true);
    setWizardError(null);
    setDuplicate(null);
    const controller = new AbortController();
    uploadController.current = controller;
    try {
      if (!uploadTask.current) {
        const saved = readUploadCheckpoints();
        if (!resumeId && !uploadRequestId && saved.creationUncertain)
          throw new UploadFailure(
            "upload_creation_unknown",
            0,
            undefined,
            false,
          );
        const numericYear =
          wizardUsesYear && wizardYear ? Number(wizardYear) : null;
        const year = Number.isInteger(numericYear) ? numericYear : null;
        const metadata: UploadMetadata = {
          curriculum_version_id: wizardUsesIntake
            ? null
            : wizardCurriculumId || null,
          document_type: wizardMaterialType,
          lesson_id: wizardUsesIntake ? null : wizardLessonId || null,
          paper_code: null,
          unit_id: wizardUsesIntake ? null : wizardUnitId || null,
          year,
          ...(wizardUsesIntake
            ? {
                intake_metadata: {
                  candidate_grade: Number(wizardGrade),
                  medium_label:
                    wizardIntakeMedium === "Not sure"
                      ? null
                      : wizardIntakeMedium,
                  subject_label: wizardIntakeSubject.trim() || null,
                  curriculum_label: wizardIntakeCurriculum.trim() || null,
                  document_type_label: materialTypeLabels[wizardMaterialType],
                  year,
                },
              }
            : {}),
        };
        uploadTask.current = new ResumableSourceUpload({
          api,
          file: file ?? undefined,
          ...(resumeId
            ? { uploadId: resumeId }
            : {
                metadata,
                ...(uploadRequestId ? { requestId: uploadRequestId } : {}),
              }),
        });
      }
      const uploaded = await uploadTask.current.run({
        signal: controller.signal,
        pollIntervalMs: 500,
        maxPolls: 240,
        onCreating: () => {
          setUploadRequestId(uploadTask.current?.requestId ?? uploadRequestId);
          setCheckpoints({ requestIds: [], ...readUploadCheckpoints() });
        },
        onSession: (session) => {
          const saved = recordUploadCheckpoint(session.id);
          if (view === uploadViewRequest.current) {
            setActiveUpload(session);
            setCheckpoints(saved);
          }
        },
        onProgress: (progress) => {
          if (view === uploadViewRequest.current) setUploadProgress(progress);
        },
      });
      if (view !== uploadViewRequest.current) return;
      try {
        setCheckpoints(finishUploadCheckpoint(uploaded.id));
      } catch (error) {
        setCheckpointError(uploadFailureMessage(error));
      }
      if (uploaded.deduplicated) {
        setDuplicate({
          id: uploaded.document_id!,
          original_filename:
            sourceById.get(uploaded.document_id!)?.original_filename ??
            uploaded.filename,
        });
        setUploadProgress(null);
        return;
      }
      let message =
        "Material uploaded. Open the material to check reading progress and metadata.";
      if (uploaded.source_read_job_id) {
        try {
          const reading = await api.GET(
            "/api/v1/admin/source-read-jobs/{job_id}",
            {
              params: { path: { job_id: uploaded.source_read_job_id } },
              cache: "no-store",
              signal: controller.signal,
            },
          );
          if (
            reading.data?.id === uploaded.source_read_job_id &&
            reading.data.document_id === uploaded.document_id
          ) {
            if (["queued", "running"].includes(reading.data.status))
              message = "Material uploaded. Reading the PDF now.";
            else if (reading.data.status === "completed")
              message =
                "Material uploaded. Check the material details and system-read text before AI use.";
            else
              message =
                "Material uploaded, but reading needs attention. Open the material to continue.";
          }
        } catch {
          /* The completed upload remains authoritative; never enqueue another reading. */
        }
      }
      if (view !== uploadViewRequest.current) return;
      let grade: number | null = Number(wizardGrade);
      if (resumeId) {
        const result = await api
          .GET("/api/v1/admin/materials", {
            params: { query: { document_id: uploaded.document_id!, limit: 1 } },
            cache: "no-store",
            signal: controller.signal,
          })
          .catch(() => null);
        const material = result?.data?.find(
          (item) => item.id === uploaded.document_id,
        );
        grade = material
          ? material.grade
          : (uploaded.intake_metadata.candidate_grade ?? selectedGrade);
      }
      if (view !== uploadViewRequest.current) return;
      const subject = resumeId || wizardUsesIntake ? "" : wizardSubjectId;
      clearMaterialFilters();
      setSelectedGrade(grade);
      setSelectedSubject(subject);
      setUploadedDocumentId(uploaded.document_id!);
      await refreshCatalog(grade, subject, true);
      setWizardOpen(false);
      resetWizard();
      setNotice(message);
    } catch (error) {
      if (view === uploadViewRequest.current) uploadProblem(error);
    } finally {
      uploadRunning.current = false;
      setUploading(false);
    }
  }

  function openRemoval(material: Material) {
    setRemoveTarget(material);
    setRemoveReason("");
    setRemoveError(null);
  }

  async function removeMaterial(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!removeTarget) return;
    const reason = removeReason.trim();
    if (
      !reason ||
      Array.from(reason).length > 512 ||
      [...reason].some((character) => {
        const point = character.codePointAt(0) ?? 0;
        return point < 32 || point === 127;
      })
    ) {
      setRemoveError({
        code: "invalid_removal_reason",
        message: "Enter a reason using 1–512 printable characters.",
      });
      return;
    }

    const body: RemoveBody = {
      expected_version: removeTarget.metadata_scope_version,
      reason,
    };
    setRemoving(true);
    setRemoveError(null);
    try {
      const result = await api.POST(
        "/api/v1/admin/materials/{document_id}/remove-from-use",
        {
          body,
          params: { path: { document_id: removeTarget.id } },
        },
      );
      if (result.error) {
        setRemoveError(uiError(result.error, result.response.status));
        return;
      }
      if (result.data) {
        setSources((current) => [
          result.data,
          ...current.filter((source) => source.id !== result.data?.id),
        ]);
      }
      await refreshCatalog();
      setRemoveTarget(null);
      setRemoveReason("");
      setNotice("Removed from AI use.");
    } catch {
      setRemoveError(networkError());
    } finally {
      setRemoving(false);
    }
  }

  async function restoreMaterial(material: Material) {
    const body: RestoreBody = {
      expected_version: material.metadata_scope_version,
    };
    setRestoringId(material.id);
    setNotice("");
    setActionError(null);
    try {
      const result = await api.POST(
        "/api/v1/admin/materials/{document_id}/restore",
        {
          body,
          params: { path: { document_id: material.id } },
        },
      );
      if (result.error) {
        setActionError(uiError(result.error, result.response.status));
        return;
      }
      if (result.data) {
        setSources((current) => [
          result.data,
          ...current.filter((source) => source.id !== result.data?.id),
        ]);
      }
      await refreshCatalog();
      setNotice("Restored for AI use.");
    } catch {
      setActionError(networkError());
    } finally {
      setRestoringId(null);
    }
  }

  async function openScopeEditor(material: Material) {
    const source = sourceById.get(material.id);
    if (!source) return;
    const assignment = curriculumById.get(source.curriculum_version_id ?? "");
    setScopeTarget(material);
    setScopeEditing(false);
    setMetadataDraft(null);
    setScopeCandidateId(
      source.metadata_candidate?.is_current
        ? source.metadata_candidate.id
        : null,
    );
    setConfirmIntakeMetadata(false);
    setScopeGrade(assignment ? String(assignment.grade) : "");
    setScopeMediumId(assignment?.medium_id ?? "");
    setScopeSubjectId(assignment?.subject_id ?? "");
    setScopeCurriculumId(assignment?.curriculum_version_id ?? "");
    setScopeUnitId(assignment ? (source.unit_id ?? "") : "");
    setScopeLessonId(assignment ? (source.lesson_id ?? "") : "");
    setScopeError(null);
    await loadScopeChoices(assignment?.curriculum_version_id ?? "", true);
  }

  async function saveMetadataCandidate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !scopeTarget ||
      !scopeSource ||
      !metadataDraft ||
      !canSaveMetadataCandidate ||
      role !== "admin"
    )
      return;
    const body: MetadataCandidateBody = {
      expected_scope_version: metadataDraft.scopeVersion,
      expected_candidate_version: metadataDraft.candidateVersion,
      material_type:
        candidateMaterialTypes.find((item) => item.label === metadataDraft.type)
          ?.value ??
        scopeSource.metadata_candidate?.material_type ??
        scopeSource.document_type,
      metadata: {
        ...scopeIntake,
        candidate_grade: metadataDraft.grade
          ? Number(metadataDraft.grade)
          : null,
        medium_label: metadataDraft.medium.trim() || null,
        subject_label: metadataDraft.subject.trim() || null,
        document_type_label: metadataDraft.type.trim() || null,
        year: metadataDraft.year ? Number(metadataDraft.year) : null,
      },
      reason: metadataDraft.reason.trim(),
    };
    setScopeSaving(true);
    setScopeError(null);
    try {
      const result = await api.POST(
        "/api/v1/admin/materials/{document_id}/metadata-candidates",
        {
          body,
          params: { path: { document_id: scopeTarget.id } },
        },
      );
      if (result.error) {
        setScopeError(uiError(result.error, result.response.status));
        return;
      }
      const updated = result.data;
      if (updated)
        setSources((current) => [
          updated,
          ...current.filter((source) => source.id !== updated.id),
        ]);
      const grade = body.metadata.candidate_grade ?? null;
      clearMaterialFilters();
      setSelectedGrade(grade);
      setSelectedSubject("");
      await refreshCatalog(grade, "", true);
      setScopeTarget(null);
      setMetadataDraft(null);
      setNotice(
        scopeSinhala
          ? "තොරතුරු පරීක්ෂාව සඳහා සුරකින ලදී. මෙය AI භාවිතයට තහවුරු කිරීමක් නොවේ."
          : "Descriptions saved for review. This does not approve the material for AI use.",
      );
    } catch {
      setScopeError(networkError());
    } finally {
      setScopeSaving(false);
    }
  }

  function changeScopeCurriculum(curriculumId: string) {
    if (curriculumId && !curriculumById.has(curriculumId)) return;
    setScopeCurriculumId(curriculumId);
    setConfirmIntakeMetadata(false);
    setScopeError(null);
    void loadScopeChoices(curriculumId);
  }

  async function saveScope(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!scopeTarget || !canSaveScope || role !== "admin") return;
    const body: ScopeBody = {
      confirm_intake_metadata: confirmIntakeMetadata && canConfirmIntake,
      ...(confirmIntakeMetadata && canConfirmIntake && scopeCandidateId
        ? { metadata_candidate_id: scopeCandidateId }
        : {}),
      curriculum_version_id: scopeCurriculumId || null,
      expected_version: scopeTarget.metadata_scope_version,
      lesson_id: scopeLessonId || null,
      unit_id: scopeUnitId || null,
    };
    setScopeSaving(true);
    setScopeError(null);
    try {
      const result = await api.PATCH(
        "/api/v1/admin/materials/{document_id}/scope",
        {
          body,
          params: { path: { document_id: scopeTarget.id } },
        },
      );
      if (result.error) {
        setScopeError(uiError(result.error, result.response.status));
        return;
      }
      if (result.data) {
        setSources((current) => [
          result.data,
          ...current.filter((source) => source.id !== result.data?.id),
        ]);
      }
      const grade = curriculumById.get(scopeCurriculumId)?.grade;
      await refreshCatalog();
      setScopeTarget(null);
      setNotice(grade ? `Moved to Grade ${grade}.` : "Material scope updated.");
    } catch {
      setScopeError(networkError());
    } finally {
      setScopeSaving(false);
    }
  }

  const selectedWizardMedium = wizardMedia.find(
    (item) => item.id === wizardMediumId,
  );
  const selectedWizardSubject = wizardSubjects.find(
    (item) => item.id === wizardSubjectId,
  );
  const selectedWizardCurriculum = curriculumById.get(wizardCurriculumId);
  const selectedWizardUnit = wizardUnits.find(
    (unit) => unit.id === wizardUnitId,
  );
  const selectedWizardLesson = wizardLessons.find(
    (lesson) => lesson.id === wizardLessonId,
  );
  const scopeCurriculum = curriculumById.get(scopeCurriculumId);
  const scopeSource = scopeTarget ? sourceById.get(scopeTarget.id) : undefined;
  const scopeNeedsReview = Boolean(
    scopeTarget?.metadata_review_required ||
    scopeSource?.metadata_review_required,
  );
  const scopeIntake = scopeSource?.metadata_candidate?.is_current
    ? scopeSource.metadata_candidate.metadata
    : (scopeTarget?.intake_metadata ?? scopeSource?.intake_metadata);
  const scopeSinhala = /sinhala|සිංහල/i.test(
    scopeIntake?.medium_label ?? scopeTarget?.medium ?? "",
  );
  const canEditMetadataCandidate =
    role === "admin" &&
    Boolean(scopeSource) &&
    !scopeSource?.curriculum_version_id &&
    scopeNeedsReview;
  const candidateYearValid =
    metadataDraft?.year === "" ||
    Boolean(
      metadataDraft &&
      /^\d{4}$/.test(metadataDraft.year) &&
      Number(metadataDraft.year) >= 1900 &&
      Number(metadataDraft.year) <= 2100,
    );
  const canSaveMetadataCandidate =
    canEditMetadataCandidate &&
    !scopeSaving &&
    Boolean(metadataDraft?.reason.trim() && candidateYearValid);
  const scopeSelectionValid = Boolean(
    scopeCurriculum &&
    (!scopeUnitId ||
      activeScopeUnits.some((unit) => unit.id === scopeUnitId)) &&
    (!scopeLessonId ||
      activeScopeLessons.some((lesson) => lesson.id === scopeLessonId)),
  );
  const canConfirmIntake =
    scopeNeedsReview && scopeSelectionValid && !scopeLoading;
  const scopeChanged = Boolean(
    scopeSource &&
    (scopeCurriculumId !== (scopeSource.curriculum_version_id ?? "") ||
      scopeUnitId !== (scopeSource.unit_id ?? "") ||
      scopeLessonId !== (scopeSource.lesson_id ?? "")),
  );
  const canSaveScope = Boolean(
    !scopeSaving &&
    !scopeLoading &&
    catalogue.length &&
    scopeSelectionValid &&
    ((scopeEditing && scopeChanged) ||
      (canConfirmIntake && confirmIntakeMetadata)),
  );
  const pendingUploadRequests = checkpoints.requestIds ?? [];
  const legacyCreationUncertain =
    checkpoints.creationUncertain && pendingUploadRequests.length === 0;
  const visibleMaterials = selectedSubject
    ? materials.filter((material) => material.subject_id === selectedSubject)
    : materials;

  return (
    <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8 lg:py-12">
      <header className="flex flex-wrap items-start justify-between gap-6 border-b border-slate-300 pb-7">
        <div className="max-w-3xl">
          <p className="text-xs font-semibold tracking-[0.18em] text-amber-800 uppercase">
            Teaching sources
          </p>
          <h1 className="mt-2 text-4xl font-semibold tracking-tight">
            Materials
          </h1>
          <p className="mt-3 max-w-2xl leading-7 text-slate-600">
            See what each grade can use, add approved PDFs, and correct mistakes
            without losing the source history.
          </p>
          <Link
            className="mt-3 inline-block rounded text-sm font-semibold underline focus-visible:ring-2 focus-visible:ring-amber-600"
            href="/admin/materials/benchmark-review"
          >
            Review selected source pages
          </Link>
        </div>
        {role === "admin" ? (
          <button
            className={primaryButton}
            disabled={
              checkpointLoading ||
              checkpoints.creationUncertain ||
              Boolean(checkpointError)
            }
            onClick={() => {
              resetWizard();
              setWizardOpen(true);
            }}
            type="button"
          >
            Upload material
          </button>
        ) : (
          <p className="rounded-lg border border-slate-300 bg-white px-4 py-3 text-sm font-semibold text-slate-700">
            Reviewer access is read-only.
          </p>
        )}
      </header>

      {role === "admin" &&
        (checkpoints.uploadIds.length > 0 ||
          checkpoints.creationUncertain ||
          checkpointError) && (
          <section
            aria-label="Saved uploads"
            className="mt-6 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950"
          >
            <h2 className="font-semibold">Saved upload progress</h2>
            <p className="mt-2">
              Continue a saved upload after a connection interruption or
              restart. Reselecting a PDF checks all previously saved content
              before continuing.
            </p>
            {checkpointError && (
              <p className="mt-2" role="alert">
                {checkpointError}
              </p>
            )}
            {legacyCreationUncertain && (
              <p className="mt-2" role="status">
                {uploadFailureMessage(
                  new UploadFailure(
                    "upload_creation_unknown",
                    0,
                    undefined,
                    false,
                  ),
                )}
              </p>
            )}
            {checkpointLoading && (
              <p className="mt-2" role="status">
                Recovering saved upload progress…
              </p>
            )}
            <button
              className={`${secondaryButton} mt-3`}
              disabled={checkpointLoading || uploading}
              onClick={() => void refreshUploadRecovery()}
              type="button"
            >
              Refresh saved upload progress
            </button>
            <div className="mt-3 flex flex-wrap gap-2">
              {pendingUploadRequests.map((id, index) => (
                <button
                  aria-label={`Continue interrupted upload ${index + 1}`}
                  className={secondaryButton}
                  disabled={checkpointLoading || Boolean(checkpointError)}
                  key={id}
                  onClick={() => openInterruptedUpload(id)}
                  type="button"
                >
                  Continue interrupted upload{" "}
                  {pendingUploadRequests.length > 1 ? index + 1 : ""}
                </button>
              ))}
              {checkpoints.uploadIds.map((id, index) => (
                <button
                  aria-label={`Continue saved upload ${index + 1}`}
                  className={secondaryButton}
                  key={id}
                  onClick={() => void openSavedUpload(id)}
                  type="button"
                >
                  Continue saved upload{" "}
                  {checkpoints.uploadIds.length > 1 ? index + 1 : ""}
                </button>
              ))}
            </div>
          </section>
        )}

      {notice && (
        <p
          className="mt-6 rounded-lg border border-emerald-300 bg-emerald-50 p-4 text-sm font-semibold text-emerald-950"
          role="status"
        >
          <span>{notice}</span>
          {uploadedDocumentId && (
            <Link
              className="ml-3 underline"
              href={`/admin/materials/${uploadedDocumentId}`}
            >
              Open uploaded material
            </Link>
          )}
        </p>
      )}
      {actionError && (
        <div className="mt-6">
          <InlineError
            error={actionError}
            title="Material could not be restored."
          />
        </div>
      )}

      {workspaceLoading && (
        <p
          aria-live="polite"
          className="mt-7 rounded-xl border border-slate-300 bg-white p-5 text-slate-600"
          role="status"
        >
          Loading Materials…
        </p>
      )}

      {!workspaceLoading && workspaceError && (
        <section
          className="mt-7 rounded-xl border border-red-300 bg-red-50 p-5"
          role="alert"
        >
          <h2 className="text-lg font-semibold text-red-950">
            {workspaceError.status === 403
              ? "Materials access required"
              : "Materials could not be loaded"}
          </h2>
          <p className="mt-2 text-sm leading-6 text-red-900">
            {workspaceError.status === 403
              ? "Your account does not have permission to view the Materials library."
              : workspaceError.message}
          </p>
          {workspaceError.status !== 403 && (
            <button
              className={`${secondaryButton} mt-4`}
              onClick={() => void loadWorkspace()}
              type="button"
            >
              Try again
            </button>
          )}
        </section>
      )}

      {!workspaceLoading && !workspaceError && (
        <>
          <section aria-label="Materials by grade" className="mt-8">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h2 className="text-2xl font-semibold">Choose a grade</h2>
                <p className="mt-1 text-sm text-slate-600">
                  Counts include active and removed material.
                </p>
              </div>
            </div>
            <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {completeGradeSummaries(summaries).map((summary) => {
                const badge = examBadge(summary.grade);
                const selected = selectedGrade === summary.grade;
                return (
                  <button
                    aria-label={`${summary.grade === null ? "Unassigned materials" : `Grade ${summary.grade}`} ${badge ?? ""} — ${plural(summary.material_count, "material")}, ${plural(summary.subject_count, "subject")}, ${summary.ready_count} Ready, ${summary.needs_review_count} Needs review, ${summary.processing_count} Processing, ${summary.removed_count} Removed`}
                    aria-pressed={selected}
                    className={`min-h-40 rounded-xl border p-4 text-left outline-none transition focus-visible:ring-2 focus-visible:ring-amber-600 focus-visible:ring-offset-2 ${
                      selected
                        ? "border-slate-950 bg-slate-950 text-white shadow-md"
                        : "border-slate-300 bg-white text-slate-950 hover:border-amber-600 hover:shadow-sm"
                    }`}
                    key={summary.grade ?? "unassigned"}
                    onClick={() => {
                      setSelectedGrade(summary.grade);
                      setSelectedMedium("");
                      setSelectedSubject("");
                      setNotice("");
                    }}
                    type="button"
                  >
                    <span className="flex items-start justify-between gap-3">
                      <span className="text-xl font-semibold">
                        {summary.grade === null
                          ? "Unassigned materials"
                          : `Grade ${summary.grade}`}
                      </span>
                      {badge && (
                        <span
                          className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                            selected
                              ? "bg-amber-300 text-slate-950"
                              : "bg-amber-100 text-amber-950"
                          }`}
                        >
                          {badge}
                        </span>
                      )}
                    </span>
                    <span
                      className={`mt-5 block text-sm ${selected ? "text-slate-200" : "text-slate-600"}`}
                    >
                      {plural(summary.material_count, "material")} ·{" "}
                      {plural(summary.subject_count, "subject")}
                    </span>
                    <span className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-xs font-semibold">
                      <span>{summary.ready_count} Ready</span>
                      <span>{summary.needs_review_count} Needs review</span>
                      {summary.processing_count > 0 && (
                        <span>{summary.processing_count} Processing</span>
                      )}
                      {summary.removed_count > 0 && (
                        <span>{summary.removed_count} Removed</span>
                      )}
                    </span>
                  </button>
                );
              })}
            </div>
          </section>

          <section
            aria-label="Uploaded materials"
            className="mt-10 border-t border-slate-300 pt-8"
          >
            <div>
              <p className="text-xs font-semibold tracking-wider text-slate-500 uppercase">
                {selectedGrade === null
                  ? "Unassigned materials"
                  : `Grade ${selectedGrade}`}
              </p>
              <h2 className="mt-1 text-2xl font-semibold">
                Uploaded materials
              </h2>
            </div>
            <section
              aria-label="Material filters"
              className="mt-5 grid gap-4 rounded-xl border border-slate-300 bg-white p-4 sm:grid-cols-2 lg:grid-cols-3"
            >
              <label className={fieldClass} htmlFor="materials-search">
                Search
                <input
                  className={inputClass}
                  id="materials-search"
                  maxLength={200}
                  onChange={(event) =>
                    setMaterialSearch(event.currentTarget.value)
                  }
                  placeholder="Search filenames"
                  type="search"
                  value={materialSearch}
                />
              </label>
              <label className={fieldClass} htmlFor="materials-subject-filter">
                Subject
                <select
                  className={inputClass}
                  id="materials-subject-filter"
                  onChange={(event) =>
                    setSelectedSubject(event.currentTarget.value)
                  }
                  value={selectedSubject}
                >
                  <option value="">All subjects</option>
                  {filterSubjects.map((subject) => (
                    <option key={subject.id} value={subject.id}>
                      {subject.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className={fieldClass} htmlFor="materials-medium-filter">
                Medium
                <select
                  className={inputClass}
                  id="materials-medium-filter"
                  onChange={(event) =>
                    setSelectedMedium(event.currentTarget.value)
                  }
                  value={selectedMedium}
                >
                  <option value="">All media</option>
                  {filterMedia.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className={fieldClass} htmlFor="materials-type-filter">
                Material type
                <select
                  className={inputClass}
                  id="materials-type-filter"
                  onChange={(event) =>
                    setSelectedMaterialType(
                      event.currentTarget.value as MaterialType | "",
                    )
                  }
                  value={selectedMaterialType}
                >
                  <option value="">All types</option>
                  {materialTypes.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className={fieldClass} htmlFor="materials-status-filter">
                Status
                <select
                  className={inputClass}
                  id="materials-status-filter"
                  onChange={(event) =>
                    setSelectedMaterialStatus(
                      event.currentTarget.value as MaterialStatus | "",
                    )
                  }
                  value={selectedMaterialStatus}
                >
                  <option value="">All statuses</option>
                  <option value="processing">Processing</option>
                  <option value="needs_review">Needs review</option>
                  <option value="ready_for_ai">Ready for AI</option>
                  <option value="removed">Removed</option>
                </select>
              </label>
              <label className={fieldClass} htmlFor="materials-year-filter">
                Year
                <input
                  className={inputClass}
                  id="materials-year-filter"
                  inputMode="numeric"
                  max="2100"
                  min="1900"
                  onChange={(event) =>
                    setSelectedYear(event.currentTarget.value)
                  }
                  placeholder="All years"
                  type="number"
                  value={selectedYear}
                />
              </label>
              <button
                className={`${secondaryButton} sm:col-span-2 lg:col-span-3 lg:justify-self-start`}
                onClick={clearMaterialFilters}
                type="button"
              >
                Clear filters
              </button>
            </section>

            {materialsLoading && (
              <p
                className="mt-5 rounded-lg border border-slate-300 bg-white p-4 text-slate-600"
                role="status"
              >
                Loading uploaded materials…
              </p>
            )}
            {!materialsLoading && materialsError && (
              <div className="mt-5" role="alert">
                <InlineError
                  error={materialsError}
                  title="Uploaded materials could not be loaded."
                />
                <button
                  className={`${secondaryButton} mt-3`}
                  onClick={() =>
                    void loadMaterials(selectedGrade, selectedSubject)
                  }
                  type="button"
                >
                  Try again
                </button>
              </div>
            )}
            {!materialsLoading &&
              !materialsError &&
              visibleMaterials.length === 0 && (
                <p className="mt-5 rounded-xl border border-dashed border-slate-400 bg-white p-8 text-center text-slate-600">
                  No materials match this grade and subject.
                </p>
              )}
            {!materialsLoading &&
              !materialsError &&
              (visibleMaterials.length > 0 || materialsOffset > 0) && (
                <div className="mt-5 grid gap-4">
                  {visibleMaterials.map((material) => {
                    const source = sourceById.get(material.id);
                    const proposal =
                      material.metadata_candidate ?? source?.metadata_candidate;
                    const intake =
                      proposal?.is_current === true
                        ? proposal.metadata
                        : (material.intake_metadata ?? source?.intake_metadata);
                    const metadataReviewRequired =
                      material.metadata_review_required ||
                      source?.metadata_review_required;
                    const typeLabel = metadataReviewRequired
                      ? (intake?.document_type_label ??
                        "Unverified material type")
                      : materialTypeLabels[material.material_type];
                    const editable =
                      source !== undefined &&
                      source.extraction_status !== "trusted";
                    return (
                      <article
                        className="rounded-xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6"
                        key={material.id}
                      >
                        <div className="flex flex-wrap items-start justify-between gap-4">
                          <div className="min-w-0">
                            <h3 className="break-words text-xl font-semibold">
                              {material.title}
                            </h3>
                            <p className="mt-1 text-sm text-slate-600">
                              {material.grade === null
                                ? "Grade not assigned"
                                : `${metadataReviewRequired ? "Candidate grade" : "Grade"} ${material.grade}`}{" "}
                              · {material.subject ?? "Subject not assigned"} ·{" "}
                              {typeLabel.toLocaleLowerCase()}
                            </p>
                          </div>
                          <span
                            className={`rounded-full border px-3 py-1 text-sm font-semibold ${statusClasses[material.status]}`}
                          >
                            {statusLabels[material.status]}
                          </span>
                        </div>

                        <MaterialIntakeMetadata
                          intake={intake}
                          reviewRequired={metadataReviewRequired}
                        />

                        <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                          <div>
                            <dt className="font-semibold text-slate-500">
                              Material type
                            </dt>
                            <dd className="mt-1">{typeLabel}</dd>
                          </div>
                          <div>
                            <dt className="font-semibold text-slate-500">
                              Medium
                            </dt>
                            <dd className="mt-1">
                              {material.medium ?? "Not assigned"}
                            </dd>
                          </div>
                          <div>
                            <dt className="font-semibold text-slate-500">
                              Year / curriculum
                            </dt>
                            <dd className="mt-1">
                              {[material.year, material.curriculum]
                                .filter(Boolean)
                                .join(" · ") || "Not recorded"}
                            </dd>
                          </div>
                          <div>
                            <dt className="font-semibold text-slate-500">
                              Pages
                            </dt>
                            <dd className="mt-1">
                              {material.page_count === null
                                ? "Reading in progress"
                                : plural(material.page_count, "page")}
                            </dd>
                          </div>
                          {(material.unit || material.lesson) && (
                            <div className="sm:col-span-2">
                              <dt className="font-semibold text-slate-500">
                                Curriculum scope
                              </dt>
                              <dd className="mt-1">
                                {[material.unit, material.lesson]
                                  .filter(Boolean)
                                  .join(" · ")}
                              </dd>
                            </div>
                          )}
                          <div>
                            <dt className="font-semibold text-slate-500">
                              Uploaded
                            </dt>
                            <dd className="mt-1">
                              {formatDate(material.uploaded_at)}
                            </dd>
                          </div>
                        </dl>

                        {role === "admin" &&
                          source?.extraction_status === "trusted" && (
                            <p className="mt-4 rounded-lg bg-slate-100 p-3 text-sm text-slate-700">
                              This material has trusted content. Remove from use
                              before assigning a corrected version.
                            </p>
                          )}

                        <div className="mt-5 flex flex-wrap gap-2 border-t border-slate-200 pt-4">
                          <Link
                            className={secondaryButton}
                            href={`/admin/materials/${material.id}`}
                          >
                            View
                          </Link>
                          <Link
                            aria-label={`Review extracted text: ${material.title}`}
                            className={secondaryButton}
                            href={`/admin/materials/${material.id}/review-text`}
                            prefetch={false}
                          >
                            Review extracted text
                          </Link>
                          {role === "admin" &&
                            editable &&
                            material.status !== "removed" && (
                              <button
                                aria-label={`Edit metadata: ${material.title}`}
                                className={secondaryButton}
                                onClick={() => void openScopeEditor(material)}
                                type="button"
                              >
                                Edit metadata
                              </button>
                            )}
                          {role === "admin" &&
                            material.status !== "removed" && (
                              <button
                                aria-label={`Remove from use: ${material.title}`}
                                className={secondaryButton}
                                onClick={() => openRemoval(material)}
                                type="button"
                              >
                                Remove from use
                              </button>
                            )}
                          {role === "admin" &&
                            material.status === "removed" && (
                              <button
                                aria-label={`Restore: ${material.title}`}
                                className={secondaryButton}
                                disabled={restoringId === material.id}
                                onClick={() => void restoreMaterial(material)}
                                type="button"
                              >
                                {restoringId === material.id
                                  ? "Restoring…"
                                  : "Restore"}
                              </button>
                            )}
                        </div>

                        <details className="mt-4 border-t border-slate-200 pt-3">
                          <summary className="w-fit cursor-pointer rounded text-sm font-semibold text-slate-600 outline-none focus-visible:ring-2 focus-visible:ring-amber-600">
                            Technical details
                          </summary>
                          <dl className="mt-3 grid gap-3 rounded-lg bg-slate-50 p-4 text-sm sm:grid-cols-2">
                            <div>
                              <dt className="font-semibold text-slate-500">
                                Source document ID
                              </dt>
                              <dd className="mt-1 break-all font-mono text-xs">
                                {material.id}
                              </dd>
                            </div>
                            <div>
                              <dt className="font-semibold text-slate-500">
                                Checksum
                              </dt>
                              <dd className="mt-1 break-all font-mono text-xs">
                                {source?.checksum_sha256 ?? "Not available"}
                              </dd>
                            </div>
                          </dl>
                        </details>
                      </article>
                    );
                  })}
                  <nav
                    aria-label="Materials pages"
                    className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-300 bg-white p-4"
                  >
                    <p className="text-sm font-semibold text-slate-700">
                      Page{" "}
                      {Math.floor(materialsOffset / MATERIAL_PAGE_LIMIT) + 1}
                    </p>
                    <div className="flex gap-2">
                      <button
                        className={secondaryButton}
                        disabled={materialsOffset === 0 || materialsLoading}
                        onClick={() =>
                          void loadMaterials(
                            selectedGrade,
                            selectedSubject,
                            Math.max(0, materialsOffset - MATERIAL_PAGE_LIMIT),
                          )
                        }
                        type="button"
                      >
                        Previous materials page
                      </button>
                      <button
                        className={secondaryButton}
                        disabled={!materialsHasNext || materialsLoading}
                        onClick={() =>
                          void loadMaterials(
                            selectedGrade,
                            selectedSubject,
                            materialsOffset + MATERIAL_PAGE_LIMIT,
                          )
                        }
                        type="button"
                      >
                        Next materials page
                      </button>
                    </div>
                  </nav>
                </div>
              )}
          </section>
        </>
      )}

      {wizardOpen && (
        <Modal labelledBy="upload-material-heading" onClose={closeWizard}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold tracking-wider text-amber-800 uppercase">
                Guided upload
              </p>
              <h2
                className="mt-1 text-2xl font-semibold"
                id="upload-material-heading"
              >
                {resumeId
                  ? "Continue upload"
                  : continuingRequest
                    ? "Continue interrupted upload"
                    : "Upload material"}
              </h2>
            </div>
            <button
              className={secondaryButton}
              disabled={uploading}
              onClick={closeWizard}
              type="button"
            >
              Close
            </button>
          </div>

          {continuingRequest && (
            <p className="mt-4 text-sm">
              Choose the same PDF and original details. This continues the saved
              upload request rather than starting a new one.
            </p>
          )}
          {resumeId ? (
            <div className="mt-5 grid gap-4">
              {resumeLoading ? (
                <p role="status">Loading saved upload…</p>
              ) : (
                activeUpload && (
                  <>
                    <h3 className="break-words text-lg font-semibold">
                      {activeUpload.filename}
                    </h3>
                    <p className="text-sm">
                      {formatBytes(activeUpload.next_offset)} of{" "}
                      {formatBytes(activeUpload.size_bytes)} saved. The original
                      upload details and year are kept unchanged.
                    </p>
                    {activeUpload.status === "uploading" && (
                      <label className={fieldClass} htmlFor="resume-upload-pdf">
                        Original PDF
                        <input
                          accept=".pdf,application/pdf"
                          className={inputClass}
                          disabled={uploading}
                          id="resume-upload-pdf"
                          onChange={(event) => {
                            setWizardFile(
                              event.currentTarget.files?.[0] ?? null,
                            );
                            uploadTask.current = null;
                            setWizardError(null);
                            setUploadRetryBlocked(false);
                          }}
                          type="file"
                        />
                      </label>
                    )}
                    {!duplicate && (
                      <button
                        className={primaryButton}
                        disabled={
                          uploading ||
                          uploadRetryBlocked ||
                          Boolean(retryDelayMs) ||
                          (activeUpload.status === "uploading" && !wizardFile)
                        }
                        onClick={() => void uploadMaterial()}
                        type="button"
                      >
                        {activeUpload.status === "uploading"
                          ? "Resume upload"
                          : "Check upload status"}
                      </button>
                    )}
                  </>
                )
              )}
              {!resumeLoading && !activeUpload && (
                <button
                  className={secondaryButton}
                  onClick={() => void openSavedUpload(resumeId)}
                  type="button"
                >
                  Try loading saved upload again
                </button>
              )}
            </div>
          ) : (
            <>
              <ol className="mt-5 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4 lg:grid-cols-7">
                {wizardStepLabels.map((label, index) => (
                  <li
                    aria-current={wizardStep === index ? "step" : undefined}
                    className={`rounded-md border px-2 py-2 text-center ${
                      wizardStep === index
                        ? "border-slate-950 bg-slate-950 font-semibold text-white"
                        : index < wizardStep
                          ? "border-emerald-300 bg-emerald-50 text-emerald-900"
                          : "border-slate-300 bg-white text-slate-600"
                    }`}
                    key={label}
                  >
                    {label}
                  </li>
                ))}
              </ol>

              {wizardGrade && wizardUsesIntake && (
                <section
                  className="mt-5 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950"
                  aria-label="Upload for metadata review"
                >
                  <h3 className="font-semibold">Upload for metadata review</h3>
                  <p className="mt-2">
                    You can upload this PDF without a confirmed curriculum.
                    Enter the details you know and leave uncertain details
                    blank. The material details and text must be reviewed before
                    AI use.
                  </p>
                  {wizardManualDetails &&
                    wizardGradeCatalogue.length > 0 &&
                    (wizardStep === 1 || wizardStep === 2) && (
                      <button
                        className={`${secondaryButton} mt-3`}
                        onClick={() => chooseUploadDetailsMode(false)}
                        type="button"
                      >
                        Use listed options instead
                      </button>
                    )}
                </section>
              )}

              <form className="mt-6 grid gap-5" onSubmit={submitUploadWizard}>
                {wizardStep === 0 && (
                  <label className={fieldClass} htmlFor="upload-grade">
                    Grade
                    <select
                      className={inputClass}
                      id="upload-grade"
                      onChange={(event) => {
                        setWizardGrade(event.currentTarget.value);
                        setWizardManualDetails(false);
                        resetWizardAssignment();
                        setWizardError(null);
                      }}
                      value={wizardGrade}
                    >
                      <option value="">Choose grade</option>
                      {grades.map((grade) => (
                        <option key={grade} value={grade}>
                          Grade {grade}
                        </option>
                      ))}
                    </select>
                  </label>
                )}

                {wizardStep === 1 && (
                  <div className="grid gap-2">
                    <label className={fieldClass} htmlFor="upload-medium">
                      Medium
                      <select
                        aria-describedby="upload-medium-help"
                        className={inputClass}
                        id="upload-medium"
                        onChange={(event) => {
                          if (wizardUsesIntake)
                            setWizardIntakeMedium(event.currentTarget.value);
                          else {
                            setWizardMediumId(event.currentTarget.value);
                            setWizardSubjectId("");
                            setWizardCurriculumId("");
                          }
                          setWizardError(null);
                        }}
                        value={
                          wizardUsesIntake ? wizardIntakeMedium : wizardMediumId
                        }
                      >
                        <option value="">Choose medium</option>
                        {wizardUsesIntake
                          ? intakeMedia.map((medium) => (
                              <option key={medium} value={medium}>
                                {medium}
                              </option>
                            ))
                          : wizardMedia.map((medium) => (
                              <option key={medium.id} value={medium.id}>
                                {medium.name}
                              </option>
                            ))}
                      </select>
                    </label>
                    <p
                      className="text-sm text-slate-600"
                      id="upload-medium-help"
                    >
                      The language used in the PDF, not the subject.{" "}
                      {wizardUsesIntake &&
                        "Choose Not sure instead of guessing."}
                    </p>
                  </div>
                )}

                {wizardStep === 2 &&
                  (wizardUsesIntake ? (
                    <label
                      className={fieldClass}
                      htmlFor="upload-intake-subject"
                    >
                      Subject (if known)
                      <input
                        className={inputClass}
                        id="upload-intake-subject"
                        maxLength={200}
                        onChange={(event) => {
                          setWizardIntakeSubject(event.currentTarget.value);
                          setWizardError(null);
                        }}
                        placeholder="For example, Mathematics"
                        value={wizardIntakeSubject}
                      />
                    </label>
                  ) : (
                    <label className={fieldClass} htmlFor="upload-subject">
                      Subject
                      <select
                        className={inputClass}
                        id="upload-subject"
                        onChange={(event) => {
                          setWizardSubjectId(event.currentTarget.value);
                          setWizardCurriculumId("");
                          setWizardError(null);
                        }}
                        value={wizardSubjectId}
                      >
                        <option value="">Choose subject</option>
                        {wizardSubjects.map((subject) => (
                          <option key={subject.id} value={subject.id}>
                            {subject.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}

                {(wizardStep === 1 || wizardStep === 2) &&
                  !wizardUsesIntake && (
                    <button
                      className={`${secondaryButton} justify-self-start`}
                      onClick={() => chooseUploadDetailsMode(true)}
                      type="button"
                    >
                      Enter details for review instead
                    </button>
                  )}

                {wizardStep === 3 && (
                  <label className={fieldClass} htmlFor="upload-material-type">
                    Material type
                    <select
                      className={inputClass}
                      id="upload-material-type"
                      onChange={(event) => {
                        setWizardMaterialType(
                          event.currentTarget.value as MaterialType,
                        );
                        setWizardYear("");
                        setWizardError(null);
                      }}
                      value={wizardMaterialType}
                    >
                      {materialTypes.map((type) => (
                        <option key={type.value} value={type.value}>
                          {type.label}
                        </option>
                      ))}
                    </select>
                  </label>
                )}

                {wizardStep === 4 && (
                  <div className="grid gap-5">
                    {wizardUsesYear && (
                      <label className={fieldClass} htmlFor="upload-year">
                        {wizardUsesIntake ? "Year (if known)" : "Year"}
                        <input
                          className={inputClass}
                          id="upload-year"
                          inputMode="numeric"
                          max={2100}
                          min={1900}
                          onChange={(event) => {
                            setWizardYear(event.currentTarget.value);
                            setWizardError(null);
                          }}
                          type="number"
                          value={wizardYear}
                        />
                      </label>
                    )}
                    {wizardUsesIntake ? (
                      <label
                        className={fieldClass}
                        htmlFor="upload-intake-curriculum"
                      >
                        Curriculum / edition (if known)
                        <input
                          className={inputClass}
                          id="upload-intake-curriculum"
                          maxLength={200}
                          onChange={(event) => {
                            setWizardIntakeCurriculum(
                              event.currentTarget.value,
                            );
                            setWizardError(null);
                          }}
                          placeholder="Copy from the PDF, or leave blank for review"
                          value={wizardIntakeCurriculum}
                        />
                      </label>
                    ) : (
                      <>
                        <label
                          className={fieldClass}
                          htmlFor="upload-curriculum"
                        >
                          Curriculum version
                          <select
                            className={inputClass}
                            id="upload-curriculum"
                            onChange={(event) => {
                              const id = event.currentTarget.value;
                              setWizardCurriculumId(id);
                              setWizardError(null);
                              void loadWizardScope(id);
                            }}
                            value={wizardCurriculumId}
                          >
                            <option value="">Choose curriculum</option>
                            {wizardCurricula.map((curriculum) => (
                              <option
                                key={curriculum.curriculum_version_id}
                                value={curriculum.curriculum_version_id}
                              >
                                {curriculum.curriculum_title}
                              </option>
                            ))}
                          </select>
                        </label>
                        {wizardScopeLoading ? (
                          <p className="text-sm text-slate-600" role="status">
                            Loading units and lessons…
                          </p>
                        ) : (
                          <div className="grid gap-4 sm:grid-cols-2">
                            <label className={fieldClass} htmlFor="upload-unit">
                              Unit (optional)
                              <select
                                className={inputClass}
                                id="upload-unit"
                                onChange={(event) => {
                                  setWizardUnitId(event.currentTarget.value);
                                  setWizardLessonId("");
                                }}
                                value={wizardUnitId}
                              >
                                <option value="">Whole curriculum</option>
                                {activeWizardUnits.map((unit) => (
                                  <option key={unit.id} value={unit.id}>
                                    {unit.title}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label
                              className={fieldClass}
                              htmlFor="upload-lesson"
                            >
                              Lesson (optional)
                              <select
                                className={inputClass}
                                disabled={!wizardUnitId}
                                id="upload-lesson"
                                onChange={(event) =>
                                  setWizardLessonId(event.currentTarget.value)
                                }
                                value={wizardLessonId}
                              >
                                <option value="">All lessons in unit</option>
                                {activeWizardLessons.map((lesson) => (
                                  <option key={lesson.id} value={lesson.id}>
                                    {lesson.title}
                                  </option>
                                ))}
                              </select>
                            </label>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}

                {wizardStep === 5 && (
                  <div className={fieldClass}>
                    <label htmlFor="upload-pdf">PDF file</label>
                    <input
                      accept=".pdf,application/pdf"
                      aria-describedby="upload-pdf-help"
                      className="block w-full cursor-pointer rounded-lg border border-dashed border-slate-400 bg-white px-3 py-6 text-sm file:mr-4 file:rounded-md file:border-0 file:bg-slate-950 file:px-4 file:py-2 file:font-semibold file:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-600"
                      id="upload-pdf"
                      onChange={(event: ChangeEvent<HTMLInputElement>) => {
                        setWizardFile(event.currentTarget.files?.[0] ?? null);
                        setWizardError(null);
                      }}
                      type="file"
                    />
                    <span
                      className="font-normal text-slate-600"
                      id="upload-pdf-help"
                    >
                      {wizardFile
                        ? `${wizardFile.name} · ${formatBytes(wizardFile.size)}`
                        : "Choose one approved PDF."}
                    </span>
                  </div>
                )}

                {wizardStep === 6 && (
                  <section
                    aria-label="Review upload"
                    className="rounded-xl border border-slate-300 bg-white p-5"
                  >
                    <h3 className="text-lg font-semibold">
                      Check before uploading
                    </h3>
                    <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                      <div>
                        <dt className="font-semibold text-slate-500">Grade</dt>
                        <dd className="mt-1">Grade {wizardGrade}</dd>
                      </div>
                      <div>
                        <dt className="font-semibold text-slate-500">Medium</dt>
                        <dd className="mt-1">
                          {wizardUsesIntake
                            ? wizardIntakeMedium
                            : selectedWizardMedium?.name}
                        </dd>
                      </div>
                      <div>
                        <dt className="font-semibold text-slate-500">
                          Subject
                        </dt>
                        <dd className="mt-1">
                          {wizardUsesIntake
                            ? wizardIntakeSubject.trim() || "Not provided"
                            : selectedWizardSubject?.name}
                        </dd>
                      </div>
                      <div>
                        <dt className="font-semibold text-slate-500">
                          Material type
                        </dt>
                        <dd className="mt-1">
                          {materialTypeLabels[wizardMaterialType]}
                        </dd>
                      </div>
                      <div>
                        <dt className="font-semibold text-slate-500">
                          Year / curriculum
                        </dt>
                        <dd className="mt-1">
                          {[
                            wizardUsesYear ? wizardYear : "",
                            wizardUsesIntake
                              ? wizardIntakeCurriculum.trim()
                              : selectedWizardCurriculum?.curriculum_title,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </dd>
                      </div>
                      {!wizardUsesIntake &&
                        (selectedWizardUnit || selectedWizardLesson) && (
                          <div>
                            <dt className="font-semibold text-slate-500">
                              Scope
                            </dt>
                            <dd className="mt-1">
                              {[
                                selectedWizardUnit?.title,
                                selectedWizardLesson?.title,
                              ]
                                .filter(Boolean)
                                .join(" · ")}
                            </dd>
                          </div>
                        )}
                      <div className="sm:col-span-2">
                        <dt className="font-semibold text-slate-500">PDF</dt>
                        <dd className="mt-1 break-words">{wizardFile?.name}</dd>
                      </div>
                    </dl>
                    {wizardUsesIntake && (
                      <p className="mt-4 font-semibold text-amber-900">
                        These details need review. Uploading does not approve
                        the curriculum or make this PDF ready for AI use.
                      </p>
                    )}
                  </section>
                )}

                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-5">
                  <button
                    className={secondaryButton}
                    disabled={
                      wizardStep === 0 ||
                      uploading ||
                      Boolean(activeUpload) ||
                      Boolean(uploadProgress)
                    }
                    onClick={previousWizardStep}
                    type="button"
                  >
                    Back
                  </button>
                  {wizardStep < 6 ? (
                    <button
                      className={primaryButton}
                      onClick={(event) => {
                        event.preventDefault();
                        void continueWizard();
                      }}
                      type="button"
                    >
                      Continue
                    </button>
                  ) : !duplicate ? (
                    <button
                      className={primaryButton}
                      disabled={
                        uploading ||
                        uploadRetryBlocked ||
                        Boolean(retryDelayMs) ||
                        (!activeUpload &&
                          !uploadRequestId &&
                          checkpoints.creationUncertain)
                      }
                      onClick={() => void uploadMaterial()}
                      type="button"
                    >
                      {uploading
                        ? "Uploading…"
                        : activeUpload || uploadRequestId
                          ? "Continue upload"
                          : "Upload material"}
                    </button>
                  ) : null}
                </div>
              </form>
            </>
          )}
          {uploadProgress && (
            <section
              aria-label="Upload progress"
              className="mt-4 rounded-lg border border-slate-300 bg-white p-4 text-sm"
              role="status"
            >
              <p className="font-semibold">
                {uploadProgress.phase === "creating"
                  ? "Preparing upload…"
                  : uploadProgress.phase === "checking"
                    ? "Checking the selected PDF against saved progress…"
                    : uploadProgress.phase === "finishing"
                      ? "Finishing upload in Studio…"
                      : "Uploading PDF…"}
              </p>
              <progress
                aria-label="PDF upload progress"
                className="mt-3 h-3 w-full"
                max={uploadProgress.totalBytes || 1}
                value={uploadProgress.uploadedBytes}
              />
              <p className="mt-2">
                {formatBytes(uploadProgress.uploadedBytes)} of{" "}
                {formatBytes(uploadProgress.totalBytes)} saved.
              </p>
              {uploadProgress.phase === "checking" && (
                <p>
                  {formatBytes(uploadProgress.checkedBytes ?? 0)} checked
                  against the saved PDF.
                </p>
              )}
              <p className="mt-2 text-slate-600">
                Uploading does not confirm the material details or trust its
                text.
              </p>
              {uploading && (
                <button
                  className={`${secondaryButton} mt-3`}
                  disabled={uploadProgress.phase === "creating"}
                  onClick={() => uploadController.current?.abort()}
                  type="button"
                >
                  Pause upload
                </button>
              )}
            </section>
          )}
          {wizardError && (
            <div className="mt-4">
              <InlineError
                error={wizardError}
                title={
                  !resumeId && wizardStep < 6
                    ? "Check upload details"
                    : "Upload was not completed."
                }
              />
            </div>
          )}
          {duplicate && (
            <div
              className="mt-4 rounded-lg border border-amber-400 bg-amber-50 p-4 text-sm text-amber-950"
              role="alert"
            >
              <p className="font-semibold">
                This exact PDF is already in Materials. No new copy was
                uploaded.
              </p>
              <p className="mt-2 break-words">{duplicate.original_filename}</p>
              <Link
                className={`${secondaryButton} mt-4`}
                href={`/admin/materials/${duplicate.id}`}
              >
                View existing material
              </Link>
            </div>
          )}
        </Modal>
      )}

      {removeTarget && (
        <Modal
          labelledBy="remove-material-heading"
          onClose={() => {
            if (!removing) setRemoveTarget(null);
          }}
        >
          <h2 className="text-2xl font-semibold" id="remove-material-heading">
            Remove {removeTarget.title} from use
          </h2>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            The original and audit history will be kept, but this material will
            no longer be allowed in future AI retrieval or paper generation.
          </p>
          <form className="mt-5 grid gap-4" onSubmit={removeMaterial}>
            <div className={fieldClass}>
              <label htmlFor="removal-reason">Reason</label>
              <textarea
                aria-describedby="removal-reason-help"
                className={`${inputClass} min-h-28 resize-y`}
                id="removal-reason"
                maxLength={512}
                onChange={(event) => {
                  setRemoveReason(event.currentTarget.value);
                  setRemoveError(null);
                }}
                value={removeReason}
              />
              <span
                className="font-normal text-slate-500"
                id="removal-reason-help"
              >
                Required · up to 512 printable characters
              </span>
            </div>
            {removeError && (
              <InlineError
                error={removeError}
                title="Material was not removed."
              />
            )}
            <div className="flex flex-wrap justify-end gap-3 border-t border-slate-200 pt-4">
              <button
                className={secondaryButton}
                disabled={removing}
                onClick={() => setRemoveTarget(null)}
                type="button"
              >
                Cancel
              </button>
              <button
                className={dangerButton}
                disabled={removing}
                type="submit"
              >
                {removing ? "Removing…" : "Remove from use"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {scopeTarget && (
        <Modal
          labelledBy="scope-material-heading"
          onClose={() => {
            if (!scopeSaving) setScopeTarget(null);
          }}
        >
          <h2 className="text-2xl font-semibold" id="scope-material-heading">
            Edit {scopeTarget.title}
          </h2>
          <MaterialIntakeMetadata
            intake={scopeIntake}
            reviewRequired={scopeNeedsReview}
          />
          {scopeSource?.metadata_candidate && (
            <details className="mt-3 text-sm">
              <summary className="cursor-pointer font-semibold">
                {scopeSinhala
                  ? "මුල් උඩුගත කිරීමේ තොරතුරු"
                  : "Original upload details"}
              </summary>
              <MaterialIntakeMetadata
                intake={scopeSource.intake_metadata}
                reviewRequired
              />
            </details>
          )}
          <p
            className="mt-3 text-sm leading-6 text-slate-600"
            lang={scopeSinhala ? "si" : "en"}
          >
            {scopeSinhala
              ? "තොරතුරු මුල් පිටුව සමඟ සසඳන්න. පරීක්ෂාව සඳහා සුරැකීම, විෂයමාලාව හෝ පෙළ තහවුරු කිරීමක් නොවේ."
              : "Compare these descriptions with the original. Saving them for review does not confirm curriculum scope or page text."}
          </p>
          <form
            className="mt-5 grid gap-5"
            onSubmit={metadataDraft ? saveMetadataCandidate : saveScope}
          >
            {canEditMetadataCandidate && !metadataDraft && (
              <button
                className={`${secondaryButton} justify-self-start`}
                type="button"
                disabled={scopeSaving}
                onClick={() => {
                  setMetadataDraft({
                    scopeVersion:
                      scopeSource?.metadata_scope_version ??
                      scopeTarget.metadata_scope_version,
                    candidateVersion:
                      scopeSource?.metadata_candidate?.version ?? 0,
                    grade: String(
                      scopeIntake?.candidate_grade ?? scopeTarget.grade ?? "",
                    ),
                    medium: scopeIntake?.medium_label ?? "",
                    subject: scopeIntake?.subject_label ?? "",
                    type: scopeIntake?.document_type_label ?? "",
                    year: String(scopeIntake?.year ?? ""),
                    reason: "",
                  });
                  setScopeEditing(false);
                  setConfirmIntakeMetadata(false);
                  setScopeError(null);
                }}
              >
                {scopeSinhala
                  ? "තොරතුරු වෙනස් කරන්න"
                  : "Change detected details"}
              </button>
            )}
            {metadataDraft && (
              <fieldset
                className="grid gap-4 sm:grid-cols-2"
                disabled={scopeSaving}
                lang={scopeSinhala ? "si" : "en"}
              >
                <legend className="mb-2 font-semibold">
                  {scopeSinhala
                    ? "පරීක්ෂාව සඳහා තොරතුරු"
                    : "Descriptions for review"}
                </legend>
                <p className="text-sm leading-6 text-amber-900 sm:col-span-2">
                  {scopeSinhala
                    ? "මෙය විෂයමාලාව හෝ පිටුවේ පෙළ තහවුරු කිරීමක් නොවේ."
                    : "This does not confirm the curriculum or page text."}
                </p>
                <label className={fieldClass} htmlFor="candidate-grade">
                  {scopeSinhala ? "ශ්‍රේණිය" : "Grade"}
                  <select
                    className={inputClass}
                    id="candidate-grade"
                    value={metadataDraft.grade}
                    onChange={(event) =>
                      setMetadataDraft({
                        ...metadataDraft,
                        grade: event.currentTarget.value,
                      })
                    }
                  >
                    <option value="">
                      {scopeSinhala ? "නොදනී" : "Not sure"}
                    </option>
                    {grades.map((grade) => (
                      <option key={grade} value={grade}>
                        {grade}
                      </option>
                    ))}
                  </select>
                </label>
                {(
                  [
                    ["medium", scopeSinhala ? "මාධ්‍යය" : "Medium"],
                    ["subject", scopeSinhala ? "විෂය" : "Subject"],
                    ["year", scopeSinhala ? "වර්ෂය" : "Year"],
                  ] as const
                ).map(([name, label]) => (
                  <label
                    key={name}
                    className={fieldClass}
                    htmlFor={`candidate-${name}`}
                  >
                    {label}
                    <input
                      className={inputClass}
                      id={`candidate-${name}`}
                      value={metadataDraft[name]}
                      maxLength={name === "year" ? 4 : 200}
                      inputMode={name === "year" ? "numeric" : "text"}
                      onChange={(event) =>
                        setMetadataDraft({
                          ...metadataDraft,
                          [name]: event.currentTarget.value,
                        })
                      }
                    />
                  </label>
                ))}
                <label className={fieldClass} htmlFor="candidate-type">
                  {scopeSinhala ? "ද්‍රව්‍ය වර්ගය" : "Material type"}
                  <select
                    className={inputClass}
                    id="candidate-type"
                    value={metadataDraft.type}
                    onChange={(event) =>
                      setMetadataDraft({
                        ...metadataDraft,
                        type: event.currentTarget.value,
                      })
                    }
                  >
                    {!candidateMaterialTypes.some(
                      (item) => item.label === metadataDraft.type,
                    ) && (
                      <option value={metadataDraft.type}>
                        {metadataDraft.type ||
                          (scopeSinhala ? "නොදනී" : "Not sure")}
                      </option>
                    )}
                    {candidateMaterialTypes.map((item) => (
                      <option key={item.label} value={item.label}>
                        {scopeSinhala ? item.sinhala : item.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label
                  className={`${fieldClass} sm:col-span-2`}
                  htmlFor="candidate-reason"
                >
                  {scopeSinhala
                    ? "වෙනස් කිරීමට හේතුව"
                    : "Reason for correction"}
                  <textarea
                    className={inputClass}
                    id="candidate-reason"
                    value={metadataDraft.reason}
                    required
                    maxLength={512}
                    rows={2}
                    onChange={(event) =>
                      setMetadataDraft({
                        ...metadataDraft,
                        reason: event.currentTarget.value,
                      })
                    }
                  />
                </label>
              </fieldset>
            )}
            {scopeCurriculum ? (
              <section
                aria-label="Curriculum assignment"
                className="rounded-lg bg-slate-100 p-3 text-sm text-slate-700"
              >
                <h3 className="font-semibold">
                  Approved curriculum assignment
                </h3>
                <p className="mt-1">
                  {scopeCurriculum.grade_label} · {scopeCurriculum.medium_name}{" "}
                  · {scopeCurriculum.subject_name} ·{" "}
                  {scopeCurriculum.curriculum_title}
                </p>
              </section>
            ) : (
              <p
                className="rounded-lg border border-amber-300 bg-amber-50 p-3 font-sans text-sm text-amber-950"
                lang="si"
              >
                විෂයමාලා තොරතුරු තහවුරු කිරීමට අවශ්‍යයි
              </p>
            )}
            {catalogue.length > 0 && !scopeEditing && (
              <button
                className={`${secondaryButton} justify-self-start`}
                disabled={scopeSaving}
                onClick={() => setScopeEditing(true)}
                type="button"
              >
                Change curriculum assignment
              </button>
            )}
            {catalogue.length > 0 && scopeEditing && (
              <fieldset
                className="grid gap-4 sm:grid-cols-2"
                disabled={scopeSaving}
              >
                <legend className="mb-3 font-semibold">
                  Change curriculum assignment
                </legend>
                <label className={fieldClass} htmlFor="scope-grade">
                  Grade
                  <select
                    className={inputClass}
                    id="scope-grade"
                    value={scopeGrade}
                    onChange={(event) => {
                      setScopeGrade(event.currentTarget.value);
                      setScopeMediumId("");
                      setScopeSubjectId("");
                      changeScopeCurriculum("");
                    }}
                  >
                    <option value="">Choose grade</option>
                    {[
                      ...new Map(
                        catalogue.map((entry) => [
                          entry.grade,
                          entry.grade_label,
                        ]),
                      ).entries(),
                    ]
                      .sort(([first], [second]) => first - second)
                      .map(([grade, label]) => (
                        <option key={grade} value={grade}>
                          {label}
                        </option>
                      ))}
                  </select>
                </label>
                <label className={fieldClass} htmlFor="scope-medium">
                  Medium
                  <select
                    className={inputClass}
                    id="scope-medium"
                    disabled={!scopeGrade}
                    value={scopeMediumId}
                    onChange={(event) => {
                      setScopeMediumId(event.currentTarget.value);
                      setScopeSubjectId("");
                      changeScopeCurriculum("");
                    }}
                  >
                    <option value="">Choose medium</option>
                    {scopeMedia.map((medium) => (
                      <option key={medium.id} value={medium.id}>
                        {medium.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className={fieldClass} htmlFor="scope-subject">
                  Subject
                  <select
                    className={inputClass}
                    id="scope-subject"
                    disabled={!scopeMediumId}
                    value={scopeSubjectId}
                    onChange={(event) => {
                      setScopeSubjectId(event.currentTarget.value);
                      changeScopeCurriculum("");
                    }}
                  >
                    <option value="">Choose subject</option>
                    {scopeSubjects.map((subject) => (
                      <option key={subject.id} value={subject.id}>
                        {subject.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className={fieldClass} htmlFor="scope-curriculum">
                  Curriculum version
                  <select
                    className={inputClass}
                    id="scope-curriculum"
                    disabled={!scopeSubjectId}
                    onChange={(event) =>
                      changeScopeCurriculum(event.currentTarget.value)
                    }
                    value={scopeCurriculumId}
                  >
                    <option value="">Choose curriculum</option>
                    {scopeCurricula.map((curriculum) => (
                      <option
                        key={curriculum.curriculum_version_id}
                        value={curriculum.curriculum_version_id}
                      >
                        {curriculum.curriculum_title}
                      </option>
                    ))}
                  </select>
                </label>
              </fieldset>
            )}

            {scopeLoading ? (
              <p className="text-sm text-slate-600" role="status">
                Loading units and lessons…
              </p>
            ) : scopeEditing && scopeCurriculum ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <label className={fieldClass} htmlFor="scope-unit">
                  Unit (optional)
                  <select
                    className={inputClass}
                    disabled={!scopeCurriculumId}
                    id="scope-unit"
                    onChange={(event) => {
                      setScopeUnitId(event.currentTarget.value);
                      setScopeLessonId("");
                      setConfirmIntakeMetadata(false);
                    }}
                    value={scopeUnitId}
                  >
                    <option value="">Whole curriculum</option>
                    {activeScopeUnits.map((unit) => (
                      <option key={unit.id} value={unit.id}>
                        {unit.title}
                      </option>
                    ))}
                  </select>
                </label>
                <label className={fieldClass} htmlFor="scope-lesson">
                  Lesson (optional)
                  <select
                    className={inputClass}
                    disabled={!scopeUnitId}
                    id="scope-lesson"
                    onChange={(event) => {
                      setScopeLessonId(event.currentTarget.value);
                      setConfirmIntakeMetadata(false);
                    }}
                    value={scopeLessonId}
                  >
                    <option value="">All lessons in unit</option>
                    {activeScopeLessons.map((lesson) => (
                      <option key={lesson.id} value={lesson.id}>
                        {lesson.title}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            ) : null}

            {scopeNeedsReview && (
              <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950">
                <label className="flex items-start gap-3 font-semibold">
                  <input
                    checked={confirmIntakeMetadata}
                    className="mt-1 h-4 w-4"
                    disabled={!canConfirmIntake || scopeSaving}
                    onChange={(event) =>
                      setConfirmIntakeMetadata(event.currentTarget.checked)
                    }
                    type="checkbox"
                  />
                  I have verified the intake metadata against the original and
                  confirm this curriculum assignment.
                </label>
                <p className="mt-2">
                  Optional: choose a valid curriculum first. Saving without this
                  confirmation keeps metadata under review. This does not trust
                  the text or make it ready for AI.
                </p>
              </div>
            )}
            {scopeError && (
              <InlineError error={scopeError} title="Changes were not saved." />
            )}
            <div className="flex flex-wrap justify-end gap-3 border-t border-slate-200 pt-4">
              <button
                className={secondaryButton}
                disabled={scopeSaving}
                onClick={() => setScopeTarget(null)}
                type="button"
              >
                Cancel
              </button>
              <button
                className={primaryButton}
                disabled={
                  metadataDraft ? !canSaveMetadataCandidate : !canSaveScope
                }
                type="submit"
              >
                {scopeSaving
                  ? metadataDraft && scopeSinhala
                    ? "සුරකිමින්…"
                    : "Saving…"
                  : metadataDraft
                    ? scopeSinhala
                      ? "පරීක්ෂාව සඳහා සුරකින්න"
                      : "Save descriptions for review"
                    : "Save changes"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
