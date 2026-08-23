# Local Grade 5 Source Data — Operator Dataset Contract

## Purpose
Real Grade 5 educational source PDFs are available locally for development/evaluation but are intentionally excluded from Git. They are used to validate document ingestion, extraction/OCR, structured knowledge creation, RAG retrieval, historical analysis and grounded generation without redistributing raw source material through the repository.

## Local path
Current operator path:

`/home/sameen/SAMDROIDX-PROJECTS/samdroidx_ai_exam_guru/RAG DATA/Grade 5`

Reported folders include:
- `grade 5 -teachers guide book - sinhala medium`
- `grade 5 English`
- `grade 5 Maths`
- `grade 5 Parisaraya`
- `grade 5 Sinhala`

The exact contents must be inspected locally by the engineering agent. Folder names alone are not evidence that a file is official, current, scanned, native-text, or legally redistributable.

## Git/privacy/copyright rule
- Keep `RAG DATA` and all raw operator-provided files out of Git.
- Do not copy bulk extracted source text into repository fixtures/docs.
- Do not commit API keys or local absolute secret paths.
- It is acceptable to commit non-sensitive/non-copyrightable hashes, counts, aggregate quality metrics and synthetic test fixtures.
- Local benchmark/adjudication artifacts containing source text should remain in a gitignored local evidence directory.

## Required local inventory
For each source file, record locally when determinable:
- SHA-256 content identity;
- filename/path;
- page count;
- native-text, scanned/image-only, or mixed;
- language/medium;
- subject;
- document type;
- year/curriculum version evidenced by the document;
- extraction/OCR suitability;
- review/trust status.

## V1 scope use
Grade 5 Scholarship Sinhala-medium content is the primary V1 acceptance dataset. English subject material may be used to prove multilingual architecture boundaries and avoid Sinhala-specific hard-coding, but it does not silently expand the approved V1 product scope.

## OCR evidence
- Use native PDF extraction first when reliable embedded text exists.
- For scanned/mixed pages, benchmark configured open-source OCR providers on representative pages.
- Native text may act as reference truth for OCR of a rendered version of the same page only after correspondence is verified.
- Scanned-only pages require human-adjudicated truth before claiming human-validated OCR quality.
- Lack of human adjudication keeps that criterion open but must not halt all later Priority 1 engineering.

## Non-waterfall rule
P0-P15 are acceptance/status gates, not a waterfall implementation schedule. A blocked human-quality criterion in an earlier gate must not stop later non-blocked implementation when dependencies can be satisfied with reviewed native content or deterministic fixtures. P10 remains the aggregate Priority 1 closure gate, and P15 remains full V1 closure.

## Knowledge/RAG use
Only reviewed/trusted extraction should become authoritative knowledge. The system should preserve:
- source document and page/block provenance;
- document/medium/subject/curriculum metadata;
- extraction provider/version and quality evidence;
- chunk type and educational boundary;
- embedding provider/model/dimension/config/version;
- review history.

The raw PDFs are source material; PostgreSQL/pgvector plus reviewed structured records form the product knowledge layer.
