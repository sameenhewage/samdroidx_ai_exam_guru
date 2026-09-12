import type { components } from "@exam-guru/api-client";
import { fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import {
  correctionIsComplete,
  SourceUnderstandingEditor,
} from "./source-understanding-editor";

type Understanding = components["schemas"]["PageUnderstanding"];

function content(): Understanding {
  return {
    schema_version: "page-understanding.v1",
    observation: {
      language: "en",
      relationships: [],
      regions: [
        {
          key: "grid",
          kind: "grid",
          reading_order: 0,
          parent_key: null,
          bounds: null,
          polygon: [],
          exact_text: "Printed source",
          equations: ["6 × 2 = 8"],
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
          visual_facts: [],
        },
        {
          key: "picture",
          kind: "repeated_object_group",
          reading_order: 1,
          parent_key: null,
          bounds: null,
          polygon: [],
          exact_text: "",
          equations: [],
          table: null,
          visual_facts: [
            {
              key: "groups",
              description: "Six groups of two shoes",
              group_count: 6,
              items_per_group: 2,
              printed_total: "8",
            },
          ],
        },
      ],
    },
    education: {
      claims: [
        {
          key: "pairs",
          kind: "skill",
          description: "Count in pairs.",
          region_keys: ["picture"],
        },
      ],
    },
    uncertainties: [
      {
        key: "uncertain",
        region_keys: ["grid"],
        field: "cell",
        reason: "Check the blank cell.",
        alternatives: [],
      },
    ],
  };
}

function Harness({
  original = content(),
  disabled = false,
}: {
  original?: Understanding;
  disabled?: boolean;
}) {
  const [value, setValue] = useState(original);
  return (
    <>
      <SourceUnderstandingEditor
        value={value}
        onChange={setValue}
        language="en"
        disabled={disabled}
      />
      <output data-testid="draft">{JSON.stringify(value)}</output>
    </>
  );
}
function draft(): Understanding {
  return JSON.parse(screen.getByTestId("draft").textContent!) as Understanding;
}

describe("structured source correction", () => {
  it("changes literal Unicode and printed equations without mutating the original or solving arithmetic", () => {
    const original = content();
    render(<Harness original={original} />);
    fireEvent.change(
      screen.getByRole("textbox", { name: "Source text — detail 1" }),
      { target: { value: "ශ්‍රී ලංකාව a\u0301" } },
    );
    expect(draft().observation.regions[0].exact_text).toBe(
      "ශ්‍රී ලංකාව a\u0301",
    );
    expect(draft().observation.regions[0].equations).toEqual(["6 × 2 = 8"]);
    expect(original.observation.regions[0].exact_text).toBe("Printed source");
    expect(draft().uncertainties).toEqual(original.uncertainties);
  });

  it("requires explicit cell state before entering a value and never fills a blank answer", () => {
    render(<Harness />);
    const cell = screen.getByRole("textbox", {
      name: "Cell text — detail 1, row 1, column 2",
    });
    expect(cell).toBeDisabled();
    expect(cell).toHaveValue("");
    fireEvent.change(
      screen.getByRole("combobox", {
        name: "Cell state — detail 1, row 1, column 2",
      }),
      { target: { value: "visible" } },
    );
    expect(cell).toBeEnabled();
    expect(correctionIsComplete(draft())).toBe(false);
    fireEvent.change(cell, { target: { value: "9" } });
    expect(draft().observation.regions[0].table!.cells[1].exact_text).toBe("9");
    fireEvent.change(
      screen.getByRole("combobox", {
        name: "Cell state — detail 1, row 1, column 2",
      }),
      { target: { value: "unreadable" } },
    );
    expect(draft().observation.regions[0].table!.cells[1]).toMatchObject({
      state: "unreadable",
      exact_text: "",
    });
    expect(correctionIsComplete(draft())).toBe(true);
  });

  it("keeps visible counts separate from printed totals", () => {
    render(<Harness />);
    fireEvent.change(
      screen.getByRole("spinbutton", {
        name: "Visible groups — detail 2, picture 1",
      }),
      { target: { value: "5" } },
    );
    expect(draft().observation.regions[1].visual_facts[0]).toMatchObject({
      group_count: 5,
      items_per_group: 2,
      printed_total: "8",
    });
    fireEvent.change(
      screen.getByRole("spinbutton", {
        name: "Items per group — detail 2, picture 1",
      }),
      { target: { value: "" } },
    );
    expect(
      draft().observation.regions[1].visual_facts[0].items_per_group,
    ).toBeNull();
  });

  it("edits proposed meaning independently and requires an explicit source link for a new teaching point", () => {
    const original = content();
    render(<Harness original={original} />);
    fireEvent.change(
      screen.getByRole("textbox", { name: "Teaching point 1" }),
      { target: { value: "Recognise equal groups." } },
    );
    expect(draft().observation).toEqual(original.observation);
    fireEvent.click(screen.getByRole("button", { name: "Add teaching point" }));
    expect(correctionIsComplete(draft())).toBe(false);
    fireEvent.change(
      screen.getByRole("textbox", { name: "Teaching point 2" }),
      { target: { value: "Read a printed equation." } },
    );
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "Source detail 1 for teaching point 2",
      }),
    );
    expect(draft().education.claims[1].region_keys).toEqual(["grid"]);
    expect(correctionIsComplete(draft())).toBe(true);
    fireEvent.click(
      screen.getByRole("button", { name: "Remove teaching point 2" }),
    );
    expect(draft().education.claims).toHaveLength(1);
  });

  it("retains bounded incomplete states for server validation instead of inventing values", () => {
    const invalid = content();
    invalid.observation.regions[1].visual_facts[0].group_count = -1;
    expect(correctionIsComplete(invalid)).toBe(false);
    invalid.observation.regions[1].visual_facts[0].group_count = 0;
    invalid.education.claims[0].region_keys = ["missing-region"];
    expect(correctionIsComplete(invalid)).toBe(false);
    invalid.education.claims[0].region_keys = ["grid"];
    invalid.observation.regions[0].exact_text = "x".repeat(1_048_577);
    expect(correctionIsComplete(invalid)).toBe(false);
  });

  it("exposes labelled editable structure without automated accessibility violations", async () => {
    const { container } = render(<Harness />);
    expect(
      (
        await axe.run(container, {
          rules: { "color-contrast": { enabled: false } },
        })
      ).violations,
    ).toEqual([]);
  });

  it("disables editing while a versioned correction is being saved", () => {
    const { container } = render(<Harness disabled />);
    for (const control of container.querySelectorAll(
      "input, textarea, select, button",
    ))
      expect(control).toBeDisabled();
  });
});
