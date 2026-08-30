import type { components } from "@exam-guru/api-client";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EmbeddingIngestion } from "./embedding-ingestion";

type HistoricalQuestion = components["schemas"]["HistoricalQuestionResponse"];
type KnowledgeChunk = components["schemas"]["KnowledgeChunkResponse"];
type EmbeddingJob = components["schemas"]["EmbeddingJobResponse"];
type EmbeddingJobCreateRequest = components["schemas"]["EmbeddingJobCreateRequest"];

const ids = {
  chunk: "00000000-0000-4000-8000-000000000502",
  competency: "00000000-0000-4000-8000-000000000401",
  config: "00000000-0000-4000-8000-000000000601",
  curriculum: "00000000-0000-4000-8000-000000000101",
  job: "00000000-0000-4000-8000-000000000701",
  otherCurriculum: "00000000-0000-4000-8000-000000000102",
  question: "00000000-0000-4000-8000-000000000501",
  retryJob: "00000000-0000-4000-8000-000000000702",
  source: "00000000-0000-4000-8000-000000000201",
} as const;

const embeddingConfiguration = {
  config_fingerprint: "sha256:fixture-config",
  dimension: 384,
  id: ids.config,
  model: "multilingual-e5-small",
  provider: "local",
  version: "v1",
} satisfies components["schemas"]["EmbeddingConfigurationMetadataResponse"];

function uuid(value: number): string {
  return `00000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
}

function question(
  id: string = ids.question,
  overrides: Partial<HistoricalQuestion> = {},
): HistoricalQuestion {
  return {
    answer: null,
    classification: {
      competency_id: ids.competency,
      learning_concept_id: null,
      skill_id: null,
      sub_skill_id: null,
    },
    created_at: "2026-08-25T00:00:00Z",
    curriculum_version_id: ids.curriculum,
    deduplicated: false,
    difficulty_confidence: null,
    difficulty_label: null,
    difficulty_source: null,
    embedding_configurations: [],
    embedding_status: "not_embedded",
    id,
    lesson_id: null,
    marking_data: null,
    marking_guidance: null,
    marks: 1,
    media_references: null,
    options: null,
    paper_code: "2025-I",
    provenance: {
      page_number: 1,
      source_block_id: null,
      source_document_id: ids.source,
    },
    question_archetype: null,
    question_number: id.slice(-4),
    question_type: "short_answer",
    review_state: "reviewed",
    text: "Server-owned historical question text must not be rendered here.",
    unit_id: null,
    updated_at: "2026-08-25T00:00:00Z",
    version: 2,
    year: 2025,
    ...overrides,
  };
}

function chunk(id: string = ids.chunk, overrides: Partial<KnowledgeChunk> = {}): KnowledgeChunk {
  return {
    chunk_type: "explanation",
    classification: {
      competency_id: ids.competency,
      learning_concept_id: null,
      skill_id: null,
      sub_skill_id: null,
    },
    created_at: "2026-08-25T00:00:00Z",
    curriculum_version_id: ids.curriculum,
    deduplicated: false,
    educational_boundary: `Geometry boundary ${id.slice(-4)}`,
    embedding_configurations: [],
    embedding_status: "not_embedded",
    id,
    lesson_id: null,
    provenance: {
      page_number: 1,
      source_block_id: null,
      source_document_id: ids.source,
    },
    review_state: "reviewed",
    sequence: 1,
    text: "Server-owned knowledge chunk text must not be rendered here.",
    unit_id: null,
    updated_at: "2026-08-25T00:00:00Z",
    version: 3,
    ...overrides,
  };
}

function job(overrides: Partial<EmbeddingJob> = {}): EmbeddingJob {
  return {
    claimed_at: null,
    completed_at: null,
    configuration: {
      config_fingerprint: embeddingConfiguration.config_fingerprint,
      dimension: embeddingConfiguration.dimension,
      model: embeddingConfiguration.model,
      provider: embeddingConfiguration.provider,
      version: embeddingConfiguration.version,
    },
    counts: { deduplicated: 0, embedded: 0, requested: 2 },
    created_at: "2026-08-25T00:01:00Z",
    created_by: "00000000-0000-4000-8000-000000000801",
    curriculum_version_id: ids.curriculum,
    deduplicated: false,
    failure_code: null,
    historical_question_ids: [ids.question],
    id: ids.job,
    knowledge_chunk_ids: [ids.chunk],
    queue_message_id: "queue-message-1",
    retry_depth: 0,
    retry_of_job_id: null,
    status: "queued",
    updated_at: "2026-08-25T00:01:00Z",
    version: 0,
    ...overrides,
  };
}

function embeddedQuestion(value: HistoricalQuestion): HistoricalQuestion {
  return {
    ...value,
    embedding_configurations: [embeddingConfiguration],
    embedding_status: "embedded",
  };
}

function embeddedChunk(value: KnowledgeChunk): KnowledgeChunk {
  return {
    ...value,
    embedding_configurations: [embeddingConfiguration],
    embedding_status: "embedded",
  };
}

function asRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(input, init);
}

type ApiFixtureOptions = {
  chunks?: KnowledgeChunk[];
  jobs?: EmbeddingJob[];
  onPoll?: (request: Request, pollIndex: number) => Promise<Response> | Response;
  onPost?: (request: Request, postIndex: number) => Promise<Response> | Response;
  questions?: HistoricalQuestion[];
};

function fixtureApi(options: ApiFixtureOptions = {}) {
  let chunks = options.chunks ?? [chunk()];
  let questions = options.questions ?? [question()];
  let jobs = options.jobs ?? [];
  let pollIndex = 0;
  let postIndex = 0;
  const requests: Request[] = [];
  const pollTimes: number[] = [];

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    requests.push(request.clone());
    const url = new URL(request.url);
    const itemMatch = url.pathname.match(/\/embedding-jobs\/([^/]+)$/);

    if (request.method === "GET" && itemMatch) {
      pollTimes.push(Date.now());
      if (options.onPoll) return options.onPoll(request, pollIndex++);
      return Response.json(jobs.find((item) => item.id === itemMatch[1]) ?? job());
    }
    if (request.method === "GET" && url.pathname.endsWith("/embedding-jobs")) {
      return Response.json(jobs);
    }
    if (request.method === "GET" && url.pathname.endsWith("/knowledge/questions")) {
      return Response.json(questions);
    }
    if (request.method === "GET" && url.pathname.endsWith("/knowledge/chunks")) {
      return Response.json(chunks);
    }
    if (request.method === "POST" && url.pathname.endsWith("/embedding-jobs")) {
      if (options.onPost) return options.onPost(request, postIndex++);
      const body = (await request.clone().json()) as EmbeddingJobCreateRequest;
      const created = job({
        completed_at: "2026-08-25T00:01:03Z",
        counts: {
          deduplicated: 0,
          embedded: body.historical_question_ids.length + body.knowledge_chunk_ids.length,
          requested: body.historical_question_ids.length + body.knowledge_chunk_ids.length,
        },
        historical_question_ids: body.historical_question_ids,
        knowledge_chunk_ids: body.knowledge_chunk_ids,
        status: "succeeded",
        version: 2,
      });
      questions = questions.map(embeddedQuestion);
      chunks = chunks.map(embeddedChunk);
      jobs = [created];
      return Response.json(created, { status: 202 });
    }
    return Response.json({ detail: { code: "unexpected_request" } }, { status: 500 });
  });

  return {
    fetchMock,
    pollTimes,
    requests,
    setChunks(next: KnowledgeChunk[]) {
      chunks = next;
    },
    setJobs(next: EmbeddingJob[]) {
      jobs = next;
    },
    setQuestions(next: HistoricalQuestion[]) {
      questions = next;
    },
  };
}

async function renderLoaded(
  role: "admin" | "reviewer",
  fixture = fixtureApi(),
  onRecordsEmbedded = vi.fn(),
) {
  vi.stubGlobal("fetch", fixture.fetchMock);
  const view = render(
    <EmbeddingIngestion
      curriculumVersionId={ids.curriculum}
      onRecordsEmbedded={onRecordsEmbedded}
      role={role}
    />,
  );
  await screen.findByRole("heading", { name: "Embedding ingestion" });
  await waitFor(() => {
    expect(
      fixture.requests.some(
        (request) =>
          request.method === "GET" && new URL(request.url).pathname.endsWith("/embedding-jobs"),
      ),
    ).toBe(true);
  });
  await waitFor(() => {
    expect(screen.queryByText("Loading embedding data…")).not.toBeInTheDocument();
  });
  return { fixture, onRecordsEmbedded, view };
}

function embeddingPosts(requests: Request[]): Request[] {
  return requests.filter(
    (request) =>
      request.method === "POST" && new URL(request.url).pathname.endsWith("/embedding-jobs"),
  );
}

function embeddingPolls(requests: Request[]): Request[] {
  return requests.filter(
    (request) =>
      request.method === "GET" && /\/embedding-jobs\/[^/]+$/.test(new URL(request.url).pathname),
  );
}

async function flushAsyncOperation() {
  await act(async () => {
    for (let index = 0; index < 12; index += 1) await Promise.resolve();
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("EmbeddingIngestion", () => {
  it("selects a unique cross-kind maximum of 100 and sends only the exact generated-client body", async () => {
    const questions = Array.from({ length: 61 }, (_, index) => question(uuid(1_000 + index)));
    const chunks = Array.from({ length: 61 }, (_, index) => chunk(uuid(2_000 + index)));
    const draft = question(uuid(9_999), { review_state: "draft" });
    const fixture = fixtureApi({
      chunks,
      questions: [questions[0], ...questions, draft],
    });
    const onRecordsEmbedded = vi.fn();
    await renderLoaded("admin", fixture, onRecordsEmbedded);

    const queueButton = screen.getByRole("button", { name: "Queue selected records" });
    expect(queueButton).toBeDisabled();
    expect(screen.queryByText(draft.question_number)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Select up to 100 reviewed records" }));

    expect(screen.getByText("100 of 100 records selected")).toBeInTheDocument();
    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes.filter((checkbox) => (checkbox as HTMLInputElement).checked)).toHaveLength(100);
    expect(checkboxes.filter((checkbox) => (checkbox as HTMLInputElement).disabled).length).toBeGreaterThan(0);

    fireEvent.click(queueButton);
    fireEvent.click(queueButton);
    expect(await screen.findByText("Embedding job succeeded.")).toBeInTheDocument();

    const posts = embeddingPosts(fixture.requests);
    expect(posts).toHaveLength(1);
    const body = (await posts[0]?.json()) as EmbeddingJobCreateRequest;
    expect(Object.keys(body).sort()).toEqual([
      "historical_question_ids",
      "knowledge_chunk_ids",
    ]);
    expect(body.historical_question_ids.length + body.knowledge_chunk_ids.length).toBe(100);
    expect(new Set([...body.historical_question_ids, ...body.knowledge_chunk_ids]).size).toBe(100);
    expect(JSON.stringify(body)).not.toMatch(/text|vector|config|review_state|embedding_status/i);
    const idempotencyKey = posts[0]?.headers.get("Idempotency-Key") ?? "";
    expect(idempotencyKey).toMatch(/^embedding-[A-Za-z0-9-]+$/);
    expect(idempotencyKey.length).toBeLessThanOrEqual(128);
    expect(idempotencyKey).not.toMatch(/\s/);
    expect(onRecordsEmbedded).toHaveBeenCalledTimes(1);

    const questionRequest = fixture.requests.find((request) =>
      new URL(request.url).pathname.endsWith("/knowledge/questions"),
    );
    const chunkRequest = fixture.requests.find((request) =>
      new URL(request.url).pathname.endsWith("/knowledge/chunks"),
    );
    for (const request of [questionRequest, chunkRequest]) {
      const url = new URL(request?.url ?? "http://localhost");
      expect(url.searchParams.get("review_state")).toBe("reviewed");
      expect(url.searchParams.get("limit")).toBe("100");
      expect(url.searchParams.get("offset")).toBe("0");
    }
  });

  it("polls queued work with bounded backoff, ignores stale versions, and refreshes embedded metadata", async () => {
    const initialQuestion = question();
    const initialChunk = chunk();
    const claimed = job({
      claimed_at: "2026-08-25T00:01:01Z",
      status: "claimed",
      version: 1,
    });
    const stale = job({ status: "queued", version: 0 });
    const succeeded = job({
      claimed_at: claimed.claimed_at,
      completed_at: "2026-08-25T00:01:03Z",
      counts: { deduplicated: 0, embedded: 2, requested: 2 },
      status: "succeeded",
      version: 2,
    });
    const fixture = fixtureApi({
      chunks: [initialChunk],
      onPoll: (_request, index) => {
        const next = [claimed, stale, succeeded][index] ?? succeeded;
        if (next.status === "succeeded") {
          fixture.setQuestions([embeddedQuestion(initialQuestion)]);
          fixture.setChunks([embeddedChunk(initialChunk)]);
        }
        return Response.json(next);
      },
      onPost: () => Response.json(job(), { status: 202 }),
      questions: [initialQuestion],
    });
    const onRecordsEmbedded = vi.fn();
    await renderLoaded("admin", fixture, onRecordsEmbedded);
    fireEvent.click(screen.getByRole("checkbox", { name: /Select historical question/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Select knowledge chunk/ }));
    vi.useFakeTimers();

    fireEvent.click(screen.getByRole("button", { name: "Queue selected records" }));
    await flushAsyncOperation();
    expect(screen.getByText("Queued", { exact: true })).toBeInTheDocument();
    expect(embeddingPolls(fixture.requests)).toHaveLength(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(249);
    });
    expect(embeddingPolls(fixture.requests)).toHaveLength(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(embeddingPolls(fixture.requests)).toHaveLength(1);
    expect(screen.getByText("Claimed", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("Version 1", { exact: true })).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(embeddingPolls(fixture.requests)).toHaveLength(2);
    expect(screen.getByText("Claimed", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("Version 1", { exact: true })).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(screen.getByText("Embedding job succeeded.")).toBeInTheDocument();
    expect(screen.getByText("Succeeded", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("Version 2", { exact: true })).toBeInTheDocument();
    expect(onRecordsEmbedded).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await flushAsyncOperation();

    const questionRow = screen.getByRole("listitem", { name: /Historical question 2025-I/ });
    const chunkRow = screen.getByRole("listitem", { name: /Knowledge chunk Geometry boundary/ });
    for (const row of [questionRow, chunkRow]) {
      expect(within(row).getByText("Embedded", { exact: true })).toBeInTheDocument();
      expect(within(row).getByText("local / multilingual-e5-small / v1 / 384d")).toBeInTheDocument();
      expect(within(row).getByText("sha256:fixture-config")).toBeInTheDocument();
    }
  });

  it.each(["curriculum change", "unmount"] as const)(
    "aborts in-flight polling on %s without accepting stale work",
    async (cancellation) => {
      let pollSignal: AbortSignal | undefined;
      const fixture = fixtureApi({
        onPoll: (request) =>
          new Promise<Response>((_resolve, reject) => {
            pollSignal = request.signal;
            request.signal.addEventListener("abort", () => {
              reject(new DOMException("Aborted", "AbortError"));
            });
          }),
        onPost: () => Response.json(job(), { status: 202 }),
      });
      const { view } = await renderLoaded("admin", fixture);
      fireEvent.click(screen.getByRole("checkbox", { name: /Select historical question/ }));
      vi.useFakeTimers();
      fireEvent.click(screen.getByRole("button", { name: "Queue selected records" }));
      await flushAsyncOperation();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });
      expect(pollSignal?.aborted).toBe(false);

      if (cancellation === "curriculum change") {
        view.rerender(
          <EmbeddingIngestion
            curriculumVersionId={ids.otherCurriculum}
            onRecordsEmbedded={vi.fn()}
            role="admin"
          />,
        );
      } else {
        view.unmount();
      }

      expect(pollSignal?.aborted).toBe(true);
      await act(async () => {
        await Promise.resolve();
      });
    },
  );

  it("caps polling at four-second intervals and pauses at two minutes", async () => {
    const fixture = fixtureApi({
      onPoll: () => Response.json(job()),
      onPost: () => Response.json(job(), { status: 202 }),
    });
    await renderLoaded("admin", fixture);
    fireEvent.click(screen.getByRole("checkbox", { name: /Select historical question/ }));
    vi.useFakeTimers();
    const startedAt = Date.now();
    fireEvent.click(screen.getByRole("button", { name: "Queue selected records" }));
    await flushAsyncOperation();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(119_999);
    });

    expect(screen.queryByText("Automatic job monitoring paused")).not.toBeInTheDocument();
    const relativeTimes = fixture.pollTimes.map((value) => value - startedAt);
    expect(relativeTimes.slice(0, 5)).toEqual([250, 750, 1_750, 3_750, 7_750]);
    expect(
      relativeTimes.slice(1).every((value, index) => value - relativeTimes[index] <= 4_000),
    ).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(screen.getByRole("heading", { name: "Automatic job monitoring paused" })).toBeVisible();
    const pollCount = fixture.pollTimes.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8_000);
    });
    expect(fixture.pollTimes).toHaveLength(pollCount);
    expect(screen.getByRole("button", { name: "Resume job monitoring" })).toBeEnabled();
  });

  it("surfaces terminal worker failure with an allowlisted code and keeps the reviewed selection retryable", async () => {
    const failed = job({
      claimed_at: "2026-08-25T00:01:01Z",
      completed_at: "2026-08-25T00:01:03Z",
      failure_code: "embedding_provider_unavailable",
      status: "failed",
      version: 2,
    });
    const fixture = fixtureApi({
      onPoll: () => Response.json(failed),
      onPost: () => Response.json(job(), { status: 202 }),
    });
    const onRecordsEmbedded = vi.fn();
    await renderLoaded("admin", fixture, onRecordsEmbedded);
    fireEvent.click(screen.getByRole("checkbox", { name: /Select historical question/ }));
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Queue selected records" }));
    await flushAsyncOperation();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });

    expect(screen.getByText("Embedding job failed.")).toBeInTheDocument();
    expect(screen.getByText("embedding_provider_unavailable", { exact: true })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Queue selected records" })).toBeEnabled();
    expect(screen.getByText("1 of 100 records selected")).toBeInTheDocument();
    expect(onRecordsEmbedded).not.toHaveBeenCalled();
  });

  it("exposes a capped embedding lineage and disables the forbidden unchanged selection", async () => {
    const capped = job({
      claimed_at: "2026-08-25T00:01:01Z",
      completed_at: "2026-08-25T00:01:03Z",
      failure_code: "embedding_provider_unavailable",
      retry_depth: 3,
      retry_of_job_id: ids.retryJob,
      status: "failed",
      version: 2,
    });
    const fixture = fixtureApi({
      jobs: [capped],
      questions: [question(), question(uuid(3_333))],
      onPost: () =>
        Response.json(
          { detail: { code: "embedding_retry_limit_exceeded" } },
          { status: 409 },
        ),
    });
    await renderLoaded("admin", fixture);

    expect(
      within(screen.getByRole("region", { name: `Embedding job ${ids.job}` })).getByText(
        "Retry depth 3 of 3",
      ),
    ).toBeVisible();
    const questionChoices = screen.getAllByRole("checkbox", {
      name: /Select historical question/,
    });
    fireEvent.click(questionChoices[0]!);
    fireEvent.click(questionChoices[1]!);
    const submit = screen.getByRole("button", { name: "Queue selected records" });
    fireEvent.click(submit);

    expect(await screen.findByRole("heading", { name: "Embedding retry limit reached" })).toBeVisible();
    expect(submit).toBeDisabled();

    fireEvent.click(questionChoices[0]!);
    fireEvent.click(questionChoices[0]!);
    expect(submit).toBeDisabled();
  });

  it("handles configuration, provider, queue, review, conflict, permission, and network failures without leaking details", async () => {
    const replies: Array<{ code: string; status: number } | "network"> = [
      { code: "embedding_config_unavailable", status: 503 },
      { code: "embedding_provider_unavailable", status: 503 },
      { code: "embedding_provider_batch_limit_exceeded", status: 422 },
      { code: "embedding_queue_unavailable", status: 503 },
      { code: "embedding_source_not_reviewed", status: 422 },
      { code: "embedding_source_conflict", status: 409 },
      { code: "embedding_idempotency_conflict", status: 409 },
      { code: "permission_denied", status: 403 },
      "network",
    ];
    const fixture = fixtureApi({
      onPost: (_request, index) => {
        const reply = replies[index];
        if (reply === "network" || !reply) throw new TypeError("secret network trace");
        return Response.json(
          { detail: { code: reply.code, internal: "provider-api-key secret traceback" } },
          { status: reply.status },
        );
      },
    });
    await renderLoaded("admin", fixture);
    fireEvent.click(screen.getByRole("checkbox", { name: /Select historical question/ }));
    const expectedHeadings = [
      "Embedding configuration unavailable",
      "Embedding provider unavailable",
      "Reduce the record selection",
      "Embedding queue unavailable",
      "Selection is no longer reviewed",
      "Embedding source conflict",
      "Embedding idempotency conflict",
      "Embedding permission required",
      "Embedding service connection failed",
    ];

    for (const heading of expectedHeadings) {
      let button = screen.getByRole("button", { name: "Queue selected records" });
      if (button.hasAttribute("disabled")) {
        await waitFor(() => {
          expect(screen.getByRole("checkbox", { name: /Select historical question/ })).toBeEnabled();
        });
        fireEvent.click(screen.getByRole("checkbox", { name: /Select historical question/ }));
        button = screen.getByRole("button", { name: "Queue selected records" });
      }
      await waitFor(() => expect(button).toBeEnabled());
      fireEvent.click(button);
      expect(await screen.findByRole("heading", { name: heading })).toBeVisible();
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Queue selected records" }),
        ).toBeInTheDocument(),
      );
    }
    expect(embeddingPosts(fixture.requests)).toHaveLength(expectedHeadings.length);
    expect(screen.queryByText(/provider-api-key|secret network trace|traceback/i)).not.toBeInTheDocument();
  });

  it("renders reviewer job metadata read-only, deduplicates versions, and never renders source text or extra response secrets", async () => {
    const unsafe = '<img src=x onerror="globalThis.__unsafe_call__()">';
    const failedJob = {
      ...job({
        completed_at: "2026-08-25T00:01:04Z",
        failure_code: "embedding_internal_error",
        id: ids.retryJob,
        status: "failed",
        version: 2,
      }),
      provider_error: "provider-secret-value",
      raw_vector: [0.1, 0.2],
      source_fingerprint: "sha256:source-secret",
      text: "job-source-text-secret",
    } as EmbeddingJob;
    const succeeded = job({
      claimed_at: "2026-08-25T00:01:01Z",
      completed_at: "2026-08-25T00:01:03Z",
      counts: { deduplicated: 1, embedded: 1, requested: 2 },
      deduplicated: true,
      retry_depth: 1,
      retry_of_job_id: ids.retryJob,
      status: "succeeded",
      version: 3,
    });
    const fixture = fixtureApi({
      jobs: [job({ version: 0 }), succeeded, failedJob],
      questions: [
        {
          ...question(undefined, { text: `record-source-text-secret ${unsafe}` }),
          raw_vector: [9, 8, 7],
        } as HistoricalQuestion,
      ],
    });
    const { container } = (await renderLoaded("reviewer", fixture)).view;

    expect(screen.queryByRole("button", { name: "Queue selected records" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Select up to/ })).not.toBeInTheDocument();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.getByText("Reviewer read-only access")).toBeInTheDocument();
    const succeededCard = screen.getByRole("region", { name: `Embedding job ${ids.job}` });
    const failedCard = screen.getByRole("region", { name: `Embedding job ${ids.retryJob}` });
    expect(screen.getAllByRole("region", { name: `Embedding job ${ids.job}` })).toHaveLength(1);
    expect(within(succeededCard).getByText("Succeeded", { exact: true })).toBeInTheDocument();
    expect(within(succeededCard).getByText("local", { exact: true })).toBeInTheDocument();
    expect(
      within(succeededCard).getByText("multilingual-e5-small", { exact: true }),
    ).toBeInTheDocument();
    expect(within(succeededCard).getByText("384", { exact: true })).toBeInTheDocument();
    expect(within(succeededCard).getByText("v1", { exact: true })).toBeInTheDocument();
    expect(
      within(succeededCard).getByText("sha256:fixture-config", { exact: true }),
    ).toBeInTheDocument();
    expect(within(succeededCard).getByText(ids.retryJob, { exact: true })).toBeInTheDocument();
    expect(within(succeededCard).getByText("Retry depth 1 of 3")).toBeInTheDocument();
    expect(within(failedCard).getByText("Retry depth 0 of 3")).toBeInTheDocument();
    expect(
      within(failedCard).getByText("embedding_internal_error", { exact: true }),
    ).toBeInTheDocument();
    expect(
      within(succeededCard).getByText("2026-08-25T00:01:00Z", { exact: true }),
    ).toBeInTheDocument();
    expect(
      within(succeededCard).getByText("2026-08-25T00:01:01Z", { exact: true }),
    ).toBeInTheDocument();
    expect(
      within(succeededCard).getByText("2026-08-25T00:01:03Z", { exact: true }),
    ).toBeInTheDocument();
    expect(within(succeededCard).getByText("Submission deduplicated: Yes")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("record-source-text-secret");
    expect(container).not.toHaveTextContent("job-source-text-secret");
    expect(container).not.toHaveTextContent("provider-secret-value");
    expect(container).not.toHaveTextContent("sha256:source-secret");
    expect(container).not.toHaveTextContent("0.1");
    expect(container.querySelector("img, script")).toBeNull();
  });

  it("shows bounded loading, retry, and no-reviewed-record states", async () => {
    let release: (() => void) | undefined;
    let firstList = true;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const base = fixtureApi({ chunks: [], questions: [] });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = asRequest(input, init);
      const url = new URL(request.url);
      if (request.method === "GET" && url.pathname.endsWith("/embedding-jobs") && firstList) {
        firstList = false;
        await gate;
        throw new TypeError("offline secret");
      }
      return base.fetchMock(request);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <EmbeddingIngestion
        curriculumVersionId={ids.curriculum}
        onRecordsEmbedded={vi.fn()}
        role="admin"
      />,
    );

    expect(screen.getByText("Loading embedding data…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Queue selected records" })).not.toBeInTheDocument();
    release?.();
    expect(
      await screen.findByRole("heading", { name: "Embedding data could not be loaded" }),
    ).toBeVisible();
    expect(screen.queryByText(/offline secret/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry embedding data" }));
    expect(await screen.findByRole("heading", { name: "No reviewed records available" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Queue selected records" })).toBeDisabled();
  });

  it("has no automated accessibility violations in the complete admin state", async () => {
    const fixture = fixtureApi({
      jobs: [
        job({
          claimed_at: "2026-08-25T00:01:01Z",
          completed_at: "2026-08-25T00:01:03Z",
          counts: { deduplicated: 0, embedded: 2, requested: 2 },
          status: "succeeded",
          version: 2,
        }),
      ],
      questions: [embeddedQuestion(question())],
      chunks: [embeddedChunk(chunk())],
    });
    const { container } = (await renderLoaded("admin", fixture)).view;

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });
});
