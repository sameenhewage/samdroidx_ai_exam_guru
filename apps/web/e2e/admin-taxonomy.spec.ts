import { expect, test } from "@playwright/test";

const unique = Date.now().toString().slice(-8);
const examCode = `G5S-${unique}`;
const mediumCode = `m${unique.slice(-6)}`;
const subjectCode = `S${unique}`;
const subjectName = `Subject ${unique}`;
const curriculumCode = `CV-${unique}`;
const rootCode = `C${unique}`;
const updatedRootCode = `${rootCode}A`;
const childCode = `S${unique}`;
const rootTitle = `Root competency ${unique}`;
const updatedRootTitle = `Updated root competency ${unique}`;
const childTitle = `Reviewed child skill ${unique}`;

async function developmentLogin(page: import("@playwright/test").Page, role: "admin" | "reviewer") {
  await page.goto("/admin/login");
  await page.getByRole("button", { name: `Continue as ${role}` }).click();
  await expect(page).toHaveURL(/\/admin\/home$/);
  await page.goto("/admin/curriculum");
  await expect(page.getByRole("heading", { name: "Configuration & taxonomy" })).toBeVisible();
}

test("authorized admin manages configuration and reviewed taxonomy with audit evidence", async ({ page }) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await developmentLogin(page, "admin");

  await page.getByLabel("Exam code").fill(examCode);
  await page.getByLabel("Exam name").fill(`Grade 5 Scholarship ${unique}`);
  await page.getByRole("spinbutton", { name: "Grade", exact: true }).fill("5");
  await page.getByRole("button", { name: "Create exam" }).click();
  await expect(page.getByRole("button", { name: `Grade 5 Scholarship ${unique}` })).toBeVisible();

  await page.getByLabel("Medium code").fill(mediumCode);
  await page.getByLabel("Medium name").fill(`Medium ${unique}`);
  await page.getByRole("button", { name: "Create medium" }).click();
  await expect(page.getByRole("button", { name: `Medium ${unique}` })).toBeVisible();

  await page.getByLabel("Subject code").fill(subjectCode);
  await page.getByLabel("Subject name").fill(subjectName);
  await page.getByRole("button", { name: "Create subject" }).click();
  await expect(page.getByRole("button", { name: subjectName })).toBeVisible();

  await page.locator('select[name="exam_configuration_id"]').selectOption({ label: `Grade 5 Scholarship ${unique} · Grade 5` });
  await page.locator('select[name="medium_id"]').selectOption({ label: `Medium ${unique}` });
  await page.locator('select[name="subject_id"]').selectOption({ label: `${subjectName} (${subjectCode})` });
  await page.getByLabel("Curriculum code").fill(curriculumCode);
  await page.getByLabel("Curriculum title").fill(`Curriculum ${unique}`);
  await page.getByRole("button", { name: "Create curriculum" }).click();
  const curriculumButton = page.getByRole("button", { name: `Curriculum ${unique}` });
  await expect(curriculumButton).toBeVisible();
  await curriculumButton.click();
  await expect(page.getByLabel("Selected curriculum").locator("option:checked")).toHaveText(
    `Curriculum ${unique} — ${subjectName}`,
  );

  await page.getByLabel("Exam code").fill(examCode);
  await page.getByLabel("Exam name").fill("Duplicate exam");
  await page.getByRole("button", { name: "Create exam" }).click();
  await expect(page.getByText("configuration_conflict", { exact: true })).toBeVisible();

  await page.getByLabel("Taxonomy code").fill(rootCode);
  await page.getByLabel("Taxonomy title").fill(rootTitle);
  await page.getByRole("button", { name: "Create taxonomy node" }).click();
  let parentCard = page.locator("article").filter({ hasText: `${rootCode} — ${rootTitle}` });
  await expect(parentCard).toBeVisible();

  await parentCard.getByLabel("Code").fill(updatedRootCode);
  await parentCard.getByLabel("Title").fill(updatedRootTitle);
  await parentCard.getByRole("button", { name: "Save node" }).click();
  parentCard = page
    .locator("article")
    .filter({ hasText: `${updatedRootCode} — ${updatedRootTitle}` });
  await expect(parentCard).toBeVisible();
  await parentCard.getByRole("button", { name: "Review node" }).click();
  await expect(parentCard.getByText("reviewed")).toBeVisible();
  await expect(parentCard.getByRole("button", { name: "Save node" })).toHaveCount(0);

  await page.getByLabel("Level").selectOption("skill");
  await page
    .getByLabel("Parent")
    .selectOption({ label: `${updatedRootCode} — ${updatedRootTitle}` });
  await page.getByLabel("Taxonomy code").fill(childCode);
  await page.getByLabel("Taxonomy title").fill(childTitle);
  await page.getByRole("button", { name: "Create taxonomy node" }).click();
  let childCard = page.locator("article").filter({ hasText: `${childCode} — ${childTitle}` });
  await childCard.getByRole("button", { name: "Review node" }).click();
  await expect(childCard.getByText("reviewed")).toBeVisible();

  await parentCard.getByRole("button", { name: "Deactivate node" }).click();
  await expect(page.getByText("inactive_parent", { exact: true })).toBeVisible();
  await childCard.getByRole("button", { name: "Deactivate node" }).click();
  childCard = page.locator("article").filter({ hasText: `${childCode} — ${childTitle}` });
  await expect(childCard.getByText("deprecated")).toBeVisible();
  await parentCard.getByRole("button", { name: "Deactivate node" }).click();
  await expect(parentCard.getByText("deprecated")).toBeVisible();

  await expect(page.getByRole("heading", { name: "Recent audit evidence" })).toBeVisible();
  await expect(page.getByText("taxonomy.node.reviewed").first()).toBeVisible();
  await expect(page.getByText("taxonomy.node.deactivated").first()).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await developmentLogin(page, "reviewer");
  await expect(page.getByText("Reviewer access is read-only.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Create exam" })).toHaveCount(0);

  const denied = await page.request.post("/api/v1/admin/exam-configurations", {
    data: { code: `DENIED-${unique}`, grade: 5, name: "Denied" },
  });
  expect(denied.status()).toBe(403);
  expect(
    browserErrors.filter(
      (error) =>
        !error.includes("status of 409") &&
        !error.includes("eval() is not supported in this environment"),
    ),
  ).toEqual([]);
});
