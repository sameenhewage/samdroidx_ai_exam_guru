import type { components } from "@exam-guru/api-client";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

function syntheticPdf(marker: string): Buffer {
  const stream = `BT\n/F1 12 Tf\n72 720 Td\n(${marker}) Tj\nET`;
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
    ...offsets.map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`),
  ].join("");
  const trailer = `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
  return Buffer.from(body + xref + trailer, "ascii");
}

type Exam = components["schemas"]["ExamConfigurationResponse"];
type Medium = components["schemas"]["MediumResponse"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type SourcePage = components["schemas"]["SourcePageResponse"];
type ExtractedBlock = components["schemas"]["ExtractedBlockResponse"];

async function login(page: Page, role: "admin" | "reviewer") {
  await page.goto("/admin/login");
  await page.getByRole("button", { name: `Continue as ${role}` }).click();
  await expect(page).toHaveURL(/\/admin\/curriculum$/);
}

async function postCreated<ResponseDto>(
  request: APIRequestContext,
  path: string,
  data: object,
): Promise<ResponseDto> {
  const response = await request.post(path, { data });
  expect(response.status()).toBe(201);
  return (await response.json()) as ResponseDto;
}

test("admin imports trusted question and chunk, then reviewer classifies and reviews both", async ({
  page,
}) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  const unique = Date.now().toString().slice(-9);
  const curriculumTitle = `Knowledge curriculum ${unique}`;
  const sourceFilename = `knowledge-source-${unique}.pdf`;
  const questionNumber = `Q-${unique}`;
  const boundary = `Geometry boundary ${unique}`;
  const competencyTitle = `Spatial competency ${unique}`;
  const skillTitle = `Polygon skill ${unique}`;
  const mediaReference = `source://page/1/figure-${unique}`;
  const optionA = "A. Triangle";
  const optionB = "B. Square";
  const answer = "B";
  const markingGuidance = `Award two marks for source label B (${unique}).`;
  const archetype = `single_best_answer_${unique}`;
  const difficultySource = `reviewer_confirmed_${unique}`;
  const year = 2025;
  const paperCode = `P-${unique}`;

  await login(page, "admin");
  const exam = await postCreated<Exam>(page.request, "/api/v1/admin/exam-configurations", {
    code: `E${unique}`,
    grade: 5,
    name: `Knowledge exam ${unique}`,
  });
  const medium = await postCreated<Medium>(page.request, "/api/v1/admin/media", {
    code: `m${unique}`,
    name: `Knowledge medium ${unique}`,
  });
  const curriculum = await postCreated<Curriculum>(page.request, "/api/v1/admin/curriculum-versions", {
    code: `CV-${unique}`,
    exam_configuration_id: exam.id,
    medium_id: medium.id,
    title: curriculumTitle,
  });
  const competency = await postCreated<TaxonomyNode>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes`,
    {
      active: true,
      code: `C${unique}`,
      level: "competency",
      parent_id: null,
      title: competencyTitle,
    },
  );
  const competencyReview = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes/${competency.id}/review`,
  );
  expect(competencyReview.ok()).toBe(true);
  const skill = await postCreated<TaxonomyNode>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes`,
    {
      active: true,
      code: `S${unique}`,
      level: "skill",
      parent_id: competency.id,
      title: skillTitle,
    },
  );
  const skillReview = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes/${skill.id}/review`,
  );
  expect(skillReview.ok()).toBe(true);

  const pdf = syntheticPdf(`knowledge-fixture-${unique}`);
  const upload = await page.request.post("/api/v1/admin/source-documents", {
    multipart: {
      curriculum_version_id: curriculum.id,
      document_type: "past_paper",
      file: { buffer: pdf, mimeType: "application/pdf", name: sourceFilename },
      paper_code: paperCode,
      year: String(year),
    },
  });
  expect(upload.status()).toBe(201);
  const source = (await upload.json()) as SourceDocument;
  const queued = await page.request.post(`/api/v1/admin/source-documents/${source.id}/extract`);
  expect(queued.status()).toBe(202);
  await expect
    .poll(
      async () => {
        const response = await page.request.get("/api/v1/admin/source-documents");
        const documents = (await response.json()) as SourceDocument[];
        return documents.find((document) => document.id === source.id)?.extraction_status;
      },
      { timeout: 30_000 },
    )
    .toBe("extracted");
  expect((await page.request.post(`/api/v1/admin/source-documents/${source.id}/review`)).ok()).toBe(
    true,
  );
  expect((await page.request.post(`/api/v1/admin/source-documents/${source.id}/trust`)).ok()).toBe(
    true,
  );
  const pagesResponse = await page.request.get(
    `/api/v1/admin/source-documents/${source.id}/pages`,
  );
  expect(pagesResponse.ok()).toBe(true);
  const [sourcePage] = (await pagesResponse.json()) as SourcePage[];
  if (!sourcePage) throw new Error("Extracted source page was not created");
  const blocksResponse = await page.request.get(
    `/api/v1/admin/source-documents/${source.id}/pages/${sourcePage.page_number}/blocks`,
  );
  expect(blocksResponse.ok()).toBe(true);
  const [sourceBlock] = (await blocksResponse.json()) as ExtractedBlock[];
  if (!sourceBlock) throw new Error("Extracted source block was not created");

  await page.goto("/admin/knowledge");
  await expect(page.getByRole("heading", { name: "Knowledge Studio" })).toBeVisible();
  await page.getByLabel("Active curriculum").selectOption(curriculum.id);
  await page.getByLabel("Trusted source document").selectOption(source.id);
  await page.getByLabel("Source page").selectOption("1");
  await page.getByLabel("Source block").selectOption({ index: 1 });
  await page.getByLabel("Question number").fill(questionNumber);
  await page.getByLabel("Marks").fill("2");
  await page.getByLabel("Media references").fill(mediaReference);
  await page.getByLabel("Options").fill(`${optionA}\n${optionB}`);
  await page.getByLabel("Answer", { exact: true }).fill(answer);
  await page.getByLabel("Marking guidance").fill(markingGuidance);
  await page.getByLabel("Marking data (JSON object)").fill(
    JSON.stringify({
      alternative_answers: [answer],
      criteria: [{ description: "Selects the square.", marks: 2 }],
    }),
  );
  await page.getByLabel("Question archetype").fill(archetype);
  await page.getByLabel("Difficulty label").selectOption("medium");
  await page.getByLabel("Difficulty confidence").fill("0.91");
  await page.getByLabel("Difficulty source").fill(difficultySource);
  await page.getByRole("button", { name: "Import historical question" }).click();
  await expect(page.getByText("Historical question imported.")).toBeVisible();
  await expect(page.getByRole("heading", { name: `${paperCode} / Question ${questionNumber}` })).toBeVisible();

  await page.getByRole("tab", { name: /Knowledge chunks/ }).click();
  await page.getByLabel("Trusted source document").selectOption(source.id);
  await page.getByLabel("Source page").selectOption("1");
  await page.getByLabel("Source block").selectOption({ index: 1 });
  await page.getByLabel("Educational boundary").fill(boundary);
  await page.getByLabel("Sequence").fill("1");
  await page.getByRole("button", { name: "Import knowledge chunk" }).click();
  await expect(page.getByText("Knowledge chunk imported.")).toBeVisible();
  await expect(page.getByRole("heading", { name: `${boundary} / Sequence 1` })).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await login(page, "reviewer");
  await page.goto("/admin/knowledge");
  await page.getByLabel("Active curriculum").selectOption(curriculum.id);
  await expect(page.getByText("Import permission required")).toBeVisible();
  await expect(page.getByRole("button", { name: "Import historical question" })).toHaveCount(0);
  const deniedImport = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/questions`,
    {
      data: {
        marks: 1,
        page_number: sourcePage.page_number,
        paper_code: paperCode,
        question_number: `DENIED-${unique}`,
        question_type: "short_answer",
        source_block_id: sourceBlock.id,
        source_document_id: source.id,
        text: "Reviewer import must be denied",
        year,
      } satisfies components["schemas"]["HistoricalQuestionImportRequest"],
    },
  );
  expect(deniedImport.status()).toBe(403);

  const questionCard = page.locator("article").filter({
    has: page.getByRole("heading", {
      exact: true,
      name: `${paperCode} / Question ${questionNumber}`,
    }),
  });
  await expect(questionCard).toBeVisible();
  const metadataHeading = questionCard.getByRole("heading", {
    exact: true,
    name: "Historical question metadata",
  });
  const metadataPanel = questionCard.locator("section").filter({ has: metadataHeading });
  await expect(metadataPanel).toBeVisible();
  await expect(metadataPanel.getByText(mediaReference, { exact: true })).toBeVisible();
  await expect(metadataPanel.getByText(optionA, { exact: true })).toBeVisible();
  await expect(metadataPanel.getByText(optionB, { exact: true })).toBeVisible();
  await expect(metadataPanel.getByText(answer, { exact: true })).toBeVisible();
  await expect(metadataPanel.getByText(markingGuidance, { exact: true })).toBeVisible();
  await expect(metadataPanel.getByText(archetype, { exact: true })).toBeVisible();
  await expect(metadataPanel.getByText("medium", { exact: true })).toBeVisible();
  await expect(metadataPanel.getByText("0.91", { exact: true })).toBeVisible();
  await expect(metadataPanel.getByText(difficultySource, { exact: true })).toBeVisible();
  await expect(metadataPanel.locator("pre")).toContainText('"description": "Selects the square."');
  await expect(metadataPanel.getByText("Not supplied")).toHaveCount(0);
  await questionCard
    .getByRole("combobox", { exact: true, name: "Competency" })
    .selectOption({ label: `C${unique} — ${competencyTitle}` });
  await questionCard
    .getByRole("combobox", { exact: true, name: "Skill" })
    .selectOption({ label: `S${unique} — ${skillTitle}` });
  await questionCard.getByRole("button", { name: "Save classification" }).click();
  await expect(questionCard.getByText("Classification saved.")).toBeVisible();
  await questionCard.getByRole("button", { name: "Start review" }).click();
  await questionCard.getByRole("button", { name: "Mark reviewed" }).click();
  await expect(questionCard.getByText("Final record — read-only")).toBeVisible();

  await page.getByRole("tab", { name: /Knowledge chunks/ }).click();
  const chunkCard = page.locator("article").filter({
    has: page.getByRole("heading", { exact: true, name: `${boundary} / Sequence 1` }),
  });
  await chunkCard
    .getByRole("combobox", { exact: true, name: "Competency" })
    .selectOption({ label: `C${unique} — ${competencyTitle}` });
  await chunkCard
    .getByRole("combobox", { exact: true, name: "Skill" })
    .selectOption({ label: `S${unique} — ${skillTitle}` });
  await chunkCard.getByRole("button", { name: "Save classification" }).click();
  await chunkCard.getByRole("button", { name: "Start review" }).click();
  await chunkCard.getByRole("button", { name: "Mark reviewed" }).click();
  await expect(chunkCard.getByText("Final record — read-only")).toBeVisible();
  await expect(chunkCard.getByText(source.id)).toBeVisible();
  await expect(chunkCard.getByText("Not embedded").first()).toBeVisible();

  expect(browserErrors).toEqual([]);
});
