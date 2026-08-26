import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AdminHome } from "./admin-home";

const teacherActions = [
  ["Manage materials", "/admin/materials"],
  ["Generate a paper", "/admin/generate-papers"],
  ["Review and approve", "/admin/review-approve"],
  ["Find published papers", "/admin/published-papers"],
] as const;

describe("AdminHome", () => {
  it("explains the normal paper workflow in teacher language with direct action cards", () => {
    render(<AdminHome role="admin" />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Create and manage exam papers" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /add teaching materials, generate a paper, check every question and answer, then publish/i,
      ),
    ).toBeInTheDocument();

    const actions = screen.getByRole("region", { name: "Start here" });
    expect(
      within(actions)
        .getAllByRole("link")
        .map((link) => [link.textContent?.trim(), link.getAttribute("href")]),
    ).toEqual(teacherActions);
    expect(actions).toHaveTextContent("See what is already uploaded or add a PDF");
    expect(actions).toHaveTextContent("Choose a grade, subject, and lesson scope");
    expect(actions).toHaveTextContent("Check questions, answers, explanations, and marking");
    expect(actions).toHaveTextContent("Open papers that are ready to use");
  });

  it("does not expose delivery phases or retrieval implementation terms", () => {
    const { container } = render(<AdminHome role="reviewer" />);
    const copy = container.textContent ?? "";

    for (const implementationTerm of [
      /\bP1\b/i,
      /\bRAG\b/i,
      /embeddings?/i,
      /generation run/i,
      /run IDs?/i,
      /blueprint IDs?/i,
      /context IDs?/i,
    ]) {
      expect(copy).not.toMatch(implementationTerm);
    }
  });
});
