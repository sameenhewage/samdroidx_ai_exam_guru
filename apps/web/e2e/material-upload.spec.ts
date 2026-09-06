import type { components } from "@exam-guru/api-client";
import { expect, test, type Page } from "@playwright/test";
import { createHash, randomUUID } from "node:crypto";

import { requireIsolatedE2ERuntime } from "../playwright-runtime";
import { seedAdmittedCurriculum } from "./helpers/teacher-content-studio";

const chunkBytes = 4_194_304;
const checkpointKey = "exam-guru.source-upload-checkpoints.v1";
type Session = components["schemas"]["SourceUploadResponse"];
type Catalogue = components["schemas"]["MaterialCatalogueEntry"];
type Material = components["schemas"]["MaterialListItemResponse"];

function pdf(marker: string, padding = 0) {
  const stream = `BT /F1 12 Tf 40 780 Td (${marker}: two plus two equals four.) Tj ET\n%${" ".repeat(padding)}\n`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let body = "%PDF-1.4\n";
  const offsets = objects.map((object, index) => {
    const offset = Buffer.byteLength(body);
    body += `${index + 1} 0 obj\n${object}\nendobj\n`;
    return offset;
  });
  const xref = Buffer.byteLength(body);
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("")}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(body);
}

async function attestedAdmin(page: Page) {
  const runtime = requireIsolatedE2ERuntime(process.env);
  await page.goto("/admin/login");
  await page.getByRole("button", { name: "Continue as admin" }).click();
  await expect(page).toHaveURL(/\/admin\/home$/);
  const identity = await page.request.get(
    "/api/v1/admin/studio-safety/runtime-identity",
  );
  expect(identity.status()).toBe(200);
  expect(await identity.json()).toMatchObject({
    application_env: "test",
    test_runtime_id: runtime.composeProjectName,
  });
  return { Origin: runtime.baseURL, "Sec-Fetch-Site": "same-origin" };
}

async function seedSession(
  page: Page,
  headers: Record<string, string>,
  filename: string,
  bytes: Buffer,
) {
  const body = {
    filename,
    request_id: randomUUID(),
    size_bytes: bytes.length,
    document_type: "other_approved",
    year: 2025,
    intake_metadata: {
      candidate_grade: 5,
      medium_label: "English",
      subject_label: "Mathematics",
      year: 2025,
      document_type_label: "Synthetic upload workflow only",
    },
  } satisfies components["schemas"]["SourceUploadCreateRequest"];
  const response = await page.request.post("/api/v1/admin/source-uploads", {
    headers,
    data: body,
  });
  expect(response.status()).toBe(201);
  return (await response.json()) as Session;
}

async function storeRecoveryLink(page: Page, id: string) {
  await page.evaluate(
    ({ id, key }) =>
      localStorage.setItem(
        key,
        JSON.stringify({ uploadIds: [id], creationUncertain: false }),
      ),
    { id, key: checkpointKey },
  );
  await page.goto("/admin/materials");
  await page.getByRole("button", { name: "Continue saved upload 1" }).click();
  const dialog = page.getByRole("dialog", {
    name: "Continue upload",
    exact: true,
  });
  await expect(dialog.getByLabel("Original PDF")).toBeVisible();
  return dialog;
}

test("real API: normal Materials resumes only the matching PDF, finishes once, follows its read job and handles deduplication", async ({
  page,
}) => {
  test.setTimeout(150_000);
  const headers = await attestedAdmin(page);
  const marker = `Upload-${randomUUID().slice(0, 8)}`;
  const filename = `${marker}.pdf`;
  // Just over one chunk: a small valid PDF with an inert stream comment, not a real teaching source.
  const bytes = pdf(marker, chunkBytes);
  const session = await seedSession(page, headers, filename, bytes);
  const prefix = bytes.subarray(0, chunkBytes);
  const stored = await page.request.put(
    `/api/v1/admin/source-uploads/${session.id}/chunks?offset=0`,
    {
      headers: {
        ...headers,
        "Content-Type": "application/octet-stream",
        "X-Chunk-SHA256": createHash("sha256").update(prefix).digest("hex"),
      },
      data: prefix,
    },
  );
  expect(stored.status()).toBe(200);
  expect(await stored.json()).toMatchObject({ next_offset: chunkBytes });
  const mutations: Array<{
    method: string;
    path: string;
    offset: string | null;
  }> = [];
  const readingRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.includes("/source-read-jobs/"))
      readingRequests.push(url.pathname);
    if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method()))
      mutations.push({
        method: request.method(),
        path: url.pathname,
        offset: url.searchParams.get("offset"),
      });
  });
  let dialog = await storeRecoveryLink(page, session.id);
  const impostor = Buffer.from(bytes);
  impostor[chunkBytes - 100] = 33;
  await dialog.getByLabel("Original PDF").setInputFiles({
    name: filename,
    mimeType: "application/pdf",
    buffer: impostor,
  });
  await dialog
    .getByRole("button", { name: "Resume upload", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText(
    "does not match the saved upload",
  );
  expect(mutations).toEqual([]);
  await dialog.getByLabel("Original PDF").setInputFiles({
    name: filename,
    mimeType: "application/pdf",
    buffer: bytes,
  });
  await dialog
    .getByRole("button", { name: "Resume upload", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "Open uploaded material" }),
  ).toBeVisible({ timeout: 90_000 });
  const status = await page.request.get(
    `/api/v1/admin/source-uploads/${session.id}`,
  );
  expect(status.status()).toBe(200);
  const completed = (await status.json()) as Session;
  expect(completed).toMatchObject({
    status: "completed",
    next_offset: bytes.length,
    verified_bytes: bytes.length,
    deduplicated: false,
  });
  expect(completed.document_id).toBeTruthy();
  expect(completed.source_read_job_id).toBeTruthy();
  expect(readingRequests).toContain(
    `/api/v1/admin/source-read-jobs/${completed.source_read_job_id}`,
  );
  expect(mutations.filter((request) => request.method === "PUT")).toEqual([
    {
      method: "PUT",
      path: `/api/v1/admin/source-uploads/${session.id}/chunks`,
      offset: String(chunkBytes),
    },
  ]);
  expect(mutations.filter((request) => request.method === "POST")).toEqual([
    {
      method: "POST",
      path: `/api/v1/admin/source-uploads/${session.id}/complete`,
      offset: null,
    },
  ]);
  const materialResponse = await page.request.get(
    `/api/v1/admin/materials?document_id=${completed.document_id}&limit=1`,
  );
  const materials = (await materialResponse.json()) as Material[];
  expect(materials[0]).toMatchObject({
    year: 2025,
    metadata_review_required: true,
  });
  expect(materials[0].status).not.toBe("ready_for_ai");

  const duplicate = await seedSession(page, headers, `copy-${filename}`, bytes);
  mutations.length = 0;
  dialog = await storeRecoveryLink(page, duplicate.id);
  await dialog.getByLabel("Original PDF").setInputFiles({
    name: filename,
    mimeType: "application/pdf",
    buffer: bytes,
  });
  await dialog
    .getByRole("button", { name: "Resume upload", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText(
    "This exact PDF is already in Materials",
    { timeout: 90_000 },
  );
  await expect(
    dialog.getByRole("link", { name: "View existing material" }),
  ).toHaveAttribute("href", `/admin/materials/${completed.document_id}`);
  expect(
    mutations.some((request) =>
      /\/(read|extract|trust|admission)$/.test(request.path),
    ),
  ).toBe(false);
  for (const font of ["noto-sans-sinhala", "noto-sans-tamil"]) {
    const license = await page.request.get(`/licenses/${font}-LICENSE.txt`);
    expect(license.status()).toBe(200);
    expect(await license.text()).toContain("SIL OPEN FONT LICENSE Version 1.1");
  }
});

async function prepareWizard(
  page: Page,
  entry: Catalogue,
  filename: string,
  bytes: Buffer,
) {
  await page.goto("/admin/materials");
  await page
    .getByRole("button", { name: "Upload material", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Upload material",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  const next = () =>
    dialog.getByRole("button", { name: "Continue", exact: true }).click();
  await dialog
    .getByRole("combobox", { name: "Grade", exact: true })
    .selectOption(String(entry.grade));
  await next();
  await dialog
    .getByRole("combobox", { name: "Medium", exact: true })
    .selectOption(entry.medium_id);
  await next();
  await dialog
    .getByRole("combobox", { name: "Subject", exact: true })
    .selectOption(entry.subject_id);
  await next();
  await dialog
    .getByRole("combobox", { name: "Material type", exact: true })
    .selectOption("past_paper");
  await next();
  await dialog.getByLabel("Year", { exact: true }).fill("2025");
  await dialog
    .getByRole("combobox", { name: "Curriculum version", exact: true })
    .selectOption(entry.curriculum_version_id);
  await next();
  await dialog.getByLabel("PDF file").setInputFiles({
    name: filename,
    mimeType: "application/pdf",
    buffer: bytes,
  });
  await next();
  return dialog;
}

test("real API: a new PDF follows the complete normal wizard with an explicitly seeded disposable curriculum", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await attestedAdmin(page);
  const entry = await seedAdmittedCurriculum(page.request);
  const filename = `wizard-${randomUUID().slice(0, 8)}.pdf`;
  const dialog = await prepareWizard(page, entry, filename, pdf(filename));
  const creation = page.waitForResponse(
    (result) =>
      result.request().method() === "POST" &&
      result.url().endsWith("/source-uploads"),
  );
  await dialog
    .getByRole("button", { name: "Upload material", exact: true })
    .click();
  const created = await creation;
  expect(created.status()).toBe(201);
  expect(created.request().postDataJSON()).toMatchObject({
    filename,
    year: 2025,
    curriculum_version_id: entry.curriculum_version_id,
  });
  await expect(
    page.getByRole("link", { name: "Open uploaded material" }),
  ).toBeVisible({ timeout: 60_000 });
});

test("real API: full browser refresh recovers a committed lost-create response without a new request identity", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await attestedAdmin(page);
  const entry = await seedAdmittedCurriculum(page.request);
  const filename = `refresh-${randomUUID().slice(0, 8)}.pdf`;
  const bytes = pdf(filename);
  const dialog = await prepareWizard(page, entry, filename, bytes);
  let committed: Session | undefined;
  let originalRequest:
    components["schemas"]["SourceUploadCreateRequest"] | undefined;
  let createCount = 0;
  const createPath = "**/api/v1/admin/source-uploads";
  await page.route(createPath, async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    createCount += 1;
    originalRequest = route
      .request()
      .postDataJSON() as components["schemas"]["SourceUploadCreateRequest"];
    const response = await route.fetch();
    expect(response.status()).toBe(201);
    committed = (await response.json()) as Session;
    await route.abort("failed");
  });
  await dialog
    .getByRole("button", { name: "Upload material", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText(
    "upload response was interrupted",
  );
  expect(committed?.request_id).toBe(originalRequest?.request_id);
  expect(originalRequest?.request_id).toMatch(/^[0-9a-f-]{36}$/);
  const checkpoint = await page.evaluate(
    (key) => JSON.parse(localStorage.getItem(key)!),
    checkpointKey,
  );
  expect(checkpoint).toEqual({
    uploadIds: [],
    requestIds: [originalRequest!.request_id],
    creationUncertain: true,
  });
  const recovered = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response
        .url()
        .endsWith(`/source-uploads/by-request/${originalRequest!.request_id}`),
  );
  await page.reload();
  expect((await recovered).status()).toBe(200);
  await page.getByRole("button", { name: "Continue saved upload 1" }).click();
  const resumed = page.getByRole("dialog", {
    name: "Continue upload",
    exact: true,
  });
  await resumed.getByLabel("Original PDF").setInputFiles({
    name: filename,
    mimeType: "application/pdf",
    buffer: bytes,
  });
  await resumed
    .getByRole("button", { name: "Resume upload", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "Open uploaded material" }),
  ).toBeVisible({ timeout: 60_000 });
  expect(createCount).toBe(1);
  const lookup = await page.request.get(
    `/api/v1/admin/source-uploads/by-request/${originalRequest!.request_id}`,
  );
  expect(lookup.status()).toBe(200);
  expect(await lookup.json()).toMatchObject({
    id: committed!.id,
    request_id: originalRequest!.request_id,
    status: "completed",
    filename,
    size_bytes: bytes.length,
  });
  const done = await page.evaluate(
    (key) => JSON.parse(localStorage.getItem(key)!),
    checkpointKey,
  );
  expect(done.uploadIds).toEqual([]);
  expect(done.requestIds ?? []).toEqual([]);
  await page.unroute(createPath);
});
