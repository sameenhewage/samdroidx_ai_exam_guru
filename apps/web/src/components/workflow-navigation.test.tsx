import { cleanup, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WorkflowNavigation } from "./workflow-navigation";

describe("WorkflowNavigation", () => {
  it("shows the home Operations workflow link only for an authenticated administrator", () => {
    render(<WorkflowNavigation role="admin" />);
    expect(screen.getByRole("link", { name: /Operations dashboard/ })).toHaveAttribute(
      "href",
      "/admin/operations",
    );

    cleanup();
    render(<WorkflowNavigation role="reviewer" />);
    expect(screen.queryByRole("link", { name: /Operations dashboard/ })).not.toBeInTheDocument();

    cleanup();
    render(<WorkflowNavigation />);
    expect(screen.queryByRole("link", { name: /Operations dashboard/ })).not.toBeInTheDocument();
  });
});
