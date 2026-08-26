"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type SourcePage = components["schemas"]["SourcePageResponse"];
type ExtractedBlock = components["schemas"]["ExtractedBlockResponse"];
type Role = "admin" | "reviewer";
type ReviewExperience = "advanced" | "materials";
type ExtractionMode = "native" | "ocr" | "hybrid";

type BoundedExtractorIdentity = {
  engine: string | null;
  version: string | null;
};

type SafePageNumbers = {
  display: string;
  values: number[];
};

type DocumentExtractionManifest = {
  configState: string;
  mode: ExtractionMode | null;
  nativeIdentity: BoundedExtractorIdentity;
  ocrIdentity: BoundedExtractorIdentity;
  ocrPageNumbers: SafePageNumbers;
};

type ScalarConfigEntry = {
  key: string;
  value: string;
};

type ScalarConfigDisplay = {
  empty: boolean;
  entries: ScalarConfigEntry[];
  omitted: boolean;
};

const buttonClass =
  "rounded-md bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-900";
const MAX_CONFIG_ENTRIES = 12;
const MAX_INSPECTED_CONFIG_ENTRIES = 48;
const MAX_CONFIG_KEY_CHARACTERS = 128;
const MAX_CONFIG_VALUE_CHARACTERS = 256;
const MAX_ENGINE_CHARACTERS = 64;
const MAX_ENGINE_VERSION_CHARACTERS = 128;
const MAX_OCR_PAGE_NUMBERS = 50;
const MAX_SOURCE_PAGE_NUMBER = 1_000;
const unsafeTextControls = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g;
const secretConfigSegments = new Set(["apikey", "credential", "password", "secret", "token"]);

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function characterCount(value: string): number {
  return Array.from(value).length;
}

function plainText(value: string): string {
  return value.replace(unsafeTextControls, "�");
}

function boundedIdentity(value: unknown, maximumCharacters: number): string | null {
  if (
    typeof value !== "string" ||
    !value ||
    value !== value.trim() ||
    characterCount(value) > maximumCharacters
  ) {
    return null;
  }
  return plainText(value);
}

function isSecretBearingConfigKey(key: string): boolean {
  const segments = key
    .replace(/([a-z\d])([A-Z])/g, "$1_$2")
    .toLocaleLowerCase("en")
    .split(/[^a-z\d]+/)
    .filter(Boolean);
  return (
    segments.some((segment) => secretConfigSegments.has(segment)) ||
    segments.some(
      (segment, index) =>
        (segment === "api" && segments[index + 1] === "key") ||
        (segment === "private" && segments[index + 1] === "key"),
    )
  );
}

function scalarConfigValue(value: unknown): string | null {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : null;
  if (typeof value !== "string" || characterCount(value) > MAX_CONFIG_VALUE_CHARACTERS) {
    return null;
  }
  if (!value) return "(empty string)";
  return plainText(value);
}

function scalarConfigDisplay(config: unknown): ScalarConfigDisplay {
  if (!isObjectRecord(config)) return { empty: false, entries: [], omitted: true };
  const keys = Object.keys(config);
  if (!keys.length) return { empty: true, entries: [], omitted: false };

  const entries: ScalarConfigEntry[] = [];
  let omitted = keys.length > MAX_INSPECTED_CONFIG_ENTRIES;
  const inspectedKeys = keys.slice(0, MAX_INSPECTED_CONFIG_ENTRIES);
  for (const key of inspectedKeys) {
    const safeKey = boundedIdentity(key, MAX_CONFIG_KEY_CHARACTERS);
    const safeValue = scalarConfigValue(config[key]);
    if (
      safeKey === null ||
      safeValue === null ||
      isSecretBearingConfigKey(key) ||
      entries.length >= MAX_CONFIG_ENTRIES
    ) {
      omitted = true;
      continue;
    }
    entries.push({ key: safeKey, value: safeValue });
  }
  return { empty: false, entries, omitted };
}

function extractorIdentity(value: unknown): BoundedExtractorIdentity {
  if (!isObjectRecord(value)) return { engine: null, version: null };
  return {
    engine: boundedIdentity(value.engine, MAX_ENGINE_CHARACTERS),
    version: boundedIdentity(value.version, MAX_ENGINE_VERSION_CHARACTERS),
  };
}

function extractionMode(value: unknown): ExtractionMode | null {
  return value === "native" || value === "ocr" || value === "hybrid" ? value : null;
}

function safePageNumbers(value: unknown): SafePageNumbers {
  if (!Array.isArray(value)) return { display: "Not recorded", values: [] };
  if (!value.length) return { display: "None", values: [] };

  const values: number[] = [];
  const seen = new Set<number>();
  let omitted = value.length > MAX_OCR_PAGE_NUMBERS;
  for (const candidate of value.slice(0, MAX_OCR_PAGE_NUMBERS)) {
    if (
      typeof candidate !== "number" ||
      !Number.isSafeInteger(candidate) ||
      candidate < 1 ||
      candidate > MAX_SOURCE_PAGE_NUMBER ||
      seen.has(candidate)
    ) {
      omitted = true;
      continue;
    }
    seen.add(candidate);
    values.push(candidate);
  }

  const display = values.length ? values.join(", ") : "No valid page numbers recorded";
  return {
    display: omitted ? `${display} (additional or invalid values not displayed)` : display,
    values,
  };
}

function documentExtractionManifest(config: unknown): DocumentExtractionManifest {
  if (config === null || config === undefined) {
    return {
      configState: "Not recorded.",
      mode: null,
      nativeIdentity: { engine: null, version: null },
      ocrIdentity: { engine: null, version: null },
      ocrPageNumbers: { display: "Not recorded", values: [] },
    };
  }
  if (!isObjectRecord(config)) {
    return {
      configState: "Invalid configuration is not displayed.",
      mode: null,
      nativeIdentity: { engine: null, version: null },
      ocrIdentity: { engine: null, version: null },
      ocrPageNumbers: { display: "Not recorded", values: [] },
    };
  }

  return {
    configState: Object.keys(config).length
      ? "Structured provenance recorded."
      : "Empty configuration.",
    mode: extractionMode(config.mode),
    nativeIdentity: extractorIdentity(config.native),
    ocrIdentity: extractorIdentity(config.ocr),
    ocrPageNumbers: safePageNumbers(config.ocr_page_numbers),
  };
}

function displayMode(mode: ExtractionMode | null): string {
  if (mode === "ocr") return "OCR";
  if (mode === "hybrid") return "Hybrid";
  if (mode === "native") return "Native";
  return "Not recorded";
}

function displayExtractorIdentity(engine: unknown, version: unknown): string {
  const safeEngine = boundedIdentity(engine, MAX_ENGINE_CHARACTERS);
  const safeVersion = boundedIdentity(version, MAX_ENGINE_VERSION_CHARACTERS);
  if (safeEngine && safeVersion) return `${safeEngine} ${safeVersion}`;
  if (safeEngine) return `${safeEngine} (version not recorded)`;
  if (safeVersion) return `Engine not recorded (version ${safeVersion})`;
  return "Not recorded";
}

function displayNestedIdentity(identity: BoundedExtractorIdentity): string {
  return displayExtractorIdentity(identity.engine, identity.version);
}

function displayCount(value: unknown): string {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? String(value)
    : "Not recorded";
}

function displayNeedsOcr(value: unknown): string {
  if (value === true) return "Yes";
  if (value === false) return "No";
  return "Not recorded";
}

function displayConfidence(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1
    ? String(value)
    : "Not recorded";
}

function displayBoundingBox(bbox: ExtractedBlock["bbox"]): string {
  if (
    bbox === null ||
    !Array.isArray(bbox) ||
    bbox.length !== 4 ||
    bbox.some((coordinate) => typeof coordinate !== "number" || !Number.isFinite(coordinate))
  ) {
    return "Not recorded";
  }
  return bbox.join(", ");
}

function pageUsesOcr(page: SourcePage, manifest: DocumentExtractionManifest): boolean {
  if (manifest.mode === "ocr") return true;
  if (manifest.ocrPageNumbers.values.includes(page.page_number)) return true;
  const pageExtractor = boundedIdentity(page.extractor, MAX_ENGINE_CHARACTERS);
  return pageExtractor !== null && pageExtractor === manifest.ocrIdentity.engine;
}

function ProvenanceDefinition({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-3">
      <dt className="text-xs font-semibold uppercase text-slate-500">{label}</dt>
      <dd className="mt-1 break-words text-sm text-slate-900"> {value}</dd>
    </div>
  );
}

function ScalarConfig({ config }: { config: unknown }) {
  const display = scalarConfigDisplay(config);
  if (display.empty) return <span>Empty configuration.</span>;

  return (
    <div className="grid gap-2">
      {display.entries.length ? (
        <dl className="grid gap-2 sm:grid-cols-2">
          {display.entries.map((entry) => (
            <div className="rounded border border-slate-200 bg-slate-50 p-2" key={entry.key}>
              <dt className="break-all font-mono text-xs text-slate-600">{entry.key}</dt>
              <dd className="mt-1 break-words text-sm text-slate-900"> {entry.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <span>No bounded scalar configuration values to display.</span>
      )}
      {display.omitted && (
        <p className="text-xs text-slate-600">
          Additional, non-scalar, or oversized configuration values are not displayed.
        </p>
      )}
    </div>
  );
}

function errorCode(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail;
    if (detail && typeof detail === "object" && "code" in detail) {
      return String((detail as { code: unknown }).code);
    }
  }
  return "request_failed";
}

function materialReviewStatus(document: SourceDocument): string {
  if (document.use_state === "removed") return "Removed";
  if (document.extraction_status === "trusted") return "Ready for AI";
  if (["extracted", "in_review"].includes(document.extraction_status)) return "Needs review";
  if (document.extraction_status === "failed") return "Reading failed";
  return "Processing";
}

export function ExtractionReviewStudio({
  documentId,
  experience = "advanced",
  role,
}: {
  documentId: string;
  experience?: ReviewExperience;
  role: Role;
}) {
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
  const [selectedPageIndex, setSelectedPageIndex] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [documentsResult, pagesResult] = await Promise.all([
        api.GET("/api/v1/admin/source-documents"),
        api.GET("/api/v1/admin/source-documents/{document_id}/pages", {
          params: { path: { document_id: documentId } },
        }),
      ]);
      const current = documentsResult.data?.find((item) => item.id === documentId) ?? null;
      if (documentsResult.error || pagesResult.error || !current) {
        setError(
          errorCode(
            documentsResult.error ??
              pagesResult.error ?? { detail: { code: "source_document_not_found" } },
          ),
        );
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
      const hasInvalidBoundingBox = blockResults.some((result) =>
        result.data?.some(
          (block) =>
            block.bbox !== null &&
            (block.bbox.length !== 4 ||
              block.bbox.some(
                (coordinate) => typeof coordinate !== "number" || !Number.isFinite(coordinate),
              )),
        ),
      );
      if (blockError || hasInvalidBoundingBox) {
        setError(blockError ? errorCode(blockError) : "invalid_extracted_block_response");
      } else {
        const nextBlocks: Record<number, ExtractedBlock[]> = {};
        nextPages.forEach((page, index) => {
          nextBlocks[page.page_number] = (blockResults[index]?.data ?? []).map(
            (block): ExtractedBlock => ({
              ...block,
              bbox:
                block.bbox === null
                  ? null
                  : [block.bbox[0], block.bbox[1], block.bbox[2], block.bbox[3]],
            }),
          );
        });
        setDocument(current);
        setPages(nextPages);
        setBlocks(nextBlocks);
      }
    } catch {
      setError("network_error");
    } finally {
      setLoading(false);
    }
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
      setNotice(experience === "materials" ? "Ready for AI" : "Trusted source");
      await load();
    }
  }

  if (loading) {
    return (
      <p className="mx-auto max-w-7xl p-8" role="status">
        Loading extraction review…
      </p>
    );
  }
  if (error) {
    if (experience === "materials") {
      return (
        <section className="mx-auto max-w-4xl px-5 py-10 sm:px-8" role="alert">
          <div className="rounded-xl border border-red-300 bg-red-50 p-5 text-red-950">
            <h1 className="text-2xl font-semibold">Text review could not be loaded</h1>
            <p className="mt-2 text-sm leading-6">
              {error === "network_error"
                ? "The connection to the document service failed. Try again when it is available."
                : error === "permission_denied"
                  ? "Your account does not have permission to review this material."
                  : error === "source_document_not_found"
                    ? "This material or its extracted pages could not be found."
                    : "The extracted pages could not be opened safely."}
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              <button className={secondaryButton} onClick={() => void load()} type="button">
                Try again
              </button>
              <Link className={secondaryButton} href={`/admin/materials/${documentId}`}>
                Back to material
              </Link>
            </div>
          </div>
        </section>
      );
    }
    return (
      <p className="mx-auto max-w-7xl p-8 text-red-800" role="alert">
        {error}
      </p>
    );
  }
  if (!document) return null;

  const manifest = documentExtractionManifest(document.extraction_config);
  const hasOcrDerivedText =
    manifest.mode === "ocr" ||
    manifest.mode === "hybrid" ||
    manifest.ocrIdentity.engine !== null ||
    manifest.ocrPageNumbers.values.length > 0 ||
    (typeof document.ocr_page_count === "number" && document.ocr_page_count > 0);

  if (experience === "materials") {
    const selectedPage = pages[selectedPageIndex] ?? pages[0] ?? null;
    const selectedBlocks = selectedPage ? blocks[selectedPage.page_number] ?? [] : [];
    const canEdit = role === "admin" && document.extraction_status === "in_review";

    return (
      <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8 lg:py-12">
        <Link
          className="text-sm font-semibold underline decoration-amber-500 underline-offset-4"
          href={`/admin/materials/${documentId}`}
        >
          Back to material
        </Link>
        <header className="mt-5 border-b border-slate-300 pb-6">
          <p className="text-xs font-semibold tracking-wider text-amber-800 uppercase">
            Human review
          </p>
          <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-3xl font-semibold sm:text-4xl">Review text</h1>
              <p className="mt-2 break-words text-slate-600">{document.original_filename}</p>
            </div>
            <span className="rounded-full border border-slate-300 bg-white px-3 py-1 text-sm font-semibold">
              {materialReviewStatus(document)}
            </span>
          </div>
          <p className="mt-4 max-w-3xl leading-7 text-slate-600">
            Compare the immutable text captured from each source page with the editable review copy.
            Uploaded content is untrusted until a person checks it and marks it ready.
          </p>

          <div className="mt-5 flex flex-wrap items-center gap-3">
            {document.extraction_status === "extracted" && role === "admin" && (
              <button className={buttonClass} onClick={() => void beginReview()} type="button">
                Begin text review
              </button>
            )}
            {document.extraction_status === "in_review" && role === "admin" && (
              <button className={buttonClass} onClick={() => void trustSource()} type="button">
                Mark reviewed / Ready for AI
              </button>
            )}
            {role === "reviewer" && (
              <p className="text-sm font-semibold text-slate-600">Reviewer access is read-only.</p>
            )}
          </div>

          {hasOcrDerivedText && (
            <aside className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-amber-950">
              <p className="font-semibold">Some text came from OCR and needs careful human review.</p>
              <p className="mt-1 text-sm">
                Confidence information is diagnostic only; it does not make the source trusted.
              </p>
            </aside>
          )}

          {notice && (
            <p className="mt-4 rounded-md bg-emerald-50 p-3 font-semibold text-emerald-900" role="status">
              {notice}
            </p>
          )}
        </header>

        {selectedPage ? (
          <article className="mt-8 rounded-xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6">
            <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-200 pb-4">
              <p className="font-semibold">
                Page {selectedPageIndex + 1} of {pages.length}
              </p>
              <div className="flex gap-2">
                <button
                  className={secondaryButton}
                  disabled={selectedPageIndex === 0}
                  onClick={() => setSelectedPageIndex((index) => Math.max(0, index - 1))}
                  type="button"
                >
                  Previous page
                </button>
                <button
                  className={secondaryButton}
                  disabled={selectedPageIndex >= pages.length - 1}
                  onClick={() =>
                    setSelectedPageIndex((index) => Math.min(pages.length - 1, index + 1))
                  }
                  type="button"
                >
                  Next page
                </button>
              </div>
            </div>

            <div className="mt-5 grid gap-5 lg:grid-cols-2">
              <section
                aria-label="Original extracted page"
                className="rounded-lg border border-slate-300 bg-slate-50 p-4"
              >
                <h2 className="text-lg font-semibold">Original extracted page</h2>
                <p className="mt-1 text-sm text-slate-600">
                  Immutable text recorded when the PDF was read.
                </p>
                <pre className="mt-4 min-h-56 whitespace-pre-wrap rounded-md bg-slate-950 p-4 text-sm text-white">
                  {selectedPage.raw_text}
                </pre>
              </section>

              <section
                aria-label="Extracted text"
                className="rounded-lg border border-slate-300 bg-white p-4"
              >
                <h2 className="text-lg font-semibold">Reviewed text</h2>
                <p className="mt-1 text-sm text-slate-600">
                  Correct reading mistakes without changing the immutable source copy.
                </p>
                {canEdit ? (
                  <form
                    className="mt-4 grid gap-3"
                    onSubmit={(event) => void savePage(event, selectedPage)}
                  >
                    <label className="grid gap-2 text-sm font-semibold">
                      Corrected text for page {selectedPage.page_number}
                      <textarea
                        className="min-h-56 rounded-md border border-slate-300 p-3 font-normal outline-none focus:border-amber-600 focus:ring-2 focus:ring-amber-200"
                        defaultValue={selectedPage.reviewed_text ?? selectedPage.raw_text}
                        name="reviewed_text"
                      />
                    </label>
                    <button className={secondaryButton} type="submit">
                      Save correction
                    </button>
                  </form>
                ) : (
                  <pre className="mt-4 min-h-56 whitespace-pre-wrap rounded-md border border-slate-300 p-4 text-sm">
                    {selectedPage.reviewed_text ?? selectedPage.raw_text}
                  </pre>
                )}
              </section>
            </div>

            <details className="mt-5 rounded-lg border border-slate-300 bg-slate-50 p-4">
              <summary className="w-fit cursor-pointer rounded text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-amber-600">
                Technical details
              </summary>
              <section
                aria-label={`Page ${selectedPage.page_number} extraction provenance`}
                className="mt-4"
              >
                <dl className="grid gap-3 sm:grid-cols-3">
                  <ProvenanceDefinition
                    label="Extractor"
                    value={
                      boundedIdentity(selectedPage.extractor, MAX_ENGINE_CHARACTERS) ??
                      "Not recorded"
                    }
                  />
                  <ProvenanceDefinition
                    label="Extractor version"
                    value={
                      boundedIdentity(
                        selectedPage.extractor_version,
                        MAX_ENGINE_VERSION_CHARACTERS,
                      ) ?? "Not recorded"
                    }
                  />
                  <ProvenanceDefinition
                    label="Confidence"
                    value={displayConfidence(selectedPage.confidence)}
                  />
                </dl>
                <p className="mt-3 text-sm text-slate-600">
                  {selectedBlocks.length} extraction {selectedBlocks.length === 1 ? "block" : "blocks"} recorded for this page.
                </p>
              </section>
            </details>
          </article>
        ) : (
          <p className="mt-8 rounded-xl border border-dashed border-slate-400 bg-white p-8 text-center text-slate-600">
            No extracted pages are available yet.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8">
      <Link className="text-sm font-semibold underline" href="/admin/documents">
        Back to documents
      </Link>
      <header className="mt-5 border-b border-slate-300 pb-6">
        <p className="font-mono text-xs uppercase text-amber-700">Human verification gate</p>
        <h1 className="mt-2 text-3xl font-semibold">Extraction review</h1>
        <p className="mt-2 break-words text-slate-600">{document.original_filename}</p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <span className="rounded-full bg-slate-200 px-3 py-1 text-sm font-semibold">
            {document.extraction_status === "trusted"
              ? "Trusted source"
              : document.extraction_status}
          </span>
          {document.extraction_status === "extracted" && (
            <button
              className={buttonClass}
              onClick={() => void beginReview()}
              type="button"
            >
              Begin human review
            </button>
          )}
          {document.extraction_status === "in_review" && role === "admin" && (
            <button
              className={buttonClass}
              onClick={() => void trustSource()}
              type="button"
            >
              Mark source trusted
            </button>
          )}
        </div>

        <section
          aria-label="Document extraction provenance"
          className="mt-5 rounded-lg border border-slate-300 bg-slate-50 p-4"
        >
          <h2 className="text-base font-semibold">Persisted extraction provenance</h2>
          <p className="mt-1 text-sm text-slate-600">
            Only bounded provenance fields are shown; nested configuration values are not expanded.
          </p>
          <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <ProvenanceDefinition
              label="Extraction mode"
              value={displayMode(manifest.mode)}
            />
            <ProvenanceDefinition
              label="Document extractor"
              value={displayExtractorIdentity(document.extractor, document.extractor_version)}
            />
            <ProvenanceDefinition
              label="OCR page count"
              value={displayCount(document.ocr_page_count)}
            />
            <ProvenanceDefinition
              label="Still needs OCR"
              value={displayNeedsOcr(document.needs_ocr)}
            />
            <ProvenanceDefinition
              label="Native extractor"
              value={displayNestedIdentity(manifest.nativeIdentity)}
            />
            <ProvenanceDefinition
              label="OCR extractor"
              value={displayNestedIdentity(manifest.ocrIdentity)}
            />
            <ProvenanceDefinition
              label="OCR page numbers"
              value={manifest.ocrPageNumbers.display}
            />
            <ProvenanceDefinition
              label="Document extraction configuration"
              value={manifest.configState}
            />
          </dl>
        </section>

        {hasOcrDerivedText && (
          <aside
            aria-label="OCR trust warning"
            className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-amber-950"
          >
            <p className="font-semibold">
              OCR-derived text is untrusted source content. Human review is required before trust or
              downstream use.
            </p>
            <p className="mt-1 text-sm">
              Recorded confidence is provenance only; no OCR quality claim is made.
            </p>
          </aside>
        )}

        {notice && (
          <p className="mt-4 rounded-md bg-emerald-50 p-3 text-emerald-900" role="status">
            {notice}
          </p>
        )}
      </header>

      <div className="mt-8 grid gap-6">
        {pages.map((page) => {
          const pageBlocks = blocks[page.page_number] ?? [];
          const usesOcr = pageUsesOcr(page, manifest);
          const headingId = `source-page-${page.id}-heading`;
          return (
            <article
              aria-labelledby={headingId}
              className="rounded-lg border border-slate-300 bg-white p-5"
              key={page.id}
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-xl font-semibold" id={headingId}>
                  Page {page.page_number}
                </h2>
                {usesOcr && (
                  <span className="rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-950">
                    OCR-derived text — untrusted; human review required
                  </span>
                )}
              </div>

              <section
                aria-label={`Page ${page.page_number} extraction provenance`}
                className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4"
              >
                <h3 className="text-sm font-semibold uppercase text-slate-600">
                  Page extraction provenance
                </h3>
                <dl className="mt-3 grid gap-3 sm:grid-cols-3">
                  <ProvenanceDefinition
                    label="Extractor"
                    value={
                      boundedIdentity(page.extractor, MAX_ENGINE_CHARACTERS) ?? "Not recorded"
                    }
                  />
                  <ProvenanceDefinition
                    label="Extractor version"
                    value={
                      boundedIdentity(page.extractor_version, MAX_ENGINE_VERSION_CHARACTERS) ??
                      "Not recorded"
                    }
                  />
                  <ProvenanceDefinition
                    label="Confidence"
                    value={displayConfidence(page.confidence)}
                  />
                  <div className="rounded-md border border-slate-200 bg-white p-3 sm:col-span-3">
                    <dt className="text-xs font-semibold uppercase text-slate-500">
                      Extractor configuration
                    </dt>
                    <dd className="mt-2 text-sm text-slate-900">
                      {" "}
                      <ScalarConfig config={page.extraction_config} />
                    </dd>
                  </div>
                </dl>
              </section>

              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                <section aria-label={`Raw page ${page.page_number} text`}>
                  <h3 className="text-sm font-semibold uppercase text-slate-500">
                    Immutable extraction
                  </h3>
                  <pre className="mt-2 min-h-32 whitespace-pre-wrap rounded-md bg-slate-950 p-4 text-sm text-white">
                    {page.raw_text}
                  </pre>
                </section>
                <section aria-label={`Reviewed page ${page.page_number}`}>
                  <h3 className="text-sm font-semibold uppercase text-slate-500">
                    Human correction
                  </h3>
                  {document.extraction_status === "in_review" ? (
                    <form
                      className="mt-2 grid gap-3"
                      onSubmit={(event) => void savePage(event, page)}
                    >
                      <label className="grid gap-1 text-sm font-medium">
                        Reviewed page {page.page_number} text
                        <textarea
                          className="min-h-32 rounded-md border border-slate-300 p-3"
                          defaultValue={page.reviewed_text ?? page.raw_text}
                          name="reviewed_text"
                        />
                      </label>
                      <button className={secondaryButton} type="submit">
                        Save page {page.page_number} correction
                      </button>
                    </form>
                  ) : (
                    <pre className="mt-2 min-h-32 whitespace-pre-wrap rounded-md border border-slate-300 p-4 text-sm">
                      {page.reviewed_text ?? page.raw_text}
                    </pre>
                  )}
                </section>
              </div>

              <details className="mt-4">
                <summary className="cursor-pointer text-sm font-semibold">
                  Block provenance ({pageBlocks.length})
                </summary>
                {pageBlocks.length ? (
                  <ol className="mt-3 grid gap-3">
                    {pageBlocks.map((block, index) => (
                      <li className="rounded border border-slate-200 p-3 text-sm" key={block.id}>
                        <section aria-label={`Block ${index + 1} extraction provenance`}>
                          <h3 className="font-semibold">Block {index + 1}</h3>
                          <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                            <ProvenanceDefinition
                              label="Reading order"
                              value={displayCount(block.reading_order)}
                            />
                            <ProvenanceDefinition
                              label="Extractor"
                              value={
                                boundedIdentity(block.extractor, MAX_ENGINE_CHARACTERS) ??
                                "Not recorded"
                              }
                            />
                            <ProvenanceDefinition
                              label="Extractor version"
                              value={
                                boundedIdentity(
                                  block.extractor_version,
                                  MAX_ENGINE_VERSION_CHARACTERS,
                                ) ?? "Not recorded"
                              }
                            />
                            <ProvenanceDefinition
                              label="Confidence"
                              value={displayConfidence(block.confidence)}
                            />
                            <ProvenanceDefinition
                              label="Bounding box"
                              value={displayBoundingBox(block.bbox)}
                            />
                            <div className="rounded-md border border-slate-200 bg-white p-3 sm:col-span-2 lg:col-span-5">
                              <dt className="text-xs font-semibold uppercase text-slate-500">
                                Extractor configuration
                              </dt>
                              <dd className="mt-2 text-sm text-slate-900">
                                {" "}
                                <ScalarConfig config={block.extraction_config} />
                              </dd>
                            </div>
                          </dl>
                          <p className="mt-3 whitespace-pre-wrap">
                            {block.reviewed_text ?? block.raw_text}
                          </p>
                        </section>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="mt-3 text-sm text-slate-600">No extraction blocks recorded.</p>
                )}
              </details>
            </article>
          );
        })}
      </div>
    </div>
  );
}
