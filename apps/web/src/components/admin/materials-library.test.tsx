import type { components } from "@exam-guru/api-client";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import axe from "axe-core";
import { File as BrowserFile } from "node:buffer";
import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UPLOAD_CHECKPOINT_KEY } from "@/lib/source-upload-checkpoint";

import { MaterialDetails } from "./material-details";
import { MaterialsLibrary } from "./materials-library";

type CatalogueEntry = components["schemas"]["MaterialCatalogueEntry"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type GradeSummary = components["schemas"]["MaterialGradeSummaryResponse"];
type Lesson = components["schemas"]["CurriculumLessonResponse"];
type Material = components["schemas"]["MaterialListItemResponse"];
type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type UploadSession = components["schemas"]["SourceUploadResponse"];
type UploadCreate = components["schemas"]["SourceUploadCreateRequest"];
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
  uploadSession: "00000000-0000-0000-0000-000000000507",
  uploadRequest: "00000000-0000-0000-0000-000000000509",
  readJob: "00000000-0000-0000-0000-000000000508",
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

const catalogue: CatalogueEntry[] = curricula.map((curriculum, index) => ({
  curriculum_version_id: curriculum.id,
  curriculum_title: curriculum.title,
  exam_configuration_id: curriculum.exam_configuration_id,
  exam_configuration_name: index === 0 ? "Grade 5 Scholarship" : "GCE O/L",
  grade: index === 0 ? 5 : 11,
  grade_label: index === 0 ? "Grade 5" : "Grade 11",
  medium_id: ids.medium,
  medium_name: "English",
  subject_id: ids.mathsSubject,
  subject_name: "Maths",
}));

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
    metadata_review_required: false,
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
    metadata_review_required: false,
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
    metadata_review_required: false,
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
    metadata_review_required: false,
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
    metadata_review_required: false,
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
    checksum_sha256:
      material.id === ids.syllabus
        ? "a".repeat(64)
        : material.id.replaceAll("-", "").padEnd(64, "0").slice(0, 64),
    content_type: "application/pdf",
    created_at: material.uploaded_at,
    curriculum_version_id:
      material.curriculum === "2026 curriculum" ? ids.curriculum : null,
    deduplicated: false,
    document_type: material.material_type,
    extracted_block_count: extractionStatus === "extraction_pending" ? null : 1,
    extracted_character_count:
      extractionStatus === "extraction_pending" ? null : 100,
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
    extractor_version:
      extractionStatus === "extraction_pending" ? null : "1.28.2",
    id: material.id,
    lesson_id: material.lesson ? ids.lesson : null,
    likely_metadata_duplicate_of_id: null,
    metadata_scope_version: material.metadata_scope_version,
    metadata_review_required: false,
    native_text_page_ratio:
      extractionStatus === "extraction_pending" ? null : 1,
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

const gradeSummaries: GradeSummary[] = Array.from(
  { length: 13 },
  (_, index) => {
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
  },
);

const intakeMaterial: Material = {
  ...materials[2],
  curriculum: null,
  grade: null,
  subject_id: null,
  subject: null,
  medium: null,
  title: "unassigned-workbook.pdf",
  material_type: "other_approved",
  metadata_review_required: true,
  intake_metadata: {
    candidate_grade: null,
    subject_label: "Mathematics",
    medium_label: "Sinhala",
    curriculum_label: "Unverified curriculum",
    document_type_label: "Workbook",
    year: 2020,
    term: "Term 2",
    publisher: "Original publisher",
    source_reference: "Local source inventory",
    evidence: ["Filename and cover only; not reviewed"],
    warnings: [
      "Grade could not be determined",
      "Legacy font needs visual review",
    ],
  },
};

const unassignedSummary: GradeSummary = {
  grade: null,
  material_count: 1,
  subject_count: 0,
  ready_count: 0,
  needs_review_count: 1,
  processing_count: 0,
  removed_count: 0,
};

type FixtureOptions = {
  catalogue?: CatalogueEntry[];
  initialMaterials?: Material[];
  summaries?: GradeSummary[];
  exactDuplicate?: boolean;
  materialPages?: Record<number, Material[]>;
  restoreConflict?: boolean;
  scopeConflict?: boolean;
  metadataCandidateConflict?: boolean;
  throwOnUpload?: boolean;
  interruptChunk?: boolean;
  pauseChunk?: boolean;
  uploadStatus?: number;
  lookupStatus?: number;
  savedUpload?: UploadSession;
  savedChecksum?: string;
  workspaceStatus?: number;
};

function asRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(input, init);
}

function fixtureApi(options: FixtureOptions = {}) {
  const requests: Request[] = [];
  let upload: UploadSession | undefined = options.savedUpload;
  let uploadMetadata: UploadCreate | undefined;
  let chunkInterrupted = false;
  let createResponseLost = false;
  const chunkReceipts: components["schemas"]["SourceUploadChunkReceipt"][] =
    options.savedUpload?.next_offset
      ? [
          {
            offset: 0,
            size_bytes: options.savedUpload.next_offset,
            checksum_sha256: options.savedChecksum ?? "a".repeat(64),
          },
        ]
      : [];
  let currentMaterials = (options.initialMaterials ?? materials).map(
    (material) => ({ ...material }),
  );
  let sources = currentMaterials.map((material) =>
    sourceDocument(material, {
      intake_metadata: material.intake_metadata,
      metadata_review_required: material.metadata_review_required,
    }),
  );

  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = asRequest(input, init);
      requests.push(request.clone());
      const url = new URL(request.url);
      const path = url.pathname;

      if (
        options.workspaceStatus &&
        request.method === "GET" &&
        path.endsWith("/materials/grade-summary")
      ) {
        return Response.json(
          {
            detail: {
              code:
                options.workspaceStatus === 403
                  ? "permission_denied"
                  : "request_failed",
            },
          },
          { status: options.workspaceStatus },
        );
      }
      if (request.method === "GET" && path.endsWith("/material-catalogue")) {
        return Response.json(options.catalogue ?? catalogue);
      }
      if (request.method === "GET" && path.endsWith("/exam-configurations")) {
        return Response.json([
          {
            active: true,
            code: "G5",
            created_at: now,
            grade: 5,
            id: ids.exam,
            name: "Grade 5 Scholarship",
            updated_at: now,
          },
          {
            active: true,
            code: "G11",
            created_at: now,
            grade: 11,
            id: ids.examEleven,
            name: "GCE O/L",
            updated_at: now,
          },
        ]);
      }
      if (request.method === "GET" && path.endsWith("/media")) {
        return Response.json([
          {
            active: true,
            code: "en",
            created_at: now,
            id: ids.medium,
            name: "English",
            updated_at: now,
          },
        ]);
      }
      if (request.method === "GET" && path.endsWith("/subjects")) {
        return Response.json([
          {
            active: true,
            code: "MATHS",
            created_at: now,
            id: ids.mathsSubject,
            name: "Maths",
            updated_at: now,
          },
          {
            active: true,
            code: "SINHALA",
            created_at: now,
            id: ids.sinhalaSubject,
            name: "Sinhala",
            updated_at: now,
          },
        ]);
      }
      if (request.method === "GET" && path.endsWith("/curriculum-versions")) {
        return Response.json(curricula);
      }
      if (
        request.method === "GET" &&
        path.endsWith(`/curriculum-versions/${ids.curriculum}/units`)
      ) {
        return Response.json([unit]);
      }
      if (
        request.method === "GET" &&
        path.endsWith(`/curriculum-versions/${ids.curriculum}/lessons`)
      ) {
        return Response.json([lesson]);
      }
      if (
        request.method === "GET" &&
        path.includes(`/curriculum-versions/${ids.curriculumEleven}/`)
      ) {
        return Response.json([]);
      }
      if (
        request.method === "GET" &&
        path.endsWith("/materials/grade-summary")
      ) {
        return Response.json(options.summaries ?? gradeSummaries);
      }
      if (request.method === "GET" && path.endsWith("/materials")) {
        if (options.materialPages) {
          return Response.json(
            options.materialPages[
              Number(url.searchParams.get("offset") ?? 0)
            ] ?? [],
          );
        }
        const grade = url.searchParams.get("grade");
        const subjectId = url.searchParams.get("subject_id");
        return Response.json(
          currentMaterials.filter(
            (material) =>
              (!grade || material.grade === Number(grade)) &&
              (url.searchParams.get("unassigned_only") !== "true" ||
                material.grade === null) &&
              (!url.searchParams.get("document_id") ||
                material.id === url.searchParams.get("document_id")) &&
              (!subjectId || material.subject_id === subjectId),
          ),
        );
      }
      if (request.method === "GET" && path.endsWith("/source-documents")) {
        return Response.json(sources);
      }
      if (request.method === "POST" && path.endsWith("/source-uploads")) {
        if (options.uploadStatus)
          return Response.json(
            {
              detail: {
                code:
                  options.uploadStatus === 409
                    ? "source_upload_quota_exceeded"
                    : options.uploadStatus === 429
                      ? "rate_limit_exceeded"
                      : "permission_denied",
              },
            },
            { status: options.uploadStatus, headers: { "Retry-After": "12" } },
          );
        const incoming = (await request.json()) as UploadCreate;
        if (upload && uploadMetadata?.request_id === incoming.request_id) {
          expect(incoming).toEqual(uploadMetadata);
          return Response.json(upload, { status: 201 });
        }
        uploadMetadata = incoming;
        upload = {
          id: ids.uploadSession,
          request_id: incoming.request_id,
          filename: uploadMetadata.filename,
          size_bytes: uploadMetadata.size_bytes,
          document_type: uploadMetadata.document_type,
          intake_metadata: uploadMetadata.intake_metadata ?? {},
          status: "uploading",
          next_offset: 0,
          verified_bytes: 0,
          version: 0,
          chunk_size_bytes: 4_194_304,
          deduplicated: false,
          created_at: now,
          updated_at: now,
        };
        if (options.throwOnUpload && !createResponseLost) {
          createResponseLost = true;
          throw new TypeError("response lost after session creation");
        }
        return Response.json(upload, { status: 201 });
      }
      if (
        request.method === "GET" &&
        path.includes("/source-uploads/by-request/")
      ) {
        if (options.lookupStatus)
          return Response.json(
            { detail: { code: "lookup_unavailable" } },
            { status: options.lookupStatus },
          );
        return upload?.request_id && path.endsWith(`/${upload.request_id}`)
          ? Response.json(upload)
          : Response.json(
              { detail: { code: "source_upload_not_found" } },
              { status: 404 },
            );
      }
      if (
        request.method === "GET" &&
        path.endsWith(`/source-uploads/${ids.uploadSession}`) &&
        upload
      ) {
        if (upload.status === "pending") {
          upload = {
            ...upload,
            status: "completed",
            document_id: options.exactDuplicate ? ids.syllabus : ids.uploaded,
            source_read_job_id: options.exactDuplicate ? null : ids.readJob,
            verified_bytes: upload.size_bytes,
            checksum_sha256: "a".repeat(64),
            deduplicated: options.exactDuplicate ?? false,
          };
          if (!options.exactDuplicate) {
            const uploadedMaterial: Material = {
              ...materials[2],
              id: ids.uploaded,
              title: upload.filename,
              year: uploadMetadata?.year ?? upload.intake_metadata.year ?? null,
              status: "processing",
              page_count: null,
              ...(Object.keys(upload.intake_metadata).length > 0
                ? {
                    grade: upload.intake_metadata.candidate_grade ?? null,
                    medium: upload.intake_metadata.medium_label ?? null,
                    subject: upload.intake_metadata.subject_label ?? null,
                    subject_id: null,
                    curriculum: null,
                    unit: null,
                    lesson: null,
                    metadata_review_required: true,
                    intake_metadata: upload.intake_metadata,
                  }
                : {}),
            };
            currentMaterials = [
              uploadedMaterial,
              ...currentMaterials.filter((item) => item.id !== ids.uploaded),
            ];
            sources = [
              sourceDocument(uploadedMaterial, {
                extraction_status: "uploaded",
                metadata_review_required:
                  uploadedMaterial.metadata_review_required,
                intake_metadata: uploadedMaterial.intake_metadata,
              }),
              ...sources.filter((item) => item.id !== ids.uploaded),
            ];
          }
        }
        return Response.json(upload);
      }
      if (
        path.endsWith(`/source-uploads/${ids.uploadSession}/chunks`) &&
        upload
      ) {
        if (request.method === "GET")
          return Response.json({
            upload_id: upload.id,
            next_offset: upload.next_offset,
            receipts: chunkReceipts,
            next_receipt_offset: null,
          });
        if (request.method === "PUT") {
          const bytes = await request.arrayBuffer();
          if (options.interruptChunk && !chunkInterrupted) {
            chunkInterrupted = true;
            throw new TypeError("connection unavailable");
          }
          chunkReceipts.push({
            offset: upload.next_offset,
            size_bytes: bytes.byteLength,
            checksum_sha256: request.headers.get("X-Chunk-SHA256")!,
          });
          upload = {
            ...upload,
            next_offset: upload.next_offset + bytes.byteLength,
            version: upload.version + 1,
          };
          if (options.pauseChunk && !chunkInterrupted) {
            chunkInterrupted = true;
            return new Promise<Response>((_resolve, reject) => {
              const abort = () =>
                reject(new DOMException("Upload paused", "AbortError"));
              if (request.signal.aborted) abort();
              else
                request.signal.addEventListener("abort", abort, { once: true });
            });
          }
          return Response.json(upload);
        }
      }
      if (
        request.method === "POST" &&
        path.endsWith(`/source-uploads/${ids.uploadSession}/complete`) &&
        upload
      ) {
        expect(await request.json()).toEqual({
          expected_version: upload.version,
        });
        upload = { ...upload, status: "pending", version: upload.version + 1 };
        return Response.json(upload, { status: 202 });
      }
      if (
        request.method === "GET" &&
        path.endsWith(`/source-read-jobs/${ids.readJob}`)
      ) {
        return Response.json({
          id: ids.readJob,
          document_id: ids.uploaded,
          status: "queued",
          next_page: 1,
          page_number: null,
          version: 0,
          failure_code: null,
        });
      }

      const material = currentMaterials.find((candidate) =>
        path.includes(candidate.id),
      );
      if (
        material &&
        request.method === "POST" &&
        path.endsWith(`/materials/${material.id}/remove-from-use`)
      ) {
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
        return Response.json(
          sources.find((source) => source.id === material.id),
        );
      }
      if (
        material &&
        request.method === "POST" &&
        path.endsWith(`/materials/${material.id}/restore`)
      ) {
        if (options.restoreConflict) {
          return Response.json(
            { detail: { code: "concurrent_material_scope_modification" } },
            { status: 409 },
          );
        }
        material.status =
          material.id === ids.syllabus ? "ready_for_ai" : "needs_review";
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
        return Response.json(
          sources.find((source) => source.id === material.id),
        );
      }
      if (
        material &&
        request.method === "POST" &&
        path.endsWith(`/materials/${material.id}/metadata-candidates`)
      ) {
        if (options.metadataCandidateConflict)
          return Response.json(
            { detail: { code: "concurrent_material_scope_modification" } },
            { status: 409 },
          );
        const body =
          (await request.json()) as components["schemas"]["MaterialMetadataCandidateRequest"];
        const candidate: components["schemas"]["SourceMetadataCandidateResponse"] =
          {
            id: "00000000-0000-0000-0000-000000000510",
            version: body.expected_candidate_version + 1,
            scope_version: body.expected_scope_version,
            metadata: body.metadata,
            material_type: body.material_type ?? material.material_type,
            reason: body.reason,
            created_by: "00000000-0000-0000-0000-000000000001",
            created_at: now,
            is_current: true,
          };
        material.metadata_candidate = candidate;
        material.material_type = candidate.material_type;
        material.grade = body.metadata.candidate_grade ?? null;
        material.subject = body.metadata.subject_label ?? null;
        material.medium = body.metadata.medium_label ?? null;
        material.year = body.metadata.year ?? null;
        material.metadata_review_required = true;
        material.status = "needs_review";
        sources = sources.map((source) =>
          source.id === material.id
            ? {
                ...source,
                metadata_candidate: candidate,
                metadata_review_required: true,
              }
            : source,
        );
        return Response.json(
          sources.find((source) => source.id === material.id),
        );
      }
      if (
        material &&
        request.method === "PATCH" &&
        path.endsWith(`/materials/${material.id}/scope`)
      ) {
        if (options.scopeConflict) {
          return Response.json(
            {
              detail: {
                code: "trusted_material_scope_immutable_remove_from_use",
              },
            },
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
        return Response.json(
          sources.find((source) => source.id === material.id),
        );
      }

      return Response.json(
        { detail: { code: "unexpected_request", path } },
        { status: 500 },
      );
    },
  );

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

async function chooseScopeAssignment(
  dialog: HTMLElement,
  entry = catalogue[1],
) {
  fireEvent.click(
    within(dialog).getByRole("button", {
      name: "Change curriculum assignment",
    }),
  );
  fireEvent.change(within(dialog).getByLabelText("Grade"), {
    target: { value: String(entry.grade) },
  });
  fireEvent.change(within(dialog).getByLabelText("Medium"), {
    target: { value: entry.medium_id },
  });
  fireEvent.change(within(dialog).getByLabelText("Subject"), {
    target: { value: entry.subject_id },
  });
  fireEvent.change(within(dialog).getByLabelText("Curriculum version"), {
    target: { value: entry.curriculum_version_id },
  });
  await waitFor(() =>
    expect(
      within(dialog).queryByText("Loading units and lessons…"),
    ).not.toBeInTheDocument(),
  );
}

async function continueWizard(dialog: HTMLElement) {
  fireEvent.click(within(dialog).getByRole("button", { name: "Continue" }));
}

async function openWizardAtPdfStep(existingDialog?: HTMLElement) {
  if (!existingDialog)
    fireEvent.click(screen.getByRole("button", { name: "Upload material" }));
  const dialog =
    existingDialog ?? screen.getByRole("dialog", { name: "Upload material" });

  fireEvent.change(within(dialog).getByLabelText("Grade"), {
    target: { value: "5" },
  });
  await continueWizard(dialog);
  fireEvent.change(within(dialog).getByLabelText("Medium"), {
    target: { value: ids.medium },
  });
  await continueWizard(dialog);
  fireEvent.change(within(dialog).getByLabelText("Subject"), {
    target: { value: ids.mathsSubject },
  });
  await continueWizard(dialog);
  fireEvent.change(within(dialog).getByLabelText("Material type"), {
    target: { value: "past_paper" },
  });
  await continueWizard(dialog);
  fireEvent.change(within(dialog).getByLabelText("Year"), {
    target: { value: "2026" },
  });
  await continueWizard(dialog);

  return dialog;
}

async function openIntakeWizardAtPdfStep({
  grade = "5",
  medium = "Sinhala",
  subject = "ගණිතය",
  curriculum = "Cover edition 2023",
  year = "2023",
} = {}) {
  fireEvent.click(screen.getByRole("button", { name: "Upload material" }));
  const dialog = screen.getByRole("dialog", { name: "Upload material" });
  fireEvent.change(within(dialog).getByLabelText("Grade"), {
    target: { value: grade },
  });
  await continueWizard(dialog);
  expect(
    within(dialog).getByRole("option", { name: medium }),
  ).toBeInTheDocument();
  fireEvent.change(within(dialog).getByLabelText("Medium"), {
    target: { value: medium },
  });
  await continueWizard(dialog);
  fireEvent.change(within(dialog).getByLabelText("Subject (if known)"), {
    target: { value: subject },
  });
  await continueWizard(dialog);
  fireEvent.change(within(dialog).getByLabelText("Material type"), {
    target: { value: "past_paper" },
  });
  await continueWizard(dialog);
  expect(
    within(dialog).queryByLabelText("Curriculum version"),
  ).not.toBeInTheDocument();
  fireEvent.change(within(dialog).getByLabelText("Year (if known)"), {
    target: { value: year },
  });
  fireEvent.change(
    within(dialog).getByLabelText("Curriculum / edition (if known)"),
    {
      target: { value: curriculum },
    },
  );
  await continueWizard(dialog);
  return dialog;
}

async function jsonBody(request: Request): Promise<unknown> {
  return request.clone().json();
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("File", BrowserFile);
  vi.stubGlobal("crypto", webcrypto);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("MaterialsLibrary", () => {
  it("uses only the admitted catalogue for normal teacher selectors, never all active configuration", async () => {
    const approved = {
      ...catalogue[0],
      curriculum_title: "Reviewed test-preparation curriculum",
      medium_name: "Sinhala medium",
      subject_name: "Mathematics",
    } satisfies CatalogueEntry;
    const { requests } = await renderLibrary("admin", {
      catalogue: [approved],
    });
    const filters = screen.getByRole("region", { name: "Material filters" });
    expect(within(filters).getByLabelText("Subject")).toHaveTextContent(
      "Mathematics",
    );
    expect(within(filters).getByLabelText("Subject")).not.toHaveTextContent(
      "Maths",
    );
    expect(within(filters).getByLabelText("Medium")).toHaveTextContent(
      "Sinhala medium",
    );
    expect(
      requests.some((request) =>
        new URL(request.url).pathname.endsWith("/material-catalogue"),
      ),
    ).toBe(true);
    expect(
      requests.some((request) =>
        /\/(exam-configurations|media|subjects|curriculum-versions)$/.test(
          new URL(request.url).pathname,
        ),
      ),
    ).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Upload material" }));
    const dialog = screen.getByRole("dialog", { name: "Upload material" });
    fireEvent.change(within(dialog).getByLabelText("Grade"), {
      target: { value: "5" },
    });
    await continueWizard(dialog);
    expect(within(dialog).getByLabelText("Medium")).toHaveTextContent(
      "Sinhala medium",
    );
    fireEvent.change(within(dialog).getByLabelText("Medium"), {
      target: { value: ids.medium },
    });
    await continueWizard(dialog);
    expect(within(dialog).getByLabelText("Subject")).toHaveTextContent(
      "Mathematics",
    );
    fireEvent.change(within(dialog).getByLabelText("Subject"), {
      target: { value: ids.mathsSubject },
    });
    await continueWizard(dialog);
    await continueWizard(dialog);
    expect(
      within(dialog).getByLabelText("Curriculum version"),
    ).toHaveTextContent("Reviewed test-preparation curriculum");
    await within(dialog).findByLabelText("Unit (optional)");
  });

  it("derives media and subjects from approved grade/medium chains rather than unrelated catalogue rows", async () => {
    const other = {
      ...catalogue[1],
      medium_id: "00000000-0000-0000-0000-000000000450",
      medium_name: "Tamil",
      subject_id: ids.sinhalaSubject,
      subject_name: "History",
    } satisfies CatalogueEntry;
    await renderLibrary("admin", { catalogue: [catalogue[0], other] });
    expect(screen.getByLabelText("Subject")).not.toHaveTextContent("History");
    expect(screen.getByLabelText("Medium")).not.toHaveTextContent("Tamil");
    fireEvent.click(screen.getByRole("button", { name: "Upload material" }));
    const dialog = screen.getByRole("dialog", { name: "Upload material" });
    fireEvent.change(within(dialog).getByLabelText("Grade"), {
      target: { value: "5" },
    });
    await continueWizard(dialog);
    expect(within(dialog).getByLabelText("Medium")).not.toHaveTextContent(
      "Tamil",
    );
    fireEvent.change(within(dialog).getByLabelText("Medium"), {
      target: { value: ids.medium },
    });
    await continueWizard(dialog);
    expect(within(dialog).getByLabelText("Subject")).not.toHaveTextContent(
      "History",
    );
  });

  it("shows detected metadata before any edit controls and does not invent a mapping when no approved curriculum is available", async () => {
    const { requests } = await renderLibrary("admin", {
      catalogue: [],
      initialMaterials: [{ ...intakeMaterial, grade: 5 }],
    });
    fireEvent.click(
      await screen.findByRole("button", {
        name: `Edit metadata: ${intakeMaterial.title}`,
      }),
    );
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("පද්ධතිය හඳුනාගත් තොරතුරු")).toBeVisible();
    expect(
      within(dialog).getByText("විෂයමාලා තොරතුරු තහවුරු කිරීමට අවශ්‍යයි"),
    ).toBeVisible();
    expect(within(dialog).queryByRole("combobox")).not.toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Save changes" }),
    ).toBeDisabled();
    const confirmation = within(dialog).getByRole("checkbox", {
      name: /I have verified the intake metadata/,
    });
    expect(confirmation).not.toBeChecked();
    expect(confirmation).toBeDisabled();
    expect(requests.every((request) => request.method === "GET")).toBe(true);
  });

  it("offers Sinhala candidate-only correction without an admitted catalogue", async () => {
    const { requests } = await renderLibrary("admin", {
      catalogue: [],
      initialMaterials: [{ ...intakeMaterial, grade: 5 }],
    });
    fireEvent.click(
      await screen.findByRole("button", {
        name: `Edit metadata: ${intakeMaterial.title}`,
      }),
    );
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByLabelText("ශ්‍රේණිය")).not.toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "තොරතුරු වෙනස් කරන්න" }),
    );
    fireEvent.change(within(dialog).getByLabelText("ශ්‍රේණිය"), {
      target: { value: "3" },
    });
    fireEvent.change(within(dialog).getByLabelText("ද්‍රව්‍ය වර්ගය"), {
      target: { value: "Worksheet" },
    });
    fireEvent.change(within(dialog).getByLabelText("වෙනස් කිරීමට හේතුව"), {
      target: { value: "Correct the unverified worksheet description" },
    });
    const save = within(dialog).getByRole("button", {
      name: "පරීක්ෂාව සඳහා සුරකින්න",
    });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() =>
      expect(
        requests.some(
          (request) =>
            request.method === "POST" &&
            new URL(request.url).pathname.endsWith("/metadata-candidates"),
        ),
      ).toBe(true),
    );
    const request = requests.find(
      (value) =>
        value.method === "POST" &&
        new URL(value.url).pathname.endsWith("/metadata-candidates"),
    )!;
    const body = await request.json();
    expect(body.metadata.candidate_grade).toBe(3);
    expect(body.metadata.document_type_label).toBe("Worksheet");
    expect(body.metadata.medium_label).toBe("Sinhala");
    expect(body.expected_candidate_version).toBe(0);
    expect(body).not.toHaveProperty("confirm_intake_metadata");
    expect(body).not.toHaveProperty("curriculum_version_id");
    expect(requests.some((value) => value.method === "PATCH")).toBe(false);
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(
      screen.getByText(
        "තොරතුරු පරීක්ෂාව සඳහා සුරකින ලදී. මෙය AI භාවිතයට තහවුරු කිරීමක් නොවේ.",
      ),
    ).toBeVisible();
    fireEvent.click(
      await screen.findByRole("button", {
        name: `Edit metadata: ${intakeMaterial.title}`,
      }),
    );
    const reopened = screen.getByRole("dialog");
    expect(within(reopened).getByText("Worksheet")).toBeVisible();
    const original = within(reopened)
      .getByText("මුල් උඩුගත කිරීමේ තොරතුරු")
      .closest("details")!;
    expect(original).not.toHaveAttribute("open");
    expect(original).toHaveTextContent("Workbook");
  });

  it("retains unverified description edits on conflict without approving source metadata", async () => {
    await renderLibrary("admin", {
      catalogue: [],
      initialMaterials: [{ ...intakeMaterial, grade: 5 }],
      metadataCandidateConflict: true,
    });
    fireEvent.click(
      await screen.findByRole("button", {
        name: `Edit metadata: ${intakeMaterial.title}`,
      }),
    );
    const dialog = screen.getByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "තොරතුරු වෙනස් කරන්න" }),
    );
    fireEvent.change(within(dialog).getByLabelText("විෂය"), {
      target: { value: "Corrected proposed subject" },
    });
    fireEvent.change(within(dialog).getByLabelText("වෙනස් කිරීමට හේතුව"), {
      target: { value: "Description correction" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "පරීක්ෂාව සඳහා සුරකින්න" }),
    );
    await within(dialog).findByRole("alert");
    expect(within(dialog).getByLabelText("විෂය")).toHaveValue(
      "Corrected proposed subject",
    );
    expect(within(dialog).getByRole("checkbox")).toBeDisabled();
    expect(within(dialog).getByRole("checkbox")).not.toBeChecked();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("presents detected grade, medium, subject, material type and year, then reveals only approved assignments on request", async () => {
    await renderLibrary("admin", {
      initialMaterials: [{ ...intakeMaterial, grade: 5 }],
    });
    fireEvent.click(
      await screen.findByRole("button", {
        name: `Edit metadata: ${intakeMaterial.title}`,
      }),
    );
    const dialog = screen.getByRole("dialog");
    const detected = within(dialog).getByRole("region", {
      name: "Intake metadata",
    });
    expect(detected).toHaveTextContent("පද්ධතිය හඳුනාගත් තොරතුරු");
    for (const text of [
      "Candidate grade",
      "Medium label",
      "Subject label",
      "Workbook",
      "2020",
    ])
      expect(detected).toHaveTextContent(text);
    expect(
      within(dialog).queryByLabelText("Curriculum version"),
    ).not.toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Change curriculum assignment",
      }),
    );
    expect(within(dialog).getByLabelText("Grade")).toBeVisible();
    expect(within(dialog).getByLabelText("Curriculum version")).toHaveValue("");
    expect(within(dialog).getByRole("checkbox")).not.toBeChecked();
  });

  it.each(
    (["library", "details", "editor"] as const).flatMap((view) =>
      [false, true].map((hasEvidence) => ({ view, hasEvidence })),
    ),
  )(
    "keeps raw provenance collapsed in $view while retaining detected metadata and warnings (evidence: $hasEvidence)",
    async ({ view, hasEvidence }) => {
      const reference = `sha256:${"b".repeat(64)}`;
      const evidence = "Cover and filename evidence remains unverified";
      const material: Material = {
        ...intakeMaterial,
        grade: 5,
        page_count: 371,
        intake_metadata: {
          ...intakeMaterial.intake_metadata,
          candidate_grade: 5,
          source_reference: reference,
          evidence: hasEvidence ? [evidence] : [],
          warnings: ["Legacy font needs visual review"],
        },
      };
      const fixture =
        view === "details"
          ? fixtureApi({ initialMaterials: [material] })
          : await renderLibrary("admin", { initialMaterials: [material] });
      if (view === "details") {
        vi.stubGlobal("fetch", fixture.fetchMock);
        render(<MaterialDetails documentId={material.id} role="admin" />);
      }
      const heading = await screen.findByRole("heading", {
        name: material.title,
      });
      let container = heading.closest("article")!;
      if (view === "editor") {
        fireEvent.click(
          screen.getByRole("button", {
            name: `Edit metadata: ${material.title}`,
          }),
        );
        container = screen.getByRole("dialog");
      }
      const detected = within(container).getByRole("region", {
        name: "Intake metadata",
      });
      for (const value of [
        "Grade 5",
        "Sinhala",
        "Mathematics",
        "Workbook",
        "2020",
        "Term 2",
        "Original publisher",
        "Metadata needs review",
        "Legacy font needs visual review",
      ]) {
        expect(
          within(detected).getByText(value, { exact: true }),
        ).toBeVisible();
      }
      const sourceReference = within(detected).getByText(reference, {
        exact: true,
      });
      expect(detected.querySelector(":scope > dl")).not.toHaveTextContent(
        reference,
      );
      expect(sourceReference).not.toBeVisible();
      expect(
        within(detected).getByText("Source reference", { exact: true }),
      ).not.toBeVisible();
      const summary = within(detected).getByText("Intake evidence", {
        exact: true,
      });
      const disclosure = summary.closest("details")!;
      expect(disclosure).not.toHaveAttribute("open");
      expect(sourceReference.closest("details")).toBe(disclosure);
      fireEvent.click(summary);
      expect(sourceReference).toBeVisible();
      expect(sourceReference.textContent).toBe(reference);
      if (hasEvidence)
        expect(
          within(disclosure).getByText(evidence, { exact: true }),
        ).toBeVisible();
      fireEvent.click(summary);
      expect(sourceReference).not.toBeVisible();
      expect(
        fixture.requests.every((request) => request.method === "GET"),
      ).toBe(true);
    },
  );

  it("shows detected information before the assignment details on a normal material card", async () => {
    await renderLibrary("admin", {
      initialMaterials: [{ ...intakeMaterial, grade: 5 }],
    });
    const article = (
      await screen.findByRole("heading", { name: intakeMaterial.title })
    ).closest("article")!;
    const detected = within(article).getByRole("region", {
      name: "Intake metadata",
    });
    expect(article.querySelector("dl")).toBe(detected.querySelector("dl"));
  });

  it("clears a previous grade's medium filter before querying a different approved chain", async () => {
    const other = {
      ...catalogue[1],
      medium_id: "00000000-0000-0000-0000-000000000450",
      medium_name: "Tamil",
    };
    const { requests } = await renderLibrary("admin", {
      catalogue: [catalogue[0], other],
    });
    fireEvent.change(screen.getByLabelText("Medium"), {
      target: { value: ids.medium },
    });
    fireEvent.click(
      within(
        screen.getByRole("region", { name: "Materials by grade" }),
      ).getByRole("button", { name: /^Grade 11\b/ }),
    );
    await waitFor(() => {
      const request = requests
        .filter((request) =>
          new URL(request.url).pathname.endsWith("/materials"),
        )
        .at(-1)!;
      const query = new URL(request.url).searchParams;
      expect(query.get("grade")).toBe("11");
      expect(query.get("medium_id")).toBeNull();
    });
    expect(screen.getByLabelText("Medium")).toHaveTextContent("Tamil");
    expect(screen.getByLabelText("Medium")).not.toHaveTextContent("English");
  });

  it("keeps all grade cards and opens unresolved materials without assigning a grade", async () => {
    const { requests } = await renderLibrary("admin", {
      initialMaterials: [intakeMaterial],
      summaries: [...gradeSummaries, unassignedSummary],
    });
    const overview = screen.getByRole("region", { name: "Materials by grade" });
    expect(
      within(overview).getAllByRole("button", { name: /^Grade \d+/i }),
    ).toHaveLength(13);
    const unassigned = within(overview).getByRole("button", {
      name: /Unassigned materials/,
    });
    expect(unassigned).toHaveTextContent("1 material");
    expect(unassigned).toHaveTextContent("1 Needs review");
    fireEvent.click(unassigned);
    const title = await screen.findByRole("heading", {
      name: intakeMaterial.title,
    });
    const article = title.closest("article")!;
    expect(article).toHaveTextContent("Metadata needs review");
    expect(article).toHaveTextContent("Candidate metadata (unverified)");
    expect(article).toHaveTextContent("Workbook");
    expect(article).toHaveTextContent("Grade could not be determined");
    expect(article).toHaveTextContent("Legacy font needs visual review");
    expect(article).not.toHaveTextContent("Grade 5");
    const queries = requests.filter((request) =>
      new URL(request.url).pathname.endsWith("/materials"),
    );
    const query = new URL(queries.at(-1)!.url).searchParams;
    expect(query.get("unassigned_only")).toBe("true");
    expect(query.has("grade")).toBe(false);
    expect(
      screen.getByRole("region", { name: "Material filters" }),
    ).toBeInTheDocument();
    fireEvent.click(
      within(overview).getByRole("button", { name: /Grade 5\b/ }),
    );
    await waitFor(() => {
      const latest = requests
        .filter((request) =>
          new URL(request.url).pathname.endsWith("/materials"),
        )
        .at(-1)!;
      expect(new URL(latest.url).searchParams.get("grade")).toBe("5");
      expect(new URL(latest.url).searchParams.get("unassigned_only")).not.toBe(
        "true",
      );
    });
    expect(requests.every((request) => request.method === "GET")).toBe(true);
  });

  it("fetches one material by document_id and shows original intake labels without invented scope", async () => {
    const fixture = fixtureApi({ initialMaterials: [intakeMaterial] });
    vi.stubGlobal("fetch", fixture.fetchMock);
    render(<MaterialDetails documentId={intakeMaterial.id} role="admin" />);
    await screen.findByRole("heading", { name: intakeMaterial.title });
    expect(screen.getByText("Metadata needs review")).toBeInTheDocument();
    const metadata = screen.getByRole("region", { name: "Intake metadata" });
    for (const label of [
      "Candidate metadata (unverified)",
      "Workbook",
      "Mathematics",
      "Sinhala",
      "Unverified curriculum",
      "Term 2",
      "Original publisher",
      "Local source inventory",
      "Grade could not be determined",
    ]) {
      expect(metadata).toHaveTextContent(label);
    }
    const queries = fixture.requests.filter((request) =>
      new URL(request.url).pathname.endsWith("/materials"),
    );
    expect(queries).toHaveLength(1);
    expect(new URL(queries[0]!.url).searchParams.get("document_id")).toBe(
      intakeMaterial.id,
    );
    expect(new URL(queries[0]!.url).searchParams.get("limit")).toBe("1");
    expect(
      screen.getByTitle(`Original PDF: ${intakeMaterial.title}`),
    ).toBeInTheDocument();
    expect(screen.queryByText("Whole curriculum")).not.toBeInTheDocument();
    expect(screen.queryByText("All lessons in scope")).not.toBeInTheDocument();
  });

  it.each([false, true])(
    "only confirms intake metadata with explicit consent (%s)",
    async (confirm) => {
      const { requests } = await renderLibrary("admin", {
        initialMaterials: [{ ...intakeMaterial, grade: 5 }],
      });
      fireEvent.click(
        await screen.findByRole("button", {
          name: `Edit metadata: ${intakeMaterial.title}`,
        }),
      );
      const dialog = screen.getByRole("dialog");
      const checkbox = within(dialog).getByRole("checkbox", {
        name: /I have verified the intake metadata/,
      });
      expect(checkbox).not.toBeChecked();
      expect(checkbox).toBeDisabled();
      await chooseScopeAssignment(dialog);
      await waitFor(() => expect(checkbox).toBeEnabled());
      if (confirm) fireEvent.click(checkbox);
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Save changes" }),
      );
      await waitFor(() =>
        expect(requests.some((request) => request.method === "PATCH")).toBe(
          true,
        ),
      );
      const body = await jsonBody(
        requests.find((request) => request.method === "PATCH")!,
      );
      expect(body).toMatchObject({
        curriculum_version_id: ids.curriculumEleven,
        expected_version: 1,
      });
      if (confirm) expect(body).toHaveProperty("confirm_intake_metadata", true);
      else expect(body).not.toHaveProperty("confirm_intake_metadata", true);
      expect(
        requests.some((request) => /\/trust$|embedding/.test(request.url)),
      ).toBe(false);
    },
  );

  it("clears explicit metadata consent when the unit or lesson changes and never rewrites the existing year", async () => {
    const { requests } = await renderLibrary("admin", {
      initialMaterials: [
        {
          ...intakeMaterial,
          grade: 5,
          curriculum: "2026 curriculum",
          year: 2025,
        },
      ],
    });
    fireEvent.click(
      await screen.findByRole("button", {
        name: `Edit metadata: ${intakeMaterial.title}`,
      }),
    );
    const dialog = screen.getByRole("dialog");
    const checkbox = within(dialog).getByRole("checkbox", {
      name: /I have verified the intake metadata/,
    });
    await waitFor(() => expect(checkbox).toBeEnabled());
    fireEvent.click(checkbox);
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Change curriculum assignment",
      }),
    );
    fireEvent.change(within(dialog).getByLabelText("Unit (optional)"), {
      target: { value: ids.unit },
    });
    expect(checkbox).not.toBeChecked();
    fireEvent.click(checkbox);
    fireEvent.change(within(dialog).getByLabelText("Lesson (optional)"), {
      target: { value: ids.lesson },
    });
    expect(checkbox).not.toBeChecked();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save changes" }),
    );
    await waitFor(() =>
      expect(requests.some((request) => request.method === "PATCH")).toBe(true),
    );
    const body = await jsonBody(
      requests.find((request) => request.method === "PATCH")!,
    );
    expect(body).toMatchObject({
      confirm_intake_metadata: false,
      unit_id: ids.unit,
      lesson_id: ids.lesson,
    });
    expect(body).not.toHaveProperty("year");
  });

  it("does not carry a previous upload medium or subject into another grade", async () => {
    await renderLibrary("admin");
    fireEvent.click(screen.getByRole("button", { name: "Upload material" }));
    const dialog = screen.getByRole("dialog", { name: "Upload material" });
    fireEvent.change(within(dialog).getByLabelText("Grade"), {
      target: { value: "5" },
    });
    await continueWizard(dialog);
    fireEvent.change(within(dialog).getByLabelText("Medium"), {
      target: { value: ids.medium },
    });
    await continueWizard(dialog);
    fireEvent.change(within(dialog).getByLabelText("Subject"), {
      target: { value: ids.mathsSubject },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Back" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Back" }));
    fireEvent.change(within(dialog).getByLabelText("Grade"), {
      target: { value: "11" },
    });
    await continueWizard(dialog);
    expect(within(dialog).getByLabelText("Medium")).toHaveValue("");
    fireEvent.change(within(dialog).getByLabelText("Medium"), {
      target: { value: ids.medium },
    });
    await continueWizard(dialog);
    expect(within(dialog).getByLabelText("Subject")).toHaveValue("");
  });

  it("provides the new comparison link for sources regardless of legacy extraction state", async () => {
    await renderLibrary("admin");
    const link = await screen.findByRole("link", {
      name: `Review extracted text: ${materials[1].title}`,
    });
    expect(link).toHaveAttribute(
      "href",
      `/admin/materials/${ids.guide}/review-text`,
    );
  });

  it("keeps unadmitted legacy scope out of the material detail assignment and shows detected evidence first", async () => {
    const fixture = fixtureApi({
      catalogue: [],
      initialMaterials: [
        { ...intakeMaterial, grade: 5, curriculum: "2026 curriculum" },
      ],
    });
    vi.stubGlobal("fetch", fixture.fetchMock);
    render(<MaterialDetails documentId={intakeMaterial.id} role="admin" />);
    await screen.findByRole("heading", { name: intakeMaterial.title });
    expect(screen.getByText("පද්ධතිය හඳුනාගත් තොරතුරු")).toBeVisible();
    expect(
      screen.getByText("විෂයමාලා තොරතුරු තහවුරු කිරීමට අවශ්‍යයි"),
    ).toBeVisible();
    const details = screen.getByRole("region", { name: "Material details" });
    expect(details).not.toHaveTextContent("2026 curriculum");
    expect(details).not.toHaveTextContent("Whole curriculum");
    expect(
      fixture.requests.some((request) =>
        request.url.includes("/material-catalogue"),
      ),
    ).toBe(true);
    expect(fixture.requests.every((request) => request.method === "GET")).toBe(
      true,
    );
  });

  it("clears intake confirmation when the curriculum changes", async () => {
    await renderLibrary("admin", {
      initialMaterials: [{ ...intakeMaterial, grade: 5 }],
    });
    fireEvent.click(
      await screen.findByRole("button", {
        name: `Edit metadata: ${intakeMaterial.title}`,
      }),
    );
    const dialog = screen.getByRole("dialog");
    const checkbox = within(dialog).getByRole("checkbox", {
      name: /I have verified the intake metadata/,
    });
    await chooseScopeAssignment(dialog);
    await waitFor(() => expect(checkbox).toBeEnabled());
    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
    fireEvent.change(within(dialog).getByLabelText("Curriculum version"), {
      target: { value: "" },
    });
    expect(checkbox).not.toBeChecked();
    expect(checkbox).toBeDisabled();
  });

  it("lets a teacher open comparison for a newly uploaded or pending source without starting reading automatically", async () => {
    const fixture = fixtureApi({ initialMaterials: [materials[1]] });
    vi.stubGlobal("fetch", fixture.fetchMock);
    render(<MaterialDetails documentId={ids.guide} role="admin" />);
    await screen.findByRole("heading", { name: materials[1].title });
    expect(screen.getByRole("link", { name: "Review text" })).toHaveAttribute(
      "href",
      `/admin/materials/${ids.guide}/review-text`,
    );
    expect(fixture.requests.every((request) => request.method === "GET")).toBe(
      true,
    );
  });

  it("uses the streaming original-PDF endpoint for the detail preview and new-tab link", async () => {
    const fixture = fixtureApi();
    vi.stubGlobal("fetch", fixture.fetchMock);
    render(<MaterialDetails documentId={ids.syllabus} role="reviewer" />);

    await screen.findByRole("heading", {
      level: 1,
      name: "grade-5-maths-syllabus.pdf",
    });
    const preview = screen.getByTitle(
      "Original PDF: grade-5-maths-syllabus.pdf",
    );
    expect(preview).toHaveAttribute(
      "src",
      `/api/v1/admin/materials/${ids.syllabus}/original`,
    );
    expect(
      screen.getByRole("link", { name: "Open original PDF in a new tab" }),
    ).toHaveAttribute(
      "href",
      `/api/v1/admin/materials/${ids.syllabus}/original`,
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
    expect(
      await screen.findByText("Grade 5 material 1.pdf"),
    ).toBeInTheDocument();
    expect(screen.queryByText(materials[0].title)).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Next materials page" }),
    );
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
        new URL(
          materialRequests[materialRequests.length - 1]!.url,
        ).searchParams.get("offset"),
      ).toBe("0");
    });
  });

  it("opens directly on the Grade 5 uploaded-material library", async () => {
    await renderLibrary("reviewer");
    const list = await screen.findByRole("region", {
      name: "Uploaded materials",
    });
    expect(list).toHaveTextContent("grade-5-maths-syllabus.pdf");
    expect(screen.getByRole("button", { name: /Grade 5/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
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
    expect(
      screen.queryByRole("dialog", { name: "Upload material" }),
    ).not.toBeInTheDocument();
    expect(upload).toHaveFocus();
  });

  it("requires explicit buttons instead of advancing or uploading on form submit", async () => {
    const { requests } = await renderLibrary("admin");
    fireEvent.click(screen.getByRole("button", { name: "Upload material" }));
    let dialog = screen.getByRole("dialog", { name: "Upload material" });
    const firstForm = dialog.querySelector("form");
    if (!firstForm) throw new Error("Upload wizard form is required");
    fireEvent.change(within(dialog).getByLabelText("Grade"), {
      target: { value: "5" },
    });
    fireEvent.submit(firstForm);
    expect(within(dialog).getByLabelText("Grade")).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Medium")).not.toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    dialog = await openWizardAtPdfStep();
    const file = new File(["%PDF-1.7\nexplicit"], "explicit-upload.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("PDF file"), {
      target: { files: [file] },
    });
    await continueWizard(dialog);
    const finalForm = dialog.querySelector("form");
    if (!finalForm) throw new Error("Upload review form is required");
    fireEvent.submit(finalForm);
    await waitFor(() =>
      expect(
        requests.filter(
          (request) =>
            request.method === "POST" &&
            new URL(request.url).pathname.endsWith("/source-uploads"),
        ),
      ).toHaveLength(0),
    );
    expect(
      within(dialog).getByRole("button", { name: "Upload material" }),
    ).toBeEnabled();
  });

  it("shows Grades 1–13 with understandable counts and national-exam badges", async () => {
    await renderLibrary("reviewer");

    const overview = screen.getByRole("region", { name: "Materials by grade" });
    expect(
      within(overview).getAllByRole("button", { name: /^Grade \d+/i }),
    ).toHaveLength(13);
    const gradeFive = within(overview).getByRole("button", {
      name: /Grade 5/i,
    });
    expect(gradeFive).toHaveTextContent("5 materials");
    expect(gradeFive).toHaveTextContent("2 subjects");
    expect(gradeFive).toHaveTextContent("2 Ready");
    expect(gradeFive).toHaveTextContent("1 Needs review");
    expect(gradeFive).toHaveTextContent("Scholarship");
    expect(
      within(overview).getByRole("button", { name: /Grade 11/i }),
    ).toHaveTextContent("O/L");
    expect(
      within(overview).getByRole("button", { name: /Grade 13/i }),
    ).toHaveTextContent("A/L");
  });

  it("searches and filters server-side while rendering readable teacher statuses", async () => {
    const { requests } = await renderLibrary("reviewer");
    await chooseGradeFiveMaths();

    const list = screen.getByRole("region", { name: "Uploaded materials" });
    expect(
      within(list).getByText("grade-5-maths-syllabus.pdf"),
    ).toBeInTheDocument();
    expect(list).toHaveTextContent("Grade 5");
    expect(list).toHaveTextContent("Maths");
    expect(list).toHaveTextContent("English");
    expect(list).toHaveTextContent("Syllabus");
    expect(list).toHaveTextContent("2026 curriculum");
    expect(list).toHaveTextContent("42 pages");
    expect(list).toHaveTextContent("23 Aug 2026");
    for (const status of [
      "Processing",
      "Needs review",
      "Ready for AI",
      "Removed",
    ]) {
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

  it.each(["Sinhala", "Tamil", "English"])(
    "uploads unconfirmed %s details when the grade catalogue is empty without inventing approved scope",
    async (medium) => {
      const { requests } = await renderLibrary("admin", {
        catalogue: [],
        initialMaterials: [],
        summaries: [],
      });
      const dialog = await openIntakeWizardAtPdfStep({ medium });
      const file = new File(
        ["%PDF-1.7\nunconfirmed"],
        "unconfirmed-paper.pdf",
        {
          type: "application/pdf",
        },
      );
      fireEvent.change(within(dialog).getByLabelText("PDF file"), {
        target: { files: [file] },
      });
      await continueWizard(dialog);
      const review = within(dialog).getByRole("region", {
        name: "Review upload",
      });
      expect(review).toHaveTextContent(medium);
      expect(review).toHaveTextContent("ගණිතය");
      expect(review).toHaveTextContent("Cover edition 2023");
      expect(review).toHaveTextContent(/details.*review/i);
      expect(review).not.toHaveTextContent("Ready for AI");
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Upload material" }),
      );
      await screen.findByRole("link", { name: "Open uploaded material" });
      const creates = requests.filter(
        (request) =>
          request.method === "POST" &&
          new URL(request.url).pathname.endsWith("/source-uploads"),
      );
      expect(creates).toHaveLength(1);
      expect(await jsonBody(creates[0])).toMatchObject({
        filename: file.name,
        document_type: "past_paper",
        curriculum_version_id: null,
        unit_id: null,
        lesson_id: null,
        year: 2023,
        intake_metadata: {
          candidate_grade: 5,
          medium_label: medium,
          subject_label: "ගණිතය",
          curriculum_label: "Cover edition 2023",
          document_type_label: "Past Paper",
          year: 2023,
        },
      });
      expect(
        requests
          .filter((request) => request.method === "POST")
          .every((request) =>
            /\/source-uploads(?:\/[^/]+\/complete)?$/.test(
              new URL(request.url).pathname,
            ),
          ),
      ).toBe(true);
      expect(
        requests.some(
          (request) =>
            new URL(request.url).searchParams.has("subject_id") &&
            request.url.includes(ids.mathsSubject),
        ),
      ).toBe(false);
    },
  );

  it("allows unknown intake details without a guessed medium, subject, year or curriculum", async () => {
    const { requests } = await renderLibrary("admin", { catalogue: [] });
    const dialog = await openIntakeWizardAtPdfStep({
      grade: "13",
      medium: "Not sure",
      subject: "",
      curriculum: "",
      year: "",
    });
    fireEvent.change(within(dialog).getByLabelText("PDF file"), {
      target: {
        files: [
          new File(["%PDF-1.7\nunknown"], "unknown.pdf", {
            type: "application/pdf",
          }),
        ],
      },
    });
    await continueWizard(dialog);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Upload material" }),
    );
    await screen.findByRole("link", { name: "Open uploaded material" });
    const create = requests.find(
      (request) =>
        request.method === "POST" &&
        new URL(request.url).pathname.endsWith("/source-uploads"),
    )!;
    expect(await jsonBody(create)).toMatchObject({
      curriculum_version_id: null,
      unit_id: null,
      lesson_id: null,
      year: null,
      intake_metadata: {
        candidate_grade: 13,
        medium_label: null,
        subject_label: null,
        curriculum_label: null,
        year: null,
      },
    });
  });

  it("offers review-only details for an unlisted language even when another curriculum is admitted", async () => {
    await renderLibrary("admin");
    fireEvent.click(screen.getByRole("button", { name: "Upload material" }));
    const dialog = screen.getByRole("dialog", { name: "Upload material" });
    fireEvent.change(within(dialog).getByLabelText("Grade"), {
      target: { value: "5" },
    });
    await continueWizard(dialog);
    expect(
      within(dialog).queryByRole("option", { name: "Sinhala" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Enter details for review instead",
      }),
    );
    expect(
      within(dialog).getByRole("option", { name: "Sinhala" }),
    ).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Medium"), {
      target: { value: "Sinhala" },
    });
    await continueWizard(dialog);
    expect(within(dialog).getByLabelText("Subject (if known)")).toHaveValue("");
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Use listed options instead",
      }),
    );
    expect(within(dialog).getByLabelText("Medium")).toHaveValue("");
    expect(
      within(dialog).queryByRole("option", { name: "Sinhala" }),
    ).not.toBeInTheDocument();
  });

  it("does not report a failed upload before one has started and clears candidate details on grade changes", async () => {
    await renderLibrary("admin", { catalogue: [] });
    fireEvent.click(screen.getByRole("button", { name: "Upload material" }));
    const dialog = screen.getByRole("dialog", { name: "Upload material" });
    fireEvent.change(within(dialog).getByLabelText("Grade"), {
      target: { value: "5" },
    });
    await continueWizard(dialog);
    await continueWizard(dialog);
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Choose a medium to continue.",
    );
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Check upload details",
    );
    expect(within(dialog).getByRole("alert")).not.toHaveTextContent(
      "Upload was not completed",
    );
    fireEvent.change(within(dialog).getByLabelText("Medium"), {
      target: { value: "Sinhala" },
    });
    await continueWizard(dialog);
    fireEvent.change(within(dialog).getByLabelText("Subject (if known)"), {
      target: { value: "Grade 5 subject" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Back" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Back" }));
    fireEvent.change(within(dialog).getByLabelText("Grade"), {
      target: { value: "7" },
    });
    await continueWizard(dialog);
    expect(within(dialog).getByLabelText("Medium")).toHaveValue("");
    fireEvent.change(within(dialog).getByLabelText("Medium"), {
      target: { value: "Tamil" },
    });
    await continueWizard(dialog);
    expect(within(dialog).getByLabelText("Subject (if known)")).toHaveValue("");
  });

  it.each(["x".repeat(201), "Mathe\u200bmatics", "Maths\u00a0text"])(
    "rejects invalid candidate subject text before any upload request: %j",
    async (subject) => {
      const { requests } = await renderLibrary("admin", { catalogue: [] });
      fireEvent.click(screen.getByRole("button", { name: "Upload material" }));
      const dialog = screen.getByRole("dialog", { name: "Upload material" });
      fireEvent.change(within(dialog).getByLabelText("Grade"), {
        target: { value: "5" },
      });
      await continueWizard(dialog);
      fireEvent.change(within(dialog).getByLabelText("Medium"), {
        target: { value: "Sinhala" },
      });
      await continueWizard(dialog);
      const input = within(dialog).getByLabelText("Subject (if known)");
      expect(input).toHaveAttribute("maxlength", "200");
      fireEvent.change(input, { target: { value: subject } });
      await continueWizard(dialog);
      expect(within(dialog).getByRole("alert")).toHaveTextContent(
        "Use up to 200 characters for the subject, without hidden formatting.",
      );
      expect(input).toBeInTheDocument();
      expect(requests.some((request) => request.method === "POST")).toBe(false);
    },
  );

  it("checks optional intake years and curriculum labels before showing the PDF step", async () => {
    const { requests } = await renderLibrary("admin", { catalogue: [] });
    const dialog = await openIntakeWizardAtPdfStep({ year: "2101" });
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Enter a whole year from 1900 through 2100.",
    );
    expect(within(dialog).queryByLabelText("PDF file")).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Year (if known)"), {
      target: { value: "2023" },
    });
    const curriculum = within(dialog).getByLabelText(
      "Curriculum / edition (if known)",
    );
    expect(curriculum).toHaveAttribute("maxlength", "200");
    fireEvent.change(curriculum, { target: { value: "Edition\u0000" } });
    await continueWizard(dialog);
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Use up to 200 characters for the curriculum / edition, without hidden formatting.",
    );
    expect(requests.some((request) => request.method === "POST")).toBe(false);
    fireEvent.change(curriculum, {
      target: { value: "  Edition from cover  " },
    });
    await continueWizard(dialog);
    expect(within(dialog).getByLabelText("PDF file")).toBeInTheDocument();
  });

  it("clears stale library filters in both state and the immediate post-upload request", async () => {
    const { requests } = await renderLibrary("admin");
    const filters = screen.getByRole("region", { name: "Material filters" });
    for (const [label, value] of [
      ["Search", "missing material"],
      ["Medium", ids.medium],
      ["Material type", "syllabus"],
      ["Status", "ready_for_ai"],
      ["Year", "1999"],
    ]) {
      fireEvent.change(within(filters).getByLabelText(label), {
        target: { value },
      });
    }
    await waitFor(() =>
      expect(
        requests.some((request) => {
          const url = new URL(request.url);
          return (
            url.pathname.endsWith("/materials") &&
            url.searchParams.get("search") === "missing material" &&
            url.searchParams.get("year") === "1999"
          );
        }),
      ).toBe(true),
    );
    const dialog = await openWizardAtPdfStep();
    fireEvent.change(within(dialog).getByLabelText("PDF file"), {
      target: {
        files: [
          new File(["%PDF-1.7\nfresh"], "fresh-paper.pdf", {
            type: "application/pdf",
          }),
        ],
      },
    });
    await continueWizard(dialog);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Upload material" }),
    );
    await screen.findByRole("link", { name: "Open uploaded material" });
    for (const label of [
      "Search",
      "Medium",
      "Material type",
      "Status",
      "Year",
    ]) {
      expect(within(filters).getByLabelText(label)).toHaveProperty("value", "");
    }
    const completedAt = requests.findIndex(
      (request) =>
        request.method === "POST" &&
        new URL(request.url).pathname.endsWith("/complete"),
    );
    const refreshes = requests
      .slice(completedAt + 1)
      .filter(
        (request) =>
          request.method === "GET" &&
          new URL(request.url).pathname.endsWith("/materials"),
      );
    expect(refreshes.length).toBeGreaterThan(0);
    for (const request of refreshes) {
      for (const key of [
        "search",
        "medium_id",
        "material_type",
        "status",
        "year",
      ]) {
        expect(new URL(request.url).searchParams.has(key), request.url).toBe(
          false,
        );
      }
    }
    expect(
      screen.getByRole("region", { name: "Uploaded materials" }),
    ).toHaveTextContent("fresh-paper.pdf");
  });

  it("shows a resumed candidate upload in its own grade instead of the currently selected unassigned list", async () => {
    const file = new File(["%PDF-1.7\nsaved grade"], "saved-grade.pdf", {
      type: "application/pdf",
    });
    const saved: UploadSession = {
      id: ids.uploadSession,
      filename: file.name,
      size_bytes: file.size,
      document_type: "teacher_guide",
      intake_metadata: {
        candidate_grade: 12,
        medium_label: "Sinhala",
        subject_label: "Mathematics",
      },
      status: "uploading",
      next_offset: 0,
      verified_bytes: 0,
      version: 0,
      chunk_size_bytes: 4_194_304,
      deduplicated: false,
      created_at: now,
      updated_at: now,
    };
    localStorage.setItem(
      UPLOAD_CHECKPOINT_KEY,
      JSON.stringify({
        uploadIds: [ids.uploadSession],
        creationUncertain: false,
      }),
    );
    await renderLibrary("admin", {
      catalogue: [],
      savedUpload: saved,
      initialMaterials: [intakeMaterial],
      summaries: [unassignedSummary],
    });
    const overview = screen.getByRole("region", { name: "Materials by grade" });
    fireEvent.click(
      within(overview).getByRole("button", { name: /Unassigned materials/i }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Continue saved upload 1" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Continue upload",
    });
    fireEvent.change(await within(dialog).findByLabelText("Original PDF"), {
      target: { files: [file] },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Resume upload" }),
    );
    await screen.findByRole("link", { name: "Open uploaded material" });
    expect(
      within(overview).getByRole("button", { name: /^Grade 12\b/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("region", { name: "Uploaded materials" }),
    ).toHaveTextContent(file.name);
  });

  it("does not carry a past-paper year into a material type that has no year field", async () => {
    const { requests } = await renderLibrary("admin", { catalogue: [] });
    const dialog = await openIntakeWizardAtPdfStep({
      year: "2025",
      curriculum: "Unverified edition",
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Back" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Back" }));
    fireEvent.change(within(dialog).getByLabelText("Material type"), {
      target: { value: "syllabus" },
    });
    await continueWizard(dialog);
    expect(
      within(dialog).queryByLabelText("Year (if known)"),
    ).not.toBeInTheDocument();
    await continueWizard(dialog);
    fireEvent.change(within(dialog).getByLabelText("PDF file"), {
      target: {
        files: [
          new File(["%PDF-1.7\nchanged type"], "changed-type.pdf", {
            type: "application/pdf",
          }),
        ],
      },
    });
    await continueWizard(dialog);
    expect(
      within(dialog).getByRole("region", { name: "Review upload" }),
    ).not.toHaveTextContent("2025");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Upload material" }),
    );
    await screen.findByRole("link", { name: "Open uploaded material" });
    const create = requests.find(
      (request) =>
        request.method === "POST" &&
        new URL(request.url).pathname.endsWith("/source-uploads"),
    )!;
    expect(await jsonBody(create)).toMatchObject({
      document_type: "syllabus",
      year: null,
      intake_metadata: { year: null },
    });
  });

  it("uses the complete guided sequence, uploads once, and queues reading only for a new PDF", async () => {
    const { requests } = await renderLibrary("admin");
    const dialog = await openWizardAtPdfStep();

    expect(
      within(dialog)
        .getAllByRole("listitem")
        .map((item) => item.textContent?.trim()),
    ).toEqual([
      "Grade",
      "Medium",
      "Subject",
      "Material type",
      "Year or curriculum",
      "PDF",
      "Review",
    ]);

    const file = new File(
      ["%PDF-1.7\nunique"],
      "grade-5-maths-2026-paper.pdf",
      {
        type: "application/pdf",
      },
    );
    fireEvent.change(within(dialog).getByLabelText("PDF file"), {
      target: { files: [file] },
    });
    const form = dialog.querySelector("form");
    if (!form) throw new Error("Upload wizard must render a form");
    fireEvent.submit(form);
    expect(
      requests.filter(
        (request) =>
          request.method === "POST" &&
          new URL(request.url).pathname.endsWith("/source-uploads"),
      ),
    ).toHaveLength(0);
    expect(within(dialog).getByLabelText("PDF file")).toBeInTheDocument();
    await continueWizard(dialog);

    const review = await within(dialog).findByRole("region", {
      name: "Review upload",
    });
    expect(review).toHaveTextContent("Grade 5");
    expect(review).toHaveTextContent("English");
    expect(review).toHaveTextContent("Maths");
    expect(review).toHaveTextContent("Past Paper");
    expect(review).toHaveTextContent("2026");
    expect(review).toHaveTextContent("grade-5-maths-2026-paper.pdf");

    fireEvent.click(
      within(dialog).getByRole("button", { name: "Upload material" }),
    );
    expect(
      await screen.findByText("Material uploaded. Reading the PDF now."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Uploaded materials" }),
    ).toHaveTextContent("grade-5-maths-2026-paper.pdf");

    const uploadRequests = requests.filter(
      (request) =>
        request.method === "POST" &&
        new URL(request.url).pathname.endsWith("/source-uploads"),
    );
    expect(uploadRequests).toHaveLength(1);
    expect(await uploadRequests[0]!.json()).toMatchObject({
      document_type: "past_paper",
      year: 2026,
      curriculum_version_id: ids.curriculum,
      filename: file.name,
      size_bytes: file.size,
      unit_id: null,
      lesson_id: null,
    });
    const chunks = requests.filter((request) => request.method === "PUT");
    expect(chunks).toHaveLength(1);
    expect(chunks[0].headers.get("Content-Type")).toBe(
      "application/octet-stream",
    );
    expect(chunks[0].headers.get("X-Chunk-SHA256")).toMatch(/^[a-f0-9]{64}$/);
    expect(new URL(chunks[0].url).searchParams.get("offset")).toBe("0");
    expect(new Uint8Array(await chunks[0].arrayBuffer())).toEqual(
      new Uint8Array(await file.slice().arrayBuffer()),
    );
    expect(
      requests.filter(
        (request) =>
          request.method === "POST" && request.url.endsWith("/complete"),
      ),
    ).toHaveLength(1);
    expect(
      requests.some(
        (request) =>
          request.method === "GET" &&
          request.url.endsWith(`/source-read-jobs/${ids.readJob}`),
      ),
    ).toBe(true);
    expect(
      requests.some(
        (request) =>
          request.method === "POST" &&
          /\/(extract|read)$/.test(new URL(request.url).pathname),
      ),
    ).toBe(false);
    expect(JSON.parse(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)!)).toEqual({
      uploadIds: [],
      creationUncertain: false,
    });
  });

  it("offers curriculum, unit, and lesson choices when the material type needs them", async () => {
    await renderLibrary("admin");
    fireEvent.click(screen.getByRole("button", { name: "Upload material" }));
    const dialog = screen.getByRole("dialog", { name: "Upload material" });
    fireEvent.change(within(dialog).getByLabelText("Grade"), {
      target: { value: "5" },
    });
    await continueWizard(dialog);
    fireEvent.change(within(dialog).getByLabelText("Medium"), {
      target: { value: ids.medium },
    });
    await continueWizard(dialog);
    fireEvent.change(within(dialog).getByLabelText("Subject"), {
      target: { value: ids.mathsSubject },
    });
    await continueWizard(dialog);
    fireEvent.change(within(dialog).getByLabelText("Material type"), {
      target: { value: "syllabus" },
    });
    await continueWizard(dialog);

    fireEvent.change(within(dialog).getByLabelText("Curriculum version"), {
      target: { value: ids.curriculum },
    });
    expect(
      await within(dialog).findByRole("option", { name: "Numbers" }),
    ).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Unit (optional)"), {
      target: { value: ids.unit },
    });
    expect(
      within(dialog).getByRole("option", { name: "Fractions" }),
    ).toBeInTheDocument();
  });

  it("stops an exact server-identified duplicate, links the item, and never queues it again", async () => {
    const { requests } = await renderLibrary("admin", { exactDuplicate: true });
    const dialog = await openWizardAtPdfStep();
    const duplicate = new File(["%PDF-1.7\nduplicate"], materials[0]!.title, {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("PDF file"), {
      target: { files: [duplicate] },
    });
    await continueWizard(dialog);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Upload material" }),
    );

    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(
      "This exact PDF is already in Materials. No new copy was uploaded.",
    );
    expect(alert).toHaveTextContent("grade-5-maths-syllabus.pdf");
    expect(
      within(alert).getByRole("link", { name: "View existing material" }),
    ).toHaveAttribute("href", `/admin/materials/${ids.syllabus}`);
    expect(
      within(dialog).queryByRole("button", { name: "Upload material" }),
    ).not.toBeInTheDocument();
    expect(
      requests.filter(
        (request) =>
          request.method === "POST" &&
          new URL(request.url).pathname.includes("/extract"),
      ),
    ).toHaveLength(0);
  });

  it("requires a bounded explicit removal reason and uses CAS for remove and restore", async () => {
    const { requests } = await renderLibrary("admin");
    await chooseGradeFiveMaths();

    fireEvent.click(
      screen.getByRole("button", {
        name: "Remove from use: grade-5-maths-syllabus.pdf",
      }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Remove grade-5-maths-syllabus.pdf from use",
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Remove from use" }),
    );
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Enter a reason",
    );
    fireEvent.change(within(dialog).getByLabelText("Reason"), {
      target: { value: "Uploaded to the wrong grade" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Remove from use" }),
    );

    expect(await screen.findByText("Removed from AI use.")).toBeInTheDocument();
    const remove = requests.find(
      (request) =>
        request.method === "POST" &&
        new URL(request.url).pathname.endsWith(
          `/materials/${ids.syllabus}/remove-from-use`,
        ),
    );
    expect(remove).toBeDefined();
    expect(await jsonBody(remove!)).toEqual({
      expected_version: 1,
      reason: "Uploaded to the wrong grade",
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Restore: grade-5-maths-syllabus.pdf",
      }),
    );
    expect(await screen.findByText("Restored for AI use.")).toBeInTheDocument();
    const restore = requests.find(
      (request) =>
        request.method === "POST" &&
        new URL(request.url).pathname.endsWith(
          `/materials/${ids.syllabus}/restore`,
        ),
    );
    expect(await jsonBody(restore!)).toEqual({ expected_version: 2 });
  });

  it("surfaces a stale restore as an error and keeps the removed material recoverable", async () => {
    await renderLibrary("admin", { restoreConflict: true });
    await chooseGradeFiveMaths();

    fireEvent.click(
      screen.getByRole("button", {
        name: "Restore: grade-5-maths-2024-answers.pdf",
      }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "changed in another session",
    );
    expect(
      screen.getByRole("button", {
        name: "Restore: grade-5-maths-2024-answers.pdf",
      }),
    ).toBeInTheDocument();
  });

  it("keeps scope choices on a trusted/downstream conflict and explains the safe recovery", async () => {
    const { requests } = await renderLibrary("admin", { scopeConflict: true });
    await chooseGradeFiveMaths();

    expect(
      screen.queryByRole("button", {
        name: "Edit metadata: grade-5-maths-syllabus.pdf",
      }),
    ).not.toBeInTheDocument();
    const syllabus = screen
      .getByText("grade-5-maths-syllabus.pdf")
      .closest("article");
    expect(syllabus).toHaveTextContent(
      "Remove from use before assigning a corrected version",
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: "Edit metadata: grade-5-maths-teacher-guide.pdf",
      }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Edit grade-5-maths-teacher-guide.pdf",
    });
    await chooseScopeAssignment(dialog);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save changes" }),
    );

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Remove it from use instead",
    );
    expect(within(dialog).getByLabelText("Curriculum version")).toHaveValue(
      ids.curriculumEleven,
    );
    const correction = requests.find(
      (request) =>
        request.method === "PATCH" &&
        new URL(request.url).pathname.endsWith(`/materials/${ids.guide}/scope`),
    );
    expect(await jsonBody(correction!)).toEqual({
      confirm_intake_metadata: false,
      curriculum_version_id: ids.curriculumEleven,
      expected_version: 1,
      lesson_id: null,
      unit_id: null,
    });
  });

  it("recovers an acknowledged lost-create request on remount without creating a second session", async () => {
    const view = await renderLibrary("admin", { throwOnUpload: true });
    const dialog = await openWizardAtPdfStep();
    const file = new File(["%PDF-1.7\nreload"], "reload.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("PDF file"), {
      target: { files: [file] },
    });
    await continueWizard(dialog);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Upload material" }),
    );
    await within(dialog).findByRole("alert");
    const create = (await view.requests
      .find((request) => request.method === "POST")!
      .clone()
      .json()) as UploadCreate;
    view.unmount();
    render(<MaterialsLibrary role="admin" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Continue saved upload 1" }),
    );
    const resumed = screen.getByRole("dialog", { name: "Continue upload" });
    fireEvent.change(await within(resumed).findByLabelText("Original PDF"), {
      target: { files: [file] },
    });
    fireEvent.click(
      within(resumed).getByRole("button", { name: "Resume upload" }),
    );
    await screen.findByText("Material uploaded. Reading the PDF now.");
    expect(
      view.requests.some(
        (request) =>
          request.method === "GET" &&
          request.url.endsWith(`/by-request/${create.request_id}`),
      ),
    ).toBe(true);
    expect(
      view.requests.filter(
        (request) =>
          request.method === "POST" && request.url.endsWith("/source-uploads"),
      ),
    ).toHaveLength(1);
  });

  it("retains a 404 request identity and reopens the wizard with that same key, never a replacement key", async () => {
    localStorage.setItem(
      UPLOAD_CHECKPOINT_KEY,
      JSON.stringify({
        uploadIds: [],
        requestIds: [ids.uploadRequest],
        creationUncertain: true,
      }),
    );
    const { requests } = await renderLibrary("admin");
    const interrupted = await screen.findByRole("button", {
      name: "Continue interrupted upload 1",
    });
    await waitFor(() => expect(interrupted).toBeEnabled());
    fireEvent.click(interrupted);
    const dialog = screen.getByRole("dialog", {
      name: "Continue interrupted upload",
    });
    await openWizardAtPdfStep(dialog);
    fireEvent.change(within(dialog).getByLabelText("PDF file"), {
      target: {
        files: [
          new File(["%PDF-1.7\nretry"], "same-request.pdf", {
            type: "application/pdf",
          }),
        ],
      },
    });
    await continueWizard(dialog);
    expect(requests.every((request) => request.method === "GET")).toBe(true);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Continue upload" }),
    );
    await screen.findByText("Material uploaded. Reading the PDF now.");
    const creates = requests.filter(
      (request) =>
        request.method === "POST" && request.url.endsWith("/source-uploads"),
    );
    expect(creates).toHaveLength(1);
    expect(await creates[0].json()).toMatchObject({
      request_id: ids.uploadRequest,
      filename: "same-request.pdf",
      year: 2026,
    });
  });

  it("keeps pending request IDs through a recovery failure and offers an explicit refresh", async () => {
    localStorage.setItem(
      UPLOAD_CHECKPOINT_KEY,
      JSON.stringify({
        uploadIds: [],
        requestIds: [ids.uploadRequest],
        creationUncertain: true,
      }),
    );
    const options: FixtureOptions = { lookupStatus: 503 };
    const { requests } = await renderLibrary("admin", options);
    await screen.findByRole("alert");
    expect(
      screen.getByRole("button", { name: "Upload material" }),
    ).toBeDisabled();
    options.lookupStatus = undefined;
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh saved upload progress" }),
    );
    await waitFor(() =>
      expect(
        requests.filter((request) => request.url.includes("/by-request/"))
          .length,
      ).toBe(2),
    );
    expect(
      JSON.parse(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)!),
    ).toMatchObject({
      requestIds: [ids.uploadRequest],
      creationUncertain: true,
    });
    expect(requests.every((request) => request.method === "GET")).toBe(true);
  });

  it("retains the safe manual guard only for legacy unkeyed create attempts", async () => {
    localStorage.setItem(
      UPLOAD_CHECKPOINT_KEY,
      JSON.stringify({ uploadIds: [], creationUncertain: true }),
    );
    const { requests } = await renderLibrary("admin");
    expect(
      await screen.findByText(/ask an administrator to recover the upload/),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Upload material" }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Continue interrupted upload 1" }),
    ).not.toBeInTheDocument();
    expect(requests.every((request) => request.method === "GET")).toBe(true);
  });

  it("preserves reviewed choices but prevents duplicate creation after an ambiguous lost create response", async () => {
    const { requests } = await renderLibrary("admin", { throwOnUpload: true });
    const dialog = await openWizardAtPdfStep();
    const file = new File(["%PDF-1.7\nretry"], "retry-this-paper.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("PDF file"), {
      target: { files: [file] },
    });
    await continueWizard(dialog);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Upload material" }),
    );

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Do not start it again",
    );
    expect(
      within(dialog).getByRole("region", { name: "Review upload" }),
    ).toHaveTextContent("retry-this-paper.pdf");
    const first = requests.find((request) => request.method === "POST")!;
    const body = (await first.clone().json()) as UploadCreate;
    expect(body.request_id).toMatch(/^[a-f0-9-]{36}$/);
    expect(
      requests.filter((request) => request.method === "POST"),
    ).toHaveLength(1);
    expect(JSON.parse(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)!)).toEqual({
      uploadIds: [],
      requestIds: [body.request_id],
      creationUncertain: true,
    });
    const retry = within(dialog).getByRole("button", {
      name: "Continue upload",
    });
    expect(retry).toBeEnabled();
    fireEvent.click(retry);
    await screen.findByText("Material uploaded. Reading the PDF now.");
    const creates = requests.filter(
      (request) =>
        request.method === "POST" && request.url.endsWith("/source-uploads"),
    );
    expect(creates).toHaveLength(2);
    expect(await creates[1].json()).toEqual(body);
  });

  it("continues an interrupted chunk from the same saved session without recreating or changing reviewed metadata", async () => {
    const { requests } = await renderLibrary("admin", { interruptChunk: true });
    const dialog = await openWizardAtPdfStep();
    const file = new File(["%PDF-1.7\ncheckpoint"], "checkpoint.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("PDF file"), {
      target: { files: [file] },
    });
    await continueWizard(dialog);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Upload material" }),
    );
    await within(dialog).findByRole("alert");
    expect(
      within(dialog).getByRole("region", { name: "Review upload" }),
    ).toHaveTextContent("checkpoint.pdf");
    expect(JSON.parse(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)!)).toEqual({
      uploadIds: [ids.uploadSession],
      creationUncertain: false,
    });
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Continue upload",
      }),
    );
    await screen.findByText("Material uploaded. Reading the PDF now.");
    expect(
      requests.filter(
        (request) =>
          request.method === "POST" && request.url.endsWith("/source-uploads"),
      ),
    ).toHaveLength(1);
    expect(
      requests.some(
        (request) =>
          request.method === "POST" &&
          /\/(read|extract|trust)$/.test(request.url),
      ),
    ).toBe(false);
  });

  it("pauses an in-flight chunk, keeps its checkpoint on close, and reconciles before resuming", async () => {
    const { requests } = await renderLibrary("admin", { pauseChunk: true });
    let dialog = await openWizardAtPdfStep();
    const file = new File(["%PDF-1.7\npaused"], "paused.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("PDF file"), {
      target: { files: [file] },
    });
    await continueWizard(dialog);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Upload material" }),
    );
    await waitFor(() =>
      expect(requests.some((request) => request.method === "PUT")).toBe(true),
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Pause upload" }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Upload paused",
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(JSON.parse(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)!)).toEqual({
      uploadIds: [ids.uploadSession],
      creationUncertain: false,
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Continue saved upload 1" }),
    );
    dialog = await screen.findByRole("dialog", { name: "Continue upload" });
    fireEvent.change(await within(dialog).findByLabelText("Original PDF"), {
      target: { files: [file] },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Resume upload" }),
    );
    await screen.findByText("Material uploaded. Reading the PDF now.");
    expect(requests.filter((request) => request.method === "PUT")).toHaveLength(
      1,
    );
    expect(
      requests.filter(
        (request) =>
          request.method === "POST" && request.url.endsWith("/source-uploads"),
      ),
    ).toHaveLength(1);
  });

  it.each([false, true])(
    "reselects and verifies a saved PDF after reload, rejecting matching-name impostors (%s)",
    async (mismatch) => {
      const original = new File(["%PDF-1.7\noriginal"], "saved.pdf", {
        type: "application/pdf",
        lastModified: 123,
      });
      const digest = await webcrypto.subtle.digest(
        "SHA-256",
        await original.slice().arrayBuffer(),
      );
      const checksum = [...new Uint8Array(digest)]
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("");
      const saved: UploadSession = {
        id: ids.uploadSession,
        filename: original.name,
        size_bytes: original.size,
        document_type: "past_paper",
        intake_metadata: { year: 2025, candidate_grade: 5 },
        status: "uploading",
        next_offset: original.size,
        verified_bytes: 0,
        version: 3,
        chunk_size_bytes: 4_194_304,
        deduplicated: false,
        created_at: now,
        updated_at: now,
      };
      localStorage.setItem(
        UPLOAD_CHECKPOINT_KEY,
        JSON.stringify({
          uploadIds: [ids.uploadSession],
          creationUncertain: false,
        }),
      );
      const { requests } = await renderLibrary("admin", {
        savedUpload: saved,
        savedChecksum: checksum,
      });
      fireEvent.click(
        await screen.findByRole("button", { name: "Continue saved upload 1" }),
      );
      const dialog = await screen.findByRole("dialog", {
        name: "Continue upload",
      });
      await within(dialog).findByText("saved.pdf");
      expect(
        within(dialog).getByRole("button", { name: "Resume upload" }),
      ).toBeDisabled();
      const selected = mismatch
        ? new File(["%PDF-1.7\nreplaced"], original.name, {
            type: "application/pdf",
            lastModified: 123,
          })
        : original;
      expect(selected.size).toBe(original.size);
      fireEvent.change(within(dialog).getByLabelText("Original PDF"), {
        target: { files: [selected] },
      });
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Resume upload" }),
      );
      if (mismatch) {
        expect(await within(dialog).findByRole("alert")).toHaveTextContent(
          "does not match the saved upload",
        );
        expect(requests.every((request) => request.method === "GET")).toBe(
          true,
        );
      } else {
        await screen.findByText("Material uploaded. Reading the PDF now.");
        expect(
          requests.filter((request) => request.method !== "GET"),
        ).toHaveLength(1);
        expect(
          requests.find((request) => request.method === "POST")?.url,
        ).toMatch(/\/complete$/);
      }
      expect(
        requests.some(
          (request) =>
            request.method === "GET" &&
            new URL(request.url).pathname.endsWith("/chunks") &&
            new URL(request.url).searchParams.get("limit") === "64",
        ),
      ).toBe(true);
    },
  );

  it.each([
    [409, /capacity/],
    [429, /12 seconds/],
    [401, /expired/],
    [403, /permission/],
  ] as const)(
    "keeps review choices and displays a teacher-friendly %s upload error",
    async (status, message) => {
      const { requests } = await renderLibrary("admin", {
        uploadStatus: status,
      });
      const dialog = await openWizardAtPdfStep();
      fireEvent.change(within(dialog).getByLabelText("PDF file"), {
        target: {
          files: [
            new File(["%PDF-1.7\ncapacity"], "capacity.pdf", {
              type: "application/pdf",
            }),
          ],
        },
      });
      await continueWizard(dialog);
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Upload material" }),
      );
      expect(await within(dialog).findByRole("alert")).toHaveTextContent(
        message,
      );
      expect(
        within(dialog).getByRole("region", { name: "Review upload" }),
      ).toHaveTextContent("capacity.pdf");
      expect(
        requests.filter((request) => request.method === "POST"),
      ).toHaveLength(1);
      if (status === 429)
        expect(
          within(dialog).getByRole("button", { name: "Continue upload" }),
        ).toBeDisabled();
    },
  );

  it("keeps reviewer access read-only", async () => {
    await renderLibrary("reviewer");
    await chooseGradeFiveMaths();
    expect(
      screen.queryByRole("button", { name: "Upload material" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("Reviewer access is read-only."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Remove from use:/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Edit metadata:/ }),
    ).not.toBeInTheDocument();
  });

  it("renders permission denied, recoverable error, and empty states explicitly", async () => {
    await renderLibrary("reviewer", { workspaceStatus: 403 });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Materials access required",
    );
  });

  it("shows an empty library after selecting a grade without materials", async () => {
    await renderLibrary("reviewer");
    const overview = screen.getByRole("region", { name: "Materials by grade" });
    fireEvent.click(within(overview).getByRole("button", { name: /Grade 2/i }));
    expect(
      await screen.findByText("No materials match this grade and subject."),
    ).toBeInTheDocument();
  });

  it("keeps checksums and source identifiers in collapsed technical details", async () => {
    await renderLibrary("reviewer");
    await chooseGradeFiveMaths();

    const filename = screen.getByText("grade-5-maths-syllabus.pdf");
    const material = filename.closest("article");
    if (!material)
      throw new Error("Each material must have a readable article");
    const summary = within(material).getByText("Technical details", {
      selector: "summary",
    });
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
