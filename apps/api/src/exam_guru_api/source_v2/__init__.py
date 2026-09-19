"""Source V2 — verified source content and the gate in front of everything else.

Pipeline: deterministic render/layout -> reader candidates -> Machine Candidate
-> human Confirm/Correct/Exclude -> Verified Source Content.

The hard invariant lives here:

    NO VERIFIED SOURCE CONTENT
      -> NO EDUCATIONAL ANALYSIS -> NO KNOWLEDGE -> NO EMBEDDINGS
      -> NO RAG -> NO GENERATION

See `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`.
"""
