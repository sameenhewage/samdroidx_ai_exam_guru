import type { components } from "@exam-guru/api-client";
import { fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AnalyticsReportStudio } from "./analytics-report-studio";

type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
type Fraction = components["schemas"]["ExactFraction"];
type AnalyticsRun = components["schemas"]["AnalyticsRunResponse"];
type AnalyticsSummary = components["schemas"]["AnalyticsRunSummaryResponse"];
type PriorityRun = components["schemas"]["PracticePriorityRunResponse"];

const ids = {
  actor: "00000000-0000-0000-0000-000000000901",
  competency: "00000000-0000-0000-0000-000000000401",
  curriculum: "00000000-0000-0000-0000-000000000101",
  document2019: "00000000-0000-0000-0000-000000000501",
  document2020: "00000000-0000-0000-0000-000000000502",
  exam: "00000000-0000-0000-0000-000000000201",
  inactiveCurriculum: "00000000-0000-0000-0000-000000000102",
  medium: "00000000-0000-0000-0000-000000000301",
  question2019: "00000000-0000-0000-0000-000000000601",
  question2020: "00000000-0000-0000-0000-000000000602",
  questionExcluded: "00000000-0000-0000-0000-000000000603",
  run: "00000000-0000-0000-0000-000000000801",
  skill: "00000000-0000-0000-0000-000000000402",
} as const;

function fraction(numerator: number, denominator: number): Fraction {
  return { denominator, numerator };
}

const curriculum = {
  active: true,
  code: "G5-SI-2026",
  created_at: "2026-08-24T00:00:00Z",
  exam_configuration_id: ids.exam,
  id: ids.curriculum,
  medium_id: ids.medium,
  title: "Grade 5 Sinhala 2026",
  updated_at: "2026-08-24T00:00:00Z",
} satisfies Curriculum;

const inactiveCurriculum = {
  ...curriculum,
  active: false,
  code: "G5-SI-OLD",
  id: ids.inactiveCurriculum,
  title: "Inactive analytics curriculum",
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
] satisfies TaxonomyNode[];

const sources = [
  { source_document_id: ids.document2019, source_version: "sha256:source-2019" },
  { source_document_id: ids.document2020, source_version: "sha256:source-2020" },
] satisfies components["schemas"]["SourceVersionResponse"][];

function priorityRun(
  method: components["schemas"]["PracticePriorityMethod"],
  recommendation: components["schemas"]["PracticeRecommendation"],
): PriorityRun {
  return {
    algorithm_version:
      method === "syllabus_balanced"
        ? "syllabus-balanced-baseline-v1"
        : "deterministic-practice-priority-v1",
    config_fingerprint: `sha256:${method}-config`,
    curriculum_version_id: ids.curriculum,
    evidence_through_year: 2019,
    feature_definitions: [
      "Frequency and marks are calculated from reviewed historical observations.",
      "Recency is descriptive practice-priority evidence, not an exam prediction.",
    ],
    input_observation_ids: [ids.question2019],
    method,
    priorities: [
      {
        competency_id: ids.competency,
        evidence_language: "Reviewed evidence supports syllabus-balanced practice for this skill.",
        features: {
          evidence_marks: 2,
          evidence_question_count: 1,
          last_observed_year: 2019,
          marks_share: fraction(1, 1),
          question_frequency_share: fraction(1, 1),
          recency_gap_share: fraction(1, 1),
          syllabus_share: fraction(1, 1),
        },
        practice_share: fraction(1, 1),
        rank: 1,
        skill_id: ids.skill,
        skill_title: "Recognise polygons",
      },
    ],
    random_seed: null,
    recommendation,
    run_fingerprint: `sha256:${method}-run`,
    sources: [sources[0]],
    target_year: 2020,
  };
}

const methodMetrics = {
  competency_distribution_accuracy: fraction(4, 5),
  competency_distribution_error: fraction(1, 5),
  composite_score: fraction(3, 4),
  skill_distribution_accuracy: fraction(3, 4),
  skill_distribution_error: fraction(1, 4),
  top_k_skill_hit_rate: fraction(1, 1),
} satisfies components["schemas"]["BacktestMetricsResponse"];

const baselineMetrics = {
  competency_distribution_accuracy: fraction(4, 5),
  competency_distribution_error: fraction(1, 5),
  composite_score: fraction(7, 10),
  skill_distribution_accuracy: fraction(7, 10),
  skill_distribution_error: fraction(3, 10),
  top_k_skill_hit_rate: fraction(1, 1),
} satisfies components["schemas"]["BacktestMetricsResponse"];

const run = {
  compute_duration_ms: 17,
  config: {
    meaningful_improvement: fraction(1, 100),
    minimum_training_years: 1,
    priority_weights: {
      frequency: fraction(1, 4),
      marks: fraction(1, 4),
      recency: fraction(1, 4),
      syllabus: fraction(1, 4),
    },
    synchronous_limits: { maximum_records: 5000, maximum_years: 50 },
    top_k_skills: 1,
  },
  config_fingerprint: "sha256:analytics-config",
  created_at: "2026-08-24T09:30:00Z",
  created_by: ids.actor,
  curriculum_version_id: ids.curriculum,
  data_quality: {
    considered_count: 3,
    excluded_count: 1,
    exclusions: [
      {
        count: 1,
        question_ids: [ids.questionExcluded],
        reason: "not_reviewed",
      },
    ],
    included_count: 2,
  },
  deduplicated: false,
  id: ids.run,
  input: {
    observation_fingerprint: "sha256:observations",
    observation_ids: [ids.question2019, ids.question2020],
    selection_fingerprint: "sha256:selection",
    syllabus: [
      {
        balance_weight: 1,
        competency_id: ids.competency,
        curriculum_version_id: ids.curriculum,
        skill_id: ids.skill,
        title: "Recognise polygons",
      },
    ],
    years: [2019, 2020],
  },
  input_fingerprint: "sha256:input",
  result: {
    backtest: {
      aggregate: {
        baseline_delta: fraction(1, 20),
        baseline_score_variance: fraction(0, 1),
        mean_baseline_score: fraction(7, 10),
        mean_method_score: fraction(3, 4),
        method_score_variance: fraction(0, 1),
        window_count: 1,
      },
      backtest_version: "rolling-heldout-backtest-v1",
      config_fingerprint: "sha256:backtest-config",
      input_fingerprint: "sha256:backtest-input",
      limitations: [
        "Small reviewed samples limit generalisation and do not establish future exam certainty.",
        "The syllabus-balanced baseline remains the safer practice fallback when improvement is not meaningful.",
      ],
      recommendation: {
        language:
          "Observed improvement did not meet the exact threshold; use syllabus-balanced practice.",
        meaningful_improvement: fraction(1, 100),
        mode: "syllabus_balanced_practice",
        observed_baseline_delta: fraction(1, 200),
        selected_method: "syllabus_balanced",
      },
      recommended_run: priorityRun("syllabus_balanced", "syllabus_balanced_practice"),
      sources,
      windows: [
        {
          baseline_delta: fraction(1, 20),
          baseline_metrics: baselineMetrics,
          baseline_run: priorityRun("syllabus_balanced", "syllabus_balanced_practice"),
          heldout_input_fingerprint: "sha256:heldout-2020",
          heldout_sources: [sources[1]],
          heldout_year: 2020,
          leakage_audit: {
            heldout_observation_ids: [ids.question2020],
            latest_training_year: 2019,
            overlapping_observation_ids: [],
            passed: true,
            training_cutoff_exclusive: 2020,
            training_observation_ids: [ids.question2019],
          },
          method_metrics: methodMetrics,
          method_run: priorityRun(
            "historical_evidence",
            "evidence_backed_practice",
          ),
          training_input_fingerprint: "sha256:training-through-2019",
          training_years: [2019],
        },
      ],
    },
    statistics: {
      algorithm_version: "historical-distributions-v1",
      competency_distribution: [
        {
          key: ids.competency,
          marks_share: fraction(1, 1),
          question_count: 2,
          question_share: fraction(1, 1),
          total_marks: 4,
        },
      ],
      curriculum_version_id: ids.curriculum,
      difficulty_distribution: [
        {
          key: "medium",
          marks_share: fraction(1, 1),
          question_count: 2,
          question_share: fraction(1, 1),
          total_marks: 4,
        },
      ],
      input_fingerprint: "sha256:statistics-input",
      input_observation_ids: [ids.question2019, ids.question2020],
      marks_distribution: [
        {
          key: 2,
          marks_share: fraction(1, 1),
          question_count: 2,
          question_share: fraction(1, 1),
          total_marks: 4,
        },
      ],
      observation_count: 2,
      question_type_distribution: [
        {
          key: "multiple_choice",
          marks_share: fraction(1, 1),
          question_count: 2,
          question_share: fraction(1, 1),
          total_marks: 4,
        },
      ],
      skill_distribution: [
        {
          key: ids.skill,
          marks_share: fraction(1, 1),
          question_count: 2,
          question_share: fraction(1, 1),
          total_marks: 4,
        },
      ],
      sources,
      total_marks: 4,
      years: [2019, 2020],
    },
  },
  result_fingerprint: "sha256:result",
  run_fingerprint: "sha256:run",
  source_fingerprint: "sha256:sources",
  sources,
  versions: {
    backtest: "rolling-heldout-backtest-v1",
    baseline: "syllabus-balanced-baseline-v1",
    practice_priority: "deterministic-practice-priority-v1",
    statistics: "historical-distributions-v1",
  },
} satisfies AnalyticsRun;

const summary = {
  aggregate: run.result.backtest.aggregate,
  backtest_algorithm_version: run.versions.backtest,
  baseline_algorithm_version: run.versions.baseline,
  config_fingerprint: run.config_fingerprint,
  created_at: run.created_at,
  created_by: run.created_by,
  curriculum_version_id: run.curriculum_version_id,
  excluded_count: run.data_quality.excluded_count,
  id: run.id,
  included_count: run.data_quality.included_count,
  input_fingerprint: run.input_fingerprint,
  practice_priority_algorithm_version: run.versions.practice_priority,
  recommendation: run.result.backtest.recommendation,
  result_fingerprint: run.result_fingerprint,
  run_fingerprint: run.run_fingerprint,
  source_fingerprint: run.source_fingerprint,
  statistics_algorithm_version: run.versions.statistics,
} satisfies AnalyticsSummary;

function asRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(input, init);
}

type FixtureOptions = {
  listStatuses?: number[];
  noRuns?: boolean;
  runStatuses?: number[];
};

function fixtureApi(options: FixtureOptions = {}) {
  const requests: Request[] = [];
  const listStatuses = [...(options.listStatuses ?? [200])];
  const runStatuses = [...(options.runStatuses ?? [201])];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    requests.push(request.clone());
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname.endsWith("/curriculum-versions")) {
      return Response.json([curriculum, inactiveCurriculum]);
    }
    if (request.method === "GET" && url.pathname.endsWith("/taxonomy/nodes")) {
      return Response.json(taxonomy);
    }
    if (
      request.method === "GET" &&
      url.pathname.endsWith(`/analytics/runs/${ids.run}`)
    ) {
      return Response.json(run);
    }
    if (request.method === "GET" && url.pathname.endsWith("/analytics/runs")) {
      const status = listStatuses.shift() ?? 200;
      if (status !== 200) {
        return Response.json(
          { detail: { code: status === 403 ? "permission_denied" : "service_unavailable" } },
          { status },
        );
      }
      return Response.json(options.noRuns ? [] : [summary]);
    }
    if (request.method === "POST" && url.pathname.endsWith("/analytics/runs")) {
      const status = runStatuses.shift() ?? 201;
      if (status === 403) {
        return Response.json({ detail: { code: "permission_denied" } }, { status });
      }
      if (status === 422) {
        return Response.json(
          {
            detail: {
              available_years: [2020],
              code: "analytics_insufficient_history",
              data_quality: {
                considered_count: 2,
                excluded_count: 1,
                exclusions: [
                  {
                    count: 1,
                    question_ids: [ids.questionExcluded],
                    reason: "not_reviewed",
                  },
                ],
                included_count: 1,
              },
              required_year_count: 2,
            },
          },
          { status },
        );
      }
      const body = (await request.clone().json()) as components["schemas"]["AnalyticsRunRequest"];
      return Response.json(
        {
          ...run,
          config: { ...run.config, meaningful_improvement: body.meaningful_improvement },
          deduplicated: status === 200,
        },
        { status },
      );
    }
    return Response.json({ detail: { code: "unexpected_request" } }, { status: 500 });
  });
  return { fetchMock, requests };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("AnalyticsReportStudio", () => {
  it("runs an exact bounded analysis and renders reproducible statistics and held-out evidence", async () => {
    const api = fixtureApi();
    vi.stubGlobal("fetch", api.fetchMock);
    const { container } = render(<AnalyticsReportStudio role="admin" />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading Analytics Report Studio");
    expect(
      await screen.findByRole("heading", { name: "Analytics Report Studio" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Active analytics curriculum")).toHaveValue(ids.curriculum);
    expect(screen.queryByRole("option", { name: inactiveCurriculum.title })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Analysis report" })).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Data quality" })).toBeInTheDocument();
    expect(screen.getByText("Not reviewed")).toBeInTheDocument();
    expect(screen.getByText(ids.questionExcluded)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Historical distributions" })).toBeInTheDocument();
    expect(screen.getAllByText("Shape and space").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Recognise polygons").length).toBeGreaterThan(0);
    expect(screen.getByText("Multiple choice")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Baseline comparison" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Rolling held-out windows" })).toBeInTheDocument();
    expect(screen.getByText("Leakage audit passed")).toBeInTheDocument();
    expect(screen.getByText("sha256:training-through-2019")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Syllabus-balanced practice fallback" })).toBeInTheDocument();
    expect(screen.getByText(/does not predict future exam questions/i)).toBeInTheDocument();
    expect(screen.getAllByText("1 / 100").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Limitations" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Provenance & fingerprints" })).toBeInTheDocument();
    expect(screen.getByText("sha256:sources")).toBeInTheDocument();
    expect(screen.getAllByText(ids.document2019).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText("Minimum training years"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByLabelText("Top skills to evaluate"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByLabelText("Meaningful improvement numerator"), {
      target: { value: "2" },
    });
    fireEvent.change(screen.getByLabelText("Meaningful improvement denominator"), {
      target: { value: "125" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

    expect(await screen.findByText("Analysis run created.")).toBeInTheDocument();
    const post = api.requests.find(
      (request) => request.method === "POST" && request.url.endsWith("/analytics/runs"),
    );
    expect(post).toBeDefined();
    await expect(post?.json()).resolves.toEqual({
      meaningful_improvement: { denominator: 125, numerator: 2 },
      minimum_training_years: 1,
      top_k_skills: 1,
    });
    expect(screen.getByText("2 / 125")).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
  });

  it("gives reviewers read-only access and an explicit no-data state", async () => {
    const api = fixtureApi({ noRuns: true });
    vi.stubGlobal("fetch", api.fetchMock);
    const { container } = render(<AnalyticsReportStudio role="reviewer" />);

    expect(
      await screen.findByRole("heading", { name: "No analytics runs yet" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Reviewer read access")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run analysis" })).not.toBeInTheDocument();
    expect(screen.getByText(/administrator must create the first bounded run/i)).toBeInTheDocument();
    expect(
      api.requests.filter((request) => request.method === "POST"),
    ).toHaveLength(0);

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });

  it("rejects a non-positive or greater-than-one exact threshold before POST", async () => {
    const api = fixtureApi();
    vi.stubGlobal("fetch", api.fetchMock);
    render(<AnalyticsReportStudio role="admin" />);

    await screen.findByRole("heading", { name: "Analysis report" });
    fireEvent.change(screen.getByLabelText("Meaningful improvement numerator"), {
      target: { value: "101" },
    });
    fireEvent.change(screen.getByLabelText("Meaningful improvement denominator"), {
      target: { value: "100" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Meaningful improvement must be greater than zero and no greater than one.",
    );
    expect(
      api.requests.filter((request) => request.method === "POST"),
    ).toHaveLength(0);
  });

  it("surfaces run permission denial and actionable insufficient-history quality evidence", async () => {
    const forbiddenApi = fixtureApi({ runStatuses: [403] });
    vi.stubGlobal("fetch", forbiddenApi.fetchMock);
    const firstRender = render(<AnalyticsReportStudio role="admin" />);
    await screen.findByRole("heading", { name: "Analysis report" });
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Run permission required");
    firstRender.unmount();

    const insufficientApi = fixtureApi({ runStatuses: [422] });
    vi.stubGlobal("fetch", insufficientApi.fetchMock);
    render(<AnalyticsReportStudio role="admin" />);
    await screen.findByRole("heading", { name: "Analysis report" });
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Insufficient eligible history");
    expect(alert).toHaveTextContent("Available years: 2020");
    expect(alert).toHaveTextContent("Not reviewed: 1");
    expect(within(alert).getByRole("link", { name: "Review historical questions" })).toHaveAttribute(
      "href",
      "/admin/knowledge",
    );
  });

  it("retries report-list service errors and restores the selectable run", async () => {
    const api = fixtureApi({ listStatuses: [503, 200] });
    vi.stubGlobal("fetch", api.fetchMock);
    render(<AnalyticsReportStudio role="reviewer" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Analytics reports temporarily unavailable",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry reports" }));
    expect(await screen.findByRole("heading", { name: "Analysis report" })).toBeInTheDocument();
    expect(
      api.requests.filter(
        (request) => request.method === "GET" && request.url.includes("/analytics/runs?"),
      ),
    ).toHaveLength(2);
  });

  it("has no automated accessibility violations for a complete report", async () => {
    const api = fixtureApi();
    vi.stubGlobal("fetch", api.fetchMock);
    const { container } = render(<AnalyticsReportStudio role="reviewer" />);

    await screen.findByRole("heading", { name: "Analysis report" });
    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });
});
