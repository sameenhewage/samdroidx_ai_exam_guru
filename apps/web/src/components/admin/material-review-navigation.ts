type ReviewNavigationEvent = Event & {
  navigationType?: string;
  destination?: { url: string; sameDocument: boolean };
};

type NavigationWindow = Window & { navigation?: EventTarget };

export function supportsMaterialReviewNavigation() {
  return (
    typeof window !== "undefined" &&
    typeof (window as NavigationWindow).navigation?.addEventListener ===
      "function"
  );
}

function leavesReviewPage(destination: string) {
  const current = new URL(window.location.href);
  const target = new URL(destination, current);
  return (
    current.origin !== target.origin ||
    current.pathname !== target.pathname ||
    current.search !== target.search
  );
}

export function installMaterialReviewNavigationGuard({
  isDirty,
  confirmLeave,
  onCommittedLeave,
}: {
  isDirty: () => boolean;
  confirmLeave: () => boolean;
  onCommittedLeave?: () => void;
}) {
  const navigation = (window as NavigationWindow).navigation;
  let hiddenWithDraft = false;
  const pageHide = () => {
    hiddenWithDraft = isDirty();
    if (hiddenWithDraft) onCommittedLeave?.();
  };
  const pageShow = (event: PageTransitionEvent) => {
    if (event.persisted && hiddenWithDraft) onCommittedLeave?.();
    hiddenWithDraft = false;
  };
  const traverse = (event: Event) => {
    const change = event as ReviewNavigationEvent;
    if (
      !event.cancelable ||
      change.navigationType !== "traverse" ||
      change.destination?.sameDocument !== true ||
      !leavesReviewPage(change.destination.url) ||
      !isDirty()
    )
      return;
    if (!confirmLeave()) event.preventDefault();
  };
  const unload = (event: BeforeUnloadEvent) => {
    if (!isDirty()) return;
    event.preventDefault();
    event.returnValue = "";
  };
  const leave = (event: MouseEvent) => {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey ||
      event.altKey ||
      !isDirty()
    )
      return;
    const anchor =
      event.target instanceof Element ? event.target.closest("a[href]") : null;
    const href = anchor?.getAttribute("href");
    if (
      !anchor ||
      !href ||
      anchor.getAttribute("target")?.toLowerCase() === "_blank" ||
      anchor.hasAttribute("download") ||
      !leavesReviewPage(href)
    )
      return;
    if (!confirmLeave()) {
      event.preventDefault();
      event.stopPropagation();
    }
  };
  navigation?.addEventListener("navigate", traverse);
  window.addEventListener("beforeunload", unload);
  window.addEventListener("pagehide", pageHide);
  window.addEventListener("pageshow", pageShow);
  document.addEventListener("click", leave, true);
  return () => {
    navigation?.removeEventListener("navigate", traverse);
    window.removeEventListener("beforeunload", unload);
    window.removeEventListener("pagehide", pageHide);
    window.removeEventListener("pageshow", pageShow);
    document.removeEventListener("click", leave, true);
  };
}
