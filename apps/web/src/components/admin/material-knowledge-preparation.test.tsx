import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MaterialKnowledgePreparation } from "./material-knowledge-preparation";

const documentId = "00000000-0000-0000-0000-000000005201";
const otherDocumentId = "00000000-0000-0000-0000-000000005202";

function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    document_id: documentId,
    requested: true,
    source_ready: true,
    scope_ready: true,
    status: "preparing",
    verified_pages: 2,
    prepared_pages: 1,
    unit_count: 4,
    projection_count: 4,
    pending_pages: 1,
    failed_pages: 0,
    ...overrides,
  };
}

function respond(body: unknown = snapshot(), status = 200) {
  const fetchMock = vi.fn<typeof fetch>(async () =>
    Response.json(body, { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("MaterialKnowledgePreparation", () => {
  it("reads one material's progress without requesting work or exposing implementation identifiers", async () => {
    const fetchMock = respond();
    render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    expect(await screen.findByText("Preparing checked pages…")).toBeVisible();
    expect(screen.getByText("1 of 2 checked pages prepared")).toBeVisible();
    expect(screen.getByText("Content sections prepared: 4")).toBeVisible();
    expect(
      screen.queryByText(/Ready for AI|embedding|projection|vector|queue/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(documentId)).not.toBeInTheDocument();
    const request = fetchMock.mock.calls[0][0] as Request;
    expect(request.method).toBe("GET");
    expect(new URL(request.url).pathname).toBe(
      `/api/v1/admin/materials/${documentId}/knowledge-preparation`,
    );
    expect(request.cache).toBe("no-store");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps source review and curriculum admission distinct", async () => {
    respond(
      snapshot({
        status: "waiting",
        source_ready: false,
        scope_ready: false,
        prepared_pages: 0,
        unit_count: 0,
        projection_count: 0,
      }),
    );
    render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    expect(
      await screen.findByText("Waiting for material review"),
    ).toBeVisible();
    expect(
      screen.getByText("Finish checking the original pages."),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Confirm the material details and curriculum assignment.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("link", { name: "Review pages" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open Materials" }),
    ).toHaveAttribute("href", "/admin/materials");
  });

  it("does not relabel preparation as curriculum approval or AI readiness", async () => {
    respond(
      snapshot({ status: "prepared", prepared_pages: 2, pending_pages: 0 }),
    );
    render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    expect(await screen.findByText("Checked content prepared")).toBeVisible();
    expect(
      screen.getByText(
        "Curriculum mappings and final AI readiness still need their own checks.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /approve|generate|index/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/^Ready for AI$/)).not.toBeInTheDocument();
  });

  it("does not invent question-generation content for decorative pages", async () => {
    respond(
      snapshot({
        status: "prepared",
        prepared_pages: 2,
        pending_pages: 0,
        projection_count: 0,
      }),
    );
    render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    expect(
      await screen.findByText(
        "No content from these pages is available for question generation.",
      ),
    ).toBeVisible();
  });

  it("leaves historical material unenrolled without a mutation", async () => {
    const fetchMock = respond(
      snapshot({
        status: "not_requested",
        requested: false,
        prepared_pages: 0,
        unit_count: 0,
        projection_count: 0,
        pending_pages: 0,
      }),
    );
    render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    expect(
      await screen.findByText("Automatic preparation not started"),
    ).toBeVisible();
    expect(
      screen.getByText(
        /New page-review decisions request preparation automatically/,
      ),
    ).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("button", { name: /start|prepare|enroll|index/i }),
    ).not.toBeInTheDocument();
  });

  it("distinguishes unenrolled historical content from an automatic preparation run", async () => {
    respond(
      snapshot({
        status: "not_requested",
        requested: false,
        prepared_pages: 2,
        pending_pages: 0,
      }),
    );
    render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    expect(
      await screen.findByText("Automatic preparation not started"),
    ).toBeVisible();
    expect(screen.getByText("2 of 2 checked pages prepared")).toBeVisible();
  });

  it("preserves source decisions when preparation fails and refreshes only the read", async () => {
    const fetchMock = respond(
      snapshot({ status: "needs_attention", failed_pages: 1 }),
    );
    render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    expect(
      await screen.findByText("Preparation needs attention"),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Source reviews have been kept. Ask an administrator to inspect the preparation failure.",
      ),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    for (const call of fetchMock.mock.calls)
      expect((call[0] as Request).method).toBe("GET");
  });

  it("does not offer an active review action for a removed material", async () => {
    respond(
      snapshot({ status: "removed", source_ready: false, scope_ready: false }),
    );
    render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    expect(await screen.findByText("Removed from AI use")).toBeVisible();
    expect(
      screen.queryByRole("link", { name: "Review pages" }),
    ).not.toBeInTheDocument();
  });

  it("uses the supplied Sinhala presentation choice without rewriting state or persisting a preference", async () => {
    const persist = vi.spyOn(Storage.prototype, "setItem");
    const fetchMock = respond(
      snapshot({ status: "prepared", prepared_pages: 2, pending_pages: 0 }),
    );
    const view = render(
      <MaterialKnowledgePreparation documentId={documentId} language="si" />,
    );
    expect(
      await screen.findByRole("heading", { name: "අන්තර්ගතය සකස් කිරීම" }),
    ).toBeVisible();
    expect(
      await screen.findByText("පරීක්ෂා කළ අන්තර්ගතය සකස් කර ඇත"),
    ).toBeVisible();
    view.rerender(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    expect(screen.getByText("Checked content prepared")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(persist).not.toHaveBeenCalled();
    persist.mockRestore();
  });

  it.each([
    [401, "Your session has expired. Sign in again to check preparation."],
    [403, "Your account cannot view this material's preparation."],
    [404, "Preparation details are not available for this material."],
    [503, "Preparation status could not be loaded. Try refreshing it."],
  ] as const)(
    "handles HTTP %s without exposing raw errors",
    async (status, message) => {
      respond({ detail: { code: "private-internal-error" } }, status);
      render(
        <MaterialKnowledgePreparation documentId={documentId} language="en" />,
      );
      expect(await screen.findByRole("alert")).toHaveTextContent(message);
      expect(
        screen.queryByText("private-internal-error"),
      ).not.toBeInTheDocument();
    },
  );

  it.each([
    { document_id: otherDocumentId },
    { status: "unknown" },
    { unit_count: true },
    { prepared_pages: 3 },
    { status: "prepared", source_ready: false },
    { status: "prepared", prepared_pages: 1, pending_pages: 0 },
    { status: "prepared", prepared_pages: 2, pending_pages: 1 },
    {
      status: "prepared",
      prepared_pages: 2,
      pending_pages: 0,
      failed_pages: 1,
    },
  ])("does not display inconsistent server state: %j", async (overrides) => {
    respond(snapshot(overrides));
    render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Preparation status could not be loaded.",
    );
    expect(
      screen.queryByText("Checked content prepared"),
    ).not.toBeInTheDocument();
  });

  it("recovers from a network error using a read-only refresh", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error("private connection details"))
      .mockResolvedValue(
        Response.json(
          snapshot({ status: "prepared", prepared_pages: 2, pending_pages: 0 }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Preparation status could not be loaded.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    expect(await screen.findByText("Checked content prepared")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("ignores an earlier material's late response", async () => {
    let finishFirst: (response: Response) => void = () => undefined;
    const first = new Promise<Response>((resolve) => {
      finishFirst = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(first)
      .mockImplementation(async () =>
        Response.json(
          snapshot({
            document_id: otherDocumentId,
            status: "prepared",
            prepared_pages: 2,
            pending_pages: 0,
          }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const view = render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    view.rerender(
      <MaterialKnowledgePreparation
        documentId={otherDocumentId}
        language="en"
      />,
    );
    expect(await screen.findByText("Checked content prepared")).toBeVisible();
    await act(async () => {
      finishFirst(
        Response.json(snapshot({ status: "needs_attention", failed_pages: 1 })),
      );
    });
    expect(
      screen.queryByText("Preparation needs attention"),
    ).not.toBeInTheDocument();
    expect((fetchMock.mock.calls[0][0] as Request).signal.aborted).toBe(true);
  });

  it("never overlaps a slow poll and cancels active polling on unmount", async () => {
    vi.useFakeTimers();
    let finish: (response: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementationOnce(async () => Response.json(snapshot()))
      .mockReturnValueOnce(pending);
    vi.stubGlobal("fetch", fetchMock);
    const view = render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    view.unmount();
    expect((fetchMock.mock.calls[1][0] as Request).signal.aborted).toBe(true);
    await act(async () => {
      finish(Response.json(snapshot()));
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("polls only active preparation and stops after completion or unmount", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(async () => Response.json(snapshot()))
      .mockImplementation(async () =>
        Response.json(
          snapshot({ status: "prepared", prepared_pages: 2, pending_pages: 0 }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const view = render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Checked content prepared")).toBeVisible();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    view.unmount();
    expect((fetchMock.mock.calls[1][0] as Request).signal.aborted).toBe(true);
  });

  it("has no automated accessibility violations", async () => {
    respond(
      snapshot({ status: "prepared", prepared_pages: 2, pending_pages: 0 }),
    );
    const { container } = render(
      <MaterialKnowledgePreparation documentId={documentId} language="en" />,
    );
    await screen.findByText("Checked content prepared");
    expect((await axe.run(container)).violations).toEqual([]);
  });
});
