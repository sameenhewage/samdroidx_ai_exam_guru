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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { reviewLanguageKey } from "@/lib/review-language";

import { MaterialCurriculumReview } from "./material-curriculum-review";

type Listing = components["schemas"]["MaterialKnowledgeUnitsResponse"];
type MaterialUnitSummary =
  components["schemas"]["MaterialKnowledgeUnitSummary"];
type Workspace = components["schemas"]["MaterialKnowledgeUnitWorkspace"];
type Indexing = components["schemas"]["MaterialKnowledgeIndexingStatus"];
type KnowledgeUnit = components["schemas"]["KnowledgeUnit"];
type Review = components["schemas"]["KnowledgeUnitReview"];
type ReviewRequest = components["schemas"]["KnowledgeReviewRequest"];
type Material = components["schemas"]["MaterialListItemResponse"];
type Unit = components["schemas"]["CurriculumUnitResponse"];
type Lesson = components["schemas"]["CurriculumLessonResponse"];
type Node = components["schemas"]["TaxonomyNodeResponse"];

const id = (value: number) =>
  `00000000-0000-0000-0000-${String(value).padStart(12, "0")}`;
const documentId = id(5301);
const curriculumId = id(5302);
const unitId = id(5303);
const secondId = id(5304);
const moduleId = id(5310);
const lessonId = id(5311);
const competencyId = id(5320);
const skillId = id(5321);
const subSkillId = id(5322);
const conceptId = id(5323);
const otherCompetency = id(5324);
const basePath = `/api/v1/admin/materials/${documentId}/knowledge-units`;
const sourceText = "ශ්‍රී ලංකාව a\u0301 <script>untrusted()</script>";

function indexing(overrides: Partial<Indexing> = {}): Indexing {
  return {
    intent_id: null,
    version: null,
    status: "not_requested",
    ready: false,
    retry_allowed: false,
    ...overrides,
  };
}
function unit(identifier = unitId, page = 1, sequence = 0): KnowledgeUnit {
  return {
    id: identifier,
    schema_version: "knowledge-unit.v1",
    derivation_version: "page-region-components.v1",
    trusted_page_id: id(5330),
    trusted_revision: 1,
    trusted_fingerprint: "a".repeat(64),
    candidate_id: id(5331),
    source: {
      document_id: documentId,
      page_number: page,
      source_sha256: "b".repeat(64),
      image_sha256: "c".repeat(64),
    },
    scope: {
      schema_version: "knowledge-scope.v2",
      document_id: documentId,
      source_sha256: "b".repeat(64),
      curriculum_version_id: curriculumId,
      curriculum_unit_id: null,
      lesson_id: null,
      grade: 7,
      subject_id: id(5332),
      medium_id: id(5333),
      material_type: "teacher_guide",
      year: null,
      paper_code: null,
      catalogue_decision_id: id(5334),
      catalogue_version: 1,
      catalogue_scope_fingerprint: "d".repeat(64),
      metadata_scope_version: 1,
    },
    sequence,
    region_ids: [id(5335)],
    observation: {
      language: "en",
      relationships: [],
      regions: [
        {
          key: "exercise",
          kind: "grid",
          reading_order: 0,
          parent_key: null,
          bounds: null,
          polygon: [],
          exact_text: sourceText,
          equations: ["6 × 2 = 8"],
          visual_facts: [],
          table: {
            rows: 1,
            columns: 2,
            cells: [
              {
                row: 0,
                column: 0,
                row_span: 1,
                column_span: 1,
                state: "visible",
                exact_text: "6 × 2 =",
              },
              {
                row: 0,
                column: 1,
                row_span: 1,
                column_span: 1,
                state: "blank",
                exact_text: "",
              },
            ],
          },
        },
      ],
    },
    education: {
      claims: [
        {
          key: "accepted",
          kind: "skill",
          description: "Practise counting in pairs.",
          region_keys: ["exercise"],
        },
      ],
    },
    resolved_uncertainties: [],
  };
}
function workspace(identifier = unitId, page = 1, sequence = 0): Workspace {
  return {
    workspace: {
      unit: unit(identifier, page, sequence),
      review: null,
      source_current: true,
      eligible: false,
    },
    indexing: indexing(),
  };
}
function reviewed(version = 1): Review {
  return {
    id: id(5340 + version),
    unit_id: unitId,
    unit_fingerprint: "e".repeat(64),
    curriculum_version_id: curriculumId,
    schema_version: "knowledge-unit-review.v1",
    actor_id: id(5349),
    version,
    state: "reviewed",
    confirmed_mapping: true,
    competency_id: competencyId,
    curriculum_unit_id: null,
    lesson_id: null,
    skill_id: null,
    sub_skill_id: null,
    learning_concept_id: null,
    reason: "Earlier explicit review.",
  };
}
function node(
  identifier: string,
  title: string,
  level: Node["level"],
  parent: string | null,
): Node {
  return {
    id: identifier,
    title,
    code: "INTERNAL-CODE",
    level,
    parent_id: parent,
    curriculum_version_id: curriculumId,
    active: true,
    review_state: "reviewed",
  };
}

let current: Workspace;
let listing: Listing;
let material: Material;
let modules: Unit[];
let lessons: Lesson[];
let nodes: Node[];
let requests: Request[];
let writes: { path: string; body: Record<string, unknown> }[];
let postStatus: number;
let getStatus: number;
let intercept:
  | ((request: Request) => Promise<Response | undefined>)
  | undefined;
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;

beforeEach(() => {
  window.localStorage.clear();
  current = workspace();
  listing = {
    document_id: documentId,
    curriculum_version_id: curriculumId,
    source_current: true,
    total: 2,
    limit: 20,
    offset: 0,
    items: [
      {
        unit_id: unitId,
        page_number: 1,
        sequence: 0,
        source_curriculum_unit_id: null,
        source_lesson_id: null,
        source_unit_title: null,
        source_lesson_title: null,
        review: null,
        has_projection: true,
        indexing: indexing(),
      },
      {
        unit_id: secondId,
        page_number: 2,
        sequence: 1,
        source_curriculum_unit_id: null,
        source_lesson_id: null,
        source_unit_title: null,
        source_lesson_title: null,
        review: null,
        has_projection: true,
        indexing: indexing(),
      },
    ],
  };
  material = {
    id: documentId,
    title: "Synthetic teacher guide.pdf",
    grade: 7,
    medium: "English",
    subject: "Mathematics",
    subject_id: id(5332),
    curriculum: "Reviewed curriculum",
    unit: null,
    lesson: null,
    material_type: "teacher_guide",
    year: null,
    page_count: 2,
    status: "needs_review",
    metadata_review_required: false,
    metadata_scope_version: 1,
    uploaded_at: "2026-09-13T00:00:00Z",
  };
  modules = [
    {
      id: moduleId,
      curriculum_version_id: curriculumId,
      title: "Numbers",
      code: "INTERNAL-MODULE",
      ordinal: 1,
      active: true,
      created_at: "",
      updated_at: "",
    },
    {
      id: id(5312),
      curriculum_version_id: curriculumId,
      title: "Shapes",
      code: "INTERNAL-MODULE-2",
      ordinal: 2,
      active: true,
      created_at: "",
      updated_at: "",
    },
  ];
  lessons = [
    {
      id: lessonId,
      curriculum_version_id: curriculumId,
      unit_id: moduleId,
      title: "Counting in pairs",
      code: "INTERNAL-LESSON",
      ordinal: 1,
      active: true,
      taxonomy_node_ids: [skillId],
      created_at: "",
      updated_at: "",
    },
  ];
  nodes = [
    node(competencyId, "Number sense", "competency", null),
    node(skillId, "Skip counting", "skill", competencyId),
    node(subSkillId, "Counting by twos", "sub_skill", skillId),
    node(conceptId, "Equal groups", "learning_concept", subSkillId),
    node(otherCompetency, "Spatial reasoning", "competency", null),
  ];
  requests = [];
  writes = [];
  postStatus = getStatus = 200;
  intercept = undefined;
  fetchMock = vi.fn<typeof fetch>(async (input) => {
    const request = input as Request;
    requests.push(request);
    const overridden = await intercept?.(request);
    if (overridden) return overridden;
    const url = new URL(request.url);
    const path = url.pathname;
    if (request.method === "POST") {
      const body = (await request.json()) as Record<string, unknown>;
      writes.push({ path, body });
      if (postStatus !== 200)
        return Response.json(
          { detail: { code: `private_failure_${id(5399)}` } },
          { status: postStatus },
        );
      if (path.endsWith("curriculum-review")) {
        current = {
          ...current,
          workspace: {
            ...current.workspace,
            review: {
              ...reviewed(),
              ...body,
              version: (body.expected_version as number) + 1,
            } as Review,
            eligible: true,
          },
          indexing: indexing({
            intent_id: id(5350),
            version: 1,
            status: "waiting_configuration",
          }),
        };
      } else {
        current = {
          ...current,
          indexing: indexing({
            intent_id: id(5350),
            version: (body.expected_version as number) + 1,
            status: "pending",
          }),
        };
      }
      return Response.json(current);
    }
    if (getStatus !== 200)
      return Response.json(
        { detail: { code: `private_failure_${id(5399)}` } },
        { status: getStatus },
      );
    if (path === "/api/v1/admin/materials") return Response.json([material]);
    if (path === basePath)
      return Response.json({
        ...listing,
        offset: Number(url.searchParams.get("offset")),
      });
    if (path === `${basePath}/${unitId}`) return Response.json(current);
    if (path === `${basePath}/${secondId}`)
      return Response.json(workspace(secondId, 2, 1));
    if (path.endsWith("/units")) return Response.json(modules);
    if (path.endsWith("/lessons")) return Response.json(lessons);
    if (path.endsWith("/taxonomy/nodes")) return Response.json(nodes);
    return Response.json({}, { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function open(role: "admin" | "reviewer" = "admin") {
  const view = render(
    <MaterialCurriculumReview documentId={documentId} role={role} />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Page 1 · Section 1" }),
  );
  const image = await screen.findByRole("img", { name: "Original page 1" });
  await act(async () => {
    fireEvent.load(image);
  });
  await screen.findByRole("heading", { name: "Curriculum mapping" });
  return view;
}
function choose(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label, { exact: true }), {
    target: { value },
  });
}
function consent() {
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "I confirm this curriculum mapping for this section.",
    }),
  );
}
function reason(text = "This section supports the selected teaching area.") {
  choose("Reason for this mapping", text);
}
function save() {
  fireEvent.click(
    screen.getByRole("button", { name: "Save curriculum mapping" }),
  );
}
async function completeDraft() {
  await open();
  choose("Teaching area", competencyId);
  reason();
  consent();
}
function aiStatus() {
  return within(
    screen.getByRole("region", { name: "AI preparation" }),
  ).getByRole("status");
}
function unitReads() {
  return requests.filter(
    (request) =>
      new URL(request.url).pathname === `${basePath}/${unitId}` &&
      request.method === "GET",
  );
}
async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("Materials curriculum review", () => {
  it("loads only the selected document in bounded groups and does not request work", async () => {
    render(<MaterialCurriculumReview documentId={documentId} role="admin" />);
    expect(
      await screen.findByRole("button", { name: "Page 1 · Section 1" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Page 2 · Section 2" }),
    ).toBeVisible();
    const listRequest = requests.find(
      (request) => new URL(request.url).pathname === basePath,
    )!;
    expect(new URL(listRequest.url).searchParams.toString()).toBe(
      "limit=20&offset=0",
    );
    expect(listRequest.cache).toBe("no-store");
    const metadata = requests.find(
      (request) => new URL(request.url).pathname === "/api/v1/admin/materials",
    )!;
    expect(new URL(metadata.url).searchParams.get("document_id")).toBe(
      documentId,
    );
    expect(new URL(metadata.url).searchParams.get("limit")).toBe("1");
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(requests.every((request) => request.method === "GET")).toBe(true);
  });

  it("does not confuse empty or unprepared content with AI readiness", async () => {
    listing.items = [];
    listing.total = 0;
    listing.source_current = false;
    listing.curriculum_version_id = null;
    render(<MaterialCurriculumReview documentId={documentId} role="admin" />);
    expect(
      await screen.findByText("No content sections are available yet."),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Review page content" }),
    ).toHaveAttribute("href", `/admin/materials/${documentId}/review-content`);
    expect(screen.queryByText(/^Ready for AI$/)).not.toBeInTheDocument();
    expect(writes).toEqual([]);
  });

  it("paginates by twenty and prompts before discarding a section draft", async () => {
    listing.total = 21;
    await completeDraft();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "Next 20 sections" }));
    expect(confirm).toHaveBeenCalled();
    expect(
      new URL(
        requests
          .filter((request) => new URL(request.url).pathname === basePath)
          .at(-1)!.url,
      ).searchParams.get("offset"),
    ).toBe("0");
    expect(screen.getByLabelText("Reason for this mapping")).toHaveValue(
      "This section supports the selected teaching area.",
    );
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Next 20 sections" }));
    await waitFor(() =>
      expect(
        requests.some(
          (request) => new URL(request.url).searchParams.get("offset") === "20",
        ),
      ).toBe(true),
    );
    expect(
      screen.queryByLabelText("Reason for this mapping"),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Previous 20 sections" }),
    );
    await waitFor(() =>
      expect(
        new URL(
          requests
            .filter((request) => new URL(request.url).pathname === basePath)
            .at(-1)!.url,
        ).searchParams.get("offset"),
      ).toBe("0"),
    );
  });

  it("renders literal accepted unit content beside exactly one app-owned page without source rewriting", async () => {
    const { container } = await open();
    expect(screen.getByText(sourceText).textContent).toBe(sourceText);
    expect(screen.getByText("6 × 2 = 8")).toBeVisible();
    expect(screen.getByText("Blank answer space")).toBeVisible();
    expect(
      screen.getByRole("region", { name: "Accepted teaching points" }),
    ).toHaveTextContent("Practise counting in pairs.");
    expect(
      screen.queryByRole("region", { name: "What it may teach" }),
    ).not.toBeInTheDocument();
    expect(container.querySelector("script, iframe, object, embed")).toBeNull();
    expect(screen.getAllByRole("img")).toHaveLength(1);
    expect(screen.getByRole("img")).toHaveAttribute(
      "src",
      `${window.location.origin}/api/v1/admin/materials/${documentId}/pages/1/image`,
    );
    expect(
      requests.some((request) =>
        new URL(request.url).pathname.endsWith("/original"),
      ),
    ).toBe(false);
    expect(screen.getByLabelText("Teaching area")).toHaveValue("");
    expect(writes).toEqual([]);
  });

  it("offers human labels only for active, reviewed, same-curriculum parent chains", async () => {
    modules.push(
      { ...modules[0], id: id(5360), title: "Inactive module", active: false },
      {
        ...modules[0],
        id: id(5361),
        title: "Other curriculum module",
        curriculum_version_id: id(5390),
      },
    );
    lessons.push({
      ...lessons[0],
      id: id(5362),
      title: "Inactive lesson",
      active: false,
    });
    nodes.push(
      {
        ...node(id(5363), "Unreviewed area", "competency", null),
        review_state: "draft",
      },
      { ...node(id(5364), "Inactive area", "competency", null), active: false },
      {
        ...node(id(5365), "Foreign area", "competency", null),
        curriculum_version_id: id(5390),
      },
      node(id(5366), "Orphan skill", "skill", id(5364)),
    );
    await open();
    choose("Unit / module", moduleId);
    choose("Lesson", lessonId);
    expect(screen.getByRole("option", { name: "Numbers" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Counting in pairs" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Lesson teaching links:/)).toHaveTextContent(
      "Skip counting",
    );
    expect(screen.getByLabelText("Teaching area")).toHaveValue("");
    for (const label of [
      "Inactive module",
      "Other curriculum module",
      "Inactive lesson",
      "Unreviewed area",
      "Inactive area",
      "Foreign area",
      "Orphan skill",
    ])
      expect(
        screen.queryByRole("option", { name: label }),
      ).not.toBeInTheDocument();
    expect(
      screen.getByRole("form", { name: "Curriculum mapping" }).textContent,
    ).not.toMatch(/00000000-|INTERNAL-|embedding|vector|job/i);
    const technical = screen.getByText("Technical details").closest("details")!;
    expect(technical).not.toHaveAttribute("open");
    expect(technical.querySelector("pre")).not.toBeVisible();
  });

  it("locks source-known unit and lesson using labels without widening or replacing them", async () => {
    current.workspace.unit.scope.curriculum_unit_id = moduleId;
    current.workspace.unit.scope.lesson_id = lessonId;
    listing.items[0].source_curriculum_unit_id = moduleId;
    listing.items[0].source_lesson_id = lessonId;
    listing.items[0].source_unit_title = "Numbers";
    listing.items[0].source_lesson_title = "Counting in pairs";
    await open();
    expect(screen.getByLabelText("Unit / module")).toHaveValue("Numbers");
    expect(screen.getByLabelText("Unit / module")).toHaveAttribute("readonly");
    expect(screen.getByLabelText("Lesson")).toHaveValue("Counting in pairs");
    expect(screen.getByLabelText("Lesson")).toHaveAttribute("readonly");
    choose("Teaching area", competencyId);
    reason();
    consent();
    save();
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].body).toMatchObject({
      curriculum_unit_id: moduleId,
      lesson_id: lessonId,
    });
  });

  it("uses the material-owned API with an explicit complete mapping, reason and fresh consent", async () => {
    await open();
    const submit = screen.getByRole("button", {
      name: "Save curriculum mapping",
    });
    expect(submit).toBeDisabled();
    choose("Unit / module", moduleId);
    choose("Lesson", lessonId);
    choose("Teaching area", competencyId);
    choose("Skill", skillId);
    choose("Sub-skill", subSkillId);
    choose("Concept", conceptId);
    reason();
    expect(submit).toBeDisabled();
    consent();
    expect(submit).toBeEnabled();
    save();
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toEqual({
      path: `${basePath}/${unitId}/curriculum-review`,
      body: {
        state: "reviewed",
        expected_version: 0,
        confirmed_mapping: true,
        reason: "This section supports the selected teaching area.",
        curriculum_unit_id: moduleId,
        lesson_id: lessonId,
        competency_id: competencyId,
        skill_id: skillId,
        sub_skill_id: subSkillId,
        learning_concept_id: conceptId,
      } satisfies ReviewRequest,
    });
    expect(await screen.findByText("Curriculum mapping saved.")).toBeVisible();
    expect(aiStatus()).toHaveTextContent("AI preparation needs setup");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/^Ready for AI$/)).not.toBeInTheDocument();
  });

  it("clears child choices and confirmation whenever a parent choice changes", async () => {
    await open();
    choose("Unit / module", moduleId);
    choose("Lesson", lessonId);
    choose("Teaching area", competencyId);
    choose("Skill", skillId);
    choose("Sub-skill", subSkillId);
    choose("Concept", conceptId);
    reason();
    consent();
    choose("Teaching area", otherCompetency);
    for (const label of ["Skill", "Sub-skill", "Concept"])
      expect(screen.getByLabelText(label, { exact: true })).toHaveValue("");
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    choose("Teaching area", competencyId);
    choose("Skill", skillId);
    choose("Sub-skill", subSkillId);
    choose("Concept", conceptId);
    consent();
    choose("Skill", "");
    expect(screen.getByLabelText("Sub-skill", { exact: true })).toHaveValue("");
    expect(screen.getByLabelText("Concept", { exact: true })).toHaveValue("");
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    choose("Unit / module", id(5312));
    expect(screen.getByLabelText("Lesson", { exact: true })).toHaveValue("");
  });

  it("lets a reviewer inspect content and existing labels but never mutate", async () => {
    current.workspace.review = reviewed();
    current.workspace.eligible = true;
    current.indexing = indexing({
      intent_id: id(5350),
      version: 1,
      status: "needs_attention",
      retry_allowed: true,
    });
    await open("reviewer");
    expect(screen.getByText("Reviewer access is read-only.")).toBeVisible();
    expect(screen.getByText(sourceText)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Save curriculum mapping" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Try AI preparation again" }),
    ).not.toBeInTheDocument();
    expect(writes).toEqual([]);
  });

  it.each([
    ["not_requested", false, "Curriculum mapping needed"],
    ["pending", false, "Waiting for AI preparation"],
    ["queued", false, "Preparing for AI…"],
    ["waiting_configuration", false, "AI preparation needs setup"],
    ["needs_attention", false, "AI preparation needs attention"],
    ["not_searchable", false, "Not available for question generation"],
    ["superseded", false, "This section has changed"],
    ["configuration_changed", false, "AI preparation settings changed"],
    ["ready", false, "AI readiness could not be confirmed"],
    ["pending", true, "AI readiness could not be confirmed"],
  ] as const)(
    "shows conservative %s readiness when ready=%s",
    async (status, ready, label) => {
      current.indexing = indexing({
        intent_id: id(5350),
        version: 1,
        status,
        ready,
      });
      await open();
      expect(aiStatus()).toHaveTextContent(label);
      expect(screen.queryByText(/^Ready for AI$/)).not.toBeInTheDocument();
    },
  );

  it("shows ready only for a consistent server-ready current eligible section", async () => {
    current.workspace.review = reviewed();
    current.workspace.eligible = true;
    current.indexing = indexing({
      intent_id: id(5350),
      version: 1,
      status: "ready",
      ready: true,
    });
    await open();
    expect(aiStatus()).toHaveTextContent(/^Ready for AI$/);
  });

  it.each([
    { intent_id: null, version: null },
    { intent_id: id(5350), version: 0 },
  ])(
    "shows a current authorized vector as ready without enrolling or polling: %j",
    async (identity) => {
      current.workspace.review = reviewed();
      current.workspace.eligible = true;
      current.indexing = indexing({
        ...identity,
        status: "ready",
        ready: true,
      });
      listing.items[0].review = current.workspace.review;
      listing.items[0].indexing = { ...current.indexing };
      const originalStatus = { ...current.indexing };
      const view = render(
        <MaterialCurriculumReview documentId={documentId} role="admin" />,
      );
      const section = await screen.findByRole("button", {
        name: "Page 1 · Section 1",
      });
      expect(within(section).getByText("Ready for AI")).toBeVisible();
      fireEvent.click(section);
      await screen.findByRole("heading", { name: "Curriculum mapping" });
      expect(aiStatus()).toHaveTextContent(/^Ready for AI$/);
      expect(
        screen.queryByRole("button", { name: "Try AI preparation again" }),
      ).not.toBeInTheDocument();
      vi.useFakeTimers();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(unitReads()).toHaveLength(1);
      expect(requests.every((request) => request.method === "GET")).toBe(true);
      expect(writes).toEqual([]);
      expect(current.indexing).toEqual(originalStatus);
      view.unmount();
    },
  );

  it("does not present a decorative zero-projection section as ready", async () => {
    listing.items[0].has_projection = false;
    current.indexing = indexing({
      intent_id: id(5350),
      version: 1,
      status: "ready",
      ready: true,
    });
    await open();
    expect(aiStatus()).toHaveTextContent(
      "Not available for question generation",
    );
    expect(screen.queryByText(/^Ready for AI$/)).not.toBeInTheDocument();
  });

  it("preserves a conflicting draft and requires successful latest loading and explicit rebase", async () => {
    await completeDraft();
    postStatus = 409;
    save();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your draft has been kept",
    );
    expect(screen.getByLabelText("Reason for this mapping")).toHaveValue(
      "This section supports the selected teaching area.",
    );
    expect(screen.getByLabelText("Teaching area")).toHaveValue(competencyId);
    expect(
      screen.getByRole("button", { name: "Save curriculum mapping" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Keep draft with latest version" }),
    ).toBeDisabled();
    getStatus = 503;
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest version" }),
    );
    await waitFor(() => expect(unitReads()).toHaveLength(2));
    expect(
      screen.getByRole("button", { name: "Keep draft with latest version" }),
    ).toBeDisabled();
    getStatus = 200;
    current.workspace.review = {
      ...reviewed(2),
      competency_id: otherCompetency,
    };
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest version" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Keep draft with latest version" }),
      ).toBeEnabled(),
    );
    expect(screen.getByLabelText("Teaching area")).toHaveValue(competencyId);
    fireEvent.click(
      screen.getByRole("button", { name: "Keep draft with latest version" }),
    );
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(
      screen.getByRole("button", { name: "Save curriculum mapping" }),
    ).toBeDisabled();
    postStatus = 200;
    consent();
    save();
    await waitFor(() => expect(writes).toHaveLength(2));
    expect(writes[1].body.expected_version).toBe(2);
  });

  it("preserves choices on source changes and refuses to rebase onto a non-current source", async () => {
    await completeDraft();
    current.workspace.source_current = false;
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest version" }),
    );
    expect(
      await screen.findByText(
        "The source or its curriculum details need review before saving.",
      ),
    ).toBeVisible();
    expect(screen.getByLabelText("Teaching area")).toHaveValue(competencyId);
    expect(screen.getByLabelText("Reason for this mapping")).toHaveValue(
      "This section supports the selected teaching area.",
    );
    expect(
      screen.getByRole("button", { name: "Save curriculum mapping" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Keep draft with latest version" }),
    ).toBeDisabled();
  });

  it("keeps drafts through image failure and requires renewed confirmation after retry", async () => {
    await completeDraft();
    fireEvent.error(screen.getByRole("img"));
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(
      screen.getByRole("button", { name: "Save curriculum mapping" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Try image again" }));
    await act(async () => {
      fireEvent.load(screen.getByRole("img"));
    });
    expect(screen.getByLabelText("Teaching area")).toHaveValue(competencyId);
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(writes).toEqual([]);
  });

  it.each([0, 7])(
    "requires a separate explicit retry using intent version %s, never another curriculum review",
    async (version) => {
      current.workspace.review = reviewed();
      current.workspace.eligible = true;
      current.indexing = indexing({
        intent_id: id(5350),
        version,
        status: "configuration_changed",
        retry_allowed: true,
      });
      await open();
      const retry = screen.getByRole("button", {
        name: "Try AI preparation again",
      });
      expect(retry).toBeDisabled();
      expect(writes).toEqual([]);
      choose(
        "Reason for trying again",
        "The administrator has checked the settings.",
      );
      expect(retry).toBeDisabled();
      fireEvent.click(
        screen.getByRole("checkbox", {
          name: "I want to try AI preparation again for this section.",
        }),
      );
      fireEvent.click(retry);
      await waitFor(() => expect(writes).toHaveLength(1));
      expect(writes[0]).toEqual({
        path: `${basePath}/${unitId}/indexing-retry`,
        body: {
          expected_version: version,
          reason: "The administrator has checked the settings.",
          confirmed_retry: true,
        },
      });
      expect(
        writes.some((write) => write.path.endsWith("curriculum-review")),
      ).toBe(false);
    },
  );

  it.each([
    { intent_id: id(5350), version: 3, retry_allowed: false },
    { intent_id: id(5350), version: null, retry_allowed: true },
    { intent_id: id(5350), version: 0, retry_allowed: false },
    { intent_id: null, version: 0, retry_allowed: true },
  ])(
    "does not retry without both server permission and a current intent version (%j)",
    async (intent) => {
      current.workspace.review = reviewed();
      current.workspace.eligible = true;
      current.indexing = indexing({
        ...intent,
        status: "needs_attention",
      });
      await open();
      expect(
        screen.queryByRole("button", { name: "Try AI preparation again" }),
      ).not.toBeInTheDocument();
      expect(writes).toEqual([]);
    },
  );

  it("prompts before changing sections or following navigation and registers unload protection", async () => {
    await completeDraft();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "Page 2 · Section 2" }));
    expect(screen.getByLabelText("Teaching area")).toHaveValue(competencyId);
    fireEvent.click(screen.getByRole("link", { name: "Back to material" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Page 2 · Section 2" }));
    expect(
      await screen.findByRole("img", { name: "Original page 2" }),
    ).toBeVisible();
  });

  it.each([401, 403, 404, 409, 422, 503])(
    "shows a safe %s error without leaking technical details",
    async (status) => {
      getStatus = status;
      render(<MaterialCurriculumReview documentId={documentId} role="admin" />);
      expect(await screen.findByRole("alert")).toBeVisible();
      expect(document.body.textContent).not.toMatch(
        /private_failure|00000000-/,
      );
      expect(writes).toEqual([]);
    },
  );

  it("uses Sinhala material hints before section selection and never persists automatic defaults", async () => {
    material.medium = "Sinhala";
    const persist = vi.spyOn(Storage.prototype, "setItem");
    render(<MaterialCurriculumReview documentId={documentId} role="admin" />);
    expect(
      await screen.findByRole("heading", {
        name: "විෂයමාලා ගැළපීම පරීක්ෂා කරන්න",
      }),
    ).toBeVisible();
    expect(persist).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "English" }));
    expect(
      screen.getByRole("heading", { name: "Review curriculum mapping" }),
    ).toBeVisible();
    expect(persist).toHaveBeenCalledWith(reviewLanguageKey, "en");
    expect(writes).toEqual([]);
  });

  it("keeps an explicit English choice over Sinhala source language without changing source content", async () => {
    window.localStorage.setItem(reviewLanguageKey, "en");
    current.workspace.unit.observation.language = "si";
    await open();
    expect(
      screen.getByRole("heading", { name: "Review curriculum mapping" }),
    ).toBeVisible();
    expect(screen.getByText(sourceText)).toHaveAttribute("lang", "si");
    fireEvent.click(screen.getByRole("button", { name: "සිංහල" }));
    expect(
      screen.getByRole("heading", { name: "විෂයමාලා ගැළපීම පරීක්ෂා කරන්න" }),
    ).toBeVisible();
    expect(screen.getByText(sourceText).textContent).toBe(sourceText);
    expect(writes).toEqual([]);
  });

  it("ignores invalid or unavailable language preferences and respects a Sinhala source", async () => {
    window.localStorage.setItem(reviewLanguageKey, "invalid");
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("Storage unavailable");
    });
    current.workspace.unit.observation.language = "si";
    render(<MaterialCurriculumReview documentId={documentId} role="admin" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Page 1 · Section 1" }),
    );
    expect(
      await screen.findByRole("heading", {
        name: "විෂයමාලා ගැළපීම පරීක්ෂා කරන්න",
      }),
    ).toBeVisible();
    expect(writes).toEqual([]);
  });

  it("fences a slow section response after selection changes and cancels it", async () => {
    let resolve!: (response: Response) => void;
    intercept = async (request) =>
      new URL(request.url).pathname === `${basePath}/${unitId}`
        ? new Promise<Response>((done) => {
            resolve = done;
          })
        : undefined;
    render(<MaterialCurriculumReview documentId={documentId} role="admin" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Page 1 · Section 1" }),
    );
    await waitFor(() => expect(unitReads()).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Page 2 · Section 2" }));
    await screen.findByRole("img", { name: "Original page 2" });
    expect(unitReads()[0].signal.aborted).toBe(true);
    await act(async () => resolve(Response.json(current)));
    expect(
      screen.queryByRole("img", { name: "Original page 1" }),
    ).not.toBeInTheDocument();
  });

  it("polls pending status serially, stops on configuration changes, and aborts on unmount", async () => {
    current.workspace.review = reviewed();
    current.workspace.eligible = true;
    current.indexing = indexing({
      intent_id: id(5350),
      version: 1,
      status: "pending",
    });
    const view = await open();
    let resolve!: (response: Response) => void;
    intercept = async (request) =>
      new URL(request.url).pathname === `${basePath}/${unitId}`
        ? new Promise<Response>((done) => {
            resolve = done;
          })
        : undefined;
    vi.useFakeTimers();
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest version" }),
    );
    await flush();
    const count = unitReads().length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(unitReads()).toHaveLength(count);
    current.indexing = indexing({
      intent_id: id(5350),
      version: 2,
      status: "configuration_changed",
      retry_allowed: true,
    });
    await act(async () => resolve(Response.json(current)));
    expect(aiStatus()).toHaveTextContent("AI preparation settings changed");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(unitReads()).toHaveLength(count);
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest version" }),
    );
    await flush();
    view.unmount();
    expect(unitReads().at(-1)!.signal.aborted).toBe(true);
    await act(async () => resolve(Response.json(current)));
    expect(writes).toEqual([]);
  });

  it("keeps stale reads from advertising current readiness after a connection failure", async () => {
    current.workspace.review = reviewed();
    current.workspace.eligible = true;
    current.indexing = indexing({
      intent_id: id(5350),
      version: 1,
      status: "ready",
      ready: true,
    });
    await open();
    intercept = async (request) => {
      if (new URL(request.url).pathname === `${basePath}/${unitId}`)
        throw new Error("private network address");
      return undefined;
    };
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest version" }),
    );
    await screen.findByRole("alert");
    expect(aiStatus()).toHaveTextContent("AI readiness could not be confirmed");
    expect(screen.queryByText(/^Ready for AI$/)).not.toBeInTheDocument();
  });

  it("blocks saving after a material-list source change without discarding the section draft", async () => {
    await completeDraft();
    listing.source_current = false;
    fireEvent.click(screen.getByRole("button", { name: "Refresh sections" }));
    await waitFor(() =>
      expect(
        requests.filter(
          (request) => new URL(request.url).pathname === basePath,
        ),
      ).toHaveLength(2),
    );
    await flush();
    expect(screen.getByLabelText("Reason for this mapping")).toHaveValue(
      "This section supports the selected teaching area.",
    );
    expect(
      screen.getByRole("button", { name: "Save curriculum mapping" }),
    ).toBeDisabled();
    expect(writes).toEqual([]);
  });

  it("requires explicit rebasing after curriculum choices change and never silently selects a replacement", async () => {
    await completeDraft();
    nodes[0].active = false;
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest version" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Keep draft with latest version" }),
      ).toBeEnabled(),
    );
    expect(screen.getByLabelText("Teaching area")).toHaveValue(competencyId);
    expect(screen.getByLabelText("Reason for this mapping")).toHaveValue(
      "This section supports the selected teaching area.",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Keep draft with latest version" }),
    );
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(
      screen.queryByRole("option", { name: "Number sense" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("Previous choice is no longer available. Choose again."),
    ).toBeVisible();
    consent();
    expect(
      screen.getByRole("button", { name: "Save curriculum mapping" }),
    ).toBeDisabled();
    choose("Teaching area", otherCompetency);
    consent();
    save();
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].body.competency_id).toBe(otherCompetency);
  });

  it.each([401, 403, 404, 409, 422, 503])(
    "keeps a save draft on %s without automatic reads or mutation retries",
    async (status) => {
      await completeDraft();
      postStatus = status;
      save();
      const alert = await screen.findByRole("alert");
      expect(alert).not.toHaveTextContent(/private_failure|00000000-/);
      expect(screen.getByLabelText("Teaching area")).toHaveValue(competencyId);
      expect(screen.getByLabelText("Reason for this mapping")).toHaveValue(
        "This section supports the selected teaching area.",
      );
      expect(
        screen.getByRole("button", { name: "Save curriculum mapping" }),
      ).toBeDisabled();
      await flush();
      expect(unitReads()).toHaveLength(1);
      expect(writes).toHaveLength(1);
    },
  );

  it("preserves a retry reason on conflict and binds a renewed explicit retry to the latest intent", async () => {
    current.workspace.review = reviewed();
    current.workspace.eligible = true;
    current.indexing = indexing({
      intent_id: id(5350),
      version: 2,
      status: "needs_attention",
      retry_allowed: true,
    });
    await open();
    choose("Reason for trying again", "Settings checked.");
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "I want to try AI preparation again for this section.",
      }),
    );
    postStatus = 409;
    fireEvent.click(
      screen.getByRole("button", { name: "Try AI preparation again" }),
    );
    await screen.findByRole("alert");
    expect(screen.getByLabelText("Reason for trying again")).toHaveValue(
      "Settings checked.",
    );
    current.indexing.version = 3;
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest version" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Keep draft with latest version" }),
      ).toBeEnabled(),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Keep draft with latest version" }),
    );
    expect(
      screen.getByRole("button", { name: "Try AI preparation again" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "I want to try AI preparation again for this section.",
      }),
    );
    postStatus = 200;
    fireEvent.click(
      screen.getByRole("button", { name: "Try AI preparation again" }),
    );
    await waitFor(() => expect(writes).toHaveLength(2));
    expect(writes[1].body).toEqual({
      expected_version: 3,
      reason: "Settings checked.",
      confirmed_retry: true,
    });
    expect(writes.every((write) => write.path.endsWith("indexing-retry"))).toBe(
      true,
    );
  });

  it("does not lose a pending retry draft when server permission to retry changes", async () => {
    current.workspace.review = reviewed();
    current.workspace.eligible = true;
    current.indexing = indexing({
      intent_id: id(5350),
      version: 2,
      status: "needs_attention",
      retry_allowed: true,
    });
    await open();
    choose("Reason for trying again", "Retain this retry reason.");
    current.indexing = {
      ...current.indexing,
      version: 3,
      retry_allowed: false,
    };
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest version" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Keep draft with latest version" }),
      ).toBeEnabled(),
    );
    expect(screen.getByLabelText("Reason for trying again")).toHaveValue(
      "Retain this retry reason.",
    );
    expect(
      screen.queryByRole("button", { name: "Try AI preparation again" }),
    ).not.toBeInTheDocument();
  });

  it("does not mix a mapping draft with an indexing retry and only discards after consent", async () => {
    current.workspace.review = reviewed();
    current.workspace.eligible = true;
    current.indexing = indexing({
      intent_id: id(5350),
      version: 2,
      status: "needs_attention",
      retry_allowed: true,
    });
    await open();
    reason("Keep my mapping reason.");
    expect(screen.getByLabelText("Reason for trying again")).toBeDisabled();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "Discard draft" }));
    expect(screen.getByLabelText("Reason for this mapping")).toHaveValue(
      "Keep my mapping reason.",
    );
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Discard draft" }));
    expect(screen.getByLabelText("Reason for this mapping")).toHaveValue("");
    expect(screen.getByLabelText("Reason for trying again")).toBeEnabled();
    expect(writes).toEqual([]);
  });

  it("polls queued status automatically and serially until ready", async () => {
    current.workspace.review = reviewed();
    current.workspace.eligible = true;
    current.indexing = indexing({
      intent_id: id(5350),
      version: 1,
      status: "queued",
    });
    const view = await open();
    vi.useFakeTimers();
    fireEvent.click(
      screen.getByRole("button", { name: "Reload latest version" }),
    );
    await flush();
    const initialCount = unitReads().length;
    let resolve!: (response: Response) => void;
    intercept = async (request) =>
      new URL(request.url).pathname === `${basePath}/${unitId}`
        ? new Promise<Response>((done) => {
            resolve = done;
          })
        : undefined;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(unitReads()).toHaveLength(initialCount + 1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(25_000);
    });
    expect(unitReads()).toHaveLength(initialCount + 1);
    current.indexing = {
      ...current.indexing,
      version: 2,
      status: "ready",
      ready: true,
    };
    await act(async () => resolve(Response.json(current)));
    expect(aiStatus()).toHaveTextContent(/^Ready for AI$/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(unitReads()).toHaveLength(initialCount + 1);
    view.unmount();
    expect(unitReads().at(-1)!.signal.aborted).toBe(true);
  });

  it("does not poll non-pending states or submit on language changes", async () => {
    current.indexing = indexing({
      intent_id: id(5350),
      version: 1,
      status: "waiting_configuration",
    });
    const view = await open();
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "සිංහල" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(unitReads()).toHaveLength(1);
    expect(writes).toEqual([]);
    view.unmount();
  });

  it("rejects a workspace from another document without showing its content", async () => {
    current.workspace.unit.source.document_id = id(5390);
    render(<MaterialCurriculumReview documentId={documentId} role="admin" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Page 1 · Section 1" }),
    );
    await screen.findByRole("alert");
    expect(screen.queryByText(sourceText)).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(writes).toEqual([]);
  });

  it.each(["wrong document", "too many sections", "missing status"])(
    "rejects a malformed section list: %s",
    async (defect) => {
      if (defect === "wrong document") listing.document_id = id(5390);
      if (defect === "too many sections")
        listing.items = Array.from({ length: 21 }, (_, index) => ({
          ...listing.items[0],
          unit_id: id(5500 + index),
        }));
      if (defect === "missing status")
        delete (listing.items[0] as Partial<MaterialUnitSummary>).indexing;
      render(<MaterialCurriculumReview documentId={documentId} role="admin" />);
      await screen.findByRole("alert");
      expect(screen.queryByRole("img")).not.toBeInTheDocument();
    },
  );

  it("fences document changes and unmounts even if old transport ignores cancellation", async () => {
    let resolve!: (response: Response) => void;
    intercept = async (request) =>
      new URL(request.url).pathname === basePath
        ? new Promise<Response>((done) => {
            resolve = done;
          })
        : undefined;
    const view = render(
      <MaterialCurriculumReview documentId={documentId} role="admin" />,
    );
    await waitFor(() =>
      expect(
        requests.some((request) => new URL(request.url).pathname === basePath),
      ).toBe(true),
    );
    const originalRequest = requests.find(
      (request) => new URL(request.url).pathname === basePath,
    )!;
    view.rerender(
      <MaterialCurriculumReview documentId={id(5390)} role="admin" />,
    );
    await screen.findByRole("alert");
    expect(originalRequest.signal.aborted).toBe(true);
    await act(async () => resolve(Response.json(listing)));
    expect(
      screen.queryByRole("button", { name: "Page 1 · Section 1" }),
    ).not.toBeInTheDocument();
    view.unmount();
  });

  it("uses current Sinhala metadata candidates and mixed-source Sinhala hints only for presentation", async () => {
    material.medium = null;
    material.intake_metadata = { medium_label: "සිංහල" };
    const persist = vi.spyOn(Storage.prototype, "setItem");
    current.workspace.unit.observation.language = "mixed";
    render(<MaterialCurriculumReview documentId={documentId} role="admin" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "පිටුව 1 · කොටස 1" }),
    );
    expect(
      await screen.findByRole("heading", { name: "විෂයමාලා ගැළපීම" }),
    ).toBeVisible();
    expect(screen.getByText(sourceText).textContent).toBe(sourceText);
    expect(persist).not.toHaveBeenCalled();
    expect(writes).toEqual([]);
  });

  it("handles traversal before route restoration and discards only after consent", async () => {
    const previousUrl = window.location.href;
    const previousState: unknown = window.history.state;
    const path = `/admin/materials/${documentId}/review-curriculum`;
    window.history.replaceState({ retained: true }, "", path);
    const navigation = new EventTarget();
    vi.stubGlobal("navigation", navigation);
    const routed = vi.fn();
    function traverse() {
      const event = Object.assign(new Event("navigate", { cancelable: true }), {
        navigationType: "traverse",
        destination: {
          url: new URL("/admin/materials", window.location.href).href,
          sameDocument: true,
        },
      });
      if (navigation.dispatchEvent(event)) {
        routed();
        window.history.replaceState({}, "", "/admin/materials");
      }
    }
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    try {
      await completeDraft();
      const reasonField = screen.getByLabelText("Reason for this mapping");
      act(traverse);
      expect(confirm).toHaveBeenCalledTimes(1);
      expect(window.location.pathname).toBe(path);
      expect(routed).not.toHaveBeenCalled();
      expect(screen.getByLabelText("Reason for this mapping")).toBe(
        reasonField,
      );
      expect(reasonField).toHaveValue(
        "This section supports the selected teaching area.",
      );
      confirm.mockReturnValue(true);
      act(traverse);
      expect(routed).toHaveBeenCalledTimes(1);
      expect(
        screen.queryByLabelText("Reason for this mapping"),
      ).not.toBeInTheDocument();
      expect(writes).toEqual([]);
    } finally {
      window.history.replaceState(previousState, "", previousUrl);
    }
  });

  it("retains a dismissed native unload draft and clears it on committed page hiding", async () => {
    vi.stubGlobal("navigation", undefined);
    await completeDraft();
    const field = screen.getByLabelText("Reason for this mapping");
    const unload = new Event("beforeunload", { cancelable: true });
    act(() => {
      window.dispatchEvent(unload);
    });
    expect(unload.defaultPrevented).toBe(true);
    expect(screen.getByLabelText("Reason for this mapping")).toBe(field);
    expect(field).toHaveValue(
      "This section supports the selected teaching area.",
    );
    act(() => {
      window.dispatchEvent(
        new PageTransitionEvent("pagehide", { persisted: true }),
      );
    });
    expect(
      screen.queryByLabelText("Reason for this mapping"),
    ).not.toBeInTheDocument();
    act(() => {
      window.dispatchEvent(
        new PageTransitionEvent("pageshow", { persisted: true }),
      );
    });
    fireEvent.click(screen.getByRole("button", { name: "Page 1 · Section 1" }));
    await screen.findByRole("heading", { name: "Curriculum mapping" });
    expect(screen.getByLabelText("Reason for this mapping")).toHaveValue("");
    expect(writes).toEqual([]);
  });

  it("keeps the single-column review pane scrollable inside the desktop shell", async () => {
    await open();
    expect(
      screen.getByRole("region", { name: "Page 1 · Section 1" }),
    ).toHaveClass("lg:overflow-y-auto");
  });

  it("has labelled controls and no automated accessibility violations", async () => {
    const { container } = await open();
    choose("Teaching area", competencyId);
    const result = await axe.run(container);
    expect(result.violations).toEqual([]);
    expect(
      within(
        screen.getByRole("form", { name: "Curriculum mapping" }),
      ).getByRole("checkbox"),
    ).toHaveAccessibleName();
  });
});
