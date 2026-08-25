"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { Button, Form } from "react-aria-components";

import { Badge } from "@/components/ui/badge";

type Exam = components["schemas"]["ExamConfigurationResponse"];
type Medium = components["schemas"]["MediumResponse"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
type TaxonomyTarget = components["schemas"]["TaxonomyTargetRequest"];
type AnalyticsSummary = components["schemas"]["AnalyticsRunSummaryResponse"];
type Blueprint = components["schemas"]["PaperBlueprintResponse"];
type BlueprintSummary = components["schemas"]["PaperBlueprintSummaryResponse"];
type BlueprintRequest = components["schemas"]["BlueprintCreateRequest"];
type QuestionType = components["schemas"]["QuestionType"];
type Difficulty = components["schemas"]["Difficulty"];
type Role = "admin" | "reviewer";

type ApiOutcome = { error?: unknown; response: Response };
type UiError = {
  code: string;
  constraint?: string;
  detail?: string;
  message: string;
  title: string;
};
type CurriculumChoice = { curriculum: Curriculum; exam: Exam; medium: Medium };
type TargetOption = { id: string; label: string; target: TaxonomyTarget };

type SectionDraft = {
  allowedDifficulties: Difficulty[];
  allowedMarks: string;
  allowedQuestionTypes: QuestionType[];
  key: string;
  marks: string;
  questionCount: string;
  retrievalHints: string;
  sectionId: string;
  title: string;
};
type QuestionAllocationDraft = {
  archetypes: string;
  enabled: boolean;
  exactMarks: string;
  exactSlots: string;
  questionType: QuestionType;
};
type DifficultyAllocationDraft = {
  difficulty: Difficulty;
  enabled: boolean;
  exactMarks: string;
  exactSlots: string;
};
type TaxonomyRequirementDraft = {
  allowedSectionKeys: string[];
  baselineEvidence: string;
  baselineScore: string;
  baselineVersion: string;
  generationInstructions: string;
  key: string;
  maximumSlots: string;
  minimumSlots: string;
  retrievalHints: string;
  targetId: string;
};

const LIST_LIMIT = 100;
const MAX_INTEGER = 2_147_483_647;
const questionTypes: readonly QuestionType[] = ["multiple_choice", "short_answer", "structured"];
const difficulties: readonly Difficulty[] = ["easy", "medium", "hard"];
const levelDepth: Record<TaxonomyNode["level"], number> = {
  competency: 1,
  skill: 2,
  sub_skill: 3,
  learning_concept: 4,
};

const fieldClass = "grid gap-1.5 text-sm font-semibold text-slate-700";
const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 outline-none transition focus:border-amber-500 focus:ring-2 focus:ring-amber-200 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const checkboxClass =
  "h-4 w-4 rounded border-slate-400 text-slate-950 outline-none focus-visible:ring-2 focus-visible:ring-amber-500";
const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none transition hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-400 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const dangerButton = `${secondaryButton} border-red-300 text-red-800 hover:bg-red-50`;

function initialSection(key = "section-1", ordinal = 1): SectionDraft {
  return {
    allowedDifficulties: [...difficulties],
    allowedMarks: "2",
    allowedQuestionTypes: [...questionTypes],
    key,
    marks: "2",
    questionCount: "1",
    retrievalHints: ordinal === 1 ? "selection section" : "reviewed curriculum section",
    sectionId: ordinal === 1 ? "A" : `S${ordinal}`,
    title: ordinal === 1 ? "Selection" : `Section ${ordinal}`,
  };
}

function initialRequirement(key = "taxonomy-1"): TaxonomyRequirementDraft {
  return {
    allowedSectionKeys: ["section-1"],
    baselineEvidence: "curriculum:reviewed-taxonomy",
    baselineScore: "100",
    baselineVersion: "syllabus-balanced-v1",
    generationInstructions: "Use an age-appropriate familiar setting.",
    key,
    maximumSlots: "1",
    minimumSlots: "1",
    retrievalHints: "reviewed polygon concepts",
    targetId: "",
  };
}

function detailObject(error: unknown): Record<string, unknown> | null {
  if (!error || typeof error !== "object" || !("detail" in error)) return null;
  const detail = (error as { detail?: unknown }).detail;
  return detail && typeof detail === "object" && !Array.isArray(detail)
    ? (detail as Record<string, unknown>)
    : null;
}

function detailCode(error: unknown): string {
  const detail = detailObject(error);
  return detail && typeof detail.code === "string" ? detail.code : "request_failed";
}

function apiError(error: unknown, status: number, surface: "workspace" | "list" | "detail" | "generate"): UiError {
  const detail = detailObject(error);
  const code = detailCode(error);
  if (status === 401) {
    return {
      code: "authentication_required",
      message: "Your admin session has expired. Sign in again before retrying.",
      title: "Authentication required",
    };
  }
  if (status === 403) {
    return {
      code: "permission_denied",
      message:
        surface === "generate"
          ? "This account cannot generate blueprints. Sign in as an administrator or ask for blueprint:generate access."
          : "This account cannot read blueprint records. Ask an administrator to verify blueprint:read access.",
      title: surface === "generate" ? "Generation permission required" : "Blueprint permission required",
    };
  }
  if (status === 409) {
    if (code === "blueprint_curriculum_inactive") {
      return {
        code,
        message: "The selected curriculum, exam, or medium is no longer active. Reload the workspace and select an active Grade 5 scope.",
        title: "Curriculum scope changed",
      };
    }
    return {
      code,
      message: "An immutable identity or persistence conflict occurred. Reload persisted blueprints before retrying; never alter an existing snapshot.",
      title: "Immutable blueprint conflict",
    };
  }
  if (status === 404) {
    return {
      code,
      message:
        surface === "detail"
          ? "The selected immutable blueprint was not found in this curriculum. Reload the persisted list."
          : "The selected curriculum is no longer available. Reload the workspace.",
      title: surface === "detail" ? "Blueprint not found" : "Curriculum not found",
    };
  }
  if (status === 422 && detail?.impossible === true) {
    return {
      code,
      constraint: typeof detail.constraint === "string" ? detail.constraint : undefined,
      detail: typeof detail.message === "string" ? detail.message : undefined,
      message: "The backend proved that these individually bounded rules cannot form a complete paper. Adjust the cited constraint and retry.",
      title: "Blueprint constraints are impossible",
    };
  }
  if (status === 422) {
    const messages: Record<string, string> = {
      blueprint_analytics_cross_curriculum:
        "The analytics run belongs to a different curriculum. Reload same-curriculum persisted runs.",
      blueprint_analytics_evidence_invalid:
        "Persisted analytics evidence failed fingerprint, leakage, scope, or version validation. Use the baseline or create a valid analytics run.",
      blueprint_analytics_run_not_found:
        "The selected analytics run no longer exists. Reload the same-curriculum run list.",
      blueprint_curriculum_scope_mismatch:
        "The submitted Grade 5 curriculum scope no longer matches the authoritative server scope. Reload before retrying.",
      blueprint_snapshot_limit_exceeded:
        "The bounded blueprint snapshot is too large. Reduce sections, slots, taxonomy targets, or instructions.",
      blueprint_taxonomy_invalid:
        "A selected taxonomy path is no longer active and reviewed, or its hierarchy changed. Reload reviewed taxonomy.",
    };
    return {
      code,
      constraint: typeof detail?.constraint === "string" ? detail.constraint : undefined,
      detail: typeof detail?.message === "string" ? detail.message : undefined,
      message:
        messages[code] ??
        "The authoritative backend rejected the blueprint specification. Review the exact constraints and retry.",
      title: "Blueprint specification rejected",
    };
  }
  if (status === 503) {
    return {
      code,
      message: "The blueprint service is temporarily unavailable. Retry without changing the intended immutable input.",
      title: surface === "workspace" ? "Blueprint workspace unavailable" : "Blueprint service unavailable",
    };
  }
  return {
    code,
    message: "The blueprint request could not be completed. Retry or contact an administrator if it persists.",
    title:
      surface === "workspace"
        ? "Blueprint workspace unavailable"
        : surface === "list"
          ? "Blueprint list unavailable"
          : surface === "detail"
            ? "Blueprint detail unavailable"
            : "Blueprint generation failed",
  };
}

function networkError(surface: "workspace" | "list" | "detail" | "generate"): UiError {
  return {
    code: "network_error",
    message: "The API could not be reached. Check the connection and retry the same deterministic operation.",
    title:
      surface === "workspace"
        ? "Blueprint workspace unavailable"
        : surface === "list"
          ? "Blueprint list unavailable"
          : surface === "detail"
            ? "Blueprint detail unavailable"
            : "Blueprint generation connection failed",
  };
}

function firstFailure(outcomes: readonly ApiOutcome[]): ApiOutcome | undefined {
  return outcomes.find((outcome) => outcome.error !== undefined);
}

function displayEnum(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function displayDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("en", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "UTC",
      }).format(date);
}

function boundedInteger(raw: string, label: string, minimum: number, maximum: number) {
  if (!/^-?\d+$/.test(raw.trim())) {
    return { error: `${label} must be a whole number from ${minimum} through ${maximum}.` };
  }
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    return { error: `${label} must be a whole number from ${minimum} through ${maximum}.` };
  }
  return { value };
}

function textList(raw: string, label: string, maximum: number, required = true) {
  const values = raw
    .split(/[\n,]+/)
    .map((value) => value.trim())
    .filter(Boolean);
  if (required && !values.length) return { error: `${label} must contain at least one item.` };
  if (values.length > maximum) return { error: `${label} must contain no more than ${maximum} items.` };
  if (new Set(values).size !== values.length) return { error: `${label} must not contain duplicate items.` };
  if (values.some((value) => value.length > 1_000)) return { error: `${label} items must be 1,000 characters or fewer.` };
  return { values };
}

function exactSectionComposition(questionCount: number, marks: number, allowedMarks: number[]): boolean {
  let totals = new Set([0]);
  for (let index = 0; index < questionCount; index += 1) {
    const next = new Set<number>();
    for (const total of totals) {
      for (const mark of allowedMarks) if (total + mark <= marks) next.add(total + mark);
    }
    totals = next;
  }
  return totals.has(marks);
}

function targetOptions(nodes: TaxonomyNode[]): TargetOption[] {
  const reviewed = nodes.filter((node) => node.active && node.review_state === "reviewed");
  const byId = new Map(reviewed.map((node) => [node.id, node]));
  const optionFor = (node: TaxonomyNode): TargetOption | null => {
    const path: TaxonomyNode[] = [node];
    let current = node;
    while (current.parent_id) {
      const parent = byId.get(current.parent_id);
      if (!parent || levelDepth[parent.level] !== levelDepth[current.level] - 1) return null;
      path.unshift(parent);
      current = parent;
    }
    if (path[0]?.level !== "competency" || path.length !== levelDepth[node.level]) return null;
    const target: TaxonomyTarget = {
      competency_id: path[0].id,
      skill_id: path.find((item) => item.level === "skill")?.id ?? null,
      sub_skill_id: path.find((item) => item.level === "sub_skill")?.id ?? null,
      learning_concept_id: path.find((item) => item.level === "learning_concept")?.id ?? null,
    };
    return {
      id: node.id,
      label: `${path.map((item) => item.code).join(" / ")} — ${node.title} (${displayEnum(node.level)})`,
      target,
    };
  };
  return reviewed
    .map(optionFor)
    .filter((value): value is TargetOption => value !== null)
    .sort((left, right) => {
      const leftDepth = Object.values(left.target).filter(Boolean).length;
      const rightDepth = Object.values(right.target).filter(Boolean).length;
      return rightDepth - leftDepth || left.label.localeCompare(right.label);
    });
}

function summaryFromBlueprint(value: Blueprint): BlueprintSummary {
  return {
    algorithm_version: value.algorithm_version,
    analytics_run_id: value.analytics_run_id,
    blueprint_id: value.blueprint_id,
    config_version: value.config_version,
    created_at: value.created_at,
    created_by: value.created_by,
    curriculum_version_id: value.curriculum_version_id,
    id: value.id,
    input_fingerprint: value.input_fingerprint,
    paper_code: value.specification.paper_code,
    result_fingerprint: value.result_fingerprint,
    schema_version: value.schema_version,
    seed: value.seed,
    slot_count: value.slot_count,
    specification_fingerprint: value.specification_fingerprint,
    title: value.specification.title,
    total_marks: value.total_marks,
  };
}

function Panel({ children, description, title }: { children: ReactNode; description?: string; title: string }) {
  return (
    <section className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6">
      <header className="border-b border-slate-200 pb-4">
        <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        {description ? <p className="mt-1 text-sm leading-6 text-slate-600">{description}</p> : null}
      </header>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function ErrorPanel({ error, onRetry, retryLabel }: { error: UiError; onRetry?: () => void; retryLabel?: string }) {
  return (
    <section className="rounded-xl border border-red-300 bg-red-50 p-4 text-red-950" role="alert">
      <h3 className="font-semibold">{error.title}</h3>
      <p className="mt-1 text-sm leading-6 text-red-900">{error.message}</p>
      {error.constraint ? (
        <p className="mt-2 break-words font-mono text-xs">
          Constraint: {error.constraint}{error.detail ? ` — ${error.detail}` : ""}
        </p>
      ) : null}
      {onRetry && retryLabel ? (
        <Button className={`${secondaryButton} mt-3 border-red-300`} onPress={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
    </section>
  );
}

function TextInput({ label, maxLength, onChange, required = true, value }: { label: string; maxLength: number; onChange: (value: string) => void; required?: boolean; value: string }) {
  return (
    <label className={fieldClass}>
      {label}
      <input
        className={inputClass}
        maxLength={maxLength}
        onChange={(event) => onChange(event.target.value)}
        required={required}
        type="text"
        value={value}
      />
    </label>
  );
}

function NumberInput({ label, maximum, minimum, onChange, value }: { label: string; maximum: number; minimum: number; onChange: (value: string) => void; value: string }) {
  return (
    <label className={fieldClass}>
      {label}
      <input
        className={inputClass}
        inputMode="numeric"
        max={maximum}
        min={minimum}
        onChange={(event) => onChange(event.target.value)}
        required
        step="1"
        type="number"
        value={value}
      />
    </label>
  );
}

function ListInput({ description, label, onChange, required = true, value }: { description?: string; label: string; onChange: (value: string) => void; required?: boolean; value: string }) {
  return (
    <label className={fieldClass}>
      {label}
      {description ? <span className="font-normal text-slate-500">{description}</span> : null}
      <textarea
        className={`${inputClass} min-h-20 resize-y font-normal`}
        onChange={(event) => onChange(event.target.value)}
        required={required}
        value={value}
      />
    </label>
  );
}

function Toggle({ checked, label, onChange }: { checked: boolean; label: string; onChange: (checked: boolean) => void }) {
  return (
    <label className="flex items-start gap-2 text-sm text-slate-700">
      <input
        checked={checked}
        className={`${checkboxClass} mt-0.5`}
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
      <span>{label}</span>
    </label>
  );
}

function Fingerprint({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-xl border border-slate-200 bg-slate-50 p-3">
      <dt className="text-xs font-semibold text-slate-500">{label}</dt>
      <dd className="mt-1 break-all font-mono text-xs text-slate-900">{value}</dd>
    </div>
  );
}

function IdList({ empty = "None", values }: { empty?: string; values: string[] }) {
  return values.length ? (
    <ul className="grid gap-1 text-sm">
      {values.map((value) => (
        <li className="break-words font-mono text-xs" key={value}>{value}</li>
      ))}
    </ul>
  ) : (
    <span className="text-sm text-slate-500">{empty}</span>
  );
}

function TargetIds({ target }: { target: components["schemas"]["TaxonomyTargetResponse"] }) {
  return (
    <dl className="grid gap-2 text-xs sm:grid-cols-2">
      <div><dt className="font-semibold text-slate-500">Competency</dt><dd className="break-all font-mono">{target.competency_id}</dd></div>
      <div><dt className="font-semibold text-slate-500">Skill</dt><dd className="break-all font-mono">{target.skill_id ?? "—"}</dd></div>
      <div><dt className="font-semibold text-slate-500">Sub-skill</dt><dd className="break-all font-mono">{target.sub_skill_id ?? "—"}</dd></div>
      <div><dt className="font-semibold text-slate-500">Learning concept</dt><dd className="break-all font-mono">{target.learning_concept_id ?? "—"}</dd></div>
    </dl>
  );
}

function AllocationTable({ blueprint }: { blueprint: Blueprint }) {
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <section>
        <h3 className="font-semibold">Question types and archetypes</h3>
        <div className="mt-2 overflow-x-auto rounded-xl border border-slate-200">
          <table className="w-full min-w-[28rem] text-left text-sm">
            <thead className="bg-slate-100 text-xs text-slate-600 uppercase"><tr><th className="px-3 py-2">Type</th><th className="px-3 py-2">Slots</th><th className="px-3 py-2">Marks</th><th className="px-3 py-2">Archetypes</th></tr></thead>
            <tbody className="divide-y divide-slate-200">
              {blueprint.blueprint.question_type_allocations.map((allocation) => (
                <tr key={allocation.question_type}><th className="px-3 py-3 font-medium">{displayEnum(allocation.question_type)}</th><td className="px-3 py-3 font-mono">{allocation.exact_slots}</td><td className="px-3 py-3 font-mono">{allocation.exact_marks ?? "Any exact composition"}</td><td className="px-3 py-3">{allocation.archetypes.join(", ")}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section>
        <h3 className="font-semibold">Difficulty</h3>
        <div className="mt-2 overflow-x-auto rounded-xl border border-slate-200">
          <table className="w-full min-w-[20rem] text-left text-sm">
            <thead className="bg-slate-100 text-xs text-slate-600 uppercase"><tr><th className="px-3 py-2">Difficulty</th><th className="px-3 py-2">Slots</th><th className="px-3 py-2">Marks</th></tr></thead>
            <tbody className="divide-y divide-slate-200">
              {blueprint.blueprint.difficulty_allocations.map((allocation) => (
                <tr key={allocation.difficulty}><th className="px-3 py-3 font-medium">{displayEnum(allocation.difficulty)}</th><td className="px-3 py-3 font-mono">{allocation.exact_slots}</td><td className="px-3 py-3 font-mono">{allocation.exact_marks ?? "Any exact composition"}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function BlueprintDetail({ value }: { value: Blueprint }) {
  const policy = value.specification.generation_policy;
  return (
    <article aria-labelledby="blueprint-detail-heading" className="grid gap-6">
      <header className="rounded-2xl border border-slate-800 bg-slate-950 p-5 text-white shadow-sm sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="font-mono text-xs tracking-[0.16em] text-amber-300 uppercase">Persisted deterministic record</p>
            <h2 className="mt-2 text-2xl font-semibold" id="blueprint-detail-heading">Immutable blueprint snapshot</h2>
            <p className="mt-2 text-sm text-slate-300">{value.specification.paper_code} · {value.specification.title}</p>
          </div>
          <Badge className="border-white/20 bg-white/10 text-white">{value.deduplicated ? "Existing identical snapshot" : "Immutable"}</Badge>
        </div>
        <p className="mt-5 rounded-lg border border-amber-300/30 bg-amber-300/10 p-3 text-sm leading-6 text-amber-100">This persisted blueprint and reviewed taxonomy snapshot are immutable and cannot be edited. Create a new version for every changed input.</p>
      </header>

      <Panel description="Exact paper totals and the optional persisted analytics relationship are part of the immutable input identity." title="Paper identity and linkage">
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Fingerprint label="Record ID" value={value.id} />
          <Fingerprint label="Blueprint ID" value={value.blueprint_id} />
          <Fingerprint label="Created by" value={value.created_by} />
          <Fingerprint label="Created at (UTC)" value={displayDate(value.created_at)} />
        </dl>
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3"><dt className="text-xs font-semibold text-slate-500">Total marks</dt><dd className="mt-1 font-mono text-lg">{value.total_marks}</dd></div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3"><dt className="text-xs font-semibold text-slate-500">Slots</dt><dd className="mt-1 font-mono text-lg">{value.slot_count}</dd></div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3"><dt className="text-xs font-semibold text-slate-500">Seed</dt><dd className="mt-1 font-mono text-lg">{value.seed}</dd></div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3"><dt className="text-xs font-semibold text-slate-500">Analytics linkage</dt><dd className="mt-1 break-all font-mono text-xs">{value.analytics_run_id ?? "None — baseline only"}</dd></div>
        </dl>
      </Panel>

      <Panel title="Versions and fingerprints" description="These identifiers make schema, algorithm, configuration, specification, input and result reproducibility inspectable.">
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Fingerprint label="Schema version" value={value.schema_version} />
          <Fingerprint label="Algorithm version" value={value.algorithm_version} />
          <Fingerprint label="Config version" value={value.config_version} />
          <Fingerprint label="Specification fingerprint" value={value.specification_fingerprint} />
          <Fingerprint label="Input fingerprint" value={value.input_fingerprint} />
          <Fingerprint label="Result fingerprint" value={value.result_fingerprint} />
        </dl>
      </Panel>

      <Panel title="Exact allocations" description="Persisted requested allocations are shown beside the generated section totals.">
        <AllocationTable blueprint={value} />
        <section className="mt-6">
          <h3 className="font-semibold">Exact sections</h3>
          <div className="mt-2 overflow-x-auto rounded-xl border border-slate-200">
            <table className="w-full min-w-[32rem] text-left text-sm">
              <thead className="bg-slate-100 text-xs text-slate-600 uppercase"><tr><th className="px-3 py-2">Section</th><th className="px-3 py-2">Title</th><th className="px-3 py-2">Slots</th><th className="px-3 py-2">Marks</th></tr></thead>
              <tbody className="divide-y divide-slate-200">{value.blueprint.sections.map((section) => <tr key={section.section_id}><th className="px-3 py-3 font-mono">{section.section_id}</th><td className="px-3 py-3">{section.title}</td><td className="px-3 py-3 font-mono">{section.slot_count}</td><td className="px-3 py-3 font-mono">{section.marks}</td></tr>)}</tbody>
            </table>
          </div>
        </section>
      </Panel>

      <Panel title="Taxonomy coverage constraints" description="Every target retains exact minimum/maximum coverage, section scope, priority inputs, retrieval hints and generation instructions.">
        <div className="grid gap-4">
          {value.specification.taxonomy_requirements.map((item, index) => (
            <section className="rounded-xl border border-slate-200 bg-slate-50 p-4" key={`${item.target.competency_id}-${item.target.skill_id}-${index}`}>
              <div className="flex flex-wrap justify-between gap-3"><h3 className="font-semibold">Target {index + 1}</h3><span className="font-mono text-xs">{item.minimum_slots}–{item.maximum_slots ?? "unbounded"} slots</span></div>
              <div className="mt-3"><TargetIds target={item.target} /></div>
              <dl className="mt-4 grid gap-3 sm:grid-cols-2">
                <div><dt className="text-xs font-semibold text-slate-500">Allowed sections</dt><dd className="mt-1 text-sm">{item.allowed_section_ids.join(", ") || "All sections"}</dd></div>
                <div><dt className="text-xs font-semibold text-slate-500">Baseline priority</dt><dd className="mt-1 text-sm">{item.priority.baseline_score} · {item.priority.baseline_version}</dd></div>
                <div><dt className="text-xs font-semibold text-slate-500">Retrieval hints</dt><dd className="mt-1 text-sm">{item.retrieval_query_hints.join(" · ")}</dd></div>
                <div><dt className="text-xs font-semibold text-slate-500">Generation instructions</dt><dd className="mt-1 text-sm">{item.generation_instructions.join(" · ")}</dd></div>
              </dl>
            </section>
          ))}
        </div>
      </Panel>

      <Panel title="Generation policy" description="Every slot inherits this response, answer, retrieval and uniqueness boundary.">
        <dl className="grid gap-4 sm:grid-cols-2">
          <div><dt className="text-xs font-semibold text-slate-500">Response language</dt><dd className="mt-1 font-mono text-sm">{policy.response_language}</dd></div>
          <div><dt className="text-xs font-semibold text-slate-500">Uniqueness</dt><dd className="mt-1 text-sm">Duplicate stems {policy.uniqueness.forbid_duplicate_stems ? "forbidden" : "allowed"}; verbatim sources {policy.uniqueness.forbid_verbatim_sources ? "forbidden" : "allowed"}; similarity ≤ {policy.uniqueness.max_similarity_basis_points} bp; ≥ {policy.uniqueness.minimum_distinct_contexts} distinct context(s).</dd></div>
          <div><dt className="text-xs font-semibold text-slate-500">Instructions</dt><dd className="mt-1 text-sm">{policy.instructions.join(" · ")}</dd></div>
          <div><dt className="text-xs font-semibold text-slate-500">Answer requirements</dt><dd className="mt-1 text-sm">{policy.answer_requirements.join(" · ")}</dd></div>
          <div className="sm:col-span-2"><dt className="text-xs font-semibold text-slate-500">Global retrieval hints</dt><dd className="mt-1 text-sm">{policy.retrieval_query_hints.join(" · ")}</dd></div>
        </dl>
      </Panel>

      <Panel title="Exact generation slots" description="Each deterministic slot is self-contained with exact marks, target, constraints, rationale and evidence.">
        <ol className="grid gap-4">
          {value.blueprint.slots.map((slot) => (
            <li className="rounded-xl border border-slate-300" key={slot.slot_id}>
              <details className="group" open={value.blueprint.slots.length === 1}>
                <summary className="cursor-pointer list-none rounded-xl p-4 outline-none focus-visible:ring-2 focus-visible:ring-amber-500">
                  <div className="flex flex-wrap items-center justify-between gap-3"><div><span className="font-mono text-xs text-slate-500">Slot {slot.ordinal} · {slot.section_id}.{slot.section_ordinal}</span><h3 className="mt-1 font-semibold">{slot.slot_id}</h3></div><div className="flex flex-wrap gap-2"><Badge>{displayEnum(slot.question_type)}</Badge><Badge>{displayEnum(slot.difficulty)}</Badge><Badge>{slot.marks} marks</Badge></div></div>
                </summary>
                <div className="border-t border-slate-200 p-4">
                  <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    <div><dt className="text-xs font-semibold text-slate-500">Section</dt><dd className="mt-1 text-sm">{slot.section_title}</dd></div>
                    <div><dt className="text-xs font-semibold text-slate-500">Archetype</dt><dd className="mt-1 font-mono text-sm">{slot.archetype}</dd></div>
                    <div><dt className="text-xs font-semibold text-slate-500">Priority mode</dt><dd className="mt-1 text-sm">{displayEnum(slot.rationale.priority_mode)}</dd></div>
                    <div><dt className="text-xs font-semibold text-slate-500">Effective priority</dt><dd className="mt-1 font-mono text-sm">{slot.rationale.effective_priority_score}</dd></div>
                  </dl>
                  <p className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm leading-6">{slot.rationale.summary}</p>
                  <section className="mt-5"><h4 className="text-sm font-semibold">Taxonomy target</h4><div className="mt-2"><TargetIds target={slot.taxonomy_target} /></div></section>
                  <section className="mt-5"><h4 className="text-sm font-semibold">Generation constraints</h4><dl className="mt-2 grid gap-3 sm:grid-cols-2"><div><dt className="text-xs font-semibold text-slate-500">Exact requirements</dt><dd className="mt-1 text-sm">{displayEnum(slot.generation_constraints.required_question_type)} · {slot.generation_constraints.required_archetype} · {displayEnum(slot.generation_constraints.required_difficulty)} · {slot.generation_constraints.exact_marks} marks · {slot.generation_constraints.response_language}</dd></div><div><dt className="text-xs font-semibold text-slate-500">Diversity key</dt><dd className="mt-1 break-all font-mono text-xs">{slot.generation_constraints.diversity_key}</dd></div><div><dt className="text-xs font-semibold text-slate-500">Instructions</dt><dd className="mt-1 text-sm">{slot.generation_constraints.instructions.join(" · ")}</dd></div><div><dt className="text-xs font-semibold text-slate-500">Answer requirements</dt><dd className="mt-1 text-sm">{slot.generation_constraints.answer_requirements.join(" · ")}</dd></div><div className="sm:col-span-2"><dt className="text-xs font-semibold text-slate-500">Retrieval hints</dt><dd className="mt-1 text-sm">{slot.generation_constraints.retrieval_query_hints.join(" · ")}</dd></div></dl></section>
                  <section className="mt-5"><h4 className="text-sm font-semibold">Rationale evidence</h4><dl className="mt-2 grid gap-3 sm:grid-cols-2"><div><dt className="text-xs font-semibold text-slate-500">Baseline</dt><dd className="mt-1 text-sm">{slot.evidence.baseline_score} · {slot.evidence.baseline_version}</dd></div><div><dt className="text-xs font-semibold text-slate-500">Forecast/backtest</dt><dd className="mt-1 text-sm">{slot.evidence.forecast_score === null ? "No linked forecast evidence; baseline only" : `${slot.evidence.forecast_score} · ${slot.evidence.forecast_version} · baseline metric ${slot.evidence.baseline_backtest_score} · forecast metric ${slot.evidence.forecast_backtest_score} · minimum improvement ${slot.evidence.minimum_backtest_improvement}`}</dd></div><div className="sm:col-span-2"><dt className="text-xs font-semibold text-slate-500">Evidence references</dt><dd className="mt-1"><IdList values={slot.evidence.evidence_refs} /></dd></div></dl></section>
                </div>
              </details>
            </li>
          ))}
        </ol>
      </Panel>

      <Panel title="Reviewed taxonomy snapshot" description="The active reviewed nodes used by this blueprint were snapshotted at creation time.">
        <div className="overflow-x-auto rounded-xl border border-slate-200">
          <table className="w-full min-w-[48rem] text-left text-sm">
            <thead className="bg-slate-100 text-xs text-slate-600 uppercase"><tr><th className="px-3 py-2">Code and title</th><th className="px-3 py-2">Level</th><th className="px-3 py-2">Node ID</th><th className="px-3 py-2">Reviewed</th><th className="px-3 py-2">Reviewer</th></tr></thead>
            <tbody className="divide-y divide-slate-200">{value.taxonomy_snapshot.map((node) => <tr key={node.id}><th className="px-3 py-3 font-medium"><span>{node.code}</span> — <span>{node.title}</span></th><td className="px-3 py-3">{displayEnum(node.level)}</td><td className="px-3 py-3 break-all font-mono text-xs">{node.id}</td><td className="px-3 py-3">{displayDate(node.reviewed_at)}</td><td className="px-3 py-3 break-all font-mono text-xs">{node.reviewed_by}</td></tr>)}</tbody>
          </table>
        </div>
      </Panel>
    </article>
  );
}

export function BlueprintStudio({ role }: { role: Role }) {
  const api = useMemo(() => createApiClient(globalThis.location?.origin ?? "http://localhost"), []);
  const [exams, setExams] = useState<Exam[]>([]);
  const [media, setMedia] = useState<Medium[]>([]);
  const [curricula, setCurricula] = useState<Curriculum[]>([]);
  const [taxonomy, setTaxonomy] = useState<TaxonomyNode[]>([]);
  const [analyticsRuns, setAnalyticsRuns] = useState<AnalyticsSummary[]>([]);
  const [summaries, setSummaries] = useState<BlueprintSummary[]>([]);
  const [selectedCurriculumId, setSelectedCurriculumId] = useState("");
  const [selectedAnalyticsRunId, setSelectedAnalyticsRunId] = useState("");
  const [selectedBlueprintId, setSelectedBlueprintId] = useState("");
  const [detail, setDetail] = useState<Blueprint | null>(null);

  const [paperCode, setPaperCode] = useState("G5-PRACTICE-01");
  const [paperTitle, setPaperTitle] = useState("Grade 5 Scholarship Practice Paper");
  const [totalMarks, setTotalMarks] = useState("2");
  const [seed, setSeed] = useState("0");
  const [configVersion, setConfigVersion] = useState("grade5-blueprint-config-v1");
  const [sections, setSections] = useState<SectionDraft[]>([initialSection()]);
  const [questionAllocations, setQuestionAllocations] = useState<QuestionAllocationDraft[]>([
    { archetypes: "single_best_answer", enabled: true, exactMarks: "2", exactSlots: "1", questionType: "multiple_choice" },
    { archetypes: "direct_response", enabled: false, exactMarks: "1", exactSlots: "1", questionType: "short_answer" },
    { archetypes: "multi_step_reasoning", enabled: false, exactMarks: "2", exactSlots: "1", questionType: "structured" },
  ]);
  const [difficultyAllocations, setDifficultyAllocations] = useState<DifficultyAllocationDraft[]>([
    { difficulty: "easy", enabled: false, exactMarks: "1", exactSlots: "1" },
    { difficulty: "medium", enabled: true, exactMarks: "2", exactSlots: "1" },
    { difficulty: "hard", enabled: false, exactMarks: "2", exactSlots: "1" },
  ]);
  const [requirements, setRequirements] = useState<TaxonomyRequirementDraft[]>([initialRequirement()]);
  const [responseLanguage, setResponseLanguage] = useState("si");
  const [generationInstructions, setGenerationInstructions] = useState("Use age-appropriate Grade 5 language.");
  const [answerRequirements, setAnswerRequirements] = useState("Provide one unambiguous answer with marking guidance.");
  const [globalRetrievalHints, setGlobalRetrievalHints] = useState("Grade 5 reviewed curriculum");
  const [forbidDuplicateStems, setForbidDuplicateStems] = useState(true);
  const [forbidVerbatimSources, setForbidVerbatimSources] = useState(true);
  const [maxSimilarity, setMaxSimilarity] = useState("8500");
  const [minimumDistinctContexts, setMinimumDistinctContexts] = useState("1");

  const [workspaceLoading, setWorkspaceLoading] = useState(true);
  const [listLoading, setListLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [generateLoading, setGenerateLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<UiError | null>(null);
  const [listError, setListError] = useState<UiError | null>(null);
  const [detailError, setDetailError] = useState<UiError | null>(null);
  const [generateError, setGenerateError] = useState<UiError | null>(null);
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  const [lastRequest, setLastRequest] = useState<BlueprintRequest | null>(null);

  const workspaceRequestId = useRef(0);
  const listRequestId = useRef(0);
  const detailRequestId = useRef(0);
  const generateRequestId = useRef(0);
  const dynamicKey = useRef(2);

  const choices = useMemo(() => {
    const examById = new Map(exams.map((item) => [item.id, item]));
    const mediumById = new Map(media.map((item) => [item.id, item]));
    return curricula.flatMap((item): CurriculumChoice[] => {
      const exam = examById.get(item.exam_configuration_id);
      const medium = mediumById.get(item.medium_id);
      return item.active && exam?.active && exam.grade === 5 && medium?.active
        ? [{ curriculum: item, exam, medium }]
        : [];
    });
  }, [curricula, exams, media]);
  const selectedChoice = choices.find((item) => item.curriculum.id === selectedCurriculumId);
  const reviewedTargets = useMemo(() => targetOptions(taxonomy), [taxonomy]);
  const canGenerate = role === "admin";

  const loadWorkspace = useCallback(async () => {
    const requestId = ++workspaceRequestId.current;
    setWorkspaceLoading(true);
    setWorkspaceError(null);
    try {
      const results = await Promise.all([
        api.GET("/api/v1/admin/exam-configurations"),
        api.GET("/api/v1/admin/media"),
        api.GET("/api/v1/admin/curriculum-versions"),
      ]);
      if (requestId !== workspaceRequestId.current) return;
      const failed = firstFailure(results);
      if (failed?.error) {
        setWorkspaceError(apiError(failed.error, failed.response.status, "workspace"));
        return;
      }
      const nextExams = results[0].data ?? [];
      const nextMedia = results[1].data ?? [];
      const nextCurricula = results[2].data ?? [];
      const examById = new Map(nextExams.map((item) => [item.id, item]));
      const mediumById = new Map(nextMedia.map((item) => [item.id, item]));
      const available = nextCurricula.filter((item) => {
        const exam = examById.get(item.exam_configuration_id);
        const medium = mediumById.get(item.medium_id);
        return item.active && exam?.active && exam.grade === 5 && medium?.active;
      });
      setExams(nextExams);
      setMedia(nextMedia);
      setCurricula(nextCurricula);
      setSelectedCurriculumId((current) => available.some((item) => item.id === current) ? current : (available[0]?.id ?? ""));
    } catch {
      if (requestId === workspaceRequestId.current) setWorkspaceError(networkError("workspace"));
    } finally {
      if (requestId === workspaceRequestId.current) setWorkspaceLoading(false);
    }
  }, [api]);

  const loadCurriculum = useCallback(async (curriculumId: string) => {
    const requestId = ++listRequestId.current;
    setListLoading(true);
    setListError(null);
    setDetailError(null);
    setNotice("");
    try {
      const path = { curriculum_version_id: curriculumId };
      const results = await Promise.all([
        api.GET("/api/v1/admin/curricula/{curriculum_version_id}/taxonomy/nodes", { params: { path } }),
        api.GET("/api/v1/admin/curricula/{curriculum_version_id}/analytics/runs", { params: { path, query: { limit: LIST_LIMIT, offset: 0 } } }),
        api.GET("/api/v1/admin/curricula/{curriculum_version_id}/blueprints", { params: { path, query: { limit: LIST_LIMIT, offset: 0 } } }),
      ]);
      if (requestId !== listRequestId.current) return;
      const failed = firstFailure(results);
      if (failed?.error) {
        setListError(apiError(failed.error, failed.response.status, "list"));
        return;
      }
      const nextSummaries = results[2].data ?? [];
      const nextTaxonomy = results[0].data ?? [];
      const nextTargets = targetOptions(nextTaxonomy);
      setTaxonomy(nextTaxonomy);
      setRequirements((current) =>
        current.map((item, index) =>
          item.targetId
            ? item
            : {
                ...item,
                targetId: nextTargets[Math.min(index, nextTargets.length - 1)]?.id ?? "",
              },
        ),
      );
      setAnalyticsRuns(results[1].data ?? []);
      setSummaries(nextSummaries);
      setSelectedAnalyticsRunId("");
      setSelectedBlueprintId((current) => nextSummaries.some((item) => item.id === current) ? current : (nextSummaries[0]?.id ?? ""));
      if (!nextSummaries.length) setDetail(null);
    } catch {
      if (requestId === listRequestId.current) setListError(networkError("list"));
    } finally {
      if (requestId === listRequestId.current) setListLoading(false);
    }
  }, [api]);

  const loadDetail = useCallback(async (curriculumId: string, blueprintId: string) => {
    const requestId = ++detailRequestId.current;
    setDetailLoading(true);
    setDetailError(null);
    try {
      const response = await api.GET("/api/v1/admin/curricula/{curriculum_version_id}/blueprints/{paper_blueprint_id}", {
        params: { path: { curriculum_version_id: curriculumId, paper_blueprint_id: blueprintId } },
      });
      if (requestId !== detailRequestId.current) return;
      if (response.error) {
        setDetailError(apiError(response.error, response.response.status, "detail"));
        setDetail(null);
        return;
      }
      setDetail((response.data as Blueprint | undefined) ?? null);
    } catch {
      if (requestId === detailRequestId.current) {
        setDetailError(networkError("detail"));
        setDetail(null);
      }
    } finally {
      if (requestId === detailRequestId.current) setDetailLoading(false);
    }
  }, [api]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadWorkspace(), 0);
    return () => window.clearTimeout(timeout);
  }, [loadWorkspace]);

  useEffect(() => {
    if (!selectedCurriculumId) return;
    const timeout = window.setTimeout(() => void loadCurriculum(selectedCurriculumId), 0);
    return () => window.clearTimeout(timeout);
  }, [loadCurriculum, selectedCurriculumId]);

  useEffect(() => {
    if (!selectedCurriculumId || !selectedBlueprintId) return;
    const timeout = window.setTimeout(() => void loadDetail(selectedCurriculumId, selectedBlueprintId), 0);
    return () => window.clearTimeout(timeout);
  }, [loadDetail, selectedBlueprintId, selectedCurriculumId]);

  function selectCurriculum(curriculumId: string) {
    if (curriculumId === selectedCurriculumId) return;
    listRequestId.current += 1;
    detailRequestId.current += 1;
    generateRequestId.current += 1;
    setTaxonomy([]);
    setAnalyticsRuns([]);
    setSummaries([]);
    setSelectedAnalyticsRunId("");
    setSelectedBlueprintId("");
    setDetail(null);
    setRequirements([initialRequirement()]);
    setListError(null);
    setDetailError(null);
    setGenerateError(null);
    setGenerateLoading(false);
    setFormError("");
    setLastRequest(null);
    setNotice("");
    const choice = choices.find((item) => item.curriculum.id === curriculumId);
    if (choice) setResponseLanguage(choice.medium.code);
    setSelectedCurriculumId(curriculumId);
  }

  function updateSection(key: string, update: Partial<SectionDraft>) {
    setSections((current) => current.map((item) => item.key === key ? { ...item, ...update } : item));
  }
  function updateQuestion(questionType: QuestionType, update: Partial<QuestionAllocationDraft>) {
    setQuestionAllocations((current) => current.map((item) => item.questionType === questionType ? { ...item, ...update } : item));
  }
  function updateDifficulty(difficulty: Difficulty, update: Partial<DifficultyAllocationDraft>) {
    setDifficultyAllocations((current) => current.map((item) => item.difficulty === difficulty ? { ...item, ...update } : item));
  }
  function updateRequirement(key: string, update: Partial<TaxonomyRequirementDraft>) {
    setRequirements((current) => current.map((item) => item.key === key ? { ...item, ...update } : item));
  }

  function buildRequest(): { error?: string; request?: BlueprintRequest } {
    if (!selectedChoice) return { error: "Select an active Grade 5 curriculum before generating." };
    if (!reviewedTargets.length) return { error: "At least one active reviewed taxonomy target is required." };
    const requiredText = [
      [paperCode, "Paper code", 64],
      [paperTitle, "Paper title", 255],
      [configVersion, "Config version", 128],
    ] as const;
    for (const [raw, label, maximum] of requiredText) {
      const value = raw.trim();
      if (!value) return { error: `${label} is required.` };
      if (value.length > maximum) return { error: `${label} must be ${maximum} characters or fewer.` };
    }
    const parsedTotal = boundedInteger(totalMarks, "Paper total marks", 1, 100_000);
    if (parsedTotal.error) return { error: parsedTotal.error };
    const parsedSeed = boundedInteger(seed, "Deterministic seed", Number.MIN_SAFE_INTEGER, Number.MAX_SAFE_INTEGER);
    if (parsedSeed.error) return { error: parsedSeed.error };
    if (!sections.length || sections.length > 20) return { error: "Use between 1 and 20 sections." };

    const parsedSections: components["schemas"]["SectionSpecificationRequest"][] = [];
    const seenSectionIds = new Set<string>();
    let sectionMarks = 0;
    let sectionSlots = 0;
    for (const [index, section] of sections.entries()) {
      const prefix = `Section ${index + 1}`;
      const sectionId = section.sectionId.trim();
      const title = section.title.trim();
      if (!sectionId || sectionId.length > 64) return { error: `${prefix} identifier must contain 1 through 64 characters.` };
      if (seenSectionIds.has(sectionId)) return { error: "Section identifiers must be unique." };
      seenSectionIds.add(sectionId);
      if (!title || title.length > 255) return { error: `${prefix} title must contain 1 through 255 characters.` };
      const marks = boundedInteger(section.marks, `${prefix} marks`, 1, 100_000);
      const slots = boundedInteger(section.questionCount, `${prefix} question count`, 1, 200);
      if (marks.error || slots.error) return { error: marks.error ?? slots.error };
      const markValues = textList(section.allowedMarks, `${prefix} allowed marks per slot`, 100);
      if (markValues.error) return { error: markValues.error };
      const allowedMarks: number[] = [];
      for (const raw of markValues.values ?? []) {
        const mark = boundedInteger(raw, `${prefix} allowed mark`, 1, 10_000);
        if (mark.error) return { error: mark.error };
        allowedMarks.push(mark.value as number);
      }
      if (new Set(allowedMarks).size !== allowedMarks.length) return { error: `${prefix} allowed marks must be unique.` };
      if (!section.allowedQuestionTypes.length) return { error: `${prefix} must allow at least one question type.` };
      if (!section.allowedDifficulties.length) return { error: `${prefix} must allow at least one difficulty.` };
      if (!exactSectionComposition(slots.value as number, marks.value as number, allowedMarks)) return { error: `${prefix} marks cannot be composed from its question count and allowed marks per slot.` };
      const hints = textList(section.retrievalHints, `${prefix} retrieval hints`, 50, false);
      if (hints.error) return { error: hints.error };
      sectionMarks += marks.value as number;
      sectionSlots += slots.value as number;
      parsedSections.push({
        allowed_difficulties: section.allowedDifficulties,
        allowed_marks_per_slot: allowedMarks,
        allowed_question_types: section.allowedQuestionTypes,
        allowed_taxonomy_targets: [],
        marks: marks.value as number,
        question_count: slots.value as number,
        retrieval_query_hints: hints.values ?? [],
        section_id: sectionId,
        title,
      });
    }
    if (sectionSlots > 200) return { error: "The blueprint must contain no more than 200 total slots." };
    if (sectionMarks !== parsedTotal.value) return { error: `Section marks must total the paper marks (${sectionMarks} of ${parsedTotal.value}).` };

    const activeQuestionAllocations = questionAllocations.filter((item) => item.enabled);
    if (!activeQuestionAllocations.length) return { error: "Enable at least one question-type allocation." };
    const parsedQuestionAllocations: components["schemas"]["QuestionTypeAllocationRequest"][] = [];
    let questionSlots = 0;
    let questionMarks = 0;
    for (const item of activeQuestionAllocations) {
      const label = displayEnum(item.questionType);
      const slots = boundedInteger(item.exactSlots, `${label} exact slots`, 1, 200);
      const marks = boundedInteger(item.exactMarks, `${label} exact marks`, 1, 100_000);
      const archetypes = textList(item.archetypes, `${label} archetypes`, 50);
      if (slots.error || marks.error || archetypes.error) return { error: slots.error ?? marks.error ?? archetypes.error };
      if ((marks.value as number) < (slots.value as number)) return { error: `${label} exact marks must allow at least one mark per slot.` };
      questionSlots += slots.value as number;
      questionMarks += marks.value as number;
      parsedQuestionAllocations.push({ archetypes: archetypes.values ?? [], exact_marks: marks.value as number, exact_slots: slots.value as number, question_type: item.questionType });
    }
    if (questionSlots !== sectionSlots) return { error: `Question-type slots must total the section question count (${questionSlots} of ${sectionSlots}).` };
    if (questionMarks !== parsedTotal.value) return { error: `Question-type marks must total the paper marks (${questionMarks} of ${parsedTotal.value}).` };

    const activeDifficultyAllocations = difficultyAllocations.filter((item) => item.enabled);
    if (!activeDifficultyAllocations.length) return { error: "Enable at least one difficulty allocation." };
    const parsedDifficultyAllocations: components["schemas"]["DifficultyAllocationRequest"][] = [];
    let difficultySlots = 0;
    let difficultyMarks = 0;
    for (const item of activeDifficultyAllocations) {
      const label = displayEnum(item.difficulty);
      const slots = boundedInteger(item.exactSlots, `${label} exact slots`, 1, 200);
      const marks = boundedInteger(item.exactMarks, `${label} exact marks`, 1, 100_000);
      if (slots.error || marks.error) return { error: slots.error ?? marks.error };
      if ((marks.value as number) < (slots.value as number)) return { error: `${label} exact marks must allow at least one mark per slot.` };
      difficultySlots += slots.value as number;
      difficultyMarks += marks.value as number;
      parsedDifficultyAllocations.push({ difficulty: item.difficulty, exact_marks: marks.value as number, exact_slots: slots.value as number });
    }
    if (difficultySlots !== sectionSlots) return { error: `Difficulty slots must total the section question count (${difficultySlots} of ${sectionSlots}).` };
    if (difficultyMarks !== parsedTotal.value) return { error: `Difficulty marks must total the paper marks (${difficultyMarks} of ${parsedTotal.value}).` };

    if (!requirements.length || requirements.length > 200) return { error: "Use between 1 and 200 taxonomy requirements." };
    const optionsById = new Map(reviewedTargets.map((item) => [item.id, item]));
    const seenTargets = new Set<string>();
    const parsedRequirements: components["schemas"]["TaxonomyRequirementRequest"][] = [];
    let minimumCoverage = 0;
    let maximumCoverage = 0;
    for (const [index, item] of requirements.entries()) {
      const label = `Taxonomy target ${index + 1}`;
      const option = optionsById.get(item.targetId);
      if (!option) return { error: `${label} must be an active reviewed taxonomy path.` };
      const targetKey = JSON.stringify(option.target);
      if (seenTargets.has(targetKey)) return { error: "Taxonomy targets must be unique." };
      seenTargets.add(targetKey);
      if (selectedAnalyticsRunId && !option.target.skill_id) return { error: "Persisted analytics evidence can only be linked to reviewed skill targets." };
      const minimum = boundedInteger(item.minimumSlots, `${label} minimum slots`, 1, 200);
      if (minimum.error) return { error: minimum.error };
      let maximumValue: number | undefined;
      if (item.maximumSlots.trim()) {
        const maximum = boundedInteger(item.maximumSlots, `${label} maximum slots`, 1, 200);
        if (maximum.error) return { error: maximum.error };
        maximumValue = maximum.value;
      }
      if (maximumValue !== undefined && maximumValue < (minimum.value as number)) return { error: `${label} maximum slots must be at least its minimum slots.` };
      const baselineScore = boundedInteger(item.baselineScore, `${label} baseline score`, 1, MAX_INTEGER);
      if (baselineScore.error) return { error: baselineScore.error };
      if (!item.baselineVersion.trim() || item.baselineVersion.trim().length > 128) return { error: `${label} baseline version must contain 1 through 128 characters.` };
      const baselineEvidence = textList(item.baselineEvidence, `${label} baseline evidence`, 100);
      const retrievalHints = textList(item.retrievalHints, `${label} retrieval hints`, 50);
      const instructions = textList(item.generationInstructions, `${label} generation instructions`, 50);
      if (baselineEvidence.error || retrievalHints.error || instructions.error) return { error: baselineEvidence.error ?? retrievalHints.error ?? instructions.error };
      const allowedSectionIds = item.allowedSectionKeys.map((key) => sections.find((section) => section.key === key)?.sectionId.trim()).filter((value): value is string => Boolean(value));
      minimumCoverage += minimum.value as number;
      maximumCoverage += maximumValue ?? sectionSlots;
      parsedRequirements.push({
        allowed_section_ids: allowedSectionIds,
        generation_instructions: instructions.values ?? [],
        maximum_slots: maximumValue ?? null,
        minimum_slots: minimum.value as number,
        priority: {
          baseline_evidence_refs: baselineEvidence.values ?? [],
          baseline_score: baselineScore.value as number,
          baseline_version: item.baselineVersion.trim(),
        },
        retrieval_query_hints: retrievalHints.values ?? [],
        target: option.target,
      });
    }
    if (minimumCoverage > sectionSlots || maximumCoverage < sectionSlots) return { error: `Taxonomy minimum/maximum slots cannot cover ${sectionSlots} total slots.` };

    const instructions = textList(generationInstructions, "Generation instructions", 100);
    const answers = textList(answerRequirements, "Answer requirements", 100);
    const globalHints = textList(globalRetrievalHints, "Global retrieval hints", 100);
    if (instructions.error || answers.error || globalHints.error) return { error: instructions.error ?? answers.error ?? globalHints.error };
    const language = responseLanguage.trim();
    if (language.length < 2 || language.length > 16) return { error: "Response language must contain 2 through 16 characters." };
    const similarity = boundedInteger(maxSimilarity, "Maximum similarity basis points", 0, 9_999);
    const contexts = boundedInteger(minimumDistinctContexts, "Minimum distinct contexts", 1, 100);
    if (similarity.error || contexts.error) return { error: similarity.error ?? contexts.error };

    return {
      request: {
        analytics_run_id: selectedAnalyticsRunId || null,
        seed: parsedSeed.value as number,
        specification: {
          config_version: configVersion.trim(),
          curriculum_scope: {
            curriculum_version_id: selectedChoice.curriculum.id,
            grade: 5,
            medium: selectedChoice.medium.code,
          },
          difficulty_allocations: parsedDifficultyAllocations,
          generation_policy: {
            answer_requirements: answers.values ?? [],
            instructions: instructions.values ?? [],
            response_language: language,
            retrieval_query_hints: globalHints.values ?? [],
            uniqueness: {
              forbid_duplicate_stems: forbidDuplicateStems,
              forbid_verbatim_sources: forbidVerbatimSources,
              max_similarity_basis_points: similarity.value as number,
              minimum_distinct_contexts: contexts.value as number,
            },
          },
          paper_code: paperCode.trim(),
          question_type_allocations: parsedQuestionAllocations,
          sections: parsedSections,
          taxonomy_requirements: parsedRequirements,
          title: paperTitle.trim(),
          total_marks: parsedTotal.value as number,
        },
      },
    };
  }

  const executeGenerate = useCallback(async (request: BlueprintRequest) => {
    if (!selectedCurriculumId) return;
    const requestId = ++generateRequestId.current;
    setGenerateLoading(true);
    setGenerateError(null);
    setFormError("");
    setNotice("");
    setLastRequest(request);
    try {
      const response = await api.POST("/api/v1/admin/curricula/{curriculum_version_id}/blueprints", {
        body: request,
        params: { path: { curriculum_version_id: selectedCurriculumId } },
      });
      if (requestId !== generateRequestId.current) return;
      if (response.error) {
        setGenerateError(apiError(response.error, response.response.status, "generate"));
        return;
      }
      const next = response.data as Blueprint;
      setDetail(next);
      setSelectedBlueprintId(next.id);
      setSummaries((current) => [summaryFromBlueprint(next), ...current.filter((item) => item.id !== next.id)]);
      setNotice(next.deduplicated ? "Existing identical blueprint selected; no duplicate was created." : "Blueprint created and persisted.");
    } catch {
      if (requestId === generateRequestId.current) setGenerateError(networkError("generate"));
    } finally {
      if (requestId === generateRequestId.current) setGenerateLoading(false);
    }
  }, [api, selectedCurriculumId]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setGenerateError(null);
    setNotice("");
    const result = buildRequest();
    if (result.error || !result.request) {
      setFormError(result.error ?? "The blueprint form is incomplete.");
      return;
    }
    setFormError("");
    void executeGenerate(result.request);
  }

  if (workspaceLoading) {
    return <div className="mx-auto max-w-7xl px-5 py-12 sm:px-8" role="status"><p className="font-semibold">Loading blueprint workspace…</p><p className="mt-2 text-sm text-slate-600">Resolving active Grade 5 scope, reviewed taxonomy, persisted analytics and immutable blueprints.</p></div>;
  }
  if (workspaceError) {
    return <div className="mx-auto max-w-3xl px-5 py-12 sm:px-8"><ErrorPanel error={workspaceError} onRetry={() => void loadWorkspace()} retryLabel="Retry workspace" /></div>;
  }

  return (
    <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8">
      <header className="border-b border-slate-300 pb-7">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div><p className="font-mono text-xs tracking-[0.18em] text-slate-500 uppercase">P6 / Deterministic paper design</p><h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Blueprint Studio</h1><p className="mt-3 max-w-3xl leading-7 text-slate-600">Compose bounded Grade 5 paper rules before generation, then inspect every immutable exact slot, constraint, rationale and evidence reference.</p></div>
          <Badge className="border-slate-300 bg-white text-slate-700">{role === "reviewer" ? "Reviewer read access" : "Admin generate access"}</Badge>
        </div>
      </header>

      {!choices.length ? (
        <section className="mt-8 rounded-2xl border border-amber-300 bg-amber-50 p-6"><h2 className="text-xl font-semibold text-amber-950">No active Grade 5 curriculum available</h2><p className="mt-2 text-sm leading-6 text-amber-900">An active Grade 5 exam, medium and curriculum version are required. Inactive and non-Grade-5 scopes are intentionally excluded.</p><Link className={`${secondaryButton} mt-4 border-amber-300`} href="/admin/curriculum">Configure curriculum</Link></section>
      ) : (
        <>
          <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,0.55fr)]">
            <Panel title="Blueprint scope" description="Only active Grade 5 curricula and their active reviewed taxonomy paths are eligible.">
              <label className={fieldClass}>Active Grade 5 curriculum<select className={inputClass} onChange={(event) => selectCurriculum(event.target.value)} value={selectedCurriculumId}>{choices.map((choice) => <option key={choice.curriculum.id} value={choice.curriculum.id}>{choice.curriculum.title}</option>)}</select></label>
              {selectedChoice ? <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-3"><div><dt className="text-xs font-semibold text-slate-500">Grade</dt><dd>5</dd></div><div><dt className="text-xs font-semibold text-slate-500">Medium</dt><dd>{selectedChoice.medium.name} ({selectedChoice.medium.code})</dd></div><div><dt className="text-xs font-semibold text-slate-500">Curriculum code</dt><dd>{selectedChoice.curriculum.code}</dd></div></dl> : null}
            </Panel>
            <section className="rounded-2xl border border-amber-300 bg-amber-50 p-5"><h2 className="font-semibold text-amber-950">Deterministic and idempotent</h2><p className="mt-2 text-sm leading-6 text-amber-900">The same inputs, reviewed taxonomy snapshot, analytics linkage, config and seed produce the same deterministic identity. Safe retries return the existing identical immutable blueprint instead of creating a duplicate. The backend remains authoritative.</p></section>
          </div>

          {listLoading ? <div className="mt-6 rounded-2xl border border-slate-300 bg-white p-5" role="status">Loading reviewed taxonomy, analytics and blueprints…</div> : null}
          {listError ? <div className="mt-6"><ErrorPanel error={listError} onRetry={() => void loadCurriculum(selectedCurriculumId)} retryLabel="Retry blueprint data" /></div> : null}

          {!listLoading && !listError && !reviewedTargets.length ? (
            <section className="mt-6 rounded-2xl border border-amber-300 bg-amber-50 p-6"><h2 className="text-xl font-semibold text-amber-950">No reviewed taxonomy targets</h2><p className="mt-2 text-sm leading-6 text-amber-900">Create, activate and review a complete taxonomy path in this curriculum before generating. Draft, deprecated, inactive and broken hierarchy paths are excluded.</p><Link className={`${secondaryButton} mt-4 border-amber-300`} href="/admin/curriculum">Review taxonomy</Link></section>
          ) : null}

          {canGenerate ? (
            <Form className="mt-8 grid gap-6" onSubmit={submit}>
              <Panel title="Paper metadata" description="Define a versioned paper identity and deterministic signed 64-bit seed.">
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5"><TextInput label="Paper code" maxLength={64} onChange={setPaperCode} value={paperCode} /><div className="sm:col-span-2"><TextInput label="Paper title" maxLength={255} onChange={setPaperTitle} value={paperTitle} /></div><NumberInput label="Paper total marks" maximum={100_000} minimum={1} onChange={setTotalMarks} value={totalMarks} /><NumberInput label="Deterministic seed" maximum={Number.MAX_SAFE_INTEGER} minimum={Number.MIN_SAFE_INTEGER} onChange={setSeed} value={seed} /></div>
                <div className="mt-4 max-w-xl"><TextInput label="Blueprint config version" maxLength={128} onChange={setConfigVersion} value={configVersion} /></div>
              </Panel>

              <Panel title="Sections" description="Add 1–20 sections. Each has exact marks/questions plus bounded allowed marks, types, difficulties and retrieval hints.">
                <div className="grid gap-4">{sections.map((section, index) => <fieldset className="rounded-xl border border-slate-300 p-4" key={section.key}><legend className="px-1 font-semibold">Section {index + 1}</legend><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><TextInput label={`Section ${index + 1} identifier`} maxLength={64} onChange={(value) => updateSection(section.key, { sectionId: value })} value={section.sectionId} /><TextInput label={`Section ${index + 1} title`} maxLength={255} onChange={(value) => updateSection(section.key, { title: value })} value={section.title} /><NumberInput label={`Section ${index + 1} marks`} maximum={100_000} minimum={1} onChange={(value) => updateSection(section.key, { marks: value })} value={section.marks} /><NumberInput label={`Section ${index + 1} question count`} maximum={200} minimum={1} onChange={(value) => updateSection(section.key, { questionCount: value })} value={section.questionCount} /></div><div className="mt-4 grid gap-4 lg:grid-cols-3"><ListInput description="Comma or line separated exact allowed values." label={`Section ${index + 1} allowed marks per slot`} onChange={(value) => updateSection(section.key, { allowedMarks: value })} value={section.allowedMarks} /><ListInput description="Optional; one hint per line." label={`Section ${index + 1} retrieval hints`} onChange={(value) => updateSection(section.key, { retrievalHints: value })} required={false} value={section.retrievalHints} /><div className="grid gap-3 sm:grid-cols-2"><fieldset><legend className="text-sm font-semibold text-slate-700">Allowed question types</legend><div className="mt-2 grid gap-2">{questionTypes.map((type) => <Toggle checked={section.allowedQuestionTypes.includes(type)} key={type} label={displayEnum(type)} onChange={(checked) => updateSection(section.key, { allowedQuestionTypes: checked ? [...section.allowedQuestionTypes, type] : section.allowedQuestionTypes.filter((item) => item !== type) })} />)}</div></fieldset><fieldset><legend className="text-sm font-semibold text-slate-700">Allowed difficulties</legend><div className="mt-2 grid gap-2">{difficulties.map((difficulty) => <Toggle checked={section.allowedDifficulties.includes(difficulty)} key={difficulty} label={displayEnum(difficulty)} onChange={(checked) => updateSection(section.key, { allowedDifficulties: checked ? [...section.allowedDifficulties, difficulty] : section.allowedDifficulties.filter((item) => item !== difficulty) })} />)}</div></fieldset></div></div>{sections.length > 1 ? <Button className={`${dangerButton} mt-4`} onPress={() => { setSections((current) => current.filter((item) => item.key !== section.key)); setRequirements((current) => current.map((item) => ({ ...item, allowedSectionKeys: item.allowedSectionKeys.filter((key) => key !== section.key) }))); }}>Remove section {index + 1}</Button> : null}</fieldset>)}</div>
                <Button className={`${secondaryButton} mt-4`} isDisabled={sections.length >= 20} onPress={() => { const ordinal = sections.length + 1; const key = `section-${dynamicKey.current++}`; setSections((current) => [...current, initialSection(key, ordinal)]); }}>Add section</Button>
              </Panel>

              <div className="grid gap-6 lg:grid-cols-2">
                <Panel title="Exact question-type allocations" description="Enable each required type, then set exact slots, exact marks and one or more bounded archetypes."><div className="grid gap-4">{questionAllocations.map((item) => { const label = displayEnum(item.questionType); return <fieldset className="rounded-xl border border-slate-300 p-4" key={item.questionType}><legend className="px-1 font-semibold">{label}</legend><Toggle checked={item.enabled} label={`Include ${label.toLowerCase()} allocation`} onChange={(enabled) => updateQuestion(item.questionType, { enabled })} />{item.enabled ? <div className="mt-4 grid gap-4 sm:grid-cols-2"><NumberInput label={`${label} exact slots`} maximum={200} minimum={1} onChange={(value) => updateQuestion(item.questionType, { exactSlots: value })} value={item.exactSlots} /><NumberInput label={`${label} exact marks`} maximum={100_000} minimum={1} onChange={(value) => updateQuestion(item.questionType, { exactMarks: value })} value={item.exactMarks} /><div className="sm:col-span-2"><ListInput description="Comma or line separated; the deterministic allocator selects among these." label={`${label} archetypes`} onChange={(value) => updateQuestion(item.questionType, { archetypes: value })} value={item.archetypes} /></div></div> : null}</fieldset>; })}</div></Panel>
                <Panel title="Exact difficulty allocations" description="Enable every difficulty used by the paper and require exact slot and mark totals."><div className="grid gap-4">{difficultyAllocations.map((item) => { const label = displayEnum(item.difficulty); return <fieldset className="rounded-xl border border-slate-300 p-4" key={item.difficulty}><legend className="px-1 font-semibold">{label}</legend><Toggle checked={item.enabled} label={`Include ${label.toLowerCase()} allocation`} onChange={(enabled) => updateDifficulty(item.difficulty, { enabled })} />{item.enabled ? <div className="mt-4 grid gap-4 sm:grid-cols-2"><NumberInput label={`${label} exact slots`} maximum={200} minimum={1} onChange={(value) => updateDifficulty(item.difficulty, { exactSlots: value })} value={item.exactSlots} /><NumberInput label={`${label} exact marks`} maximum={100_000} minimum={1} onChange={(value) => updateDifficulty(item.difficulty, { exactMarks: value })} value={item.exactMarks} /></div> : null}</fieldset>; })}</div></Panel>
              </div>

              <Panel title="Taxonomy coverage" description="Select only active reviewed paths. Set exact coverage bounds, section constraints, fallback baseline priority, retrieval hints and generation instructions.">
                <label className={`${fieldClass} max-w-2xl`}>Persisted analytics evidence (optional)<select className={inputClass} onChange={(event) => setSelectedAnalyticsRunId(event.target.value)} value={selectedAnalyticsRunId}><option value="">No analytics run — use explicit syllabus-balanced baseline</option>{analyticsRuns.map((run) => <option key={run.id} value={run.id}>{displayDate(run.created_at)} · {run.recommendation.mode === "evidence_backed_practice" ? "evidence-backed" : "baseline fallback"} · {run.id}</option>)}</select></label>
                {selectedAnalyticsRunId ? <p className="mt-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm leading-6 text-blue-950">The server will derive forecast and baseline evidence exclusively from this persisted same-curriculum analytics run and verify its fingerprints, versions and leakage audit. No forecast evidence is accepted from client fields.</p> : <p className="mt-3 text-sm leading-6 text-slate-600">No analytics is linked. Explicit syllabus-balanced baseline priority and evidence below remain authoritative for allocation.</p>}
                {!analyticsRuns.length ? <p className="mt-2 text-sm text-slate-600">No persisted analytics runs exist for this curriculum. Baseline-only blueprinting remains available and safe.</p> : null}
                <div className="mt-5 grid gap-4">{requirements.map((item, index) => <fieldset className="rounded-xl border border-slate-300 p-4" key={item.key}><legend className="px-1 font-semibold">Taxonomy target {index + 1}</legend><label className={fieldClass}>Taxonomy target {index + 1}<select aria-label={`Taxonomy target ${index + 1}`} className={inputClass} onChange={(event) => updateRequirement(item.key, { targetId: event.target.value })} required value={item.targetId}><option value="" disabled>{reviewedTargets.length ? "Select reviewed taxonomy" : "No reviewed targets"}</option>{reviewedTargets.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label><div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><NumberInput label={`Minimum slots for taxonomy target ${index + 1}`} maximum={200} minimum={1} onChange={(value) => updateRequirement(item.key, { minimumSlots: value })} value={item.minimumSlots} /><label className={fieldClass}>Maximum slots for taxonomy target {index + 1}<input className={inputClass} inputMode="numeric" max={200} min={1} onChange={(event) => updateRequirement(item.key, { maximumSlots: event.target.value })} placeholder="Blank = no maximum" step="1" type="number" value={item.maximumSlots} /></label>{!selectedAnalyticsRunId ? <><NumberInput label={`Baseline score for taxonomy target ${index + 1}`} maximum={MAX_INTEGER} minimum={1} onChange={(value) => updateRequirement(item.key, { baselineScore: value })} value={item.baselineScore} /><TextInput label={`Baseline version for taxonomy target ${index + 1}`} maxLength={128} onChange={(value) => updateRequirement(item.key, { baselineVersion: value })} value={item.baselineVersion} /></> : null}</div>{!selectedAnalyticsRunId ? <div className="mt-4"><ListInput description="Stable curriculum or policy references." label={`Baseline evidence for taxonomy target ${index + 1}`} onChange={(value) => updateRequirement(item.key, { baselineEvidence: value })} value={item.baselineEvidence} /></div> : null}<fieldset className="mt-4"><legend className="text-sm font-semibold text-slate-700">Allowed sections for taxonomy target {index + 1}</legend><p className="mt-1 text-xs text-slate-500">Leave all clear to permit every section.</p><div className="mt-2 flex flex-wrap gap-4">{sections.map((section) => <Toggle checked={item.allowedSectionKeys.includes(section.key)} key={section.key} label={`${section.sectionId || "Unnamed"} — ${section.title || "Untitled"}`} onChange={(checked) => updateRequirement(item.key, { allowedSectionKeys: checked ? [...item.allowedSectionKeys, section.key] : item.allowedSectionKeys.filter((key) => key !== section.key) })} />)}</div></fieldset><div className="mt-4 grid gap-4 lg:grid-cols-2"><ListInput label={`Retrieval hints for taxonomy target ${index + 1}`} onChange={(value) => updateRequirement(item.key, { retrievalHints: value })} value={item.retrievalHints} /><ListInput label={`Generation instructions for taxonomy target ${index + 1}`} onChange={(value) => updateRequirement(item.key, { generationInstructions: value })} value={item.generationInstructions} /></div>{requirements.length > 1 ? <Button className={`${dangerButton} mt-4`} onPress={() => setRequirements((current) => current.filter((value) => value.key !== item.key))}>Remove taxonomy target {index + 1}</Button> : null}</fieldset>)}</div>
                <Button className={`${secondaryButton} mt-4`} isDisabled={!reviewedTargets.length || requirements.length >= 200} onPress={() => { const key = `taxonomy-${dynamicKey.current++}`; setRequirements((current) => [...current, { ...initialRequirement(key), allowedSectionKeys: sections.map((item) => item.key), targetId: reviewedTargets.find((option) => !current.some((existing) => existing.targetId === option.id))?.id ?? reviewedTargets[0]?.id ?? "" }]); }}>Add taxonomy target</Button>
              </Panel>

              <Panel title="Generation policy" description="These trusted operator constraints are embedded into every slot; retrieved source text remains data, never instruction authority.">
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><TextInput label="Response language" maxLength={16} onChange={setResponseLanguage} value={responseLanguage} /><NumberInput label="Maximum similarity basis points" maximum={9_999} minimum={0} onChange={setMaxSimilarity} value={maxSimilarity} /><NumberInput label="Minimum distinct contexts" maximum={100} minimum={1} onChange={setMinimumDistinctContexts} value={minimumDistinctContexts} /><fieldset className="grid content-start gap-2"><legend className="text-sm font-semibold text-slate-700">Uniqueness requirements</legend><Toggle checked={forbidDuplicateStems} label="Forbid duplicate stems" onChange={setForbidDuplicateStems} /><Toggle checked={forbidVerbatimSources} label="Forbid verbatim source text" onChange={setForbidVerbatimSources} /></fieldset></div><div className="mt-4 grid gap-4 lg:grid-cols-3"><ListInput label="Generation instructions" onChange={setGenerationInstructions} value={generationInstructions} /><ListInput label="Answer requirements" onChange={setAnswerRequirements} value={answerRequirements} /><ListInput label="Global retrieval hints" onChange={setGlobalRetrievalHints} value={globalRetrievalHints} /></div>
              </Panel>

              {formError ? <p className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm font-medium text-red-950" role="alert">{formError}</p> : null}
              {generateError ? <ErrorPanel error={generateError} onRetry={lastRequest ? () => void executeGenerate(lastRequest) : undefined} retryLabel={lastRequest ? "Retry blueprint generation" : undefined} /> : null}
              {notice ? <p className="rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-sm font-medium text-emerald-950" role="status">{notice}</p> : null}
              <div className="flex flex-wrap items-center gap-4"><Button className={primaryButton} isDisabled={generateLoading || !reviewedTargets.length || Boolean(listError)} type="submit">{generateLoading ? "Generating deterministic blueprint…" : "Generate immutable blueprint"}</Button><p className="max-w-2xl text-sm text-slate-600">Client checks totals and bounds for guidance. The backend revalidates every rule, active scope, reviewed taxonomy path, analytics fingerprint and impossible combination.</p></div>
            </Form>
          ) : (
            <section className="mt-8 rounded-2xl border border-slate-300 bg-white p-6 shadow-sm"><h2 className="text-xl font-semibold">Reviewer read-only mode</h2><p className="mt-2 text-sm leading-6 text-slate-600">Reviewers can select and inspect every persisted immutable blueprint, slot, constraint and evidence snapshot. Only administrators can submit generation requests.</p></section>
          )}

          {!listLoading && !listError ? <div className="mt-10 grid gap-6 xl:grid-cols-[21rem_minmax(0,1fr)]"><aside><Panel title="Persisted blueprints" description="Select an immutable snapshot in this curriculum.">{summaries.length ? <ol aria-label="Persisted paper blueprints" className="grid gap-2">{summaries.map((item) => <li key={item.id}><button aria-pressed={selectedBlueprintId === item.id} className={`w-full rounded-xl border p-3 text-left outline-none transition focus-visible:ring-2 focus-visible:ring-amber-500 ${selectedBlueprintId === item.id ? "border-slate-900 bg-slate-950 text-white" : "border-slate-300 bg-white hover:bg-slate-50"}`} onClick={() => setSelectedBlueprintId(item.id)} type="button"><span className="block text-sm font-semibold">{item.paper_code}</span><span className="mt-1 block text-sm">{item.title}</span><span className={`mt-2 block text-xs ${selectedBlueprintId === item.id ? "text-slate-300" : "text-slate-600"}`}>{item.slot_count} slots · {item.total_marks} marks · seed {item.seed}</span><span className={`mt-1 block break-all font-mono text-xs ${selectedBlueprintId === item.id ? "text-amber-200" : "text-slate-500"}`}>{item.id}</span></button></li>)}</ol> : <section className="rounded-xl border border-dashed border-slate-300 p-4"><h2 className="font-semibold">No blueprints yet</h2><p className="mt-2 text-sm leading-6 text-slate-600">{canGenerate ? "Complete the guided exact specification to persist the first immutable blueprint." : "An administrator must generate the first immutable blueprint before reviewer inspection."}</p></section>}</Panel></aside><div className="min-w-0">{detailLoading ? <div className="rounded-2xl border border-slate-300 bg-white p-6" role="status">Loading immutable blueprint snapshot…</div> : null}{detailError ? <ErrorPanel error={detailError} onRetry={() => void loadDetail(selectedCurriculumId, selectedBlueprintId)} retryLabel="Retry selected blueprint" /> : null}{!detailLoading && !detailError && detail ? <BlueprintDetail value={detail} /> : null}{!detailLoading && !detailError && !detail ? <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-6"><h2 className="font-semibold">No immutable blueprint selected</h2><p className="mt-2 text-sm leading-6 text-slate-600">Exact sections, slots, taxonomy targets, constraints, rationale, evidence, analytics linkage, versions and fingerprints will appear here.</p></div> : null}</div></div> : null}
        </>
      )}
    </div>
  );
}
