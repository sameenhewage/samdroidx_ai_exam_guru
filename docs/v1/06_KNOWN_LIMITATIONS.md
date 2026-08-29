# V1 Known Limitations

This document records limitations that remain after the implemented Priority 1 engineering controls. It is evidence for the P10 known-limitations criterion; it does not waive any incomplete acceptance gate or unlock Priority 2.

## Release-blocking evidence gaps

### Sinhala OCR quality

- The native-first extraction worker, provider-independent OCR port, Tesseract adapter, persisted provenance and human correction workflow are implemented.
- This host does not have a Sinhala-capable Tesseract runtime/traineddata installation.
- The repository does not contain legally usable representative scanned Sinhala Grade 5 pages with human-adjudicated ground truth.
- No Sinhala character-error-rate, page-coverage or question-structure quality claim is made until that corpus is obtained and adjudicated.

### Grade 3–5 pilot corpus and Scholarship policy

- A controlled private-runtime inventory covers the available Grade 3, Grade 4 and Grade 5 source corpus. Only the explicitly human-reviewed Grade 4 and Grade 5 sources are active for retrieval.
- The available Grade 3 Sinhala Maths extraction was removed from active use after visual comparison exposed legacy-font glyph corruption despite non-empty extracted Unicode text. Grade 3 requires a corrected font mapping or human-adjudicated OCR before it can re-enter retrieval.
- Real Grade 5 Scholarship generation remains fail-closed. No authoritative reviewed Paper I ability/reasoning framework or past-paper evidence is available, and Paper II cannot have complete active Grade 3–5 evidence while Grade 3 remains excluded.
- Deterministic Paper I, Paper II and full-package fixtures prove orchestration, versioning and cross-grade isolation only. They are not real Scholarship educational-quality evidence.
- Term-test generation also remains fail-closed until an authoritative reviewed term-coverage policy is configured; the application does not guess term boundaries from filenames or generated text.

### Representative knowledge and retrieval quality

- Structured questions, curriculum chunks, review, versioned embeddings, hybrid retrieval and leakage-safe scope filtering are implemented.
- Current deterministic fixtures prove mechanics and isolation, not production relevance on a representative human-reviewed Grade 5 corpus.
- P3 remains open for representative data-quality evidence.
- P4 remains open until a documented threshold is met on the agreed human-reviewed embedded fixture set.

### Historical forecasting evidence

- Deterministic statistics, rolling held-out backtests, baseline comparison and safe fallback wording are implemented.
- Current multi-year fixtures prove the algorithm and persistence mechanics rather than real Grade 5 forecasting value.
- P5 remains open until the same report is run on representative human-reviewed historical records.
- The product must use syllabus-balanced practice wording whenever measured improvement is not meaningful. It must never claim exact future-paper prediction.

### Live generation and validation baseline scope

- The opt-in OpenAI generation-to-validation baseline has been executed for the versioned English `gpt-4o-mini-2024-07-18` configuration, with tokens, latency, integer-microusd cost, fingerprints and validation finding codes recorded in the phase tracker.
- Normal CI intentionally does not call a paid model.
- This single non-failing English structured-contract run closes P8's baseline-execution gate; it is not a statistical quality study or evidence of Sinhala fluency, factual correctness, age appropriateness or semantic uniqueness.
- Every generated result remains untrusted, requires canonical validation and cannot publish without human approval.

### Multi-grade and subject-quality evidence

- The reusable model, hard retrieval filters and teacher workflow now support Grades 1–13 with subject, unit and lesson scope; Grade 7 Maths fixtures prove architecture and isolation only.
- Real educational-quality acceptance remains Grade 5-first. No Grade 1–4 or Grade 6–13 curriculum-quality claim is made from deterministic fixtures.
- The Maths checker deliberately supports only bounded exact arithmetic, fractions, decimals and percentages. Symbolic algebra, word problems and unit conversion return an explicit warning for human review rather than a false pass.
- Factual/language subjects now have an optional strict OpenAI semantic-verifier adapter. Versioned deterministic decomposition separates each selected/accepted answer, sentence-bounded explanation unit and marking criterion, then verifies the bounded claim set in one provider call with exact claim identity/order, aggregate-status and source/page-reference checks. Immutable findings retain claim outcomes, provider/model/prompt/pricing lineage and exact token/cost/latency accounting; admin Operations aggregates those records without exposing source text. This is a verification aid, not proof by model agreement.
- `deterministic-factual-claims.v1` uses stable punctuation/newline boundaries rather than a linguistic proposition parser. Abbreviations, unusual punctuation and compound sentences can over- or under-segment an explanation; unsupported or unavailable checks remain WARN and every claim remains subject to human review.
- No real semantic-verifier credential/configuration or opt-in live quality/cost baseline is available in this environment. The adapter therefore remains disabled by default; missing, failed or insufficient verification remains WARN and requires human judgement. The committed live benchmark uses small synthetic reviewed English Science fixtures and cannot establish Sinhala fluency or production factual quality when it is eventually executed.
- Reviewer revisions are audited and stale validation cannot approve edited content. Structured correction feedback, explicit promotion to immutable draft quality examples, second-reviewer approval, bounded export and deterministic replay are implemented. Promotion never trains a model or changes prompts, validators or thresholds automatically.

### Live production identity provider

- The backend supports strict OIDC JWT/JWKS validation with fixed asymmetric algorithms, issuer/audience/time checks, bounded role mapping and stable UUID subject derivation.
- The web supports authorization code flow with PKCE, single-use state, bounded token exchange, authoritative backend session validation, secure cookies and optional RP-initiated logout.
- No external provider tenant, client registration, role/group mapping, production redirect URI or credentials are available in this repository/environment.
- Route-level tests and deterministic local browser acceptance do not count as a live production IdP acceptance run.
- The current web flow stores no refresh token or ID token. Sessions end at the earlier of access-token expiry and the configured application maximum.
- Back-channel logout and provider-specific session revocation are not implemented. Local logout always clears the application session; optional RP logout depends on provider support.
- New JWKS keys become usable after the bounded JWKS cache expires. Unknown key IDs do not force an immediate network refresh, limiting attacker-controlled JWKS fetch amplification.

## Operational deployment limitations

### Observability deployment

- Content-free structured operational events, manual OpenTelemetry spans, Sentry-compatible reporting and the admin Operations dashboard are implemented.
- A production collector, dashboards, alert rules and on-call routing are deployment responsibilities and are not provisioned by this repository.
- Production ingress and access logs must redact OIDC callback query strings because authorization codes and state appear in the callback URL.

### Object-storage lifecycle

- The private Studio now defaults to durable local POSIX filesystem storage. The configured host path must support hard links, atomic replacement, directory `fsync` and Unix permission semantics.
- Existing MinIO/S3 source objects are not migrated automatically. Switching an existing database to local storage requires explicit checksum-verified export/import; retained MinIO volumes must not be deleted before migration and backup verification.
- Local reconciliation tags are bounded atomic sidecars owned by the application; they do not modify immutable source bytes and are not interchangeable with arbitrary S3 operator tags.
- Scheduled reconciliation detects unreferenced content-addressed source objects after a grace period and persists candidate/resolution evidence.
- Reconciliation never deletes objects. Optional app-owned candidate tags are disabled by default and require explicit production configuration.
- Any lifecycle deletion rule requires separate operator approval, versioning/retention controls and recovery validation. It must never target the live `sources/` namespace without the documented candidate-tag conditions.

### Backup and recovery policy

- Guarded PostgreSQL backup/restore scripts, a source-verified runbook and a disposable PostgreSQL/pgvector restore test are implemented.
- Production RPO, RTO, retention, encryption/KMS, off-site replication and restore-drill frequency remain deployment decisions.
- No destructive restore has been run against a persistent project database.

### Abuse and network controls

- Authenticated costly operations have atomic per-principal Valkey limits and fail closed when the limiter is unavailable.
- These controls are not edge DDoS protection. Production ingress must independently enforce unauthenticated, per-IP and network-level limits.

### Production configuration

- Production settings reject deterministic identity, deterministic generation/embeddings, local credentials, insecure service connections and disabled application cost controls.
- A deployment still needs real TLS endpoints, database/Valkey credentials, a private writable local storage root (or optional S3 credentials), OIDC configuration, OpenTelemetry/Sentry destinations and a non-deterministic embedding provider.

## Product boundaries

- Retrieved/uploaded text is untrusted data and can contain prompt-injection-like content. It never authorizes an action.
- Lexical n-gram duplicate detection is not semantic paraphrase detection and can produce false positives or negatives.
- Automated validation cannot approve or publish content. Human review remains mandatory.
- Published paper serving requires no live model call, but Priority 2 student product work remains blocked until P10 is legitimately complete.

## Gate impact

P10 remains `IN_PROGRESS` while P2-P5 retain the human-reviewed real-data evidence gaps above and live external OIDC acceptance is unavailable. The automated journey, adversarial review, paid P8 baseline and release-commit CI gates are complete. Priority 2 remains blocked.