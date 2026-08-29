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
type GenerationOptions = components["schemas"]["TeacherPaperOptionsResponse"];
type CurriculumLabel = components["schemas"]["CurriculumLabelResponse"];
type Lesson = components["schemas"]["LessonOption"];
type PaperJob = components["schemas"]["TeacherPaperJobResponse"];
type PaperIntent = components["schemas"]["TeacherPaperJobCreateRequest"];
type Difficulty = components["schemas"]["PaperDifficulty"];
type PaperType = components["schemas"]["TeacherPaperType"];
type ScholarshipMode = components["schemas"]["ScholarshipPaperMode"];
type SchoolTerm = components["schemas"]["SchoolTerm"];
type UiError = {
  code: string;
  message: string;
  retryable: boolean;
  title: string;
};
type StoredSubmission = {
  body: PaperIntent;
  fingerprint: string;
  idempotencyKey: string;
};
type StoredRetry = {
  idempotencyKey: string;
  jobId: string;
  version: number;
};
type ApiOutcome = { error?: unknown; response: Response };
type JsonObject = Record<string, unknown>;

const MAX_QUESTIONS = 50;
const MAX_DURATION_MINUTES = 600;
const POLL_DELAYS_MS = [0, 150, 300, 600, 1_000, 2_000, 4_000, 5_000, 5_000, 5_000] as const;
const POLL_MAX_DURATION_MS = 60_000;
const TERMINAL_JOB_STATUSES = new Set<PaperJob["status"]>(["ready_for_review", "failed"]);

const fieldClass = "grid gap-1.5 text-sm font-semibold text-slate-800";
const inputClass =
  "min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 outline-none transition focus:border-amber-600 focus:ring-2 focus:ring-amber-200 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none transition hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-500 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function detailCode(error: unknown): string {
  const detail = asObject(asObject(error)?.detail);
  return typeof detail?.code === "string" ? detail.code : "request_failed";
}

function firstFailure(outcomes: readonly ApiOutcome[]): ApiOutcome | undefined {
  return outcomes.find((outcome) => outcome.error !== undefined);
}

function generationError(error: unknown, response: Response, surface: "selection" | "create" | "poll" | "retry"): UiError {
  const code = detailCode(error);
  const status = response.status;
  if (status === 401) {
    return {
      code: "authentication_required",
      message: "Your session has expired. Sign in again before continuing.",
      retryable: false,
      title: "Sign in again",
    };
  }
  if (status === 403) {
    return {
      code: "permission_denied",
      message:
        surface === "selection" || surface === "poll"
          ? "This account cannot view paper-generation information."
          : "This account cannot generate papers. Ask an administrator for generation access.",
      retryable: false,
      title: "Permission required",
    };
  }
  if (status === 404 || code === "paper_generation_curriculum_not_found") {
    return {
      code,
      message:
        surface === "poll"
          ? "This paper is no longer available. Your generation choices are still here."
          : "No matching curriculum content is available for these choices. Choose another target or ask a curriculum administrator to add reviewed material.",
      retryable: surface === "poll",
      title: surface === "poll" ? "Paper not found" : "No matching curriculum content",
    };
  }
  if (status === 409) {
    if (code === "paper_generation_curriculum_ambiguous") {
      return {
        code,
        message:
          "More than one curriculum matches these choices. Ask a curriculum administrator to resolve the duplicate active curriculum before generating.",
        retryable: true,
        title: "More than one curriculum matches",
      };
    }
    if (code === "paper_generation_idempotency_conflict") {
      return {
        code,
        message:
          "This safe retry no longer matches the original paper request. Review your choices and start a new generation request.",
        retryable: false,
        title: "Paper request changed",
      };
    }
    if (code === "paper_generation_retry_limit_exceeded") {
      return {
        code,
        message:
          "The failed questions have reached the safe retry limit. Open the paper for review or start a new paper request.",
        retryable: false,
        title: "Retry limit reached",
      };
    }
    if (code === "paper_generation_cost_limit_exceeded") {
      return {
        code,
        message:
          "This paper reached its configured generation limit. Review any prepared questions or start a smaller paper.",
        retryable: false,
        title: "Paper generation limit reached",
      };
    }
    return {
      code,
      message:
        "The paper changed while this action was being processed. Check the latest progress before trying again.",
      retryable: true,
      title: "Paper state changed",
    };
  }
  if (status === 422) {
    const messages: Record<string, { message: string; title: string }> = {
      paper_generation_context_unavailable: {
        message:
          "There is not enough reviewed material for this scope yet. Choose another scope or add material that is Ready for AI.",
        title: "Reviewed material is needed",
      },
      paper_generation_curriculum_content_missing: {
        message: "No reviewed lesson content is available for this subject yet.",
        title: "No content available",
      },
      paper_generation_lesson_range_invalid: {
        message: "Choose an inclusive lesson range with the first lesson before the last lesson.",
        title: "Check the lesson range",
      },
      paper_generation_lesson_range_not_found: {
        message: "One or more lessons in that range are not available in this curriculum.",
        title: "Lesson range unavailable",
      },
      paper_generation_lesson_unmapped: {
        message:
          "A selected lesson is not ready for paper generation. Ask a curriculum administrator to complete its educational mapping.",
        title: "Lesson is not ready",
      },
      paper_generation_pilot_grade_unavailable: {
        message: "The teacher pilot currently supports Grade 5 only.",
        title: "Grade is not available in the pilot",
      },
      paper_generation_programme_policy_unavailable: {
        message:
          "The reviewed Grade 5 Scholarship programme policy is not ready yet. No paper was generated.",
        title: "Scholarship policy is not ready",
      },
      paper_generation_term_policy_unavailable: {
        message: "The selected term does not yet have reviewed curriculum coverage.",
        title: "Term coverage is not ready",
      },
      paper_generation_scope_invalid: {
        message: "The selected curriculum scope is not valid. Review the lesson choices and try again.",
        title: "Check the curriculum scope",
      },
      paper_generation_slot_lesson_mapping_missing: {
        message:
          "The paper could not be matched safely to every selected lesson. Try a smaller range or ask a curriculum administrator to review the lesson mapping.",
        title: "Paper scope could not be matched",
      },
    };
    const known = messages[code];
    return {
      code,
      message:
        known?.message ??
        "The paper request contains a choice the service cannot accept. Review the target, scope, and paper settings.",
      retryable: false,
      title: known?.title ?? "Check the paper choices",
    };
  }
  if (status === 429 || code === "rate_limit_exceeded") {
    const retryAfter = response.headers.get("Retry-After");
    return {
      code,
      message: retryAfter
        ? `Several generation requests were made recently. Try again in ${retryAfter} seconds; your choices have been kept.`
        : "Several generation requests were made recently. Wait a short time and try again; your choices have been kept.",
      retryable: true,
      title: "Please wait before trying again",
    };
  }
  if (status === 503) {
    return {
      code,
      message:
        code === "paper_generation_runtime_unavailable"
          ? "Paper generation is temporarily unavailable. Your choices have been kept so you can try again safely."
          : code === "rate_limiter_unavailable"
            ? "The service cannot safely accept a costly request right now. Your choices have been kept so you can try again later."
            : "The paper service is temporarily unavailable. Your choices have been kept so you can try again safely.",
      retryable: true,
      title: "Paper generation is temporarily unavailable",
    };
  }
  return {
    code,
    message:
      surface === "poll"
        ? "Progress could not be checked. The paper may still be running; check again without starting a duplicate request."
        : "The request could not be completed. Your choices have been kept so you can try again safely.",
    retryable: true,
    title: surface === "poll" ? "Progress check paused" : "Paper request failed",
  };
}

function networkError(surface: "selection" | "create" | "poll" | "retry"): UiError {
  return {
    code: "network_error",
    message:
      surface === "poll"
        ? "Progress could not be checked. The paper may still be running; check again without starting a duplicate request."
        : "The service could not be reached. Your choices have been kept so you can try the same request safely.",
    retryable: true,
    title: surface === "poll" ? "Progress check paused" : "Connection unavailable",
  };
}

function secureOperationKey(prefix: "teacher-paper" | "teacher-paper-retry"): string {
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
  const key = `${prefix}-${random}`;
  if (key.length > 128 || /\s/.test(key)) throw new Error("Unsafe operation key");
  return key;
}

function Panel({ children, description, title }: { children: ReactNode; description: string; title: string }) {
  return (
    <section className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6">
      <header className="border-b border-slate-200 pb-4">
        <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-600">{description}</p>
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

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds));
}

function stageIsReached(job: PaperJob, stage: "preparing" | "generating" | "checking_answers" | "ready_for_review") {
  if (job.progress.includes(stage)) return true;
  if (stage === "generating" && job.progress.includes("generating_questions")) return true;
  const rank: Record<PaperJob["status"], number> = {
    checking_answers: 3,
    failed: 0,
    generating: 2,
    preparing: 1,
    ready_for_review: 4,
  };
  const target = { checking_answers: 3, generating: 2, preparing: 1, ready_for_review: 4 }[stage];
  return job.status !== "failed" && rank[job.status] >= target;
}

export function GeneratePapersWizard({ role }: { role: Role }) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [options, setOptions] = useState<GenerationOptions | null>(null);
  const [optionsLoading, setOptionsLoading] = useState(true);
  const [optionsError, setOptionsError] = useState<UiError | null>(null);
  const [grade, setGrade] = useState("");
  const [medium, setMedium] = useState("");
  const [paperType, setPaperType] = useState<PaperType | "">("");
  const [subject, setSubject] = useState("");
  const [term, setTerm] = useState<SchoolTerm | "">("");
  const [scholarshipMode, setScholarshipMode] = useState<ScholarshipMode | "">("");
  const [curriculum, setCurriculum] = useState<CurriculumLabel | null>(null);
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [selectionLoading, setSelectionLoading] = useState(false);
  const [selectionError, setSelectionError] = useState<UiError | null>(null);
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [scopeKind, setScopeKind] = useState<
    "full_subject" | "full_term" | "programme" | "lesson_range" | "selected_lessons"
  >("full_subject");
  const [firstLesson, setFirstLesson] = useState("");
  const [lastLesson, setLastLesson] = useState("");
  const [selectedLessonNumbers, setSelectedLessonNumbers] = useState<number[]>([]);
  const [paperName, setPaperName] = useState("Grade 5 practice paper");
  const [mcqCount, setMcqCount] = useState(5);
  const [writtenCount, setWrittenCount] = useState(5);
  const [structuredCount, setStructuredCount] = useState(0);
  const [durationMinutes, setDurationMinutes] = useState(45);
  const [difficulty, setDifficulty] = useState<Difficulty>("balanced");
  const [teacherInstruction, setTeacherInstruction] = useState("");
  const [formError, setFormError] = useState("");
  const [requestError, setRequestError] = useState<UiError | null>(null);
  const [busy, setBusy] = useState<"create" | "retry" | "poll" | "">("");
  const [job, setJob] = useState<PaperJob | null>(null);
  const [pollingStopped, setPollingStopped] = useState(false);

  const optionsRequest = useRef(0);
  const selectionRequest = useRef(0);
  const pollRequest = useRef(0);
  const submission = useRef<StoredSubmission | null>(null);
  const failedRetry = useRef<StoredRetry | null>(null);

  const availableSubjects = useMemo(() => {
    if (!options || !grade || !medium) return [];
    const deduplicated = new Map<string, GenerationOptions["subjects"][number]>();
    for (const item of options.subjects) {
      if (item.grade === Number(grade) && item.medium === medium && !deduplicated.has(item.code)) {
        deduplicated.set(item.code, item);
      }
    }
    return [...deduplicated.values()];
  }, [grade, medium, options]);

  const availablePaperTypes = useMemo(
    () => options?.paper_types.filter((item) => item.grade === Number(grade)) ?? [],
    [grade, options],
  );

  const selectedSubject = useMemo(
    () => availableSubjects.find((item) => item.code === subject) ?? null,
    [availableSubjects, subject],
  );
  const subjectLabel = selectedSubject?.label ?? subject;
  const scholarshipSelected = paperType === "scholarship_practice";
  const targetReady = Boolean(
    grade &&
      medium &&
      paperType &&
      (scholarshipSelected ? scholarshipMode : subject) &&
      (paperType !== "term_test" || term),
  );
  const lessonNumbers = useMemo(() => new Set(lessons.map((lesson) => lesson.number)), [lessons]);
  const rangeStart = Number(firstLesson);
  const rangeEnd = Number(lastLesson);
  const rangeValid =
    scopeKind !== "lesson_range" ||
    (Number.isInteger(rangeStart) &&
      Number.isInteger(rangeEnd) &&
      rangeStart <= rangeEnd &&
      Array.from({ length: rangeEnd - rangeStart + 1 }, (_, index) => rangeStart + index).every(
        (number) => lessonNumbers.has(number),
      ));
  const selectedLessonsValid =
    scopeKind !== "selected_lessons" ||
    (selectedLessonNumbers.length > 0 &&
      selectedLessonNumbers.every((number) => lessonNumbers.has(number)));
  const scopeValid = rangeValid && selectedLessonsValid;
  const totalQuestions = mcqCount + writtenCount + structuredCount;
  const settingsValid =
    Boolean(paperName.trim()) &&
    [mcqCount, writtenCount, structuredCount].every(
      (count) => Number.isInteger(count) && count >= 0 && count <= MAX_QUESTIONS,
    ) &&
    totalQuestions >= 1 &&
    totalQuestions <= MAX_QUESTIONS &&
    Number.isInteger(durationMinutes) &&
    durationMinutes >= 1 &&
    durationMinutes <= MAX_DURATION_MINUTES;

  const loadOptions = useCallback(async () => {
    const requestId = ++optionsRequest.current;
    setOptionsLoading(true);
    setOptionsError(null);
    try {
      const outcome = await api.GET("/api/v1/admin/paper-generation/options");
      if (requestId !== optionsRequest.current) return;
      if (outcome.error !== undefined) {
        setOptionsError(generationError(outcome.error, outcome.response, "selection"));
        return;
      }
      const data = outcome.data;
      if (!data) {
        setOptionsError({
          code: "paper_generation_options_empty",
          message: "Paper choices were not returned. Try loading them again.",
          retryable: true,
          title: "Paper choices unavailable",
        });
        return;
      }
      setOptions(data);
      setPaperName(data.defaults.paper_name);
      setMcqCount(data.defaults.mcq_count);
      setWrittenCount(data.defaults.written_count);
      setStructuredCount(data.defaults.structured_count);
      setDurationMinutes(data.defaults.duration_minutes);
      setDifficulty(data.defaults.difficulty);
      setTeacherInstruction(data.defaults.teacher_instruction ?? "");
    } catch {
      if (requestId === optionsRequest.current) setOptionsError(networkError("selection"));
    } finally {
      if (requestId === optionsRequest.current) setOptionsLoading(false);
    }
  }, [api]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadOptions(), 0);
    return () => {
      window.clearTimeout(timeout);
      optionsRequest.current += 1;
      selectionRequest.current += 1;
      pollRequest.current += 1;
    };
  }, [loadOptions]);

  useEffect(() => {
    if (!targetReady || scholarshipSelected) return;
    const timeout = window.setTimeout(() => {
      const requestId = ++selectionRequest.current;
      setSelectionLoading(true);
      setSelectionError(null);
      setCurriculum(null);
      setLessons([]);
      const query = {
        grade: Number(grade),
        medium,
        subject,
      };
      void (async () => {
      try {
        const outcomes = await Promise.all([
          api.GET("/api/v1/admin/paper-generation/curricula", { params: { query } }),
          api.GET("/api/v1/admin/paper-generation/lessons", { params: { query } }),
        ]);
        if (requestId !== selectionRequest.current) return;
        const failure = firstFailure(outcomes);
        if (failure?.error !== undefined) {
          setSelectionError(generationError(failure.error, failure.response, "selection"));
          return;
        }
        const curricula = outcomes[0].data?.items ?? [];
        const lessonResponse = outcomes[1].data;
        if (!curricula.length || !lessonResponse) {
          setSelectionError({
            code: "paper_generation_curriculum_not_found",
            message:
              "No matching curriculum content is available for these choices. Choose another target or ask a curriculum administrator to add reviewed material.",
            retryable: true,
            title: "No matching curriculum content",
          });
          return;
        }
        if (curricula.length > 1) {
          setSelectionError({
            code: "paper_generation_curriculum_ambiguous",
            message:
              "More than one curriculum matches these choices. Ask a curriculum administrator to resolve the duplicate active curriculum before generating.",
            retryable: true,
            title: "More than one curriculum matches",
          });
          return;
        }
        if (
          lessonResponse.grade !== Number(grade) ||
          lessonResponse.medium !== medium ||
          lessonResponse.subject !== subject
        ) {
          setSelectionError({
            code: "paper_generation_scope_mismatch",
            message: "The returned lesson choices do not match the selected target. Reload before continuing.",
            retryable: true,
            title: "Curriculum choices changed",
          });
          return;
        }
        const nextLessons = [...lessonResponse.lessons].sort((left, right) => left.number - right.number);
        setCurriculum(curricula[0] ?? null);
        setLessons(nextLessons);
        setSelectedLessonNumbers([]);
        setFirstLesson(String(nextLessons[0]?.number ?? ""));
        setLastLesson(String(nextLessons.at(-1)?.number ?? ""));
        } catch {
          if (requestId === selectionRequest.current) setSelectionError(networkError("selection"));
        } finally {
          if (requestId === selectionRequest.current) setSelectionLoading(false);
        }
      })();
    }, 0);
    return () => {
      window.clearTimeout(timeout);
      selectionRequest.current += 1;
    };
  }, [api, grade, medium, scholarshipSelected, subject, targetReady, term]);

  const pollJob = useCallback(
    async (initialJob: PaperJob) => {
      const requestId = ++pollRequest.current;
      const startedAt = Date.now();
      let latest = initialJob;
      setPollingStopped(false);
      if (TERMINAL_JOB_STATUSES.has(latest.status)) return;
      setBusy("poll");
      for (const wait of POLL_DELAYS_MS) {
        if (requestId !== pollRequest.current) return;
        if (Date.now() - startedAt > POLL_MAX_DURATION_MS) break;
        if (wait) await delay(wait);
        if (requestId !== pollRequest.current) return;
        try {
          const outcome = await api.GET(
            "/api/v1/admin/paper-generation/jobs/{paper_job_id}",
            { params: { path: { paper_job_id: latest.job_id } } },
          );
          if (requestId !== pollRequest.current) return;
          if (outcome.error !== undefined) {
            setRequestError(generationError(outcome.error, outcome.response, "poll"));
            setBusy("");
            setPollingStopped(true);
            return;
          }
          if (!outcome.data) continue;
          latest = outcome.data;
          setJob(latest);
          if (TERMINAL_JOB_STATUSES.has(latest.status)) {
            setBusy("");
            return;
          }
        } catch {
          if (requestId === pollRequest.current) {
            setRequestError(networkError("poll"));
            setBusy("");
            setPollingStopped(true);
          }
          return;
        }
      }
      if (requestId === pollRequest.current) {
        setBusy("");
        setPollingStopped(true);
      }
    },
    [api],
  );

  function buildIntent(): PaperIntent | null {
    setFormError("");
    if (!targetReady || (!scholarshipSelected && !curriculum)) {
      setFormError("Choose an available Grade 5 paper target before continuing.");
      return null;
    }
    if (!scopeValid) {
      setFormError(
        scopeKind === "selected_lessons"
          ? "Choose at least one available lesson."
          : "Choose an exact inclusive lesson range from the available lessons.",
      );
      return null;
    }
    if (!settingsValid) {
      setFormError(
        `Choose 1–${MAX_QUESTIONS} questions in total and a duration from 1–${MAX_DURATION_MINUTES} minutes.`,
      );
      return null;
    }
    const scope: PaperIntent["scope"] =
      scopeKind === "lesson_range"
        ? { end_lesson: rangeEnd, kind: "lesson_range", start_lesson: rangeStart }
        : scopeKind === "selected_lessons"
          ? { kind: "selected_lessons", lesson_numbers: selectedLessonNumbers }
          : { kind: scopeKind };
    const target: PaperIntent["target"] = {
      grade: Number(grade),
      medium,
      paper_type: paperType as PaperType,
      ...(subject ? { subject } : {}),
      ...(term ? { term } : {}),
      ...(scholarshipMode ? { scholarship_mode: scholarshipMode } : {}),
    };
    return {
      scope,
      settings: {
        difficulty,
        duration_minutes: durationMinutes,
        mcq_count: mcqCount,
        paper_name: paperName.trim(),
        structured_count: structuredCount,
        teacher_instruction: teacherInstruction.trim() || null,
        written_count: writtenCount,
      },
      target,
    };
  }

  const createPaper = useCallback(
    async (body: PaperIntent) => {
      const fingerprint = JSON.stringify(body);
      let stored = submission.current;
      if (!stored || stored.fingerprint !== fingerprint) {
        try {
          stored = {
            body,
            fingerprint,
            idempotencyKey: secureOperationKey("teacher-paper"),
          };
        } catch {
          setRequestError({
            code: "secure_randomness_unavailable",
            message: "This browser cannot create a safe paper request. Reload in a supported browser.",
            retryable: false,
            title: "Safe request unavailable",
          });
          return;
        }
        submission.current = stored;
      }
      setBusy("create");
      setRequestError(null);
      setPollingStopped(false);
      pollRequest.current += 1;
      try {
        const outcome = await api.POST("/api/v1/admin/paper-generation/jobs", {
          body: stored.body,
          params: { header: { "Idempotency-Key": stored.idempotencyKey } },
        });
        if (outcome.error !== undefined) {
          setRequestError(generationError(outcome.error, outcome.response, "create"));
          return;
        }
        if (!outcome.data) {
          setRequestError({
            code: "paper_generation_response_empty",
            message: "The service accepted no readable paper job. Try the same request safely.",
            retryable: true,
            title: "Paper response unavailable",
          });
          return;
        }
        setJob(outcome.data);
        setStep(4);
        setBusy("");
        await pollJob(outcome.data);
      } catch {
        setRequestError(networkError("create"));
      } finally {
        setBusy((current) => (current === "create" ? "" : current));
      }
    },
    [api, pollJob],
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const intent = buildIntent();
    if (intent) await createPaper(intent);
  }

  async function checkProgress() {
    if (!job || busy) return;
    setRequestError(null);
    setPollingStopped(false);
    await pollJob(job);
  }

  async function retryFailedQuestions() {
    if (!job || busy || job.status !== "failed") return;
    let stored = failedRetry.current;
    if (!stored || stored.jobId !== job.job_id || stored.version !== job.version) {
      try {
        stored = {
          idempotencyKey: secureOperationKey("teacher-paper-retry"),
          jobId: job.job_id,
          version: job.version,
        };
      } catch {
        setRequestError({
          code: "secure_randomness_unavailable",
          message: "This browser cannot create a safe retry. Reload in a supported browser.",
          retryable: false,
          title: "Safe retry unavailable",
        });
        return;
      }
      failedRetry.current = stored;
    }
    setBusy("retry");
    setRequestError(null);
    try {
      const outcome = await api.POST(
        "/api/v1/admin/paper-generation/jobs/{paper_job_id}/retry",
        {
          body: { expected_version: stored.version },
          params: {
            header: { "Idempotency-Key": stored.idempotencyKey },
            path: { paper_job_id: stored.jobId },
          },
        },
      );
      if (outcome.error !== undefined) {
        setRequestError(generationError(outcome.error, outcome.response, "retry"));
        return;
      }
      if (!outcome.data) return;
      setJob(outcome.data);
      setBusy("");
      await pollJob(outcome.data);
    } catch {
      setRequestError(networkError("retry"));
    } finally {
      setBusy((current) => (current === "retry" ? "" : current));
    }
  }

  function resetAfterTargetChange() {
    setStep(1);
    setJob(null);
    setCurriculum(null);
    setLessons([]);
    setSelectedLessonNumbers([]);
    setSelectionError(null);
    setSelectionLoading(false);
    setRequestError(null);
    setFormError("");
    submission.current = null;
    failedRetry.current = null;
    pollRequest.current += 1;
  }

  const scholarshipLabel =
    options?.scholarship_modes.find((item) => item.code === scholarshipMode)?.label ?? "Scholarship";
  const termLabel = options?.terms.find((item) => item.code === term)?.label ?? "Term";
  const selectedLessonsLabel =
    selectedLessonNumbers.length === 1
      ? String(selectedLessonNumbers[0])
      : selectedLessonNumbers.length === 2
        ? `${selectedLessonNumbers[0]} and ${selectedLessonNumbers[1]}`
        : selectedLessonNumbers.length > 2
          ? `${selectedLessonNumbers.slice(0, -1).join(", ")}, and ${selectedLessonNumbers.at(-1)}`
          : "none";
  const selectedScope =
    scopeKind === "programme"
      ? `Grade ${grade} Scholarship · ${scholarshipLabel}`
      : scopeKind === "full_term"
        ? `Grade ${grade} ${subjectLabel} · ${termLabel}`
        : scopeKind === "full_subject"
          ? `Grade ${grade} ${subjectLabel} · Full subject`
          : scopeKind === "selected_lessons"
            ? `Grade ${grade} ${subjectLabel} · Lessons ${selectedLessonsLabel}`
            : `Grade ${grade} ${subjectLabel} · Lessons ${firstLesson || "?"}–${lastLesson || "?"}`;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-5 py-8 sm:px-8 sm:py-10">
      <header className="rounded-3xl bg-slate-900 p-6 text-white shadow-lg sm:p-8">
        <p className="text-xs font-semibold tracking-[0.18em] text-amber-300 uppercase">
          Teacher paper builder
        </p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Generate Papers</h1>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-200 sm:text-base">
          Choose who the paper is for, select the curriculum scope, and set simple paper details.
          Blueprint, source selection, generation, and answer checks happen safely behind the scenes.
        </p>
      </header>

      {optionsLoading ? (
        <section className="rounded-2xl border border-slate-300 bg-white p-6" aria-live="polite">
          Loading paper choices…
        </section>
      ) : optionsError ? (
        <ErrorPanel
          error={optionsError}
          action={
            optionsError.retryable ? (
              <button className={secondaryButton} onClick={() => void loadOptions()} type="button">
                Load choices again
              </button>
            ) : undefined
          }
        />
      ) : options ? (
        <form aria-label="Generate a paper" className="space-y-6" onSubmit={(event) => void submit(event)}>
          <Panel
            description="Start with teacher-readable curriculum choices. No internal curriculum or generation identifiers are needed."
            title="1. Choose the paper target"
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <label className={fieldClass}>
                Grade
                <select
                  className={inputClass}
                  onChange={(event) => {
                    setGrade(event.target.value);
                    setPaperType("");
                    setSubject("");
                    setTerm("");
                    setScholarshipMode("");
                    resetAfterTargetChange();
                  }}
                  value={grade}
                >
                  <option value="">Choose grade</option>
                  {options.grades.map((item) => (
                    <option key={item} value={item}>
                      Grade {item}
                    </option>
                  ))}
                </select>
              </label>

              <label className={fieldClass}>
                Medium
                <select
                  className={inputClass}
                  onChange={(event) => {
                    setMedium(event.target.value);
                    setPaperType("");
                    setSubject("");
                    setTerm("");
                    setScholarshipMode("");
                    resetAfterTargetChange();
                  }}
                  value={medium}
                >
                  <option value="">Choose medium</option>
                  {options.media.map((item) => (
                    <option key={item.code} value={item.code}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <fieldset className="mt-5 space-y-3">
              <legend className="text-sm font-semibold text-slate-800">Paper type</legend>
              <div className="grid gap-3 md:grid-cols-3">
                {availablePaperTypes.map((item) => (
                  <label
                    className="flex min-h-20 cursor-pointer items-start gap-3 rounded-xl border border-slate-300 bg-white p-4 focus-within:ring-2 focus-within:ring-amber-500"
                    key={item.code}
                  >
                    <input
                      aria-label={item.label}
                      checked={paperType === item.code}
                      className="mt-1 size-4 accent-slate-950"
                      disabled={!grade || !medium}
                      name="paper-type"
                      onChange={() => {
                        setPaperType(item.code);
                        setSubject("");
                        setTerm("");
                        setScholarshipMode("");
                        setScopeKind(
                          item.code === "scholarship_practice"
                            ? "programme"
                            : item.code === "term_test"
                              ? "full_term"
                              : "full_subject",
                        );
                        resetAfterTargetChange();
                      }}
                      type="radio"
                    />
                    <span className="font-semibold">{item.label}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            {paperType && !scholarshipSelected ? (
              <div className="mt-5 grid gap-4 sm:grid-cols-2">
                <label className={fieldClass}>
                  Subject
                  <select
                    aria-label="Subject"
                    className={inputClass}
                    disabled={!grade || !medium}
                    onChange={(event) => {
                      setSubject(event.target.value);
                      resetAfterTargetChange();
                    }}
                    value={subject}
                  >
                    <option value="">Choose subject</option>
                    {availableSubjects.map((item) => (
                      <option key={item.code} value={item.code}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>
                {paperType === "term_test" ? (
                  <label className={fieldClass}>
                    Term
                    <select
                      aria-label="Term"
                      className={inputClass}
                      onChange={(event) => {
                        setTerm(event.target.value as SchoolTerm);
                        resetAfterTargetChange();
                      }}
                      value={term}
                    >
                      <option value="">Choose term</option>
                      {options.terms.map((item) => (
                        <option key={item.code} value={item.code}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
              </div>
            ) : null}

            {scholarshipSelected ? (
              <fieldset className="mt-5 space-y-3">
                <legend className="text-sm font-semibold text-slate-800">Scholarship paper</legend>
                <p className="text-sm leading-6 text-slate-600">
                  Uses the reviewed Grade 5 Scholarship coverage configured for this exam.
                </p>
                <div className="grid gap-3 md:grid-cols-3">
                  {options.scholarship_modes.map((item) => (
                    <label
                      className="flex min-h-24 cursor-pointer items-start gap-3 rounded-xl border border-slate-300 bg-white p-4 focus-within:ring-2 focus-within:ring-amber-500"
                      key={item.code}
                    >
                      <input
                        aria-label={item.label}
                        checked={scholarshipMode === item.code}
                        className="mt-1 size-4 accent-slate-950"
                        name="scholarship-mode"
                        onChange={() => {
                          setScholarshipMode(item.code);
                          resetAfterTargetChange();
                        }}
                        type="radio"
                      />
                      <span className="font-semibold">{item.label}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            ) : null}

            {selectionLoading ? (
              <p className="mt-4 text-sm text-slate-600" aria-live="polite">
                Checking curriculum and lesson availability…
              </p>
            ) : null}
            {selectionError ? (
              <div className="mt-4">
                <ErrorPanel error={selectionError} />
              </div>
            ) : null}
            {curriculum ? (
              <div className="mt-4 rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-sm text-emerald-950">
                <p className="font-semibold">Curriculum available</p>
                <p className="mt-1">
                  {curriculum.label} · {curriculum.assessment_label}
                </p>
              </div>
            ) : null}

            <div className="mt-5 flex justify-end">
              <button
                className={primaryButton}
                disabled={
                  !targetReady ||
                  selectionLoading ||
                  Boolean(selectionError) ||
                  (!scholarshipSelected && !curriculum)
                }
                onClick={() => setStep(2)}
                type="button"
              >
                Continue to scope
              </button>
            </div>
          </Panel>

          {step >= 2 ? (
            <Panel
              description="Choose the whole subject, individual lessons, or an inclusive lesson range. Only reviewed material in this scope may be used."
              title="2. Choose curriculum scope"
            >
              {scholarshipSelected ? (
                <section className="rounded-xl border border-slate-300 bg-slate-50 p-4">
                  <h3 className="font-semibold">Scholarship programme coverage</h3>
                  <p className="mt-1 text-sm leading-6 text-slate-600">
                    The selected Paper I, Paper II, or full Scholarship policy controls coverage. No
                    ordinary single-subject lesson picker is needed.
                  </p>
                </section>
              ) : (
                <fieldset className="space-y-4">
                  <legend className="text-sm font-semibold text-slate-800">Paper coverage</legend>
                  <label className="flex min-h-12 cursor-pointer items-start gap-3 rounded-xl border border-slate-300 bg-white p-4 focus-within:ring-2 focus-within:ring-amber-500">
                    <input
                      aria-label={paperType === "term_test" ? "All lessons for this term" : "Full subject"}
                      checked={scopeKind === (paperType === "term_test" ? "full_term" : "full_subject")}
                      className="mt-1 size-4 accent-slate-950"
                      name="scope"
                      onChange={() => {
                        setScopeKind(paperType === "term_test" ? "full_term" : "full_subject");
                        setFormError("");
                        submission.current = null;
                      }}
                      type="radio"
                    />
                    <span>
                      <span className="block font-semibold">
                        {paperType === "term_test" ? "All lessons for this term" : "Full subject"}
                      </span>
                      <span className="mt-1 block text-sm text-slate-600">
                        Use all reviewed content within this exact scope.
                      </span>
                    </span>
                  </label>
                  <label className="flex min-h-12 cursor-pointer items-start gap-3 rounded-xl border border-slate-300 bg-white p-4 focus-within:ring-2 focus-within:ring-amber-500">
                    <input
                      aria-label="Choose specific lessons"
                      checked={scopeKind === "lesson_range"}
                      className="mt-1 size-4 accent-slate-950"
                      name="scope"
                      onChange={() => {
                        setScopeKind("lesson_range");
                        setFormError("");
                        submission.current = null;
                      }}
                      type="radio"
                    />
                    <span>
                      <span className="block font-semibold">Choose specific lessons</span>
                      <span className="mt-1 block text-sm text-slate-600">
                        Include every lesson from the first through the last selected lesson.
                      </span>
                    </span>
                  </label>
                  <label className="flex min-h-12 cursor-pointer items-start gap-3 rounded-xl border border-slate-300 bg-white p-4 focus-within:ring-2 focus-within:ring-amber-500">
                    <input
                      aria-label="Pick individual lessons"
                      checked={scopeKind === "selected_lessons"}
                      className="mt-1 size-4 accent-slate-950"
                      name="scope"
                      onChange={() => {
                        setScopeKind("selected_lessons");
                        setFormError("");
                        submission.current = null;
                      }}
                      type="radio"
                    />
                    <span>
                      <span className="block font-semibold">Pick individual lessons</span>
                      <span className="mt-1 block text-sm text-slate-600">
                        Choose only the lessons you want, even when they are not next to each other.
                      </span>
                    </span>
                  </label>
                </fieldset>
              )}

              {scopeKind === "lesson_range" ? (
                lessons.length ? (
                  <div className="mt-4 grid gap-4 sm:grid-cols-2">
                    <label className={fieldClass}>
                      First lesson
                      <select
                        className={inputClass}
                        onChange={(event) => {
                          setFirstLesson(event.target.value);
                          setFormError("");
                          submission.current = null;
                        }}
                        value={firstLesson}
                      >
                        {lessons.map((lesson) => (
                          <option key={lesson.code} value={lesson.number}>
                            {lesson.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className={fieldClass}>
                      Last lesson
                      <select
                        className={inputClass}
                        onChange={(event) => {
                          setLastLesson(event.target.value);
                          setFormError("");
                          submission.current = null;
                        }}
                        value={lastLesson}
                      >
                        {lessons.map((lesson) => (
                          <option key={lesson.code} value={lesson.number}>
                            {lesson.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                ) : (
                  <ErrorPanel
                    error={{
                      code: "paper_generation_lessons_empty",
                      message: "No active lessons are available for a lesson-range paper.",
                      retryable: false,
                      title: "No lessons available",
                    }}
                  />
                )
              ) : null}

              {scopeKind === "selected_lessons" ? (
                lessons.length ? (
                  <fieldset className="mt-4 rounded-xl border border-slate-300 bg-white p-4">
                    <legend className="px-1 text-sm font-semibold text-slate-800">
                      Lessons to include
                    </legend>
                    <div className="mt-2 grid gap-2 sm:grid-cols-2">
                      {lessons.map((lesson) => (
                        <label
                          className="flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border border-slate-200 p-3 focus-within:ring-2 focus-within:ring-amber-500"
                          key={lesson.code}
                        >
                          <input
                            aria-label={lesson.label}
                            checked={selectedLessonNumbers.includes(lesson.number)}
                            className="mt-1 size-4 accent-slate-950"
                            onChange={(event) => {
                              setSelectedLessonNumbers((current) =>
                                event.target.checked
                                  ? [...current, lesson.number].sort((left, right) => left - right)
                                  : current.filter((number) => number !== lesson.number),
                              );
                              setFormError("");
                              submission.current = null;
                            }}
                            type="checkbox"
                          />
                          <span>
                            <span className="block font-semibold">{lesson.label}</span>
                            <span className="mt-1 block text-xs text-slate-600">{lesson.unit}</span>
                          </span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                ) : (
                  <ErrorPanel
                    error={{
                      code: "paper_generation_lessons_empty",
                      message: "No active lessons are available for individual selection.",
                      retryable: false,
                      title: "No lessons available",
                    }}
                  />
                )
              ) : null}

              <section
                aria-label="Selected scope"
                className="mt-5 rounded-xl border border-slate-300 bg-slate-50 p-4"
              >
                <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                  Selected scope
                </p>
                <p className="mt-1 font-semibold">{selectedScope}</p>
                {scopeKind === "lesson_range" && !rangeValid ? (
                  <p className="mt-2 text-sm text-red-800">
                    Choose a complete range in which every lesson is available.
                  </p>
                ) : scopeKind === "selected_lessons" && !selectedLessonsValid ? (
                  <p className="mt-2 text-sm text-red-800">
                    Choose at least one available lesson.
                  </p>
                ) : null}
              </section>

              <div className="mt-5 flex flex-wrap justify-between gap-3">
                <button className={secondaryButton} onClick={() => setStep(1)} type="button">
                  Back to target
                </button>
                <button
                  className={primaryButton}
                  disabled={
                    !scopeValid ||
                    ((scopeKind === "lesson_range" || scopeKind === "selected_lessons") &&
                      !lessons.length)
                  }
                  onClick={() => setStep(3)}
                  type="button"
                >
                  Continue to paper settings
                </button>
              </div>
            </Panel>
          ) : null}

          {step >= 3 ? (
            <Panel
              description="Use simple settings your learners will understand. The service applies the detailed blueprint and source rules."
              title="3. Set up the paper"
            >
              <label className={fieldClass}>
                Paper name
                <input
                  className={inputClass}
                  maxLength={512}
                  onChange={(event) => {
                    setPaperName(event.target.value);
                    setFormError("");
                    submission.current = null;
                  }}
                  value={paperName}
                />
              </label>
              <fieldset className="mt-4">
                <legend className="text-sm font-semibold text-slate-800">Question counts</legend>
                <div className="mt-2 grid gap-4 sm:grid-cols-3">
                  {(
                    [
                      ["MCQ questions", mcqCount, setMcqCount],
                      ["Written questions", writtenCount, setWrittenCount],
                      ["Structured questions", structuredCount, setStructuredCount],
                    ] as const
                  ).map(([label, value, update]) => (
                    <label className={fieldClass} key={label}>
                      {label}
                      <input
                        className={inputClass}
                        max={MAX_QUESTIONS}
                        min={0}
                        onChange={(event) => {
                          update(event.currentTarget.valueAsNumber);
                          setFormError("");
                          submission.current = null;
                        }}
                        type="number"
                        value={Number.isNaN(value) ? "" : value}
                      />
                    </label>
                  ))}
                </div>
                <p className="mt-2 text-sm text-slate-600">Total questions: {totalQuestions}</p>
              </fieldset>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <label className={fieldClass}>
                  Duration in minutes
                  <input
                    className={inputClass}
                    max={MAX_DURATION_MINUTES}
                    min={1}
                    onChange={(event) => {
                      setDurationMinutes(event.currentTarget.valueAsNumber);
                      setFormError("");
                      submission.current = null;
                    }}
                    type="number"
                    value={Number.isNaN(durationMinutes) ? "" : durationMinutes}
                  />
                </label>
                <label className={fieldClass}>
                  Difficulty
                  <select
                    className={inputClass}
                    onChange={(event) => {
                      setDifficulty(event.target.value as Difficulty);
                      setFormError("");
                      submission.current = null;
                    }}
                    value={difficulty}
                  >
                    <option value="balanced">Balanced</option>
                    <option value="easier">Easier</option>
                    <option value="challenging">Challenging</option>
                  </select>
                </label>
              </div>
              <label className={`${fieldClass} mt-4`}>
                Teacher instruction (optional)
                <textarea
                  className={`${inputClass} min-h-24`}
                  maxLength={2_048}
                  onChange={(event) => {
                    setTeacherInstruction(event.target.value);
                    setFormError("");
                    submission.current = null;
                  }}
                  value={teacherInstruction}
                />
              </label>

              {role !== "admin" ? (
                <p className="mt-4 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950">
                  You can prepare these choices, but an administrator must start generation.
                </p>
              ) : null}
              {formError ? (
                <p className="mt-4 rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-950" role="alert">
                  {formError}
                </p>
              ) : null}
              {requestError && step === 3 ? (
                <div className="mt-4">
                  <ErrorPanel
                    error={requestError}
                    action={
                      requestError.retryable ? (
                        <button
                          className={secondaryButton}
                          disabled={Boolean(busy)}
                          onClick={() => {
                            const stored = submission.current;
                            if (stored) void createPaper(stored.body);
                          }}
                          type="button"
                        >
                          Try again safely
                        </button>
                      ) : undefined
                    }
                  />
                </div>
              ) : null}

              <div className="mt-5 flex flex-wrap justify-between gap-3">
                <button className={secondaryButton} onClick={() => setStep(2)} type="button">
                  Back to scope
                </button>
                <button
                  className={primaryButton}
                  disabled={!settingsValid || role !== "admin" || Boolean(busy)}
                  type="submit"
                >
                  {busy === "create" ? "Starting paper…" : "Generate paper"}
                </button>
              </div>
            </Panel>
          ) : null}
        </form>
      ) : null}

      {step === 4 && job ? (
        <section
          aria-label="Paper progress"
          className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6"
        >
          <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 pb-4">
            <div>
              <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                Paper progress
              </p>
              <h2 className="mt-1 text-2xl font-semibold">{job.title}</h2>
              <p className="mt-2 text-sm text-slate-600">
                Grade {job.grade} · {job.subject} · {job.scope_summary}
              </p>
            </div>
            <Badge
              className={
                job.status === "ready_for_review"
                  ? "border-emerald-300 bg-emerald-50 text-emerald-900"
                  : job.status === "failed"
                    ? "border-red-300 bg-red-50 text-red-900"
                    : "border-blue-300 bg-blue-50 text-blue-900"
              }
            >
              {job.status === "ready_for_review"
                ? "Ready for review"
                : job.status === "failed"
                  ? "Needs action"
                  : "In progress"}
            </Badge>
          </header>

          <ol className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {(
              [
                ["preparing", "Preparing paper"],
                ["generating", "Generating questions"],
                ["checking_answers", "Checking answers"],
                ["ready_for_review", "Ready for review"],
              ] as const
            ).map(([stage, label]) => {
              const reached = stageIsReached(job, stage);
              return (
                <li
                  className={`rounded-xl border p-4 ${
                    reached
                      ? "border-emerald-300 bg-emerald-50 text-emerald-950"
                      : "border-slate-200 bg-slate-50 text-slate-600"
                  }`}
                  key={stage}
                >
                  <span className="block text-xs font-semibold uppercase">
                    {reached ? "Reached" : "Waiting"}
                  </span>
                  <span className="mt-1 block font-semibold">{label}</span>
                </li>
              );
            })}
          </ol>

          {busy === "poll" ? (
            <p className="mt-4 text-sm text-slate-600" aria-live="polite">
              Checking paper progress…
            </p>
          ) : null}

          {job.status === "failed" ? (
            <section className="mt-5 rounded-xl border border-red-300 bg-red-50 p-4 text-red-950">
              <h3 className="font-semibold">
                {job.counts.generated > 0
                  ? `${job.counts.generated} of ${job.counts.requested} questions were prepared`
                  : "The paper could not be prepared"}
              </h3>
              <p className="mt-1 text-sm leading-6">
                {job.failure?.message ??
                  "One or more questions failed safely. Nothing was published automatically."}
              </p>
              {job.counts.failed > 0 && role === "admin" ? (
                <button
                  className={`${secondaryButton} mt-3 border-red-300`}
                  disabled={Boolean(busy)}
                  onClick={() => void retryFailedQuestions()}
                  type="button"
                >
                  {busy === "retry" ? "Retrying…" : "Retry failed questions"}
                </button>
              ) : null}
            </section>
          ) : null}

          {requestError ? (
            <div className="mt-5">
              <ErrorPanel
                error={requestError}
                action={
                  requestError.retryable ? (
                    job.status === "failed" && job.counts.failed > 0 ? undefined : (
                      <button
                        className={secondaryButton}
                        disabled={Boolean(busy)}
                        onClick={() => void checkProgress()}
                        type="button"
                      >
                        Check progress again
                      </button>
                    )
                  ) : undefined
                }
              />
            </div>
          ) : pollingStopped && !TERMINAL_JOB_STATUSES.has(job.status) ? (
            <section className="mt-5 rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-950">
              <h3 className="font-semibold">The paper is still being prepared</h3>
              <p className="mt-1 text-sm leading-6">
                Automatic checks paused after a bounded wait. This does not start another paper.
              </p>
              <button className={`${secondaryButton} mt-3`} onClick={() => void checkProgress()} type="button">
                Check progress again
              </button>
            </section>
          ) : null}

          <div className="mt-5 flex flex-wrap gap-3">
            {job.review_url ? (
              <Link className={primaryButton} href={job.review_url}>
                Review this paper
              </Link>
            ) : null}
            <button
              className={secondaryButton}
              onClick={() => {
                pollRequest.current += 1;
                setStep(1);
                setJob(null);
                setRequestError(null);
                setBusy("");
                submission.current = null;
                failedRetry.current = null;
              }}
              type="button"
            >
              Start another paper
            </button>
          </div>
        </section>
      ) : null}
    </div>
  );
}
