import type { components } from "@exam-guru/api-client";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GeneratePapersWizard } from "./generate-papers-wizard";

type GenerationOptions = components["schemas"]["TeacherPaperOptionsResponse"];
type CurriculumLabels = components["schemas"]["CurriculumLabelsResponse"];
type LessonLabels = components["schemas"]["LessonLabelsResponse"];
type PaperJob = components["schemas"]["TeacherPaperJobResponse"];

const generationOptions = {
  assessment_programmes: [
    { code: "SCHOOL-G7", grade: 7, label: "School practice paper" },
  ],
  defaults: {
    difficulty: "balanced",
    duration_minutes: 45,
    question_count: 10,
  },
  grades: Array.from({ length: 13 }, (_, index) => index + 1),
  media: [{ code: "en", label: "English" }],
  subjects: [
    {
      assessment_programme: "SCHOOL-G7",
      code: "MATHEMATICS",
      grade: 7,
      label: "Maths",
      lessons: [
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
      ],
      medium: "en",
      units: [{ code: "NUMBERS", label: "Numbers" }],
    },
  ],
} satisfies GenerationOptions;

const curriculumLabels = {
  items: [
    {
      assessment_label: "School Grade 7",
      assessment_programme: "SCHOOL-G7",
      code: "G7-MATH-V1",
      label: "Grade 7 Mathematics",
    },
  ],
} satisfies CurriculumLabels;

const lessonLabels = {
  curriculum: curriculumLabels.items[0],
  grade: 7,
  lessons: generationOptions.subjects[0].lessons,
  medium: "en",
  subject: "MATHEMATICS",
} satisfies LessonLabels;

const jobId = "00000000-0000-0000-0000-000000000801";
const paperId = "00000000-0000-0000-0000-000000000802";
const reviewUrl = `/admin/review-approve?paper=${jobId}`;

function paperJob(overrides: Partial<PaperJob> = {}): PaperJob {
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
    job_id: jobId,
    medium: "English",
    paper_id: paperId,
    paper_reference: "EGP-G7-MATH-0001",
    progress: ["preparing", "generating", "checking_answers", "ready_for_review"],
    review_url: reviewUrl,
    scope_summary: "Lessons 1–3",
    slots: [1, 2, 3].map((number) => ({
      candidate_id: `00000000-0000-0000-0000-00000000081${number}`,
      failure: null,
      generation_run_id: `00000000-0000-0000-0000-00000000082${number}`,
      id: `00000000-0000-0000-0000-00000000083${number}`,
      lesson: `Lesson ${number}`,
      number,
      status: "awaiting_review",
      validation: "ready" as const,
      version: 3,
    })),
    status: "ready_for_review",
    subject: "Mathematics",
    title: "Grade 7 Mathematics practice paper",
    total_tokens: 2_100,
    updated_at: "2026-08-25T10:03:00Z",
    version: 4,
    ...overrides,
  };
}

function asRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(input, init);
}

type FixtureConfiguration = {
  curricula?: CurriculumLabels;
  curriculaError?: { code: string; status: number };
  createError?: { code: string; retryAfter?: string; status: number };
  failCreateOnce?: boolean;
  pollJob?: PaperJob;
  retryJob?: PaperJob;
};

function fixtureApi(configuration: FixtureConfiguration = {}) {
  const requests: Request[] = [];
  let createAttempts = 0;
  let retryAccepted = false;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    requests.push(request.clone());
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === "GET" && path.endsWith("/paper-generation/options")) {
      return Response.json(generationOptions);
    }
    if (request.method === "GET" && path.endsWith("/paper-generation/curricula")) {
      if (configuration.curriculaError) {
        return Response.json(
          { detail: { code: configuration.curriculaError.code } },
          { status: configuration.curriculaError.status },
        );
      }
      return Response.json(configuration.curricula ?? curriculumLabels);
    }
    if (request.method === "GET" && path.endsWith("/paper-generation/lessons")) {
      return Response.json(lessonLabels);
    }
    if (request.method === "POST" && path.endsWith("/paper-generation/jobs")) {
      createAttempts += 1;
      if (configuration.failCreateOnce && createAttempts === 1) {
        return Response.json(
          { detail: { code: "paper_generation_queue_unavailable" } },
          { status: 503 },
        );
      }
      if (configuration.createError) {
        const headers = configuration.createError.retryAfter
          ? { "Retry-After": configuration.createError.retryAfter }
          : undefined;
        return Response.json(
          { detail: { code: configuration.createError.code } },
          { headers, status: configuration.createError.status },
        );
      }
      return Response.json(
        paperJob({
          completed_at: null,
          counts: {
            approved: 0,
            candidates: 0,
            failed: 0,
            generated: 0,
            requested: 3,
            validated: 0,
          },
          progress: ["preparing"],
          review_url: null,
          slots: [],
          status: "preparing",
          updated_at: "2026-08-25T10:00:00Z",
          version: 1,
        }),
        { status: 202 },
      );
    }
    if (request.method === "POST" && path.endsWith(`/paper-generation/jobs/${jobId}/retry`)) {
      retryAccepted = true;
      return Response.json(
        configuration.retryJob ??
          paperJob({
            completed_at: null,
            failure: null,
            progress: ["preparing", "generating"],
            review_url: null,
            status: "generating",
            version: 6,
          }),
        { status: 202 },
      );
    }
    if (request.method === "GET" && path.endsWith(`/paper-generation/jobs/${jobId}`)) {
      if (retryAccepted) return Response.json(paperJob({ version: 7 }));
      return Response.json(configuration.pollJob ?? paperJob());
    }
    return Response.json({ detail: { code: "unexpected_request", path } }, { status: 500 });
  });
  return { fetchMock, requests };
}

async function renderWizard(configuration: FixtureConfiguration = {}) {
  const fixture = fixtureApi(configuration);
  vi.stubGlobal("fetch", fixture.fetchMock);
  const view = render(<GeneratePapersWizard role="admin" />);
  await screen.findByRole("heading", { level: 1, name: "Generate Papers" });
  await screen.findByLabelText("Grade");
  return { ...fixture, ...view };
}

async function chooseGradeSevenMaths() {
  fireEvent.change(screen.getByLabelText("Grade"), { target: { value: "7" } });
  fireEvent.change(screen.getByLabelText("Medium"), { target: { value: "en" } });
  fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "MATHEMATICS" } });
  fireEvent.change(screen.getByLabelText("Paper type"), {
    target: { value: "SCHOOL-G7" },
  });
  const continueButton = screen.getByRole("button", { name: "Continue to scope" });
  await waitFor(() => expect(continueButton).toBeEnabled());
  fireEvent.click(continueButton);
  await screen.findByRole("region", { name: "Selected scope" });
}

function chooseSimpleSettings() {
  fireEvent.click(screen.getByRole("button", { name: "Continue to paper settings" }));
  fireEvent.change(screen.getByLabelText("Number of questions"), { target: { value: "12" } });
  fireEvent.change(screen.getByLabelText("Duration in minutes"), { target: { value: "50" } });
  fireEvent.change(screen.getByLabelText("Difficulty"), { target: { value: "balanced" } });
}

async function submittedBody(requests: Request[]): Promise<Record<string, unknown>> {
  await waitFor(() => {
    expect(
      requests.some(
        (request) =>
          request.method === "POST" &&
          new URL(request.url).pathname.endsWith("/paper-generation/jobs"),
      ),
    ).toBe(true);
  });
  const request = requests.find(
    (candidate) =>
      candidate.method === "POST" &&
      new URL(candidate.url).pathname.endsWith("/paper-generation/jobs"),
  );
  if (!request) throw new Error("Expected a teacher paper-generation request");
  return (await request.json()) as Record<string, unknown>;
}

function allKeys(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(allKeys);
  if (!value || typeof value !== "object") return [];
  return Object.entries(value).flatMap(([key, nested]) => [key, ...allKeys(nested)]);
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("GeneratePapersWizard", () => {
  it("generates Grade 7 Maths Lessons 1–3 from current teacher API choices", async () => {
    const { requests } = await renderWizard();

    await chooseGradeSevenMaths();
    fireEvent.click(screen.getByRole("radio", { name: "Lesson range" }));
    fireEvent.change(screen.getByLabelText("First lesson"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Last lesson"), { target: { value: "3" } });
    expect(screen.getByRole("region", { name: "Selected scope" })).toHaveTextContent(
      "Grade 7 Maths · Lessons 1–3",
    );

    chooseSimpleSettings();
    fireEvent.click(screen.getByRole("button", { name: "Generate paper" }));

    const body = await submittedBody(requests);
    expect(body).toMatchObject({
      scope: { end_lesson: 3, kind: "lesson_range", start_lesson: 1 },
      settings: { difficulty: "balanced", duration_minutes: 50, question_count: 12 },
      target: {
        assessment_programme: "SCHOOL-G7",
        grade: 7,
        medium: "en",
        subject: "MATHEMATICS",
      },
    });
    expect(allKeys(body)).not.toEqual(
      expect.arrayContaining([
        "blueprint_id",
        "blueprint_slot_ids",
        "context_ids",
        "provider",
        "model",
        "retrieval_configuration",
      ]),
    );
    const createRequest = requests.find(
      (request) =>
        request.method === "POST" &&
        new URL(request.url).pathname.endsWith("/paper-generation/jobs"),
    );
    expect(createRequest?.headers.get("Idempotency-Key")).toMatch(/^teacher-paper-\S+$/);
    expect(
      requests.some((request) => new URL(request.url).pathname.endsWith("/paper-generation/curricula")),
    ).toBe(true);
    expect(
      requests.some((request) => new URL(request.url).pathname.endsWith("/paper-generation/lessons")),
    ).toBe(true);

    const progress = await screen.findByRole("region", { name: "Paper progress" });
    for (const label of [
      "Preparing paper",
      "Generating questions",
      "Checking answers",
      "Ready for review",
    ]) {
      expect(progress).toHaveTextContent(label);
    }
    expect(await within(progress).findByRole("link", { name: "Review this paper" })).toHaveAttribute(
      "href",
      reviewUrl,
    );
    expect(progress).not.toHaveTextContent("microusd");
    expect(progress).not.toHaveTextContent("token");
    expect(progress).not.toHaveTextContent("generation run");
  });

  it("supports a full Grade 7 Maths subject without requiring lesson or module IDs", async () => {
    const { requests } = await renderWizard();

    await chooseGradeSevenMaths();
    fireEvent.click(screen.getByRole("radio", { name: "Full syllabus" }));
    expect(screen.getByRole("region", { name: "Selected scope" })).toHaveTextContent(
      "Grade 7 Maths · Full syllabus",
    );
    chooseSimpleSettings();
    fireEvent.click(screen.getByRole("button", { name: "Generate paper" }));

    await expect(submittedBody(requests)).resolves.toMatchObject({
      scope: { kind: "full_subject" },
      target: { grade: 7, medium: "en", subject: "MATHEMATICS" },
    });
  });

  it("does not present raw IDs, context selection, blueprint controls, provider settings, or cost", async () => {
    const { container } = await renderWizard();
    const form = screen.getByRole("form", { name: "Generate a paper" });

    for (const forbiddenControl of [
      /curriculum ID/i,
      /context ID/i,
      /blueprint/i,
      /provider/i,
      /model version/i,
      /retrieval/i,
      /generation run/i,
      /cost/i,
    ]) {
      expect(within(form).queryByLabelText(forbiddenControl)).not.toBeInTheDocument();
    }
    expect(container).not.toHaveTextContent(jobId);
  });

  it("reuses the same idempotency key for an explicit safe retry and preserves inputs", async () => {
    const { requests } = await renderWizard({ failCreateOnce: true });
    await chooseGradeSevenMaths();
    fireEvent.click(screen.getByRole("radio", { name: "Full syllabus" }));
    chooseSimpleSettings();
    fireEvent.click(screen.getByRole("button", { name: "Generate paper" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("temporarily unavailable");
    expect(screen.getByLabelText("Number of questions")).toHaveValue(12);
    fireEvent.click(within(alert).getByRole("button", { name: "Try again safely" }));
    await screen.findByRole("link", { name: "Review this paper" });

    const posts = requests.filter(
      (request) =>
        request.method === "POST" &&
        new URL(request.url).pathname.endsWith("/paper-generation/jobs"),
    );
    expect(posts).toHaveLength(2);
    expect(posts[0]?.headers.get("Idempotency-Key")).toBe(posts[1]?.headers.get("Idempotency-Key"));
    await expect(posts[0]?.clone().json()).resolves.toEqual(await posts[1]?.clone().json());
  });

  it("shows a rate-limit recovery message without clearing the teacher's paper", async () => {
    await renderWizard({
      createError: { code: "rate_limit_exceeded", retryAfter: "17", status: 429 },
    });
    await chooseGradeSevenMaths();
    fireEvent.click(screen.getByRole("radio", { name: "Full syllabus" }));
    chooseSimpleSettings();
    fireEvent.click(screen.getByRole("button", { name: "Generate paper" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("17 seconds");
    expect(screen.getByLabelText("Grade")).toHaveValue("7");
    expect(screen.getByLabelText("Number of questions")).toHaveValue(12);
  });

  it.each([
    ["paper_generation_curriculum_ambiguous", 409, "More than one curriculum matches"],
    ["paper_generation_curriculum_not_found", 404, "No matching curriculum content"],
  ])("handles %s while preserving target choices", async (code, status, message) => {
    await renderWizard({ curriculaError: { code, status } });
    fireEvent.change(screen.getByLabelText("Grade"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("Medium"), { target: { value: "en" } });
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "MATHEMATICS" } });
    fireEvent.change(screen.getByLabelText("Paper type"), { target: { value: "SCHOOL-G7" } });

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByLabelText("Grade")).toHaveValue("7");
    expect(screen.getByLabelText("Subject")).toHaveValue("MATHEMATICS");
  });

  it("offers a bounded explicit retry for partial generation failure", async () => {
    const partial = paperJob({
      counts: {
        approved: 0,
        candidates: 0,
        failed: 1,
        generated: 2,
        requested: 3,
        validated: 1,
      },
      failure: {
        code: "paper_generation_slot_failed",
        message: "One question could not be prepared.",
      },
      progress: ["preparing", "generating", "checking_answers", "failed"],
      review_url: null,
      status: "failed",
      version: 5,
    });
    const { requests } = await renderWizard({ pollJob: partial });
    await chooseGradeSevenMaths();
    fireEvent.click(screen.getByRole("radio", { name: "Lesson range" }));
    fireEvent.change(screen.getByLabelText("First lesson"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Last lesson"), { target: { value: "3" } });
    chooseSimpleSettings();
    fireEvent.click(screen.getByRole("button", { name: "Generate paper" }));

    const progress = await screen.findByRole("region", { name: "Paper progress" });
    expect(progress).toHaveTextContent("2 of 3 questions were prepared");
    fireEvent.click(within(progress).getByRole("button", { name: "Retry failed questions" }));
    await within(progress).findByRole("link", { name: "Review this paper" });

    const retryRequest = requests.find(
      (request) =>
        request.method === "POST" &&
        new URL(request.url).pathname.endsWith(`/paper-generation/jobs/${jobId}/retry`),
    );
    expect(retryRequest?.headers.get("Idempotency-Key")).toMatch(/^teacher-paper-retry-\S+$/);
    await expect(retryRequest?.clone().json()).resolves.toEqual({ expected_version: 5 });
  });

  it("has no automated accessibility violations in the loaded wizard", async () => {
    const { container } = await renderWizard();
    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });
});
