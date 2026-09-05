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
  type ChangeEvent,
  type FormEvent,
  type ReactNode,
} from "react";

import type { AdminRole } from "./admin-header";
import { MaterialIntakeMetadata } from "./material-details";

type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type ExamConfiguration = components["schemas"]["ExamConfigurationResponse"];
type GradeSummary = components["schemas"]["MaterialGradeSummaryResponse"];
type Lesson = components["schemas"]["CurriculumLessonResponse"];
type Material = components["schemas"]["MaterialListItemResponse"];
type MaterialStatus = components["schemas"]["MaterialStatus"];
type MaterialType = components["schemas"]["SourceDocumentType"];
type Medium = components["schemas"]["MediumResponse"];
type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type Subject = components["schemas"]["SubjectResponse"];
type Unit = components["schemas"]["CurriculumUnitResponse"];
type UploadBody =
  operations["upload_source_document"]["requestBody"]["content"]["multipart/form-data"];
type RemoveBody = components["schemas"]["MaterialRemoveRequest"];
type RestoreBody = components["schemas"]["MaterialRestoreRequest"];
type ScopeBody = components["schemas"]["MaterialScopeCorrectionRequest"];

type UiError = Readonly<{
  code: string;
  message: string;
  status?: number;
}>;

type WizardStep = 0 | 1 | 2 | 3 | 4 | 5 | 6;

const grades = Array.from({ length: 13 }, (_, index) => index + 1);
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

function uploadFormData(body: UploadBody, file: File): FormData {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("document_type", body.document_type);
  if (body.curriculum_version_id) {
    form.append("curriculum_version_id", body.curriculum_version_id);
  }
  if (body.unit_id) form.append("unit_id", body.unit_id);
  if (body.lesson_id) form.append("lesson_id", body.lesson_id);
  if (body.year !== undefined && body.year !== null)
    form.append("year", String(body.year));
  if (body.paper_code) form.append("paper_code", body.paper_code);
  return form;
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
  const [examConfigurations, setExamConfigurations] = useState<
    ExamConfiguration[]
  >([]);
  const [media, setMedia] = useState<Medium[]>([]);
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [curricula, setCurricula] = useState<Curriculum[]>([]);
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
  const [duplicate, setDuplicate] = useState<SourceDocument | null>(null);

  const [removeTarget, setRemoveTarget] = useState<Material | null>(null);
  const [removeReason, setRemoveReason] = useState("");
  const [removeError, setRemoveError] = useState<UiError | null>(null);
  const [removing, setRemoving] = useState(false);
  const [restoringId, setRestoringId] = useState<string | null>(null);

  const [scopeTarget, setScopeTarget] = useState<Material | null>(null);
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
  const examById = useMemo(
    () =>
      new Map(
        examConfigurations.map((configuration) => [
          configuration.id,
          configuration,
        ]),
      ),
    [examConfigurations],
  );
  const mediumById = useMemo(
    () => new Map(media.map((item) => [item.id, item])),
    [media],
  );
  const subjectById = useMemo(
    () => new Map(subjects.map((subject) => [subject.id, subject])),
    [subjects],
  );
  const curriculumById = useMemo(
    () => new Map(curricula.map((curriculum) => [curriculum.id, curriculum])),
    [curricula],
  );

  const loadWorkspace = useCallback(async () => {
    setWorkspaceLoading(true);
    setWorkspaceError(null);
    try {
      const [
        summaryResult,
        examResult,
        mediaResult,
        subjectResult,
        curriculumResult,
        sourceResult,
      ] = await Promise.all([
        api.GET("/api/v1/admin/materials/grade-summary"),
        api.GET("/api/v1/admin/exam-configurations"),
        api.GET("/api/v1/admin/media"),
        api.GET("/api/v1/admin/subjects"),
        api.GET("/api/v1/admin/curriculum-versions"),
        api.GET("/api/v1/admin/source-documents"),
      ]);
      const workspaceResults: ReadonlyArray<{
        error?: unknown;
        response: Response;
      }> = [
        summaryResult,
        examResult,
        mediaResult,
        subjectResult,
        curriculumResult,
        sourceResult,
      ];
      const failure = workspaceResults.find(
        (result) => !result.response.ok || result.error,
      );
      if (failure) {
        setWorkspaceError(uiError(failure.error, failure.response.status));
        return;
      }
      setSummaries(completeGradeSummaries(summaryResult.data ?? []));
      setExamConfigurations(examResult.data ?? []);
      setMedia(mediaResult.data ?? []);
      setSubjects(subjectResult.data ?? []);
      setCurricula(curriculumResult.data ?? []);
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
    ): Promise<MaterialDiscovery> => {
      const result = await api.GET("/api/v1/admin/materials", {
        params: {
          query: {
            ...(grade === null ? { unassigned_only: true } : { grade }),
            limit: MATERIAL_PAGE_LIMIT,
            material_type: selectedMaterialType || null,
            medium_id: selectedMedium || null,
            offset,
            search: materialSearch.trim() || null,
            status: selectedMaterialStatus || null,
            subject_id: subjectId || null,
            year:
              Number(selectedYear) >= 1900 && Number(selectedYear) <= 2100
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
    ) => {
      try {
        const [summaryResult, sourceResult, materialResult] = await Promise.all(
          [
            api.GET("/api/v1/admin/materials/grade-summary"),
            api.GET("/api/v1/admin/source-documents"),
            discoverMaterials(grade, subjectId, 0),
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

  const wizardCurricula = useMemo(() => {
    const grade = Number(wizardGrade);
    return curricula.filter((curriculum) => {
      const configuration = examById.get(curriculum.exam_configuration_id);
      return (
        curriculum.active &&
        configuration?.grade === grade &&
        curriculum.medium_id === wizardMediumId &&
        curriculum.subject_id === wizardSubjectId
      );
    });
  }, [curricula, examById, wizardGrade, wizardMediumId, wizardSubjectId]);

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

  function resetWizard() {
    setWizardStep(0);
    setWizardGrade("");
    setWizardMediumId("");
    setWizardSubjectId("");
    setWizardMaterialType("syllabus");
    setWizardYear("");
    setWizardCurriculumId("");
    setWizardUnitId("");
    setWizardLessonId("");
    setWizardUnits([]);
    setWizardLessons([]);
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
    if (wizardStep === 1 && !wizardMediumId) {
      setWizardError({
        code: "medium_required",
        message: "Choose a medium to continue.",
      });
      return;
    }
    if (wizardStep === 2 && !wizardSubjectId) {
      setWizardError({
        code: "subject_required",
        message: "Choose a subject to continue.",
      });
      return;
    }
    if (wizardStep === 3) {
      const defaultCurriculum =
        wizardCurricula.find(
          (curriculum) => curriculum.id === wizardCurriculumId,
        ) ?? wizardCurricula[0];
      if (!defaultCurriculum) {
        setWizardError({
          code: "curriculum_required",
          message:
            "No active curriculum connects this grade, medium, and subject. Ask an administrator to configure it before uploading.",
        });
        return;
      }
      if (defaultCurriculum.id !== wizardCurriculumId) {
        setWizardCurriculumId(defaultCurriculum.id);
        void loadWizardScope(defaultCurriculum.id);
      }
    }
    if (wizardStep === 4) {
      if (!wizardCurriculumId) {
        setWizardError({
          code: "curriculum_required",
          message: "Choose the curriculum this material belongs to.",
        });
        return;
      }
      const needsYear = [
        "past_paper",
        "marking_scheme",
        "evaluation_report",
      ].includes(wizardMaterialType);
      const year = Number(wizardYear);
      if (
        needsYear &&
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
    setWizardError(null);
    setWizardStep(Math.max(0, wizardStep - 1) as WizardStep);
  }

  function submitUploadWizard(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
  }

  async function uploadMaterial() {
    const file = wizardFile;
    const fileError = validatePdf(file);
    if (fileError) {
      setWizardError(fileError);
      return;
    }
    if (!file) return;

    const numericYear = wizardYear ? Number(wizardYear) : null;
    const body: UploadBody = {
      curriculum_version_id: wizardCurriculumId || null,
      document_type: wizardMaterialType,
      file: file.name,
      lesson_id: wizardLessonId || null,
      paper_code: null,
      unit_id: wizardUnitId || null,
      year: Number.isInteger(numericYear) ? numericYear : null,
    };

    setUploading(true);
    setWizardError(null);
    setDuplicate(null);
    try {
      const uploadResult = await api.POST("/api/v1/admin/source-documents", {
        body,
        bodySerializer: (requestBody) => uploadFormData(requestBody, file),
      });
      if (uploadResult.error || !uploadResult.data) {
        setWizardError(
          uiError(uploadResult.error, uploadResult.response.status),
        );
        return;
      }

      const uploaded = uploadResult.data;
      if (uploaded.deduplicated || uploadResult.response.status === 200) {
        setDuplicate(uploaded);
        return;
      }

      const extractionResult = await api.POST(
        "/api/v1/admin/source-documents/{document_id}/extract",
        { params: { path: { document_id: uploaded.id } } },
      );
      const uploadedGrade = Number(wizardGrade);
      setSelectedGrade(uploadedGrade);
      setSelectedSubject(wizardSubjectId);
      await refreshCatalog(uploadedGrade, wizardSubjectId);
      setWizardOpen(false);
      resetWizard();
      if (extractionResult.error) {
        setNotice(
          "Material uploaded, but reading could not start. Open Advanced → Documents to retry reading.",
        );
      } else {
        setNotice("Material uploaded. Reading the PDF now.");
      }
    } catch {
      setWizardError(networkError());
    } finally {
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
    setScopeTarget(material);
    setConfirmIntakeMetadata(false);
    setScopeCurriculumId(source.curriculum_version_id ?? "");
    setScopeUnitId(source.unit_id ?? "");
    setScopeLessonId(source.lesson_id ?? "");
    setScopeError(null);
    if (source.curriculum_version_id) {
      await loadScopeChoices(source.curriculum_version_id, true);
    }
  }

  async function saveScope(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!scopeTarget) return;
    const body: ScopeBody = {
      confirm_intake_metadata: confirmIntakeMetadata && canConfirmIntake,
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
      const curriculum = curriculumById.get(scopeCurriculumId);
      const grade = curriculum
        ? examById.get(curriculum.exam_configuration_id)?.grade
        : undefined;
      await refreshCatalog();
      setScopeTarget(null);
      setNotice(grade ? `Moved to Grade ${grade}.` : "Material scope updated.");
    } catch {
      setScopeError(networkError());
    } finally {
      setScopeSaving(false);
    }
  }

  const selectedWizardMedium = mediumById.get(wizardMediumId);
  const selectedWizardSubject = subjectById.get(wizardSubjectId);
  const selectedWizardCurriculum = curriculumById.get(wizardCurriculumId);
  const selectedWizardUnit = wizardUnits.find(
    (unit) => unit.id === wizardUnitId,
  );
  const selectedWizardLesson = wizardLessons.find(
    (lesson) => lesson.id === wizardLessonId,
  );
  const scopeCurriculum = curriculumById.get(scopeCurriculumId);
  const scopeConfiguration = scopeCurriculum
    ? examById.get(scopeCurriculum.exam_configuration_id)
    : undefined;
  const scopeMedium = scopeCurriculum
    ? mediumById.get(scopeCurriculum.medium_id)
    : undefined;
  const scopeSubject = scopeCurriculum
    ? subjectById.get(scopeCurriculum.subject_id)
    : undefined;
  const scopeNeedsReview =
    scopeTarget?.metadata_review_required ||
    (scopeTarget
      ? sourceById.get(scopeTarget.id)?.metadata_review_required
      : false);
  const canConfirmIntake = Boolean(
    scopeNeedsReview &&
    scopeCurriculum?.active &&
    scopeConfiguration?.active &&
    scopeMedium?.active &&
    scopeSubject?.active &&
    !scopeLoading,
  );
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
        </div>
        {role === "admin" ? (
          <button
            className={primaryButton}
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

      {notice && (
        <p
          className="mt-6 rounded-lg border border-emerald-300 bg-emerald-50 p-4 text-sm font-semibold text-emerald-950"
          role="status"
        >
          {notice}
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
                  {subjects
                    .filter((subject) => subject.active)
                    .map((subject) => (
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
                  {media
                    .filter((item) => item.active)
                    .map((item) => (
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
                onClick={() => {
                  setMaterialSearch("");
                  setSelectedSubject("");
                  setSelectedMedium("");
                  setSelectedMaterialType("");
                  setSelectedMaterialStatus("");
                  setSelectedYear("");
                }}
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
                    const intake =
                      material.intake_metadata ?? source?.intake_metadata;
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

                        <MaterialIntakeMetadata
                          intake={intake}
                          reviewRequired={metadataReviewRequired}
                        />

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
                          {source &&
                            ["extracted", "in_review", "trusted"].includes(
                              source.extraction_status,
                            ) && (
                              <Link
                                aria-label={`Review extracted text: ${material.title}`}
                                className={secondaryButton}
                                href={`/admin/materials/${material.id}/review-text`}
                              >
                                Review extracted text
                              </Link>
                            )}
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
                Upload material
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

          <form className="mt-6 grid gap-5" onSubmit={submitUploadWizard}>
            {wizardStep === 0 && (
              <label className={fieldClass} htmlFor="upload-grade">
                Grade
                <select
                  className={inputClass}
                  id="upload-grade"
                  onChange={(event) => {
                    setWizardGrade(event.currentTarget.value);
                    setWizardCurriculumId("");
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
              <label className={fieldClass} htmlFor="upload-medium">
                Medium
                <select
                  className={inputClass}
                  id="upload-medium"
                  onChange={(event) => {
                    setWizardMediumId(event.currentTarget.value);
                    setWizardCurriculumId("");
                    setWizardError(null);
                  }}
                  value={wizardMediumId}
                >
                  <option value="">Choose medium</option>
                  {media
                    .filter((medium) => medium.active)
                    .map((medium) => (
                      <option key={medium.id} value={medium.id}>
                        {medium.name}
                      </option>
                    ))}
                </select>
              </label>
            )}

            {wizardStep === 2 && (
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
                  {subjects
                    .filter((subject) => subject.active)
                    .map((subject) => (
                      <option key={subject.id} value={subject.id}>
                        {subject.name}
                      </option>
                    ))}
                </select>
              </label>
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
                {["past_paper", "marking_scheme", "evaluation_report"].includes(
                  wizardMaterialType,
                ) && (
                  <label className={fieldClass} htmlFor="upload-year">
                    Year
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
                <label className={fieldClass} htmlFor="upload-curriculum">
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
                      <option key={curriculum.id} value={curriculum.id}>
                        {curriculum.title}
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
                    <label className={fieldClass} htmlFor="upload-lesson">
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
                    <dd className="mt-1">{selectedWizardMedium?.name}</dd>
                  </div>
                  <div>
                    <dt className="font-semibold text-slate-500">Subject</dt>
                    <dd className="mt-1">{selectedWizardSubject?.name}</dd>
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
                      {[wizardYear, selectedWizardCurriculum?.title]
                        .filter(Boolean)
                        .join(" · ")}
                    </dd>
                  </div>
                  {(selectedWizardUnit || selectedWizardLesson) && (
                    <div>
                      <dt className="font-semibold text-slate-500">Scope</dt>
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
              </section>
            )}

            {wizardError && (
              <InlineError
                error={wizardError}
                title="Upload was not completed."
              />
            )}

            {duplicate && (
              <div
                className="rounded-lg border border-amber-400 bg-amber-50 p-4 text-sm text-amber-950"
                role="alert"
              >
                <p className="font-semibold">
                  This exact PDF is already in Materials. No new copy was
                  uploaded.
                </p>
                <p className="mt-2 break-words">
                  {duplicate.original_filename}
                </p>
                <Link
                  className={`${secondaryButton} mt-4`}
                  href={`/admin/materials/${duplicate.id}`}
                >
                  View existing material
                </Link>
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-5">
              <button
                className={secondaryButton}
                disabled={wizardStep === 0 || uploading}
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
                  disabled={uploading}
                  onClick={() => void uploadMaterial()}
                  type="button"
                >
                  {uploading ? "Uploading…" : "Upload material"}
                </button>
              ) : null}
            </div>
          </form>
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
          <p className="mt-3 text-sm leading-6 text-slate-600">
            Select the correct curriculum. Its grade, medium, and subject are
            changed together so the material cannot cross education scopes
            accidentally.
          </p>
          <form className="mt-5 grid gap-5" onSubmit={saveScope}>
            <label className={fieldClass} htmlFor="scope-curriculum">
              Curriculum version
              <select
                className={inputClass}
                id="scope-curriculum"
                onChange={(event) => {
                  const id = event.currentTarget.value;
                  setScopeCurriculumId(id);
                  setConfirmIntakeMetadata(false);
                  setScopeError(null);
                  void loadScopeChoices(id);
                }}
                value={scopeCurriculumId}
              >
                <option value="">No curriculum assignment</option>
                {curricula
                  .filter((curriculum) => curriculum.active)
                  .map((curriculum) => {
                    const configuration = examById.get(
                      curriculum.exam_configuration_id,
                    );
                    const medium = mediumById.get(curriculum.medium_id);
                    const subject = subjectById.get(curriculum.subject_id);
                    return (
                      <option key={curriculum.id} value={curriculum.id}>
                        {configuration ? `Grade ${configuration.grade} · ` : ""}
                        {medium?.name ?? "Medium"} ·{" "}
                        {subject?.name ?? "Subject"} · {curriculum.title}
                      </option>
                    );
                  })}
              </select>
            </label>

            {scopeCurriculum && (
              <p className="rounded-lg bg-slate-100 p-3 text-sm text-slate-700">
                New assignment: Grade{" "}
                {scopeConfiguration?.grade ?? "not configured"} ·{" "}
                {scopeMedium?.name ?? "Medium not configured"} ·{" "}
                {scopeSubject?.name ?? "Subject not configured"}
              </p>
            )}

            {scopeLoading ? (
              <p className="text-sm text-slate-600" role="status">
                Loading units and lessons…
              </p>
            ) : (
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
                    onChange={(event) =>
                      setScopeLessonId(event.currentTarget.value)
                    }
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
            )}

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
                disabled={scopeSaving}
                type="submit"
              >
                {scopeSaving ? "Saving…" : "Save changes"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
