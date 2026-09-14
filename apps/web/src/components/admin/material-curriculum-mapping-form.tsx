import { useId, type FormEvent } from "react";
import { Button } from "react-aria-components";

import { cn } from "@/lib/utils";

import type { CurriculumReviewCopy } from "./material-curriculum-review-copy";
import type {
  Choices,
  KnowledgeWorkspace,
  MappingRequest,
  MaterialUnitSummary,
} from "./material-curriculum-review-state";
import { viewerButtonClass } from "./original-page-viewer";

export const curriculumPrimaryButton = cn(
  viewerButtonClass,
  "border-slate-950 bg-slate-950 text-white hover:bg-slate-800 disabled:border-slate-300 disabled:bg-slate-200 disabled:text-slate-700",
);
export const curriculumInputClass =
  "min-h-10 w-full rounded-lg border border-slate-400 bg-white p-2 text-slate-950 outline-none focus-visible:ring-2 focus-visible:ring-amber-600 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-700";

export function MaterialCurriculumMappingForm({
  workspace,
  summary,
  choices,
  mapping,
  writable,
  busy,
  blocked,
  maySave,
  imageReady,
  copy,
  onChange,
  onSubmit,
}: {
  workspace: KnowledgeWorkspace;
  summary: MaterialUnitSummary;
  choices: Choices;
  mapping: MappingRequest;
  writable: boolean;
  busy: boolean;
  blocked: boolean;
  maySave: boolean;
  imageReady: boolean;
  copy: CurriculumReviewCopy;
  onChange: (value: MappingRequest) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const id = useId();
  const fields = [
    ["curriculum_unit_id", copy.module, choices.units],
    [
      "lesson_id",
      copy.lesson,
      choices.lessons.filter(
        (lesson) => lesson.unit_id === mapping.curriculum_unit_id,
      ),
    ],
    [
      "competency_id",
      copy.competency,
      choices.nodes.filter((node) => node.level === "competency"),
    ],
    [
      "skill_id",
      copy.skill,
      choices.nodes.filter(
        (node) =>
          node.level === "skill" && node.parent_id === mapping.competency_id,
      ),
    ],
    [
      "sub_skill_id",
      copy.subSkill,
      choices.nodes.filter(
        (node) =>
          node.level === "sub_skill" && node.parent_id === mapping.skill_id,
      ),
    ],
    [
      "learning_concept_id",
      copy.concept,
      choices.nodes.filter(
        (node) =>
          node.level === "learning_concept" &&
          node.parent_id === mapping.sub_skill_id,
      ),
    ],
  ] as const;
  const linkedLesson = choices.lessons.find(
    (lesson) => lesson.id === mapping.lesson_id,
  );
  const links = choices.nodes
    .filter((node) => linkedLesson?.taxonomy_node_ids.includes(node.id))
    .map((node) => node.title);
  return (
    <form
      aria-labelledby={`${id}-heading`}
      onSubmit={onSubmit}
      className="space-y-4 rounded-xl border border-slate-300 bg-white p-4"
    >
      <h2 id={`${id}-heading`} className="text-xl font-semibold">
        {copy.mapping}
      </h2>
      <p className="text-sm text-slate-700">
        {workspace.review?.state === "reviewed" &&
        workspace.review.confirmed_mapping === true
          ? copy.confirmed
          : copy.notConfirmed}
      </p>
      <p className="text-sm text-slate-700">{copy.optional}</p>
      <div className="grid gap-4 sm:grid-cols-2">
        {fields.map(([field, label, options], index) => {
          const lockedId =
            field === "curriculum_unit_id"
              ? workspace.unit.scope.curriculum_unit_id
              : field === "lesson_id"
                ? workspace.unit.scope.lesson_id
                : null;
          const lockedTitle =
            options.find((option) => option.id === lockedId)?.title ??
            (field === "curriculum_unit_id"
              ? summary.source_unit_title
              : summary.source_lesson_title) ??
            copy.unavailableLabel;
          const value = mapping[field] ?? "";
          const available =
            !value || options.some((option) => option.id === value);
          return (
            <div key={field}>
              <label
                htmlFor={`${id}-${field}`}
                className="mb-1 block text-sm font-semibold"
              >
                {label}
              </label>
              {lockedId ? (
                <>
                  <input
                    id={`${id}-${field}`}
                    className={curriculumInputClass}
                    value={lockedTitle}
                    readOnly
                    aria-describedby={`${id}-${field}-help`}
                  />
                  <p
                    id={`${id}-${field}-help`}
                    className="mt-1 text-sm text-slate-600"
                  >
                    {copy.locked}
                  </p>
                </>
              ) : (
                <>
                  <select
                    id={`${id}-${field}`}
                    className={curriculumInputClass}
                    value={available ? value : ""}
                    disabled={
                      !writable ||
                      busy ||
                      blocked ||
                      (index === 1 && !mapping.curriculum_unit_id) ||
                      (index > 2 && !mapping[fields[index - 1][0]])
                    }
                    required={field === "competency_id"}
                    aria-invalid={!available || undefined}
                    aria-describedby={
                      !available ? `${id}-${field}-unavailable` : undefined
                    }
                    onChange={(event) => {
                      const next = {
                        ...mapping,
                        [field]: event.target.value || null,
                        confirmed_mapping: false,
                      };
                      if (field === "curriculum_unit_id") next.lesson_id = null;
                      if (index >= 2)
                        for (const [child] of fields.slice(index + 1))
                          next[child] = null;
                      onChange(next);
                    }}
                  >
                    <option value="">
                      {field === "competency_id" ? copy.chooseArea : copy.none}
                    </option>
                    {options.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.title}
                      </option>
                    ))}
                  </select>
                  {!available && (
                    <p
                      id={`${id}-${field}-unavailable`}
                      className="mt-1 text-sm text-red-900"
                    >
                      {copy.unavailableChoice}
                    </p>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>
      {links.length > 0 && (
        <div className="rounded-lg bg-slate-100 p-3 text-sm text-slate-700">
          <p>
            {copy.lessonLinks} {links.join(" · ")}
          </p>
          <p className="mt-1">{copy.explicit}</p>
        </div>
      )}
      {writable && (
        <>
          <label
            className="block text-sm font-semibold"
            htmlFor={`${id}-reason`}
          >
            {copy.reason}
          </label>
          <textarea
            id={`${id}-reason`}
            className={curriculumInputClass}
            rows={3}
            required
            maxLength={2000}
            disabled={busy || blocked}
            value={mapping.reason}
            onChange={(event) =>
              onChange({
                ...mapping,
                reason: event.target.value,
                confirmed_mapping: false,
              })
            }
          />
          <label className="flex items-start gap-3 text-sm">
            <input
              type="checkbox"
              className="mt-1 size-4 accent-slate-950"
              checked={mapping.confirmed_mapping}
              disabled={busy || blocked || !imageReady}
              onChange={(event) =>
                onChange({
                  ...mapping,
                  confirmed_mapping: event.target.checked,
                })
              }
            />
            {copy.consent}
          </label>
          <Button
            type="submit"
            className={curriculumPrimaryButton}
            isDisabled={!maySave}
          >
            {busy ? copy.saving : copy.save}
          </Button>
        </>
      )}
    </form>
  );
}
