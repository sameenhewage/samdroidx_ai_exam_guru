"""Build the Machine Candidate from several witnesses.

Rules this file exists to enforce (decision D4):

* never blend or average two readings — a candidate is always one witness's
  actual text, chosen for a stated reason;
* a conflict on one critical token pins *that token* and does not condemn the
  region;
* where no witness can be trusted the candidate abstains, and abstaining is a
  correct answer;
* the selection reason travels with the candidate so a human can see why.

The candidate is never trust. Only a human creates Verified Source Content.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tools.source_factory.candidate.alignment import DisagreementMap, build_disagreement_map

# A reading whose decoder collapsed is evidence of failure, not a reading.
DEGENERATE_REPETITION = 0.5
DEGENERATE_STRUCTURE = 0.6


@dataclass
class Witness:
    """One reader's proposal for one region, with the signals we measured."""

    reader: str
    text: str
    seconds: float = 0.0
    failed: bool = False
    repetition: float = 0.0
    structural_repetition: float = 0.0
    foreign_script: float = 0.0
    rank: int = 0  # lower wins; set by the measured per-language selection

    @property
    def degenerate(self) -> bool:
        return (
            self.repetition >= DEGENERATE_REPETITION
            or self.structural_repetition >= DEGENERATE_STRUCTURE
        )

    @property
    def trustworthy(self) -> bool:
        return bool(self.text.strip()) and not self.failed and not self.degenerate


@dataclass
class MachineCandidate:
    region_id: str
    region_type: str
    text: str
    abstained: bool
    reason: str
    chosen_reader: str | None
    witnesses: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    uncertain_tokens: list[dict] = field(default_factory=list)
    agreement_ratio: float = 1.0
    critical_conflict: bool = False
    disagreement: DisagreementMap | None = None

    def to_json(self) -> dict:
        payload = {
            "region_id": self.region_id,
            "region_type": self.region_type,
            "text": self.text,
            "abstained": self.abstained,
            "reason": self.reason,
            "chosen_reader": self.chosen_reader,
            "witnesses": self.witnesses,
            "rejected": self.rejected,
            "agreement_ratio": round(self.agreement_ratio, 4),
            "critical_conflict": self.critical_conflict,
            "uncertain_tokens": self.uncertain_tokens,
        }
        if self.disagreement is not None:
            payload["disagreement"] = self.disagreement.to_json()
        return payload


def _rejection_reason(witness: Witness) -> str:
    if witness.failed:
        return "reader failed"
    if not witness.text.strip():
        return "reader produced nothing"
    if witness.repetition >= DEGENERATE_REPETITION:
        return "decoder repeated the same character run"
    return "decoder enumerated a repeated line template"


def build(
    *,
    region_id: str,
    region_type: str,
    witnesses: list[Witness],
) -> MachineCandidate:
    names = [witness.reader for witness in witnesses]
    rejected = {
        witness.reader: _rejection_reason(witness)
        for witness in witnesses
        if not witness.trustworthy
    }
    usable = [witness for witness in witnesses if witness.trustworthy]

    if not usable:
        return MachineCandidate(
            region_id=region_id,
            region_type=region_type,
            text="",
            abstained=True,
            reason="no witness was trustworthy for this region",
            chosen_reader=None,
            witnesses=names,
            rejected=rejected,
            agreement_ratio=0.0,
        )

    if len(usable) == 1:
        only = usable[0]
        return MachineCandidate(
            region_id=region_id,
            region_type=region_type,
            text=only.text,
            abstained=False,
            reason=(
                "single trustworthy witness; unverified because nothing corroborates it"
                if rejected
                else "single configured witness"
            ),
            chosen_reader=only.reader,
            witnesses=names,
            rejected=rejected,
            agreement_ratio=1.0,
        )

    disagreement = build_disagreement_map({w.reader: w.text for w in usable})
    # The candidate text is the highest-ranked trustworthy witness's actual
    # text. Rank comes from the measured per-language benchmark, never from a
    # vote and never from a blend.
    chosen = min(usable, key=lambda witness: (witness.rank, witness.reader))
    uncertain = [cell.to_json() for cell in disagreement.cells if cell.critical]
    return MachineCandidate(
        region_id=region_id,
        region_type=region_type,
        text=chosen.text,
        abstained=False,
        reason=(
            f"selected {chosen.reader} by measured rank; "
            f"{len(disagreement.cells)} token conflicts, {len(uncertain)} critical"
        ),
        chosen_reader=chosen.reader,
        witnesses=names,
        rejected=rejected,
        uncertain_tokens=uncertain,
        agreement_ratio=disagreement.agreement_ratio,
        critical_conflict=disagreement.critical_conflict,
        disagreement=disagreement,
    )
