# Source V2 — Locked Decisions

Decisions recorded here are **locked**. Do not redesign them in a later session.
To change one, add a new dated entry that supersedes it and say why the evidence
changed. Never silently reverse one.

Specification: `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`.

---

## D1 — Source reading is local + Codex/Astra. OpenAI is banned from it.
**Locked 2026-09-19.**
OpenAI must not be called for source OCR/extraction — not as primary, not as a
witness, not as a sanity check. Evidence: general providers hallucinated Sinhala
and substituted `www.moe.gov.lk` for the printed `www.nie.lk`. OpenAI remains
allowed for non-source work (generation, validation) where a human gate exists.

## D2 — Readers are language specialists, chosen by measurement.
**Locked 2026-09-19.**
Sinhala: `avishadilhara/sinhala-lightonocr-2-1b-Qlora`,
`avishadilhara/sinhala-deepseek-ocr-Qlora`. Tamil: selected by benchmark.
A published benchmark is not evidence. Selection is per **language × region
type** from our own measured table (CER, hallucinations, insertions, deletions,
exact match, critical tokens, runtime, VRAM).

## D3 — Layout is deterministic and carries no text.
**Locked 2026-09-19.**
Geometry is produced by deterministic code from the rendered image, never by a
model, and the layout contract explicitly holds no text. Naive whole-page
vertical projection is forbidden as the primary algorithm: columns come from a
row-tolerant coverage profile, and elements crossing a gutter become straddlers.

## D4 — No blind majority voting.
**Locked 2026-09-19.**
Witnesses are aligned token- and character-wise. A conflict pins the specific
token; it never makes the whole region uncertain. Where no witness is
trustworthy the Machine Candidate abstains. **Abstaining is a correct answer.**

## D5 — Only a human creates Verified Source Content.
**Locked 2026-09-19.**
A reader result, a `can_confirm` diagnostic, model agreement, a legacy trusted
flag or a successful job is not ground truth. Confirmation binds an explicit
original-page comparison to the current candidate and review version. A
correction creates an unverified child candidate.

## D6 — Machine first; the teacher is not an OCR typist.
**Locked 2026-09-19.**
The human workflow is Confirm / Correct / Exclude over a Machine Candidate
presented beside the original page. Never present an empty box to type into.

## D7 — The hard invariant is enforced in code.
**Locked 2026-09-19.**
`NO VERIFIED SOURCE CONTENT → NO EDUCATIONAL ANALYSIS → NO KNOWLEDGE → NO
EMBEDDINGS → NO RAG → NO GENERATION`. Enforced at the domain boundary and proved
by a test that fails when bypass is attempted. A document stating it is not enough.

## D8 — Forward migrations only; Studio data is preserved.
**Locked 2026-09-19.**
Historical migrations are never deleted or rewritten. A forward migration must
not promote a legacy record to verified. Removing a material from use must not
destroy provenance or audit history.

## D9 — Exactly one source architecture ships.
**Locked 2026-09-19.**
Cleanup happens only after V2 passes, and then it is complete: old source OCR
runtime, the obsolete OpenAI document-understanding source path, and the
Qwen/Ornith/Luna/Tesseract source-reading paths are removed along with their
env/config/dependencies/tests. Two active source architectures must not coexist.

## D10 — Reuse is narrow and deliberate.
**Locked 2026-09-19.**
Only these V1 pieces survive: deterministic rendering, source identities and
checksums, the human verification concepts, and the provider-neutral
`build_disagreement_map` / `align_source_tokens` alignment. They are **ported**
into the V2 package, not imported from the old module, so the old module can be
deleted whole.

## D11 — Real pages are the acceptance evidence.
**Locked 2026-09-19.**
Synthetic fixtures prove mechanics only and may never be used to claim real
Sinhala or Tamil reading quality. Benchmark page membership is fixed; if the
code struggles on a page, fix the code. Chrome DevTools MCP acceptance runs on
the real Studio against the real source, continuously during integration.

## D12 — V2 code lives in `tools/source_factory/`, then moves.
**Locked 2026-09-19.**
Development and benchmarking happen in `tools/source_factory/` where they can
run without the API stack. Domain logic that the product needs is moved into
`apps/api/src/exam_guru_api/` behind ports when Phase 5 wires persistence. The
tools package must never become a second production runtime.

## D13 — Python environments are declared per script (PEP 723).
**Locked 2026-09-19.**
`tools/` scripts declare their own dependencies inline so `uv run` needs no
project environment. numpy must stay `< 2.3` while opencv is `4.12`. Heavy model
dependencies (torch, transformers, peft) are declared only by the reader/benchmark
scripts that need them, never by the layout or contract tooling.
