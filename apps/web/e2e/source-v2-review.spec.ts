import { expect, test, type Page } from "@playwright/test";
import { createHash, randomUUID } from "node:crypto";

import {
  assertDisposableStudio,
  seedAdmittedScope,
  syntheticTextPdf,
} from "./helpers/teacher-content-studio";

/**
 * The Source V2 review loop, driven the way a reviewer drives it.
 *
 * The readings below are deliberately short ASCII/Sinhala fixtures: this proves
 * the *workflow* — confirm, correct, confirm the correction, exclude, reload,
 * and the stale-revision refusal. It is not evidence about real Sinhala
 * reading accuracy, which is only ever established by a human checking the
 * one machine reading against the original page.
 */

const ORIGINAL_HEADING = "ක්‍රියාකාරකම 11";
const MISREAD_HEADING = "ක්‍රියාකාරකම් 11";
const PROSE = "පාසල් වත්තේ හෝ ආසන්න පරිසරයේ හෝ ශාකවල විවිධත්වය සොයා බලන්න.";
const FOOTER = "141";

type ImportResult = { page_id: string; page_number: number; regions: number };
type RegionView = {
  region_id: string;
  candidate_id: string;
  revision: number;
  state: string;
  text: string;
};
type PageView = {
  page_id: string;
  image_sha256: string;
  progress: { total: number; verified: number; excluded: number; unverified: number };
  regions: RegionView[];
};

function region(
  id: string,
  type: string,
  text: string,
  extra: Record<string, unknown> = {},
) {
  return {
    region_id: id,
    region_type: type,
    text,
    abstained: false,
    reason: "primary reading by the executing agent from the canonical crop",
    ...extra,
  };
}

/** Sign in the way an administrator actually does. */
async function signIn(page: Page): Promise<void> {
  await page.goto("/admin/login");
  await page.getByRole("button", { name: "Continue as admin" }).click();
  await page.waitForURL("**/admin/home");
}

/** A real `source_documents` row to hang the Source V2 page off. */
async function seedDocument(page: Page): Promise<string> {
  const headers = await assertDisposableStudio(page.request);
  const scope = await seedAdmittedScope(page.request);
  const marker = `SourceV2-${randomUUID()}`;
  const response = await page.request.post("/api/v1/admin/source-documents", {
    headers,
    multipart: {
      file: {
        name: `${marker}.pdf`,
        mimeType: "application/pdf",
        buffer: syntheticTextPdf([marker], marker).bytes,
      },
      document_type: "teacher_guide",
      curriculum_version_id: scope.curriculum.id,
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  return ((await response.json()) as { id: string }).id;
}

async function importPage(
  page: Page,
  documentId: string,
  extra: ReturnType<typeof region>[] = [],
) {
  const pageNumber = 800_000 + Math.floor(Math.random() * 90_000);
  const sha = createHash("sha256").update(randomUUID()).digest("hex");
  const candidates = [
    region("p001-r001", "heading", MISREAD_HEADING),
    region("p001-r002", "text", PROSE),
    region("p001-r003", "decorative", FOOTER),
    region("p001-r004", "unknown", "", {
      abstained: true,
      reason: "primary reading found no text: no printed text in this region",
    }),
    ...extra,
  ];
  const response = await page.request.post("/api/v1/admin/source-v2/pages", {
    data: {
      document_id: documentId,
      page_number: pageNumber,
      language: "sinhala",
      image_sha256: sha,
      width: 2480,
      height: 3509,
      dpi: 300,
      detector_version: "e2e",
      layout: {
        regions: [
          { id: "p001-r001", bbox: [10, 10, 100, 40] },
          { id: "p001-r005", bbox: [10, 60, 400, 300] },
        ],
      },
      candidates,
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  const body = (await response.json()) as ImportResult;
  expect(body.regions).toBe(candidates.length);
  return { pageId: body.page_id, sha };
}

async function readPage(page: Page, pageId: string): Promise<PageView> {
  const response = await page.request.get(`/api/v1/admin/source-v2/pages/${pageId}`);
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as PageView;
}

function card(page: Page, regionId: string) {
  return page.getByTestId(`region-${regionId}`);
}

test.describe("Source V2 review", () => {
  test("machine first, human decides, and every decision survives a reload", async ({
    page,
  }) => {
    await signIn(page);
    const documentId = await seedDocument(page);
    const { pageId } = await importPage(page, documentId);

    await page.goto(`/admin/source-v2/${pageId}`);
    await expect(page.getByTestId("source-v2-progress")).toContainText("4");

    // The reviewer is never shown an empty box: the reading is already there.
    await expect(card(page, "p001-r001")).toHaveAttribute(
      "data-region-state",
      "unverified",
    );
    await expect(page.getByTestId("text-p001-r001")).toHaveText(MISREAD_HEADING);

    // One machine reading, so no ensemble to compare: no reader panel, no
    // "readers disagree" badge, no OCR wording anywhere on the screen.
    await expect(page.getByText(/Technical details/i)).toHaveCount(0);
    await expect(page.getByText(/readers disagree/i)).toHaveCount(0);
    await expect(page.getByText(/deepseek|lightonocr|\bOCR\b/i)).toHaveCount(0);

    // 1. confirm an acceptable reading
    await page.getByTestId("confirm-p001-r002").click();
    await expect(card(page, "p001-r002")).toHaveAttribute(
      "data-region-state",
      "verified",
    );
    await expect(page.getByTestId("confirm-p001-r002")).toBeDisabled();

    // 2. correct a wrong reading — correcting is not verifying
    await page.getByTestId("correct-p001-r001").click();
    await page.getByTestId("editor-p001-r001").fill(ORIGINAL_HEADING);
    await page.getByTestId("save-p001-r001").click();
    await expect(page.getByTestId("text-p001-r001")).toHaveText(ORIGINAL_HEADING);
    await expect(card(page, "p001-r001")).toHaveAttribute(
      "data-region-state",
      "unverified",
    );

    // 3. confirm the corrected text
    await page.getByTestId("confirm-p001-r001").click();
    await expect(card(page, "p001-r001")).toHaveAttribute(
      "data-region-state",
      "verified",
    );

    // 3b. verification is reversible, and editing resumes from the human text
    //
    // The reviewer must be able to change their mind. The affordance renames
    // itself once a region is verified, and the editor opens on the text the
    // human verified — not empty, and not the machine's original reading,
    // either of which would silently discard their correction.
    await expect(page.getByTestId("correct-p001-r001")).toBeEnabled();
    await expect(page.getByTestId("correct-p001-r001")).toHaveText(/Edit again/i);
    await page.getByTestId("correct-p001-r001").click();
    await expect(page.getByTestId("editor-p001-r001")).toHaveValue(ORIGINAL_HEADING);

    // Saving an edit withdraws verification and demands it again.
    await page.getByTestId("editor-p001-r001").fill(`${ORIGINAL_HEADING} (සංශෝධිත)`);
    await page.getByTestId("save-p001-r001").click();
    await expect(card(page, "p001-r001")).toHaveAttribute(
      "data-region-state",
      "unverified",
    );
    await page.getByTestId("confirm-p001-r001").click();
    await expect(card(page, "p001-r001")).toHaveAttribute(
      "data-region-state",
      "verified",
    );

    // 3c. the card's Space/Enter shortcut must not eat the editor's keys
    //
    // The region card is a focusable `li` that selects itself on Space/Enter.
    // Key events bubble, so those keys typed into the correction textarea
    // reached the card and were cancelled: a teacher could not type a space,
    // and Enter inserted no newline. Typed here key by key rather than with
    // fill(), because fill() sets the value directly and would never have
    // caught this.
    await page.getByTestId("correct-p001-r001").click();
    const editor = page.getByTestId("editor-p001-r001");
    await editor.fill("");
    await editor.pressSequentially("ක්‍රියාකාරකම 11");
    await editor.press("Enter");
    await editor.pressSequentially("දෙවන පේළිය");
    const TYPED = "ක්‍රියාකාරකම 11\nදෙවන පේළිය";
    await expect(editor).toHaveValue(TYPED);

    await page.getByTestId("save-p001-r001").click();
    await expect(page.getByTestId("text-p001-r001")).toHaveText(TYPED);
    await expect(card(page, "p001-r001")).toHaveAttribute(
      "data-region-state",
      "unverified",
    );
    await page.getByTestId("confirm-p001-r001").click();
    await expect(card(page, "p001-r001")).toHaveAttribute(
      "data-region-state",
      "verified",
    );

    // 4. exclude page furniture, with a reason
    await page.getByTestId("exclusion-note").fill("running footer, not source content");
    await page.getByTestId("exclude-p001-r003").click();
    await expect(card(page, "p001-r003")).toHaveAttribute(
      "data-region-state",
      "excluded",
    );

    // an abstention cannot be rubber-stamped
    await expect(page.getByTestId("confirm-p001-r004")).toBeDisabled();

    // 5. reload: the decisions are in the database, not in the tab
    await page.reload();
    await expect(card(page, "p001-r001")).toHaveAttribute(
      "data-region-state",
      "verified",
    );
    // The *last* human text, not the first: step 3c corrected it again.
    await expect(page.getByTestId("text-p001-r001")).toHaveText(TYPED);
    await expect(card(page, "p001-r002")).toHaveAttribute(
      "data-region-state",
      "verified",
    );
    await expect(card(page, "p001-r003")).toHaveAttribute(
      "data-region-state",
      "excluded",
    );
    await expect(page.getByTestId("source-v2-progress")).toContainText("2");
  });

  test("a correction can itself be corrected, before and after confirming", async ({
    page,
  }) => {
    // "Text correction appears to work only once" — the reviewer's words.
    // Three corrections in a row with no confirmation between them, then a
    // confirmation, then two more corrections before reconfirming. Every
    // step must cite the revision the previous step created, so a UI that
    // failed to pick up the refreshed candidate would be refused with a 409
    // rather than quietly passing this test.
    await signIn(page);
    const documentId = await seedDocument(page);
    const { pageId } = await importPage(page, documentId);
    await page.goto(`/admin/source-v2/${pageId}`);

    const editor = page.getByTestId("editor-p001-r001");
    const shown = page.getByTestId("text-p001-r001");
    const state = card(page, "p001-r001");

    async function correct(next: string, expectedPreload: string) {
      await page.getByTestId("correct-p001-r001").click();
      await expect(editor).toHaveValue(expectedPreload);
      await editor.fill(next);
      await page.getByTestId("save-p001-r001").click();
      await expect(shown).toHaveText(next);
    }

    // r1 -> r2 -> r3 -> r4, no confirmation anywhere in between.
    await expect(shown).toHaveText(MISREAD_HEADING);
    await correct(ORIGINAL_HEADING, MISREAD_HEADING);
    await correct(`${ORIGINAL_HEADING} (දෙවන)`, ORIGINAL_HEADING);
    await correct(`${ORIGINAL_HEADING} (තෙවන)`, `${ORIGINAL_HEADING} (දෙවන)`);
    await expect(state).toHaveAttribute("data-region-state", "unverified");

    let current = (await readPage(page, pageId)).regions.find(
      (item) => item.region_id === "p001-r001",
    )!;
    expect(current.revision).toBe(4);
    expect(current.text).toBe(`${ORIGINAL_HEADING} (තෙවන)`);

    // Confirm only the final revision.
    await page.getByTestId("confirm-p001-r001").click();
    await expect(state).toHaveAttribute("data-region-state", "verified");
    await expect(page.getByTestId("correct-p001-r001")).toHaveText(/Edit again/i);

    // Edit again, and edit that again before reconfirming.
    await correct(`${ORIGINAL_HEADING} (හතරවන)`, `${ORIGINAL_HEADING} (තෙවන)`);
    await expect(state).toHaveAttribute("data-region-state", "unverified");
    await correct(`${ORIGINAL_HEADING} (පස්වන)`, `${ORIGINAL_HEADING} (හතරවන)`);

    await page.getByTestId("confirm-p001-r001").click();
    await expect(state).toHaveAttribute("data-region-state", "verified");

    // The last human text is what is verified, and a reload proves the
    // database agrees with the screen.
    await page.reload();
    await expect(page.getByTestId("text-p001-r001")).toHaveText(
      `${ORIGINAL_HEADING} (පස්වන)`,
    );
    await expect(card(page, "p001-r001")).toHaveAttribute(
      "data-region-state",
      "verified",
    );
    current = (await readPage(page, pageId)).regions.find(
      (item) => item.region_id === "p001-r001",
    )!;
    expect(current.revision).toBe(6);
    expect(current.state).toBe("verified");
  });

  test("a corrected figure keeps the reviewer's text visible", async ({ page }) => {
    // The live scar this pins: `p186-r003` is a `visual_only` figure with a
    // correction event carrying 48 characters of human text, and a current
    // candidate carrying none. The card showed "no text in image" whatever
    // the candidate held, so the correction was invisible; the only action
    // left was Confirm visual, which writes the declared emptiness over the
    // candidate and deletes it.
    await signIn(page);
    const documentId = await seedDocument(page);
    const { pageId } = await importPage(page, documentId, [
      region("p001-r005", "figure", "", {
        abstained: true,
        reason: "primary reading found no text: line-art figure only",
        crop_sha256: createHash("sha256").update("e2e-crop").digest("hex"),
      }),
    ]);
    await page.goto(`/admin/source-v2/${pageId}`);

    // A textless figure states the fact and offers the visual confirm.
    await expect(page.getByText("රූපයේ ඇති පෙළ: නොමැත (No text in image)")).toBeVisible();
    await expect(page.getByTestId("confirm-p001-r005")).toBeEnabled();

    const LABEL = "උතුරු ධ්‍රැවය";
    await page.getByTestId("correct-p001-r005").click();
    await page.getByTestId("editor-p001-r005").fill(LABEL);
    await page.getByTestId("save-p001-r005").click();

    // The correction is on the card, not only in the database.
    await expect(page.getByTestId("text-p001-r005")).toHaveText(LABEL);
    await expect(page.getByText("රූපයේ ඇති පෙළ: නොමැත (No text in image)")).toHaveCount(0);
    // And the button that would erase it is gone, replaced by an
    // explanation and the reclassification that actually resolves this.
    await expect(page.getByTestId("confirm-p001-r005")).toHaveCount(0);
    await expect(page.getByTestId("visual-text-note-p001-r005")).toBeVisible();

    await page.getByTestId("text-present-p001-r005").click();
    await expect(page.getByTestId("kind-p001-r005")).toHaveAttribute(
      "data-source-kind",
      "visual_with_text",
    );
    await expect(page.getByTestId("text-p001-r005")).toHaveText(LABEL);

    // Confirming the figure now keeps the words the reviewer typed.
    await page.getByTestId("confirm-p001-r005").click();
    await expect(card(page, "p001-r005")).toHaveAttribute(
      "data-region-state",
      "verified",
    );
    const verified = (await readPage(page, pageId)).regions.find(
      (item) => item.region_id === "p001-r005",
    )!;
    expect(verified.text).toBe(LABEL);
  });

  test("a superseded revision can no longer be confirmed", async ({ page }) => {
    await signIn(page);
    const documentId = await seedDocument(page);
    const { pageId, sha } = await importPage(page, documentId);

    const before = await readPage(page, pageId);
    const target = before.regions.find((item) => item.region_id === "p001-r001");
    expect(target).toBeDefined();

    // the reviewer corrects it, which moves the region to a new revision
    const corrected = await page.request.post(
      `/api/v1/admin/source-v2/pages/${pageId}/regions/p001-r001/correct`,
      {
        data: {
          candidate_id: target!.candidate_id,
          revision: target!.revision,
          corrected_text: ORIGINAL_HEADING,
        },
      },
    );
    expect(corrected.ok(), await corrected.text()).toBe(true);

    // confirming the revision they were looking at before must be refused
    const stale = await page.request.post(
      `/api/v1/admin/source-v2/pages/${pageId}/regions/p001-r001/confirm`,
      {
        data: {
          candidate_id: target!.candidate_id,
          revision: target!.revision,
          compared_with_image_sha256: sha,
        },
      },
    );
    expect(stale.status()).toBe(409);
    expect(await stale.text()).toContain("moved on");

    // and so must a confirmation citing a page image that was never compared
    const current = (await readPage(page, pageId)).regions.find(
      (item) => item.region_id === "p001-r001",
    )!;
    const wrongImage = await page.request.post(
      `/api/v1/admin/source-v2/pages/${pageId}/regions/p001-r001/confirm`,
      {
        data: {
          candidate_id: current.candidate_id,
          revision: current.revision,
          compared_with_image_sha256: createHash("sha256")
            .update("a different render")
            .digest("hex"),
        },
      },
    );
    expect(wrongImage.status()).toBe(409);
  });

  test("the document gate refuses while any region is undecided", async ({ page }) => {
    await signIn(page);
    const documentId = await seedDocument(page);
    await importPage(page, documentId);

    const response = await page.request.get(
      `/api/v1/admin/source-v2/documents/${documentId}/gate`,
    );
    expect(response.ok(), await response.text()).toBe(true);
    const gate = (await response.json()) as { usable: boolean; reason: string | null };
    expect(gate.usable).toBe(false);
    expect(gate.reason).toContain("unresolved pages");
  });
});
