"""Which readers may witness a region, and how much weight their evidence carries.

This is a record of measurement, not a preference. The executing agent's own
visual reading is the primary source of text (D14); everything here concerns
the *secondary* witnesses that run afterwards on the same pixels.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

PRIMARY_READER = "primary-agent-reading"


class Tier(StrEnum):
    """What a reading is allowed to do.

    There are only two roles now. The measured evidence closed the gap that
    used to exist between "strong" and "weak" local readers: at 306-7506%
    character error they are not competing transcriptions, they are audit
    signals. See docs/source-v2/DECISIONS.md D15.
    """

    PRIMARY = "primary"
    AUDIT_ONLY = "audit-only"


@dataclass(frozen=True)
class Selection:
    evidence: str
    tiers: dict[str, Tier]

    def tier(self, reader: str) -> Tier:
        return self.tiers.get(reader, Tier.AUDIT_ONLY)

    def readers_for(self, language: str, region_type: str) -> tuple[str, ...]:
        """Every configured secondary witness. Region type does not gate them."""

        _ = (language, region_type)
        return tuple(
            name for name, tier in self.tiers.items() if tier is not Tier.PRIMARY
        )

    def rank(self, language: str, region_type: str, reader: str) -> int:
        """Lower is listed first. Ordering only affects how evidence is shown."""

        _ = (language, region_type)
        return 0 if self.tier(reader) is Tier.PRIMARY else 1

    def audit_only(self, reader: str) -> bool:
        return self.tier(reader) is Tier.AUDIT_ONLY


# Measured against human-confirmed source on real pages, reported as a ratio
# and a percentage (`benchmark/primary-vs-readers.json`):
#
#   document          reader                mean CER        exact   insertions
#   sankhya-rata      sinhala-deepseek      3.27  (327%)    2/16     6 870
#   sankhya-rata      sinhala-lightonocr   75.06 (7 506%)   0/16     2 617
#   mawbasa 156+186   sinhala-deepseek      3.06  (306%)      -      10 591
#   mawbasa 156+186   sinhala-lightonocr    0.48   (48%)      -         951
#
# A character error rate above 1.0 means the reading contains more errors than
# the reference has characters: the model is inventing, not misreading. On top
# of that both have produced foreign-script output on Sinhala pages - Myanmar
# glyphs on a heading, fluent English on an instruction block - and LightOnOCR
# invented 1808 characters of LaTeX on a two-character folio.
#
# These are not candidate transcriptions. They are **audit witnesses**: they
# may raise a warning, contribute disagreement evidence and force human
# attention, and they may never replace, rewrite or outvote the primary
# reading. Effort spent making either of them the primary reader is effort
# spent in the wrong place (D15).
SINHALA_V3 = Selection(
    evidence="docs/source-v2/BENCHMARK_READERS.md + benchmark/primary-vs-readers.json",
    tiers={
        PRIMARY_READER: Tier.PRIMARY,
        "sinhala-deepseek": Tier.AUDIT_ONLY,
        "sinhala-lightonocr": Tier.AUDIT_ONLY,
    },
)

ACTIVE = SINHALA_V3
