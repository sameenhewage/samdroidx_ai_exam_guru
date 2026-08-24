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
type BlueprintSlot = components["schemas"]["BlueprintSlotResponse"];
type BlueprintRequest = components["schemas"]["BlueprintCreateRequest"];
type QuestionType = components["schemas"]["QuestionType"];
type GenerationRun = components["schemas"]["GenerationRunResponse"];
type GenerationRunSummary = components["schemas"]["GenerationRunSummaryResponse"];
type GenerationRequest = components["schemas"]["GenerationRunCreateRequest"];
type ValidationReport = components["schemas"]["ValidationRunResponse"];
type ValidationReportSummary = components["schemas"]["ValidationRunSummaryResponse"];
type ValidationFinding = components["schemas"]["ValidationFindingResponse"];
type ValidationRequest = components["schemas"]["ValidationRunCreateRequest"];
type ReviewCandidate = components["schemas"]["ReviewCandidateResponse"];
type ReviewEditRequest = components["schemas"]["ReviewCandidateEditRequest"];
type PaperDraft = components["schemas"]["PaperDraftVersionResponse"];
type PaperPublishRequest = components["schemas"]["PaperPublishRequest"];
type PaperSummary = components["schemas"]["PaperSummaryResponse"];
type Publication = components["schemas"]["PublishedPaperVersionResponse"];
type AuditEvent = components["schemas"]["AdminAuditEventResponse"];

type GeneratedSlot = {
  expectedStem: string;
  run: GenerationRun;
  slot: BlueprintSlot;
};

type ValidatedSlot = GeneratedSlot & {
  validation: ValidationReport;
};

type ReviewedSlot = ValidatedSlot & {
  candidate: ReviewCandidate;
  publishedStem: string;
};

const DETERMINISTIC_STEMS: Record<QuestionType, string> = {
  multiple_choice: "Which response is supported by the reviewed context?",
  short_answer: "Write a short answer supported by the reviewed context.",
  structured: "Construct a response using evidence from the reviewed source.",
};
const QUESTION_TYPE_LABELS: Record<QuestionType, string> = {
  multiple_choice: "Multiple choice",
  short_answer: "Short answer",
  structured: "Structured",
};

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

async function getJson<ResponseDto>(
  request: APIRequestContext,
  path: string,
): Promise<ResponseDto> {
  const response = await request.get(path);
  expect(response.ok(), `GET ${path} should succeed`).toBe(true);
  return (await response.json()) as ResponseDto;
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
      config_version: "generation-complete-paper-e2e-v1",
      curriculum_scope: {
        curriculum_version_id: curriculum.id,
        grade: 5,
        medium: medium.code,
      },
      difficulty_allocations: [{ difficulty: "medium", exact_marks: 6, exact_slots: 3 }],
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
        {
          archetypes: ["short_constructed_response"],
          exact_marks: 2,
          exact_slots: 1,
          question_type: "short_answer",
        },
        {
          archetypes: ["evidence_response"],
          exact_marks: 2,
          exact_slots: 1,
          question_type: "structured",
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
        {
          allowed_difficulties: ["medium"],
          allowed_marks_per_slot: [2],
          allowed_question_types: ["short_answer"],
          allowed_taxonomy_targets: [target],
          marks: 2,
          question_count: 1,
          retrieval_query_hints: ["short answer section"],
          section_id: "B",
          title: "Short answer",
        },
        {
          allowed_difficulties: ["medium"],
          allowed_marks_per_slot: [2],
          allowed_question_types: ["structured"],
          allowed_taxonomy_targets: [target],
          marks: 2,
          question_count: 1,
          retrieval_query_hints: ["structured response section"],
          section_id: "C",
          title: "Structured response",
        },
      ],
      taxonomy_requirements: [
        {
          allowed_section_ids: ["A", "B", "C"],
          generation_instructions: ["Use a familiar number setting."],
          maximum_slots: 3,
          minimum_slots: 3,
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
      total_marks: 6,
    },
  };
}

function assertRepresentativeBlueprint(
  blueprint: Blueprint,
  competency: TaxonomyNode,
  skill: TaxonomyNode,
) {
  const slots = blueprint.blueprint.slots;
  expect(blueprint.slot_count).toBe(3);
  expect(blueprint.total_marks).toBe(6);
  expect(blueprint.specification.total_marks).toBe(6);
  expect(
    blueprint.specification.question_type_allocations.map((allocation) => ({
      marks: allocation.exact_marks,
      slots: allocation.exact_slots,
      type: allocation.question_type,
    })),
  ).toEqual([
    { marks: 2, slots: 1, type: "multiple_choice" },
    { marks: 2, slots: 1, type: "short_answer" },
    { marks: 2, slots: 1, type: "structured" },
  ]);
  expect(
    blueprint.specification.sections.map((section) => ({
      marks: section.marks,
      questionCount: section.question_count,
      sectionId: section.section_id,
    })),
  ).toEqual([
    { marks: 2, questionCount: 1, sectionId: "A" },
    { marks: 2, questionCount: 1, sectionId: "B" },
    { marks: 2, questionCount: 1, sectionId: "C" },
  ]);
  expect(blueprint.blueprint.sections).toEqual([
    { marks: 2, section_id: "A", slot_count: 1, title: "Selection" },
    { marks: 2, section_id: "B", slot_count: 1, title: "Short answer" },
    { marks: 2, section_id: "C", slot_count: 1, title: "Structured response" },
  ]);
  expect(blueprint.specification.taxonomy_requirements).toHaveLength(1);
  expect(blueprint.specification.taxonomy_requirements[0]).toMatchObject({
    allowed_section_ids: ["A", "B", "C"],
    maximum_slots: 3,
    minimum_slots: 3,
    target: { competency_id: competency.id, skill_id: skill.id },
  });
  expect(
    slots.map((slot) => ({
      marks: slot.marks,
      ordinal: slot.ordinal,
      sectionId: slot.section_id,
      sectionOrdinal: slot.section_ordinal,
      taxonomy: slot.taxonomy_target,
      type: slot.question_type,
    })),
  ).toEqual([
    {
      marks: 2,
      ordinal: 1,
      sectionId: "A",
      sectionOrdinal: 1,
      taxonomy: expect.objectContaining({ competency_id: competency.id, skill_id: skill.id }),
      type: "multiple_choice",
    },
    {
      marks: 2,
      ordinal: 2,
      sectionId: "B",
      sectionOrdinal: 1,
      taxonomy: expect.objectContaining({ competency_id: competency.id, skill_id: skill.id }),
      type: "short_answer",
    },
    {
      marks: 2,
      ordinal: 3,
      sectionId: "C",
      sectionOrdinal: 1,
      taxonomy: expect.objectContaining({ competency_id: competency.id, skill_id: skill.id }),
      type: "structured",
    },
  ]);
}

async function loadGenerationRunForSlot(
  request: APIRequestContext,
  curriculumId: string,
  blueprintId: string,
  slotId: string,
): Promise<GenerationRun> {
  const summaries = await getJson<GenerationRunSummary[]>(
    request,
    `/api/v1/admin/curricula/${curriculumId}/generation-runs`,
  );
  const matches = summaries.filter(
    (run) => run.paper_blueprint_id === blueprintId && run.slot_id === slotId,
  );
  expect(matches).toHaveLength(1);
  return getJson<GenerationRun>(
    request,
    `/api/v1/admin/curricula/${curriculumId}/generation-runs/${matches[0]?.id}`,
  );
}

async function generateSlotThroughUi(
  page: Page,
  curriculum: Curriculum,
  blueprint: Blueprint,
  slot: BlueprintSlot,
  context: KnowledgeChunk,
): Promise<GeneratedSlot> {
  const expectedStem = DETERMINISTIC_STEMS[slot.question_type];
  await page.getByLabel("Exact blueprint slot").selectOption(slot.slot_id);
  await expect(page.getByLabel("Exact blueprint slot")).toHaveValue(slot.slot_id);
  const contextChoice = page.getByRole("checkbox", {
    name: `Select knowledge chunk ${context.id}`,
  });
  await expect(contextChoice).toBeEnabled();
  await contextChoice.check();
  await page.getByRole("button", { name: "Create generation run" }).click();
  await expect(page.getByText("Generation run queued.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Generation run overview" })).toContainText(
    "Succeeded",
    { timeout: 45_000 },
  );
  await expect(
    page.getByRole("region", { name: "Immutable blueprint and slot snapshot" }),
  ).toContainText(slot.slot_id);
  await expect(page.getByRole("region", { name: "Persisted generation context" })).toContainText(
    context.text,
  );
  await expect(page.getByRole("region", { name: "Generated candidate" })).toContainText(
    expectedStem,
  );
  await expect(page.getByText("REQUIRES VALIDATION")).toBeVisible();

  const run = await loadGenerationRunForSlot(
    page.request,
    curriculum.id,
    blueprint.id,
    slot.slot_id,
  );
  expect(run.status).toBe("succeeded");
  expect(run.disposition).toBe("requires_validation");
  expect(run.provider).toBe("deterministic-fake");
  expect(run.model).toBe("fixture-model");
  expect(run.cost_microusd).toBe(0);
  expect(run.context.map((item) => item.record_id)).toEqual([context.id]);
  expect(run.candidate).toMatchObject({
    question_type: slot.question_type,
    stem: expectedStem,
  });
  return { expectedStem, run, slot };
}

async function validateSlotThroughUi(
  page: Page,
  curriculum: Curriculum,
  source: SourceDocument,
  generated: GeneratedSlot,
  expectedDuplicateReferenceCount: number,
): Promise<ValidatedSlot> {
  await page.getByLabel("Generation run").selectOption(generated.run.id);
  await page.getByRole("button", { name: "Run deterministic validation" }).click();
  const reportMetadata = page.getByRole("region", { name: "Validation report metadata" });
  await expect(reportMetadata).toContainText(generated.run.id);
  await expect(reportMetadata).toContainText("Deterministic result: Pass");
  await expect(page.getByRole("region", { name: "Grounding provenance" })).toContainText(
    source.id,
  );

  const summaries = await getJson<ValidationReportSummary[]>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/validation-runs`,
  );
  const matching = summaries.filter((report) => report.generation_run_id === generated.run.id);
  expect(matching).toHaveLength(1);
  const validation = await getJson<ValidationReport>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/validation-runs/${matching[0]?.id}`,
  );
  const findings = await getJson<ValidationFinding[]>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/validation-runs/${validation.id}/findings?limit=100&offset=0`,
  );
  const duplicateFindings = findings.filter((finding) => finding.code.startsWith("duplicate."));
  const lexicalFinding = duplicateFindings.find(
    (finding) => finding.code === "duplicate.lexical_similarity_indicator",
  );
  const lexicalEvidence = lexicalFinding?.evidence.find((item) =>
    item.observed?.includes("score_basis_points="),
  );
  const lexicalScore = Number(
    lexicalEvidence?.observed.match(/score_basis_points=(\d+)/)?.[1] ?? Number.NaN,
  );

  expect(validation.overall_status).toBe("pass");
  expect(validation.duplicate_reference_count).toBe(expectedDuplicateReferenceCount);
  expect(duplicateFindings).toHaveLength(3);
  expect(duplicateFindings.map((finding) => finding.status)).toEqual(["pass", "pass", "pass"]);
  expect(lexicalScore).toBeLessThan(8_000);
  expect(validation.limitations.join(" ").toLowerCase()).toContain(
    "does not establish factual or semantic correctness",
  );
  return { ...generated, validation };
}

async function reviewSlotThroughUi(
  page: Page,
  curriculum: Curriculum,
  context: KnowledgeChunk,
  validated: ValidatedSlot,
  editStem: string | null,
): Promise<ReviewedSlot> {
  await page.getByLabel("Passing validation run").selectOption(validated.validation.id);
  await page.getByRole("button", { name: "Create review candidate" }).click();
  await expect(
    page.getByText("Review candidate created from persisted PASS validation evidence."),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Generated revision 1 evidence" })).toContainText(
    validated.expectedStem,
  );
  await expect(page.getByRole("region", { name: "Generation blueprint evidence" })).toContainText(
    validated.slot.slot_id,
  );
  await expect(page.getByRole("region", { name: "Generation context provenance" })).toContainText(
    context.id,
  );
  await expect(
    page.getByRole("region", { name: "P8 validation report and findings" }),
  ).toContainText(validated.validation.pipeline_version);

  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByText("Human review started.")).toBeVisible();
  await expect(page.getByLabel("Question type (locked)")).toBeDisabled();
  await expect(page.getByLabel("Marks (locked)")).toBeDisabled();

  if (editStem !== null) {
    await page.getByLabel("Question stem").fill(editStem);
    await page.getByLabel("Edit reason").fill("Clarify the reviewed context wording.");
    await page.getByRole("button", { name: "Save revision" }).click();
    await expect(
      page.getByText("Revision 2 saved. Automated validation still applies only to revision 1."),
    ).toBeVisible();
    await expect(page.getByLabel("Question stem")).toHaveValue(editStem);
  }

  const approvalNote = `Human checked ${validated.slot.question_type} source, answer, and marking.`;
  await page.getByLabel("Approval note (optional)").fill(approvalNote);
  await page.getByRole("button", { name: "Approve candidate" }).click();
  await expect(page.getByText("Candidate approved. This is not a publish action.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Approved terminal state" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Candidate revisions and events" })).toContainText(
    "Approved",
  );

  const candidate = await getJson<ReviewCandidate>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/review-candidates/${validated.run.id}`,
  );
  const expectedActions = editStem === null ? ["started", "approved"] : ["started", "edited", "approved"];
  expect(candidate).toMatchObject({
    blueprint_slot_id: validated.slot.slot_id,
    current_content: {
      marks: 2,
      question_type: validated.slot.question_type,
      stem: editStem ?? validated.expectedStem,
    },
    generation_run_id: validated.run.id,
    state: "approved",
    validation: {
      passed: true,
      validated_revision: 1,
      validation_run_id: validated.validation.id,
    },
  });
  expect(candidate.current_revision).toBe(editStem === null ? 1 : 2);
  expect(candidate.events.map((event) => event.action)).toEqual(expectedActions);
  return {
    ...validated,
    candidate,
    publishedStem: editStem ?? validated.expectedStem,
  };
}

test("representative mixed paper completes generation, validation, human review, and immutable publication", async ({
  page,
}) => {
  test.setTimeout(240_000);
  test.info().annotations.push({
    type: "limitation",
    description:
      "This deterministic three-type fixture proves representative lifecycle and exact-slot acceptance only; it makes no factual, semantic, language, curriculum, paraphrase-uniqueness, or paid-model quality claim.",
  });
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
        const documents = await getJson<SourceDocument[]>(
          page.request,
          "/api/v1/admin/source-documents",
        );
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

  const [sourcePage] = await getJson<SourcePage[]>(
    page.request,
    `/api/v1/admin/source-documents/${source.id}/pages`,
  );
  if (!sourcePage) throw new Error("Generation source page was not extracted");
  const [sourceBlock] = await getJson<ExtractedBlock[]>(
    page.request,
    `/api/v1/admin/source-documents/${source.id}/pages/${sourcePage.page_number}/blocks`,
  );
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

  const blueprint = await postCreated<Blueprint>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/blueprints`,
    blueprintRequest(curriculum, medium, competency, skill, unique),
  );
  assertRepresentativeBlueprint(blueprint, competency, skill);
  const exactSlots = blueprint.blueprint.slots;

  await page.goto("/admin/generation");
  await expect(page.getByRole("heading", { name: "Generation Studio" })).toBeVisible();
  await page.getByLabel("Active Grade 5 curriculum").selectOption(curriculum.id);
  await page.getByLabel("Immutable blueprint").selectOption(blueprint.id);
  const generatedSlots: GeneratedSlot[] = [];
  for (const slot of exactSlots) {
    generatedSlots.push(
      await generateSlotThroughUi(page, curriculum, blueprint, slot, reviewedChunk),
    );
  }
  await expect(page.getByText(/No publish action is available/i)).toBeVisible();
  expect(generatedSlots.map((item) => item.run.slot_id)).toEqual(exactSlots.map((slot) => slot.slot_id));
  expect(generatedSlots.map((item) => item.run.provider)).toEqual([
    "deterministic-fake",
    "deterministic-fake",
    "deterministic-fake",
  ]);

  await page.goto("/admin/validation");
  await expect(page.getByRole("heading", { name: "Validation Studio" })).toBeVisible();
  await page.getByLabel("Active Grade 5 curriculum").selectOption(curriculum.id);
  const validatedSlots: ValidatedSlot[] = [];
  for (const [index, generated] of generatedSlots.entries()) {
    validatedSlots.push(
      await validateSlotThroughUi(page, curriculum, source, generated, index),
    );
  }
  await expect(
    page.getByRole("heading", { name: "Deterministic validation is limited" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await login(page, "reviewer");
  await page.goto("/admin/review");
  await expect(page.getByRole("heading", { name: "Reviewer Studio" })).toBeVisible();
  await page.getByLabel("Active Grade 5 curriculum").selectOption(curriculum.id);
  const reviewedSlots: ReviewedSlot[] = [];
  for (const validated of validatedSlots) {
    const editStem =
      validated.slot.question_type === "multiple_choice"
        ? `Which response is supported by the reviewed context for ${unique}?`
        : null;
    reviewedSlots.push(
      await reviewSlotThroughUi(page, curriculum, reviewedChunk, validated, editStem),
    );
  }
  expect(reviewedSlots.map((item) => item.candidate.state)).toEqual([
    "approved",
    "approved",
    "approved",
  ]);
  expect(reviewedSlots.filter((item) => item.candidate.current_revision === 2)).toHaveLength(1);

  const editedCandidate = reviewedSlots.find((item) => item.candidate.current_revision === 2)?.candidate;
  if (!editedCandidate) throw new Error("The representative paper did not retain its required edit");
  const terminalPayload: ReviewEditRequest = {
    content: editedCandidate.current_content,
    expected_version: editedCandidate.version,
    reason: "Attempt to mutate an approved terminal candidate.",
  };
  const terminalMutation = await page.request.patch(
    `/api/v1/admin/curricula/${curriculum.id}/review-candidates/${editedCandidate.id}`,
    { data: terminalPayload },
  );
  expect(terminalMutation.status()).toBe(409);
  expect((await terminalMutation.json()).detail.code).toBe("review_candidate_state_conflict");

  const candidateAudit = await getJson<AuditEvent[]>(
    page.request,
    "/api/v1/admin/audit-events?resource_type=question_candidate&limit=200",
  );
  for (const reviewed of reviewedSlots) {
    const actions = candidateAudit
      .filter((event) => event.resource_id === reviewed.candidate.id)
      .map((event) => event.action);
    expect(actions).toEqual(
      expect.arrayContaining([
        "question_candidate.created",
        "question_candidate.review_started",
        "question_candidate.approved",
        ...(reviewed.candidate.current_revision === 2 ? ["question_candidate.edited"] : []),
      ]),
    );
  }

  const [firstReviewed] = reviewedSlots;
  if (!firstReviewed) throw new Error("The representative paper has no reviewed slot");
  const reviewerValidationPayload: ValidationRequest = {
    generation_run_id: firstReviewed.run.id,
  };
  const reviewerValidationDenied = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/validation-runs`,
    { data: reviewerValidationPayload },
  );
  expect(reviewerValidationDenied.status()).toBe(403);
  const reviewerGenerationPayload: GenerationRequest = {
    historical_question_ids: [],
    knowledge_chunk_ids: [reviewedChunk.id],
    paper_blueprint_id: blueprint.id,
    slot_id: firstReviewed.slot.slot_id,
  };
  const reviewerGenerationDenied = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/generation-runs`,
    {
      data: reviewerGenerationPayload,
      headers: { "Idempotency-Key": `generation-reviewer-denied-${unique}` },
    },
  );
  expect(reviewerGenerationDenied.status()).toBe(403);

  const generationStateBeforePaper = await getJson<GenerationRunSummary[]>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/generation-runs`,
  );
  expect(generationStateBeforePaper).toHaveLength(3);
  expect(
    generationStateBeforePaper.map((run) => ({
      attempts: run.attempt_count,
      provider: run.provider,
      status: run.status,
    })),
  ).toEqual([
    { attempts: 1, provider: "deterministic-fake", status: "succeeded" },
    { attempts: 1, provider: "deterministic-fake", status: "succeeded" },
    { attempts: 1, provider: "deterministic-fake", status: "succeeded" },
  ]);

  await page.goto("/admin/papers");
  await expect(page.getByRole("heading", { name: "Paper Studio" })).toBeVisible();
  await page.getByLabel("Active Grade 5 curriculum").selectOption(curriculum.id);
  await page.getByLabel("Immutable paper blueprint").selectOption(blueprint.id);
  const slotRows = page.getByTestId("exact-blueprint-slot");
  await expect(slotRows).toHaveCount(3);
  for (const [index, reviewed] of reviewedSlots.entries()) {
    await expect(slotRows.nth(index)).toContainText(reviewed.slot.slot_id);
    await expect(slotRows.nth(index)).toContainText(
      QUESTION_TYPE_LABELS[reviewed.slot.question_type],
    );
    await expect(slotRows.nth(index)).toContainText("2 marks");
    await page
      .getByLabel(`Candidate for exact slot ${reviewed.slot.slot_id}`)
      .selectOption(reviewed.candidate.id);
  }
  const paperTitle = `Published generation paper ${unique}`;
  await page.getByLabel("Paper title").fill(paperTitle);
  await page.getByRole("button", { name: "Create immutable draft" }).click();
  await expect(page.getByText("Immutable draft version 1 created.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Selected paper lifecycle" })).toContainText(
    "Draft",
  );
  for (const reviewed of reviewedSlots) {
    await expect(page.getByRole("region", { name: "Immutable draft versions" })).toContainText(
      reviewed.candidate.id,
    );
  }

  const papers = await getJson<PaperSummary[]>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/papers`,
  );
  const persistedPaper = papers.find((paper) => paper.title === paperTitle);
  if (!persistedPaper) throw new Error("Paper Studio did not persist the reviewer-assembled draft");
  const [paperDraft] = await getJson<PaperDraft[]>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/papers/${persistedPaper.id}/draft-versions`,
  );
  expect(paperDraft?.candidates).toEqual(
    reviewedSlots.map((item, index) =>
      expect.objectContaining({
        blueprint_slot_id: item.slot.slot_id,
        candidate_id: item.candidate.id,
        ordinal: index + 1,
      }),
    ),
  );

  const reviewerPublishPayload: PaperPublishRequest = {
    expected_version: persistedPaper.current_version,
  };
  const reviewerPublishDenied = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/papers/${persistedPaper.id}/publish`,
    { data: reviewerPublishPayload },
  );
  expect(reviewerPublishDenied.status()).toBe(403);
  await expect(page.getByRole("button", { name: "Publish current draft" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Archive paper terminally" })).toHaveCount(0);

  await page.getByRole("button", { name: "Sign out" }).click();
  await login(page, "admin");
  await page.goto("/admin/papers");
  await page.getByLabel("Active Grade 5 curriculum").selectOption(curriculum.id);
  await page.getByRole("button", { name: `Select paper ${persistedPaper.id}` }).click();
  await expect(page.getByRole("region", { name: "Selected paper lifecycle" })).toContainText(
    "Draft",
  );
  await page.getByRole("button", { name: "Publish current draft" }).click();
  await expect(
    page.getByText("Publication version 1 created as an immutable verified snapshot."),
  ).toBeVisible();

  const snapshotRegion = page.getByRole("region", {
    name: "Verified immutable publication snapshot",
  });
  await expect(snapshotRegion).toContainText("Student serving requires no live LLM or provider call");
  await expect(snapshotRegion).toContainText("Immutable, hash-verified snapshot");
  await expect(snapshotRegion).toContainText("deterministic-fake");
  await expect(snapshotRegion).toContainText(reviewedChunk.id);
  await expect(snapshotRegion.getByRole("heading", { name: "Validation evidence" })).toHaveCount(3);
  await expect(snapshotRegion.getByRole("heading", { name: "Reviewer revisions" })).toHaveCount(3);
  await expect(snapshotRegion.getByRole("heading", { name: "Review history" })).toHaveCount(3);
  await expect(snapshotRegion.getByRole("heading", { name: "Review decision" })).toHaveCount(3);
  await expect(snapshotRegion).toContainText("Validated revision");
  await expect(snapshotRegion).toContainText("Approved");
  for (const reviewed of reviewedSlots) {
    await expect(snapshotRegion).toContainText(reviewed.slot.slot_id);
    await expect(snapshotRegion).toContainText(reviewed.publishedStem);
  }

  const immutablePublication = await getJson<Publication>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/papers/${persistedPaper.id}/publication-versions/1`,
  );
  expect(immutablePublication.content_hash).toMatch(/^[a-f0-9]{64}$/);
  expect(immutablePublication.snapshot.title).toBe(paperTitle);
  expect(immutablePublication.snapshot.blueprint.slot_ids).toEqual(
    exactSlots.map((slot) => slot.slot_id),
  );
  expect(immutablePublication.snapshot.questions).toHaveLength(3);
  expect(immutablePublication.snapshot.questions.map((question) => question.slot_id)).toEqual(
    exactSlots.map((slot) => slot.slot_id),
  );
  expect(
    immutablePublication.snapshot.questions.map((question) => question.content.question_type),
  ).toEqual(exactSlots.map((slot) => slot.question_type));

  for (const [index, question] of immutablePublication.snapshot.questions.entries()) {
    const reviewed = reviewedSlots[index];
    if (!reviewed) throw new Error(`Published question ${index + 1} has no reviewed source`);
    expect(question).toMatchObject({
      candidate_id: reviewed.candidate.id,
      content: {
        marks: 2,
        question_type: reviewed.slot.question_type,
        stem: reviewed.publishedStem,
      },
      content_revision: reviewed.candidate.current_revision,
      decision: { state: "approved" },
      lineage: {
        blueprint_slot_id: reviewed.slot.slot_id,
        generation_id: reviewed.run.id,
        provider: "deterministic-fake",
      },
      slot_id: reviewed.slot.slot_id,
      validation: {
        passed: true,
        validated_revision: 1,
      },
    });
    expect(question.lineage.provenance).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          chunk_id: reviewedChunk.id,
          source_document_id: source.id,
        }),
      ]),
    );
    expect(question.validation.finding_refs.length).toBeGreaterThan(0);
    expect(question.review_history.map((item) => item.action)).toEqual(
      reviewed.candidate.current_revision === 2
        ? ["started", "edited", "approved"]
        : ["started", "approved"],
    );
    expect(question.revisions).toHaveLength(reviewed.candidate.current_revision);
  }

  const generationStateAfterPublication = await getJson<GenerationRunSummary[]>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/generation-runs`,
  );
  expect(generationStateAfterPublication).toEqual(generationStateBeforePaper);
  const finalPaper = await getJson<PaperSummary>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/papers/${persistedPaper.id}`,
  );
  expect(finalPaper.state).toBe("published");
  expect(browserErrors).toEqual([]);
});
