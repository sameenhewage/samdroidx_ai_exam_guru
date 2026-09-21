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
/** What `p186-r007` actually carries: the printed folio. Not source content. */
const FOLIO = "171";
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

/** A running header or folio: printed, read correctly, and not source content. */
function decorative(id: string, overrides: RegionOverrides = {}) {
  return region(id, {
    region_type: "decorative",
    source_kind: "decorative",
    proposed_source_kind: "decorative",
    text: FOLIO,
    crop_sha256: null,
    crop_url: null,
    ...overrides,
  });
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

describe("SourceV2Review editor keyboard ownership", () => {
  /**
   * The card is a focusable `li` that activates on Space/Enter. Key events
   * bubble, so a Space typed into the correction textarea reached the card's
   * handler and was `preventDefault()`-ed: the teacher could not type a space,
   * and Enter could not insert a newline.
   *
   * jsdom does not do native text insertion, so what these tests pin is the
   * thing that actually broke - whether the card cancels a key event it does
   * not own. A cancelled keydown is exactly what stops the browser inserting
   * the character.
   */

  async function openTextEditor(id: string) {
    const item = await card(id);
    fireEvent.click(within(item).getByTestId(`edit-text-${id}`));
    return await screen.findByTestId(`editor-${id}`);
  }

  it("does not cancel Space or Enter typed inside the correction editor", async () => {
    serve([
      region("p186-r003", {
        source_kind: "visual_with_text",
        text: "පහත දැක්වෙන රූපය",
        detected_labels: [LABEL_TEXT],
      }),
    ]);
    render(<SourceV2Review pageId={pageId} />);
    const editor = await openTextEditor("p186-r003");

    expect(fireEvent.keyDown(editor, { key: " ", code: "Space" })).toBe(true);
    expect(fireEvent.keyDown(editor, { key: "Enter", code: "Enter" })).toBe(true);
  });

  it("keeps whitespace the teacher types, including newlines", async () => {
    serve([region("p186-r003", { source_kind: "visual_with_text", text: "පෙළ" })]);
    render(<SourceV2Review pageId={pageId} />);
    const editor = (await openTextEditor("p186-r003")) as HTMLTextAreaElement;

    const typed = "පළමු පේළිය\nදෙවන  පේළිය ";
    fireEvent.change(editor, { target: { value: typed } });
    expect(editor.value).toBe(typed);
    expect(editor.value).toContain(" ");
    expect(editor.value).toContain("\n");
  });

  it("still activates the card when the card itself owns the key", async () => {
    serve([region("p186-r002"), region("p186-r003", { source_kind: "visual_with_text" })]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r003");

    fireEvent.keyDown(item, { key: " ", code: "Space", target: item });
    await waitFor(() => expect(item.dataset.selected).toBe("true"));
  });

  it("activates the card on Enter as well as Space", async () => {
    serve([region("p186-r002"), region("p186-r003", { source_kind: "visual_with_text" })]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r003");

    fireEvent.keyDown(item, { key: "Enter", code: "Enter" });
    await waitFor(() => expect(item.dataset.selected).toBe("true"));
  });

  it("does not hijack keys aimed at a nested button", async () => {
    serve([region("p186-r003", { source_kind: "visual_with_text", text: "පෙළ" })]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r003");
    const button = within(item).getByTestId("confirm-p186-r003");

    // A cancelled keydown would stop the browser firing the button's own
    // activation, so the card must leave it alone.
    expect(fireEvent.keyDown(button, { key: " ", code: "Space" })).toBe(true);
    expect(fireEvent.keyDown(button, { key: "Enter", code: "Enter" })).toBe(true);
  });

  it("does not cancel Space or Enter inside the description editor either", async () => {
    serve([
      region("p186-r002", {
        visual_description: DESCRIPTION,
      }),
    ]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r002");
    fireEvent.click(within(item).getByTestId("edit-description-p186-r002"));
    const editor = (await screen.findByTestId(
      "description-editor-p186-r002",
    )) as HTMLTextAreaElement;

    expect(fireEvent.keyDown(editor, { key: " ", code: "Space" })).toBe(true);
    expect(fireEvent.keyDown(editor, { key: "Enter", code: "Enter" })).toBe(true);

    const typed = `${DESCRIPTION}\nදෙවන පේළියක් ද ඇත.`;
    fireEvent.change(editor, { target: { value: typed } });
    expect(editor.value).toBe(typed);
  });

  it("preloads the description editor with the saved description", async () => {
    serve([region("p186-r002", { visual_description: DESCRIPTION })]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r002");
    fireEvent.click(within(item).getByTestId("edit-description-p186-r002"));

    const editor = (await screen.findByTestId(
      "description-editor-p186-r002",
    )) as HTMLTextAreaElement;
    expect(editor.value).toBe(DESCRIPTION);
  });
});

/**
 * Decorative regions offer the decisions that exist, and only those.
 *
 * Reported from the real pilot: `p186-r000` (a running header) and
 * `p186-r007` (the folio `171`) are `decorative`, and the card showed the
 * ordinary green "පෙළ නිවැරදියි" button for both. Pressing it returned 422,
 * because decorative content can never become Verified Source Content — and
 * the error told the reviewer to use confirm-visual, which refuses decorative
 * too. A button whose only possible outcome is an error is the bug; disabling
 * it would only make the dead end quieter.
 */
describe("SourceV2Review decorative regions", () => {
  it("offers no ordinary text-confirm button at all, not even a disabled one", async () => {
    serve([decorative("p186-r007")]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r007");

    expect(within(item).queryByTestId("confirm-p186-r007")).not.toBeInTheDocument();
    // Not merely absent by test id: no control anywhere on the card carries
    // the confirm wording, enabled or otherwise.
    expect(within(item).queryByText("පෙළ නිවැරදියි")).not.toBeInTheDocument();
    expect(within(item).queryByText("රූපය තහවුරු කරන්න")).not.toBeInTheDocument();
    // And the card says why, rather than leaving a gap where a button was.
    expect(within(item).getByTestId("decorative-note-p186-r007")).toHaveTextContent(
      "මූලාශ්‍ර අන්තර්ගතයක් ලෙස තහවුරු කළ නොහැක",
    );
  });

  it("makes Exclude the normal resolution, enabled and prominent", async () => {
    const calls = serve([decorative("p186-r000", { text: "පරිසරය ආශ්‍රිත ක්‍රියාකාරකම්" })]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r000");

    const exclude = within(item).getByTestId("exclude-p186-r000");
    expect(exclude).toBeEnabled();
    expect(exclude).toHaveTextContent("මෙම කොටස භාවිත නොකරන්න");

    fireEvent.click(exclude);
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith("/exclude"))).toBe(true),
    );
    const excluded = calls.find((call) => call.url.endsWith("/exclude"))!;
    expect(excluded.method).toBe("POST");
    // The permanent reason says what this is, not "it was not read": the
    // machine read the header perfectly well.
    expect(excluded.body).toEqual({
      candidate_id: "00000000-0000-0000-0000-00000000c001",
      revision: 1,
      note: "අලංකරණ කොටසකි; මූලාශ්‍ර අන්තර්ගතයක් නොවේ.",
    });
  });

  it("exposes an explicit Change-kind control listing every destination kind", async () => {
    serve([decorative("p186-r007")]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r007");

    const select = within(item).getByTestId("kind-select-p186-r007");
    // Reachable by its visible label, not only by test id.
    expect(within(item).getByLabelText("මෙම කොටස කුමක්ද?")).toBe(select);
    expect(
      [...(select as HTMLSelectElement).options].map((option) => option.value),
    ).toEqual(["text_only", "visual_only", "visual_with_text", "decorative", "undecided"]);
    // It opens on what the region currently is, so nothing is preselected
    // away from the machine's proposal.
    expect(select).toHaveValue("decorative");
    expect(within(item).getByTestId("reclassify-p186-r007")).toHaveTextContent(
      "වර්ගය වෙනස් කරන්න",
    );
  });

  it("reclassifies to text_only against the current candidate and revision", async () => {
    const calls = serve([decorative("p186-r007", { revision: 2 })]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r007");

    fireEvent.change(within(item).getByTestId("kind-select-p186-r007"), {
      target: { value: "text_only" },
    });
    fireEvent.click(within(item).getByTestId("reclassify-p186-r007"));

    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith("/reclassify"))).toBe(true),
    );
    const sent = calls.find((call) => call.url.endsWith("/reclassify"))!;
    expect(sent.method).toBe("POST");
    expect(sent.url).toBe(
      `/api/v1/admin/source-v2/pages/${pageId}/regions/p186-r007/reclassify`,
    );
    expect(sent.body).toEqual({
      candidate_id: "00000000-0000-0000-0000-00000000c001",
      revision: 2,
      source_kind: "text_only",
      note: "reviewer reclassified this region as text_only",
    });
    // Nothing was confirmed on the way past.
    expect(
      calls.some((call) => /\/(confirm|confirm-visual)$/.test(call.url)),
    ).toBe(false);
  });

  it("shows the ordinary text actions once the server says it is text_only", async () => {
    const served = [decorative("p186-r007")];
    serve(served);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r007");

    fireEvent.change(within(item).getByTestId("kind-select-p186-r007"), {
      target: { value: "text_only" },
    });
    // The reload after the decision is what decides the next set of actions,
    // so the server's answer changes here and nowhere else.
    served[0] = region("p186-r007", {
      region_type: "decorative",
      source_kind: "text_only",
      proposed_source_kind: "decorative",
      text: FOLIO,
      crop_sha256: null,
      crop_url: null,
    });
    fireEvent.click(within(item).getByTestId("reclassify-p186-r007"));

    const confirm = await screen.findByTestId("confirm-p186-r007");
    expect(confirm).toBeEnabled();
    expect(confirm).toHaveTextContent("පෙළ නිවැරදියි");
    expect(screen.queryByTestId("decorative-note-p186-r007")).not.toBeInTheDocument();
    expect(screen.queryByTestId("kind-select-p186-r007")).not.toBeInTheDocument();
    expect(screen.getByTestId("text-p186-r007")).toHaveTextContent(FOLIO);
  });

  it("exposes confirm-visual once the server says it is visual_only", async () => {
    const served = [decorative("p186-r000")];
    const calls = serve(served);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r000");

    fireEvent.change(within(item).getByTestId("kind-select-p186-r000"), {
      target: { value: "visual_only" },
    });
    served[0] = region("p186-r000", {
      region_type: "decorative",
      source_kind: "visual_only",
      proposed_source_kind: "decorative",
      text: "",
    });
    fireEvent.click(within(item).getByTestId("reclassify-p186-r000"));

    const confirm = await screen.findByTestId("confirm-p186-r000");
    expect(confirm).toHaveTextContent("රූපය තහවුරු කරන්න");
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith("/confirm-visual"))).toBe(true),
    );
    expect(calls.find((call) => call.url.endsWith("/confirm-visual"))!.body).toEqual({
      candidate_id: "00000000-0000-0000-0000-00000000c001",
      revision: 1,
      compared_with_image_sha256: "a".repeat(64),
      source_kind: "visual_only",
    });
  });

  it("never reclassifies without an explicit click", async () => {
    const calls = serve([decorative("p186-r007")]);
    render(<SourceV2Review pageId={pageId} />);
    const item = await card("p186-r007");

    // Rendering the card decides nothing.
    const wrote = () => calls.filter((call) => call.method !== "GET");
    expect(wrote()).toEqual([]);
    // Nor does the control offering the region's own kind back to it.
    expect(within(item).getByTestId("reclassify-p186-r007")).toBeDisabled();

    // Picking an option is not deciding either: the draft moves, the server
    // is not told, and the reviewer can still change their mind.
    fireEvent.change(within(item).getByTestId("kind-select-p186-r007"), {
      target: { value: "visual_with_text" },
    });
    expect(within(item).getByTestId("kind-select-p186-r007")).toHaveValue(
      "visual_with_text",
    );
    expect(within(item).getByTestId("reclassify-p186-r007")).toBeEnabled();
    await waitFor(() => expect(wrote()).toEqual([]));

    // Putting it back leaves nothing to apply.
    fireEvent.change(within(item).getByTestId("kind-select-p186-r007"), {
      target: { value: "decorative" },
    });
    expect(within(item).getByTestId("reclassify-p186-r007")).toBeDisabled();
    expect(wrote()).toEqual([]);
  });

  it("leaves the visual_only and visual_with_text controls exactly as they were", async () => {
    const calls = serve([
      region("p186-r002"),
      region("p186-r003", { source_kind: "visual_with_text", text: LABEL_TEXT }),
    ]);
    render(<SourceV2Review pageId={pageId} />);

    // visual_only keeps `text-present-`, and it still reclassifies upward.
    fireEvent.click(
      within(await card("p186-r002")).getByTestId("text-present-p186-r002"),
    );
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith("/reclassify"))).toBe(true),
    );
    expect(calls.find((call) => call.url.endsWith("/reclassify"))!.body).toEqual({
      candidate_id: "00000000-0000-0000-0000-00000000c001",
      revision: 1,
      source_kind: "visual_with_text",
      note: "reviewer sees printed text in this figure",
    });

    // visual_with_text keeps the single-button `reclassify-` control rather
    // than gaining the decorative card's select.
    const figure = await card("p186-r003");
    expect(within(figure).getByTestId("reclassify-p186-r003")).toHaveTextContent(
      "වර්ගය වෙනස් කරන්න",
    );
    expect(within(figure).queryByTestId("kind-select-p186-r003")).not.toBeInTheDocument();
    expect(within(figure).getByTestId("confirm-p186-r003")).toBeEnabled();
  });
});
