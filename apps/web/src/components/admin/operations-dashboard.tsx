"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import { Badge } from "@/components/ui/badge";

type Summary = components["schemas"]["OperationsSummaryResponse"];
type Failure = components["schemas"]["FailureCodeCountResponse"];
type Query = { end: string; start: string };
type WindowSelection = { label: string; query?: Query };
type UiError = { message: string; title: string };

const SUMMARY_PATH = "/api/v1/admin/operations/summary" as const;
const MAX_WINDOW_MS = 31 * 24 * 60 * 60 * 1_000;
const DEFAULT_SELECTION: WindowSelection = { label: "Last 24 hours" };
const PRESETS = [
  { durationMs: 60 * 60 * 1_000, label: "Last 1 hour" },
  { durationMs: 24 * 60 * 60 * 1_000, label: "Last 24 hours" },
  { durationMs: 7 * 24 * 60 * 60 * 1_000, label: "Last 7 days" },
  { durationMs: MAX_WINDOW_MS, label: "Last 31 days" },
] as const;
const countFormatter = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

const fieldClass = "grid gap-1.5 text-sm font-semibold text-slate-700";
const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 font-mono text-sm text-slate-950 outline-none transition focus:border-amber-500 focus:ring-2 focus:ring-amber-200 disabled:cursor-not-allowed disabled:bg-slate-100";
const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-400 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";

function isNonNegativeSafeInteger(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 0;
}

function formatCount(value: number): string {
  return isNonNegativeSafeInteger(value) ? countFormatter.format(value) : "Unavailable";
}

function formatMicrousd(value: number): string {
  return isNonNegativeSafeInteger(value) ? `${formatCount(value)} microusd` : "Unavailable";
}

function formatUsdFromMicrousd(value: number): string {
  if (!isNonNegativeSafeInteger(value)) return "Unavailable";
  const exactDigits = String(value).padStart(7, "0");
  const dollars = exactDigits.slice(0, -6);
  const fraction = exactDigits.slice(-6);
  return `${dollars}.${fraction} USD`;
}

function formatMilliseconds(value: number): string {
  return isNonNegativeSafeInteger(value) ? `${formatCount(value)} ms` : "Unavailable";
}

function formatUtc(value: string | null): string {
  if (value === null) return "No observations";
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : "Unavailable";
}

function expectedUnit(value: string, expected: string): string {
  return value === expected ? value : "Unavailable";
}

function sanitizedFailureCode(value: unknown): string {
  return typeof value === "string" && /^[a-z][a-z0-9_.-]{0,127}$/.test(value)
    ? value
    : "unrecognized_failure_code";
}

function errorForStatus(status: number): UiError {
  if (status === 401) {
    return {
      message: "Your admin session has expired. Sign in again before retrying.",
      title: "Authentication required",
    };
  }
  if (status === 403) {
    return {
      message: "Operational aggregates are restricted to administrators.",
      title: "Administrator access required",
    };
  }
  if (status === 422) {
    return {
      message: "The server rejected this UTC window. Choose a valid half-open range of 31 days or less.",
      title: "Window rejected",
    };
  }
  return {
    message: "The persisted aggregate summary could not be loaded. Retry the same bounded request.",
    title: "Operations summary unavailable",
  };
}

function networkError(): UiError {
  return {
    message: "The persisted aggregate summary could not be reached. Check the service and retry.",
    title: "Operations summary unavailable",
  };
}

function utcFromControl(value: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(value)) return null;
  const timestamp = Date.parse(`${value}Z`);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : null;
}

function Definition({
  label,
  testId,
  value,
}: {
  label: string;
  testId: string;
  value: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-3">
      <dt className="text-xs font-semibold tracking-wide text-slate-500 uppercase">{label}</dt>
      <dd className="mt-1 font-mono text-sm font-semibold text-slate-950" data-testid={testId}>
        {value}
      </dd>
    </div>
  );
}

function StatusDefinitions({
  entries,
  prefix,
}: {
  entries: ReadonlyArray<readonly [label: string, testIdSuffix: string, value: number]>;
  prefix: string;
}) {
  return (
    <dl className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
      {entries.map(([label, suffix, value]) => (
        <Definition
          key={suffix}
          label={label}
          testId={`${prefix}-${suffix}`}
          value={formatCount(value)}
        />
      ))}
    </dl>
  );
}

function FailureCodes({ failures, prefix }: { failures: Failure[]; prefix: string }) {
  if (failures.length === 0) {
    return (
      <p className="text-sm text-slate-500" data-testid={`${prefix}-failure-empty`}>
        No failures recorded.
      </p>
    );
  }
  return (
    <ul className="grid gap-2 text-sm text-slate-700">
      {failures.map((failure, index) => (
        <li
          className="flex items-center justify-between gap-4 rounded-md border border-slate-200 bg-white px-3 py-2"
          data-testid={`${prefix}-failure-${index}`}
          key={index}
        >
          <code className="break-all text-xs">{sanitizedFailureCode(failure.code)}</code>
          <span className="font-mono font-semibold"> — {formatCount(failure.count)}</span>
        </li>
      ))}
    </ul>
  );
}

function ErrorPanel({ error, onRetry }: { error: UiError; onRetry: () => void }) {
  return (
    <section
      aria-labelledby="operations-error-heading"
      className="border border-rose-300 bg-rose-50 p-5"
      role="alert"
    >
      <h2 className="font-semibold text-rose-950" id="operations-error-heading">
        {error.title}
      </h2>
      <p className="mt-2 max-w-2xl text-sm leading-6 text-rose-900">{error.message}</p>
      <div className="mt-4 flex flex-wrap gap-3">
        <button className={secondaryButton} onClick={onRetry} type="button">
          Retry summary
        </button>
        {error.title === "Authentication required" ? (
          <Link
            className="inline-flex min-h-10 items-center px-2 text-sm font-semibold text-rose-950 underline outline-none focus-visible:ring-2 focus-visible:ring-amber-500"
            href="/admin/login"
          >
            Sign in again
          </Link>
        ) : null}
      </div>
    </section>
  );
}

function SummaryView({ summary }: { summary: Summary }) {
  const empty =
    summary.data_bounds.earliest_observed_at === null &&
    summary.data_bounds.latest_observed_at === null;
  const reconciliation = summary.object_storage.reconciliation;

  return (
    <div className="grid gap-6">
      {empty ? (
        <p className="border border-sky-300 bg-sky-50 px-4 py-3 text-sm text-sky-950" role="status">
          No operational data was observed in this window.
        </p>
      ) : null}

      <section
        aria-labelledby="window-evidence-heading"
        className="grid gap-5 border border-slate-300 bg-white p-5 shadow-sm lg:grid-cols-[1.3fr_1fr]"
      >
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="text-lg font-semibold" id="window-evidence-heading">
              UTC evidence window
            </h2>
            <Badge variant="foundation">Start inclusive · end exclusive</Badge>
          </div>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Counts include observations at the start and exclude observations at the end. Displayed
            bounds are the earliest and latest persisted records actually included.
          </p>
          <dl className="mt-4 grid gap-2 sm:grid-cols-2">
            <Definition label="Window start (UTC)" testId="window-start" value={formatUtc(summary.window.start)} />
            <Definition label="Window end (UTC)" testId="window-end" value={formatUtc(summary.window.end)} />
            <Definition
              label="Earliest observed (UTC)"
              testId="data-earliest"
              value={formatUtc(summary.data_bounds.earliest_observed_at)}
            />
            <Definition
              label="Latest observed (UTC)"
              testId="data-latest"
              value={formatUtc(summary.data_bounds.latest_observed_at)}
            />
          </dl>
        </div>
        <div>
          <h3 className="text-sm font-semibold tracking-wide text-slate-600 uppercase">Units</h3>
          <dl className="mt-4 grid gap-2 sm:grid-cols-2">
            <Definition
              label="Counts"
              testId="unit-counts"
              value={expectedUnit(summary.units.counts, "count")}
            />
            <Definition
              label="Tokens"
              testId="unit-tokens"
              value={expectedUnit(summary.units.tokens, "token")}
            />
            <Definition
              label="Cost"
              testId="unit-cost"
              value={expectedUnit(summary.units.cost, "microusd")}
            />
            <Definition
              label="Latency"
              testId="unit-latency"
              value={expectedUnit(summary.units.latency, "millisecond")}
            />
            <Definition
              label="Timestamps"
              testId="unit-timestamps"
              value={expectedUnit(summary.units.timestamps, "UTC")}
            />
          </dl>
        </div>
      </section>

      <section aria-labelledby="generation-operations-heading" className="border border-slate-300 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-200 pb-4">
          <div>
            <p className="font-mono text-xs text-slate-500">AI accounting</p>
            <h2 className="mt-1 text-xl font-semibold" id="generation-operations-heading">
              Generation
            </h2>
          </div>
          <dl>
            <Definition
              label="Generation runs"
              testId="generation-run-count"
              value={formatCount(summary.generation.run_count)}
            />
          </dl>
        </div>
        <div className="mt-5 grid gap-5">
          <div>
            <h3 className="mb-3 text-sm font-semibold">Run statuses</h3>
            <StatusDefinitions
              entries={[
                ["Pending", "status-pending", summary.generation.status_counts.pending],
                ["Running", "status-running", summary.generation.status_counts.running],
                ["Succeeded", "status-succeeded", summary.generation.status_counts.succeeded],
                ["Failed", "status-failed", summary.generation.status_counts.failed],
              ]}
              prefix="generation"
            />
          </div>
          <div>
            <h3 className="mb-3 text-sm font-semibold">Attempts, tokens, and exact cost</h3>
            <dl className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
              <Definition
                label="Attempts"
                testId="generation-attempt-count"
                value={formatCount(summary.generation.attempt_count)}
              />
              <Definition
                label="Input tokens"
                testId="generation-input-tokens"
                value={formatCount(summary.generation.input_tokens)}
              />
              <Definition
                label="Output tokens"
                testId="generation-output-tokens"
                value={formatCount(summary.generation.output_tokens)}
              />
              <Definition
                label="Total tokens"
                testId="generation-total-tokens"
                value={formatCount(summary.generation.total_tokens)}
              />
              <Definition
                label="Exact cost"
                testId="generation-cost-microusd"
                value={formatMicrousd(summary.generation.cost_microusd)}
              />
              <Definition
                label="Lossless USD"
                testId="generation-cost-usd"
                value={formatUsdFromMicrousd(summary.generation.cost_microusd)}
              />
            </dl>
          </div>
          <div>
            <h3 className="mb-3 text-sm font-semibold">Latency</h3>
            <dl className="grid gap-2 sm:grid-cols-3">
              <Definition
                label="Total latency"
                testId="generation-latency-total"
                value={formatMilliseconds(summary.generation.latency_ms.total)}
              />
              <Definition
                label="Average latency"
                testId="generation-latency-average"
                value={formatMilliseconds(summary.generation.latency_ms.average)}
              />
              <Definition
                label="Maximum latency"
                testId="generation-latency-maximum"
                value={formatMilliseconds(summary.generation.latency_ms.maximum)}
              />
            </dl>
          </div>
          <div>
            <h3 className="mb-3 text-sm font-semibold">Sanitized failure codes</h3>
            <FailureCodes failures={summary.generation.failure_codes} prefix="generation" />
          </div>
        </div>
      </section>

      <section
        aria-labelledby="semantic-operations-heading"
        className="border border-slate-300 bg-white p-5 shadow-sm"
      >
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-200 pb-4">
          <div>
            <p className="font-mono text-xs text-slate-500">Grounded answer-check accounting</p>
            <h2 className="mt-1 text-xl font-semibold" id="semantic-operations-heading">
              Semantic verification
            </h2>
          </div>
          <dl className="grid gap-2 sm:grid-cols-3">
            <Definition
              label="Recorded checks"
              testId="semantic-record-count"
              value={formatCount(summary.semantic_verifier.record_count)}
            />
            <Definition
              label="Attempts"
              testId="semantic-attempt-count"
              value={formatCount(summary.semantic_verifier.attempt_count)}
            />
            <Definition
              label="With accounting"
              testId="semantic-accounted-count"
              value={formatCount(summary.semantic_verifier.accounted_count)}
            />
          </dl>
        </div>
        <div className="mt-5 grid gap-5">
          <div className="grid gap-5 xl:grid-cols-2">
            <div>
              <h3 className="mb-3 text-sm font-semibold">Check outcomes</h3>
              <StatusDefinitions
                entries={[
                  ["Supported", "status-supported", summary.semantic_verifier.status_counts.supported],
                  [
                    "Contradicted",
                    "status-contradicted",
                    summary.semantic_verifier.status_counts.contradicted,
                  ],
                  [
                    "Needs evidence",
                    "status-insufficient-evidence",
                    summary.semantic_verifier.status_counts.insufficient_evidence,
                  ],
                  ["Unavailable", "status-unavailable", summary.semantic_verifier.status_counts.unavailable],
                ]}
                prefix="semantic"
              />
            </div>
            <div>
              <h3 className="mb-3 text-sm font-semibold">Individual claim outcomes</h3>
              <dl className="mb-2">
                <Definition
                  label="Claims checked"
                  testId="semantic-claim-count"
                  value={formatCount(summary.semantic_verifier.claim_count)}
                />
              </dl>
              <StatusDefinitions
                entries={[
                  [
                    "Supported",
                    "status-supported",
                    summary.semantic_verifier.claim_status_counts.supported,
                  ],
                  [
                    "Contradicted",
                    "status-contradicted",
                    summary.semantic_verifier.claim_status_counts.contradicted,
                  ],
                  [
                    "Needs evidence",
                    "status-insufficient-evidence",
                    summary.semantic_verifier.claim_status_counts.insufficient_evidence,
                  ],
                  [
                    "Unavailable",
                    "status-unavailable",
                    summary.semantic_verifier.claim_status_counts.unavailable,
                  ],
                ]}
                prefix="semantic-claim"
              />
            </div>
          </div>
          <div>
            <h3 className="mb-3 text-sm font-semibold">Tokens and exact cost</h3>
            <dl className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
              <Definition
                label="Input tokens"
                testId="semantic-input-tokens"
                value={formatCount(summary.semantic_verifier.input_tokens)}
              />
              <Definition
                label="Output tokens"
                testId="semantic-output-tokens"
                value={formatCount(summary.semantic_verifier.output_tokens)}
              />
              <Definition
                label="Total tokens"
                testId="semantic-total-tokens"
                value={formatCount(summary.semantic_verifier.total_tokens)}
              />
              <Definition
                label="Exact cost"
                testId="semantic-cost-microusd"
                value={formatMicrousd(summary.semantic_verifier.cost_microusd)}
              />
              <Definition
                label="Lossless USD"
                testId="semantic-cost-usd"
                value={formatUsdFromMicrousd(summary.semantic_verifier.cost_microusd)}
              />
            </dl>
          </div>
          <div>
            <h3 className="mb-3 text-sm font-semibold">Latency for accounted checks</h3>
            <dl className="grid gap-2 sm:grid-cols-3">
              <Definition
                label="Total latency"
                testId="semantic-latency-total"
                value={formatMilliseconds(summary.semantic_verifier.latency_ms.total)}
              />
              <Definition
                label="Average latency"
                testId="semantic-latency-average"
                value={formatMilliseconds(summary.semantic_verifier.latency_ms.average)}
              />
              <Definition
                label="Maximum latency"
                testId="semantic-latency-maximum"
                value={formatMilliseconds(summary.semantic_verifier.latency_ms.maximum)}
              />
            </dl>
          </div>
          <div>
            <h3 className="mb-3 text-sm font-semibold">Sanitized failure codes</h3>
            <FailureCodes failures={summary.semantic_verifier.failure_codes} prefix="semantic" />
          </div>
        </div>
      </section>

      <div className="grid gap-6 xl:grid-cols-2">
        <section aria-labelledby="validation-operations-heading" className="border border-slate-300 bg-white p-5 shadow-sm">
          <h2 className="text-xl font-semibold" id="validation-operations-heading">
            Validation
          </h2>
          <div className="mt-5 grid gap-5">
            <div>
              <h3 className="mb-3 text-sm font-semibold">Run statuses</h3>
              <dl className="mb-2">
                <Definition
                  label="Validation runs"
                  testId="validation-run-count"
                  value={formatCount(summary.validation.run_count)}
                />
              </dl>
              <StatusDefinitions
                entries={[
                  ["Pass", "run-status-pass", summary.validation.run_status_counts.pass],
                  ["Warn", "run-status-warn", summary.validation.run_status_counts.warn],
                  ["Fail", "run-status-fail", summary.validation.run_status_counts.fail],
                ]}
                prefix="validation"
              />
            </div>
            <div>
              <h3 className="mb-3 text-sm font-semibold">Finding statuses</h3>
              <dl className="mb-2">
                <Definition
                  label="Findings"
                  testId="validation-finding-count"
                  value={formatCount(summary.validation.finding_count)}
                />
              </dl>
              <StatusDefinitions
                entries={[
                  ["Pass", "finding-status-pass", summary.validation.finding_status_counts.pass],
                  ["Warn", "finding-status-warn", summary.validation.finding_status_counts.warn],
                  ["Fail", "finding-status-fail", summary.validation.finding_status_counts.fail],
                ]}
                prefix="validation"
              />
            </div>
          </div>
        </section>

        <section aria-labelledby="extraction-operations-heading" className="border border-slate-300 bg-white p-5 shadow-sm">
          <h2 className="text-xl font-semibold" id="extraction-operations-heading">
            Extraction and OCR
          </h2>
          <dl className="mt-5 grid gap-2 sm:grid-cols-2">
            <Definition
              label="Documents"
              testId="extraction-document-count"
              value={formatCount(summary.extraction.document_count)}
            />
            <Definition
              label="OCR pages"
              testId="extraction-ocr-page-count"
              value={formatCount(summary.extraction.ocr_page_count)}
            />
          </dl>
          <h3 className="mt-5 mb-3 text-sm font-semibold">Document statuses</h3>
          <StatusDefinitions
            entries={[
              ["Uploaded", "status-uploaded", summary.extraction.status_counts.uploaded],
              [
                "Extraction pending",
                "status-extraction-pending",
                summary.extraction.status_counts.extraction_pending,
              ],
              ["Extracted", "status-extracted", summary.extraction.status_counts.extracted],
              ["In review", "status-in-review", summary.extraction.status_counts.in_review],
              ["Trusted", "status-trusted", summary.extraction.status_counts.trusted],
              ["Failed", "status-failed", summary.extraction.status_counts.failed],
            ]}
            prefix="extraction"
          />
          <h3 className="mt-5 mb-3 text-sm font-semibold">Sanitized failure codes</h3>
          <FailureCodes failures={summary.extraction.failure_codes} prefix="extraction" />
        </section>

        <section aria-labelledby="embedding-operations-heading" className="border border-slate-300 bg-white p-5 shadow-sm">
          <h2 className="text-xl font-semibold" id="embedding-operations-heading">
            Embedding ingestion
          </h2>
          <dl className="mt-5 grid gap-2 sm:grid-cols-2">
            <Definition
              label="Jobs"
              testId="embedding-job-count"
              value={formatCount(summary.embedding.job_count)}
            />
            <Definition
              label="Requested records"
              testId="embedding-requested-count"
              value={formatCount(summary.embedding.requested_count)}
            />
            <Definition
              label="Embedded records"
              testId="embedding-embedded-count"
              value={formatCount(summary.embedding.embedded_count)}
            />
            <Definition
              label="Deduplicated records"
              testId="embedding-deduplicated-count"
              value={formatCount(summary.embedding.deduplicated_count)}
            />
          </dl>
          <h3 className="mt-5 mb-3 text-sm font-semibold">Job statuses</h3>
          <StatusDefinitions
            entries={[
              ["Queued", "status-queued", summary.embedding.status_counts.queued],
              ["Claimed", "status-claimed", summary.embedding.status_counts.claimed],
              ["Succeeded", "status-succeeded", summary.embedding.status_counts.succeeded],
              ["Failed", "status-failed", summary.embedding.status_counts.failed],
            ]}
            prefix="embedding"
          />
          <h3 className="mt-5 mb-3 text-sm font-semibold">Sanitized failure codes</h3>
          <FailureCodes failures={summary.embedding.failure_codes} prefix="embedding" />
        </section>

        <section aria-labelledby="papers-operations-heading" className="border border-slate-300 bg-white p-5 shadow-sm">
          <h2 className="text-xl font-semibold" id="papers-operations-heading">
            Papers and publications
          </h2>
          <dl className="mt-5 grid gap-2 sm:grid-cols-3">
            <Definition
              label="Papers"
              testId="papers-paper-count"
              value={formatCount(summary.practice_papers.paper_count)}
            />
            <Definition
              label="Publications"
              testId="papers-publication-count"
              value={formatCount(summary.practice_papers.publication_count)}
            />
            <Definition
              label="Archives"
              testId="papers-archive-count"
              value={formatCount(summary.practice_papers.archive_count)}
            />
          </dl>
          <h3 className="mt-5 mb-3 text-sm font-semibold">Paper states</h3>
          <StatusDefinitions
            entries={[
              ["Draft", "status-draft", summary.practice_papers.state_counts.draft],
              ["Published", "status-published", summary.practice_papers.state_counts.published],
              ["Archived", "status-archived", summary.practice_papers.state_counts.archived],
            ]}
            prefix="papers"
          />
        </section>
      </div>

      <section
        aria-labelledby="storage-reconciliation-operations-heading"
        className="border border-slate-300 bg-white p-5 shadow-sm"
      >
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-200 pb-4">
          <div>
            <p className="font-mono text-xs text-slate-500">Object safety accounting</p>
            <h2
              className="mt-1 text-xl font-semibold"
              id="storage-reconciliation-operations-heading"
            >
              Object storage reconciliation
            </h2>
          </div>
          <Badge variant="foundation">Aggregate only</Badge>
        </div>
        <p className="mt-4 max-w-4xl text-sm leading-6 text-slate-600">
          Run totals, object counts, failures, and truncation cover completed runs in the selected
          half-open window. Current candidates and the last completed run are service-wide safety
          snapshots.
        </p>
        <dl className="mt-5 grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          <Definition
            label="Completed runs"
            testId="storage-reconciliation-run-count"
            value={formatCount(reconciliation.run_count)}
          />
          <Definition
            label="Objects scanned"
            testId="storage-reconciliation-scanned-count"
            value={formatCount(reconciliation.scanned_count)}
          />
          <Definition
            label="Referenced objects"
            testId="storage-reconciliation-referenced-count"
            value={formatCount(reconciliation.referenced_count)}
          />
          <Definition
            label="Candidates detected"
            testId="storage-reconciliation-candidate-count"
            value={formatCount(reconciliation.candidate_count)}
          />
          <Definition
            label="Candidates resolved"
            testId="storage-reconciliation-resolved-count"
            value={formatCount(reconciliation.resolved_count)}
          />
          <Definition
            label="Candidates tagged"
            testId="storage-reconciliation-tagged-count"
            value={formatCount(reconciliation.tagged_count)}
          />
          <Definition
            label="Failures"
            testId="storage-reconciliation-failure-count"
            value={formatCount(reconciliation.failure_count)}
          />
          <Definition
            label="Truncated runs"
            testId="storage-reconciliation-truncated-run-count"
            value={formatCount(reconciliation.truncated_run_count)}
          />
          <Definition
            label="Current candidates"
            testId="storage-reconciliation-current-candidate-count"
            value={formatCount(reconciliation.current_candidate_count)}
          />
          <Definition
            label="Last completed run (UTC)"
            testId="storage-reconciliation-last-completed-at"
            value={formatUtc(reconciliation.last_completed_at)}
          />
        </dl>
        <div className="mt-5 grid gap-5 border-t border-slate-200 pt-5 lg:grid-cols-[1fr_1.4fr]">
          <div>
            <h3 className="mb-3 text-sm font-semibold">Sanitized failure codes</h3>
            <FailureCodes
              failures={reconciliation.failure_codes}
              prefix="storage-reconciliation"
            />
          </div>
          <aside
            aria-labelledby="storage-reconciliation-boundary-heading"
            className="border-l-4 border-sky-500 bg-sky-50 px-4 py-3 text-sm leading-6 text-sky-950"
          >
            <h3 className="font-semibold" id="storage-reconciliation-boundary-heading">
              Dry-run / no-delete boundary
            </h3>
            <p className="mt-1">
              Reconciliation may persist findings and, only when explicitly configured, merge or
              remove application-owned candidate tags; it never deletes an object or overwrites
              operator-owned tags.
            </p>
            <p className="mt-2">
              Any external lifecycle deletion remains a separate, explicitly approved storage-policy
              action outside application reconciliation. This dashboard reports no lifecycle approval
              and cannot authorize deletion.
            </p>
          </aside>
        </div>
      </section>

      <aside className="border-l-4 border-amber-500 bg-amber-50 px-5 py-4 text-sm leading-6 text-amber-950">
        <h2 className="font-semibold">Observability boundary</h2>
        <p className="mt-1">
          Logs and spans require a configured collector and dashboard. This summary is persisted
          aggregates from fixed server-side dimensions; it is not a log, trace, or live telemetry
          stream.
        </p>
      </aside>
    </div>
  );
}

export function OperationsDashboard() {
  const api = useMemo(() => createApiClient(globalThis.location?.origin ?? "http://localhost"), []);
  const requestVersion = useRef(0);
  const [selection, setSelection] = useState<WindowSelection>(DEFAULT_SELECTION);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<UiError | null>(null);
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [customError, setCustomError] = useState<string | null>(null);

  const load = useCallback(
    async (nextSelection: WindowSelection) => {
      const version = ++requestVersion.current;
      setLoading(true);
      setError(null);
      setSummary(null);
      try {
        const result = nextSelection.query
          ? await api.GET(SUMMARY_PATH, { params: { query: nextSelection.query } })
          : await api.GET(SUMMARY_PATH);
        if (version !== requestVersion.current) return;
        if (!result.response.ok || !result.data) {
          setError(errorForStatus(result.response.status));
          return;
        }
        setSummary(result.data);
      } catch {
        if (version === requestVersion.current) setError(networkError());
      } finally {
        if (version === requestVersion.current) setLoading(false);
      }
    },
    [api],
  );

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(DEFAULT_SELECTION), 0);
    return () => {
      window.clearTimeout(timeout);
      requestVersion.current += 1;
    };
  }, [load]);

  function applyPreset(label: string, durationMs: number) {
    const end = new Date();
    const start = new Date(end.getTime() - durationMs);
    const nextSelection = {
      label,
      query: { end: end.toISOString(), start: start.toISOString() },
    };
    setCustomError(null);
    setSelection(nextSelection);
    void load(nextSelection);
  }

  function applyCustom(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const start = utcFromControl(customStart);
    const end = utcFromControl(customEnd);
    if (!start || !end) {
      setCustomError("Enter both valid UTC start and end values.");
      return;
    }
    const duration = Date.parse(end) - Date.parse(start);
    if (duration <= 0) {
      setCustomError("Custom start must be before the exclusive end.");
      return;
    }
    if (duration > MAX_WINDOW_MS) {
      setCustomError("Custom window must be 31 days or less.");
      return;
    }
    const nextSelection = { label: "Custom UTC window", query: { end, start } };
    setCustomError(null);
    setSelection(nextSelection);
    void load(nextSelection);
  }

  return (
    <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8 lg:py-10">
      <header className="border-b border-slate-300 pb-7">
        <p className="font-mono text-xs font-semibold tracking-[0.18em] text-slate-500 uppercase">
          Admin / persisted operational aggregates
        </p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">
          Operations Dashboard
        </h1>
        <p className="mt-3 max-w-3xl text-sm leading-7 text-slate-600 sm:text-base">
          Inspect bounded pipeline throughput, validation, AI token cost, latency, extraction,
          embedding, object-storage reconciliation, and publication counts without exposing resource
          identifiers or content.
        </p>
      </header>

      <section aria-labelledby="operations-window-heading" className="my-6 border border-slate-300 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold" id="operations-window-heading">
              Summary window
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Fixed presets and an optional custom UTC range; maximum 31 days.
            </p>
          </div>
          <Badge>{selection.label}</Badge>
        </div>
        <div aria-label="Window presets" className="mt-4 flex flex-wrap gap-2" role="group">
          {PRESETS.map((preset) => (
            <button
              aria-pressed={selection.label === preset.label}
              className={secondaryButton}
              disabled={loading}
              key={preset.label}
              onClick={() => applyPreset(preset.label, preset.durationMs)}
              type="button"
            >
              {preset.label}
            </button>
          ))}
        </div>
        <form className="mt-5 grid items-end gap-3 border-t border-slate-200 pt-5 md:grid-cols-[1fr_1fr_auto]" onSubmit={applyCustom}>
          <label className={fieldClass}>
            Start (UTC)
            <input
              className={inputClass}
              disabled={loading}
              onChange={(event) => setCustomStart(event.target.value)}
              step="1"
              type="datetime-local"
              value={customStart}
            />
          </label>
          <label className={fieldClass}>
            End (UTC)
            <input
              className={inputClass}
              disabled={loading}
              onChange={(event) => setCustomEnd(event.target.value)}
              step="1"
              type="datetime-local"
              value={customEnd}
            />
          </label>
          <button className={secondaryButton} disabled={loading} type="submit">
            Apply custom window
          </button>
        </form>
        <p className="mt-2 text-xs leading-5 text-slate-500">
          Date-time control values are interpreted as UTC. Start is inclusive; end is exclusive.
        </p>
        {customError ? (
          <p className="mt-3 border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-950" role="alert">
            {customError}
          </p>
        ) : null}
      </section>

      {loading ? (
        <p aria-live="polite" className="border border-slate-300 bg-white px-5 py-4 text-sm text-slate-600" role="status">
          Loading operational summary…
        </p>
      ) : error ? (
        <ErrorPanel error={error} onRetry={() => void load(selection)} />
      ) : summary ? (
        <SummaryView summary={summary} />
      ) : null}
    </div>
  );
}
