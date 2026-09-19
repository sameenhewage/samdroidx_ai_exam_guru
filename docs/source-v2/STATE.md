# Source V2 — STATE

Resumable execution state only.
Specification: `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`
Locked decisions: `docs/source-v2/DECISIONS.md`

```
phase:          6 — Studio review + Chrome MCP acceptance
status:         review loop PASSES on real page 156; document-level acceptance pending
last_validated: be8e749
updated:        2026-09-19
```

## completed

- **Phase 1 — layout segmentation: PASS.** 8 fixed real pages. `92950a4`.
- **Phase 2 — contracts: DONE.** `page-layout.schema.json` enforced on write. `c1046a5`.
- **Phase 3 — reader benchmark: DONE.** Both Sinhala readers, 30 real crops.
  `docs/source-v2/BENCHMARK_READERS.md`. Tamil blocked, see `TAMIL_BLOCKER.md`.
- **Phase 4 — Machine Candidate: DONE.** Neutral alignment, no blending, no voting.
- **Phase 5 — persistence, human gate, HTTP: DONE.** `source_v2/{domain,models,service,repository,gate,schemas}.py`,
  migration `0056_source_v2`, six endpoints under `/admin/source-v2`.
  `require_verified_source` guards knowledge preparation.
- **Phase 6 — Studio review UI + Chrome MCP: review loop verified on real page 156.**
  `/admin/source-v2/{pageId}` shows the original render beside the Machine
  Candidate with Confirm / Correct / Exclude.

## verified in the real Studio (Chrome DevTools MCP, page 156)

- original page renders in the browser: 2480x3509 PNG, checksum-verified server-side
- Confirm -> `verified`, progress advances, button disables
- Correct -> new revision, state stays `unverified` (correcting is not verifying)
- Confirm on the corrected text -> `verified`
- Exclude with a reason -> `excluded`
- hard reload: all three states and the corrected text persist
- console clean apart from one pre-existing form-field-id advisory
- stale revision over HTTP returns 409; document gate returns `usable: false`
  naming the unresolved pages

## defect found by looking at the real UI, fixed, same page re-run

`p156-r001`/`r003` displayed **Myanmar script** (`အေသးစိတ်စံပြု 11`) for Sinhala
headings. `foreign_script_ratio` only counted Sinhala/Tamil/Latin, so an
unlisted script scored 0.0. Now every script block is counted and anything
outside the expected set is foreign; a witness over 50% foreign script is
rejected by the Machine Candidate. DeepSeek's real mean foreign-script on the
benchmark was 0.30, not the 0.033 first reported. After the fix the headings
read `ක්‍රියාකාරකම් 11` / `12`.

Re-importing used a new `--refresh` path: a re-run of the pipeline is a
**re-read**, so it supersedes with a new revision, keeps the old one linked as
parent, and withdraws any verification of changed text. Evidence is never
deleted. Revision history for `p156-r003` now reads: r1 Myanmar hallucination
(superseded), r2 corrected machine reading, r3 human correction (verified).

## blockers

- **Tamil**: no Tamil material anywhere in `RAG DATA/`. `docs/source-v2/TAMIL_BLOCKER.md`.
- **Document-level acceptance**: the gate needs *every* page of a document
  verified or excluded. 3 of 293 pages are imported and 2 of 6 regions on page
  156 are verified. Resolving a whole document is teacher work, not engineering.

## exact next step

Phase 6 completion, then 7 and 8 in order:

1. Import the rest of the benchmark pages (`uv run tools/source_factory/import_studio.py --pages 4,152,157,163,197`)
   and run the same Chrome MCP loop on page 186 to confirm the two-column page
   behaves identically.
2. Add a Playwright E2E for the review loop (web AGENTS.md requires browser
   evidence, not unit tests alone) covering confirm, correct-then-confirm,
   exclude and reload persistence.
3. Resolve one whole small document end to end so the gate flips to
   `usable: true`, and prove knowledge preparation then proceeds.
4. **Only then** Phase 7: delete the old source-reading architecture
   (`tesseract_ocr.py`, `source_reading_qwen.py`, `source_reading_openai.py`,
   `understanding_openai.py` and their env/config/tests), switch
   `LegacyPolicy` to `CUTOVER` and delete `PRE_CUTOVER`, using forward
   migrations only.
5. Phase 8: repo-wide audit proving one active source architecture.

Do not start step 4 before step 3 passes.

## running it

```
docker compose up -d                       # api :8000, web :3000, postgres :55432
uv run tools/source_factory/import_studio.py --pages 156,171,186 [--refresh]
# Studio: http://localhost:3000/admin/source-v2/<page_id>
#   use localhost, not 127.0.0.1 - the browser guard rejects the cross-site origin
```

Rebuild note: `migrate` and `worker` are **separate images** from `api`. After
changing a migration, `docker compose build migrate` too, or the stack fails
with "Can't locate revision".

## environment

- GPU: RTX 3060, 12 GiB. `.venv-sourcev2` (transformers 5.0.0, LightOnOCR) and
  `.venv-sourcev2-ds` (transformers 4.57.1, DeepSeek-OCR) cannot be merged.
- `apps/api` cannot run natively on Windows (`fcntl`, `resource`); use the container.
