import type { components } from "@exam-guru/api-client";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

type Exam = components["schemas"]["ExamConfigurationResponse"];
type Medium = components["schemas"]["MediumResponse"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type SourcePage = components["schemas"]["SourcePageResponse"];
type ExtractedBlock = components["schemas"]["ExtractedBlockResponse"];
type KnowledgeChunk = components["schemas"]["KnowledgeChunkResponse"];
type Blueprint = components["schemas"]["PaperBlueprintResponse"];
type BlueprintRequest = components["schemas"]["BlueprintCreateRequest"];
type GenerationRun = components["schemas"]["GenerationRunResponse"];
type GenerationRunSummary = components["schemas"]["GenerationRunSummaryResponse"];
type GenerationRequest = components["schemas"]["GenerationRunCreateRequest"];
type ValidationReport = components["schemas"]["ValidationRunResponse"];
type ValidationReportSummary = components["schemas"]["ValidationRunSummaryResponse"];
type ValidationRequest = components["schemas"]["ValidationRunCreateRequest"];
type ReviewCandidate = components["schemas"]["ReviewCandidateResponse"];
type ReviewEditRequest = components["schemas"]["ReviewCandidateEditRequest"];
type AuditEvent = components["schemas"]["AdminAuditEventResponse"];

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
  return Buffer.from(
    `${body}${xref}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`,
    "ascii",
  );
}

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

function blueprintRequest(
  curriculum: Curriculum,
  medium: Medium,
  competency: TaxonomyNode,
  skill: TaxonomyNode,
  unique: string,
): BlueprintRequest {
  const target = {
    competency_id: competency.id,
    learning_concept_id: null,
    skill_id: skill.id,
    sub_skill_id: null,
  };
  return {
    analytics_run_id: null,
    seed: 2026,
    specification: {
      config_version: "generation-e2e-v1",
      curriculum_scope: {
        curriculum_version_id: curriculum.id,
        grade: 5,
        medium: medium.code,
      },
      difficulty_allocations: [{ difficulty: "medium", exact_marks: 2, exact_slots: 1 }],
      generation_policy: {
        answer_requirements: ["Provide one unambiguous answer with marking guidance."],
        instructions: ["Use age-appropriate Grade 5 language."],
        response_language: medium.code,
        retrieval_query_hints: ["reviewed even number knowledge"],
        uniqueness: {
          forbid_duplicate_stems: true,
          forbid_verbatim_sources: true,
          max_similarity_basis_points: 8500,
          minimum_distinct_contexts: 1,
        },
      },
      paper_code: `GEN-${unique.toUpperCase()}`,
      question_type_allocations: [
        {
          archetypes: ["single_best_answer"],
          exact_marks: 2,
          exact_slots: 1,
          question_type: "multiple_choice",
        },
      ],
      sections: [
        {
          allowed_difficulties: ["medium"],
          allowed_marks_per_slot: [2],
          allowed_question_types: ["multiple_choice"],
          allowed_taxonomy_targets: [target],
          marks: 2,
          question_count: 1,
          retrieval_query_hints: ["selection section"],
          section_id: "A",
          title: "Selection",
        },
      ],
      taxonomy_requirements: [
        {
          allowed_section_ids: ["A"],
          generation_instructions: ["Use a familiar number setting."],
          maximum_slots: 1,
          minimum_slots: 1,
          priority: {
            baseline_evidence_refs: ["curriculum:reviewed-taxonomy"],
            baseline_score: 100,
            baseline_version: "syllabus-balanced-v1",
          },
          retrieval_query_hints: ["even numbers"],
          target,
        },
      ],
      title: `Generation E2E paper ${unique}`,
      total_marks: 2,
    },
  };
}

test("real generation reaches validation and reviewer approval with terminal audit history", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  const unique = `${Date.now()}`
    .slice(-9)
    .replaceAll(/\d/g, (digit) => String.fromCharCode(97 + Number(digit)));
  const code = unique.toUpperCase();
  const curriculumTitle = `Generation curriculum ${unique}`;
  const boundary = `Even number knowledge ${unique}`;
  await login(page, "admin");

  const exam = await postCreated<Exam>(page.request, "/api/v1/admin/exam-configurations", {
    code: `GE${code}`,
    grade: 5,
    name: `Generation exam ${unique}`,
  });
  const medium = await postCreated<Medium>(page.request, "/api/v1/admin/media", {
    code: `en-${unique.slice(-7)}`,
    name: `Generation English ${unique}`,
  });
  const curriculum = await postCreated<Curriculum>(
    page.request,
    "/api/v1/admin/curriculum-versions",
    {
      code: `GC-${code}`,
      exam_configuration_id: exam.id,
      medium_id: medium.id,
      title: curriculumTitle,
    },
  );
  const competency = await postCreated<TaxonomyNode>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes`,
    {
      active: true,
      code: `C${code}`,
      level: "competency",
      parent_id: null,
      title: `Number competency ${unique}`,
    },
  );
  expect(
    (
      await page.request.post(
        `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes/${competency.id}/review`,
      )
    ).ok(),
  ).toBe(true);
  const skill = await postCreated<TaxonomyNode>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes`,
    {
      active: true,
      code: `S${code}`,
      level: "skill",
      parent_id: competency.id,
      title: `Even number skill ${unique}`,
    },
  );
  expect(
    (
      await page.request.post(
        `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes/${skill.id}/review`,
      )
    ).ok(),
  ).toBe(true);

  const upload = await page.request.post("/api/v1/admin/source-documents", {
    multipart: {
      curriculum_version_id: curriculum.id,
      document_type: "syllabus",
      file: {
        buffer: syntheticPdf(`Four is an even number ${unique}`),
        mimeType: "application/pdf",
        name: `generation-source-${unique}.pdf`,
      },
    },
  });
  expect(upload.status()).toBe(201);
  const source = (await upload.json()) as SourceDocument;
  expect((await page.request.post(`/api/v1/admin/source-documents/${source.id}/extract`)).status()).toBe(
    202,
  );
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
  if (!sourcePage) throw new Error("Generation source page was not extracted");
  const blocksResponse = await page.request.get(
    `/api/v1/admin/source-documents/${source.id}/pages/${sourcePage.page_number}/blocks`,
  );
  expect(blocksResponse.ok()).toBe(true);
  const [sourceBlock] = (await blocksResponse.json()) as ExtractedBlock[];
  if (!sourceBlock) throw new Error("Generation source block was not extracted");

  const importedChunk = await postCreated<KnowledgeChunk>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/chunks`,
    {
      chunk_type: "explanation",
      educational_boundary: boundary,
      page_number: sourcePage.page_number,
      sequence: 1,
      source_block_id: sourceBlock.id,
      source_document_id: source.id,
      text: sourceBlock.reviewed_text ?? sourceBlock.raw_text,
    },
  );
  const classificationResponse = await page.request.patch(
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/chunks/${importedChunk.id}/classification`,
    {
      data: {
        competency_id: competency.id,
        expected_version: importedChunk.version,
        learning_concept_id: null,
        skill_id: skill.id,
        sub_skill_id: null,
      },
    },
  );
  expect(classificationResponse.ok()).toBe(true);
  const classifiedChunk = (await classificationResponse.json()) as KnowledgeChunk;
  const inReviewResponse = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/chunks/${classifiedChunk.id}/review`,
    { data: { expected_version: classifiedChunk.version, target: "in_review" } },
  );
  expect(inReviewResponse.ok()).toBe(true);
  const inReviewChunk = (await inReviewResponse.json()) as KnowledgeChunk;
  const reviewedResponse = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/chunks/${inReviewChunk.id}/review`,
    { data: { expected_version: inReviewChunk.version, target: "reviewed" } },
  );
  expect(reviewedResponse.ok()).toBe(true);
  const reviewedChunk = (await reviewedResponse.json()) as KnowledgeChunk;
  expect(reviewedChunk.review_state).toBe("reviewed");

  const blueprintResponse = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/blueprints`,
    { data: blueprintRequest(curriculum, medium, competency, skill, unique) },
  );
  expect(blueprintResponse.status()).toBe(201);
  const blueprint = (await blueprintResponse.json()) as Blueprint;
  const exactSlot = blueprint.blueprint.slots[0];
  if (!exactSlot) throw new Error("Generation blueprint did not contain an exact slot");

  await page.goto("/admin/generation");
  await expect(page.getByRole("heading", { name: "Generation Studio" })).toBeVisible();
  await page.getByLabel("Active Grade 5 curriculum").selectOption(curriculum.id);
  await page.getByLabel("Immutable blueprint").selectOption(blueprint.id);
  await expect(page.getByLabel("Exact blueprint slot")).toHaveValue(exactSlot.slot_id);
  const contextChoice = page.getByRole("checkbox", {
    name: `Select knowledge chunk ${reviewedChunk.id}`,
  });
  await expect(contextChoice).toBeEnabled();
  await contextChoice.check();
  await page.getByRole("button", { name: "Create generation run" }).click();
  await expect(page.getByText("Generation run queued.")).toBeVisible();
  await expect(page.getByText("REQUIRES VALIDATION")).toBeVisible({ timeout: 45_000 });
  await expect(page.getByRole("region", { name: "Generation run overview" })).toContainText(
    "Succeeded",
  );
  await expect(page.getByRole("region", { name: "Persisted generation context" })).toContainText(
    reviewedChunk.text,
  );
  await expect(page.getByRole("region", { name: "Generated candidate" })).toContainText(
    "Which response is supported by the reviewed context?",
  );
  await expect(page.getByText(/No publish action is available/i)).toBeVisible();

  const listResponse = await page.request.get(
    `/api/v1/admin/curricula/${curriculum.id}/generation-runs`,
  );
  expect(listResponse.ok()).toBe(true);
  const summaries = (await listResponse.json()) as GenerationRunSummary[];
  const persisted = summaries.find((run) => run.paper_blueprint_id === blueprint.id);
  expect(persisted?.status).toBe("succeeded");
  const detailResponse = await page.request.get(
    `/api/v1/admin/curricula/${curriculum.id}/generation-runs/${persisted?.id}`,
  );
  expect(detailResponse.ok()).toBe(true);
  const run = (await detailResponse.json()) as GenerationRun;
  expect(run.disposition).toBe("requires_validation");
  expect(run.context[0]?.record_id).toBe(reviewedChunk.id);
  expect(run.provider).toBe("deterministic-fake");

  await page.goto("/admin/validation");
  await expect(page.getByRole("heading", { name: "Validation Studio" })).toBeVisible();
  await page.getByLabel("Active Grade 5 curriculum").selectOption(curriculum.id);
  await expect(page.getByLabel("Generation run")).toContainText(run.id);
  await page.getByLabel("Generation run").selectOption(run.id);
  await page.getByRole("button", { name: "Run deterministic validation" }).click();
  await expect(page.getByText("Immutable validation report created. Human review is still required.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Validation report metadata" })).toContainText(
    "Deterministic result:",
  );
  await expect(page.getByRole("region", { name: "Grounding provenance" })).toContainText(
    source.id,
  );

  const validationListResponse = await page.request.get(
    `/api/v1/admin/curricula/${curriculum.id}/validation-runs`,
  );
  expect(validationListResponse.ok()).toBe(true);
  const validationSummaries = (await validationListResponse.json()) as ValidationReportSummary[];
  const validationSummary = validationSummaries.find(
    (item) => item.generation_run_id === run.id,
  );
  if (!validationSummary) throw new Error("Validation UI did not persist a report for the generation");
  const validationDetailResponse = await page.request.get(
    `/api/v1/admin/curricula/${curriculum.id}/validation-runs/${validationSummary.id}`,
  );
  expect(validationDetailResponse.ok()).toBe(true);
  const validationReport = (await validationDetailResponse.json()) as ValidationReport;
  expect(validationReport.finding_count).toBeGreaterThan(0);
  expect(validationReport.limitations.length).toBeGreaterThan(0);

  await page.getByRole("button", { name: "Sign out" }).click();
  await login(page, "reviewer");
  await page.goto("/admin/generation");
  await page.getByLabel("Active Grade 5 curriculum").selectOption(curriculum.id);
  await expect(page.getByText("Reviewer read access")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Reviewer read-only mode" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Generation run overview" })).toContainText(
    "Succeeded",
  );
  await expect(page.getByRole("region", { name: "Generated candidate" })).toContainText(
    "Which response is supported by the reviewed context?",
  );
  await expect(page.getByRole("button", { name: "Create generation run" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry failed run" })).toHaveCount(0);

  await page.goto("/admin/validation");
  await page.getByLabel("Active Grade 5 curriculum").selectOption(curriculum.id);
  await expect(page.getByRole("heading", { name: "Reviewer read-only mode" })).toBeVisible();
  await page.getByRole("button", { name: `Select validation report ${validationSummary.id}` }).click();
  await expect(page.getByRole("region", { name: "Validation report metadata" })).toContainText(
    validationReport.pipeline_version,
  );
  await expect(page.getByText(/A passing report does not establish factual or semantic correctness/i)).toBeVisible();
  await expect(page.getByRole("region", { name: "Validation findings" })).toContainText(
    `of ${validationReport.finding_count}`,
  );
  await expect(page.getByRole("region", { name: "Grounding provenance" })).toContainText(
    source.id,
  );
  await expect(page.getByRole("button", { name: "Run deterministic validation" })).toHaveCount(0);
  expect(validationReport.overall_status).toBe("pass");

  await page.goto("/admin/review");
  await expect(page.getByRole("heading", { name: "Reviewer Studio" })).toBeVisible();
  await page.getByLabel("Active Grade 5 curriculum").selectOption(curriculum.id);
  await expect(page.getByLabel("Passing validation run")).toContainText(validationSummary.id);
  await page.getByLabel("Passing validation run").selectOption(validationSummary.id);
  await page.getByRole("button", { name: "Create review candidate" }).click();
  await expect(
    page.getByText("Review candidate created from persisted PASS validation evidence."),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Candidate review editor" })).toContainText(
    "Validated",
  );
  await expect(page.getByRole("region", { name: "Generated revision 1 evidence" })).toContainText(
    "Which response is supported by the reviewed context?",
  );
  await expect(page.getByRole("region", { name: "Generation context provenance" })).toContainText(
    reviewedChunk.id,
  );
  await expect(page.getByRole("region", { name: "P8 validation report and findings" })).toContainText(
    validationReport.pipeline_version,
  );
  await expect(page.getByText(/Automated validation applies to generated revision 1 only/i)).toBeVisible();
  await expect(page.getByText(/Human edits are not automatically revalidated/i)).toBeVisible();
  await expect(page.getByText(/Approval does not publish/i)).toBeVisible();

  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByText("Human review started.")).toBeVisible();
  await expect(page.getByLabel("Question type (locked)")).toBeDisabled();
  await expect(page.getByLabel("Marks (locked)")).toBeDisabled();
  const reviewedStem = `Which response is supported by the reviewed context for ${unique}?`;
  await page.getByLabel("Question stem").fill(reviewedStem);
  await page.getByLabel("Option B text").fill("The supported even-number choice");
  await page.getByLabel("Explanation").fill("The trusted reviewed source supports option B.");
  await page
    .getByLabel("Marking guide (one item per line)")
    .fill("Award two marks for selecting B.\nAward no marks for unsupported choices.");
  await page.getByLabel("Edit reason").fill("Clarify the grounded wording and marking guidance.");
  await page.getByRole("button", { name: "Save revision" }).click();
  await expect(
    page.getByText("Revision 2 saved. Automated validation still applies only to revision 1."),
  ).toBeVisible();
  await expect(page.getByLabel("Question stem")).toHaveValue(reviewedStem);

  await page
    .getByLabel("Approval note (optional)")
    .fill("Source, answer, explanation, and marking guidance reviewed.");
  await page.getByRole("button", { name: "Approve candidate" }).click();
  await expect(page.getByText("Candidate approved. This is not a publish action.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Approved terminal state" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Candidate revisions and events" })).toContainText(
    "Revision 2",
  );
  await expect(page.getByRole("region", { name: "Candidate revisions and events" })).toContainText(
    "Started",
  );
  await expect(page.getByRole("region", { name: "Candidate revisions and events" })).toContainText(
    "Edited",
  );
  await expect(page.getByRole("region", { name: "Candidate revisions and events" })).toContainText(
    "Approved",
  );
  await expect(page.getByRole("region", { name: "Review decision" })).toContainText(
    "Source, answer, explanation, and marking guidance reviewed.",
  );

  const candidateResponse = await page.request.get(
    `/api/v1/admin/curricula/${curriculum.id}/review-candidates/${run.id}`,
  );
  expect(candidateResponse.ok()).toBe(true);
  const approvedCandidate = (await candidateResponse.json()) as ReviewCandidate;
  expect(approvedCandidate.state).toBe("approved");
  expect(approvedCandidate.current_revision).toBe(2);
  expect(approvedCandidate.current_content.stem).toBe(reviewedStem);
  expect(approvedCandidate.validation.validated_revision).toBe(1);
  expect(approvedCandidate.events.map((event) => event.action)).toEqual([
    "started",
    "edited",
    "approved",
  ]);

  const auditResponse = await page.request.get(
    "/api/v1/admin/audit-events?resource_type=question_candidate&limit=200",
  );
  expect(auditResponse.ok()).toBe(true);
  const candidateAudit = ((await auditResponse.json()) as AuditEvent[])
    .filter((event) => event.resource_id === approvedCandidate.id)
    .map((event) => event.action);
  expect(candidateAudit).toEqual(
    expect.arrayContaining([
      "question_candidate.created",
      "question_candidate.review_started",
      "question_candidate.edited",
      "question_candidate.approved",
    ]),
  );

  const terminalPayload: ReviewEditRequest = {
    content: approvedCandidate.current_content,
    expected_version: approvedCandidate.version,
    reason: "Attempt to mutate an approved terminal candidate.",
  };
  const terminalMutation = await page.request.patch(
    `/api/v1/admin/curricula/${curriculum.id}/review-candidates/${approvedCandidate.id}`,
    { data: terminalPayload },
  );
  expect(terminalMutation.status()).toBe(409);
  expect((await terminalMutation.json()).detail.code).toBe("review_candidate_state_conflict");

  const reviewerValidationPayload: ValidationRequest = { generation_run_id: run.id };
  const validationDenied = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/validation-runs`,
    { data: reviewerValidationPayload },
  );
  expect(validationDenied.status()).toBe(403);

  const reviewerPayload: GenerationRequest = {
    historical_question_ids: [],
    knowledge_chunk_ids: [reviewedChunk.id],
    paper_blueprint_id: blueprint.id,
    slot_id: exactSlot.slot_id,
  };
  const denied = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/generation-runs`,
    {
      data: reviewerPayload,
      headers: { "Idempotency-Key": `generation-reviewer-denied-${unique}` },
    },
  );
  expect(denied.status()).toBe(403);
  expect(browserErrors).toEqual([]);
});
