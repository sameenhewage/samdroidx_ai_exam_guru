# AI Exam Guru — System Architecture

## Status
This is the **authoritative system architecture document** for AI Exam Guru.

It records the target deployment, data-boundary, storage, publishing, security and runtime architecture for the product as a whole. V1 phase documents may add implementation detail, but they must not silently contradict this document. If the architecture changes, update this file in the same change.

## 1. Architectural intent
AI Exam Guru is split into two deliberately different environments:

1. **Exam Guru Studio — private/local content and AI factory**
2. **Exam Guru Student — hosted/public student platform**

The core rule is:

> Raw educational source material and the AI/RAG content-production system stay local. Only human-approved, validated, versioned student-ready content is published to the hosted student platform.

This design is intentional for:
- low infrastructure cost while the product is bootstrapped and seeking investment;
- strong isolation of syllabus, teacher-guide, past-paper and RAG source material;
- use of existing 1–2 TB local machines instead of paid bulk cloud storage;
- lower production attack surface;
- predictable student-serving cost and latency;
- human-controlled publishing rather than live LLM generation for students.

## 2. Top-level architecture

```text
                         OUTBOUND ONLY WHERE REQUIRED
                    ┌──────────────────────────────────┐
                    │ OpenAI / approved AI providers  │
                    └────────────────▲─────────────────┘
                                     │
                                     │ HTTPS outbound
                                     │
┌────────────────────────────────────┴────────────────────────────────────┐
│                 EXAM GURU STUDIO — PRIVATE / LOCAL                      │
│                                                                          │
│  Docker Compose on an operator-owned PC/workstation                      │
│                                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌─────────────────────────────┐  │
│  │ Next.js      │   │ FastAPI      │   │ Workers / Valkey           │  │
│  │ teacher UI   │──▶│ domain/API   │──▶│ OCR, ingest, generation    │  │
│  └──────────────┘   └──────┬───────┘   └─────────────────────────────┘  │
│                            │                                             │
│            ┌───────────────┼────────────────┐                            │
│            │               │                │                            │
│            ▼               ▼                ▼                            │
│   Local filesystem   PostgreSQL+pgvector   Local audit/config            │
│   raw materials      metadata/chunks/RAG   generation/review data        │
│                                                                          │
│  NO PUBLIC INBOUND ACCESS TO RAW MATERIAL OR RAG DATA                     │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
                               │ explicit human-approved publish action
                               │ signed/versioned publish package
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    EXAM GURU STUDENT — HOSTED                            │
│                                                                          │
│  Student authentication / subscriptions                                  │
│  Published paper catalog                                                 │
│  Exam runner                                                              │
│  Server-side marking                                                      │
│  Student attempts / progress / analytics                                 │
│                                                                          │
│  Stores only student-runtime data and approved published content.         │
│  Does NOT require raw teacher guides, source PDFs, RAG corpus or vectors. │
└──────────────────────────────────────────────────────────────────────────┘
```

## 3. Exam Guru Studio — local/private content factory

### 3.1 Runtime model
The Studio is intended to run locally using versioned Docker images and Docker Compose.

Local services may include:
- Next.js teacher/content-operator UI;
- FastAPI backend;
- PostgreSQL + pgvector;
- Valkey;
- background workers;
- OCR/extraction tooling;
- provider-independent LLM and embedding adapters;
- OpenAI as the initial external model provider.

The local Studio is not intended to be a public internet service.

### 3.2 Network boundary
Default network posture:

```text
Internet -> Local Studio                  DENY
Local Studio -> approved external APIs    ALLOW outbound as required
Local browser -> localhost Studio         ALLOW
Local containers -> local data services   ALLOW
```

Do not expose PostgreSQL, Valkey, raw-material storage, OCR services or internal APIs through router port-forwarding or public ingress.

If LAN access is later required, it must be an explicit security decision rather than a default.

## 4. Local storage architecture

### 4.1 Raw material storage
Raw educational files are stored on the host machine's filesystem, outside ephemeral container filesystems.

Examples include:
- syllabuses;
- teacher guides;
- past papers;
- marking schemes;
- evaluation reports;
- scanned source pages;
- approved source images or student-facing source assets where needed.

A configurable storage root should be used, for example:

```text
/home/sameen/exam-guru-data/
```

Conceptual layout:

```text
exam-guru-data/
├── materials/
├── working/
├── temporary/
├── exports/
├── backups/
└── postgres-backups/
```

The teacher should not be required to manually manage this directory structure. The application owns storage keys and organization.

### 4.2 Docker persistence
Raw files must **not** live only inside a Docker container layer.

Use a host bind mount or durable host volume, conceptually:

```text
HOST:      /home/sameen/exam-guru-data/materials
CONTAINER: /data/materials
```

Rebuilding, replacing or upgrading Docker images must not delete source material.

### 4.3 Storage abstraction
The domain should depend on a storage interface rather than directly on MinIO or Amazon S3.

Conceptually:

```text
ObjectStorage
├── LocalFileStorage   <- default for the local Studio
├── S3Storage          <- optional future deployment
└── MinioStorage       <- optional S3-compatible deployment
```

Current target default:

```env
STORAGE_BACKEND=local
STORAGE_ROOT=/data
```

MinIO/S3 must not be required merely because the application runs in Docker.

### 4.4 What belongs in PostgreSQL
PostgreSQL stores structured application state, not bulk PDF blobs.

Examples:
- document identity and metadata;
- grade, subject, medium and curriculum relationships;
- document type/year/version;
- local storage key/path reference;
- SHA-256 checksum;
- extraction/OCR status;
- extracted and reviewed text blocks;
- provenance and review history;
- semantic chunks;
- embedding metadata and vectors;
- taxonomy and curriculum mappings;
- historical-question normalization;
- blueprint definitions;
- generation/validation/review records;
- approved/published paper source-of-truth;
- audit history.

Imported sources may retain bounded, immutable intake candidates before an authoritative curriculum assignment exists. Candidate grade/subject/medium labels are display-only and must be visibly marked as needing review. They never become retrieval scope authority. Explicit audited metadata confirmation requires an active curriculum assignment; extraction trust is a separate gate that also rejects unresolved OCR/font risks. Original-page images for review are rendered on demand through an authenticated, checksum-verified, resource-bounded endpoint, without serving the private source directory or weakening PDF sandbox controls.

## 5. Duplicate and source-integrity model
Every raw upload must be content-addressed/deduplicated using a cryptographic hash such as SHA-256.

```text
upload
  ↓
SHA-256
  ↓
already known?
  ├─ yes -> point user to existing material / reuse safely
  └─ no  -> persist one original file + metadata
```

Uploading the same PDF repeatedly must not create repeated physical copies.

The original source is immutable evidence. Metadata may be corrected through audited/versioned workflows.

## 6. Material lifecycle
A material may move through operator-friendly states such as:

```text
Processing -> Needs review -> Ready for AI -> Removed from use
```

`Removed from use` means the material is excluded from future retrieval/generation while preserving audit/provenance unless a separate safe-purge policy allows physical deletion.

Example: if a Grade 11 paper was mistakenly classified under Grade 5, the teacher must be able to:
- correct its grade/subject metadata where legitimate; or
- remove it from AI use.

A wrong classification must not silently contaminate RAG results.

## 7. Multi-grade educational model
The reusable architecture supports Sri Lankan **Grades 1–13** while V1 quality acceptance remains Grade 5 first.

Curriculum hierarchy:

```text
Grade
  -> Medium
      -> Subject
          -> Curriculum Version
              -> Unit / Module
                  -> Lesson / Topic
```

Educational taxonomy remains separately modeled:

```text
Competency -> Skill -> Sub-skill -> Learning Concept
```

Lessons/topics may map to one or more competency/skill nodes. Do not force both hierarchies to be identical.

National-exam programmes are assessment programmes associated with grade/curriculum scope, including:
- Grade 5 Scholarship;
- GCE O/L, normally Grade 11;
- GCE A/L, normally Grade 13;
- ordinary school-grade papers for Grades 1–13.

The paper-generation system must support both full-grade/full-subject generation and narrower scope such as:

```text
Grade 7 -> Mathematics -> Lessons 1–3 only
```

Hard grade/subject/curriculum/lesson filters must apply before semantic retrieval so content cannot leak across incompatible scopes.

## 8. RAG and AI data boundary
The private Studio owns the complete content-intelligence system:
- raw source documents;
- reviewed extraction/OCR data;
- curriculum knowledge base;
- historical question bank;
- chunks;
- embeddings;
- pgvector indexes;
- retrieval diagnostics;
- forecasting/backtesting data;
- blueprint logic;
- model prompts/configurations;
- generation attempts;
- validation evidence;
- reviewer decisions.

The hosted Student platform does **not** need this private corpus to serve an exam.

Students must never trigger live RAG + LLM generation as part of starting an exam in the normal product flow.

## 9. Teacher-first operator workflow
The primary Studio users are teachers, reviewers and education/content staff, not engineers.

Normal navigation should focus on goals:

```text
Home
Materials
Generate Papers
Review & Approve
Published Papers
```

Engineering internals such as vector IDs, request fingerprints, prompt versions, retry lineage, context IDs and raw JSON belong behind Advanced/Technical areas.

Typical content flow:

```text
Upload material
  ↓
Extract/OCR
  ↓
Teacher reviews/corrects source text where needed
  ↓
Ready for AI
  ↓
RAG / historical intelligence
  ↓
Generate paper + answer/marking scheme
  ↓
Automated validation
  ↓
Teacher/reviewer validation
  ↓
APPROVED
  ↓
Publish
```

## 10. Paper generation and validation
A generated paper is never treated as trusted merely because an LLM produced it.

Required chain:

```text
Scoped teacher request
  ↓
Deterministic/versioned blueprint
  ↓
Hard metadata filters + RAG
  ↓
Structured LLM generation
  ↓
Question + answer + marking scheme
  ↓
Automated validators/evals
  ↓
Human review/edit/reject/regenerate/approve
  ↓
Approved immutable paper version
```

The reviewer experience should show readable questions, options, correct answers, explanations, marks, source scope and validation findings. Engineering diagnostics are secondary.

## 11. Publishing boundary
### 11.1 Copy/publish, never move the source-of-truth
Approved content is **published/copied** from the local Studio to the hosted Student platform.

Do not move the only copy out of the Studio.

```text
LOCAL Studio source-of-truth
        │
        ├── keeps immutable approved version
        │
        └── exports/publishes copy
                    ↓
             Hosted Student platform
```

### 11.2 Publish package
The publication boundary should use an explicit, versioned package/API contract.

A package may include:
- stable paper ID;
- paper version;
- grade;
- subject;
- medium;
- curriculum/version reference;
- unit/module/lesson scope;
- exam programme where applicable;
- duration/instructions;
- questions and options;
- marks;
- server-side answers and marking rules;
- approved explanations shown only at the appropriate stage;
- student-facing assets required by the questions;
- publication schema version;
- checksum/content digest;
- approval/audit reference;
- created/published timestamps.

It must not include raw teacher guides, bulk source PDFs, the private RAG corpus or embedding database merely to serve the exam.

### 11.3 Hosted ingestion
The hosted side must validate before accepting a publication:
- schema/version compatibility;
- checksum/integrity;
- paper ID/version uniqueness;
- required metadata;
- asset references;
- publication authorization.

Publication should eventually be a controlled product action, e.g. `Publish to Student Platform`, rather than a teacher manually copying database dumps or SSH files.

## 12. Published-paper immutability and versioning
A published paper is immutable.

Do not edit production version `v3` in place. A correction creates `v4`.

```text
v3 -> remains historical/archived as required
v4 -> corrected -> validated -> approved -> published
```

Important versioned artifacts include:
- application/Docker image version;
- database schema/migration version;
- curriculum version;
- extraction/OCR implementation version;
- embedding provider/model/config version;
- retrieval version;
- forecasting/blueprint version;
- prompt version;
- LLM provider/model configuration;
- validation-rule version;
- paper version;
- publication-package schema version.

Reproducibility metadata belongs in the Studio even when the student does not see it.

## 13. Hosted Student platform
The hosted product is intentionally smaller and easier to scale than the Studio.

Responsibilities:
- student identity/login;
- subscription/entitlements;
- published-paper catalog;
- exam runner;
- timer/navigation/autosave/resume;
- answer submission;
- deterministic/server-side marking where possible;
- skill/progress analytics;
- recommendations based on approved application logic;
- student-facing reporting.

It should not require OpenAI/RAG availability to start or complete a normal published exam.

## 14. Answer security on the hosted platform
Do not send the full answer key or marking scheme to the browser when the exam starts.

```text
Browser during exam:
  questions/options        YES
  correct answers          NO
  private marking rules    NO

Hosted backend:
  correct answers          YES
  marking rules            YES
```

After submission, the product may reveal explanations/correct answers according to the configured exam policy.

## 15. Security model
### 15.1 Local Studio
Protect the local source-of-truth with:
- full-disk encryption where available;
- OS account permissions;
- no raw-data directory served by Next.js/static web hosting;
- no public inbound exposure by default;
- local firewall;
- secret management outside Git;
- prompt-injection handling for uploaded/retrieved text;
- audited admin/reviewer actions;
- least-privilege access to local files/database;
- checksums and provenance.

### 15.2 Security benefit of the split
A compromise of the public Student platform must not automatically provide access to:
- raw syllabus/teacher-guide library;
- past-paper source corpus;
- private OCR corpus;
- embedding/vector database;
- retrieval diagnostics;
- private generation prompts/configurations.

The public environment only holds data genuinely required to operate the student service.

## 16. Backup and disaster recovery
Local-only source ownership makes backup mandatory.

At minimum maintain:
- the active local data store on the primary Studio machine;
- a second physical encrypted copy on another disk/machine;
- repeatable PostgreSQL backups;
- configuration/version manifests needed to restore the Studio.

A sensible bootstrap pattern:

```text
Primary 1–2 TB workstation
        ↓ scheduled backup
External disk / secondary laptop / NAS
```

Do not scatter the active source library independently across several laptops without a single authoritative database/storage index. Prefer one primary Studio source-of-truth plus backups.

Temporary OCR/render/worker files should have cleanup/lifecycle rules and should not be treated as backups.

## 17. Cost model
The architecture deliberately avoids paying for bulk cloud storage before it adds business value.

Local resources provide:
- bulk PDF/source storage;
- PostgreSQL + pgvector;
- OCR and background processing;
- private RAG/AI production workflows.

Hosted resources are concentrated on the customer-facing workload:
- API/app hosting;
- authentication;
- subscriptions;
- published papers;
- student attempts/results/analytics;
- required student-facing assets.

This keeps early-stage infrastructure spend lower while preserving a clean future migration path.

## 18. Future evolution
The local-first choice must not trap the system permanently.

Because storage, LLM and embedding providers are behind interfaces, the Studio may later move to:
- a dedicated local server;
- a NAS-backed deployment;
- a private network/VPN environment;
- S3-compatible object storage;
- managed PostgreSQL;
- a secured cloud/VPC environment;

without changing curriculum/RAG/publishing domain semantics.

Do not introduce these costs/complexities until scale, availability, collaboration or investor-backed operations justify them.

## 19. Non-negotiable architecture invariants
1. Raw educational source material is private Studio data by default.
2. The Studio's active source-of-truth runs locally/private in the bootstrap architecture.
3. Raw PDFs are stored on durable host storage, not ephemeral container layers.
4. PostgreSQL stores structured metadata/content/vector state, not bulk source PDFs as blobs.
5. Same-file uploads are deduplicated by content identity.
6. Grade/subject/curriculum/lesson boundaries are enforced before RAG retrieval.
7. LLM output is untrusted until automated validation and human approval complete.
8. Only approved/versioned student-ready content crosses the Studio -> Student boundary.
9. Publishing copies content; it does not remove the Studio's source-of-truth.
10. Published paper versions are immutable.
11. The hosted Student platform does not need the private RAG corpus for normal exam serving.
12. Correct answers/marking rules remain server-side during active exams.
13. Production student service compromise must not directly expose the local raw material library.
14. Local data must have a tested backup/restore path.
15. V1 validates Grade 5 first, but reusable domain/storage/publishing architecture supports Grades 1–13.

## 20. Architecture change discipline
Any future change that alters one of the following must update this document in the same engineering change:
- local-vs-hosted data boundary;
- source storage provider/default;
- Studio public exposure/network model;
- RAG data location;
- publication contract;
- hosted answer-security model;
- published-paper immutability;
- backup/source-of-truth model;
- multi-grade core hierarchy.

Architecture decisions should not exist only in chat messages, prompts or implementation code.