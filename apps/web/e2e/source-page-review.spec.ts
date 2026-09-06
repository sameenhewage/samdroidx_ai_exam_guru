import type { components } from "@exam-guru/api-client";
import { expect, test, type APIResponse, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

import { requireIsolatedE2ERuntime } from "../playwright-runtime";

type Source = components["schemas"]["SourceDocumentResponse"];
type Workspace = components["schemas"]["PageReviewWorkspaceResponse"];
type ReadJob = components["schemas"]["SourceReadJobResponse"];
type Benchmark = components["schemas"]["SourceBenchmarkResponse"];

// Synthetic ASCII pages prove workflow mechanics, never real-source accuracy.
function syntheticBook(marker: string, count = 40): Buffer {
  const pageIds = Array.from({ length: count }, (_, index) => 4 + index * 2);
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    `<< /Type /Pages /Count ${count} /Kids [${pageIds.map((id) => `${id} 0 R`).join(" ")}] >>`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  for (const [index, pageId] of pageIds.entries()) {
    const lines = Array.from(
      { length: 40 },
      (_, line) =>
        `(${marker} page ${index + 1}, line ${line + 1}: 2 + 2 = 4.) Tj T*`,
    ).join("\n");
    const stream = `BT /F1 10 Tf 15 TL 30 800 Td\n${lines}\nET`;
    objects.push(
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents ${pageId + 1} 0 R >>`,
      `<< /Length ${Buffer.byteLength(stream, "ascii")} >>\nstream\n${stream}\nendstream`,
    );
  }
  let body = "%PDF-1.4\n";
  const offsets = objects.map((object, index) => {
    const offset = Buffer.byteLength(body, "ascii");
    body += `${index + 1} 0 obj\n${object}\nendobj\n`;
    return offset;
  });
  const xref = Buffer.byteLength(body, "ascii");
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  body += offsets
    .map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`)
    .join("");
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(body, "ascii");
}

async function json<T>(response: APIResponse, status = 200): Promise<T> {
  expect(
    response.status(),
    `Unexpected response for ${new URL(response.url()).pathname}`,
  ).toBe(status);
  return response.json() as Promise<T>;
}

async function login(page: Page, role: "admin" | "reviewer") {
  await page.goto("/admin/login");
  await page.getByRole("button", { name: `Continue as ${role}` }).click();
  await expect(page).toHaveURL(/\/admin\/home$/);
}

async function workspace(page: Page, documentId: string, pageNumber = 1) {
  return json<Workspace>(
    await page.request.get(
      `/api/v1/admin/materials/${documentId}/review-workspace?page_number=${pageNumber}`,
    ),
  );
}

async function jump(page: Page, pageNumber: number) {
  await page
    .getByRole("spinbutton", { name: "Page number", exact: true })
    .fill(String(pageNumber));
  await page.getByRole("button", { name: "Go to page", exact: true }).click();
  await expect(
    page.getByRole("img", { name: `Original page ${pageNumber}`, exact: true }),
  ).toBeVisible();
}

async function imageReady(page: Page, pageNumber: number) {
  const image = page.getByRole("img", {
    name: `Original page ${pageNumber}`,
    exact: true,
  });
  await expect
    .poll(() =>
      image.evaluate(
        (element: HTMLImageElement) =>
          element.complete && element.naturalWidth > 0,
      ),
    )
    .toBe(true);
}

async function benchmark(page: Page, id: string) {
  return json<Benchmark>(
    await page.request.get(`/api/v1/admin/source-benchmarks/${id}`),
  );
}

test("real APIs: private page comparison, versioned drafts, explicit decisions and honest 40-page reference counts", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const runtime = requireIsolatedE2ERuntime(process.env);
  await page.setViewportSize({ width: 1280, height: 800 });
  await login(page, "admin");
  // Fail closed even if this spec is launched outside the normal global setup.
  const identity = await json<{
    application_env: string;
    test_runtime_id: string;
  }>(await page.request.get("/api/v1/admin/studio-safety/runtime-identity"));
  expect(identity).toMatchObject({
    application_env: "test",
    test_runtime_id: runtime.composeProjectName,
  });
  const headers = { Origin: runtime.baseURL, "Sec-Fetch-Site": "same-origin" };
  const marker = `Synthetic ${randomUUID().replaceAll("-", "").slice(0, 12)}`;
  const filename = `${marker.replaceAll(" ", "-")}.pdf`;
  const original = syntheticBook(marker);
  const source = await json<Source>(
    await page.request.post("/api/v1/admin/source-documents", {
      headers,
      multipart: {
        file: { name: filename, mimeType: "application/pdf", buffer: original },
        document_type: "other_approved",
        intake_metadata: JSON.stringify({
          candidate_grade: 5,
          medium_label: "English",
          subject_label: "Mathematics",
          document_type_label: "Synthetic workflow pages",
          year: 2026,
          evidence: [
            "Disposable browser workflow fixture, not educational ground truth",
          ],
        }),
      },
    }),
    201,
  );
  expect(source.metadata_review_required).toBe(true);
  expect((await workspace(page, source.id)).progress.total_pages).toBe(0);

  await page.goto("/admin/materials");
  await expect(
    page
      .getByRole("region", { name: "Materials by grade" })
      .getByRole("button", { name: /^Grade \d+/ }),
  ).toHaveCount(13);
  const material = page.locator("article").filter({
    has: page.getByRole("heading", { name: filename, exact: true }),
  });
  await expect(material.getByText("පද්ධතිය හඳුනාගත් තොරතුරු")).toBeVisible();
  await expect(material.getByText("Metadata needs review")).toBeVisible();
  await material
    .getByRole("link", { name: `Review extracted text: ${filename}` })
    .click();
  await expect(
    page.getByText("No pages are available for comparison yet."),
  ).toBeVisible();
  const accepted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith(`/source-documents/${source.id}/read`),
  );
  await page
    .getByRole("button", { name: "Read document", exact: true })
    .click();
  const readResponse = await accepted;
  expect(readResponse.status()).toBe(202);
  const readJob = (await readResponse.json()) as ReadJob;
  await expect
    .poll(
      async () => {
        const job = await json<ReadJob>(
          await page.request.get(
            `/api/v1/admin/source-read-jobs/${readJob.id}`,
          ),
        );
        return job.status;
      },
      { timeout: 90_000, intervals: [500, 1000, 2000] },
    )
    .toBe("completed");
  const initial = await workspace(page, source.id);
  expect(initial.progress).toMatchObject({
    total_pages: 40,
    processed_pages: 40,
    verified_pages: 0,
    excluded_pages: 0,
    remaining_pages: 40,
  });

  const selection = {
    name: `${marker} review set`,
    pages: Array.from({ length: 40 }, (_, index) => ({
      document_id: source.id,
      page_number: index + 1,
      categories: ["synthetic-workflow-only"],
    })),
    selection: { purpose: "Disposable UI mechanics; not real-source accuracy" },
  } satisfies components["schemas"]["SourceBenchmarkCreateRequest"];
  const set = await json<Benchmark>(
    await page.request.post("/api/v1/admin/source-benchmarks", {
      headers,
      data: selection,
    }),
    201,
  );
  expect(set.adjudicated_pages).toBe(0);
  await page.goto("/admin/materials");
  await page
    .getByRole("link", { name: "Review selected source pages", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Review set", exact: true })
    .selectOption(set.id);
  await expect(
    page.getByRole("table", { name: "Selected source pages" }).getByRole("row"),
  ).toHaveCount(41);
  await expect(
    page.getByText(
      "Accuracy is not established. These pages still need human comparison.",
    ),
  ).toBeVisible();
  await page
    .getByRole("link", { name: `Compare page 1 of ${filename}`, exact: true })
    .click();
  await imageReady(page, 1);
  await expect(
    page.getByRole("textbox", { name: "Correction", exact: true }),
  ).toHaveCount(0);
  const originalPanel = page.getByRole("region", {
    name: "Original page",
    exact: true,
  });
  const textPanel = page.getByTestId("text-scroll-panel");
  const leftBounds = await originalPanel.boundingBox();
  const rightBounds = await page
    .getByRole("region", { name: "System-read text", exact: true })
    .boundingBox();
  expect(leftBounds).not.toBeNull();
  expect(rightBounds).not.toBeNull();
  expect(leftBounds!.x + leftBounds!.width).toBeLessThan(rightBounds!.x);
  expect(leftBounds!.y).toBeCloseTo(rightBounds!.y, 0);
  expect(leftBounds!.height).toBeGreaterThan(100);
  const dimensions = await page
    .getByTestId("source-page-workspace")
    .evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return { bottom: rect.bottom, viewport: innerHeight };
    });
  expect(dimensions.bottom).toBeLessThanOrEqual(dimensions.viewport + 1);
  await originalPanel.evaluate((element) => {
    element.scrollTop = 120;
  });
  expect(
    await originalPanel.evaluate((element) => element.scrollTop),
  ).toBeGreaterThan(0);
  expect(await textPanel.evaluate((element) => element.scrollTop)).toBe(0);
  const originalScroll = await originalPanel.evaluate(
    (element) => element.scrollTop,
  );
  await textPanel.evaluate((element) => {
    element.scrollTop = 140;
  });
  expect(
    await textPanel.evaluate((element) => element.scrollTop),
  ).toBeGreaterThan(0);
  expect(await originalPanel.evaluate((element) => element.scrollTop)).toBe(
    originalScroll,
  );
  const actionBounds = await page
    .getByRole("group", { name: "Page actions" })
    .boundingBox();
  expect(actionBounds).not.toBeNull();
  expect(actionBounds!.y + actionBounds!.height).toBeLessThanOrEqual(801);
  expect(await page.evaluate(() => scrollY)).toBe(0);

  await page.getByRole("button", { name: "Last page", exact: true }).click();
  await imageReady(page, 40);
  await page
    .getByRole("button", { name: "Previous flagged page", exact: true })
    .click();
  await imageReady(page, 39);
  await page
    .getByRole("button", { name: "Next flagged page", exact: true })
    .click();
  await imageReady(page, 40);
  await page.getByRole("button", { name: "First page", exact: true }).click();
  await imageReady(page, 1);
  await jump(page, 20);
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await imageReady(page, 21);
  await page
    .getByRole("button", { name: "Previous page", exact: true })
    .click();
  await imageReady(page, 20);
  await jump(page, 1);
  expect(await page.getByRole("img").count()).toBe(1);

  const textBefore = await page.getByTestId("system-page-text").textContent();
  await page
    .getByRole("button", { name: "Correct the text", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Correction", exact: true })
    .fill("A draft to discard");
  await page
    .getByRole("button", { name: "Cancel editing", exact: true })
    .click();
  expect(await page.getByTestId("system-page-text").textContent()).toBe(
    textBefore,
  );
  await page
    .getByRole("button", { name: "Correct the text", exact: true })
    .click();
  const draft =
    "ශ්‍රී ලංකාව — ක්‍රියා\nதமிழ் க்ஷேத்திரம்\n  ½ × 2 = 1; x² ≤ 4; √9 = 3; π ≠ Ω  ";
  await page
    .getByRole("textbox", { name: "Correction", exact: true })
    .fill(draft);
  await page
    .getByRole("textbox", { name: "Reason for correction" })
    .fill("Synthetic Unicode preservation test; not confirmed");
  const current = await workspace(page, source.id);
  await json(
    await page.request.post(
      `/api/v1/admin/materials/${source.id}/pages/1/edit`,
      {
        headers,
        data: {
          expected_version: current.page!.version,
          text: "Concurrent synthetic reading",
          reason: "Exercise real conflict recovery",
        },
      },
    ),
  );
  await page
    .getByRole("button", { name: "Save correction", exact: true })
    .click();
  await expect(
    page.getByTestId("source-page-workspace").getByRole("alert"),
  ).toContainText("This page changed in another session");
  await expect(
    page.getByRole("textbox", { name: "Correction", exact: true }),
  ).toHaveValue(draft);
  await page
    .getByRole("button", { name: "Reload latest page, keeping correction" })
    .click();
  await expect(
    page.getByRole("region", { name: "Latest system text" }),
  ).toContainText("Concurrent synthetic reading");
  await expect(
    page.getByRole("button", { name: "Save correction", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Use this version for my correction" })
    .click();
  await page
    .getByRole("button", { name: "Save correction", exact: true })
    .click();
  await expect(
    page.getByText(
      "Correction saved. Compare it with the original before confirming.",
    ),
  ).toBeVisible();
  expect(await page.getByTestId("system-page-text").textContent()).toBe(draft);
  const saved = await workspace(page, source.id);
  expect(saved.page!.system_text).toBe(draft);
  expect(saved.page!.state).toBe("needs_review");
  expect((await benchmark(page, set.id)).adjudicated_pages).toBe(0);
  await page.evaluate(async () => {
    await document.fonts.load('400 16px "Noto Sans Sinhala"', "ශ්‍රී ලංකාව");
    await document.fonts.load('400 16px "Noto Sans Tamil"', "தமிழ்");
    await document.fonts.ready;
  });
  expect(
    await page
      .getByTestId("system-page-text")
      .evaluate((element) => getComputedStyle(element).fontFamily),
  ).toContain("Noto Sans Sinhala");
  const fontUrls = await page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .map((entry) => entry.name)
      .filter((name) => /\.woff2(?:\?|$)/.test(name)),
  );
  expect(fontUrls.length).toBeGreaterThan(0);
  expect(fontUrls.every((url) => new URL(url).origin === runtime.baseURL)).toBe(
    true,
  );

  await jump(page, 2);
  await page
    .getByRole("button", { name: "Do not use this page", exact: true })
    .click();
  const exclusion = page.getByRole("dialog", {
    name: "Exclude this page from use?",
  });
  await exclusion
    .getByRole("textbox", { name: "Reason for not using this page" })
    .fill("Synthetic exclusion workflow check");
  await expect(
    exclusion.getByRole("button", { name: "Exclude page", exact: true }),
  ).toBeDisabled();
  await exclusion.getByRole("checkbox").check();
  await exclusion
    .getByRole("button", { name: "Exclude page", exact: true })
    .click();
  await expect(
    page.getByText("Excluded from use. Its history is preserved."),
  ).toBeVisible();
  expect((await benchmark(page, set.id)).adjudicated_pages).toBe(0);

  // Inject only a network failure; all successful source, review and image data comes from the real API.
  const previewPattern = `**/materials/${source.id}/pages/3/image*`;
  await page.route(previewPattern, (route) => route.abort("failed"));
  await jump(page, 3);
  await expect(originalPanel.getByRole("alert")).toContainText(
    "The original page could not be loaded",
  );
  await expect(
    page.getByRole("button", { name: "Text is correct", exact: true }),
  ).toBeDisabled();
  await page.unroute(previewPattern);
  await page
    .getByRole("button", { name: "Try image again", exact: true })
    .click();
  await imageReady(page, 3);
  await page
    .getByRole("button", { name: "Text is correct", exact: true })
    .click();
  const confirmation = page.getByRole("dialog", { name: "Confirm this page" });
  await expect(confirmation.getByRole("checkbox")).not.toBeChecked();
  await expect(
    confirmation.getByRole("button", { name: "Confirm compared text" }),
  ).toBeDisabled();
  await confirmation.getByRole("checkbox").check();
  await confirmation
    .getByRole("button", { name: "Confirm compared text" })
    .click();
  await expect(page.getByText("Confirmed against the original.")).toBeVisible();
  expect((await benchmark(page, set.id)).adjudicated_pages).toBe(1);
  expect((await workspace(page, source.id, 3)).ready_for_ai).toBe(false);

  await jump(page, 4);
  const rereadAccepted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith(`/materials/${source.id}/pages/4/reread`),
  );
  await page.getByRole("button", { name: "Read again", exact: true }).click();
  const rereadResponse = await rereadAccepted;
  expect(rereadResponse.status()).toBe(202);
  const rereadJob = (await rereadResponse.json()) as ReadJob;
  await expect
    .poll(
      async () =>
        (
          await json<ReadJob>(
            await page.request.get(
              `/api/v1/admin/source-read-jobs/${rereadJob.id}`,
            ),
          )
        ).status,
      { timeout: 60_000 },
    )
    .toBe("completed");
  expect((await workspace(page, source.id, 4)).page!.state).toBe(
    "needs_review",
  );
  const originalResponse = await page.request.get(
    `/api/v1/admin/source-documents/${source.id}/content`,
  );
  expect(await originalResponse.body()).toEqual(original);

  await page
    .getByRole("link", { name: "Back to review queue", exact: true })
    .click();
  const setProgress = page.getByRole("region", { name: "Review set progress" });
  await expect(
    setProgress
      .locator("div")
      .filter({
        has: page.getByText("Human-confirmed reference pages", { exact: true }),
      })
      .last(),
  ).toHaveText(/Human-confirmed reference pages\s*1$/);
  const finalSet = await benchmark(page, set.id);
  expect(finalSet).toMatchObject({ adjudicated_pages: 1, pending_pages: 39 });
  expect(finalSet.pages[0].ground_truth_versions).toBe(0);
  expect(finalSet.pages[1]).toMatchObject({
    state: "excluded",
    ground_truth_versions: 0,
  });
  expect(finalSet.pages[2]).toMatchObject({
    state: "verified",
    ground_truth_versions: 1,
  });
  await expect(
    page.getByText(
      "Human-confirmed references are available. This is not an accuracy result.",
    ),
  ).toBeVisible();

  await page.context().clearCookies();
  await login(page, "reviewer");
  await page.goto(`/admin/materials/${source.id}/review-text?page_number=4`);
  await imageReady(page, 4);
  for (const name of [
    "Text is correct",
    "Read again",
    "Correct the text",
    "Do not use this page",
  ])
    await expect(
      page.getByRole("button", { name, exact: true }),
    ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Next page", exact: true }),
  ).toBeEnabled();
});
