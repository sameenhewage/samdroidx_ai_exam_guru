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
type Subject = components["schemas"]["SubjectResponse"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type SourcePage = components["schemas"]["SourcePageResponse"];
type ExtractedBlock = components["schemas"]["ExtractedBlockResponse"];
type HistoricalQuestion = components["schemas"]["HistoricalQuestionResponse"];
type KnowledgeChunk = components["schemas"]["KnowledgeChunkResponse"];
type EmbeddingJob = components["schemas"]["EmbeddingJobResponse"];
type RetrievalResult = components["schemas"]["RetrievalExploreResponse"];

async function login(page: Page, role: "admin" | "reviewer") {
  await page.goto("/admin/login");
  await page.getByRole("button", { name: `Continue as ${role}` }).click();
  await expect(page).toHaveURL(/\/admin\/home$/);
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

test("admin imports and reviews knowledge, embeds it, then proves scoped hybrid retrieval", async ({
  page,
}) => {
  test.setTimeout(240_000);
  test.info().annotations.push({
    type: "limitation",
    description:
      "This proves successful hybrid browser mechanics with generated fixture data; it does not claim a human-reviewed real-data quality threshold.",
  });
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
  const forbiddenBoundary = `Forbidden draft boundary ${unique}`;
  const sourceMarker = `triangle-marker-${unique}`;
  const untrustedSourceText = `<img src=x onerror=alert> ${sourceMarker}`;
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
  const subject = await postCreated<Subject>(page.request, "/api/v1/admin/subjects", {
    code: `S${unique}`,
    name: `Knowledge subject ${unique}`,
  } satisfies components["schemas"]["SubjectCreate"]);
  const curriculum = await postCreated<Curriculum>(page.request, "/api/v1/admin/curriculum-versions", {
    code: `CV-${unique}`,
    exam_configuration_id: exam.id,
    medium_id: medium.id,
    subject_id: subject.id,
    title: curriculumTitle,
  } satisfies components["schemas"]["CurriculumVersionCreate"]);
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

  const pdf = syntheticPdf(untrustedSourceText);
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
  const forbiddenDraft = await postCreated<KnowledgeChunk>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/chunks`,
    {
      chunk_type: "explanation",
      educational_boundary: forbiddenBoundary,
      page_number: sourcePage.page_number,
      sequence: 99,
      source_block_id: sourceBlock.id,
      source_document_id: source.id,
      text: sourceBlock.reviewed_text ?? sourceBlock.raw_text,
    } satisfies components["schemas"]["KnowledgeChunkImportRequest"],
  );

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
  const questionCreatedResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith(`/curricula/${curriculum.id}/knowledge/questions`),
  );
  await page.getByRole("button", { name: "Import historical question" }).click();
  const importedQuestion = (await (await questionCreatedResponse).json()) as HistoricalQuestion;
  await expect(page.getByText("Historical question imported.")).toBeVisible();
  await expect(page.getByRole("heading", { name: `${paperCode} / Question ${questionNumber}` })).toBeVisible();

  await page.getByRole("tab", { name: /Knowledge chunks/ }).click();
  await page.getByLabel("Trusted source document").selectOption(source.id);
  await page.getByLabel("Source page").selectOption("1");
  await page.getByLabel("Source block").selectOption({ index: 1 });
  await page.getByLabel("Educational boundary").fill(boundary);
  await page.getByRole("spinbutton", { exact: true, name: "Sequence" }).fill("1");
  const chunkCreatedResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith(`/curricula/${curriculum.id}/knowledge/chunks`),
  );
  await page.getByRole("button", { name: "Import knowledge chunk" }).click();
  const importedChunk = (await (await chunkCreatedResponse).json()) as KnowledgeChunk;
  await expect(page.getByText("Knowledge chunk imported.")).toBeVisible();
  await expect(page.getByRole("heading", { name: `${boundary} / Sequence 1` })).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await login(page, "reviewer");
  await page.goto("/admin/knowledge");
  await page.getByLabel("Active curriculum").selectOption(curriculum.id);
  await expect(page.getByText("Import permission required")).toBeVisible();
  await expect(page.getByRole("button", { name: "Import historical question" })).toHaveCount(0);
  await expect(page.getByText("Reviewer read-only access")).toBeVisible();
  await expect(page.getByRole("button", { name: "Queue selected records" })).toHaveCount(0);
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
  const metadataPanel = questionCard.getByRole("region", {
    exact: true,
    name: "Historical question metadata",
  });
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

  const deniedEmbedding = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/embedding-jobs`,
    {
      data: {
        historical_question_ids: [importedQuestion.id],
        knowledge_chunk_ids: [importedChunk.id],
      } satisfies components["schemas"]["EmbeddingJobCreateRequest"],
      headers: { "Idempotency-Key": `embedding-reviewer-denied-${unique}` },
    },
  );
  expect(deniedEmbedding.status()).toBe(403);

  await page.getByRole("button", { name: "Sign out" }).click();
  await login(page, "admin");
  await page.goto("/admin/knowledge");
  await page.getByLabel("Active curriculum").selectOption(curriculum.id);
  await expect(page.getByText("Admin create access")).toBeVisible();
  const questionSelection = page.getByRole("checkbox", {
    name: /Select historical question/i,
  });
  const chunkSelection = page.getByRole("checkbox", { name: /Select knowledge chunk/i });
  await expect(questionSelection).toBeVisible();
  await expect(chunkSelection).toBeVisible();
  await questionSelection.check();
  await chunkSelection.check();
  await expect(page.getByText("2 of 100 records selected")).toBeVisible();

  const jobCreatedResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith(`/curricula/${curriculum.id}/embedding-jobs`),
  );
  await page.getByRole("button", { name: "Queue selected records" }).click();
  const createResponse = await jobCreatedResponse;
  expect(createResponse.status()).toBe(202);
  const createdJob = (await createResponse.json()) as EmbeddingJob;
  expect(createdJob.historical_question_ids).toEqual([importedQuestion.id]);
  expect(createdJob.knowledge_chunk_ids).toEqual([importedChunk.id]);

  const jobCard = page.getByRole("region", {
    exact: true,
    name: `Embedding job ${createdJob.id}`,
  });
  await expect(jobCard).toBeVisible();
  await expect(jobCard.getByText("Succeeded", { exact: true })).toBeVisible({ timeout: 120_000 });
  await expect(jobCard.getByText(createdJob.configuration.provider, { exact: true })).toBeVisible();
  await expect(jobCard.getByText(createdJob.configuration.model, { exact: true })).toBeVisible();
  await expect(jobCard.getByText(String(createdJob.configuration.dimension), { exact: true })).toBeVisible();
  await expect(jobCard.getByText(createdJob.configuration.version, { exact: true })).toBeVisible();
  await expect(
    jobCard.getByText(createdJob.configuration.config_fingerprint, { exact: true }),
  ).toBeVisible();
  await expect(jobCard.getByText("Requested", { exact: true }).locator("..")).toContainText("2");
  await expect(jobCard.getByText("Embedded", { exact: true }).locator("..")).toContainText("2");
  await expect(jobCard.getByText("Deduplicated", { exact: true }).locator("..")).toContainText("0");
  await expect(jobCard.getByText("Submission deduplicated: No")).toBeVisible();
  await expect(jobCard.getByText("Original attempt", { exact: true })).toBeVisible();
  await expect(jobCard.getByText("Queued at").locator("..")).not.toContainText("Not yet");
  await expect(jobCard.getByText("Claimed at").locator("..")).not.toContainText("Not yet");
  await expect(jobCard.getByText("Completed at").locator("..")).not.toContainText("Not yet");
  await expect(jobCard.getByText("Sanitized failure code").locator("..")).toContainText("None");

  const configurationLabel = `${createdJob.configuration.provider} / ${createdJob.configuration.model} / ${createdJob.configuration.version} / ${createdJob.configuration.dimension}d`;
  const embeddedQuestionRow = page.getByRole("listitem", {
    name: new RegExp(`Historical question ${paperCode}`, "i"),
  });
  const embeddedChunkRow = page.getByRole("listitem", {
    name: new RegExp(`Knowledge chunk ${boundary}`, "i"),
  });
  for (const row of [embeddedQuestionRow, embeddedChunkRow]) {
    await expect(row.getByText("Embedded", { exact: true })).toBeVisible();
    await expect(row.getByText(configurationLabel, { exact: true })).toBeVisible();
    await expect(
      row.getByText(createdJob.configuration.config_fingerprint, { exact: true }),
    ).toBeVisible();
  }

  await page.getByRole("link", { name: "RAG Explorer" }).click();
  await expect(page.getByRole("heading", { name: "RAG Explorer" })).toBeVisible();
  await page.getByLabel("Active retrieval curriculum").selectOption(curriculum.id);
  const competencySelect = page.locator(`select:has(option[value="${competency.id}"])`);
  const skillSelect = page.locator(`select:has(option[value="${skill.id}"])`);
  const embeddingSelect = page.locator(
    `select:has(option[value="${createdJob.configuration.config_fingerprint}"])`,
  );
  await expect(embeddingSelect).toBeVisible();
  await competencySelect.selectOption(competency.id);
  await skillSelect.selectOption(skill.id);
  await embeddingSelect.selectOption(createdJob.configuration.config_fingerprint);
  await page.getByLabel("Retrieval query").fill(sourceMarker);

  const retrievalResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" && response.url().endsWith("/admin/retrieval/explore"),
  );
  await page.getByRole("button", { name: "Run retrieval" }).click();
  const retrievalResponse = await retrievalResponsePromise;
  expect(retrievalResponse.status()).toBe(200);
  const retrieval = (await retrievalResponse.json()) as RetrievalResult;
  expect(retrieval.channels.lexical.length).toBeGreaterThan(0);
  expect(retrieval.channels.vector.length).toBeGreaterThan(0);
  expect(retrieval.fused_candidates.length).toBeGreaterThan(0);
  expect(retrieval.context.items.length).toBeGreaterThan(0);
  expect(retrieval.context.character_count).toBeGreaterThan(0);
  expect(retrieval.diagnostics.hard_scope_filter_applied).toBe(true);
  expect(retrieval.embedding_config.config_fingerprint).toBe(
    createdJob.configuration.config_fingerprint,
  );

  const allowedRecordIds = new Set([importedQuestion.id, importedChunk.id]);
  for (const candidate of [...retrieval.channels.lexical, ...retrieval.channels.vector]) {
    expect(allowedRecordIds.has(candidate.chunk_id)).toBe(true);
    expect(candidate.scope.curriculum_version_id).toBe(curriculum.id);
    expect(candidate.scope.subject_id).toBe(subject.id);
    expect(candidate.scope.unit_ids).toEqual([]);
    expect(candidate.scope.lesson_ids).toEqual([]);
    expect(candidate.scope.taxonomy.competency_id).toBe(competency.id);
    expect(candidate.scope.taxonomy.skill_id).toBe(skill.id);
    expect(candidate.provenance.source_document_id).toBe(source.id);
    expect(candidate.trust).toBe("untrusted_source_data");
  }
  for (const candidate of retrieval.fused_candidates) {
    expect(candidate.source_chunk_ids.length).toBeGreaterThan(0);
    expect(candidate.source_chunk_ids.every((id) => allowedRecordIds.has(id))).toBe(true);
    expect(candidate.provenances.length).toBeGreaterThan(0);
    expect(candidate.scope.curriculum_version_id).toBe(curriculum.id);
    expect(candidate.scope.subject_id).toBe(subject.id);
    expect(candidate.scope.unit_ids).toEqual([]);
    expect(candidate.scope.lesson_ids).toEqual([]);
    expect(candidate.scope.taxonomy.competency_id).toBe(competency.id);
    expect(candidate.scope.taxonomy.skill_id).toBe(skill.id);
    expect(candidate.trust).toBe("untrusted_source_data");
  }
  for (const item of retrieval.context.items) {
    expect(item.source_chunk_ids.length).toBeGreaterThan(0);
    expect(item.source_chunk_ids.every((id) => allowedRecordIds.has(id))).toBe(true);
    expect(item.provenances.length).toBeGreaterThan(0);
    expect(item.scope.curriculum_version_id).toBe(curriculum.id);
    expect(item.scope.subject_id).toBe(subject.id);
    expect(item.scope.unit_ids).toEqual([]);
    expect(item.scope.lesson_ids).toEqual([]);
    expect(item.scope.taxonomy.competency_id).toBe(competency.id);
    expect(item.scope.taxonomy.skill_id).toBe(skill.id);
    expect(item.trust).toBe("untrusted_source_data");
  }
  expect(JSON.stringify(retrieval)).not.toContain(forbiddenDraft.id);
  expect(
    [...retrieval.channels.lexical, ...retrieval.channels.vector].some((candidate) =>
      candidate.text.includes(sourceMarker),
    ),
  ).toBe(true);

  const lexicalSection = page
    .getByRole("heading", { name: "Lexical channel" })
    .locator("xpath=ancestor::section[1]");
  const vectorSection = page
    .getByRole("heading", { name: "Vector channel" })
    .locator("xpath=ancestor::section[1]");
  const fusedSection = page
    .getByRole("heading", { name: "Fused ranking" })
    .locator("xpath=ancestor::section[1]");
  const contextSection = page
    .getByRole("heading", { name: "Bounded context" })
    .locator("xpath=ancestor::section[1]");
  const diagnosticsSection = page
    .getByRole("heading", { name: "Retrieval diagnostics" })
    .locator("xpath=ancestor::section[1]");
  await expect(lexicalSection.locator("ol > li").first()).toBeVisible();
  await expect(vectorSection.locator("ol > li").first()).toBeVisible();
  await expect(fusedSection.locator("ol > li").first()).toBeVisible();
  await expect(contextSection.locator("ol > li").first()).toBeVisible();
  await expect(contextSection.getByRole("list", { name: "Source provenance" }).first()).toBeVisible();
  await expect(diagnosticsSection.getByText("Yes", { exact: true })).toBeVisible();
  await expect(page.getByText(untrustedSourceText, { exact: true }).first()).toBeVisible();
  await expect(page.locator('img[src="x"]')).toHaveCount(0);
  await expect(page.getByText(forbiddenBoundary, { exact: true })).toHaveCount(0);

  expect(browserErrors.filter((error) => !error.includes("eval() is not supported in this environment"))).toEqual([]);
});
