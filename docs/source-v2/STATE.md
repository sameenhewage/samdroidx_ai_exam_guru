# Source V2 — STATE

Resumable execution state only.
Specification: `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`
Locked decisions: `docs/source-v2/DECISIONS.md`

```
phase:          7 — legacy cutover
status:         MECHANISM ACCEPTED. Cutover blocked on corpus migration, not engineering.
last_validated: 82389a1
updated:        2026-09-19
```

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

1. **Migrate the corpus, document by document**, newest/most-needed first:
   ```
   scripts/source_pipeline/render_pdf.py <folder>
   uv run tools/source_factory/layout/cli.py --document <folder> detect
   uv run tools/source_factory/readers/cli.py --document <folder> crops
   .venv-sourcev2-ds/Scripts/python.exe tools/source_factory/readers/bench.py --document <folder> --reader sinhala-deepseek
   .venv-sourcev2/Scripts/python.exe    tools/source_factory/readers/bench.py --document <folder> --reader sinhala-lightonocr
   uv run tools/source_factory/candidate/cli.py --document <folder> build
   uv run tools/source_factory/publish_to_studio.py --document <folder> [--document-id <uuid>] [--refresh]
   ```
   Then review each page in the Studio until its gate is `usable: true`.
   Budget: reading is 12 s/region (LightOnOCR) to 44 s/region (DeepSeek), so a
   293-page guide is roughly 20 GPU-hours plus the human review. This needs a
   queue, not a session.
2. **Then** flip `LegacyPolicy` to `CUTOVER` and delete `PRE_CUTOVER`
   (`apps/api/src/exam_guru_api/source_v2/gate.py`). `tests/source_v2/test_gate.py`
   asserts both members exist, so it will fail and tell you to finish the job.
3. **Then** remove the V1 source path, in this order so nothing is orphaned:
   - providers first: `source_reading_openai.py` (banned outright by D1),
     `source_reading_qwen.py`, `tesseract_ocr.py`, `understanding_openai.py`
   - then their config keys in `core/config.py` and `compose.yaml`
   - then `source_reading.py`, `ocr.py`, `page_reading.py`,
     `source_consensus_provider.py`, `understanding_*`, `fidelity_*`
   - then the 50 test files, and the routes `source_fidelity.py`,
     `source_evaluation.py`, `understanding.py`
   Use forward migrations only; never delete a historical migration.
4. **Then** the repo audit: no import of a V1 source module remains, the docs
   describe one architecture, and `docs/SYSTEM_ARCHITECTURE.md` §4.8 points at
   Source V2.

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
