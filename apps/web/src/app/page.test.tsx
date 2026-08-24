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
  "Validation",
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
    expect(within(navigation).getByRole("link", { name: /Generation/ })).toHaveAttribute(
      "href",
      "/admin/generation",
    );
    expect(within(navigation).getByRole("link", { name: /Validation/ })).toHaveAttribute(
      "href",
      "/admin/validation",
    );
    expect(within(navigation).getByRole("link", { name: /Review queue/ })).toHaveAttribute(
      "href",
      "/admin/review",
    );
    const validationCard = document.querySelector("#validation");
    expect(validationCard).not.toBeNull();
    expect(within(validationCard as HTMLElement).getByRole("link", { name: "Open Validation Studio" })).toHaveAttribute(
      "href",
      "/admin/validation",
    );
    const reviewCard = document.querySelector("#review-queue");
    expect(reviewCard).not.toBeNull();
    expect(within(reviewCard as HTMLElement).getByRole("link", { name: "Open Reviewer Studio" })).toHaveAttribute(
      "href",
      "/admin/review",
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
