import type { components } from "@exam-guru/api-client";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KnowledgeStudio } from "./knowledge-studio";

type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type SourcePage = components["schemas"]["SourcePageResponse"];
type ExtractedBlock = components["schemas"]["ExtractedBlockResponse"];
type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
type HistoricalQuestion = components["schemas"]["HistoricalQuestionResponse"];
type KnowledgeChunk = components["schemas"]["KnowledgeChunkResponse"];

const ids = {
  block: "00000000-0000-0000-0000-000000000301",
  chunk: "00000000-0000-0000-0000-000000000501",
  competency: "00000000-0000-0000-0000-000000000401",
  concept: "00000000-0000-0000-0000-000000000404",
  crossCurriculum: "00000000-0000-0000-0000-000000000102",
  crossDocument: "00000000-0000-0000-0000-000000000204",
  curriculum: "00000000-0000-0000-0000-000000000101",
  inactiveCurriculum: "00000000-0000-0000-0000-000000000103",
  page: "00000000-0000-0000-0000-000000000302",
  paper: "00000000-0000-0000-0000-000000000201",
  question: "00000000-0000-0000-0000-000000000501",
  skill: "00000000-0000-0000-0000-000000000402",
  subSkill: "00000000-0000-0000-0000-000000000403",
  subject: "00000000-0000-0000-0000-000000000013",
  syllabus: "00000000-0000-0000-0000-000000000202",
  untrusted: "00000000-0000-0000-0000-000000000203",
} as const;

const curriculum = {
  active: true,
  code: "G5-SI-2026",
  created_at: "2026-08-23T00:00:00Z",
  exam_configuration_id: "00000000-0000-0000-0000-000000000011",
  id: ids.curriculum,
  medium_id: "00000000-0000-0000-0000-000000000012",
  subject_id: ids.subject,
  title: "Grade 5 Sinhala 2026",
  updated_at: "2026-08-23T00:00:00Z",
} satisfies Curriculum;

const inactiveCurriculum = {
  ...curriculum,
  active: false,
  code: "G5-SI-OLD",
  id: ids.inactiveCurriculum,
  title: "Inactive curriculum",
} satisfies Curriculum;

function sourceDocument(
  overrides: Partial<SourceDocument> & Pick<SourceDocument, "document_type" | "id" | "original_filename">,
): SourceDocument {
  return {
    active_for_ai: true,
    checksum_sha256: "a".repeat(64),
    content_type: "application/pdf",
    created_at: "2026-08-23T00:00:00Z",
    curriculum_version_id: ids.curriculum,
    deduplicated: false,
    extracted_block_count: 1,
    extracted_character_count: 42,
    extracted_page_count: 1,
    extraction_attempt_count: 1,
    extraction_completed_at: "2026-08-23T00:01:00Z",
    extraction_config: {
      mode: "native",
      native: {
        config: {},
        engine: "pymupdf",
        version: "1.26.4",
      },
    },
    extraction_failure_code: null,
    extraction_queue_message_id: null,
    extraction_started_at: "2026-08-23T00:00:30Z",
    extraction_status: "trusted",
    extractor: "pymupdf",
    extractor_version: "1.26.4",
    lesson_id: null,
    likely_metadata_duplicate_of_id: null,
    metadata_scope_version: 1,
    native_text_page_ratio: 1,
    needs_ocr: false,
    ocr_page_count: 0,
    paper_code: null,
    removal_reason: null,
    removed_at: null,
    removed_by: null,
    size_bytes: 512,
    subject_id: ids.subject,
    unit_id: null,
    use_state: "active",
    year: null,
    ...overrides,
  };
}

const paper = sourceDocument({
  document_type: "past_paper",
  id: ids.paper,
  original_filename: "grade-5-2025-paper.pdf",
  paper_code: "2025-I",
  year: 2025,
});
const syllabus = sourceDocument({
  document_type: "syllabus",
  id: ids.syllabus,
  original_filename: "grade-5-syllabus.pdf",
});
const untrusted = sourceDocument({
  document_type: "past_paper",
  extraction_status: "in_review",
  id: ids.untrusted,
  original_filename: "not-trusted.pdf",
  paper_code: "2024-I",
  year: 2024,
});
const crossDocument = sourceDocument({
  curriculum_version_id: ids.crossCurriculum,
  document_type: "past_paper",
  id: ids.crossDocument,
  original_filename: "other-curriculum.pdf",
  paper_code: "2023-I",
  year: 2023,
});

const sourcePage = {
  block_count: 1,
  character_count: 42,
  confidence: null,
  created_at: "2026-08-23T00:00:00Z",
  extraction_config: {},
  extractor: "pymupdf",
  extractor_version: "1.26.4",
  id: ids.page,
  page_number: 1,
  raw_text: "Which shape has exactly three sides?",
  reviewed_text: "Which shape has exactly three sides?",
  source_document_id: ids.paper,
  updated_at: "2026-08-23T00:00:00Z",
  version: 1,
} satisfies SourcePage;

function blockFor(documentId: string): ExtractedBlock {
  return {
    bbox: null,
    character_count: 35,
    confidence: null,
    created_at: "2026-08-23T00:00:00Z",
    extraction_config: {},
    extractor: "pymupdf",
    extractor_version: "1.26.4",
    id: ids.block,
    page_number: 1,
    raw_text: "Which shape has exactly three sides?",
    reading_order: 0,
    reviewed_text: "Which shape has exactly three sides?",
    source_document_id: documentId,
    source_page_id: ids.page,
    updated_at: "2026-08-23T00:00:00Z",
    version: 1,
  };
}

const taxonomy = [
  {
    active: true,
    code: "C1",
    curriculum_version_id: ids.curriculum,
    id: ids.competency,
    level: "competency",
    parent_id: null,
    review_state: "reviewed",
    title: "Shape and space",
  },
  {
    active: true,
    code: "S1",
    curriculum_version_id: ids.curriculum,
    id: ids.skill,
    level: "skill",
    parent_id: ids.competency,
    review_state: "reviewed",
    title: "Recognise polygons",
  },
  {
    active: true,
    code: "SS1",
    curriculum_version_id: ids.curriculum,
    id: ids.subSkill,
    level: "sub_skill",
    parent_id: ids.skill,
    review_state: "reviewed",
    title: "Classify triangles",
  },
  {
    active: true,
    code: "LC1",
    curriculum_version_id: ids.curriculum,
    id: ids.concept,
    level: "learning_concept",
    parent_id: ids.subSkill,
    review_state: "reviewed",
    title: "Three-sided polygons",
  },
] satisfies TaxonomyNode[];

function question(overrides: Partial<HistoricalQuestion> = {}): HistoricalQuestion {
  return {
    answer: null,
    classification: {
      competency_id: null,
      learning_concept_id: null,
      skill_id: null,
      sub_skill_id: null,
    },
    created_at: "2026-08-23T02:00:00Z",
    curriculum_version_id: ids.curriculum,
    deduplicated: false,
    difficulty_confidence: null,
    difficulty_label: null,
    difficulty_source: null,
    embedding_configurations: [
      {
        config_fingerprint: "sha256:fixture",
        dimension: 384,
        id: "00000000-0000-0000-0000-000000000601",
        model: "multilingual-e5-small",
        provider: "local",
        version: "v1",
      },
    ],
    embedding_status: "embedded",
    id: ids.question,
    lesson_id: null,
    marking_data: null,
    marking_guidance: null,
    marks: 2,
    media_references: null,
    options: null,
    paper_code: "2025-I",
    provenance: {
      page_number: 1,
      source_block_id: ids.block,
      source_document_id: ids.paper,
    },
    question_archetype: null,
    question_number: "12",
    question_type: "multiple_choice",
    review_state: "draft",
    text: "Which shape has exactly three sides?",
    unit_id: null,
    updated_at: "2026-08-23T02:00:00Z",
    version: 4,
    year: 2025,
    ...overrides,
  };
}

function chunk(overrides: Partial<KnowledgeChunk> = {}): KnowledgeChunk {
  return {
    chunk_type: "explanation",
    classification: {
      competency_id: null,
      learning_concept_id: null,
      skill_id: null,
      sub_skill_id: null,
    },
    created_at: "2026-08-23T02:00:00Z",
    curriculum_version_id: ids.curriculum,
    deduplicated: false,
    educational_boundary: "Geometry / triangles",
    embedding_configurations: [],
    embedding_status: "not_embedded",
    id: ids.chunk,
    lesson_id: null,
    provenance: {
      page_number: 1,
      source_block_id: ids.block,
      source_document_id: ids.syllabus,
    },
    review_state: "draft",
    sequence: 3,
    text: "A triangle is a polygon with three sides.",
    unit_id: null,
    updated_at: "2026-08-23T02:00:00Z",
    version: 0,
    ...overrides,
  };
}

function asRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(input, init);
}

type ApiFixtureOptions = {
  chunks?: KnowledgeChunk[];
  documents?: SourceDocument[];
  questions?: HistoricalQuestion[];
};

function fixtureApi(options: ApiFixtureOptions = {}) {
  let questions = options.questions ?? [];
  let chunks = options.chunks ?? [];
  const documents = options.documents ?? [paper, syllabus, untrusted, crossDocument];
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    requests.push(request.clone());
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname.endsWith("/curriculum-versions")) {
      return Response.json([curriculum, inactiveCurriculum]);
    }
    if (request.method === "GET" && url.pathname.endsWith("/source-documents")) {
      return Response.json(documents);
    }
    if (request.method === "GET" && url.pathname.endsWith("/taxonomy/nodes")) {
      return Response.json(taxonomy);
    }
    if (request.method === "GET" && /\/source-documents\/[^/]+\/pages$/.test(url.pathname)) {
      const documentId = url.pathname.split("/").at(-2) ?? "";
      return Response.json([{ ...sourcePage, source_document_id: documentId }]);
    }
    if (request.method === "GET" && url.pathname.endsWith("/blocks")) {
      const documentId = url.pathname.split("/").at(-4) ?? "";
      return Response.json([blockFor(documentId)]);
    }
    if (request.method === "GET" && url.pathname.endsWith("/embedding-jobs")) {
      return Response.json([]);
    }
    if (request.method === "GET" && url.pathname.endsWith("/knowledge/questions")) {
      return Response.json(questions);
    }
    if (request.method === "GET" && url.pathname.endsWith("/knowledge/chunks")) {
      return Response.json(chunks);
    }
    if (request.method === "POST" && url.pathname.endsWith("/knowledge/questions")) {
      const body = (await request.json()) as components["schemas"]["HistoricalQuestionImportRequest"];
      const imported = question({
        answer: body.answer ?? null,
        difficulty_confidence: body.difficulty_confidence ?? null,
        difficulty_label: body.difficulty_label ?? null,
        difficulty_source: body.difficulty_source ?? null,
        embedding_configurations: [],
        embedding_status: "not_embedded",
        marking_data: body.marking_data ?? null,
        marking_guidance: body.marking_guidance ?? null,
        media_references: body.media_references ?? null,
        options: body.options ?? null,
        provenance: {
          page_number: body.page_number,
          source_block_id: body.source_block_id ?? null,
          source_document_id: body.source_document_id,
        },
        question_archetype: body.question_archetype ?? null,
        question_number: body.question_number,
        text: body.text,
        version: 0,
      });
      questions = [imported, ...questions];
      return Response.json(imported, { status: 201 });
    }
    if (request.method === "POST" && url.pathname.endsWith("/knowledge/chunks")) {
      const body = (await request.json()) as components["schemas"]["KnowledgeChunkImportRequest"];
      const imported = chunk({
        chunk_type: body.chunk_type,
        educational_boundary: body.educational_boundary,
        provenance: {
          page_number: body.page_number,
          source_block_id: body.source_block_id ?? null,
          source_document_id: body.source_document_id,
        },
        sequence: body.sequence,
        text: body.text,
      });
      chunks = [imported, ...chunks];
      return Response.json(imported, { status: 201 });
    }
    return Response.json({ detail: { code: "unexpected_request" } }, { status: 500 });
  });
  return { fetchMock, requests };
}

async function selectProvenance(sourceLabel: string) {
  fireEvent.change(screen.getByLabelText("Trusted source document"), {
    target: { value: sourceLabel === paper.original_filename ? ids.paper : ids.syllabus },
  });
  await screen.findByRole("option", { name: "Page 1" });
  fireEvent.change(screen.getByLabelText("Source page"), { target: { value: "1" } });
  await screen.findByRole("option", { name: /Block 1/ });
  fireEvent.change(screen.getByLabelText("Source block"), { target: { value: ids.block } });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("KnowledgeStudio", () => {
  it("imports a historical question only from selectable trusted provenance", async () => {
    const api = fixtureApi();
    vi.stubGlobal("fetch", api.fetchMock);
    render(<KnowledgeStudio role="admin" />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading Knowledge Studio");
    expect(await screen.findByRole("heading", { name: "Knowledge Studio" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Embedding ingestion" })).toBeInTheDocument();
    expect(screen.getByLabelText("Active curriculum")).toHaveValue(ids.curriculum);
    expect(screen.queryByRole("option", { name: inactiveCurriculum.title })).not.toBeInTheDocument();

    const sourceSelect = screen.getByLabelText("Trusted source document");
    expect(within(sourceSelect).getByRole("option", { name: /grade-5-2025-paper.pdf/ })).toBeInTheDocument();
    expect(within(sourceSelect).queryByRole("option", { name: /not-trusted.pdf/ })).not.toBeInTheDocument();
    expect(within(sourceSelect).queryByRole("option", { name: /other-curriculum.pdf/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/source document id/i)).not.toBeInTheDocument();

    await selectProvenance(paper.original_filename);
    expect(screen.getByText("2025-I", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText("2025", { selector: "dd" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Question number"), { target: { value: "12" } });
    fireEvent.change(screen.getByLabelText("Marks"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Import historical question" }));

    expect(await screen.findByText("Historical question imported.")).toBeInTheDocument();
    const post = api.requests.find(
      (request) => request.method === "POST" && request.url.endsWith("/knowledge/questions"),
    );
    expect(post).toBeDefined();
    await expect(post?.json()).resolves.toEqual({
      marks: 2,
      page_number: 1,
      paper_code: "2025-I",
      question_number: "12",
      question_type: "multiple_choice",
      source_block_id: ids.block,
      source_document_id: ids.paper,
      text: "Which shape has exactly three sides?",
      year: 2025,
    });
    expect(screen.getByRole("heading", { name: "2025-I / Question 12" })).toBeInTheDocument();
  });

  it("imports optional historical metadata without defaults or answer inference", async () => {
    const api = fixtureApi();
    vi.stubGlobal("fetch", api.fetchMock);
    render(<KnowledgeStudio role="admin" />);

    await screen.findByRole("heading", { name: "Knowledge Studio" });
    await selectProvenance(paper.original_filename);
    fireEvent.change(screen.getByLabelText("Question number"), { target: { value: "13" } });
    fireEvent.change(screen.getByLabelText("Marks"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Media references"), {
      target: {
        value: "source://page/1/figure/A\nsource://page/1/table/1",
      },
    });
    fireEvent.change(screen.getByLabelText("Options"), {
      target: { value: "A. Triangle\nB. Square\nC. Circle" },
    });
    fireEvent.change(screen.getByLabelText("Answer"), { target: { value: "B" } });
    fireEvent.change(screen.getByLabelText("Marking guidance"), {
      target: { value: "Award two marks for the source-labelled answer B." },
    });
    fireEvent.change(screen.getByLabelText("Marking data (JSON object)"), {
      target: {
        value: JSON.stringify({
          alternative_answers: ["B"],
          criteria: [{ description: "Selects the square.", marks: 2 }],
        }),
      },
    });
    fireEvent.change(screen.getByLabelText("Question archetype"), {
      target: { value: "single_best_answer" },
    });
    fireEvent.change(screen.getByLabelText("Difficulty label"), {
      target: { value: "medium" },
    });
    fireEvent.change(screen.getByLabelText("Difficulty confidence"), {
      target: { value: "0.85" },
    });
    fireEvent.change(screen.getByLabelText("Difficulty source"), {
      target: { value: "reviewer_confirmed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import historical question" }));

    expect(await screen.findByText("Historical question imported.")).toBeInTheDocument();
    const post = api.requests.find(
      (request) => request.method === "POST" && request.url.endsWith("/knowledge/questions"),
    );
    await expect(post?.json()).resolves.toEqual({
      answer: "B",
      difficulty_confidence: 0.85,
      difficulty_label: "medium",
      difficulty_source: "reviewer_confirmed",
      marking_data: {
        alternative_answers: ["B"],
        criteria: [{ description: "Selects the square.", marks: 2 }],
      },
      marking_guidance: "Award two marks for the source-labelled answer B.",
      marks: 2,
      media_references: ["source://page/1/figure/A", "source://page/1/table/1"],
      options: ["A. Triangle", "B. Square", "C. Circle"],
      page_number: 1,
      paper_code: "2025-I",
      question_archetype: "single_best_answer",
      question_number: "13",
      question_type: "multiple_choice",
      source_block_id: ids.block,
      source_document_id: ids.paper,
      text: "Which shape has exactly three sides?",
      year: 2025,
    });
  });

  it("rejects malformed, unbounded, or incomplete metadata before import", async () => {
    const api = fixtureApi();
    vi.stubGlobal("fetch", api.fetchMock);
    render(<KnowledgeStudio role="admin" />);

    await screen.findByRole("heading", { name: "Knowledge Studio" });
    await selectProvenance(paper.original_filename);
    fireEvent.change(screen.getByLabelText("Question number"), { target: { value: "14" } });
    fireEvent.change(screen.getByLabelText("Marks"), { target: { value: "1" } });
    const markingData = screen.getByLabelText("Marking data (JSON object)");

    fireEvent.change(markingData, {
      target: { value: '{"criterion": globalThis.__unsafe_call__()}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import historical question" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Marking data must be a valid JSON object.",
    );

    fireEvent.change(markingData, {
      target: { value: JSON.stringify({ guidance: "é".repeat(33_000) }) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import historical question" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Marking data is limited to 64 KiB of UTF-8 JSON.",
    );

    fireEvent.change(markingData, { target: { value: '{"marks": 1}' } });
    fireEvent.change(screen.getByLabelText("Difficulty label"), {
      target: { value: "hard" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import historical question" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Difficulty label, confidence, and source must all be supplied or all left blank.",
    );

    expect(
      api.requests.filter(
        (request) => request.method === "POST" && request.url.endsWith("/knowledge/questions"),
      ),
    ).toHaveLength(0);
  });

  it("shows reviewer metadata as safe text and never exposes response vectors", async () => {
    const unsafeMarkup = '<img src=x onerror="globalThis.__unsafe_call__()">';
    const richQuestion = {
      ...question({
        answer: "B",
        difficulty_confidence: 0.85,
        difficulty_label: "medium",
        difficulty_source: "reviewer_confirmed",
        marking_data: {
          criteria: [{ description: unsafeMarkup, marks: 2 }],
        },
        marking_guidance: `Treat ${unsafeMarkup} as source text.`,
        media_references: ["source://page/1/figure/A"],
        options: ["A. Triangle", "B. Square"],
        question_archetype: "single_best_answer",
      }),
      raw_vector: ["raw-vector-secret"],
    } as HistoricalQuestion;
    const api = fixtureApi({ questions: [richQuestion] });
    vi.stubGlobal("fetch", api.fetchMock);
    const { container } = render(<KnowledgeStudio role="reviewer" />);

    const heading = await screen.findByRole("heading", { name: "Historical question metadata" });
    const metadata = heading.closest("section");
    expect(metadata).not.toBeNull();
    expect(within(metadata as HTMLElement).getByText("source://page/1/figure/A")).toBeInTheDocument();
    expect(within(metadata as HTMLElement).getByText("A. Triangle")).toBeInTheDocument();
    expect(within(metadata as HTMLElement).getByText("B. Square")).toBeInTheDocument();
    expect(within(metadata as HTMLElement).getByText("single_best_answer")).toBeInTheDocument();
    expect(within(metadata as HTMLElement).getByText("medium")).toBeInTheDocument();
    expect(within(metadata as HTMLElement).getByText("0.85")).toBeInTheDocument();
    expect(within(metadata as HTMLElement).getByText("reviewer_confirmed")).toBeInTheDocument();
    expect(metadata).toHaveTextContent(unsafeMarkup);
    expect(metadata?.querySelector("img, script")).toBeNull();
    expect(container).not.toHaveTextContent("raw-vector-secret");
  });

  it("supports chunk import, API-bounded filters, and empty pagination", async () => {
    const api = fixtureApi();
    vi.stubGlobal("fetch", api.fetchMock);
    render(<KnowledgeStudio role="admin" />);

    await screen.findByRole("heading", { name: "Knowledge Studio" });
    fireEvent.click(screen.getByRole("tab", { name: /Knowledge chunks/ }));
    expect(await screen.findByText("No knowledge chunks match these filters.")).toBeInTheDocument();
    expect(screen.getByLabelText("Records per page")).toHaveValue("25");
    expect(
      within(screen.getByLabelText("Records per page"))
        .getAllByRole("option")
        .map((option) => option.getAttribute("value")),
    ).toEqual(["10", "25", "50", "100"]);
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();

    await selectProvenance(syllabus.original_filename);
    fireEvent.change(screen.getByLabelText("Educational boundary"), {
      target: { value: "Geometry / triangles" },
    });
    fireEvent.change(screen.getByLabelText("Sequence"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Import knowledge chunk" }));

    expect(await screen.findByText("Knowledge chunk imported.")).toBeInTheDocument();
    const post = api.requests.find(
      (request) => request.method === "POST" && request.url.endsWith("/knowledge/chunks"),
    );
    await expect(post?.json()).resolves.toEqual({
      chunk_type: "explanation",
      educational_boundary: "Geometry / triangles",
      page_number: 1,
      sequence: 3,
      source_block_id: ids.block,
      source_document_id: ids.syllabus,
      text: "Which shape has exactly three sides?",
    });

    fireEvent.change(screen.getByLabelText("Records per page"), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => {
      const listRequests = api.requests.filter(
        (request) => request.method === "GET" && request.url.includes("/knowledge/chunks?"),
      );
      expect(listRequests.at(-1)?.url).toContain("limit=100");
      expect(listRequests.at(-1)?.url).toContain("offset=0");
    });
  });

  it("lets a reviewer classify through the current hierarchy and complete forward review", async () => {
    let currentQuestion = question();
    const requests: Request[] = [];
    const base = fixtureApi({ questions: [currentQuestion] });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = asRequest(input, init);
      requests.push(request.clone());
      const url = new URL(request.url);
      if (request.method === "GET" && url.pathname.endsWith("/knowledge/questions")) {
        return Response.json([currentQuestion]);
      }
      if (request.method === "PATCH" && url.pathname.endsWith("/classification")) {
        const body = (await request.json()) as components["schemas"]["KnowledgeClassificationRequest"];
        currentQuestion = question({
          classification: {
            competency_id: body.competency_id ?? null,
            learning_concept_id: body.learning_concept_id ?? null,
            skill_id: body.skill_id ?? null,
            sub_skill_id: body.sub_skill_id ?? null,
          },
          version: currentQuestion.version + 1,
        });
        return Response.json(currentQuestion);
      }
      if (request.method === "POST" && url.pathname.endsWith("/review")) {
        const body = (await request.json()) as components["schemas"]["KnowledgeReviewTransitionRequest"];
        currentQuestion = question({
          classification: currentQuestion.classification,
          review_state: body.target,
          version: currentQuestion.version + 1,
        });
        return Response.json(currentQuestion);
      }
      return base.fetchMock(request);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<KnowledgeStudio role="reviewer" />);

    expect(await screen.findByText("Import permission required")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Import historical question" })).not.toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "Immutable provenance" }),
    ).toBeInTheDocument();
    expect(screen.getByText(ids.block)).toBeInTheDocument();
    expect(screen.getByText("multilingual-e5-small")).toBeInTheDocument();
    expect(screen.getByText("sha256:fixture")).toBeInTheDocument();
    const metadataHeading = screen.getByRole("heading", {
      name: "Historical question metadata",
    });
    const metadata = metadataHeading.closest("section");
    expect(metadata).not.toBeNull();
    expect(within(metadata as HTMLElement).getAllByText("Not supplied").length).toBeGreaterThanOrEqual(
      7,
    );

    fireEvent.change(screen.getByLabelText("Competency"), { target: { value: ids.competency } });
    expect(screen.getByRole("option", { name: "S1 — Recognise polygons" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Skill"), { target: { value: ids.skill } });
    fireEvent.change(screen.getByLabelText("Sub-skill"), { target: { value: ids.subSkill } });
    fireEvent.change(screen.getByLabelText("Learning concept"), { target: { value: ids.concept } });
    fireEvent.click(screen.getByRole("button", { name: "Save classification" }));

    expect(await screen.findByText("Classification saved.")).toBeInTheDocument();
    const classificationRequest = requests.find(
      (request) => request.method === "PATCH" && request.url.endsWith("/classification"),
    );
    await expect(classificationRequest?.json()).resolves.toMatchObject({
      competency_id: ids.competency,
      expected_version: 4,
      learning_concept_id: ids.concept,
      skill_id: ids.skill,
      sub_skill_id: ids.subSkill,
    });

    fireEvent.click(screen.getByRole("button", { name: "Start review" }));
    expect(await screen.findByText("Review started.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mark reviewed" }));
    expect(await screen.findByText("Record marked reviewed.")).toBeInTheDocument();
    expect(screen.getByText("Final record — read-only")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save classification" })).not.toBeInTheDocument();

    const transitions = requests.filter(
      (request) => request.method === "POST" && request.url.endsWith("/review"),
    );
    await expect(transitions[0]?.json()).resolves.toEqual({ expected_version: 5, target: "in_review" });
    await expect(transitions[1]?.json()).resolves.toEqual({ expected_version: 6, target: "reviewed" });
  });

  it("allows only the forward in-review to rejected transition and then locks the record", async () => {
    let currentChunk = chunk({
      classification: {
        competency_id: ids.competency,
        learning_concept_id: null,
        skill_id: null,
        sub_skill_id: null,
      },
      review_state: "in_review",
      version: 2,
    });
    let reviewRequest: Request | undefined;
    const base = fixtureApi();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = asRequest(input, init);
      const url = new URL(request.url);
      if (request.method === "GET" && url.pathname.endsWith("/knowledge/chunks")) {
        return Response.json([currentChunk]);
      }
      if (request.method === "POST" && url.pathname.endsWith("/review")) {
        reviewRequest = request.clone();
        currentChunk = chunk({
          classification: currentChunk.classification,
          review_state: "rejected",
          version: 3,
        });
        return Response.json(currentChunk);
      }
      return base.fetchMock(request);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<KnowledgeStudio role="reviewer" />);

    await screen.findByRole("heading", { name: "Knowledge Studio" });
    fireEvent.click(screen.getByRole("tab", { name: /Knowledge chunks/ }));
    await screen.findByRole("heading", { name: "Geometry / triangles / Sequence 3" });
    fireEvent.click(screen.getByRole("button", { name: "Reject record" }));

    expect(await screen.findByText("Record rejected.")).toBeInTheDocument();
    expect(screen.getByText("Final record — read-only")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject record" })).not.toBeInTheDocument();
    await expect(reviewRequest?.json()).resolves.toEqual({ expected_version: 2, target: "rejected" });
  });

  it("refreshes the record after a 409 optimistic-concurrency conflict", async () => {
    const stale = question();
    const latest = question({
      classification: {
        competency_id: ids.competency,
        learning_concept_id: null,
        skill_id: null,
        sub_skill_id: null,
      },
      version: 5,
    });
    let itemRefreshes = 0;
    const base = fixtureApi({ questions: [stale] });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = asRequest(input, init);
      const url = new URL(request.url);
      if (request.method === "PATCH" && url.pathname.endsWith("/classification")) {
        return Response.json(
          {
            detail: {
              actual_version: 5,
              code: "concurrent_knowledge_modification",
              expected_version: 4,
            },
          },
          { status: 409 },
        );
      }
      if (request.method === "GET" && url.pathname.endsWith(`/questions/${ids.question}`)) {
        itemRefreshes += 1;
        return Response.json(latest);
      }
      return base.fetchMock(request);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<KnowledgeStudio role="reviewer" />);

    await screen.findByRole("button", { name: "Save classification" });
    fireEvent.change(screen.getByLabelText("Competency"), { target: { value: ids.competency } });
    fireEvent.click(screen.getByRole("button", { name: "Save classification" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Conflict detected");
    expect(alert).toHaveTextContent("latest version was loaded");
    expect(await screen.findByText("Version 5")).toBeInTheDocument();
    expect(itemRefreshes).toBe(1);
  });

  it("shows retryable list errors and explicit API permission denial", async () => {
    let listAttempts = 0;
    const base = fixtureApi();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = asRequest(input, init);
      const url = new URL(request.url);
      if (
        request.method === "GET" &&
        url.pathname.endsWith("/knowledge/questions") &&
        !url.searchParams.has("review_state")
      ) {
        listAttempts += 1;
        if (listAttempts === 1) {
          return Response.json({ detail: { code: "service_unavailable" } }, { status: 503 });
        }
        if (listAttempts === 2) {
          return Response.json({ detail: { code: "permission_denied" } }, { status: 403 });
        }
        return Response.json([]);
      }
      return base.fetchMock(request);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<KnowledgeStudio role="admin" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Knowledge records could not be loaded");
    fireEvent.click(screen.getByRole("button", { name: "Retry loading records" }));
    expect(await screen.findByText("Knowledge read permission required")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry loading records" }));
    expect(await screen.findByText("No historical questions match these filters.")).toBeInTheDocument();
    expect(listAttempts).toBe(3);
  });

  it("has no automated accessibility violations", async () => {
    const api = fixtureApi({
      chunks: [chunk()],
      questions: [
        question({
          answer: "B",
          difficulty_confidence: 0.85,
          difficulty_label: "medium",
          difficulty_source: "reviewer_confirmed",
          marking_data: {
            criteria: [{ description: "Selects the square.", marks: 2 }],
          },
          marking_guidance: "Award two marks for B.",
          media_references: ["source://page/1/figure/A"],
          options: ["A. Triangle", "B. Square"],
          question_archetype: "single_best_answer",
        }),
      ],
    });
    vi.stubGlobal("fetch", api.fetchMock);
    const { container } = render(<KnowledgeStudio role="reviewer" />);
    await screen.findByRole("heading", { name: "2025-I / Question 12" });

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});
