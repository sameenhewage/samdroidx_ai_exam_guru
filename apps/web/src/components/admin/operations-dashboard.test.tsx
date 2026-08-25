import type { components } from "@exam-guru/api-client";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OperationsDashboard } from "./operations-dashboard";

type Summary = components["schemas"]["OperationsSummaryResponse"];

const summary = {
  window: {
    start: "2026-08-24T08:30:00Z",
    end: "2026-08-24T09:30:00Z",
    semantics: "start_inclusive_end_exclusive",
  },
  data_bounds: {
    earliest_observed_at: "2026-08-24T08:45:00Z",
    latest_observed_at: "2026-08-24T09:20:00Z",
  },
  units: {
    counts: "count",
    tokens: "token",
    cost: "microusd",
    latency: "millisecond",
    timestamps: "UTC",
  },
  generation: {
    run_count: 4,
    status_counts: { pending: 1, running: 1, succeeded: 1, failed: 1 },
    failure_codes: [
      { code: "provider_timeout", count: 2 },
      { code: "<script>unsafe()</script>", count: 1 },
    ],
    attempt_count: 6,
    input_tokens: 1_000,
    output_tokens: 234,
    total_tokens: 1_234,
    cost_microusd: 1_234_567,
    latency_ms: { total: 400, average: 100, maximum: 180 },
  },
  validation: {
    run_count: 3,
    run_status_counts: { pass: 1, warn: 1, fail: 1 },
    finding_count: 6,
    finding_status_counts: { pass: 4, warn: 1, fail: 1 },
  },
  extraction: {
    document_count: 6,
    status_counts: {
      uploaded: 1,
      extraction_pending: 1,
      extracted: 1,
      in_review: 1,
      trusted: 1,
      failed: 1,
    },
    failure_codes: [{ code: "ocr_timeout", count: 1 }],
    ocr_page_count: 7,
  },
  embedding: {
    job_count: 4,
    status_counts: { queued: 1, claimed: 1, succeeded: 1, failed: 1 },
    failure_codes: [{ code: "embedding_provider_unavailable", count: 1 }],
    requested_count: 20,
    embedded_count: 17,
    deduplicated_count: 2,
  },
  object_storage: {
    reconciliation: {
      run_count: 3,
      scanned_count: 48,
      referenced_count: 31,
      candidate_count: 9,
      resolved_count: 4,
      tagged_count: 6,
      failure_count: 2,
      truncated_run_count: 1,
      current_candidate_count: 5,
      last_completed_at: "2026-08-24T09:15:00Z",
      failure_codes: [
        { code: "object_storage_list_failed", count: 1 },
        { code: "<img src=x onerror=unsafe()>", count: 1 },
      ],
    },
  },
  practice_papers: {
    paper_count: 3,
    state_counts: { draft: 1, published: 1, archived: 1 },
    publication_count: 5,
    archive_count: 1,
  },
} satisfies Summary;

const emptySummary = {
  ...summary,
  data_bounds: { earliest_observed_at: null, latest_observed_at: null },
  generation: {
    run_count: 0,
    status_counts: { pending: 0, running: 0, succeeded: 0, failed: 0 },
    failure_codes: [],
    attempt_count: 0,
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    cost_microusd: 0,
    latency_ms: { total: 0, average: 0, maximum: 0 },
  },
  validation: {
    run_count: 0,
    run_status_counts: { pass: 0, warn: 0, fail: 0 },
    finding_count: 0,
    finding_status_counts: { pass: 0, warn: 0, fail: 0 },
  },
  extraction: {
    document_count: 0,
    status_counts: {
      uploaded: 0,
      extraction_pending: 0,
      extracted: 0,
      in_review: 0,
      trusted: 0,
      failed: 0,
    },
    failure_codes: [],
    ocr_page_count: 0,
  },
  embedding: {
    job_count: 0,
    status_counts: { queued: 0, claimed: 0, succeeded: 0, failed: 0 },
    failure_codes: [],
    requested_count: 0,
    embedded_count: 0,
    deduplicated_count: 0,
  },
  object_storage: {
    reconciliation: {
      run_count: 0,
      scanned_count: 0,
      referenced_count: 0,
      candidate_count: 0,
      resolved_count: 0,
      tagged_count: 0,
      failure_count: 0,
      truncated_run_count: 0,
      current_candidate_count: 0,
      last_completed_at: null,
      failure_codes: [],
    },
  },
  practice_papers: {
    paper_count: 0,
    state_counts: { draft: 0, published: 0, archived: 0 },
    publication_count: 0,
    archive_count: 0,
  },
} satisfies Summary;

function requestFrom(input: string | URL | Request): Request {
  return input instanceof Request ? input : new Request(input);
}

function summaryApi(body: object = summary) {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    requests.push(requestFrom(input));
    return Response.json(body);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, requests };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("OperationsDashboard", () => {
  it("loads the fixed generated-client summary and renders every aggregate with explicit UTC bounds and units", async () => {
    const { requests } = summaryApi();

    render(<OperationsDashboard />);

    expect(screen.getByText("Loading operational summary…")).toBeInTheDocument();
    await screen.findByTestId("generation-run-count");

    expect(requests).toHaveLength(1);
    const url = new URL(requests[0].url);
    expect(url.pathname).toBe("/api/v1/admin/operations/summary");
    expect([...url.searchParams.keys()]).toEqual([]);

    expect(screen.getByText("Start inclusive · end exclusive")).toBeVisible();
    expect(screen.getByTestId("window-start")).toHaveTextContent("2026-08-24T08:30:00.000Z");
    expect(screen.getByTestId("window-end")).toHaveTextContent("2026-08-24T09:30:00.000Z");
    expect(screen.getByTestId("data-earliest")).toHaveTextContent("2026-08-24T08:45:00.000Z");
    expect(screen.getByTestId("data-latest")).toHaveTextContent("2026-08-24T09:20:00.000Z");
    expect(screen.getByTestId("unit-counts")).toHaveTextContent("count");
    expect(screen.getByTestId("unit-tokens")).toHaveTextContent("token");
    expect(screen.getByTestId("unit-cost")).toHaveTextContent("microusd");
    expect(screen.getByTestId("unit-latency")).toHaveTextContent("millisecond");
    expect(screen.getByTestId("unit-timestamps")).toHaveTextContent("UTC");

    expect(screen.getByTestId("generation-run-count")).toHaveTextContent("4");
    expect(screen.getByTestId("generation-status-pending")).toHaveTextContent("1");
    expect(screen.getByTestId("generation-status-running")).toHaveTextContent("1");
    expect(screen.getByTestId("generation-status-succeeded")).toHaveTextContent("1");
    expect(screen.getByTestId("generation-status-failed")).toHaveTextContent("1");
    expect(screen.getByTestId("generation-attempt-count")).toHaveTextContent("6");
    expect(screen.getByTestId("generation-input-tokens")).toHaveTextContent("1,000");
    expect(screen.getByTestId("generation-output-tokens")).toHaveTextContent("234");
    expect(screen.getByTestId("generation-total-tokens")).toHaveTextContent("1,234");
    expect(screen.getByTestId("generation-cost-microusd")).toHaveTextContent(
      "1,234,567 microusd",
    );
    expect(screen.getByTestId("generation-cost-usd")).toHaveTextContent("1.234567 USD");
    expect(screen.getByTestId("generation-latency-total")).toHaveTextContent("400 ms");
    expect(screen.getByTestId("generation-latency-average")).toHaveTextContent("100 ms");
    expect(screen.getByTestId("generation-latency-maximum")).toHaveTextContent("180 ms");
    expect(screen.getByTestId("generation-failure-0")).toHaveTextContent(
      "provider_timeout — 2",
    );
    expect(screen.getByTestId("generation-failure-1")).toHaveTextContent(
      "unrecognized_failure_code — 1",
    );

    expect(screen.getByTestId("validation-run-count")).toHaveTextContent("3");
    expect(screen.getByTestId("validation-run-status-pass")).toHaveTextContent("1");
    expect(screen.getByTestId("validation-run-status-warn")).toHaveTextContent("1");
    expect(screen.getByTestId("validation-run-status-fail")).toHaveTextContent("1");
    expect(screen.getByTestId("validation-finding-count")).toHaveTextContent("6");
    expect(screen.getByTestId("validation-finding-status-pass")).toHaveTextContent("4");
    expect(screen.getByTestId("validation-finding-status-warn")).toHaveTextContent("1");
    expect(screen.getByTestId("validation-finding-status-fail")).toHaveTextContent("1");

    expect(screen.getByTestId("extraction-document-count")).toHaveTextContent("6");
    expect(screen.getByTestId("extraction-ocr-page-count")).toHaveTextContent("7");
    for (const status of [
      "uploaded",
      "extraction-pending",
      "extracted",
      "in-review",
      "trusted",
      "failed",
    ]) {
      expect(screen.getByTestId(`extraction-status-${status}`)).toHaveTextContent("1");
    }
    expect(screen.getByTestId("extraction-failure-0")).toHaveTextContent("ocr_timeout — 1");

    expect(screen.getByTestId("embedding-job-count")).toHaveTextContent("4");
    expect(screen.getByTestId("embedding-requested-count")).toHaveTextContent("20");
    expect(screen.getByTestId("embedding-embedded-count")).toHaveTextContent("17");
    expect(screen.getByTestId("embedding-deduplicated-count")).toHaveTextContent("2");
    for (const status of ["queued", "claimed", "succeeded", "failed"]) {
      expect(screen.getByTestId(`embedding-status-${status}`)).toHaveTextContent("1");
    }
    expect(screen.getByTestId("embedding-failure-0")).toHaveTextContent(
      "embedding_provider_unavailable — 1",
    );

    expect(
      screen.getByRole("heading", { level: 2, name: "Object storage reconciliation" }),
    ).toBeVisible();
    expect(screen.getByTestId("storage-reconciliation-run-count")).toHaveTextContent("3");
    expect(screen.getByTestId("storage-reconciliation-scanned-count")).toHaveTextContent("48");
    expect(screen.getByTestId("storage-reconciliation-referenced-count")).toHaveTextContent("31");
    expect(screen.getByTestId("storage-reconciliation-candidate-count")).toHaveTextContent("9");
    expect(screen.getByTestId("storage-reconciliation-resolved-count")).toHaveTextContent("4");
    expect(screen.getByTestId("storage-reconciliation-tagged-count")).toHaveTextContent("6");
    expect(screen.getByTestId("storage-reconciliation-failure-count")).toHaveTextContent("2");
    expect(screen.getByTestId("storage-reconciliation-truncated-run-count")).toHaveTextContent(
      "1",
    );
    expect(screen.getByTestId("storage-reconciliation-current-candidate-count")).toHaveTextContent(
      "5",
    );
    expect(screen.getByTestId("storage-reconciliation-last-completed-at")).toHaveTextContent(
      "2026-08-24T09:15:00.000Z",
    );
    expect(screen.getByTestId("storage-reconciliation-failure-0")).toHaveTextContent(
      "object_storage_list_failed — 1",
    );
    expect(screen.getByTestId("storage-reconciliation-failure-1")).toHaveTextContent(
      "unrecognized_failure_code — 1",
    );
    expect(screen.getByText("Dry-run / no-delete boundary")).toBeVisible();
    expect(screen.getByText(/it never deletes an object or overwrites operator-owned tags/i)).toBeVisible();
    expect(
      screen.getByText(
        /external lifecycle deletion remains a separate, explicitly approved storage-policy action outside application reconciliation/i,
      ),
    ).toBeVisible();

    expect(screen.getByTestId("papers-paper-count")).toHaveTextContent("3");
    expect(screen.getByTestId("papers-status-draft")).toHaveTextContent("1");
    expect(screen.getByTestId("papers-status-published")).toHaveTextContent("1");
    expect(screen.getByTestId("papers-status-archived")).toHaveTextContent("1");
    expect(screen.getByTestId("papers-publication-count")).toHaveTextContent("5");
    expect(screen.getByTestId("papers-archive-count")).toHaveTextContent("1");
    expect(
      screen.getByText(/Logs and spans require a configured collector and dashboard/),
    ).toBeVisible();
    expect(screen.getByText(/This summary is persisted aggregates/)).toBeVisible();
  });

  it("supports only fixed presets and validates a half-open custom UTC window before requesting", async () => {
    const { requests } = summaryApi();
    render(<OperationsDashboard />);
    await screen.findByTestId("generation-run-count");

    for (const [name, duration] of [
      ["Last 1 hour", 60 * 60 * 1_000],
      ["Last 24 hours", 24 * 60 * 60 * 1_000],
      ["Last 7 days", 7 * 24 * 60 * 60 * 1_000],
      ["Last 31 days", 31 * 24 * 60 * 60 * 1_000],
    ] as const) {
      fireEvent.click(screen.getByRole("button", { name }));
      await waitFor(() => expect(requests).toHaveLength(2));
      const presetUrl = new URL(requests[1].url);
      expect([...presetUrl.searchParams.keys()].sort()).toEqual(["end", "start"]);
      const start = Date.parse(presetUrl.searchParams.get("start") ?? "");
      const end = Date.parse(presetUrl.searchParams.get("end") ?? "");
      expect(end - start).toBe(duration);
      expect(presetUrl.searchParams.get("start")).toMatch(/Z$/);
      expect(presetUrl.searchParams.get("end")).toMatch(/Z$/);
      requests.splice(1);
    }

    fireEvent.change(screen.getByLabelText("Start (UTC)"), {
      target: { value: "2026-01-01T00:00" },
    });
    fireEvent.change(screen.getByLabelText("End (UTC)"), {
      target: { value: "2026-02-02T00:00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply custom window" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Custom window must be 31 days or less.",
    );
    expect(requests).toHaveLength(1);

    fireEvent.change(screen.getByLabelText("End (UTC)"), {
      target: { value: "2026-01-02T00:00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply custom window" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    const customUrl = new URL(requests[1].url);
    expect(Object.fromEntries(customUrl.searchParams)).toEqual({
      start: "2026-01-01T00:00:00.000Z",
      end: "2026-01-02T00:00:00.000Z",
    });
  });

  it.each([
    [401, "Authentication required", "Your admin session has expired."],
    [403, "Administrator access required", "Operational aggregates are restricted to administrators."],
    [422, "Window rejected", "The server rejected this UTC window."],
  ])("renders a retryable %i response without stale aggregates", async (status, title, message) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ detail: { code: "operations_window_invalid_order" } }, { status }),
      ),
    );

    render(<OperationsDashboard />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(title);
    expect(alert).toHaveTextContent(message);
    expect(screen.getByRole("button", { name: "Retry summary" })).toBeVisible();
    expect(screen.queryByTestId("generation-run-count")).not.toBeInTheDocument();
    if (status === 401) {
      expect(screen.getByRole("link", { name: "Sign in again" })).toHaveAttribute(
        "href",
        "/admin/login",
      );
    }
  });

  it("renders loading, empty, network failure, and successful retry states", async () => {
    let attempt = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        attempt += 1;
        if (attempt === 1) throw new TypeError("private network detail must not render");
        return Response.json(emptySummary);
      }),
    );

    render(<OperationsDashboard />);
    expect(screen.getByText("Loading operational summary…")).toBeVisible();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Operations summary unavailable");
    expect(alert).not.toHaveTextContent("private network detail");

    fireEvent.click(screen.getByRole("button", { name: "Retry summary" }));
    expect(await screen.findByText("No operational data was observed in this window.")).toBeVisible();
    expect(screen.getByTestId("generation-run-count")).toHaveTextContent("0");
    expect(screen.getByTestId("generation-cost-usd")).toHaveTextContent("0.000000 USD");
    expect(screen.getByTestId("data-earliest")).toHaveTextContent("No observations");
    expect(screen.getByTestId("storage-reconciliation-run-count")).toHaveTextContent("0");
    expect(screen.getByTestId("storage-reconciliation-current-candidate-count")).toHaveTextContent(
      "0",
    );
    expect(screen.getByTestId("storage-reconciliation-last-completed-at")).toHaveTextContent(
      "No observations",
    );
    expect(screen.getByTestId("storage-reconciliation-failure-empty")).toHaveTextContent(
      "No failures recorded.",
    );
  });

  it("is accessible and renders only allow-listed aggregate text, sanitizing untrusted codes", async () => {
    const sensitivePayload = {
      ...summary,
      object_storage: {
        ...summary.object_storage,
        continuation_cursor: "continuation-cursor-must-not-render",
        object_key: "object-key-must-not-render",
        reconciliation: {
          ...summary.object_storage.reconciliation,
          continuation_cursor: "nested-continuation-cursor-must-not-render",
          finding_id: "finding-id-must-not-render",
          object_key: "nested-object-key-must-not-render",
          run_id: "run-id-must-not-render",
          tag_values: ["tag-value-must-not-render"],
        },
        storage_id: "storage-id-must-not-render",
        tag_values: ["outer-tag-value-must-not-render"],
      },
      resource_id: "resource-id-must-not-render",
      content: "content-must-not-render",
      prompt: "prompt-must-not-render",
      vector: "vector-must-not-render",
      ["se" + "cret"]: "se" + "cret-must-not-render",
    };
    summaryApi(sensitivePayload);

    const { container } = render(<OperationsDashboard />);
    await screen.findByTestId("generation-run-count");

    expect(container.querySelector("script")).toBeNull();
    expect(container).not.toHaveTextContent("<script>unsafe()</script>");
    for (const value of [
      "continuation_cursor",
      "continuation-cursor-must-not-render",
      "nested-continuation-cursor-must-not-render",
      "object_key",
      "object-key-must-not-render",
      "nested-object-key-must-not-render",
      "tag-value-must-not-render",
      "outer-tag-value-must-not-render",
      "finding-id-must-not-render",
      "run-id-must-not-render",
      "storage-id-must-not-render",
      "resource-id-must-not-render",
      "content-must-not-render",
      "prompt-must-not-render",
      "vector-must-not-render",
      "secret-must-not-render",
    ]) {
      expect(container).not.toHaveTextContent(value);
    }

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });
});
