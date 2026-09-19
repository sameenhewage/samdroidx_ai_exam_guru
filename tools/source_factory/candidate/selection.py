"""Which reader is trusted first, for which language and which region type.

Ranks come from the committed benchmark report, never from a published claim
(decision D2). Lower rank wins. A reader absent from a bucket is not used for
that bucket at all.

This file is data, deliberately: changing a rank must be a reviewable diff that
points at the measurement that justified it.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_RANK = 99


@dataclass(frozen=True)
class Selection:
    """Ordered readers per (language, region type). First entry is rank 0."""

    order: dict[tuple[str, str], tuple[str, ...]]
    evidence: str

    def rank(self, language: str, region_type: str, reader: str) -> int:
        readers = self.order.get((language, region_type)) or self.order.get((language, "*"), ())
        return readers.index(reader) if reader in readers else DEFAULT_RANK

    def readers_for(self, language: str, region_type: str) -> tuple[str, ...]:
        return self.order.get((language, region_type)) or self.order.get((language, "*"), ())


# Justified by docs/source-v2/BENCHMARK_READERS.md, run 2026-09-19 over 30 real
# region crops from the fixed benchmark pages.
#
#   sinhala-deepseek    mean CER 1.54, 1 degeneration, foreign script 0.033,
#                       54.2 s/crop, 7283 MiB peak.
#                       Lost `OBIHIRO` on the Latin crop (2/3 tokens).
#   sinhala-lightonocr  mean CER 451.5, 2 degenerations, foreign script 0.067,
#                       27.8 s/crop, 2644 MiB peak.
#                       Recovered `OBIHIRO` exactly, but lost the printed
#                       `2 cm` and invented 1808 characters on a two-character
#                       folio.
#
# DeepSeek leads on overall fidelity — two orders of magnitude on CER, fewer
# degenerations, less foreign script — so it reads first. LightOnOCR is kept as
# the corroborating witness rather than dropped, because it is genuinely better
# on Latin tokens, and that disagreement is worth surfacing to a human instead
# of hiding behind a single reader. Neither is trusted: this ordering only
# decides whose text is proposed, and every conflict is still recorded.
SINHALA_V1 = Selection(
    evidence="docs/source-v2/BENCHMARK_READERS.md (30 crops, 2026-09-19)",
    order={("sinhala", "*"): ("sinhala-deepseek", "sinhala-lightonocr")},
)

ACTIVE = SINHALA_V1
