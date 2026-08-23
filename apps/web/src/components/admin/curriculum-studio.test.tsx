import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CurriculumStudio } from "./curriculum-studio";

const emptyLists = () => {
  const exams: object[] = [];
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init);
    const url = request.url;
    if (request.method === "POST" && url.endsWith("/exam-configurations")) {
      const created = {
        active: true,
        code: "G5S-2026",
        created_at: "2026-08-23T00:00:00Z",
        grade: 5,
        id: "00000000-0000-0000-0000-000000000001",
        name: "Grade 5 Scholarship 2026",
        updated_at: "2026-08-23T00:00:00Z",
      };
      exams.push(created);
      return Response.json(created, { status: 201 });
    }
    if (request.method === "GET" && url.endsWith("/exam-configurations")) {
      return Response.json(exams);
    }
    return Response.json([]);
  });
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CurriculumStudio", () => {
  it("loads empty configuration state and creates an exam as admin", async () => {
    const fetchMock = emptyLists();
    vi.stubGlobal("fetch", fetchMock);
    render(<CurriculumStudio role="admin" />);

    expect(
      await screen.findByRole("heading", { name: "Configuration & taxonomy" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("No exam configurations yet.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Exam code"), { target: { value: "G5S-2026" } });
    fireEvent.change(screen.getByLabelText("Exam name"), {
      target: { value: "Grade 5 Scholarship 2026" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create exam" }));

    expect(
      await screen.findByRole("button", { name: "Grade 5 Scholarship 2026" }),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) => {
        const request = input instanceof Request ? input : new Request(input);
        return request.method === "POST" && request.url.endsWith("/exam-configurations");
      }),
    ).toBe(true);
  });

  it("renders reviewer mode without write controls", async () => {
    vi.stubGlobal("fetch", emptyLists());
    render(<CurriculumStudio role="reviewer" />);

    expect(await screen.findByText("Reviewer access is read-only.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create exam" })).not.toBeInTheDocument();
  });

  it("has no automated accessibility violations", async () => {
    vi.stubGlobal("fetch", emptyLists());
    const { container } = render(<CurriculumStudio role="admin" />);
    await waitFor(() => expect(screen.getByText("No exam configurations yet.")).toBeInTheDocument());

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});
