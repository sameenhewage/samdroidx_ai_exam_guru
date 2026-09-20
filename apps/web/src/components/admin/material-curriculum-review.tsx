"use client";

import { createApiClient } from "@exam-guru/api-client";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type FormEvent,
} from "react";
import { Button } from "react-aria-components";
import { flushSync } from "react-dom";

import {
  reviewLanguageKey,
  savedReviewLanguage,
  subscribeReviewLanguage,
  type ReviewLanguage,
} from "@/lib/review-language";
import { cn } from "@/lib/utils";

import type { AdminRole } from "./admin-header";
import {
  curriculumInputClass,
  curriculumPrimaryButton,
  MaterialCurriculumMappingForm,
} from "./material-curriculum-mapping-form";
import { curriculumReviewCopy } from "./material-curriculum-review-copy";
import {
  hasIntent,
  mappingFor,
  materialLanguage,
  problemFor,
  readiness,
  reviewedChoices,
  sectionLimit,
  shouldPoll,
  unitLanguage,
  validList,
  validMapping,
  validReason,
  validWorkspace,
  workspaceVersion,
  type Choices,
  type IndexingRetry,
  type MappingRequest,
  type Material,
  type MaterialUnitList,
  type MaterialUnitSummary,
  type MaterialUnitWorkspace,
  type Problem,
} from "./material-curriculum-review-state";
import { installMaterialReviewNavigationGuard } from "./material-review-navigation";
import { OriginalPageViewer, viewerButtonClass } from "./original-page-viewer";
import { SourceUnderstandingContent } from "./source-understanding-content";

type Props = { documentId: string; role: AdminRole };
type Selection = { summary: MaterialUnitSummary; curriculumId: string };
type Draft = {
  mode: "mapping" | "retry";
  base: MaterialUnitWorkspace;
  choices: Choices;
  mapping: MappingRequest;
  retry: IndexingRetry;
};

export function MaterialCurriculumReview(props: Props) {
  return <ReviewDocument key={props.documentId} {...props} />;
}

function ReviewDocument({ documentId, role }: Props) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const savedLanguage = useSyncExternalStore(
    subscribeReviewLanguage,
    savedReviewLanguage,
    () => null,
  );
  const [chosenLanguage, setChosenLanguage] = useState<ReviewLanguage | null>(
    null,
  );
  const [selected, setSelected] = useState<Selection | null>(null);
  const [selectedSnapshot, setSelectedSnapshot] =
    useState<MaterialUnitWorkspace | null>(null);
  const [selectedFresh, setSelectedFresh] = useState(false);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{
    offset: number;
    revision: number;
    data?: MaterialUnitList;
    problem?: Problem;
  } | null>(null);
  const [material, setMaterial] = useState<Material | null>(null);
  const dirty = useRef(false);
  const language =
    chosenLanguage ??
    savedLanguage ??
    (selectedSnapshot
      ? unitLanguage(selectedSnapshot.workspace, materialLanguage(material))
      : materialLanguage(material));
  const copy = curriculumReviewCopy(language);
  const current =
    state?.offset === offset && state.revision === revision ? state : null;
  const list = current?.data;
  const id = useId();

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    async function load() {
      try {
        const [sections, details] = await Promise.all([
          api.GET("/api/v1/admin/materials/{document_id}/knowledge-units", {
            params: {
              path: { document_id: documentId },
              query: { limit: sectionLimit, offset },
            },
            cache: "no-store",
            signal: controller.signal,
          }),
          api.GET("/api/v1/admin/materials", {
            params: { query: { document_id: documentId, limit: 1 } },
            cache: "no-store",
            signal: controller.signal,
          }),
        ]);
        if (!active) return;
        const detail = details.data?.find((item) => item.id === documentId);
        if (
          !sections.response.ok ||
          !validList(sections.data, documentId, offset) ||
          !details.response.ok ||
          !detail
        ) {
          setState({
            offset,
            revision,
            problem: problemFor(
              !sections.response.ok
                ? sections.response.status
                : !details.response.ok
                  ? details.response.status
                  : 404,
            ),
          });
          return;
        }
        setMaterial(detail);
        setState({ offset, revision, data: sections.data });
      } catch {
        if (active) setState({ offset, revision, problem: "failed" });
      }
    }
    void load();
    return () => {
      active = false;
      controller.abort();
    };
  }, [api, documentId, offset, revision]);

  useEffect(() => {
    const discard = () => {
      dirty.current = false;
      setSelected(null);
      setSelectedSnapshot(null);
    };
    return installMaterialReviewNavigationGuard({
      isDirty: () => dirty.current,
      confirmLeave: () => {
        if (!window.confirm(copy.leave)) return false;
        discard();
        return true;
      },
      onCommittedLeave: () => flushSync(discard),
    });
  }, [copy.leave]);

  const onDirty = useCallback((value: boolean) => {
    dirty.current = value;
  }, []);
  const onLoaded = useCallback((value: MaterialUnitWorkspace | null) => {
    setSelectedFresh(value !== null);
    if (value) setSelectedSnapshot(value);
  }, []);
  function mayLeave() {
    return !dirty.current || window.confirm(copy.leave);
  }
  function changePage(next: number) {
    if (!mayLeave()) return;
    dirty.current = false;
    setSelected(null);
    setSelectedSnapshot(null);
    setOffset(next);
  }
  function select(summary: MaterialUnitSummary) {
    if (
      selected?.summary.unit_id === summary.unit_id ||
      !list?.curriculum_version_id ||
      !mayLeave()
    )
      return;
    dirty.current = false;
    setSelectedSnapshot(null);
    setSelectedFresh(false);
    setSelected({ summary, curriculumId: list.curriculum_version_id });
  }
  const pages = [
    ...new Set<number>(
      list?.items.map((item: MaterialUnitSummary) => item.page_number) ?? [],
    ),
  ];
  return (
    <div className="flex min-h-0 flex-1 flex-col" lang={language}>
      <header className="shrink-0 space-y-3 border-b border-slate-300 px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Link
            className={viewerButtonClass}
            href={`/admin/materials/${documentId}`}
            prefetch={false}
          >
            {copy.back}
          </Link>
          <div className="flex gap-2" aria-label="Review language / භාෂාව">
            {(["en", "si"] as const).map((value) => (
              <Button
                key={value}
                lang={value}
                className={viewerButtonClass}
                aria-pressed={language === value}
                onPress={() => {
                  setChosenLanguage(value);
                  try {
                    window.localStorage.setItem(reviewLanguageKey, value);
                  } catch {}
                }}
              >
                {value === "si" ? "සිංහල" : "English"}
              </Button>
            ))}
          </div>
        </div>
        <h1 className="text-2xl font-semibold">{copy.title}</h1>
        {material && (
          <p className="break-words text-sm font-semibold text-slate-700">
            {material.title}
            {material.subject ? ` · ${material.subject}` : ""}
            {material.curriculum ? ` · ${material.curriculum}` : ""}
          </p>
        )}
        <p className="max-w-4xl text-sm text-slate-700">{copy.intro}</p>
        {role === "reviewer" && (
          <p className="text-sm font-semibold text-slate-700">
            {copy.readonly}
          </p>
        )}
      </header>
      <div className="grid min-h-0 flex-1 gap-4 p-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <aside
          aria-labelledby={`${id}-sections`}
          className="space-y-3 lg:overflow-auto"
        >
          <h2 id={`${id}-sections`} className="text-lg font-semibold">
            {copy.sections}
          </h2>
          <Button
            className={viewerButtonClass}
            isDisabled={!current}
            onPress={() => setRevision((value) => value + 1)}
          >
            {copy.refreshList}
          </Button>
          {!current && (
            <p role="status" className="text-sm">
              {copy.loading}
            </p>
          )}
          {current?.problem && (
            <p
              role="alert"
              className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-950"
            >
              {copy[current.problem]}
            </p>
          )}
          {list?.items.length === 0 && (
            <div className="space-y-3 text-sm">
              <p className="font-semibold">{copy.empty}</p>
              <p>{copy.emptyHelp}</p>
            </div>
          )}
          {pages.map((page) => (
            <section
              key={page}
              aria-label={`${copy.page} ${page}`}
              className="space-y-2"
            >
              <h3 className="text-sm font-semibold">
                {copy.page} {page}
              </h3>
              <ul className="space-y-2">
                {list!.items
                  .filter(
                    (item: MaterialUnitSummary) => item.page_number === page,
                  )
                  .map((item: MaterialUnitSummary) => {
                    const response =
                      selectedSnapshot?.workspace.unit.id === item.unit_id
                        ? selectedSnapshot
                        : null;
                    const status =
                      selected?.summary.unit_id === item.unit_id &&
                      !selectedFresh
                        ? "inconsistent"
                        : readiness(
                            response?.indexing ?? item.indexing,
                            item.has_projection,
                            list!.source_current &&
                              (response?.workspace.source_current ?? true),
                            response?.workspace.eligible ??
                              (item.review?.state === "reviewed" &&
                                item.review.confirmed_mapping === true),
                          );
                    const label = `${copy.page} ${page} · ${copy.section} ${item.sequence + 1}`;
                    return (
                      <li key={item.unit_id}>
                        <Button
                          aria-label={label}
                          aria-pressed={
                            selected?.summary.unit_id === item.unit_id
                          }
                          className={cn(
                            viewerButtonClass,
                            "w-full flex-col items-start gap-1 text-left",
                            selected?.summary.unit_id === item.unit_id &&
                              "border-slate-950 ring-1 ring-slate-950",
                          )}
                          onPress={() => select(item)}
                        >
                          <span>{label}</span>
                          {(item.source_unit_title ||
                            item.source_lesson_title) && (
                            <span className="text-sm font-normal">
                              {[
                                item.source_unit_title,
                                item.source_lesson_title,
                              ]
                                .filter(Boolean)
                                .join(" · ")}
                            </span>
                          )}
                          <span className="text-xs font-normal">
                            {copy[status]}
                          </span>
                        </Button>
                      </li>
                    );
                  })}
              </ul>
            </section>
          ))}
          {list && list.total > 0 && (
            <nav
              aria-label={copy.sections}
              className="space-y-2 border-t border-slate-300 pt-3"
            >
              <p className="text-sm">
                {Math.min(offset + 1, list.total)}–
                {Math.min(offset + list.items.length, list.total)} /{" "}
                {list.total}
              </p>
              <Button
                className={viewerButtonClass}
                isDisabled={offset === 0}
                onPress={() => changePage(Math.max(0, offset - sectionLimit))}
              >
                {copy.previous}
              </Button>
              <Button
                className={viewerButtonClass}
                isDisabled={offset + sectionLimit >= list.total}
                onPress={() => changePage(offset + sectionLimit)}
              >
                {copy.next}
              </Button>
            </nav>
          )}
        </aside>
        {selected ? (
          <CurriculumSection
            key={selected.summary.unit_id}
            documentId={documentId}
            role={role}
            api={api}
            selection={selected}
            language={language}
            onDirty={onDirty}
            onLoaded={onLoaded}
            sourceScopeCurrent={
              state?.data?.source_current === true &&
              state.data.curriculum_version_id === selected.curriculumId &&
              state.data.items.some(
                (item: MaterialUnitSummary) =>
                  item.unit_id === selected.summary.unit_id,
              )
            }
          />
        ) : (
          <p className="rounded-xl border border-slate-300 bg-white p-6 text-slate-700">
            {copy.selectSection}
          </p>
        )}
      </div>
    </div>
  );
}

function CurriculumSection({
  documentId,
  role,
  api,
  selection,
  language,
  sourceScopeCurrent,
  onDirty,
  onLoaded,
}: Props & {
  api: ReturnType<typeof createApiClient>;
  selection: Selection;
  language: ReviewLanguage;
  sourceScopeCurrent: boolean;
  onDirty: (value: boolean) => void;
  onLoaded: (value: MaterialUnitWorkspace | null) => void;
}) {
  const { summary, curriculumId } = selection;
  const unitId = summary.unit_id;
  const copy = curriculumReviewCopy(language);
  const id = useId();
  const [snapshot, setSnapshot] = useState<MaterialUnitWorkspace | null>(null);
  const [choices, setChoices] = useState<Choices | null>(null);
  const [draft, setDraftState] = useState<Draft | null>(null);
  const draftRef = useRef<Draft | null>(null);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [fresh, setFresh] = useState(false);
  const [latestLoaded, setLatestLoaded] = useState(false);
  const [needsRebase, setNeedsRebase] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [message, setMessage] = useState<"saved" | "retried" | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [imageReady, setImageReady] = useState(false);
  const mutation = useRef<AbortController | null>(null);
  const read = useRef<AbortController | null>(null);
  const awaitingReload = useRef(false);
  const setDraft = useCallback(
    (value: Draft | null) => {
      draftRef.current = value;
      setDraftState(value);
      onDirty(value !== null);
    },
    [onDirty],
  );

  useEffect(() => {
    return () => {
      mutation.current?.abort();
      onDirty(false);
    };
  }, [onDirty]);

  useEffect(() => {
    if (busy || awaitingReload.current) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    read.current = controller;
    async function load(includeChoices: boolean) {
      try {
        const response = await api.GET(
          "/api/v1/admin/materials/{document_id}/knowledge-units/{unit_id}",
          {
            params: { path: { document_id: documentId, unit_id: unitId } },
            cache: "no-store",
            signal: controller.signal,
          },
        );
        if (!active || controller.signal.aborted) return;
        if (
          !response.response.ok ||
          !validWorkspace(response.data, documentId, summary, curriculumId)
        ) {
          fail(problemFor(response.response.status));
          return;
        }
        const incoming: MaterialUnitWorkspace = response.data;
        let available: Choices | null = null;
        if (includeChoices) {
          const params = { path: { curriculum_version_id: curriculumId } };
          const options = {
            params,
            cache: "no-store" as const,
            signal: controller.signal,
          };
          const [units, lessons, nodes] = await Promise.all([
            api.GET(
              "/api/v1/admin/curriculum-versions/{curriculum_version_id}/units",
              options,
            ),
            api.GET(
              "/api/v1/admin/curriculum-versions/{curriculum_version_id}/lessons",
              options,
            ),
            api.GET(
              "/api/v1/admin/curricula/{curriculum_version_id}/taxonomy/nodes",
              options,
            ),
          ]);
          if (!active || controller.signal.aborted) return;
          const failed = [units, lessons, nodes].find(
            (result) => !result.response.ok || !Array.isArray(result.data),
          );
          if (failed) {
            fail(problemFor(failed.response.status));
            return;
          }
          available = reviewedChoices(
            curriculumId,
            units.data!,
            lessons.data!,
            nodes.data!,
          );
          setChoices(available);
        }
        const existing = draftRef.current;
        if (
          existing &&
          (!sourceScopeCurrent ||
            workspaceVersion(existing.base) !== workspaceVersion(incoming) ||
            (available &&
              JSON.stringify(existing.choices) !== JSON.stringify(available)))
        )
          setNeedsRebase(true);
        setSnapshot(incoming);
        onLoaded(incoming);
        setFresh(true);
        setProblem(null);
        if (includeChoices) setLatestLoaded(true);
        if (
          incoming.workspace.source_current === true &&
          shouldPoll(incoming.indexing)
        )
          timer = setTimeout(() => {
            setLoading(true);
            void load(false);
          }, 5000);
      } catch {
        if (active && !controller.signal.aborted) fail("failed");
      } finally {
        if (active && !controller.signal.aborted) setLoading(false);
      }
    }
    function fail(value: Problem) {
      setProblem(value);
      setFresh(false);
      onLoaded(null);
      setLatestLoaded(false);
      if (draftRef.current) setNeedsRebase(true);
    }
    void load(true);
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [
    api,
    documentId,
    unitId,
    curriculumId,
    summary,
    revision,
    busy,
    sourceScopeCurrent,
    onLoaded,
  ]);

  const mapping =
    draft?.mapping ?? (snapshot ? mappingFor(snapshot.workspace) : null);
  const retry = draft?.retry ?? {
    expected_version: snapshot?.indexing.version ?? 0,
    reason: "",
    confirmed_retry: false,
  };
  const shownWorkspace = draft?.base.workspace ?? snapshot?.workspace;
  const shownChoices = needsRebase && draft ? draft.choices : choices;
  const readyToAct =
    fresh &&
    !loading &&
    !busy &&
    !needsRebase &&
    sourceScopeCurrent &&
    snapshot?.workspace.source_current === true;
  const writable = role === "admin";
  const maySave = !!(
    writable &&
    draft?.mode !== "retry" &&
    readyToAct &&
    imageReady &&
    mapping?.confirmed_mapping === true &&
    choices &&
    snapshot &&
    mapping.expected_version === (snapshot.workspace.review?.version ?? 0) &&
    validMapping(mapping, snapshot.workspace, choices)
  );
  const canRetry = !!(
    writable &&
    snapshot &&
    sourceScopeCurrent &&
    summary.has_projection &&
    snapshot.workspace.source_current === true &&
    snapshot.workspace.eligible === true &&
    snapshot.indexing.ready === false &&
    snapshot.indexing.retry_allowed === true &&
    hasIntent(snapshot.indexing)
  );
  const mayRetry =
    canRetry &&
    draft?.mode !== "mapping" &&
    readyToAct &&
    retry.confirmed_retry === true &&
    retry.expected_version === snapshot?.indexing.version &&
    validReason(retry.reason);

  function beginDraft(mode: Draft["mode"]): Draft | null {
    if (draftRef.current)
      return draftRef.current.mode === mode ? draftRef.current : null;
    if (!snapshot || !choices) return null;
    return {
      mode,
      base: snapshot,
      choices,
      mapping: mappingFor(snapshot.workspace),
      retry: {
        expected_version: snapshot.indexing.version ?? 0,
        reason: "",
        confirmed_retry: false,
      },
    };
  }
  function changeMapping(value: MappingRequest) {
    if (!writable || busyRef.current) return;
    const next = beginDraft("mapping");
    if (next) {
      setDraft({ ...next, mapping: value });
      setMessage(null);
    }
  }
  function changeRetry(value: IndexingRetry) {
    if (!writable || busyRef.current) return;
    const next = beginDraft("retry");
    if (next) {
      setDraft({ ...next, retry: value });
      setMessage(null);
    }
  }
  const imageState = useCallback(
    (ready: boolean) => {
      setImageReady(ready);
      const current = draftRef.current;
      if (!ready && current?.mapping.confirmed_mapping)
        setDraft({
          ...current,
          mapping: { ...current.mapping, confirmed_mapping: false },
        });
    },
    [setDraft],
  );

  async function submit(event: FormEvent, action: "mapping" | "retry") {
    event.preventDefault();
    if (
      busyRef.current ||
      (action === "mapping" ? !maySave : !mayRetry) ||
      !mapping
    )
      return;
    busyRef.current = true;
    setBusy(true);
    setMessage(null);
    setFresh(false);
    onLoaded(null);
    setLatestLoaded(false);
    read.current?.abort();
    const controller = new AbortController();
    mutation.current = controller;
    try {
      const params = { path: { document_id: documentId, unit_id: unitId } };
      const response =
        action === "mapping"
          ? await api.POST(
              "/api/v1/admin/materials/{document_id}/knowledge-units/{unit_id}/curriculum-review",
              { params, body: mapping, signal: controller.signal },
            )
          : await api.POST(
              "/api/v1/admin/materials/{document_id}/knowledge-units/{unit_id}/indexing-retry",
              { params, body: retry, signal: controller.signal },
            );
      if (controller.signal.aborted) return;
      if (
        !response.response.ok ||
        !validWorkspace(response.data, documentId, summary, curriculumId)
      ) {
        awaitingReload.current = true;
        setProblem(problemFor(response.response.status));
        setNeedsRebase(true);
        return;
      }
      setSnapshot(response.data);
      onLoaded(response.data);
      setDraft(null);
      setNeedsRebase(false);
      setProblem(null);
      setFresh(true);
      setMessage(action === "mapping" ? "saved" : "retried");
    } catch {
      if (!controller.signal.aborted) {
        awaitingReload.current = true;
        setProblem("failed");
        setNeedsRebase(true);
      }
    } finally {
      if (!controller.signal.aborted) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  }

  function refresh() {
    if (busyRef.current) return;
    awaitingReload.current = false;
    setFresh(false);
    onLoaded(null);
    setLatestLoaded(false);
    setLoading(true);
    setRevision((value) => value + 1);
  }
  function rebase() {
    if (
      !draft ||
      !snapshot ||
      !choices ||
      !latestLoaded ||
      !fresh ||
      loading ||
      !sourceScopeCurrent ||
      !snapshot.workspace.source_current
    )
      return;
    setDraft({
      ...draft,
      base: snapshot,
      choices,
      mapping: {
        ...draft.mapping,
        expected_version: snapshot.workspace.review?.version ?? 0,
        confirmed_mapping: false,
      },
      retry: {
        ...draft.retry,
        expected_version: snapshot.indexing.version ?? 0,
        confirmed_retry: false,
      },
    });
    setNeedsRebase(false);
    setProblem(null);
  }
  const status = snapshot
    ? !fresh
      ? "inconsistent"
      : readiness(
          snapshot.indexing,
          summary.has_projection,
          sourceScopeCurrent && snapshot.workspace.source_current,
          snapshot.workspace.eligible,
        )
    : null;
  return (
    <section
      className="flex min-h-0 flex-col gap-3 lg:overflow-y-auto xl:overflow-visible"
      aria-label={`${copy.page} ${summary.page_number} · ${copy.section} ${summary.sequence + 1}`}
    >
      <div className="flex shrink-0 flex-wrap items-center gap-3">
        <h2 className="mr-auto text-xl font-semibold">
          {copy.page} {summary.page_number} · {copy.section}{" "}
          {summary.sequence + 1}
        </h2>
        <Button
          className={viewerButtonClass}
          isDisabled={loading || busy}
          onPress={refresh}
        >
          {copy.refresh}
        </Button>
        {draft && (
          <Button
            className={viewerButtonClass}
            isDisabled={busy}
            onPress={() => {
              if (window.confirm(copy.leave)) {
                setDraft(null);
                setNeedsRebase(false);
              }
            }}
          >
            {copy.discard}
          </Button>
        )}
      </div>
      {draft && <p className="text-sm text-slate-700">{copy.oneDraft}</p>}
      {loading && (
        <p role="status" className="text-sm">
          {copy.loading}
        </p>
      )}
      {problem && (
        <p
          role="alert"
          className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-950"
        >
          {copy[problem]}
        </p>
      )}
      {message && (
        <p
          role="status"
          className="rounded-lg border border-emerald-400 bg-emerald-50 p-3 text-sm text-emerald-950"
        >
          {copy[message]}
        </p>
      )}
      {snapshot &&
        (!sourceScopeCurrent || !snapshot.workspace.source_current) && (
          <p className="rounded-lg border border-amber-400 bg-amber-50 p-3 text-sm text-amber-950">
            {copy.sourceChanged}
          </p>
        )}
      {needsRebase && (
        <div className="space-y-2 rounded-lg border border-amber-400 bg-amber-50 p-3 text-sm text-amber-950">
          <p>{copy.rebaseHelp}</p>
          <Button
            className={viewerButtonClass}
            isDisabled={
              !latestLoaded ||
              !fresh ||
              loading ||
              busy ||
              !sourceScopeCurrent ||
              !snapshot?.workspace.source_current
            }
            onPress={rebase}
          >
            {copy.rebase}
          </Button>
        </div>
      )}
      {shownWorkspace && (
        <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-2">
          <OriginalPageViewer
            documentId={documentId}
            pageNumber={shownWorkspace.unit.source.page_number}
            language={language}
            onReady={imageState}
            initialFit
            className="h-[65dvh] xl:h-full"
          />
          <div className="min-h-0 space-y-4 xl:overflow-auto xl:pr-2">
            <SourceUnderstandingContent
              unit={shownWorkspace.unit}
              language={language}
            />
            <p className="text-sm text-slate-700">{copy.separate}</p>
            {mapping && shownChoices && (
              <MaterialCurriculumMappingForm
                workspace={shownWorkspace}
                summary={summary}
                choices={shownChoices}
                mapping={mapping}
                writable={writable}
                busy={busy}
                blocked={draft?.mode === "retry"}
                maySave={maySave}
                imageReady={imageReady}
                copy={copy}
                onChange={changeMapping}
                onSubmit={(event) => void submit(event, "mapping")}
              />
            )}
            {snapshot && status && (
              <section
                aria-labelledby={`${id}-ai`}
                className="space-y-3 rounded-xl border border-slate-300 bg-white p-4 text-sm"
              >
                <h2 id={`${id}-ai`} className="text-xl font-semibold">
                  {copy.ai}
                </h2>
                <p role="status" className="font-semibold">
                  {copy[status]}
                </p>
                {status === "waiting_configuration" && <p>{copy.setupHelp}</p>}
                {["needs_attention", "configuration_changed"].includes(
                  status,
                ) && <p>{copy.attentionHelp}</p>}
                {status === "not_searchable" && <p>{copy.noSearchHelp}</p>}
                {(canRetry || draft?.mode === "retry") && (
                  <form
                    aria-label={copy.retry}
                    className="space-y-3"
                    onSubmit={(event) => void submit(event, "retry")}
                  >
                    <label
                      className="block font-semibold"
                      htmlFor={`${id}-retry-reason`}
                    >
                      {copy.retryReason}
                    </label>
                    <textarea
                      id={`${id}-retry-reason`}
                      className={curriculumInputClass}
                      rows={2}
                      maxLength={2000}
                      required
                      disabled={busy || !canRetry || draft?.mode === "mapping"}
                      value={retry.reason}
                      onChange={(event) =>
                        changeRetry({
                          ...retry,
                          reason: event.target.value,
                          confirmed_retry: false,
                        })
                      }
                    />
                    <label className="flex items-start gap-3">
                      <input
                        className="mt-1 size-4 accent-slate-950"
                        type="checkbox"
                        checked={retry.confirmed_retry}
                        disabled={
                          busy || !canRetry || draft?.mode === "mapping"
                        }
                        onChange={(event) =>
                          changeRetry({
                            ...retry,
                            confirmed_retry: event.target.checked,
                          })
                        }
                      />
                      {copy.retryConsent}
                    </label>
                    {canRetry && (
                      <Button
                        type="submit"
                        className={curriculumPrimaryButton}
                        isDisabled={!mayRetry}
                      >
                        {busy ? copy.saving : copy.retry}
                      </Button>
                    )}
                  </form>
                )}
              </section>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
