import { render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it } from "vitest";

import Home from "./page";

const workflowAreas = [
  "Curriculum",
  "Documents",
  "Extraction review",
  "Historical questions",
  "RAG explorer",
  "Exam intelligence",
  "Blueprints",
  "Generation",
  "Review queue",
  "Papers",
];

describe("admin foundation shell", () => {
  it("presents the Priority 1 content workflow", () => {
    render(<Home />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Admin Content Studio" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Priority 1 foundation")).toBeInTheDocument();

    const navigation = screen.getByRole("navigation", {
      name: "Content workflow",
    });
    for (const area of workflowAreas) {
      expect(navigation).toHaveTextContent(area);
    }
    expect(
      within(navigation).getByRole("link", { name: /Historical questions/ }),
    ).toHaveAttribute("href", "/admin/knowledge");
    expect(within(navigation).getByRole("link", { name: /RAG explorer/ })).toHaveAttribute(
      "href",
      "/admin/retrieval",
    );
    expect(
      within(navigation).getByRole("link", { name: /Exam intelligence/ }),
    ).toHaveAttribute("href", "/admin/analytics");
    expect(within(navigation).getByRole("link", { name: /Blueprints/ })).toHaveAttribute(
      "href",
      "/admin/blueprints",
    );
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(<Home />);
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { level: 1, name: "Admin Content Studio" }),
      ).toBeInTheDocument(),
    );

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});
