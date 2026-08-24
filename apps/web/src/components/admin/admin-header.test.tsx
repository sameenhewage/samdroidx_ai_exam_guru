import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AdminHeader } from "./admin-header";

describe("AdminHeader", () => {
  it("links every existing admin studio to Knowledge Studio and marks the current area", () => {
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
    expect(screen.getByRole("link", { name: "Knowledge" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByText("reviewer")).toBeInTheDocument();
  });
});
