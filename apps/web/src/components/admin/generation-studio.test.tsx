import type { components } from "@exam-guru/api-client";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GenerationStudio } from "./generation-studio";

type Exam = components["schemas"]["ExamConfigurationResponse"];
type Medium = components["schemas"]["MediumResponse"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type Blueprint = components["schemas"]["PaperBlueprintResponse"];
type BlueprintSummary = components["schemas"]["PaperBlueprintSummaryResponse"];
type KnowledgeChunk = components["schemas"]["KnowledgeChunkResponse"];
type HistoricalQuestion = components["schemas"]["HistoricalQuestionResponse"];
type GenerationRun = components["schemas"]["GenerationRunResponse"];
type GenerationRunSummary = components["schemas"]["GenerationRunSummaryResponse"];
type GenerationAttempt = components["schemas"]["GenerationAttemptResponse"];
type GenerationJob = components["schemas"]["GenerationJobResponse"];
type GenerationRequest = components["schemas"]["GenerationRunCreateRequest"];

const ids = {
  actor: "00000000-0000-0000-0000-000000000901",
  blueprint: "00000000-0000-0000-0000-000000000701",
  chunk: "00000000-0000-0000-0000-000000000501",
  competency: "00000000-0000-0000-0000-000000000401",
  curriculum: "00000000-0000-0000-0000-000000000101",
  draftChunk: "00000000-0000-0000-0000-000000000503",
  exam: "00000000-0000-0000-0000-000000000201",
  failedRun: "00000000-0000-0000-0000-000000000802",
  job: "00000000-0000-0000-0000-000000000801",
  medium: "00000000-0000-0000-0000-000000000301",
  mismatchChunk: "00000000-0000-0000-0000-000000000502",
  otherCompetency: "00000000-0000-0000-0000-000000000404",
  otherCurriculum: "00000000-0000-0000-0000-000000000102",
  question: "00000000-0000-0000-0000-000000000601",
  run: "00000000-0000-0000-0000-000000000803",
  skill: "00000000-0000-0000-0000-000000000402",
} as const;

const now = "2026-08-24T09:30:00Z";
const later = "2026-08-24T09:30:01Z";
const hash = (value: string) => `sha256:${value.repeat(64).slice(0, 64)}`;

const exam = {
  active: true,
  code: "G5-SCH",
  created_at: now,
  grade: 5,
  id: ids.exam,
  name: "Grade 5 Scholarship",
  updated_at: now,
} satisfies Exam;

const medium = {
  active: true,
  code: "en",
  created_at: now,
  id: ids.medium,
  name: "English",
  updated_at: now,
} satisfies Medium;

const curriculum = {
  active: true,
  code: "G5-EN-2026",
  created_at: now,
  exam_configuration_id: ids.exam,
  id: ids.curriculum,
  medium_id: ids.medium,
  title: "Grade 5 English 2026",
  updated_at: now,
} satisfies Curriculum;

const otherCurriculum = {
  ...curriculum,
  code: "G5-EN-2027",
  id: ids.otherCurriculum,
  title: "Grade 5 English 2027",
} satisfies Curriculum;

const taxonomyTarget = {
  competency_id: ids.competency,
  learning_concept_id: null,
  skill_id: ids.skill,
  sub_skill_id: null,
};

const slot = {
  archetype: "single_best_answer",
  difficulty: "medium" as const,
  evidence: {
    baseline_backtest_score: null,
    baseline_score: 100,
    baseline_version: "syllabus-balanced-v1",
    config_version: "grade5-blueprint-config-v1",
    evidence_refs: ["curriculum:reviewed-taxonomy"],
    forecast_backtest_score: null,
    forecast_score: null,
    forecast_version: null,
    minimum_backtest_improvement: 1,
  },
  generation_constraints: {
    answer_requirements: ["Provide one unambiguous answer."],
    curriculum_scope: {
      curriculum_version_id: ids.curriculum,
      grade: 5,
      medium: "en",
    },
    diversity_key: "A:1:number-skill",
    exact_marks: 2,
    instructions: ["Use age-appropriate Grade 5 language."],
    required_archetype: "single_best_answer",
    required_difficulty: "medium" as const,
    required_question_type: "multiple_choice" as const,
    response_language: "en",
    retrieval_query_hints: ["reviewed number concepts"],
    taxonomy_target: taxonomyTarget,
    uniqueness: {
      forbid_duplicate_stems: true,
      forbid_verbatim_sources: true,
      max_similarity_basis_points: 8500,
      minimum_distinct_contexts: 1,
    },
  },
  marks: 2,
  ordinal: 1,
  paper_code: "GEN-01",
  question_type: "multiple_choice" as const,
  rationale: {
    effective_priority_score: 100,
    priority_mode: "baseline_only" as const,
    summary: "Syllabus-balanced baseline selected.",
  },
  section_id: "A",
  section_ordinal: 1,
  section_title: "Selection",
  slot_id: "GEN-01-A-001",
  taxonomy_target: taxonomyTarget,
};

const requirement = {
  allowed_section_ids: ["A"],
  generation_instructions: ["Use a familiar setting."],
  maximum_slots: 1,
  minimum_slots: 1,
  priority: {
    baseline_backtest_score: null,
    baseline_evidence_refs: ["curriculum:reviewed-taxonomy"],
    baseline_score: 100,
    baseline_version: "syllabus-balanced-v1",
    forecast_backtest_score: null,
    forecast_evidence_refs: [],
    forecast_score: null,
    forecast_version: null,
    minimum_backtest_improvement: 1,
  },
  retrieval_query_hints: ["reviewed number concepts"],
  target: taxonomyTarget,
};

const blueprint = {
  algorithm_version: "deterministic-blueprint-v1",
  analytics_run_id: null,
  blueprint: {
    curriculum_scope: {
      curriculum_version_id: ids.curriculum,
      grade: 5,
      medium: "en",
    },
    difficulty_allocations: [{ difficulty: "medium" as const, exact_marks: 2, exact_slots: 1 }],
    paper_code: "GEN-01",
    question_type_allocations: [
      {
        archetypes: ["single_best_answer"],
        exact_marks: 2,
        exact_slots: 1,
        question_type: "multiple_choice" as const,
      },
    ],
    sections: [{ marks: 2, section_id: "A", slot_count: 1, title: "Selection" }],
    seed: 17,
    slots: [slot],
    taxonomy_requirements: [requirement],
    title: "Generation practice paper",
    total_marks: 2,
    version: {
      algorithm_version: "deterministic-blueprint-v1",
      blueprint_id: "bp_1234567890abcdef12345678",
      config_version: "grade5-blueprint-config-v1",
      input_fingerprint: "f".repeat(64),
      schema_version: "paper-blueprint-v1",
    },
  },
  blueprint_id: "bp_1234567890abcdef12345678",
  config_version: "grade5-blueprint-config-v1",
  created_at: now,
  created_by: ids.actor,
  curriculum_version_id: ids.curriculum,
  deduplicated: false,
  id: ids.blueprint,
  input_fingerprint: hash("f"),
  result_fingerprint: hash("1"),
  schema_version: "paper-blueprint-v1",
  seed: 17,
  slot_count: 1,
  specification: {
    config_version: "grade5-blueprint-config-v1",
    curriculum_scope: {
      curriculum_version_id: ids.curriculum,
      grade: 5,
      medium: "en",
    },
    difficulty_allocations: [{ difficulty: "medium" as const, exact_marks: 2, exact_slots: 1 }],
    generation_policy: {
      answer_requirements: ["Provide one unambiguous answer."],
      instructions: ["Use age-appropriate Grade 5 language."],
      response_language: "en",
      retrieval_query_hints: ["reviewed curriculum"],
      uniqueness: {
        forbid_duplicate_stems: true,
        forbid_verbatim_sources: true,
        max_similarity_basis_points: 8500,
        minimum_distinct_contexts: 1,
      },
    },
    paper_code: "GEN-01",
    question_type_allocations: [
      {
        archetypes: ["single_best_answer"],
        exact_marks: 2,
        exact_slots: 1,
        question_type: "multiple_choice" as const,
      },
    ],
    sections: [
      {
        allowed_difficulties: ["medium" as const],
        allowed_marks_per_slot: [2],
        allowed_question_types: ["multiple_choice" as const],
        allowed_taxonomy_targets: [],
        marks: 2,
        question_count: 1,
        retrieval_query_hints: ["selection"],
        section_id: "A",
        title: "Selection",
      },
    ],
    taxonomy_requirements: [requirement],
    title: "Generation practice paper",
    total_marks: 2,
  },
  specification_fingerprint: hash("2"),
  taxonomy_snapshot: [
    {
      active: true,
      code: "C1",
      curriculum_version_id: ids.curriculum,
      id: ids.competency,
      level: "competency" as const,
      parent_id: null,
      review_state: "reviewed" as const,
      reviewed_at: now,
      reviewed_by: ids.actor,
      title: "Number competency",
    },
    {
      active: true,
      code: "S1",
      curriculum_version_id: ids.curriculum,
      id: ids.skill,
      level: "skill" as const,
      parent_id: ids.competency,
      review_state: "reviewed" as const,
      reviewed_at: now,
      reviewed_by: ids.actor,
      title: "Number skill",
    },
  ],
  total_marks: 2,
} satisfies Blueprint;

function blueprintSummary(value: Blueprint): BlueprintSummary {
  return {
    algorithm_version: value.algorithm_version,
    analytics_run_id: value.analytics_run_id,
    blueprint_id: value.blueprint_id,
    config_version: value.config_version,
    created_at: value.created_at,
    created_by: value.created_by,
    curriculum_version_id: value.curriculum_version_id,
    id: value.id,
    input_fingerprint: value.input_fingerprint,
    paper_code: value.specification.paper_code,
    result_fingerprint: value.result_fingerprint,
    schema_version: value.schema_version,
    seed: value.seed,
    slot_count: value.slot_count,
    specification_fingerprint: value.specification_fingerprint,
    title: value.specification.title,
    total_marks: value.total_marks,
  };
}

function chunkFixture(
  id: string,
  overrides: Partial<KnowledgeChunk> = {},
): KnowledgeChunk {
  return {
    chunk_type: "explanation",
    classification: {
      competency_id: ids.competency,
      learning_concept_id: null,
      skill_id: ids.skill,
      sub_skill_id: null,
    },
    created_at: now,
    curriculum_version_id: ids.curriculum,
    deduplicated: false,
    educational_boundary: `Even number boundary ${id.slice(-3)}`,
    embedding_configurations: [],
    embedding_status: "not_embedded",
    id,
    provenance: {
      page_number: 1,
      source_block_id: "00000000-0000-0000-0000-000000000991",
      source_document_id: "00000000-0000-0000-0000-000000000990",
    },
    review_state: "reviewed",
    sequence: 1,
    text: `Reviewed chunk text ${id.slice(-3)}`,
    updated_at: now,
    version: 3,
    ...overrides,
  };
}

const matchingChunk = chunkFixture(ids.chunk);
const mismatchChunk = chunkFixture(ids.mismatchChunk, {
  classification: {
    competency_id: ids.otherCompetency,
    learning_concept_id: null,
    skill_id: null,
    sub_skill_id: null,
  },
  educational_boundary: "Outside exact slot taxonomy",
  text: "Reviewed but taxonomy-mismatched source text",
});
const draftChunk = chunkFixture(ids.draftChunk, {
  educational_boundary: "Draft context must not load",
  review_state: "draft",
});
const crossCurriculumChunk = chunkFixture("00000000-0000-0000-0000-000000000504", {
  curriculum_version_id: ids.otherCurriculum,
  educational_boundary: "Cross-curriculum context must not load",
});

const matchingQuestion = {
  answer: "B",
  classification: {
    competency_id: ids.competency,
    learning_concept_id: null,
    skill_id: ids.skill,
    sub_skill_id: null,
  },
  created_at: now,
  curriculum_version_id: ids.curriculum,
  deduplicated: false,
  difficulty_confidence: 0.9,
  difficulty_label: "medium" as const,
  difficulty_source: "reviewer",
  embedding_configurations: [],
  embedding_status: "not_embedded" as const,
  id: ids.question,
  marking_data: null,
  marking_guidance: "Award two marks for B.",
  marks: 2,
  media_references: null,
  options: ["A. Odd", "B. Even"],
  paper_code: "PAST-2025",
  provenance: {
    page_number: 2,
    source_block_id: "00000000-0000-0000-0000-000000000993",
    source_document_id: "00000000-0000-0000-0000-000000000992",
  },
  question_archetype: "single_best_answer",
  question_number: "4",
  question_type: "multiple_choice" as const,
  review_state: "reviewed" as const,
  text: "Which number is even?",
  updated_at: now,
  version: 4,
  year: 2025,
} satisfies HistoricalQuestion;

const candidate = {
  answer: {
    accepted_responses: [],
    correct_option_id: "B",
    explanation: "The reviewed context supports option B.",
  },
  marking: {
    criteria: [
      {
        criterion_id: "grounded-answer",
        description: "Selects the context-grounded answer.",
        marks: 2,
      },
    ],
    total_marks: 2,
  },
  options: [
    { option_id: "A", text: "The unsupported choice" },
    { option_id: "B", text: "The supported choice" },
  ],
  question_type: "multiple_choice",
  stem: "Which response is supported by the reviewed context?",
};

const succeededRun = {
  attempt_count: 2,
  blueprint_id: blueprint.blueprint_id,
  blueprint_slot: slot,
  blueprint_version: blueprint.blueprint_id,
  budgets: {
    max_attempts: 3,
    max_cost_microusd: 100000,
    max_input_tokens: 12000,
    max_output_tokens: 2048,
  },
  candidate,
  completed_at: later,
  context: [
    {
      context_id: `knowledge_chunk:${ids.chunk}`,
      provenance: {
        chunk_id: ids.chunk,
        page_number: 1,
        source_block_id: "00000000-0000-0000-0000-000000000991",
        source_document_id: "00000000-0000-0000-0000-000000000990",
        source_version: hash("a"),
      },
      record_id: ids.chunk,
      record_kind: "knowledge_chunk",
      record_version: 3,
      taxonomy: taxonomyTarget,
      text: "Four is an even number.",
      trust: "untrusted_data",
    },
  ],
  cost_microusd: 321,
  created_at: now,
  created_by: ids.actor,
  curriculum_version_id: ids.curriculum,
  disposition: "requires_validation" as const,
  failure_code: null,
  generation_parameters: {
    max_output_tokens: 512,
    seed: 17,
    temperature: 0,
  },
  id: ids.run,
  input_tokens: 25,
  latency_ms: 41,
  model: "fixture-model",
  model_version: "2026-01",
  output_tokens: 32,
  paper_blueprint_id: ids.blueprint,
  pricing_version: "deterministic-pricing-v1",
  prompt_id: "question-generation",
  prompt_version: "1.0.0",
  provider: "deterministic-fake",
  provider_version: "1.0.0",
  request_fingerprint: hash("r"),
  retrieval_version: "reviewed-selected-context-v1",
  retry_of_run_id: ids.failedRun,
  schema_version: "question.v1",
  slot_id: slot.slot_id,
  started_at: now,
  status: "succeeded" as const,
  total_tokens: 57,
  version: 4,
} satisfies GenerationRun;

const failedRun = {
  ...succeededRun,
  attempt_count: 1,
  candidate: null,
  cost_microusd: 0,
  disposition: null,
  failure_code: "provider_invalid_response",
  id: ids.failedRun,
  input_tokens: 0,
  output_tokens: 0,
  retry_of_run_id: null,
  status: "failed" as const,
  total_tokens: 0,
  version: 2,
} satisfies GenerationRun;

const pendingRun = {
  ...succeededRun,
  attempt_count: 0,
  candidate: null,
  completed_at: null,
  cost_microusd: 0,
  disposition: null,
  failure_code: null,
  input_tokens: 0,
  latency_ms: 0,
  output_tokens: 0,
  retry_of_run_id: null,
  started_at: null,
  status: "pending" as const,
  total_tokens: 0,
  version: 1,
} satisfies GenerationRun;

const runningRun = {
  ...pendingRun,
  started_at: now,
  status: "running" as const,
  version: 2,
} satisfies GenerationRun;

function runSummary(value: GenerationRun): GenerationRunSummary {
  return {
    attempt_count: value.attempt_count,
    completed_at: value.completed_at,
    cost_microusd: value.cost_microusd,
    created_at: value.created_at,
    created_by: value.created_by,
    curriculum_version_id: value.curriculum_version_id,
    disposition: value.disposition,
    failure_code: value.failure_code,
    id: value.id,
    latency_ms: value.latency_ms,
    model: value.model,
    paper_blueprint_id: value.paper_blueprint_id,
    prompt_version: value.prompt_version,
    provider: value.provider,
    request_fingerprint: value.request_fingerprint,
    retry_of_run_id: value.retry_of_run_id,
    slot_id: value.slot_id,
    started_at: value.started_at,
    status: value.status,
    total_tokens: value.total_tokens,
    version: value.version,
  };
}

const attempts = [
  {
    accounting_known: false,
    attempt_number: 1,
    candidate: null,
    completed_at: now,
    cost_microusd: null,
    disposition: null,
    failure_code: "timeout",
    generation_run_id: ids.run,
    id: "00000000-0000-0000-0000-000000000811",
    input_tokens: null,
    latency_ms: 17,
    output_tokens: null,
    provider_idempotency_key: `generation-${ids.run.replaceAll("-", "")}`,
    retry_after_ms: 250,
    retry_of_attempt_id: null,
    started_at: now,
    status: "failed" as const,
    total_tokens: null,
  },
  {
    accounting_known: true,
    attempt_number: 2,
    candidate,
    completed_at: later,
    cost_microusd: 321,
    disposition: "requires_validation" as const,
    failure_code: null,
    generation_run_id: ids.run,
    id: "00000000-0000-0000-0000-000000000812",
    input_tokens: 25,
    latency_ms: 24,
    output_tokens: 32,
    provider_idempotency_key: `generation-${ids.run.replaceAll("-", "")}`,
    retry_after_ms: null,
    retry_of_attempt_id: "00000000-0000-0000-0000-000000000811",
    started_at: now,
    status: "succeeded" as const,
    total_tokens: 57,
  },
] satisfies GenerationAttempt[];

const queuedJob = {
  claimed_at: null,
  completed_at: null,
  created_at: now,
  created_by: ids.actor,
  curriculum_version_id: ids.curriculum,
  deduplicated: false,
  failure_code: null,
  generation_run_id: ids.run,
  id: ids.job,
  queue_message_id: "generation-message-1",
  status: "queued" as const,
  version: 1,
} satisfies GenerationJob;

function asRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(input, init);
}

type ErrorReply = { code: string; status: number };
type FixtureOptions = {
  blueprints?: BlueprintSummary[];
  chunks?: KnowledgeChunk[];
  createReplies?: ErrorReply[];
  curricula?: Curriculum[];
  attemptsByRun?: Record<string, GenerationAttempt[]>;
  beforeBlueprintDetail?: () => Promise<void>;
  beforeRetry?: () => Promise<void>;
  beforeRunDetail?: (requestNumber: number) => Promise<void>;
  pollJob?: GenerationJob;
  questions?: HistoricalQuestion[];
  retryReplies?: ErrorReply[];
  runDetailReplies?: GenerationRun[];
  runDetails?: Record<string, GenerationRun>;
  runs?: GenerationRunSummary[];
  workspaceReplies?: ErrorReply[];
};

function fixtureApi(options: FixtureOptions = {}) {
  const requests: Request[] = [];
  const createReplies = [...(options.createReplies ?? [])];
  const retryReplies = [...(options.retryReplies ?? [])];
  const workspaceReplies = [...(options.workspaceReplies ?? [])];
  const runDetails = options.runDetails ?? {};
  let runDetailRequests = 0;

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    requests.push(request.clone());
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === "GET" && path.endsWith("/exam-configurations")) {
      return Response.json([exam]);
    }
    if (request.method === "GET" && path.endsWith("/media")) {
      return Response.json([medium]);
    }
    if (request.method === "GET" && path.endsWith("/curriculum-versions")) {
      const reply = workspaceReplies.shift();
      return reply
        ? Response.json({ detail: { code: reply.code } }, { status: reply.status })
        : Response.json(options.curricula ?? [curriculum]);
    }
    if (request.method === "GET" && path.endsWith(`/blueprints/${ids.blueprint}`)) {
      await options.beforeBlueprintDetail?.();
      return Response.json(blueprint);
    }
    if (request.method === "GET" && path.endsWith("/blueprints")) {
      return Response.json(options.blueprints ?? [blueprintSummary(blueprint)]);
    }
    if (request.method === "GET" && path.endsWith("/knowledge/chunks")) {
      return Response.json(options.chunks ?? [matchingChunk, mismatchChunk]);
    }
    if (request.method === "GET" && path.endsWith("/knowledge/questions")) {
      return Response.json(options.questions ?? [matchingQuestion]);
    }
    if (request.method === "GET" && path.includes("/generation-jobs/")) {
      return Response.json(
        options.pollJob ?? { ...queuedJob, completed_at: later, status: "succeeded", version: 3 },
      );
    }
    if (request.method === "GET" && path.endsWith("/attempts")) {
      const runId = path.split("/").at(-2) ?? "";
      return Response.json(
        options.attemptsByRun?.[runId] ?? (runId === ids.run ? attempts : [attempts[0]]),
      );
    }
    if (request.method === "GET" && /\/generation-runs\/[^/]+$/.test(path)) {
      const runId = path.split("/").at(-1) ?? "";
      const requestNumber = runDetailRequests++;
      await options.beforeRunDetail?.(requestNumber);
      return Response.json(
        options.runDetailReplies?.[requestNumber] ?? runDetails[runId] ?? succeededRun,
      );
    }
    if (request.method === "GET" && path.endsWith("/generation-runs")) {
      return Response.json(options.runs ?? []);
    }
    if (request.method === "POST" && path.endsWith("/retry")) {
      await options.beforeRetry?.();
      const reply = retryReplies.shift();
      return reply
        ? Response.json({ detail: { code: reply.code } }, { status: reply.status })
        : Response.json(queuedJob, { status: 202 });
    }
    if (request.method === "POST" && path.endsWith("/generation-runs")) {
      const reply = createReplies.shift();
      return reply
        ? Response.json({ detail: { code: reply.code } }, { status: reply.status })
        : Response.json(queuedJob, { status: 202 });
    }
    return Response.json({ detail: { code: "unexpected_request" } }, { status: 500 });
  });

  return { fetchMock, requests };
}

async function renderLoaded(
  role: "admin" | "reviewer" = "admin",
  options: FixtureOptions = {},
) {
  const fixture = fixtureApi(options);
  vi.stubGlobal("fetch", fixture.fetchMock);
  const view = render(<GenerationStudio role={role} />);
  await screen.findByRole("heading", { level: 1, name: "Generation Studio" });
  await waitFor(() =>
    expect(screen.queryByText("Loading generation workspace…")).not.toBeInTheDocument(),
  );
  if ((options.curricula ?? [curriculum]).length) {
    await screen.findByLabelText("Immutable blueprint");
    await waitFor(() =>
      expect(screen.queryByText("Loading generation data…")).not.toBeInTheDocument(),
    );
    if ((options.blueprints ?? [blueprintSummary(blueprint)]).length && !options.beforeBlueprintDetail) {
      await waitFor(() =>
        expect(screen.getByLabelText("Exact blueprint slot")).toHaveValue(slot.slot_id),
      );
    }
  }
  return { ...fixture, container: view.container };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("GenerationStudio", () => {
  it("selects an active immutable blueprint and exact slot, then submits IDs only with a bounded generated idempotency key", async () => {
    const { requests } = await renderLoaded("admin", {
      chunks: [matchingChunk, mismatchChunk, draftChunk, crossCurriculumChunk],
    });

    expect(screen.getByLabelText("Active Grade 5 curriculum")).toHaveValue(ids.curriculum);
    expect(screen.getByLabelText("Immutable blueprint")).toHaveValue(ids.blueprint);
    expect(screen.getByLabelText("Exact blueprint slot")).toHaveValue(slot.slot_id);
    expect(screen.getByText("Immutable blueprint snapshot")).toBeVisible();
    expect(screen.getByText(slot.slot_id)).toBeVisible();

    const matching = screen.getByRole("checkbox", {
      name: `Select knowledge chunk ${matchingChunk.id}`,
    });
    const question = screen.getByRole("checkbox", {
      name: `Select historical question ${matchingQuestion.id}`,
    });
    const mismatch = screen.getByRole("checkbox", {
      name: `Select knowledge chunk ${mismatchChunk.id}`,
    });
    expect(matching).toBeEnabled();
    expect(question).toBeEnabled();
    expect(mismatch).toBeDisabled();
    expect(screen.getByText("Taxonomy does not match the exact slot")).toBeVisible();
    expect(screen.queryByText(draftChunk.educational_boundary)).not.toBeInTheDocument();
    expect(screen.queryByText(crossCurriculumChunk.educational_boundary)).not.toBeInTheDocument();

    const contextGets = requests.filter((request) =>
      new URL(request.url).pathname.includes("/knowledge/"),
    );
    expect(contextGets).toHaveLength(2);
    for (const request of contextGets) {
      const url = new URL(request.url);
      expect(url.pathname).toContain(`/curricula/${ids.curriculum}/knowledge/`);
      expect(url.searchParams.get("review_state")).toBe("reviewed");
    }

    fireEvent.click(matching);
    fireEvent.click(question);
    fireEvent.click(screen.getByRole("button", { name: "Create generation run" }));

    expect(await screen.findByText("Generation run queued.")).toBeVisible();
    const post = requests.find(
      (request) =>
        request.method === "POST" && new URL(request.url).pathname.endsWith("/generation-runs"),
    );
    expect(post).toBeDefined();
    const body = (await post?.json()) as GenerationRequest;
    expect(body).toEqual({
      historical_question_ids: [ids.question],
      knowledge_chunk_ids: [ids.chunk],
      paper_blueprint_id: ids.blueprint,
      slot_id: slot.slot_id,
    });
    expect(Object.keys(body).sort()).toEqual([
      "historical_question_ids",
      "knowledge_chunk_ids",
      "paper_blueprint_id",
      "slot_id",
    ]);
    const idempotencyKey = post?.headers.get("Idempotency-Key") ?? "";
    expect(idempotencyKey).toMatch(/^generation-[A-Za-z0-9-]+$/);
    expect(idempotencyKey.length).toBeGreaterThan(20);
    expect(idempotencyKey.length).toBeLessThanOrEqual(128);
    expect(idempotencyKey).not.toMatch(/\s/);
    const wirePayload = JSON.stringify(body);
    for (const forbidden of [
      matchingChunk.text,
      "vector",
      "provider",
      "model",
      "prompt",
      "pricing",
      "credential",
      "api_key",
    ]) {
      expect(wirePayload).not.toContain(forbidden);
    }
  });

  it("keeps an in-flight immutable blueprint load when its already-selected option is confirmed", async () => {
    let releaseBlueprint: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      releaseBlueprint = resolve;
    });
    await renderLoaded("admin", { beforeBlueprintDetail: () => gate });
    const blueprintSelect = screen.getByLabelText("Immutable blueprint");

    fireEvent.change(blueprintSelect, { target: { value: ids.blueprint } });
    releaseBlueprint?.();

    await waitFor(() =>
      expect(screen.getByLabelText("Exact blueprint slot")).toHaveValue(slot.slot_id),
    );
  });

  it("requires 1 to 16 matching references and disables additional choices at the bound", async () => {
    const manyChunks = Array.from({ length: 17 }, (_, index) =>
      chunkFixture(`00000000-0000-0000-0000-${String(index + 1).padStart(12, "0")}`),
    );
    const { requests } = await renderLoaded("admin", { chunks: manyChunks, questions: [] });

    fireEvent.click(screen.getByRole("button", { name: "Create generation run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Select at least one reviewed reference",
    );
    expect(requests.filter((request) => request.method === "POST")).toHaveLength(0);

    const choices = screen.getAllByRole("checkbox", { name: /Select knowledge chunk/ });
    for (const choice of choices.slice(0, 16)) fireEvent.click(choice);
    expect(screen.getByText("16 of 16 references selected")).toBeVisible();
    expect(choices[16]).toBeDisabled();
    fireEvent.click(choices[0]);
    expect(choices[16]).toBeEnabled();
  });

  it("gives a reviewer read-only inspection of versions, snapshots, untrusted provenance, attempts, accounting, and the unvalidated candidate", async () => {
    await renderLoaded("reviewer", {
      runDetails: { [ids.run]: succeededRun },
      runs: [runSummary(succeededRun)],
    });

    expect(screen.getByText("Reviewer read access")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Reviewer read-only mode" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Create generation run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry failed run" })).not.toBeInTheDocument();
    for (const checkbox of screen.getAllByRole("checkbox", { name: /Select knowledge chunk/ })) {
      expect(checkbox).toBeDisabled();
    }

    const overview = await screen.findByRole("region", { name: "Generation run overview" });
    expect(overview).toHaveTextContent("Succeeded");
    expect(overview).toHaveTextContent("Version 4");
    expect(overview).toHaveTextContent(ids.failedRun);
    expect(overview).toHaveTextContent(succeededRun.request_fingerprint);

    const versions = screen.getByRole("region", { name: "Generation configuration versions" });
    for (const value of [
      "question-generation",
      "1.0.0",
      "deterministic-fake",
      "fixture-model",
      "2026-01",
      "reviewed-selected-context-v1",
      "question.v1",
      "deterministic-pricing-v1",
    ]) {
      expect(versions).toHaveTextContent(value);
    }
    expect(screen.getByRole("region", { name: "Generation budgets and parameters" })).toHaveTextContent(
      "max_input_tokens",
    );

    const snapshot = screen.getByRole("region", { name: "Immutable blueprint and slot snapshot" });
    expect(snapshot).toHaveTextContent(blueprint.blueprint_id);
    expect(snapshot).toHaveTextContent(slot.slot_id);
    expect(snapshot).toHaveTextContent("single_best_answer");
    expect(snapshot).toHaveTextContent(ids.skill);

    const context = screen.getByRole("region", { name: "Persisted generation context" });
    expect(context).toHaveTextContent("Untrusted source data");
    expect(context).toHaveTextContent("Four is an even number.");
    expect(context).toHaveTextContent(ids.chunk);
    expect(context).toHaveTextContent("source_version");
    expect(context).toHaveTextContent(ids.competency);
    expect(context).toHaveTextContent(ids.skill);

    const attemptList = screen.getByRole("region", { name: "Provider attempts" });
    expect(attemptList).toHaveTextContent("Attempt 1");
    expect(attemptList).toHaveTextContent("timeout");
    expect(attemptList).toHaveTextContent("250 ms");
    expect(attemptList).toHaveTextContent("Attempt 2");
    expect(attemptList).toHaveTextContent("57");
    expect(attemptList).toHaveTextContent("321 microusd");

    const candidatePanel = screen.getByRole("region", { name: "Generated candidate" });
    expect(candidatePanel).toHaveTextContent(candidate.stem);
    expect(candidatePanel).toHaveTextContent("The supported choice");
    expect(candidatePanel).toHaveTextContent("The reviewed context supports option B.");
    expect(candidatePanel).toHaveTextContent("Selects the context-grounded answer.");
    expect(screen.getByText("REQUIRES VALIDATION")).toBeVisible();
    expect(screen.getByText(/No publish action is available/i)).toBeVisible();
    expect(screen.queryByRole("button", { name: /publish/i })).not.toBeInTheDocument();
  });

  it("renders a running durable state without inventing a candidate or retry action", async () => {
    await renderLoaded("admin", {
      attemptsByRun: { [ids.run]: [] },
      runDetails: { [ids.run]: runningRun },
      runs: [runSummary(runningRun)],
    });

    const overview = await screen.findByRole("region", { name: "Generation run overview" });
    expect(overview).toHaveTextContent("Running");
    expect(overview).toHaveTextContent("The worker is running this durable request");
    expect(screen.getByRole("region", { name: "Generated candidate" })).toHaveTextContent(
      "No candidate available",
    );
    expect(screen.queryByRole("button", { name: "Retry failed run" })).not.toBeInTheDocument();
  });

  it("allows an administrator to explicitly retry only a failed run with a new bounded key and no request body", async () => {
    const { requests } = await renderLoaded("admin", {
      runDetails: { [ids.failedRun]: failedRun },
      runs: [runSummary(failedRun)],
    });

    expect(await screen.findByText("provider_invalid_response")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Retry failed run" }));
    expect(await screen.findByText("Failed run retry queued.")).toBeVisible();

    const retry = requests.find(
      (request) => request.method === "POST" && new URL(request.url).pathname.endsWith("/retry"),
    );
    expect(retry).toBeDefined();
    expect(await retry?.text()).toBe("");
    const key = retry?.headers.get("Idempotency-Key") ?? "";
    expect(key).toMatch(/^generation-retry-[A-Za-z0-9-]+$/);
    expect(key.length).toBeLessThanOrEqual(128);
    expect(key).not.toMatch(/\s/);
  });

  it("blocks duplicate explicit retry clicks while the first retry request is in flight", async () => {
    let releaseRetry: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      releaseRetry = resolve;
    });
    const { requests } = await renderLoaded("admin", {
      beforeRetry: () => gate,
      runDetails: { [ids.failedRun]: failedRun },
      runs: [runSummary(failedRun)],
    });
    const retry = await screen.findByRole("button", { name: "Retry failed run" });

    fireEvent.click(retry);
    expect(retry).toBeDisabled();
    fireEvent.click(retry);
    expect(
      requests.filter(
        (request) => request.method === "POST" && new URL(request.url).pathname.endsWith("/retry"),
      ),
    ).toHaveLength(1);

    releaseRetry?.();
    expect(await screen.findByText("Failed run retry queued.")).toBeVisible();
  });

  it("maps configuration, queue, idempotency, context, and permission failures to actionable safe states", async () => {
    await renderLoaded("admin", {
      createReplies: [
        { code: "generation_runtime_unavailable", status: 503 },
        { code: "generation_queue_unavailable", status: 503 },
        { code: "generation_idempotency_conflict", status: 409 },
        { code: "generation_context_taxonomy_mismatch", status: 422 },
        { code: "permission_denied", status: 403 },
      ],
    });
    fireEvent.click(screen.getByRole("checkbox", { name: `Select knowledge chunk ${ids.chunk}` }));
    const submit = screen.getByRole("button", { name: "Create generation run" });

    for (const heading of [
      "Generation configuration unavailable",
      "Generation queue unavailable",
      "Idempotency conflict",
      "Context does not match slot",
      "Generation permission required",
    ]) {
      fireEvent.click(submit);
      expect(await screen.findByRole("heading", { name: heading })).toBeVisible();
    }
    expect(screen.queryByText(/api[_ -]?key|traceback|secret-value/i)).not.toBeInTheDocument();
  });

  it("renders loading, empty, retry, and permission-aware workspace states", async () => {
    const fixture = fixtureApi({
      blueprints: [],
      chunks: [],
      createReplies: [],
      questions: [],
      workspaceReplies: [{ code: "permission_denied", status: 403 }],
    });
    vi.stubGlobal("fetch", fixture.fetchMock);
    render(<GenerationStudio role="admin" />);
    expect(screen.getByText("Loading generation workspace…")).toBeVisible();
    expect(await screen.findByRole("heading", { name: "Generation workspace permission required" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Retry generation workspace" }));
    expect(await screen.findByRole("heading", { name: "No immutable blueprints yet" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "No generation runs yet" })).toBeVisible();
  });

  it("polls a durable job and run with bounded backoff and cancels stale work on scope change", async () => {
    const fixture = await renderLoaded("admin", {
      attemptsByRun: { [ids.run]: [] },
      curricula: [curriculum, otherCurriculum],
      pollJob: queuedJob,
      runDetails: { [ids.run]: pendingRun },
    });
    await screen.findByRole("checkbox", { name: `Select knowledge chunk ${ids.chunk}` });
    vi.useFakeTimers();

    fireEvent.click(screen.getByRole("checkbox", { name: `Select knowledge chunk ${ids.chunk}` }));
    fireEvent.click(screen.getByRole("button", { name: "Create generation run" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const jobRequests = () =>
      fixture.requests.filter((request) =>
        new URL(request.url).pathname.includes("/generation-jobs/"),
      );
    expect(jobRequests()).toHaveLength(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(249);
    });
    expect(jobRequests()).toHaveLength(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(jobRequests()).toHaveLength(1);
    expect(screen.getByText("The durable run is pending queue claim.", { exact: false })).toBeVisible();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(499);
    });
    expect(jobRequests()).toHaveLength(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(jobRequests()).toHaveLength(2);

    const signal = jobRequests()[1]?.signal;
    fireEvent.change(screen.getByLabelText("Active Grade 5 curriculum"), {
      target: { value: ids.otherCurriculum },
    });
    expect(signal?.aborted).toBe(true);
    expect(screen.queryByRole("region", { name: "Generation run overview" })).not.toBeInTheDocument();
  });

  it("does not let a late stale detail response overwrite a newer polled run version", async () => {
    let releaseStaleDetail: (() => void) | undefined;
    const staleGate = new Promise<void>((resolve) => {
      releaseStaleDetail = resolve;
    });
    await renderLoaded("admin", {
      beforeRunDetail: (requestNumber) =>
        requestNumber === 0 ? staleGate : Promise.resolve(),
      runDetailReplies: [pendingRun, succeededRun],
    });
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("checkbox", { name: `Select knowledge chunk ${ids.chunk}` }));
    fireEvent.click(screen.getByRole("button", { name: "Create generation run" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(screen.getByText("REQUIRES VALIDATION")).toBeVisible();

    releaseStaleDetail?.();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText("REQUIRES VALIDATION")).toBeVisible();
    expect(screen.getByRole("region", { name: "Generation run overview" })).toHaveTextContent(
      "Version 4",
    );
  });

  it("has no automated accessibility violations in the complete inspection state", async () => {
    const { container } = await renderLoaded("admin", {
      runDetails: { [ids.run]: succeededRun },
      runs: [runSummary(succeededRun)],
    });
    await screen.findByRole("region", { name: "Generated candidate" }, { timeout: 3_000 });

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });
});
