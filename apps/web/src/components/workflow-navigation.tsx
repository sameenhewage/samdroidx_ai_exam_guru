"use client";

import { Link } from "react-aria-components";

import { contentWorkflow } from "@/lib/content-workflow";

export function WorkflowNavigation({ role }: { role?: "admin" | "reviewer" }) {
  return (
    <nav aria-label="Content workflow">
      <ol className="space-y-1">
        {contentWorkflow.map((area, index) => (
          <li key={area.id}>
            <Link
              className="group flex rounded-lg px-3 py-2.5 text-sm text-slate-300 outline-none transition hover:bg-white/10 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-300"
              href={
                area.id === "curriculum"
                  ? "/admin/curriculum"
                  : area.id === "documents"
                    ? "/admin/documents"
                    : area.id === "historical-questions"
                      ? "/admin/knowledge"
                      : area.id === "rag-explorer"
                        ? "/admin/retrieval"
                        : area.id === "exam-intelligence"
                          ? "/admin/analytics"
                          : area.id === "blueprints"
                            ? "/admin/blueprints"
                            : area.id === "generation"
                              ? "/admin/generation"
                              : area.id === "validation"
                                ? "/admin/validation"
                                : area.id === "review-queue"
                                  ? "/admin/review"
                                  : area.id === "papers"
                                    ? "/admin/papers"
                                    : `#${area.id}`
              }
            >
              <span className="mr-3 font-mono text-xs text-slate-500 group-hover:text-amber-300">
                {String(index + 1).padStart(2, "0")}
              </span>
              {area.label}
            </Link>
          </li>
        ))}
        {role === "admin" ? (
          <li>
            <Link
              className="group flex rounded-lg border border-amber-300/30 px-3 py-2.5 text-sm text-amber-100 outline-none transition hover:bg-white/10 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-300"
              href="/admin/operations"
            >
              <span className="mr-3 font-mono text-xs text-amber-300">12</span>
              Operations dashboard
            </Link>
          </li>
        ) : null}
      </ol>
    </nav>
  );
}
