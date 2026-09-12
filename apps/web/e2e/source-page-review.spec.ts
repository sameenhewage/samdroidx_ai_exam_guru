import type { components } from "@exam-guru/api-client";
import {
  expect,
  test,
  type APIResponse,
  type Locator,
  type Page,
} from "@playwright/test";
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

async function imageReady(
  page: Page,
  pageNumber: number,
  label = "Original page",
) {
  const image = page.getByRole("img", {
    name: `${label} ${pageNumber}`,
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

async function expectReadableButton(button: Locator) {
  await expect(button).toBeVisible();
  const computed = await button.evaluate((element) => {
    const style = getComputedStyle(element);
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d")!;
    const luminance = (color: string) => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      const channels = context.getImageData(0, 0, 1, 1).data;
      if (channels[3] !== 255)
        throw new Error("Primary button colors must be opaque");
      const [red, green, blue] = Array.from(channels)
        .slice(0, 3)
        .map((value) => {
          const channel = value / 255;
          return channel <= 0.04045
            ? channel / 12.92
            : ((channel + 0.055) / 1.055) ** 2.4;
        });
      return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
    };
    const foreground = luminance(style.color);
    const background = luminance(style.backgroundColor);
    return {
      contrast:
        (Math.max(foreground, background) + 0.05) /
        (Math.min(foreground, background) + 0.05),
      color: style.color,
      backgroundColor: style.backgroundColor,
      cursor: style.cursor,
      opacity: style.opacity,
    };
  });
  expect(computed.contrast).toBeGreaterThanOrEqual(4.5);
  await expect(button).toHaveCSS("opacity", "1");
  return computed;
}

test("real APIs: independent evaluation references preserve blocked sources and concurrent human drafts", async ({
  page,
}, testInfo) => {
  const runtime = requireIsolatedE2ERuntime(process.env);
  await login(page, "admin");
  expect(
    await json(
      await page.request.get("/api/v1/admin/studio-safety/runtime-identity"),
    ),
  ).toMatchObject({
    application_env: "test",
    test_runtime_id: runtime.composeProjectName,
  });
  const headers = { Origin: runtime.baseURL, "Sec-Fetch-Site": "same-origin" };
  const marker = `ReferenceFixture-${randomUUID().slice(0, 8)}`;
  const source = await json<Source>(
    await page.request.post("/api/v1/admin/source-documents", {
      headers,
      multipart: {
        file: {
          name: `${marker}.pdf`,
          mimeType: "application/pdf",
          buffer: syntheticBook(marker, 1),
        },
        document_type: "other_approved",
        intake_metadata: JSON.stringify({
          candidate_grade: 5,
          medium_label: "English",
          subject_label: "Synthetic workflow only",
        }),
      },
    }),
    201,
  );
  const job = await json<ReadJob>(
    await page.request.post(
      `/api/v1/admin/source-documents/${source.id}/read`,
      { headers },
    ),
    202,
  );
  await expect
    .poll(
      async () =>
        (
          await json<ReadJob>(
            await page.request.get(`/api/v1/admin/source-read-jobs/${job.id}`),
          )
        ).status,
      { timeout: 90_000 },
    )
    .toBe("completed");
  const read = await workspace(page, source.id);
  await json(
    await page.request.post(
      `/api/v1/admin/materials/${source.id}/pages/1/edit`,
      {
        headers,
        data: {
          expected_version: read.page!.version,
          text: "An intentionally incomplete synthetic reading.",
          reason:
            "Exercise blocked-page evaluation without source confirmation",
        },
      },
    ),
  );
  const before = await workspace(page, source.id);
  expect(before.page!.can_confirm).toBe(false);
  expect(before.progress.verified_pages).toBe(0);
  const set = await json<Benchmark>(
    await page.request.post("/api/v1/admin/source-benchmarks", {
      headers,
      data: {
        name: marker,
        pages: [
          {
            document_id: source.id,
            page_number: 1,
            categories: ["synthetic-evaluation-only"],
          },
        ],
        selection: {
          purpose:
            "Disposable reference workflow test, not real-source accuracy",
        },
      },
    }),
    201,
  );
  const path = `/api/v1/admin/source-benchmarks/${set.id}/pages/${source.id}/1`;
  const imagePattern = "**/evaluation-previews/*/image";
  await page.route(imagePattern, (route) => route.abort("failed"));
  await page.goto(`/admin/materials/benchmark-review?benchmark_id=${set.id}`);
  await page
    .getByRole("button", {
      name: `Add evaluation reference for page 1 of ${marker}.pdf`,
    })
    .click();
  const editor = page.getByRole("region", {
    name: "Evaluation reference editor",
    exact: true,
  });
  await editor.getByRole("button", { name: "English", exact: true }).click();
  await expect(editor.getByRole("alert")).toContainText(
    "The original image could not be verified",
  );
  await editor
    .getByRole("textbox", { name: "Reference text", exact: true })
    .fill("Preserve this temporary synthetic draft");
  await page.unroute(imagePattern);
  await editor
    .getByRole("button", { name: "Try image again", exact: true })
    .click();
  await imageReady(page, 1, "Original comparison page");
  await expect(
    editor.getByRole("textbox", { name: "Reference text", exact: true }),
  ).toHaveValue("Preserve this temporary synthetic draft");
  await editor
    .getByRole("textbox", { name: "Reference text", exact: true })
    .fill("");
  const input = editor.getByRole("textbox", {
    name: "Reference text",
    exact: true,
  });
  const save = editor.getByRole("button", {
    name: "Save evaluation reference",
    exact: true,
  });
  await expect(input).toHaveValue("");
  await expect(save).toBeDisabled();
  for (const viewport of [
    { width: 1366, height: 768 },
    { width: 1280, height: 720 },
  ]) {
    await page.setViewportSize(viewport);
    await page.evaluate(() => document.fonts.ready.then(() => undefined));
    await expect(save).toBeInViewport({ ratio: 1 });
    expect(
      await page.evaluate(() => document.documentElement.scrollHeight),
    ).toBeLessThanOrEqual(viewport.height + 1);
    expect((await input.boundingBox())!.height).toBeGreaterThanOrEqual(240);
    await expectReadableButton(save);
    await expect(save).toHaveCSS("cursor", "not-allowed");
    await testInfo.attach(`reference-layout-${viewport.width}`, {
      body: await page.screenshot(),
      contentType: "image/png",
    });
  }
  const draft = Array.from(
    { length: 40 },
    (_, index) => `${marker} page 1, line ${index + 1}: 2 + 2 = 4.`,
  ).join("\n");
  await input.fill(draft);
  await editor
    .getByRole("textbox", { name: "Reason for reference" })
    .fill("Compare the complete synthetic fixture transcription");
  await editor
    .getByRole("checkbox", { name: /I compared this reference/ })
    .check();
  await expect(save).toBeEnabled();
  await expect(save).toHaveCSS("cursor", "pointer");
  await expectReadableButton(save);
  await save.hover();
  await expectReadableButton(save);
  await save.focus();
  await expect(save).toBeFocused();
  await expectReadableButton(save);
  const competing = await json<
    components["schemas"]["EvaluationPreviewResponse"]
  >(await page.request.post(`${path}/evaluation-preview`, { headers }), 201);
  await json(
    await page.request.post(`${path}/evaluation-references`, {
      headers,
      data: {
        preview_id: competing.id,
        expected_version: 0,
        text: "Competing synthetic reference",
        human_reviewed: true,
        compared_with_original: true,
        reason: "Synthetic concurrency fixture",
      },
    }),
    201,
  );
  await save.click();
  await expect(editor.getByRole("alert")).toContainText(
    "Another reference was saved",
  );
  await expect(input).toHaveValue(draft);
  await editor.getByRole("button", { name: "Load latest reference" }).click();
  await expect(
    editor.getByRole("region", { name: "Latest saved reference" }),
  ).toContainText("Competing synthetic reference");
  await editor
    .getByRole("button", { name: "Keep my text and use latest version" })
    .click();
  await expect(save).toBeDisabled();
  await editor
    .getByRole("checkbox", { name: /I compared this reference/ })
    .check();
  await save.click();
  await expect(editor.getByRole("status")).toContainText(
    "Reference saved for evaluation only.",
  );
  const history = await json<
    components["schemas"]["EvaluationReferenceResponse"][]
  >(await page.request.get(`${path}/evaluation-references`));
  expect(history.map((reference) => reference.version)).toEqual([2, 1]);
  expect(history[0].text).toBe(draft);
  expect(await workspace(page, source.id)).toEqual(before);
  const summary = await benchmark(page, set.id);
  expect(summary.adjudicated_pages).toBe(0);
  expect(summary.accuracy_status).toBe("awaiting_human_adjudication");
  expect(summary.evaluation_references).toMatchObject({
    referenced_pages: 1,
    pending_pages: 0,
    selected_pages: 1,
  });
  await editor.getByRole("button", { name: "Back to review set" }).click();
  await expect(
    page.getByRole("region", { name: "Evaluation reference progress" }),
  ).toContainText("1 of 1");
  await page
    .getByRole("button", {
      name: `Edit evaluation reference for page 1 of ${marker}.pdf`,
    })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Reference text", exact: true }),
  ).toHaveValue(draft);
});

for (const pageCount of [4, 371]) {
  test(`focused review: useful laptop panes and Sinhala navigation (${pageCount} synthetic pages)`, async ({
    page,
  }) => {
    const runtime = requireIsolatedE2ERuntime(process.env);
    await login(page, "admin");
    expect(
      await json(
        await page.request.get("/api/v1/admin/studio-safety/runtime-identity"),
      ),
    ).toMatchObject({
      application_env: "test",
      test_runtime_id: runtime.composeProjectName,
    });
    // Presentation/navigation fixtures only: no upload, source read, trust or corpus writes.
    const documentId = randomUUID();
    const requestedPages: number[] = [];
    const writes: string[] = [];
    const materialPath = `**/api/v1/admin/materials/${documentId}/**`;
    await page.route(materialPath, async (route) => {
      const request = route.request();
      if (request.method() !== "GET") {
        writes.push(request.method());
        await route.abort();
        return;
      }
      const url = new URL(request.url());
      if (url.pathname.endsWith("/image")) {
        const pageNumber = Number(url.pathname.split("/").at(-2));
        await route.fulfill({
          contentType: "image/svg+xml",
          body: `<svg xmlns="http://www.w3.org/2000/svg" width="600" height="1200"><rect width="600" height="1200" fill="white"/><text x="40" y="40">Synthetic page ${pageNumber} of ${pageCount}</text>${Array.from({ length: 38 }, (_, index) => `<path d="M40 ${80 + index * 28}h520" stroke="#334155"/>`).join("")}</svg>`,
        });
        return;
      }
      const pageNumber = Number(url.searchParams.get("page_number"));
      requestedPages.push(pageNumber);
      const failed = pageNumber !== 1;
      const view: Workspace = {
        document_id: documentId,
        document_title: `පරීක්ෂණ මූලාශ්‍රය — ${pageCount} පිටු.pdf`,
        language: "si",
        metadata_review_required: true,
        source_active: true,
        ready_for_ai: false,
        progress: {
          total_pages: pageCount,
          processed_pages: pageCount,
          verified_pages: 0,
          excluded_pages: 0,
          flagged_pages: pageCount,
          remaining_pages: pageCount,
        },
        previous_flagged_page: pageNumber > 1 ? pageNumber - 1 : null,
        next_flagged_page: pageNumber < pageCount ? pageNumber + 1 : null,
        page: {
          page_number: pageNumber,
          state: failed ? "failed" : "needs_review",
          version: 7,
          candidate_id: `${documentId.slice(0, -3)}001`,
          system_text:
            "මෙය පරීක්ෂණ පිටුවකි. අංක සහ වගු මුල් පිටුව සමඟ සසඳන්න.\n".repeat(
              40,
            ),
          language: "si",
          can_confirm: !failed,
          risk_codes: failed ? ["maths_fidelity_unconfirmed"] : [],
          diagnostics: { text_readable: true },
          provenance: {
            source_languages: ["si"],
            engine: "synthetic-layout-fixture",
          },
          history: [],
          preview_url: `/api/v1/admin/materials/${documentId}/pages/${pageNumber}/image`,
        },
      };
      await route.fulfill({ json: view });
    });
    await page.goto(`/admin/materials/${documentId}/review-text?page_number=2`);
    await imageReady(page, 2, "මුල් පිටුව");
    const original = page.getByRole("region", {
      name: "මුල් පිටුව",
      exact: true,
    });
    const text = page.getByTestId("text-scroll-panel");
    const actions = page.getByRole("group", { name: "පිටුව සඳහා ක්‍රියා" });
    const failure = page.getByText("මෙම පිටුවේ පෙළ නිවැරදිව කියවී නොමැත.", {
      exact: true,
    });
    for (const viewport of [
      { width: 1440, height: 1000 },
      { width: 1366, height: 768 },
      { width: 1280, height: 720 },
    ]) {
      await page.setViewportSize(viewport);
      await page.evaluate(() => document.fonts.ready.then(() => undefined));
      const dimensions = await page.evaluate(() => {
        const workspace = document.querySelector(
          '[data-testid="source-page-workspace"]',
        )!;
        const original = workspace.querySelector("img")!.closest("section")!;
        const text = workspace.querySelector(
          '[data-testid="text-scroll-panel"]',
        )!;
        const actions = workspace.querySelector('[role="group"]')!;
        return {
          originalContentHeight:
            original.clientHeight -
            original.querySelector("header")!.getBoundingClientRect().height,
          textContentHeight: text.clientHeight,
          originalTop: original.getBoundingClientRect().top,
          textPanelTop: text.parentElement!.getBoundingClientRect().top,
          actionBottom: actions.getBoundingClientRect().bottom,
          documentHeight: document.documentElement.scrollHeight,
          documentWidth: document.documentElement.scrollWidth,
        };
      });
      console.log(
        "FOCUSED_REVIEW_LAYOUT",
        JSON.stringify({ pageCount, viewport, ...dimensions }),
      );
      await test.info().attach(`layout-${viewport.width}x${viewport.height}`, {
        body: JSON.stringify({ pageCount, viewport, ...dimensions }),
        contentType: "application/json",
      });
      expect(dimensions.originalContentHeight).toBeGreaterThanOrEqual(240);
      expect(dimensions.textContentHeight).toBeGreaterThanOrEqual(240);
      expect(dimensions.originalTop).toBe(dimensions.textPanelTop);
      expect(dimensions.actionBottom).toBeLessThanOrEqual(viewport.height);
      expect(dimensions.documentHeight).toBeLessThanOrEqual(
        viewport.height + 1,
      );
      expect(dimensions.documentWidth).toBeLessThanOrEqual(viewport.width);
      await expect(failure).toBeInViewport({ ratio: 1 });
      await expect(actions).toBeInViewport({ ratio: 1 });
      await expect(
        page.getByRole("navigation", { name: "Primary admin navigation" }),
      ).toBeInViewport({ ratio: 1 });
      await expect(
        page.getByRole("link", { name: "මූලාශ්‍රය වෙත ආපසු", exact: true }),
      ).toBeVisible();
      await expect(
        page.getByText("Advanced", { exact: true }).locator(".."),
      ).not.toHaveAttribute("open", "");
      await expect(
        page.getByText("තාක්ෂණික විස්තර", { exact: true }).locator(".."),
      ).not.toHaveAttribute("open", "");
      await expectReadableButton(
        actions.getByRole("button", { name: "නැවත කියවන්න", exact: true }),
      );
      await expect(
        actions.getByRole("button", { name: "පෙළ නිවැරදියි", exact: true }),
      ).toBeDisabled();
      await test.info().attach(`review-${viewport.width}x${viewport.height}`, {
        body: await page.screenshot(),
        contentType: "image/png",
      });
    }
    const advanced = page.getByText("Advanced", { exact: true });
    await advanced.click();
    await expect(
      page.getByRole("navigation", { name: "Advanced admin navigation" }),
    ).toBeInViewport({ ratio: 1 });
    expect(
      await text.evaluate((element) => element.clientHeight),
    ).toBeGreaterThanOrEqual(240);
    await advanced.click();
    const reread = actions.getByRole("button", {
      name: "නැවත කියවන්න",
      exact: true,
    });
    await reread.focus();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Shift+Tab");
    await expect(reread).toBeFocused();
    expect(
      await reread.evaluate((element) => element.matches(":focus-visible")),
    ).toBe(true);
    await expect(reread).toHaveCSS("cursor", "pointer");
    await expectReadableButton(reread);
    await original.evaluate((element) => {
      element.scrollTop = 300;
    });
    expect(await original.evaluate((element) => element.scrollTop)).toBe(300);
    expect(await text.evaluate((element) => element.scrollTop)).toBe(0);
    await text.evaluate((element) => {
      element.scrollTop = 200;
    });
    expect(await text.evaluate((element) => element.scrollTop)).toBe(200);
    expect(await original.evaluate((element) => element.scrollTop)).toBe(300);
    expect(await page.evaluate(() => scrollY)).toBe(0);
    await expect(failure).toBeInViewport({ ratio: 1 });
    await expect(actions).toBeInViewport({ ratio: 1 });
    await actions
      .getByRole("button", { name: "පෙළ නිවැරදි කරන්න", exact: true })
      .click();
    await expect(
      page.getByRole("textbox", { name: "නිවැරදි කළ පෙළ", exact: true }),
    ).toBeVisible();
    await expect(actions).toBeInViewport({ ratio: 1 });
    await actions
      .getByRole("button", { name: "සංස්කරණය අවලංගු කරන්න", exact: true })
      .click();
    const clickPage = async (name: string, pageNumber: number) => {
      await page.getByRole("button", { name, exact: true }).click();
      await imageReady(page, pageNumber, "මුල් පිටුව");
    };
    await clickPage("අවසාන පිටුව", pageCount);
    await expect(
      page.getByRole("button", { name: "ඊළඟ පිටුව", exact: true }),
    ).toBeDisabled();
    await clickPage("පළමු පිටුව", 1);
    await clickPage("ඊළඟ පිටුව", 2);
    await page
      .getByRole("spinbutton", { name: "පිටු අංකය", exact: true })
      .fill(String(pageCount - 1));
    await clickPage("පිටුවට යන්න", pageCount - 1);
    await clickPage("අවධානය අවශ්‍ය පෙර පිටුව", pageCount - 2);
    await clickPage("අවධානය අවශ්‍ය ඊළඟ පිටුව", pageCount - 1);
    expect(requestedPages).toEqual([
      2,
      pageCount,
      1,
      2,
      pageCount - 1,
      pageCount - 2,
      pageCount - 1,
    ]);
    expect(writes).toEqual([]);
    await page.getByRole("link", { name: "Home", exact: true }).click();
    await expect(page.getByTestId("source-page-workspace")).toHaveCount(0);
    const normalHeaderHeight = await page
      .getByRole("navigation", { name: "Primary admin navigation" })
      .evaluate(
        (element) => element.closest("header")!.getBoundingClientRect().height,
      );
    expect(normalHeaderHeight).toBeGreaterThanOrEqual(200);
  });
}

test("real APIs: unread pages keep source-language controls without assigning text language or trust", async ({
  page,
}, testInfo) => {
  const runtime = requireIsolatedE2ERuntime(process.env);
  await login(page, "admin");
  const identity = await json<{
    application_env: string;
    test_runtime_id: string;
  }>(await page.request.get("/api/v1/admin/studio-safety/runtime-identity"));
  expect(identity).toMatchObject({
    application_env: "test",
    test_runtime_id: runtime.composeProjectName,
  });
  const headers = { Origin: runtime.baseURL, "Sec-Fetch-Site": "same-origin" };
  const marker = `Unread-${randomUUID().slice(0, 8)}`;
  const source = await json<Source>(
    await page.request.post("/api/v1/admin/source-documents", {
      headers,
      multipart: {
        file: {
          name: `${marker}.pdf`,
          mimeType: "application/pdf",
          buffer: syntheticBook(marker, 371),
        },
        document_type: "teacher_guide",
        intake_metadata: JSON.stringify({
          candidate_grade: 5,
          medium_label: "Sinhala",
          subject_label: "Unverified workflow fixture",
        }),
      },
    }),
    201,
  );
  const initial = await workspace(page, source.id);
  expect(initial.page).toBeNull();
  expect(initial.language).toBe("si");
  const read = await page.request.post(
    `/api/v1/admin/source-documents/${source.id}/read`,
    { headers },
  );
  expect(read.status()).toBe(202);
  await expect
    .poll(async () => (await workspace(page, source.id)).progress.total_pages)
    .toBe(371);
  await page.goto(`/admin/materials/${source.id}/review-text`);
  await page.getByRole("button", { name: /^(Last page|අවසාන පිටුව)$/ }).click();
  await expect(
    page.getByRole("img", { name: "මුල් පිටුව 371", exact: true }),
  ).toBeVisible();
  await imageReady(page, 371, "මුල් පිටුව");
  await expect(
    page.getByRole("button", { name: "අවසාන පිටුව", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "පෙළ නිවැරදියි", exact: true }),
  ).toBeDisabled();
  await expectReadableButton(
    page.getByRole("button", { name: "පළමු පිටුව", exact: true }),
  );
  const pending = await workspace(page, source.id, 371);
  expect(pending.language).toBe("si");
  expect(pending.page).toMatchObject({
    state: "pending",
    language: "und",
    candidate_id: null,
    can_confirm: false,
    system_text: "",
  });
  expect(pending.ready_for_ai).toBe(false);
  expect(pending.progress.verified_pages).toBe(0);
  await testInfo.attach("unread-source-language", {
    body: await page.screenshot(),
    contentType: "image/png",
  });
});

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
  await expectReadableButton(
    page.getByRole("button", { name: "Text is correct", exact: true }),
  );
  await expect(
    page.getByRole("button", { name: "Next page", exact: true }),
  ).toHaveCSS("cursor", "pointer");
  await expect(
    page.getByRole("button", { name: "First page", exact: true }),
  ).toHaveCSS("cursor", "not-allowed");
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
  const saveButton = page.getByRole("button", {
    name: "Save correction",
    exact: true,
  });
  await expect(saveButton).toBeDisabled();
  await expectReadableButton(saveButton);
  await expect(saveButton).toHaveCSS("cursor", "not-allowed");
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
  const draft = textBefore!
    .replaceAll("Synthetic", "ශ්‍රී ලංකාව ක්‍රියා")
    .replaceAll("page", "தமிழ் க்ஷேத்திரம்")
    .replaceAll("line", "පේළිය");
  await page
    .getByRole("textbox", { name: "Correction", exact: true })
    .fill(draft);
  await page
    .getByRole("textbox", { name: "Reason for correction" })
    .fill("Synthetic Unicode preservation test; not confirmed");
  await expect(saveButton).toBeEnabled();
  await expectReadableButton(saveButton);
  await expect(saveButton).toHaveCSS("cursor", "pointer");
  await saveButton.hover();
  await expectReadableButton(saveButton);
  await page.mouse.move(0, 0);
  await saveButton.focus();
  await expect(saveButton).toBeFocused();
  await expectReadableButton(saveButton);
  const languageControl = page.getByRole("combobox", {
    name: "Review language / භාෂාව",
  });
  await expect(languageControl).toHaveValue("en");
  await languageControl.selectOption("si");
  await expect(page.getByTestId("source-page-workspace")).toHaveAttribute(
    "lang",
    "si",
  );
  await expect(
    page.getByRole("textbox", { name: "නිවැරදි කළ පෙළ", exact: true }),
  ).toHaveValue(draft);
  await expect(
    page.getByRole("textbox", { name: "නිවැරදි කිරීමට හේතුව" }),
  ).toHaveValue("Synthetic Unicode preservation test; not confirmed");
  await expectReadableButton(
    page.getByRole("button", { name: "නිවැරදි කළ පෙළ සුරකින්න", exact: true }),
  );
  await languageControl.selectOption("en");
  await expect(
    page.getByRole("textbox", { name: "Correction", exact: true }),
  ).toHaveValue(draft);
  const current = await workspace(page, source.id);
  await json(
    await page.request.post(
      `/api/v1/admin/materials/${source.id}/pages/1/edit`,
      {
        headers,
        data: {
          expected_version: current.page!.version,
          text: textBefore!.replaceAll(
            "Synthetic",
            "Concurrent synthetic reading",
          ),
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
  const saved = await workspace(page, source.id);
  expect(saved.page!.system_text).toBe(draft);
  expect(saved.page!.state).toBe("needs_review");
  expect(saved.page!.can_confirm).toBe(true);
  expect(saved.page!.diagnostics.text_readable).toBe(true);
  expect(await page.getByTestId("system-page-text").textContent()).toBe(draft);
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

  const reviewUrl = page.url();
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  await languageControl.selectOption("si");
  await page.getByRole("button", { name: "ඊළඟ පිටුව", exact: true }).click();
  await expect(
    page.getByRole("img", { name: "මුල් පිටුව 2", exact: true }),
  ).toBeVisible();
  await expect(languageControl).toHaveValue("si");
  await page.reload();
  await expect(
    page.getByRole("img", { name: "මුල් පිටුව 1", exact: true }),
  ).toBeVisible();
  await expect(languageControl).toHaveValue("si");
  await page.getByRole("link", { name: "Materials", exact: true }).click();
  await page.goto(reviewUrl);
  await expect(
    page.getByRole("img", { name: "මුල් පිටුව 1", exact: true }),
  ).toBeVisible();
  await expect(languageControl).toHaveValue("si");
  await languageControl.selectOption("en");
  await imageReady(page, 1);
  expect(await page.getByTestId("system-page-text").textContent()).toBe(draft);
  expect(browserErrors).toEqual([]);

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

test("real APIs: synthetic Sinhala failures and readable maths-blocked recovery never auto-confirm", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const runtime = requireIsolatedE2ERuntime(process.env);
  await page.setViewportSize({ width: 1280, height: 800 });
  await login(page, "admin");
  expect(
    await json(
      await page.request.get("/api/v1/admin/studio-safety/runtime-identity"),
    ),
  ).toMatchObject({
    application_env: "test",
    test_runtime_id: runtime.composeProjectName,
  });
  const headers = { Origin: runtime.baseURL, "Sec-Fetch-Site": "same-origin" };
  const marker = `Synthetic ${randomUUID().replaceAll("-", "").slice(0, 12)}`;
  const original = syntheticBook(marker, 1);
  const source = await json<Source>(
    await page.request.post("/api/v1/admin/source-documents", {
      headers,
      multipart: {
        file: {
          name: `${marker.replaceAll(" ", "-")}.pdf`,
          mimeType: "application/pdf",
          buffer: original,
        },
        document_type: "other_approved",
        intake_metadata: JSON.stringify({
          candidate_grade: 5,
          medium_label: "English",
          subject_label: "Mathematics",
          document_type_label: "Synthetic failure workflow",
          year: 2026,
          evidence: [
            "Disposable UI and rejection fixture; not Sinhala OCR or educational ground truth",
          ],
        }),
      },
    }),
    201,
  );
  const read = await json<ReadJob>(
    await page.request.post(
      `/api/v1/admin/source-documents/${source.id}/read`,
      { headers },
    ),
    202,
  );
  await expect
    .poll(
      async () =>
        (
          await json<ReadJob>(
            await page.request.get(`/api/v1/admin/source-read-jobs/${read.id}`),
          )
        ).status,
      {
        timeout: 90_000,
        intervals: [500, 1000, 2000],
      },
    )
    .toBe("completed");
  const initial = await workspace(page, source.id);
  expect(initial.page).toMatchObject({
    state: "needs_review",
    can_confirm: true,
  });
  const corrupt = `ශ්‍රී ලංකාව — පරීක්ෂණ පෙළ\n${"\uFFFD".repeat(40)}\nwkd ñ`;
  await json(
    await page.request.post(
      `/api/v1/admin/materials/${source.id}/pages/1/edit`,
      {
        headers,
        data: {
          expected_version: initial.page!.version,
          text: corrupt,
          reason:
            "Synthetic unreadable candidate; must not become ground truth",
        },
      },
    ),
  );
  const damaged = await workspace(page, source.id);
  expect(damaged.page).toMatchObject({
    state: "failed",
    can_confirm: false,
    diagnostics: { text_readable: false },
  });
  expect(damaged.progress.verified_pages).toBe(0);
  await page.goto(`/admin/materials/${source.id}/review-text`);
  await imageReady(page, 1, "මුල් පිටුව");
  await expect(
    page.getByRole("combobox", { name: "Review language / භාෂාව" }),
  ).toHaveValue("si");
  expect(
    await page.evaluate(() =>
      localStorage.getItem("exam-guru:review-language:v1"),
    ),
  ).toBeNull();
  const failure = page.getByText("මෙම පිටුවේ පෙළ නිවැරදිව කියවී නොමැත.", {
    exact: true,
  });
  await expect(failure).toBeVisible();
  await expect(page.getByTestId("system-page-text")).toHaveCount(0);
  await expect(page.getByTestId("recovered-page-text")).toHaveCount(0);
  const details = page
    .locator("details")
    .filter({ has: page.getByText("තාක්ෂණික විස්තර", { exact: true }) });
  await expect(details).not.toHaveAttribute("open", "");
  await expect(page.getByTestId("failed-page-text")).toBeHidden();
  expect(await page.getByTestId("failed-page-text").textContent()).toBe(
    damaged.page!.system_text,
  );
  const actions = page.getByRole("group", { name: "පිටුව සඳහා ක්‍රියා" });
  await expect(actions.getByRole("button")).toHaveText([
    "නැවත කියවන්න",
    "පෙළ නිවැරදි කරන්න",
    "මෙම පිටුව භාවිත නොකරන්න",
    "පෙළ නිවැරදියි",
  ]);
  const reread = page.getByRole("button", {
    name: "නැවත කියවන්න",
    exact: true,
  });
  const confirm = page.getByRole("button", {
    name: "පෙළ නිවැරදියි",
    exact: true,
  });
  await expect(reread).toBeEnabled();
  await expect(reread).toHaveCSS("cursor", "pointer");
  await expect(confirm).toBeDisabled();
  await expect(confirm).toHaveCSS("cursor", "not-allowed");
  const normalStyle = await expectReadableButton(reread);
  await reread.hover();
  const hoverStyle = await expectReadableButton(reread);
  await page.mouse.move(0, 0);
  await reread.focus();
  await expect(reread).toBeFocused();
  const focusStyle = await expectReadableButton(reread);
  const disabledConfirmStyle = await confirm.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      color: style.color,
      backgroundColor: style.backgroundColor,
      cursor: style.cursor,
      opacity: style.opacity,
    };
  });
  console.log(
    "SOURCE_REVIEW_STYLE_EVIDENCE",
    JSON.stringify({
      normalStyle,
      hoverStyle,
      focusStyle,
      disabledConfirmStyle,
    }),
  );

  const recovered = initial
    .page!.system_text.replaceAll("Synthetic", "ශ්‍රී ලංකාව")
    .replaceAll("page", "පිටුව")
    .replaceAll("line", "පේළිය")
    .replace("2 + 2 = 4", "2 + 2 = 5");
  expect(recovered).not.toBe(initial.page!.system_text);
  expect(recovered).toContain("2 + 2 = 5");
  await page
    .getByRole("button", { name: "පෙළ නිවැරදි කරන්න", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "නිවැරදි කළ පෙළ", exact: true })
    .fill(recovered);
  await page
    .getByRole("textbox", { name: "නිවැරදි කිරීමට හේතුව" })
    .fill(
      "Synthetic readable Sinhala with one changed number; must remain blocked",
    );
  const editAccepted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith(`/materials/${source.id}/pages/1/edit`),
  );
  await page
    .getByRole("button", { name: "නිවැරදි කළ පෙළ සුරකින්න", exact: true })
    .click();
  const editResponse = await editAccepted;
  expect(editResponse.status()).toBe(200);
  expect(editResponse.request().postDataJSON()).toMatchObject({
    expected_version: damaged.page!.version,
    text: recovered,
  });
  const recoveredPanel = page.getByRole("region", {
    name: "නැවත කියවූ පෙළ — තහවුරු කිරීමට සූදානම් නැත",
  });
  await expect(recoveredPanel).toBeVisible();
  expect(await page.getByTestId("recovered-page-text").textContent()).toBe(
    recovered,
  );
  await expect(
    recoveredPanel.getByText(/අංක, සංකේත සහ වගු තවමත් වැරදි විය හැක/),
  ).toBeVisible();
  await expect(failure).toBeVisible();
  await expect(page.getByTestId("system-page-text")).toHaveCount(0);
  await imageReady(page, 1, "මුල් පිටුව");
  await expect(confirm).toBeDisabled();
  const blocked = await workspace(page, source.id);
  expect(blocked.page).toMatchObject({
    state: "failed",
    can_confirm: false,
    system_text: recovered,
    diagnostics: { text_readable: true },
  });
  expect(blocked.page!.risk_codes).toContain("math_tokens_changed");
  expect(blocked.page!.history).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        id: initial.page!.candidate_id,
        is_current: false,
      }),
      expect.objectContaining({
        id: damaged.page!.candidate_id,
        is_current: false,
      }),
      expect.objectContaining({
        id: blocked.page!.candidate_id,
        is_current: true,
      }),
    ]),
  );
  expect(blocked.ready_for_ai).toBe(false);
  expect(blocked.progress.verified_pages).toBe(0);
  const rejected = await page.request.post(
    `/api/v1/admin/materials/${source.id}/pages/1/confirm`,
    {
      headers,
      data: {
        expected_version: blocked.page!.version,
        candidate_id: blocked.page!.candidate_id,
        compared_with_original: true,
        reason:
          "Synthetic guard rejection assertion; never educational ground truth",
      },
    },
  );
  expect(await json(rejected, 409)).toMatchObject({
    detail: { code: "source_page_verification_blocked" },
  });
  const afterRejected = await workspace(page, source.id);
  expect(afterRejected.page!.version).toBe(blocked.page!.version);
  expect(afterRejected.progress.verified_pages).toBe(0);

  const rereadAccepted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith(`/materials/${source.id}/pages/1/reread`),
  );
  await reread.click();
  const rereadResponse = await rereadAccepted;
  expect(rereadResponse.request().postDataJSON()).toEqual({
    expected_version: blocked.page!.version,
  });
  expect(rereadResponse.status()).toBe(202);
  const job = (await rereadResponse.json()) as ReadJob;
  await expect
    .poll(
      async () =>
        (
          await json<ReadJob>(
            await page.request.get(`/api/v1/admin/source-read-jobs/${job.id}`),
          )
        ).status,
      {
        timeout: 90_000,
        intervals: [500, 1000, 2000],
      },
    )
    .toBe("completed");
  const fresh = await workspace(page, source.id);
  expect(fresh.page!.version).toBeGreaterThan(blocked.page!.version);
  expect(fresh.page!.candidate_id).not.toBe(blocked.page!.candidate_id);
  expect(fresh.page!.state).toBe("needs_review");
  expect(fresh.page!.system_text).toBe(initial.page!.system_text);
  expect(fresh.page!.history).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        id: damaged.page!.candidate_id,
        is_current: false,
      }),
      expect.objectContaining({
        id: blocked.page!.candidate_id,
        is_current: false,
      }),
      expect.objectContaining({
        id: fresh.page!.candidate_id,
        is_current: true,
      }),
    ]),
  );
  expect(fresh.progress.verified_pages).toBe(0);
  expect(fresh.ready_for_ai).toBe(false);
  await page.reload();
  await imageReady(page, 1);
  expect(await page.getByTestId("system-page-text").textContent()).toBe(
    fresh.page!.system_text,
  );
  await expect(
    page.getByText("Not yet checked against the original.", { exact: true }),
  ).toBeVisible();
  expect(
    await (
      await page.request.get(
        `/api/v1/admin/source-documents/${source.id}/content`,
      )
    ).body(),
  ).toEqual(original);
  console.log(
    "SOURCE_REVIEW_REAL_API_EVIDENCE",
    JSON.stringify({
      failedVersion: damaged.page!.version,
      recoveredVersion: blocked.page!.version,
      rereadVersion: fresh.page!.version,
      retainedCandidates: fresh.page!.history.length,
      verifiedPages: fresh.progress.verified_pages,
    }),
  );
});

test("enabled native and React Aria buttons consistently show a pointer across Studio", async ({
  page,
}) => {
  await page.goto("/admin/login");
  await expect(
    page.getByRole("button", { name: "Continue as admin", exact: true }),
  ).toHaveCSS("cursor", "pointer");
  await login(page, "admin");
  await expect(
    page.getByRole("button", { name: "Sign out", exact: true }),
  ).toHaveCSS("cursor", "pointer");
  await page.getByRole("link", { name: "Materials", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Upload material", exact: true }),
  ).toHaveCSS("cursor", "pointer");
  await expect(page.getByRole("button", { name: /^Grade 1 —/ })).toHaveCSS(
    "cursor",
    "pointer",
  );
  await expect(
    page.getByRole("combobox", { name: "Material type", exact: true }),
  ).toHaveCSS("cursor", "pointer");
});
