import { expect, test, type Page } from "@playwright/test";

const pdfBase64 =
  "JVBERi0xLjcKJcK1wrYKJSBXcml0dGVuIGJ5IE11UERGIDEuMjguMgoKMSAwIG9iago8PC9UeXBlL0NhdGFsb2cvUGFnZXMgMiAwIFIvSW5mbzw8L1Byb2R1Y2VyKE11UERGIDEuMjguMik+Pj4+CmVuZG9iagoKMiAwIG9iago8PC9UeXBlL1BhZ2VzL0NvdW50IDEvS2lkc1s0IDAgUl0+PgplbmRvYmoKCjMgMCBvYmoKPDwvRm9udDw8L2hlbHYgNSAwIFI+Pj4+CmVuZG9iagoKNCAwIG9iago8PC9UeXBlL1BhZ2UvTWVkaWFCb3hbMCAwIDU5NSA4NDJdL1JvdGF0ZSAwL1Jlc291cmNlcyAzIDAgUi9QYXJlbnQgMiAwIFIvQ29udGVudHNbNiAwIFJdPj4KZW5kb2JqCgo1IDAgb2JqCjw8L1R5cGUvRm9udC9TdWJ0eXBlL1R5cGUxL0Jhc2VGb250L0hlbHZldGljYS9FbmNvZGluZy9XaW5BbnNpRW5jb2Rpbmc+PgplbmRvYmoKCjYgMCBvYmoKPDwvTGVuZ3RoIDg2L0ZpbHRlci9GbGF0ZURlY29kZT4+CnN0cmVhbQp4nOMq5HIK4TJUMABCQwVzIwVzcwOFkFwu/YzUnDIFQ0OFkDSFaBsTc3MjM0MzEzNTIwNjIDY3NkszNwWKGYNEzIHi5hbmJnaxIV5criFcgVwAIR8SqQplbmRzdHJlYW0KZW5kb2JqCgp4cmVmCjAgNwowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwNDIgMDAwMDAgbiAKMDAwMDAwMDEyMCAwMDAwMCBuIAowMDAwMDAwMTcyIDAwMDAwIG4gCjAwMDAwMDAyMTMgMDAwMDAgbiAKMDAwMDAwMDMyMCAwMDAwMCBuIAowMDAwMDAwNDA5IDAwMDAwIG4gCgp0cmFpbGVyCjw8L1NpemUgNy9Sb290IDEgMCBSL0lEWzwyRkMzQUQzMzA2QzJBMUMzQUYyMjczQzI5QkMyODFDMj48ODRFRTMxQ0E2M0Y2MjI4MjVCOEFFMzI5OTQwNkE0QzM+XT4+CnN0YXJ0eHJlZgo1NjMKJSVFT0YK";

async function login(page: Page, role: "admin" | "reviewer") {
  await page.goto("/admin/login");
  await page.getByRole("button", { name: `Continue as ${role}` }).click();
  await expect(page).toHaveURL(/\/admin\/home$/);
  await page.goto("/admin/documents");
  await expect(page.getByRole("heading", { name: "Source documents" })).toBeVisible();
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

  await login(page, "admin");
  await page.getByLabel("PDF file").setInputFiles({
    buffer: pdf,
    mimeType: "application/pdf",
    name: filename,
  });
  await page.getByLabel("Document type").selectOption("syllabus");
  await page.getByRole("button", { name: "Upload source document" }).click();
  await expect(page.getByText("Source document uploaded.")).toBeVisible();

  const catalogResponse = await page.request.get("/api/v1/admin/source-documents");
  expect(catalogResponse.ok()).toBe(true);
  const catalog = (await catalogResponse.json()) as Array<{
    extraction_status: string;
    id: string;
    original_filename: string;
  }>;
  const source = catalog.find((item) => item.original_filename === filename);
  expect(source).toBeTruthy();
  if (!source) return;

  await page.getByRole("button", { name: `Queue extraction for ${filename}` }).click();
  await expect(page.getByText("Native extraction queued.")).toBeVisible();

  await expect
    .poll(
      async () => {
        const response = await page.request.get("/api/v1/admin/source-documents");
        const documents = (await response.json()) as Array<{
          extraction_status: string;
          id: string;
        }>;
        return documents.find((item) => item.id === source.id)?.extraction_status;
      },
      { timeout: 30_000 },
    )
    .toBe("extracted");

  const contentResponse = await page.request.get(
    `/api/v1/admin/source-documents/${source.id}/content`,
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
  await expect(page.getByRole("heading", { name: "Review text" })).toBeVisible();
  const originalPreview = page.getByRole("img", {
    name: "Original PDF page 1",
  });
  await expect(originalPreview).toBeVisible();
  await expect(originalPreview).toHaveJSProperty(
    "src",
    new URL(`/api/v1/admin/source-documents/${source.id}/pages/1/preview`, page.url()).href,
  );
  await expect
    .poll(() => originalPreview.evaluate((image: HTMLImageElement) => image.naturalWidth))
    .toBeGreaterThan(0);
  expect(await contentResponse.body()).toEqual(pdf);
  await expect(page.getByRole("region", { name: "Extracted and corrected text" })).toContainText(
    "Grade 5 source text",
  );
  await expect(
    page.locator("details").filter({ hasText: "Technical details" }),
  ).not.toHaveAttribute("open", "");

  await page.goto(`/admin/documents/${source.id}`);
  await expect(
    page.locator("pre").filter({ hasText: "Grade 5 source text" }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Begin human review" }).click();
  const reviewedText = page.getByLabel("Reviewed page 1 text");
  await reviewedText.fill("Human-verified Grade 5 source text");
  await page.getByRole("button", { name: "Save page 1 correction" }).click();
  await expect(page.getByText("Page 1 correction saved.")).toBeVisible();
  await page.getByRole("button", { name: "Mark source trusted" }).click();
  await expect(page.getByText("Trusted source").first()).toBeVisible();

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
  const actions = ((await auditResponse.json()) as Array<{ action: string }>).map(
    (event) => event.action,
  );
  expect(actions).toEqual(
    expect.arrayContaining([
      "source_document.uploaded",
      "source_document.extracted",
      "source_document.page_corrected",
      "source_document.trusted",
    ]),
  );

  await page.getByRole("button", { name: "Sign out" }).click();
  await login(page, "reviewer");
  await expect(page.getByText("Reviewer access is read-only for source documents.")).toBeVisible();
  const denied = await page.request.post(`/api/v1/admin/source-documents/${source.id}/extract`);
  expect(denied.status()).toBe(403);
});

test("intake year remains visible through assignment and explicit metadata confirmation", async ({
  page,
}) => {
  await login(page, "admin");
  const unique = Date.now().toString();
  const examResponse = await page.request.post("/api/v1/admin/exam-configurations", {
    data: { code: `YEAR-G7-${unique}`, name: "Year review Grade 7", grade: 7 },
  });
  expect(examResponse.ok()).toBe(true);
  const subjectResponse = await page.request.post("/api/v1/admin/subjects", {
    data: { code: `YEAR-MATH-${unique}`, name: "Year review mathematics" },
  });
  expect(subjectResponse.ok()).toBe(true);
  const mediumResponse = await page.request.post("/api/v1/admin/media", {
    data: { code: `yr-${unique.slice(-10)}`, name: "Year review English" },
  });
  expect(mediumResponse.ok()).toBe(true);
  const curriculumResponse = await page.request.post("/api/v1/admin/curriculum-versions", {
    data: {
      code: `YEAR-V1-${unique}`,
      title: "Reviewed year scope",
      exam_configuration_id: (await examResponse.json()).id,
      subject_id: (await subjectResponse.json()).id,
      medium_id: (await mediumResponse.json()).id,
    },
  });
  expect(curriculumResponse.ok()).toBe(true);
  const curriculum = await curriculumResponse.json();
  const sourceResponse = await page.request.post("/api/v1/admin/source-documents", {
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
  });
  expect(sourceResponse.status()).toBe(201);
  const source = await sourceResponse.json();
  expect(source.year).toBeNull();
  await page.goto(`/admin/materials/${source.id}`);
  await expect(
    page.getByRole("region", { name: "Material details" })
      .getByText("Year", { exact: true }).locator("..").locator("dd"),
  ).toHaveText("2024");

  const scopeUrl = `/api/v1/admin/materials/${source.id}/scope`;
  const assigned = await page.request.patch(scopeUrl, {
    data: { curriculum_version_id: curriculum.id, expected_version: 0 },
  });
  expect(assigned.ok()).toBe(true);
  expect(await assigned.json()).toMatchObject({ year: null, metadata_review_required: true });
  await page.reload();
  await expect(
    page.getByRole("region", { name: "Material details" })
      .getByText("Year", { exact: true }).locator("..").locator("dd"),
  ).toHaveText("2024");

  const confirmed = await page.request.patch(scopeUrl, {
    data: {
      curriculum_version_id: curriculum.id,
      expected_version: 1,
      confirm_intake_metadata: true,
    },
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
    page.getByRole("region", { name: "Material details" })
      .getByText("Year", { exact: true }).locator("..").locator("dd"),
  ).toHaveText("2024");
  const filtered = await page.request.get("/api/v1/admin/materials", {
    params: { document_id: source.id, year: 2024 },
  });
  expect(filtered.ok()).toBe(true);
  expect(await filtered.json()).toEqual([expect.objectContaining({ id: source.id, year: 2024 })]);
});
