import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AdminHeader } from "./admin-header";

const primaryLinks = [
  ["Home", "/admin/home"],
  ["Materials", "/admin/materials"],
  ["Generate Papers", "/admin/generate-papers"],
  ["Review & Approve", "/admin/review-approve"],
  ["Published Papers", "/admin/published-papers"],
] as const;

const retainedSpecialistUrls = [
  "/admin/curriculum",
  "/admin/documents",
  "/admin/knowledge",
  "/admin/retrieval",
  "/admin/analytics",
  "/admin/blueprints",
  "/admin/generation",
  "/admin/validation",
  "/admin/review",
  "/admin/papers",
] as const;

function advancedDetails(): HTMLDetailsElement {
  const summary = screen.getByText("Advanced", { selector: "summary" });
  const details = summary.closest("details");
  if (!(details instanceof HTMLDetailsElement)) throw new Error("Advanced must be a details disclosure");
  return details;
}

describe("AdminHeader", () => {
  it("shows exactly the five teacher tasks in primary navigation", () => {
    render(<AdminHeader current="materials" role="reviewer" />);

    const primary = screen.getByRole("navigation", { name: "Primary admin navigation" });
    expect(
      within(primary)
        .getAllByRole("link")
        .map((link) => [link.textContent?.trim(), link.getAttribute("href")]),
    ).toEqual(primaryLinks);
    expect(within(primary).getByRole("link", { name: "Materials" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    for (const technicalLabel of [
      "Curriculum",
      "Knowledge",
      "RAG",
      "Analytics",
      "Blueprints",
      "Generation diagnostics",
      "Validation details",
      "Operations",
    ]) {
      expect(within(primary).queryByText(technicalLabel, { exact: false })).not.toBeInTheDocument();
    }
    expect(screen.getByText("reviewer")).toBeInTheDocument();
  });

  it("keeps specialist studios in a collapsed Advanced disclosure for reviewers", () => {
    render(<AdminHeader current="knowledge" role="reviewer" />);

    const advanced = advancedDetails();
    expect(advanced).not.toHaveAttribute("open");
    expect(advanced).toHaveTextContent("Curriculum");
    expect(advanced).toHaveTextContent(/Knowledge\s*\/\s*RAG/);
    expect(advanced).toHaveTextContent("Analytics");
    expect(advanced).toHaveTextContent("Blueprints");
    expect(advanced).toHaveTextContent("Generation diagnostics");
    expect(advanced).toHaveTextContent("Validation details");
    expect(advanced).not.toHaveTextContent("Operations");

    const advancedLinks = Array.from(advanced.querySelectorAll("a"));
    const advancedUrls = advancedLinks.map((link) => link.getAttribute("href"));
    expect(advancedUrls).toEqual(expect.arrayContaining([...retainedSpecialistUrls]));
    for (const href of retainedSpecialistUrls) {
      const link = advancedLinks.find((candidate) => candidate.getAttribute("href") === href);
      expect(link).toBeInTheDocument();
      expect(link?.textContent?.trim()).not.toBe("");
    }
    expect(advancedUrls).not.toContain("/admin/operations");
    expect(advancedLinks.find((link) => link.getAttribute("href") === "/admin/knowledge")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("adds Operations to Advanced only for an administrator", () => {
    render(<AdminHeader current="operations" role="admin" />);

    const advanced = advancedDetails();
    expect(advanced).not.toHaveAttribute("open");
    const operations = Array.from(advanced.querySelectorAll("a")).find(
      (link) => link.getAttribute("href") === "/admin/operations",
    );
    expect(operations).toHaveTextContent("Operations");
    expect(operations).toHaveAttribute("aria-current", "page");
  });
});
