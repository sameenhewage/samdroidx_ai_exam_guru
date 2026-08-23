"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type SourcePage = components["schemas"]["SourcePageResponse"];
type ExtractedBlock = Omit<components["schemas"]["ExtractedBlockResponse"], "bbox"> & {
  bbox: number[];
};
type Role = "admin" | "reviewer";

const buttonClass =
  "rounded-md bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-900";

function errorCode(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail;
    if (detail && typeof detail === "object" && "code" in detail) {
      return String((detail as { code: unknown }).code);
    }
  }
  return "request_failed";
}

export function ExtractionReviewStudio({ documentId, role }: { documentId: string; role: Role }) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [document, setDocument] = useState<SourceDocument | null>(null);
  const [pages, setPages] = useState<SourcePage[]>([]);
  const [blocks, setBlocks] = useState<Record<number, ExtractedBlock[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const [documentsResult, pagesResult] = await Promise.all([
      api.GET("/api/v1/admin/source-documents"),
      api.GET("/api/v1/admin/source-documents/{document_id}/pages", {
        params: { path: { document_id: documentId } },
      }),
    ]);
    const current = documentsResult.data?.find((item) => item.id === documentId) ?? null;
    if (documentsResult.error || pagesResult.error || !current) {
      setError(errorCode(documentsResult.error ?? pagesResult.error ?? { detail: { code: "source_document_not_found" } }));
      setLoading(false);
      return;
    }
    const nextPages = pagesResult.data ?? [];
    const blockResults = await Promise.all(
      nextPages.map((page) =>
        api.GET(
          "/api/v1/admin/source-documents/{document_id}/pages/{page_number}/blocks",
          { params: { path: { document_id: documentId, page_number: page.page_number } } },
        ),
      ),
    );
    const blockError = blockResults.find((result) => result.error)?.error;
    if (blockError) {
      setError(errorCode(blockError));
    } else {
      setDocument(current);
      setPages(nextPages);
      setBlocks(
        Object.fromEntries(
          nextPages.map((page, index) => [page.page_number, blockResults[index]?.data ?? []]),
        ),
      );
      setError("");
    }
    setLoading(false);
  }, [api, documentId]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  async function beginReview() {
    const result = await api.POST("/api/v1/admin/source-documents/{document_id}/review", {
      params: { path: { document_id: documentId } },
    });
    if (result.error) setError(errorCode(result.error));
    else {
      setNotice("Human review started.");
      await load();
    }
  }

  async function savePage(event: FormEvent<HTMLFormElement>, page: SourcePage) {
    event.preventDefault();
    const reviewedText = String(new FormData(event.currentTarget).get("reviewed_text"));
    const result = await api.PATCH(
      "/api/v1/admin/source-documents/{document_id}/pages/{page_number}",
      {
        body: { expected_version: page.version, reviewed_text: reviewedText },
        params: { path: { document_id: documentId, page_number: page.page_number } },
      },
    );
    if (result.error) setError(errorCode(result.error));
    else {
      setPages((current) => current.map((item) => (item.id === page.id ? result.data : item)));
      setNotice(`Page ${page.page_number} correction saved.`);
    }
  }

  async function trustSource() {
    const result = await api.POST("/api/v1/admin/source-documents/{document_id}/trust", {
      params: { path: { document_id: documentId } },
    });
    if (result.error) setError(errorCode(result.error));
    else {
      setNotice("Trusted source");
      await load();
    }
  }

  if (loading) return <p className="mx-auto max-w-7xl p-8" role="status">Loading extraction review…</p>;
  if (error) return <p className="mx-auto max-w-7xl p-8 text-red-800" role="alert">{error}</p>;
  if (!document) return null;

  return (
    <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8">
      <Link className="text-sm font-semibold underline" href="/admin/documents">Back to documents</Link>
      <header className="mt-5 border-b border-slate-300 pb-6">
        <p className="font-mono text-xs uppercase text-amber-700">Human verification gate</p>
        <h1 className="mt-2 text-3xl font-semibold">Extraction review</h1>
        <p className="mt-2 break-words text-slate-600">{document.original_filename}</p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <span className="rounded-full bg-slate-200 px-3 py-1 text-sm font-semibold">
            {document.extraction_status === "trusted" ? "Trusted source" : document.extraction_status}
          </span>
          {document.extraction_status === "extracted" && (
            <button className={buttonClass} onClick={() => void beginReview()} type="button">Begin human review</button>
          )}
          {document.extraction_status === "in_review" && role === "admin" && (
            <button className={buttonClass} onClick={() => void trustSource()} type="button">Mark source trusted</button>
          )}
        </div>
        {notice && <p className="mt-4 rounded-md bg-emerald-50 p-3 text-emerald-900" role="status">{notice}</p>}
      </header>

      <div className="mt-8 grid gap-6">
        {pages.map((page) => (
          <article className="rounded-lg border border-slate-300 bg-white p-5" key={page.id}>
            <div className="flex items-center justify-between gap-4">
              <h2 className="text-xl font-semibold">Page {page.page_number}</h2>
              <span className="text-xs text-slate-500">{page.extractor} {page.extractor_version}</span>
            </div>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <section aria-label={`Raw page ${page.page_number} text`}>
                <h3 className="text-sm font-semibold uppercase text-slate-500">Immutable extraction</h3>
                <pre className="mt-2 min-h-32 whitespace-pre-wrap rounded-md bg-slate-950 p-4 text-sm text-white">{page.raw_text}</pre>
              </section>
              <section aria-label={`Reviewed page ${page.page_number}`}>
                <h3 className="text-sm font-semibold uppercase text-slate-500">Human correction</h3>
                {document.extraction_status === "in_review" ? (
                  <form className="mt-2 grid gap-3" onSubmit={(event) => void savePage(event, page)}>
                    <label className="grid gap-1 text-sm font-medium">
                      Reviewed page {page.page_number} text
                      <textarea className="min-h-32 rounded-md border border-slate-300 p-3" defaultValue={page.reviewed_text ?? page.raw_text} name="reviewed_text" />
                    </label>
                    <button className={secondaryButton} type="submit">Save page {page.page_number} correction</button>
                  </form>
                ) : (
                  <pre className="mt-2 min-h-32 whitespace-pre-wrap rounded-md border border-slate-300 p-4 text-sm">{page.reviewed_text ?? page.raw_text}</pre>
                )}
              </section>
            </div>
            <details className="mt-4">
              <summary className="cursor-pointer text-sm font-semibold">Block provenance ({blocks[page.page_number]?.length ?? 0})</summary>
              <ol className="mt-3 grid gap-2">
                {(blocks[page.page_number] ?? []).map((block) => (
                  <li className="rounded border border-slate-200 p-3 text-sm" key={block.id}>
                    <span className="font-mono text-xs text-slate-500">#{block.reading_order} · bbox {block.bbox.join(", ")}</span>
                    <p className="mt-1 whitespace-pre-wrap">{block.reviewed_text ?? block.raw_text}</p>
                  </li>
                ))}
              </ol>
            </details>
          </article>
        ))}
      </div>
    </div>
  );
}
