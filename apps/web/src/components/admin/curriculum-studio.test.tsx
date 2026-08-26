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
    fireEvent.change(screen.getByLabelText("Grade"), { target: { value: "5" } });
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

  it("creates a subject and sends the selected subject when creating a curriculum", async () => {
    const examId = "00000000-0000-0000-0000-000000000101";
    const mediumId = "00000000-0000-0000-0000-000000000102";
    const subjectId = "00000000-0000-0000-0000-000000000103";
    const curriculumId = "00000000-0000-0000-0000-000000000104";
    const subjects: object[] = [];
    const curricula: object[] = [];
    const requests: Request[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init);
      requests.push(request.clone());
      const path = new URL(request.url).pathname;
      if (request.method === "GET" && path.endsWith("/exam-configurations")) {
        return Response.json([
          {
            active: true,
            code: "G7",
            created_at: "2026-08-23T00:00:00Z",
            grade: 7,
            id: examId,
            name: "Grade 7",
            updated_at: "2026-08-23T00:00:00Z",
          },
        ]);
      }
      if (request.method === "GET" && path.endsWith("/media")) {
        return Response.json([
          {
            active: true,
            code: "en",
            created_at: "2026-08-23T00:00:00Z",
            id: mediumId,
            name: "English",
            updated_at: "2026-08-23T00:00:00Z",
          },
        ]);
      }
      if (request.method === "GET" && path.endsWith("/subjects")) return Response.json(subjects);
      if (request.method === "GET" && path.endsWith("/curriculum-versions")) {
        return Response.json(curricula);
      }
      if (request.method === "POST" && path.endsWith("/subjects")) {
        const body = (await request.json()) as { code: string; name: string };
        const created = {
          active: true,
          ...body,
          created_at: "2026-08-23T00:00:00Z",
          id: subjectId,
          updated_at: "2026-08-23T00:00:00Z",
        };
        subjects.push(created);
        return Response.json(created, { status: 201 });
      }
      if (request.method === "POST" && path.endsWith("/curriculum-versions")) {
        const body = (await request.json()) as {
          code: string;
          exam_configuration_id: string;
          medium_id: string;
          subject_id: string;
          title: string;
        };
        const created = {
          active: true,
          ...body,
          created_at: "2026-08-23T00:00:00Z",
          id: curriculumId,
          updated_at: "2026-08-23T00:00:00Z",
        };
        curricula.push(created);
        return Response.json(created, { status: 201 });
      }
      return Response.json([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<CurriculumStudio role="admin" />);
    await screen.findByText("No subjects yet.");

    fireEvent.change(screen.getByLabelText("Subject code"), { target: { value: "MATHS" } });
    fireEvent.change(screen.getByLabelText("Subject name"), { target: { value: "Mathematics" } });
    fireEvent.click(screen.getByRole("button", { name: "Create subject" }));

    expect(await screen.findByRole("button", { name: "Mathematics" })).toBeVisible();
    expect(screen.getByLabelText("Curriculum subject")).toHaveValue(subjectId);
    fireEvent.change(screen.getByLabelText("Curriculum code"), {
      target: { value: "G7-MATHS-2026" },
    });
    fireEvent.change(screen.getByLabelText("Curriculum title"), {
      target: { value: "Grade 7 Mathematics 2026" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create curriculum" }));

    expect(
      await screen.findByRole("button", { name: "Grade 7 Mathematics 2026" }),
    ).toBeVisible();
    const curriculumRequest = requests.find(
      (request) =>
        request.method === "POST" && request.url.endsWith("/curriculum-versions"),
    );
    expect(await curriculumRequest?.json()).toMatchObject({
      exam_configuration_id: examId,
      medium_id: mediumId,
      subject_id: subjectId,
    });
    expect(screen.getByText("Mathematics · Grade 7 · English")).toBeVisible();
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
