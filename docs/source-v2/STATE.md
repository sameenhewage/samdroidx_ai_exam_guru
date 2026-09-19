# Source V2 — STATE

Resumable execution state only.
Specification: `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`
Locked decisions: `docs/source-v2/DECISIONS.md`

```
phase:          6b — primary-agent-first reader order (D14)
status:         Astra/DeepSeek-first REVERSED. Primary-first proven on pages 156 and 186.
last_validated: b38c7c5
updated:        2026-09-19
```

## READER ORDER IS LOCKED (D14) — read this before touching the pipeline

```
1 render/layout  ->  2 PRIMARY VISUAL READING BY THE EXECUTING AGENT
                 ->  3 local specialist readers on the SAME crops
                 ->  4 comparison  ->  5 Machine Candidate
                 ->  6 human Confirm/Correct  ->  7 Verified Source Content
```

The executing agent reads the original pixels and writes
`<document>/primary/pages/page-NNN.json`. DeepSeek and LightOnOCR are
**secondary witnesses**. They may not create the initial candidate and may not
replace the primary reading. `candidate/cli.py` now **fails** if the primary
JSON is missing, and `test_machine.py` locks the behaviour. There is no "Astra"
provider and none is to be built.

## acceptance: the mechanism passes

- **Phase 1 layout** — 8 fixed real pages. `92950a4`
- **Phase 2 contracts** — schema enforced on every write. `c1046a5`
- **Phase 3 readers** — both Sinhala readers measured on 30 real crops.
  `docs/source-v2/BENCHMARK_READERS.md`
- **Phase 4 Machine Candidate** — no blending, no voting, abstains when no
  witness is trustworthy.
- **Phase 5 persistence + human gate** — migration `0056`, seven endpoints,
  `require_verified_source` at the knowledge boundary.
- **Phase 6 Studio UI + browser acceptance** — `/admin/source-v2/{pageId}`.
  - Playwright: 3 tests green in the isolated runtime
    (`bash scripts/run_isolated_e2e.sh apps/web/e2e/source-v2-review.spec.ts`)
  - Chrome DevTools MCP on real pages 156, 186 and all of `sankhya-rata`
- **A whole real document is resolved.** `sankhya-rata` — a 3-page Grade 5
  Sinhala maths activity sheet — went through render, layout, both readers,
  Machine Candidate, API publish, and region-by-region human review against the
  original pages. 17 regions: **16 verified, 1 figure excluded, 0 undecided.**
  `GET /admin/source-v2/documents/{id}/gate` returns **`usable: true`**.
  31 review events: 16 confirm, 14 correct, 1 exclude. Every verified row cites
  the page render it was compared against.

## why the cutover has NOT started

The mechanism is proven; the corpus is not migrated. Measured today:

| | |
|---|---|
| documents fully resolved in V2 | **1** (3 pages, 17 regions) |
| pages of the teacher guide in V2 | 5 of 293 |
| modules in `apps/api/src/exam_guru_api/documents/` | 51 |
| imports of the V1 source path across the API | ~43 |
| test files touching the V1 source path | 50 |

Deleting the V1 source path today would leave every other material — the
293-page teacher guide and all Grade 3/4/5 content — with no working read path
and no way back. Decision D13 says one architecture ships; it does not say
delete the only working one before its replacement has read the corpus.
**Acceptance of the mechanism is not migration of the corpus.**

## exact next step

1. **Re-read the sankhya-rata document primary-first.** Its 17 regions were
   verified against DeepSeek-first candidates, so its gate reads `usable: true`
   on the wrong basis. Seal primary readings for its 3 pages, rebuild, publish
   with `--refresh`, and re-review whatever text changes.
   That document''s `usable: true` does not count until this is done.
2. Finish reviewing pages 156 and 186 (2 of 16 regions verified so far).
3. Then the corpus migration, then the cutover, then the audit — unchanged and
   still blocked on GPU + review time, not on engineering.

## what changed in the reader order

- `tools/source_factory/primary/` — schema, fidelity validation and the `seal`
  CLI. `schemas/source-content/primary-reading.schema.json`.
- `candidate/machine.py` — `build(primary=..., witnesses=[...])`. Candidate
  text is always the primary reading. Rank no longer selects text.
- `candidate/cli.py` — hard failure without a primary reading; uncropped
  regions (figures, headers, folios) now carry the agent''s own reading.
- Broad disagreement (< 50% agreement) forces review even with no critical
  token, because a flat contradiction is exactly what a human should see.

## proven on real pages 156 and 186

| | |
|---|---|
| regions read by the agent | 16 (8 per page, including the header/footer bars the old flow never saw) |
| primary characters | 2278 (p156) + 1207 (p186) |
| regions with recorded uncertainty | 14 |
| candidates superseded to a new revision | 6 |
| verifications withdrawn | 1 |

The withdrawal is the point: `p156-r001` had been verified as
`ක්‍රියාකාරකම් 11`. The page prints `ක්‍රියාකාරකම 11` with **no hal kirima** —
a DeepSeek artefact I had confirmed. Reading the pixels first caught it and the
supersession withdrew the bad verification automatically.

Fidelity held through the Studio and a reload: `(Bar magnet )` keeps its space
before the bracket, `Horse shoe mag-` keeps its line-break hyphen,
`(20 cm x 6 cm)` keeps the lowercase Latin x, `කෝටු කැබලි  දෙකක්` keeps its
double space, and `3/4 කින්` versus `3 /4 ක්` keep their different spacing.
The printed folios (141, 171) are recorded beside the PDF page numbers
(156, 186) rather than reconciled.
## blockers

- **Corpus migration** — see above. Engineering is ready; this is GPU time and
  teacher review time.
- **Tamil** — no Tamil material exists anywhere in `RAG DATA/`. The port,
  metrics and selection are language-agnostic and waiting.
  `docs/source-v2/TAMIL_BLOCKER.md`. This does **not** block the Sinhala
  cutover.

## defects found by running it, all fixed

| found by | defect |
|---|---|
| looking at the Studio | DeepSeek rendered Sinhala headings in **Myanmar** script; `foreign_script_ratio` only knew Sinhala/Tamil/Latin so it scored 0.0 |
| reviewing a real page | DeepSeek answered Sinhala blocks in fluent **English**; Latin is tolerated, so nothing flagged it. Now rejected when another reader found the page's own script |
| resolving a document | figures/tables were never cropped, so they never reached the reviewer and a page could look resolved with a figure undecided |
| re-running the pipeline | `--refresh` superseded existing regions but silently dropped newly detected ones |
| bringing the stack up | `migrate` and `worker` build from **separate images** from `api` |
| first UI load | crop-id to region-id mapping produced `p156-rr001`, so no reader evidence joined |
| first UI load | the browser guard rejects `127.0.0.1` as cross-site; use `localhost` |

## running it

```
docker compose up -d                  # api :8000, web :3000, postgres :55432
# Studio: http://localhost:3000/admin/source-v2/<page_id>
uv run tools/source_factory/publish_to_studio.py --document <folder>
bash scripts/run_isolated_e2e.sh apps/web/e2e/source-v2-review.spec.ts
cd apps/api; $env:EXAM_GURU_TEST_DSN="host=127.0.0.1 port=55432 dbname=exam_guru user=exam_guru password=exam-guru-local-db"
uv run --with "psycopg[binary]==3.2.10" pytest tests/source_v2 -q
```

`apps/api` cannot run natively on Windows (`fcntl`, `resource`); use the
container. The two reader virtualenvs need incompatible transformers versions
and cannot be merged.
