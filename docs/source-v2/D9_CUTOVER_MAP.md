# D9 — legacy V1 source architecture: call-graph map before removal

Mapped from the real entry points on 2026-09-19 at `c453f16`. Nothing has been
deleted yet. This exists because `tesseract_ocr.py` cannot simply be deleted:
one of its exports is a rendering primitive the surviving system still uses,
and the removal has to happen in dependency order or the build breaks.

## Entry points that reach the legacy runtime

```
main.py         L17  ExtractionDispatcher, create_extraction_dispatcher   (documents.jobs)
                L18  documents.page_reading_jobs
worker.py       L23  extract_document, recover_extraction_jobs
                L24  read_source, recover_source_read_jobs
api/router.py        source_fidelity_router, page_images_router
```

**Upload completion does dispatch V1 reading.** `resumable_uploads.py` imports
`queue_source_read` from `page_reading_jobs`, and `upload_jobs.py` imports from
the same module. So finishing an upload starts a legacy read. That path must be
cut first, otherwise removing the actors leaves a dangling dispatch.

## Worker actors to unregister (`worker.py` `_register_actors`)

Legacy source reading — remove:

```
extract_document              recover_extraction_jobs
read_source                   recover_source_read_jobs
understand_source_page        recover_understanding_page_jobs
```

Keep — these are not source reading:

```
finalize_source_upload        recover_source_upload_jobs
generate_question             recover_generation_jobs
ingest_embeddings             recover_embedding_jobs
prepare_knowledge_page        recover_material_knowledge
recover_material_knowledge_indexing
reconcile_source_objects
advance_teacher_paper         recover_teacher_papers
```

`finalize_source_upload` stays, but its body must stop queueing a source read.

## Modules and their dependents

| module | lines | imported by |
|---|---|---|
| `tesseract_ocr.py` | 1199 | `jobs.py`, `page_images.py`, `page_reading.py` |
| `page_reading.py` | 949 | `page_reading_jobs.py` |
| `page_reading_jobs.py` | 918 | `resumable_uploads.py`, `upload_jobs.py`, `main.py`, `worker.py` |
| `extraction_service.py` | 1188 | `jobs.py` |
| `jobs.py` | 176 | `main.py`, `worker.py`, `api/dependencies.py` |
| `understanding_jobs.py` | 984 | `worker.py` |
| `source_consensus.py` | 472 | 7 modules — see below |

`source_consensus.py` dependents: `source_consensus_provider.py`,
`source_machine_service.py`, `source_machine.py`, `source_reading_openai.py`,
`source_reading_qwen.py`, `source_renders.py`, `understanding_jobs.py`.

Roughly **5 900 lines** before counting routes, config, schemas and tests.

## The one thing that must NOT be deleted with it

`page_images.py` imports `RenderedPageImage` and `open_pdf_file` from
`tesseract_ocr.py`. Those are **deterministic PDF rendering primitives**, not
OCR, and D9 explicitly preserves them. Before `tesseract_ocr.py` is deleted
they must be moved to a neutral module — `documents/pdf_render.py` — and
`page_images.py` repointed. Deleting first and fixing after would break page
image serving, which Source V2's Studio depends on.

## Safe removal order

1. Cut the dispatch: `resumable_uploads.py` and `upload_jobs.py` stop importing
   `page_reading_jobs`; `finalize_source_upload` no longer queues a read.
2. Move `RenderedPageImage` / `open_pdf_file` out of `tesseract_ocr.py` into
   `documents/pdf_render.py`; repoint `page_images.py`.
3. Unregister the six legacy actors in `worker.py`; drop the `main.py` wiring
   and `ExtractionDispatcher` from `api/dependencies.py`.
4. Remove `source_fidelity` routes from `api/router.py`.
5. Delete, leaves first: `page_reading_jobs.py`, `page_reading.py`,
   `understanding_jobs.py`, `page_understanding*`, `source_reading_openai.py`,
   `source_reading_qwen.py`, `source_machine*.py`, `source_consensus*.py`,
   `extraction_service.py`, `jobs.py`, `tesseract_ocr.py`.
6. Remove their settings/env keys from `config.py` (~24 references) and any
   Tesseract install from the API Dockerfile.
7. Delete their tests. Keep `tests/source_v2/**` untouched.

## What survives

Source V2 in full; immutable upload and object storage; deterministic render,
layout and checksum primitives; historical migrations and all existing
evidence rows; and every downstream OpenAI use — generation, embeddings and
semantic verification — which is not source reading and is not in scope.

## Verification required after removal

Cold rebuild of api, migrate and worker; the legacy routes absent from
`/openapi.json`; the six actors absent from worker registration; completing an
upload dispatching no read; backend and frontend suites; Chrome DevTools MCP
over pages 156, 186 and all three sankhya-rata pages; both document gates still
`usable: true`; and a repo-wide grep proving no active Tesseract, Qwen or
OpenAI *source-reader* path remains.
