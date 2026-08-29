"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";

type Role = "admin" | "reviewer";
type Exam = components["schemas"]["ExamConfigurationResponse"];
type Medium = components["schemas"]["MediumResponse"];
type Subject = components["schemas"]["SubjectResponse"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type Paper = components["schemas"]["PaperSummaryResponse"];
type ApiOutcome = { error?: unknown; response: Response };
type JsonObject = Record<string, unknown>;
type UiError = { code: string; message: string; retryable: boolean; title: string };
type PublishedRecord = {
  curriculum: Curriculum;
  exam?: Exam;
  medium?: Medium;
  paper: Paper;
  subject?: Subject;
};

const LIST_LIMIT = 100;
const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none transition hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2";
const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-500 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2";

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function detailCode(error: unknown): string {
  const detail = asObject(asObject(error)?.detail);
  return typeof detail?.code === "string" ? detail.code : "request_failed";
}

function safeText(value: unknown, fallback = "Not recorded"): string {
  if (value === null || value === undefined || value === "") return fallback;
  const text =
    typeof value === "string" || typeof value === "number" || typeof value === "boolean"
      ? String(value)
      : fallback;
  const cleaned = text.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "�");
  return cleaned.length > 4_096 ? `${cleaned.slice(0, 4_096)}…` : cleaned;
}

function firstFailure(outcomes: readonly ApiOutcome[]): ApiOutcome | undefined {
  return outcomes.find((outcome) => outcome.error !== undefined);
}

function libraryError(error: unknown, response: Response): UiError {
  const code = detailCode(error);
  if (response.status === 401) {
    return {
      code: "authentication_required",
      message: "Your session has expired. Sign in again to view published papers.",
      retryable: false,
      title: "Sign in again",
    };
  }
  if (response.status === 403) {
    return {
      code: "permission_denied",
      message: "This account cannot view the published paper library.",
      retryable: false,
      title: "Published paper permission required",
    };
  }
  return {
    code,
    message: "Published papers could not be loaded. Try again without changing any paper state.",
    retryable: true,
    title: "Published Papers unavailable",
  };
}

function networkError(): UiError {
  return {
    code: "network_error",
    message: "The published paper service could not be reached. Check the connection and try again.",
    retryable: true,
    title: "Connection unavailable",
  };
}

function ErrorPanel({ action, error }: { action?: ReactNode; error: UiError }) {
  return (
    <section className="rounded-xl border border-red-300 bg-red-50 p-4 text-red-950" role="alert">
      <h2 className="font-semibold">{error.title}</h2>
      <p className="mt-1 text-sm leading-6">{error.message}</p>
      {action ? <div className="mt-3">{action}</div> : null}
    </section>
  );
}

export function PublishedPapersLibrary({ role }: { role: Role }) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [records, setRecords] = useState<PublishedRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<UiError | null>(null);
  const [activeCurricula, setActiveCurricula] = useState<Curriculum[]>([]);
  const [selectedCurriculumId, setSelectedCurriculumId] = useState("");
  const [paperOffset, setPaperOffset] = useState(0);
  const [hasNextPage, setHasNextPage] = useState(false);
  const requestId = useRef(0);

  const load = useCallback(async () => {
    const currentRequest = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const workspace = await Promise.all([
        api.GET("/api/v1/admin/exam-configurations"),
        api.GET("/api/v1/admin/media"),
        api.GET("/api/v1/admin/subjects"),
        api.GET("/api/v1/admin/curriculum-versions"),
      ]);
      if (currentRequest !== requestId.current) return;
      const workspaceFailure = firstFailure(workspace);
      if (workspaceFailure?.error !== undefined) {
        setError(libraryError(workspaceFailure.error, workspaceFailure.response));
        return;
      }
      const exams = (workspace[0].data ?? []) as Exam[];
      const media = (workspace[1].data ?? []) as Medium[];
      const subjects = (workspace[2].data ?? []) as Subject[];
      const curricula = (workspace[3].data ?? []) as Curriculum[];
      const examsById = new Map(exams.map((item) => [item.id, item]));
      const mediaById = new Map(media.map((item) => [item.id, item]));
      const subjectsById = new Map(subjects.map((item) => [item.id, item]));
      const active = curricula.filter((curriculum) => {
        const exam = examsById.get(curriculum.exam_configuration_id);
        const medium = mediaById.get(curriculum.medium_id);
        const subject = subjectsById.get(curriculum.subject_id);
        return curriculum.active && exam?.active && medium?.active && subject?.active;
      });
      setActiveCurricula(active);
      const search = new URLSearchParams(globalThis.location?.search ?? "");
      const requestedCurriculum = search.get("curriculum");
      const curriculum =
        active.find((item) => item.id === selectedCurriculumId) ??
        active.find((item) => item.id === requestedCurriculum) ??
        active[0];
      if (!curriculum) {
        setRecords([]);
        setHasNextPage(false);
        return;
      }
      if (selectedCurriculumId !== curriculum.id) setSelectedCurriculumId(curriculum.id);
      const paperOutcome = await api.GET(
        "/api/v1/admin/curricula/{curriculum_version_id}/papers",
        {
          params: {
            path: { curriculum_version_id: curriculum.id },
            query: { limit: LIST_LIMIT, offset: paperOffset },
          },
        },
      );
      if (currentRequest !== requestId.current) return;
      if (paperOutcome.error !== undefined) {
        setError(libraryError(paperOutcome.error, paperOutcome.response));
        return;
      }
      const requestedPaper = search.get("paper");
      const papers = (paperOutcome.data ?? []).filter(
        (paper) => paper.curriculum_version_id === curriculum.id,
      );
      papers.sort((left, right) => {
        if (requestedPaper && left.id === requestedPaper) return -1;
        if (requestedPaper && right.id === requestedPaper) return 1;
        return right.updated_at.localeCompare(left.updated_at);
      });
      setHasNextPage(papers.length === LIST_LIMIT);
      setRecords(
        papers.map((paper) => ({
          curriculum,
          exam: examsById.get(curriculum.exam_configuration_id),
          medium: mediaById.get(curriculum.medium_id),
          paper,
          subject: subjectsById.get(curriculum.subject_id),
        })),
      );
    } catch {
      if (currentRequest === requestId.current) setError(networkError());
    } finally {
      if (currentRequest === requestId.current) setLoading(false);
    }
  }, [api, paperOffset, selectedCurriculumId]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => {
      window.clearTimeout(timeout);
      requestId.current += 1;
    };
  }, [load]);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-5 py-8 sm:px-8 sm:py-10">
      <header className="rounded-3xl bg-slate-900 p-6 text-white shadow-lg sm:p-8">
        <p className="text-xs font-semibold tracking-[0.18em] text-amber-300 uppercase">
          Teacher paper library
        </p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Published Papers</h1>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-200 sm:text-base">
          Find drafts and published papers by readable curriculum details, title, state, and version.
          Advanced paper assembly and lifecycle diagnostics remain in the specialist Paper Studio.
        </p>
        <div className="mt-5">
          <Link className="rounded-md text-sm font-semibold text-amber-300 underline outline-none focus-visible:ring-2 focus-visible:ring-amber-300" href="/admin/papers" prefetch={false}>
            Open Advanced Paper Studio
          </Link>
        </div>
      </header>

      {loading ? (
        <section className="rounded-2xl border border-slate-300 bg-white p-6" aria-live="polite">
          Loading published papers…
        </section>
      ) : error ? (
        <ErrorPanel
          error={error}
          action={
            error.retryable ? (
              <button className={secondaryButton} onClick={() => void load()} type="button">
                Reload Published Papers
              </button>
            ) : undefined
          }
        />
      ) : (
        <section
          aria-label="Published paper library"
          className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6"
        >
          <header className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-200 pb-4">
            <div>
              <h2 className="text-xl font-semibold">Published paper library</h2>
              <p className="mt-1 text-sm text-slate-600">
                {records.length} publication-stage {records.length === 1 ? "paper" : "papers"} on this page
              </p>
            </div>
            <Link className={secondaryButton} href="/admin/review-approve" prefetch={false}>
              Open review queue
            </Link>
          </header>

          {activeCurricula.length ? (
            <label className="mt-4 grid max-w-xl gap-1.5 text-sm font-semibold text-slate-800">
              Curriculum
              <select
                className="min-h-11 rounded-lg border border-slate-300 bg-white px-3 py-2 text-slate-950 outline-none focus:border-amber-600 focus:ring-2 focus:ring-amber-200"
                onChange={(event) => {
                  setPaperOffset(0);
                  setSelectedCurriculumId(event.target.value);
                }}
                value={selectedCurriculumId || activeCurricula[0]?.id}
              >
                {activeCurricula.map((curriculum) => (
                  <option key={curriculum.id} value={curriculum.id}>
                    {safeText(curriculum.title)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

          {records.length ? (
            <div className="mt-5 grid gap-4 lg:grid-cols-2">
              {records.map(({ curriculum, exam, medium, paper, subject }) => (
                <article
                  aria-label={safeText(paper.title)}
                  className="rounded-2xl border border-slate-300 bg-[#fbfbf8] p-5"
                  key={`${curriculum.id}-${paper.id}`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      {exam && subject && medium ? (
                        <p className="text-sm font-semibold text-slate-600">
                          Grade {exam.grade} · {safeText(subject.name)} · {safeText(medium.name)}
                        </p>
                      ) : null}
                      <h3 className="mt-2 break-words text-xl font-semibold">{safeText(paper.title)}</h3>
                      <p className="mt-2 text-sm text-slate-600">{safeText(curriculum.title)}</p>
                    </div>
                    <Badge
                      className={
                        paper.state === "published"
                          ? "border-emerald-300 bg-emerald-50 text-emerald-950"
                          : paper.state === "draft"
                            ? "border-amber-300 bg-amber-50 text-amber-950"
                            : "border-slate-300 bg-slate-100 text-slate-800"
                      }
                    >
                      {paper.state === "published"
                        ? "Published"
                        : paper.state === "draft"
                          ? "Draft"
                          : "Archived"}
                    </Badge>
                  </div>

                  <dl className="mt-4 grid gap-3 sm:grid-cols-2">
                    <div className="rounded-lg border border-slate-200 bg-white p-3">
                      <dt className="text-xs font-semibold text-slate-500">State</dt>
                      <dd className="mt-1 text-sm font-semibold">
                        {paper.state === "published"
                          ? "Published"
                          : paper.state === "draft"
                            ? "Draft"
                            : "Archived"}
                      </dd>
                    </div>
                    <div className="rounded-lg border border-slate-200 bg-white p-3">
                      <dt className="text-xs font-semibold text-slate-500">Version</dt>
                      <dd className="mt-1 text-sm font-semibold">Version {paper.current_version}</dd>
                    </div>
                  </dl>

                  <details className="mt-4 rounded-xl border border-slate-200 bg-white p-3">
                    <summary className="cursor-pointer text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-amber-500">
                      Technical details
                    </summary>
                    <dl className="mt-3 grid gap-3 text-xs sm:grid-cols-2">
                      {[
                        ["Paper ID", paper.id],
                        ["Curriculum ID", curriculum.id],
                        ["Paper blueprint ID", paper.paper_blueprint_id],
                        ["Blueprint code", paper.blueprint_id],
                        ["Blueprint version", paper.blueprint_version],
                        ["Publication hash", paper.latest_publication_hash ?? "Not recorded"],
                      ].map(([label, value]) => (
                        <div className="min-w-0 rounded-lg border border-slate-200 bg-slate-50 p-3" key={label}>
                          <dt className="font-semibold text-slate-500">{label}</dt>
                          <dd className="mt-1 break-all font-mono">{safeText(value)}</dd>
                        </div>
                      ))}
                    </dl>
                  </details>
                  <Link
                    className={`${secondaryButton} mt-4`}
                    href={`/admin/papers?curriculum=${curriculum.id}&paper=${paper.id}`}
                    prefetch={false}
                  >
                    Open paper
                  </Link>
                </article>
              ))}
            </div>
          ) : (
            <div className="mt-5 rounded-2xl border border-dashed border-slate-400 p-6 text-center">
              <h3 className="text-lg font-semibold">No published papers yet</h3>
              <p className="mx-auto mt-2 max-w-2xl text-sm leading-6 text-slate-600">
                Finish question review, create a draft, and publish it through the controlled paper lifecycle. Nothing is published automatically.
              </p>
              <Link className={`${primaryButton} mt-4`} href="/admin/review-approve" prefetch={false}>
                Review papers
              </Link>
            </div>
          )}

          {(records.length > 0 || paperOffset > 0) && activeCurricula.length ? (
            <nav
              aria-label="Published paper pages"
              className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4"
            >
              <button
                className={secondaryButton}
                disabled={paperOffset === 0}
                onClick={() => setPaperOffset((current) => Math.max(0, current - LIST_LIMIT))}
                type="button"
              >
                Previous page
              </button>
              <p className="text-sm font-semibold text-slate-700">
                Page {Math.floor(paperOffset / LIST_LIMIT) + 1}
              </p>
              <button
                className={secondaryButton}
                disabled={!hasNextPage}
                onClick={() => setPaperOffset((current) => current + LIST_LIMIT)}
                type="button"
              >
                Next page
              </button>
            </nav>
          ) : null}
        </section>
      )}

      <footer className="rounded-2xl border border-slate-300 bg-white p-5 text-sm text-slate-600">
        Signed in as <span className="font-semibold capitalize text-slate-900">{role}</span>. Only authoritative fields returned by the paper and curriculum APIs are shown; unavailable scope details are not guessed.
      </footer>
    </div>
  );
}
