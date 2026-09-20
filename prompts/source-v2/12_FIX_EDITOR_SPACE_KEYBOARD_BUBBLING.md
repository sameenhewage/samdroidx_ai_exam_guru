# Source V2 — Fix Space/Enter Being Blocked Inside Review Editors

## Bug

In the Source V2 review screen, when a teacher edits text inside the correction textarea, pressing **Space** does not insert a space. The same parent keyboard handler also risks blocking **Enter/newline** inside child editors.

Observed screen:
`/admin/source-v2/[pageId]`

Primary file:
`apps/web/src/components/admin/source-v2-review.tsx`

## Root cause

Each region card is a focusable `<li tabIndex={0}>` with this keyboard handler:

```tsx
onKeyDown={(event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    select(region.region_id, "card");
  }
}}
```

Keyboard events from nested controls (especially the text correction `<textarea>`, and any other input/textarea/button) bubble to the `li`.

Therefore:
- Space inside textarea bubbles to the card;
- card calls `preventDefault()`;
- browser never inserts the space;
- Enter/newline can be blocked for the same reason.

The existing `onFocus` handler already correctly guards with:

```tsx
if (event.target === event.currentTarget) {
  select(...)
}
```

The key handler should follow the same ownership rule.

---

## Required fix

Make the region-card keyboard activation run **only when the card itself owns the key event**.

Preferred shape:

```tsx
onKeyDown={(event) => {
  if (event.target !== event.currentTarget) {
    return;
  }

  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    select(region.region_id, "card");
  }
}}
```

Equivalent robust handling is acceptable, but do **not** solve this by globally stopping keyboard propagation in every child control.

Card keyboard accessibility must remain:
- card focused + Space → select card;
- card focused + Enter → select card.

Nested editable controls must behave natively:
- textarea Space inserts a space;
- textarea Enter inserts a newline;
- text input Space works;
- buttons retain normal keyboard activation;
- description editor behaves correctly too.

---

## Regression tests

Update:

`apps/web/src/components/admin/source-v2-review.test.tsx`

Add focused tests proving:

1. Open a text correction editor.
2. Type a Sinhala/English string containing spaces.
3. Press Space inside the textarea and verify the value contains it.
4. Press Enter inside the textarea and verify a newline is inserted.
5. The region card remains selected normally when the card itself receives Space/Enter.
6. Nested button keyboard events are not hijacked by the parent card.
7. If the visual-description editor is a textarea/input inside the same card, verify spaces/newlines there too.

Prefer `@testing-library/user-event` for realistic keyboard behavior if already available; otherwise use the repository's existing test conventions.

Also extend:

`apps/web/e2e/source-v2-review.spec.ts`

with a real-browser regression:
- open correction editor;
- enter text containing spaces;
- optionally insert a newline;
- assert exact textarea value before save;
- save;
- assert saved text preserves whitespace.

Use disposable/synthetic Source V2 fixture data only. Do not alter real pilot review state.

---

## Validation

Run at least:

- focused Source V2 review component tests;
- Source V2 Playwright E2E;
- frontend lint;
- frontend TypeScript;
- frontend production build.

Then use Chrome DevTools MCP on the real review page and manually verify:
- clicking "Correct text" opens editor;
- Space inserts space;
- Enter inserts newline;
- existing text is not unexpectedly modified;
- no console error;
- no failed network request caused by the fix.

Do not perform a real human verification/confirmation as part of this bugfix.

---

## Scope

This is a focused UI input bugfix.

Do not:
- redesign Source V2;
- change transcript contents;
- change source verification state;
- regenerate pilot data;
- touch RAG/vectorization;
- rework unrelated keyboard shortcuts.

If another ancestor keyboard handler has the same bubbling bug, fix it only where necessary and add regression coverage.

---

## Commit

After PASS:

`fix(source-v2): allow whitespace in review editors`

Push master.

## Final response

Return only:

SOURCE V2 EDITOR INPUT FIX: PASS / BLOCKED

Final commit:
<hash>

Root cause:
<short>

Component tests:
<result>

Playwright:
<result>

TypeScript/lint/build:
<result>

Chrome DevTools MCP:
<result>

Real source review state changed:
NO

Blocker:
<only if blocked>
