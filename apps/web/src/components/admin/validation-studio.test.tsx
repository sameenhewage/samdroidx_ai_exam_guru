import type { components } from "@exam-guru/api-client";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ValidationStudio } from "./validation-studio";

type Generation = components["schemas"]["GenerationRunSummaryResponse"];
type Report = components["schemas"]["ValidationRunResponse"];
type ReportSummary = components["schemas"]["ValidationRunSummaryResponse"];
type Finding = components["schemas"]["ValidationFindingResponse"];

const ids = {
  actor: "00000000-0000-0000-0000-000000000901",
  attempt: "00000000-0000-0000-0000-000000000801",
  curriculum: "00000000-0000-0000-0000-000000000101",
  exam: "00000000-0000-0000-0000-000000000201",
  generation: "00000000-0000-0000-0000-000000000701",
  medium: "00000000-0000-0000-0000-000000000301",
  otherCurriculum: "00000000-0000-0000-0000-000000000102",
  pending: "00000000-0000-0000-0000-000000000702",
  report: "00000000-0000-0000-0000-000000000601",
} as const;
const now = "2026-08-24T09:30:00Z";
const hash = (marker: string) => marker.repeat(64).slice(0, 64);

function deferred<Value>() {
  let resolve!: (value: Value) => void;
  const promise = new Promise<Value>((fulfill) => {
    resolve = fulfill;
  });
  return { promise, resolve };
}

const succeeded = {
  attempt_count: 1,
  completed_at: now,
  cost_microusd: 10,
  created_at: now,
  created_by: ids.actor,
  curriculum_version_id: ids.curriculum,
  disposition: "requires_validation",
  failure_code: null,
  id: ids.generation,
  latency_ms: 8,
  model: "deterministic-fixture",
  paper_blueprint_id: "00000000-0000-0000-0000-000000000501",
  prompt_version: "prompt.v1",
  provider: "deterministic-fake",
  request_fingerprint: hash("a"),
  retry_depth: 0,
  retry_of_run_id: null,
  slot_id: "P8-A-001",
  started_at: now,
  status: "succeeded",
  total_tokens: 42,
  version: 2,
} satisfies Generation;
const pending = {
  ...succeeded,
  attempt_count: 0,
  completed_at: null,
  disposition: null,
  id: ids.pending,
  started_at: null,
  status: "pending",
  version: 1,
} satisfies Generation;

const report = {
  candidate_fingerprint: hash("b"),
  created_at: now,
  created_by: ids.actor,
  curriculum_version_id: ids.curriculum,
  deduplicated: false,
  duplicate_reference_count: 1,
  finding_count: 12,
  generation_attempt_id: ids.attempt,
  generation_result_fingerprint: hash("c"),
  generation_run_id: ids.generation,
  grounding_source_count: 1,
  id: ids.report,
  input_fingerprint: hash("d"),
  input_schema_version: "validation-input.v1",
  input_snapshot: {
    trust: "server_reconstructed",
    generation: {
      generation_run_id: ids.generation,
      model: "deterministic-fixture",
      model_version: "fixture.2026",
      prompt_version: "prompt.v1",
      provider: "deterministic-fake",
      provider_version: "provider.v1",
      retrieval_version: "retrieval.v1",
      generation_schema_version: "question.v1",
    },
    grounding_sources: [
      {
        chunk_id: "chunk-1",
        context_id: "knowledge_chunk:chunk-1",
        page_number: 4,
        source_document_id: "document-1",
        source_version: "reviewed.v3",
        text: "<img src=x onerror=alert(1)> Four is even.\u0000",
        trust: "untrusted_data",
      },
    ],
    duplicate_references: [
      {
        content_sha256: hash("e"),
        provenance: { record_id: "historical-1", source_document_id: "document-2" },
        question_id: "historical_question:historical-1",
        text: null,
      },
    ],
  },
  limitations: ["This deterministic report does not replace qualified human review."],
  overall_status: "pass",
  pipeline_fingerprint: hash("f"),
  pipeline_version: "canonical-validation.v1",
  report_fingerprint: hash("9"),
  report_schema_version: "validation-report.v1",
  validator_count: 6,
  validator_lineage: [
    { validator_id: "grounding-provenance", validator_version: "1.2.0" },
    { validator_id: "duplicate-similarity", validator_version: "1.1.0" },
  ],
} satisfies Report;

function summary(value: Report): ReportSummary {
  return {
    candidate_fingerprint: value.candidate_fingerprint,
    created_at: value.created_at,
    created_by: value.created_by,
    curriculum_version_id: value.curriculum_version_id,
    deduplicated: value.deduplicated,
    duplicate_reference_count: value.duplicate_reference_count,
    finding_count: value.finding_count,
    generation_attempt_id: value.generation_attempt_id,
    generation_result_fingerprint: value.generation_result_fingerprint,
    generation_run_id: value.generation_run_id,
    grounding_source_count: value.grounding_source_count,
    id: value.id,
    input_fingerprint: value.input_fingerprint,
    overall_status: value.overall_status,
    pipeline_fingerprint: value.pipeline_fingerprint,
    pipeline_version: value.pipeline_version,
    report_fingerprint: value.report_fingerprint,
    validator_count: value.validator_count,
  };
}

function finding(ordinal: number): Finding {
  return {
    code: ordinal === 2 ? "grounding_reference_warning" : "schema_complete",
    created_at: now,
    evidence: [{ location: `$.candidate[${ordinal}]`, note: ordinal === 2 ? "<script>unsafe()</script>" : "bounded evidence" }],
    evidence_count: 1,
    id: `00000000-0000-0000-0000-${String(ordinal).padStart(12, "0")}`,
    message: ordinal === 2 ? "Grounding needs human interpretation." : `Deterministic check ${ordinal} completed.`,
    ordinal,
    status: ordinal === 2 ? "warn" : "pass",
    validation_run_id: ids.report,
    validator_id: ordinal === 2 ? "grounding-provenance" : "schema-contract",
    validator_version: "1.0.0",
  };
}

type FixtureOptions = {
  createDeduplicated?: boolean;
  curriculaStatus?: number;
  generations?: Generation[];
  reports?: ReportSummary[];
};

function fixtureApi(options: FixtureOptions = {}) {
  const requests: Request[] = [];
  const findings = Array.from({ length: 12 }, (_, index) => finding(index + 1));
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init);
    requests.push(request.clone());
    const url = new URL(request.url);
    const path = url.pathname;
    if (request.method === "GET" && path.endsWith("/exam-configurations")) {
      return Response.json([{ active: true, code: "G5", created_at: now, grade: 5, id: ids.exam, name: "Grade 5", updated_at: now }]);
    }
    if (request.method === "GET" && path.endsWith("/media")) {
      return Response.json([{ active: true, code: "en", created_at: now, id: ids.medium, name: "English", updated_at: now }]);
    }
    if (request.method === "GET" && path.endsWith("/curriculum-versions")) {
      if (options.curriculaStatus) return Response.json({ detail: { code: "permission_denied" } }, { status: options.curriculaStatus });
      return Response.json([{ active: true, code: "G5-EN", created_at: now, exam_configuration_id: ids.exam, id: ids.curriculum, medium_id: ids.medium, title: "Grade 5 English", updated_at: now }]);
    }
    if (request.method === "GET" && path.endsWith("/generation-runs")) {
      return Response.json(options.generations ?? [pending, succeeded]);
    }
    if (request.method === "GET" && path.endsWith("/validation-runs")) {
      return Response.json(options.reports ?? [summary(report)]);
    }
    if (request.method === "POST" && path.endsWith("/validation-runs")) {
      return Response.json({ ...report, deduplicated: options.createDeduplicated ?? true }, { status: 201 });
    }
    if (request.method === "GET" && path.endsWith(`/validation-runs/${ids.report}`)) {
      return Response.json(report);
    }
    if (request.method === "GET" && path.endsWith(`/validation-runs/${ids.report}/findings`)) {
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 10);
      return Response.json(findings.slice(offset, offset + limit));
    }
    return Response.json({ detail: { code: "unexpected_request" } }, { status: 500 });
  });
  return { fetchMock, requests };
}

async function renderLoaded(role: "admin" | "reviewer" = "admin", options: FixtureOptions = {}) {
  const fixture = fixtureApi(options);
  vi.stubGlobal("fetch", fixture.fetchMock);
  const view = render(<ValidationStudio role={role} />);
  await screen.findByRole("heading", { level: 1, name: "Validation Studio" });
  await waitFor(() => expect(screen.queryByText("Loading validation workspace…")).not.toBeInTheDocument());
  if (!options.curriculaStatus) {
    await waitFor(() => expect(fixture.requests.some((request) => request.method === "GET" && new URL(request.url).pathname.endsWith("/generation-runs"))).toBe(true));
    await waitFor(() => expect(screen.queryByText("Loading generation runs and validation reports…")).not.toBeInTheDocument());
  }
  return { ...fixture, ...view };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ValidationStudio", () => {
  it("prefers a succeeded requires-validation generation and POSTs exactly its ID", async () => {
    const { requests } = await renderLoaded("admin", { reports: [] });

    expect(screen.getByLabelText("Generation run")).toHaveValue(ids.generation);
    expect(screen.getByText(/Preferred: succeeded and requires validation/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Run deterministic validation" }));

    await screen.findByText("Existing immutable report reused; no duplicate report was created.");
    const post = requests.find((request) => request.method === "POST");
    expect(post).toBeDefined();
    const payload = (await post!.json()) as Record<string, unknown>;
    expect(payload).toEqual({ generation_run_id: ids.generation });
    expect(Object.keys(payload)).toEqual(["generation_run_id"]);
  });

  it("ignores stale list responses after the curriculum scope changes", async () => {
    const oldGenerations = deferred<Response>();
    const oldReports = deferred<Response>();
    const requests: Request[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init);
      requests.push(request.clone());
      const path = new URL(request.url).pathname;
      if (request.method === "GET" && path.endsWith("/exam-configurations")) {
        return Response.json([
          {
            active: true,
            code: "G5",
            created_at: now,
            grade: 5,
            id: ids.exam,
            name: "Grade 5",
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
        return Response.json([
          {
            active: true,
            code: "G5-EN-A",
            created_at: now,
            exam_configuration_id: ids.exam,
            id: ids.curriculum,
            medium_id: ids.medium,
            title: "Grade 5 English A",
            updated_at: now,
          },
          {
            active: true,
            code: "G5-EN-B",
            created_at: now,
            exam_configuration_id: ids.exam,
            id: ids.otherCurriculum,
            medium_id: ids.medium,
            title: "Grade 5 English B",
            updated_at: now,
          },
        ]);
      }
      if (request.method === "GET" && path.endsWith("/generation-runs")) {
        return path.includes(`/curricula/${ids.curriculum}/`)
          ? oldGenerations.promise
          : Response.json([]);
      }
      if (request.method === "GET" && path.endsWith("/validation-runs")) {
        return path.includes(`/curricula/${ids.curriculum}/`)
          ? oldReports.promise
          : Response.json([]);
      }
      return Response.json({ detail: { code: "unexpected_request" } }, { status: 500 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ValidationStudio role="admin" />);

    const curriculumSelect = await screen.findByLabelText("Active Grade 5 curriculum");
    await waitFor(() =>
      expect(
        requests.filter((request) => {
          const path = new URL(request.url).pathname;
          return (
            path.includes(`/curricula/${ids.curriculum}/`) &&
            (path.endsWith("/generation-runs") || path.endsWith("/validation-runs"))
          );
        }),
      ).toHaveLength(2),
    );
    fireEvent.change(curriculumSelect, { target: { value: ids.otherCurriculum } });
    expect(curriculumSelect).toHaveValue(ids.otherCurriculum);
    expect(await screen.findByRole("heading", { name: "No generation runs yet" })).toBeVisible();

    await act(async () => {
      oldGenerations.resolve(Response.json([pending, succeeded]));
      oldReports.resolve(Response.json([summary(report)]));
      await Promise.all([oldGenerations.promise, oldReports.promise]);
    });

    expect(
      screen.queryByRole("button", { name: `Select validation report ${ids.report}` }),
    ).not.toBeInTheDocument();
    expect(
      requests.some((request) =>
        new URL(request.url).pathname.includes(
          `/curricula/${ids.otherCurriculum}/validation-runs/${ids.report}`,
        ),
      ),
    ).toBe(false);
  });

  it("selects immutable reports, exposes lineage and provenance, and paginates bounded text findings", async () => {
    await renderLoaded();

    fireEvent.click(screen.getByRole("button", { name: `Select validation report ${ids.report}` }));
    expect(await screen.findByRole("heading", { name: "Deterministic result: Pass" })).toBeVisible();
    expect(screen.getAllByText(/does not establish factual correctness, semantic quality, curriculum approval, or language approval/i)[0]).toBeVisible();
    expect(screen.getAllByText(/Human review is still required/i)[0]).toBeVisible();
    const metadata = screen.getByRole("region", { name: "Validation report metadata" });
    expect(metadata).toHaveTextContent("canonical-validation.v1");
    expect(metadata).toHaveTextContent("validation-input.v1");
    expect(metadata).toHaveTextContent("grounding-provenance");
    expect(metadata).toHaveTextContent(hash("f"));
    expect(screen.getByRole("region", { name: "Grounding provenance" })).toHaveTextContent("document-1");
    expect(screen.getByRole("region", { name: "Duplicate provenance" })).toHaveTextContent("historical-1");
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText("<script>unsafe()</script>")).toBeVisible();
    expect(screen.getByText("Findings 1–10 of 12")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Next findings page" }));
    await screen.findByText("Findings 11–12 of 12");
    expect(screen.getByText("Deterministic check 11 completed.")).toBeVisible();
  });

  it("keeps reviewers read-only and handles empty, pending, and permission states", async () => {
    const first = await renderLoaded("reviewer", { generations: [pending], reports: [] });
    expect(screen.getByRole("heading", { name: "Reviewer read-only mode" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Run deterministic validation" })).not.toBeInTheDocument();
    expect(screen.getByText(/Pending generations are listed for context/i)).toBeVisible();
    expect(screen.getByRole("heading", { name: "No validation reports yet" })).toBeVisible();
    first.unmount();

    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    const second = await renderLoaded("admin", { generations: [], reports: [] });
    expect(screen.getByRole("heading", { name: "No generation runs yet" })).toBeVisible();
    second.unmount();

    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    await renderLoaded("reviewer", { curriculaStatus: 403 });
    expect(screen.getByRole("alert")).toHaveTextContent("Validation workspace permission required");
  });

  it("has no automated accessibility violations in a loaded report", async () => {
    const { container } = await renderLoaded("reviewer");
    await screen.findByRole("region", { name: "Validation report metadata" });
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
});
