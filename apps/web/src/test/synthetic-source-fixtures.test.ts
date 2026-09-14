import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";

import {
  syntheticGenerationSource,
  syntheticHistoricalQuestion,
  syntheticTextPdf,
} from "../../e2e/helpers/teacher-content-studio";

const nonces = ["dcjfhccgf", "dchgdjdjf"] as const;
const sha256 = (bytes: Buffer) =>
  createHash("sha256").update(bytes).digest("hex");
const visiblePdf = (bytes: Buffer) =>
  bytes.toString("ascii").split("%%EOF\n")[0];

it.each(nonces)(
  "keeps the generation source stable with identity %s",
  (identity) => {
    const source = syntheticGenerationSource(identity);
    expect(source.retrievalMarker).toBe("Human correction");
    expect(source.forbiddenMarker).toBe("Incorrect example");
    expect(source.correctedText).toBe(
      "Human correction: Four is an even number.",
    );
    expect(source.forbiddenText).toBe("Incorrect example: Four is odd.");
    expect(source.fixture.text).toBe(
      `${source.correctedText}\n${source.forbiddenText}`,
    );
    expect(source.correctedText).toContain(source.retrievalMarker);
    expect(source.correctedText).not.toContain(source.forbiddenMarker);
    expect(source.forbiddenText).toContain(source.forbiddenMarker);
    expect(source.forbiddenText).not.toContain(source.retrievalMarker);
    expect(visiblePdf(source.fixture.bytes)).not.toContain(identity);
    expect(source.fixture.bytes.toString("ascii")).toContain(
      `% fixture-identity: ${identity}\n`,
    );
    const other = syntheticGenerationSource(`${identity}-different-scope`);
    expect(other.fixture.text).toBe(source.fixture.text);
    expect(visiblePdf(other.fixture.bytes)).toBe(
      visiblePdf(source.fixture.bytes),
    );
    expect(sha256(other.fixture.bytes)).not.toBe(sha256(source.fixture.bytes));
  },
);

it.each([2019, 2020])(
  "keeps historical %i source text stable across scopes",
  (year) => {
    const [first, second] = nonces.map((identity) =>
      syntheticHistoricalQuestion(year, identity),
    );
    expect(first.text).toBe(
      `Historical choice ${year}: A three; B four; answer B.`,
    );
    expect(second.text).toBe(first.text);
    expect(visiblePdf(first.bytes)).toBe(visiblePdf(second.bytes));
    expect(sha256(first.bytes)).not.toBe(sha256(second.bytes));
    for (const [index, fixture] of [first, second].entries()) {
      expect(visiblePdf(fixture.bytes)).not.toContain(nonces[index]);
      expect(fixture.bytes.toString("ascii")).toContain(
        `% fixture-identity: ${nonces[index]}\n`,
      );
    }
  },
);

it("appends identity without altering the visible stream, lengths or xref offsets", () => {
  const lines = ["Read (one) \\ example.", "Four is an even number."];
  const original = syntheticTextPdf(lines);
  const identified = syntheticTextPdf(lines, nonces[0]);
  expect(identified.text).toBe(original.text);
  expect(identified.bytes.subarray(0, original.bytes.length)).toEqual(
    original.bytes,
  );
  expect(
    identified.bytes.subarray(original.bytes.length).toString("ascii"),
  ).toBe(`% fixture-identity: ${nonces[0]}\n`);
  expect(sha256(identified.bytes)).not.toBe(sha256(original.bytes));
});

it.each([
  [
    "dcjfhccgf",
    "d445be64bc2282a31a8c1cfc57e523facfb27985bf3b186f4006c7e0e1b01488",
  ],
  [
    "dchgdjdjf",
    "b064e11866c4e776b6a062e4240c6b7a5d358d71b00424594710b3dce713ad17",
  ],
])(
  "retains the exact rejected printed-noise fixture %s without sanitization",
  (identity, expectedSha) => {
    const lines = [
      `Human correction human-even-correction-${identity}: Four is an even number.`,
      `Incorrect example: Four is odd. uncorrected-odd-${identity}`,
    ];
    const original = syntheticTextPdf(lines);
    expect(original.bytes.length).toBe(719);
    expect(sha256(original.bytes)).toBe(expectedSha);
    const identified = syntheticTextPdf(lines, "separate-identity");
    expect(identified.text).toBe(lines.join("\n"));
    expect(visiblePdf(identified.bytes)).toBe(visiblePdf(original.bytes));
    expect(identified.text).toContain(identity);
  },
);

describe("bounded single-line ASCII identity comments", () => {
  it.each([
    "",
    "x".repeat(129),
    "a\nb",
    "a\rb",
    "a\tb",
    "a\u0000b",
    "a\u007fb",
    "ආ",
  ])("rejects invalid identity %j", (identity) => {
    expect(() => syntheticTextPdf(["Four is even."], identity)).toThrow(
      "Synthetic PDF identity",
    );
  });
  it("accepts the inclusive limit without expanding the printed page", () => {
    const identity = "x".repeat(128);
    const fixture = syntheticTextPdf(["Four is even."], identity);
    expect(fixture.text).toBe("Four is even.");
    expect(visiblePdf(fixture.bytes)).not.toContain(identity);
    expect(fixture.bytes.toString("ascii")).toContain(
      `% fixture-identity: ${identity}\n`,
    );
  });
});
