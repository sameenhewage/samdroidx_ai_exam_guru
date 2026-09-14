import type { components } from "@exam-guru/api-client";
import {
  errors,
  expect,
  test,
  type APIResponse,
  type Dialog,
  type CDPSession,
  type Locator,
  type Page,
} from "@playwright/test";
import { randomUUID } from "node:crypto";

import { requireIsolatedE2ERuntime } from "../playwright-runtime";
import {
  assertDisposableStudio,
  seedAdmittedScope,
  SYNTHETIC_WORKFLOW_EVIDENCE,
  syntheticPdf,
} from "./helpers/teacher-content-studio";

type Source = components["schemas"]["SourceDocumentResponse"];
type Reading = components["schemas"]["SourceReadJobResponse"];
type Understanding = components["schemas"]["UnderstandingPageResponse"];
type Preparation =
  components["schemas"]["MaterialKnowledgePreparationResponse"];
type Sections = components["schemas"]["MaterialKnowledgeUnitsResponse"];
type Workspace = components["schemas"]["MaterialKnowledgeUnitWorkspace"];
type Node = components["schemas"]["TaxonomyNodeResponse"];
type Unit = components["schemas"]["CurriculumUnitResponse"];
type Lesson = components["schemas"]["CurriculumLessonResponse"];

let navigationEvents: string[] = [];
let disposeTargetDiagnostics: ((failed: boolean) => Promise<void>) | undefined;
let verifyNativeReviewTab:
  | ((activate: () => Promise<void>, expectedUrl: string) => Promise<void>)
  | undefined;

async function inspectNativeTarget(session: CDPSession, targetId: string) {
  const attached = await session.send("Target.attachToTarget", {
    targetId,
    flatten: false,
  });
  try {
    const response = new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = setTimeout(() => {
        session.off("Target.receivedMessageFromTarget", receive);
        reject(new Error("Native target inspection timeout"));
      }, 5000);
      function receive(message: { sessionId: string; message: string }) {
        if (message.sessionId !== attached.sessionId) return;
        const parsed = JSON.parse(message.message);
        if (parsed.id !== 1) return;
        clearTimeout(timer);
        session.off("Target.receivedMessageFromTarget", receive);
        resolve(parsed.result?.result?.value ?? { evaluationFailed: true });
      }
      session.on("Target.receivedMessageFromTarget", receive);
    });
    const [result] = await Promise.all([
      response,
      session.send("Target.sendMessageToTarget", {
        sessionId: attached.sessionId,
        message: JSON.stringify({
          id: 1,
          method: "Runtime.evaluate",
          params: {
            returnByValue: true,
            expression:
              "(() => ({ ready: document.readyState, reviewRoute: location.pathname.endsWith('/review-curriculum'), reviewHeading: document.querySelector('h1')?.textContent === 'Review curriculum mapping', navigationUnavailable: typeof window.navigation === 'undefined', bodyPresent: !!document.body }))()",
          },
        }),
      }),
    ]);
    return result;
  } finally {
    await session.send("Target.detachFromTarget", {
      sessionId: attached.sessionId,
    });
  }
}

function navigationDiagnostic(
  event: string,
  detail: Record<string, unknown> = {},
) {
  const message =
    "MATERIAL_NAV_DIAG " +
    JSON.stringify({ event, time: Date.now(), ...detail });
  navigationEvents.push(message);
  console.log(message);
}

function diagnosticRoute(url: string) {
  try {
    const path = new URL(url).pathname;
    return path.endsWith("/review-curriculum")
      ? "curriculum-review"
      : /\/materials\/[^/]+$/.test(path)
        ? "material"
        : "other";
  } catch {
    return "pending";
  }
}

test.beforeEach(async ({ page, browser }, testInfo) => {
  navigationEvents = [];
  page.on("console", (message) => {
    if (message.text().startsWith("MATERIAL_NAV_DIAG ")) {
      navigationEvents.push(message.text());
      console.log(message.text());
    }
  });
  page.on("dialog", (dialog) =>
    navigationEvents.push(`MATERIAL_NAV_DIAG dialog:${dialog.type()}`),
  );
  const contextPage = (opened: Page) => {
    navigationDiagnostic("context-page", {
      route: diagnosticRoute(opened.url()),
      pages: page.context().pages().length,
    });
    opened.on("framenavigated", (frame) => {
      if (frame === opened.mainFrame())
        navigationDiagnostic("opened-page-navigation", {
          route: diagnosticRoute(frame.url()),
        });
    });
  };
  const popup = (opened: Page) =>
    navigationDiagnostic("opener-popup", {
      route: diagnosticRoute(opened.url()),
    });
  page.context().on("page", contextPage);
  page.on("popup", popup);
  const pageSession = await page.context().newCDPSession(page);
  const main = (await pageSession.send("Target.getTargetInfo")).targetInfo;
  await pageSession.detach();
  const targets = await browser.newBrowserCDPSession();
  const identities = new Map<string, number>();
  const identity = (id: string) => {
    if (!identities.has(id)) identities.set(id, identities.size + 1);
    return identities.get(id);
  };
  const targetEvent = (event: string, info: typeof main) =>
    navigationDiagnostic(event, {
      target: identity(info.targetId),
      main: info.targetId === main.targetId,
      sameContext: info.browserContextId === main.browserContextId,
      openerIsMain: info.openerId === main.targetId,
      attached: info.attached,
      type: info.type,
      route: diagnosticRoute(info.url),
    });
  targets.on("Target.targetCreated", ({ targetInfo }) =>
    targetEvent("target-created", targetInfo),
  );
  targets.on("Target.targetInfoChanged", ({ targetInfo }) =>
    targetEvent("target-changed", targetInfo),
  );
  targets.on("Target.targetDestroyed", ({ targetId }) =>
    navigationDiagnostic("target-destroyed", { target: identity(targetId) }),
  );
  await targets.send("Target.setDiscoverTargets", { discover: true });
  verifyNativeReviewTab = async (activate, expectedUrl) => {
    const before = new Set(
      (await targets.send("Target.getTargets")).targetInfos.map(
        (info) => info.targetId,
      ),
    );
    let opened: typeof main | undefined;
    await activate();
    try {
      await expect
        .poll(
          async () => {
            const current = (
              await targets.send("Target.getTargets")
            ).targetInfos.filter(
              (info) =>
                info.type === "page" &&
                info.browserContextId === main.browserContextId &&
                !before.has(info.targetId),
            );
            expect(current.length).toBeLessThanOrEqual(1);
            opened = current[0];
            return opened?.url;
          },
          { timeout: 10000 },
        )
        .toBe(expectedUrl);
      const targetId = opened!.targetId;
      await expect
        .poll(() => inspectNativeTarget(targets, targetId), { timeout: 10000 })
        .toEqual({
          ready: "complete",
          reviewRoute: true,
          reviewHeading: true,
          navigationUnavailable: true,
          bodyPresent: true,
        });
      navigationDiagnostic("native-review-tab-verified", {
        target: identity(targetId),
        publishedPages: page.context().pages().length,
      });
      expect(
        (await targets.send("Target.closeTarget", { targetId })).success,
      ).toBe(true);
      await expect
        .poll(
          async () =>
            (await targets.send("Target.getTargets")).targetInfos.some(
              (info) => info.targetId === targetId,
            ),
          { timeout: 10000 },
        )
        .toBe(false);
      opened = undefined;
    } finally {
      if (opened)
        await targets.send("Target.closeTarget", { targetId: opened.targetId });
    }
  };
  disposeTargetDiagnostics = async (failed) => {
    if (failed) {
      const current = await targets.send("Target.getTargets");
      const extra = current.targetInfos.filter(
        (info) =>
          info.type === "page" &&
          info.browserContextId === main.browserContextId &&
          info.targetId !== main.targetId,
      );
      navigationDiagnostic("failed-context-targets", {
        count: extra.length,
        publishedPages: page.context().pages().length,
      });
      for (const info of extra.slice(0, 3)) {
        try {
          navigationDiagnostic("native-target-document", {
            target: identity(info.targetId),
            ...(await inspectNativeTarget(targets, info.targetId)),
          });
        } catch (error) {
          navigationDiagnostic("native-target-document-unavailable", {
            target: identity(info.targetId),
            error: error instanceof Error ? error.name : "unknown",
          });
        }
      }
    }
    page.context().off("page", contextPage);
    page.off("popup", popup);
    await targets.detach();
  };
  await page.context().addInitScript((unavailable: boolean) => {
    const navigation = (
      window as Window & {
        navigation?: EventTarget & { currentEntry?: { index: number } };
      }
    ).navigation;
    if (unavailable) {
      const descriptor = Object.getOwnPropertyDescriptor(window, "navigation");
      if (descriptor && !descriptor.configurable)
        throw new Error("Navigation API test override is unavailable");
      Object.defineProperty(window, "navigation", {
        configurable: true,
        value: undefined,
      });
    }
    let sequence = 0;
    function route(value: string) {
      const path = new URL(value).pathname;
      return path.endsWith("/review-curriculum")
        ? "curriculum-review"
        : /\/materials\/[^/]+$/.test(path)
          ? "material"
          : "other";
    }
    function record(event: string, detail: Record<string, unknown> = {}) {
      const fields = [...document.querySelectorAll("textarea")];
      console.info(
        "MATERIAL_NAV_DIAG " +
          JSON.stringify({
            sequence: ++sequence,
            origin: performance.timeOrigin,
            time: performance.now(),
            event,
            route: route(location.href),
            historyIndex: navigation?.currentEntry?.index,
            historyLength: history.length,
            textareas: fields.length,
            visibleTextareas: fields.filter(
              (field) => field.getClientRects().length > 0,
            ).length,
            ...detail,
          }),
      );
    }
    for (const type of [
      "pointerdown",
      "mousedown",
      "pointerup",
      "mouseup",
      "click",
      "auxclick",
    ]) {
      window.addEventListener(
        type,
        (event) => {
          const anchor =
            event.target instanceof Element
              ? event.target.closest<HTMLAnchorElement>("a[href]")
              : null;
          if (!anchor?.pathname.endsWith("/review-curriculum")) return;
          const pointer = event as MouseEvent;
          record("entry-window-capture", {
            type,
            ctrl: pointer.ctrlKey,
            meta: pointer.metaKey,
            shift: pointer.shiftKey,
            alt: pointer.altKey,
            button: pointer.button,
            buttons: pointer.buttons,
            detail: pointer.detail,
            trusted: pointer.isTrusted,
            defaultPrevented: pointer.defaultPrevented,
            focused: document.hasFocus(),
            visibility: document.visibilityState,
          });
          if (type === "click")
            setTimeout(
              () =>
                record("entry-final-default", {
                  ctrl: pointer.ctrlKey,
                  defaultPrevented: pointer.defaultPrevented,
                }),
              0,
            );
        },
        true,
      );
    }
    window.addEventListener(
      "keydown",
      (event) => {
        if (["Control", "Meta", "Shift", "Alt"].includes(event.key))
          record("modifier-down", {
            key: event.key,
            ctrl: event.ctrlKey,
            meta: event.metaKey,
          });
      },
      true,
    );
    window.addEventListener(
      "keyup",
      (event) => {
        if (["Control", "Meta", "Shift", "Alt"].includes(event.key))
          record("modifier-up", {
            key: event.key,
            ctrl: event.ctrlKey,
            meta: event.metaKey,
          });
      },
      true,
    );
    window.addEventListener("material-review-navigation", (event) =>
      record(
        "component",
        (event as CustomEvent<Record<string, unknown>>).detail,
      ),
    );
    window.addEventListener("popstate", () => record("popstate-capture"), true);
    window.addEventListener("popstate", () => record("popstate-bubble"));
    window.addEventListener("pageshow", (event) =>
      record("pageshow", { persisted: event.persisted }),
    );
    window.addEventListener("pagehide", (event) =>
      record("pagehide", { persisted: event.persisted }),
    );
    navigation?.addEventListener(
      "navigate",
      (event) => {
        const change = event as Event & {
          navigationType?: string;
          canIntercept?: boolean;
          destination?: { url: string; sameDocument: boolean };
        };
        record("navigate-capture", {
          navigationType: change.navigationType,
          cancelable: change.cancelable,
          canIntercept: change.canIntercept,
          sameDocument: change.destination?.sameDocument,
          destination: change.destination
            ? route(change.destination.url)
            : "unknown",
        });
      },
      true,
    );
    navigation?.addEventListener("currententrychange", () =>
      record("currententrychange"),
    );
    const confirm = window.confirm.bind(window);
    window.confirm = (message) => {
      record("confirm-call");
      const accepted = confirm(message);
      record("confirm-result", { accepted });
      return accepted;
    };
    record("document-init", {
      nativeNavigationAvailable: !!navigation,
      applicationNavigationAvailable:
        typeof (window as Window & { navigation?: unknown }).navigation !==
        "undefined",
    });
  }, testInfo.title.startsWith("fallback Materials:"));
});

test.afterEach(async ({ page }, testInfo) => {
  if (testInfo.status !== testInfo.expectedStatus) {
    try {
      navigationDiagnostic(
        "entry-failure-state",
        await page.evaluate(
          () =>
            (
              window as Window & {
                __materialEntryProbe?: () => Record<string, unknown>;
              }
            ).__materialEntryProbe?.() ?? { probeMissing: true },
        ),
      );
    } catch {
      navigationDiagnostic("entry-failure-state-unavailable");
    }
  }
  await disposeTargetDiagnostics?.(testInfo.status !== testInfo.expectedStatus);
  disposeTargetDiagnostics = undefined;
  verifyNativeReviewTab = undefined;
  await testInfo.attach("material-navigation-events", {
    body: navigationEvents.join("\n"),
    contentType: "text/plain",
  });
});

async function json<T>(response: APIResponse, status = 200): Promise<T> {
  expect(response.status(), new URL(response.url()).pathname).toBe(status);
  return response.json() as Promise<T>;
}

async function cancelHistoryTraversal(
  page: Page,
  direction: "back" | "forward",
  expectedDialog: "confirm" | "beforeunload" = "confirm",
) {
  let dismissed = false;
  const dismiss = async (dialog: Dialog) => {
    expect(dialog.type()).toBe(expectedDialog);
    dismissed = true;
    await dialog.dismiss();
  };
  page.once("dialog", dismiss);
  try {
    try {
      const options = { waitUntil: "commit" as const, timeout: 1500 };
      if (direction === "back") await page.goBack(options);
      else await page.goForward(options);
    } catch (error) {
      if (!(error instanceof errors.TimeoutError)) throw error;
    }
    expect(dismissed).toBe(true);
  } finally {
    page.off("dialog", dismiss);
  }
}

async function historyPosition(page: Page) {
  const session = await page.context().newCDPSession(page);
  try {
    const history = await session.send("Page.getNavigationHistory");
    return { index: history.currentIndex, length: history.entries.length };
  } finally {
    await session.detach();
  }
}

async function presentation(button: Locator) {
  await expect(button).toBeVisible();
  const result = await button.evaluate((element) => {
    const style = getComputedStyle(element);
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d")!;
    function luminance(color: string) {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      const values = context.getImageData(0, 0, 1, 1).data;
      if (values[3] !== 255) throw new Error("Action colors must be opaque");
      const channels = Array.from(values)
        .slice(0, 3)
        .map((value) => {
          const channel = value / 255;
          return channel <= 0.04045
            ? channel / 12.92
            : ((channel + 0.055) / 1.055) ** 2.4;
        });
      return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
    }
    const foreground = luminance(style.color);
    const background = luminance(style.backgroundColor);
    return {
      color: style.color,
      background: style.backgroundColor,
      cursor: style.cursor,
      opacity: style.opacity,
      contrast:
        (Math.max(foreground, background) + 0.05) /
        (Math.min(foreground, background) + 0.05),
      focusVisible: element.matches(":focus-visible"),
      boxShadow: style.boxShadow,
    };
  });
  expect(result.contrast).toBeGreaterThanOrEqual(4.5);
  expect(result.opacity).toBe("1");
  return result;
}

for (const fallback of [false, true]) {
  const scenario = fallback
    ? "fallback Materials: native entry protects curriculum drafts without Navigation API"
    : "normal Materials: source review, automatic preparation, labelled mapping and isolated worker readiness";
  test(scenario, async ({ page, browser }, testInfo) => {
    test.setTimeout(300_000);
    const runtime = requireIsolatedE2ERuntime(process.env);
    expect([undefined, "", "deterministic"]).toContain(
      process.env.EXAM_GURU_RETRIEVAL_EMBEDDING_PROVIDER,
    );
    await page.goto("/admin/login");
    await page
      .getByRole("button", { name: "Continue as admin", exact: true })
      .click();
    await expect(page).toHaveURL(/\/admin\/home$/);
    const headers = await assertDisposableStudio(page.request);
    const scope = await seedAdmittedScope(page.request, 7);
    const marker = `curriculum-workflow-${randomUUID().replaceAll("-", "").slice(0, 12)}`;
    const source = await json<Source>(
      await page.request.post("/api/v1/admin/source-documents", {
        headers,
        multipart: {
          file: {
            name: `${marker}.pdf`,
            mimeType: "application/pdf",
            buffer: Buffer.concat([
              syntheticPdf("Synthetic visual-understanding fixture."),
              Buffer.from(`\n% ${marker}\n`, "ascii"),
            ]),
          },
          document_type: "other_approved",
          intake_metadata: JSON.stringify({
            candidate_grade: 7,
            medium_label: "English",
            subject_label: "Mathematics",
          }),
        },
      }),
      201,
    );
    const reading = await json<Reading>(
      await page.request.post(
        `/api/v1/admin/source-documents/${source.id}/read`,
        { headers },
      ),
      202,
    );
    await expect
      .poll(
        async () =>
          (
            await json<Reading>(
              await page.request.get(
                `/api/v1/admin/source-read-jobs/${reading.id}`,
              ),
            )
          ).status,
        { timeout: 45_000 },
      )
      .toBe("completed");
    await json<Source>(
      await page.request.patch(`/api/v1/admin/materials/${source.id}/scope`, {
        headers,
        data: {
          curriculum_version_id: scope.curriculum.id,
          unit_id: null,
          lesson_id: null,
          expected_version: source.metadata_scope_version,
          confirm_intake_metadata: true,
        } satisfies components["schemas"]["MaterialScopeCorrectionRequest"],
      }),
    );

    const curriculumPath = `/api/v1/admin/curriculum-versions/${scope.curriculum.id}`;
    const unit = await json<Unit>(
      await page.request.post(`${curriculumPath}/units`, {
        headers,
        data: {
          code: "NUMBER-WORK",
          title: "Number work",
          ordinal: 1,
        } satisfies components["schemas"]["CurriculumUnitCreate"],
      }),
      201,
    );
    const taxonomyPath = `/api/v1/admin/curricula/${scope.curriculum.id}/taxonomy/nodes`;
    const taxonomy: Node[] = [];
    for (const [level, title] of [
      ["competency", "Counting and grouping"],
      ["skill", "Recognising pairs"],
      ["sub_skill", "Counting by twos"],
      ["learning_concept", "Equal groups"],
    ] as const) {
      const created = await json<Node>(
        await page.request.post(taxonomyPath, {
          headers,
          data: {
            parent_id: taxonomy.at(-1)?.id ?? null,
            level,
            code: level.toUpperCase(),
            title,
            active: true,
          } satisfies components["schemas"]["TaxonomyNodeCreate"],
        }),
        201,
      );
      taxonomy.push(
        await json<Node>(
          await page.request.post(`${taxonomyPath}/${created.id}/review`, {
            headers,
          }),
        ),
      );
    }
    const lesson = await json<Lesson>(
      await page.request.post(`${curriculumPath}/lessons`, {
        headers,
        data: {
          unit_id: unit.id,
          code: "PAIRS",
          title: "Counting in pairs",
          ordinal: 1,
          taxonomy_node_ids: [taxonomy[1].id],
        } satisfies components["schemas"]["CurriculumLessonCreate"],
      }),
      201,
    );

    const observedRequests: { method: string; path: string }[] = [];
    const downloads: string[] = [];
    page.on("request", (request) =>
      observedRequests.push({
        method: request.method(),
        path: new URL(request.url()).pathname,
      }),
    );
    page.on("download", (download) =>
      downloads.push(download.suggestedFilename()),
    );
    await page.goto("/admin/materials");
    await page
      .getByRole("region", { name: "Materials by grade", exact: true })
      .getByRole("button", { name: /Grade 7\b/ })
      .click();
    const library = page.getByRole("region", {
      name: "Uploaded materials",
      exact: true,
    });
    await library
      .getByRole("searchbox", { name: "Search", exact: true })
      .fill(marker);
    await library
      .locator("article")
      .filter({ hasText: source.original_filename })
      .getByRole("link", { name: "View", exact: true })
      .click();
    await page
      .getByRole("link", { name: "Review page content", exact: true })
      .click();
    await page.getByRole("button", { name: "English", exact: true }).click();
    await page
      .getByRole("button", { name: "Analyze page", exact: true })
      .click();
    const understandingPath = `/api/v1/admin/materials/${source.id}/pages/1/understanding`;
    await expect
      .poll(
        async () =>
          (await json<Understanding>(await page.request.get(understandingPath)))
            .latest_job?.status,
        { timeout: 45_000 },
      )
      .toBe("succeeded");
    const original = page.getByRole("img", {
      name: "Original page 1",
      exact: true,
    });
    await expect
      .poll(() =>
        original.evaluate(
          (image: HTMLImageElement) => image.complete && image.naturalWidth > 0,
        ),
      )
      .toBe(true);
    const candidate = await json<Understanding>(
      await page.request.get(understandingPath),
    );
    await page
      .getByRole("button", { name: "Review this reading", exact: true })
      .click();
    await page
      .getByRole("checkbox", {
        name: "I compared this reading with the original page.",
        exact: true,
      })
      .check();
    await page
      .getByRole("checkbox", {
        name: "I checked every visible source detail.",
        exact: true,
      })
      .check();
    for (const uncertainty of candidate.candidate!.content.uncertainties)
      await page
        .getByRole("checkbox", { name: uncertainty.reason, exact: true })
        .check();
    await page
      .getByRole("textbox", {
        name: "Reason for accepting this reading",
        exact: true,
      })
      .fill(SYNTHETIC_WORKFLOW_EVIDENCE);
    await page
      .getByRole("button", { name: "Confirm checked page", exact: true })
      .click();
    await expect(
      page.getByText("Page checked against the original", { exact: true }),
    ).toBeVisible();
    const checked = await json<Understanding>(
      await page.request.get(understandingPath),
    );
    await page
      .getByRole("link", { name: "Back to material", exact: true })
      .click();
    const preparation = page.getByRole("region", {
      name: "Content preparation",
      exact: true,
    });
    const preparationPath = `/api/v1/admin/materials/${source.id}/knowledge-preparation`;
    await expect
      .poll(
        async () =>
          (await json<Preparation>(await page.request.get(preparationPath)))
            .status,
        { timeout: 90_000 },
      )
      .toBe("prepared");
    await preparation
      .getByRole("button", { name: "Refresh status", exact: true })
      .click();
    await expect(
      preparation.getByText("Checked content prepared", { exact: true }),
    ).toBeVisible();
    await expect(preparation.getByText(/^Ready for AI$/)).toHaveCount(0);
    const beforeMapping = observedRequests.length;
    const entry = page.getByRole("link", {
      name: "Review curriculum mapping",
      exact: true,
    });
    const entryDocument = await page.evaluate(() => performance.timeOrigin);
    if (fallback) {
      for (let entryAttempt = 0; entryAttempt < 12; entryAttempt += 1) {
        navigationDiagnostic("entry-stress-attempt", {
          attempt: entryAttempt + 1,
        });
        await entry.evaluate((element) => {
          let prevented = 0;
          let clicks = 0;
          const snapshot = () => ({
            prevented,
            clicks,
            connected: element.isConnected,
            currentAnchorSame:
              [...document.querySelectorAll<HTMLAnchorElement>("a[href]")].find(
                (anchor) => anchor.pathname.endsWith("/review-curriculum"),
              ) === element,
            focused: document.hasFocus(),
            visibility: document.visibilityState,
          });
          Object.assign(window, { __materialEntryProbe: snapshot });
          const record = (phase: string, event?: MouseEvent) =>
            console.info(
              "MATERIAL_NAV_DIAG " +
                JSON.stringify({
                  event: phase,
                  time: performance.now(),
                  ...snapshot(),
                  ctrl: event?.ctrlKey,
                  meta: event?.metaKey,
                  button: event?.button,
                  detail: event?.detail,
                  trusted: event?.isTrusted,
                  defaultPrevented: event?.defaultPrevented,
                }),
            );
          const target = (event: Event) => {
            clicks += 1;
            record("entry-target-capture", event as MouseEvent);
          };
          const bubble = (event: MouseEvent) => {
            if (event.composedPath().includes(element))
              record("entry-document-bubble", event);
          };
          const prevent = (event: Event) => {
            record("entry-preventer-before", event as MouseEvent);
            event.preventDefault();
            prevented += 1;
            record("entry-preventer-after", event as MouseEvent);
          };
          element.addEventListener("click", target, true);
          document.addEventListener("click", bubble);
          element.addEventListener("click", prevent, { once: true });
          Object.assign(window, {
            __materialEntryCleanup: () => {
              element.removeEventListener("click", target, true);
              document.removeEventListener("click", bubble);
              element.removeEventListener("click", prevent);
            },
          });
          record("entry-probe-installed");
        });
        await entry.click();
        await expect(page).toHaveURL(
          new RegExp(`/admin/materials/${source.id}$`),
        );
        navigationDiagnostic("entry-after-prevented-click", {
          pages: page.context().pages().length,
        });
        await verifyNativeReviewTab!(async () => {
          await entry.click({ modifiers: ["Control"] });
          navigationDiagnostic("entry-after-control-click", {
            pages: page.context().pages().length,
          });
        }, `${runtime.baseURL}/admin/materials/${source.id}/review-curriculum`);
        await expect(page).toHaveURL(
          new RegExp(`/admin/materials/${source.id}$`),
        );
        await entry.evaluate(() =>
          (
            window as Window & { __materialEntryCleanup?: () => void }
          ).__materialEntryCleanup?.(),
        );
      }
    }
    await entry.click();
    await expect(page).toHaveURL(
      new RegExp(`/admin/materials/${source.id}/review-curriculum$`),
    );
    expect(
      await page.evaluate(
        (original) => performance.timeOrigin === original,
        entryDocument,
      ),
    ).toBe(!fallback);
    expect(
      await page.evaluate(
        () =>
          typeof (window as Window & { navigation?: unknown }).navigation ===
          "undefined",
      ),
    ).toBe(fallback);
    if (!fallback) {
      await page
        .getByRole("link", { name: "Back to material", exact: true })
        .click();
      await expect(page).toHaveURL(
        new RegExp(`/admin/materials/${source.id}$`),
      );
      await page.goBack();
      await expect(page).toHaveURL(
        new RegExp(`/admin/materials/${source.id}/review-curriculum$`),
      );
    }
    const listPath = `/api/v1/admin/materials/${source.id}/knowledge-units`;
    const sections = await json<Sections>(
      await page.request.get(`${listPath}?limit=20&offset=0`),
    );
    expect(sections).toMatchObject({
      document_id: source.id,
      source_current: true,
      limit: 20,
      offset: 0,
      total: 1,
    });
    const summary = sections.items[0];
    expect(summary).toMatchObject({
      page_number: 1,
      sequence: 0,
      review: null,
      has_projection: true,
    });
    await page
      .getByRole("button", { name: "Page 1 · Section 1", exact: true })
      .click();
    await expect
      .poll(() =>
        original.evaluate(
          (image: HTMLImageElement) => image.complete && image.naturalWidth > 0,
        ),
      )
      .toBe(true);
    await expect(original).toHaveAttribute(
      "src",
      new RegExp(`/materials/${source.id}/pages/1/image$`),
    );
    await expect(page.locator("[data-original-page-viewer] img")).toHaveCount(
      1,
    );
    await expect(page.locator("iframe, embed, object")).toHaveCount(0);
    await expect(
      page.getByRole("region", { name: "What is visible", exact: true }),
    ).toContainText("Synthetic visual-understanding fixture.");
    await expect(
      page.getByRole("region", {
        name: "Accepted teaching points",
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.locator("details pre")).not.toBeVisible();
    const mapping = page.getByRole("form", {
      name: "Curriculum mapping",
      exact: true,
    });
    await mapping
      .getByRole("combobox", { name: "Unit / module", exact: true })
      .selectOption({ label: "Number work" });
    await mapping
      .getByRole("combobox", { name: "Lesson", exact: true })
      .selectOption({ label: "Counting in pairs" });
    await expect(
      mapping.getByRole("combobox", { name: "Teaching area", exact: true }),
    ).toHaveValue("");
    await expect(mapping).toContainText(
      "Lesson teaching links: Recognising pairs",
    );
    for (const [label, title] of [
      ["Teaching area", "Counting and grouping"],
      ["Skill", "Recognising pairs"],
      ["Sub-skill", "Counting by twos"],
      ["Concept", "Equal groups"],
    ])
      await mapping
        .getByRole("combobox", { name: label, exact: true })
        .selectOption({ label: title });
    await mapping
      .getByRole("textbox", { name: "Reason for this mapping", exact: true })
      .fill(SYNTHETIC_WORKFLOW_EVIDENCE);
    await expect(mapping).not.toContainText(
      /\b[0-9a-f]{8}-[0-9a-f-]{27,}\b|embedding|vector|fingerprint/i,
    );
    const save = mapping.getByRole("button", {
      name: "Save curriculum mapping",
      exact: true,
    });
    await expect(save).toBeDisabled();
    await save.scrollIntoViewIfNeeded();
    await page.mouse.move(0, 0);
    const disabled = await presentation(save);
    expect(disabled.cursor).toBe("not-allowed");
    const consent = mapping.getByRole("checkbox", {
      name: "I confirm this curriculum mapping for this section.",
      exact: true,
    });
    await consent.check();
    await expect(save).toBeEnabled();

    await page.setViewportSize({ width: 1024, height: 768 });
    const section = page.getByRole("region", {
      name: "Page 1 · Section 1",
      exact: true,
    });
    await expect(section).toHaveCSS("overflow-y", "auto");
    await save.scrollIntoViewIfNeeded();
    await expect(save).toBeInViewport();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await save.scrollIntoViewIfNeeded();
    await page.mouse.move(0, 0);
    await save.evaluate((element: HTMLElement) => element.blur());
    const normal = await presentation(save);
    expect(normal.cursor).toBe("pointer");
    await save.hover();
    const hover = await presentation(save);
    expect(hover.cursor).toBe("pointer");
    await page.mouse.move(0, 0);
    await consent.focus();
    await page.keyboard.press("Tab");
    await expect(save).toBeFocused();
    const keyboard = await presentation(save);
    expect(keyboard.cursor).toBe("pointer");
    expect(keyboard.focusVisible).toBe(true);
    expect(keyboard.boxShadow).not.toBe("none");
    expect(
      observedRequests
        .slice(beforeMapping)
        .every((request) => request.method === "GET"),
    ).toBe(true);
    const originalDraftField = await mapping
      .getByRole("textbox", { name: "Reason for this mapping", exact: true })
      .elementHandle();
    const originalHistory = await historyPosition(page);
    await cancelHistoryTraversal(
      page,
      "back",
      fallback ? "beforeunload" : "confirm",
    );
    await expect(page).toHaveURL(
      new RegExp(`/admin/materials/${source.id}/review-curriculum$`),
    );
    await expect(
      mapping.getByRole("textbox", {
        name: "Reason for this mapping",
        exact: true,
      }),
    ).toHaveValue(SYNTHETIC_WORKFLOW_EVIDENCE);
    expect(
      await originalDraftField!.evaluate((field) => field.isConnected),
    ).toBe(true);
    expect(await historyPosition(page)).toEqual(originalHistory);
    for (const direction of (fallback
      ? ["back", "back"]
      : ["forward", "back", "forward", "back"]) as ("back" | "forward")[]) {
      const dialogs = navigationEvents.filter((event) =>
        event.includes("dialog:"),
      ).length;
      await cancelHistoryTraversal(
        page,
        direction,
        fallback ? "beforeunload" : "confirm",
      );
      await expect(page).toHaveURL(
        new RegExp(`/admin/materials/${source.id}/review-curriculum$`),
      );
      expect(await historyPosition(page)).toEqual(originalHistory);
      expect(
        await originalDraftField!.evaluate((field) => field.isConnected),
      ).toBe(true);
      expect(
        navigationEvents.filter((event) => event.includes("dialog:")).length,
      ).toBe(dialogs + 1);
      await expect(
        mapping.getByRole("textbox", {
          name: "Reason for this mapping",
          exact: true,
        }),
      ).toHaveValue(SYNTHETIC_WORKFLOW_EVIDENCE);
      await expect(consent).toBeChecked();
    }
    const dialogsBeforeHash = navigationEvents.filter((event) =>
      event.includes("dialog:"),
    ).length;
    await page.evaluate(() => {
      window.location.hash = "curriculum-navigation-check";
    });
    await expect(page).toHaveURL(/#curriculum-navigation-check$/);
    await page.goBack();
    await expect(page).toHaveURL(
      new RegExp(`/admin/materials/${source.id}/review-curriculum$`),
    );
    expect(
      navigationEvents.filter((event) => event.includes("dialog:")).length,
    ).toBe(dialogsBeforeHash);
    expect(
      await originalDraftField!.evaluate((field) => field.isConnected),
    ).toBe(true);
    page.once("dialog", (dialog) => dialog.dismiss());
    await page
      .getByRole("link", { name: "Back to material", exact: true })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`/admin/materials/${source.id}/review-curriculum$`),
    );
    await expect(
      mapping.getByRole("textbox", {
        name: "Reason for this mapping",
        exact: true,
      }),
    ).toHaveValue(SYNTHETIC_WORKFLOW_EVIDENCE);
    await save.focus();

    const reviewPath = `${listPath}/${summary.unit_id}/curriculum-review`;
    const responsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === reviewPath &&
        response.request().method() === "POST",
    );
    await page.keyboard.press("Enter");
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    expect(response.request().postDataJSON()).toEqual({
      state: "reviewed",
      expected_version: 0,
      confirmed_mapping: true,
      reason: SYNTHETIC_WORKFLOW_EVIDENCE,
      curriculum_unit_id: unit.id,
      lesson_id: lesson.id,
      competency_id: taxonomy[0].id,
      skill_id: taxonomy[1].id,
      sub_skill_id: taxonomy[2].id,
      learning_concept_id: taxonomy[3].id,
    } satisfies components["schemas"]["KnowledgeReviewRequest"]);
    const reviewed = (await response.json()) as Workspace;
    await expect(
      page.getByText("Curriculum mapping saved.", { exact: true }),
    ).toBeVisible();
    expect(reviewed.workspace.review).toMatchObject({
      version: 1,
      state: "reviewed",
      confirmed_mapping: true,
    });
    const workspacePath = `${listPath}/${summary.unit_id}`;
    await expect
      .poll(
        async () =>
          (await json<Workspace>(await page.request.get(workspacePath)))
            .indexing.ready,
        { timeout: 120_000 },
      )
      .toBe(true);
    const current = await json<Workspace>(
      await page.request.get(workspacePath),
    );
    expect(current.indexing).toMatchObject({
      status: "ready",
      ready: true,
      retry_allowed: false,
    });
    expect(current.workspace).toMatchObject({
      source_current: true,
      eligible: true,
    });
    expect(current.workspace.review).toEqual(reviewed.workspace.review);
    const ai = page.getByRole("region", {
      name: "AI preparation",
      exact: true,
    });
    await expect(ai.getByText("Ready for AI", { exact: true })).toBeVisible({
      timeout: 15_000,
    });
    expect(
      observedRequests
        .slice(beforeMapping)
        .filter((request) => request.method === "POST"),
    ).toEqual([{ method: "POST", path: reviewPath }]);
    expect(
      observedRequests.some((request) =>
        /embedding-jobs|\/prepare$|\/knowledge-units\/[^/]+\/review$/.test(
          request.path,
        ),
      ),
    ).toBe(false);
    expect(
      observedRequests.some((request) => request.path.endsWith("/original")),
    ).toBe(false);
    expect(downloads).toEqual([]);
    expect(
      (await json<Understanding>(await page.request.get(understandingPath)))
        .trusted,
    ).toEqual(checked.trusted);
    await testInfo.attach("curriculum-review-action-styles", {
      body: JSON.stringify({ disabled, normal, hover, keyboard }),
      contentType: "application/json",
    });
    await testInfo.attach("curriculum-review-ready", {
      body: await page.screenshot(),
      contentType: "image/png",
    });

    for (const direction of (fallback ? ["back"] : ["back", "forward"]) as (
      | "back"
      | "forward"
    )[]) {
      if (direction === "forward") {
        await page.goForward();
        await expect(page).toHaveURL(
          new RegExp(`/admin/materials/${source.id}/review-curriculum$`),
        );
        await page
          .getByRole("button", { name: "Page 1 · Section 1", exact: true })
          .click();
        await expect(
          mapping.getByRole("textbox", {
            name: "Reason for this mapping",
            exact: true,
          }),
        ).toHaveValue("");
        await page
          .getByRole("link", { name: "Back to material", exact: true })
          .click();
        await expect(page).toHaveURL(
          new RegExp(`/admin/materials/${source.id}$`),
        );
        await page.goBack();
        await expect(page).toHaveURL(
          new RegExp(`/admin/materials/${source.id}/review-curriculum$`),
        );
        await page
          .getByRole("button", { name: "Page 1 · Section 1", exact: true })
          .click();
      }
      await mapping
        .getByRole("textbox", { name: "Reason for this mapping", exact: true })
        .fill(
          "Discard this navigation-only draft; do not save another review.",
        );
      const beforeLeaving = await historyPosition(page);
      page.once("dialog", (dialog) => dialog.accept());
      if (direction === "back") await page.goBack();
      else await page.goForward();
      await expect(page).toHaveURL(
        new RegExp(`/admin/materials/${source.id}$`),
      );
      expect(await historyPosition(page)).toEqual({
        index: beforeLeaving.index + (direction === "back" ? -1 : 1),
        length: beforeLeaving.length,
      });
      await expect(
        page.getByRole("textbox", {
          name: "Reason for this mapping",
          exact: true,
        }),
      ).toHaveCount(0);
    }
    if (fallback) {
      await page.goForward();
      await expect(page).toHaveURL(
        new RegExp(`/admin/materials/${source.id}/review-curriculum$`),
      );
      expect(
        await page.evaluate(
          () =>
            typeof (window as Window & { navigation?: unknown }).navigation ===
            "undefined",
        ),
      ).toBe(true);
      await page
        .getByRole("button", { name: "Page 1 · Section 1", exact: true })
        .click();
      await expect(
        mapping.getByRole("textbox", {
          name: "Reason for this mapping",
          exact: true,
        }),
      ).toHaveValue("");
      await mapping
        .getByRole("textbox", { name: "Reason for this mapping", exact: true })
        .fill("Discard this ordinary-link draft without saving.");
      page.once("dialog", (dialog) => dialog.accept());
      await page
        .getByRole("link", { name: "Back to material", exact: true })
        .click();
      await expect(page).toHaveURL(
        new RegExp(`/admin/materials/${source.id}$`),
      );
    }
    expect(
      observedRequests
        .slice(beforeMapping)
        .filter((request) => request.method === "POST"),
    ).toEqual([{ method: "POST", path: reviewPath }]);
    expect(
      (await json<Workspace>(await page.request.get(workspacePath))).workspace
        .review,
    ).toEqual(reviewed.workspace.review);

    const reviewer = await browser.newPage({ baseURL: runtime.baseURL });
    try {
      await reviewer.goto("/admin/login");
      await reviewer
        .getByRole("button", { name: "Continue as reviewer", exact: true })
        .click();
      await reviewer.goto(`/admin/materials/${source.id}/review-curriculum`);
      await reviewer
        .getByRole("button", { name: "Page 1 · Section 1", exact: true })
        .click();
      await expect(
        reviewer.getByText("Reviewer access is read-only.", { exact: true }),
      ).toBeVisible();
      await expect(
        reviewer.getByRole("region", {
          name: "Accepted teaching points",
          exact: true,
        }),
      ).toBeVisible();
      await expect(
        reviewer.getByRole("button", {
          name: "Save curriculum mapping",
          exact: true,
        }),
      ).toHaveCount(0);
      await expect(
        reviewer.getByRole("button", {
          name: "Try AI preparation again",
          exact: true,
        }),
      ).toHaveCount(0);
    } finally {
      await reviewer.close();
    }
  });
}
