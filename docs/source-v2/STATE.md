# Source V2 — STATE

Resumable execution state only.
Specification: `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`
Locked decisions: `docs/source-v2/DECISIONS.md`

```
phase:          5 — persistence + human verification
status:         IN_PROGRESS (domain + schema done; service/API wiring next)
last_validated: 6fd326e
updated:        2026-09-19
```

## completed

- **Phase 1 — layout segmentation: PASS.** `tools/source_factory/layout/`.
  8 fixed real pages segment correctly; all five acceptance criteria verified
  on annotated previews. `92950a4`.
- **Phase 2 — contracts: DONE.** `schemas/source-content/page-layout.schema.json`
  enforced on every write (schema, dense reading order, parent containment).
  `c1046a5`.
- **Phase 3 — reader benchmark: DONE.** Both Sinhala readers run on real crops.
  30 region crops, 0 failures each. Report: `docs/source-v2/BENCHMARK_READERS.md`.
  Selection recorded with its evidence in `candidate/selection.py`.
  Tamil: explicit real-data blocker, `docs/source-v2/TAMIL_BLOCKER.md`.
- **Phase 4 — Machine Candidate: DONE.** Neutral alignment ported; candidates
  built for real pages 4, 156, 171, 186, 197. 30 regions, 0 abstentions,
  9 regions with a critical-token conflict. 10 tests.

- **Phase 5 part 1 — human gate + schema: DONE.**
  `apps/api/src/exam_guru_api/source_v2/{domain,models}.py` and forward
  migration `0056_source_v2`. Applied to the real Studio database and proved
  by round-trip (`upgrade` → `downgrade -1` → `upgrade`).
  27 tests pass: 18 pure domain + 9 against real PostgreSQL.

## blockers

- **Tamil**: no Tamil material exists in `RAG DATA/` at all. Engineering is
  language-agnostic and ready; see `docs/source-v2/TAMIL_BLOCKER.md`.

## exact next step

Phase 5 part 2 — wire the on-disk candidates into the database and expose the
gate over HTTP.

1. `source_v2/service.py`: import `.exam-guru-data/.../candidates/page-NNN.json`
   plus the layout JSON into `source_v2_pages`, `source_v2_reader_candidates`
   and `source_v2_machine_candidates`, bound to document id, page number and
   the rendered image sha256. Idempotent on (document, page, image sha).
2. `api/routes/source_v2.py`: list a page's regions with the Machine Candidate
   and its disagreement, and expose Confirm / Correct / Exclude. Long reads are
   background jobs — a region read is 28–54 s, so nothing runs in a request.
3. Call `require_verified_source` at the knowledge/embedding/RAG/generation
   boundary and add the integration test that proves the bypass fails there too.

Then Phase 6 (Studio UI + continuous Chrome DevTools MCP on real pages),
Phase 7 (remove the old source architecture), Phase 8 (final audit).

## running the database tests

```
docker start ai-exam-guru-postgres-1
$env:EXAM_GURU_DATABASE_URL="postgresql+asyncpg://exam_guru:exam-guru-local-db@127.0.0.1:55432/exam_guru"
uv run alembic upgrade head                       # from apps/api
$env:EXAM_GURU_TEST_DSN="host=127.0.0.1 port=55432 dbname=exam_guru user=exam_guru password=exam-guru-local-db"
uv run --with "psycopg[binary]==3.2.10" pytest tests/source_v2 -q
```

The PostgreSQL tests skip themselves when `EXAM_GURU_TEST_DSN` is unset. They
are never replaced by fakes: a fake cannot prove a trigger fires.

## environment

- GPU: RTX 3060, 12 GiB, driver 616.92.
- `.venv-sourcev2` — torch 2.6.0+cu124, **transformers 5.0.0** (LightOnOCR-2
  needs the explicit `LightOnOcr*` classes, absent before 5.x).
- `.venv-sourcev2-ds` — torch 2.6.0+cu124, **transformers 4.57.1** (DeepSeek-OCR
  remote code needs `DeepseekV2Model`, gone in 5.x; 4.46 lacks `DeepseekV2MoE`).
  The two readers cannot share one environment.

## commands

```
uv run tools/source_factory/layout/cli.py benchmark
uv run tools/source_factory/layout/tests/test_detect.py
uv run tools/source_factory/readers/cli.py crops --pages 156,186,171,4,197
.venv-sourcev2/Scripts/python.exe    tools/source_factory/readers/bench.py --reader sinhala-lightonocr
.venv-sourcev2-ds/Scripts/python.exe tools/source_factory/readers/bench.py --reader sinhala-deepseek
uv run tools/source_factory/readers/cli.py rescore     # re-measure without re-running models
uv run tools/source_factory/readers/report.py          # regenerate BENCHMARK_READERS.md
uv run tools/source_factory/candidate/cli.py build
uv run tools/source_factory/candidate/cli.py show --page 156
uv run tools/source_factory/candidate/tests/test_machine.py
```

## notes

- Reading a region crop is 28 s (LightOnOCR) to 54 s (DeepSeek); a full page of
  regions is minutes. Phase 5 must run reads as background jobs, never in a
  request.
- DeepSeek ignores `max_new_tokens` passed to `bench.py`; it has run for 700 s
  on one region. A generation cap belongs in the reader before production use.
- Old source-reading surface to delete in Phase 7: ~50 modules under
  `apps/api/src/exam_guru_api/documents/`, including `tesseract_ocr.py`,
  `source_reading_qwen.py`, `source_reading_openai.py`, `understanding_openai.py`.
