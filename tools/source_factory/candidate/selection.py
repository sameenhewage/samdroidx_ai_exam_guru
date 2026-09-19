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
    """How far a witness's evidence is allowed to carry."""

    PRIMARY = "primary"
    STRONG_SECONDARY = "strong-secondary"
    WEAK_CORROBORATING = "weak-corroborating"


@dataclass(frozen=True)
class Selection:
    evidence: str
    tiers: dict[str, Tier]

    def tier(self, reader: str) -> Tier:
        return self.tiers.get(reader, Tier.WEAK_CORROBORATING)

    def readers_for(self, language: str, region_type: str) -> tuple[str, ...]:
        """Every configured secondary witness. Region type does not gate them."""

        _ = (language, region_type)
        return tuple(
            name for name, tier in self.tiers.items() if tier is not Tier.PRIMARY
        )

    def rank(self, language: str, region_type: str, reader: str) -> int:
        """Lower is stronger. Used to order evidence, never to select text."""

        _ = (language, region_type)
        order = {
            Tier.PRIMARY: 0,
            Tier.STRONG_SECONDARY: 1,
            Tier.WEAK_CORROBORATING: 2,
        }
        return order[self.tier(reader)]


# Measured on real pages, recorded in docs/source-v2/BENCHMARK_READERS.md and
# extended by what the Studio showed on pages 156 and 186:
#
#   primary-agent-reading   the executing agent, reading the original pixels.
#                           Produced the only reading that matched the printed
#                           page on the heading bars, the folios and the Latin
#                           resource line.
#   sinhala-deepseek        mean CER 1.54, real mean foreign-script 0.30. Good
#                           enough to be worth hearing, and wrong often enough
#                           that it cannot lead: it rendered Sinhala headings
#                           in Myanmar script, answered Sinhala instruction
#                           blocks in fluent English, and read JICA OBIHIRO as
#                           JICA ORHRO.
#   sinhala-lightonocr      mean CER 451.5, 2 degenerations in 30 crops,
#                           invented 1808 characters of LaTeX on a
#                           two-character folio. Severe glyph substitution.
#                           Kept only because it is genuinely better on Latin
#                           tokens and so occasionally contradicts DeepSeek
#                           usefully - but it never carries a region on its own.
#
# Neither local reader may create or replace the primary reading. This ordering
# only decides how loudly a disagreement is reported.
SINHALA_V2 = Selection(
    evidence="docs/source-v2/BENCHMARK_READERS.md + Studio evidence on pages 156/186",
    tiers={
        PRIMARY_READER: Tier.PRIMARY,
        "sinhala-deepseek": Tier.STRONG_SECONDARY,
        "sinhala-lightonocr": Tier.WEAK_CORROBORATING,
    },
)

ACTIVE = SINHALA_V2
