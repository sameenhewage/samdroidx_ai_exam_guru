import { expect, test, type Locator, type Page } from "@playwright/test";

import {
  installTeacherStudioFixture,
  loginAs,
  syntheticPdf,
  type TeacherStudioFixture,
} from "./helpers/teacher-content-studio";

test.describe.configure({ timeout: 30_000 });

async function authenticatedFixture(
  page: Page,
  role: "admin" | "reviewer" = "admin",
): Promise<TeacherStudioFixture> {
  await loginAs(page, role);
  return installTeacherStudioFixture(page);
}

async function openGradeFiveMaths(page: Page) {
  await page.goto("/admin/materials");
  await expect(page.getByRole("heading", { level: 1, name: "Materials" })).toBeVisible();
  const overview = page.getByRole("region", { name: "Materials by grade" });
  await overview.getByRole("button", { name: /Grade 5/i }).click();
  await page.getByLabel("Subject").selectOption({ label: "Maths" });
  await expect(page.getByRole("region", { name: "Uploaded materials" })).toBeVisible();
}

async function continueUpload(dialog: Locator) {
  await dialog.getByRole("button", { name: "Continue" }).click();
}

async function openUploadAtPdf(page: Page): Promise<Locator> {
  await page.getByRole("button", { name: "Upload material" }).click();
  const dialog = page.getByRole("dialog", { name: "Upload material" });
  await expect(dialog).toBeVisible();

  await dialog.getByLabel("Grade").selectOption("5");
  await continueUpload(dialog);
  await dialog.getByLabel("Medium").selectOption({ label: "English" });
  await continueUpload(dialog);
  await dialog.getByLabel("Subject").selectOption({ label: "Maths" });
  await continueUpload(dialog);
  await dialog.getByLabel("Material type").selectOption("past_paper");
  await continueUpload(dialog);
  await dialog.getByLabel("Year").fill("2026");
  await continueUpload(dialog);
  return dialog;
}

async function chooseGradeSevenMaths(page: Page) {
  await page.getByLabel("Grade").selectOption("7");
  await page.getByLabel("Medium").selectOption("en");
  await page.getByLabel("Subject").selectOption("MATHEMATICS");
  await page.getByLabel("Paper type").selectOption("SCHOOL-G7");
  await page.getByRole("button", { name: "Continue to scope" }).click();
}

async function choosePaperSettings(page: Page) {
  await page.getByRole("button", { name: "Continue to paper settings" }).click();
  await page.getByLabel("Number of questions").fill("12");
  await page.getByLabel("Duration in minutes").fill("50");
  await page.getByLabel("Difficulty").selectOption("balanced");
  await page.getByRole("button", { name: "Generate paper" }).click();
}

function generationIntent(fixture: TeacherStudioFixture) {
  const value = fixture.generationIntents[0];
  if (!value || typeof value !== "object") throw new Error("Expected one generation intent");
  return value;
}

test("contract 1: Materials overview shows Grades 1–13 with useful counts", async ({ page }) => {
  await authenticatedFixture(page, "reviewer");
  await page.goto("/admin/materials");

  await expect(page.getByRole("heading", { level: 1, name: "Materials" })).toBeVisible();
  const overview = page.getByRole("region", { name: "Materials by grade" });
  await expect(overview).toBeVisible();
  for (let grade = 1; grade <= 13; grade += 1) {
    await expect(overview.getByRole("button", { name: new RegExp(`Grade ${grade}\\b`) })).toBeVisible();
  }
  await expect(overview.getByRole("button", { name: /Grade 5/i })).toContainText("4 materials");
  await expect(overview.getByRole("button", { name: /Grade 5/i })).toContainText("1 subject");
  await expect(overview.getByRole("button", { name: /Grade 5/i })).toContainText("1 Ready");
  await expect(overview.getByRole("button", { name: /Grade 5/i })).toContainText("1 Needs review");
});

test("contract 2: Grade 5 opens a readable uploaded-material list", async ({ page }) => {
  await authenticatedFixture(page, "reviewer");
  await openGradeFiveMaths(page);

  const list = page.getByRole("region", { name: "Uploaded materials" });
  await expect(list.getByText("grade-5-maths-syllabus.pdf")).toBeVisible();
  await expect(list).toContainText("Grade 5 · Maths · syllabus");
  await expect(list).toContainText("Syllabus");
  await expect(list).toContainText("English");
  await expect(list).toContainText("42 pages");
  await expect(list).toContainText("Ready for AI");
  await expect(list.getByText("grade-5-maths-teacher-guide.pdf")).toBeVisible();
  await expect(list).toContainText("Processing");
  await expect(list.getByText("grade-5-maths-2025-paper.pdf")).toBeVisible();
  await expect(list).toContainText("Needs review");
});

test("contract 3: an exact duplicate upload is stopped and links to the existing item", async ({
  page,
}) => {
  const fixture = await authenticatedFixture(page, "admin");
  await openGradeFiveMaths(page);
  const dialog = await openUploadAtPdf(page);

  await dialog.getByLabel("PDF file").setInputFiles({
    buffer: syntheticPdf("exact duplicate Grade 5 Maths syllabus"),
    mimeType: "application/pdf",
    name: "grade-5-maths-syllabus.pdf",
  });
  await continueUpload(dialog);
  await dialog.getByRole("button", { name: "Upload material" }).click();

  const alert = dialog.getByRole("alert");
  await expect(alert).toContainText(
    "This exact PDF is already in Materials. No new copy was uploaded.",
  );
  await expect(alert).toContainText("grade-5-maths-syllabus.pdf");
  const existingMaterial = alert.getByRole("link", { name: "View existing material" });
  await expect(existingMaterial).toHaveAttribute(
    "href",
    `/admin/materials/${fixture.materialIds.duplicate}`,
  );
  expect(
    fixture.requests.filter(
      (request) => request.method === "POST" && request.path.endsWith("/source-documents"),
    ),
  ).toHaveLength(1);

  await existingMaterial.click();
  await expect(page).toHaveURL(
    new RegExp(`/admin/materials/${fixture.materialIds.duplicate}$`),
  );
  await expect(
    page.getByRole("heading", { level: 1, name: "grade-5-maths-syllabus.pdf" }),
  ).toBeVisible();
  await expect(page.getByText("Ready for AI").first()).toBeVisible();
});

test("contract 4: an untrusted wrong-grade material is corrected out of Grade 5 scope", async ({
  page,
}) => {
  const fixture = await authenticatedFixture(page, "admin");
  await openGradeFiveMaths(page);

  const list = page.getByRole("region", { name: "Uploaded materials" });
  await expect(list.getByText("grade-11-algebra-paper.pdf")).toBeVisible();
  await page.getByRole("button", { name: "Edit metadata: grade-11-algebra-paper.pdf" }).click();
  const editor = page.getByRole("dialog", { name: "Edit grade-11-algebra-paper.pdf" });
  await editor
    .getByLabel("Curriculum version")
    .selectOption(fixture.curriculumIds.gradeEleven);
  await editor.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Moved to Grade 11.")).toBeVisible();
  await expect(list.getByText("grade-11-algebra-paper.pdf")).toHaveCount(0);

  const correction = fixture.requests.find(
    (request) =>
      request.method === "PATCH" &&
      request.path.endsWith(`/materials/${fixture.materialIds.wrongGrade}/scope`),
  );
  expect(correction?.body).toEqual({
    curriculum_version_id: fixture.curriculumIds.gradeEleven,
    expected_version: 1,
    lesson_id: null,
    unit_id: null,
  });
  expect(
    fixture.materials.find((material) => material.id === fixture.materialIds.wrongGrade),
  ).toMatchObject({ grade: 11, metadata_scope_version: 2 });
  expect(
    fixture.sourceDocuments.find((source) => source.id === fixture.materialIds.wrongGrade),
  ).toMatchObject({
    curriculum_version_id: fixture.curriculumIds.gradeEleven,
    metadata_scope_version: 2,
  });
});

test("contract 5: text correction compares immutable and editable extraction", async ({ page }) => {
  const fixture = await authenticatedFixture(page, "admin");
  await openGradeFiveMaths(page);

  await page
    .getByRole("link", { name: "Review extracted text: grade-5-maths-2025-paper.pdf" })
    .click();
  await expect(page).toHaveURL(
    new RegExp(`/admin/materials/${fixture.materialIds.ocr}/review-text$`),
  );
  await expect(page.getByRole("region", { name: "Original extracted page" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Extracted text" })).toBeVisible();
  await expect(page.getByText("Page 1 of 2")).toBeVisible();
  await page.getByRole("button", { name: "Begin text review" }).click();

  const text = page.getByLabel("Corrected text for page 1");
  await expect(text).toHaveValue("Thre equal parts are shaded.");
  await text.fill("Three equal parts are shaded.");
  await page.getByRole("button", { name: "Save correction" }).click();
  await expect(page.getByText("Page 1 correction saved.")).toBeVisible();
  await page.getByRole("button", { name: "Mark reviewed / Ready for AI" }).click();
  await expect(page.getByText("Ready for AI").first()).toBeVisible();
  expect(fixture.corrections).toEqual(["Three equal parts are shaded."]);
});

test("contract 6: Grade 7 Maths Lessons 1–3 generation uses teacher intent", async ({ page }) => {
  const fixture = await authenticatedFixture(page, "admin");
  await page.goto("/admin/generate-papers");
  await expect(page.getByRole("heading", { level: 1, name: "Generate Papers" })).toBeVisible();

  await chooseGradeSevenMaths(page);
  await page.getByRole("radio", { name: "Lesson range" }).check();
  await page.getByLabel("First lesson").selectOption("1");
  await page.getByLabel("Last lesson").selectOption("3");
  await expect(page.getByRole("region", { name: "Selected scope" })).toContainText(
    "Grade 7 Maths · Lessons 1–3",
  );
  await choosePaperSettings(page);

  await expect.poll(() => fixture.generationIntents.length, { timeout: 5_000 }).toBe(1);
  expect(generationIntent(fixture)).toMatchObject({
    scope: { end_lesson: 3, kind: "lesson_range", start_lesson: 1 },
    settings: { difficulty: "balanced", duration_minutes: 50, question_count: 12 },
    target: {
      assessment_programme: "SCHOOL-G7",
      grade: 7,
      medium: "en",
      subject: "MATHEMATICS",
    },
  });
  const createRequest = fixture.requests.find(
    (request) => request.method === "POST" && request.path.endsWith("/paper-generation/jobs"),
  );
  expect(createRequest?.headers["idempotency-key"]).toMatch(/^teacher-paper-\S+$/);
  expect(
    fixture.requests.some(
      (request) => request.method === "GET" && request.path.endsWith("/paper-generation/curricula"),
    ),
  ).toBe(true);
  expect(
    fixture.requests.some(
      (request) => request.method === "GET" && request.path.endsWith("/paper-generation/lessons"),
    ),
  ).toBe(true);
  const progress = page.getByRole("region", { name: "Paper progress" });
  await expect(progress).toContainText("Preparing paper");
  await expect(progress).toContainText("Generating questions");
  await expect(progress).toContainText("Checking answers");
  await expect(progress).toContainText("Ready for review");
});

test("contract 7: Grade 7 Maths supports full-subject generation", async ({ page }) => {
  const fixture = await authenticatedFixture(page, "admin");
  await page.goto("/admin/generate-papers");
  await expect(page.getByRole("heading", { level: 1, name: "Generate Papers" })).toBeVisible();

  await chooseGradeSevenMaths(page);
  await page.getByRole("radio", { name: "Full syllabus" }).check();
  await expect(page.getByRole("region", { name: "Selected scope" })).toContainText(
    "Grade 7 Maths · Full syllabus",
  );
  await choosePaperSettings(page);

  await expect.poll(() => fixture.generationIntents.length, { timeout: 5_000 }).toBe(1);
  expect(generationIntent(fixture)).toMatchObject({
    scope: { kind: "full_subject" },
    target: {
      assessment_programme: "SCHOOL-G7",
      grade: 7,
      medium: "en",
      subject: "MATHEMATICS",
    },
  });
});

test("contract 8: Review & Approve shows the generated question, answer, and marking together", async ({
  page,
}) => {
  const fixture = await authenticatedFixture(page, "reviewer");
  await page.goto("/admin/review-approve");
  await expect(page.getByRole("heading", { level: 1, name: "Review & Approve" })).toBeVisible();

  const question = page.getByRole("region", { name: "Question 1 of 1" });
  await expect(question).toContainText(
    "What fraction of the four equal parts is shaded when three parts are shaded?",
  );
  await expect(question.getByRole("list", { name: "Answer options" })).toContainText("B 3/4");
  await expect(question).toContainText("Proposed answer");
  await expect(question).toContainText("B — 3/4");
  await expect(question).toContainText(
    "Three of the four equal parts are shaded, so the fraction is 3/4.",
  );
  await expect(question).toContainText("2 marks");
  await expect(question).toContainText("Grade 7 Maths · Lessons 1–3 · Fractions");
  await expect(question).toContainText("Grade 7 Maths Teacher Guide — page 18");
  await expect(question).toContainText("Ready");
  await expect(question).toContainText("Answer check: Passed");
  await expect(question).toContainText("Calculation check: Passed");
  await expect(question).toContainText("Source check: Passed");
  for (const action of [
    "Start review",
    "Approve",
    "Edit",
    "Reject",
    "Regenerate question",
    "Previous",
    "Next",
  ]) {
    await expect(question.getByRole("button", { name: action })).toBeVisible();
  }

  await expect(question.getByRole("button", { name: "Approve" })).toBeDisabled();
  await question.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByText("Review started.")).toBeVisible();
  await expect(question.getByRole("button", { name: "Approve" })).toBeEnabled();
  await question.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Question approved.")).toBeVisible();

  const startRequest = fixture.requests.find(
    (request) =>
      request.method === "POST" &&
      request.path.endsWith(`/questions/${fixture.reviewQuestionId}/start`),
  );
  const approveRequest = fixture.requests.find(
    (request) =>
      request.method === "POST" &&
      request.path.endsWith(`/questions/${fixture.reviewQuestionId}/approve`),
  );
  expect(startRequest?.body).toEqual({ expected_version: 1 });
  expect(approveRequest?.body).toEqual({ expected_version: 2, note: null });

  const draftReady = page.getByRole("region", { name: "Paper ready for draft" });
  await expect(draftReady).toBeVisible();
  await draftReady.getByRole("button", { name: "Create draft" }).click();
  await expect(page.getByText("Draft created. It is ready in Published Papers.")).toBeVisible();
  const publishedLink = page.getByRole("link", { name: "Go to Published Papers" });
  await expect(publishedLink).toHaveAttribute("href", /\/admin\/published-papers\?paper=/);
  await publishedLink.click();
  await expect(page.getByRole("heading", { level: 1, name: "Published Papers" })).toBeVisible();
  await expect(
    page.getByRole("article", { name: "Grade 7 Maths Lessons 1–3 practice paper" }),
  ).toContainText("Grade 7 · Maths · English");
});

test("contract 9: technical diagnostics stay hidden until Advanced or Technical details opens", async ({
  page,
}) => {
  await authenticatedFixture(page, "admin");
  await page.goto("/admin/materials");
  await expect(page.getByRole("heading", { level: 1, name: "Materials" })).toBeVisible();

  const primary = page.getByRole("navigation", { name: "Primary admin navigation" });
  await expect(primary.getByRole("link")).toHaveText([
    "Home",
    "Materials",
    "Generate Papers",
    "Review & Approve",
    "Published Papers",
  ]);
  await expect(primary).not.toContainText("RAG");
  await expect(primary).not.toContainText("Blueprints");
  const advancedSummary = page.locator("summary").filter({ hasText: /^Advanced$/ });
  const advanced = advancedSummary.locator("xpath=..");
  await expect(advanced).not.toHaveAttribute("open", "");
  await expect(advanced.getByRole("link", { name: /Generation diagnostics/i })).toBeHidden();
  await advancedSummary.click();
  await expect(advanced.getByRole("link", { name: /Curriculum/i })).toBeVisible();
  await expect(advanced).toContainText(/Knowledge\s*\/\s*RAG/);
  await expect(advanced.getByRole("link", { name: /Generation diagnostics/i })).toBeVisible();
  await expect(advanced.getByRole("link", { name: /Validation details/i })).toBeVisible();
  await expect(advanced.getByRole("link", { name: /Operations/i })).toBeVisible();

  await page.goto("/admin/review-approve");
  const technicalSummary = page.locator("summary").filter({ hasText: /^Technical details$/ });
  const technical = technicalSummary.locator("xpath=..");
  await expect(technical).not.toHaveAttribute("open", "");
  await expect(technical.getByText("deterministic-fixture-provider")).toBeHidden();
  await technicalSummary.click();
  await expect(technical.getByText("deterministic-fixture-provider")).toBeVisible();

  await page.goto("/admin/published-papers");
  await expect(page.getByRole("heading", { level: 1, name: "Published Papers" })).toBeVisible();
  const published = page.getByRole("article", {
    name: "Grade 7 Maths Lessons 1–3 practice paper",
  });
  await expect(published).toContainText("Published");
  await expect(published).toContainText("Version 2");
  const publishedTechnicalSummary = published.locator("summary").filter({
    hasText: /^Technical details$/,
  });
  const publishedTechnical = publishedTechnicalSummary.locator("xpath=..");
  await expect(publishedTechnical).not.toHaveAttribute("open", "");
  await expect(publishedTechnical.getByText(/00000000-0000-0000-0000-000000002005/)).toBeHidden();
});
