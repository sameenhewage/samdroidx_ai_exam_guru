import type { components } from "@exam-guru/api-client";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import axe from "axe-core";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { SourceEvaluationReferenceEditor } from "./source-evaluation-reference-editor";

type Preview = components["schemas"]["EvaluationPreviewResponse"];
type Reference = components["schemas"]["EvaluationReferenceResponse"];
const preview: Preview = {
  id: "00000000-0000-0000-0000-000000000971",
  benchmark_id: "00000000-0000-0000-0000-000000000972",
  document_id: "00000000-0000-0000-0000-000000000973",
  document_title: "Source comparison.pdf",
  page_number: 1,
  source_checksum_sha256: "a".repeat(64),
  image_sha256: "b".repeat(64),
  image_width: 800,
  image_height: 1200,
  preview_url:
    "/api/v1/admin/source-benchmarks/00000000-0000-0000-0000-000000000972/evaluation-previews/00000000-0000-0000-0000-000000000971/image",
  language: "en",
  reference_version: 0,
  latest_reference: null,
  evaluation_only: true,
};
function reference(version: number, text = "Saved human text"): Reference {
  return {
    id: "00000000-0000-0000-0000-000000000974",
    preview_id: preview.id,
    benchmark_id: preview.benchmark_id,
    document_id: preview.document_id,
    page_number: 1,
    version,
    text,
    normalized_text: text,
    text_sha256: "c".repeat(64),
    normalized_sha256: "c".repeat(64),
    blank_reference: text === "",
    reviewer_id: "00000000-0000-0000-0000-000000000975",
    reviewed_at: "2026-09-12T00:00:00Z",
    reason: "Compared with original",
    evaluation_only: true,
  };
}
async function ready(label = "Original comparison page 1") {
  const image = screen.getByRole("img", { name: label });
  Object.defineProperty(image, "naturalWidth", {
    configurable: true,
    value: 800,
  });
  Object.defineProperty(image, "naturalHeight", {
    configurable: true,
    value: 1200,
  });
  await act(async () => fireEvent.load(image));
}
function edit() {
  fireEvent.change(screen.getByLabelText("Reference text"), {
    target: { value: "My reviewed reference" },
  });
  fireEvent.change(screen.getByLabelText("Reason for reference"), {
    target: { value: "Compared the original" },
  });
  fireEvent.click(
    screen.getByRole("checkbox", { name: /I compared this reference/ }),
  );
}
beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

it("keeps a conflict blocked when loading the latest reference fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request =
        input instanceof Request ? input : new Request(input, init);
      return Response.json(
        { detail: { code: "evaluation_reference_version_conflict" } },
        { status: request.method === "POST" ? 409 : 503 },
      );
    }),
  );
  render(
    <SourceEvaluationReferenceEditor
      preview={preview}
      onClose={vi.fn()}
      onSaved={vi.fn()}
    />,
  );
  await ready();
  edit();
  fireEvent.click(
    screen.getByRole("button", { name: "Save evaluation reference" }),
  );
  await screen.findByText(/Another reference was saved/);
  fireEvent.click(
    screen.getByRole("button", { name: "Load latest reference" }),
  );
  await screen.findByText(/The reference was not saved/);
  expect(screen.getByLabelText("Reference text")).toHaveValue(
    "My reviewed reference",
  );
  expect(
    screen.getByRole("button", { name: "Save evaluation reference" }),
  ).toBeDisabled();
});

it("requires explicit rebasing and comparison again after a conflict", async () => {
  const requests: Request[] = [];
  let writes = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request =
        input instanceof Request ? input : new Request(input, init);
      requests.push(request.clone());
      if (request.method === "GET") return Response.json([reference(2)]);
      writes += 1;
      if (writes === 1)
        return Response.json(
          { detail: { code: "evaluation_reference_version_conflict" } },
          { status: 409 },
        );
      const body = await request.json();
      expect(body.expected_version).toBe(2);
      return Response.json(
        { ...reference(3, body.text), reason: body.reason },
        { status: 201 },
      );
    }),
  );
  const saved = vi.fn();
  render(
    <SourceEvaluationReferenceEditor
      preview={preview}
      onClose={vi.fn()}
      onSaved={saved}
    />,
  );
  await ready();
  edit();
  fireEvent.click(
    screen.getByRole("button", { name: "Save evaluation reference" }),
  );
  await screen.findByText(/Another reference was saved/);
  fireEvent.click(
    screen.getByRole("button", { name: "Load latest reference" }),
  );
  await screen.findByRole("region", { name: "Latest saved reference" });
  fireEvent.click(
    screen.getByRole("button", { name: "Keep my text and use latest version" }),
  );
  expect(screen.getByLabelText("Reference text")).toHaveValue(
    "My reviewed reference",
  );
  expect(
    screen.getByRole("checkbox", { name: /I compared this reference/ }),
  ).not.toBeChecked();
  expect(
    screen.getByRole("button", { name: "Save evaluation reference" }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole("checkbox", { name: /I compared this reference/ }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Save evaluation reference" }),
  );
  await screen.findByText("Reference saved for evaluation only.");
  expect(saved).toHaveBeenCalledTimes(1);
  expect(requests.filter((request) => request.method === "POST")).toHaveLength(
    2,
  );
});

it("preserves unsaved changes until discard is explicitly chosen", async () => {
  const close = vi.fn();
  render(
    <SourceEvaluationReferenceEditor
      preview={preview}
      onClose={close}
      onSaved={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("Reference text"), {
    target: { value: "Keep this text" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Back to review set" }));
  expect(screen.getByRole("alertdialog")).toBeVisible();
  expect(close).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
  expect(screen.getByLabelText("Reference text")).toHaveValue("Keep this text");
  fireEvent.click(screen.getByRole("button", { name: "Back to review set" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Discard unsaved changes" }),
  );
  expect(close).toHaveBeenCalledTimes(1);
});

it("does not allow saving with a missing or mismatched original image", async () => {
  render(
    <SourceEvaluationReferenceEditor
      preview={preview}
      onClose={vi.fn()}
      onSaved={vi.fn()}
    />,
  );
  const image = screen.getByRole("img", { name: "Original comparison page 1" });
  Object.defineProperty(image, "naturalWidth", {
    configurable: true,
    value: 2,
  });
  Object.defineProperty(image, "naturalHeight", {
    configurable: true,
    value: 2,
  });
  await act(async () => fireEvent.load(image));
  expect(screen.getByRole("alert")).toHaveTextContent(
    /image could not be verified/,
  );
  expect(
    screen.getByRole("checkbox", { name: /I compared this reference/ }),
  ).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Save evaluation reference" }),
  ).toBeDisabled();
});

it("uses Sinhala presentation without changing reference text or persisting an automatic preference", async () => {
  render(
    <SourceEvaluationReferenceEditor
      preview={{ ...preview, language: "si" }}
      onClose={vi.fn()}
      onSaved={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("යොමු පෙළ")).toHaveValue("");
  expect(localStorage.getItem("exam-guru:review-language:v1")).toBeNull();
  fireEvent.change(screen.getByLabelText("යොමු පෙළ"), {
    target: { value: "ශ්‍රී ලංකාව" },
  });
  fireEvent.click(screen.getByRole("button", { name: "English" }));
  expect(screen.getByLabelText("Reference text")).toHaveValue("ශ්‍රී ලංකාව");
  expect(localStorage.getItem("exam-guru:review-language:v1")).toBe("en");
});

it("retries a failed original image without discarding the human draft", async () => {
  render(
    <SourceEvaluationReferenceEditor
      preview={preview}
      onClose={vi.fn()}
      onSaved={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("Reference text"), {
    target: { value: "Keep my transcription" },
  });
  fireEvent.error(
    screen.getByRole("img", { name: "Original comparison page 1" }),
  );
  expect(
    screen.getByRole("button", { name: "Save evaluation reference" }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Try image again" }));
  await ready();
  expect(screen.getByLabelText("Reference text")).toHaveValue(
    "Keep my transcription",
  );
  expect(
    screen.getByRole("checkbox", { name: /I compared this reference/ }),
  ).not.toBeChecked();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("has accessible original comparison and explicit human reference controls", async () => {
  const view = render(
    <SourceEvaluationReferenceEditor
      preview={preview}
      onClose={vi.fn()}
      onSaved={vi.fn()}
    />,
  );
  await ready();
  const result = await axe.run(view.container);
  expect(result.violations).toEqual([]);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Save evaluation reference" }),
    ).toBeDisabled(),
  );
});
