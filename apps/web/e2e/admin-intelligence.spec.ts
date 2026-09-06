import type { components } from "@exam-guru/api-client";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

import { openAdvancedArea } from "./helpers/advanced-navigation";
import {
  assertVerifiedRecord,
  confirmSyntheticPage,
  readSyntheticSource,
  seedAdmittedScope,
  syntheticTextPdf,
  SYNTHETIC_WORKFLOW_EVIDENCE,
} from "./helpers/teacher-content-studio";

type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
type SourceDocument = components["schemas"]["SourceDocumentResponse"];
type SourcePage = components["schemas"]["SourcePageResponse"];
type ExtractedBlock = components["schemas"]["ExtractedBlockResponse"];
type HistoricalQuestion = components["schemas"]["HistoricalQuestionResponse"];

async function login(page: Page, role: "admin" | "reviewer") {
  await page.goto("/admin/login");
  await page.getByRole("button", { name: `Continue as ${role}` }).click();
  await expect(page).toHaveURL(/\/admin\/home$/);
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

async function createReviewedHistoricalQuestion({
  competencyId,
  curriculumId,
  marker,
  page,
  paperCode,
  skillId,
  year,
}: {
  competencyId: string;
  curriculumId: string;
  marker: string;
  page: Page;
  paperCode: string;
  skillId: string;
  year: number;
}) {
  const questionText = `Reviewed geometry question ${year} ${marker}`;
  const fixture = syntheticTextPdf([questionText]);
  const upload = await page.request.post("/api/v1/admin/source-documents", {
    multipart: {
      curriculum_version_id: curriculumId,
      document_type: "past_paper",
      intake_metadata: JSON.stringify({
        candidate_grade: 5,
        medium_label: "English",
        subject_label: "Mathematics",
        year,
        evidence: [SYNTHETIC_WORKFLOW_EVIDENCE],
      } satisfies components["schemas"]["SourceIntakeMetadata"]),
      file: {
        buffer: fixture.bytes,
        mimeType: "application/pdf",
        name: `analytics-${year}-${marker}.pdf`,
      },
      paper_code: paperCode,
      year: String(year),
    },
  });
  expect(upload.status()).toBe(201);
  const source = (await upload.json()) as SourceDocument;

  const queued = await page.request.post(
    `/api/v1/admin/source-documents/${source.id}/extract`,
  );
  expect(queued.status()).toBe(202);
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          "/api/v1/admin/source-documents",
        );
        expect(response.ok()).toBe(true);
        const documents = (await response.json()) as SourceDocument[];
        return documents.find((document) => document.id === source.id)
          ?.extraction_status;
      },
      { timeout: 30_000 },
    )
    .toBe("extracted");
  expect(
    (
      await page.request.post(
        `/api/v1/admin/source-documents/${source.id}/review`,
      )
    ).ok(),
  ).toBe(true);
  const candidate = await readSyntheticSource(
    page.request,
    source,
    fixture.text,
  );
  const verifiedPage = await confirmSyntheticPage(
    page.request,
    source.id,
    candidate,
    fixture.text,
  );
  expect(
    (
      await page.request.post(
        `/api/v1/admin/source-documents/${source.id}/trust`,
      )
    ).ok(),
  ).toBe(true);

  const pagesResponse = await page.request.get(
    `/api/v1/admin/source-documents/${source.id}/pages`,
  );
  expect(pagesResponse.ok()).toBe(true);
  const [sourcePage] = (await pagesResponse.json()) as SourcePage[];
  if (!sourcePage) throw new Error("Expected an extracted source page");
  const blocksResponse = await page.request.get(
    `/api/v1/admin/source-documents/${source.id}/pages/${sourcePage.page_number}/blocks`,
  );
  expect(blocksResponse.ok()).toBe(true);
  const [sourceBlock] = (await blocksResponse.json()) as ExtractedBlock[];
  if (!sourceBlock) throw new Error("Expected an extracted source block");

  const question = await postCreated<HistoricalQuestion>(
    page.request,
    `/api/v1/admin/curricula/${curriculumId}/knowledge/questions`,
    {
      difficulty_confidence: 0.9,
      difficulty_label: "medium",
      difficulty_source: "reviewer_confirmed",
      marks: 2,
      page_number: sourcePage.page_number,
      paper_code: paperCode,
      question_number: `Q-${year}`,
      question_type: "multiple_choice",
      source_block_id: sourceBlock.id,
      source_document_id: source.id,
      text: questionText,
      year,
    } satisfies components["schemas"]["HistoricalQuestionImportRequest"],
  );
  expect(question.text).toBe(questionText);
  await assertVerifiedRecord(
    page.request,
    "historical_question",
    question,
    verifiedPage,
    fixture.text,
  );
  const classifiedResponse = await page.request.patch(
    `/api/v1/admin/curricula/${curriculumId}/knowledge/questions/${question.id}/classification`,
    {
      data: {
        competency_id: competencyId,
        expected_version: question.version,
        learning_concept_id: null,
        skill_id: skillId,
        sub_skill_id: null,
      } satisfies components["schemas"]["KnowledgeClassificationRequest"],
    },
  );
  expect(classifiedResponse.ok()).toBe(true);
  const classified = (await classifiedResponse.json()) as HistoricalQuestion;
  const inReviewResponse = await page.request.post(
    `/api/v1/admin/curricula/${curriculumId}/knowledge/questions/${question.id}/review`,
    {
      data: {
        expected_version: classified.version,
        target: "in_review",
      } satisfies components["schemas"]["KnowledgeReviewTransitionRequest"],
    },
  );
  expect(inReviewResponse.ok()).toBe(true);
  const inReview = (await inReviewResponse.json()) as HistoricalQuestion;
  const reviewedResponse = await page.request.post(
    `/api/v1/admin/curricula/${curriculumId}/knowledge/questions/${question.id}/review`,
    {
      data: {
        expected_version: inReview.version,
        target: "reviewed",
      } satisfies components["schemas"]["KnowledgeReviewTransitionRequest"],
    },
  );
  expect(reviewedResponse.ok()).toBe(true);
  return { questionId: question.id, sourceId: source.id };
}

test("admin runs a real held-out report; reviewer reads it and sees actionable RAG embedding readiness", async ({
  page,
}) => {
  test.setTimeout(180_000);
  test.info().annotations.push({
    type: "limitation",
    description:
      "Disposable synthetic workflow mechanics only, not real historical or forecast quality.",
  });
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  const unique = `${Date.now()}`.slice(-9);
  await login(page, "admin");

  const { curriculum } = await seedAdmittedScope(page.request);
  const competency = await postCreated<TaxonomyNode>(
    page.request,
    `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes`,
    {
      active: true,
      code: `C${unique}`,
      level: "competency",
      parent_id: null,
      title: `Geometry competency ${unique}`,
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
      title: `Polygon skill ${unique}`,
    },
  );
  expect(
    (
      await page.request.post(
        `/api/v1/admin/curricula/${curriculum.id}/taxonomy/nodes/${skill.id}/review`,
      )
    ).ok(),
  ).toBe(true);

  const evidence2019 = await createReviewedHistoricalQuestion({
    competencyId: competency.id,
    curriculumId: curriculum.id,
    marker: unique,
    page,
    paperCode: `P19-${unique}`,
    skillId: skill.id,
    year: 2019,
  });
  const evidence2020 = await createReviewedHistoricalQuestion({
    competencyId: competency.id,
    curriculumId: curriculum.id,
    marker: unique,
    page,
    paperCode: `P20-${unique}`,
    skillId: skill.id,
    year: 2020,
  });

  await page.goto("/admin/analytics");
  await expect(
    page.getByRole("heading", { name: "Analytics Report Studio" }),
  ).toBeVisible();
  await page
    .getByLabel("Active analytics curriculum")
    .selectOption(curriculum.id);
  await expect(
    page.getByRole("heading", { name: "No analytics runs yet" }),
  ).toBeVisible();
  await page.getByLabel("Minimum training years").fill("1");
  await page.getByLabel("Top skills to evaluate").fill("1");
  await page.getByLabel("Meaningful improvement numerator").fill("1");
  await page.getByLabel("Meaningful improvement denominator").fill("100");
  await page.getByRole("button", { name: "Run analysis" }).click();

  await expect(page.getByText("Analysis run created.")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Analysis report" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Historical distributions" }),
  ).toBeVisible();
  const heldoutWindows = page
    .getByRole("heading", { name: "Rolling held-out windows" })
    .locator("..")
    .locator("..");
  await expect(heldoutWindows).toBeVisible();
  await expect(heldoutWindows.getByText("Leakage audit passed")).toBeVisible();
  await expect(heldoutWindows.getByText("Training years: 2019")).toBeVisible();
  await expect(
    heldoutWindows.getByRole("heading", { name: "Holdout 2020" }),
  ).toBeVisible();
  await expect(
    page.getByText(evidence2019.sourceId, { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText(evidence2020.sourceId, { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText(/does not predict future exam questions/i),
  ).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await login(page, "reviewer");
  await page.goto("/admin/analytics");
  await page
    .getByLabel("Active analytics curriculum")
    .selectOption(curriculum.id);
  await expect(page.getByText("Reviewer read access")).toBeVisible();
  await expect(page.getByRole("button", { name: "Run analysis" })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("heading", { name: "Analysis report" }),
  ).toBeVisible();

  await openAdvancedArea(page, "Knowledge / RAG");
  await expect(page).toHaveURL(/\/admin\/retrieval$/);
  await page
    .getByLabel("Active retrieval curriculum")
    .selectOption(curriculum.id);
  await expect(
    page.getByRole("heading", { name: "No persisted embeddings available" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Review knowledge records" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run retrieval" }),
  ).toBeDisabled();

  expect(evidence2019.questionId).not.toBe(evidence2020.questionId);
  expect(
    browserErrors.filter(
      (error) => !error.includes("eval() is not supported in this environment"),
    ),
  ).toEqual([]);
});
