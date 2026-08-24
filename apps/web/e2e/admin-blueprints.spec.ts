import type { components } from "@exam-guru/api-client";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

type Exam = components["schemas"]["ExamConfigurationResponse"];
type Medium = components["schemas"]["MediumResponse"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
type Blueprint = components["schemas"]["PaperBlueprintResponse"];
type BlueprintSummary = components["schemas"]["PaperBlueprintSummaryResponse"];
type BlueprintRequest = components["schemas"]["BlueprintCreateRequest"];

async function login(page: Page, role: "admin" | "reviewer") {
  await page.goto("/admin/login");
  await page.getByRole("button", { name: `Continue as ${role}` }).click();
  await expect(page).toHaveURL(/\/admin\/curriculum$/);
}

async function postCreated<ResponseDto>(
  request: APIRequestContext,
  path: string,
  data: object,
): Promise<ResponseDto> {
  const response = await request.post(path, { data });
  expect(response.status()).toBe(201);
  return (await response.json()) as ResponseDto;
}

function repeatRequest(value: Blueprint): BlueprintRequest {
  return {
    analytics_run_id: value.analytics_run_id,
    seed: value.seed,
    specification: {
      ...value.specification,
      taxonomy_requirements: value.specification.taxonomy_requirements.map((requirement) => ({
        allowed_section_ids: requirement.allowed_section_ids,
        generation_instructions: requirement.generation_instructions,
        maximum_slots: requirement.maximum_slots,
        minimum_slots: requirement.minimum_slots,
        priority: {
          baseline_evidence_refs: requirement.priority.baseline_evidence_refs,
          baseline_score: requirement.priority.baseline_score,
          baseline_version: requirement.priority.baseline_version,
        },
        retrieval_query_hints: requirement.retrieval_query_hints,
        target: requirement.target,
      })),
    },
  };
}

test("admin generates a real exact immutable blueprint and reviewer inspects it read-only", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  const unique = `${Date.now()}`.slice(-9);
  const curriculumTitle = `Blueprint curriculum ${unique}`;
  const paperCode = `BP-${unique}`;
  await login(page, "admin");

  const exam = await postCreated<Exam>(page.request, "/api/v1/admin/exam-configurations", {
    code: `BE${unique}`,
    grade: 5,
    name: `Blueprint exam ${unique}`,
  });
  const medium = await postCreated<Medium>(page.request, "/api/v1/admin/media", {
    code: `bp${unique.slice(-6)}`,
    name: `Blueprint medium ${unique}`,
  });
  const curriculum = await postCreated<Curriculum>(
    page.request,
    "/api/v1/admin/curriculum-versions",
    {
      code: `BC-${unique}`,
      exam_configuration_id: exam.id,
      medium_id: medium.id,
      title: curriculumTitle,
    },
  );
  const competency = await postCreated<TaxonomyNode>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes`,
    {
      active: true,
      code: `C${unique}`,
      level: "competency",
      parent_id: null,
      title: `Reasoning competency ${unique}`,
    },
  );
  expect(
    (
      await page.request.post(
        `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes/${competency.id}/review`,
      )
    ).ok(),
  ).toBe(true);
  const skill = await postCreated<TaxonomyNode>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes`,
    {
      active: true,
      code: `S${unique}`,
      level: "skill",
      parent_id: competency.id,
      title: `Pattern skill ${unique}`,
    },
  );
  expect(
    (
      await page.request.post(
        `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes/${skill.id}/review`,
      )
    ).ok(),
  ).toBe(true);

  await page.goto("/admin/blueprints");
  await expect(page.getByRole("heading", { name: "Blueprint Studio" })).toBeVisible();
  await page.getByLabel("Active Grade 5 curriculum").selectOption(curriculum.id);
  await expect(page.getByRole("heading", { name: "No blueprints yet" })).toBeVisible();
  await page.getByLabel("Taxonomy target 1", { exact: true }).selectOption(skill.id);
  await page.getByLabel("Paper code").fill(paperCode);
  await page.getByLabel("Paper title").fill(`Exact practice paper ${unique}`);
  await page.getByLabel("Deterministic seed").fill("2026");
  await expect(page.getByLabel(/forecast/i)).toHaveCount(0);
  await page.getByRole("button", { name: "Generate immutable blueprint" }).click();

  await expect(page.getByText("Blueprint created and persisted.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Immutable blueprint snapshot" })).toBeVisible();
  await expect(page.getByText(/immutable and cannot be edited/i)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Exact generation slots" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Reviewed taxonomy snapshot" })).toBeVisible();

  const listResponse = await page.request.get(
    `/api/v1/admin/curricula/${curriculum.id}/blueprints`,
  );
  expect(listResponse.ok()).toBe(true);
  const summaries = (await listResponse.json()) as BlueprintSummary[];
  const persisted = summaries.find((item) => item.paper_code === paperCode);
  expect(persisted).toBeDefined();
  const detailResponse = await page.request.get(
    `/api/v1/admin/curricula/${curriculum.id}/blueprints/${persisted?.id}`,
  );
  expect(detailResponse.ok()).toBe(true);
  const blueprint = (await detailResponse.json()) as Blueprint;
  expect(blueprint.slot_count).toBe(1);
  expect(blueprint.total_marks).toBe(2);
  expect(blueprint.blueprint.slots[0]?.marks).toBe(2);
  await expect(page.getByText(blueprint.blueprint.slots[0]?.slot_id ?? "missing-slot")).toBeVisible();
  await expect(
    page.getByText("Schema version", { exact: true }).locator("..").locator("dd"),
  ).toHaveText(blueprint.schema_version);
  await expect(
    page.getByText("Algorithm version", { exact: true }).locator("..").locator("dd"),
  ).toHaveText(blueprint.algorithm_version);
  await expect(
    page.getByText("Input fingerprint", { exact: true }).locator("..").locator("dd"),
  ).toHaveText(blueprint.input_fingerprint);

  await page.getByRole("button", { name: "Generate immutable blueprint" }).click();
  await expect(
    page.getByText("Existing identical blueprint selected; no duplicate was created."),
  ).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await login(page, "reviewer");
  await page.goto("/admin/blueprints");
  await page.getByLabel("Active Grade 5 curriculum").selectOption(curriculum.id);
  await expect(page.getByText("Reviewer read access")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Reviewer read-only mode" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Generate immutable blueprint" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Immutable blueprint snapshot" })).toBeVisible();
  await expect(page.getByText(blueprint.blueprint.slots[0]?.slot_id ?? "missing-slot")).toBeVisible();
  await expect(page.getByText(skill.title, { exact: true })).toBeVisible();

  const denied = await page.request.post(
    `/api/v1/admin/curricula/${curriculum.id}/blueprints`,
    { data: repeatRequest(blueprint) },
  );
  expect(denied.status()).toBe(403);
  expect(browserErrors).toEqual([]);
});
