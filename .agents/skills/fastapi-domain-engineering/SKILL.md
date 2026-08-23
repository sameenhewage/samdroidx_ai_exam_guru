---
name: fastapi-domain-engineering
description: Use for Python/FastAPI backend, Pydantic contracts, SQLAlchemy/Alembic persistence, REST/OpenAPI APIs, background jobs, provider abstractions, and modular-monolith domain design in AI Exam Guru.
---

# FastAPI Domain Engineering

## Architecture
Build a modular monolith. Domain/application logic must not live in route handlers.

Preferred dependency direction:
`API -> application/use-cases -> domain -> ports/interfaces -> infrastructure adapters`

Keep modules cohesive around V1 capabilities such as curriculum, documents, question bank, papers, review/publishing, analytics and AI orchestration.

## API rules
- REST + OpenAPI is the V1 contract.
- Use typed Pydantic request/response models.
- Generate the TypeScript client from OpenAPI; do not hand-maintain duplicate frontend DTOs.
- Use explicit status codes and stable machine-readable error shapes.
- Enforce authorization in application/domain boundaries, not only UI.
- Long-running OCR/generation/eval work returns a job resource; never hold request threads for the whole job.

## Persistence
- PostgreSQL is source of truth.
- Use SQLAlchemy 2 and Alembic.
- All schema changes require migrations and clean-database migration tests.
- Protect data with DB constraints where practical: uniqueness, foreign keys, state constraints and transactional invariants.
- Design jobs for idempotency, retry safety and deduplication.
- Preserve immutable provenance and published-paper versions.

## Async boundaries
Use async where it gives real I/O concurrency, but do not make CPU-heavy/OCR/model work execute in the API process. Route heavy work through background workers/queue infrastructure.

## Domain rules
- Deterministic exam rules belong in code.
- LLM/provider SDK types must not leak into domain models.
- Provider adapters implement small first-party ports/interfaces.
- RAG, forecasting, blueprinting and validation remain first-party domain/application logic.

## Testing
Always pair with `tdd-eval-engineering`. Use real PostgreSQL + pgvector and Valkey containers for integration behavior that depends on them.
