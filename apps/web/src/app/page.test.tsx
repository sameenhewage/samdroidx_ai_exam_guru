import { render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { cookies } from "next/headers";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Home from "./page";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));

function useRole(role?: string) {
  vi.mocked(cookies).mockResolvedValue({
    get: (name: string) => {
      if (!role) return undefined;
      if (name === "exam_guru_admin_role") return { name, value: role };
      if (name === "exam_guru_admin_token") return { name, value: "session-token" };
      return undefined;
    },
  } as unknown as Awaited<ReturnType<typeof cookies>>);
}

async function renderHome() {
  render(await Home());
}

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
  beforeEach(() => useRole());

  it("presents the Priority 1 content workflow", async () => {
    await renderHome();

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
    expect(within(navigation).getByRole("link", { name: /Papers/ })).toHaveAttribute(
      "href",
      "/admin/papers",
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
    const papersCard = document.querySelector("#papers");
    expect(papersCard).not.toBeNull();
    expect(within(papersCard as HTMLElement).getByRole("link", { name: "Open Paper Studio" })).toHaveAttribute(
      "href",
      "/admin/papers",
    );
  });

  it("shows the Operations workflow link only on the administrator home", async () => {
    useRole("admin");
    await renderHome();

    const navigation = screen.getByRole("navigation", { name: "Content workflow" });
    expect(within(navigation).getByRole("link", { name: /Operations dashboard/ })).toHaveAttribute(
      "href",
      "/admin/operations",
    );
  });

  it("does not expose the Operations workflow link on the reviewer home", async () => {
    useRole("reviewer");
    await renderHome();

    const navigation = screen.getByRole("navigation", { name: "Content workflow" });
    expect(
      within(navigation).queryByRole("link", { name: /Operations dashboard/ }),
    ).not.toBeInTheDocument();
  });

  it("fails closed when a session has an unrecognized role", async () => {
    useRole("operator");
    await renderHome();

    const navigation = screen.getByRole("navigation", { name: "Content workflow" });
    expect(
      within(navigation).queryByRole("link", { name: /Operations dashboard/ }),
    ).not.toBeInTheDocument();
  });

  it("has no automated accessibility violations", async () => {
    const home = await Home();
    const { container } = render(home);
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
