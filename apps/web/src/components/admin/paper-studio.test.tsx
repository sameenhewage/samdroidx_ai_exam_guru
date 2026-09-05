import type { components } from "@exam-guru/api-client";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PaperStudio } from "./paper-studio";

type Blueprint = components["schemas"]["PaperBlueprintResponse"];
type BlueprintSummary = components["schemas"]["PaperBlueprintSummaryResponse"];
type CandidateSummary = components["schemas"]["ReviewCandidateSummaryResponse"];
type PaperAggregate = components["schemas"]["PaperAggregateResponse"];
type PaperDraft = components["schemas"]["PaperDraftVersionResponse"];
type PaperSummary = components["schemas"]["PaperSummaryResponse"];
type Publication = components["schemas"]["PublishedPaperVersionResponse"];
type PublicationSummary = components["schemas"]["PublishedPaperVersionSummaryResponse"];
type PaperArchive = components["schemas"]["PaperArchiveResponse"];

const ids = {
  actor: "00000000-0000-0000-0000-000000000901",
  attempt: "00000000-0000-0000-0000-000000000801",
  blueprint: "00000000-0000-0000-0000-000000000501",
  candidateA: "00000000-0000-0000-0000-000000000701",
  candidateB: "00000000-0000-0000-0000-000000000702",
  candidateOther: "00000000-0000-0000-0000-000000000703",
  competency: "00000000-0000-0000-0000-000000000802",
  curriculum: "00000000-0000-0000-0000-000000000101",
  exam: "00000000-0000-0000-0000-000000000201",
  generationA: "00000000-0000-0000-0000-000000000601",
  generationB: "00000000-0000-0000-0000-000000000602",
  medium: "00000000-0000-0000-0000-000000000301",
  paper: "00000000-0000-0000-0000-000000000401",
  skill: "00000000-0000-0000-0000-000000000803",
  subject: "00000000-0000-0000-0000-000000000302",
  validation: "00000000-0000-0000-0000-000000000301",
} as const;

const now = "2026-08-24T09:30:00Z";
const later = "2026-08-24T09:31:00Z";
const blueprintVersion = "bp_paper_studio_fixture_v1";
const contentHash = "a".repeat(64);
const secondContentHash = "b".repeat(64);

const curriculumScope = {
  curriculum_version_id: ids.curriculum,
  grade: 5,
  lesson_ids: [],
  medium: "en",
  subject_id: ids.subject,
  unit_ids: [],
} satisfies components["schemas"]["CurriculumScopeResponse"];

const taxonomyTarget = {
  competency_id: ids.competency,
  learning_concept_id: null,
  skill_id: ids.skill,
  sub_skill_id: null,
};

const uniqueness = {
  forbid_duplicate_stems: true,
  forbid_verbatim_sources: true,
  max_similarity_basis_points: 8500,
  minimum_distinct_contexts: 1,
};

const slotA = {
  archetype: "single_best_answer",
  difficulty: "medium",
  evidence: {
    baseline_backtest_score: null,
    baseline_score: 100,
    baseline_version: "syllabus-balanced-v1",
    config_version: "paper-studio-v1",
    evidence_refs: ["curriculum:reviewed-taxonomy"],
    forecast_backtest_score: null,
    forecast_score: null,
    forecast_version: null,
    minimum_backtest_improvement: 1,
  },
  generation_constraints: {
    answer_requirements: ["Provide one unambiguous answer."],
    curriculum_scope: curriculumScope,
    diversity_key: "A:1:selection",
    exact_marks: 2,
    instructions: ["Use age-appropriate language."],
    required_archetype: "single_best_answer",
    required_difficulty: "medium",
    required_question_type: "multiple_choice",
    response_language: "en",
    retrieval_query_hints: ["reviewed curriculum"],
    taxonomy_target: taxonomyTarget,
    uniqueness,
  },
  marks: 2,
  ordinal: 1,
  paper_code: "PAPER-STUDIO-01",
  question_type: "multiple_choice",
  rationale: {
    effective_priority_score: 100,
    priority_mode: "baseline_only",
    summary: "Syllabus-balanced baseline selected.",
  },
  section_id: "A",
  section_ordinal: 1,
  section_title: "Selection",
  slot_id: "PAPER-STUDIO-A-001",
  taxonomy_target: taxonomyTarget,
} satisfies components["schemas"]["BlueprintSlotResponse"];

const slotB = {
  ...slotA,
  archetype: "short_response",
  generation_constraints: {
    ...slotA.generation_constraints,
    diversity_key: "B:1:reasoning",
    exact_marks: 3,
    required_archetype: "short_response",
    required_question_type: "short_answer",
  },
  marks: 3,
  ordinal: 2,
  question_type: "short_answer",
  section_id: "B",
  section_ordinal: 1,
  section_title: "Reasoning",
  slot_id: "PAPER-STUDIO-B-001",
} satisfies components["schemas"]["BlueprintSlotResponse"];

const blueprint = {
  algorithm_version: "deterministic-blueprint-v1",
  analytics_run_id: null,
  blueprint: {
    curriculum_scope: curriculumScope,
    difficulty_allocations: [],
    paper_code: "PAPER-STUDIO-01",
    question_type_allocations: [],
    sections: [
      { marks: 2, section_id: "A", slot_count: 1, title: "Selection" },
      { marks: 3, section_id: "B", slot_count: 1, title: "Reasoning" },
    ],
    seed: 17,
    slots: [slotA, slotB],
    taxonomy_requirements: [],
    title: "Grade 5 Paper Studio fixture",
    total_marks: 5,
    version: {
      algorithm_version: "deterministic-blueprint-v1",
      blueprint_id: blueprintVersion,
      config_version: "paper-studio-v1",
      input_fingerprint: "c".repeat(64),
      schema_version: "paper-blueprint-v1",
    },
  },
  blueprint_id: blueprintVersion,
  config_version: "paper-studio-v1",
  created_at: now,
  created_by: ids.actor,
  curriculum_version_id: ids.curriculum,
  deduplicated: false,
  id: ids.blueprint,
  input_fingerprint: "c".repeat(64),
  result_fingerprint: "d".repeat(64),
  schema_version: "paper-blueprint-v1",
  seed: 17,
  slot_count: 2,
  specification: { title: "Grade 5 Paper Studio fixture", total_marks: 5 },
  specification_fingerprint: "e".repeat(64),
  taxonomy_snapshot: [],
  total_marks: 5,
} as unknown as Blueprint;

const blueprintSummary = {
  algorithm_version: blueprint.algorithm_version,
  analytics_run_id: null,
  blueprint_id: blueprint.blueprint_id,
  config_version: blueprint.config_version,
  created_at: now,
  created_by: ids.actor,
  curriculum_version_id: ids.curriculum,
  id: ids.blueprint,
  input_fingerprint: blueprint.input_fingerprint,
  paper_code: "PAPER-STUDIO-01",
  result_fingerprint: blueprint.result_fingerprint,
  schema_version: blueprint.schema_version,
  seed: 17,
  slot_count: 2,
  specification_fingerprint: blueprint.specification_fingerprint,
  title: "Grade 5 Paper Studio fixture",
  total_marks: 5,
} satisfies BlueprintSummary;

function candidate(
  id: string,
  slotId: string,
  stem: string,
  overrides: Partial<CandidateSummary> = {},
): CandidateSummary {
  return {
    blueprint_id: blueprintVersion,
    blueprint_slot_id: slotId,
    blueprint_version: blueprintVersion,
    created_at: now,
    created_by: ids.actor,
    current_revision: 2,
    current_revision_created_at: later,
    curriculum_version_id: ids.curriculum,
    generation_attempt_id: ids.attempt,
    generation_run_id: id === ids.candidateA ? ids.generationA : ids.generationB,
    id,
    marks: slotId === slotA.slot_id ? 2 : 3,
    paper_blueprint_id: ids.blueprint,
    question_type: slotId === slotA.slot_id ? "multiple_choice" : "short_answer",
    state: "approved",
    stem_preview: stem,
    validation_run_id: ids.validation,
    version: 5,
    ...overrides,
  };
}

const candidateA = candidate(ids.candidateA, slotA.slot_id, "Which number is even?");
const candidateB = candidate(ids.candidateB, slotB.slot_id, "Explain why four is even.");
const wrongBlueprintCandidate = candidate(
  ids.candidateOther,
  slotA.slot_id,
  "A cross-blueprint candidate must never be selectable.",
  {
    blueprint_id: "bp_other",
    blueprint_version: "bp_other",
    paper_blueprint_id: "00000000-0000-0000-0000-000000000599",
  },
);

const paperSummary = {
  blueprint_id: blueprintVersion,
  blueprint_version: blueprintVersion,
  created_at: now,
  created_by: ids.actor,
  current_version: 1,
  curriculum_version_id: ids.curriculum,
  id: ids.paper,
  latest_publication_hash: null,
  paper_blueprint_id: ids.blueprint,
  state: "draft",
  title: "Fixture practice paper",
  updated_at: later,
  updated_by: ids.actor,
} satisfies PaperSummary;

function aggregate(state: PaperAggregate["state"], version = state === "archived" ? 2 : 1): PaperAggregate {
  return {
    blueprint_id: blueprintVersion,
    blueprint_version: blueprintVersion,
    created_at: now,
    created_by: ids.actor,
    current_version: version,
    curriculum_version_id: ids.curriculum,
    id: ids.paper,
    paper_blueprint_id: ids.blueprint,
    state,
    updated_at: later,
    updated_by: ids.actor,
  };
}

const draft = {
  candidates: [
    {
      blueprint_slot_id: slotA.slot_id,
      candidate_id: ids.candidateA,
      candidate_revision: 2,
      candidate_version: 5,
      ordinal: 1,
    },
    {
      blueprint_slot_id: slotB.slot_id,
      candidate_id: ids.candidateB,
      candidate_revision: 1,
      candidate_version: 4,
      ordinal: 2,
    },
  ],
  created_at: now,
  created_by: ids.actor,
  curriculum_version_id: ids.curriculum,
  deduplicated: false,
  paper_id: ids.paper,
  supersedes_content_hash: null,
  title: "Fixture practice paper",
  version: 1,
} satisfies PaperDraft;

const maliciousStem = "<img src=x onerror=alert(1)> Which number is even?";
const veryLongExplanation = `<script>unsafe()</script>${"x".repeat(5_000)}`;

function publishedQuestion(
  candidateId: string,
  slotId: string,
  stem: string,
  marks: number,
): Publication["snapshot"]["questions"][number] {
  const content = {
    answer: candidateId === ids.candidateA ? "B" : "Four is divisible by two.",
    explanation: candidateId === ids.candidateA ? veryLongExplanation : "Four makes two equal pairs.",
    marking_guide: ["Award marks only for the supported answer.", "Do not award unsupported work."],
    marking_point_marks: [1, marks - 1],
    marks,
    options:
      candidateId === ids.candidateA
        ? [
            { option_id: "A", text: "Three" },
            { option_id: "B", text: "Four" },
          ]
        : [],
    question_type: candidateId === ids.candidateA ? ("multiple_choice" as const) : ("short_answer" as const),
    stem,
  };
  return {
    candidate_id: candidateId,
    candidate_version: 5,
    content,
    content_revision: 2,
    decision: {
      candidate_version: 5,
      reason: "Source, answer, wording, and marking were reviewed.",
      reviewer_id: ids.actor,
      state: "approved",
    },
    lineage: {
      blueprint_id: blueprintVersion,
      blueprint_slot_id: slotId,
      blueprint_version: blueprintVersion,
      generation_attempt_id: ids.attempt,
      generation_id: candidateId === ids.candidateA ? ids.generationA : ids.generationB,
      model_version: "fixture-model.2026-08",
      prompt_version: "question-prompt.v3",
      provenance: [
        {
          chunk_id: "reviewed-chunk-1",
          page_number: 4,
          source_document_id: "source-document-1",
          source_version: "reviewed.v3",
        },
      ],
      provider: "deterministic-fake",
      retrieval_version: "hybrid-retrieval.v2",
      schema_version: "question.v1",
    },
    review_history: [
      { action: "started", candidate_version: 3, reason: null, reviewer_id: ids.actor },
      {
        action: "edited",
        candidate_version: 4,
        reason: "Clarify grounded wording.",
        reviewer_id: ids.actor,
      },
      {
        action: "approved",
        candidate_version: 5,
        reason: "Source, answer, wording, and marking were reviewed.",
        reviewer_id: ids.actor,
      },
    ],
    revisions: [
      {
        content: { ...content, stem: `Generated revision for ${slotId}` },
        reason: null,
        reviewer_id: null,
        revision: 1,
      },
      {
        content,
        reason: "Clarify grounded wording.",
        reviewer_id: ids.actor,
        revision: 2,
      },
    ],
    slot_id: slotId,
    validation: {
      finding_refs: ["finding:schema:pass", "finding:grounding:pass"],
      passed: true,
      validated_revision: 1,
      validation_run_id: ids.validation,
      validator_version: "canonical-validation.v1",
    },
  };
}

const publication = {
  content_hash: contentHash,
  curriculum_version_id: ids.curriculum,
  deduplicated: false,
  paper_id: ids.paper,
  previous_version: null,
  published_at: later,
  published_by: ids.actor,
  snapshot: {
    blueprint: {
      blueprint_id: blueprintVersion,
      blueprint_version: blueprintVersion,
      paper_blueprint_id: ids.blueprint,
      slot_ids: [slotA.slot_id, slotB.slot_id],
    },
    paper_id: ids.paper,
    paper_version: 1,
    questions: [
      publishedQuestion(ids.candidateA, slotA.slot_id, maliciousStem, 2),
      publishedQuestion(ids.candidateB, slotB.slot_id, "Explain why four is even.", 3),
    ],
    schema: "published-paper.v1",
    title: "Fixture immutable publication",
  },
  supersedes_content_hash: null,
  version: 1,
} satisfies Publication;

const secondPublication = {
  ...publication,
  content_hash: secondContentHash,
  previous_version: 1,
  snapshot: {
    ...publication.snapshot,
    paper_version: 2,
    title: "Fixture corrected immutable publication",
  },
  supersedes_content_hash: contentHash,
  version: 2,
} satisfies Publication;

const publicationSummary = {
  content_hash: contentHash,
  curriculum_version_id: ids.curriculum,
  paper_id: ids.paper,
  published_at: later,
  published_by: ids.actor,
  version: 1,
} satisfies PublicationSummary;

const secondPublicationSummary = {
  ...publicationSummary,
  content_hash: secondContentHash,
  version: 2,
} satisfies PublicationSummary;

const archive = {
  archived_at: later,
  archived_by: ids.actor,
  content_hash: contentHash,
  curriculum_version_id: ids.curriculum,
  deduplicated: false,
  paper_id: ids.paper,
  reason: "Superseded by a corrected syllabus release.",
  version: 2,
} satisfies PaperArchive;

type Operation = "archive" | "create" | "publish" | "revise";
type FixtureOptions = {
  candidates?: CandidateSummary[];
  createDeduplicated?: boolean;
  gate?: Promise<void>;
  noActiveCurriculum?: boolean;
  operationError?: { action: Operation; code: string; status: number };
  paperDetailStatus?: number;
  paperState?: PaperAggregate["state"];
  paperVersion?: number;
  priorPublication?: boolean;
  workspaceNetworkError?: boolean;
  workspaceStatus?: number;
};

function fixtureApi(options: FixtureOptions = {}) {
  const requests: Request[] = [];
  let state = options.paperState;
  let version = options.paperVersion ?? (state === "archived" ? 2 : 1);
  let latestHash =
    state === "published" || state === "archived" || options.priorPublication ? contentHash : null;

  const errorFor = (action: Operation) =>
    options.operationError?.action === action
      ? Response.json(
          { detail: { code: options.operationError.code } },
          { status: options.operationError.status },
        )
      : null;

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init);
    requests.push(request.clone());
    const url = new URL(request.url);
    const path = url.pathname;

    if (options.workspaceNetworkError && path.endsWith("/exam-configurations")) {
      throw new TypeError("network unavailable");
    }
    if (request.method === "GET" && path.endsWith("/exam-configurations")) {
      if (options.workspaceStatus) {
        return Response.json(
          { detail: { code: options.workspaceStatus === 403 ? "permission_denied" : "workspace_failed" } },
          { status: options.workspaceStatus },
        );
      }
      return Response.json([
        {
          active: true,
          code: "G5-SCH",
          created_at: now,
          grade: 5,
          id: ids.exam,
          name: "Grade 5 Scholarship",
          updated_at: now,
        },
      ]);
    }
    if (request.method === "GET" && path.endsWith("/media")) {
      return Response.json([
        {
          active: true,
          code: "en",
          created_at: now,
          id: ids.medium,
          name: "English",
          updated_at: now,
        },
      ]);
    }
    if (request.method === "GET" && path.endsWith("/curriculum-versions")) {
      return Response.json(
        options.noActiveCurriculum
          ? []
          : [
              {
                active: true,
                code: "G5-EN-2026",
                created_at: now,
                exam_configuration_id: ids.exam,
                id: ids.curriculum,
                medium_id: ids.medium,
                title: "Grade 5 English 2026",
                updated_at: now,
              },
            ],
      );
    }
    if (
      request.method === "GET" &&
      path.endsWith(`/blueprints/${ids.blueprint}`)
    ) {
      return Response.json(blueprint);
    }
    if (request.method === "GET" && path.endsWith("/blueprints")) {
      return Response.json([blueprintSummary]);
    }
    if (request.method === "GET" && path.endsWith("/review-candidates")) {
      return Response.json(options.candidates ?? [candidateA, candidateB, wrongBlueprintCandidate]);
    }
    if (request.method === "POST" && path.endsWith("/paper-drafts")) {
      const error = errorFor("create");
      if (error) return error;
      if (options.gate) await options.gate;
      state = "draft";
      version = 1;
      latestHash = null;
      return Response.json(
        { ...draft, deduplicated: options.createDeduplicated ?? false },
        { status: 201 },
      );
    }
    if (request.method === "GET" && path.endsWith(`/papers/${ids.paper}/draft-versions`)) {
      return Response.json([draft]);
    }
    if (request.method === "GET" && path.endsWith(`/papers/${ids.paper}/publication-versions/1`)) {
      return Response.json(publication);
    }
    if (request.method === "GET" && path.endsWith(`/papers/${ids.paper}/publication-versions/2`)) {
      return Response.json(secondPublication);
    }
    if (request.method === "GET" && path.endsWith(`/papers/${ids.paper}/publication-versions`)) {
      const hasPublication =
        state === "published" || state === "archived" || options.priorPublication;
      return Response.json(
        hasPublication
          ? [publicationSummary, ...(state === "published" && version === 2 ? [secondPublicationSummary] : [])]
          : [],
      );
    }
    if (request.method === "GET" && path.endsWith(`/papers/${ids.paper}/archive`)) {
      return Response.json(archive);
    }
    if (request.method === "POST" && path.endsWith(`/papers/${ids.paper}/revisions`)) {
      const error = errorFor("revise");
      if (error) return error;
      state = "draft";
      version = 2;
      return Response.json(
        { ...draft, supersedes_content_hash: contentHash, version: 2 },
        { status: 201 },
      );
    }
    if (request.method === "POST" && path.endsWith(`/papers/${ids.paper}/publish`)) {
      const error = errorFor("publish");
      if (error) return error;
      state = "published";
      latestHash = version === 2 ? secondContentHash : contentHash;
      return Response.json(version === 2 ? secondPublication : publication);
    }
    if (request.method === "POST" && path.endsWith(`/papers/${ids.paper}/archive`)) {
      const error = errorFor("archive");
      if (error) return error;
      state = "archived";
      return Response.json(archive);
    }
    if (request.method === "GET" && path.endsWith(`/papers/${ids.paper}`)) {
      if (options.paperDetailStatus) {
        return Response.json(
          { detail: { code: "paper_not_found" } },
          { status: options.paperDetailStatus },
        );
      }
      return Response.json(aggregate(state ?? "draft", version));
    }
    if (request.method === "GET" && path.endsWith("/papers")) {
      return Response.json(
        state
          ? [
              {
                ...paperSummary,
                current_version: version,
                latest_publication_hash: latestHash,
                state,
              },
            ]
          : [],
      );
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
  const view = render(<PaperStudio role={role} />);
  await screen.findByRole("heading", { level: 1, name: "Paper Studio" });
  await waitFor(() =>
    expect(screen.queryByText("Loading Paper Studio workspace…")).not.toBeInTheDocument(),
  );
  if (!options.noActiveCurriculum && !options.workspaceStatus && !options.workspaceNetworkError) {
    await screen.findByLabelText("Immutable paper blueprint");
    await waitFor(() =>
      expect(screen.queryByText("Loading immutable blueprint and approved queue…")).not.toBeInTheDocument(),
    );
    await screen.findAllByTestId("exact-blueprint-slot");
  }
  return { ...fixture, view };
}

function requestsEnding(requests: Request[], method: string, suffix: string): Request[] {
  return requests.filter(
    (request) => request.method === method && new URL(request.url).pathname.endsWith(suffix),
  );
}

function lastRequest(requests: Request[], method: string, suffix: string): Request {
  const request = requestsEnding(requests, method, suffix).at(-1);
  if (!request) throw new Error(`Missing ${method} request ending ${suffix}`);
  return request;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("PaperStudio", () => {
  it("loads only active Grade 5 scope, immutable blueprints, approved lightweight candidates, summaries, and every exact slot in blueprint order", async () => {
    const { requests } = await renderLoaded("reviewer");

    expect(screen.getByLabelText("Active Grade 5 curriculum")).toHaveValue(ids.curriculum);
    expect(screen.getByLabelText("Immutable paper blueprint")).toHaveValue(ids.blueprint);
    const exactSlots = screen.getAllByTestId("exact-blueprint-slot");
    expect(exactSlots).toHaveLength(2);
    expect(exactSlots[0]).toHaveTextContent(`1${slotA.slot_id}`);
    expect(exactSlots[1]).toHaveTextContent(`2${slotB.slot_id}`);
    expect(screen.getByLabelText(`Candidate for exact slot ${slotA.slot_id}`)).toHaveTextContent(
      candidateA.stem_preview,
    );
    expect(screen.getByLabelText(`Candidate for exact slot ${slotB.slot_id}`)).toHaveTextContent(
      candidateB.stem_preview,
    );
    expect(screen.queryByText(wrongBlueprintCandidate.stem_preview)).not.toBeInTheDocument();

    const candidateRequest = lastRequest(requests, "GET", "/review-candidates");
    const query = new URL(candidateRequest.url).searchParams;
    expect(query.get("state")).toBe("approved");
    expect(query.get("paper_blueprint_id")).toBe(ids.blueprint);
    expect(query.get("limit")).toBe("100");
    expect(screen.getByText("No papers exist in this curriculum yet.")).toBeVisible();
  });

  it("assembles as a reviewer with one unique candidate per exact slot, an exact DTO body, one bounded key, and duplicate-click protection", async () => {
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { requests } = await renderLoaded("reviewer", { gate });

    fireEvent.change(screen.getByLabelText("Paper title"), {
      target: { value: "Reviewer assembled paper" },
    });
    fireEvent.change(screen.getByLabelText(`Candidate for exact slot ${slotA.slot_id}`), {
      target: { value: ids.candidateA },
    });
    fireEvent.change(screen.getByLabelText(`Candidate for exact slot ${slotB.slot_id}`), {
      target: { value: ids.candidateB },
    });
    const create = screen.getByRole("button", { name: "Create immutable draft" });
    fireEvent.click(create);
    fireEvent.click(create);

    await waitFor(() =>
      expect(requestsEnding(requests, "POST", "/paper-drafts")).toHaveLength(1),
    );
    expect(create).toBeDisabled();
    const request = lastRequest(requests, "POST", "/paper-drafts");
    const payload = (await request.json()) as Record<string, unknown>;
    expect(payload).toEqual({
      candidate_ids: [ids.candidateA, ids.candidateB],
      paper_blueprint_id: ids.blueprint,
      title: "Reviewer assembled paper",
    });
    expect(Object.keys(payload).sort()).toEqual([
      "candidate_ids",
      "paper_blueprint_id",
      "title",
    ]);
    const key = request.headers.get("Idempotency-Key") ?? "";
    expect(key).toMatch(/^paper-draft-[A-Za-z0-9-]+$/);
    expect(key.length).toBeLessThanOrEqual(128);
    expect(key).not.toMatch(/\s/);

    await act(async () => release?.());
    expect(await screen.findByText("Immutable draft version 1 created.")).toBeVisible();
  });

  it("prevents duplicate candidate selection and explains incomplete exact approved coverage", async () => {
    const forgedDuplicate = candidate(
      ids.candidateA,
      slotB.slot_id,
      "Forged duplicate identifier for another slot.",
    );
    const duplicateView = await renderLoaded("admin", {
      candidates: [candidateA, forgedDuplicate],
    });

    fireEvent.change(screen.getByLabelText(`Candidate for exact slot ${slotA.slot_id}`), {
      target: { value: ids.candidateA },
    });
    const second = screen.getByLabelText(`Candidate for exact slot ${slotB.slot_id}`);
    expect(within(second).getByRole("option", { name: /Forged duplicate identifier/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Create immutable draft" })).toBeDisabled();
    duplicateView.view.unmount();

    const { view } = await renderLoaded("admin", { candidates: [candidateA] });
    expect(screen.getByRole("heading", { name: "No exact approved coverage" })).toBeVisible();
    expect(screen.getByText(slotB.slot_id)).toBeVisible();
    expect(screen.getByText(/Generate, validate, and approve exactly one same-blueprint candidate/i)).toBeVisible();
    view.unmount();
  });

  it("lets a reviewer inspect and revise only the current publication while hiding publish and archive controls", async () => {
    const { requests } = await renderLoaded("reviewer", { paperState: "published" });
    await screen.findByRole("region", { name: "Verified immutable publication snapshot" });

    expect(screen.queryByRole("button", { name: "Publish current draft" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archive paper terminally" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Revise current publication" })).toBeVisible();
    await waitFor(() =>
      expect(screen.getByLabelText(`Revision candidate for exact slot ${slotA.slot_id}`)).toHaveValue(
        ids.candidateA,
      ),
    );
    expect(screen.getByLabelText(`Revision candidate for exact slot ${slotB.slot_id}`)).toHaveValue(
      ids.candidateB,
    );
    fireEvent.click(screen.getByRole("button", { name: "Create revision draft" }));
    expect(await screen.findByText("Revision draft version 2 created from publication version 1.")).toBeVisible();

    const request = lastRequest(requests, "POST", `/papers/${ids.paper}/revisions`);
    expect(await request.json()).toEqual({
      candidate_ids: [ids.candidateA, ids.candidateB],
      expected_version: 1,
    });
  });

  it("publishes with expected_version only, requires an archive reason with a terminal warning, and reports deduplication", async () => {
    const { requests } = await renderLoaded("admin", {
      createDeduplicated: true,
      paperState: "draft",
    });
    await screen.findByRole("region", { name: "Selected paper lifecycle" });

    fireEvent.click(screen.getByRole("button", { name: "Publish current draft" }));
    expect(await screen.findByText("Publication version 1 created as an immutable verified snapshot.")).toBeVisible();
    const publish = lastRequest(requests, "POST", `/papers/${ids.paper}/publish`);
    const publishPayload = (await publish.json()) as Record<string, unknown>;
    expect(publishPayload).toEqual({ expected_version: 1 });
    expect(Object.keys(publishPayload)).toEqual(["expected_version"]);

    const archiveButton = await screen.findByRole("button", { name: "Archive paper terminally" });
    expect(screen.getByText(/Archiving is terminal/i)).toBeVisible();
    expect(archiveButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Archive reason (required)"), {
      target: { value: "Superseded by a corrected syllabus release." },
    });
    expect(archiveButton).toBeEnabled();
    fireEvent.click(archiveButton);
    expect(await screen.findByText("Paper archived terminally. Existing immutable publication snapshots remain inspectable.")).toBeVisible();
    const archiveRequest = lastRequest(requests, "POST", `/papers/${ids.paper}/archive`);
    expect(await archiveRequest.json()).toEqual({
      expected_version: 1,
      reason: "Superseded by a corrected syllabus release.",
    });
  });

  it("selects the newly published current version instead of leaving an older publication snapshot selected", async () => {
    const { requests } = await renderLoaded("admin", {
      paperState: "draft",
      paperVersion: 2,
      priorPublication: true,
    });
    await screen.findByRole("region", { name: "Verified immutable publication snapshot" });
    expect(screen.getByText("Fixture immutable publication")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Publish current draft" }));
    expect(
      await screen.findByText("Publication version 2 created as an immutable verified snapshot."),
    ).toBeVisible();
    await waitFor(() =>
      expect(screen.getByRole("region", { name: "Verified immutable publication snapshot" })).toHaveTextContent(
        "Fixture corrected immutable publication",
      ),
    );
    await waitFor(() =>
      expect(
        requestsEnding(requests, "GET", `/papers/${ids.paper}/publication-versions/2`),
      ).not.toHaveLength(0),
    );
  });

  it("renders the complete verified snapshot, version chain, content, lineage, provenance, revision evidence, and untrusted text as bounded plain text", async () => {
    const { view } = await renderLoaded("admin", { paperState: "published" });
    const snapshot = await screen.findByRole("region", {
      name: "Verified immutable publication snapshot",
    });
    await waitFor(() =>
      expect(screen.getByLabelText(`Revision candidate for exact slot ${slotA.slot_id}`)).toHaveValue(
        ids.candidateA,
      ),
    );

    expect(snapshot).toHaveTextContent("Student serving requires no live LLM or provider call");
    expect(snapshot).toHaveTextContent("immutable, hash-verified snapshot");
    expect(snapshot).toHaveTextContent("Fixture immutable publication");
    expect(snapshot).toHaveTextContent(contentHash);
    expect(snapshot).toHaveTextContent("Previous versionNone");
    expect(snapshot).toHaveTextContent("Supersedes hashNone");
    expect(snapshot).toHaveTextContent(maliciousStem);
    expect(snapshot).toHaveTextContent("B");
    expect(snapshot).toHaveTextContent("Three");
    expect(snapshot).toHaveTextContent("Award marks only for the supported answer.");
    expect(snapshot).toHaveTextContent("deterministic-fake");
    expect(snapshot).toHaveTextContent("fixture-model.2026-08");
    expect(snapshot).toHaveTextContent("source-document-1");
    expect(snapshot).toHaveTextContent("reviewed-chunk-1");
    expect(snapshot).toHaveTextContent("Validated revision1");
    expect(snapshot).toHaveTextContent("canonical-validation.v1");
    expect(snapshot).toHaveTextContent("Revision 1");
    expect(snapshot).toHaveTextContent("Revision 2");
    expect(snapshot).toHaveTextContent("Clarify grounded wording.");
    expect(snapshot).toHaveTextContent("Approved");
    expect(view.container.querySelector("img")).toBeNull();
    expect(view.container.querySelector("script")).toBeNull();
    expect(snapshot.textContent).not.toContain("x".repeat(4_097));

    const results = await axe.run(view.container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });

  it("surfaces too-large selection and stale-version conflicts with an authoritative reload action", async () => {
    const tooLarge = await renderLoaded("reviewer", {
      operationError: {
        action: "create",
        code: "paper_candidate_selection_too_large",
        status: 422,
      },
    });
    fireEvent.change(screen.getByLabelText(`Candidate for exact slot ${slotA.slot_id}`), {
      target: { value: ids.candidateA },
    });
    fireEvent.change(screen.getByLabelText(`Candidate for exact slot ${slotB.slot_id}`), {
      target: { value: ids.candidateB },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create immutable draft" }));
    expect(await screen.findByRole("heading", { name: "Candidate selection is too large" })).toBeVisible();
    expect(screen.getByText(/Reduce persisted candidate source size/i)).toBeVisible();
    tooLarge.view.unmount();

    const stale = await renderLoaded("admin", {
      operationError: { action: "publish", code: "paper_version_conflict", status: 409 },
      paperState: "draft",
    });
    const initialPaperGets = requestsEnding(
      stale.requests,
      "GET",
      `/papers/${ids.paper}`,
    ).length;
    fireEvent.click(screen.getByRole("button", { name: "Publish current draft" }));
    expect(await screen.findByRole("heading", { name: "Paper version changed" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Reload authoritative paper" }));
    await waitFor(() =>
      expect(requestsEnding(stale.requests, "GET", `/papers/${ids.paper}`).length).toBeGreaterThan(
        initialPaperGets,
      ),
    );
  });

  it("keeps archived papers terminal while preserving the archive event and immutable publication", async () => {
    await renderLoaded("admin", { paperState: "archived" });
    expect(await screen.findByRole("heading", { name: "Archived terminal state" })).toBeVisible();
    expect(screen.getByText("Superseded by a corrected syllabus release.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Publish current draft" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create revision draft" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archive paper terminally" })).not.toBeInTheDocument();
    expect(
      await screen.findByRole("region", { name: "Verified immutable publication snapshot" }),
    ).toBeVisible();
  });

  it("handles a scoped 404 with an authoritative paper reload action", async () => {
    await renderLoaded("reviewer", { paperDetailStatus: 404, paperState: "draft" });
    expect(await screen.findByRole("heading", { name: "Paper resource not found" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Reload selected paper" })).toBeVisible();
  });

  it("handles permission, network, and empty active-curriculum states explicitly", async () => {
    const denied = await renderLoaded("reviewer", { workspaceStatus: 403 });
    expect(screen.getByRole("heading", { name: "Paper Studio permission required" })).toBeVisible();
    denied.view.unmount();

    const network = await renderLoaded("admin", { workspaceNetworkError: true });
    expect(screen.getByRole("heading", { name: "Paper Studio unavailable" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry Paper Studio workspace" })).toBeVisible();
    network.view.unmount();

    await renderLoaded("admin", { noActiveCurriculum: true });
    expect(screen.getByRole("heading", { name: "No active Grade 5 curriculum" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Open Curriculum Studio" })).toHaveAttribute(
      "href",
      "/admin/curriculum",
    );
  });

  it("reports a deduplicated immutable draft without implying a second paper was created", async () => {
    await renderLoaded("admin", { createDeduplicated: true });
    fireEvent.change(screen.getByLabelText(`Candidate for exact slot ${slotA.slot_id}`), {
      target: { value: ids.candidateA },
    });
    fireEvent.change(screen.getByLabelText(`Candidate for exact slot ${slotB.slot_id}`), {
      target: { value: ids.candidateB },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create immutable draft" }));
    expect(
      await screen.findByText("Matching immutable draft version 1 already existed; it was reused."),
    ).toBeVisible();
  });
});
