import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ExtractionReviewStudio } from "./extraction-review-studio";

const documentId = "00000000-0000-0000-0000-000000000021";
const pageId = "00000000-0000-0000-0000-000000000022";

function requestOf(input: RequestInfo | URL, init?: RequestInit) {
  return input instanceof Request ? input : new Request(input, init);
}

function document(status: "extracted" | "in_review" | "trusted") {
  return {
    checksum_sha256: "a".repeat(64),
    content_type: "application/pdf",
    created_at: "2026-08-23T12:30:00Z",
    curriculum_version_id: null,
    deduplicated: false,
    document_type: "syllabus",
    extracted_block_count: 1,
    extracted_character_count: 12,
    extracted_page_count: 1,
    extraction_attempt_count: 1,
    extraction_completed_at: "2026-08-23T12:31:00Z",
    extraction_failure_code: null,
    extraction_started_at: "2026-08-23T12:30:30Z",
    extraction_status: status,
    extractor: "pymupdf",
    extractor_version: "1.28.2",
    id: documentId,
    native_text_page_ratio: 1,
    needs_ocr: false,
    original_filename: "grade-5-source.pdf",
    paper_code: null,
    size_bytes: 100,
    year: null,
  };
}

const sourcePage = {
  block_count: 1,
  character_count: 12,
  created_at: "2026-08-23T12:31:00Z",
  extractor: "pymupdf",
  extractor_version: "1.28.2",
  id: pageId,
  page_number: 1,
  raw_text: "Original text",
  reviewed_text: null as string | null,
  source_document_id: documentId,
  updated_at: "2026-08-23T12:31:00Z",
  version: 0,
};

const sourceBlock = {
  bbox: [0, 0, 10, 10],
  character_count: 12,
  created_at: "2026-08-23T12:31:00Z",
  extractor: "pymupdf",
  extractor_version: "1.28.2",
  id: "00000000-0000-0000-0000-000000000023",
  page_number: 1,
  raw_text: "Original text",
  reading_order: 0,
  reviewed_text: null as string | null,
  source_document_id: documentId,
  source_page_id: pageId,
  updated_at: "2026-08-23T12:31:00Z",
  version: 0,
};

afterEach(() => vi.unstubAllGlobals());

it("supports compare, correction, and trusted promotion", async () => {
  let status: "extracted" | "in_review" | "trusted" = "extracted";
  let page = sourcePage;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = requestOf(input, init);
      if (request.method === "GET" && request.url.endsWith("/source-documents")) {
        return Response.json([document(status)]);
      }
      if (request.method === "GET" && request.url.endsWith("/pages")) {
        return Response.json([page]);
      }
      if (request.method === "GET" && request.url.endsWith("/blocks")) {
        return Response.json([sourceBlock]);
      }
      if (request.method === "POST" && request.url.endsWith("/review")) {
        status = "in_review";
        return Response.json(document(status));
      }
      if (request.method === "PATCH" && request.url.endsWith("/pages/1")) {
        page = { ...page, reviewed_text: "Corrected text", version: 1 };
        return Response.json(page);
      }
      if (request.method === "POST" && request.url.endsWith("/trust")) {
        status = "trusted";
        return Response.json(document(status));
      }
      return Response.json({ detail: { code: "unexpected_request" } }, { status: 500 });
    }),
  );

  render(<ExtractionReviewStudio documentId={documentId} role="admin" />);

  expect((await screen.findAllByText("Original text", { selector: "pre" }))[0]).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Begin human review" }));
  const reviewedText = await screen.findByLabelText("Reviewed page 1 text");
  fireEvent.change(reviewedText, { target: { value: "Corrected text" } });
  fireEvent.click(screen.getByRole("button", { name: "Save page 1 correction" }));
  expect(await screen.findByText("Page 1 correction saved.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Mark source trusted" }));
  expect((await screen.findAllByText("Trusted source"))[0]).toBeInTheDocument();
});
