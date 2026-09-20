"""Build the Machine Candidate from the one primary reading.

There is exactly one machine source reader: the executing AI agent, looking at
the canonical crop. No second reader proposes text, corroborates it, votes on
it or contradicts it.

Rules this file exists to enforce:

* the candidate text is *always* the sealed primary reading, carried through
  unmutated — nothing here rewrites, normalises or replaces it;
* deterministic validators run on that text and its geometry, and they report
  only: a finding makes a region need human attention, never new text;
* where the agent read no text the candidate abstains, and abstaining is a
  correct answer for a figure, a rule or a blank cell;
* the reason travels with the candidate so a human can see how it was formed.

The candidate is never trust. Only a human creates Verified Source Content.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tools.source_factory.candidate.validators import validate_primary

# The executing agent, reading the canonical crop. Not a model, not a
# provider, not a service, and not one of several.
PRIMARY_READER = "primary-agent-reading"


@dataclass(frozen=True)
class PrimaryReading:
    """What the executing agent saw on the canonical crop. The only reading."""

    region_id: str
    region_type: str
    text: str
    uncertainty: tuple[dict, ...] = ()
    language: str = "sinhala"
    layout_lines: int | None = None

    @property
    def blank(self) -> bool:
        return not self.text.strip()


@dataclass
class MachineCandidate:
    region_id: str
    region_type: str
    text: str
    abstained: bool
    reason: str
    validation_findings: list[str] = field(default_factory=list)
    uncertain: bool = False

    @property
    def requires_human_attention(self) -> bool:
        """Anything a reviewer must look at rather than skim.

        Deliberately broad. The cost of flagging a clean region is a glance;
        the cost of missing a wrong one is corrupt source content.
        """

        return bool(self.uncertain or self.abstained or self.validation_findings)

    def to_json(self) -> dict:
        return {
            "region_id": self.region_id,
            "region_type": self.region_type,
            "text": self.text,
            "abstained": self.abstained,
            "reason": self.reason,
            "validation_findings": self.validation_findings,
            "uncertain": self.uncertain,
            "requires_human_attention": self.requires_human_attention,
        }


def build(*, primary: PrimaryReading) -> MachineCandidate:
    """Seal the primary reading into a Machine Candidate.

    The candidate text is the primary reading, byte for byte. The only work
    done here is deterministic: describe what the validators noticed and say
    whether a human has to look closely.
    """

    flagged = [item.get("kind", "?") for item in primary.uncertainty]
    findings: list[str] = [
        f"primary reading marked uncertain: {item.get('detail', '')}"[:200]
        for item in primary.uncertainty
    ]
    # Deterministic validators run on the primary reading and the geometry it
    # came from. They cannot hallucinate and they need no second opinion.
    findings += [
        str(item)
        for item in validate_primary(
            primary.text,
            region_type=primary.region_type,
            language=primary.language,
            layout_lines=primary.layout_lines,
        )
    ]

    if primary.blank:
        # A region the agent read as carrying no text - a figure, a rule, a
        # blank cell. It still has to be decided, so it abstains rather than
        # proposing emptiness as a reading.
        detail = primary.uncertainty[0]["detail"] if primary.uncertainty else "no text"
        return MachineCandidate(
            region_id=primary.region_id,
            region_type=primary.region_type,
            text="",
            abstained=True,
            reason=f"primary reading found no text: {detail}"[:400],
            validation_findings=findings,
            uncertain=bool(flagged),
        )

    return MachineCandidate(
        region_id=primary.region_id,
        region_type=primary.region_type,
        text=primary.text,
        abstained=False,
        reason=(
            "primary reading by the executing agent from the canonical crop; "
            f"{len(findings)} deterministic finding(s)"
            + (f"; primary flagged {flagged}" if flagged else "")
        )[:400],
        validation_findings=findings,
        uncertain=bool(flagged),
    )
