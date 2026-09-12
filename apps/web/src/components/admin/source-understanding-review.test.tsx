import type { components } from "@exam-guru/api-client";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { reviewLanguageKey } from "@/lib/review-language";

import { SourceUnderstandingReview } from "./source-understanding-review";

type Snapshot = components["schemas"]["UnderstandingPageResponse"];
type Workspace = components["schemas"]["PageReviewWorkspaceResponse"];
const documentId = "00000000-0000-0000-0000-000000003501";
const candidateId = "00000000-0000-0000-0000-000000003502";
const source = {
  document_id: documentId,
  source_sha256: "a".repeat(64),
  page_number: 1,
  image_sha256: "b".repeat(64),
};

function snapshot(): Snapshot {
  return {
    document_id: documentId,
    page_number: 1,
    version: 1,
    state: "needs_human_review",
    active_job_id: null,
    latest_job: null,
    provider_available: true,
    trusted: null,
    candidate: {
      id: candidateId,
      run_id: "00000000-0000-0000-0000-000000003503",
      revision: 1,
      method: "visual_ai",
      source,
      content: {
        schema_version: "page-understanding.v1",
        observation: {
          language: "en",
          regions: [
            {
              key: "text",
              kind: "paragraph",
              reading_order: 0,
              parent_key: null,
              bounds: null,
              polygon: [],
              exact_text: "Six groups, two objects in each group.",
              equations: [],
              table: null,
              visual_facts: [],
            },
          ],
          relationships: [],
        },
        education: {
          claims: [
            {
              key: "skill",
              kind: "skill",
              description: "Practise counting in pairs.",
              region_keys: ["text"],
            },
          ],
        },
        uncertainties: [
          {
            key: "uncertain",
            region_keys: ["text"],
            field: "symbol",
            reason: "Check the small symbol.",
            alternatives: [],
          },
        ],
      },
    },
    report: {
      candidate_id: candidateId,
      candidate_fingerprint: "c".repeat(64),
      anchor_fingerprint: "d".repeat(64),
      anchor_evidence_ids: [],
      policy_version: "page-understanding-verification.v1",
      source_checker_version: "source-fidelity-v2/rules-2/ucd-15.0.0",
      findings: [
        {
          code: "original_comparison_required",
          severity: "review",
          region_keys: [],
          summary: "Compare with the original.",
        },
      ],
    },
  };
}

function workspace(): Workspace {
  return {
    document_id: documentId,
    document_title: "Synthetic teacher page.pdf",
    language: "en",
    metadata_review_required: true,
    source_active: true,
    ready_for_ai: false,
    progress: {
      total_pages: 2,
      processed_pages: 0,
      verified_pages: 0,
      excluded_pages: 0,
      flagged_pages: 0,
      remaining_pages: 2,
    },
    page: null,
    previous_flagged_page: null,
    next_flagged_page: null,
  };
}

let current: Snapshot;
let context: Workspace;
let writes: { path: string; body: unknown }[];
let verificationStatus: number;
let loadStatus: number;

beforeEach(() => {
  window.localStorage.clear();
  current = snapshot();
  context = workspace();
  writes = [];
  verificationStatus = 200;
  loadStatus = 200;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const request =
        input instanceof Request ? input : new Request(String(input));
      const path = new URL(request.url).pathname;
      if (request.method === "POST") {
        writes.push({ path, body: await request.json() });
        return Response.json(
          verificationStatus === 200
            ? {}
            : { detail: { code: "source_understanding_conflict" } },
          { status: verificationStatus },
        );
      }
      if (loadStatus !== 200)
        return Response.json(
          { detail: { code: "unavailable" } },
          { status: loadStatus },
        );
      return Response.json(
        path.endsWith("review-workspace") ? context : current,
      );
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

async function openReview() {
  render(<SourceUnderstandingReview documentId={documentId} role="admin" />);
  const image = await screen.findByRole("img", { name: "Original page 1" });
  await act(async () => {
    fireEvent.load(image);
  });
  fireEvent.click(
    await screen.findByRole("button", { name: "Review this reading" }),
  );
  return image;
}

function attest() {
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "I compared this reading with the original page.",
    }),
  );
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "I checked every visible source detail.",
    }),
  );
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Practise counting in pairs." }),
  );
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Check the small symbol." }),
  );
  fireEvent.change(
    screen.getByRole("textbox", { name: "Reason for accepting this reading" }),
    { target: { value: "Compared all details with the original." } },
  );
}

describe("teacher page understanding review", () => {
  it("loads a bounded comparison without starting paid work or treating readiness as trust", async () => {
    render(<SourceUnderstandingReview documentId={documentId} role="admin" />);
    const image = await screen.findByRole("img", { name: "Original page 1" });
    const preview = new URL(image.getAttribute("src")!, window.location.origin);
    expect(preview.origin).toBe(window.location.origin);
    expect(preview.pathname).toBe(
      `/api/v1/admin/materials/${documentId}/pages/1/understanding/candidates/${candidateId}/image`,
    );
    expect(
      screen.getByRole("region", { name: "What is visible" }),
    ).toBeVisible();
    expect(
      screen.getByRole("region", { name: "What it may teach" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Review this reading" }),
    ).toBeDisabled();
    expect(writes).toEqual([]);
  });

  it("does not invalidate the current comparison when navigating to the same page", async () => {
    render(<SourceUnderstandingReview documentId={documentId} role="admin" />);
    const image = await screen.findByRole("img", { name: "Original page 1" });
    await act(async () => {
      fireEvent.load(image);
    });
    expect(
      screen.getByRole("button", { name: "Review this reading" }),
    ).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Go to page" }));
    expect(
      screen.getByRole("button", { name: "Review this reading" }),
    ).toBeEnabled();
  });

  it("saves a source correction as a new unverified child at the captured version", async () => {
    render(<SourceUnderstandingReview documentId={documentId} role="admin" />);
    const image = await screen.findByRole("img", { name: "Original page 1" });
    await act(async () => {
      fireEvent.load(image);
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Correct this reading" }),
    );
    expect(
      screen.getByRole("button", { name: "Save correction" }),
    ).toBeDisabled();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Source text — detail 1" }),
      { target: { value: "Corrected a\u0301 — printed source only." } },
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Reason for correction" }),
      { target: { value: "Compared the literal source details." } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].path).toBe(
      `/api/v1/admin/materials/${documentId}/pages/1/understanding/corrections`,
    );
    expect(writes[0].body).toMatchObject({
      parent_candidate_id: candidateId,
      expected_version: 1,
      request_id: expect.any(String),
      reason: "Compared the literal source details.",
      content: {
        observation: {
          regions: [
            {
              key: "text",
              exact_text: "Corrected a\u0301 — printed source only.",
            },
          ],
        },
      },
    });
    expect(writes[0].body).not.toHaveProperty("trusted");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Correct this reading" }),
      ).toBeEnabled(),
    );
    expect(
      screen.queryByText("Page checked against the original"),
    ).not.toBeInTheDocument();
  });

  it("keeps correction text and reason after a stale-version failure", async () => {
    render(<SourceUnderstandingReview documentId={documentId} role="admin" />);
    const image = await screen.findByRole("img", { name: "Original page 1" });
    await act(async () => {
      fireEvent.load(image);
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Correct this reading" }),
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Source text — detail 1" }),
      { target: { value: "Unsaved correction" } },
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Reason for correction" }),
      { target: { value: "Keep this explanation" } },
    );
    verificationStatus = 409;
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await screen.findByText(
      "This reading changed. Your review choices have been kept.",
    );
    expect(
      screen.getByRole("textbox", { name: "Source text — detail 1" }),
    ).toHaveValue("Unsaved correction");
    expect(
      screen.getByRole("textbox", { name: "Reason for correction" }),
    ).toHaveValue("Keep this explanation");
    expect(
      screen.getByRole("button", { name: "Save correction" }),
    ).toBeDisabled();
  });

  it("keeps structured edits through an explicit rebase and binds the next parent image", async () => {
    render(<SourceUnderstandingReview documentId={documentId} role="admin" />);
    const image = await screen.findByRole("img", { name: "Original page 1" });
    await act(async () => {
      fireEvent.load(image);
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Correct this reading" }),
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Source text — detail 1" }),
      { target: { value: "Keep this structured correction" } },
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Reason for correction" }),
      { target: { value: "Preserve my explanation" } },
    );
    verificationStatus = 409;
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await screen.findByText(
      "This reading changed. Your review choices have been kept.",
    );
    const nextId = "00000000-0000-0000-0000-000000003509";
    current = {
      ...current,
      version: 2,
      candidate: { ...current.candidate!, id: nextId, revision: 2 },
      report: { ...current.report!, candidate_id: nextId },
    };
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest reading" }),
    );
    const rebase = screen.getByRole("button", {
      name: "Keep correction with latest reading",
    });
    await waitFor(() => expect(rebase).toBeEnabled());
    fireEvent.click(rebase);
    expect(
      screen.getByRole("textbox", { name: "Source text — detail 1" }),
    ).toHaveValue("Keep this structured correction");
    expect(
      screen.getByRole("textbox", { name: "Reason for correction" }),
    ).toHaveValue("Preserve my explanation");
    expect(
      screen.getByRole("img", { name: "Original page 1" }).getAttribute("src"),
    ).toContain(nextId);
    expect(
      screen.getByRole("button", { name: "Save correction" }),
    ).toBeDisabled();
    await act(async () => {
      fireEvent.load(screen.getByRole("img", { name: "Original page 1" }));
    });
    verificationStatus = 200;
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await waitFor(() => expect(writes).toHaveLength(2));
    expect(writes[1].body).toMatchObject({
      parent_candidate_id: nextId,
      expected_version: 2,
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Correct this reading" }),
      ).toBeEnabled(),
    );
  });

  it("does not rebase a correction onto an excluded page", async () => {
    render(<SourceUnderstandingReview documentId={documentId} role="admin" />);
    const image = await screen.findByRole("img", { name: "Original page 1" });
    await act(async () => {
      fireEvent.load(image);
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Correct this reading" }),
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Source text — detail 1" }),
      { target: { value: "Retained draft" } },
    );
    current = { ...current, version: 2, state: "excluded" };
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest reading" }),
    );
    await screen.findByText(
      "This reading changed. Your review choices have been kept.",
    );
    expect(
      screen.getByRole("button", {
        name: "Keep correction with latest reading",
      }),
    ).toBeDisabled();
    expect(
      screen.getByRole("textbox", { name: "Source text — detail 1" }),
    ).toHaveValue("Retained draft");
  });

  it("requires an explicit reason and decision to exclude even an unread page", async () => {
    current = {
      ...current,
      version: 0,
      state: "unprocessed",
      candidate: null,
      report: null,
    };
    render(<SourceUnderstandingReview documentId={documentId} role="admin" />);
    await screen.findByText(context.document_title);
    fireEvent.click(
      screen.getByRole("button", { name: "Do not use this page" }),
    );
    expect(screen.getByRole("button", { name: "Exclude page" })).toBeDisabled();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "I understand this page will not be used for AI.",
      }),
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Reason for excluding this page" }),
      { target: { value: "Not suitable for this collection" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Exclude page" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toEqual({
      path: `/api/v1/admin/materials/${documentId}/pages/1/understanding/exclude`,
      body: {
        expected_version: 0,
        confirm_exclusion: true,
        reason: "Not suitable for this collection",
      },
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Do not use this page" }),
      ).toBeEnabled(),
    );
  });

  it("returns an excluded page to review without restoring its former trust", async () => {
    current = {
      ...current,
      version: 2,
      state: "excluded",
      trusted: null,
      exclusion: {
        event_id: "00000000-0000-0000-0000-000000003507",
        document_id: documentId,
        page_number: 1,
        source_sha256: source.source_sha256,
        version: 2,
        actor_id: "00000000-0000-0000-0000-000000003508",
        reason: "Historical exclusion",
      },
    };
    render(<SourceUnderstandingReview documentId={documentId} role="admin" />);
    await screen.findByText("Historical exclusion");
    expect(
      screen.queryByRole("button", { name: "Correct this reading" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Review this page again" }),
    );
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "I want to return this page to review, without restoring trust.",
      }),
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Reason for reopening this page" }),
      { target: { value: "Reconsider this page" } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Return page to review" }),
    );
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toEqual({
      path: `/api/v1/admin/materials/${documentId}/pages/1/understanding/reopen`,
      body: {
        expected_version: 2,
        confirm_reopen: true,
        reason: "Reconsider this page",
      },
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Review this page again" }),
      ).toBeEnabled(),
    );
    expect(
      screen.queryByText("Page checked against the original"),
    ).not.toBeInTheDocument();
  });

  it("keeps a known page language ahead of the workspace hint", async () => {
    context.language = "si";
    context.page = {
      page_number: 1,
      state: "needs_review",
      version: 1,
      candidate_id: candidateId,
      system_text: "Known English page",
      language: "en",
      can_confirm: false,
      risk_codes: [],
      preview_url: "",
      provenance: {},
      diagnostics: {},
      history: [],
    };
    render(<SourceUnderstandingReview documentId={documentId} role="admin" />);
    await screen.findByText(context.document_title);
    expect(screen.getByRole("button", { name: "English" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByRole("heading", { name: "Review page content" }),
    ).toBeVisible();
  });

  it("uses the observed page language when no earlier page language is known and persists only an explicit choice", async () => {
    current.candidate!.content.observation.language = "si";
    render(<SourceUnderstandingReview documentId={documentId} role="admin" />);
    await screen.findByText(context.document_title);
    expect(screen.getByRole("button", { name: "සිංහල" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(window.localStorage.getItem(reviewLanguageKey)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "English" }));
    expect(window.localStorage.getItem(reviewLanguageKey)).toBe("en");
  });

  it.each([
    [401, "Your session has expired. Sign in again before continuing."],
    [403, "Your account does not have permission for this action."],
  ] as const)(
    "reports access failure %s without presenting an ordinary candidate",
    async (status, message) => {
      loadStatus = status;
      render(
        <SourceUnderstandingReview documentId={documentId} role="admin" />,
      );
      expect(await screen.findByText(message)).toBeVisible();
      expect(
        screen.queryByRole("region", { name: "What is visible" }),
      ).not.toBeInTheDocument();
    },
  );

  it("never starts analysis on load and reuses the same explicit request after an interrupted response", async () => {
    current = {
      ...current,
      candidate: null,
      report: null,
      state: "unprocessed",
      version: 0,
    };
    render(<SourceUnderstandingReview documentId={documentId} role="admin" />);
    await screen.findByText(context.document_title);
    expect(writes).toEqual([]);
    verificationStatus = 503;
    fireEvent.click(screen.getByRole("button", { name: "Analyze page" }));
    await screen.findByText(
      "The request could not be completed. Reload the latest reading before trying again.",
    );
    expect(
      screen.getByRole("button", { name: "Retry analysis request" }),
    ).toBeDisabled();
    const first = writes[0].body;
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest reading" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Retry analysis request" }),
      ).toBeEnabled(),
    );
    verificationStatus = 200;
    fireEvent.click(
      screen.getByRole("button", { name: "Retry analysis request" }),
    );
    await waitFor(() => expect(writes).toHaveLength(2));
    expect(writes[1].body).toEqual(first);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Analyze page" }),
      ).toBeEnabled(),
    );
  });

  it.each(["reviewer", "blocked", "removed", "unconfigured"] as const)(
    "keeps the %s state non-authorizing",
    async (mode) => {
      if (mode === "blocked") current.report!.findings[0].severity = "block";
      if (mode === "removed") context.source_active = false;
      if (mode === "unconfigured")
        current = {
          ...current,
          candidate: null,
          report: null,
          state: "unprocessed",
          version: 0,
          provider_available: false,
        };
      render(
        <SourceUnderstandingReview
          documentId={documentId}
          role={mode === "reviewer" ? "reviewer" : "admin"}
        />,
      );
      const image = await screen.findByRole("img", { name: "Original page 1" });
      await act(async () => {
        fireEvent.load(image);
      });
      if (mode === "unconfigured")
        expect(
          screen.getByRole("button", { name: "Analyze page" }),
        ).toBeDisabled();
      else
        expect(
          screen.getByRole("button", { name: "Review this reading" }),
        ).toBeDisabled();
      expect(writes).toEqual([]);
    },
  );

  it("shows only accepted teaching meaning after server verification", async () => {
    const content = current.candidate!.content;
    const identifier = "00000000-0000-0000-0000-000000003504";
    current = {
      ...current,
      version: 2,
      state: "verified",
      trusted: {
        id: identifier,
        source,
        revision: 1,
        observation: content.observation,
        education: { claims: [] },
        resolved_uncertainties: content.uncertainties,
        decision: {
          id: identifier,
          actor_id: "00000000-0000-0000-0000-000000003505",
          source,
          candidate_id: candidateId,
          candidate_fingerprint: "c".repeat(64),
          report_fingerprint: "d".repeat(64),
          verified_content_fingerprint: "e".repeat(64),
          policy_version: "page-understanding-verification.v1",
          source_checker_version: "source-fidelity-v2/rules-2/ucd-15.0.0",
          compared_with_original: true,
          reviewed_region_keys: ["text"],
          accepted_claim_keys: [],
          resolved_uncertainty_keys: ["uncertain"],
          reason: "Checked source, declined the proposed teaching claim.",
        },
      },
    };
    render(<SourceUnderstandingReview documentId={documentId} role="admin" />);
    await screen.findByText("Page checked against the original");
    expect(
      screen.getByRole("region", { name: "Accepted teaching points" }),
    ).toBeVisible();
    expect(
      screen.queryByText("Practise counting in pairs."),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Details checked by the teacher" }),
    ).toBeVisible();
  });

  it("submits only explicit original, region, meaning and uncertainty decisions at the current version", async () => {
    await openReview();
    const confirm = screen.getByRole("button", {
      name: "Confirm checked page",
    });
    expect(confirm).toBeDisabled();
    attest();
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toEqual({
      path: `/api/v1/admin/materials/${documentId}/pages/1/understanding/verify`,
      body: {
        candidate_id: candidateId,
        expected_version: 1,
        compared_with_original: true,
        reviewed_region_keys: ["text"],
        accepted_claim_keys: ["skill"],
        resolved_uncertainty_keys: ["uncertain"],
        reason: "Compared all details with the original.",
      },
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Review this reading" }),
      ).toBeEnabled(),
    );
  });

  it("keeps review choices on image failure and requires comparison again after retry", async () => {
    const image = await openReview();
    attest();
    fireEvent.error(image);
    expect(
      screen.getByRole("button", { name: "Confirm checked page" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("textbox", {
        name: "Reason for accepting this reading",
      }),
    ).toHaveValue("Compared all details with the original.");
    fireEvent.click(screen.getByRole("button", { name: "Try image again" }));
    await act(async () => {
      fireEvent.load(screen.getByRole("img", { name: "Original page 1" }));
    });
    expect(
      screen.getByRole("checkbox", {
        name: "I compared this reading with the original page.",
      }),
    ).not.toBeChecked();
    expect(
      screen.getByRole("button", { name: "Confirm checked page" }),
    ).toBeDisabled();
  });

  it("rebases only by explicit choice and requires a new image comparison", async () => {
    await openReview();
    attest();
    verificationStatus = 409;
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm checked page" }),
    );
    await screen.findByText(
      "This reading changed. Your review choices have been kept.",
    );
    const nextId = "00000000-0000-0000-0000-000000003506";
    current = {
      ...current,
      version: 2,
      candidate: { ...current.candidate!, id: nextId, revision: 2 },
      report: { ...current.report!, candidate_id: nextId },
    };
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest reading" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Review latest reading" }),
      ).toBeEnabled(),
    );
    expect(
      screen.getByRole("img", { name: "Original page 1" }).getAttribute("src"),
    ).toContain(candidateId);
    fireEvent.click(
      screen.getByRole("button", { name: "Review latest reading" }),
    );
    expect(
      screen.getByRole("img", { name: "Original page 1" }).getAttribute("src"),
    ).toContain(nextId);
    expect(
      screen.getByRole("checkbox", {
        name: "I compared this reading with the original page.",
      }),
    ).not.toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "Practise counting in pairs." }),
    ).not.toBeChecked();
    expect(
      screen.getByRole("textbox", {
        name: "Reason for accepting this reading",
      }),
    ).toHaveValue("Compared all details with the original.");
    expect(
      screen.getByRole("button", { name: "Confirm checked page" }),
    ).toBeDisabled();
  });

  it("keeps an unsaved review when the teacher declines to leave", async () => {
    await openReview();
    attest();
    const leave = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(leave).toHaveBeenCalledOnce();
    expect(
      screen.getByRole("textbox", {
        name: "Reason for accepting this reading",
      }),
    ).toHaveValue("Compared all details with the original.");
    expect(
      screen.getByRole("img", { name: "Original page 1" }),
    ).toBeInTheDocument();
    leave.mockRestore();
  });

  it("preserves a conflicted review and never unlocks it after a failed reload", async () => {
    await openReview();
    attest();
    verificationStatus = 409;
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm checked page" }),
    );
    await screen.findByText(
      "This reading changed. Your review choices have been kept.",
    );
    current = { ...current, version: 2 };
    loadStatus = 503;
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest reading" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Confirm checked page" }),
      ).toBeDisabled(),
    );
    expect(
      screen.getByRole("textbox", {
        name: "Reason for accepting this reading",
      }),
    ).toHaveValue("Compared all details with the original.");
    expect(writes).toHaveLength(1);
  });
});
