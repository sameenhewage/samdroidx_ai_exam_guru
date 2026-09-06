import type { components } from "@exam-guru/api-client";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MaterialsLibrary } from "./materials-library";
import { SourceBenchmarkReview } from "./source-benchmark-review";

type Benchmark = components["schemas"]["SourceBenchmarkResponse"];

const benchmarkId = "00000000-0000-0000-0000-000000000881";
const secondBenchmarkId = "00000000-0000-0000-0000-000000000882";
const documentId = "00000000-0000-0000-0000-000000000883";
const basePath = "/api/v1/admin/source-benchmarks";

function benchmark(
  overrides: Partial<Benchmark> = {},
  pageCount = 40,
): Benchmark {
  return {
    id: benchmarkId,
    name: "Forty selected source pages",
    created_at: "2026-09-06T10:00:00Z",
    pages: Array.from({ length: pageCount }, (_, index) => ({
      document_id: documentId,
      document_title: "Original teacher guide.pdf",
      page_number: index + 1,
      categories: ["sinhala", index % 2 === 0 ? "maths" : "tables"],
      state:
        index === 0 ? "verified" : index === 1 ? "excluded" : "needs_review",
      ground_truth_versions: 0,
    })),
    adjudicated_pages: 0,
    pending_pages: pageCount,
    accuracy_status: "awaiting_human_adjudication",
    ...overrides,
  };
}

function fixtureApi(
  sets: Benchmark[] = [benchmark()],
  handle?: (request: Request) => Response | Promise<Response> | undefined,
) {
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request =
        input instanceof Request ? input : new Request(input, init);
      requests.push(request.clone());
      const response = handle?.(request);
      if (response) return response;
      const path = new URL(request.url).pathname;
      if (request.method === "GET" && path === basePath)
        return Response.json(sets);
      const selected = sets.find((set) => path === `${basePath}/${set.id}`);
      if (request.method === "GET" && selected) return Response.json(selected);
      return Response.json(
        { detail: { code: "unexpected_request" } },
        { status: 500 },
      );
    }),
  );
  return requests;
}

function summary(label: string, value: number) {
  const term = within(
    screen.getByRole("region", { name: "Review set progress" }),
  ).getByText(label);
  expect(term.parentElement).toHaveTextContent(
    new RegExp(`${label}\\s*${value}$`),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("human source review queue", () => {
  it("distinguishes selected, pending and excluded candidates from actual human-confirmed references", async () => {
    const requests = fixtureApi();
    const view = render(<SourceBenchmarkReview role="admin" />);
    await screen.findByRole("table", { name: "Selected source pages" });
    summary("Selected pages", 40);
    summary("Human-confirmed reference pages", 0);
    summary("Pages without a confirmed reference", 40);
    summary("Currently excluded pages", 1);
    expect(
      screen.getByText(
        "Accuracy is not established. These pages still need human comparison.",
      ),
    ).toBeVisible();
    const rows = within(
      screen.getByRole("table", { name: "Selected source pages" }),
    ).getAllByRole("row");
    expect(rows).toHaveLength(41);
    expect(rows[1]).toHaveTextContent("Page confirmed");
    expect(within(rows[1]).getByRole("cell", { name: "0" })).toBeVisible();
    expect(rows[2]).toHaveTextContent("Not used");
    expect(
      screen.queryByRole("button", { name: /approve|confirm|trust/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/100%|accuracy passed|production.ready/i),
    ).not.toBeInTheDocument();
    expect(requests.every((request) => request.method === "GET")).toBe(true);
    expect(requests).toHaveLength(2);
    expect(requests.every((request) => request.cache === "no-store")).toBe(
      true,
    );
    expect(new URL(requests[0].url).searchParams.get("limit")).toBe("40");
    expect(view.container.textContent).not.toContain("fixture-model");
    expect(
      screen.getByText("Technical details").closest("details"),
    ).not.toHaveAttribute("open");
  });

  it("uses the fresh benchmark detail counts rather than candidate state or stale list totals", async () => {
    const detail = benchmark({
      adjudicated_pages: 1,
      pending_pages: 39,
      accuracy_status: "references_available",
    });
    detail.pages[2] = {
      ...detail.pages[2],
      state: "verified",
      ground_truth_versions: 2,
    };
    fixtureApi(
      [benchmark({ adjudicated_pages: 40, pending_pages: 0 })],
      (request) => {
        if (new URL(request.url).pathname === `${basePath}/${benchmarkId}`)
          return Response.json(detail);
      },
    );
    render(<SourceBenchmarkReview role="admin" />);
    await screen.findByRole("table", { name: "Selected source pages" });
    summary("Human-confirmed reference pages", 1);
    summary("Pages without a confirmed reference", 39);
    expect(
      screen.getByText(
        "Human-confirmed references are available. This is not an accuracy result.",
      ),
    ).toBeVisible();
    const rows = within(
      screen.getByRole("table", { name: "Selected source pages" }),
    ).getAllByRole("row");
    expect(within(rows[3]).getByRole("cell", { name: "2" })).toBeVisible();
  });

  it("links each original page to an explicit comparison without automatically approving or reading it", async () => {
    const requests = fixtureApi();
    render(<SourceBenchmarkReview role="admin" />);
    const link = await screen.findByRole("link", {
      name: "Compare page 17 of Original teacher guide.pdf",
    });
    expect(link).toHaveAttribute(
      "href",
      `/admin/materials/${documentId}/review-text?page_number=17&benchmark_id=${benchmarkId}`,
    );
    expect(
      requests.every((request) =>
        new URL(request.url).pathname.startsWith(basePath),
      ),
    ).toBe(true);
    expect(requests.some((request) => request.method === "POST")).toBe(false);
    expect(
      screen.queryByRole("button", { name: /all|approve/i }),
    ).not.toBeInTheDocument();
  });

  it("bounds even a 371-page selection to 40 queue rows and preserves exact page links across batches", async () => {
    const requests = fixtureApi([benchmark({}, 371)]);
    render(<SourceBenchmarkReview role="admin" />);
    const table = await screen.findByRole("table", {
      name: "Selected source pages",
    });
    expect(within(table).getAllByRole("row")).toHaveLength(41);
    expect(table.parentElement).toHaveClass("max-h-[60dvh]", "overflow-auto");
    expect(screen.getByText("Pages 1–40 of 371")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Previous 40 pages" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Next 40 pages" }));
    expect(screen.getByText("Pages 41–80 of 371")).toBeVisible();
    expect(within(table).getAllByRole("row")).toHaveLength(41);
    expect(
      screen.getByRole("link", {
        name: "Compare page 41 of Original teacher guide.pdf",
      }),
    ).toHaveAttribute(
      "href",
      `/admin/materials/${documentId}/review-text?page_number=41&benchmark_id=${benchmarkId}`,
    );
    fireEvent.click(screen.getByRole("button", { name: "Previous 40 pages" }));
    expect(screen.getByText("Pages 1–40 of 371")).toBeVisible();
    expect(requests).toHaveLength(2);
  });

  it("can return from an empty next catalog page without losing access to earlier review sets", async () => {
    const sets = Array.from({ length: 40 }, (_, index) =>
      benchmark({
        id: `00000000-0000-0000-0000-${String(index + 1).padStart(12, "0")}`,
        name: `Review set ${index + 1}`,
      }),
    );
    fixtureApi(sets, (request) => {
      const url = new URL(request.url);
      if (url.pathname === basePath && url.searchParams.get("offset") === "40")
        return Response.json([]);
    });
    render(<SourceBenchmarkReview role="admin" />);
    await screen.findByRole("table", { name: "Selected source pages" });
    fireEvent.click(screen.getByRole("button", { name: "More review sets" }));
    expect(
      await screen.findByText("No more review sets on this page."),
    ).toBeVisible();
    const previous = screen.getByRole("button", {
      name: "Previous review sets",
    });
    expect(previous).toBeEnabled();
    fireEvent.click(previous);
    await screen.findByRole("table", { name: "Selected source pages" });
    expect(screen.getByRole("combobox", { name: "Review set" })).toHaveValue(
      sets[0].id,
    );
  });

  it("opens a requested review set rather than silently selecting a different first set", async () => {
    fixtureApi([
      benchmark(),
      benchmark({ id: secondBenchmarkId, name: "Requested review set" }),
    ]);
    render(
      <SourceBenchmarkReview
        role="admin"
        initialBenchmarkId={secondBenchmarkId}
      />,
    );
    await screen.findByRole("table", { name: "Selected source pages" });
    expect(screen.getByRole("combobox", { name: "Review set" })).toHaveValue(
      secondBenchmarkId,
    );
    expect(
      screen.getByRole("link", {
        name: "Compare page 1 of Original teacher guide.pdf",
      }),
    ).toHaveAttribute(
      "href",
      `/admin/materials/${documentId}/review-text?page_number=1&benchmark_id=${secondBenchmarkId}`,
    );
  });

  it("ignores late responses for a previously selected review set", async () => {
    let release!: (response: Response) => void;
    const delayed = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const sets = [
      benchmark(),
      benchmark({
        id: secondBenchmarkId,
        name: "Second review set",
        adjudicated_pages: 3,
        pending_pages: 37,
      }),
    ];
    const requests = fixtureApi(sets, (request) => {
      if (new URL(request.url).pathname === `${basePath}/${benchmarkId}`)
        return delayed;
    });
    render(<SourceBenchmarkReview role="admin" />);
    const selector = await screen.findByRole("combobox", {
      name: "Review set",
    });
    await waitFor(() =>
      expect(
        requests.some((request) => request.url.endsWith(benchmarkId)),
      ).toBe(true),
    );
    fireEvent.change(selector, { target: { value: secondBenchmarkId } });
    await screen.findByRole("table", { name: "Selected source pages" });
    summary("Human-confirmed reference pages", 3);
    await act(async () => release(Response.json(sets[0])));
    expect(selector).toHaveValue(secondBenchmarkId);
    summary("Human-confirmed reference pages", 3);
  });

  it.each([false, true])(
    "keeps the current review set when it is selected again (pending: %s)",
    async (pending) => {
      let release!: (response: Response) => void;
      const delayed = new Promise<Response>((resolve) => {
        release = resolve;
      });
      const requests = fixtureApi([benchmark()], (request) =>
        pending && new URL(request.url).pathname.endsWith(`/${benchmarkId}`)
          ? delayed
          : undefined,
      );
      render(<SourceBenchmarkReview role="admin" />);
      const select = await screen.findByRole("combobox", {
        name: "Review set",
      });
      await waitFor(() =>
        expect(
          requests.some((request) => request.url.endsWith(benchmarkId)),
        ).toBe(true),
      );
      if (!pending)
        await screen.findByRole("table", { name: "Selected source pages" });
      fireEvent.change(select, { target: { value: benchmarkId } });
      if (pending) await act(async () => release(Response.json(benchmark())));
      await screen.findByRole("table", { name: "Selected source pages" });
      expect(
        requests.filter((request) => request.url.endsWith(benchmarkId)),
      ).toHaveLength(1);
      expect(requests.at(-1)?.signal.aborted).toBe(false);
    },
  );

  it("refreshes actual reference counts without mutating any selected source", async () => {
    let current = benchmark();
    const requests = fixtureApi([current], (request) => {
      if (new URL(request.url).pathname === `${basePath}/${benchmarkId}`)
        return Response.json(current);
    });
    render(<SourceBenchmarkReview role="admin" />);
    await screen.findByRole("table", { name: "Selected source pages" });
    current = benchmark({
      adjudicated_pages: 1,
      pending_pages: 39,
      accuracy_status: "references_available",
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh review counts" }),
    );
    await waitFor(() => summary("Human-confirmed reference pages", 1));
    expect(requests.every((request) => request.method === "GET")).toBe(true);
  });

  it("supports read-only reviewers and provides accessible queue controls", async () => {
    fixtureApi();
    const view = render(<SourceBenchmarkReview role="reviewer" />);
    await screen.findByRole("table", { name: "Selected source pages" });
    expect(screen.getByText("Reviewer access is read-only.")).toBeVisible();
    await act(async () => {
      expect(
        await axe.run(view.container, {
          rules: { "color-contrast": { enabled: false } },
        }),
      ).toMatchObject({ violations: [] });
    });
  });

  it("shows an empty selection without manufacturing a completed benchmark", async () => {
    fixtureApi([]);
    render(<SourceBenchmarkReview role="admin" />);
    expect(
      await screen.findByText("No page review sets are available yet."),
    ).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/ready|accuracy passed|100%/i),
    ).not.toBeInTheDocument();
  });

  it.each([401, 403, 503])(
    "handles a private API failure (%s) and a manual retry",
    async (status) => {
      let failed = true;
      fixtureApi(undefined, () =>
        failed
          ? Response.json({ detail: { code: "private_failure" } }, { status })
          : undefined,
      );
      render(<SourceBenchmarkReview role="admin" />);
      expect(await screen.findByRole("alert")).toBeVisible();
      expect(screen.queryByText("private_failure")).not.toBeInTheDocument();
      failed = false;
      fireEvent.click(
        screen.getByRole("button", { name: "Try loading again" }),
      );
      await screen.findByRole("table", { name: "Selected source pages" });
    },
  );

  it("provides a small entry link from Materials without changing its workflows", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json([])),
    );
    render(<MaterialsLibrary role="admin" />);
    expect(
      await screen.findByRole("link", { name: "Review selected source pages" }),
    ).toHaveAttribute("href", "/admin/materials/benchmark-review");
  });
});
