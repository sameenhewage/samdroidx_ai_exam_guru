# V1 Known Limitations

This document records limitations that remain after the implemented Priority 1 engineering controls. It is evidence for the P10 known-limitations criterion; it does not waive any incomplete acceptance gate or unlock Priority 2.

## Release-blocking evidence gaps

### Sinhala OCR quality

- The native-first extraction worker, provider-independent OCR port, Tesseract adapter, persisted provenance and human correction workflow are implemented.
- This host does not have a Sinhala-capable Tesseract runtime/traineddata installation.
- The repository does not contain legally usable representative scanned Sinhala Grade 5 pages with human-adjudicated ground truth.
- No Sinhala character-error-rate, page-coverage or question-structure quality claim is made until that corpus is obtained and adjudicated.

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
- A deployment still needs real TLS endpoints, database/object-storage/Valkey credentials, OIDC configuration, OpenTelemetry/Sentry destinations and a non-deterministic embedding provider.

## Product boundaries

- Retrieved/uploaded text is untrusted data and can contain prompt-injection-like content. It never authorizes an action.
- Lexical n-gram duplicate detection is not semantic paraphrase detection and can produce false positives or negatives.
- Automated validation cannot approve or publish content. Human review remains mandatory.
- Published paper serving requires no live model call, but Priority 2 student product work remains blocked until P10 is legitimately complete.

## Gate impact

P10 remains `IN_PROGRESS` while P2-P5 retain the human-reviewed real-data evidence gaps above and live external OIDC acceptance is unavailable. The automated journey, adversarial review, paid P8 baseline and release-commit CI gates are complete. Priority 2 remains blocked.