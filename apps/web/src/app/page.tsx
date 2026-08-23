import Link from "next/link";

import { WorkflowNavigation } from "@/components/workflow-navigation";
import { Badge } from "@/components/ui/badge";
import { contentWorkflow } from "@/lib/content-workflow";

export default function Home() {
  return (
    <main className="min-h-screen bg-[#f3f4ef] text-slate-950 lg:grid lg:grid-cols-[18rem_1fr]">
      <aside className="bg-slate-950 px-5 py-6 text-white lg:min-h-screen lg:px-6">
        <div className="lg:sticky lg:top-6">
          <header className="mb-8 border-b border-white/10 pb-6">
            <p className="text-xs font-semibold tracking-[0.24em] text-amber-300 uppercase">
              AI Exam Guru
            </p>
            <p className="mt-2 text-lg font-semibold">Content operations</p>
            <Badge className="mt-4" variant="foundation">
              Priority 1 foundation
            </Badge>
          </header>

          <WorkflowNavigation />

          <dl className="mt-8 grid grid-cols-2 gap-3 border-t border-white/10 pt-5 text-xs">
            <div>
              <dt className="text-slate-500">Active gate</dt>
              <dd className="mt-1 font-mono text-slate-200">P1</dd>
            </div>
            <div>
              <dt className="text-slate-500">Student product</dt>
              <dd className="mt-1 font-mono text-slate-200">Blocked</dd>
            </div>
          </dl>
        </div>
      </aside>

      <div className="px-5 py-8 sm:px-8 lg:px-12 lg:py-12">
        <header className="mx-auto max-w-6xl border-b border-slate-300 pb-9">
          <p className="font-mono text-xs font-semibold tracking-[0.18em] text-slate-500 uppercase">
            Admin workspace / Grade 5 Scholarship
          </p>
          <div className="mt-5 max-w-3xl">
            <h1 className="text-4xl font-semibold tracking-[-0.04em] text-balance sm:text-5xl">
              Admin Content Studio
            </h1>
            <p className="mt-4 max-w-2xl text-base leading-7 text-slate-600 sm:text-lg">
              Build a traceable path from trusted curriculum sources to reviewed, validated, and
              publishable Grade 5 practice papers.
            </p>
          </div>
          <div className="mt-8 grid gap-3 sm:grid-cols-2">
            <div className="border-l-2 border-amber-500 bg-white px-4 py-3 shadow-sm">
              <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                Repository foundation
              </p>
              <p className="mt-1 font-medium">Infrastructure and quality gates in progress</p>
            </div>
            <div className="border-l-2 border-slate-400 bg-white px-4 py-3 shadow-sm">
              <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                Release rule
              </p>
              <p className="mt-1 font-medium">Human review remains required before publishing</p>
            </div>
          </div>
          <Link
            className="mt-6 inline-flex bg-slate-950 px-5 py-3 text-sm font-semibold text-white"
            href="/admin/login"
          >
            Open Admin Content Studio
          </Link>
        </header>

        <section aria-labelledby="workflow-heading" className="mx-auto mt-10 max-w-6xl">
          <div className="flex items-end justify-between gap-6">
            <div>
              <p className="font-mono text-xs text-slate-500">10 operational areas</p>
              <h2 id="workflow-heading" className="mt-2 text-2xl font-semibold tracking-tight">
                Priority 1 workflow
              </h2>
            </div>
            <p className="hidden max-w-sm text-right text-sm leading-6 text-slate-500 md:block">
              Each area will surface evidence, failure states, provenance, and auditable review
              actions as its acceptance gate is implemented.
            </p>
          </div>

          <div className="mt-6 grid gap-px overflow-hidden border border-slate-300 bg-slate-300 sm:grid-cols-2 xl:grid-cols-3">
            {contentWorkflow.map((area, index) => (
              <article
                className="scroll-mt-6 bg-white p-5 transition hover:bg-amber-50/50"
                id={area.id}
                key={area.id}
              >
                <div className="flex items-start justify-between gap-4">
                  <span className="font-mono text-xs text-slate-400">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <Badge>Not started</Badge>
                </div>
                <h3 className="mt-8 text-lg font-semibold">{area.label}</h3>
                <p className="mt-2 text-sm leading-6 text-slate-600">{area.description}</p>
              </article>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
