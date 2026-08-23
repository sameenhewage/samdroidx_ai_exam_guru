---
name: document-ingestion-ocr
description: Use for syllabus, teacher-guide, past-paper and marking-scheme upload, PDF parsing, OCR, layout extraction, provenance, extraction review, chunk preparation, and ingestion quality in AI Exam Guru.
---

# Document Ingestion and OCR

## Goal
Convert education source documents into auditable structured content without losing the original source or pretending extraction is infallible.

## Pipeline
1. Accept and validate upload metadata and file type/size.
2. Persist original file with checksum and immutable source identity.
3. Detect whether the PDF has usable native text.
4. Prefer native extraction (for example PyMuPDF) when quality is sufficient.
5. For scans/poor text, route through the selected open-source OCR adapter.
6. Preserve page numbers, bounding/layout references when available, and extraction-engine/version metadata.
7. Normalize text conservatively; never silently rewrite educational meaning.
8. Produce reviewable extraction records before knowledge-base publication.
9. Require human correction/review where confidence or structure is uncertain.
10. Chunk by educational/document structure, not blind fixed-size slicing alone.

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
