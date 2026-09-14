import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installMaterialReviewNavigationGuard } from "./material-review-navigation";

const reviewPath = "/admin/materials/current/review-curriculum";
let navigation: EventTarget;
let dispose: (() => void) | undefined;
let previousUrl: string;

function traverse(destination: string, cancelable = true) {
  return Object.assign(new Event("navigate", { cancelable }), {
    navigationType: "traverse",
    destination: {
      url: new URL(destination, window.location.href).href,
      sameDocument: true,
    },
  });
}

beforeEach(() => {
  previousUrl = window.location.href;
  window.history.replaceState(null, "", reviewPath);
  navigation = new EventTarget();
  vi.stubGlobal("navigation", navigation);
});

afterEach(() => {
  dispose?.();
  dispose = undefined;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", previousUrl);
  document.body.replaceChildren();
});

describe("material review pre-navigation guard", () => {
  it.each(["/admin/materials/current", "/admin/home"])(
    "cancels traversal to %s before history or route restoration",
    (destination) => {
      const confirmLeave = vi.fn(() => false);
      dispose = installMaterialReviewNavigationGuard({
        isDirty: () => true,
        confirmLeave,
      });
      const push = vi.spyOn(window.history, "pushState");
      const replace = vi.spyOn(window.history, "replaceState");
      const event = traverse(destination);
      expect(navigation.dispatchEvent(event)).toBe(false);
      expect(event.defaultPrevented).toBe(true);
      expect(confirmLeave).toHaveBeenCalledTimes(1);
      expect(push).not.toHaveBeenCalled();
      expect(replace).not.toHaveBeenCalled();
      expect(window.location.pathname).toBe(reviewPath);
    },
  );

  it("allows consented traversal once without a late popstate confirmation", () => {
    const confirmLeave = vi.fn(() => true);
    dispose = installMaterialReviewNavigationGuard({
      isDirty: () => true,
      confirmLeave,
    });
    expect(navigation.dispatchEvent(traverse("/admin/materials/current"))).toBe(
      true,
    );
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(confirmLeave).toHaveBeenCalledTimes(1);
  });

  it("reads the live draft state and removes its listeners on disposal", () => {
    let dirty = false;
    const confirmLeave = vi.fn(() => false);
    dispose = installMaterialReviewNavigationGuard({
      isDirty: () => dirty,
      confirmLeave,
    });
    expect(navigation.dispatchEvent(traverse("/admin/materials/current"))).toBe(
      true,
    );
    expect(confirmLeave).not.toHaveBeenCalled();
    dirty = true;
    expect(navigation.dispatchEvent(traverse("/admin/materials/current"))).toBe(
      false,
    );
    dispose();
    expect(navigation.dispatchEvent(traverse("/admin/materials/current"))).toBe(
      true,
    );
    expect(confirmLeave).toHaveBeenCalledTimes(1);
  });

  it("leaves same-page hash navigation and framework push/replace events alone", () => {
    const confirmLeave = vi.fn(() => false);
    dispose = installMaterialReviewNavigationGuard({
      isDirty: () => true,
      confirmLeave,
    });
    expect(
      navigation.dispatchEvent(traverse(`${reviewPath}#checked-content`)),
    ).toBe(true);
    for (const navigationType of ["push", "replace"]) {
      const event = Object.assign(traverse("/admin/materials/current"), {
        navigationType,
      });
      expect(navigation.dispatchEvent(event)).toBe(true);
    }
    expect(confirmLeave).not.toHaveBeenCalled();
  });

  it("retains native unload and ordinary-link protection without the Navigation API", () => {
    vi.stubGlobal("navigation", undefined);
    const confirmLeave = vi.fn(() => false);
    dispose = installMaterialReviewNavigationGuard({
      isDirty: () => true,
      confirmLeave,
    });
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);
    const link = document.createElement("a");
    link.href = "/admin/materials/current";
    document.body.append(link);
    const click = new MouseEvent("click", {
      button: 0,
      bubbles: true,
      cancelable: true,
    });
    link.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    expect(confirmLeave).toHaveBeenCalledTimes(1);
  });

  it("discards only after committed pagehide and keeps a cached return discarded", () => {
    vi.stubGlobal("navigation", undefined);
    let dirty = true;
    const committed = vi.fn(() => {
      dirty = false;
    });
    dispose = installMaterialReviewNavigationGuard({
      isDirty: () => dirty,
      confirmLeave: () => false,
      onCommittedLeave: committed,
    });
    window.dispatchEvent(new Event("beforeunload", { cancelable: true }));
    expect(committed).not.toHaveBeenCalled();
    expect(dirty).toBe(true);
    window.dispatchEvent(
      new PageTransitionEvent("pagehide", { persisted: true }),
    );
    expect(committed).toHaveBeenCalledTimes(1);
    expect(dirty).toBe(false);
    window.dispatchEvent(
      new PageTransitionEvent("pageshow", { persisted: true }),
    );
    expect(committed).toHaveBeenCalledTimes(2);
    window.dispatchEvent(
      new PageTransitionEvent("pageshow", { persisted: true }),
    );
    expect(committed).toHaveBeenCalledTimes(2);
  });

  it("does not discard for an initial pageshow or a clean pagehide", () => {
    const committed = vi.fn();
    dispose = installMaterialReviewNavigationGuard({
      isDirty: () => false,
      confirmLeave: () => false,
      onCommittedLeave: committed,
    });
    window.dispatchEvent(
      new PageTransitionEvent("pageshow", { persisted: false }),
    );
    window.dispatchEvent(
      new PageTransitionEvent("pagehide", { persisted: true }),
    );
    window.dispatchEvent(
      new PageTransitionEvent("pageshow", { persisted: true }),
    );
    expect(committed).not.toHaveBeenCalled();
  });

  it("does not promise cancellation for non-cancellable or cross-document traversal", () => {
    const confirmLeave = vi.fn(() => false);
    dispose = installMaterialReviewNavigationGuard({
      isDirty: () => true,
      confirmLeave,
    });
    expect(
      navigation.dispatchEvent(traverse("/admin/materials/current", false)),
    ).toBe(true);
    const crossDocument = traverse("/admin/materials/current");
    crossDocument.destination.sameDocument = false;
    expect(navigation.dispatchEvent(crossDocument)).toBe(true);
    expect(confirmLeave).not.toHaveBeenCalled();
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);
  });
});
