import type { components } from "@exam-guru/api-client";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import axe, { type AxeResults } from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PublishedPapersLibrary } from "./published-papers-library";

type Exam = components["schemas"]["ExamConfigurationResponse"];
type Medium = components["schemas"]["MediumResponse"];
type Subject = components["schemas"]["SubjectResponse"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type Paper = components["schemas"]["PaperSummaryResponse"];

const exam = {
  active: true,
  code: "SCHOOL-G7",
  created_at: "2026-08-20T10:00:00Z",
  grade: 7,
  id: "00000000-0000-0000-0000-000000001001",
  name: "School Grade 7",
  updated_at: "2026-08-20T10:00:00Z",
} satisfies Exam;

const medium = {
  active: true,
  code: "en",
  created_at: "2026-08-20T10:00:00Z",
  id: "00000000-0000-0000-0000-000000001002",
  name: "English",
  updated_at: "2026-08-20T10:00:00Z",
} satisfies Medium;

const subject = {
  active: true,
  code: "MATHEMATICS",
  created_at: "2026-08-20T10:00:00Z",
  id: "00000000-0000-0000-0000-000000001003",
  name: "Maths",
  updated_at: "2026-08-20T10:00:00Z",
} satisfies Subject;

const curriculum = {
  active: true,
  code: "G7-MATH-V1",
  created_at: "2026-08-20T10:00:00Z",
  exam_configuration_id: exam.id,
  id: "00000000-0000-0000-0000-000000001004",
  medium_id: medium.id,
  subject_id: subject.id,
  title: "Grade 7 Mathematics",
  updated_at: "2026-08-20T10:00:00Z",
} satisfies Curriculum;

const publishedPaper = {
  blueprint_id: "grade7-maths-lessons-1-3",
  blueprint_version: "1.0.0",
  created_at: "2026-08-25T10:00:00Z",
  created_by: "00000000-0000-0000-0000-000000001005",
  current_version: 2,
  curriculum_version_id: curriculum.id,
  id: "00000000-0000-0000-0000-000000001006",
  latest_publication_hash: `sha256:${"b".repeat(64)}`,
  paper_blueprint_id: "00000000-0000-0000-0000-000000001007",
  state: "published",
  title: "Grade 7 Maths Lessons 1–3 practice paper",
  updated_at: "2026-08-25T11:00:00Z",
  updated_by: "00000000-0000-0000-0000-000000001008",
} satisfies Paper;

const draftPaper = {
  ...publishedPaper,
  current_version: 1,
  id: "00000000-0000-0000-0000-000000001009",
  latest_publication_hash: null,
  state: "draft",
  title: "Grade 7 Maths draft ready to publish",
} satisfies Paper;

function asRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(input, init);
}

type FixtureConfiguration = {
  pages?: Record<number, Paper[]>;
  papers?: Paper[];
};

function fixtureApi({ pages, papers = [publishedPaper] }: FixtureConfiguration = {}) {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    requests.push(request.clone());
    const url = new URL(request.url);
    const path = url.pathname;
    if (request.method === "GET" && path.endsWith("/exam-configurations")) {
      return Response.json([exam]);
    }
    if (request.method === "GET" && path.endsWith("/media")) return Response.json([medium]);
    if (request.method === "GET" && path.endsWith("/subjects")) return Response.json([subject]);
    if (request.method === "GET" && path.endsWith("/curriculum-versions")) {
      return Response.json([curriculum]);
    }
    if (
      request.method === "GET" &&
      path.endsWith(`/curricula/${curriculum.id}/papers`)
    ) {
      const offset = Number(url.searchParams.get("offset") ?? 0);
      return Response.json(pages ? (pages[offset] ?? []) : papers);
    }
    return Response.json({ detail: { code: "unexpected_request", path } }, { status: 500 });
  });
  return { fetchMock, requests };
}

async function renderLibrary(configuration: FixtureConfiguration = {}) {
  const fixture = fixtureApi(configuration);
  vi.stubGlobal("fetch", fixture.fetchMock);
  const view = render(<PublishedPapersLibrary role="reviewer" />);
  await screen.findByRole("heading", { level: 1, name: "Published Papers" });
  return { ...fixture, ...view };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("PublishedPapersLibrary", () => {
  it("lists authoritative teacher-readable paper fields through the generated paper API", async () => {
    const { requests } = await renderLibrary();
    const library = await screen.findByRole("region", { name: "Published paper library" });
    const paper = within(library).getByRole("article", { name: publishedPaper.title });

    expect(paper).toHaveTextContent("Grade 7 · Maths · English");
    expect(paper).toHaveTextContent(publishedPaper.title);
    expect(paper).toHaveTextContent("Published");
    expect(paper).toHaveTextContent("Version 2");
    expect(within(paper).getByText(publishedPaper.id)).not.toBeVisible();
    expect(paper).not.toHaveTextContent("Unknown scope");
    expect(screen.getByRole("link", { name: "Open Advanced Paper Studio" })).toHaveAttribute(
      "href",
      "/admin/papers",
    );

    const paperRequest = requests.find((request) =>
      new URL(request.url).pathname.endsWith(`/curricula/${curriculum.id}/papers`),
    );
    expect(new URL(paperRequest!.url).searchParams.has("state")).toBe(false);
  });

  it("shows an authoritative draft state so a newly created draft is findable", async () => {
    await renderLibrary({ papers: [draftPaper] });
    const paper = await screen.findByRole("article", { name: draftPaper.title });
    expect(paper).toHaveTextContent("Draft");
    expect(paper).toHaveTextContent("Version 1");
    expect(within(paper).queryByText("Published")).not.toBeInTheDocument();
  });

  it("uses bounded pagination so papers after the first page remain findable", async () => {
    const firstPage = Array.from({ length: 100 }, (_, index) => ({
      ...publishedPaper,
      id: `00000000-0000-0000-0000-${String(index + 100).padStart(12, "0")}`,
      title: `Published paper ${index + 1}`,
    })) satisfies Paper[];
    const { requests } = await renderLibrary({
      pages: { 0: firstPage, 100: [draftPaper], 200: [] },
    });

    await screen.findByRole("article", { name: "Published paper 1" });
    expect(screen.queryByRole("article", { name: draftPaper.title })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByRole("article", { name: draftPaper.title })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeEnabled();
    const offsets = requests
      .filter((request) =>
        new URL(request.url).pathname.endsWith(`/curricula/${curriculum.id}/papers`),
      )
      .map((request) => new URL(request.url).searchParams.get("offset"));
    expect(offsets).toEqual(["0", "100"]);
  });

  it("keeps paper and curriculum identifiers collapsed as technical details", async () => {
    await renderLibrary();
    const paper = await screen.findByRole("article", { name: publishedPaper.title });
    const summary = within(paper).getByText("Technical details", { selector: "summary" });
    const details = summary.closest("details");
    if (!details) throw new Error("Published technical data must use a details disclosure");

    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText(publishedPaper.id)).not.toBeVisible();
    fireEvent.click(summary);
    expect(within(details).getByText(publishedPaper.id)).toBeVisible();
    expect(within(details).getByText(curriculum.id)).toBeVisible();
    expect(within(details).getByText(publishedPaper.paper_blueprint_id)).toBeVisible();
  });

  it("shows a useful empty state", async () => {
    await renderLibrary({ papers: [] });
    expect(await screen.findByText("No published papers yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review papers" })).toHaveAttribute(
      "href",
      "/admin/review-approve",
    );
  });

  it("has no automated accessibility violations", async () => {
    const { container } = await renderLibrary();
    await screen.findByRole("article", { name: publishedPaper.title });
    let results: AxeResults | undefined;
    await act(async () => {
      results = await axe.run(container, {
        rules: { "color-contrast": { enabled: false } },
      });
    });
    expect(results?.violations).toEqual([]);
  });
});
