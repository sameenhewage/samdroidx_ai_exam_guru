import { fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DocumentsStudio } from "./documents-studio";

const curriculum = {
  active: true,
  code: "G5-SI-2026",
  created_at: "2026-08-23T00:00:00Z",
  exam_configuration_id: "00000000-0000-0000-0000-000000000011",
  id: "00000000-0000-0000-0000-000000000013",
  medium_id: "00000000-0000-0000-0000-000000000012",
  title: "Grade 5 Sinhala 2026",
  updated_at: "2026-08-23T00:00:00Z",
};

const uploadedDocument = {
  checksum_sha256: "a".repeat(64),
  content_type: "application/pdf",
  created_at: "2026-08-23T12:30:00Z",
  curriculum_version_id: curriculum.id,
  deduplicated: false,
  document_type: "past_paper" as const,
  extraction_status: "uploaded" as const,
  id: "00000000-0000-0000-0000-000000000021",
  object_key: `sources/aa/${"a".repeat(64)}.pdf`,
  original_filename: "grade-5-2025-paper.pdf",
  paper_code: "2025-I",
  size_bytes: 18,
  year: 2025,
};

function asRequest(input: RequestInfo | URL, init?: RequestInit) {
  return input instanceof Request ? input : new Request(input, init);
}

function successfulApi(
  options: { deduplicated?: boolean; documents?: object[]; status?: number } = {},
) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    if (request.method === "GET" && request.url.endsWith("/curriculum-versions")) {
      return Response.json([curriculum]);
    }
    if (request.method === "GET" && request.url.endsWith("/source-documents")) {
      return Response.json(options.documents ?? []);
    }
    if (request.method === "POST" && request.url.endsWith("/source-documents")) {
      return Response.json(
        { ...uploadedDocument, deduplicated: options.deduplicated ?? false },
        { status: options.status ?? 201 },
      );
    }
    return Response.json({ detail: { code: "unexpected_request" } }, { status: 500 });
  });
}

async function selectValidPdf() {
  await screen.findByRole("button", { name: "Upload source document" });
  const file = new File(["%PDF-1.7\nfixture"], uploadedDocument.original_filename, {
    type: "application/pdf",
  });
  fireEvent.change(screen.getByLabelText("PDF file"), { target: { files: [file] } });
  return file;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("DocumentsStudio", () => {
  it("renders loading, empty, and extraction-review guidance", async () => {
    let resolveRequest!: (response: Response) => void;
    const pendingRequest = new Promise<Response>((resolve) => {
      resolveRequest = resolve;
    });
    vi.stubGlobal("fetch", vi.fn(async () => (await pendingRequest).clone()));

    render(<DocumentsStudio role="admin" />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading document workspace");
    resolveRequest(Response.json([]));

    expect(await screen.findByText("No source documents uploaded yet.")).toBeInTheDocument();
    expect(screen.getByText("No active curriculum versions are available.")).toBeInTheDocument();
    expect(screen.getByText("Human gate active")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose an extracted source" })).toBeDisabled();
  });

  it("loads persisted document statuses from the source catalog", async () => {
    vi.stubGlobal("fetch", successfulApi({ documents: [uploadedDocument] }));

    render(<DocumentsStudio role="reviewer" />);

    expect(await screen.findByText(uploadedDocument.original_filename)).toBeInTheDocument();
    expect(screen.getByText("Uploaded")).toBeInTheDocument();
  });

  it("queues native extraction without running it in the browser", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = asRequest(input, init);
      if (request.method === "GET" && request.url.endsWith("/curriculum-versions")) {
        return Response.json([curriculum]);
      }
      if (request.method === "GET" && request.url.endsWith("/source-documents")) {
        return Response.json([uploadedDocument]);
      }
      if (request.method === "POST" && request.url.endsWith("/extract")) {
        return Response.json(
          {
            document_id: uploadedDocument.id,
            message_id: "message-1",
            status: "uploaded",
          },
          { status: 202 },
        );
      }
      return Response.json({ detail: { code: "unexpected_request" } }, { status: 500 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DocumentsStudio role="admin" />);

    const button = await screen.findByRole("button", {
      name: `Queue extraction for ${uploadedDocument.original_filename}`,
    });
    fireEvent.click(button);

    expect(await screen.findByText("Native extraction queued.")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => asRequest(input).url.endsWith("/extract"))).toBe(
      true,
    );
  });

  it("uploads PDF metadata through the same-origin generated endpoint and shows status", async () => {
    let uploadRequest: Request | undefined;
    const appendSpy = vi.spyOn(FormData.prototype, "append");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = asRequest(input, init);
      if (request.method === "GET") {
        return Response.json(request.url.endsWith("/curriculum-versions") ? [curriculum] : []);
      }
      uploadRequest = request;
      return Response.json(uploadedDocument, { status: 201 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DocumentsStudio role="admin" />);

    const file = await selectValidPdf();
    fireEvent.change(screen.getByLabelText("Document type"), { target: { value: "past_paper" } });
    fireEvent.change(screen.getByLabelText("Curriculum version (optional)"), {
      target: { value: curriculum.id },
    });
    fireEvent.change(screen.getByLabelText("Year (optional)"), { target: { value: "2025" } });
    fireEvent.change(screen.getByLabelText("Paper code (optional)"), {
      target: { value: "2025-I" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Upload source document" }));

    expect(await screen.findByText("Source document uploaded.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: uploadedDocument.original_filename })).toBeInTheDocument();
    expect(screen.getByText("Uploaded", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText("New immutable source")).toBeInTheDocument();

    expect(uploadRequest?.url).toBe(`${window.location.origin}/api/v1/admin/source-documents`);
    expect(uploadRequest?.headers.get("authorization")).toBeNull();
    expect(appendSpy).toHaveBeenCalledWith("file", file);
    expect(appendSpy).toHaveBeenCalledWith("document_type", "past_paper");
    expect(appendSpy).toHaveBeenCalledWith("curriculum_version_id", curriculum.id);
    expect(appendSpy).toHaveBeenCalledWith("year", "2025");
    expect(appendSpy).toHaveBeenCalledWith("paper_code", "2025-I");
  });

  it("makes an idempotent duplicate response prominent", async () => {
    vi.stubGlobal("fetch", successfulApi({ deduplicated: true, status: 200 }));
    render(<DocumentsStudio role="admin" />);

    await selectValidPdf();
    fireEvent.click(screen.getByRole("button", { name: "Upload source document" }));

    expect(await screen.findByText("Duplicate source reused.")).toBeInTheDocument();
    expect(
      screen.getByText("The checksum matched an existing immutable source; no second copy was created."),
    ).toBeInTheDocument();
    expect(screen.getByText("Duplicate response")).toBeInTheDocument();
  });

  it("shows API errors and lets an admin retry the preserved selection", async () => {
    let uploadAttempts = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = asRequest(input, init);
      if (request.method === "GET") {
        return Response.json(request.url.endsWith("/curriculum-versions") ? [curriculum] : []);
      }
      uploadAttempts += 1;
      if (uploadAttempts === 1) {
        return Response.json(
          { detail: { code: "invalid_pdf_signature" } },
          { status: 422 },
        );
      }
      return Response.json(uploadedDocument, { status: 201 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DocumentsStudio role="admin" />);

    await selectValidPdf();
    fireEvent.click(screen.getByRole("button", { name: "Upload source document" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The selected file did not contain a valid PDF signature.");
    expect(alert).toHaveTextContent("invalid_pdf_signature");
    expect(screen.getByText(uploadedDocument.original_filename, { selector: "strong" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry upload" }));
    expect(await screen.findByText("Source document uploaded.")).toBeInTheDocument();
    expect(uploadAttempts).toBe(2);
  });

  it("shows a retryable workspace error", async () => {
    let curriculumAttempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        curriculumAttempts += 1;
        if (curriculumAttempts === 1) {
          return Response.json({ detail: { code: "service_unavailable" } }, { status: 503 });
        }
        return Response.json([]);
      }),
    );
    render(<DocumentsStudio role="admin" />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Document metadata could not be loaded.");
    fireEvent.click(screen.getByRole("button", { name: "Retry loading metadata" }));

    expect(await screen.findByText("No active curriculum versions are available.")).toBeInTheDocument();
    expect(curriculumAttempts).toBe(4);
  });

  it("renders an explicit permission state without upload controls for reviewers", async () => {
    vi.stubGlobal("fetch", successfulApi());
    render(<DocumentsStudio role="reviewer" />);

    expect(await screen.findByText("Upload permission required")).toBeInTheDocument();
    expect(
      screen.getByText("Reviewer access is read-only for source documents."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("PDF file")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Upload source document" })).not.toBeInTheDocument();
  });

  it("rejects a non-PDF before sending untrusted content", async () => {
    const fetchMock = successfulApi();
    vi.stubGlobal("fetch", fetchMock);
    render(<DocumentsStudio role="admin" />);

    await screen.findByRole("button", { name: "Upload source document" });
    const file = new File(["plain text"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("PDF file"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload source document" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a PDF file with a .pdf name.");
    expect(
      fetchMock.mock.calls.filter(([input, init]) => asRequest(input, init).method === "POST"),
    ).toHaveLength(0);
  });

  it("has no automated accessibility violations", async () => {
    vi.stubGlobal("fetch", successfulApi());
    const { container } = render(<DocumentsStudio role="admin" />);
    await screen.findByRole("button", { name: "Upload source document" });

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});
