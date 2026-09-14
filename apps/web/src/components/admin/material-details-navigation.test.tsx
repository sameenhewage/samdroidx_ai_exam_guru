import { render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MaterialCurriculumReviewLink } from "./material-details";

vi.mock("next/link", () => ({
  default: ({
    prefetch,
    ...props
  }: ComponentProps<"a"> & { prefetch?: boolean }) => (
    <a
      {...props}
      data-client-navigation="true"
      data-prefetch={String(prefetch)}
    />
  ),
}));

afterEach(() => vi.unstubAllGlobals());

describe("normal Materials curriculum review entry", () => {
  it.each([false, true])(
    "uses native document navigation only when pre-navigation protection is unavailable (%s)",
    (available) => {
      vi.stubGlobal("navigation", available ? new EventTarget() : undefined);
      render(
        <MaterialCurriculumReviewLink documentId="material" language="en" />,
      );
      const link = screen.getByRole("link", {
        name: "Review curriculum mapping",
      });
      expect(link).toHaveAttribute(
        "href",
        "/admin/materials/material/review-curriculum",
      );
      expect(link).not.toHaveAttribute("target");
      if (available) {
        expect(link).toHaveAttribute("data-client-navigation", "true");
        expect(link).toHaveAttribute("data-prefetch", "false");
      } else {
        expect(link).not.toHaveAttribute("data-client-navigation");
      }
    },
  );

  it("starts with a safe native entry during server rendering", () => {
    vi.stubGlobal("navigation", new EventTarget());
    const html = renderToString(
      <MaterialCurriculumReviewLink documentId="material" language="si" />,
    );
    expect(html).toContain("විෂයමාලා ගැළපීම පරීක්ෂා කරන්න");
    expect(html).not.toContain("data-client-navigation");
  });

  it("does not intercept native, modified, or already-prevented fallback clicks", () => {
    vi.stubGlobal("navigation", undefined);
    render(
      <MaterialCurriculumReviewLink documentId="material" language="en" />,
    );
    const link = screen.getByRole("link", {
      name: "Review curriculum mapping",
    });
    const prevented: boolean[] = [];
    const stopDefault = (event: MouseEvent) => {
      prevented.push(event.defaultPrevented);
      event.preventDefault();
    };
    document.body.addEventListener("click", stopDefault);
    try {
      for (const options of [
        {},
        { ctrlKey: true },
        { metaKey: true },
        { shiftKey: true },
        { button: 1 },
      ]) {
        link.dispatchEvent(
          new MouseEvent("click", {
            bubbles: true,
            cancelable: true,
            ...options,
          }),
        );
      }
      const cancelled = new MouseEvent("click", {
        bubbles: true,
        cancelable: true,
      });
      cancelled.preventDefault();
      link.dispatchEvent(cancelled);
      expect(prevented).toEqual([false, false, false, false, false, true]);
    } finally {
      document.body.removeEventListener("click", stopDefault);
    }
  });
});
