import type { components } from "@exam-guru/api-client";
import {
  expect,
  test,
  type APIRequestContext,
  type Locator,
  type Page,
} from "@playwright/test";

import { openAdvancedArea } from "./helpers/advanced-navigation";

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
type AnalyticsRun = components["schemas"]["AnalyticsRunResponse"];
type AnalyticsRunSummary = components["schemas"]["AnalyticsRunSummaryResponse"];
type EmbeddingJob = components["schemas"]["EmbeddingJobResponse"];
type RetrievalResult = components["schemas"]["RetrievalExploreResponse"];
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
  await expect(page).toHaveURL(/\/admin\/home$/);
}

async function selectOptionIfNeeded(select: Locator, value: string) {
  await expect(select).toBeVisible();
  if ((await select.inputValue()) !== value) await select.selectOption(value);
  await expect(select).toHaveValue(value);
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

type ReviewedHistoricalEvidence = {
  block: ExtractedBlock;
  page: SourcePage;
  question: HistoricalQuestion;
  source: SourceDocument;
};

async function createReviewedHistoricalQuestion({
  competency,
  curriculum,
  marker,
  paperCode,
  request,
  skill,
  year,
}: {
  competency: TaxonomyNode;
  curriculum: Curriculum;
  marker: string;
  paperCode: string;
  request: APIRequestContext;
  skill: TaxonomyNode;
  year: number;
}): Promise<ReviewedHistoricalEvidence> {
  const questionText = `Historical choice ${year} ${marker}: A three; B four; answer B.`;
  const upload = await request.post("/api/v1/admin/source-documents", {
    multipart: {
      curriculum_version_id: curriculum.id,
      document_type: "past_paper",
      file: {
        buffer: syntheticPdf(questionText),
        mimeType: "application/pdf",
        name: `historical-${year}-${marker}.pdf`,
      },
      paper_code: paperCode,
      year: String(year),
    },
  });
  expect(upload.status()).toBe(201);
  const uploadedSource = (await upload.json()) as SourceDocument;

  expect(
    (await request.post(`/api/v1/admin/source-documents/${uploadedSource.id}/extract`)).status(),
  ).toBe(202);
  await expect
    .poll(
      async () => {
        const documents = await getJson<SourceDocument[]>(
          request,
          "/api/v1/admin/source-documents",
        );
        return documents.find((document) => document.id === uploadedSource.id)?.extraction_status;
      },
      { timeout: 30_000 },
    )
    .toBe("extracted");

  const review = await request.post(`/api/v1/admin/source-documents/${uploadedSource.id}/review`);
  expect(review.ok()).toBe(true);
  const trust = await request.post(`/api/v1/admin/source-documents/${uploadedSource.id}/trust`);
  expect(trust.ok()).toBe(true);
  const source = (await trust.json()) as SourceDocument;
  expect(source).toMatchObject({
    curriculum_version_id: curriculum.id,
    document_type: "past_paper",
    extraction_status: "trusted",
    lesson_id: null,
    paper_code: paperCode,
    subject_id: curriculum.subject_id,
    unit_id: null,
    year,
  });

  const [sourcePage] = await getJson<SourcePage[]>(
    request,
    `/api/v1/admin/source-documents/${source.id}/pages`,
  );
  if (!sourcePage) throw new Error(`Historical ${year} source page was not extracted`);
  const [sourceBlock] = await getJson<ExtractedBlock[]>(
    request,
    `/api/v1/admin/source-documents/${source.id}/pages/${sourcePage.page_number}/blocks`,
  );
  if (!sourceBlock) throw new Error(`Historical ${year} source block was not extracted`);
  expect(sourceBlock).toMatchObject({
    page_number: sourcePage.page_number,
    source_document_id: source.id,
    source_page_id: sourcePage.id,
  });
  expect(sourceBlock.raw_text).toContain(questionText);

  const question = await postCreated<HistoricalQuestion>(
    request,
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/questions`,
    {
      answer: "B",
      difficulty_confidence: 0.9,
      difficulty_label: "medium",
      difficulty_source: "reviewer_confirmed",
      marking_guidance: "Award two marks for option B.",
      marks: 2,
      options: ["A. three", "B. four"],
      page_number: sourcePage.page_number,
      paper_code: paperCode,
      question_archetype: "single_best_answer",
      question_number: `Q-${year}`,
      question_type: "multiple_choice",
      source_block_id: sourceBlock.id,
      source_document_id: source.id,
      text: sourceBlock.reviewed_text ?? sourceBlock.raw_text,
      year,
    } satisfies components["schemas"]["HistoricalQuestionImportRequest"],
  );
  const classifiedResponse = await request.patch(
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/questions/${question.id}/classification`,
    {
      data: {
        competency_id: competency.id,
        expected_version: question.version,
        learning_concept_id: null,
        skill_id: skill.id,
        sub_skill_id: null,
      } satisfies components["schemas"]["KnowledgeClassificationRequest"],
    },
  );
  expect(classifiedResponse.ok()).toBe(true);
  const classified = (await classifiedResponse.json()) as HistoricalQuestion;
  const inReviewResponse = await request.post(
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/questions/${question.id}/review`,
    {
      data: {
        expected_version: classified.version,
        target: "in_review",
      } satisfies components["schemas"]["KnowledgeReviewTransitionRequest"],
    },
  );
  expect(inReviewResponse.ok()).toBe(true);
  const inReview = (await inReviewResponse.json()) as HistoricalQuestion;
  const reviewedResponse = await request.post(
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/questions/${question.id}/review`,
    {
      data: {
        expected_version: inReview.version,
        target: "reviewed",
      } satisfies components["schemas"]["KnowledgeReviewTransitionRequest"],
    },
  );
  expect(reviewedResponse.ok()).toBe(true);
  const reviewed = (await reviewedResponse.json()) as HistoricalQuestion;
  expect(reviewed).toMatchObject({
    classification: {
      competency_id: competency.id,
      skill_id: skill.id,
    },
    lesson_id: null,
    paper_code: paperCode,
    provenance: {
      page_number: sourcePage.page_number,
      source_block_id: sourceBlock.id,
      source_document_id: source.id,
    },
    question_type: "multiple_choice",
    review_state: "reviewed",
    unit_id: null,
    year,
  });
  const fetched = await getJson<HistoricalQuestion>(
    request,
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/questions/${reviewed.id}`,
  );
  expect(fetched).toEqual(reviewed);
  return { block: sourceBlock, page: sourcePage, question: reviewed, source };
}

function blueprintRequest(
  curriculum: Curriculum,
  medium: Medium,
  competency: TaxonomyNode,
  skill: TaxonomyNode,
  unique: string,
  analyticsRunId: string,
): BlueprintRequest {
  const target = {
    competency_id: competency.id,
    learning_concept_id: null,
    skill_id: skill.id,
    sub_skill_id: null,
  };
  return {
    analytics_run_id: analyticsRunId,
    seed: 2026,
    specification: {
      config_version: "generation-complete-paper-e2e-v1",
      curriculum_scope: {
        curriculum_version_id: curriculum.id,
        grade: 5,
        lesson_ids: [],
        medium: medium.code,
        subject_id: curriculum.subject_id,
        unit_ids: [],
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
  expect(run.prompt_version).toBe("2.0.0");
  expect(run.retrieval_version).toBe("active-reviewed-multigrade-scope-v2");
  expect(run.schema_version).toBe("question.v1");
  expect(run.cost_microusd).toBe(0);
  expect(run.paper_blueprint_id).toBe(blueprint.id);
  expect(run.blueprint_id).toBe(blueprint.blueprint_id);
  expect(run.blueprint_slot).toMatchObject({
    evidence: {
      evidence_refs: expect.arrayContaining([
        `analytics:persisted-run:${blueprint.analytics_run_id}`,
      ]),
    },
    slot_id: slot.slot_id,
  });
  expect(run.context).toEqual([
    expect.objectContaining({
      learning_scope: { lesson_id: null, unit_id: null },
      record_id: context.id,
      record_kind: "knowledge_chunk",
      text: context.text,
      trust: "untrusted_data",
      provenance: expect.objectContaining({
        chunk_id: context.id,
        page_number: context.provenance.page_number,
        source_block_id: context.provenance.source_block_id,
        source_document_id: context.provenance.source_document_id,
      }),
    }),
  ]);
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
  await expect(reportMetadata).toContainText("Deterministic result: Warn");
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

  expect(validation.overall_status).toBe("warn");
  expect(validation.duplicate_reference_count).toBe(expectedDuplicateReferenceCount);
  expect(duplicateFindings).toHaveLength(3);
  expect(duplicateFindings.map((finding) => finding.status)).toEqual(["pass", "pass", "pass"]);
  expect(lexicalScore).toBeLessThan(8_000);
  expect(findings).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ code: "subject.unregistered", status: "warn" }),
    ]),
  );
  expect(validation.limitations.join(" ").toLowerCase()).toContain("human review");
  return { ...generated, validation };
}

async function reviewSlotThroughUi(
  page: Page,
  curriculum: Curriculum,
  context: KnowledgeChunk,
  validated: ValidatedSlot,
  editStem: string | null,
): Promise<ReviewedSlot> {
  await page.getByLabel("Eligible validation run").selectOption(validated.validation.id);
  await page.getByRole("button", { name: "Create review candidate" }).click();
  await expect(
    page.getByText("Review candidate created from persisted non-failing validation evidence."),
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

test("integrated deterministic P10 mechanics preserve one corrected lineage through publication", async ({
  page,
}) => {
  test.setTimeout(240_000);
  test.info().annotations.push({
    type: "limitation",
    description:
      "This proves integrated deterministic mechanics only, not OCR, retrieval, forecast, Sinhala, semantic, or paid-model quality; it is not a P10 DONE claim.",
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
  const boundary = `Corrected even number knowledge ${unique}`;
  const forbiddenBoundary = `Forbidden unreviewed knowledge ${unique}`;
  const forbiddenMarker = `uncorrected-odd-${unique}`;
  const retrievalMarker = `human-even-correction-${unique}`;
  const correctedText = `Human correction ${retrievalMarker}: Four is an even number.`;
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
  const subject = await postCreated<Subject>(page.request, "/api/v1/admin/subjects", {
    code: `M${code}`,
    name: `Generation mathematics ${unique}`,
  } satisfies components["schemas"]["SubjectCreate"]);
  const curriculum = await postCreated<Curriculum>(
    page.request,
    "/api/v1/admin/curriculum-versions",
    {
      code: `GC-${code}`,
      exam_configuration_id: exam.id,
      medium_id: medium.id,
      subject_id: subject.id,
      title: curriculumTitle,
    } satisfies components["schemas"]["CurriculumVersionCreate"],
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
        buffer: syntheticPdf(`Four is incorrectly odd ${forbiddenMarker}`),
        mimeType: "application/pdf",
        name: `generation-source-${unique}.pdf`,
      },
    },
  });
  expect(upload.status()).toBe(201);
  const uploadedSource = (await upload.json()) as SourceDocument;
  expect(
    (await page.request.post(`/api/v1/admin/source-documents/${uploadedSource.id}/extract`)).status(),
  ).toBe(202);
  await expect
    .poll(
      async () => {
        const documents = await getJson<SourceDocument[]>(
          page.request,
          "/api/v1/admin/source-documents",
        );
        return documents.find((document) => document.id === uploadedSource.id)?.extraction_status;
      },
      { timeout: 30_000 },
    )
    .toBe("extracted");

  const extractedSources = await getJson<SourceDocument[]>(
    page.request,
    "/api/v1/admin/source-documents",
  );
  const extractedSource = extractedSources.find((document) => document.id === uploadedSource.id);
  if (!extractedSource) throw new Error("Generation source was not retained after extraction");
  expect(extractedSource).toMatchObject({
    extraction_config: { mode: "native" },
    extraction_status: "extracted",
    extractor: "pymupdf",
    native_text_page_ratio: 1,
    needs_ocr: false,
    ocr_page_count: 0,
  });

  const [sourcePage] = await getJson<SourcePage[]>(
    page.request,
    `/api/v1/admin/source-documents/${uploadedSource.id}/pages`,
  );
  if (!sourcePage) throw new Error("Generation source page was not extracted");
  const [sourceBlock] = await getJson<ExtractedBlock[]>(
    page.request,
    `/api/v1/admin/source-documents/${uploadedSource.id}/pages/${sourcePage.page_number}/blocks`,
  );
  if (!sourceBlock) throw new Error("Generation source block was not extracted");
  expect(sourcePage.raw_text).toContain(forbiddenMarker);
  expect(sourcePage.reviewed_text).toBeNull();
  expect(sourceBlock.raw_text).toContain(forbiddenMarker);
  expect(sourceBlock.reviewed_text).toBeNull();

  const reviewResponse = await page.request.post(
    `/api/v1/admin/source-documents/${uploadedSource.id}/review`,
  );
  expect(reviewResponse.ok()).toBe(true);
  const correctionResponse = await page.request.patch(
    `/api/v1/admin/source-documents/${uploadedSource.id}/pages/${sourcePage.page_number}`,
    {
      data: {
        expected_version: sourcePage.version,
        reviewed_text: correctedText,
      } satisfies components["schemas"]["ReviewedTextUpdate"],
    },
  );
  expect(correctionResponse.ok()).toBe(true);
  const correctedPage = (await correctionResponse.json()) as SourcePage;
  expect(correctedPage.raw_text).toBe(sourcePage.raw_text);
  expect(correctedPage.reviewed_text).toBe(correctedText);
  expect(correctedPage.version).toBe(sourcePage.version + 1);

  const [fetchedCorrectedPage] = await getJson<SourcePage[]>(
    page.request,
    `/api/v1/admin/source-documents/${uploadedSource.id}/pages`,
  );
  if (!fetchedCorrectedPage) throw new Error("Corrected generation page could not be fetched");
  expect(fetchedCorrectedPage).toMatchObject({
    id: sourcePage.id,
    raw_text: sourcePage.raw_text,
    reviewed_text: correctedText,
    version: sourcePage.version + 1,
  });
  const [fetchedSourceBlock] = await getJson<ExtractedBlock[]>(
    page.request,
    `/api/v1/admin/source-documents/${uploadedSource.id}/pages/${sourcePage.page_number}/blocks`,
  );
  if (!fetchedSourceBlock) throw new Error("Corrected generation block provenance was lost");
  expect(fetchedSourceBlock).toMatchObject({
    id: sourceBlock.id,
    page_number: fetchedCorrectedPage.page_number,
    raw_text: sourceBlock.raw_text,
    reviewed_text: null,
    source_document_id: uploadedSource.id,
    source_page_id: fetchedCorrectedPage.id,
  });

  const trustResponse = await page.request.post(
    `/api/v1/admin/source-documents/${uploadedSource.id}/trust`,
  );
  expect(trustResponse.ok()).toBe(true);
  const source = (await trustResponse.json()) as SourceDocument;
  expect(source).toMatchObject({
    curriculum_version_id: curriculum.id,
    extraction_status: "trusted",
    lesson_id: null,
    subject_id: subject.id,
    unit_id: null,
  });

  const forbiddenDraft = await postCreated<KnowledgeChunk>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/chunks`,
    {
      chunk_type: "explanation",
      educational_boundary: forbiddenBoundary,
      page_number: fetchedCorrectedPage.page_number,
      sequence: 99,
      source_block_id: fetchedSourceBlock.id,
      source_document_id: source.id,
      text: fetchedSourceBlock.raw_text,
    } satisfies components["schemas"]["KnowledgeChunkImportRequest"],
  );
  const forbiddenClassificationResponse = await page.request.patch(
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/chunks/${forbiddenDraft.id}/classification`,
    {
      data: {
        competency_id: competency.id,
        expected_version: forbiddenDraft.version,
        learning_concept_id: null,
        skill_id: skill.id,
        sub_skill_id: null,
      } satisfies components["schemas"]["KnowledgeClassificationRequest"],
    },
  );
  expect(forbiddenClassificationResponse.ok()).toBe(true);
  const classifiedForbiddenDraft =
    (await forbiddenClassificationResponse.json()) as KnowledgeChunk;
  expect(classifiedForbiddenDraft.review_state).toBe("draft");

  if (fetchedCorrectedPage.reviewed_text === null) {
    throw new Error("The human correction was not retained for knowledge import");
  }
  const importedChunk = await postCreated<KnowledgeChunk>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/chunks`,
    {
      chunk_type: "explanation",
      educational_boundary: boundary,
      page_number: fetchedCorrectedPage.page_number,
      sequence: 1,
      source_block_id: fetchedSourceBlock.id,
      source_document_id: source.id,
      text: fetchedCorrectedPage.reviewed_text,
    } satisfies components["schemas"]["KnowledgeChunkImportRequest"],
  );
  expect(importedChunk).toMatchObject({
    provenance: {
      page_number: fetchedCorrectedPage.page_number,
      source_block_id: fetchedSourceBlock.id,
      source_document_id: source.id,
    },
    text: correctedText,
  });
  const classificationResponse = await page.request.patch(
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/chunks/${importedChunk.id}/classification`,
    {
      data: {
        competency_id: competency.id,
        expected_version: importedChunk.version,
        learning_concept_id: null,
        skill_id: skill.id,
        sub_skill_id: null,
      } satisfies components["schemas"]["KnowledgeClassificationRequest"],
    },
  );
  expect(classificationResponse.ok()).toBe(true);
  const classifiedChunk = (await classificationResponse.json()) as KnowledgeChunk;
  const inReviewResponse = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/chunks/${classifiedChunk.id}/review`,
    {
      data: {
        expected_version: classifiedChunk.version,
        target: "in_review",
      } satisfies components["schemas"]["KnowledgeReviewTransitionRequest"],
    },
  );
  expect(inReviewResponse.ok()).toBe(true);
  const inReviewChunk = (await inReviewResponse.json()) as KnowledgeChunk;
  const reviewedResponse = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/chunks/${inReviewChunk.id}/review`,
    {
      data: {
        expected_version: inReviewChunk.version,
        target: "reviewed",
      } satisfies components["schemas"]["KnowledgeReviewTransitionRequest"],
    },
  );
  expect(reviewedResponse.ok()).toBe(true);
  const reviewedChunk = (await reviewedResponse.json()) as KnowledgeChunk;
  expect(reviewedChunk).toMatchObject({
    classification: {
      competency_id: competency.id,
      skill_id: skill.id,
    },
    lesson_id: null,
    provenance: {
      page_number: fetchedCorrectedPage.page_number,
      source_block_id: fetchedSourceBlock.id,
      source_document_id: source.id,
    },
    review_state: "reviewed",
    unit_id: null,
    text: correctedText,
  });

  const historical2019 = await createReviewedHistoricalQuestion({
    competency,
    curriculum,
    marker: unique,
    paperCode: `P19-${code}`,
    request: page.request,
    skill,
    year: 2019,
  });
  const historical2020 = await createReviewedHistoricalQuestion({
    competency,
    curriculum,
    marker: unique,
    paperCode: `P20-${code}`,
    request: page.request,
    skill,
    year: 2020,
  });
  const historicalEvidence = [historical2019, historical2020];
  expect(new Set(historicalEvidence.map((item) => item.question.year)).size).toBe(2);
  expect(new Set(historicalEvidence.map((item) => item.source.id)).size).toBe(2);
  expect(
    historicalEvidence.every(
      (item) =>
        item.question.paper_code === item.source.paper_code &&
        item.question.year === item.source.year &&
        item.question.provenance.source_block_id === item.block.id &&
        item.question.provenance.page_number === item.page.page_number,
    ),
  ).toBe(true);

  await page.goto("/admin/analytics");
  await expect(page.getByRole("heading", { name: "Analytics Report Studio" })).toBeVisible();
  await selectOptionIfNeeded(page.getByLabel("Active analytics curriculum"), curriculum.id);
  await expect(page.getByRole("heading", { name: "No analytics runs yet" })).toBeVisible();
  await page.getByLabel("Minimum training years").fill("1");
  await page.getByLabel("Top skills to evaluate").fill("1");
  await page.getByLabel("Meaningful improvement numerator").fill("1");
  await page.getByLabel("Meaningful improvement denominator").fill("100");
  const analyticsResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith(`/curricula/${curriculum.id}/analytics/runs`),
  );
  await page.getByRole("button", { name: "Run analysis" }).click();
  const analyticsResponse = await analyticsResponsePromise;
  expect(analyticsResponse.status()).toBe(201);
  const createdAnalytics = (await analyticsResponse.json()) as AnalyticsRun;

  await expect(page.getByText("Analysis run created.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Analysis report" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Rolling held-out windows" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Holdout 2020" })).toBeVisible();
  await expect(page.getByText("Training years: 2019")).toBeVisible();
  await expect(page.getByText("Leakage audit passed")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Baseline comparison" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Syllabus-balanced practice fallback" }),
  ).toBeVisible();
  await expect(
    page.getByText(/safer syllabus-balanced practice method is selected/i),
  ).toBeVisible();
  await expect(page.getByText(/does not predict future exam questions/i)).toBeVisible();

  const analyticsSummaries = await getJson<AnalyticsRunSummary[]>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/analytics/runs`,
  );
  expect(analyticsSummaries).toHaveLength(1);
  const [analyticsSummary] = analyticsSummaries;
  if (!analyticsSummary) throw new Error("The persisted analytics run ID was not returned");
  const analyticsRunId = analyticsSummary.id;
  expect(analyticsRunId).toBe(createdAnalytics.id);
  await expect(page.getByText(analyticsRunId, { exact: true })).toBeVisible();
  const analyticsRun = await getJson<AnalyticsRun>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/analytics/runs/${analyticsRunId}`,
  );
  expect(analyticsRun.id).toBe(analyticsRunId);
  expect(analyticsRun.input.observation_ids.toSorted()).toEqual(
    historicalEvidence.map((item) => item.question.id).toSorted(),
  );
  expect(analyticsRun.result.backtest.recommendation.mode).toBe(
    "syllabus_balanced_practice",
  );
  expect(analyticsRun.result.backtest.windows).toHaveLength(1);
  expect(analyticsRun.result.backtest.windows[0]).toMatchObject({
    heldout_year: 2020,
    leakage_audit: {
      heldout_observation_ids: [historical2020.question.id],
      overlapping_observation_ids: [],
      passed: true,
      training_observation_ids: [historical2019.question.id],
    },
    training_years: [2019],
  });

  await page.goto("/admin/knowledge");
  await expect(page.getByRole("heading", { name: "Knowledge Studio" })).toBeVisible();
  await selectOptionIfNeeded(page.getByLabel("Active curriculum"), curriculum.id);
  const chunkEmbeddingSelection = page.getByRole("checkbox", {
    name: `Select knowledge chunk ${boundary.toLowerCase()} / sequence 1`,
  });
  await expect(chunkEmbeddingSelection).toBeVisible();
  await chunkEmbeddingSelection.check();
  await expect(page.getByText("1 of 100 records selected")).toBeVisible();
  const embeddingResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith(`/curricula/${curriculum.id}/embedding-jobs`),
  );
  await page.getByRole("button", { name: "Queue selected records" }).click();
  const embeddingResponse = await embeddingResponsePromise;
  expect(embeddingResponse.status()).toBe(202);
  expect(embeddingResponse.request().postDataJSON()).toEqual({
    historical_question_ids: [],
    knowledge_chunk_ids: [reviewedChunk.id],
  });
  const createdEmbeddingJob = (await embeddingResponse.json()) as EmbeddingJob;
  expect(createdEmbeddingJob).toMatchObject({
    curriculum_version_id: curriculum.id,
    historical_question_ids: [],
    knowledge_chunk_ids: [reviewedChunk.id],
  });
  expect(createdEmbeddingJob.configuration).toMatchObject({
    dimension: 32,
    model: "grade5-deterministic-shake256",
    provider: "deterministic",
    version: "v1",
  });

  let completedEmbeddingJob = createdEmbeddingJob;
  await expect
    .poll(
      async () => {
        completedEmbeddingJob = await getJson<EmbeddingJob>(
          page.request,
          `/api/v1/admin/curricula/${curriculum.id}/embedding-jobs/${createdEmbeddingJob.id}`,
        );
        return completedEmbeddingJob.status;
      },
      { intervals: [250, 500, 1_000, 2_000], timeout: 120_000 },
    )
    .toBe("succeeded");
  expect(completedEmbeddingJob).toMatchObject({
    claimed_at: expect.any(String),
    completed_at: expect.any(String),
    counts: { deduplicated: 0, embedded: 1, requested: 1 },
    failure_code: null,
    status: "succeeded",
  });
  await expect(page.getByText("Embedding job succeeded.")).toBeVisible({ timeout: 120_000 });
  await page.getByRole("button", { name: "Refresh embedding data" }).click();
  const embeddedChunkRow = page.getByRole("listitem", {
    name: `Knowledge chunk ${boundary} / Sequence 1`,
  });
  const embeddingConfigurationLabel = `${completedEmbeddingJob.configuration.provider} / ${completedEmbeddingJob.configuration.model} / ${completedEmbeddingJob.configuration.version} / ${completedEmbeddingJob.configuration.dimension}d`;
  await expect(embeddedChunkRow.getByText("Embedded", { exact: true })).toBeVisible();
  await expect(
    embeddedChunkRow.getByText(embeddingConfigurationLabel, { exact: true }),
  ).toBeVisible();
  await expect(
    embeddedChunkRow.getByText(completedEmbeddingJob.configuration.config_fingerprint, {
      exact: true,
    }),
  ).toBeVisible();
  const refreshedChunk = await getJson<KnowledgeChunk>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/chunks/${reviewedChunk.id}`,
  );
  expect(refreshedChunk.embedding_status).toBe("embedded");
  expect(refreshedChunk.embedding_configurations).toEqual([
    expect.objectContaining(completedEmbeddingJob.configuration),
  ]);
  const refreshedForbiddenDraft = await getJson<KnowledgeChunk>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/knowledge/chunks/${classifiedForbiddenDraft.id}`,
  );
  expect(refreshedForbiddenDraft.embedding_status).toBe("not_embedded");

  await openAdvancedArea(page, "Knowledge / RAG");
  await expect(page.getByRole("heading", { name: "RAG Explorer" })).toBeVisible();
  await selectOptionIfNeeded(page.getByLabel("Active retrieval curriculum"), curriculum.id);
  const competencySelect = page.locator(`select:has(option[value="${competency.id}"])`);
  const skillSelect = page.locator(`select:has(option[value="${skill.id}"])`);
  const embeddingSelect = page.locator(
    `select:has(option[value="${completedEmbeddingJob.configuration.config_fingerprint}"])`,
  );
  await expect(embeddingSelect).toBeVisible();
  await competencySelect.selectOption(competency.id);
  await skillSelect.selectOption(skill.id);
  await embeddingSelect.selectOption(completedEmbeddingJob.configuration.config_fingerprint);
  await page.getByLabel("Retrieval query").fill(retrievalMarker);
  const retrievalResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" && response.url().endsWith("/admin/retrieval/explore"),
  );
  await page.getByRole("button", { name: "Run retrieval" }).click();
  const retrievalResponse = await retrievalResponsePromise;
  expect(retrievalResponse.status()).toBe(200);
  const retrievalRequest = retrievalResponse.request().postDataJSON() as components["schemas"]["RetrievalExploreRequest"];
  expect(retrievalRequest.embedding_config).toEqual(completedEmbeddingJob.configuration);
  expect(retrievalRequest.query).toBe(retrievalMarker);
  expect(retrievalRequest.scope).toEqual({
    curriculum_version_id: curriculum.id,
    exam_id: exam.id,
    grade: 5,
    lesson_ids: [],
    medium_id: medium.id,
    subject_id: subject.id,
    taxonomy: {
      competency_id: competency.id,
      learning_concept_id: null,
      skill_id: skill.id,
      sub_skill_id: null,
    },
    unit_ids: [],
  });
  const retrieval = (await retrievalResponse.json()) as RetrievalResult;
  expect(retrieval.channels.lexical.length).toBeGreaterThan(0);
  expect(retrieval.channels.vector.length).toBeGreaterThan(0);
  expect(retrieval.fused_candidates.length).toBeGreaterThan(0);
  expect(retrieval.context.items.length).toBeGreaterThan(0);
  expect(retrieval.context.character_count).toBeGreaterThan(0);
  expect(retrieval.context.trust).toBe("untrusted_source_data");
  expect(retrieval.diagnostics.hard_scope_filter_applied).toBe(true);
  expect(retrieval.embedding_config).toEqual(completedEmbeddingJob.configuration);
  expect(retrieval.scope).toEqual(retrievalRequest.scope);
  for (const candidate of [...retrieval.channels.lexical, ...retrieval.channels.vector]) {
    expect(candidate).toMatchObject({
      chunk_id: reviewedChunk.id,
      provenance: {
        page_number: fetchedCorrectedPage.page_number,
        source_block_id: fetchedSourceBlock.id,
        source_document_id: source.id,
      },
      scope: retrievalRequest.scope,
      text: correctedText,
      trust: "untrusted_source_data",
    });
  }
  for (const candidate of retrieval.fused_candidates) {
    expect(candidate.source_chunk_ids).toEqual([reviewedChunk.id]);
    expect(candidate.provenances).toEqual([
      {
        page_number: fetchedCorrectedPage.page_number,
        source_block_id: fetchedSourceBlock.id,
        source_document_id: source.id,
      },
    ]);
    expect(candidate.scope).toMatchObject(retrievalRequest.scope);
    expect(candidate.text).toBe(correctedText);
    expect(candidate.trust).toBe("untrusted_source_data");
  }
  for (const item of retrieval.context.items) {
    expect(item.source_chunk_ids).toEqual([reviewedChunk.id]);
    expect(item.provenances).toEqual([
      {
        page_number: fetchedCorrectedPage.page_number,
        source_block_id: fetchedSourceBlock.id,
        source_document_id: source.id,
      },
    ]);
    expect(item.scope).toMatchObject(retrievalRequest.scope);
    expect(item.text).toBe(correctedText);
    expect(item.trust).toBe("untrusted_source_data");
  }
  expect(JSON.stringify(retrieval)).not.toContain(classifiedForbiddenDraft.id);
  expect(JSON.stringify(retrieval)).not.toContain(classifiedForbiddenDraft.text);
  expect(JSON.stringify(retrieval)).not.toContain(forbiddenMarker);

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
  await expect(page.getByText("Untrusted source data").first()).toBeVisible();
  await expect(page.getByText(correctedText, { exact: true }).first()).toBeVisible();
  await expect(page.getByText(reviewedChunk.id, { exact: true }).first()).toBeVisible();
  await expect(page.getByText(source.id, { exact: true }).first()).toBeVisible();
  await expect(page.getByText(fetchedSourceBlock.id, { exact: true }).first()).toBeVisible();
  await expect(page.getByText(classifiedForbiddenDraft.id, { exact: true })).toHaveCount(0);
  await expect(page.getByText(classifiedForbiddenDraft.text, { exact: true })).toHaveCount(0);
  await expect(page.getByText(forbiddenMarker, { exact: true })).toHaveCount(0);

  const createBlueprintRequest = blueprintRequest(
    curriculum,
    medium,
    competency,
    skill,
    unique,
    analyticsRunId,
  );
  expect(createBlueprintRequest.analytics_run_id).toBe(analyticsRunId);
  expect(Object.keys(createBlueprintRequest.specification.taxonomy_requirements[0]?.priority ?? {})).toEqual(
    ["baseline_evidence_refs", "baseline_score", "baseline_version"],
  );
  expect(JSON.stringify(createBlueprintRequest)).not.toContain("forecast_");
  const blueprint = await postCreated<Blueprint>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/blueprints`,
    createBlueprintRequest,
  );
  assertRepresentativeBlueprint(blueprint, competency, skill);
  expect(blueprint.specification.curriculum_scope).toEqual({
    curriculum_version_id: curriculum.id,
    grade: 5,
    lesson_ids: [],
    medium: medium.code,
    subject_id: subject.id,
    unit_ids: [],
  });
  const analyticsEvidenceRef = `analytics:persisted-run:${analyticsRunId}`;
  expect(blueprint.analytics_run_id).toBe(analyticsRunId);
  expect(
    blueprint.specification.taxonomy_requirements[0]?.priority.forecast_evidence_refs,
  ).toContain(analyticsEvidenceRef);
  expect(blueprint.specification.taxonomy_requirements[0]?.priority.forecast_score).not.toBeNull();
  for (const slot of blueprint.blueprint.slots) {
    expect(slot.evidence.evidence_refs).toContain(analyticsEvidenceRef);
    expect(slot.rationale.priority_mode).toBe("baseline_fallback");
  }
  const persistedBlueprint = await getJson<Blueprint>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/blueprints/${blueprint.id}`,
  );
  expect(persistedBlueprint).toEqual(blueprint);
  const exactSlots = blueprint.blueprint.slots;

  await page.goto("/admin/generation");
  await expect(page.getByRole("heading", { name: "Generation Studio" })).toBeVisible();
  await selectOptionIfNeeded(page.getByLabel("Active Grade 5 curriculum"), curriculum.id);
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
  await selectOptionIfNeeded(page.getByLabel("Active Grade 5 curriculum"), curriculum.id);
  const validatedSlots: ValidatedSlot[] = [];
  for (const [index, generated] of generatedSlots.entries()) {
    validatedSlots.push(
      await validateSlotThroughUi(
        page,
        curriculum,
        source,
        generated,
        historicalEvidence.length + index,
      ),
    );
  }
  await expect(
    page.getByRole("heading", { name: "Deterministic validation is limited" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await login(page, "reviewer");
  await page.goto("/admin/review");
  await expect(page.getByRole("heading", { name: "Reviewer Studio" })).toBeVisible();
  await selectOptionIfNeeded(page.getByLabel("Active Grade 5 curriculum"), curriculum.id);
  const reviewedSlots: ReviewedSlot[] = [];
  for (const validated of validatedSlots) {
    reviewedSlots.push(
      await reviewSlotThroughUi(page, curriculum, reviewedChunk, validated, null),
    );
  }
  expect(reviewedSlots.map((item) => item.candidate.state)).toEqual([
    "approved",
    "approved",
    "approved",
  ]);
  expect(reviewedSlots.every((item) => item.candidate.current_revision === 1)).toBe(true);

  const approvedCandidate = reviewedSlots[0]?.candidate;
  if (!approvedCandidate) throw new Error("The representative paper did not retain an approval");
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
  await selectOptionIfNeeded(page.getByLabel("Active Grade 5 curriculum"), curriculum.id);
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
  await selectOptionIfNeeded(page.getByLabel("Active Grade 5 curriculum"), curriculum.id);
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
          page_number: fetchedCorrectedPage.page_number,
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
  expect(browserErrors.filter((error) => !error.includes("eval() is not supported in this environment"))).toEqual([]);
});
