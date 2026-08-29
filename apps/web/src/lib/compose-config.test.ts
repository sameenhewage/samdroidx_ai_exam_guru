import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const ciPath = resolve(process.cwd(), "../../.github/workflows/ci.yml");
const composePath = resolve(process.cwd(), "../../compose.yaml");
const dockerfilePath = resolve(process.cwd(), "Dockerfile");
const packagePath = resolve(process.cwd(), "../../package.json");
const playwrightPath = resolve(process.cwd(), "playwright.config.ts");
const runnerPath = resolve(process.cwd(), "../../scripts/run_isolated_e2e.sh");

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

describe("isolated browser acceptance runtime", () => {
  it("parameterizes the environment and browser origin instead of fixing the normal Studio runtime", () => {
    const compose = readFileSync(composePath, "utf8");

    expect(compose).toContain("EXAM_GURU_ENVIRONMENT: ${EXAM_GURU_ENVIRONMENT:-local}");
    expect(compose).toContain("APP_BASE_URL: ${APP_BASE_URL:-http://localhost:3000}");
  });

  it("runs browser acceptance through a fail-closed throwaway Compose project", () => {
    const ci = readFileSync(ciPath, "utf8");
    const packageConfig = JSON.parse(readFileSync(packagePath, "utf8")) as {
      scripts: Record<string, string>;
    };
    const playwright = readFileSync(playwrightPath, "utf8");
    const runner = readFileSync(runnerPath, "utf8");

    expect(packageConfig.scripts["test:e2e:isolated"]).toBe("bash scripts/run_isolated_e2e.sh");
    expect(playwright).toContain("requireIsolatedE2ERuntime(process.env)");
    expect(runner).toContain("docker compose --project-name \"$project_name\"");
    expect(runner).toContain("EXAM_GURU_DATA_PATH=\"$data_path\"");
    expect(runner).toContain("E2E_RUNTIME_ISOLATED=true");
    expect(runner).toContain("down --volumes --remove-orphans");
    expect(ci).toContain("npm run test:e2e:isolated");
    expect(ci).not.toContain("run: npm run test:e2e --prefix apps/web");
  });
});
