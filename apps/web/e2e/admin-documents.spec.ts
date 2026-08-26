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

test("admin uploads, extracts, corrects, trusts, and reuses an immutable source", async ({ page }) => {
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

  await page
    .getByRole("button", { name: `Queue extraction for ${filename}` })
    .click();
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

  await page.goto(`/admin/documents/${source.id}`);
  await expect(page.locator("pre").filter({ hasText: "Grade 5 source text" }).first()).toBeVisible();
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
  const denied = await page.request.post(
    `/api/v1/admin/source-documents/${source.id}/extract`,
  );
  expect(denied.status()).toBe(403);
});
