import type { components } from "@exam-guru/api-client";
import { useId } from "react";

import type { ReviewLanguage } from "@/lib/review-language";
import { cn } from "@/lib/utils";

type Understanding = components["schemas"]["PageUnderstanding"];
type Trusted = components["schemas"]["TrustedPageKnowledge"];
type UnitContent = Pick<
  components["schemas"]["KnowledgeUnit"],
  "observation" | "education" | "resolved_uncertainties"
>;
type Props = {
  language?: ReviewLanguage;
  sourceOnly?: boolean;
  sourceVerified?: boolean;
} & (
  | { understanding: Understanding; trusted?: never; unit?: never }
  | { trusted: Trusted; understanding?: never; unit?: never }
  | { unit: UnitContent; trusted?: never; understanding?: never }
);
type Table = components["schemas"]["ObservedTable"];
type Relationship = components["schemas"]["ObservedRelationship"]["kind"];

const english = {
  visible: "What is visible",
  sourceReading: "System-read source content",
  verifiedSource: "Verified source content",
  sourceChecked:
    "Source content verified against the original. Educational analysis is a separate later step.",
  meaning: "What it may teach",
  uncertain: "Details to check",
  needsAttention: "Needs attention",
  proposed: "Proposed reading — compare with the original before accepting it.",
  detail: "Source detail",
  unspecified: "Unspecified source detail",
  blank: "Blank answer space",
  unreadable: "Could not read",
  noText: "Visual detail without readable text",
  groups: "Visible groups",
  items: "Items in each group",
  printed: "Printed total",
  noPrinted: "Printed total not stated",
  unknown: "Not recorded",
  noMeaning: "No teaching points proposed yet.",
  noUncertainty:
    "No uncertainties were recorded. Compare with the original anyway.",
  alternatives: "Possible readings",
  technical: "Technical details",
  partOf: "Part of",
  acceptedMeaning: "Accepted teaching points",
  checkedDetails: "Details checked by the teacher",
  verified:
    "Checked against the original. Only accepted teaching points are shown.",
  noAccepted: "No teaching points were accepted in this review.",
  noCheckedDetails: "No uncertain details were recorded for this review.",
};

const sinhala: Record<keyof typeof english, string> = {
  visible: "පිටුවේ පෙනෙන දේ",
  sourceReading: "පද්ධතිය කියවූ මූලාශ්‍ර අන්තර්ගතය",
  verifiedSource: "තහවුරු කළ මූලාශ්‍ර අන්තර්ගතය",
  sourceChecked:
    "මූලාශ්‍ර අන්තර්ගතය මුල් පිටුව සමඟ සසඳා තහවුරු කර ඇත. අධ්‍යාපනික විශ්ලේෂණය වෙනම ඊළඟ පියවරකි.",
  meaning: "පිටුවෙන් ඉගැන්විය හැකි දේ",
  uncertain: "පරීක්ෂා කළ යුතු කරුණු",
  needsAttention: "පරීක්ෂා කරන්න",
  proposed: "යෝජිත කියවීම — පිළිගැනීමට පෙර මුල් පිටුව සමඟ සසඳන්න.",
  detail: "මූලාශ්‍ර කරුණ",
  unspecified: "නිශ්චිතව සඳහන් නොකළ මූලාශ්‍ර කරුණ",
  blank: "හිස් පිළිතුරු ඉඩ",
  unreadable: "කියවිය නොහැකි විය",
  noText: "කියවිය හැකි පෙළ නොමැති රූපමය කරුණ",
  groups: "පෙනෙන කණ්ඩායම්",
  items: "එක් කණ්ඩායමක දේවල්",
  printed: "මුද්‍රිත එකතුව",
  noPrinted: "මුද්‍රිත එකතුව සඳහන් නොවේ",
  unknown: "සටහන් කර නැත",
  noMeaning: "ඉගැන්වීමේ කරුණු තවම යෝජනා කර නැත.",
  noUncertainty: "අවිනිශ්චිත කරුණු සටහන් කර නැත. එහෙත් මුල් පිටුව සමඟ සසඳන්න.",
  alternatives: "විය හැකි කියවීම්",
  technical: "තාක්ෂණික විස්තර",
  partOf: "කොටසකි",
  acceptedMeaning: "පිළිගත් ඉගැන්වීමේ කරුණු",
  checkedDetails: "ගුරුවරයා පරීක්ෂා කළ කරුණු",
  verified: "මුල් පිටුව සමඟ සසඳා ඇත. පිළිගත් ඉගැන්වීමේ කරුණු පමණක් පෙන්වයි.",
  noAccepted: "මෙම පරීක්ෂාවේදී ඉගැන්වීමේ කරුණු පිළිගෙන නැත.",
  noCheckedDetails: "මෙම පරීක්ෂාව සඳහා අවිනිශ්චිත කරුණු සටහන් කර නැත.",
};

const relationshipLabels: Record<
  ReviewLanguage,
  Record<Relationship, string>
> = {
  en: {
    label_for: "Label for",
    answer_area_for: "Answer area for",
    grouped_with: "Grouped with",
    aligned_with: "Aligned with",
    part_of: "Part of",
    reading_next: "Next in reading order",
    illustrates: "Illustrates",
  },
  si: {
    label_for: "ලේබලය",
    answer_area_for: "පිළිතුරු ඉඩ",
    grouped_with: "එකට කණ්ඩායම් කළ",
    aligned_with: "පෙළගැසුණු",
    part_of: "කොටසකි",
    reading_next: "ඊළඟට කියවිය යුතු",
    illustrates: "නිදර්ශනය කරයි",
  },
};

function SourceTable({
  table,
  label,
  copy,
  sourceLanguage,
  sourceOnly = false,
}: {
  table: Table;
  label: string;
  copy: typeof english;
  sourceLanguage: string;
  sourceOnly?: boolean;
}) {
  return (
    <div className="overflow-x-auto">
      <table
        aria-label={label}
        className="w-full border-collapse text-left text-sm"
      >
        <tbody>
          {Array.from({ length: table.rows }, (_, row) => (
            <tr key={row}>
              {table.cells
                .filter((cell) => cell.row === row)
                .toSorted((a, b) => a.column - b.column)
                .map((cell) => (
                  <td
                    key={cell.column}
                    rowSpan={cell.row_span}
                    colSpan={cell.column_span}
                    aria-label={
                      sourceOnly && cell.state === "blank"
                        ? copy.blank
                        : undefined
                    }
                    className="min-w-16 border border-slate-300 p-3 align-top whitespace-pre-wrap"
                  >
                    {cell.state === "visible" ? (
                      <span lang={sourceLanguage}>{cell.exact_text}</span>
                    ) : sourceOnly && cell.state === "blank" ? null : (
                      <span className="text-sm italic text-slate-600">
                        {cell.state === "blank" ? copy.blank : copy.unreadable}
                      </span>
                    )}
                  </td>
                ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SourceUnderstandingContent(props: Props) {
  const { language, sourceOnly = false, sourceVerified = false } = props;
  const trusted = props.trusted ?? props.unit;
  const checked = sourceOnly ? sourceVerified : !!trusted;
  const understanding: Understanding = trusted
    ? {
        schema_version: "page-understanding.v1",
        observation: trusted.observation,
        education: trusted.education,
        uncertainties: trusted.resolved_uncertainties,
      }
    : props.understanding!;
  const id = useId();
  const selected =
    language ?? (understanding.observation.language === "si" ? "si" : "en");
  const copy = selected === "si" ? sinhala : english;
  const regions = understanding.observation.regions.toSorted(
    (a, b) => a.reading_order - b.reading_order,
  );
  const positions = new Map(
    regions.map((region, index) => [region.key, index + 1]),
  );
  const reference = (key: string) => {
    const position = positions.get(key);
    return position == null ? copy.unspecified : `${copy.detail} ${position}`;
  };
  const unresolved = new Set(sourceOnly && !sourceVerified ? understanding.uncertainties.flatMap((item) => item.region_keys) : []);
  const sourceLanguage = ["si", "ta", "en"].includes(
    understanding.observation.language,
  )
    ? understanding.observation.language
    : "";

  return (
    <div lang={selected} className="space-y-5 text-slate-950">
      <p
        className={cn(
          "rounded-lg border p-3 text-sm",
          checked
            ? "border-emerald-400 bg-emerald-50 text-emerald-950"
            : "border-amber-300 bg-amber-50 text-amber-950",
        )}
      >
        {sourceOnly
          ? sourceVerified
            ? copy.sourceChecked
            : copy.proposed
          : trusted
            ? copy.verified
            : copy.proposed}
      </p>
      <section aria-labelledby={`${id}-visible`} className="space-y-3">
        <h2 id={`${id}-visible`} className="text-lg font-semibold">
          {sourceOnly
            ? sourceVerified
              ? copy.verifiedSource
              : copy.sourceReading
            : copy.visible}
        </h2>
        {regions.map((region) => (
          <article
            key={region.key}
            aria-label={reference(region.key)}
            data-source-region={region.key}
            className={cn("space-y-3 rounded-lg border border-slate-300 bg-white p-4", unresolved.has(region.key) && "border-amber-500 bg-amber-50")}
          >
            <h3 className="font-semibold">{reference(region.key)}</h3>
            {unresolved.has(region.key) && <p className="text-sm font-semibold text-amber-950">{copy.needsAttention}</p>}
            {region.parent_key && (
              <p className="text-sm text-slate-600">
                {copy.partOf}: {reference(region.parent_key)}
              </p>
            )}
            {region.exact_text && (
              <p
                lang={sourceLanguage}
                className={cn(
                  "whitespace-pre-wrap break-words",
                  region.kind === "vertical_arithmetic" &&
                    "font-mono tabular-nums",
                )}
              >
                {region.exact_text}
              </p>
            )}
            {region.equations.map((equation, index) => (
              <p
                key={index}
                lang={sourceLanguage}
                className="whitespace-pre-wrap break-words"
              >
                {equation}
              </p>
            ))}
            {region.table && (
              <SourceTable
                table={region.table}
                label={reference(region.key)}
                copy={copy}
                sourceLanguage={sourceLanguage}
                sourceOnly={sourceOnly}
              />
            )}
            {region.visual_facts.map((fact) => (
              <div
                key={fact.key}
                className="space-y-2 rounded-lg bg-slate-50 p-3 text-sm"
              >
                <p lang={sourceLanguage}>{fact.description}</p>
                <dl className="grid grid-cols-2 gap-2">
                  <dt>{copy.groups}</dt>
                  <dd>{fact.group_count ?? copy.unknown}</dd>
                  <dt>{copy.items}</dt>
                  <dd>{fact.items_per_group ?? copy.unknown}</dd>
                  {fact.printed_total !== null && (
                    <>
                      <dt>{copy.printed}</dt>
                      <dd lang={sourceLanguage}>{fact.printed_total}</dd>
                    </>
                  )}
                </dl>
                {fact.printed_total === null && (
                  <p className="text-slate-600">{copy.noPrinted}</p>
                )}
              </div>
            ))}
            {!region.exact_text &&
              !region.equations.length &&
              !region.table &&
              !region.visual_facts.length && (
                <p className="text-sm text-slate-600">{copy.noText}</p>
              )}
          </article>
        ))}
        {understanding.observation.relationships.length > 0 && (
          <ul className="space-y-2 text-sm text-slate-700">
            {understanding.observation.relationships.map((relation, index) => (
              <li key={index}>
                {reference(relation.source_key)} —{" "}
                {relationshipLabels[selected][relation.kind]} —{" "}
                {reference(relation.target_key)}
              </li>
            ))}
          </ul>
        )}
      </section>
      {!sourceOnly && (
        <section aria-labelledby={`${id}-meaning`} className="space-y-3">
          <h2 id={`${id}-meaning`} className="text-lg font-semibold">
            {trusted ? copy.acceptedMeaning : copy.meaning}
          </h2>
          {!understanding.education.claims.length && (
            <p className="text-sm text-slate-600">
              {trusted ? copy.noAccepted : copy.noMeaning}
            </p>
          )}
          {understanding.education.claims.map((claim) => (
            <article
              key={claim.key}
              className="rounded-lg border border-slate-300 bg-white p-4"
            >
              <p className="whitespace-pre-wrap break-words">
                {claim.description}
              </p>
              <p className="mt-2 text-sm text-slate-600">
                {claim.region_keys.map(reference).join(" · ")}
              </p>
            </article>
          ))}
        </section>
      )}
      <section aria-labelledby={`${id}-uncertain`} className="space-y-3">
        <h2 id={`${id}-uncertain`} className="text-lg font-semibold">
          {trusted ? copy.checkedDetails : copy.uncertain}
        </h2>
        {!understanding.uncertainties.length && (
          <p className="text-sm text-slate-600">
            {trusted ? copy.noCheckedDetails : copy.noUncertainty}
          </p>
        )}
        {understanding.uncertainties.map((uncertainty) => (
          <article
            key={uncertainty.key}
            className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-amber-950"
          >
            <p>{uncertainty.reason}</p>
            <p className="mt-2 text-sm">
              {uncertainty.region_keys.map(reference).join(" · ")}
            </p>
            {!!uncertainty.alternatives.length && (
              <p className="mt-2 text-sm">
                {copy.alternatives}: {uncertainty.alternatives.join(" / ")}
              </p>
            )}
          </article>
        ))}
      </section>
      <details className="rounded-lg border border-slate-300 bg-white p-3 text-sm">
        <summary className="cursor-pointer font-semibold">
          {copy.technical}
        </summary>
        <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words">
          {JSON.stringify(
            sourceOnly
              ? {
                  schema_version: "source-read-candidate.v1",
                  observation: understanding.observation,
                  uncertainties: understanding.uncertainties,
                }
              : (trusted ?? understanding),
            null,
            2,
          )}
        </pre>
      </details>
    </div>
  );
}
