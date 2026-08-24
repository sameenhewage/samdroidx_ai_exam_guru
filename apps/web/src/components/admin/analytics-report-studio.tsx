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
import { Button, Form } from "react-aria-components";

import { Badge } from "@/components/ui/badge";

type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
type AnalyticsRun = components["schemas"]["AnalyticsRunResponse"];
type AnalyticsSummary = components["schemas"]["AnalyticsRunSummaryResponse"];
type AnalyticsRequest = components["schemas"]["AnalyticsRunRequest"];
type ExactFraction = components["schemas"]["ExactFraction"];
type DistributionBucket = components["schemas"]["DistributionBucketResponse"];
type BacktestMetrics = components["schemas"]["BacktestMetricsResponse"];
type DataQuality = components["schemas"]["AnalyticsDataQualityResponse"];
type AnalyticsExclusionReason = components["schemas"]["AnalyticsExclusionReason"];
type Role = "admin" | "reviewer";

type UiError = {
  availableYears?: number[];
  code: string;
  dataQuality?: DataQuality;
  message: string;
  requiredYearCount?: number;
  title: string;
};

type ApiOutcome = {
  error?: unknown;
  response: Response;
};

const LIST_LIMIT = 100;
const MAX_EXACT_INTEGER = 2_147_483_647;
const fieldClass = "grid gap-1.5 text-sm font-semibold text-slate-700";
const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 outline-none transition focus:border-amber-500 focus:ring-2 focus:ring-amber-200 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white outline-none transition hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-400 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";

const exclusionLabels: Record<AnalyticsExclusionReason, string> = {
  competency_mismatch: "Competency mismatch",
  incomplete_difficulty_evidence: "Incomplete difficulty evidence",
  invalid_source_checksum: "Invalid source checksum",
  missing_competency_id: "Missing competency classification",
  missing_skill_id: "Missing skill classification",
  missing_source_block_id: "Missing source block provenance",
  missing_source_checksum: "Missing source checksum",
  non_finite_difficulty_confidence: "Invalid difficulty confidence",
  not_reviewed: "Not reviewed",
  skill_not_in_reviewed_syllabus: "Skill outside reviewed syllabus",
  source_not_trusted: "Source not trusted",
};

const metricLabels: ReadonlyArray<[keyof BacktestMetrics, string]> = [
  ["composite_score", "Composite score"],
  ["competency_distribution_accuracy", "Competency accuracy"],
  ["skill_distribution_accuracy", "Skill accuracy"],
  ["top_k_skill_hit_rate", "Top-k skill hit rate"],
  ["competency_distribution_error", "Competency error"],
  ["skill_distribution_error", "Skill error"],
];

function detailObject(error: unknown): Record<string, unknown> | null {
  if (!error || typeof error !== "object" || !("detail" in error)) return null;
  const detail = (error as { detail?: unknown }).detail;
  return detail && typeof detail === "object" && !Array.isArray(detail)
    ? (detail as Record<string, unknown>)
    : null;
}

function detailCode(error: unknown): string {
  const detail = detailObject(error);
  return detail && "code" in detail ? String(detail.code) : "request_failed";
}

function parseDataQuality(value: unknown): DataQuality | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const quality = value as Partial<DataQuality>;
  return typeof quality.considered_count === "number" &&
    typeof quality.included_count === "number" &&
    typeof quality.excluded_count === "number" &&
    Array.isArray(quality.exclusions)
    ? (quality as DataQuality)
    : undefined;
}

function uiError(error: unknown, status?: number, surface: "detail" | "list" | "run" = "detail"): UiError {
  if (status === 401) {
    return {
      code: "authentication_required",
      message: "Your admin session has expired. Sign in again before retrying.",
      title: "Authentication required",
    };
  }
  if (status === 403) {
    return {
      code: "permission_denied",
      message:
        surface === "run"
          ? "This account cannot create analytics runs. Ask an administrator to verify analytics:run access."
          : "This account cannot read analytics reports. Ask an administrator to verify analytics:read access.",
      title: surface === "run" ? "Run permission required" : "Analytics permission required",
    };
  }
  if (status === 503) {
    return {
      code: "service_unavailable",
      message: "The analytics service is temporarily unavailable. Retry without changing the persisted evidence.",
      title: "Analytics reports temporarily unavailable",
    };
  }

  const code = detailCode(error);
  const detail = detailObject(error);
  if (code === "analytics_insufficient_history") {
    const availableYears = Array.isArray(detail?.available_years)
      ? detail.available_years.filter((year): year is number => typeof year === "number")
      : [];
    return {
      availableYears,
      code,
      dataQuality: parseDataQuality(detail?.data_quality),
      message:
        "The run needs more eligible reviewed years after data-quality exclusions. Review historical questions and trusted provenance before retrying.",
      requiredYearCount:
        typeof detail?.required_year_count === "number"
          ? detail.required_year_count
          : undefined,
      title: "Insufficient eligible history",
    };
  }
  if (code === "analytics_syllabus_empty") {
    return {
      code,
      message: "Review and activate at least one competency and skill in this curriculum before running analysis.",
      title: "Reviewed syllabus is empty",
    };
  }
  if (code === "analytics_record_limit_exceeded" || code === "analytics_year_limit_exceeded") {
    return {
      code,
      message: "The synchronous run boundary was exceeded. Reduce the reviewed evidence scope or use a bounded background workflow when available.",
      title: "Synchronous analytics limit exceeded",
    };
  }
  if (code === "analytics_run_fingerprint_conflict" || status === 409) {
    return {
      code,
      message: "A run with conflicting reproducibility fingerprints already exists. Reload persisted reports before retrying.",
      title: "Analytics fingerprint conflict",
    };
  }
  if (code === "analytics_run_not_found" || status === 404) {
    return {
      code,
      message: "That persisted analytics run no longer exists in the selected curriculum.",
      title: "Analytics run not found",
    };
  }
  if (status === 422 || code === "analytics_input_invalid") {
    return {
      code: "analytics_input_invalid",
      message: "The bounded analytics configuration was rejected. Review all integer and exact-fraction fields.",
      title: "Invalid analytics configuration",
    };
  }
  return {
    code,
    message: "The analytics request could not be completed. Retry or contact an administrator if the failure persists.",
    title: surface === "list" ? "Analytics reports could not be loaded" : "Analytics request failed",
  };
}

function networkError(surface: "detail" | "list" | "run"): UiError {
  return {
    code: "network_error",
    message: "The analytics service could not be reached. Check the connection and retry.",
    title: surface === "run" ? "Analysis run connection failed" : "Analytics reports temporarily unavailable",
  };
}

function firstApiFailure(outcomes: readonly ApiOutcome[]): ApiOutcome | undefined {
  return outcomes.find((outcome) => outcome.error !== undefined);
}

function boundedInteger(
  raw: string,
  label: string,
  minimum: number,
  maximum: number,
): { error?: string; value?: number } {
  if (!/^\d+$/.test(raw.trim())) {
    return { error: `${label} must be a whole number from ${minimum} through ${maximum}.` };
  }
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    return { error: `${label} must be a whole number from ${minimum} through ${maximum}.` };
  }
  return { value };
}

function summaryFromRun(run: AnalyticsRun): AnalyticsSummary {
  return {
    aggregate: run.result.backtest.aggregate,
    backtest_algorithm_version: run.versions.backtest,
    baseline_algorithm_version: run.versions.baseline,
    config_fingerprint: run.config_fingerprint,
    created_at: run.created_at,
    created_by: run.created_by,
    curriculum_version_id: run.curriculum_version_id,
    excluded_count: run.data_quality.excluded_count,
    id: run.id,
    included_count: run.data_quality.included_count,
    input_fingerprint: run.input_fingerprint,
    practice_priority_algorithm_version: run.versions.practice_priority,
    recommendation: run.result.backtest.recommendation,
    result_fingerprint: run.result_fingerprint,
    run_fingerprint: run.run_fingerprint,
    source_fingerprint: run.source_fingerprint,
    statistics_algorithm_version: run.versions.statistics,
  };
}

function displayDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("en", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "UTC",
      }).format(date);
}

function displayEnum(value: string | number): string {
  if (typeof value === "number") return `${value} ${value === 1 ? "mark" : "marks"}`;
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function percentage(value: ExactFraction): string {
  return `${((value.numerator / value.denominator) * 100).toFixed(2)}%`;
}

function ExactFractionValue({ value }: { value: ExactFraction }) {
  return (
    <span className="inline-flex flex-wrap items-baseline gap-1">
      <span className="font-mono font-semibold">
        {value.numerator} / {value.denominator}
      </span>
      <span className="text-xs text-slate-500">({percentage(value)})</span>
    </span>
  );
}

function Section({
  children,
  description,
  title,
}: {
  children: ReactNode;
  description?: string;
  title: string;
}) {
  return (
    <section className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6">
      <header className="border-b border-slate-200 pb-4">
        <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        {description ? <p className="mt-1 text-sm leading-6 text-slate-600">{description}</p> : null}
      </header>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function ErrorPanel({
  error,
  onRetry,
  retryLabel,
}: {
  error: UiError;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div className="rounded-xl border border-red-300 bg-red-50 p-4 text-red-950" role="alert">
      <p className="font-semibold">{error.title}</p>
      <p className="mt-1 text-sm leading-6 text-red-900">{error.message}</p>
      {error.requiredYearCount ? (
        <p className="mt-2 text-sm">
          Required year count: <strong>{error.requiredYearCount}</strong>. Available years:{" "}
          <strong>{error.availableYears?.join(", ") || "none"}</strong>.
        </p>
      ) : null}
      {error.dataQuality?.exclusions.length ? (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
          {error.dataQuality.exclusions.map((item) => (
            <li key={item.reason}>
              {exclusionLabels[item.reason]}: {item.count}
            </li>
          ))}
        </ul>
      ) : null}
      {error.code === "analytics_insufficient_history" ? (
        <Link className="mt-3 inline-flex text-sm font-semibold underline" href="/admin/knowledge">
          Review historical questions
        </Link>
      ) : null}
      {onRetry && retryLabel ? (
        <Button className={`${secondaryButton} mt-3 border-red-300 bg-white`} onPress={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
    </div>
  );
}

function MetricCard({
  label,
  value,
}: {
  label: string;
  value: ExactFraction | number | string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
      <dt className="text-xs font-semibold text-slate-500">{label}</dt>
      <dd className="mt-1 text-sm text-slate-950">
        {typeof value === "object" ? <ExactFractionValue value={value} /> : value}
      </dd>
    </div>
  );
}

function DistributionTable({
  buckets,
  labelForKey,
  title,
}: {
  buckets: DistributionBucket[];
  labelForKey: (key: string | number) => string;
  title: string;
}) {
  return (
    <section>
      <h3 className="font-semibold text-slate-900">{title}</h3>
      {buckets.length ? (
        <div className="mt-2 overflow-x-auto rounded-xl border border-slate-200">
          <table className="w-full min-w-[36rem] border-collapse text-left text-sm">
            <caption className="sr-only">{title} distribution</caption>
            <thead className="bg-slate-100 text-xs text-slate-600 uppercase">
              <tr>
                <th className="px-3 py-2" scope="col">Bucket</th>
                <th className="px-3 py-2" scope="col">Questions</th>
                <th className="px-3 py-2" scope="col">Question share</th>
                <th className="px-3 py-2" scope="col">Marks</th>
                <th className="px-3 py-2" scope="col">Marks share</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {buckets.map((bucket) => (
                <tr key={String(bucket.key)}>
                  <th className="px-3 py-3 font-medium" scope="row">
                    {labelForKey(bucket.key)}
                  </th>
                  <td className="px-3 py-3 font-mono">{bucket.question_count}</td>
                  <td className="px-3 py-3"><ExactFractionValue value={bucket.question_share} /></td>
                  <td className="px-3 py-3 font-mono">{bucket.total_marks}</td>
                  <td className="px-3 py-3"><ExactFractionValue value={bucket.marks_share} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-2 text-sm text-slate-500">No eligible buckets.</p>
      )}
    </section>
  );
}

function MetricsComparison({
  baseline,
  method,
}: {
  baseline: BacktestMetrics;
  method: BacktestMetrics;
}) {
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200">
      <table className="w-full min-w-[38rem] border-collapse text-left text-sm">
        <caption className="sr-only">Historical evidence method compared with syllabus-balanced baseline</caption>
        <thead className="bg-slate-100 text-xs text-slate-600 uppercase">
          <tr>
            <th className="px-3 py-2" scope="col">Metric</th>
            <th className="px-3 py-2" scope="col">Historical method</th>
            <th className="px-3 py-2" scope="col">Syllabus baseline</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200">
          {metricLabels.map(([key, label]) => (
            <tr key={key}>
              <th className="px-3 py-3 font-medium" scope="row">{label}</th>
              <td className="px-3 py-3"><ExactFractionValue value={method[key]} /></td>
              <td className="px-3 py-3"><ExactFractionValue value={baseline[key]} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Fingerprint({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <dt className="text-xs font-semibold text-slate-500">{label}</dt>
      <dd className="mt-1 break-all font-mono text-xs text-slate-900">{value}</dd>
    </div>
  );
}

function SourceVersions({
  sources,
}: {
  sources: components["schemas"]["SourceVersionResponse"][];
}) {
  return sources.length ? (
    <ul className="grid gap-2">
      {sources.map((source) => (
        <li
          className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs"
          key={`${source.source_document_id}-${source.source_version}`}
        >
          <p className="break-all font-mono text-slate-900">{source.source_document_id}</p>
          <p className="mt-1 break-all font-mono text-slate-500">{source.source_version}</p>
        </li>
      ))}
    </ul>
  ) : (
    <p className="text-sm text-slate-500">No source versions recorded.</p>
  );
}

function Report({ report, taxonomy }: { report: AnalyticsRun; taxonomy: TaxonomyNode[] }) {
  const statistics = report.result.statistics;
  const backtest = report.result.backtest;
  const recommendation = backtest.recommendation;
  const priorities = backtest.recommended_run.priorities;
  const taxonomyById = new Map(taxonomy.map((node) => [node.id, node]));
  const taxonomyLabel = (key: string | number) => {
    const node = typeof key === "string" ? taxonomyById.get(key) : undefined;
    return node ? node.title : displayEnum(key);
  };

  return (
    <article className="grid gap-6" aria-labelledby="analysis-report-heading">
      <header className="rounded-2xl border border-slate-800 bg-slate-950 p-5 text-white shadow-sm sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div>
            <p className="font-mono text-xs tracking-[0.16em] text-amber-300 uppercase">
              Persisted deterministic run
            </p>
            <h2 className="mt-2 text-2xl font-semibold" id="analysis-report-heading">
              Analysis report
            </h2>
            <p className="mt-2 text-sm text-slate-300">
              Created {displayDate(report.created_at)} · {report.compute_duration_ms} ms
            </p>
          </div>
          <Badge className="border-white/20 bg-white/10 text-white">
            {report.deduplicated ? "Existing identical run" : "Persisted run"}
          </Badge>
        </div>
        <p className="mt-5 rounded-lg border border-amber-300/30 bg-amber-300/10 p-3 text-sm leading-6 text-amber-100">
          Practice-priority evidence only. This report does not predict future exam questions or
          claim certainty.
        </p>
      </header>

      <Section
        description="Eligibility is deterministic. Every excluded question remains visible by reason and identifier."
        title="Data quality"
      >
        <dl className="grid gap-3 sm:grid-cols-3">
          <MetricCard label="Considered" value={report.data_quality.considered_count} />
          <MetricCard label="Included" value={report.data_quality.included_count} />
          <MetricCard label="Excluded" value={report.data_quality.excluded_count} />
        </dl>
        {report.data_quality.exclusions.length ? (
          <div className="mt-5 grid gap-3">
            {report.data_quality.exclusions.map((exclusion) => (
              <section className="rounded-xl border border-amber-200 bg-amber-50 p-4" key={exclusion.reason}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="font-semibold text-amber-950">
                    {exclusionLabels[exclusion.reason]}
                  </h3>
                  <Badge className="border-amber-300 bg-white text-amber-950">
                    {exclusion.count} excluded
                  </Badge>
                </div>
                <ul className="mt-2 grid gap-1 font-mono text-xs text-amber-900">
                  {exclusion.question_ids.map((id) => (
                    <li className="break-all" key={id}>{id}</li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        ) : (
          <p className="mt-4 text-sm text-slate-600">All considered questions were eligible.</p>
        )}
      </Section>

      <Section
        description={`${statistics.observation_count} reviewed observations, ${statistics.total_marks} marks, years ${statistics.years.join(", ") || "none"}. Exact question and marks shares are retained as fractions.`}
        title="Historical distributions"
      >
        <div className="grid gap-7">
          <DistributionTable
            buckets={statistics.competency_distribution}
            labelForKey={taxonomyLabel}
            title="Competency"
          />
          <DistributionTable
            buckets={statistics.skill_distribution}
            labelForKey={taxonomyLabel}
            title="Skill"
          />
          <DistributionTable
            buckets={statistics.question_type_distribution}
            labelForKey={displayEnum}
            title="Question type"
          />
          <DistributionTable
            buckets={statistics.difficulty_distribution}
            labelForKey={displayEnum}
            title="Difficulty"
          />
          <DistributionTable
            buckets={statistics.marks_distribution}
            labelForKey={displayEnum}
            title="Marks"
          />
        </div>
      </Section>

      <Section
        description="Aggregate held-out method scores are compared with the syllabus-balanced baseline across every available rolling window."
        title="Baseline comparison"
      >
        <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <MetricCard label="Rolling windows" value={backtest.aggregate.window_count} />
          <MetricCard label="Mean method score" value={backtest.aggregate.mean_method_score} />
          <MetricCard label="Mean baseline score" value={backtest.aggregate.mean_baseline_score} />
          <MetricCard label="Observed baseline delta" value={backtest.aggregate.baseline_delta} />
          <MetricCard label="Method score variance" value={backtest.aggregate.method_score_variance} />
          <MetricCard label="Baseline score variance" value={backtest.aggregate.baseline_score_variance} />
        </dl>
        <div className="mt-5 rounded-xl border border-slate-300 bg-slate-50 p-4">
          <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
            Meaningful-improvement threshold
          </p>
          <p className="mt-1"><ExactFractionValue value={recommendation.meaningful_improvement} /></p>
        </div>
      </Section>

      <section
        className={`rounded-2xl border p-5 shadow-sm sm:p-6 ${
          recommendation.mode === "syllabus_balanced_practice"
            ? "border-amber-300 bg-amber-50"
            : "border-emerald-300 bg-emerald-50"
        }`}
      >
        <h2 className="text-xl font-semibold tracking-tight">
          {recommendation.mode === "syllabus_balanced_practice"
            ? "Syllabus-balanced practice fallback"
            : "Evidence-backed practice priorities"}
        </h2>
        <p className="mt-2 max-w-4xl text-sm leading-6">{recommendation.language}</p>
        <dl className="mt-4 grid gap-3 sm:grid-cols-3">
          <MetricCard label="Selected method" value={displayEnum(recommendation.selected_method)} />
          <MetricCard label="Observed baseline delta" value={recommendation.observed_baseline_delta} />
          <MetricCard label="Required improvement" value={recommendation.meaningful_improvement} />
        </dl>
        {recommendation.mode === "syllabus_balanced_practice" ? (
          <p className="mt-4 rounded-lg border border-amber-300 bg-white/70 p-3 text-sm leading-6 text-amber-950">
            The historical method did not clear the exact configured improvement threshold. The
            safer syllabus-balanced practice method is selected; this is not a prediction claim.
          </p>
        ) : null}
      </section>

      <Section
        description="Recommended practice shares and their deterministic evidence features."
        title="Practice priorities"
      >
        {priorities.length ? (
          <ol className="grid gap-4">
            {priorities.map((priority) => (
              <li className="rounded-xl border border-slate-300 bg-slate-50 p-4" key={priority.skill_id}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="font-mono text-xs text-slate-500">Rank {priority.rank}</p>
                    <h3 className="mt-1 font-semibold">{priority.skill_title}</h3>
                  </div>
                  <ExactFractionValue value={priority.practice_share} />
                </div>
                <p className="mt-3 text-sm leading-6 text-slate-700">{priority.evidence_language}</p>
                <dl className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <MetricCard label="Syllabus share" value={priority.features.syllabus_share} />
                  <MetricCard label="Question frequency" value={priority.features.question_frequency_share} />
                  <MetricCard label="Marks share" value={priority.features.marks_share} />
                  <MetricCard label="Recency gap share" value={priority.features.recency_gap_share} />
                  <MetricCard label="Evidence questions" value={priority.features.evidence_question_count} />
                  <MetricCard label="Evidence marks" value={priority.features.evidence_marks} />
                  <MetricCard label="Last observed year" value={priority.features.last_observed_year ?? "Never observed"} />
                </dl>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-slate-600">No practice priorities were produced.</p>
        )}
        <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-4">
          <h3 className="font-semibold">Feature definitions</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6 text-slate-700">
            {backtest.recommended_run.feature_definitions.map((definition) => (
              <li key={definition}>{definition}</li>
            ))}
          </ul>
        </div>
      </Section>

      <Section
        description="Each year is held out from all preceding training evidence. Overlap IDs must remain empty."
        title="Rolling held-out windows"
      >
        {backtest.windows.length ? (
          <div className="grid gap-5">
            {backtest.windows.map((window) => (
              <article className="rounded-xl border border-slate-300 p-4" key={window.heldout_year}>
                <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 pb-4">
                  <div>
                    <h3 className="text-lg font-semibold">Holdout {window.heldout_year}</h3>
                    <p className="mt-1 text-sm text-slate-600">
                      Training years: {window.training_years.join(", ") || "none"}
                    </p>
                  </div>
                  <Badge
                    className={
                      window.leakage_audit.passed
                        ? "border-emerald-300 bg-emerald-50 text-emerald-900"
                        : "border-red-300 bg-red-50 text-red-900"
                    }
                  >
                    {window.leakage_audit.passed
                      ? "Leakage audit passed"
                      : "Leakage audit failed"}
                  </Badge>
                </header>
                <dl className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <MetricCard label="Baseline delta" value={window.baseline_delta} />
                  <MetricCard label="Training cutoff (exclusive)" value={window.leakage_audit.training_cutoff_exclusive} />
                  <MetricCard label="Latest training year" value={window.leakage_audit.latest_training_year} />
                  <MetricCard label="Overlap IDs" value={window.leakage_audit.overlapping_observation_ids.length} />
                </dl>
                <div className="mt-4">
                  <MetricsComparison baseline={window.baseline_metrics} method={window.method_metrics} />
                </div>
                <dl className="mt-4 grid gap-3 sm:grid-cols-2">
                  <Fingerprint label="Training input fingerprint" value={window.training_input_fingerprint} />
                  <Fingerprint label="Held-out input fingerprint" value={window.heldout_input_fingerprint} />
                </dl>
                <details className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
                  <summary className="cursor-pointer font-semibold">Leakage audit observation IDs</summary>
                  <div className="mt-3 grid gap-4 sm:grid-cols-2">
                    <div>
                      <h4 className="text-xs font-semibold text-slate-500 uppercase">Training</h4>
                      <ul className="mt-1 grid gap-1 font-mono text-xs">
                        {window.leakage_audit.training_observation_ids.map((id) => (
                          <li className="break-all" key={id}>{id}</li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <h4 className="text-xs font-semibold text-slate-500 uppercase">Held out</h4>
                      <ul className="mt-1 grid gap-1 font-mono text-xs">
                        {window.leakage_audit.heldout_observation_ids.map((id) => (
                          <li className="break-all" key={id}>{id}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </details>
                <div className="mt-4">
                  <h4 className="text-sm font-semibold">Held-out source versions</h4>
                  <div className="mt-2"><SourceVersions sources={window.heldout_sources} /></div>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-600">No held-out windows were available.</p>
        )}
      </Section>

      <Section title="Limitations">
        {backtest.limitations.length ? (
          <ul className="list-disc space-y-2 pl-5 text-sm leading-6 text-slate-700">
            {backtest.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}
          </ul>
        ) : (
          <p className="text-sm text-slate-600">No limitations were recorded.</p>
        )}
      </Section>

      <Section
        description="Persisted versions, immutable source hashes, exact configuration, and fingerprints reproduce this report."
        title="Provenance & fingerprints"
      >
        <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <Fingerprint label="Run fingerprint" value={report.run_fingerprint} />
          <Fingerprint label="Configuration fingerprint" value={report.config_fingerprint} />
          <Fingerprint label="Input fingerprint" value={report.input_fingerprint} />
          <Fingerprint label="Source fingerprint" value={report.source_fingerprint} />
          <Fingerprint label="Result fingerprint" value={report.result_fingerprint} />
          <Fingerprint label="Observation fingerprint" value={report.input.observation_fingerprint} />
          <Fingerprint label="Selection fingerprint" value={report.input.selection_fingerprint} />
          <Fingerprint label="Created by" value={report.created_by} />
        </dl>
        <div className="mt-6 grid gap-5 xl:grid-cols-2">
          <section>
            <h3 className="font-semibold">Algorithm versions</h3>
            <dl className="mt-2 grid gap-2">
              <Fingerprint label="Statistics" value={report.versions.statistics} />
              <Fingerprint label="Practice priority" value={report.versions.practice_priority} />
              <Fingerprint label="Syllabus baseline" value={report.versions.baseline} />
              <Fingerprint label="Rolling backtest" value={report.versions.backtest} />
            </dl>
          </section>
          <section>
            <h3 className="font-semibold">Source versions</h3>
            <div className="mt-2"><SourceVersions sources={report.sources} /></div>
          </section>
        </div>
        <div className="mt-6 grid gap-5 xl:grid-cols-2">
          <section className="rounded-xl border border-slate-200 bg-slate-50 p-4">
            <h3 className="font-semibold">Exact run configuration</h3>
            <dl className="mt-3 grid gap-3 sm:grid-cols-2">
              <MetricCard label="Minimum training years" value={report.config.minimum_training_years} />
              <MetricCard label="Top-k skills" value={report.config.top_k_skills} />
              <MetricCard label="Meaningful improvement" value={report.config.meaningful_improvement} />
              <MetricCard label="Maximum synchronous records" value={report.config.synchronous_limits.maximum_records} />
              <MetricCard label="Maximum synchronous years" value={report.config.synchronous_limits.maximum_years} />
            </dl>
          </section>
          <section className="rounded-xl border border-slate-200 bg-slate-50 p-4">
            <h3 className="font-semibold">Priority weights</h3>
            <dl className="mt-3 grid gap-3 sm:grid-cols-2">
              <MetricCard label="Syllabus" value={report.config.priority_weights.syllabus} />
              <MetricCard label="Frequency" value={report.config.priority_weights.frequency} />
              <MetricCard label="Marks" value={report.config.priority_weights.marks} />
              <MetricCard label="Recency" value={report.config.priority_weights.recency} />
            </dl>
          </section>
        </div>
      </Section>
    </article>
  );
}

export function AnalyticsReportStudio({ role }: { role: Role }) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [curricula, setCurricula] = useState<Curriculum[]>([]);
  const [selectedCurriculumId, setSelectedCurriculumId] = useState("");
  const [taxonomy, setTaxonomy] = useState<TaxonomyNode[]>([]);
  const [summaries, setSummaries] = useState<AnalyticsSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [report, setReport] = useState<AnalyticsRun | null>(null);
  const [minimumTrainingYears, setMinimumTrainingYears] = useState("2");
  const [topKSkills, setTopKSkills] = useState("3");
  const [improvementNumerator, setImprovementNumerator] = useState("1");
  const [improvementDenominator, setImprovementDenominator] = useState("100");
  const [workspaceLoading, setWorkspaceLoading] = useState(true);
  const [listLoading, setListLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [runLoading, setRunLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<UiError | null>(null);
  const [listError, setListError] = useState<UiError | null>(null);
  const [detailError, setDetailError] = useState<UiError | null>(null);
  const [runError, setRunError] = useState<UiError | null>(null);
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  const [lastRunRequest, setLastRunRequest] = useState<AnalyticsRequest | null>(null);
  const workspaceRequestId = useRef(0);
  const listRequestId = useRef(0);
  const detailRequestId = useRef(0);
  const runRequestId = useRef(0);
  const activeCurricula = useMemo(() => curricula.filter((item) => item.active), [curricula]);
  const canRun = role === "admin";

  const loadWorkspace = useCallback(async () => {
    const requestId = ++workspaceRequestId.current;
    setWorkspaceLoading(true);
    setWorkspaceError(null);
    try {
      const response = await api.GET("/api/v1/admin/curriculum-versions");
      if (requestId !== workspaceRequestId.current) return;
      const failed = firstApiFailure([response]);
      if (failed?.error) {
        setWorkspaceError(uiError(failed.error, failed.response.status, "list"));
        return;
      }
      const nextCurricula = response.data ?? [];
      const nextActive = nextCurricula.filter((item) => item.active);
      setCurricula(nextCurricula);
      setTaxonomy([]);
      setSummaries([]);
      setSelectedRunId("");
      setReport(null);
      setListLoading(nextActive.length > 0);
      setSelectedCurriculumId((current) =>
        nextActive.some((item) => item.id === current) ? current : (nextActive[0]?.id ?? ""),
      );
    } catch {
      if (requestId === workspaceRequestId.current) setWorkspaceError(networkError("list"));
    } finally {
      if (requestId === workspaceRequestId.current) setWorkspaceLoading(false);
    }
  }, [api]);

  const loadReports = useCallback(
    async (curriculumVersionId: string) => {
      const requestId = ++listRequestId.current;
      setListLoading(true);
      setListError(null);
      setDetailError(null);
      setNotice("");
      try {
        const path = { curriculum_version_id: curriculumVersionId };
        const [taxonomyResponse, listResponse] = await Promise.all([
          api.GET("/api/v1/admin/curricula/{curriculum_version_id}/taxonomy/nodes", {
            params: { path },
          }),
          api.GET("/api/v1/admin/curricula/{curriculum_version_id}/analytics/runs", {
            params: { path, query: { limit: LIST_LIMIT, offset: 0 } },
          }),
        ]);
        if (requestId !== listRequestId.current) return;
        const failed = firstApiFailure([taxonomyResponse, listResponse]);
        if (failed?.error) {
          setListError(uiError(failed.error, failed.response.status, "list"));
          return;
        }
        const nextSummaries = listResponse.data ?? [];
        setTaxonomy(taxonomyResponse.data ?? []);
        setSummaries(nextSummaries);
        setSelectedRunId((current) =>
          nextSummaries.some((item) => item.id === current)
            ? current
            : (nextSummaries[0]?.id ?? ""),
        );
        if (!nextSummaries.length) setReport(null);
      } catch {
        if (requestId === listRequestId.current) setListError(networkError("list"));
      } finally {
        if (requestId === listRequestId.current) setListLoading(false);
      }
    },
    [api],
  );

  const loadReport = useCallback(
    async (curriculumVersionId: string, runId: string) => {
      const requestId = ++detailRequestId.current;
      setDetailLoading(true);
      setDetailError(null);
      try {
        const response = await api.GET(
          "/api/v1/admin/curricula/{curriculum_version_id}/analytics/runs/{run_id}",
          { params: { path: { curriculum_version_id: curriculumVersionId, run_id: runId } } },
        );
        if (requestId !== detailRequestId.current) return;
        if (response.error) {
          setDetailError(uiError(response.error, response.response.status));
          setReport(null);
          return;
        }
        setReport((response.data as AnalyticsRun | undefined) ?? null);
      } catch {
        if (requestId === detailRequestId.current) {
          setDetailError(networkError("detail"));
          setReport(null);
        }
      } finally {
        if (requestId === detailRequestId.current) setDetailLoading(false);
      }
    },
    [api],
  );

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadWorkspace(), 0);
    return () => window.clearTimeout(timeout);
  }, [loadWorkspace]);

  useEffect(() => {
    if (!selectedCurriculumId) return;
    const timeout = window.setTimeout(() => void loadReports(selectedCurriculumId), 0);
    return () => window.clearTimeout(timeout);
  }, [loadReports, selectedCurriculumId]);

  useEffect(() => {
    if (!selectedCurriculumId || !selectedRunId) return;
    const timeout = window.setTimeout(
      () => void loadReport(selectedCurriculumId, selectedRunId),
      0,
    );
    return () => window.clearTimeout(timeout);
  }, [loadReport, selectedCurriculumId, selectedRunId]);

  const executeRun = useCallback(
    async (request: AnalyticsRequest) => {
      if (!selectedCurriculumId) return;
      const requestId = ++runRequestId.current;
      setRunLoading(true);
      setRunError(null);
      setFormError("");
      setNotice("");
      setLastRunRequest(request);
      try {
        const response = await api.POST(
          "/api/v1/admin/curricula/{curriculum_version_id}/analytics/runs",
          {
            body: request,
            params: { path: { curriculum_version_id: selectedCurriculumId } },
          },
        );
        if (requestId !== runRequestId.current) return;
        if (response.error) {
          setRunError(uiError(response.error, response.response.status, "run"));
          return;
        }
        const nextReport = response.data as AnalyticsRun;
        setReport(nextReport);
        setSelectedRunId(nextReport.id);
        setSummaries((current) => [
          summaryFromRun(nextReport),
          ...current.filter((item) => item.id !== nextReport.id),
        ]);
        setNotice(
          nextReport.deduplicated
            ? "Existing identical analysis run selected."
            : "Analysis run created.",
        );
      } catch {
        if (requestId === runRequestId.current) setRunError(networkError("run"));
      } finally {
        if (requestId === runRequestId.current) setRunLoading(false);
      }
    },
    [api, selectedCurriculumId],
  );

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const minimumYears = boundedInteger(
      minimumTrainingYears,
      "Minimum training years",
      1,
      20,
    );
    const topSkills = boundedInteger(topKSkills, "Top skills to evaluate", 1, 100);
    const numerator = boundedInteger(
      improvementNumerator,
      "Meaningful improvement numerator",
      1,
      MAX_EXACT_INTEGER,
    );
    const denominator = boundedInteger(
      improvementDenominator,
      "Meaningful improvement denominator",
      1,
      MAX_EXACT_INTEGER,
    );
    const firstError = [minimumYears, topSkills, numerator, denominator].find(
      (value) => value.error,
    )?.error;
    if (firstError) {
      setFormError(firstError);
      return;
    }
    if ((numerator.value ?? 0) > (denominator.value ?? 0)) {
      setFormError(
        "Meaningful improvement must be greater than zero and no greater than one.",
      );
      return;
    }
    void executeRun({
      meaningful_improvement: {
        denominator: denominator.value as number,
        numerator: numerator.value as number,
      },
      minimum_training_years: minimumYears.value as number,
      top_k_skills: topSkills.value as number,
    });
  }

  if (workspaceLoading) {
    return (
      <div className="mx-auto max-w-7xl px-5 py-12 sm:px-8" role="status">
        <p className="font-semibold">Loading Analytics Report Studio…</p>
        <p className="mt-2 text-sm text-slate-600">
          Resolving active curricula and persisted deterministic reports.
        </p>
      </div>
    );
  }

  if (workspaceError) {
    return (
      <div className="mx-auto max-w-3xl px-5 py-12 sm:px-8">
        <ErrorPanel
          error={workspaceError}
          onRetry={() => void loadWorkspace()}
          retryLabel="Retry workspace"
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8">
      <header className="border-b border-slate-300 pb-7">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div>
            <p className="font-mono text-xs tracking-[0.18em] text-slate-500 uppercase">
              P5 / Historical exam intelligence
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">
              Analytics Report Studio
            </h1>
            <p className="mt-3 max-w-3xl leading-7 text-slate-600">
              Run and inspect reproducible historical distributions, rolling held-out practice
              evaluation, leakage audits, and syllabus-balanced baseline comparisons. Reports
              describe practice priorities; they do not predict an exam.
            </p>
          </div>
          <Badge className="border-slate-300 bg-white text-slate-700">
            {role === "reviewer" ? "Reviewer read access" : "Admin run access"}
          </Badge>
        </div>
      </header>

      {!activeCurricula.length ? (
        <section className="mt-8 rounded-2xl border border-amber-300 bg-amber-50 p-6">
          <h2 className="text-xl font-semibold text-amber-950">No active curriculum available</h2>
          <p className="mt-2 text-sm leading-6 text-amber-900">
            Activate a Grade 5 curriculum before listing or creating analytics reports.
          </p>
          <Link className={`${secondaryButton} mt-4 border-amber-300`} href="/admin/curriculum">
            Configure curriculum
          </Link>
        </section>
      ) : (
        <>
          <div className="mt-8 grid gap-6 xl:grid-cols-[20rem_minmax(0,1fr)]">
            <aside className="grid content-start gap-6">
              <Section title="Report scope">
                <label className={fieldClass}>
                  Active analytics curriculum
                  <select
                    className={inputClass}
                    onChange={(event) => {
                      setTaxonomy([]);
                      setSummaries([]);
                      setSelectedRunId("");
                      setReport(null);
                      setListLoading(true);
                      setSelectedCurriculumId(event.target.value);
                    }}
                    value={selectedCurriculumId}
                  >
                    {activeCurricula.map((item) => (
                      <option key={item.id} value={item.id}>{item.title}</option>
                    ))}
                  </select>
                </label>
              </Section>

              {canRun ? (
                <Section
                  description="All values are bounded by the API. Meaningful improvement is sent and stored as an exact fraction, never a floating-point threshold."
                  title="Run configuration"
                >
                  <Form className="grid gap-4" onSubmit={submit}>
                    <NumberField
                      label="Minimum training years"
                      maximum={20}
                      minimum={1}
                      onChange={setMinimumTrainingYears}
                      value={minimumTrainingYears}
                    />
                    <NumberField
                      label="Top skills to evaluate"
                      maximum={100}
                      minimum={1}
                      onChange={setTopKSkills}
                      value={topKSkills}
                    />
                    <fieldset className="grid gap-3 rounded-xl border border-slate-300 p-3">
                      <legend className="px-1 text-sm font-semibold">Exact meaningful improvement</legend>
                      <NumberField
                        label="Meaningful improvement numerator"
                        maximum={MAX_EXACT_INTEGER}
                        minimum={1}
                        onChange={setImprovementNumerator}
                        value={improvementNumerator}
                      />
                      <NumberField
                        label="Meaningful improvement denominator"
                        maximum={MAX_EXACT_INTEGER}
                        minimum={1}
                        onChange={setImprovementDenominator}
                        value={improvementDenominator}
                      />
                    </fieldset>
                    {formError ? (
                      <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900" role="alert">
                        {formError}
                      </p>
                    ) : null}
                    {runError ? (
                      <ErrorPanel
                        error={runError}
                        onRetry={lastRunRequest ? () => void executeRun(lastRunRequest) : undefined}
                        retryLabel={lastRunRequest ? "Retry analysis run" : undefined}
                      />
                    ) : null}
                    {notice ? (
                      <p className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm font-medium text-emerald-900" role="status">
                        {notice}
                      </p>
                    ) : null}
                    <Button className={primaryButton} isDisabled={runLoading} type="submit">
                      {runLoading ? "Running analysis…" : "Run analysis"}
                    </Button>
                  </Form>
                </Section>
              ) : (
                <section className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm">
                  <h2 className="font-semibold">Reviewer read-only mode</h2>
                  <p className="mt-2 text-sm leading-6 text-slate-600">
                    Reviewers can inspect every persisted report and its evidence. Only administrators
                    can create or rerun bounded analysis.
                  </p>
                </section>
              )}

              <Section title="Persisted runs">
                {listLoading ? <p className="text-sm text-slate-600" role="status">Loading run list…</p> : null}
                {listError ? (
                  <ErrorPanel
                    error={listError}
                    onRetry={() => void loadReports(selectedCurriculumId)}
                    retryLabel="Retry reports"
                  />
                ) : null}
                {!listLoading && !listError && summaries.length ? (
                  <ol aria-label="Persisted analytics runs" className="grid gap-2">
                    {summaries.map((item) => (
                      <li key={item.id}>
                        <button
                          aria-pressed={item.id === selectedRunId}
                          className={`w-full rounded-xl border p-3 text-left outline-none transition focus-visible:ring-2 focus-visible:ring-amber-500 ${
                            item.id === selectedRunId
                              ? "border-slate-900 bg-slate-950 text-white"
                              : "border-slate-300 bg-white hover:bg-slate-50"
                          }`}
                          onClick={() => setSelectedRunId(item.id)}
                          type="button"
                        >
                          <span className="block text-sm font-semibold">{displayDate(item.created_at)}</span>
                          <span className={`mt-1 block font-mono text-xs break-all ${item.id === selectedRunId ? "text-slate-300" : "text-slate-500"}`}>
                            {item.id}
                          </span>
                          <span className={`mt-2 block text-xs ${item.id === selectedRunId ? "text-amber-200" : "text-slate-600"}`}>
                            {item.recommendation.mode === "syllabus_balanced_practice"
                              ? "Syllabus-balanced fallback"
                              : "Evidence-backed practice"}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ol>
                ) : null}
                {!listLoading && !listError && !summaries.length ? (
                  <div className="rounded-xl border border-dashed border-slate-300 p-4">
                    <h2 className="font-semibold">No analytics runs yet</h2>
                    <p className="mt-2 text-sm leading-6 text-slate-600">
                      {canRun
                        ? "Create a bounded run after reviewed multi-year historical evidence is ready."
                        : "An administrator must create the first bounded run before reviewers can inspect a report."}
                    </p>
                  </div>
                ) : null}
              </Section>
            </aside>

            <div className="min-w-0">
              {detailLoading ? (
                <div className="rounded-2xl border border-slate-300 bg-white p-6" role="status">
                  Loading persisted report…
                </div>
              ) : null}
              {detailError ? (
                <ErrorPanel
                  error={detailError}
                  onRetry={() => void loadReport(selectedCurriculumId, selectedRunId)}
                  retryLabel="Retry selected report"
                />
              ) : null}
              {!detailLoading && !detailError && report ? (
                <Report report={report} taxonomy={taxonomy} />
              ) : null}
              {!detailLoading && !detailError && !report && summaries.length ? (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-600">
                  Select a persisted run to inspect its complete report.
                </div>
              ) : null}
              {!detailLoading && !detailError && !report && !summaries.length ? (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-6">
                  <p className="font-semibold">No report selected</p>
                  <p className="mt-2 text-sm leading-6 text-slate-600">
                    Data-quality evidence, distributions, rolling windows, limitations, and
                    reproducibility fingerprints will appear here after a persisted run exists.
                  </p>
                </div>
              ) : null}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function NumberField({
  label,
  maximum,
  minimum,
  onChange,
  value,
}: {
  label: string;
  maximum: number;
  minimum: number;
  onChange: (value: string) => void;
  value: string;
}) {
  return (
    <label className={fieldClass}>
      {label}
      <input
        className={inputClass}
        inputMode="numeric"
        max={maximum}
        min={minimum}
        onChange={(event) => onChange(event.target.value)}
        required
        step={1}
        type="number"
        value={value}
      />
    </label>
  );
}
