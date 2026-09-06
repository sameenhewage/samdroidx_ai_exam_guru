import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const require = createRequire(import.meta.url);
const webRoot = resolve(import.meta.dirname, "../..");

describe("self-hosted font license distribution", () => {
  it.each(["noto-sans-sinhala", "noto-sans-tamil"])(
    "ships the installed %s license verbatim",
    (name) => {
      const installed = readFileSync(
        require.resolve(`@fontsource/${name}/LICENSE`),
        "utf8",
      );
      const distributed = readFileSync(
        resolve(webRoot, `public/licenses/${name}-LICENSE.txt`),
        "utf8",
      );
      expect(distributed).toBe(installed);
      expect(distributed).toContain("SIL OPEN FONT LICENSE Version 1.1");
      expect(distributed).toContain("The Noto Project Authors");
    },
  );

  it("copies public license resources into the standalone runtime image", () => {
    expect(readFileSync(resolve(webRoot, "Dockerfile"), "utf8")).toContain(
      "COPY --from=builder --chown=node:node /app/apps/web/public ./apps/web/public",
    );
  });
});
