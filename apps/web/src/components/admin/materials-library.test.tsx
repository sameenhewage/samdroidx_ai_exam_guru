import type { components } from "@exam-guru/api-client";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MaterialDetails } from "./material-details";
import { MaterialsLibrary } from "./materials-library";

type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type GradeSummary = components["schemas"]["MaterialGradeSummaryResponse"];
type Lesson = components["schemas"]["CurriculumLessonResponse"];
type Material = components["schemas"]["MaterialListItemResponse"];
type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type Unit = components["schemas"]["CurriculumUnitResponse"];

const now = "2026-08-25T11:00:00Z";
const ids = {
  curriculum: "00000000-0000-0000-0000-000000000403",
  curriculumEleven: "00000000-0000-0000-0000-000000000413",
  exam: "00000000-0000-0000-0000-000000000404",
  examEleven: "00000000-0000-0000-0000-000000000414",
  guide: "00000000-0000-0000-0000-000000000502",
  lesson: "00000000-0000-0000-0000-000000000407",
  mathsSubject: "00000000-0000-0000-0000-000000000401",
  medium: "00000000-0000-0000-0000-000000000405",
  oldAnswers: "00000000-0000-0000-0000-000000000504",
  pastPaper: "00000000-0000-0000-0000-000000000503",
  sinhalaGuide: "00000000-0000-0000-0000-000000000505",
  sinhalaSubject: "00000000-0000-0000-0000-000000000402",
  syllabus: "00000000-0000-0000-0000-000000000501",
  unit: "00000000-0000-0000-0000-000000000406",
  uploaded: "00000000-0000-0000-0000-000000000506",
} as const;

const curricula: Curriculum[] = [
  {
    active: true,
    code: "G5-MATHS-2026",
    created_at: "2026-08-23T00:00:00Z",
    exam_configuration_id: ids.exam,
    id: ids.curriculum,
    medium_id: ids.medium,
    subject_id: ids.mathsSubject,
    title: "2026 curriculum",
    updated_at: "2026-08-23T00:00:00Z",
  },
  {
    active: true,
    code: "G11-MATHS-2026",
    created_at: "2026-08-23T00:00:00Z",
    exam_configuration_id: ids.examEleven,
    id: ids.curriculumEleven,
    medium_id: ids.medium,
    subject_id: ids.mathsSubject,
    title: "Grade 11 Maths 2026",
    updated_at: "2026-08-23T00:00:00Z",
  },
];

const unit: Unit = {
  active: true,
  code: "NUMBERS",
  created_at: now,
  curriculum_version_id: ids.curriculum,
  id: ids.unit,
  ordinal: 1,
  title: "Numbers",
  updated_at: now,
};
const lesson: Lesson = {
  active: true,
  code: "FRACTIONS",
  created_at: now,
  curriculum_version_id: ids.curriculum,
  id: ids.lesson,
  ordinal: 1,
  taxonomy_node_ids: [],
  title: "Fractions",
  unit_id: ids.unit,
  updated_at: now,
};

const materials: Material[] = [
  {
    curriculum: "2026 curriculum",
    grade: 5,
    id: ids.syllabus,
    lesson: null,
    material_type: "syllabus",
    medium: "English",
    metadata_scope_version: 1,
    page_count: 42,
    status: "ready_for_ai",
    subject: "Maths",
    subject_id: ids.mathsSubject,
    title: "grade-5-maths-syllabus.pdf",
    unit: null,
    uploaded_at: "2026-08-23T12:30:00Z",
    year: 2026,
  },
  {
    curriculum: "2026 curriculum",
    grade: 5,
    id: ids.guide,
    lesson: "Fractions",
    material_type: "teacher_guide",
    medium: "English",
    metadata_scope_version: 1,
    page_count: 96,
    status: "processing",
    subject: "Maths",
    subject_id: ids.mathsSubject,
    title: "grade-5-maths-teacher-guide.pdf",
    unit: "Numbers",
    uploaded_at: "2026-08-24T09:00:00Z",
    year: 2026,
  },
  {
    curriculum: null,
    grade: 5,
    id: ids.pastPaper,
    lesson: null,
    material_type: "past_paper",
    medium: "English",
    metadata_scope_version: 1,
    page_count: 12,
    status: "needs_review",
    subject: "Maths",
    subject_id: ids.mathsSubject,
    title: "grade-5-maths-2025-paper.pdf",
    unit: null,
    uploaded_at: "2026-08-24T10:00:00Z",
    year: 2025,
  },
  {
    curriculum: null,
    grade: 5,
    id: ids.oldAnswers,
    lesson: null,
    material_type: "marking_scheme",
    medium: "English",
    metadata_scope_version: 1,
    page_count: 8,
    status: "removed",
    subject: "Maths",
    subject_id: ids.mathsSubject,
    title: "grade-5-maths-2024-answers.pdf",
    unit: null,
    uploaded_at: "2026-08-20T08:00:00Z",
    year: 2024,
  },
  {
    curriculum: "2026 curriculum",
    grade: 5,
    id: ids.sinhalaGuide,
    lesson: null,
    material_type: "teacher_guide",
    medium: "Sinhala",
    metadata_scope_version: 1,
    page_count: 120,
    status: "ready_for_ai",
    subject: "Sinhala",
    subject_id: ids.sinhalaSubject,
    title: "grade-5-sinhala-teacher-guide.pdf",
    unit: null,
    uploaded_at: "2026-08-19T08:00:00Z",
    year: 2026,
  },
];

function sourceDocument(
  material: Material,
  overrides: Partial<SourceDocument> = {},
): SourceDocument {
  const removed = material.status === "removed";
  const extractionStatus =
    material.status === "processing"
      ? "extraction_pending"
      : material.status === "needs_review"
        ? "extracted"
        : "trusted";
  return {
    active_for_ai: !removed,
    checksum_sha256: material.id === ids.syllabus ? "a".repeat(64) : material.id.replaceAll("-", "").padEnd(64, "0").slice(0, 64),
    content_type: "application/pdf",
    created_at: material.uploaded_at,
    curriculum_version_id:
      material.curriculum === "2026 curriculum" ? ids.curriculum : null,
    deduplicated: false,
    document_type: material.material_type,
    extracted_block_count: extractionStatus === "extraction_pending" ? null : 1,
    extracted_character_count: extractionStatus === "extraction_pending" ? null : 100,
    extracted_page_count: material.page_count,
    extraction_attempt_count: extractionStatus === "extraction_pending" ? 1 : 1,
    extraction_completed_at:
      extractionStatus === "extraction_pending" ? null : "2026-08-24T10:01:00Z",
    extraction_config: null,
    extraction_failure_code: null,
    extraction_queue_message_id:
      extractionStatus === "extraction_pending" ? "fixture-message" : null,
    extraction_started_at:
      extractionStatus === "extraction_pending" ? "2026-08-24T10:00:30Z" : null,
    extraction_status: extractionStatus,
    extractor: extractionStatus === "extraction_pending" ? null : "pymupdf",
    extractor_version: extractionStatus === "extraction_pending" ? null : "1.28.2",
    id: material.id,
    lesson_id: material.lesson ? ids.lesson : null,
    likely_metadata_duplicate_of_id: null,
    metadata_scope_version: material.metadata_scope_version,
    native_text_page_ratio: extractionStatus === "extraction_pending" ? null : 1,
    needs_ocr: extractionStatus === "extraction_pending" ? null : false,
    ocr_page_count: extractionStatus === "extraction_pending" ? null : 0,
    original_filename: material.title,
    paper_code: null,
    removal_reason: removed ? "Superseded answer set" : null,
    removed_at: removed ? "2026-08-21T08:00:00Z" : null,
    removed_by: removed ? "00000000-0000-0000-0000-000000000001" : null,
    size_bytes: 1_024,
    subject_id: material.subject_id,
    unit_id: material.unit ? ids.unit : null,
    use_state: removed ? "removed" : "active",
    year: material.year,
    ...overrides,
  };
}

const gradeSummaries: GradeSummary[] = Array.from({ length: 13 }, (_, index) => {
  const grade = index + 1;
  if (grade === 5) {
    return {
      grade,
      material_count: 5,
      needs_review_count: 1,
      processing_count: 1,
      ready_count: 2,
      removed_count: 1,
      subject_count: 2,
    };
  }
  return {
    grade,
    material_count: grade === 7 ? 3 : 0,
    needs_review_count: 0,
    processing_count: 0,
    ready_count: grade === 7 ? 3 : 0,
    removed_count: 0,
    subject_count: grade === 7 ? 1 : 0,
  };
});

type FixtureOptions = {
  exactDuplicate?: boolean;
  materialPages?: Record<number, Material[]>;
  restoreConflict?: boolean;
  scopeConflict?: boolean;
  throwOnUpload?: boolean;
  workspaceStatus?: number;
};

function asRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(input, init);
}

function fixtureApi(options: FixtureOptions = {}) {
  const requests: Request[] = [];
  let currentMaterials = materials.map((material) => ({ ...material }));
  let sources = currentMaterials.map((material) => sourceDocument(material));

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    requests.push(request.clone());
    const url = new URL(request.url);
    const path = url.pathname;

    if (options.workspaceStatus && request.method === "GET" && path.endsWith("/materials/grade-summary")) {
      return Response.json(
        { detail: { code: options.workspaceStatus === 403 ? "permission_denied" : "request_failed" } },
        { status: options.workspaceStatus },
      );
    }
    if (request.method === "GET" && path.endsWith("/exam-configurations")) {
      return Response.json([
        { active: true, code: "G5", created_at: now, grade: 5, id: ids.exam, name: "Grade 5 Scholarship", updated_at: now },
        { active: true, code: "G11", created_at: now, grade: 11, id: ids.examEleven, name: "GCE O/L", updated_at: now },
      ]);
    }
    if (request.method === "GET" && path.endsWith("/media")) {
      return Response.json([
        { active: true, code: "en", created_at: now, id: ids.medium, name: "English", updated_at: now },
      ]);
    }
    if (request.method === "GET" && path.endsWith("/subjects")) {
      return Response.json([
        { active: true, code: "MATHS", created_at: now, id: ids.mathsSubject, name: "Maths", updated_at: now },
        { active: true, code: "SINHALA", created_at: now, id: ids.sinhalaSubject, name: "Sinhala", updated_at: now },
      ]);
    }
    if (request.method === "GET" && path.endsWith("/curriculum-versions")) {
      return Response.json(curricula);
    }
    if (request.method === "GET" && path.endsWith(`/curriculum-versions/${ids.curriculum}/units`)) {
      return Response.json([unit]);
    }
    if (request.method === "GET" && path.endsWith(`/curriculum-versions/${ids.curriculum}/lessons`)) {
      return Response.json([lesson]);
    }
    if (request.method === "GET" && path.includes(`/curriculum-versions/${ids.curriculumEleven}/`)) {
      return Response.json([]);
    }
    if (request.method === "GET" && path.endsWith("/materials/grade-summary")) {
      return Response.json(gradeSummaries);
    }
    if (request.method === "GET" && path.endsWith("/materials")) {
      if (options.materialPages) {
        return Response.json(options.materialPages[Number(url.searchParams.get("offset") ?? 0)] ?? []);
      }
      const grade = url.searchParams.get("grade");
      const subjectId = url.searchParams.get("subject_id");
      return Response.json(
        currentMaterials.filter(
          (material) =>
            (!grade || material.grade === Number(grade)) &&
            (!subjectId || material.subject_id === subjectId),
        ),
      );
    }
    if (request.method === "GET" && path.endsWith("/source-documents")) {
      return Response.json(sources);
    }
    if (request.method === "POST" && path.endsWith("/source-documents")) {
      if (options.throwOnUpload) throw new TypeError("network unavailable");
      if (options.exactDuplicate) {
        return Response.json(
          { ...sources.find((source) => source.id === ids.syllabus), deduplicated: true },
          { status: 200 },
        );
      }
      const uploadedMaterial: Material = {
        curriculum: null,
        grade: 5,
        id: ids.uploaded,
        lesson: null,
        material_type: "past_paper",
        medium: "English",
        metadata_scope_version: 0,
        page_count: null,
        status: "processing",
        subject: "Maths",
        subject_id: ids.mathsSubject,
        title: "grade-5-maths-2026-paper.pdf",
        unit: null,
        uploaded_at: now,
        year: 2026,
      };
      const uploadedSource = sourceDocument(uploadedMaterial, {
        extracted_page_count: null,
        extraction_attempt_count: 0,
        extraction_queue_message_id: null,
        extraction_status: "uploaded",
      });
      currentMaterials = [uploadedMaterial, ...currentMaterials];
      sources = [uploadedSource, ...sources];
      return Response.json(uploadedSource, { status: 201 });
    }
    if (request.method === "POST" && path.endsWith(`/source-documents/${ids.uploaded}/extract`)) {
      sources = sources.map((source) =>
        source.id === ids.uploaded
          ? { ...source, extraction_attempt_count: 1, extraction_status: "extraction_pending" }
          : source,
      );
      return Response.json(
        { document_id: ids.uploaded, message_id: "fixture-message", status: "extraction_pending" },
        { status: 202 },
      );
    }

    const material = currentMaterials.find((candidate) => path.includes(candidate.id));
    if (material && request.method === "POST" && path.endsWith(`/materials/${material.id}/remove-from-use`)) {
      material.status = "removed";
      material.metadata_scope_version += 1;
      sources = sources.map((source) =>
        source.id === material.id
          ? {
              ...source,
              active_for_ai: false,
              metadata_scope_version: material.metadata_scope_version,
              removal_reason: "Uploaded to the wrong grade",
              removed_at: now,
              removed_by: "00000000-0000-0000-0000-000000000001",
              use_state: "removed",
            }
          : source,
      );
      return Response.json(sources.find((source) => source.id === material.id));
    }
    if (material && request.method === "POST" && path.endsWith(`/materials/${material.id}/restore`)) {
      if (options.restoreConflict) {
        return Response.json(
          { detail: { code: "concurrent_material_scope_modification" } },
          { status: 409 },
        );
      }
      material.status = material.id === ids.syllabus ? "ready_for_ai" : "needs_review";
      material.metadata_scope_version += 1;
      sources = sources.map((source) =>
        source.id === material.id
          ? {
              ...source,
              active_for_ai: true,
              metadata_scope_version: material.metadata_scope_version,
              removal_reason: null,
              removed_at: null,
              removed_by: null,
              use_state: "active",
            }
          : source,
      );
      return Response.json(sources.find((source) => source.id === material.id));
    }
    if (material && request.method === "PATCH" && path.endsWith(`/materials/${material.id}/scope`)) {
      if (options.scopeConflict) {
        return Response.json(
          { detail: { code: "trusted_material_scope_immutable_remove_from_use" } },
          { status: 409 },
        );
      }
      material.curriculum = "Grade 11 Maths 2026";
      material.grade = 11;
      material.lesson = null;
      material.metadata_scope_version += 1;
      material.unit = null;
      sources = sources.map((source) =>
        source.id === material.id
          ? {
              ...source,
              curriculum_version_id: ids.curriculumEleven,
              lesson_id: null,
              metadata_scope_version: material.metadata_scope_version,
              unit_id: null,
            }
          : source,
      );
      return Response.json(sources.find((source) => source.id === material.id));
    }

    return Response.json({ detail: { code: "unexpected_request", path } }, { status: 500 });
  });

  return { fetchMock, requests };
}

async function renderLibrary(
  role: "admin" | "reviewer" = "admin",
  options: FixtureOptions = {},
) {
  const fixture = fixtureApi(options);
  vi.stubGlobal("fetch", fixture.fetchMock);
  const view = render(<MaterialsLibrary role={role} />);
  await screen.findByRole("heading", { level: 1, name: "Materials" });
  if (!options.workspaceStatus) {
    await screen.findByRole("region", { name: "Materials by grade" });
  }
  return { ...fixture, ...view };
}

async function chooseGradeFiveMaths() {
  const grades = screen.getByRole("region", { name: "Materials by grade" });
  fireEvent.click(within(grades).getByRole("button", { name: /Grade 5/i }));
  const subject = await screen.findByLabelText("Subject");
  fireEvent.change(subject, { target: { value: ids.mathsSubject } });
  await screen.findByText("grade-5-maths-syllabus.pdf");
}

async function continueWizard(dialog: HTMLElement) {
  fireEvent.click(within(dialog).getByRole("button", { name: "Continue" }));
}

async function openWizardAtPdfStep() {
  fireEvent.click(screen.getByRole("button", { name: "Upload material" }));
  const dialog = screen.getByRole("dialog", { name: "Upload material" });

  fireEvent.change(within(dialog).getByLabelText("Grade"), { target: { value: "5" } });
  await continueWizard(dialog);
  fireEvent.change(within(dialog).getByLabelText("Medium"), { target: { value: ids.medium } });
  await continueWizard(dialog);
  fireEvent.change(within(dialog).getByLabelText("Subject"), { target: { value: ids.mathsSubject } });
  await continueWizard(dialog);
  fireEvent.change(within(dialog).getByLabelText("Material type"), { target: { value: "past_paper" } });
  await continueWizard(dialog);
  fireEvent.change(within(dialog).getByLabelText("Year"), { target: { value: "2026" } });
  await continueWizard(dialog);

  return dialog;
}

async function jsonBody(request: Request): Promise<unknown> {
  return request.clone().json();
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("MaterialsLibrary", () => {
  it("opens the verified original PDF inside the material details view", async () => {
    const fixture = fixtureApi();
    vi.stubGlobal("fetch", fixture.fetchMock);
    render(<MaterialDetails documentId={ids.syllabus} role="reviewer" />);

    await screen.findByRole("heading", {
      level: 1,
      name: "grade-5-maths-syllabus.pdf",
    });
    const preview = screen.getByTitle("Original PDF: grade-5-maths-syllabus.pdf");
    expect(preview).toHaveAttribute(
      "src",
      `/api/v1/admin/source-documents/${ids.syllabus}/content`,
    );
    expect(screen.getByRole("link", { name: "Open original PDF in a new tab" })).toHaveAttribute(
      "href",
      `/api/v1/admin/source-documents/${ids.syllabus}/content`,
    );
  });

  it("uses bounded pagination so every material remains discoverable", async () => {
    const firstPage = Array.from({ length: 100 }, (_, index) => ({
      ...materials[0],
      id: `00000000-0000-0000-0001-${String(index + 1).padStart(12, "0")}`,
      title: `Grade 5 material ${index + 1}.pdf`,
    })) satisfies Material[];
    const { requests } = await renderLibrary("reviewer", {
      materialPages: { 0: firstPage, 100: [materials[0]] },
    });

    const overview = screen.getByRole("region", { name: "Materials by grade" });
    fireEvent.click(within(overview).getByRole("button", { name: /Grade 5/i }));
    expect(await screen.findByText("Grade 5 material 1.pdf")).toBeInTheDocument();
    expect(screen.queryByText(materials[0].title)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next materials page" }));
    expect(await screen.findByText(materials[0].title)).toBeInTheDocument();
    const offsets = requests
      .filter((request) => new URL(request.url).pathname.endsWith("/materials"))
      .map((request) => new URL(request.url).searchParams.get("offset"));
    expect(offsets).toEqual(["0", "100"]);

    fireEvent.change(screen.getByLabelText("Subject"), {
      target: { value: ids.mathsSubject },
    });
    await waitFor(() => {
      const materialRequests = requests.filter((request) =>
        new URL(request.url).pathname.endsWith("/materials"),
      );
      expect(
        new URL(materialRequests[materialRequests.length - 1]!.url).searchParams.get("offset"),
      ).toBe("0");
    });
  });

  it("opens directly on the Grade 5 uploaded-material library", async () => {
    await renderLibrary("reviewer");
    const list = await screen.findByRole("region", { name: "Uploaded materials" });
    expect(list).toHaveTextContent("grade-5-maths-syllabus.pdf");
    expect(
      screen.getByRole("button", { name: /Grade 5/i }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("moves focus into dialogs and closes them with Escape", async () => {
    await renderLibrary("admin");
    const upload = screen.getByRole("button", { name: "Upload material" });
    upload.focus();
    fireEvent.click(upload);
    const dialog = screen.getByRole("dialog", { name: "Upload material" });
    await waitFor(() =>
      expect(dialog).toContainElement(document.activeElement as HTMLElement),
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Upload material" })).not.toBeInTheDocument();
    expect(upload).toHaveFocus();
  });

  it("requires explicit buttons instead of advancing or uploading on form submit", async () => {
    const { requests } = await renderLibrary("admin");
    fireEvent.click(screen.getByRole("button", { name: "Upload material" }));
    let dialog = screen.getByRole("dialog", { name: "Upload material" });
    const firstForm = dialog.querySelector("form");
    if (!firstForm) throw new Error("Upload wizard form is required");
    fireEvent.change(within(dialog).getByLabelText("Grade"), { target: { value: "5" } });
    fireEvent.submit(firstForm);
    expect(within(dialog).getByLabelText("Grade")).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Medium")).not.toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    dialog = await openWizardAtPdfStep();
    const file = new File(["%PDF-1.7\nexplicit"], "explicit-upload.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("PDF file"), { target: { files: [file] } });
    await continueWizard(dialog);
    const finalForm = dialog.querySelector("form");
    if (!finalForm) throw new Error("Upload review form is required");
    fireEvent.submit(finalForm);
    await waitFor(() =>
      expect(
        requests.filter(
          (request) =>
            request.method === "POST" &&
            new URL(request.url).pathname.endsWith("/source-documents"),
        ),
      ).toHaveLength(0),
    );
    expect(within(dialog).getByRole("button", { name: "Upload material" })).toBeEnabled();
  });

  it("shows Grades 1–13 with understandable counts and national-exam badges", async () => {
    await renderLibrary("reviewer");

    const overview = screen.getByRole("region", { name: "Materials by grade" });
    expect(within(overview).getAllByRole("button", { name: /^Grade \d+/i })).toHaveLength(13);
    const gradeFive = within(overview).getByRole("button", { name: /Grade 5/i });
    expect(gradeFive).toHaveTextContent("5 materials");
    expect(gradeFive).toHaveTextContent("2 subjects");
    expect(gradeFive).toHaveTextContent("2 Ready");
    expect(gradeFive).toHaveTextContent("1 Needs review");
    expect(gradeFive).toHaveTextContent("Scholarship");
    expect(within(overview).getByRole("button", { name: /Grade 11/i })).toHaveTextContent("O/L");
    expect(within(overview).getByRole("button", { name: /Grade 13/i })).toHaveTextContent("A/L");
  });

  it("searches and filters server-side while rendering readable teacher statuses", async () => {
    const { requests } = await renderLibrary("reviewer");
    await chooseGradeFiveMaths();

    const list = screen.getByRole("region", { name: "Uploaded materials" });
    expect(within(list).getByText("grade-5-maths-syllabus.pdf")).toBeInTheDocument();
    expect(list).toHaveTextContent("Grade 5");
    expect(list).toHaveTextContent("Maths");
    expect(list).toHaveTextContent("English");
    expect(list).toHaveTextContent("Syllabus");
    expect(list).toHaveTextContent("2026 curriculum");
    expect(list).toHaveTextContent("42 pages");
    expect(list).toHaveTextContent("23 Aug 2026");
    for (const status of ["Processing", "Needs review", "Ready for AI", "Removed"]) {
      expect(list).toHaveTextContent(status);
    }
    expect(list).not.toHaveTextContent("grade-5-sinhala-teacher-guide.pdf");

    fireEvent.change(within(list).getByLabelText("Search"), {
      target: { value: "syllabus" },
    });
    fireEvent.change(within(list).getByLabelText("Medium"), {
      target: { value: ids.medium },
    });
    fireEvent.change(within(list).getByLabelText("Material type"), {
      target: { value: "syllabus" },
    });
    fireEvent.change(within(list).getByLabelText("Status"), {
      target: { value: "ready_for_ai" },
    });
    fireEvent.change(within(list).getByLabelText("Year"), {
      target: { value: "2026" },
    });

    await waitFor(() => {
      expect(
        requests.some((candidate) => {
          const url = new URL(candidate.url);
          return (
            candidate.method === "GET" &&
            url.pathname.endsWith("/materials") &&
            url.searchParams.get("grade") === "5" &&
            url.searchParams.get("subject_id") === ids.mathsSubject &&
            url.searchParams.get("medium_id") === ids.medium &&
            url.searchParams.get("material_type") === "syllabus" &&
            url.searchParams.get("status") === "ready_for_ai" &&
            url.searchParams.get("year") === "2026" &&
            url.searchParams.get("search") === "syllabus"
          );
        }),
      ).toBe(true);
    });
  });

  it("uses the complete guided sequence, uploads once, and queues reading only for a new PDF", async () => {
    const { requests } = await renderLibrary("admin");
    const dialog = await openWizardAtPdfStep();

    expect(
      within(dialog)
        .getAllByRole("listitem")
        .map((item) => item.textContent?.trim()),
    ).toEqual(["Grade", "Medium", "Subject", "Material type", "Year or curriculum", "PDF", "Review"]);

    const file = new File(["%PDF-1.7\nunique"], "grade-5-maths-2026-paper.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("PDF file"), { target: { files: [file] } });
    const form = dialog.querySelector("form");
    if (!form) throw new Error("Upload wizard must render a form");
    fireEvent.submit(form);
    expect(
      requests.filter(
        (request) =>
          request.method === "POST" &&
          new URL(request.url).pathname.endsWith("/source-documents"),
      ),
    ).toHaveLength(0);
    expect(within(dialog).getByLabelText("PDF file")).toBeInTheDocument();
    await continueWizard(dialog);

    const review = await within(dialog).findByRole("region", { name: "Review upload" });
    expect(review).toHaveTextContent("Grade 5");
    expect(review).toHaveTextContent("English");
    expect(review).toHaveTextContent("Maths");
    expect(review).toHaveTextContent("Past Paper");
    expect(review).toHaveTextContent("2026");
    expect(review).toHaveTextContent("grade-5-maths-2026-paper.pdf");

    fireEvent.click(within(dialog).getByRole("button", { name: "Upload material" }));
    expect(await screen.findByText("Material uploaded. Reading the PDF now.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Uploaded materials" })).toHaveTextContent(
      "grade-5-maths-2026-paper.pdf",
    );

    const uploadRequests = requests.filter(
      (request) => request.method === "POST" && new URL(request.url).pathname.endsWith("/source-documents"),
    );
    expect(uploadRequests).toHaveLength(1);
    // jsdom and Node currently use different File implementations when Request.formData()
    // reparses multipart bodies, so inspect the real multipart request without reconstructing it.
    const uploadBody = await uploadRequests[0]!.clone().text();
    expect(uploadBody).toMatch(/name="document_type"\r?\n\r?\npast_paper/);
    expect(uploadBody).toMatch(/name="year"\r?\n\r?\n2026/);
    expect(
      requests.filter(
        (request) => request.method === "POST" && new URL(request.url).pathname.endsWith(`/source-documents/${ids.uploaded}/extract`),
      ),
    ).toHaveLength(1);
  });

  it("offers curriculum, unit, and lesson choices when the material type needs them", async () => {
    await renderLibrary("admin");
    fireEvent.click(screen.getByRole("button", { name: "Upload material" }));
    const dialog = screen.getByRole("dialog", { name: "Upload material" });
    fireEvent.change(within(dialog).getByLabelText("Grade"), { target: { value: "5" } });
    await continueWizard(dialog);
    fireEvent.change(within(dialog).getByLabelText("Medium"), { target: { value: ids.medium } });
    await continueWizard(dialog);
    fireEvent.change(within(dialog).getByLabelText("Subject"), { target: { value: ids.mathsSubject } });
    await continueWizard(dialog);
    fireEvent.change(within(dialog).getByLabelText("Material type"), { target: { value: "syllabus" } });
    await continueWizard(dialog);

    fireEvent.change(within(dialog).getByLabelText("Curriculum version"), {
      target: { value: ids.curriculum },
    });
    expect(await within(dialog).findByRole("option", { name: "Numbers" })).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Unit (optional)"), {
      target: { value: ids.unit },
    });
    expect(within(dialog).getByRole("option", { name: "Fractions" })).toBeInTheDocument();
  });

  it("stops an exact server-identified duplicate, links the item, and never queues it again", async () => {
    const { requests } = await renderLibrary("admin", { exactDuplicate: true });
    const dialog = await openWizardAtPdfStep();
    const duplicate = new File(["%PDF-1.7\nduplicate"], materials[0]!.title, {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("PDF file"), { target: { files: [duplicate] } });
    await continueWizard(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "Upload material" }));

    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("This exact PDF is already in Materials. No new copy was uploaded.");
    expect(alert).toHaveTextContent("grade-5-maths-syllabus.pdf");
    expect(within(alert).getByRole("link", { name: "View existing material" })).toHaveAttribute(
      "href",
      `/admin/materials/${ids.syllabus}`,
    );
    expect(within(dialog).queryByRole("button", { name: "Upload material" })).not.toBeInTheDocument();
    expect(
      requests.filter(
        (request) => request.method === "POST" && new URL(request.url).pathname.includes("/extract"),
      ),
    ).toHaveLength(0);
  });

  it("requires a bounded explicit removal reason and uses CAS for remove and restore", async () => {
    const { requests } = await renderLibrary("admin");
    await chooseGradeFiveMaths();

    fireEvent.click(screen.getByRole("button", { name: "Remove from use: grade-5-maths-syllabus.pdf" }));
    const dialog = screen.getByRole("dialog", { name: "Remove grade-5-maths-syllabus.pdf from use" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove from use" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Enter a reason");
    fireEvent.change(within(dialog).getByLabelText("Reason"), {
      target: { value: "Uploaded to the wrong grade" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove from use" }));

    expect(await screen.findByText("Removed from AI use.")).toBeInTheDocument();
    const remove = requests.find(
      (request) => request.method === "POST" && new URL(request.url).pathname.endsWith(`/materials/${ids.syllabus}/remove-from-use`),
    );
    expect(remove).toBeDefined();
    expect(await jsonBody(remove!)).toEqual({
      expected_version: 1,
      reason: "Uploaded to the wrong grade",
    });

    fireEvent.click(screen.getByRole("button", { name: "Restore: grade-5-maths-syllabus.pdf" }));
    expect(await screen.findByText("Restored for AI use.")).toBeInTheDocument();
    const restore = requests.find(
      (request) => request.method === "POST" && new URL(request.url).pathname.endsWith(`/materials/${ids.syllabus}/restore`),
    );
    expect(await jsonBody(restore!)).toEqual({ expected_version: 2 });
  });

  it("surfaces a stale restore as an error and keeps the removed material recoverable", async () => {
    await renderLibrary("admin", { restoreConflict: true });
    await chooseGradeFiveMaths();

    fireEvent.click(
      screen.getByRole("button", { name: "Restore: grade-5-maths-2024-answers.pdf" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("changed in another session");
    expect(
      screen.getByRole("button", { name: "Restore: grade-5-maths-2024-answers.pdf" }),
    ).toBeInTheDocument();
  });

  it("keeps scope choices on a trusted/downstream conflict and explains the safe recovery", async () => {
    const { requests } = await renderLibrary("admin", { scopeConflict: true });
    await chooseGradeFiveMaths();

    expect(
      screen.queryByRole("button", { name: "Edit metadata: grade-5-maths-syllabus.pdf" }),
    ).not.toBeInTheDocument();
    const syllabus = screen.getByText("grade-5-maths-syllabus.pdf").closest("article");
    expect(syllabus).toHaveTextContent("Remove from use before assigning a corrected version");

    fireEvent.click(
      screen.getByRole("button", { name: "Edit metadata: grade-5-maths-teacher-guide.pdf" }),
    );
    const dialog = screen.getByRole("dialog", { name: "Edit grade-5-maths-teacher-guide.pdf" });
    fireEvent.change(within(dialog).getByLabelText("Curriculum version"), {
      target: { value: ids.curriculumEleven },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Remove it from use instead",
    );
    expect(within(dialog).getByLabelText("Curriculum version")).toHaveValue(ids.curriculumEleven);
    const correction = requests.find(
      (request) => request.method === "PATCH" && new URL(request.url).pathname.endsWith(`/materials/${ids.guide}/scope`),
    );
    expect(await jsonBody(correction!)).toEqual({
      curriculum_version_id: ids.curriculumEleven,
      expected_version: 1,
      lesson_id: null,
      unit_id: null,
    });
  });

  it("preserves the reviewed upload when the network fails", async () => {
    await renderLibrary("admin", { throwOnUpload: true });
    const dialog = await openWizardAtPdfStep();
    const file = new File(["%PDF-1.7\nretry"], "retry-this-paper.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("PDF file"), { target: { files: [file] } });
    await continueWizard(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "Upload material" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("connection");
    expect(within(dialog).getByRole("region", { name: "Review upload" })).toHaveTextContent(
      "retry-this-paper.pdf",
    );
    expect(within(dialog).getByRole("button", { name: "Upload material" })).toBeInTheDocument();
  });

  it("keeps reviewer access read-only", async () => {
    await renderLibrary("reviewer");
    await chooseGradeFiveMaths();
    expect(screen.queryByRole("button", { name: "Upload material" })).not.toBeInTheDocument();
    expect(screen.getByText("Reviewer access is read-only.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove from use:/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit metadata:/ })).not.toBeInTheDocument();
  });

  it("renders permission denied, recoverable error, and empty states explicitly", async () => {
    await renderLibrary("reviewer", { workspaceStatus: 403 });
    expect(await screen.findByRole("alert")).toHaveTextContent("Materials access required");
  });

  it("shows an empty library after selecting a grade without materials", async () => {
    await renderLibrary("reviewer");
    const overview = screen.getByRole("region", { name: "Materials by grade" });
    fireEvent.click(within(overview).getByRole("button", { name: /Grade 2/i }));
    expect(await screen.findByText("No materials match this grade and subject.")).toBeInTheDocument();
  });

  it("keeps checksums and source identifiers in collapsed technical details", async () => {
    await renderLibrary("reviewer");
    await chooseGradeFiveMaths();

    const filename = screen.getByText("grade-5-maths-syllabus.pdf");
    const material = filename.closest("article");
    if (!material) throw new Error("Each material must have a readable article");
    const summary = within(material).getByText("Technical details", { selector: "summary" });
    const details = summary.closest("details");
    if (!details) throw new Error("Technical details must use a disclosure");
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText("Checksum")).not.toBeVisible();
    expect(within(details).getByText(ids.syllabus)).not.toBeVisible();
    fireEvent.click(summary);
    expect(within(details).getByText("Checksum")).toBeVisible();
    expect(within(details).getByText(ids.syllabus)).toBeVisible();
  });

  it("has no automated accessibility violations in the loaded library", async () => {
    const { container } = await renderLibrary("admin");
    await chooseGradeFiveMaths();
    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });
});
