---
name: document-ingestion-ocr
description: Use for source upload, rendered-page visual understanding, native/OCR evidence, structured observations and educational interpretation, page/region verification, provenance, teacher review, trusted knowledge preparation, and ingestion quality in AI Exam Guru.
---

# Document Ingestion and OCR

## Goal

Convert education source documents into auditable structured content without losing the original source or pretending extraction is infallible.

## Pipeline

Read the document-understanding contract in `docs/SYSTEM_ARCHITECTURE.md` §4.8; its rollout status is not a quality or migration-completion claim.

1. Validate intake and preserve the checksum-bound immutable original.
2. Render bounded, integrity-checked pages/regions for visual inspection and understanding.
3. Retain native extraction, OCR, font/script diagnostics and geometry as attributed evidence/candidates; unsafe text layers cannot become authority.
4. Use a replaceable rendered-image understanding provider to propose structured source observations and separate educational interpretation.
5. Preserve exact Unicode, literal equations/values, blank/unreadable cells, visual groups, labels and source/region/reading-order lineage. NFC is a separate view; never invent source answers or correct the source's arithmetic.
6. Run independent deterministic/source-fidelity checks and explicit teacher verification where required. Model confidence/agreement is not trust.
7. Persist versioned decisions and TrustedPageKnowledge; keep independent human evaluation references separate.
8. Derive educational KnowledgeUnits and deterministic retrieval projections only from current verified knowledge and admitted scope.
9. Invalidate active downstream use when trusted source, scope or transformation lineage changes; retain immutable history.
10. Prove the fixed real-page and retrieval/generation comparisons before broad corpus backfill. Synthetic fixtures prove mechanics only.

## OCR selection

Do not hard-code Azure or any vendor. Keep a pluggable extractor/OCR interface. Benchmark candidate open-source engines on representative Sinhala Grade 5 pages before declaring one production-ready.

## Provenance

Every extracted segment must be traceable to:

- document id/checksum;
- document type/version;
- source page;
- extraction engine + version/configuration;
- review/correction history.

## Security

Treat uploads and extracted text as untrusted input. Test malformed PDFs, oversized files, duplicate uploads, unsupported content, path/name attacks, decompression/resource exhaustion where applicable, and prompt-injection text embedded in source documents.

## Tests/evals

Pair with `tdd-eval-engineering`.
Maintain fixtures for native PDFs, scanned pages, bad OCR, multi-column/layout cases, question numbering/options and Sinhala text. Synthetic fixtures may test pipeline mechanics but must not be used to claim real Sinhala OCR quality.
