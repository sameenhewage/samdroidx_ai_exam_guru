import type { components } from "@exam-guru/api-client";
import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

import { requireIsolatedE2ERuntime } from "../../playwright-runtime";

export type AdminRole = "admin" | "reviewer";

export async function loginAs(page: Page, role: AdminRole) {
  await page.goto("/admin/login");
  const origin = new URL(page.url()).origin;
  await page.context().addCookies([
    {
      httpOnly: true,
      name: "exam_guru_admin_token",
      sameSite: "Lax",
      url: origin,
      value: `teacher-studio-${role}-fixture-token`,
    },
    {
      httpOnly: true,
      name: "exam_guru_admin_role",
      sameSite: "Lax",
      url: origin,
      value: role,
    },
  ]);
  await page.goto("/admin/home");
  await expect(
    page.getByRole("heading", { name: "Create and manage exam papers" }),
  ).toBeVisible();
}

export function syntheticPdf(marker: string): Buffer {
  const safeMarker = marker.replaceAll(/[()\\]/g, " ").slice(0, 120);
  const stream = `BT\n/F1 12 Tf\n72 720 Td\n(${safeMarker}) Tj\nET`;
  const objects = [
    "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
    "2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n",
    "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>\nendobj\n",
    `4 0 obj\n<< /Length ${Buffer.byteLength(stream, "ascii")} >>\nstream\n${stream}\nendstream\nendobj\n`,
    "5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
  ];
  let body = "%PDF-1.4\n";
  const offsets = objects.map((object) => {
    const offset = Buffer.byteLength(body, "ascii");
    body += object;
    return offset;
  });
  const xrefOffset = Buffer.byteLength(body, "ascii");
  const xref = [
    `xref\n0 ${objects.length + 1}\n`,
    "0000000000 65535 f \n",
    ...offsets.map(
      (offset) => `${String(offset).padStart(10, "0")} 00000 n \n`,
    ),
  ].join("");
  return Buffer.from(
    `${body}${xref}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`,
    "ascii",
  );
}

export async function assertDisposableStudio(request: APIRequestContext) {
  const runtime = requireIsolatedE2ERuntime(process.env);
  const response = await request.get(
    "/api/v1/admin/studio-safety/runtime-identity",
  );
  expect(response.status()).toBe(200);
  expect(await response.json()).toMatchObject({
    application_env: "test",
    test_runtime_id: runtime.composeProjectName,
  });
  return { Origin: runtime.baseURL, "Sec-Fetch-Site": "same-origin" };
}

export async function seedAdmittedCurriculum(
  request: APIRequestContext,
  grade = 5,
): Promise<components["schemas"]["MaterialCatalogueEntry"]> {
  return (await seedAdmittedScope(request, grade)).entry;
}

export async function seedAdmittedScope(request: APIRequestContext, grade = 5) {
  const headers = await assertDisposableStudio(request);
  const unique = randomUUID().replaceAll("-", "").slice(0, 12);
  const post = async <T>(path: string, data: unknown): Promise<T> => {
    const response = await request.post(path, { headers, data });
    expect(response.status(), path).toBe(201);
    return (await response.json()) as T;
  };
  const exam = await post<components["schemas"]["ExamConfigurationResponse"]>(
    "/api/v1/admin/exam-configurations",
    {
      code: `Q${unique.toUpperCase()}E`,
      name: `Grade ${grade} school assessment`,
      grade,
    },
  );
  const medium = await post<components["schemas"]["MediumResponse"]>(
    "/api/v1/admin/media",
    { code: `q${unique}`, name: "English" },
  );
  const subject = await post<components["schemas"]["SubjectResponse"]>(
    "/api/v1/admin/subjects",
    { code: `Q${unique.toUpperCase()}S`, name: "Mathematics" },
  );
  const curriculum = await post<
    components["schemas"]["CurriculumVersionResponse"]
  >("/api/v1/admin/curriculum-versions", {
    code: `Q${unique.toUpperCase()}C`,
    title: `Grade ${grade} Mathematics curriculum`,
    exam_configuration_id: exam.id,
    medium_id: medium.id,
    subject_id: subject.id,
  });
  const reviewResponse = await request.get(
    `/api/v1/admin/curriculum-versions/${curriculum.id}/admission`,
  );
  expect(reviewResponse.status()).toBe(200);
  const review =
    (await reviewResponse.json()) as components["schemas"]["CatalogueAdmissionReview"];
  const explanation =
    "Disposable synthetic workflow fixture, NOT real educational approval";
  await post(`/api/v1/admin/curriculum-versions/${curriculum.id}/admission`, {
    state: "approved",
    expected_version: review.version,
    expected_scope_fingerprint: review.scope_fingerprint,
    educational_approval: true,
    reason: explanation,
    source_reference: explanation,
    evidence: [explanation],
  } satisfies components["schemas"]["AdmissionDecisionRequest"]);
  const catalogueResponse = await request.get(
    "/api/v1/admin/material-catalogue",
    { params: { grade, medium_id: medium.id, subject_id: subject.id } },
  );
  expect(catalogueResponse.status()).toBe(200);
  const entries =
    (await catalogueResponse.json()) as components["schemas"]["MaterialCatalogueEntry"][];
  const entry = entries.find(
    (item) => item.curriculum_version_id === curriculum.id,
  );
  expect(entry).toBeDefined();
  if (!entry)
    throw new Error(
      "The explicitly admitted disposable curriculum must be available",
    );
  return { entry, curriculum, exam, medium, subject };
}

export const SYNTHETIC_WORKFLOW_EVIDENCE =
  "Disposable synthetic workflow fixture, NOT real educational approval";

/** Explicit printable lines prevent clipping or silent changes to imported source spans. */
export function syntheticTextPdf(lines: readonly string[], identity?: string) {
  if (
    identity !== undefined &&
    (typeof identity !== "string" ||
      identity.length < 1 ||
      identity.length > 128 ||
      [...identity].some(
        (character) =>
          character.charCodeAt(0) < 32 || character.charCodeAt(0) > 126,
      ))
  ) {
    throw new Error(
      "Synthetic PDF identity must be 1 to 128 printable ASCII characters",
    );
  }
  const identityComment =
    identity === undefined ? "" : `% fixture-identity: ${identity}\n`;
  if (
    lines.length < 1 ||
    lines.length > 40 ||
    lines.some(
      (line) =>
        line.length < 1 ||
        line.length > 80 ||
        [...line].some(
          (character) =>
            character.charCodeAt(0) < 32 || character.charCodeAt(0) > 126,
        ),
    )
  ) {
    throw new Error(
      "Synthetic PDF lines must be printable ASCII and fit the declared page layout",
    );
  }
  const escaped = lines.map((line) =>
    line.replaceAll("\\", "\\\\").replaceAll("(", "\\(").replaceAll(")", "\\)"),
  );
  const stream = `BT\n/F1 10 Tf\n14 TL\n54 760 Td\n${escaped.map((line, index) => `${index ? "T*\n" : ""}(${line}) Tj`).join("\n")}\nET`;
  const objects = [
    "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
    "2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n",
    "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>\nendobj\n",
    `4 0 obj\n<< /Length ${Buffer.byteLength(stream, "ascii")} >>\nstream\n${stream}\nendstream\nendobj\n`,
    "5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\nendobj\n",
  ];
  let body = "%PDF-1.4\n";
  const offsets = objects.map((object) => {
    const offset = Buffer.byteLength(body, "ascii");
    body += object;
    return offset;
  });
  const xrefOffset = Buffer.byteLength(body, "ascii");
  const xref = `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("")}`;
  return {
    bytes: Buffer.from(
      `${body}${xref}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n${identityComment}`,
      "ascii",
    ),
    text: lines.join("\n"),
  };
}

export function syntheticGenerationSource(identity: string) {
  const forbiddenMarker = "Incorrect example";
  const retrievalMarker = "Human correction";
  const correctedText = `${retrievalMarker}: Four is an even number.`;
  const forbiddenText = `${forbiddenMarker}: Four is odd.`;
  return {
    correctedText,
    forbiddenText,
    forbiddenMarker,
    retrievalMarker,
    fixture: syntheticTextPdf([correctedText, forbiddenText], identity),
  };
}

export function syntheticHistoricalQuestion(year: number, identity: string) {
  return syntheticTextPdf(
    [`Historical choice ${year}: A three; B four; answer B.`],
    identity,
  );
}
