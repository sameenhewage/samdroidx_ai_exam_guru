import type { components } from "@exam-guru/api-client";
import { describe, expect, it } from "vitest";

import { hasIntent, readiness } from "./material-curriculum-review-state";

type Indexing = components["schemas"]["MaterialKnowledgeIndexingStatus"];
const intentId = "00000000-0000-0000-0000-000000005301";

function status(overrides: Partial<Indexing> = {}): Indexing {
  return {
    intent_id: null,
    version: null,
    status: "ready",
    ready: true,
    retry_allowed: false,
    ...overrides,
  };
}

describe("material indexing readiness contract", () => {
  it.each([
    { intent_id: null, version: null },
    { intent_id: intentId, version: 0 },
  ])(
    "accepts current authorized vectors without inventing enrollment: %j",
    (identity) => {
      const value = status(identity);
      expect(readiness(value, true, true, true)).toBe("ready");
      expect(value).toEqual(status(identity));
    },
  );

  it("recognizes a new intent at its valid initial version", () => {
    expect(hasIntent(status({ intent_id: intentId, version: 0 }))).toBe(true);
    expect(hasIntent(status({ intent_id: intentId, version: -1 }))).toBe(false);
    expect(hasIntent(status())).toBe(false);
  });

  it("does not replace source, review or projection authority with a readiness label", () => {
    const value = status({ intent_id: intentId, version: 1 });
    expect(readiness(value, true, false, true)).not.toBe("ready");
    expect(readiness(value, true, true, false)).not.toBe("ready");
    expect(readiness(value, false, true, true)).not.toBe("ready");
    expect(readiness(status({ status: "queued" }), true, true, true)).toBe(
      "inconsistent",
    );
    expect(readiness(status({ ready: false }), true, true, true)).toBe(
      "inconsistent",
    );
    for (const ready of [false, true]) {
      expect(
        readiness(
          status({ status: "unrecognized" as Indexing["status"], ready }),
          true,
          true,
          true,
        ),
      ).toBe("inconsistent");
    }
  });
});
