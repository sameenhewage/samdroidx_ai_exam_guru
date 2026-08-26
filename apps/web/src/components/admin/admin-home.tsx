import Link from "next/link";

import type { AdminRole } from "./admin-header";

const teacherActions = [
  {
    description: "See what is already uploaded or add a PDF",
    href: "/admin/materials",
    label: "Manage materials",
  },
  {
    description: "Choose a grade, subject, and lesson scope",
    href: "/admin/generate-papers",
    label: "Generate a paper",
  },
  {
    description: "Check questions, answers, explanations, and marking",
    href: "/admin/review-approve",
    label: "Review and approve",
  },
  {
    description: "Open papers that are ready to use",
    href: "/admin/published-papers",
    label: "Find published papers",
  },
] as const;

export function AdminHome({ role }: { role: AdminRole }) {
  const workspaceLabel = role === "admin" ? "Administrator workspace" : "Reviewer workspace";

  return (
    <section className="mx-auto max-w-7xl px-5 py-10 sm:px-8 sm:py-14">
      <div className="max-w-3xl">
        <p className="text-xs font-semibold tracking-[0.18em] text-slate-500 uppercase">
          {workspaceLabel}
        </p>
        <h1 className="mt-3 text-4xl font-semibold tracking-[-0.04em] text-balance sm:text-5xl">
          Create and manage exam papers
        </h1>
        <p className="mt-5 max-w-2xl text-base leading-7 text-slate-600 sm:text-lg">
          Add teaching materials, generate a paper, check every question and answer, then publish
          it when it is ready.
        </p>
      </div>

      <section aria-label="Start here" className="mt-10 grid gap-4 sm:grid-cols-2">
        {teacherActions.map((action, index) => (
          <article
            className="border border-slate-300 bg-white p-6 shadow-sm transition hover:border-amber-500 hover:shadow-md"
            key={action.href}
          >
            <p className="font-mono text-xs font-semibold text-amber-700">
              {String(index + 1).padStart(2, "0")}
            </p>
            <h2 className="mt-6 text-xl font-semibold tracking-tight">
              <Link
                className="rounded-sm outline-none underline decoration-amber-500 decoration-2 underline-offset-4 focus-visible:ring-2 focus-visible:ring-amber-600 focus-visible:ring-offset-4"
                href={action.href}
              >
                {action.label}
              </Link>
            </h2>
            <p className="mt-3 text-sm leading-6 text-slate-600">{action.description}</p>
          </article>
        ))}
      </section>
    </section>
  );
}
