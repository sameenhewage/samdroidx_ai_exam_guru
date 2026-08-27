import type { components } from "@exam-guru/api-client";
import { expect, type Page, type Route } from "@playwright/test";

export type AdminRole = "admin" | "reviewer";

type CurriculumUnit = components["schemas"]["CurriculumUnitResponse"];
type CurriculumLesson = components["schemas"]["CurriculumLessonResponse"];
type ExtractedBlock = components["schemas"]["ExtractedBlockResponse"];
type Material = components["schemas"]["MaterialListItemResponse"];
type MaterialRemoveRequest = components["schemas"]["MaterialRemoveRequest"];
type MaterialRestoreRequest = components["schemas"]["MaterialRestoreRequest"];
type MaterialScopeRequest = components["schemas"]["MaterialScopeCorrectionRequest"];
type ReviewedTextUpdate = components["schemas"]["ReviewedTextUpdate"];
type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type SourcePage = components["schemas"]["SourcePageResponse"];
type TeacherPaperOptions = components["schemas"]["TeacherPaperOptionsResponse"];
type CurriculumLabels = components["schemas"]["CurriculumLabelsResponse"];
type LessonLabels = components["schemas"]["LessonLabelsResponse"];
type TeacherPaperJob = components["schemas"]["TeacherPaperJobResponse"];
type ReviewPaper = components["schemas"]["ReviewPaperDetailResponse"];
type ReviewQuestion = components["schemas"]["ReviewQuestionResponse"];
type PublishedPaper = components["schemas"]["PaperSummaryResponse"];

type RecordedRequest = {
  body: unknown;
  headers: Record<string, string>;
  method: string;
  path: string;
  search: string;
};

export type TeacherStudioFixture = {
  corrections: string[];
  curriculumIds: {
    gradeEleven: string;
  };
  generationIntents: unknown[];
  materialIds: {
    duplicate: string;
    ocr: string;
    wrongGrade: string;
  };
  materials: Material[];
  requests: RecordedRequest[];
  reviewQuestionId: string;
  sourceDocuments: SourceDocument[];
};

const educationIds = {
  gradeFiveCurriculum: "00000000-0000-0000-0000-000000000901",
  gradeFiveExam: "00000000-0000-0000-0000-000000000902",
  gradeSevenCurriculum: "00000000-0000-0000-0000-000000000903",
  gradeSevenExam: "00000000-0000-0000-0000-000000000904",
  gradeSevenLessonOne: "00000000-0000-0000-0000-000000000911",
  gradeSevenLessonTwo: "00000000-0000-0000-0000-000000000912",
  gradeSevenLessonThree: "00000000-0000-0000-0000-000000000913",
  gradeSevenLessonFour: "00000000-0000-0000-0000-000000000914",
  gradeSevenUnit: "00000000-0000-0000-0000-000000000910",
  gradeElevenCurriculum: "00000000-0000-0000-0000-000000000905",
  gradeElevenExam: "00000000-0000-0000-0000-000000000906",
  mathsSubject: "00000000-0000-0000-0000-000000000907",
  medium: "00000000-0000-0000-0000-000000000908",
} as const;

const gradeSevenUnits = [
  {
    active: true,
    code: "NUMBERS",
    created_at: "2026-08-23T00:00:00Z",
    curriculum_version_id: educationIds.gradeSevenCurriculum,
    id: educationIds.gradeSevenUnit,
    ordinal: 1,
    title: "Numbers",
    updated_at: "2026-08-23T00:00:00Z",
  },
] satisfies CurriculumUnit[];

const gradeSevenLessons = [
  [educationIds.gradeSevenLessonOne, "L1", "Whole numbers"],
  [educationIds.gradeSevenLessonTwo, "L2", "Factors and multiples"],
  [educationIds.gradeSevenLessonThree, "L3", "Fractions"],
  [educationIds.gradeSevenLessonFour, "L4", "Decimals"],
].map(([id, code, title], index) => ({
  active: true,
  code,
  created_at: "2026-08-23T00:00:00Z",
  curriculum_version_id: educationIds.gradeSevenCurriculum,
  id,
  ordinal: index + 1,
  taxonomy_node_ids: [],
  title,
  unit_id: educationIds.gradeSevenUnit,
  updated_at: "2026-08-23T00:00:00Z",
})) satisfies CurriculumLesson[];

const materialIds = {
  duplicate: "00000000-0000-0000-0000-000000001001",
  guide: "00000000-0000-0000-0000-000000001002",
  ocr: "00000000-0000-0000-0000-000000001003",
  wrongGrade: "00000000-0000-0000-0000-000000001004",
} as const;

const generationJobId = "00000000-0000-0000-0000-000000002002";
const paperId = generationJobId;
const questionId = "00000000-0000-0000-0000-000000002003";
const feedbackId = "00000000-0000-0000-0000-000000002006";
const evalCaseId = "00000000-0000-0000-0000-000000002007";
const draftPaperId = "00000000-0000-0000-0000-000000002004";
const publishedPaperId = "00000000-0000-0000-0000-000000002005";

const fixtureMaterials: Material[] = [
  {
    curriculum: "Grade 5 Maths 2026",
    grade: 5,
    id: materialIds.duplicate,
    lesson: null,
    material_type: "syllabus",
    medium: "English",
    metadata_scope_version: 1,
    page_count: 42,
    status: "ready_for_ai",
    subject: "Maths",
    subject_id: educationIds.mathsSubject,
    title: "grade-5-maths-syllabus.pdf",
    unit: null,
    uploaded_at: "2026-08-23T12:30:00Z",
    year: 2026,
  },
  {
    curriculum: "Grade 5 Maths 2026",
    grade: 5,
    id: materialIds.guide,
    lesson: null,
    material_type: "teacher_guide",
    medium: "English",
    metadata_scope_version: 1,
    page_count: 96,
    status: "processing",
    subject: "Maths",
    subject_id: educationIds.mathsSubject,
    title: "grade-5-maths-teacher-guide.pdf",
    unit: null,
    uploaded_at: "2026-08-24T09:00:00Z",
    year: 2026,
  },
  {
    curriculum: "Grade 5 Maths 2026",
    grade: 5,
    id: materialIds.ocr,
    lesson: null,
    material_type: "past_paper",
    medium: "English",
    metadata_scope_version: 1,
    page_count: 2,
    status: "needs_review",
    subject: "Maths",
    subject_id: educationIds.mathsSubject,
    title: "grade-5-maths-2025-paper.pdf",
    unit: null,
    uploaded_at: "2026-08-24T10:00:00Z",
    year: 2025,
  },
  {
    curriculum: "Grade 5 Maths 2026",
    grade: 5,
    id: materialIds.wrongGrade,
    lesson: null,
    material_type: "past_paper",
    medium: "English",
    metadata_scope_version: 1,
    page_count: 16,
    status: "processing",
    subject: "Maths",
    subject_id: educationIds.mathsSubject,
    title: "grade-11-algebra-paper.pdf",
    unit: null,
    uploaded_at: "2026-08-24T11:00:00Z",
    year: 2025,
  },
];

function fixtureSource(
  material: Material,
  extractionStatus: SourceDocument["extraction_status"],
  checksumCharacter: string,
  overrides: Partial<SourceDocument> = {},
): SourceDocument {
  const complete = ["extracted", "in_review", "trusted"].includes(extractionStatus);
  return {
    active_for_ai: true,
    checksum_sha256: checksumCharacter.repeat(64),
    content_type: "application/pdf",
    created_at: material.uploaded_at,
    curriculum_version_id: educationIds.gradeFiveCurriculum,
    deduplicated: false,
    document_type: material.material_type,
    extracted_block_count: complete ? material.page_count : null,
    extracted_character_count: complete ? 64 : null,
    extracted_page_count: material.page_count,
    extraction_attempt_count: 1,
    extraction_completed_at: complete ? "2026-08-24T10:01:00Z" : null,
    extraction_config: null,
    extraction_failure_code: null,
    extraction_queue_message_id: complete ? null : "fixture-reading-message",
    extraction_started_at: complete ? "2026-08-24T10:00:30Z" : null,
    extraction_status: extractionStatus,
    extractor: complete ? "pymupdf" : null,
    extractor_version: complete ? "1.28.2" : null,
    id: material.id,
    lesson_id: null,
    likely_metadata_duplicate_of_id: null,
    metadata_scope_version: material.metadata_scope_version,
    native_text_page_ratio: complete ? 1 : null,
    needs_ocr: complete ? false : null,
    ocr_page_count: complete ? 0 : null,
    original_filename: material.title,
    paper_code: null,
    removal_reason: null,
    removed_at: null,
    removed_by: null,
    size_bytes: 1_024,
    subject_id: material.subject_id,
    unit_id: null,
    use_state: "active",
    year: material.year,
    ...overrides,
  };
}

const fixtureSources: SourceDocument[] = [
  fixtureSource(fixtureMaterials[0]!, "trusted", "a"),
  fixtureSource(fixtureMaterials[1]!, "extraction_pending", "b"),
  fixtureSource(fixtureMaterials[2]!, "extracted", "c", {
    extraction_config: {
      mode: "ocr",
      ocr: { engine: "tesseract", version: "5.4.1" },
      ocr_page_numbers: [1, 2],
    },
    extractor: "tesseract",
    extractor_version: "5.4.1",
    native_text_page_ratio: 0,
    needs_ocr: false,
    ocr_page_count: 2,
  }),
  fixtureSource(fixtureMaterials[3]!, "extraction_pending", "d"),
];

const fixturePages: SourcePage[] = [
  {
    block_count: 1,
    character_count: 34,
    confidence: 0.82,
    created_at: "2026-08-24T10:01:00Z",
    extraction_config: { language: "eng" },
    extractor: "tesseract",
    extractor_version: "5.4.1",
    id: "00000000-0000-0000-0000-000000001101",
    page_number: 1,
    raw_text: "Thre equal parts are shaded.",
    reviewed_text: null,
    source_document_id: materialIds.ocr,
    updated_at: "2026-08-24T10:01:00Z",
    version: 1,
  },
  {
    block_count: 1,
    character_count: 8,
    confidence: 0.95,
    created_at: "2026-08-24T10:01:00Z",
    extraction_config: { language: "eng" },
    extractor: "tesseract",
    extractor_version: "5.4.1",
    id: "00000000-0000-0000-0000-000000001102",
    page_number: 2,
    raw_text: "Answer B",
    reviewed_text: null,
    source_document_id: materialIds.ocr,
    updated_at: "2026-08-24T10:01:00Z",
    version: 1,
  },
];

const fixtureBlocks: ExtractedBlock[] = fixturePages.map((page, index) => ({
  bbox: [0, 0, 100, 100],
  character_count: page.character_count,
  confidence: page.confidence,
  created_at: page.created_at,
  extraction_config: page.extraction_config,
  extractor: page.extractor,
  extractor_version: page.extractor_version,
  id: `00000000-0000-0000-0000-00000000120${index + 1}`,
  page_number: page.page_number,
  raw_text: page.raw_text,
  reading_order: 0,
  reviewed_text: page.reviewed_text,
  source_document_id: materialIds.ocr,
  source_page_id: page.id,
  updated_at: page.updated_at,
  version: page.version,
}));

const generationLessons = [
  {
    code: "LESSON-1",
    label: "Lesson 1 — Whole numbers",
    number: 1,
    taxonomy: ["Whole numbers"],
    unit: "Numbers",
  },
  {
    code: "LESSON-2",
    label: "Lesson 2 — Factors and multiples",
    number: 2,
    taxonomy: ["Factors and multiples"],
    unit: "Numbers",
  },
  {
    code: "LESSON-3",
    label: "Lesson 3 — Fractions",
    number: 3,
    taxonomy: ["Fractions"],
    unit: "Numbers",
  },
  {
    code: "LESSON-4",
    label: "Lesson 4 — Decimals",
    number: 4,
    taxonomy: ["Decimals"],
    unit: "Numbers",
  },
] satisfies components["schemas"]["LessonOption"][];

const generationOptions = {
  assessment_programmes: [
    { code: "SCHOOL-G7", grade: 7, label: "School practice paper" },
  ],
  defaults: { difficulty: "balanced", duration_minutes: 45, question_count: 10 },
  grades: Array.from({ length: 13 }, (_, index) => index + 1),
  media: [{ code: "en", label: "English" }],
  subjects: [
    {
      assessment_programme: "SCHOOL-G7",
      code: "MATHEMATICS",
      grade: 7,
      label: "Maths",
      lessons: generationLessons,
      medium: "en",
      units: [{ code: "NUMBERS", label: "Numbers" }],
    },
  ],
} satisfies TeacherPaperOptions;

const teacherCurricula = {
  items: [
    {
      assessment_label: "School Grade 7",
      assessment_programme: "SCHOOL-G7",
      code: "G7-MATHS-2026",
      label: "Grade 7 Maths 2026",
    },
  ],
} satisfies CurriculumLabels;

const teacherLessons = {
  curriculum: teacherCurricula.items[0],
  grade: 7,
  lessons: generationLessons,
  medium: "en",
  subject: "MATHEMATICS",
} satisfies LessonLabels;

function teacherPaperJob(overrides: Partial<TeacherPaperJob> = {}): TeacherPaperJob {
  return {
    completed_at: "2026-08-25T10:03:00Z",
    cost_microusd: 45_000,
    counts: {
      approved: 0,
      candidates: 3,
      failed: 0,
      generated: 3,
      requested: 3,
      validated: 3,
    },
    created_at: "2026-08-25T10:00:00Z",
    deduplicated: false,
    failure: null,
    grade: 7,
    job_id: generationJobId,
    medium: "English",
    paper_id: paperId,
    paper_reference: "EGP-G7-MATH-0001",
    progress: ["preparing", "generating", "checking_answers", "ready_for_review"],
    review_url: `/admin/review-approve?paper=${generationJobId}`,
    scope_summary: "Lessons 1–3",
    slots: [1, 2, 3].map((number) => ({
      candidate_id: `00000000-0000-0000-0000-00000000202${number}`,
      failure: null,
      generation_run_id: `00000000-0000-0000-0000-00000000203${number}`,
      id: `00000000-0000-0000-0000-00000000204${number}`,
      lesson: `Lesson ${number}`,
      number,
      status: "awaiting_review",
      validation: "ready" as const,
      version: 3,
    })),
    status: "ready_for_review",
    subject: "Maths",
    title: "Grade 7 Maths practice paper",
    total_tokens: 2_100,
    updated_at: "2026-08-25T10:03:00Z",
    version: 4,
    ...overrides,
  };
}

const reviewQuestion = {
  aggregate_slot_version: 4,
  answer: "B — 3/4",
  content: {
    answer: "B — 3/4",
    explanation: "Three of the four equal parts are shaded, so the fraction is 3/4.",
    marking_guide: ["Identifies three shaded parts out of four equal parts."],
    marks: 2,
    options: [
      { option_id: "A", text: "1/4" },
      { option_id: "B", text: "3/4" },
      { option_id: "C", text: "3/3" },
      { option_id: "D", text: "4/3" },
    ],
    question_type: "multiple_choice",
    stem: "What fraction of the four equal parts is shaded when three parts are shaded?",
  },
  explanation: "Three of the four equal parts are shaded, so the fraction is 3/4.",
  id: questionId,
  marking_scheme: {
    criteria: ["Identifies three shaded parts out of four equal parts."],
    total_marks: 2,
  },
  number: 1,
  options: [
    { label: "A", text: "1/4" },
    { label: "B", text: "3/4" },
    { label: "C", text: "3/3" },
    { label: "D", text: "4/3" },
  ],
  requires_revalidation: false,
  review_state: "validated",
  scope: {
    grade: 7,
    lesson: "Lesson 3 — Fractions",
    lessons: "Lessons 1–3",
    subject: "Maths",
    taxonomy: "Fractions",
    unit: "Numbers",
  },
  sources: [
    {
      filename: "grade-7-maths-teacher-guide.pdf",
      page: 18,
      title: "Grade 7 Maths Teacher Guide",
    },
  ],
  stem: "What fraction of the four equal parts is shaded when three parts are shaded?",
  technical_details: {
    blueprint_slot_id: "slot-1",
    candidate_id: "00000000-0000-0000-0000-000000002012",
    context_ids: ["knowledge_chunk:00000000-0000-0000-0000-000000002010"],
    generation_run_id: "00000000-0000-0000-0000-000000002011",
    model_version: "fixture-model-v1",
    provider: "deterministic-fixture-provider",
    validation_run_id: "00000000-0000-0000-0000-000000002013",
    validator_findings: [
      {
        code: "subject.answer.consistency",
        evidence: [],
        message: "The proposed answer matches the checked result.",
        status: "pass",
      },
      {
        code: "subject.math.numeric_equivalence",
        evidence: [
          {
            location: "$.candidate.answer",
            expected: "3/4",
            observed: "3/4",
          },
        ],
        message: "The fraction calculation is correct.",
        status: "pass",
      },
      {
        code: "subject.factual.source_supported",
        evidence: [
          {
            location: "$.semantic_verification",
            expected: "reviewed evidence",
            observed: "supported",
          },
        ],
        semantic_verification: {
          schema_version: "semantic-verification.v1",
          decomposition_version: "deterministic-factual-claims.v1",
          call_attempted: true,
          failure_code: null,
          status: "supported",
          summary: "Reviewed material supports the answer and explanation.",
          claims: [
            {
              claim_id: "answer",
              claim_type: "answer",
              location: "$.candidate.answer",
              status: "supported",
              summary: "The proposed answer is supported.",
              evidence_refs: [
                {
                  context_id: "context-01",
                  source_document_id: "grade-7-maths-teacher-guide",
                  page_number: 18,
                },
              ],
            },
          ],
          lineage: {
            verifier_id: "semantic-fixture",
            verifier_version: "2.0.0",
            prompt_version: "semantic.v2",
            provider: "fixture",
            provider_version: "1.0.0",
            model: "fixture-model",
            model_version: "fixture-model-v1",
            pricing_version: "fixture-pricing-v1",
          },
          accounting: {
            input_tokens: 100,
            output_tokens: 20,
            total_tokens: 120,
            cost_microusd: 31,
            latency_ms: 80,
          },
        },
        message: "The source supports the question and answer.",
        status: "pass",
      },
    ],
  },
  validation: {
    findings: [],
    status: "ready",
    summary: "The answer, calculation, and source checks passed.",
  },
  version: 1,
} satisfies ReviewQuestion;

const reviewPaper = {
  created_at: "2026-08-25T10:00:00Z",
  draft: null,
  grade: 7,
  id: paperId,
  medium: "English",
  paper_reference: "EGP-G7-MATH-0001",
  questions: [reviewQuestion],
  scope_summary: "Lessons 1–3",
  status: "in_review",
  subject: "Maths",
  technical_details: {
    cost_microusd: 45_000,
    curriculum_version_id: educationIds.gradeSevenCurriculum,
    paper_blueprint_id: "00000000-0000-0000-0000-000000002014",
    request_fingerprint: `sha256:${"a".repeat(64)}`,
    total_tokens: 2_100,
  },
  title: "Grade 7 Maths practice paper",
  version: 4,
} satisfies ReviewPaper;

const publishedPaper = {
  blueprint_id: "grade7-maths-lessons-1-3",
  blueprint_version: "1.0.0",
  created_at: "2026-08-25T10:00:00Z",
  created_by: "00000000-0000-0000-0000-000000002021",
  current_version: 2,
  curriculum_version_id: educationIds.gradeSevenCurriculum,
  id: publishedPaperId,
  latest_publication_hash: `sha256:${"b".repeat(64)}`,
  paper_blueprint_id: "00000000-0000-0000-0000-000000002022",
  state: "published",
  title: "Grade 7 Maths Lessons 1–3 practice paper",
  updated_at: "2026-08-25T11:00:00Z",
  updated_by: "00000000-0000-0000-0000-000000002023",
} satisfies PublishedPaper;

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({
    body: JSON.stringify(value),
    contentType: "application/json",
    status,
  });
}

function requestBody(request: ReturnType<Route["request"]>): unknown {
  const contentType = request.headers()["content-type"] ?? "";
  if (!contentType.includes("application/json")) return null;
  try {
    return request.postDataJSON();
  } catch {
    return null;
  }
}

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
  await expect(page.getByRole("heading", { name: "Create and manage exam papers" })).toBeVisible();
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
    ...offsets.map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`),
  ].join("");
  return Buffer.from(
    `${body}${xref}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`,
    "ascii",
  );
}

export async function installTeacherStudioFixture(page: Page): Promise<TeacherStudioFixture> {
  const state: TeacherStudioFixture = {
    corrections: [],
    curriculumIds: { gradeEleven: educationIds.gradeElevenCurriculum },
    generationIntents: [],
    materialIds,
    materials: fixtureMaterials.map((material) => ({ ...material })),
    requests: [],
    reviewQuestionId: questionId,
    sourceDocuments: fixtureSources.map((source) => ({ ...source })),
  };
  let sourcePages = fixturePages.map((sourcePage) => ({ ...sourcePage }));
  let sourceBlocks = fixtureBlocks.map((sourceBlock) => ({ ...sourceBlock }));
  let currentReviewPaper: ReviewPaper = structuredClone(reviewPaper);

  await page.route("**/api/v1/admin/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = requestBody(request);
    state.requests.push({ body, headers: request.headers(), method, path, search: url.search });

    if (method === "GET" && path.endsWith("/exam-configurations")) {
      return json(route, [
        {
          active: true,
          code: "G5",
          created_at: "2026-08-23T00:00:00Z",
          grade: 5,
          id: educationIds.gradeFiveExam,
          name: "Grade 5 Scholarship",
          updated_at: "2026-08-23T00:00:00Z",
        },
        {
          active: true,
          code: "G7",
          created_at: "2026-08-23T00:00:00Z",
          grade: 7,
          id: educationIds.gradeSevenExam,
          name: "Grade 7 school papers",
          updated_at: "2026-08-23T00:00:00Z",
        },
        {
          active: true,
          code: "G11",
          created_at: "2026-08-23T00:00:00Z",
          grade: 11,
          id: educationIds.gradeElevenExam,
          name: "GCE O/L",
          updated_at: "2026-08-23T00:00:00Z",
        },
      ]);
    }
    if (method === "GET" && path.endsWith("/media")) {
      return json(route, [
        {
          active: true,
          code: "en",
          created_at: "2026-08-23T00:00:00Z",
          id: educationIds.medium,
          name: "English",
          updated_at: "2026-08-23T00:00:00Z",
        },
      ]);
    }
    if (method === "GET" && path.endsWith("/subjects")) {
      return json(route, [
        {
          active: true,
          code: "MATHS",
          created_at: "2026-08-23T00:00:00Z",
          id: educationIds.mathsSubject,
          name: "Maths",
          updated_at: "2026-08-23T00:00:00Z",
        },
      ]);
    }
    if (method === "GET" && path.endsWith("/curriculum-versions")) {
      return json(route, [
        {
          active: true,
          code: "G5-MATHS-2026",
          created_at: "2026-08-23T00:00:00Z",
          exam_configuration_id: educationIds.gradeFiveExam,
          id: educationIds.gradeFiveCurriculum,
          medium_id: educationIds.medium,
          subject_id: educationIds.mathsSubject,
          title: "Grade 5 Maths 2026",
          updated_at: "2026-08-23T00:00:00Z",
        },
        {
          active: true,
          code: "G7-MATHS-2026",
          created_at: "2026-08-23T00:00:00Z",
          exam_configuration_id: educationIds.gradeSevenExam,
          id: educationIds.gradeSevenCurriculum,
          medium_id: educationIds.medium,
          subject_id: educationIds.mathsSubject,
          title: "Grade 7 Maths 2026",
          updated_at: "2026-08-23T00:00:00Z",
        },
        {
          active: true,
          code: "G11-MATHS-2026",
          created_at: "2026-08-23T00:00:00Z",
          exam_configuration_id: educationIds.gradeElevenExam,
          id: educationIds.gradeElevenCurriculum,
          medium_id: educationIds.medium,
          subject_id: educationIds.mathsSubject,
          title: "Grade 11 Maths 2026",
          updated_at: "2026-08-23T00:00:00Z",
        },
      ]);
    }
    const unitScope = path.match(/\/curriculum-versions\/([^/]+)\/units$/);
    if (method === "GET" && unitScope) {
      return json(
        route,
        unitScope[1] === educationIds.gradeSevenCurriculum ? gradeSevenUnits : [],
      );
    }
    const lessonScope = path.match(/\/curriculum-versions\/([^/]+)\/lessons$/);
    if (method === "GET" && lessonScope) {
      return json(
        route,
        lessonScope[1] === educationIds.gradeSevenCurriculum ? gradeSevenLessons : [],
      );
    }

    if (method === "GET" && path.endsWith("/materials/grade-summary")) {
      const grades = Array.from({ length: 13 }, (_, index) => {
        const grade = index + 1;
        const gradeMaterials = state.materials.filter((material) => material.grade === grade);
        return {
          grade,
          material_count: gradeMaterials.length,
          needs_review_count: gradeMaterials.filter(
            (material) => material.status === "needs_review",
          ).length,
          processing_count: gradeMaterials.filter((material) => material.status === "processing")
            .length,
          ready_count: gradeMaterials.filter((material) => material.status === "ready_for_ai")
            .length,
          removed_count: gradeMaterials.filter((material) => material.status === "removed").length,
          subject_count: new Set(gradeMaterials.map((material) => material.subject_id)).size,
        };
      });
      return json(route, grades);
    }

    if (method === "GET" && path.endsWith("/materials")) {
      const grade = url.searchParams.get("grade");
      const subjectId = url.searchParams.get("subject_id");
      return json(
        route,
        state.materials.filter(
          (material) =>
            (!grade || material.grade === Number(grade)) &&
            (!subjectId || material.subject_id === subjectId),
        ),
      );
    }

    if (method === "GET" && path.endsWith("/source-documents")) {
      return json(route, state.sourceDocuments);
    }
    if (method === "POST" && path.endsWith("/source-documents")) {
      return json(route, { ...state.sourceDocuments[0], deduplicated: true }, 200);
    }

    if (
      method === "GET" &&
      path.endsWith(`/source-documents/${materialIds.ocr}/pages`)
    ) {
      return json(route, sourcePages);
    }
    const sourcePage = sourcePages.find((candidate) =>
      path.includes(
        `/source-documents/${materialIds.ocr}/pages/${candidate.page_number}`,
      ),
    );
    if (sourcePage && method === "GET" && path.endsWith("/blocks")) {
      return json(
        route,
        sourceBlocks.filter((candidate) => candidate.page_number === sourcePage.page_number),
      );
    }
    if (
      method === "POST" &&
      path.endsWith(`/source-documents/${materialIds.ocr}/review`)
    ) {
      const source = state.sourceDocuments.find((candidate) => candidate.id === materialIds.ocr)!;
      source.extraction_status = "in_review";
      return json(route, source);
    }
    if (sourcePage && method === "PATCH") {
      const payload = body as ReviewedTextUpdate | null;
      if (!payload || payload.expected_version !== sourcePage.version) {
        return json(route, { detail: { code: "concurrent_review_modification" } }, 409);
      }
      const correctedPage: SourcePage = {
        ...sourcePage,
        reviewed_text: payload.reviewed_text,
        updated_at: "2026-08-25T11:00:00Z",
        version: sourcePage.version + 1,
      };
      sourcePages = sourcePages.map((candidate) =>
        candidate.id === sourcePage.id ? correctedPage : candidate,
      );
      sourceBlocks = sourceBlocks.map((candidate) =>
        candidate.page_number === sourcePage.page_number
          ? {
              ...candidate,
              reviewed_text: payload.reviewed_text,
              updated_at: correctedPage.updated_at,
              version: candidate.version + 1,
            }
          : candidate,
      );
      state.corrections.push(payload.reviewed_text);
      return json(route, correctedPage);
    }
    if (
      method === "POST" &&
      path.endsWith(`/source-documents/${materialIds.ocr}/trust`)
    ) {
      const source = state.sourceDocuments.find((candidate) => candidate.id === materialIds.ocr)!;
      const material = state.materials.find((candidate) => candidate.id === materialIds.ocr)!;
      source.extraction_status = "trusted";
      material.status = "ready_for_ai";
      return json(route, source);
    }

    const material = state.materials.find((candidate) => path.includes(candidate.id));
    const source = material
      ? state.sourceDocuments.find((candidate) => candidate.id === material.id)
      : undefined;
    if (
      material &&
      source &&
      method === "PATCH" &&
      path.endsWith(`/materials/${material.id}/scope`)
    ) {
      const payload = body as MaterialScopeRequest | null;
      if (!payload || payload.expected_version !== source.metadata_scope_version) {
        return json(
          route,
          { detail: { code: "concurrent_material_scope_modification" } },
          409,
        );
      }
      if (payload.curriculum_version_id === educationIds.gradeElevenCurriculum) {
        material.curriculum = "Grade 11 Maths 2026";
        material.grade = 11;
        material.lesson = null;
        material.unit = null;
        material.metadata_scope_version += 1;
        source.curriculum_version_id = educationIds.gradeElevenCurriculum;
        source.lesson_id = payload.lesson_id ?? null;
        source.unit_id = payload.unit_id ?? null;
        source.metadata_scope_version += 1;
      }
      return json(route, source);
    }
    if (
      material &&
      source &&
      method === "POST" &&
      path.endsWith(`/materials/${material.id}/remove-from-use`)
    ) {
      const payload = body as MaterialRemoveRequest | null;
      if (
        !payload ||
        !payload.reason.trim() ||
        payload.reason.length > 512 ||
        payload.expected_version !== source.metadata_scope_version
      ) {
        return json(route, { detail: { code: "invalid_removal_reason" } }, 422);
      }
      material.status = "removed";
      material.metadata_scope_version += 1;
      source.active_for_ai = false;
      source.metadata_scope_version += 1;
      source.removal_reason = payload.reason;
      source.removed_at = "2026-08-25T11:00:00Z";
      source.removed_by = "00000000-0000-0000-0000-000000000001";
      source.use_state = "removed";
      return json(route, source);
    }
    if (
      material &&
      source &&
      method === "POST" &&
      path.endsWith(`/materials/${material.id}/restore`)
    ) {
      const payload = body as MaterialRestoreRequest | null;
      if (!payload || payload.expected_version !== source.metadata_scope_version) {
        return json(
          route,
          { detail: { code: "concurrent_material_scope_modification" } },
          409,
        );
      }
      material.status = source.extraction_status === "trusted" ? "ready_for_ai" : "needs_review";
      material.metadata_scope_version += 1;
      source.active_for_ai = true;
      source.metadata_scope_version += 1;
      source.removal_reason = null;
      source.removed_at = null;
      source.removed_by = null;
      source.use_state = "active";
      return json(route, source);
    }

    if (method === "GET" && path.endsWith("/paper-generation/options")) {
      return json(route, generationOptions);
    }
    if (method === "GET" && path.endsWith("/paper-generation/curricula")) {
      return json(route, teacherCurricula);
    }
    if (method === "GET" && path.endsWith("/paper-generation/lessons")) {
      return json(route, teacherLessons);
    }
    if (method === "POST" && path.endsWith("/paper-generation/jobs")) {
      state.generationIntents.push(body);
      return json(
        route,
        teacherPaperJob({
          completed_at: null,
          cost_microusd: 0,
          counts: {
            approved: 0,
            candidates: 0,
            failed: 0,
            generated: 0,
            requested: 12,
            validated: 0,
          },
          progress: ["preparing"],
          review_url: null,
          slots: [],
          status: "preparing",
          total_tokens: 0,
          updated_at: "2026-08-25T10:00:00Z",
          version: 1,
        }),
        202,
      );
    }
    if (method === "GET" && path.endsWith(`/paper-generation/jobs/${generationJobId}`)) {
      return json(route, teacherPaperJob());
    }

    if (method === "GET" && path.endsWith("/review-papers")) {
      return json(route, {
        items: [
          {
            approved_count: currentReviewPaper.questions.filter(
              (question) => question.review_state === "approved",
            ).length,
            created_at: currentReviewPaper.created_at,
            grade: currentReviewPaper.grade,
            id: currentReviewPaper.id,
            paper_reference: currentReviewPaper.paper_reference,
            question_count: currentReviewPaper.questions.length,
            scope_summary: currentReviewPaper.scope_summary,
            status: currentReviewPaper.status,
            subject: currentReviewPaper.subject,
            title: currentReviewPaper.title,
          },
        ],
      } satisfies components["schemas"]["ReviewPaperListResponse"]);
    }
    if (method === "GET" && path.endsWith(`/review-papers/${paperId}`)) {
      return json(route, currentReviewPaper);
    }

    const currentQuestion = currentReviewPaper.questions.find((question) =>
      path.includes(`/questions/${question.id}`),
    );
    if (method === "POST" && currentQuestion && path.endsWith(`/questions/${currentQuestion.id}/start`)) {
      const payload = body as components["schemas"]["ReviewCandidateStartRequest"] | null;
      if (!payload || payload.expected_version !== currentQuestion.version) {
        return json(route, { detail: { code: "review_question_version_conflict" } }, 409);
      }
      const started: ReviewQuestion = {
        ...currentQuestion,
        review_state: "in_review",
        version: currentQuestion.version + 1,
      };
      currentReviewPaper = {
        ...currentReviewPaper,
        questions: [started],
        version: currentReviewPaper.version + 1,
      };
      return json(route, started);
    }
    if (method === "PATCH" && currentQuestion && path.endsWith(`/questions/${currentQuestion.id}`)) {
      const payload = body as components["schemas"]["ReviewQuestionEditRequest"] | null;
      if (!payload || payload.expected_version !== currentQuestion.version || !payload.reason_code) {
        return json(route, { detail: { code: "review_question_version_conflict" } }, 409);
      }
      const edited: ReviewQuestion = {
        ...currentQuestion,
        aggregate_slot_version: currentQuestion.aggregate_slot_version + 1,
        answer: payload.content.answer,
        content: { ...payload.content },
        explanation: payload.content.explanation,
        marking_scheme: {
          criteria: payload.content.marking_guide,
          total_marks: payload.content.marks,
        },
        options: payload.content.options.map((option) => ({
          label: option.option_id,
          text: option.text,
        })),
        quality_feedback_id: feedbackId,
        requires_revalidation: true,
        stem: payload.content.stem,
        validation: {
          findings: ["The edit needs a fresh check."],
          status: "needs_attention",
          summary: "Changes saved; a fresh check is required before approval.",
        },
        version: currentQuestion.version + 1,
      };
      currentReviewPaper = {
        ...currentReviewPaper,
        questions: [edited],
        version: currentReviewPaper.version + 1,
      };
      return json(route, edited);
    }
    if (method === "POST" && currentQuestion && path.endsWith(`/questions/${currentQuestion.id}/approve`)) {
      const payload = body as components["schemas"]["ReviewCandidateApproveRequest"] | null;
      if (
        !payload ||
        payload.expected_version !== currentQuestion.version ||
        currentQuestion.requires_revalidation ||
        currentQuestion.validation.status === "failed_check"
      ) {
        return json(route, { detail: { code: "review_question_revalidation_required" } }, 409);
      }
      const approved: ReviewQuestion = {
        ...currentQuestion,
        review_state: "approved",
        version: currentQuestion.version + 1,
      };
      currentReviewPaper = {
        ...currentReviewPaper,
        questions: [approved],
        version: currentReviewPaper.version + 1,
      };
      return json(route, approved);
    }
    if (method === "POST" && currentQuestion && path.endsWith(`/questions/${currentQuestion.id}/reject`)) {
      const payload = body as components["schemas"]["ReviewQuestionRejectRequest"] | null;
      if (!payload || payload.expected_version !== currentQuestion.version || !payload.reason_code) {
        return json(route, { detail: { code: "review_question_version_conflict" } }, 409);
      }
      const rejected: ReviewQuestion = {
        ...currentQuestion,
        quality_feedback_id: feedbackId,
        review_state: "rejected",
        version: currentQuestion.version + 1,
      };
      currentReviewPaper = {
        ...currentReviewPaper,
        questions: [rejected],
        version: currentReviewPaper.version + 1,
      };
      return json(route, rejected);
    }
    if (method === "POST" && currentQuestion && path.endsWith(`/questions/${currentQuestion.id}/regenerate`)) {
      const payload = body as components["schemas"]["ReviewQuestionRegenerateRequest"] | null;
      if (
        !payload ||
        payload.expected_version !== currentQuestion.aggregate_slot_version ||
        !payload.reason_code
      ) {
        return json(route, { detail: { code: "review_question_version_conflict" } }, 409);
      }
      const replacement: ReviewQuestion = {
        ...currentQuestion,
        aggregate_slot_version: currentQuestion.aggregate_slot_version + 1,
        requires_revalidation: false,
        review_state: "validated",
        validation: {
          findings: [],
          status: "ready",
          summary: "The replacement passed fresh checks.",
        },
        version: currentQuestion.version + 1,
      };
      currentReviewPaper = {
        ...currentReviewPaper,
        questions: [replacement],
        version: currentReviewPaper.version + 1,
      };
      return json(
        route,
        {
          job_id: "00000000-0000-0000-0000-000000002031",
          paper_id: paperId,
          quality_feedback_id: feedbackId,
          question_id: questionId,
          status: "generating",
          version: replacement.aggregate_slot_version,
        } satisfies components["schemas"]["ReviewQuestionRegenerationResponse"],
        202,
      );
    }
    if (method === "POST" && path.endsWith(`/subject-quality/feedback/${feedbackId}/promote`)) {
      return json(
        route,
        {
          approved_at: null,
          approved_by: null,
          can_approve: true,
          case_fingerprint: `sha256:${"b".repeat(64)}`,
          created_at: "2026-08-25T10:05:00Z",
          deduplicated: false,
          defect_category: "language_clarity",
          eval_case_id: evalCaseId,
          expected_finding_codes: ["subject.language.ambiguous_wording"],
          expected_status: "warn",
          promoted_by: "00000000-0000-0000-0000-000000002099",
          source_feedback_id: feedbackId,
          state: "draft",
          version: 1,
        } satisfies components["schemas"]["SubjectQualityEvalCaseResponse"],
        201,
      );
    }
    if (method === "POST" && path.endsWith(`/subject-quality/eval-cases/${evalCaseId}/approve`)) {
      return json(route, {
        approved_at: "2026-08-25T10:06:00Z",
        approved_by: "00000000-0000-0000-0000-000000002098",
        can_approve: false,
        case_fingerprint: `sha256:${"b".repeat(64)}`,
        created_at: "2026-08-25T10:05:00Z",
        deduplicated: false,
        defect_category: "language_clarity",
        eval_case_id: evalCaseId,
        expected_finding_codes: ["subject.language.ambiguous_wording"],
        expected_status: "warn",
        promoted_by: "00000000-0000-0000-0000-000000002099",
        source_feedback_id: feedbackId,
        state: "approved",
        version: 2,
      } satisfies components["schemas"]["SubjectQualityEvalCaseResponse"]);
    }
    if (method === "POST" && path.endsWith(`/review-papers/${paperId}/create-draft`)) {
      const payload = body as components["schemas"]["ReviewPaperCreateDraftRequest"] | null;
      if (!payload || payload.expected_version !== currentReviewPaper.version) {
        return json(route, { detail: { code: "review_question_version_conflict" } }, 409);
      }
      currentReviewPaper = {
        ...currentReviewPaper,
        draft: { draft_id: draftPaperId, version: 1 },
        status: "draft_created",
      };
      return json(
        route,
        {
          draft_id: draftPaperId,
          draft_version: 1,
          paper_id: paperId,
          paper_reference: currentReviewPaper.paper_reference,
          publication_path: `/api/v1/admin/curricula/${educationIds.gradeSevenCurriculum}/papers/${draftPaperId}`,
        } satisfies components["schemas"]["ReviewPaperDraftCreatedResponse"],
        201,
      );
    }

    const paperLibrary = path.match(/\/curricula\/([^/]+)\/papers$/);
    if (method === "GET" && paperLibrary) {
      return json(
        route,
        paperLibrary[1] === educationIds.gradeSevenCurriculum ? [publishedPaper] : [],
      );
    }

    return route.fallback();
  });

  return state;
}
