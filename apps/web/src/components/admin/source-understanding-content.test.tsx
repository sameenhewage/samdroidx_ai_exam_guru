import type { components } from "@exam-guru/api-client";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it } from "vitest";

import { SourceUnderstandingContent } from "./source-understanding-content";

type Understanding = components["schemas"]["PageUnderstanding"];

export function understandingFixture(): Understanding {
  return {
    schema_version: "page-understanding.v1",
    observation: {
      language: "si",
      regions: [
        {
          key: "heading",
          kind: "heading",
          reading_order: 0,
          parent_key: null,
          bounds: null,
          polygon: [],
          exact_text: "ශ්‍රී ලංකාව a\u0301 <script>untrusted()</script>",
          equations: [],
          table: null,
          visual_facts: [],
        },
        {
          key: "answers",
          kind: "grid",
          reading_order: 1,
          parent_key: null,
          bounds: null,
          polygon: [],
          exact_text: "",
          equations: ["6 × 2 = 8"],
          visual_facts: [],
          table: {
            rows: 2,
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
              {
                row: 1,
                column: 0,
                row_span: 1,
                column_span: 1,
                state: "unreadable",
                exact_text: "",
              },
              {
                row: 1,
                column: 1,
                row_span: 1,
                column_span: 1,
                state: "visible",
                exact_text: "8",
              },
            ],
          },
        },
        {
          key: "groups",
          kind: "repeated_object_group",
          reading_order: 2,
          parent_key: null,
          bounds: null,
          polygon: [],
          exact_text: "",
          equations: [],
          table: null,
          visual_facts: [
            {
              key: "shoes",
              description: "Six visible groups of shoes",
              group_count: 6,
              items_per_group: 2,
              printed_total: null,
            },
          ],
        },
      ],
      relationships: [
        {
          source_key: "answers",
          target_key: "heading",
          kind: "answer_area_for",
        },
      ],
    },
    education: {
      claims: [
        {
          key: "skip_counting",
          kind: "skill",
          description: "Practise counting by twos.",
          region_keys: ["groups"],
        },
      ],
    },
    uncertainties: [
      {
        key: "unclear_symbol",
        region_keys: ["groups"],
        field: "visual_facts",
        reason: "One symbol is unclear.",
        alternatives: ["Six groups", "Seven groups"],
      },
    ],
  };
}

describe("teacher-facing structured source evidence", () => {
  it("keeps literal source, teaching interpretation and uncertainty in distinct sections", () => {
    const content = understandingFixture();
    const { container } = render(
      <SourceUnderstandingContent understanding={content} language="en" />,
    );
    const observed = screen.getByRole("region", { name: "What is visible" });
    const meaning = screen.getByRole("region", { name: "What it may teach" });
    const uncertain = screen.getByRole("region", { name: "Details to check" });
    expect(
      within(observed).getByText(content.observation.regions[0].exact_text),
    ).toHaveTextContent("a\u0301");
    expect(within(observed).getByText("6 × 2 = 8")).toBeVisible();
    expect(
      within(observed).queryByText("12", { exact: true }),
    ).not.toBeInTheDocument();
    expect(
      within(observed).queryByText("Practise counting by twos."),
    ).not.toBeInTheDocument();
    expect(
      within(meaning).getByText("Practise counting by twos."),
    ).toBeVisible();
    expect(within(meaning).getByText("Source detail 3")).toBeVisible();
    expect(within(uncertain).getByText("One symbol is unclear.")).toBeVisible();
    expect(container.querySelector("script")).toBeNull();
    expect(
      screen.getByText(
        "Proposed reading — compare with the original before accepting it.",
      ),
    ).toBeVisible();
  });

  it("distinguishes blank and unreadable cells without solving source exercises", () => {
    render(
      <SourceUnderstandingContent
        understanding={understandingFixture()}
        language="en"
      />,
    );
    const table = screen.getByRole("table", { name: "Source detail 2" });
    expect(within(table).getByText("Blank answer space")).toBeVisible();
    expect(within(table).getByText("Could not read")).toBeVisible();
    expect(within(table).getAllByRole("cell")).toHaveLength(4);
    expect(within(table).queryByText("12")).not.toBeInTheDocument();
    expect(screen.getByText("Printed total not stated")).toBeVisible();
  });

  it("defaults presentation to Sinhala without rewriting the source Unicode", () => {
    const content = understandingFixture();
    render(<SourceUnderstandingContent understanding={content} />);
    expect(
      screen.getByRole("region", { name: "පිටුවේ පෙනෙන දේ" }),
    ).toBeVisible();
    expect(
      screen.getByText(content.observation.regions[0].exact_text).textContent,
    ).toBe(content.observation.regions[0].exact_text);
    expect(
      screen.getByText("තාක්ෂණික විස්තර").closest("details"),
    ).not.toHaveAttribute("open");
  });

  it("keeps source-cell language independent from the chosen interface language", () => {
    const content = understandingFixture();
    content.observation.regions[1].table!.cells[0].exact_text = "සිංහල වචන";
    render(
      <SourceUnderstandingContent understanding={content} language="en" />,
    );
    expect(screen.getByText("සිංහල වචන").closest("[lang]")).toHaveAttribute(
      "lang",
      "si",
    );
    expect(
      screen.getByText("Blank answer space").closest("[lang]"),
    ).toHaveAttribute("lang", "en");
  });

  it("keeps zero counts and printed values separate from missing observations", () => {
    const content = understandingFixture();
    content.observation.regions[2].parent_key = "heading";
    content.observation.regions[2].visual_facts[0] = {
      ...content.observation.regions[2].visual_facts[0],
      group_count: 0,
      items_per_group: null,
      printed_total: "0",
    };
    render(
      <SourceUnderstandingContent understanding={content} language="en" />,
    );
    const detail = screen.getByRole("article", { name: "Source detail 3" });
    expect(within(detail).getAllByText("0", { exact: true })).toHaveLength(2);
    expect(within(detail).getByText("Not recorded")).toBeVisible();
    expect(
      within(detail).queryByText("Printed total not stated"),
    ).not.toBeInTheDocument();
  });

  it("retains table spans and source reading order", () => {
    const content = understandingFixture();
    content.observation.regions[1].table = {
      rows: 2,
      columns: 2,
      cells: [
        {
          row: 1,
          column: 1,
          row_span: 1,
          column_span: 1,
          state: "unreadable",
          exact_text: "",
        },
        {
          row: 0,
          column: 1,
          row_span: 1,
          column_span: 1,
          state: "blank",
          exact_text: "",
        },
        {
          row: 0,
          column: 0,
          row_span: 2,
          column_span: 1,
          state: "visible",
          exact_text: "6",
        },
      ],
    };
    content.observation.regions.reverse();
    render(
      <SourceUnderstandingContent understanding={content} language="en" />,
    );
    const table = screen.getByRole("table", { name: "Source detail 2" });
    expect(within(table).getAllByRole("cell")).toHaveLength(3);
    expect(within(table).getByText("6").closest("td")).toHaveAttribute(
      "rowspan",
      "2",
    );
  });

  it("does not turn absent interpretation or uncertainty into a quality pass", () => {
    const content = understandingFixture();
    content.observation.language = "und";
    content.observation.regions = [
      {
        ...content.observation.regions[0],
        kind: "decorative_image",
        exact_text: "",
      },
    ];
    content.observation.relationships = [];
    content.education.claims = [];
    content.uncertainties = [];
    render(<SourceUnderstandingContent understanding={content} />);
    expect(screen.getByText("No teaching points proposed yet.")).toBeVisible();
    expect(
      screen.getByText(
        "No uncertainties were recorded. Compare with the original anyway.",
      ),
    ).toBeVisible();
    expect(
      screen.getByText("Visual detail without readable text"),
    ).toBeVisible();
  });

  it("shows an unspecified reference instead of inventing a source detail", () => {
    const content = understandingFixture();
    content.uncertainties[0].region_keys = ["unavailable"];
    content.uncertainties[0].alternatives = [];
    content.observation.regions[2].visual_facts[0].group_count = null;
    render(
      <SourceUnderstandingContent understanding={content} language="en" />,
    );
    expect(screen.getByText("Unspecified source detail")).toBeVisible();
    expect(screen.getByText("Not recorded")).toBeVisible();
  });

  it("keeps diagnostics closed and satisfies basic accessible structure", async () => {
    const { container } = render(
      <SourceUnderstandingContent
        understanding={understandingFixture()}
        language="en"
      />,
    );
    const details = screen.getByText("Technical details").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(
      (
        await axe.run(container, {
          rules: { "color-contrast": { enabled: false } },
        })
      ).violations,
    ).toEqual([]);
  });
});
