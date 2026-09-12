"use client";

import type { components } from "@exam-guru/api-client";
import { Button } from "react-aria-components";

import type { ReviewLanguage } from "@/lib/review-language";

import { viewerButtonClass } from "./original-page-viewer";

type Understanding = components["schemas"]["PageUnderstanding"];
type Region = components["schemas"]["PageRegionObservation"];
type Claim = components["schemas"]["EducationalClaim"];
type Language = components["schemas"]["PageObservation"]["language"];
const inputClass =
  "w-full rounded-lg border border-slate-400 bg-white p-2 text-slate-950 outline-none focus-visible:ring-2 focus-visible:ring-amber-600 disabled:bg-slate-100 disabled:text-slate-600";
const english = {
  title: "Correct the recorded page content",
  notice:
    "Saving creates a new unverified reading. The original and earlier versions are kept. Source positions and recorded uncertainties are not silently changed.",
  detail: "detail",
  sourceDetail: "Source detail",
  text: "Source text",
  equation: "Printed equation",
  cell: "Cell text",
  state: "Cell state",
  row: "row",
  column: "column",
  visible: "Visible text",
  blank: "Blank answer space",
  unreadable: "Unreadable",
  picture: "picture",
  description: "Picture description",
  groups: "Visible groups",
  items: "Items per group",
  total: "Printed total",
  meaning: "Proposed teaching points",
  teaching: "Teaching point",
  kind: "Teaching point type",
  add: "Add teaching point",
  remove: "Remove teaching point",
  forTeaching: "for teaching point",
  language: "Source text language",
  incomplete:
    "Complete the visible cell values and teaching-point details before saving.",
};
const sinhala: Record<keyof typeof english, string> = {
  title: "සටහන් කළ පිටු අන්තර්ගතය නිවැරදි කරන්න",
  notice:
    "සුරැකීමෙන් තහවුරු නොකළ නව කියවීමක් සාදයි. මුල් පිටුව සහ පෙර අනුවාද රඳවා ගනී. පිහිටීම් හා අවිනිශ්චිත කරුණු නොදන්වා වෙනස් නොකරයි.",
  detail: "කරුණ",
  sourceDetail: "මූලාශ්‍ර කරුණ",
  text: "මූලාශ්‍ර පෙළ",
  equation: "මුද්‍රිත සමීකරණය",
  cell: "කොටුවේ පෙළ",
  state: "කොටුවේ කියවීම",
  row: "පේළිය",
  column: "තීරුව",
  visible: "පෙනෙන පෙළ",
  blank: "හිස් පිළිතුරු ඉඩ",
  unreadable: "කියවිය නොහැක",
  picture: "රූපය",
  description: "රූප විස්තරය",
  groups: "පෙනෙන කණ්ඩායම්",
  items: "එක් කණ්ඩායමක දේවල්",
  total: "මුද්‍රිත එකතුව",
  meaning: "යෝජිත ඉගැන්වීමේ කරුණු",
  teaching: "ඉගැන්වීමේ කරුණ",
  kind: "ඉගැන්වීමේ කරුණු වර්ගය",
  add: "ඉගැන්වීමේ කරුණක් එක් කරන්න",
  remove: "ඉගැන්වීමේ කරුණ ඉවත් කරන්න",
  forTeaching: "ඉගැන්වීමේ කරුණ සඳහා",
  language: "මූලාශ්‍ර පෙළේ භාෂාව",
  incomplete:
    "සුරැකීමට පෙර පෙනෙන කොටුවල අගයන් සහ ඉගැන්වීමේ කරුණුවල විස්තර සම්පූර්ණ කරන්න.",
};
const kinds: Record<Claim["kind"], [string, string]> = {
  topic: ["Topic", "මාතෘකාව"],
  concept: ["Concept", "සංකල්පය"],
  skill: ["Skill", "කුසලතාව"],
  learning_objective: ["Learning objective", "ඉගෙනුම් අරමුණ"],
  educational_purpose: ["Educational purpose", "අධ්‍යාපනික අරමුණ"],
  activity_type: ["Activity", "ක්‍රියාකාරකම"],
  worked_example: ["Worked example", "විසඳූ උදාහරණය"],
  relationship: ["Relationship", "සම්බන්ධතාව"],
  prerequisite: ["Prerequisite", "පූර්ව දැනුම"],
};
const languages: Record<Language, [string, string]> = {
  si: ["Sinhala", "සිංහල"],
  ta: ["Tamil", "දෙමළ"],
  en: ["English", "ඉංග්‍රීසි"],
  mixed: ["Mixed", "මිශ්‍ර"],
  und: ["Not determined", "නිශ්චිත නැත"],
};

export function correctionIsComplete(value: Understanding): boolean {
  const count = (number: number | null) =>
    number === null ||
    (Number.isSafeInteger(number) && number >= 0 && number <= 10000);
  const regions = new Set(
    value.observation.regions.map((region) => region.key),
  );
  return (
    new TextEncoder().encode(JSON.stringify(value)).length <= 1_048_576 &&
    value.observation.regions.every(
      (region) =>
        region.equations.every((equation) => equation.trim().length > 0) &&
        (region.table?.cells.every((cell) =>
          cell.state === "visible"
            ? cell.exact_text.trim().length > 0
            : cell.exact_text === "",
        ) ??
          true) &&
        region.visual_facts.every(
          (fact) =>
            fact.description.trim().length > 0 &&
            count(fact.group_count) &&
            count(fact.items_per_group) &&
            (fact.printed_total === null ||
              fact.printed_total.trim().length > 0),
        ),
    ) &&
    value.education.claims.every(
      (claim) =>
        claim.description.trim().length > 0 &&
        claim.region_keys.length > 0 &&
        claim.region_keys.every((key) => regions.has(key)),
    )
  );
}

export function SourceUnderstandingEditor({
  value,
  onChange,
  language,
  disabled = false,
}: {
  value: Understanding;
  onChange: (value: Understanding) => void;
  language: ReviewLanguage;
  disabled?: boolean;
}) {
  const copy = language === "si" ? sinhala : english;
  const labelIndex = language === "si" ? 1 : 0;
  const regions = value.observation.regions
    .map((region, index) => ({ region, index }))
    .toSorted((a, b) => a.region.reading_order - b.region.reading_order);
  function regionChange(index: number, update: Partial<Region>) {
    onChange({
      ...value,
      observation: {
        ...value.observation,
        regions: value.observation.regions.map((region, position) =>
          position === index ? { ...region, ...update } : region,
        ),
      },
    });
  }
  function claimChange(index: number, update: Partial<Claim>) {
    onChange({
      ...value,
      education: {
        claims: value.education.claims.map((claim, position) =>
          position === index ? { ...claim, ...update } : claim,
        ),
      },
    });
  }
  return (
    <fieldset disabled={disabled} className="space-y-5" lang={language}>
      <legend className="text-lg font-semibold">{copy.title}</legend>
      <p className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
        {copy.notice}
      </p>
      <label className="block text-sm font-semibold">
        {copy.language}
        <select
          className={inputClass}
          value={value.observation.language}
          onChange={(event) =>
            onChange({
              ...value,
              observation: {
                ...value.observation,
                language: event.target.value as Language,
              },
            })
          }
        >
          {Object.entries(languages).map(([key, labels]) => (
            <option key={key} value={key}>
              {labels[labelIndex]}
            </option>
          ))}
        </select>
      </label>
      {regions.map(({ region, index }, position) => {
        const detail = `${copy.detail} ${position + 1}`;
        return (
          <section
            key={region.key}
            aria-label={`${copy.sourceDetail} ${position + 1}`}
            className="space-y-3 rounded-lg border border-slate-300 p-3"
          >
            <h3 className="font-semibold">
              {copy.sourceDetail} {position + 1}
            </h3>
            <label className="block text-sm">
              {copy.text} — {detail}
              <textarea
                className={inputClass}
                rows={3}
                maxLength={100000}
                value={region.exact_text}
                onChange={(event) =>
                  regionChange(index, { exact_text: event.target.value })
                }
              />
            </label>
            {region.equations.map((equation, number) => (
              <label className="block text-sm" key={number}>
                {copy.equation} {number + 1} — {detail}
                <input
                  className={inputClass}
                  maxLength={2000}
                  value={equation}
                  onChange={(event) =>
                    regionChange(index, {
                      equations: region.equations.map((item, itemIndex) =>
                        itemIndex === number ? event.target.value : item,
                      ),
                    })
                  }
                />
              </label>
            ))}
            {region.table && (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-left">
                  <tbody>
                    {Array.from({ length: region.table.rows }, (_, row) => (
                      <tr key={row}>
                        {region
                          .table!.cells.map((cell, cellIndex) => ({
                            cell,
                            cellIndex,
                          }))
                          .filter((item) => item.cell.row === row)
                          .toSorted((a, b) => a.cell.column - b.cell.column)
                          .map(({ cell, cellIndex }) => {
                            const place = `${detail}, ${copy.row} ${cell.row + 1}, ${copy.column} ${cell.column + 1}`;
                            const change = (
                              state: typeof cell.state,
                              text: string,
                            ) =>
                              regionChange(index, {
                                table: {
                                  ...region.table!,
                                  cells: region.table!.cells.map(
                                    (item, itemIndex) =>
                                      itemIndex === cellIndex
                                        ? { ...cell, state, exact_text: text }
                                        : item,
                                  ),
                                },
                              });
                            return (
                              <td
                                key={cell.column}
                                rowSpan={cell.row_span}
                                colSpan={cell.column_span}
                                className="min-w-40 space-y-2 border border-slate-300 p-2 align-top"
                              >
                                <label className="block text-xs">
                                  {copy.state} — {place}
                                  <select
                                    className={inputClass}
                                    value={cell.state}
                                    onChange={(event) => {
                                      const state = event.target
                                        .value as typeof cell.state;
                                      change(
                                        state,
                                        state === "visible"
                                          ? cell.exact_text
                                          : "",
                                      );
                                    }}
                                  >
                                    <option value="visible">
                                      {copy.visible}
                                    </option>
                                    <option value="blank">{copy.blank}</option>
                                    <option value="unreadable">
                                      {copy.unreadable}
                                    </option>
                                  </select>
                                </label>
                                <label className="block text-xs">
                                  {copy.cell} — {place}
                                  <textarea
                                    className={inputClass}
                                    rows={2}
                                    maxLength={100000}
                                    disabled={
                                      disabled || cell.state !== "visible"
                                    }
                                    value={cell.exact_text}
                                    onChange={(event) =>
                                      change("visible", event.target.value)
                                    }
                                  />
                                </label>
                              </td>
                            );
                          })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {region.visual_facts.map((fact, factIndex) => {
              const place = `${detail}, ${copy.picture} ${factIndex + 1}`;
              const change = (update: Partial<typeof fact>) =>
                regionChange(index, {
                  visual_facts: region.visual_facts.map((item, itemIndex) =>
                    itemIndex === factIndex ? { ...fact, ...update } : item,
                  ),
                });
              const numeric = (
                field: "group_count" | "items_per_group",
                text: string,
              ) => {
                const number = text === "" ? null : Number(text);
                if (
                  number === null ||
                  (Number.isSafeInteger(number) &&
                    number >= 0 &&
                    number <= 10000)
                )
                  change({ [field]: number });
              };
              return (
                <div
                  key={fact.key}
                  className="space-y-2 rounded-lg bg-slate-50 p-3"
                >
                  <label className="block text-sm">
                    {copy.description} — {place}
                    <textarea
                      className={inputClass}
                      maxLength={2000}
                      value={fact.description}
                      onChange={(event) =>
                        change({ description: event.target.value })
                      }
                    />
                  </label>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <label className="block text-sm">
                      {copy.groups} — {place}
                      <input
                        className={inputClass}
                        type="number"
                        min={0}
                        max={10000}
                        step={1}
                        value={fact.group_count ?? ""}
                        onChange={(event) =>
                          numeric("group_count", event.target.value)
                        }
                      />
                    </label>
                    <label className="block text-sm">
                      {copy.items} — {place}
                      <input
                        className={inputClass}
                        type="number"
                        min={0}
                        max={10000}
                        step={1}
                        value={fact.items_per_group ?? ""}
                        onChange={(event) =>
                          numeric("items_per_group", event.target.value)
                        }
                      />
                    </label>
                  </div>
                  <label className="block text-sm">
                    {copy.total} — {place}
                    <input
                      className={inputClass}
                      maxLength={2000}
                      value={fact.printed_total ?? ""}
                      onChange={(event) =>
                        change({
                          printed_total:
                            event.target.value === ""
                              ? null
                              : event.target.value,
                        })
                      }
                    />
                  </label>
                </div>
              );
            })}
          </section>
        );
      })}
      <section className="space-y-3" aria-label={copy.meaning}>
        <h3 className="font-semibold">{copy.meaning}</h3>
        {value.education.claims.map((claim, index) => (
          <fieldset
            key={claim.key}
            className="space-y-2 rounded-lg border border-slate-300 p-3"
          >
            <label className="block text-sm">
              {copy.teaching} {index + 1}
              <textarea
                className={inputClass}
                maxLength={2000}
                value={claim.description}
                onChange={(event) =>
                  claimChange(index, { description: event.target.value })
                }
              />
            </label>
            <label className="block text-sm">
              {copy.kind} {index + 1}
              <select
                className={inputClass}
                value={claim.kind}
                onChange={(event) =>
                  claimChange(index, {
                    kind: event.target.value as Claim["kind"],
                  })
                }
              >
                {Object.entries(kinds).map(([kind, labels]) => (
                  <option key={kind} value={kind}>
                    {labels[labelIndex]}
                  </option>
                ))}
              </select>
            </label>
            {regions.map(({ region }, sourceIndex) => (
              <label className="flex gap-2 text-sm" key={region.key}>
                <input
                  type="checkbox"
                  checked={claim.region_keys.includes(region.key)}
                  onChange={(event) =>
                    claimChange(index, {
                      region_keys: event.target.checked
                        ? [
                            ...claim.region_keys.filter(
                              (key) => key !== region.key,
                            ),
                            region.key,
                          ]
                        : claim.region_keys.filter((key) => key !== region.key),
                    })
                  }
                />
                {copy.sourceDetail} {sourceIndex + 1} {copy.forTeaching}{" "}
                {index + 1}
              </label>
            ))}
            <Button
              className={viewerButtonClass}
              isDisabled={disabled}
              onPress={() =>
                onChange({
                  ...value,
                  education: {
                    claims: value.education.claims.filter(
                      (_, position) => position !== index,
                    ),
                  },
                })
              }
            >
              {copy.remove} {index + 1}
            </Button>
          </fieldset>
        ))}
        <Button
          className={viewerButtonClass}
          isDisabled={disabled || value.education.claims.length >= 128}
          onPress={() =>
            onChange({
              ...value,
              education: {
                claims: [
                  ...value.education.claims,
                  {
                    key: `claim-${crypto.randomUUID()}`,
                    kind: "concept",
                    description: "",
                    region_keys: [],
                  },
                ],
              },
            })
          }
        >
          {copy.add}
        </Button>
      </section>
      {!correctionIsComplete(value) && (
        <p role="status" className="text-sm text-amber-950">
          {copy.incomplete}
        </p>
      )}
    </fieldset>
  );
}
