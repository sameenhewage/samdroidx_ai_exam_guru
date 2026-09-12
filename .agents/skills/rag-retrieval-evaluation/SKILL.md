---
name: rag-retrieval-evaluation
description: Use for curriculum knowledge base, embeddings, pgvector, hybrid retrieval, metadata filtering, reranking, context building, provenance, retrieval quality metrics, and RAG regression evaluation in AI Exam Guru.
---

# RAG Retrieval and Evaluation

## Principle

RAG is a first-party retrieval system, not an LLM prompt trick. Retrieval must be independently measurable before generation quality is judged.

## Data model

Under the architecture correction in `docs/SYSTEM_ARCHITECTURE.md` §4.8, new RAG knowledge derives only from current TrustedPageKnowledge. KnowledgeUnits retain exact observed evidence separately from accepted educational meaning; deterministic, versioned projections feed the existing lexical/vector infrastructure. Raw/native/OCR/visual candidates and free-form model summaries are not eligible sources. Preserve legacy history without automatically rebinding it.

Each retrievable unit/projection should carry enough metadata to prevent cross-scope leakage, including relevant fields such as:

- grade/exam;
- medium;
- document type/version;
- paper/year;
- competency/skill/sub-skill;
- source document/page/regions and original/rendered-image checksums;
- trusted-page, knowledge-unit and projection revisions;
- approved subject/curriculum/unit/lesson scope;
- embedding model/version.

## Retrieval flow

1. Parse request/blueprint into explicit retrieval constraints.
2. Apply hard metadata filters first where semantics require them.
3. Run lexical/full-text retrieval and vector similarity retrieval.
4. Fuse/rank results deterministically and add reranking only when measured benefit justifies it.
5. Deduplicate near-identical source segments.
6. Build a bounded context with source ids/page provenance.
7. Keep retrieved source text clearly separated from trusted system instructions.
8. Pass structured source-evidence/accepted-meaning bundles to generation only after scope, verification and current-lineage checks; a retrieval projection is not itself source truth.
9. Recheck freshness through indexing, generation, validation replay, approval and new publication. Changed trusted knowledge or scope makes stale projections/vectors ineligible without deleting historical evidence.

## Evaluation-first rules

Maintain fixed retrieval eval datasets with:

- query/blueprint input;
- required/acceptable source ids;
- forbidden cross-grade/cross-medium sources;
- expected competency/skill coverage.

Track metrics appropriate to the dataset, such as recall@k, precision@k, MRR/nDCG, source coverage and leakage rate. Choose thresholds based on baseline data rather than arbitrary perfection claims.

## Prompt-injection resistance

Source documents are untrusted data. Never treat text inside retrieved content as instructions to the model. Add adversarial fixtures containing prompt injection, irrelevant instructions and malicious metadata.

## Versioning

Persist retrieval configuration with generation records:

- embedding model/version;
- chunking version;
- lexical/vector weights;
- filters;
- reranker/version if any;
- top-k/context limits.

## Change rule

Any change to chunking, embeddings, filters, ranking or context-building requires the fixed retrieval eval suite to run before and after. Do not accept a change that improves one example while materially regressing the broader suite without documented trade-off.

Always pair with `tdd-eval-engineering`.
