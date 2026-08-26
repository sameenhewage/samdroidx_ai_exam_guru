import type { components } from "@exam-guru/api-client";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReviewerStudio } from "./reviewer-studio";

type Candidate = components["schemas"]["ReviewCandidateResponse"];
type CandidateSummary = components["schemas"]["ReviewCandidateSummaryResponse"];
type Generation = components["schemas"]["GenerationRunResponse"];
type Validation = components["schemas"]["ValidationRunResponse"];
type ValidationSummary = components["schemas"]["ValidationRunSummaryResponse"];
type Finding = components["schemas"]["ValidationFindingResponse"];

const ids = {
  actor: "00000000-0000-0000-0000-000000000901",
  attempt: "00000000-0000-0000-0000-000000000801",
  blueprint: "00000000-0000-0000-0000-000000000501",
  candidate: "00000000-0000-0000-0000-000000000701",
  curriculum: "00000000-0000-0000-0000-000000000101",
  exam: "00000000-0000-0000-0000-000000000201",
  finding: "00000000-0000-0000-0000-000000000601",
  generation: "00000000-0000-0000-0000-000000000701",
  medium: "00000000-0000-0000-0000-000000000301",
  validation: "00000000-0000-0000-0000-000000000401",
  warnedValidation: "00000000-0000-0000-0000-000000000402",
} as const;
const now = "2026-08-24T09:30:00Z";
const later = "2026-08-24T09:31:00Z";
const hash = (marker: string) => marker.repeat(64).slice(0, 64);

const originalContent = {
  answer: "B",
  explanation: "Four is even, so option B is supported.",
  marking_guide: ["Award two marks for selecting B."],
  marks: 2,
  options: [
    { option_id: "A", text: "Three" },
    { option_id: "B", text: "Four" },
  ],
  question_type: "multiple_choice" as const,
  stem: "Which number is even?",
};

const editedContent = {
  ...originalContent,
  explanation: "The reviewed source identifies four as even.",
  marking_guide: ["Award two marks for B.", "Award no marks for A."],
  options: [
    { option_id: "A", text: "The number three" },
    { option_id: "B", text: "The number four" },
  ],
  stem: "Which displayed number is even?",
};

function candidateFixture(
  state: Candidate["state"] = "in_review",
  version = state === "validated" ? 2 : state === "in_review" ? 3 : 5,
  content: Candidate["current_content"] = originalContent,
): Candidate {
  const started = {
    action: "started" as const,
    candidate_version: 3,
    created_at: now,
    reason: null,
    reviewer_id: ids.actor,
    revision: 1,
  };
  const edited = {
    action: "edited" as const,
    candidate_version: 4,
    created_at: later,
    reason: "Clarify the wording.",
    reviewer_id: ids.actor,
    revision: 2,
  };
  const terminal =
    state === "approved" || state === "rejected"
      ? {
          action: state,
          candidate_version: version,
          created_at: later,
          reason:
            state === "approved"
              ? "Source, answer, and wording reviewed."
              : "Answer is not uniquely supported.",
          reviewer_id: ids.actor,
          revision: content === originalContent ? 1 : 2,
        }
      : null;
  const hasEdit = content !== originalContent;
  return {
    blueprint_id: "bp_reviewer_fixture_v1",
    blueprint_slot_id: "REV-A-001",
    blueprint_version: "bp_reviewer_fixture_v1",
    created_at: now,
    created_by: ids.actor,
    current_content: content,
    current_revision: hasEdit ? 2 : 1,
    curriculum_version_id: ids.curriculum,
    deduplicated: false,
    events:
      state === "validated"
        ? []
        : [started, ...(hasEdit ? [edited] : []), ...(terminal ? [terminal] : [])],
    generation_attempt_id: ids.attempt,
    generation_run_id: ids.generation,
    id: ids.candidate,
    lineage: {
      blueprint_id: "bp_reviewer_fixture_v1",
      blueprint_slot_id: "REV-A-001",
      blueprint_version: "bp_reviewer_fixture_v1",
      generation_attempt_id: ids.attempt,
      generation_id: ids.generation,
      model_version: "fixture.2026",
      paper_blueprint_id: ids.blueprint,
      prompt_version: "prompt.v1",
      provenance: [
        {
          chunk_id: "chunk-1",
          page_number: 4,
          source_document_id: "document-1",
          source_version: "reviewed.v3",
        },
      ],
      provider: "deterministic-fake",
      retrieval_version: "retrieval.v1",
      schema_version: "question.v1",
    },
    paper_blueprint_id: ids.blueprint,
    revisions: [
      {
        candidate_version: 1,
        content: originalContent,
        created_at: now,
        reason: null,
        reviewer_id: null,
        revision: 1,
      },
      ...(hasEdit
        ? [
            {
              candidate_version: 4,
              content,
              created_at: later,
              reason: "Clarify the wording.",
              reviewer_id: ids.actor,
              revision: 2,
            },
          ]
        : []),
    ],
    state,
    validation: {
      finding_refs: [ids.finding],
      passed: true,
      validated_revision: 1,
      validation_run_id: ids.validation,
      validator_version: "canonical-validation.v1",
    },
    validation_run_id: ids.validation,
    version,
  };
}

function candidateSummary(value: Candidate): CandidateSummary {
  return {
    blueprint_id: value.blueprint_id,
    blueprint_slot_id: value.blueprint_slot_id,
    blueprint_version: value.blueprint_version,
    created_at: value.created_at,
    created_by: value.created_by,
    current_revision: value.current_revision,
    current_revision_created_at: later,
    curriculum_version_id: value.curriculum_version_id,
    generation_attempt_id: value.generation_attempt_id,
    generation_run_id: value.generation_run_id,
    id: value.id,
    marks: value.current_content.marks,
    paper_blueprint_id: value.paper_blueprint_id,
    question_type: value.current_content.question_type,
    state: value.state,
    stem_preview: value.current_content.stem,
    validation_run_id: value.validation_run_id,
    version: value.version,
  };
}

const generation = {
  attempt_count: 1,
  blueprint_id: "bp_reviewer_fixture_v1",
  blueprint_slot: {
    marks: 2,
    question_type: "multiple_choice",
    section_title: "Selection",
    slot_id: "REV-A-001",
    untrusted_instruction: "<img src=x onerror=alert(1)> Keep this as data",
  },
  blueprint_version: "bp_reviewer_fixture_v1",
  budgets: { max_attempts: 3, max_output_tokens: 2_048 },
  candidate: {
    answer: {
      correct_option_id: "B",
      explanation: "<script>unsafe()</script> Four is the even number.",
    },
    marking: { criteria: [{ description: "Select B", marks: 2 }], total_marks: 2 },
    options: [
      { option_id: "A", text: "Three" },
      { option_id: "B", text: "Four" },
    ],
    question_type: "multiple_choice",
    stem: "<img src=x onerror=alert(1)> Which number is even?",
  },
  completed_at: now,
  context: [
    {
      context_id: "knowledge_chunk:chunk-1",
      provenance: {
        chunk_id: "chunk-1",
        page_number: 4,
        source_document_id: "document-1",
        source_version: "reviewed.v3",
      },
      record_id: "chunk-1",
      record_kind: "knowledge_chunk",
      text: "<script>contextAttack()</script> Four is even.\u0000",
      trust: "untrusted_data",
    },
  ],
  cost_microusd: 10,
  created_at: now,
  created_by: ids.actor,
  curriculum_version_id: ids.curriculum,
  disposition: "requires_validation",
  failure_code: null,
  generation_parameters: { seed: 17, temperature: 0 },
  id: ids.generation,
  input_tokens: 20,
  latency_ms: 8,
  model: "deterministic-fixture",
  model_version: "fixture.2026",
  output_tokens: 22,
  paper_blueprint_id: ids.blueprint,
  pricing_version: "pricing.v1",
  prompt_id: "question-generation",
  prompt_version: "prompt.v1",
  provider: "deterministic-fake",
  provider_version: "provider.v1",
  request_fingerprint: hash("a"),
  retrieval_version: "retrieval.v1",
  retry_depth: 0,
  retry_of_run_id: null,
  schema_version: "question.v1",
  slot_id: "REV-A-001",
  started_at: now,
  status: "succeeded",
  total_tokens: 42,
  version: 2,
} satisfies Generation;

const validation = {
  candidate_fingerprint: hash("b"),
  created_at: now,
  created_by: ids.actor,
  curriculum_version_id: ids.curriculum,
  deduplicated: false,
  duplicate_reference_count: 0,
  finding_count: 1,
  generation_attempt_id: ids.attempt,
  generation_result_fingerprint: hash("c"),
  generation_run_id: ids.generation,
  grounding_source_count: 1,
  id: ids.validation,
  input_fingerprint: hash("d"),
  input_schema_version: "validation-input.v1",
  input_snapshot: { trust: "server_reconstructed" },
  limitations: ["Automated checks do not replace qualified human review."],
  overall_status: "pass",
  pipeline_fingerprint: hash("e"),
  pipeline_version: "canonical-validation.v1",
  report_fingerprint: hash("f"),
  report_schema_version: "validation-report.v1",
  validator_count: 2,
  validator_lineage: [
    { validator_id: "schema-contract", validator_version: "1.0.0" },
    { validator_id: "grounding-provenance", validator_version: "1.2.0" },
  ],
} satisfies Validation;

function validationSummary(value: Validation, overrides: Partial<ValidationSummary> = {}): ValidationSummary {
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
    ...overrides,
  };
}

const finding = {
  code: "grounding_reference_present",
  created_at: now,
  evidence: [{ note: "<script>findingAttack()</script> Grounding reference retained." }],
  evidence_count: 1,
  id: ids.finding,
  message: "Generated answer has persisted grounding provenance.",
  ordinal: 1,
  status: "pass",
  validation_run_id: ids.validation,
  validator_id: "grounding-provenance",
  validator_version: "1.2.0",
} satisfies Finding;

type FixtureOptions = {
  candidate?: Candidate;
  candidates?: CandidateSummary[];
  createGate?: Promise<void>;
  detailSequence?: Candidate[];
  editConflict?: boolean;
  editStatus?: number;
  queueStatus?: number;
  throwOnQueue?: boolean;
  validationSummaries?: ValidationSummary[];
  workspaceStatus?: number;
};

function fixtureApi(options: FixtureOptions = {}) {
  const requests: Request[] = [];
  let current = options.candidate ?? candidateFixture();
  let detailCalls = 0;
  let editConflictReturned = false;

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init);
    requests.push(request.clone());
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === "GET" && path.endsWith("/exam-configurations")) {
      return Response.json([
        { active: true, code: "G5", created_at: now, grade: 5, id: ids.exam, name: "Grade 5", updated_at: now },
      ]);
    }
    if (request.method === "GET" && path.endsWith("/media")) {
      return Response.json([
        { active: true, code: "en", created_at: now, id: ids.medium, name: "English", updated_at: now },
      ]);
    }
    if (request.method === "GET" && path.endsWith("/curriculum-versions")) {
      if (options.workspaceStatus) {
        return Response.json({ detail: { code: "permission_denied" } }, { status: options.workspaceStatus });
      }
      return Response.json([
        {
          active: true,
          code: "G5-EN",
          created_at: now,
          exam_configuration_id: ids.exam,
          id: ids.curriculum,
          medium_id: ids.medium,
          title: "Grade 5 English",
          updated_at: now,
        },
      ]);
    }
    if (request.method === "GET" && path.endsWith("/validation-runs")) {
      return Response.json(
        options.validationSummaries ?? [
          validationSummary(validation),
          validationSummary(validation, {
            id: ids.warnedValidation,
            overall_status: "warn",
            report_fingerprint: hash("9"),
          }),
        ],
      );
    }
    if (request.method === "GET" && path.endsWith("/review-candidates")) {
      if (options.throwOnQueue) throw new TypeError("network unavailable");
      if (options.queueStatus) {
        return Response.json({ detail: { code: options.queueStatus === 403 ? "permission_denied" : "request_failed" } }, { status: options.queueStatus });
      }
      return Response.json(options.candidates ?? [candidateSummary(current)]);
    }
    if (request.method === "POST" && path.endsWith("/review-candidates")) {
      await options.createGate;
      current = candidateFixture("validated");
      return Response.json(current, { status: 201 });
    }
    if (request.method === "GET" && path.endsWith(`/review-candidates/${ids.candidate}`)) {
      const reply = options.detailSequence?.[Math.min(detailCalls, options.detailSequence.length - 1)];
      detailCalls += 1;
      if (reply) current = reply;
      return Response.json(current);
    }
    if (request.method === "GET" && path.endsWith(`/generation-runs/${ids.generation}`)) {
      return Response.json(generation);
    }
    if (request.method === "GET" && path.endsWith(`/validation-runs/${ids.validation}`)) {
      return Response.json(validation);
    }
    if (request.method === "GET" && path.endsWith(`/validation-runs/${ids.validation}/findings`)) {
      return Response.json([finding]);
    }
    if (request.method === "POST" && path.endsWith(`/review-candidates/${ids.candidate}/start-review`)) {
      current = candidateFixture("in_review", 3);
      return Response.json(current);
    }
    if (request.method === "PATCH" && path.endsWith(`/review-candidates/${ids.candidate}`)) {
      if (options.editConflict && !editConflictReturned) {
        editConflictReturned = true;
        current = candidateFixture("in_review", 4, {
          ...originalContent,
          stem: "Authoritative concurrent stem",
        });
        return Response.json(
          { detail: { code: "review_candidate_version_conflict" } },
          { status: 409 },
        );
      }
      if (options.editStatus) {
        return Response.json(
          { detail: { code: "review_candidate_content_invalid" } },
          { status: options.editStatus },
        );
      }
      const payload = (await request.json()) as {
        content: Candidate["current_content"];
        expected_version: number;
        reason: string;
      };
      current = candidateFixture("in_review", payload.expected_version + 1, payload.content);
      return Response.json(current);
    }
    if (request.method === "POST" && path.endsWith(`/review-candidates/${ids.candidate}/approve`)) {
      current = candidateFixture("approved", current.version + 1, current.current_content);
      return Response.json(current);
    }
    if (request.method === "POST" && path.endsWith(`/review-candidates/${ids.candidate}/reject`)) {
      current = candidateFixture("rejected", current.version + 1, current.current_content);
      return Response.json(current);
    }
    return Response.json({ detail: { code: "unexpected_request" } }, { status: 500 });
  });

  return { fetchMock, requests };
}

async function renderLoaded(role: "admin" | "reviewer" = "reviewer", options: FixtureOptions = {}) {
  const fixture = fixtureApi(options);
  vi.stubGlobal("fetch", fixture.fetchMock);
  const view = render(<ReviewerStudio role={role} />);
  await screen.findByRole("heading", { level: 1, name: "Reviewer Studio" });
  await waitFor(() => expect(screen.queryByText("Loading reviewer workspace…")).not.toBeInTheDocument());
  if (!options.workspaceStatus) {
    await waitFor(() => expect(screen.queryByText("Loading review candidates…")).not.toBeInTheDocument());
  }
  return { ...fixture, ...view };
}

async function selectCandidate() {
  fireEvent.click(screen.getByRole("button", { name: `Select review candidate ${ids.candidate}` }));
  await screen.findByRole("region", { name: "Candidate review editor" });
  await screen.findByRole("region", { name: "Generated revision 1 evidence" });
}

function requestBySuffix(requests: Request[], method: string, suffix: string): Request {
  const request = requests.find(
    (item) => item.method === method && new URL(item.url).pathname.endsWith(suffix),
  );
  if (!request) throw new Error(`Missing ${method} request ending ${suffix}`);
  return request;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ReviewerStudio", () => {
  it("loads a lightweight filtered queue and fetches generation and validation evidence only after selection", async () => {
    const { requests } = await renderLoaded();

    expect(
      requests.some((request) => new URL(request.url).pathname.endsWith(`/review-candidates/${ids.candidate}`)),
    ).toBe(false);
    fireEvent.change(screen.getByLabelText("Review state"), { target: { value: "in_review" } });
    fireEvent.change(screen.getByLabelText("Paper blueprint ID"), { target: { value: ids.blueprint } });
    fireEvent.change(screen.getByLabelText("Blueprint slot ID"), { target: { value: "REV-A-001" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply queue filters" }));
    await waitFor(() => {
      const filtered = requests.filter(
        (request) => request.method === "GET" && new URL(request.url).pathname.endsWith("/review-candidates"),
      ).at(-1);
      expect(filtered).toBeDefined();
      expect(new URL(filtered!.url).searchParams.get("state")).toBe("in_review");
      expect(new URL(filtered!.url).searchParams.get("paper_blueprint_id")).toBe(ids.blueprint);
      expect(new URL(filtered!.url).searchParams.get("blueprint_slot_id")).toBe("REV-A-001");
    });

    await selectCandidate();
    expect(requestBySuffix(requests, "GET", `/generation-runs/${ids.generation}`)).toBeDefined();
    expect(requestBySuffix(requests, "GET", `/validation-runs/${ids.validation}`)).toBeDefined();
    expect(requestBySuffix(requests, "GET", `/validation-runs/${ids.validation}/findings`)).toBeDefined();
    expect(screen.getByText(/Automated validation applies to generated revision 1 only/i)).toBeVisible();
    expect(screen.getByText(/Human edits are not automatically revalidated/i)).toBeVisible();
    expect(screen.getByText(/Approval does not publish/i)).toBeVisible();
    expect(screen.getByRole("region", { name: "Generated revision 1 evidence" })).toHaveTextContent(
      "<img src=x onerror=alert(1)> Which number is even?",
    );
    expect(screen.getByRole("region", { name: "Generation blueprint evidence" })).toHaveTextContent("REV-A-001");
    expect(screen.getByRole("region", { name: "Generation context provenance" })).toHaveTextContent("document-1");
    expect(screen.getByRole("region", { name: "P8 validation report and findings" })).toHaveTextContent("canonical-validation.v1");
    expect(screen.getByRole("region", { name: "P8 validation report and findings" })).toHaveTextContent("grounding_reference_present");
    expect(screen.getByRole("region", { name: "Candidate revisions and events" })).toHaveTextContent("Started");
    expect(screen.getByRole("region", { name: "Review decision" })).toHaveTextContent("No final decision recorded");
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText("<script>findingAttack()</script> Grounding reference retained.")).toBeVisible();
  });

  it("allows a reviewer to create from persisted non-failing evidence, including WARN, and blocks duplicate commands", async () => {
    let releaseCreate: (() => void) | undefined;
    const createGate = new Promise<void>((resolve) => {
      releaseCreate = resolve;
    });
    const { requests } = await renderLoaded("reviewer", { candidates: [], createGate });

    const selector = screen.getByLabelText("Eligible validation run");
    expect(selector).toHaveValue(ids.validation);
    expect(within(selector).getByRole("option", { name: new RegExp(ids.warnedValidation) })).toBeVisible();
    fireEvent.change(selector, { target: { value: ids.warnedValidation } });
    const submit = screen.getByRole("button", { name: "Create review candidate" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    await waitFor(() => {
      expect(
        requests.filter(
          (request) => request.method === "POST" && new URL(request.url).pathname.endsWith("/review-candidates"),
        ),
      ).toHaveLength(1);
    });
    expect(submit).toBeDisabled();

    await act(async () => releaseCreate?.());
    expect(
      await screen.findByText("Review candidate created from persisted non-failing validation evidence."),
    ).toBeVisible();
    const create = requestBySuffix(requests, "POST", "/review-candidates");
    const payload = (await create.json()) as Record<string, unknown>;
    expect(payload).toEqual({ validation_run_id: ids.warnedValidation });
    expect(Object.keys(payload)).toEqual(["validation_run_id"]);
  });

  it("starts, edits with locked type and marks, approves with the current expected version, and exposes terminal history", async () => {
    const { requests } = await renderLoaded("admin", { candidate: candidateFixture("validated") });
    await selectCandidate();

    fireEvent.click(screen.getByRole("button", { name: "Start review" }));
    await screen.findByText("Human review started.");
    const start = requestBySuffix(requests, "POST", `/review-candidates/${ids.candidate}/start-review`);
    expect(await start.json()).toEqual({ expected_version: 2 });

    expect(screen.getByLabelText("Question type (locked)")).toBeDisabled();
    expect(screen.getByLabelText("Marks (locked)")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Question stem"), { target: { value: editedContent.stem } });
    fireEvent.change(screen.getByLabelText("Option A text"), { target: { value: editedContent.options[0].text } });
    fireEvent.change(screen.getByLabelText("Option B text"), { target: { value: editedContent.options[1].text } });
    fireEvent.change(screen.getByLabelText("Explanation"), { target: { value: editedContent.explanation } });
    fireEvent.change(screen.getByLabelText("Marking guide (one item per line)"), {
      target: { value: editedContent.marking_guide.join("\n") },
    });
    fireEvent.change(screen.getByLabelText("Edit reason"), { target: { value: "Clarify the wording." } });
    expect(screen.getByRole("button", { name: "Approve candidate" })).toBeDisabled();
    expect(screen.getByText(/Save or discard the unsaved revision before approval/i)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Save revision" }));
    await screen.findByText("Revision 2 saved. Automated validation still applies only to revision 1.");

    const patch = requestBySuffix(requests, "PATCH", `/review-candidates/${ids.candidate}`);
    const editPayload = (await patch.json()) as Record<string, unknown>;
    expect(editPayload).toMatchObject({ expected_version: 3, reason: "Clarify the wording." });
    expect(editPayload.content).toMatchObject({
      marks: 2,
      question_type: "multiple_choice",
      stem: editedContent.stem,
    });

    fireEvent.change(screen.getByLabelText("Approval note (optional)"), {
      target: { value: "Source, answer, and wording reviewed." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Approve candidate" }));
    await screen.findByText("Candidate approved. This is not a publish action.");
    const approve = requestBySuffix(requests, "POST", `/review-candidates/${ids.candidate}/approve`);
    expect(await approve.json()).toEqual({
      expected_version: 4,
      note: "Source, answer, and wording reviewed.",
    });
    expect(screen.getByRole("heading", { name: "Approved terminal state" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Review decision" })).toHaveTextContent("Approved");
    expect(screen.getByRole("region", { name: "Candidate revisions and events" })).toHaveTextContent("Edited");
    expect(screen.queryByRole("button", { name: "Save revision" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject candidate" })).not.toBeInTheDocument();
  });

  it("requires a rejection reason and sends the current expected version", async () => {
    const { requests } = await renderLoaded();
    await selectCandidate();

    const reject = screen.getByRole("button", { name: "Reject candidate" });
    expect(reject).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Rejection reason (required)"), {
      target: { value: "Answer is not uniquely supported." },
    });
    expect(reject).toBeEnabled();
    fireEvent.click(reject);
    expect(await screen.findByText("Candidate rejected. Rejected candidates cannot be published.")).toBeVisible();
    const request = requestBySuffix(requests, "POST", `/review-candidates/${ids.candidate}/reject`);
    expect(await request.json()).toEqual({
      expected_version: 3,
      reason: "Answer is not uniquely supported.",
    });
    expect(screen.getByRole("heading", { name: "Rejected terminal state" })).toBeVisible();
  });

  it("preserves unsaved edits on refresh and offers authoritative keep-or-discard choices after a 409", async () => {
    const authoritative = candidateFixture("in_review", 4, {
      ...originalContent,
      stem: "Authoritative concurrent stem",
    });
    await renderLoaded("reviewer", {
      detailSequence: [candidateFixture(), authoritative],
      editConflict: true,
    });
    await selectCandidate();

    fireEvent.change(screen.getByLabelText("Question stem"), {
      target: { value: "My unsaved reviewer stem" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Refresh candidate and evidence" }));
    await screen.findByText(/Authoritative version 4; local draft is based on version 3/i);
    expect(screen.getByLabelText("Question stem")).toHaveValue("My unsaved reviewer stem");

    fireEvent.change(screen.getByLabelText("Edit reason"), { target: { value: "Keep local clarity." } });
    fireEvent.click(screen.getByRole("button", { name: "Save revision" }));
    const conflict = await screen.findByRole("alert");
    expect(conflict).toHaveTextContent("Authoritative version changed");
    expect(within(conflict).getByRole("button", { name: "Reload authoritative and keep draft" })).toBeVisible();
    expect(within(conflict).getByRole("button", { name: "Discard draft and use authoritative" })).toBeVisible();

    fireEvent.click(within(conflict).getByRole("button", { name: "Reload authoritative and keep draft" }));
    await waitFor(() => expect(screen.queryByText("Authoritative version changed")).not.toBeInTheDocument());
    expect(screen.getByLabelText("Question stem")).toHaveValue("My unsaved reviewer stem");
    expect(screen.getByText(/Local draft rebased onto authoritative version 4/i)).toBeVisible();
  });

  it("surfaces empty, network, permission, and 422 states without losing the selected candidate", async () => {
    const empty = await renderLoaded("reviewer", { candidates: [], validationSummaries: [] });
    expect(screen.getByRole("heading", { name: "No review candidates match" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "No persisted non-failing validation reports" })).toBeVisible();
    empty.unmount();

    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    const network = await renderLoaded("reviewer", { throwOnQueue: true });
    expect(screen.getByRole("alert")).toHaveTextContent("Review queue unavailable");
    expect(screen.getByRole("button", { name: "Retry review queue" })).toBeVisible();
    network.unmount();

    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    const denied = await renderLoaded("reviewer", { workspaceStatus: 403 });
    expect(screen.getByRole("alert")).toHaveTextContent("Reviewer workspace permission required");
    denied.unmount();

    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    await renderLoaded("reviewer", { editStatus: 422 });
    await selectCandidate();
    fireEvent.change(screen.getByLabelText("Question stem"), { target: { value: "Locally valid edit" } });
    fireEvent.change(screen.getByLabelText("Edit reason"), { target: { value: "Server boundary test." } });
    fireEvent.click(screen.getByRole("button", { name: "Save revision" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Candidate update rejected");
    expect(screen.getByLabelText("Question stem")).toHaveValue("Locally valid edit");
  });

  it("has no automated accessibility violations in the complete review workspace", async () => {
    const { container } = await renderLoaded();
    await selectCandidate();
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
});
