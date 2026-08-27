import type { components } from "@exam-guru/api-client";
import { fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReviewApproveStudio } from "./review-approve-studio";

type ReviewPaper = components["schemas"]["ReviewPaperDetailResponse"];
type ReviewQuestion = components["schemas"]["ReviewQuestionResponse"];
type ReviewQuestionEdit = components["schemas"]["ReviewQuestionEditRequest"];
type ReviewQuestionRegenerate = components["schemas"]["ReviewQuestionRegenerateRequest"];

const paperId = "00000000-0000-0000-0000-000000000901";
const draftId = "00000000-0000-0000-0000-000000000902";
const replacementId = "00000000-0000-0000-0000-000000000914";
const feedbackId = "00000000-0000-0000-0000-000000000915";
const evalCaseId = "00000000-0000-0000-0000-000000000916";
const questionIds = [
  "00000000-0000-0000-0000-000000000911",
  "00000000-0000-0000-0000-000000000912",
  "00000000-0000-0000-0000-000000000913",
] as const;

function content(
  stem: string,
  answer: string,
  explanation: string,
  marks: number,
  options: Array<{ label: string; text: string }>,
  markingGuide: string[],
): components["schemas"]["QuestionContentResponse"] {
  return {
    answer,
    explanation,
    marking_guide: markingGuide,
    marks,
    options: options.map((option) => ({ option_id: option.label, text: option.text })),
    question_type: options.length ? "multiple_choice" : "structured_response",
    stem,
  };
}

const firstOptions = [
  { label: "A", text: "1/4" },
  { label: "B", text: "3/4" },
  { label: "C", text: "3/3" },
  { label: "D", text: "4/3" },
];

const questions = [
  {
    aggregate_slot_version: 4,
    answer: "B — 3/4",
    content: content(
      "What fraction of the four equal parts is shaded when three parts are shaded?",
      "B — 3/4",
      "Three of the four equal parts are shaded, so the fraction is 3/4.",
      2,
      firstOptions,
      ["Identifies three shaded parts out of four equal parts."],
    ),
    explanation: "Three of the four equal parts are shaded, so the fraction is 3/4.",
    id: questionIds[0],
    marking_scheme: {
      criteria: ["Identifies three shaded parts out of four equal parts."],
      total_marks: 2,
    },
    number: 1,
    options: firstOptions,
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
      candidate_id: "00000000-0000-0000-0000-000000000931",
      context_ids: ["knowledge_chunk:00000000-0000-0000-0000-000000000921"],
      generation_run_id: "00000000-0000-0000-0000-000000000922",
      model_version: "fixture-model-v1",
      provider: "deterministic-fixture-provider",
      validation_run_id: "00000000-0000-0000-0000-000000000932",
      validator_findings: [
        {
          code: "subject.answer.consistency",
          evidence: [],
          message: "The proposed answer matches the checked result.",
          status: "pass",
        },
        {
          code: "subject.math.numeric_equivalence",
          evidence: [{ result: "3/4" }],
          message: "The fraction calculation is correct.",
          status: "pass",
        },
        {
          code: "subject.factual.source_supported",
          evidence: [{ page: "18" }],
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
  },
  {
    aggregate_slot_version: 2,
    answer: "A — 12",
    content: content(
      "What is 3 × 4?",
      "A — 12",
      "Three groups of four make twelve.",
      1,
      [
        { label: "A", text: "12" },
        { label: "B", text: "7" },
      ],
      ["Multiplies 3 by 4."],
    ),
    explanation: "Three groups of four make twelve.",
    id: questionIds[1],
    marking_scheme: { criteria: ["Multiplies 3 by 4."], total_marks: 1 },
    number: 2,
    options: [
      { label: "A", text: "12" },
      { label: "B", text: "7" },
    ],
    requires_revalidation: false,
    review_state: "validated",
    scope: {
      grade: 7,
      lesson: "Lesson 1 — Whole numbers",
      lessons: "Lessons 1–3",
      subject: "Maths",
      taxonomy: "Whole numbers",
      unit: "Numbers",
    },
    sources: [
      {
        filename: "grade-7-maths-syllabus.pdf",
        page: 7,
        title: "Grade 7 Maths Syllabus",
      },
    ],
    stem: "What is 3 × 4?",
    technical_details: {
      blueprint_slot_id: "slot-2",
      candidate_id: "00000000-0000-0000-0000-000000000933",
      context_ids: ["knowledge_chunk:00000000-0000-0000-0000-000000000923"],
      generation_run_id: "00000000-0000-0000-0000-000000000924",
      model_version: "fixture-model-v1",
      provider: "deterministic-fixture-provider",
      validation_run_id: "00000000-0000-0000-0000-000000000934",
      validator_findings: [
        {
          code: "subject.language.ambiguous_wording",
          evidence: [],
          message: "A reviewer should confirm that the wording is sufficiently distinct.",
          status: "warn",
        },
      ],
    },
    validation: {
      findings: ["This wording needs human judgement before approval."],
      status: "needs_attention",
      summary: "A language warning needs human judgement.",
    },
    version: 1,
  },
  {
    aggregate_slot_version: 3,
    answer: "Not yet supported",
    content: content(
      "Explain the unsupported relationship.",
      "Not yet supported",
      "The selected sources do not support a single answer.",
      2,
      [],
      [],
    ),
    explanation: "The selected sources do not support a single answer.",
    id: questionIds[2],
    marking_scheme: { criteria: [], total_marks: 2 },
    number: 3,
    options: [],
    requires_revalidation: false,
    review_state: "failed_check",
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
        page: 19,
        title: "Grade 7 Maths Teacher Guide",
      },
    ],
    stem: "Explain the unsupported relationship.",
    technical_details: {
      blueprint_slot_id: "slot-3",
      candidate_id: null,
      context_ids: [],
      generation_run_id: "00000000-0000-0000-0000-000000000925",
      model_version: "fixture-model-v1",
      provider: "deterministic-fixture-provider",
      validation_run_id: "00000000-0000-0000-0000-000000000935",
      validator_findings: [
        {
          code: "subject.factual.unsupported_claim",
          evidence: [],
          message: "The answer is not supported by the selected material.",
          status: "fail",
        },
        {
          code: "subject.scope.outside_selected_lesson",
          evidence: [{ selected_scope: "Lessons 1–3" }],
          message: "The question may be outside the selected lessons.",
          status: "fail",
        },
      ],
    },
    validation: {
      findings: ["The proposed answer is unsupported."],
      status: "failed_check",
      summary: "The proposed answer is not supported by the selected material.",
    },
    version: 1,
  },
] satisfies ReviewQuestion[];

function reviewPaper(questionRecords: ReviewQuestion[] = structuredClone(questions)): ReviewPaper {
  return {
    created_at: "2026-08-25T10:00:00Z",
    draft: null,
    grade: 7,
    id: paperId,
    medium: "English",
    paper_reference: "EGP-G7-MATH-0001",
    questions: questionRecords,
    scope_summary: "Lessons 1–3",
    status: "in_review",
    subject: "Maths",
    technical_details: {
      cost_microusd: 45_000,
      curriculum_version_id: "00000000-0000-0000-0000-000000000940",
      paper_blueprint_id: "00000000-0000-0000-0000-000000000941",
      request_fingerprint: `sha256:${"a".repeat(64)}`,
      total_tokens: 2_100,
    },
    title: "Grade 7 Maths practice paper",
    version: 4,
  };
}

function asRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(input, init);
}

type FixtureConfiguration = {
  allApproved?: boolean;
  editConflict?: boolean;
};

function fixtureApi(configuration: FixtureConfiguration = {}) {
  const requests: Request[] = [];
  const initialQuestions = structuredClone(questions);
  if (configuration.allApproved) {
    for (const question of initialQuestions) question.review_state = "approved";
  }
  let currentPaper = reviewPaper(initialQuestions);

  function replaceQuestion(question: ReviewQuestion) {
    currentPaper = {
      ...currentPaper,
      questions: currentPaper.questions.map((candidate) =>
        candidate.id === question.id ? question : candidate,
      ),
      version: currentPaper.version + 1,
    };
  }

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    requests.push(request.clone());
    const path = new URL(request.url).pathname;

    if (request.method === "GET" && path.endsWith("/review-papers")) {
      return Response.json({
        items: [
          {
            approved_count: currentPaper.questions.filter(
              (question) => question.review_state === "approved",
            ).length,
            created_at: currentPaper.created_at,
            grade: currentPaper.grade,
            id: paperId,
            paper_reference: currentPaper.paper_reference,
            question_count: currentPaper.questions.length,
            scope_summary: currentPaper.scope_summary,
            status: currentPaper.status,
            subject: currentPaper.subject,
            title: currentPaper.title,
          },
        ],
      } satisfies components["schemas"]["ReviewPaperListResponse"]);
    }
    if (request.method === "GET" && path.endsWith(`/review-papers/${paperId}`)) {
      return Response.json(currentPaper);
    }

    const question = currentPaper.questions.find((candidate) => path.includes(candidate.id));
    if (request.method === "POST" && question && path.endsWith(`/questions/${question.id}/start`)) {
      const body = (await request.json()) as components["schemas"]["ReviewCandidateStartRequest"];
      if (body.expected_version !== question.version) {
        return Response.json({ detail: { code: "review_question_version_conflict" } }, { status: 409 });
      }
      const started = { ...question, review_state: "in_review", version: question.version + 1 };
      replaceQuestion(started);
      return Response.json(started);
    }
    if (request.method === "PATCH" && question && path.endsWith(`/questions/${question.id}`)) {
      if (configuration.editConflict) {
        return Response.json({ detail: { code: "review_question_version_conflict" } }, { status: 409 });
      }
      const body = (await request.json()) as ReviewQuestionEdit;
      if (body.expected_version !== question.version) {
        return Response.json({ detail: { code: "review_question_version_conflict" } }, { status: 409 });
      }
      const edited: ReviewQuestion = {
        ...question,
        aggregate_slot_version: question.aggregate_slot_version + 1,
        answer: body.content.answer,
        content: { ...body.content },
        explanation: body.content.explanation,
        marking_scheme: {
          criteria: body.content.marking_guide,
          total_marks: body.content.marks,
        },
        options: body.content.options.map((option) => ({
          label: option.option_id,
          text: option.text,
        })),
        quality_feedback_id: feedbackId,
        requires_revalidation: true,
        stem: body.content.stem,
        validation: {
          findings: ["The edited content must receive a fresh canonical check."],
          status: "needs_attention",
          summary: "Changes saved; a fresh check is required before approval.",
        },
        version: question.version + 1,
      };
      replaceQuestion(edited);
      return Response.json(edited);
    }
    if (request.method === "POST" && question && path.endsWith(`/questions/${question.id}/approve`)) {
      const body = (await request.json()) as components["schemas"]["ReviewCandidateApproveRequest"];
      if (body.expected_version !== question.version) {
        return Response.json({ detail: { code: "review_question_version_conflict" } }, { status: 409 });
      }
      if (question.requires_revalidation || question.validation.status === "failed_check") {
        return Response.json(
          { detail: { code: "review_question_revalidation_required" } },
          { status: 409 },
        );
      }
      const approved = { ...question, review_state: "approved", version: question.version + 1 };
      replaceQuestion(approved);
      return Response.json(approved);
    }
    if (request.method === "POST" && question && path.endsWith(`/questions/${question.id}/reject`)) {
      const body = (await request.json()) as components["schemas"]["ReviewQuestionRejectRequest"];
      if (body.expected_version !== question.version || !body.reason_code) {
        return Response.json({ detail: { code: "review_question_version_conflict" } }, { status: 409 });
      }
      const rejected: ReviewQuestion = {
        ...question,
        quality_feedback_id: feedbackId,
        review_state: "rejected",
        version: question.version + 1,
      };
      replaceQuestion(rejected);
      return Response.json(rejected);
    }
    if (
      request.method === "POST" &&
      question &&
      path.endsWith(`/questions/${question.id}/regenerate`)
    ) {
      const body = (await request.json()) as ReviewQuestionRegenerate;
      if (body.expected_version !== question.aggregate_slot_version || !body.reason_code) {
        return Response.json({ detail: { code: "review_question_version_conflict" } }, { status: 409 });
      }
      const replacement: ReviewQuestion = {
        ...question,
        aggregate_slot_version: question.aggregate_slot_version + 1,
        id: replacementId,
        requires_revalidation: false,
        review_state: "validated",
        stem: `${question.stem} (freshly checked)`,
        validation: {
          findings: [],
          status: "ready",
          summary: "The replacement question passed fresh checks.",
        },
        version: 1,
      };
      currentPaper = {
        ...currentPaper,
        questions: currentPaper.questions.map((candidate) =>
          candidate.id === question.id ? replacement : candidate,
        ),
        version: currentPaper.version + 1,
      };
      return Response.json(
        {
          job_id: "00000000-0000-0000-0000-000000000951",
          paper_id: paperId,
          quality_feedback_id: feedbackId,
          question_id: replacementId,
          status: "generating",
          version: replacement.aggregate_slot_version,
        } satisfies components["schemas"]["ReviewQuestionRegenerationResponse"],
        { status: 202 },
      );
    }
    if (request.method === "POST" && path.endsWith(`/feedback/${feedbackId}/promote`)) {
      return Response.json(
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
          promoted_by: "00000000-0000-0000-0000-000000000999",
          source_feedback_id: feedbackId,
          state: "draft",
          version: 1,
        } satisfies components["schemas"]["SubjectQualityEvalCaseResponse"],
        { status: 201 },
      );
    }
    if (request.method === "POST" && path.endsWith(`/eval-cases/${evalCaseId}/approve`)) {
      return Response.json({
        approved_at: "2026-08-25T10:06:00Z",
        approved_by: "00000000-0000-0000-0000-000000000998",
        can_approve: false,
        case_fingerprint: `sha256:${"b".repeat(64)}`,
        created_at: "2026-08-25T10:05:00Z",
        deduplicated: false,
        defect_category: "language_clarity",
        eval_case_id: evalCaseId,
        expected_finding_codes: ["subject.language.ambiguous_wording"],
        expected_status: "warn",
        promoted_by: "00000000-0000-0000-0000-000000000999",
        source_feedback_id: feedbackId,
        state: "approved",
        version: 2,
      } satisfies components["schemas"]["SubjectQualityEvalCaseResponse"]);
    }
    if (request.method === "POST" && path.endsWith(`/review-papers/${paperId}/create-draft`)) {
      const body = (await request.json()) as components["schemas"]["ReviewPaperCreateDraftRequest"];
      if (body.expected_version !== currentPaper.version) {
        return Response.json({ detail: { code: "review_question_version_conflict" } }, { status: 409 });
      }
      currentPaper = {
        ...currentPaper,
        draft: { draft_id: draftId, version: 1 },
        status: "draft_created",
      };
      return Response.json(
        {
          draft_id: draftId,
          draft_version: 1,
          paper_id: paperId,
          paper_reference: currentPaper.paper_reference,
          publication_path: `/api/v1/admin/curricula/00000000-0000-0000-0000-000000000940/papers/${draftId}`,
        } satisfies components["schemas"]["ReviewPaperDraftCreatedResponse"],
        { status: 201 },
      );
    }
    return Response.json({ detail: { code: "unexpected_request", path } }, { status: 500 });
  });
  return { fetchMock, requests };
}

async function renderStudio(configuration: FixtureConfiguration = {}) {
  const fixture = fixtureApi(configuration);
  vi.stubGlobal("fetch", fixture.fetchMock);
  const view = render(<ReviewApproveStudio role="reviewer" />);
  await screen.findByRole("heading", { level: 1, name: "Review & Approve" });
  await screen.findByText(questions[0].stem);
  return { ...fixture, ...view };
}

async function startFirstQuestion() {
  fireEvent.click(screen.getByRole("button", { name: "Start review" }));
  await screen.findByText("Review started.");
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ReviewApproveStudio", () => {
  it("shows the question, answer, explanation, marking, scope, sources, and checks together", async () => {
    await renderStudio();

    const question = screen.getByRole("region", { name: "Question 1 of 3" });
    expect(question).toHaveTextContent(questions[0].stem);
    const options = within(question).getByRole("list", { name: "Answer options" });
    for (const option of questions[0].options) {
      expect(options).toHaveTextContent(`${option.label} ${option.text}`);
    }
    expect(question).toHaveTextContent("Proposed answer");
    expect(question).toHaveTextContent("B — 3/4");
    expect(question).toHaveTextContent("Explanation");
    expect(question).toHaveTextContent(
      "Three of the four equal parts are shaded, so the fraction is 3/4.",
    );
    expect(question).toHaveTextContent("2 marks");
    expect(question).toHaveTextContent("Identifies three shaded parts out of four equal parts.");
    expect(question).toHaveTextContent("Grade 7 Maths · Lessons 1–3 · Fractions");
    expect(question).toHaveTextContent("Grade 7 Maths Teacher Guide — page 18");
    expect(question).toHaveTextContent("Ready");
    expect(question).toHaveTextContent("Answer check: Passed");
    expect(question).toHaveTextContent("Calculation check: Passed");
    expect(question).toHaveTextContent("Source check: Passed");

    for (const action of [
      "Start review",
      "Approve",
      "Edit",
      "Reject",
      "Regenerate question",
      "Previous",
      "Next",
    ]) {
      expect(within(question).getByRole("button", { name: action })).toBeInTheDocument();
    }
    expect(within(question).getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(within(question).getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(within(question).getByRole("button", { name: "Next" })).toBeEnabled();

    await startFirstQuestion();
    expect(within(question).getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("keeps warning judgement explicit and prevents a failed check from approval", async () => {
    await renderStudio();

    expect(screen.getByRole("status", { name: "Validation status" })).toHaveTextContent("Ready");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    const second = await screen.findByRole("region", { name: "Question 2 of 3" });
    expect(second).toHaveTextContent("Needs attention");
    expect(second).toHaveTextContent("Human judgement required");
    expect(second).toHaveTextContent("Language check: Needs attention");
    expect(second).not.toHaveTextContent("Language check: Passed");

    fireEvent.click(within(second).getByRole("button", { name: "Next" }));
    const third = await screen.findByRole("region", { name: "Question 3 of 3" });
    expect(third).toHaveTextContent("Failed check");
    expect(third).toHaveTextContent("Source check: Failed");
    expect(third).toHaveTextContent("Scope check: Failed");
    expect(within(third).getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(within(third).getByRole("button", { name: "Next" })).toBeDisabled();

    fireEvent.click(within(third).getByRole("button", { name: "Previous" }));
    expect(await screen.findByRole("region", { name: "Question 2 of 3" })).toBeInTheDocument();
  });

  it("starts and approves with the authoritative expected version", async () => {
    const { requests } = await renderStudio();
    await startFirstQuestion();

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Question approved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Regenerate question" })).toBeDisabled();

    const startRequest = requests.find((request) =>
      new URL(request.url).pathname.endsWith(`/questions/${questionIds[0]}/start`),
    );
    const approveRequest = requests.find((request) =>
      new URL(request.url).pathname.endsWith(`/questions/${questionIds[0]}/approve`),
    );
    await expect(startRequest?.clone().json()).resolves.toEqual({ expected_version: 1 });
    await expect(approveRequest?.clone().json()).resolves.toEqual({
      expected_version: 2,
      note: null,
    });
  });

  it("saves a complete teacher edit and requires a fresh check before approval", async () => {
    const { requests } = await renderStudio();
    await startFirstQuestion();

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const editor = screen.getByRole("dialog", { name: "Edit question 1" });
    fireEvent.change(within(editor).getByLabelText("Question"), {
      target: { value: "What fraction is shaded when three of four equal parts are shaded?" },
    });
    fireEvent.change(within(editor).getByLabelText("Why are you changing this question?"), {
      target: { value: "ambiguous_wording" },
    });
    fireEvent.change(within(editor).getByLabelText("Optional note"), {
      target: { value: "Clarify the wording for learners." },
    });
    fireEvent.click(within(editor).getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Question changes saved. A fresh check is required.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByText("Fresh validation required before approval.")).toBeInTheDocument();

    const patch = requests.find(
      (request) =>
        request.method === "PATCH" &&
        new URL(request.url).pathname.endsWith(`/questions/${questionIds[0]}`),
    );
    const body = (await patch?.clone().json()) as ReviewQuestionEdit;
    expect(body).toMatchObject({
      expected_version: 2,
      note: "Clarify the wording for learners.",
      reason_code: "ambiguous_wording",
      content: {
        answer: "B — 3/4",
        marks: 2,
        question_type: "multiple_choice",
        stem: "What fraction is shaded when three of four equal parts are shaded?",
      },
    });

    fireEvent.click(screen.getByRole("button", { name: "Regenerate question" }));
    const regenerate = screen.getByRole("dialog", { name: "Regenerate question 1" });
    fireEvent.change(within(regenerate).getByLabelText("Why should this question be replaced?"), {
      target: { value: "answer_incorrect" },
    });
    fireEvent.change(within(regenerate).getByLabelText("Optional note"), {
      target: { value: "Prepare and validate a fresh replacement." },
    });
    fireEvent.click(within(regenerate).getByRole("button", { name: "Regenerate" }));
    expect(await screen.findByText("A replacement question is being prepared and checked.")).toBeInTheDocument();

    const regenerateRequest = requests.find(
      (request) =>
        request.method === "POST" &&
        new URL(request.url).pathname.endsWith(`/questions/${questionIds[0]}/regenerate`),
    );
    expect(regenerateRequest?.headers.get("Idempotency-Key")).toMatch(/^review-regenerate-\S+$/);
    await expect(regenerateRequest?.clone().json()).resolves.toEqual({
      expected_version: 5,
      note: "Prepare and validate a fresh replacement.",
      reason_code: "answer_incorrect",
    });
  });

  it("requires and sends a teacher rejection reason", async () => {
    const { requests } = await renderStudio();
    await startFirstQuestion();
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    const dialog = screen.getByRole("dialog", { name: "Reject question 1" });
    fireEvent.change(within(dialog).getByLabelText("Why are you rejecting this question?"), {
      target: { value: "distractor_quality" },
    });
    fireEvent.change(within(dialog).getByLabelText("Optional note"), {
      target: { value: "The distractors are not suitable for this lesson." },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject question" }));
    expect(await screen.findByText("Question rejected.")).toBeInTheDocument();

    const rejectRequest = requests.find((request) =>
      new URL(request.url).pathname.endsWith(`/questions/${questionIds[0]}/reject`),
    );
    await expect(rejectRequest?.clone().json()).resolves.toEqual({
      expected_version: 2,
      note: "The distractors are not suitable for this lesson.",
      reason_code: "distractor_quality",
    });
  });

  it("captures a plain-language reason and promotes review evidence without claiming model training", async () => {
    const { requests } = await renderStudio();
    await startFirstQuestion();

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const editor = screen.getByRole("dialog", { name: "Edit question 1" });
    fireEvent.change(within(editor).getByLabelText("Question"), {
      target: { value: "What fraction is shaded when three of four equal parts are shaded?" },
    });
    fireEvent.change(within(editor).getByLabelText("Why are you changing this question?"), {
      target: { value: "ambiguous_wording" },
    });
    fireEvent.change(within(editor).getByLabelText("Optional note"), {
      target: { value: "Two readings were possible." },
    });
    fireEvent.click(within(editor).getByRole("button", { name: "Save changes" }));

    const addExample = await screen.findByRole("button", { name: "Add to quality examples" });
    fireEvent.click(addExample);
    const promotion = screen.getByRole("dialog", { name: "Add review evidence to quality examples" });
    expect(promotion).toHaveTextContent("does not train or automatically change the model");
    fireEvent.change(within(promotion).getByLabelText("Expected check result"), {
      target: { value: "warn" },
    });
    fireEvent.change(within(promotion).getByLabelText("Defect category"), {
      target: { value: "language_clarity" },
    });
    const technical = within(promotion).getByText("Technical eval details", { selector: "summary" });
    expect(technical.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(within(promotion).getByRole("button", { name: "Create draft quality example" }));
    expect(
      await screen.findByText("Draft quality example created. A second reviewer or administrator must approve it."),
    ).toBeInTheDocument();

    const patch = requests.find(
      (request) =>
        request.method === "PATCH" &&
        new URL(request.url).pathname.endsWith(`/questions/${questionIds[0]}`),
    );
    await expect(patch?.clone().json()).resolves.toMatchObject({
      expected_version: 2,
      note: "Two readings were possible.",
      reason_code: "ambiguous_wording",
    });
    const promote = requests.find(
      (request) =>
        request.method === "POST" &&
        new URL(request.url).pathname.endsWith("/promote"),
    );
    expect(promote?.headers.get("Idempotency-Key")).toMatch(/^quality-promotion-\S+$/);
    await expect(promote?.clone().json()).resolves.toEqual({
      defect_category: "language_clarity",
      expected_finding_codes: ["subject.language.ambiguous_wording"],
      expected_status: "warn",
    });
  });

  it("preserves the local edit when a 409 reports another reviewer changed the question", async () => {
    await renderStudio({ editConflict: true });
    await startFirstQuestion();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const editor = screen.getByRole("dialog", { name: "Edit question 1" });
    const revisedStem = "Keep this local wording even when the version changed.";
    fireEvent.change(within(editor).getByLabelText("Question"), {
      target: { value: revisedStem },
    });
    fireEvent.change(within(editor).getByLabelText("Why are you changing this question?"), {
      target: { value: "ambiguous_wording" },
    });
    fireEvent.change(within(editor).getByLabelText("Optional note"), {
      target: { value: "Clarify wording." },
    });
    fireEvent.click(within(editor).getByRole("button", { name: "Save changes" }));

    const alert = await within(editor).findByRole("alert");
    expect(alert).toHaveTextContent("Another reviewer changed this question");
    expect(alert).toHaveTextContent("Your edits are still here");
    expect(within(editor).getByLabelText("Question")).toHaveValue(revisedStem);
  });

  it("keeps run, context, provider, model, and validator data collapsed", async () => {
    await renderStudio();

    const question = screen.getByRole("region", { name: "Question 1 of 3" });
    const summary = within(question).getByText("Technical details", { selector: "summary" });
    const details = summary.closest("details");
    if (!details) throw new Error("Technical metadata must use a details disclosure");

    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText(questions[0].technical_details.generation_run_id)).not.toBeVisible();
    expect(within(details).getByText(questions[0].technical_details.provider)).not.toBeVisible();

    fireEvent.click(summary);
    expect(details).toHaveAttribute("open");
    expect(within(details).getByText(questions[0].technical_details.generation_run_id)).toBeVisible();
    expect(within(details).getByText(questions[0].technical_details.context_ids[0])).toBeVisible();
    expect(within(details).getByText(questions[0].technical_details.provider)).toBeVisible();
    expect(within(details).getByText("subject.math.numeric_equivalence")).toBeVisible();
  });

  it("creates a draft explicitly only when every question is approved", async () => {
    const { requests } = await renderStudio({ allApproved: true });

    const ready = screen.getByRole("region", { name: "Paper ready for draft" });
    fireEvent.click(within(ready).getByRole("button", { name: "Create draft" }));
    expect(await screen.findByText("Draft created. It is ready in Published Papers.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Published Papers" })).toHaveAttribute(
      "href",
      `/admin/published-papers?paper=${draftId}`,
    );

    const request = requests.find(
      (candidate) =>
        candidate.method === "POST" &&
        new URL(candidate.url).pathname.endsWith(`/review-papers/${paperId}/create-draft`),
    );
    await expect(request?.clone().json()).resolves.toEqual({ expected_version: 4 });
  });

  it("has no automated accessibility violations in the loaded review studio", async () => {
    const { container } = await renderStudio();
    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });
});
