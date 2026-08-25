import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const composePath = resolve(process.cwd(), "../../compose.yaml");
const dockerfilePath = resolve(process.cwd(), "Dockerfile");

describe("local web identity configuration", () => {
  it("keeps Compose explicitly deterministic and passes the mode through the image", () => {
    const compose = readFileSync(composePath, "utf8");
    const dockerfile = readFileSync(dockerfilePath, "utf8");

    expect(compose).toMatch(/web:\n[\s\S]*?WEB_IDENTITY_PROVIDER: deterministic/);
    expect(compose).toMatch(/web:\n[\s\S]*?ENABLE_DETERMINISTIC_IDENTITY: "true"/);
    expect(dockerfile).toContain("ARG WEB_IDENTITY_PROVIDER=deny");
    expect(dockerfile).toContain("WEB_IDENTITY_PROVIDER=${WEB_IDENTITY_PROVIDER}");
  });
});
