"""The primary visual reading: what the executing agent saw on the original page.

This runs *before* any local OCR. DeepSeek and LightOnOCR are secondary
witnesses that read the same pixels afterwards; they may corroborate or
contradict the primary reading but never replace it silently, and they never
create the initial Machine Candidate.

See `docs/source-v2/DECISIONS.md` D14.
"""
