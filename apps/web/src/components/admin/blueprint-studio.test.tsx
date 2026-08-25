import type { components } from "@exam-guru/api-client";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BlueprintStudio } from "./blueprint-studio";

type Exam = components["schemas"]["ExamConfigurationResponse"];
type Medium = components["schemas"]["MediumResponse"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
type AnalyticsSummary = components["schemas"]["AnalyticsRunSummaryResponse"];
type Blueprint = components["schemas"]["PaperBlueprintResponse"];
type BlueprintSummary = components["schemas"]["PaperBlueprintSummaryResponse"];
type BlueprintRequest = components["schemas"]["BlueprintCreateRequest"];

const ids = {
  actor: "00000000-0000-0000-0000-000000000901",
  analytics: "00000000-0000-0000-0000-000000000801",
  blueprint: "00000000-0000-0000-0000-000000000701",
  competency: "00000000-0000-0000-0000-000000000401",
  curriculum: "00000000-0000-0000-0000-000000000101",
  draftSkill: "00000000-0000-0000-0000-000000000403",
  exam: "00000000-0000-0000-0000-000000000201",
  gradeSixCurriculum: "00000000-0000-0000-0000-000000000102",
  gradeSixExam: "00000000-0000-0000-0000-000000000202",
  medium: "00000000-0000-0000-0000-000000000301",
  otherCurriculum: "00000000-0000-0000-0000-000000000103",
  skill: "00000000-0000-0000-0000-000000000402",
} as const;

const now = "2026-08-24T09:30:00Z";
const hash = (value: string) => `sha256:${value.repeat(64).slice(0, 64)}`;

const exams = [
  {
    active: true,
    code: "G5-SCH",
    created_at: now,
    grade: 5,
    id: ids.exam,
    name: "Grade 5 Scholarship",
    updated_at: now,
  },
  {
    active: true,
    code: "G6",
    created_at: now,
    grade: 6,
    id: ids.gradeSixExam,
    name: "Grade 6",
    updated_at: now,
  },
] satisfies Exam[];

const media = [
  {
    active: true,
    code: "si",
    created_at: now,
    id: ids.medium,
    name: "Sinhala",
    updated_at: now,
  },
] satisfies Medium[];

const curriculum = {
  active: true,
  code: "G5-SI-2026",
  created_at: now,
  exam_configuration_id: ids.exam,
  id: ids.curriculum,
  medium_id: ids.medium,
  title: "Grade 5 Sinhala 2026",
  updated_at: now,
} satisfies Curriculum;

const gradeSixCurriculum = {
  ...curriculum,
  code: "G6-SI-2026",
  exam_configuration_id: ids.gradeSixExam,
  id: ids.gradeSixCurriculum,
  title: "Grade 6 Sinhala 2026",
} satisfies Curriculum;

const otherCurriculum = {
  ...curriculum,
  code: "G5-SI-2027",
  id: ids.otherCurriculum,
  title: "Grade 5 Sinhala 2027",
} satisfies Curriculum;

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
    code: "S-DRAFT",
    curriculum_version_id: ids.curriculum,
    id: ids.draftSkill,
    level: "skill",
    parent_id: ids.competency,
    review_state: "draft",
    title: "Unreviewed skill",
  },
] satisfies TaxonomyNode[];

const analytics = {
  aggregate: {
    baseline_delta: { denominator: 20, numerator: 1 },
    baseline_score_variance: { denominator: 1, numerator: 0 },
    mean_baseline_score: { denominator: 10, numerator: 7 },
    mean_method_score: { denominator: 4, numerator: 3 },
    method_score_variance: { denominator: 1, numerator: 0 },
    window_count: 2,
  },
  backtest_algorithm_version: "rolling-heldout-v1",
  baseline_algorithm_version: "syllabus-balanced-v1",
  config_fingerprint: hash("a"),
  created_at: now,
  created_by: ids.actor,
  curriculum_version_id: ids.curriculum,
  excluded_count: 0,
  id: ids.analytics,
  included_count: 12,
  input_fingerprint: hash("b"),
  practice_priority_algorithm_version: "practice-priority-v1",
  recommendation: {
    language: "Use evidence-backed practice priorities.",
    meaningful_improvement: { denominator: 100, numerator: 1 },
    mode: "evidence_backed_practice",
    observed_baseline_delta: { denominator: 20, numerator: 1 },
    selected_method: "historical_evidence",
  },
  result_fingerprint: hash("c"),
  run_fingerprint: hash("d"),
  source_fingerprint: hash("e"),
  statistics_algorithm_version: "historical-statistics-v1",
} satisfies AnalyticsSummary;

const target = {
  competency_id: ids.competency,
  learning_concept_id: null,
  skill_id: ids.skill,
  sub_skill_id: null,
};
const scope = {
  curriculum_version_id: ids.curriculum,
  grade: 5,
  medium: "si",
};
const uniqueness = {
  forbid_duplicate_stems: true,
  forbid_verbatim_sources: true,
  max_similarity_basis_points: 8500,
  minimum_distinct_contexts: 1,
};
const requirement = {
  allowed_section_ids: ["A"],
  generation_instructions: ["Use an age-appropriate familiar setting."],
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
  retrieval_query_hints: ["reviewed polygon concepts"],
  target,
};
const specification = {
  config_version: "grade5-blueprint-config-v1",
  curriculum_scope: scope,
  difficulty_allocations: [{ difficulty: "medium" as const, exact_marks: 2, exact_slots: 1 }],
  generation_policy: {
    answer_requirements: ["Provide one unambiguous answer with marking guidance."],
    instructions: ["Use age-appropriate Grade 5 language."],
    response_language: "si",
    retrieval_query_hints: ["Grade 5 reviewed curriculum"],
    uniqueness,
  },
  paper_code: "G5-PRACTICE-01",
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
      allowed_difficulties: ["easy" as const, "medium" as const, "hard" as const],
      allowed_marks_per_slot: [2],
      allowed_question_types: [
        "multiple_choice" as const,
        "short_answer" as const,
        "structured" as const,
      ],
      allowed_taxonomy_targets: [],
      marks: 2,
      question_count: 1,
      retrieval_query_hints: ["selection section"],
      section_id: "A",
      title: "Selection",
    },
  ],
  taxonomy_requirements: [requirement],
  title: "Grade 5 Scholarship Practice Paper",
  total_marks: 2,
};

const blueprint = {
  algorithm_version: "deterministic-blueprint-v1",
  analytics_run_id: null,
  blueprint: {
    curriculum_scope: scope,
    difficulty_allocations: specification.difficulty_allocations,
    paper_code: specification.paper_code,
    question_type_allocations: specification.question_type_allocations,
    sections: [{ marks: 2, section_id: "A", slot_count: 1, title: "Selection" }],
    seed: 0,
    slots: [
      {
        archetype: "single_best_answer",
        difficulty: "medium",
        evidence: {
          baseline_backtest_score: null,
          baseline_score: 100,
          baseline_version: "syllabus-balanced-v1",
          config_version: specification.config_version,
          evidence_refs: ["curriculum:reviewed-taxonomy"],
          forecast_backtest_score: null,
          forecast_score: null,
          forecast_version: null,
          minimum_backtest_improvement: 1,
        },
        generation_constraints: {
          answer_requirements: specification.generation_policy.answer_requirements,
          curriculum_scope: scope,
          diversity_key: "A:1:recognise-polygons",
          exact_marks: 2,
          instructions: [
            ...specification.generation_policy.instructions,
            ...requirement.generation_instructions,
          ],
          required_archetype: "single_best_answer",
          required_difficulty: "medium",
          required_question_type: "multiple_choice",
          response_language: "si",
          retrieval_query_hints: [
            ...specification.generation_policy.retrieval_query_hints,
            ...specification.sections[0].retrieval_query_hints,
            ...requirement.retrieval_query_hints,
          ],
          taxonomy_target: target,
          uniqueness,
        },
        marks: 2,
        ordinal: 1,
        paper_code: specification.paper_code,
        question_type: "multiple_choice",
        rationale: {
          effective_priority_score: 100,
          priority_mode: "baseline_only",
          summary: "Syllabus-balanced baseline selected because no analytics run was linked.",
        },
        section_id: "A",
        section_ordinal: 1,
        section_title: "Selection",
        slot_id: "G5-PRACTICE-01-A-001",
        taxonomy_target: target,
      },
    ],
    taxonomy_requirements: [requirement],
    title: specification.title,
    total_marks: 2,
    version: {
      algorithm_version: "deterministic-blueprint-v1",
      blueprint_id: "bp_1234567890abcdef12345678",
      config_version: specification.config_version,
      input_fingerprint: "f".repeat(64),
      schema_version: "paper-blueprint-v1",
    },
  },
  blueprint_id: "bp_1234567890abcdef12345678",
  config_version: specification.config_version,
  created_at: now,
  created_by: ids.actor,
  curriculum_version_id: ids.curriculum,
  deduplicated: false,
  id: ids.blueprint,
  input_fingerprint: hash("f"),
  result_fingerprint: hash("1"),
  schema_version: "paper-blueprint-v1",
  seed: 0,
  slot_count: 1,
  specification,
  specification_fingerprint: hash("2"),
  taxonomy_snapshot: [
    {
      active: true,
      code: "C1",
      curriculum_version_id: ids.curriculum,
      id: ids.competency,
      level: "competency",
      parent_id: null,
      review_state: "reviewed",
      reviewed_at: now,
      reviewed_by: ids.actor,
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
      reviewed_at: now,
      reviewed_by: ids.actor,
      title: "Recognise polygons",
    },
  ],
  total_marks: 2,
} satisfies Blueprint;

function summaryFromBlueprint(value: Blueprint): BlueprintSummary {
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

function asRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(input, init);
}

type FixtureOptions = {
  analytics?: AnalyticsSummary[];
  beforeCreate?: () => Promise<void>;
  beforeTaxonomyLoad?: () => Promise<void>;
  blueprints?: BlueprintSummary[];
  createStatuses?: number[];
  curricula?: Curriculum[];
  rejectWorkspaceOnce?: boolean;
  taxonomy?: TaxonomyNode[];
  workspaceStatuses?: number[];
};

function fixtureApi(options: FixtureOptions = {}) {
  const requests: Request[] = [];
  const createStatuses = [...(options.createStatuses ?? [201])];
  const workspaceStatuses = [...(options.workspaceStatuses ?? [])];
  let rejectWorkspace = options.rejectWorkspaceOnce ?? false;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    requests.push(request.clone());
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname.endsWith("/exam-configurations")) {
      if (rejectWorkspace) {
        rejectWorkspace = false;
        throw new TypeError("network unavailable");
      }
      return Response.json(exams);
    }
    if (request.method === "GET" && url.pathname.endsWith("/media")) {
      return Response.json(media);
    }
    if (request.method === "GET" && url.pathname.endsWith("/curriculum-versions")) {
      const status = workspaceStatuses.shift() ?? 200;
      return status === 200
        ? Response.json(options.curricula ?? [curriculum, gradeSixCurriculum])
        : Response.json({ detail: { code: "permission_denied" } }, { status });
    }
    if (request.method === "GET" && url.pathname.endsWith("/taxonomy/nodes")) {
      await options.beforeTaxonomyLoad?.();
      return Response.json(options.taxonomy ?? taxonomy);
    }
    if (request.method === "GET" && url.pathname.endsWith("/analytics/runs")) {
      return Response.json(options.analytics ?? [analytics]);
    }
    if (request.method === "GET" && url.pathname.endsWith(`/blueprints/${ids.blueprint}`)) {
      return Response.json(blueprint);
    }
    if (request.method === "GET" && url.pathname.endsWith("/blueprints")) {
      return Response.json(options.blueprints ?? []);
    }
    if (request.method === "POST" && url.pathname.endsWith("/blueprints")) {
      await options.beforeCreate?.();
      const status = createStatuses.shift() ?? 201;
      if (status === 422) {
        return Response.json(
          {
            detail: {
              code: "blueprint_constraint_violation",
              constraint: "section.A.marks",
              impossible: true,
              message: "cannot compose the requested exact marks",
              violation: "section_marks_impossible",
            },
          },
          { status },
        );
      }
      if (status === 409) {
        return Response.json(
          { detail: { code: "blueprint_fingerprint_conflict" } },
          { status },
        );
      }
      if (status === 403) {
        return Response.json({ detail: { code: "permission_denied" } }, { status });
      }
      return Response.json(
        { ...blueprint, deduplicated: status === 200 },
        { status },
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
  render(<BlueprintStudio role={role} />);
  await screen.findByRole("heading", { name: "Blueprint Studio" });
  await waitFor(() =>
    expect(screen.queryByText("Loading blueprint workspace…")).not.toBeInTheDocument(),
  );
  const availableCurricula = options.curricula ?? [curriculum, gradeSixCurriculum];
  if (availableCurricula.some((item) => item.active && item.exam_configuration_id === ids.exam)) {
    await waitFor(() =>
      expect(
        fixture.requests.some(
          (request) => request.method === "GET" && request.url.endsWith("/taxonomy/nodes"),
        ),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(
        screen.queryByText("Loading reviewed taxonomy, analytics and blueprints…"),
      ).not.toBeInTheDocument(),
    );
  }
  return fixture;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("BlueprintStudio", () => {
  it("keeps the in-flight curriculum load when the auto-selected scope is selected again", async () => {
    let releaseTaxonomy: (() => void) | undefined;
    const taxonomyGate = new Promise<void>((resolve) => {
      releaseTaxonomy = resolve;
    });
    const { fetchMock } = fixtureApi({
      beforeTaxonomyLoad: () => taxonomyGate,
      curricula: [curriculum],
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<BlueprintStudio role="admin" />);

    const curriculumSelect = await screen.findByLabelText("Active Grade 5 curriculum");
    await waitFor(() => expect((curriculumSelect as HTMLSelectElement).value).toBe(ids.curriculum));
    fireEvent.change(curriculumSelect, { target: { value: ids.curriculum } });
    await act(async () => releaseTaxonomy?.());

    const taxonomySelect = await screen.findByLabelText("Taxonomy target 1");
    expect(within(taxonomySelect).getByRole("option", { name: /Recognise polygons/ })).toBeVisible();
    expect(
      fetchMock.mock.calls.filter(([input, init]) => {
        const request = asRequest(input as RequestInfo | URL, init as RequestInit | undefined);
        return request.method === "GET" && request.url.endsWith("/taxonomy/nodes");
      }),
    ).toHaveLength(1);
  });

  it("guides an admin through bounded exact controls and sends a typed baseline-only request", async () => {
    const { requests } = await renderLoaded();

    const curriculumSelect = screen.getByLabelText("Active Grade 5 curriculum");
    expect(within(curriculumSelect).getByRole("option", { name: curriculum.title })).toBeVisible();
    expect(within(curriculumSelect).queryByRole("option", { name: gradeSixCurriculum.title })).toBeNull();

    const taxonomySelect = await screen.findByLabelText("Taxonomy target 1");
    expect(within(taxonomySelect).getByRole("option", { name: /Recognise polygons/ })).toBeVisible();
    expect(within(taxonomySelect).queryByRole("option", { name: /Unreviewed skill/ })).toBeNull();
    fireEvent.change(taxonomySelect, { target: { value: ids.skill } });

    expect(screen.getByRole("heading", { name: "Paper metadata" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Sections" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Exact question-type allocations" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Exact difficulty allocations" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Taxonomy coverage" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Generation policy" })).toBeVisible();
    expect(screen.getByText(/same inputs, reviewed taxonomy snapshot, analytics linkage, config and seed/i)).toBeVisible();
    expect(screen.queryByLabelText(/forecast/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add section" }));
    expect(screen.getByLabelText("Section 2 identifier")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Remove section 2" }));
    expect(screen.queryByLabelText("Section 2 identifier")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Generate immutable blueprint" }));

    await screen.findByText("Blueprint created and persisted.");
    const post = requests.find(
      (request) => request.method === "POST" && new URL(request.url).pathname.endsWith("/blueprints"),
    );
    expect(post).toBeDefined();
    const body = (await post?.json()) as BlueprintRequest;
    expect(body).toMatchObject({
      analytics_run_id: null,
      seed: 0,
      specification: {
        curriculum_scope: { curriculum_version_id: ids.curriculum, grade: 5, medium: "si" },
        difficulty_allocations: [{ difficulty: "medium", exact_marks: 2, exact_slots: 1 }],
        question_type_allocations: [
          {
            archetypes: ["single_best_answer"],
            exact_marks: 2,
            exact_slots: 1,
            question_type: "multiple_choice",
          },
        ],
        sections: [{ marks: 2, question_count: 1, section_id: "A" }],
        taxonomy_requirements: [
          {
            maximum_slots: 1,
            minimum_slots: 1,
            priority: {
              baseline_evidence_refs: ["curriculum:reviewed-taxonomy"],
              baseline_score: 100,
              baseline_version: "syllabus-balanced-v1",
            },
            target: { competency_id: ids.competency, skill_id: ids.skill },
          },
        ],
        total_marks: 2,
      },
    });
    expect(JSON.stringify(body)).not.toContain("forecast_");
    expect(await screen.findByRole("heading", { name: "Immutable blueprint snapshot" })).toBeVisible();
    expect(screen.getByText("G5-PRACTICE-01-A-001")).toBeVisible();
  });

  it("validates mark and slot totals before making the authoritative backend request", async () => {
    const { requests } = await renderLoaded();
    await screen.findByLabelText("Taxonomy target 1");

    fireEvent.change(screen.getByLabelText("Paper total marks"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate immutable blueprint" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Section marks must total the paper marks",
    );
    expect(requests.filter((request) => request.method === "POST")).toHaveLength(0);

    fireEvent.change(screen.getByLabelText("Paper total marks"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Medium exact slots"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate immutable blueprint" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Difficulty slots must total the section question count",
    );
    expect(requests.filter((request) => request.method === "POST")).toHaveLength(0);
  });

  it("links only a same-curriculum persisted analytics run without exposing forecast evidence inputs", async () => {
    const { requests } = await renderLoaded();
    await screen.findByLabelText("Taxonomy target 1");

    fireEvent.change(screen.getByLabelText("Persisted analytics evidence (optional)"), {
      target: { value: ids.analytics },
    });
    expect(screen.getByText(/server will derive forecast and baseline evidence/i)).toBeVisible();
    expect(screen.queryByLabelText("Baseline score for taxonomy target 1")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/forecast/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Generate immutable blueprint" }));
    await screen.findByText("Blueprint created and persisted.");
    const post = requests.find((request) => request.method === "POST");
    const body = (await post?.json()) as BlueprintRequest;
    expect(body.analytics_run_id).toBe(ids.analytics);
    expect(JSON.stringify(body)).not.toContain("forecast_");
  });

  it("discards an in-flight generation response after the curriculum scope changes", async () => {
    let releaseCreate: (() => void) | undefined;
    const createGate = new Promise<void>((resolve) => {
      releaseCreate = resolve;
    });
    const { requests } = await renderLoaded("admin", {
      beforeCreate: () => createGate,
      curricula: [curriculum, otherCurriculum],
    });
    await screen.findByLabelText("Taxonomy target 1");

    fireEvent.click(screen.getByRole("button", { name: "Generate immutable blueprint" }));
    await waitFor(() =>
      expect(requests.some((request) => request.method === "POST")).toBe(true),
    );
    fireEvent.change(screen.getByLabelText("Active Grade 5 curriculum"), {
      target: { value: ids.otherCurriculum },
    });
    await waitFor(() =>
      expect((screen.getByLabelText("Taxonomy target 1") as HTMLSelectElement).value).toBe(
        ids.skill,
      ),
    );
    await act(async () => releaseCreate?.());
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Generate immutable blueprint" })).toBeEnabled(),
    );

    expect(screen.queryByRole("heading", { name: "Immutable blueprint snapshot" })).toBeNull();
    expect(screen.queryByText("Blueprint created and persisted.")).toBeNull();
  });

  it("keeps reviewers read-only while allowing complete immutable inspection", async () => {
    await renderLoaded("reviewer", { blueprints: [summaryFromBlueprint(blueprint)] });

    expect(await screen.findByText("Reviewer read access")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Reviewer read-only mode" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Generate immutable blueprint" })).toBeNull();
    expect(await screen.findByRole("heading", { name: "Immutable blueprint snapshot" })).toBeVisible();
    expect(screen.getByText("paper-blueprint-v1")).toBeVisible();
    expect(screen.getByText("deterministic-blueprint-v1")).toBeVisible();
    expect(screen.getByText("Syllabus-balanced baseline selected because no analytics run was linked.")).toBeVisible();
    expect(screen.getByText("curriculum:reviewed-taxonomy")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Reviewed taxonomy snapshot" })).toBeVisible();
    expect(screen.getByText("Recognise polygons")).toBeVisible();
    expect(screen.getByText(/immutable and cannot be edited/i)).toBeVisible();
  });

  it("surfaces impossible, conflict, and permission responses with safe retries", async () => {
    await renderLoaded("admin", { createStatuses: [422, 409, 403] });
    await screen.findByLabelText("Taxonomy target 1");

    fireEvent.click(screen.getByRole("button", { name: "Generate immutable blueprint" }));
    expect(await screen.findByRole("heading", { name: "Blueprint constraints are impossible" })).toBeVisible();
    expect(screen.getByText(/section\.A\.marks/)).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Retry blueprint generation" }));
    expect(await screen.findByRole("heading", { name: "Immutable blueprint conflict" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Retry blueprint generation" }));
    expect(await screen.findByRole("heading", { name: "Generation permission required" })).toBeVisible();
  });

  it("renders explicit empty and workspace retry states", async () => {
    const first = fixtureApi({ rejectWorkspaceOnce: true });
    vi.stubGlobal("fetch", first.fetchMock);
    const { unmount } = render(<BlueprintStudio role="admin" />);
    expect(await screen.findByRole("heading", { name: "Blueprint workspace unavailable" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Retry workspace" }));
    expect(await screen.findByRole("heading", { name: "Blueprint Studio" })).toBeVisible();
    unmount();

    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    await renderLoaded("admin", { curricula: [gradeSixCurriculum] });
    expect(screen.getByRole("heading", { name: "No active Grade 5 curriculum available" })).toBeVisible();
  });

  it("requires reviewed active taxonomy and presents an empty immutable list", async () => {
    await renderLoaded("admin", { analytics: [], taxonomy: [taxonomy[2]] });

    expect(await screen.findByRole("heading", { name: "No reviewed taxonomy targets" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "No blueprints yet" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Generate immutable blueprint" })).toBeDisabled();
  });

  it("has no automated accessibility violations in the loaded admin state", async () => {
    const fixture = fixtureApi();
    vi.stubGlobal("fetch", fixture.fetchMock);
    const { container } = render(<BlueprintStudio role="admin" />);
    await waitFor(() =>
      expect((screen.getByLabelText("Taxonomy target 1") as HTMLSelectElement).value).toBe(
        ids.skill,
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByText("Loading reviewed taxonomy, analytics and blueprints…"),
      ).not.toBeInTheDocument(),
    );

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });
});
