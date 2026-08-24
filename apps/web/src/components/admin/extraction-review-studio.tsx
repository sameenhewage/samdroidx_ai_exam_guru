"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type SourcePage = components["schemas"]["SourcePageResponse"];
type ExtractedBlock = components["schemas"]["ExtractedBlockResponse"];
type Role = "admin" | "reviewer";
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

  if (loading) {
    return (
      <p className="mx-auto max-w-7xl p-8" role="status">
        Loading extraction review…
      </p>
    );
  }
  if (error) {
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
