import type { components } from "@exam-guru/api-client";
import { expect, test, type Page } from "@playwright/test";

import {
  assertDisposableStudio,
  seedAdmittedCurriculum,
} from "./helpers/teacher-content-studio";

const pdfBase64 =
  "JVBERi0xLjcKJcK1wrYKJSBXcml0dGVuIGJ5IE11UERGIDEuMjguMgoKMSAwIG9iago8PC9UeXBlL0NhdGFsb2cvUGFnZXMgMiAwIFIvSW5mbzw8L1Byb2R1Y2VyKE11UERGIDEuMjguMik+Pj4+CmVuZG9iagoKMiAwIG9iago8PC9UeXBlL1BhZ2VzL0NvdW50IDEvS2lkc1s0IDAgUl0+PgplbmRvYmoKCjMgMCBvYmoKPDwvRm9udDw8L2hlbHYgNSAwIFI+Pj4+CmVuZG9iagoKNCAwIG9iago8PC9UeXBlL1BhZ2UvTWVkaWFCb3hbMCAwIDU5NSA4NDJdL1JvdGF0ZSAwL1Jlc291cmNlcyAzIDAgUi9QYXJlbnQgMiAwIFIvQ29udGVudHNbNiAwIFJdPj4KZW5kb2JqCgo1IDAgb2JqCjw8L1R5cGUvRm9udC9TdWJ0eXBlL1R5cGUxL0Jhc2VGb250L0hlbHZldGljYS9FbmNvZGluZy9XaW5BbnNpRW5jb2Rpbmc+PgplbmRvYmoKCjYgMCBvYmoKPDwvTGVuZ3RoIDg2L0ZpbHRlci9GbGF0ZURlY29kZT4+CnN0cmVhbQp4nOMq5HIK4TJUMABCQwVzIwVzcwOFkFwu/YzUnDIFQ0OFkDSFaBsTc3MjM0MzEzNTIwNjIDY3NkszNwWKGYNEzIHi5hbmJnaxIV5criFcgVwAIR8SqQplbmRzdHJlYW0KZW5kb2JqCgp4cmVmCjAgNwowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwNDIgMDAwMDAgbiAKMDAwMDAwMDEyMCAwMDAwMCBuIAowMDAwMDAwMTcyIDAwMDAwIG4gCjAwMDAwMDAyMTMgMDAwMDAgbiAKMDAwMDAwMDMyMCAwMDAwMCBuIAowMDAwMDAwNDA5IDAwMDAwIG4gCgp0cmFpbGVyCjw8L1NpemUgNy9Sb290IDEgMCBSL0lEWzwyRkMzQUQzMzA2QzJBMUMzQUYyMjczQzI5QkMyODFDMj48ODRFRTMxQ0E2M0Y2MjI4MjVCOEFFMzI5OTQwNkE0QzM+XT4+CnN0YXJ0eHJlZgo1NjMKJSVFT0YK";

async function login(page: Page, role: "admin" | "reviewer") {
  await page.goto("/admin/login");
  await page.getByRole("button", { name: `Continue as ${role}` }).click();
  await expect(page).toHaveURL(/\/admin\/home$/);
  await page.goto("/admin/documents");
  await expect(
    page.getByRole("heading", { name: "Source documents" }),
  ).toBeVisible();
}

test("admin uploads, extracts, corrects, trusts, and reuses an immutable source", async ({
  page,
}) => {
  const unique = Date.now().toString();
  const filename = `grade-5-source-${unique}.pdf`;
  const pdf = Buffer.concat([
    Buffer.from(pdfBase64, "base64"),
    Buffer.from(`\n% fixture-${unique}\n`),
  ]);

  test.setTimeout(120_000);
  await login(page, "admin");
  const headers = await assertDisposableStudio(page.request);
  const scope = await seedAdmittedCurriculum(page.request);
  await page.getByLabel("PDF file").setInputFiles({
    buffer: pdf,
    mimeType: "application/pdf",
    name: filename,
  });
  await page.getByLabel("Document type").selectOption("syllabus");
  await page.getByRole("button", { name: "Upload source document" }).click();
  await expect(page.getByText("Source document uploaded.")).toBeVisible();

  const catalogResponse = await page.request.get(
    "/api/v1/admin/source-documents",
  );
  expect(catalogResponse.ok()).toBe(true);
  const catalog =
    (await catalogResponse.json()) as components["schemas"]["SourceDocumentResponse"][];
  const source = catalog.find((item) => item.original_filename === filename);
  expect(source).toBeTruthy();
  if (!source)
    throw new Error("The uploaded synthetic source must be discoverable");
  const scopeResponse = await page.request.patch(
    `/api/v1/admin/materials/${source.id}/scope`,
    {
      headers,
      data: {
        curriculum_version_id: scope.curriculum_version_id,
        expected_version: source.metadata_scope_version,
        confirm_intake_metadata: true,
      } satisfies components["schemas"]["MaterialScopeCorrectionRequest"],
    },
  );
  expect(scopeResponse.status()).toBe(200);
  expect(await scopeResponse.json()).toMatchObject({
    metadata_review_required: false,
  });

  await page
    .getByRole("button", { name: `Queue extraction for ${filename}` })
    .click();
  await expect(page.getByText("Native extraction queued.")).toBeVisible();

  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          "/api/v1/admin/source-documents",
        );
        const documents = (await response.json()) as Array<{
          extraction_status: string;
          id: string;
        }>;
        return documents.find((item) => item.id === source.id)
          ?.extraction_status;
      },
      { timeout: 30_000 },
    )
    .toBe("extracted");
  expect(
    (
      await page.request.post(
        `/api/v1/admin/source-documents/${source.id}/review`,
        { headers },
      )
    ).status(),
  ).toBe(200);
  const blockedTrust = await page.request.post(
    `/api/v1/admin/source-documents/${source.id}/trust`,
    { headers },
  );
  expect(blockedTrust.status()).toBe(409);
  expect(await blockedTrust.json()).toMatchObject({
    detail: { reason_code: "page_verification_required" },
  });
  const readingResponse = await page.request.post(
    `/api/v1/admin/source-documents/${source.id}/read`,
    { headers },
  );
  expect(readingResponse.status()).toBe(202);
  const reading =
    (await readingResponse.json()) as components["schemas"]["SourceReadJobResponse"];
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `/api/v1/admin/source-read-jobs/${reading.id}`,
        );
        expect(response.status()).toBe(200);
        return (
          (await response.json()) as components["schemas"]["SourceReadJobResponse"]
        ).status;
      },
      { timeout: 60_000 },
    )
    .toBe("completed");
  const legacyPages = await page.request.get(
    `/api/v1/admin/source-documents/${source.id}/pages`,
  );
  const [legacyPage] =
    (await legacyPages.json()) as components["schemas"]["SourcePageResponse"][];
  const rejectedLegacyEdit = await page.request.patch(
    `/api/v1/admin/source-documents/${source.id}/pages/1`,
    {
      headers,
      data: {
        expected_version: legacyPage.version,
        reviewed_text: "A legacy edit must not bypass the page workspace",
      },
    },
  );
  expect(rejectedLegacyEdit.status()).toBe(409);
  expect(await rejectedLegacyEdit.json()).toMatchObject({
    detail: { code: "page_review_workspace_required" },
  });

  const contentResponse = await page.request.get(
    `/api/v1/admin/materials/${source.id}/original`,
  );
  expect(contentResponse.ok()).toBe(true);
  expect(contentResponse.headers()).toMatchObject({
    "cache-control": "private, no-store",
    "content-type": "application/pdf",
    "x-content-type-options": "nosniff",
    "x-frame-options": "SAMEORIGIN",
  });
  expect(contentResponse.headers()["content-disposition"]).toContain("inline;");

  await page.goto(`/admin/materials/${source.id}/review-text`);
  await expect(
    page.getByRole("heading", { name: "Check the system-read text" }),
  ).toBeVisible();
  const originalPreview = page.getByRole("img", {
    name: "Original page 1",
  });
  await expect(originalPreview).toBeVisible();
  await expect(originalPreview).toHaveJSProperty(
    "src",
    new URL(`/api/v1/admin/materials/${source.id}/pages/1/image`, page.url())
      .href,
  );
  await expect
    .poll(() =>
      originalPreview.evaluate((image: HTMLImageElement) => image.naturalWidth),
    )
    .toBeGreaterThan(0);
  expect(await contentResponse.body()).toEqual(pdf);
  await expect(
    page.getByRole("region", { name: "System-read text", exact: true }),
  ).toContainText("Grade 5 source text");
  await expect(
    page.locator("details").filter({ hasText: "Technical details" }),
  ).not.toHaveAttribute("open", "");

  await expect(
    page.getByRole("textbox", { name: "Correction", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Correct the text", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Correction", exact: true })
    .fill("Human-verified Grade 5 source text");
  await page
    .getByRole("textbox", { name: "Reason for correction" })
    .fill(
      "Disposable synthetic workflow fixture, NOT real educational approval",
    );
  await page
    .getByRole("button", { name: "Save correction", exact: true })
    .click();
  await expect(
    page.getByText(
      "Correction saved. Compare it with the original before confirming.",
    ),
  ).toBeVisible();
  await expect(page.getByText("Ready for AI", { exact: true })).toHaveCount(0);
  await page
    .getByRole("button", { name: "Text is correct", exact: true })
    .click();
  const confirmation = page.getByRole("dialog", { name: "Confirm this page" });
  await expect(
    confirmation.getByRole("button", { name: "Confirm compared text" }),
  ).toBeDisabled();
  await confirmation.getByRole("checkbox").check();
  await confirmation
    .getByRole("button", { name: "Confirm compared text" })
    .click();
  await expect(page.getByText("Confirmed against the original.")).toBeVisible();
  await expect(page.getByText("Ready for AI", { exact: true })).toBeVisible();
  const trusted = await page.request.post(
    `/api/v1/admin/source-documents/${source.id}/trust`,
    { headers },
  );
  expect(trusted.status()).toBe(200);
  expect(await trusted.json()).toMatchObject({
    extraction_status: "trusted",
    metadata_review_required: false,
  });
  const ready = await page.request.get("/api/v1/admin/materials", {
    params: { document_id: source.id, limit: 1 },
  });
  expect(await ready.json()).toEqual([
    expect.objectContaining({ id: source.id, status: "ready_for_ai" }),
  ]);

  await page.goto("/admin/documents");
  await page.getByLabel("PDF file").setInputFiles({
    buffer: pdf,
    mimeType: "application/pdf",
    name: `retry-${filename}`,
  });
  await page.getByRole("button", { name: "Upload source document" }).click();
  await expect(page.getByText("Duplicate source reused.")).toBeVisible();

  const auditResponse = await page.request.get(
    "/api/v1/admin/audit-events?resource_type=source_document&limit=50",
  );
  const actions = (
    (await auditResponse.json()) as Array<{ action: string }>
  ).map((event) => event.action);
  expect(actions).toEqual(
    expect.arrayContaining([
      "source_document.uploaded",
      "source_document.extracted",
      "source_document.trusted",
    ]),
  );

  const pageAudits = await page.request.get(
    "/api/v1/admin/audit-events?resource_type=source_page_review&limit=50",
  );
  expect(pageAudits.status()).toBe(200);
  expect(
    ((await pageAudits.json()) as Array<{ action: string }>).map(
      (event) => event.action,
    ),
  ).toEqual(
    expect.arrayContaining(["source_page.edited", "source_page.confirmed"]),
  );

  await page.getByRole("button", { name: "Sign out" }).click();
  await login(page, "reviewer");
  await expect(
    page.getByText("Reviewer access is read-only for source documents."),
  ).toBeVisible();
  const denied = await page.request.post(
    `/api/v1/admin/source-documents/${source.id}/extract`,
  );
  expect(denied.status()).toBe(403);
});

test("intake year remains visible through assignment and explicit metadata confirmation", async ({
  page,
}) => {
  await login(page, "admin");
  const unique = Date.now().toString();
  const headers = await assertDisposableStudio(page.request);
  const curriculum = await seedAdmittedCurriculum(page.request, 7);
  const sourceResponse = await page.request.post(
    "/api/v1/admin/source-documents",
    {
      headers,
      multipart: {
        document_type: "past_paper",
        intake_metadata: JSON.stringify({ candidate_grade: 7, year: 2024 }),
        file: {
          name: `year-review-${unique}.pdf`,
          mimeType: "application/pdf",
          buffer: Buffer.concat([
            Buffer.from(pdfBase64, "base64"),
            Buffer.from(`\n% year-review-${unique}\n`),
          ]),
        },
      },
    },
  );
  expect(sourceResponse.status()).toBe(201);
  const source =
    (await sourceResponse.json()) as components["schemas"]["SourceDocumentResponse"];
  expect(source.year).toBeNull();
  await page.goto(`/admin/materials/${source.id}`);
  await expect(
    page
      .getByRole("region", { name: "Material details" })
      .getByText("Year", { exact: true })
      .locator("..")
      .locator("dd"),
  ).toHaveText("2024");

  const scopeUrl = `/api/v1/admin/materials/${source.id}/scope`;
  const assigned = await page.request.patch(scopeUrl, {
    headers,
    data: {
      curriculum_version_id: curriculum.curriculum_version_id,
      expected_version: source.metadata_scope_version,
      confirm_intake_metadata: false,
    } satisfies components["schemas"]["MaterialScopeCorrectionRequest"],
  });
  expect(assigned.ok()).toBe(true);
  const assignedSource =
    (await assigned.json()) as components["schemas"]["SourceDocumentResponse"];
  expect(assignedSource).toMatchObject({
    year: null,
    metadata_review_required: true,
  });
  await page.reload();
  await expect(
    page
      .getByRole("region", { name: "Material details" })
      .getByText("Year", { exact: true })
      .locator("..")
      .locator("dd"),
  ).toHaveText("2024");

  const confirmed = await page.request.patch(scopeUrl, {
    headers,
    data: {
      curriculum_version_id: curriculum.curriculum_version_id,
      expected_version: assignedSource.metadata_scope_version,
      confirm_intake_metadata: true,
    } satisfies components["schemas"]["MaterialScopeCorrectionRequest"],
  });
  expect(confirmed.ok()).toBe(true);
  expect(await confirmed.json()).toMatchObject({
    year: 2024,
    metadata_review_required: false,
    extraction_status: "uploaded",
    intake_metadata: { year: 2024 },
  });
  await page.reload();
  await expect(
    page
      .getByRole("region", { name: "Material details" })
      .getByText("Year", { exact: true })
      .locator("..")
      .locator("dd"),
  ).toHaveText("2024");
  const filtered = await page.request.get("/api/v1/admin/materials", {
    params: { document_id: source.id, year: 2024 },
  });
  expect(filtered.ok()).toBe(true);
  expect(await filtered.json()).toEqual([
    expect.objectContaining({ id: source.id, year: 2024 }),
  ]);
});
