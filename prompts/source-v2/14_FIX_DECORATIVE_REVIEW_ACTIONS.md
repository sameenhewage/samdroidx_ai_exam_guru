# Source V2 — Fix Decorative Region Review Actions and Error Guidance

## Bug observed by the human reviewer

Route:
`/admin/source-v2/[pageId]`

Real pilot example:
- teacher guide page 186
- regions `p186-r000` and `p186-r007`
- both currently classified `decorative`

The UI currently shows the ordinary green text-confirm button for a decorative region. Clicking it sends:

`POST .../confirm`

The backend correctly refuses it with HTTP 422, e.g.:

`region p186-r007 is decorative; use confirm-visual or reclassify it first`

This is a UX/domain mismatch.

A `decorative` region is explicitly non-educational and must never become Verified Source Content. Therefore the ordinary text-confirm button must not be offered for a decorative region.

The backend error is also misleading because `confirm-visual` only accepts `visual_only` or `visual_with_text`; a decorative region cannot be sent directly to confirm-visual without reclassification.

## Correct domain behavior

For a current region whose `source_kind === "decorative"`:

### If the reviewer agrees it is page furniture / decorative

The intended action is:

`Exclude / Do not use this region`

This resolves the region without creating Verified Source Content.

Examples:
- running header
- folio/page number
- purely decorative rule/artwork

### If the reviewer disagrees and it is educational content

The reviewer must first reclassify it, then use the action appropriate to the new kind:

- `text_only` → confirm/correct text
- `visual_only` → confirm visual
- `visual_with_text` → confirm visual + printed text
- `undecided` → remains unresolved

Do not silently reclassify.

## Required frontend fix

Primary file:

`apps/web/src/components/admin/source-v2-review.tsx`

1. Add explicit handling for:
   `region.source_kind === "decorative"`

2. Do not render or enable the normal `confirm` button for decorative regions.

3. Make the primary resolution action for decorative content clearly:
   `Do not use this region` / the existing localized Exclude action.

4. Add a clear reviewer option:
   `Change kind`
   that allows a human to reclassify decorative content when the machine proposal is wrong.

5. Reclassification must be explicit and must use the existing `/reclassify` API.

6. The UI should support all meaningful destination kinds:
   - text_only
   - visual_only
   - visual_with_text
   - decorative
   - undecided where appropriate

A small accessible select/popover/button group is acceptable. Avoid overengineering.

7. After reclassification, refresh the region and show only actions valid for the new kind.

8. Keep existing human review state and real pilot data intact. Do not auto-click or auto-resolve any real region.

## Required backend error correction

Primary file:

`apps/api/src/exam_guru_api/source_v2/repository.py`

The current ordinary text-confirm guard treats `visual_only` and `decorative` together and says:

`use confirm-visual or reclassify it first`

Split the error semantics.

For `visual_only`:
- message may direct to confirm-visual or reclassify.

For `decorative`:
- message must say that decorative content cannot be verified as source;
- direct the reviewer to exclude it or reclassify it first.

Do not weaken the backend guard.

Decorative content must remain impossible to insert into `source_v2_verified_regions`.

## Regression tests

Update/add frontend component tests proving:

1. decorative region does not show an enabled normal confirm button;
2. decorative region exposes Exclude as the normal resolution;
3. decorative region exposes an explicit Change kind/reclassify control;
4. reclassifying decorative → text_only calls `/reclassify` with the correct candidate/revision/kind;
5. after the returned state is text_only, normal text review actions are available;
6. reclassifying decorative → visual_only exposes confirm-visual;
7. no reclassification occurs without explicit reviewer action.

Update Source V2 API/domain tests proving:

8. `/confirm` on decorative returns 422 with accurate guidance;
9. `/confirm-visual` cannot accept decorative;
10. decorative can never create a verified-source row;
11. exclude resolves decorative content;
12. reclassify creates append-only review lineage and leaves it unverified until the reviewer performs the next valid action.

Use synthetic/disposable fixtures for automated tests.

Do not mutate the real human-reviewed pilot as part of automated testing.

## Chrome DevTools MCP validation

Validate on the real page 186 UI without making a review decision for the user:

- inspect `p186-r000`;
- inspect `p186-r007`;
- verify ordinary green text-confirm is not offered for decorative;
- verify Exclude is present;
- verify Change kind is present;
- do not click Exclude/reclassify/confirm on the real records;
- no console errors;
- no failed network requests caused by rendering/actions.

## Full validation

Run:

- focused Source V2 frontend tests;
- Source V2 backend tests;
- relevant Playwright E2E;
- TypeScript;
- frontend lint;
- Ruff;
- backend typecheck if configured;
- production web build.

## Scope

Focused UX/domain bugfix only.

Do not:
- reset DB;
- change existing real review decisions;
- re-read PDFs;
- regenerate transcripts;
- build RAG;
- alter verified human text.

## Commit

After PASS:

`fix(source-v2): make decorative review actions explicit`

Push master.

## Final response

Return only:

SOURCE V2 DECORATIVE REVIEW FIX: PASS / BLOCKED

Final commit:
<hash>

Frontend behavior:
<summary>

Backend guidance:
<summary>

Tests:
<result>

Chrome DevTools MCP:
<result>

Real human review state changed:
NO

Blocker:
<only if blocked>
