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
  useState,
  type FormEvent,
} from "react";
import { Button, Form, Input, Label, TextField } from "react-aria-components";

type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type DocumentType = components["schemas"]["SourceDocumentType"];
type ExtractionStatus = components["schemas"]["ExtractionStatus"];
type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type UploadBody = operations["upload_source_document"]["requestBody"]["content"]["multipart/form-data"];
type Role = "admin" | "reviewer";

type UiError = {
  code: string;
  message: string;
};

const documentTypes: ReadonlyArray<{ label: string; value: DocumentType }> = [
  { label: "Syllabus", value: "syllabus" },
  { label: "Teacher guide", value: "teacher_guide" },
  { label: "Past paper", value: "past_paper" },
  { label: "Marking scheme", value: "marking_scheme" },
  { label: "Evaluation report", value: "evaluation_report" },
  { label: "Other approved source", value: "other_approved" },
];

const extractionLabels: Record<ExtractionStatus, string> = {
  uploaded: "Uploaded",
  extraction_pending: "Extraction pending",
  extracted: "Extracted",
  in_review: "In review",
  trusted: "Trusted",
  failed: "Failed",
};

const errorMessages: Record<string, string> = {
  authentication_required: "Your admin session has expired. Sign in again before retrying.",
  curriculum_version_inactive: "The selected curriculum version is inactive. Choose another version.",
  curriculum_version_not_found: "The selected curriculum version no longer exists.",
  empty_file: "The selected PDF is empty.",
  file_too_large: "The selected PDF exceeds the configured upload limit.",
  invalid_pdf_signature: "The selected file did not contain a valid PDF signature.",
  invalid_proxy_path: "The upload path was rejected by the same-origin proxy.",
  network_error: "The service could not be reached. Check the connection and retry.",
  permission_denied: "Your account does not have permission to upload source documents.",
  request_too_large: "The upload exceeds the current same-origin proxy request limit.",
  service_unavailable: "The document service is temporarily unavailable.",
  unsafe_filename: "Rename the PDF to remove path or control characters, then retry.",
  unsupported_media_type: "Only PDF uploads are accepted.",
  upload_response_invalid: "The server accepted the request but returned no document metadata.",
};

const fieldClass = "grid gap-1.5 text-sm font-medium text-slate-700";
const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 outline-none transition focus:border-amber-500 focus:ring-2 focus:ring-amber-200 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none transition hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-55";
const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-400 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";

function errorCode(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail) && "code" in detail) {
      return String((detail as { code: unknown }).code);
    }
  }
  return "request_failed";
}

function uiError(error: unknown): UiError {
  const code = errorCode(error);
  return {
    code,
    message: errorMessages[code] ?? "The request could not be completed. Retry or contact an administrator.",
  };
}

function networkError(): UiError {
  return { code: "network_error", message: errorMessages.network_error };
}

function uploadFormData(body: UploadBody, file: File): FormData {
  const form = new FormData();
  form.append("file", file);
  form.append("document_type", body.document_type);
  if (body.curriculum_version_id) form.append("curriculum_version_id", body.curriculum_version_id);
  if (body.year !== undefined && body.year !== null) form.append("year", String(body.year));
  if (body.paper_code) form.append("paper_code", body.paper_code);
  return form;
}

function formatBytes(bytes: number): string {
  if (bytes < 1_024) return `${bytes} B`;
  if (bytes < 1_024 * 1_024) return `${(bytes / 1_024).toFixed(1)} KB`;
  return `${(bytes / (1_024 * 1_024)).toFixed(1)} MB`;
}

function validatePdf(file: File | null): UiError | null {
  if (!file) return { code: "pdf_required", message: "Choose a PDF file before uploading." };
  if (file.size === 0) return { code: "empty_file", message: errorMessages.empty_file };
  if (file.type !== "application/pdf" || !file.name.toLocaleLowerCase().endsWith(".pdf")) {
    return { code: "invalid_pdf_selection", message: "Choose a PDF file with a .pdf name." };
  }
  if (
    file.name !== file.name.normalize("NFC") ||
    file.name.includes("/") ||
    file.name.includes("\\") ||
    [...file.name].some((character) => {
      const code = character.codePointAt(0) ?? 0;
      return code < 32 || code === 127;
    })
  ) {
    return { code: "unsafe_filename", message: errorMessages.unsafe_filename };
  }
  return null;
}

export function DocumentsStudio({ role }: { role: Role }) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [curricula, setCurricula] = useState<Curriculum[]>([]);
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [loadError, setLoadError] = useState<UiError | null>(null);
  const [uploadError, setUploadError] = useState<UiError | null>(null);
  const [lastResult, setLastResult] = useState<SourceDocument | null>(null);
  const [uploadPermissionDenied, setUploadPermissionDenied] = useState(false);
  const [extractionNotice, setExtractionNotice] = useState("");

  const activeCurricula = curricula.filter((curriculum) => curriculum.active);
  const canUpload = role === "admin" && !uploadPermissionDenied;

  const loadMetadata = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [curriculumResult, documentResult] = await Promise.all([
        api.GET("/api/v1/admin/curriculum-versions"),
        api.GET("/api/v1/admin/source-documents"),
      ]);
      if (!curriculumResult.response.ok || !documentResult.response.ok) {
        const responseError = curriculumResult.error ?? documentResult.error;
        setLoadError(uiError(responseError));
      } else {
        setCurricula(curriculumResult.data ?? []);
        setDocuments(documentResult.data ?? []);
      }
    } catch {
      setLoadError(networkError());
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadMetadata(), 0);
    return () => window.clearTimeout(timeout);
  }, [loadMetadata]);

  async function uploadDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const file = selectedFile;
    const fileError = validatePdf(file);
    if (fileError) {
      setUploadError(fileError);
      setLastResult(null);
      return;
    }
    if (!file) return;

    const form = new FormData(formElement);
    const rawYear = String(form.get("year") ?? "").trim();
    const year = rawYear ? Number(rawYear) : null;
    if (year !== null && (!Number.isInteger(year) || year < 1900 || year > 2100)) {
      setUploadError({
        code: "invalid_year",
        message: "Enter a whole year from 1900 through 2100, or leave it blank.",
      });
      setLastResult(null);
      return;
    }

    const curriculumVersionId = String(form.get("curriculum_version_id") ?? "").trim();
    const paperCode = String(form.get("paper_code") ?? "").trim();
    const body: UploadBody = {
      curriculum_version_id: curriculumVersionId || null,
      document_type: String(form.get("document_type")) as DocumentType,
      file: file.name,
      paper_code: paperCode || null,
      year,
    };

    setUploading(true);
    setUploadError(null);
    setLastResult(null);
    try {
      const result = await api.POST("/api/v1/admin/source-documents", {
        body,
        bodySerializer: (requestBody) => uploadFormData(requestBody, file),
      });
      if (result.error) {
        const nextError = uiError(result.error);
        if (result.response.status === 403) {
          nextError.code = "permission_denied";
          nextError.message = errorMessages.permission_denied;
          setUploadPermissionDenied(true);
        }
        setUploadError(nextError);
        return;
      }

      // The API returns the same response body for 201 creation and idempotent 200 reuse.
      const document = result.data as SourceDocument | undefined;
      if (!document) {
        setUploadError({
          code: "upload_response_invalid",
          message: errorMessages.upload_response_invalid,
        });
        return;
      }

      setDocuments((current) => [document, ...current.filter((item) => item.id !== document.id)]);
      setLastResult(document);
      setSelectedFile(null);
      formElement.reset();
    } catch {
      setUploadError(networkError());
    } finally {
      setUploading(false);
    }
  }

  async function queueExtraction(document: SourceDocument) {
    setExtractionNotice("");
    try {
      const result = await api.POST(
        "/api/v1/admin/source-documents/{document_id}/extract",
        { params: { path: { document_id: document.id } } },
      );
      if (result.error) {
        setExtractionNotice(`Extraction was not queued: ${errorCode(result.error)}`);
        return;
      }
      setExtractionNotice("Native extraction queued.");
    } catch {
      setExtractionNotice("Extraction was not queued: network_error");
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8 lg:py-10">
      <header className="border-b border-slate-300 pb-7">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div>
            <p className="font-mono text-xs font-semibold tracking-[0.18em] text-amber-700 uppercase">
              P2 / source ingestion
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Source documents</h1>
            <p className="mt-3 max-w-3xl leading-7 text-slate-600">
              Preserve approved Grade 5 PDFs with curriculum and paper metadata, then inspect the
              upload response and extraction state returned by the document service.
            </p>
          </div>
          <div className="rounded-lg border border-slate-300 bg-white px-4 py-3 text-sm shadow-sm">
            <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Access</p>
            <p className="mt-1 font-semibold capitalize">{role}</p>
          </div>
        </div>
      </header>

      {extractionNotice && (
        <p className="mt-6 rounded-lg border border-sky-300 bg-sky-50 p-4 text-sm text-sky-900" role="status">
          {extractionNotice}
        </p>
      )}

      {loading && (
        <div
          aria-live="polite"
          className="mt-7 flex items-center gap-3 rounded-lg border border-slate-300 bg-white p-5 text-sm text-slate-600"
          role="status"
        >
          <span aria-hidden="true" className="size-2 animate-pulse rounded-full bg-amber-500" />
          Loading document workspace…
        </div>
      )}

      {!loading && loadError && (
        <div className="mt-7 rounded-lg border border-red-300 bg-red-50 p-5 text-sm text-red-900" role="alert">
          <p className="font-semibold">Document metadata could not be loaded.</p>
          <p className="mt-1">{loadError.message}</p>
          <p className="mt-2 font-mono text-xs">Error code: {loadError.code}</p>
          <Button className={`${secondaryButton} mt-4`} onPress={() => void loadMetadata()}>
            Retry loading metadata
          </Button>
        </div>
      )}

      {!loading && (
        <div className="mt-8 grid items-start gap-8 xl:grid-cols-[minmax(0,1.1fr)_minmax(22rem,0.9fr)]">
          <section aria-labelledby="upload-heading" className="rounded-xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="font-mono text-xs text-slate-500 uppercase">Immutable source</p>
                <h2 className="mt-1 text-2xl font-semibold" id="upload-heading">Upload a PDF</h2>
              </div>
              <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-900">
                PDF only
              </span>
            </div>

            {!canUpload ? (
              <div className="mt-6 rounded-lg border border-amber-300 bg-amber-50 p-5">
                <h3 className="font-semibold text-amber-950">Upload permission required</h3>
                <p className="mt-2 text-sm leading-6 text-amber-900">
                  {role === "reviewer"
                    ? "Reviewer access is read-only for source documents."
                    : "The service denied upload permission for this admin session."}
                </p>
                <p className="mt-2 text-sm text-amber-800">
                  Sign in with an authorized admin identity to add an immutable source.
                </p>
              </div>
            ) : (
              <Form className="mt-6 grid gap-5" onSubmit={uploadDocument} validationBehavior="aria">
                <div className={fieldClass}>
                  <label htmlFor="source-pdf">PDF file</label>
                  <input
                    accept=".pdf,application/pdf"
                    aria-describedby="pdf-help selected-pdf"
                    aria-required="true"
                    className="block w-full cursor-pointer rounded-lg border border-dashed border-slate-400 bg-slate-50 px-3 py-5 text-sm text-slate-700 file:mr-4 file:rounded-md file:border-0 file:bg-slate-950 file:px-4 file:py-2 file:font-semibold file:text-white hover:border-amber-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500"
                    id="source-pdf"
                    name="file"
                    onChange={(event) => {
                      setSelectedFile(event.currentTarget.files?.[0] ?? null);
                      setUploadError(null);
                      setLastResult(null);
                    }}
                    type="file"
                  />
                  <span className="font-normal leading-5 text-slate-500" id="pdf-help">
                    The server validates the filename, media type, PDF signature, and configured size limit.
                  </span>
                  <span aria-live="polite" className="font-normal text-slate-700" id="selected-pdf">
                    {selectedFile ? (
                      <>
                        Selected <strong>{selectedFile.name}</strong> ({formatBytes(selectedFile.size)})
                      </>
                    ) : (
                      "No PDF selected."
                    )}
                  </span>
                </div>

                <div className="grid gap-5 sm:grid-cols-2">
                  <label className={fieldClass} htmlFor="document-type">
                    Document type
                    <select className={inputClass} defaultValue="syllabus" id="document-type" name="document_type">
                      {documentTypes.map((type) => (
                        <option key={type.value} value={type.value}>{type.label}</option>
                      ))}
                    </select>
                  </label>

                  <label className={fieldClass} htmlFor="curriculum-version">
                    Curriculum version (optional)
                    <select className={inputClass} id="curriculum-version" name="curriculum_version_id">
                      <option value="">No curriculum link</option>
                      {activeCurricula.map((curriculum) => (
                        <option key={curriculum.id} value={curriculum.id}>
                          {curriculum.code} — {curriculum.title}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                {!activeCurricula.length && (
                  <div className="rounded-md bg-slate-100 px-3 py-2 text-sm text-slate-600">
                    <p>No active curriculum versions are available.</p>
                    <p className="mt-1">The PDF can still be uploaded without a curriculum link.</p>
                  </div>
                )}

                <div className="grid gap-5 sm:grid-cols-2">
                  <TextField className={fieldClass} name="year">
                    <Label>Year (optional)</Label>
                    <Input className={inputClass} inputMode="numeric" max={2100} min={1900} placeholder="e.g. 2025" type="number" />
                  </TextField>
                  <TextField className={fieldClass} name="paper_code">
                    <Label>Paper code (optional)</Label>
                    <Input className={inputClass} maxLength={64} placeholder="e.g. 2025-I" />
                  </TextField>
                </div>

                {uploadError && (
                  <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-900" role="alert">
                    <p className="font-semibold">Upload was not completed.</p>
                    <p className="mt-1">{uploadError.message}</p>
                    <p className="mt-2 font-mono text-xs">Error code: {uploadError.code}</p>
                  </div>
                )}

                {lastResult && (
                  <div
                    aria-live="polite"
                    className={`rounded-lg border p-4 text-sm ${
                      lastResult.deduplicated
                        ? "border-sky-300 bg-sky-50 text-sky-950"
                        : "border-emerald-300 bg-emerald-50 text-emerald-950"
                    }`}
                    role="status"
                  >
                    <p className="font-semibold">
                      {lastResult.deduplicated ? "Duplicate source reused." : "Source document uploaded."}
                    </p>
                    <p className="mt-1">
                      {lastResult.deduplicated
                        ? "The checksum matched an existing immutable source; no second copy was created."
                        : "The original PDF is preserved and its initial extraction status is shown below."}
                    </p>
                  </div>
                )}

                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-5">
                  <p className="max-w-md text-xs leading-5 text-slate-500">
                    Uploaded documents are untrusted input until extraction and human review are complete.
                  </p>
                  <Button className={primaryButton} isDisabled={uploading} type="submit">
                    {uploading ? "Uploading PDF…" : uploadError ? "Retry upload" : "Upload source document"}
                  </Button>
                </div>
              </Form>
            )}
          </section>

          <section aria-labelledby="responses-heading" className="rounded-xl border border-slate-300 bg-slate-100/70 p-5 sm:p-6">
            <p className="font-mono text-xs text-slate-500 uppercase">Persisted source catalog</p>
            <h2 className="mt-1 text-2xl font-semibold" id="responses-heading">Document status</h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              Statuses are loaded from PostgreSQL and refresh after each immutable upload or idempotent retry.
            </p>

            <div className="mt-5 grid gap-4">
              {!documents.length && (
                <div className="rounded-lg border border-dashed border-slate-400 bg-white p-6 text-center">
                  <p className="font-medium text-slate-700">No source documents uploaded yet.</p>
                  <p className="mt-1 text-sm text-slate-500">A successful upload or idempotent retry will appear here.</p>
                </div>
              )}
              {documents.map((document) => (
                <DocumentStatusCard
                  canManage={role === "admin"}
                  curriculum={curricula.find((item) => item.id === document.curriculum_version_id)}
                  document={document}
                  key={document.id}
                  onQueueExtraction={() => void queueExtraction(document)}
                />
              ))}
            </div>
          </section>
        </div>
      )}

      <section aria-labelledby="review-heading" className="mt-8 rounded-xl border border-slate-300 bg-slate-950 p-5 text-white sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div className="max-w-3xl">
            <div className="flex flex-wrap items-center gap-3">
              <p className="font-mono text-xs tracking-wide text-slate-400 uppercase">Next pipeline step</p>
              <span className="rounded-full border border-amber-300/40 bg-amber-300/10 px-2.5 py-1 text-xs font-semibold text-amber-200">
                Human gate active
              </span>
            </div>
            <h2 className="mt-2 text-2xl font-semibold" id="review-heading">Extraction review</h2>
            <p className="mt-3 leading-7 text-slate-300">
              Open an extracted source from its status card to compare immutable page/block provenance, record corrections with conflict detection, and promote reviewed content to trusted.
            </p>
          </div>
          <Button
            aria-describedby="review-pending-detail"
            className="inline-flex min-h-11 items-center justify-center rounded-lg border border-white/20 bg-white/10 px-5 py-2.5 text-sm font-semibold text-slate-400 disabled:cursor-not-allowed"
            isDisabled
          >
            Choose an extracted source
          </Button>
        </div>
        <p className="sr-only" id="review-pending-detail">Choose an extracted source from the document status list.</p>
      </section>
    </div>
  );
}

function DocumentStatusCard({
  canManage,
  curriculum,
  document,
  onQueueExtraction,
}: {
  canManage: boolean;
  curriculum: Curriculum | undefined;
  document: SourceDocument;
  onQueueExtraction: () => void;
}) {
  const documentType = documentTypes.find((type) => type.value === document.document_type)?.label ?? document.document_type;
  return (
    <article className="rounded-lg border border-slate-300 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-xs text-slate-500 uppercase">{documentType}</p>
          <h3 className="mt-1 break-words font-semibold" title={document.original_filename}>{document.original_filename}</h3>
        </div>
        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
          document.deduplicated ? "bg-sky-100 text-sky-900" : "bg-emerald-100 text-emerald-900"
        }`}>
          {document.deduplicated ? "Duplicate response" : "New immutable source"}
        </span>
      </div>

      <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-4 text-sm">
        <div>
          <dt className="text-xs text-slate-500">Extraction status</dt>
          <dd className="mt-1 font-semibold">{extractionLabels[document.extraction_status]}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">File size</dt>
          <dd className="mt-1 font-semibold">{formatBytes(document.size_bytes)}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Year</dt>
          <dd className="mt-1 font-semibold">{document.year ?? "Not supplied"}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Paper code</dt>
          <dd className="mt-1 break-words font-semibold">{document.paper_code ?? "Not supplied"}</dd>
        </div>
      </dl>

      <div className="mt-5 border-t border-slate-200 pt-4 text-xs text-slate-600">
        <p>
          <span className="font-semibold text-slate-700">Curriculum:</span>{" "}
          {curriculum ? `${curriculum.code} — ${curriculum.title}` : "Not linked"}
        </p>
        <p className="mt-2 break-all">
          <span className="font-semibold text-slate-700">SHA-256:</span>{" "}
          <code>{document.checksum_sha256}</code>
        </p>
        <p className="mt-2 break-all">
          <span className="font-semibold text-slate-700">Source ID:</span>{" "}
          <code>{document.id}</code>
        </p>
      </div>

      <div className="mt-5 flex flex-wrap gap-2 border-t border-slate-200 pt-4">
        {canManage && ["uploaded", "failed"].includes(document.extraction_status) && (
          <button
            aria-label={`Queue extraction for ${document.original_filename}`}
            className={secondaryButton}
            onClick={onQueueExtraction}
            type="button"
          >
            Queue extraction
          </button>
        )}
        {["extracted", "in_review", "trusted"].includes(document.extraction_status) && (
          <Link className={secondaryButton} href={`/admin/documents/${document.id}`}>
            Open extraction review
          </Link>
        )}
      </div>
    </article>
  );
}
