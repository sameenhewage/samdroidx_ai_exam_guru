import type { components } from "@exam-guru/api-client";

import type { ReviewLanguage } from "@/lib/review-language";

export type MaterialUnitList =
  components["schemas"]["MaterialKnowledgeUnitsResponse"];
export type MaterialUnitSummary =
  components["schemas"]["MaterialKnowledgeUnitSummary"];
export type MaterialUnitWorkspace =
  components["schemas"]["MaterialKnowledgeUnitWorkspace"];
export type IndexingStatus =
  components["schemas"]["MaterialKnowledgeIndexingStatus"];
export type IndexingRetry =
  components["schemas"]["MaterialKnowledgeIndexRetryRequest"];
export type MappingRequest = components["schemas"]["KnowledgeReviewRequest"];
export type KnowledgeWorkspace =
  components["schemas"]["KnowledgeUnitWorkspace"];
export type Material = components["schemas"]["MaterialListItemResponse"];
export type CurriculumUnit = components["schemas"]["CurriculumUnitResponse"];
export type Lesson = components["schemas"]["CurriculumLessonResponse"];
export type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
export type Choices = {
  units: CurriculumUnit[];
  lessons: Lesson[];
  nodes: TaxonomyNode[];
};
export type Problem =
  | "expired"
  | "denied"
  | "missing"
  | "changed"
  | "invalid"
  | "unavailable"
  | "failed";
export const sectionLimit = 20;

export function problemFor(status: number): Problem {
  switch (status) {
    case 401:
      return "expired";
    case 403:
      return "denied";
    case 404:
      return "missing";
    case 409:
      return "changed";
    case 422:
      return "invalid";
    case 503:
      return "unavailable";
    default:
      return "failed";
  }
}

function nonnegative(value: number) {
  return Number.isSafeInteger(value) && value >= 0;
}
export function validList(
  value: MaterialUnitList | undefined,
  documentId: string,
  offset: number,
): value is MaterialUnitList {
  return (
    !!value &&
    value.document_id === documentId &&
    value.limit === sectionLimit &&
    value.offset === offset &&
    typeof value.source_current === "boolean" &&
    nonnegative(value.total) &&
    Array.isArray(value.items) &&
    value.items.length <= sectionLimit &&
    new Set(value.items.map((item: MaterialUnitSummary) => item.unit_id))
      .size === value.items.length &&
    value.items.every(
      (item: MaterialUnitSummary) =>
        !!item.unit_id &&
        nonnegative(item.sequence) &&
        nonnegative(item.page_number) &&
        item.page_number > 0 &&
        typeof item.has_projection === "boolean" &&
        !!item.indexing &&
        typeof item.indexing === "object",
    )
  );
}
export function validWorkspace(
  value: MaterialUnitWorkspace | undefined,
  documentId: string,
  summary: MaterialUnitSummary,
  curriculumId: string,
): value is MaterialUnitWorkspace {
  const unit = value?.workspace?.unit;
  return (
    !!unit &&
    unit.id === summary.unit_id &&
    unit.source.document_id === documentId &&
    unit.scope.document_id === documentId &&
    unit.scope.curriculum_version_id === curriculumId &&
    unit.source.page_number === summary.page_number &&
    unit.sequence === summary.sequence &&
    typeof value.workspace.source_current === "boolean" &&
    typeof value.workspace.eligible === "boolean" &&
    !!value.indexing
  );
}

export function reviewedChoices(
  curriculumId: string,
  units: CurriculumUnit[],
  lessons: Lesson[],
  nodes: TaxonomyNode[],
): Choices {
  const currentUnits = units
    .filter(
      (unit) =>
        unit.curriculum_version_id === curriculumId && unit.active === true,
    )
    .toSorted((a, b) => a.ordinal - b.ordinal);
  const levels = [
    "competency",
    "skill",
    "sub_skill",
    "learning_concept",
  ] as const;
  const currentNodes: TaxonomyNode[] = [];
  for (const [index, level] of levels.entries()) {
    currentNodes.push(
      ...nodes.filter(
        (node) =>
          node.curriculum_version_id === curriculumId &&
          node.active === true &&
          node.review_state === "reviewed" &&
          node.level === level &&
          (index === 0
            ? node.parent_id === null
            : currentNodes.some(
                (parent) =>
                  parent.id === node.parent_id &&
                  parent.level === levels[index - 1],
              )),
      ),
    );
  }
  return {
    units: currentUnits,
    lessons: lessons
      .filter(
        (lesson) =>
          lesson.curriculum_version_id === curriculumId &&
          lesson.active === true &&
          currentUnits.some((unit) => unit.id === lesson.unit_id),
      )
      .toSorted((a, b) => a.ordinal - b.ordinal),
    nodes: currentNodes,
  };
}

export function mappingFor(workspace: KnowledgeWorkspace): MappingRequest {
  const review =
    workspace.review?.state === "reviewed" ? workspace.review : null;
  return {
    state: "reviewed",
    confirmed_mapping: false,
    expected_version: workspace.review?.version ?? 0,
    reason: "",
    curriculum_unit_id:
      workspace.unit.scope.curriculum_unit_id ??
      review?.curriculum_unit_id ??
      null,
    lesson_id: workspace.unit.scope.lesson_id ?? review?.lesson_id ?? null,
    competency_id: review?.competency_id ?? null,
    skill_id: review?.skill_id ?? null,
    sub_skill_id: review?.sub_skill_id ?? null,
    learning_concept_id: review?.learning_concept_id ?? null,
  };
}
export function validReason(value: string) {
  return value.trim().length > 0 && value.length <= 2000;
}
export function validMapping(
  request: MappingRequest,
  workspace: KnowledgeWorkspace,
  choices: Choices,
) {
  const scope = workspace.unit.scope;
  if (
    scope.curriculum_unit_id &&
    scope.curriculum_unit_id !== request.curriculum_unit_id
  )
    return false;
  if (scope.lesson_id && scope.lesson_id !== request.lesson_id) return false;
  if (
    request.curriculum_unit_id &&
    !choices.units.some((unit) => unit.id === request.curriculum_unit_id)
  )
    return false;
  if (
    request.lesson_id &&
    !choices.lessons.some(
      (lesson) =>
        lesson.id === request.lesson_id &&
        lesson.unit_id === request.curriculum_unit_id,
    )
  )
    return false;
  const path = [
    request.competency_id,
    request.skill_id,
    request.sub_skill_id,
    request.learning_concept_id,
  ];
  const levels = ["competency", "skill", "sub_skill", "learning_concept"];
  return (
    !!request.competency_id &&
    path.every(
      (id, index) =>
        !id ||
        choices.nodes.some(
          (node) =>
            node.id === id &&
            node.level === levels[index] &&
            node.parent_id === (index === 0 ? null : path[index - 1]),
        ),
    ) &&
    validReason(request.reason)
  );
}
export function workspaceVersion(value: MaterialUnitWorkspace) {
  const unit = value.workspace.unit;
  return JSON.stringify([
    unit.id,
    unit.source,
    unit.scope,
    unit.trusted_page_id,
    unit.trusted_revision,
    unit.trusted_fingerprint,
    value.workspace.review?.id,
    value.workspace.review?.version,
    value.workspace.source_current,
    value.indexing.intent_id,
    value.indexing.version,
    ["configuration_changed", "superseded"].includes(value.indexing.status),
  ]);
}
export function hasIntent(status: IndexingStatus) {
  return (
    !!status.intent_id &&
    Number.isSafeInteger(status.version) &&
    status.version !== null &&
    status.version >= 0
  );
}
export function shouldPoll(status: IndexingStatus) {
  return (
    status.ready === false && ["pending", "queued"].includes(status.status)
  );
}
export function readiness(
  status: IndexingStatus,
  hasProjection: boolean,
  sourceCurrent: boolean,
  eligible: boolean,
) {
  if (!hasProjection) return "not_searchable";
  if (!sourceCurrent) return "superseded";
  if (status.ready === true)
    return status.status === "ready" && eligible ? "ready" : "inconsistent";
  if (status.ready !== false || status.status === "ready")
    return "inconsistent";
  switch (status.status) {
    case "not_requested":
    case "waiting_configuration":
    case "pending":
    case "queued":
    case "needs_attention":
    case "superseded":
    case "not_searchable":
    case "configuration_changed":
      return status.status;
    default:
      return "inconsistent";
  }
}
export function materialLanguage(material: Material | null): ReviewLanguage {
  const candidate = material?.metadata_candidate;
  const intake =
    candidate?.is_current === true
      ? candidate.metadata
      : material?.intake_metadata;
  const hint = material?.medium ?? intake?.medium_label ?? "";
  return /^(si|sin|sinhala|සිංහල)(?:[-_ ]|$)/i.test(hint) ? "si" : "en";
}
export function unitLanguage(
  workspace: KnowledgeWorkspace,
  fallback: ReviewLanguage,
): ReviewLanguage {
  const observation = workspace.unit.observation;
  if (observation.language === "si") return "si";
  if (observation.language === "en" || observation.language === "ta")
    return "en";
  if (
    observation.language === "mixed" &&
    observation.regions.some((region) =>
      /[\u0d80-\u0dff]/u.test(region.exact_text),
    )
  )
    return "si";
  return fallback;
}
