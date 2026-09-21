# Source V2 — Fix Repeat Editing and Establish Bilingual Review UX

## User-reported problems

The human reviewer reported two real usability problems on the Source V2 review screen.

### A. Text correction appears to work only once

A region can be edited/corrected once, but when the reviewer tries to edit the corrected text again, the workflow does not behave correctly.

This is not acceptable.

Human review must support an arbitrary revision chain:

machine r1
→ human correction r2
→ human correction r3
→ human correction r4
→ confirm current revision

and also:

verified revision
→ Edit again
→ new unverified revision
→ edit again if needed
→ confirm final revision

The reviewer must never be forced to accept a typo just because they already edited once.

### B. The review UI is too Sinhala-only

The product is for Sri Lankan teachers, so Sinhala guidance is useful, but this is also a technical/admin review tool with stable domain terminology.

The user explicitly wants:

- Sinhala + English mixed for domain labels/statuses/help.
- Action buttons in English.
- Do not make the entire system 100% Sinhala.

This becomes a review-UX contract, not a one-off text tweak.

---

## 1. Reproduce the repeat-edit bug before changing code

Start from latest master.

Use a disposable Source V2 fixture and reproduce the full correction chain against the actual API/UI.

At minimum prove the current behavior for:

1. machine candidate revision 1;
2. click Edit;
3. save correction → revision 2;
4. without confirming, click Edit again;
5. change text again;
6. save correction → revision 3;
7. edit revision 3 again if needed;
8. confirm only the final revision;
9. reload page;
10. final verified text must equal the last human text.

Also test:

verified revision
→ Edit again
→ save new correction
→ state becomes unverified
→ Edit again before confirming
→ save another correction
→ confirm final revision.

Do not guess the root cause. Capture the actual failing API response/UI state first.

Inspect both:

- `apps/web/src/components/admin/source-v2-review.tsx`
- `apps/api/src/exam_guru_api/source_v2/repository.py`

and relevant schemas/routes/database constraints.

The backend already conceptually supports child revisions; if the actual bug is UI stale state, stale candidate/revision data, event handling, reload timing, or a DB invariant, fix the real cause.

---

## 2. Repeat-edit domain contract

The final behavior must be:

- Every save creates a new immutable candidate revision.
- Earlier candidates remain historical with `is_current=false`.
- Exactly one current candidate exists per region.
- Every correction event remains append-only.
- The editor always opens on the current displayed human text.
- The user can edit an unverified human correction again.
- The user can edit a verified revision again.
- Editing a verified revision withdraws current verification and creates a new unverified child.
- No edit overwrites an older revision in place.
- Confirm always applies only to the current candidate/revision.
- A stale browser request must still be rejected.

Do not cap the number of human correction revisions.

---

## 3. Required repeat-edit tests

Add backend PostgreSQL tests proving:

- r1 machine → correct → r2 human;
- r2 → correct → r3 human;
- r3 → correct → r4 human;
- revision numbers are monotonic;
- parent_id forms the correct chain;
- only r4 is current;
- all correction review events remain;
- confirming r4 verifies r4 only;
- stale r1/r2/r3 cannot be confirmed;
- editing an already verified revision deletes/withdraws the current verified row and creates the next unverified revision.

Add frontend component tests proving:

- after saving first correction, Edit remains available;
- second edit opens with the first corrected text preloaded;
- saving the second correction shows the new text;
- third edit is still possible;
- after Confirm, the action becomes Edit again;
- Edit again can itself be followed by another edit before reconfirmation.

Add Playwright E2E covering at least two consecutive corrections before confirm.

Use disposable fixture data only.

Do not alter the real pilot review data while testing.

---

# 4. Bilingual review UX contract

The Source V2 review UI must use a deliberate hybrid language policy.

## Domain labels and statuses: Sinhala + English

Use this pattern:

- `පෙළ (Text)`
- `රූපය පමණි (Visual only)`
- `රූපය + පෙළ (Visual + text)`
- `අලංකරණ (Decorative)`
- `තීරණය අවශ්‍යයි (Needs decision)`

Statuses:

- `තහවුරු කර ඇත (Verified)`
- `තහවුරු කර නොමැත (Not verified)`
- `භාවිතයෙන් ඉවත් කර ඇත (Excluded)`

Useful section labels may follow the same pattern, e.g.:

- `මුල් රූපය (Original image)`
- `රූපයේ ඇති පෙළ (Text in image)`
- `රූප විස්තරය (Visual description)`
- `තාක්ෂණික විස්තර (Technical details)`

Do not translate original source text.

## Action buttons: English only

All interactive actions on this review screen should use concise English labels.

Use consistent labels such as:

- Confirm
- Confirm visual
- Edit
- Edit again
- Save
- Cancel
- Exclude
- Change kind
- Apply
- Edit description
- Save description
- Locate

Do not use Sinhala-only action text such as:
- `පෙළ නිවැරදියි`
- `පෙළ නිවැරදි කරන්න`
- `වර්ගය වෙනස් කරන්න`

The domain meaning may still appear bilingually in nearby explanatory text/status chips.

## Explanatory/help text

Sinhala-first with English term in brackets where the term is important is acceptable.

Do not make every sentence bilingual if it creates clutter.

Goal:

teacher-friendly + developer/operator-unambiguous.

---

# 5. Decorative and source-kind controls

Preserve the recently fixed decorative behavior.

For decorative regions:

- no normal Confirm;
- primary action = Exclude;
- Change kind remains explicit;
- kind dropdown options use bilingual labels;
- Apply button is English;
- if reclassified to text_only, normal text actions appear;
- if reclassified to visual_only/visual_with_text, correct visual actions appear.

Do not regress the D18 guards.

---

# 6. Visual and description editors

Apply the same repeat-edit principle to source text inside visual regions.

If the source text of a `visual_with_text` region is corrected multiple times, every revision must work.

Visual descriptions are derived knowledge, not source verification. Their editor should also:

- reopen with the latest saved description;
- allow repeated edits;
- use English action buttons;
- preserve Sinhala content typed by the reviewer.

Do not merge source text and description semantics.

---

# 7. Preserve keyboard behavior

Keep the existing Space/Enter bubbling fix.

Verify:

- Space inserts spaces in text editor;
- Enter inserts newline;
- same for description editor;
- card keyboard activation still works when card itself is focused.

---

# 8. UI wording consistency sweep

Audit `apps/web/src/components/admin/source-v2-review.tsx` for the complete review-screen vocabulary.

Remove inconsistent mixtures such as:
- some actions Sinhala;
- some actions English;
- domain kinds Sinhala-only;
- technical labels English-only when a bilingual label is clearer.

Use one explicit language map.

Do not change unrelated Studio screens in this task unless they share the same component/labels.

---

# 9. Document the language policy

Add a short authoritative repository document or update the teacher/content-studio UX documentation with this rule:

> Source-review domain labels/statuses are bilingual Sinhala + English where the term matters. Action controls use concise English. Original source content is never translated.

Reference this rule from the Source V2 review implementation docs so future agents do not revert the screen to 100% Sinhala.

---

# 10. Chrome DevTools MCP validation

Use the real application after automated tests.

Do NOT change real review decisions.

Validate read-only where possible, and use disposable fixture data for mutating flows.

Real page checks:

- source-kind chips are bilingual;
- statuses are bilingual;
- action buttons are English;
- decorative controls remain correct;
- no source text translation;
- layout remains compact and readable.

Disposable fixture/browser checks:

- Edit → Save → Edit again → Save again → Edit again works;
- current text is always preloaded;
- revision increments correctly through API;
- Confirm verifies only final revision;
- reload preserves final text;
- Space and Enter work in editors;
- no console errors;
- no failed requests.

---

# 11. Validation

Run:

- focused Source V2 backend PostgreSQL tests;
- Source V2 frontend component tests;
- Source V2 Playwright E2E;
- Ruff;
- mypy/typecheck if configured;
- frontend TypeScript;
- frontend lint;
- frontend production build;
- OpenAPI/client reproducibility if contracts changed.

Do not reset the real DB.
Do not modify real human review decisions.
Do not build RAG in this task.

---

# 12. Commit

After PASS:

`fix(source-v2): support repeat edits and bilingual review UX`

Push master.

---

# 13. Final response

Return only:

SOURCE V2 REVIEW UX: PASS / BLOCKED

Final commit:
<hash>

Repeat-edit root cause:
<actual cause>

Revision-chain validation:
<result>

Bilingual labels/statuses:
<result>

English action buttons:
<result>

Backend tests:
<result>

Frontend tests:
<result>

Playwright:
<result>

Type/lint/build:
<result>

Chrome DevTools MCP:
<result>

Real human review state changed:
NO

Blocker:
<only if blocked>
