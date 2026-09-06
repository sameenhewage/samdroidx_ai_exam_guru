"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { Button } from "react-aria-components";

import type { AdminRole } from "./admin-header";

type Benchmark = components["schemas"]["SourceBenchmarkResponse"];

const pageLimit = 40;
const buttonClass =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-950 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

function errorMessage(status: number): string {
  if (status === 401)
    return "Your session has expired. Sign in again before retrying.";
  if (status === 403)
    return "Your account does not have permission to view these source review sets.";
  if (status === 404)
    return "This review set could not be found. Select another set or try loading again.";
  return "The source review set could not be loaded. Check the connection and try again.";
}

function pageState(state: string): string {
  if (state === "verified") return "Page confirmed";
  if (state === "excluded") return "Not used";
  if (state === "processing") return "Reading in progress";
  if (state === "failed") return "Reading failed";
  if (state === "pending") return "Awaiting reading";
  return "Needs comparison";
}

export function SourceBenchmarkReview({
  role,
  initialBenchmarkId = "",
}: {
  role: AdminRole;
  initialBenchmarkId?: string;
}) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [sets, setSets] = useState<Benchmark[]>([]);
  const [selectedId, setSelectedId] = useState(initialBenchmarkId);
  const [benchmark, setBenchmark] = useState<Benchmark | null>(null);
  const [catalogOffset, setCatalogOffset] = useState(0);
  const [pageOffset, setPageOffset] = useState(0);
  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [listError, setListError] = useState<number | null>(null);
  const [detailError, setDetailError] = useState<number | null>(null);
  const listRequest = useRef(0);
  const detailRequest = useRef(0);
  const listController = useRef<AbortController | null>(null);
  const detailController = useRef<AbortController | null>(null);
  const selectorId = useId();

  const loadSets = useCallback(
    async (offset: number, selectFirst = false) => {
      const requestId = ++listRequest.current;
      listController.current?.abort();
      const controller = new AbortController();
      listController.current = controller;
      setListLoading(true);
      setListError(null);
      try {
        const result = await api.GET("/api/v1/admin/source-benchmarks", {
          params: { query: { limit: pageLimit, offset } },
          cache: "no-store",
          signal: controller.signal,
        });
        if (requestId !== listRequest.current) return;
        if (!result.response.ok || result.error || !result.data) {
          setListError(result.response.status);
          return;
        }
        const nextSets = result.data;
        setSets(nextSets);
        setCatalogOffset(offset);
        setSelectedId((current) =>
          selectFirst
            ? (nextSets[0]?.id ?? "")
            : current || nextSets[0]?.id || "",
        );
      } catch {
        if (requestId === listRequest.current && !controller.signal.aborted)
          setListError(0);
      } finally {
        if (requestId === listRequest.current) setListLoading(false);
      }
    },
    [api],
  );

  const loadBenchmark = useCallback(
    async (id: string) => {
      const requestId = ++detailRequest.current;
      detailController.current?.abort();
      const controller = new AbortController();
      detailController.current = controller;
      setDetailLoading(true);
      setDetailError(null);
      try {
        const result = await api.GET(
          "/api/v1/admin/source-benchmarks/{benchmark_id}",
          {
            params: { path: { benchmark_id: id } },
            cache: "no-store",
            signal: controller.signal,
          },
        );
        if (requestId !== detailRequest.current) return;
        if (
          !result.response.ok ||
          result.error ||
          !result.data ||
          result.data.id !== id
        ) {
          setDetailError(result.response.status || 502);
          return;
        }
        const next = result.data;
        setBenchmark(next);
        setPageOffset((current) =>
          Math.min(
            current,
            Math.max(
              0,
              Math.floor((next.pages.length - 1) / pageLimit) * pageLimit,
            ),
          ),
        );
      } catch {
        if (requestId === detailRequest.current && !controller.signal.aborted)
          setDetailError(0);
      } finally {
        if (requestId === detailRequest.current) setDetailLoading(false);
      }
    },
    [api],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => void loadSets(0), 0);
    return () => {
      window.clearTimeout(timer);
      listRequest.current += 1;
      listController.current?.abort();
    };
  }, [loadSets]);

  useEffect(() => {
    if (!selectedId) return;
    const timer = window.setTimeout(() => void loadBenchmark(selectedId), 0);
    return () => {
      window.clearTimeout(timer);
      detailRequest.current += 1;
      detailController.current?.abort();
    };
  }, [loadBenchmark, selectedId]);

  const visibleBenchmark =
    benchmark?.id === selectedId && !detailLoading && detailError === null
      ? benchmark
      : null;
  const pages =
    visibleBenchmark?.pages.slice(pageOffset, pageOffset + pageLimit) ?? [];

  return (
    <section
      className="mx-auto max-w-[100rem] space-y-5 p-4 font-sans sm:p-6"
      lang="en"
    >
      <header>
        <Link
          className="rounded text-sm underline focus-visible:ring-2 focus-visible:ring-amber-600"
          href="/admin/materials"
        >
          Back to Materials
        </Link>
        <h1 className="mt-3 text-2xl font-semibold">
          Review selected source pages
        </h1>
        <p className="mt-2 max-w-4xl text-sm leading-7 text-slate-700">
          Compare each selected page with its original. Text that looks readable
          is still only a candidate until a person confirms it. Saving a
          correction does not confirm a page.
        </p>
        {role === "reviewer" && (
          <p className="mt-2 text-sm font-semibold">
            Reviewer access is read-only.
          </p>
        )}
      </header>

      {listLoading && <p role="status">Loading source review sets…</p>}
      {listError !== null && (
        <div
          className="rounded-lg border border-amber-400 bg-amber-50 p-4"
          role="alert"
        >
          <p>{errorMessage(listError)}</p>
          <Button
            className={`${buttonClass} mt-3`}
            isDisabled={listLoading}
            onPress={() => void loadSets(catalogOffset)}
          >
            Try loading again
          </Button>
        </div>
      )}
      {!listLoading && listError === null && !sets.length && !selectedId && (
        <p>
          {catalogOffset > 0
            ? "No more review sets on this page."
            : "No page review sets are available yet."}
        </p>
      )}

      {(sets.length > 0 || selectedId || catalogOffset > 0) && (
        <div className="flex flex-wrap items-end gap-3">
          <label
            className="grid max-w-xl flex-1 gap-1 text-sm font-semibold"
            htmlFor={selectorId}
          >
            Review set
            <select
              className="min-h-11 rounded-lg border border-slate-400 bg-white px-3 py-2 outline-none focus-visible:ring-2 focus-visible:ring-amber-600"
              disabled={listLoading}
              id={selectorId}
              onChange={(event) => {
                if (event.target.value === selectedId) return;
                detailRequest.current += 1;
                detailController.current?.abort();
                setSelectedId(event.target.value);
                setBenchmark(null);
                setPageOffset(0);
              }}
              value={selectedId}
            >
              {selectedId && !sets.some((set) => set.id === selectedId) && (
                <option value={selectedId}>
                  {benchmark?.id === selectedId
                    ? benchmark.name
                    : "Selected review set"}
                </option>
              )}
              {sets.map((set) => (
                <option key={set.id} value={set.id}>
                  {set.name}
                </option>
              ))}
            </select>
          </label>
          <Button
            className={buttonClass}
            isDisabled={!selectedId || detailLoading}
            onPress={() => void loadBenchmark(selectedId)}
          >
            Refresh review counts
          </Button>
          {(catalogOffset > 0 || sets.length === pageLimit) && (
            <nav aria-label="Review sets" className="flex gap-2">
              <Button
                className={buttonClass}
                isDisabled={listLoading || catalogOffset === 0}
                onPress={() =>
                  void loadSets(Math.max(0, catalogOffset - pageLimit), true)
                }
              >
                Previous review sets
              </Button>
              <Button
                className={buttonClass}
                isDisabled={listLoading || sets.length < pageLimit}
                onPress={() => void loadSets(catalogOffset + pageLimit, true)}
              >
                More review sets
              </Button>
            </nav>
          )}
        </div>
      )}

      {detailLoading && <p role="status">Loading selected pages…</p>}
      {detailError !== null && (
        <div
          className="rounded-lg border border-amber-400 bg-amber-50 p-4"
          role="alert"
        >
          <p>{errorMessage(detailError)}</p>
          <Button
            className={`${buttonClass} mt-3`}
            isDisabled={detailLoading}
            onPress={() => void loadBenchmark(selectedId)}
          >
            Try loading again
          </Button>
        </div>
      )}

      {visibleBenchmark && (
        <>
          <section
            aria-label="Review set progress"
            className="rounded-lg border border-slate-300 bg-white p-4"
          >
            <h2 className="font-semibold">{visibleBenchmark.name}</h2>
            <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
              {(
                [
                  ["Selected pages", visibleBenchmark.pages.length],
                  [
                    "Human-confirmed reference pages",
                    visibleBenchmark.adjudicated_pages,
                  ],
                  [
                    "Pages without a confirmed reference",
                    visibleBenchmark.pending_pages,
                  ],
                  [
                    "Currently excluded pages",
                    visibleBenchmark.pages.filter(
                      (page) => page.state === "excluded",
                    ).length,
                  ],
                ] as const
              ).map(([label, value]) => (
                <div key={label}>
                  <dt className="text-slate-700">{label}</dt>
                  <dd className="mt-1 text-xl font-semibold tabular-nums">
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
            <p className="mt-3 text-sm font-semibold">
              {visibleBenchmark.accuracy_status === "references_available" &&
              visibleBenchmark.adjudicated_pages > 0
                ? "Human-confirmed references are available. This is not an accuracy result."
                : "Accuracy is not established. These pages still need human comparison."}
            </p>
            <p className="mt-1 text-sm leading-6 text-slate-700">
              Excluded pages do not supply a confirmed reference. Page decisions
              and reference versions are shown separately; neither readability
              nor a saved candidate establishes accuracy.
            </p>
          </section>
          {pages.length ? (
            <>
              <nav
                aria-label="Selected page batches"
                className="flex flex-wrap items-center justify-between gap-3"
              >
                <p className="text-sm">
                  Pages {pageOffset + 1}–
                  {Math.min(
                    pageOffset + pageLimit,
                    visibleBenchmark.pages.length,
                  )}{" "}
                  of {visibleBenchmark.pages.length}
                </p>
                <div className="flex gap-2">
                  <Button
                    className={buttonClass}
                    isDisabled={pageOffset === 0}
                    onPress={() =>
                      setPageOffset((offset) => Math.max(0, offset - pageLimit))
                    }
                  >
                    Previous 40 pages
                  </Button>
                  <Button
                    className={buttonClass}
                    isDisabled={
                      pageOffset + pageLimit >= visibleBenchmark.pages.length
                    }
                    onPress={() =>
                      setPageOffset((offset) => offset + pageLimit)
                    }
                  >
                    Next 40 pages
                  </Button>
                </div>
              </nav>
              <div
                className="max-h-[60dvh] overflow-auto overscroll-contain rounded-lg border border-slate-300 bg-white"
                tabIndex={0}
              >
                <table
                  aria-label="Selected source pages"
                  className="w-full border-collapse text-left text-sm"
                >
                  <thead className="sticky top-0 z-10 bg-slate-100">
                    <tr>
                      {[
                        "Original source",
                        "Page",
                        "Page decision",
                        "Human-confirmed reference versions",
                        "Review",
                      ].map((label) => (
                        <th
                          className="border-b border-slate-300 p-3"
                          key={label}
                          scope="col"
                        >
                          {label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pages.map((page) => (
                      <tr
                        className="border-b border-slate-200 last:border-0"
                        key={`${page.document_id}:${page.page_number}`}
                      >
                        <th
                          className="max-w-md break-words p-3 font-medium"
                          scope="row"
                        >
                          {page.document_title}
                        </th>
                        <td className="p-3 tabular-nums">{page.page_number}</td>
                        <td className="p-3">{pageState(page.state)}</td>
                        <td className="p-3 tabular-nums">
                          {page.ground_truth_versions}
                        </td>
                        <td className="p-3">
                          <Link
                            aria-label={`Compare page ${page.page_number} of ${page.document_title}`}
                            className={`${buttonClass} whitespace-nowrap`}
                            href={`/admin/materials/${encodeURIComponent(page.document_id)}/review-text?page_number=${page.page_number}&benchmark_id=${encodeURIComponent(visibleBenchmark.id)}`}
                            prefetch={false}
                          >
                            Compare page
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <p>No pages are included in this review set.</p>
          )}
          <details className="rounded-lg border border-slate-300 bg-white p-3">
            <summary className="cursor-pointer rounded font-semibold focus-visible:ring-2 focus-visible:ring-amber-600">
              Technical details
            </summary>
            <dl className="mt-3 space-y-2 break-words text-sm">
              <div>
                <dt>Review set identifier</dt>
                <dd>{visibleBenchmark.id}</dd>
              </div>
              <div>
                <dt>Created at</dt>
                <dd>{visibleBenchmark.created_at}</dd>
              </div>
              <div>
                <dt>Reference status</dt>
                <dd>{visibleBenchmark.accuracy_status}</dd>
              </div>
            </dl>
            <ul className="mt-3 space-y-2 text-sm">
              {pages.map((page) => (
                <li key={`${page.document_id}:${page.page_number}`}>
                  {page.document_title} · Page {page.page_number}:{" "}
                  {page.categories.join(", ")}
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </section>
  );
}
