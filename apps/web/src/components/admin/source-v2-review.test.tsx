/**
 * The visual review card keeps three different things apart.
 *
 * Before this, a figure arrived as one block of text mixing the words printed
 * inside the picture, a machine's description of the picture, and validator
 * diagnostics. On page 186 that produced an English sentence — "Line-art
 * figure only (a foam block, a ring magnet...)" — sitting in the field that
 * means "the exact Sinhala text printed here". A reviewer had no way to tell
 * which part came off the page.
 *
 * These tests pin the separation itself, not the styling: the crop is shown,
 * source and machine-generated content are labelled as such, and the
 * diagnostics live inside a disclosure that starts closed.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SourceV2Review } from "./source-v2-review";

const pageId = "00000000-0000-0000-0000-0000000001b6";

/** "A ring magnet, a paper butterfly and a piece of thread are shown." */
const DESCRIPTION = "වළලු චුම්බකයක්, කඩදාසි සමනලයෙක් සහ නූල් කැබැල්ලක් දැක්වේ.";
const LABEL_TEXT = "රෙජිෆෝම්/මැටි";
const PROSE = "පාසල් වත්තේ හෝ ආසන්න පරිසරයේ හෝ ශාකවල විවිධත්වය සොයා බලන්න.";
const NO_TEXT_REASON =
  "primary reading found no text: Line-art figure only (a foam block, a ring magnet, " +
  "a paper butterfly, a pin, two sticks and a length of thread).";

type RegionOverrides = Record<string, unknown>;

function region(id: string, overrides: RegionOverrides = {}) {
  const base = {
    region_id: id,
    region_type: "figure",
    candidate_id: "00000000-0000-0000-0000-00000000c001",
    revision: 1,
    origin: "machine",
    text: "",
    abstained: false,
    reason: "primary reading by the executing agent from the canonical crop",
    state: "unverified",
    bbox: [10, 20, 110, 90],
    verified_text: null,
    source_kind: "visual_only",
    proposed_source_kind: "visual_only",
    crop_sha256: "d".repeat(64),
    visual_description: null,
    detected_labels: [] as string[],
    crop_url: `/api/v1/admin/source-v2/pages/${pageId}/regions/${id}/crop`,
    ...overrides,
  };
  return {
    ...base,
    technical_evidence: {
      reason: base.reason,
      findings: [],
      uncertainty: [],
      abstained: base.abstained,
      proposed_source_kind: base.proposed_source_kind,
      origin: base.origin,
      revision: base.revision,
      crop_sha256: base.crop_sha256,
      ...((overrides.technical_evidence as RegionOverrides) ?? {}),
    },
  };
}

function pageView(regions: ReturnType<typeof region>[]) {
  return {
    page_id: pageId,
    document_id: "00000000-0000-0000-0000-0000000000d1",
    page_number: 186,
    image_sha256: "a".repeat(64),
    width: 2480,
    height: 3509,
    dpi: 300,
    language: "sinhala",
    detector_version: "test",
    progress: {
      total: regions.length,
      unverified: regions.length,
      verified: 0,
      excluded: 0,
      resolved: 0,
    },
    regions,
  };
}

function serve(regions: ReturnType<typeof region>[]) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = typeof input === "string" ? input : String(input);
    calls.push({
      url: request,
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return Response.json(pageView(regions));
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

beforeEach(() => {
  // jsdom implements no scrolling at all. The card↔overlay selection calls
  // `scrollTo` on both panes and must keep doing so, hence a stub here
  // rather than a weakened component.
  Object.defineProperty(Element.prototype, "scrollTo", {
    configurable: true,
    writable: true,
    value: () => {},
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function card(id: string) {
  return await screen.findByTestId(`region-${id}`);
}

describe("SourceV2Review visual card", () => {
  it("shows a visual_only figure as crop, an explicit 'no text', and a description slot", async () => {
    serve([region("p186-r002", { reason: NO_TEXT_REASON, abstained: true })]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r002");

    // 1. the real crop, fetched from the crop endpoint
    const crop = within(item).getByTestId("crop-p186-r002");
    expect(crop).toHaveAttribute(
      "src",
      `/api/v1/admin/source-v2/pages/${pageId}/regions/p186-r002/crop`,
    );

    // 2. emptiness stated as a fact, in the exact agreed wording
    expect(within(item).getByText("රූපයේ ඇති පෙළ: නොමැත")).toBeVisible();

    // 3. a description slot that is honest about being empty, and about
    //    being machine-generated rather than printed on the page
    expect(within(item).getByTestId("description-p186-r002")).toHaveTextContent(
      "රූප විස්තරයක් තවම ලියා නැත.",
    );
    expect(
      within(item).getByTestId("description-provenance-p186-r002"),
    ).toHaveAttribute("data-provenance", "machine");
    expect(
      within(item).getByTestId("text-in-image-provenance-p186-r002"),
    ).toHaveAttribute("data-provenance", "source");

    // the machine's English abstention prose is not presented as source text
    expect(within(item).queryByTestId("text-p186-r002")).not.toBeInTheDocument();
  });

  it("keeps the three sections distinct for visual_with_text", async () => {
    serve([
      region("p186-r003", {
        source_kind: "visual_with_text",
        proposed_source_kind: "visual_with_text",
        text: LABEL_TEXT,
        visual_description: DESCRIPTION,
        detected_labels: [LABEL_TEXT],
      }),
    ]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r003");

    expect(within(item).getByTestId("crop-p186-r003")).toBeVisible();
    // printed text — source
    expect(within(item).getByTestId("text-p186-r003")).toHaveTextContent(LABEL_TEXT);
    expect(within(item).getByText("රූපයේ ඇති පෙළ")).toBeVisible();
    // description — derived knowledge
    expect(within(item).getByTestId("description-p186-r003")).toHaveTextContent(
      DESCRIPTION,
    );
    expect(within(item).getByText("රූප විස්තරය")).toBeVisible();
    // labels — only rendered because there are some
    expect(within(item).getByText("හඳුනාගත් ලේබල්")).toBeVisible();
    expect(within(item).getByTestId("labels-p186-r003")).toHaveTextContent(LABEL_TEXT);

    // the description is never mistaken for source text
    expect(within(item).getByTestId("text-p186-r003")).not.toHaveTextContent(
      DESCRIPTION,
    );
  });

  it("omits the label list entirely when nothing is legible in the crop", async () => {
    serve([region("p186-r002")]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r002");
    expect(within(item).queryByTestId("labels-p186-r002")).not.toBeInTheDocument();
    expect(within(item).queryByText("හඳුනාගත් ලේබල්")).not.toBeInTheDocument();
  });

  it("hides every diagnostic inside a collapsed technical disclosure", async () => {
    serve([
      region("p186-r003", {
        source_kind: "visual_with_text",
        text: LABEL_TEXT,
        reason:
          "primary reading by the executing agent from the canonical crop; " +
          "2 deterministic finding(s)",
        technical_evidence: {
          findings: ["2 deterministic finding(s)"],
          uncertainty: ["spacing-doubt", "non-text"],
        },
      }),
    ]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r003");

    const details = within(item).getByTestId("technical-p186-r003");
    expect(details.tagName).toBe("DETAILS");
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText("▸ තාක්ෂණික විස්තර")).toBeVisible();

    // Every diagnostic string lives inside that disclosure, not beside the
    // picture. `closest` proves containment rather than mere co-existence.
    for (const needle of [
      /primary reading by the executing agent/,
      /2 deterministic finding\(s\)/,
      /spacing-doubt/,
    ]) {
      const nodes = within(item).getAllByText(needle, { exact: false });
      expect(nodes.length).toBeGreaterThan(0);
      for (const node of nodes) {
        expect(node.closest("details")).toBe(details);
      }
    }
  });

  it("marks an abstention and the crop checksum as technical, not as source", async () => {
    serve([
      region("p186-r002", {
        abstained: true,
        reason: NO_TEXT_REASON,
        technical_evidence: { uncertainty: ["no-text"], findings: ["Line-art figure only"] },
      }),
    ]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r002");
    const details = within(item).getByTestId("technical-p186-r002");
    for (const needle of [/no-text/, new RegExp(`^${"d".repeat(64)}$`), /Line-art/]) {
      const nodes = within(item).getAllByText(needle, { exact: false });
      expect(nodes.length).toBeGreaterThan(0);
      for (const node of nodes) {
        expect(node.closest("details")).toBe(details);
      }
    }
  });

  it("says so plainly when the canonical crop cannot be served", async () => {
    serve([region("p186-r002", { crop_url: null, crop_sha256: null })]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r002");
    expect(within(item).getByTestId("crop-missing-p186-r002")).toBeVisible();
    expect(within(item).queryByTestId("crop-p186-r002")).not.toBeInTheDocument();
  });
});

describe("SourceV2Review description editing", () => {
  it("preloads the saved description rather than an empty box", async () => {
    serve([
      region("p186-r002", { visual_description: DESCRIPTION, state: "verified" }),
    ]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r002");

    fireEvent.click(within(item).getByTestId("describe-p186-r002"));
    expect(within(item).getByTestId("description-editor-p186-r002")).toHaveValue(
      DESCRIPTION,
    );
  });

  it("saves a description as an ordinary edit, never as a verification", async () => {
    const calls = serve([
      region("p186-r002", { visual_description: DESCRIPTION, state: "verified" }),
    ]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r002");

    fireEvent.click(within(item).getByTestId("edit-description-p186-r002"));
    fireEvent.change(within(item).getByTestId("description-editor-p186-r002"), {
      target: { value: `${DESCRIPTION} නූල් කැබැල්ලකි.` },
    });
    fireEvent.click(within(item).getByTestId("save-description-p186-r002"));

    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith("/describe"))).toBe(true),
    );
    const save = calls.find((call) => call.url.endsWith("/describe"))!;
    expect(save.method).toBe("PUT");
    expect(save.body).toEqual({
      candidate_id: "00000000-0000-0000-0000-00000000c001",
      revision: 1,
      visual_description: `${DESCRIPTION} නූල් කැබැල්ලකි.`,
      detected_labels: [],
    });
    // Nothing about a confirmation: no compared_with_image_sha256, and no
    // request to any of the review endpoints.
    expect(save.body).not.toHaveProperty("compared_with_image_sha256");
    expect(
      calls.some((call) => /\/(confirm|confirm-visual|correct|exclude)$/.test(call.url)),
    ).toBe(false);
  });

  it("refuses to save an empty description", async () => {
    serve([region("p186-r002")]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r002");
    fireEvent.click(within(item).getByTestId("describe-p186-r002"));
    expect(within(item).getByTestId("save-description-p186-r002")).toBeDisabled();
  });
});

describe("SourceV2Review text regions", () => {
  it("leaves a text_only region's layout exactly as it was", async () => {
    serve([
      region("p186-r005", {
        region_type: "text",
        source_kind: "text_only",
        proposed_source_kind: "text_only",
        text: PROSE,
        crop_sha256: null,
        crop_url: null,
      }),
    ]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r005");

    expect(within(item).getByTestId("text-p186-r005")).toHaveTextContent(PROSE);
    // None of the visual scaffolding appears for prose.
    for (const testId of [
      "visual-p186-r005",
      "crop-p186-r005",
      "crop-missing-p186-r005",
      "description-p186-r005",
      "technical-p186-r005",
      "describe-p186-r005",
    ]) {
      expect(within(item).queryByTestId(testId)).not.toBeInTheDocument();
    }
    // The old reason line is still where it was.
    expect(within(item).getByText(/^හේතුව:/)).toBeVisible();
  });

  it("still preloads the verified text when editing again", async () => {
    serve([
      region("p186-r005", {
        region_type: "text",
        source_kind: "text_only",
        state: "verified",
        text: "යන්ත්‍රය කියවූ පෙළ",
        verified_text: PROSE,
      }),
    ]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r005");
    fireEvent.click(within(item).getByTestId("correct-p186-r005"));
    expect(within(item).getByTestId("editor-p186-r005")).toHaveValue(PROSE);
  });

  it("keeps the card and its bbox overlay selectable in both directions", async () => {
    serve([region("p186-r002")]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r002");
    const overlay = screen.getByTestId("overlay-p186-r002");

    fireEvent.click(overlay);
    await waitFor(() => expect(item).toHaveAttribute("data-selected", "true"));
    expect(overlay).toHaveAttribute("data-selected", "true");
  });
});
