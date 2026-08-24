import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AdminHeader } from "./admin-header";

describe("AdminHeader", () => {
  it("links every shared admin studio, hides Operations from reviewers, and marks the current area", () => {
    render(<AdminHeader current="knowledge" role="reviewer" />);

    expect(screen.getByRole("link", { name: "Curriculum" })).toHaveAttribute(
      "href",
      "/admin/curriculum",
    );
    expect(screen.getByRole("link", { name: "Documents" })).toHaveAttribute(
      "href",
      "/admin/documents",
    );
    expect(screen.getByRole("link", { name: "Knowledge" })).toHaveAttribute(
      "href",
      "/admin/knowledge",
    );
    expect(screen.getByRole("link", { name: "RAG Explorer" })).toHaveAttribute(
      "href",
      "/admin/retrieval",
    );
    expect(screen.getByRole("link", { name: "Analytics" })).toHaveAttribute(
      "href",
      "/admin/analytics",
    );
    expect(screen.getByRole("link", { name: "Blueprints" })).toHaveAttribute(
      "href",
      "/admin/blueprints",
    );
    expect(screen.getByRole("link", { name: "Generation" })).toHaveAttribute(
      "href",
      "/admin/generation",
    );
    expect(screen.getByRole("link", { name: "Validation" })).toHaveAttribute(
      "href",
      "/admin/validation",
    );
    expect(screen.getByRole("link", { name: "Review" })).toHaveAttribute(
      "href",
      "/admin/review",
    );
    expect(screen.getByRole("link", { name: "Papers" })).toHaveAttribute(
      "href",
      "/admin/papers",
    );
    expect(screen.queryByRole("link", { name: "Operations" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Knowledge" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByText("reviewer")).toBeInTheDocument();
  });

  it("shows the Operations dashboard link only to an administrator", () => {
    render(<AdminHeader current="operations" role="admin" />);

    expect(screen.getByRole("link", { name: "Operations" })).toHaveAttribute(
      "href",
      "/admin/operations",
    );
    expect(screen.getByRole("link", { name: "Operations" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
