import type { components } from "@exam-guru/api-client";
import { expect, test, type Page } from "@playwright/test";

type Summary = components["schemas"]["OperationsSummaryResponse"];

type Failure = components["schemas"]["FailureCodeCountResponse"];

const securityHeaders = {
  "content-security-policy": [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; "),
  "referrer-policy": "strict-origin-when-cross-origin",
  "x-content-type-options": "nosniff",
  "x-frame-options": "DENY",
};

async function login(page: Page, role: "admin" | "reviewer") {
  await page.goto("/admin/login");
  await page.getByRole("button", { name: `Continue as ${role}` }).click();
  await expect(page).toHaveURL(/\/admin\/home$/);
}

function count(value: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
}

function utc(value: string): string {
  return new Date(value).toISOString();
}

function usdFromMicrousd(value: number): string {
  const exactDigits = String(value).padStart(7, "0");
  return `${exactDigits.slice(0, -6)}.${exactDigits.slice(-6)} USD`;
}

function sanitizedFailureCode(code: string): string {
  return /^[a-z][a-z0-9_.-]{0,127}$/.test(code) ? code : "unrecognized_failure_code";
}

async function expectValue(page: Page, testId: string, value: string | number) {
  await expect(page.getByTestId(testId)).toHaveText(String(value));
}

async function expectFailures(page: Page, prefix: string, failures: Failure[]) {
  if (failures.length === 0) {
    await expect(page.getByTestId(`${prefix}-failure-empty`)).toHaveText("No failures recorded.");
    return;
  }
  for (const [index, failure] of failures.entries()) {
    await expectValue(
      page,
      `${prefix}-failure-${index}`,
      `${sanitizedFailureCode(failure.code)} — ${count(failure.count)}`,
    );
  }
}

async function expectExactSummary(page: Page, summary: Summary) {
  await expectValue(page, "window-start", utc(summary.window.start));
  await expectValue(page, "window-end", utc(summary.window.end));
  await expectValue(
    page,
    "data-earliest",
    summary.data_bounds.earliest_observed_at
      ? utc(summary.data_bounds.earliest_observed_at)
      : "No observations",
  );
  await expectValue(
    page,
    "data-latest",
    summary.data_bounds.latest_observed_at
      ? utc(summary.data_bounds.latest_observed_at)
      : "No observations",
  );
  await expectValue(page, "unit-counts", summary.units.counts);
  await expectValue(page, "unit-tokens", summary.units.tokens);
  await expectValue(page, "unit-cost", summary.units.cost);
  await expectValue(page, "unit-latency", summary.units.latency);
  await expectValue(page, "unit-timestamps", summary.units.timestamps);

  await expectValue(page, "generation-run-count", count(summary.generation.run_count));
  for (const [status, value] of Object.entries(summary.generation.status_counts)) {
    await expectValue(page, `generation-status-${status}`, count(value));
  }
  await expectValue(page, "generation-attempt-count", count(summary.generation.attempt_count));
  await expectValue(page, "generation-input-tokens", count(summary.generation.input_tokens));
  await expectValue(page, "generation-output-tokens", count(summary.generation.output_tokens));
  await expectValue(page, "generation-total-tokens", count(summary.generation.total_tokens));
  await expectValue(
    page,
    "generation-cost-microusd",
    `${count(summary.generation.cost_microusd)} microusd`,
  );
  await expectValue(
    page,
    "generation-cost-usd",
    usdFromMicrousd(summary.generation.cost_microusd),
  );
  await expectValue(
    page,
    "generation-latency-total",
    `${count(summary.generation.latency_ms.total)} ms`,
  );
  await expectValue(
    page,
    "generation-latency-average",
    `${count(summary.generation.latency_ms.average)} ms`,
  );
  await expectValue(
    page,
    "generation-latency-maximum",
    `${count(summary.generation.latency_ms.maximum)} ms`,
  );
  await expectFailures(page, "generation", summary.generation.failure_codes);

  await expectValue(page, "validation-run-count", count(summary.validation.run_count));
  for (const [status, value] of Object.entries(summary.validation.run_status_counts)) {
    await expectValue(page, `validation-run-status-${status}`, count(value));
  }
  await expectValue(page, "validation-finding-count", count(summary.validation.finding_count));
  for (const [status, value] of Object.entries(summary.validation.finding_status_counts)) {
    await expectValue(page, `validation-finding-status-${status}`, count(value));
  }

  await expectValue(page, "extraction-document-count", count(summary.extraction.document_count));
  await expectValue(page, "extraction-ocr-page-count", count(summary.extraction.ocr_page_count));
  for (const [status, value] of Object.entries(summary.extraction.status_counts)) {
    await expectValue(page, `extraction-status-${status.replaceAll("_", "-")}`, count(value));
  }
  await expectFailures(page, "extraction", summary.extraction.failure_codes);

  await expectValue(page, "embedding-job-count", count(summary.embedding.job_count));
  await expectValue(page, "embedding-requested-count", count(summary.embedding.requested_count));
  await expectValue(page, "embedding-embedded-count", count(summary.embedding.embedded_count));
  await expectValue(
    page,
    "embedding-deduplicated-count",
    count(summary.embedding.deduplicated_count),
  );
  for (const [status, value] of Object.entries(summary.embedding.status_counts)) {
    await expectValue(page, `embedding-status-${status}`, count(value));
  }
  await expectFailures(page, "embedding", summary.embedding.failure_codes);

  const reconciliation = summary.object_storage.reconciliation;
  await expectValue(page, "storage-reconciliation-run-count", count(reconciliation.run_count));
  await expectValue(
    page,
    "storage-reconciliation-scanned-count",
    count(reconciliation.scanned_count),
  );
  await expectValue(
    page,
    "storage-reconciliation-referenced-count",
    count(reconciliation.referenced_count),
  );
  await expectValue(
    page,
    "storage-reconciliation-candidate-count",
    count(reconciliation.candidate_count),
  );
  await expectValue(
    page,
    "storage-reconciliation-resolved-count",
    count(reconciliation.resolved_count),
  );
  await expectValue(
    page,
    "storage-reconciliation-tagged-count",
    count(reconciliation.tagged_count),
  );
  await expectValue(
    page,
    "storage-reconciliation-failure-count",
    count(reconciliation.failure_count),
  );
  await expectValue(
    page,
    "storage-reconciliation-truncated-run-count",
    count(reconciliation.truncated_run_count),
  );
  await expectValue(
    page,
    "storage-reconciliation-current-candidate-count",
    count(reconciliation.current_candidate_count),
  );
  await expectValue(
    page,
    "storage-reconciliation-last-completed-at",
    reconciliation.last_completed_at ? utc(reconciliation.last_completed_at) : "No observations",
  );
  await expectFailures(page, "storage-reconciliation", reconciliation.failure_codes);

  await expectValue(page, "papers-paper-count", count(summary.practice_papers.paper_count));
  for (const [status, value] of Object.entries(summary.practice_papers.state_counts)) {
    await expectValue(page, `papers-status-${status}`, count(value));
  }
  await expectValue(
    page,
    "papers-publication-count",
    count(summary.practice_papers.publication_count),
  );
  await expectValue(page, "papers-archive-count", count(summary.practice_papers.archive_count));
}

function expectHardened(response: { headers(): Record<string, string> } | null) {
  expect(response).not.toBeNull();
  expect(response?.headers()).toMatchObject(securityHeaders);
}

test("administrator can inspect exact persisted operations while reviewer navigation and API access remain denied", async ({
  page,
}) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.goto("/admin/operations");
  await expect(page).toHaveURL(/\/admin\/login$/);

  await login(page, "admin");
  const adminNavigation = page.getByRole("navigation", { name: "Admin content areas" });
  await expect(adminNavigation.getByRole("link", { name: "Operations" })).toHaveAttribute(
    "href",
    "/admin/operations",
  );

  const summaryResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/v1/admin/operations/summary" && response.request().method() === "GET";
  });
  const operationsResponse = await page.goto("/admin/operations");
  expectHardened(operationsResponse);
  const summaryResponse = await summaryResponsePromise;
  expect(summaryResponse.ok()).toBe(true);
  expectHardened(summaryResponse);
  const summary = (await summaryResponse.json()) as Summary;

  await expect(page.getByRole("heading", { level: 1, name: "Operations Dashboard" })).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Admin content areas" }).getByRole("link", {
      name: "Operations",
    }),
  ).toHaveAttribute("aria-current", "page");
  await expect(page.getByText("Start inclusive · end exclusive")).toBeVisible();
  await expectExactSummary(page, summary);
  await expect(
    page.getByRole("heading", { level: 2, name: "Object storage reconciliation" }),
  ).toBeVisible();
  await expect(page.getByText("Dry-run / no-delete boundary")).toBeVisible();
  await expect(
    page.getByText(/it never deletes an object or overwrites operator-owned tags/i),
  ).toBeVisible();
  await expect(
    page.getByText(
      /external lifecycle deletion remains a separate, explicitly approved storage-policy action outside application reconciliation/i,
    ),
  ).toBeVisible();
  await expect(
    page.getByText(/Logs and spans require a configured collector and dashboard/),
  ).toBeVisible();
  await expect(page.getByText(/This summary is persisted aggregates/)).toBeVisible();

  await page.goto("/");
  await expect(
    page
      .getByRole("navigation", { name: "Content workflow" })
      .getByRole("link", { name: /Operations dashboard/ }),
  ).toHaveAttribute("href", "/admin/operations");

  await page.context().clearCookies();
  await login(page, "reviewer");
  await expect(
    page
      .getByRole("navigation", { name: "Admin content areas" })
      .getByRole("link", { name: "Operations" }),
  ).toHaveCount(0);
  await page.goto("/");
  await expect(
    page
      .getByRole("navigation", { name: "Content workflow" })
      .getByRole("link", { name: /Operations dashboard/ }),
  ).toHaveCount(0);

  const denied = await page.request.get("/api/v1/admin/operations/summary");
  expect(denied.status()).toBe(403);
  expect(await denied.json()).toEqual({ detail: { code: "permission_denied" } });

  const pageDenialPromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/v1/admin/operations/summary" && response.status() === 403;
  });
  await page.goto("/admin/operations");
  await pageDenialPromise;
  await expect(page.getByRole("heading", { level: 1, name: "Operations Dashboard" })).toBeVisible();
  await expect(
    page.getByRole("alert", { name: "Administrator access required" }),
  ).toContainText("Operational aggregates are restricted to administrators.");
  await expect(page.getByTestId("generation-run-count")).toHaveCount(0);
  await expect(
    page
      .getByRole("navigation", { name: "Admin content areas" })
      .getByRole("link", { name: "Operations" }),
  ).toHaveCount(0);
  expect(
    browserErrors.filter(
      (message) => !message.includes("server responded with a status of 403 (Forbidden)"),
    ),
  ).toEqual([]);
});
