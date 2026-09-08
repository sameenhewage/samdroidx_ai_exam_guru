import type { components } from "@exam-guru/api-client";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import axe from "axe-core";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SourcePageReviewWorkspace } from "./source-page-review-workspace";

type Workspace = components["schemas"]["PageReviewWorkspaceResponse"];
type Page = components["schemas"]["PageReviewView"];
type ReadJob = components["schemas"]["SourceReadJobResponse"];

const documentId = "00000000-0000-0000-0000-000000000871";
const candidateId = "00000000-0000-0000-0000-000000000872";
const secondCandidateId = "00000000-0000-0000-0000-000000000873";
const benchmarkId = "00000000-0000-0000-0000-000000000874";
const unicodeText =
  "ශ්‍රී ලංකාව — ක්‍රියා හා ප්‍රශ්න\nதமிழ் க்ஷேத்திரம்: ½ × 2 = 1; x² ≤ 4; √9 = 3\n  English ABC + π ÷ 2 ≠ Ω; 1 < 2 & 3 > 2\n<script>not executable</script>";
const workspacePath = `/api/v1/admin/materials/${documentId}/review-workspace`;
const pagePath = `/api/v1/admin/materials/${documentId}/pages`;

function workspace(
  pageNumber = 1,
  pageOverrides: Partial<Page> = {},
  overrides: Partial<Workspace> = {},
): Workspace {
  return {
    document_id: documentId,
    document_title: "Teacher's original source.pdf",
    language: "en",
    metadata_review_required: false,
    source_active: true,
    ready_for_ai: false,
    progress: {
      total_pages: 371,
      processed_pages: 68,
      verified_pages: 2,
      excluded_pages: 1,
      flagged_pages: 17,
      remaining_pages: 368,
    },
    page: {
      page_number: pageNumber,
      state: "needs_review",
      version: 7,
      candidate_id: candidateId,
      system_text: unicodeText,
      language: "en",
      can_confirm: true,
      risk_codes: [],
      preview_url: `/api/v1/admin/source-documents/${documentId}/pages/${pageNumber}/preview?source=original`,
      provenance: {
        engine: "fixture-engine",
        source_checksum_sha256: "a".repeat(64),
      },
      diagnostics: { confidence: 0.99, raw_native_text: "wkd ñ\uFFFD\uE001" },
      history: [
        {
          id: candidateId,
          method: "native",
          created_at: "2026-09-06T10:00:00Z",
          text_sha256: "b".repeat(64),
          is_current: true,
        },
      ],
      ...pageOverrides,
    },
    previous_flagged_page: pageNumber > 17 ? 17 : null,
    next_flagged_page: pageNumber < 23 ? 23 : null,
    ...overrides,
  };
}

function emptyWorkspace(): Workspace {
  return workspace(
    1,
    {},
    {
      page: null,
      progress: {
        total_pages: 0,
        processed_pages: 0,
        verified_pages: 0,
        excluded_pages: 0,
        flagged_pages: 0,
        remaining_pages: 0,
      },
      previous_flagged_page: null,
      next_flagged_page: null,
    },
  );
}

const readJob: ReadJob = {
  id: "00000000-0000-0000-0000-000000000875",
  document_id: documentId,
  page_number: null,
  next_page: 1,
  status: "queued",
  failure_code: null,
  version: 0,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function fixtureApi(
  getWorkspace: (pageNumber: number) => Workspace = (pageNumber) =>
    workspace(pageNumber),
  handle?: (request: Request) => Response | Promise<Response> | undefined,
) {
  const requests: Request[] = [];
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const request =
        input instanceof Request ? input : new Request(input, init);
      requests.push(request.clone());
      const handled = handle?.(request);
      if (handled) return handled;
      const url = new URL(request.url);
      if (request.method === "GET" && url.pathname === workspacePath) {
        return Response.json(
          getWorkspace(Number(url.searchParams.get("page_number"))),
        );
      }
      return Response.json(
        { detail: { code: "unexpected_request" } },
        { status: 500 },
      );
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return {
    requests,
    mutations: () => requests.filter((request) => request.method !== "GET"),
    pages: () =>
      requests
        .filter((request) => request.method === "GET")
        .map((request) =>
          Number(new URL(request.url).searchParams.get("page_number")),
        ),
  };
}

async function renderWorkspace(
  getWorkspace?: (pageNumber: number) => Workspace,
  handle?: (request: Request) => Response | Promise<Response> | undefined,
  props: Partial<Parameters<typeof SourcePageReviewWorkspace>[0]> = {},
) {
  const api = fixtureApi(getWorkspace, handle);
  const view = render(
    <SourcePageReviewWorkspace
      documentId={documentId}
      role="admin"
      {...props}
    />,
  );
  await screen.findByRole("region", { name: /^(Original page|මුල් පිටුව)$/ });
  return { ...api, ...view };
}

async function loadPreview(pageNumber = 1) {
  await act(async () => {
    fireEvent.load(
      screen.getByRole("img", { name: `Original page ${pageNumber}` }),
    );
  });
}

function systemText() {
  const text = within(
    screen.getByRole("region", { name: "System-read text" }),
  ).getByTestId("system-page-text");
  return text;
}

function count(label: string, value: number) {
  const term = within(
    screen.getByRole("region", { name: "Review progress" }),
  ).getByText(label);
  expect(term.parentElement).toHaveTextContent(
    new RegExp(`${label}\\s*${value}$`),
  );
}

const reviewLanguageLabel = "Review language / භාෂාව";
const reviewLanguageKey = "exam-guru:review-language:v1";

beforeEach(() => localStorage.clear());

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("review language preference", () => {
  it("follows detected page languages across navigation and refresh without persisting an automatic English default", async () => {
    const api = await renderWorkspace((pageNumber) => {
      const language = pageNumber === 1 ? "en" : "si";
      return workspace(pageNumber, { language }, { language });
    });
    expect(localStorage.getItem(reviewLanguageKey)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByRole("img", { name: "මුල් පිටුව 2" });
    expect(screen.getByTestId("source-page-workspace")).toHaveAttribute(
      "lang",
      "si",
    );
    expect(
      screen.getByRole("heading", { name: "පද්ධතිය කියවූ පෙළ පරීක්ෂා කරන්න" }),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "පිටුවේ තත්ත්වය යාවත්කාලීන කරන්න" }),
    );
    await screen.findByRole("img", { name: "මුල් පිටුව 2" });
    expect(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
    ).toHaveValue("si");
    expect(screen.getByTestId("system-page-text")).toHaveAttribute(
      "lang",
      "si",
    );
    expect(screen.getByTestId("system-page-text").textContent).toBe(
      unicodeText,
    );
    expect(localStorage.getItem(reviewLanguageKey)).toBeNull();
    expect(api.pages()).toEqual([1, 2, 2]);
    fireEvent.click(screen.getByRole("button", { name: "පෙර පිටුව" }));
    await screen.findByRole("img", { name: "Original page 1" });
    expect(api.mutations()).toEqual([]);
  });

  it.each([
    { language: "si" },
    { language: "si-LK" },
    { language: "Sinhala" },
    { language: "mul", diagnostics: { languages: ["en", "si"] } },
    { language: "und", diagnostics: { detected_language: "si" } },
  ] satisfies Partial<Page>[])(
    "defaults to Sinhala from detected page signals: %j",
    async (page) => {
      const api = await renderWorkspace((pageNumber) =>
        workspace(pageNumber, page),
      );
      expect(screen.getByTestId("source-page-workspace")).toHaveAttribute(
        "lang",
        "si",
      );
      expect(
        screen.getByRole("combobox", { name: reviewLanguageLabel }),
      ).toHaveValue("si");
      expect(localStorage.getItem(reviewLanguageKey)).toBeNull();
      expect(api.mutations()).toEqual([]);
    },
  );

  it("uses the Sinhala source language when a page has no useful language detection", async () => {
    await renderWorkspace((pageNumber) =>
      workspace(
        pageNumber,
        { language: "und", diagnostics: { languages: [null, 42] } },
        { language: "si" },
      ),
    );
    expect(screen.getByTestId("source-page-workspace")).toHaveAttribute(
      "lang",
      "si",
    );
  });

  it("preserves a teacher's explicit English choice on Sinhala pages across remounts", async () => {
    const api = await renderWorkspace((pageNumber) =>
      workspace(pageNumber, { language: "si" }, { language: "si" }),
    );
    expect(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
    ).toHaveValue("si");
    fireEvent.change(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
      {
        target: { value: "en" },
      },
    );
    expect(localStorage.getItem(reviewLanguageKey)).toBe("en");
    api.unmount();
    render(<SourcePageReviewWorkspace documentId={documentId} role="admin" />);
    await screen.findByRole("img", { name: "Original page 1" });
    expect(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
    ).toHaveValue("en");
    expect(systemText()).toHaveAttribute("lang", "si");
    expect(api.mutations()).toEqual([]);
  });

  it("remembers Sinhala across English and Tamil source pages and review-session remounts", async () => {
    const api = await renderWorkspace((pageNumber) => {
      const language = pageNumber === 1 ? "en" : "ta";
      return workspace(pageNumber, { language }, { language });
    });
    fireEvent.change(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
      {
        target: { value: "si" },
      },
    );
    expect(localStorage.getItem(reviewLanguageKey)).toBe("si");
    expect(screen.getByTestId("source-page-workspace")).toHaveAttribute(
      "lang",
      "si",
    );
    expect(screen.getByTestId("system-page-text")).toHaveAttribute(
      "lang",
      "en",
    );
    fireEvent.click(screen.getByRole("button", { name: "ඊළඟ පිටුව" }));
    await screen.findByRole("img", { name: "මුල් පිටුව 2" });
    expect(screen.getByTestId("system-page-text")).toHaveAttribute(
      "lang",
      "ta",
    );
    expect(screen.getByTestId("system-page-text").textContent).toBe(
      unicodeText,
    );
    api.unmount();
    render(
      <SourcePageReviewWorkspace
        documentId={documentId}
        initialPageNumber={2}
        role="reviewer"
      />,
    );
    await screen.findByRole("img", { name: "මුල් පිටුව 2" });
    expect(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
    ).toHaveValue("si");
    expect(
      screen.getByRole("button", { name: "පෙළ නිවැරදියි" }),
    ).toBeDisabled();
    fireEvent.change(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
      {
        target: { value: "en" },
      },
    );
    expect(screen.getByRole("img", { name: "Original page 2" })).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Text is correct" }),
    ).toBeDisabled();
    expect(localStorage.getItem(reviewLanguageKey)).toBe("en");
    expect(api.mutations()).toEqual([]);
  });

  it("does not reset the image, zoom, unsaved Unicode correction or reason when switching language", async () => {
    localStorage.setItem(reviewLanguageKey, "en");
    const api = await renderWorkspace((pageNumber) =>
      workspace(pageNumber, { language: "si" }),
    );
    await loadPreview();
    const original = screen.getByRole("img", { name: "Original page 1" });
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    fireEvent.click(screen.getByRole("button", { name: "Correct the text" }));
    const draft = `${unicodeText}\nUnconfirmed correction`;
    fireEvent.change(screen.getByRole("textbox", { name: "Correction" }), {
      target: { value: draft },
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Reason for correction" }),
      {
        target: { value: "Original-page comparison in progress" },
      },
    );
    fireEvent.change(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
      {
        target: { value: "si" },
      },
    );
    expect(screen.getByRole("textbox", { name: "නිවැරදි කළ පෙළ" })).toHaveValue(
      draft,
    );
    expect(
      screen.getByRole("textbox", { name: "නිවැරදි කිරීමට හේතුව" }),
    ).toHaveValue("Original-page comparison in progress");
    expect(screen.getByRole("img", { name: "මුල් පිටුව 1" })).toBe(original);
    expect(screen.getByLabelText("පිටුවේ විශාලත්වය")).toHaveTextContent("125%");
    expect(
      screen.getByRole("button", { name: "නිවැරදි කළ පෙළ සුරකින්න" }),
    ).toBeEnabled();
    fireEvent.change(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
      {
        target: { value: "en" },
      },
    );
    expect(screen.getByRole("textbox", { name: "Correction" })).toHaveValue(
      draft,
    );
    expect(screen.getByRole("textbox", { name: "Correction" })).toHaveAttribute(
      "lang",
      "si",
    );
    expect(
      screen.getByRole("button", { name: "Save correction" }),
    ).toBeEnabled();
    expect(api.pages()).toEqual([1]);
    expect(api.mutations()).toEqual([]);
  });

  it("keeps a language choice made while the next page request is pending", async () => {
    const response = deferred<Response>();
    const api = await renderWorkspace(undefined, (request) => {
      if (new URL(request.url).searchParams.get("page_number") === "2")
        return response.promise;
    });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(api.pages()).toEqual([1, 2]));
    fireEvent.change(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
      {
        target: { value: "si" },
      },
    );
    expect(screen.getByText("පිටුව පූරණය වෙමින් පවතී…")).toBeVisible();
    await act(async () => response.resolve(Response.json(workspace(2))));
    expect(screen.getByRole("img", { name: "මුල් පිටුව 2" })).toBeVisible();
    expect(screen.getByTestId("source-page-workspace")).toHaveAttribute(
      "lang",
      "si",
    );
    expect(screen.getByTestId("system-page-text")).toHaveAttribute(
      "lang",
      "en",
    );
    expect(api.mutations()).toEqual([]);
  });

  it.each(["", "ta", "unknown", "<script>si</script>"])(
    "ignores an unsupported saved preference %s and uses the detected source language",
    async (saved) => {
      localStorage.setItem(reviewLanguageKey, saved);
      await renderWorkspace((pageNumber) =>
        workspace(pageNumber, { language: "si" }, { language: "si" }),
      );
      expect(
        screen.getByRole("combobox", { name: reviewLanguageLabel }),
      ).toHaveValue("si");
      expect(screen.getByTestId("source-page-workspace")).toHaveAttribute(
        "lang",
        "si",
      );
      expect(screen.getByTestId("system-page-text")).toHaveAttribute(
        "lang",
        "si",
      );
    },
  );

  it("still changes language when browser preference storage is unavailable", async () => {
    for (const method of ["getItem", "setItem"] as const) {
      vi.spyOn(Storage.prototype, method).mockImplementation(() => {
        throw new DOMException("Storage unavailable", "SecurityError");
      });
    }
    const api = await renderWorkspace();
    fireEvent.change(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
      {
        target: { value: "si" },
      },
    );
    expect(
      screen.getByRole("heading", { name: "පද්ධතිය කියවූ පෙළ පරීක්ෂා කරන්න" }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "ඊළඟ පිටුව" }));
    await screen.findByRole("img", { name: "මුල් පිටුව 2" });
    fireEvent.change(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
      {
        target: { value: "en" },
      },
    );
    expect(screen.getByRole("img", { name: "Original page 2" })).toBeVisible();
    expect(api.mutations()).toEqual([]);
  });

  it("defaults to detected Sinhala when browser preference storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Storage unavailable", "SecurityError");
    });
    await renderWorkspace((pageNumber) =>
      workspace(pageNumber, { language: "si" }),
    );
    expect(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
    ).toHaveValue("si");
  });

  it("hydrates saved Sinhala preferences without mismatching server markup", async () => {
    localStorage.setItem(reviewLanguageKey, "si");
    fixtureApi();
    const element = (
      <SourcePageReviewWorkspace documentId={documentId} role="admin" />
    );
    const container = document.createElement("div");
    container.innerHTML = renderToString(element);
    expect(
      container.querySelector('[data-testid="source-page-workspace"]'),
    ).toHaveAttribute("lang", "en");
    document.body.append(container);
    const onRecoverableError = vi.fn();
    render(element, { container, hydrate: true, onRecoverableError });
    await screen.findByRole("img", { name: "මුල් පිටුව 1" });
    expect(
      screen.getByRole("combobox", { name: reviewLanguageLabel }),
    ).toHaveValue("si");
    expect(onRecoverableError).not.toHaveBeenCalled();
  });
});

describe("faithful source comparison", () => {
  it("uses the selected Sinhala wording and preserves conjuncts, Tamil, Latin and maths exactly", async () => {
    localStorage.setItem(reviewLanguageKey, "si");
    fixtureApi((pageNumber) =>
      workspace(
        pageNumber,
        { language: "si", risk_codes: ["legacy_font"] },
        { language: "si" },
      ),
    );
    const view = render(
      <SourcePageReviewWorkspace documentId={documentId} role="admin" />,
    );
    expect(
      await screen.findByRole("heading", {
        name: "පද්ධතිය කියවූ පෙළ පරීක්ෂා කරන්න",
      }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "වම් පැත්තේ මුල් පිටුවත්, දකුණු පැත්තේ පද්ධතිය කියවූ පෙළත් සසඳන්න.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("region", { name: "මුල් පිටුව" })).toBeVisible();
    const text = within(
      screen.getByRole("region", { name: "පද්ධතිය කියවූ පෙළ" }),
    ).getByTestId("system-page-text");
    expect(text.textContent).toBe(unicodeText);
    expect(text).toHaveAttribute("lang", "si");
    expect(text).toHaveClass("font-sans", "whitespace-pre-wrap");
    expect(text).not.toHaveClass("font-mono");
    expect(view.container.querySelector("script")).toBeNull();
    expect(
      screen.getByText("මෙම පිටුවේ පෙළ නිවැරදිව කියවී නොමැති බව පෙනේ."),
    ).toBeVisible();
    for (const name of [
      "පෙළ නිවැරදියි",
      "නැවත කියවන්න",
      "පෙළ නිවැරදි කරන්න",
      "මෙම පිටුව භාවිත නොකරන්න",
    ]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(
      screen.getByText("තාක්ෂණික විස්තර").closest("details"),
    ).not.toHaveAttribute("open");
  });

  it.each([
    ["en", "en"],
    ["und", "en"],
    ["unknown", "en"],
    ["si", "si"],
    ["si-LK", "si"],
    ["Sinhala", "si"],
    ["ta", "ta"],
    ["ta_LK", "ta"],
    ["tam", "ta"],
  ])(
    "uses an explicitly chosen English UI for %s while retaining the appropriate text language",
    async (language, textLanguage) => {
      localStorage.setItem(reviewLanguageKey, "en");
      await renderWorkspace((pageNumber) =>
        workspace(pageNumber, { language }, { language }),
      );
      expect(
        screen.getByRole("heading", { name: "Check the system-read text" }),
      ).toBeVisible();
      expect(systemText().textContent).toBe(unicodeText);
      expect(systemText()).toHaveAttribute("lang", textLanguage);
      expect(
        screen.queryByRole("textbox", { name: "Correction" }),
      ).not.toBeInTheDocument();
    },
  );

  it("gives confirmation and save actions non-conflicting primary and disabled colors", async () => {
    const api = await renderWorkspace();
    await loadPreview();
    const confirm = screen.getByRole("button", { name: "Text is correct" });
    expect(confirm).toHaveClass("bg-slate-950", "text-white");
    expect(confirm).not.toHaveClass("bg-white");
    expect(confirm).not.toHaveClass("text-slate-950");
    fireEvent.click(confirm);
    const submit = screen.getByRole("button", {
      name: "Confirm compared text",
    });
    expect(submit).toBeDisabled();
    expect(submit).toHaveClass(
      "disabled:bg-slate-200",
      "disabled:text-slate-600",
    );
    expect(submit).not.toHaveClass("bg-white");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Correct the text" }));
    const save = screen.getByRole("button", { name: "Save correction" });
    expect(save).toBeDisabled();
    expect(save).toHaveClass(
      "bg-slate-950",
      "text-white",
      "disabled:bg-slate-200",
      "disabled:text-slate-600",
    );
    expect(save).not.toHaveClass("bg-white");
    expect(api.mutations()).toEqual([]);
  });

  it("does not call a raw corrupt native reading reviewed or allow readability to grant trust", async () => {
    const corrupt = "wkd ñ\uFFFD\uE001";
    const api = await renderWorkspace((pageNumber) =>
      workspace(pageNumber, {
        system_text: corrupt,
        can_confirm: false,
        risk_codes: ["legacy_font", "replacement_character"],
      }),
    );
    await loadPreview();
    expect(screen.queryByTestId("system-page-text")).not.toBeInTheDocument();
    expect(screen.getByTestId("failed-page-text").textContent).toBe(corrupt);
    expect(screen.getByTestId("failed-page-text")).not.toBeVisible();
    expect(screen.queryByText(/reviewed text/i)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Text is correct" }),
    ).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The page could not be read. Try again or correct the text against the original.",
    );
    const details = screen.getByText("Technical details").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent("fixture-engine");
    expect(screen.getByText(/fixture-engine/)).not.toBeVisible();
    expect(
      screen.queryByText("Not yet checked against the original."),
    ).not.toBeInTheDocument();
    expect(api.mutations()).toEqual([]);
  });

  it.each([
    { state: "failed", can_confirm: false, language: "si", risk_codes: [] },
    {
      state: "needs_review",
      can_confirm: false,
      language: "mul",
      diagnostics: { languages: ["en", "si"] },
      risk_codes: [],
    },
    {
      state: "needs_review",
      can_confirm: false,
      language: "si",
      risk_codes: ["maths_source_fidelity_failed"],
    },
    { state: "failed", can_confirm: true, language: "si", risk_codes: [] },
  ] satisfies Partial<Page>[])(
    "presents unsafe Sinhala readings as failures with reread primary regardless of risk codes: %j",
    async (overrides) => {
      const corrupt = "wkd ñ\uFFFD\uE001 <script>bad candidate</script> 1 + =";
      const api = await renderWorkspace((pageNumber) =>
        workspace(pageNumber, { ...overrides, system_text: corrupt }),
      );
      await act(async () =>
        fireEvent.load(screen.getByRole("img", { name: "මුල් පිටුව 1" })),
      );
      expect(screen.getByRole("alert")).toHaveTextContent(
        "මෙම පිටුවේ පෙළ නිවැරදිව කියවී නොමැත.",
      );
      expect(screen.queryByTestId("system-page-text")).not.toBeInTheDocument();
      const failedText = screen.getByTestId("failed-page-text");
      expect(failedText.textContent).toBe(corrupt);
      expect(failedText).not.toBeVisible();
      const details = screen.getByText("තාක්ෂණික විස්තර").closest("details")!;
      expect(details).not.toHaveAttribute("open");
      expect(details).toContainElement(failedText);
      expect(details).toHaveTextContent(candidateId);
      expect(details).toHaveTextContent("fixture-engine");
      expect(api.container.querySelector("script")).toBeNull();
      const actions = within(
        screen.getByRole("group", { name: "පිටුව සඳහා ක්‍රියා" }),
      );
      const buttons = actions.getAllByRole("button");
      expect(buttons.map((button) => button.textContent)).toEqual([
        "නැවත කියවන්න",
        "පෙළ නිවැරදි කරන්න",
        "මෙම පිටුව භාවිත නොකරන්න",
        "පෙළ නිවැරදියි",
      ]);
      expect(buttons[0]).toBeEnabled();
      expect(buttons[0]).toHaveClass("bg-slate-950", "text-white");
      expect(buttons[0]).not.toHaveClass("bg-white");
      for (const button of buttons.slice(1))
        expect(button).not.toHaveClass("bg-slate-950");
      expect(buttons[1]).toBeEnabled();
      expect(buttons[2]).toBeEnabled();
      expect(buttons[3]).toBeDisabled();
      fireEvent.click(buttons[3]);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(api.mutations()).toEqual([]);
      fireEvent.click(screen.getByText("තාක්ෂණික විස්තර"));
      expect(failedText).toBeVisible();
    },
  );

  it.each(["failed", "needs_review"] as const)(
    "shows the single readable Sinhala recovery with a warning, never confirmation, for %s maths failure",
    async (state) => {
      const raw = "wkd ñ\uFFFD\uE001 raw rejected candidate";
      const api = await renderWorkspace((pageNumber) =>
        workspace(pageNumber, {
          state,
          can_confirm: false,
          language: "si",
          system_text: unicodeText,
          risk_codes: ["math_grid_fidelity_failed"],
          diagnostics: { text_readable: true, raw_native_text: raw },
        }),
      );
      const recovery = screen.getByRole("region", {
        name: "නැවත කියවූ පෙළ — තහවුරු කිරීමට සූදානම් නැත",
      });
      expect(recovery).toBeVisible();
      expect(
        within(recovery).getByText(/අංක, සංකේත සහ වගු තවමත් වැරදි විය හැක/),
      ).toBeVisible();
      expect(
        within(recovery).getByTestId("recovered-page-text").textContent,
      ).toBe(unicodeText);
      expect(screen.getAllByTestId("recovered-page-text")).toHaveLength(1);
      expect(screen.queryByTestId("system-page-text")).not.toBeInTheDocument();
      expect(screen.queryByTestId("failed-page-text")).not.toBeInTheDocument();
      expect(screen.getByRole("alert")).toHaveTextContent(
        "මෙම පිටුවේ පෙළ නිවැරදිව කියවී නොමැත.",
      );
      expect(
        screen.queryByText("මුල් පිටුව සමඟ සසඳා තහවුරු කර ඇත."),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText("AI භාවිතයට සූදානම්", { exact: true }),
      ).not.toBeInTheDocument();
      const details = screen.getByText("තාක්ෂණික විස්තර").closest("details")!;
      expect(details).not.toHaveAttribute("open");
      expect(details).toHaveTextContent(raw);
      expect(details).toHaveTextContent(candidateId);
      expect(screen.getByText(/raw rejected candidate/)).not.toBeVisible();
      expect(api.container.querySelector("script")).toBeNull();
      const confirm = screen.getByRole("button", { name: "පෙළ නිවැරදියි" });
      expect(confirm).toBeDisabled();
      await act(async () =>
        fireEvent.load(screen.getByRole("img", { name: "මුල් පිටුව 1" })),
      );
      expect(confirm).toBeDisabled();
      const buttons = within(
        screen.getByRole("group", { name: "පිටුව සඳහා ක්‍රියා" }),
      ).getAllByRole("button");
      expect(buttons.map((button) => button.textContent)).toEqual([
        "නැවත කියවන්න",
        "පෙළ නිවැරදි කරන්න",
        "මෙම පිටුව භාවිත නොකරන්න",
        "පෙළ නිවැරදියි",
      ]);
      expect(buttons[0]).toBeEnabled();
      expect(buttons[0]).toHaveClass("bg-slate-950", "text-white");
      expect(buttons[1]).toBeEnabled();
      expect(buttons[2]).toBeEnabled();
      fireEvent.click(confirm);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(api.mutations()).toEqual([]);
      fireEvent.change(
        screen.getByRole("combobox", { name: reviewLanguageLabel }),
        { target: { value: "en" } },
      );
      expect(
        screen.getByRole("region", {
          name: "Re-read text — not ready for confirmation",
        }),
      ).toBeVisible();
      expect(
        screen.getByText(/Numbers, symbols and tables may still be incorrect/),
      ).toBeVisible();
      expect(
        screen.getByRole("button", { name: "Text is correct" }),
      ).toBeDisabled();
      await act(async () => {
        const result = await axe.run(api.container, {
          rules: { "color-contrast": { enabled: false } },
        });
        expect(result.violations).toEqual([]);
      });
    },
  );

  it.each([false, undefined, null, "true", 1, [true], { value: true }])(
    "keeps failed text in closed technical details unless text_readable is strictly true: %j",
    async (textReadable) => {
      const api = await renderWorkspace((pageNumber) =>
        workspace(pageNumber, {
          state: "failed",
          can_confirm: false,
          language: "si",
          diagnostics: { text_readable: textReadable },
        }),
      );
      expect(
        screen.queryByTestId("recovered-page-text"),
      ).not.toBeInTheDocument();
      expect(screen.queryByTestId("system-page-text")).not.toBeInTheDocument();
      expect(screen.getByTestId("failed-page-text").textContent).toBe(
        unicodeText,
      );
      expect(screen.getByTestId("failed-page-text")).not.toBeVisible();
      expect(
        screen.getByRole("button", { name: "පෙළ නිවැරදියි" }),
      ).toBeDisabled();
      expect(api.mutations()).toEqual([]);
    },
  );

  it.each([
    { system_text: "" },
    { system_text: "  \n\t" },
    { candidate_id: null },
  ])(
    "does not show a missing or blank recovery despite text_readable true: %j",
    async (overrides) => {
      await renderWorkspace((pageNumber) =>
        workspace(pageNumber, {
          state: "failed",
          can_confirm: false,
          diagnostics: { text_readable: true },
          ...overrides,
        }),
      );
      expect(
        screen.queryByTestId("recovered-page-text"),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Text is correct" }),
      ).toBeDisabled();
    },
  );

  it.each(["", "  \n\t"])(
    "never enables confirmation of blank text %j even if the server flag is true",
    async (text) => {
      const api = await renderWorkspace((pageNumber) =>
        workspace(pageNumber, { system_text: text }),
      );
      await loadPreview();
      expect(
        screen.getByRole("button", { name: "Text is correct" }),
      ).toBeDisabled();
      expect(screen.getByRole("button", { name: "Read again" })).toHaveClass(
        "bg-slate-950",
      );
      expect(api.mutations()).toEqual([]);
    },
  );

  it.each(["verified", "excluded", "processing"] as const)(
    "does not mislabel a %s page as a failed reading just because confirmation is unavailable",
    async (state) => {
      await renderWorkspace((pageNumber) =>
        workspace(pageNumber, { state, can_confirm: false }),
      );
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.queryByTestId("failed-page-text")).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Text is correct" }),
      ).toBeDisabled();
    },
  );

  it("shows only server progress and bounds a 371-page book to two independently scrolling panels", async () => {
    const api = await renderWorkspace();
    count("Total pages", 371);
    count("Processed pages", 68);
    count("Verified pages", 2);
    count("Excluded pages", 1);
    count("Flagged pages", 17);
    count("Remaining pages", 368);
    const comparison = screen.getByRole("region", {
      name: "Original and system text comparison",
    });
    expect(comparison).toHaveClass(
      "min-h-0",
      "overflow-hidden",
      "lg:grid-cols-2",
    );
    expect(screen.getByTestId("source-page-workspace")).toHaveClass(
      "lg:h-[calc(100dvh-15rem)]",
    );
    expect(screen.getByRole("region", { name: "Original page" })).toHaveClass(
      "overflow-auto",
      "min-h-0",
    );
    expect(
      within(
        screen.getByRole("region", { name: "System-read text" }),
      ).getByTestId("text-scroll-panel"),
    ).toHaveClass("overflow-auto", "min-h-0");
    expect(
      screen.getByRole("navigation", { name: "Page navigation" }),
    ).toHaveClass("sticky", "top-0");
    expect(screen.getByRole("group", { name: "Page actions" })).toHaveClass(
      "sticky",
      "bottom-0",
    );
    expect(screen.getAllByRole("img")).toHaveLength(1);
    expect(api.pages()).toEqual([1]);
    expect(api.requests[0].cache).toBe("no-store");
    expect(
      screen.queryByText("Ready for AI", { exact: true }),
    ).not.toBeInTheDocument();
  });

  it.each([
    { ready_for_ai: true },
    { ready_for_ai: true, metadata_review_required: true },
    { ready_for_ai: true, source_active: false },
  ])(
    "fails closed for unresolved pages even if readiness is inconsistent: %j",
    async (overrides) => {
      await renderWorkspace((pageNumber) =>
        workspace(pageNumber, {}, overrides),
      );
      expect(
        screen.queryByText("Ready for AI", { exact: true }),
      ).not.toBeInTheDocument();
    },
  );

  it.each(["pending", "needs_review", "processing", "failed"] as const)(
    "does not show Ready for AI while the current page is unresolved (%s)",
    async (state) => {
      await renderWorkspace((pageNumber) =>
        workspace(
          pageNumber,
          { state },
          {
            ready_for_ai: true,
            progress: {
              total_pages: 371,
              processed_pages: 371,
              verified_pages: 370,
              excluded_pages: 1,
              flagged_pages: 0,
              remaining_pages: 0,
            },
          },
        ),
      );
      expect(
        screen.queryByText("Ready for AI", { exact: true }),
      ).not.toBeInTheDocument();
    },
  );

  it("shows readiness only when the backend explicitly reports a completed, active review", async () => {
    await renderWorkspace((pageNumber) =>
      workspace(
        pageNumber,
        { state: "verified" },
        {
          ready_for_ai: true,
          progress: {
            total_pages: 371,
            processed_pages: 371,
            verified_pages: 370,
            excluded_pages: 1,
            flagged_pages: 0,
            remaining_pages: 0,
          },
        },
      ),
    );
    expect(screen.getByText("Ready for AI", { exact: true })).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Text is correct" }),
    ).toBeDisabled();
  });

  it("uses the exact preview URL, handles failure and retry, and supplies accessible bounded zoom", async () => {
    await renderWorkspace();
    const image = screen.getByRole("img", { name: "Original page 1" });
    expect(image).toHaveAttribute(
      "src",
      `${window.location.origin}${workspace().page?.preview_url}`,
    );
    expect(image).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(screen.getByText("Loading original page…")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Text is correct" }),
    ).toBeDisabled();
    fireEvent.error(image);
    expect(
      within(screen.getByRole("region", { name: "Original page" })).getByRole(
        "alert",
      ),
    ).toHaveTextContent("The original page could not be loaded");
    expect(
      screen.getByRole("button", { name: "Text is correct" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Try image again" }));
    await loadPreview();
    expect(
      screen.getByRole("button", { name: "Text is correct" }),
    ).toBeEnabled();
    expect(screen.getByLabelText("Page zoom")).toHaveTextContent("100%");
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByLabelText("Page zoom")).toHaveTextContent("125%");
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(screen.getByLabelText("Page zoom")).toHaveTextContent("100%");
    fireEvent.click(screen.getByRole("button", { name: "Reset zoom" }));
    expect(
      screen.getByRole("img", { name: "Original page 1" }),
    ).toHaveAttribute(
      "src",
      `${window.location.origin}${workspace().page?.preview_url}`,
    );
  });
});

describe("page navigation", () => {
  it("requests First, Previous, jump, Next, Last and flagged pages rather than loading the whole book", async () => {
    const api = await renderWorkspace();
    expect(screen.getByRole("button", { name: "First page" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Previous page" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByRole("img", { name: "Original page 2" });
    fireEvent.click(screen.getByRole("button", { name: "Last page" }));
    await screen.findByRole("img", { name: "Original page 371" });
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    await screen.findByRole("img", { name: "Original page 370" });
    fireEvent.click(screen.getByRole("button", { name: "First page" }));
    await screen.findByRole("img", { name: "Original page 1" });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Page number" }), {
      target: { value: "246" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Go to page" }));
    await screen.findByRole("img", { name: "Original page 246" });
    fireEvent.click(
      screen.getByRole("button", { name: "Previous flagged page" }),
    );
    await screen.findByRole("img", { name: "Original page 17" });
    fireEvent.click(screen.getByRole("button", { name: "Next flagged page" }));
    await screen.findByRole("img", { name: "Original page 23" });
    expect(api.pages()).toEqual([1, 2, 371, 370, 1, 246, 17, 23]);
    expect(screen.getByText("of 371")).toBeVisible();
  });

  it.each(["0", "372", "1.5", ""])(
    "rejects an invalid jump %s without a page request",
    async (value) => {
      const api = await renderWorkspace();
      fireEvent.change(
        screen.getByRole("spinbutton", { name: "Page number" }),
        { target: { value } },
      );
      fireEvent.click(screen.getByRole("button", { name: "Go to page" }));
      expect(
        await screen.findByText("Choose a whole page number from 1 to 371."),
      ).toBeVisible();
      expect(api.pages()).toEqual([1]);
    },
  );

  it("starts at the explicitly requested page and keeps the human review queue link", async () => {
    const api = await renderWorkspace(undefined, undefined, {
      initialPageNumber: 37,
      benchmarkId,
    });
    expect(api.pages()).toEqual([37]);
    expect(screen.getByRole("img", { name: "Original page 37" })).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Back to review queue" }),
    ).toHaveAttribute(
      "href",
      `/admin/materials/benchmark-review?benchmark_id=${benchmarkId}`,
    );
  });

  it.each(["success", "failure"])(
    "ignores a late page %s after a newer navigation",
    async (lateResult) => {
      const late = deferred<Response>();
      const api = await renderWorkspace(
        (pageNumber) =>
          workspace(pageNumber, { system_text: `Text for page ${pageNumber}` }),
        (request) => {
          if (
            request.method === "GET" &&
            new URL(request.url).searchParams.get("page_number") === "2"
          )
            return late.promise;
        },
      );
      fireEvent.click(screen.getByRole("button", { name: "Next page" }));
      await waitFor(() => expect(api.pages()).toEqual([1, 2]));
      expect(
        screen.getByRole("button", { name: "Correct the text" }),
      ).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: "Last page" }));
      await screen.findByRole("img", { name: "Original page 371" });
      await act(async () => {
        late.resolve(
          lateResult === "success"
            ? Response.json(workspace(2))
            : Response.json(
                { detail: { code: "late_failure" } },
                { status: 500 },
              ),
        );
      });
      expect(systemText().textContent).toBe("Text for page 371");
      expect(
        screen.getByRole("spinbutton", { name: "Page number" }),
      ).toHaveValue(371);
      expect(screen.queryByText(/late_failure/)).not.toBeInTheDocument();
      expect(api.requests[1].signal.aborted).toBe(true);
    },
  );

  it("ignores an old page response even after a correction has begun on the new page", async () => {
    const late = deferred<Response>();
    const api = await renderWorkspace(undefined, (request) => {
      if (
        request.method === "GET" &&
        new URL(request.url).searchParams.get("page_number") === "2"
      )
        return late.promise;
    });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(api.pages()).toEqual([1, 2]));
    fireEvent.click(screen.getByRole("button", { name: "Last page" }));
    await screen.findByRole("img", { name: "Original page 371" });
    fireEvent.click(screen.getByRole("button", { name: "Correct the text" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Correction" }), {
      target: { value: "Draft for the last original page" },
    });
    await act(async () =>
      late.resolve(
        Response.json(workspace(2, { system_text: "Old page response" })),
      ),
    );
    expect(screen.getByRole("textbox", { name: "Correction" })).toHaveValue(
      "Draft for the last original page",
    );
    expect(
      screen.getByRole("img", { name: "Original page 371" }),
    ).toBeVisible();
    expect(screen.queryByText("Old page response")).not.toBeInTheDocument();
  });

  it("guards draft navigation, links and browser unload without discarding a correction", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const api = await renderWorkspace();
    fireEvent.click(screen.getByRole("button", { name: "Correct the text" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Correction" }), {
      target: { value: "Unsaved human correction" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(confirm).toHaveBeenCalled();
    expect(api.pages()).toEqual([1]);
    expect(screen.getByRole("textbox", { name: "Correction" })).toHaveValue(
      "Unsaved human correction",
    );
    expect(
      fireEvent.click(screen.getByRole("link", { name: "Back to material" })),
    ).toBe(false);
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByRole("img", { name: "Original page 2" });
    expect(
      screen.queryByRole("textbox", { name: "Correction" }),
    ).not.toBeInTheDocument();
    expect(api.mutations()).toEqual([]);
  });
});

describe("explicit versioned decisions", () => {
  it.each([false, true])(
    "preserves correction recovery from a Sinhala failure and waits for server validation (%s)",
    async (canConfirm) => {
      const corrupt = "wkd ñ\uFFFD\uE001";
      const corrected = "ශ්‍රී ලංකාව — ½ × 2 = 1";
      let current = workspace(1, {
        state: "failed",
        can_confirm: false,
        language: "si",
        system_text: corrupt,
      });
      const api = await renderWorkspace(
        () => current,
        (request) => {
          if (request.method === "POST" && request.url.endsWith("/edit")) {
            current = workspace(1, {
              state: "needs_review",
              language: "si",
              can_confirm: canConfirm,
              system_text: corrected,
              version: 8,
              candidate_id: secondCandidateId,
            });
            return Response.json({
              document_id: documentId,
              page_number: 1,
              state: "needs_review",
              version: 8,
              candidate_id: secondCandidateId,
            });
          }
        },
      );
      const leave = vi.spyOn(window, "confirm").mockReturnValue(false);
      fireEvent.click(
        screen.getByRole("button", { name: "පෙළ නිවැරදි කරන්න" }),
      );
      const editor = screen.getByRole("textbox", { name: "නිවැරදි කළ පෙළ" });
      expect(editor).toHaveValue(corrupt);
      fireEvent.change(editor, { target: { value: corrected } });
      expect(
        screen.getByRole("button", { name: "නිවැරදි කළ පෙළ සුරකින්න" }),
      ).toBeDisabled();
      fireEvent.change(
        screen.getByRole("textbox", { name: "නිවැරදි කිරීමට හේතුව" }),
        {
          target: { value: "මුල් පිටුව සමඟ සැසඳුවෙමි" },
        },
      );
      fireEvent.click(screen.getByRole("button", { name: "ඊළඟ පිටුව" }));
      expect(leave).toHaveBeenCalled();
      expect(editor).toHaveValue(corrected);
      expect(api.pages()).toEqual([1]);
      fireEvent.click(
        screen.getByRole("button", { name: "නිවැරදි කළ පෙළ සුරකින්න" }),
      );
      await screen.findByText(
        "නිවැරදි කළ පෙළ සුරැකුණි. තහවුරු කිරීමට පෙර එය මුල් පිටුව සමඟ සසඳන්න.",
      );
      expect(api.mutations()).toHaveLength(1);
      expect(await api.mutations()[0].json()).toEqual({
        expected_version: 7,
        text: corrected,
        reason: "මුල් පිටුව සමඟ සැසඳුවෙමි",
      });
      expect(
        screen.getByRole("button", { name: "පෙළ නිවැරදියි" }),
      ).toBeDisabled();
      await act(async () =>
        fireEvent.load(screen.getByRole("img", { name: "මුල් පිටුව 1" })),
      );
      const confirm = screen.getByRole("button", { name: "පෙළ නිවැරදියි" });
      if (canConfirm) {
        expect(confirm).toBeEnabled();
        expect(screen.getByTestId("system-page-text").textContent).toBe(
          corrected,
        );
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      } else {
        expect(confirm).toBeDisabled();
        expect(screen.getByRole("alert")).toHaveTextContent(
          "මෙම පිටුවේ පෙළ නිවැරදිව කියවී නොමැත.",
        );
        expect(
          screen.queryByTestId("system-page-text"),
        ).not.toBeInTheDocument();
      }
      expect(api.mutations()).toHaveLength(1);
    },
  );

  it("requires Edit, a reason and Save; Cancel restores the unmodified system reading", async () => {
    const api = await renderWorkspace();
    expect(
      screen.queryByRole("textbox", { name: "Correction" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Correct the text" }));
    const correction = screen.getByRole("textbox", { name: "Correction" });
    expect(correction).toHaveValue(unicodeText);
    expect(correction).toHaveClass("font-sans");
    fireEvent.change(correction, {
      target: { value: `${unicodeText}\nHuman change` },
    });
    expect(
      screen.getByRole("button", { name: "Save correction" }),
    ).toBeDisabled();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Reason for correction" }),
      { target: { value: "  " } },
    );
    expect(
      screen.getByRole("button", { name: "Save correction" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel editing" }));
    expect(systemText().textContent).toBe(unicodeText);
    expect(api.mutations()).toEqual([]);
  });

  it("sends exact Unicode edits with the captured version and never confirms on save", async () => {
    const edited = `${unicodeText}\n  නිවැරදි කළ පෙළ  `;
    let current = workspace();
    const api = await renderWorkspace(
      () => current,
      (request) => {
        if (
          request.method === "POST" &&
          new URL(request.url).pathname === `${pagePath}/1/edit`
        ) {
          current = workspace(1, {
            system_text: edited,
            version: 8,
            candidate_id: secondCandidateId,
          });
          return Response.json({
            document_id: documentId,
            page_number: 1,
            version: 8,
            state: "needs_review",
            candidate_id: secondCandidateId,
          });
        }
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Correct the text" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Correction" }), {
      target: { value: edited },
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Reason for correction" }),
      { target: { value: "Corrected against the original" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await screen.findByText(
      "Correction saved. Compare it with the original before confirming.",
    );
    expect(systemText().textContent).toBe(edited);
    expect(api.mutations()).toHaveLength(1);
    expect(await api.mutations()[0].json()).toEqual({
      expected_version: 7,
      text: edited,
      reason: "Corrected against the original",
    });
    expect(new URL(api.mutations()[0].url).pathname).toBe(`${pagePath}/1/edit`);
    count("Verified pages", 2);
    expect(
      screen.getByText("Not yet checked against the original."),
    ).toBeVisible();
    expect(
      screen.queryByText("Ready for AI", { exact: true }),
    ).not.toBeInTheDocument();
  });

  it("requires an explicit original-page comparison, uses candidate/version CAS, and blocks duplicate confirmation", async () => {
    const submitted = deferred<Response>();
    let current = workspace();
    const api = await renderWorkspace(
      () => current,
      (request) => {
        if (request.method === "POST") return submitted.promise;
      },
    );
    await loadPreview();
    fireEvent.click(screen.getByRole("button", { name: "Text is correct" }));
    let dialog = screen.getByRole("dialog", { name: "Confirm this page" });
    expect(
      within(dialog).getByRole("button", { name: "Confirm compared text" }),
    ).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(api.mutations()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Text is correct" }));
    dialog = screen.getByRole("dialog", { name: "Confirm this page" });
    const comparison = within(dialog).getByRole("checkbox", {
      name: "I compared this text with the original page.",
    });
    expect(comparison).not.toBeChecked();
    fireEvent.click(comparison);
    expect(api.mutations()).toEqual([]);
    const confirm = within(dialog).getByRole("button", {
      name: "Confirm compared text",
    });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(api.mutations()).toHaveLength(1));
    expect(confirm).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeDisabled();
    expect(new URL(api.mutations()[0].url).pathname).toBe(
      `${pagePath}/1/confirm`,
    );
    expect(await api.mutations()[0].json()).toEqual({
      expected_version: 7,
      candidate_id: candidateId,
      compared_with_original: true,
      reason: "Compared this text with the original page.",
    });
    current = workspace(
      1,
      { state: "verified", version: 8 },
      {
        progress: {
          ...current.progress,
          verified_pages: 3,
          remaining_pages: 367,
        },
      },
    );
    await act(async () => {
      submitted.resolve(
        Response.json({
          document_id: documentId,
          page_number: 1,
          version: 8,
          state: "verified",
          candidate_id: candidateId,
        }),
      );
    });
    await screen.findByText("Confirmed against the original.");
    count("Verified pages", 3);
    expect(
      screen.queryByText("Ready for AI", { exact: true }),
    ).not.toBeInTheDocument();
  });

  it("requires a fresh comparison after a confirmation conflict and uses the newly loaded candidate", async () => {
    let current = workspace();
    let attempts = 0;
    const api = await renderWorkspace(
      () => current,
      (request) => {
        if (request.method !== "POST") return;
        attempts += 1;
        if (attempts === 1) {
          current = workspace(1, {
            system_text: "A new reading to compare",
            version: 9,
            candidate_id: secondCandidateId,
          });
          return Response.json(
            { detail: { code: "source_candidate_changed" } },
            { status: 409 },
          );
        }
        current = workspace(1, {
          state: "verified",
          system_text: "A new reading to compare",
          version: 10,
          candidate_id: secondCandidateId,
        });
        return Response.json({
          document_id: documentId,
          page_number: 1,
          state: "verified",
          version: 10,
          candidate_id: secondCandidateId,
        });
      },
    );
    await loadPreview();
    fireEvent.click(screen.getByRole("button", { name: "Text is correct" }));
    const dialog = screen.getByRole("dialog", { name: "Confirm this page" });
    fireEvent.click(within(dialog).getByRole("checkbox"));
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Confirm compared text" }),
    );
    await within(dialog).findByRole("alert");
    expect(within(dialog).getByRole("checkbox")).not.toBeChecked();
    expect(
      within(dialog).getByRole("button", { name: "Confirm compared text" }),
    ).toBeDisabled();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Refresh page status" }),
    );
    await waitFor(() => expect(api.pages()).toEqual([1, 1]));
    await waitFor(() =>
      expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument(),
    );
    await act(async () =>
      fireEvent.load(screen.getByAltText("Original page 1")),
    );
    expect(within(dialog).getByRole("checkbox")).not.toBeChecked();
    expect(
      within(dialog).getByRole("button", { name: "Confirm compared text" }),
    ).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("checkbox"));
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Confirm compared text" }),
    );
    await screen.findByText("Confirmed against the original.");
    expect(await api.mutations()[1].json()).toMatchObject({
      expected_version: 9,
      candidate_id: secondCandidateId,
      compared_with_original: true,
    });
  });

  it("cancels an exclusion without sending a request or retaining an old agreement", async () => {
    const api = await renderWorkspace();
    fireEvent.click(
      screen.getByRole("button", { name: "Do not use this page" }),
    );
    let dialog = screen.getByRole("dialog", {
      name: "Exclude this page from use?",
    });
    fireEvent.change(
      within(dialog).getByRole("textbox", {
        name: "Reason for not using this page",
      }),
      { target: { value: "A decision not yet made" } },
    );
    fireEvent.click(within(dialog).getByRole("checkbox"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Do not use this page" }),
    );
    dialog = screen.getByRole("dialog", {
      name: "Exclude this page from use?",
    });
    expect(within(dialog).getByRole("checkbox")).not.toBeChecked();
    expect(
      within(dialog).getByRole("textbox", {
        name: "Reason for not using this page",
      }),
    ).toHaveValue("");
    expect(api.mutations()).toEqual([]);
  });

  it("requires exclusion confirmation and a reason, preserving original text and history", async () => {
    let current = workspace();
    const api = await renderWorkspace(
      () => current,
      (request) => {
        if (request.method === "POST") {
          current = workspace(
            1,
            { state: "excluded", version: 8 },
            {
              progress: {
                ...current.progress,
                excluded_pages: 2,
                remaining_pages: 367,
              },
            },
          );
          return Response.json({
            document_id: documentId,
            page_number: 1,
            version: 8,
            state: "excluded",
            candidate_id: candidateId,
          });
        }
      },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Do not use this page" }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Exclude this page from use?",
    });
    expect(dialog).toHaveTextContent(
      "The original page, past readings and review history are kept. The page is not deleted.",
    );
    const exclude = within(dialog).getByRole("button", {
      name: "Exclude page",
    });
    expect(exclude).toBeDisabled();
    fireEvent.change(
      within(dialog).getByRole("textbox", {
        name: "Reason for not using this page",
      }),
      { target: { value: "Blank exercise answer space" } },
    );
    expect(exclude).toBeDisabled();
    fireEvent.click(
      within(dialog).getByRole("checkbox", {
        name: "I understand this page will not be used.",
      }),
    );
    fireEvent.click(exclude);
    await screen.findByText("Excluded from use. Its history is preserved.");
    expect(new URL(api.mutations()[0].url).pathname).toBe(
      `${pagePath}/1/exclude`,
    );
    expect(await api.mutations()[0].json()).toEqual({
      expected_version: 7,
      confirm_exclusion: true,
      reason: "Blank exercise answer space",
    });
    expect(systemText().textContent).toBe(unicodeText);
    expect(
      screen.getByText("Technical details").closest("details"),
    ).toHaveTextContent(candidateId);
    count("Verified pages", 2);
    count("Excluded pages", 2);
  });

  it("sends the primary Sinhala reread action with the failed page's version, never a confirmation", async () => {
    let current = workspace(1, {
      state: "failed",
      language: "si",
      can_confirm: false,
    });
    const response = deferred<Response>();
    const api = await renderWorkspace(
      () => current,
      (request) => {
        if (request.method === "POST") return response.promise;
      },
    );
    const reread = screen.getByRole("button", { name: "නැවත කියවන්න" });
    fireEvent.click(reread);
    fireEvent.click(reread);
    await waitFor(() => expect(api.mutations()).toHaveLength(1));
    expect(reread).toBeDisabled();
    expect(reread).toHaveClass(
      "disabled:bg-slate-200",
      "disabled:text-slate-600",
    );
    expect(api.mutations()[0].url).toContain(`${pagePath}/1/reread`);
    expect(await api.mutations()[0].json()).toEqual({ expected_version: 7 });
    current = workspace(1, {
      state: "processing",
      language: "si",
      can_confirm: false,
      version: 8,
    });
    await act(async () =>
      response.resolve(Response.json({ status: "accepted" }, { status: 202 })),
    );
    await screen.findByText("මෙම පිටුව කියවමින් පවතී…");
    expect(
      screen.getByRole("button", { name: "පෙළ නිවැරදියි" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "නැවත කියවන්න" })).toBeDisabled();
    expect(api.mutations()).toHaveLength(1);
  });

  it("keeps all failed-page mutation actions disabled for a reviewer", async () => {
    const api = await renderWorkspace(
      (pageNumber) =>
        workspace(pageNumber, {
          state: "failed",
          language: "si",
          can_confirm: false,
        }),
      undefined,
      { role: "reviewer" },
    );
    const actions = within(
      screen.getByRole("group", { name: "පිටුව සඳහා ක්‍රියා" }),
    );
    for (const button of actions.getAllByRole("button")) {
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(api.mutations()).toEqual([]);
  });

  it("queues a real reread once, then displays the reloaded server processing/failure state", async () => {
    const queued = deferred<Response>();
    let current = workspace();
    const api = await renderWorkspace(
      () => current,
      (request) => {
        if (request.method === "POST") return queued.promise;
      },
    );
    const reread = screen.getByRole("button", { name: "Read again" });
    fireEvent.click(reread);
    fireEvent.click(reread);
    await waitFor(() => expect(api.mutations()).toHaveLength(1));
    expect(reread).toBeDisabled();
    expect(screen.queryByText("Reading this page…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    expect(new URL(api.mutations()[0].url).pathname).toBe(
      `${pagePath}/1/reread`,
    );
    expect(await api.mutations()[0].json()).toEqual({ expected_version: 7 });
    current = workspace(1, {
      state: "processing",
      version: 8,
      can_confirm: false,
    });
    await act(async () =>
      queued.resolve(Response.json({ status: "accepted" }, { status: 202 })),
    );
    await screen.findByText("Reading this page…");
    expect(
      screen.getByRole("button", { name: "Text is correct" }),
    ).toBeDisabled();
    current = workspace(1, {
      state: "failed",
      version: 9,
      can_confirm: false,
      diagnostics: { failure_code: "reader_unavailable" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh page status" }),
    );
    await screen.findByText(
      "The page could not be read. Try again or correct the text against the original.",
    );
    expect(screen.getByRole("button", { name: "Read again" })).toBeEnabled();
    count("Verified pages", 2);
  });

  it("does not report reread success when the actual API fails", async () => {
    const api = await renderWorkspace(undefined, (request) => {
      if (request.method === "POST")
        return Response.json(
          { detail: { code: "reader_unavailable" } },
          { status: 503 },
        );
    });
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The request could not be completed",
    );
    expect(screen.queryByText("Reading this page…")).not.toBeInTheDocument();
    expect(systemText().textContent).toBe(unicodeText);
    expect(api.mutations()).toHaveLength(1);
  });

  it("keeps a correction and requires a fresh status after an ambiguous server failure", async () => {
    const api = await renderWorkspace(undefined, (request) => {
      if (request.method === "POST")
        return Response.json(
          { detail: { code: "private_failure" } },
          { status: 503 },
        );
    });
    fireEvent.click(screen.getByRole("button", { name: "Correct the text" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Correction" }), {
      target: { value: "Unsure whether this saved" },
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Reason for correction" }),
      { target: { value: "Compared each printed symbol" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("textbox", { name: "Correction" })).toHaveValue(
      "Unsure whether this saved",
    );
    expect(
      screen.getByRole("textbox", { name: "Reason for correction" }),
    ).toHaveValue("Compared each printed symbol");
    expect(
      screen.getByRole("button", { name: "Save correction" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    expect(api.mutations()).toHaveLength(1);
    fireEvent.click(
      screen.getByRole("button", {
        name: "Reload latest page, keeping correction",
      }),
    );
    await screen.findByRole("region", { name: "Latest system text" });
    expect(
      screen.getByRole("button", { name: "Save correction" }),
    ).toBeDisabled();
  });

  it.each([true, false, "true", undefined])(
    "rechecks the latest failed candidate's readability after a correction conflict: %j",
    async (textReadable) => {
      let current = workspace(1, {
        state: "failed",
        can_confirm: false,
        system_text: "Previous recovery",
        diagnostics: { text_readable: textReadable !== true },
      });
      let edits = 0;
      const latestText = "ශ්‍රී ලංකාව — නව කියවීම ½ × 2 = 1";
      const api = await renderWorkspace(
        () => current,
        (request) => {
          if (request.method !== "POST") return;
          edits += 1;
          if (edits === 1) {
            current = workspace(1, {
              state: "failed",
              can_confirm: false,
              system_text: latestText,
              version: 9,
              candidate_id: secondCandidateId,
              diagnostics: { text_readable: textReadable },
            });
            return Response.json(
              { detail: { code: "source_page_version_conflict" } },
              { status: 409 },
            );
          }
          return Response.json({
            document_id: documentId,
            page_number: 1,
            state: "failed",
            version: 10,
            candidate_id: secondCandidateId,
          });
        },
      );
      if (textReadable !== true)
        expect(screen.getByTestId("recovered-page-text")).toHaveTextContent(
          "Previous recovery",
        );
      fireEvent.click(screen.getByRole("button", { name: "Correct the text" }));
      fireEvent.change(screen.getByRole("textbox", { name: "Correction" }), {
        target: { value: "My unsaved correction" },
      });
      fireEvent.change(
        screen.getByRole("textbox", { name: "Reason for correction" }),
        { target: { value: "Compared the original" } },
      );
      fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
      await screen.findByText(/This page changed in another session/);
      fireEvent.click(
        screen.getByRole("button", {
          name: "Reload latest page, keeping correction",
        }),
      );
      const latest = await screen.findByRole("region", {
        name: "Latest system text",
      });
      if (textReadable === true) {
        expect(
          within(latest).getByRole("region", {
            name: "Re-read text — not ready for confirmation",
          }),
        ).toBeVisible();
        expect(
          within(latest).getByTestId("recovered-page-text").textContent,
        ).toBe(latestText);
        expect(
          screen.queryByTestId("failed-page-text"),
        ).not.toBeInTheDocument();
      } else {
        expect(
          screen.queryByTestId("recovered-page-text"),
        ).not.toBeInTheDocument();
        expect(latest).not.toHaveTextContent(latestText);
        expect(screen.getByTestId("failed-page-text").textContent).toBe(
          latestText,
        );
        expect(screen.getByTestId("failed-page-text")).not.toBeVisible();
      }
      expect(screen.getByRole("textbox", { name: "Correction" })).toHaveValue(
        "My unsaved correction",
      );
      expect(
        screen.getByRole("textbox", { name: "Reason for correction" }),
      ).toHaveValue("Compared the original");
      expect(
        screen.getByRole("button", { name: "Save correction" }),
      ).toBeDisabled();
      expect(api.mutations()).toHaveLength(1);
      fireEvent.click(
        within(latest).getByRole("button", {
          name: "Use this version for my correction",
        }),
      );
      fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
      await waitFor(() => expect(api.mutations()).toHaveLength(2));
      expect(await api.mutations()[1].json()).toEqual({
        expected_version: 9,
        text: "My unsaved correction",
        reason: "Compared the original",
      });
      expect(
        api.mutations().every((request) => request.url.endsWith("/edit")),
      ).toBe(true);
    },
  );

  it("keeps a failed latest candidate in technical details when rebasing an unsaved correction", async () => {
    const corrupt = "wkd ñ\uFFFD\uE001 latest failed reading";
    let current = workspace();
    const api = await renderWorkspace(
      () => current,
      (request) => {
        if (request.method === "POST") {
          current = workspace(1, {
            state: "failed",
            can_confirm: false,
            system_text: corrupt,
            version: 9,
            candidate_id: secondCandidateId,
          });
          return Response.json(
            { detail: { code: "source_page_version_conflict" } },
            { status: 409 },
          );
        }
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Correct the text" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Correction" }), {
      target: { value: "My correction" },
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Reason for correction" }),
      { target: { value: "Compared original" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await screen.findByText(/This page changed in another session/);
    fireEvent.click(
      screen.getByRole("button", {
        name: "Reload latest page, keeping correction",
      }),
    );
    const latest = await screen.findByRole("region", {
      name: "Latest system text",
    });
    expect(latest).not.toHaveTextContent(corrupt);
    expect(screen.getByTestId("failed-page-text").textContent).toBe(corrupt);
    expect(screen.getByTestId("failed-page-text")).not.toBeVisible();
    expect(screen.getByRole("textbox", { name: "Correction" })).toHaveValue(
      "My correction",
    );
    expect(
      screen.getByRole("textbox", { name: "Reason for correction" }),
    ).toHaveValue("Compared original");
    expect(
      screen.getByRole("button", { name: "Save correction" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Use this version for my correction",
      }),
    );
    expect(screen.getByRole("textbox", { name: "Correction" })).toHaveValue(
      "My correction",
    );
    expect(
      screen.getByRole("button", { name: "Save correction" }),
    ).toBeEnabled();
    expect(api.mutations()).toHaveLength(1);
  });

  it("preserves correction and reason on conflict, and requires explicit rebasing after reloading the latest text", async () => {
    let current = workspace();
    let edits = 0;
    const api = await renderWorkspace(
      () => current,
      (request) => {
        if (request.method !== "POST") return;
        edits += 1;
        if (edits === 1) {
          current = workspace(1, {
            system_text: "A newer human reading",
            version: 9,
            candidate_id: secondCandidateId,
          });
          return Response.json(
            { detail: { code: "source_page_version_conflict" } },
            { status: 409 },
          );
        }
        current = workspace(1, {
          system_text: "My exact correction",
          version: 10,
        });
        return Response.json({
          document_id: documentId,
          page_number: 1,
          version: 10,
          state: "needs_review",
          candidate_id: candidateId,
        });
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Correct the text" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Correction" }), {
      target: { value: "My exact correction" },
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Reason for correction" }),
      { target: { value: "Compared the printed question" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This page changed in another session",
    );
    expect(screen.getByRole("textbox", { name: "Correction" })).toHaveValue(
      "My exact correction",
    );
    expect(
      screen.getByRole("textbox", { name: "Reason for correction" }),
    ).toHaveValue("Compared the printed question");
    expect(
      screen.getByRole("button", { name: "Save correction" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Reload latest page, keeping correction",
      }),
    );
    await screen.findByText("A newer human reading");
    expect(screen.getByRole("textbox", { name: "Correction" })).toHaveValue(
      "My exact correction",
    );
    expect(
      screen.getByRole("button", { name: "Save correction" }),
    ).toBeDisabled();
    expect(api.mutations()).toHaveLength(1);
    fireEvent.click(
      screen.getByRole("button", {
        name: "Use this version for my correction",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await screen.findByText(
      "Correction saved. Compare it with the original before confirming.",
    );
    expect(await api.mutations()[1].json()).toEqual({
      expected_version: 9,
      text: "My exact correction",
      reason: "Compared the printed question",
    });
    expect(
      api.mutations().every((request) => request.url.endsWith("/edit")),
    ).toBe(true);
  });

  it("keeps reviewer access read-only even when the backend page is confirmable", async () => {
    const api = await renderWorkspace(undefined, undefined, {
      role: "reviewer",
    });
    await loadPreview();
    expect(screen.getByText("Reviewer access is read-only.")).toBeVisible();
    for (const name of [
      "Text is correct",
      "Read again",
      "Correct the text",
      "Do not use this page",
    ]) {
      const button = screen.getByRole("button", { name });
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(api.mutations()).toEqual([]);
    expect(screen.getByRole("button", { name: "Next page" })).toBeEnabled();
  });
});

describe("unavailable and accessible workspaces", () => {
  it("only initializes unknown pages after an explicit read request, then follows the actual job", async () => {
    let current = emptyWorkspace();
    let job = readJob;
    const submitted = deferred<Response>();
    const api = fixtureApi(
      () => current,
      (request) => {
        const path = new URL(request.url).pathname;
        if (
          request.method === "POST" &&
          path === `/api/v1/admin/source-documents/${documentId}/read`
        )
          return submitted.promise;
        if (
          request.method === "GET" &&
          path === `/api/v1/admin/source-read-jobs/${readJob.id}`
        )
          return Response.json(job);
      },
    );
    render(<SourcePageReviewWorkspace documentId={documentId} role="admin" />);
    const read = await screen.findByRole("button", { name: "Read document" });
    expect(api.mutations()).toEqual([]);
    fireEvent.click(read);
    fireEvent.click(read);
    await waitFor(() => expect(api.mutations()).toHaveLength(1));
    expect(read).toBeDisabled();
    expect(screen.queryByText("Reading the document…")).not.toBeInTheDocument();
    await act(async () =>
      submitted.resolve(Response.json(readJob, { status: 202 })),
    );
    expect(await screen.findByText("Reading the document…")).toBeVisible();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    current = workspace();
    job = { ...readJob, status: "completed", next_page: 372, version: 2 };
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh page status" }),
    );
    await screen.findByRole("img", { name: "Original page 1" });
    await waitFor(() =>
      expect(
        screen.queryByText("Reading the document…"),
      ).not.toBeInTheDocument(),
    );
    expect(
      api.requests.some((request) =>
        request.url.endsWith(`/source-read-jobs/${readJob.id}`),
      ),
    ).toBe(true);
    expect(api.mutations()).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "Text is correct" }),
    ).toBeDisabled();
  });

  it.each(["reviewer", "removed"])(
    "cannot start document reading for %s access",
    async (access) => {
      const api = fixtureApi(() => ({
        ...emptyWorkspace(),
        source_active: access !== "removed",
      }));
      render(
        <SourcePageReviewWorkspace
          documentId={documentId}
          role={access === "reviewer" ? "reviewer" : "admin"}
        />,
      );
      await screen.findByText("No pages are available for comparison yet.");
      const read = screen.queryByRole("button", { name: "Read document" });
      if (read) {
        expect(read).toBeDisabled();
        fireEvent.click(read);
      }
      expect(api.mutations()).toEqual([]);
    },
  );

  it("reports a failed document job without treating its candidates as confirmed and allows an explicit retry", async () => {
    let job = readJob;
    const api = fixtureApi(emptyWorkspace, (request) => {
      const path = new URL(request.url).pathname;
      if (request.method === "POST" && path.endsWith("/read"))
        return Response.json(readJob, { status: 202 });
      if (path.endsWith(`/source-read-jobs/${readJob.id}`))
        return Response.json(job);
    });
    render(<SourcePageReviewWorkspace documentId={documentId} role="admin" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Read document" }),
    );
    await screen.findByText("Reading the document…");
    job = {
      ...readJob,
      status: "failed",
      failure_code: "private_reader_failure",
    };
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh page status" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The document could not be fully read",
    );
    expect(
      screen.queryByText("private_reader_failure"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Read document" })).toBeEnabled();
    expect(api.mutations()).toHaveLength(1);
    expect(
      screen.queryByText("Ready for AI", { exact: true }),
    ).not.toBeInTheDocument();
  });

  it("does not invent pages or readiness before any original pages are available", async () => {
    fixtureApi(() =>
      workspace(
        1,
        {},
        {
          page: null,
          ready_for_ai: false,
          progress: {
            total_pages: 0,
            processed_pages: 0,
            verified_pages: 0,
            excluded_pages: 0,
            flagged_pages: 0,
            remaining_pages: 0,
          },
          previous_flagged_page: null,
          next_flagged_page: null,
        },
      ),
    );
    render(<SourcePageReviewWorkspace documentId={documentId} role="admin" />);
    expect(
      await screen.findByText("No pages are available for comparison yet."),
    ).toBeVisible();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Ready for AI", { exact: true }),
    ).not.toBeInTheDocument();
  });

  it.each([401, 403, 404, 503])(
    "shows a recoverable fetch error (%s) without enabling page actions",
    async (status) => {
      let fail = true;
      fixtureApi(undefined, () =>
        fail
          ? Response.json(
              { detail: { code: "private_internal_failure" } },
              { status },
            )
          : undefined,
      );
      render(
        <SourcePageReviewWorkspace documentId={documentId} role="admin" />,
      );
      expect(await screen.findByRole("alert")).toBeVisible();
      expect(
        screen.queryByText("private_internal_failure"),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Text is correct" }),
      ).toBeDisabled();
      fail = false;
      fireEvent.click(
        screen.getByRole("button", { name: "Try loading again" }),
      );
      await screen.findByRole("img", { name: "Original page 1" });
    },
  );

  it("has accessible comparison, edit and confirmation controls", async () => {
    const view = await renderWorkspace();
    await loadPreview();
    const expectAccessible = async (element: HTMLElement) => {
      await act(async () => {
        const result = await axe.run(element, {
          rules: { "color-contrast": { enabled: false } },
        });
        expect(result.violations).toEqual([]);
      });
    };
    await expectAccessible(view.container);
    fireEvent.click(screen.getByRole("button", { name: "Correct the text" }));
    await expectAccessible(view.container);
    fireEvent.click(screen.getByRole("button", { name: "Cancel editing" }));
    fireEvent.click(screen.getByRole("button", { name: "Text is correct" }));
    await expectAccessible(screen.getByRole("dialog"));
  });
});
