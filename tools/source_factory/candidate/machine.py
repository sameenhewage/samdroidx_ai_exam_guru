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
from tools.source_factory.candidate.validators import audit_warnings, validate_primary

# A reading whose decoder collapsed is evidence of failure, not a reading.
DEGENERATE_REPETITION = 0.5
DEGENERATE_STRUCTURE = 0.6
# A reading mostly written in a script the page is not printed in is not a
# reading of that page. Seen for real: DeepSeek answered a Sinhala heading
# crop with Myanmar glyphs, fluently and without repeating itself.
FOREIGN_SCRIPT_LIMIT = 0.5
# Latin is tolerated inside a Sinhala page, so a reading that is *entirely*
# English carries no foreign script by that measure and slips through. Seen for
# real: DeepSeek answered a Sinhala instruction block with "3. Write a function
# called check that checks whether a number is even". The tell is cross-witness:
# another reader saw the page's own script in the same region.
OUTLIER_EXPECTED_SCRIPT = 0.15
CORROBORATED_EXPECTED_SCRIPT = 0.50

# The executing agent, reading the original pixels. Not a model, not a
# provider, not a service. It reads first and the local readers corroborate.
PRIMARY_READER = "primary-agent-reading"
# Below this agreement the witnesses are not quibbling about a glyph, they are
# reading a different text. That has to reach a human even with no critical
# token in sight.
SUBSTANTIVE_DISAGREEMENT = 0.5


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
    expected_script: float = 1.0  # share of letters in the page's own script
    rank: int = 0  # lower wins; set by the measured per-language selection
    script_outlier: bool = False  # set by build(), needs the other witnesses

    @property
    def degenerate(self) -> bool:
        return (
            self.repetition >= DEGENERATE_REPETITION
            or self.structural_repetition >= DEGENERATE_STRUCTURE
        )

    @property
    def wrong_script(self) -> bool:
        return self.foreign_script >= FOREIGN_SCRIPT_LIMIT

    @property
    def trustworthy(self) -> bool:
        return (
            bool(self.text.strip())
            and not self.failed
            and not self.degenerate
            and not self.wrong_script
            and not self.script_outlier
        )


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
    primary_text: str = ""
    supporting_readers: list[str] = field(default_factory=list)
    validation_findings: list[str] = field(default_factory=list)
    uncertain: bool = False

    @property
    def selected_source(self) -> str | None:
        return self.chosen_reader

    @property
    def requires_human_attention(self) -> bool:
        """Anything a reviewer must look at rather than skim.

        Deliberately broad. The cost of flagging a clean region is a glance;
        the cost of missing a wrong one is corrupt source content.
        """

        return bool(
            self.critical_conflict
            or self.uncertain
            or self.abstained
            or self.validation_findings
        )

    def to_json(self) -> dict:
        payload = {
            "region_id": self.region_id,
            "region_type": self.region_type,
            # `text` is what the Studio proposes. `selected_text` is the same
            # value named for the comparison record; `primary_text` is kept
            # beside it so a reviewer can always see what the agent read even
            # if a later revision changes the proposal.
            "text": self.text,
            "primary_text": self.primary_text,
            "selected_text": self.text,
            "selected_source": self.chosen_reader,
            "abstained": self.abstained,
            "reason": self.reason,
            "chosen_reader": self.chosen_reader,
            "witnesses": self.witnesses,
            "supporting_readers": self.supporting_readers,
            "rejected_readers": self.rejected,
            "rejected": self.rejected,
            "validation_findings": self.validation_findings,
            "uncertain": self.uncertain,
            "requires_human_attention": self.requires_human_attention,
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
    if witness.structural_repetition >= DEGENERATE_STRUCTURE:
        return "decoder enumerated a repeated line template"
    if witness.script_outlier:
        return (
            "reading contains none of the page's own script while another reader read it"
        )
    return (
        f"reading is {witness.foreign_script:.0%} in a script the page is not printed in"
    )


def _flag_script_outliers(witnesses: list[Witness]) -> None:
    """Reject a reading that shows none of the page's script when another does.

    One reader seeing the page's own script is positive evidence that the
    region contains it. A second reader answering the same pixels with none of
    it is not a competing transcription, it is an invention. Where *no* reader
    finds the expected script the region may genuinely be Latin, so nothing is
    flagged and the disagreement machinery handles it.
    """

    readable = [w for w in witnesses if w.text.strip() and not w.failed]
    if len(readable) < 2:
        return
    if not any(w.expected_script >= CORROBORATED_EXPECTED_SCRIPT for w in readable):
        return
    for witness in readable:
        if witness.expected_script < OUTLIER_EXPECTED_SCRIPT:
            witness.script_outlier = True


@dataclass(frozen=True)
class PrimaryReading:
    """What the executing agent saw on the original page. Always the base text."""

    region_id: str
    region_type: str
    text: str
    uncertainty: tuple[dict, ...] = ()
    language: str = "sinhala"

    @property
    def blank(self) -> bool:
        return not self.text.strip()


def build(
    *,
    primary: PrimaryReading,
    witnesses: list[Witness],
) -> MachineCandidate:
    """Propose the primary reading, corroborated or contradicted by the witnesses.

    The candidate text is *always* the primary reading. Local OCR never
    replaces it, never outvotes it and never supplies the initial proposal:
    it is here to disagree loudly so a human looks again (D14).
    """

    _flag_script_outliers(witnesses)
    names = [witness.reader for witness in witnesses]
    rejected = {
        witness.reader: _rejection_reason(witness)
        for witness in witnesses
        if not witness.trustworthy
    }
    usable = [witness for witness in witnesses if witness.trustworthy]
    flagged = [item.get("kind", "?") for item in primary.uncertainty]
    findings: list[str] = [
        f"primary reading marked uncertain: {item.get('detail', '')}"[:200]
        for item in primary.uncertainty
    ]
    # Deterministic validators run on the primary reading *before* any local
    # reader is consulted. They cannot hallucinate and they need no second
    # opinion (D15).
    findings += [
        str(item)
        for item in validate_primary(
            primary.text, region_type=primary.region_type, language=primary.language
        )
    ]
    # Then the audit-only readers contribute warnings. Never text.
    findings += [
        str(item)
        for item in audit_warnings(
            primary.text,
            {witness.reader: witness.text for witness in usable},
            rejected=rejected,
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
            chosen_reader=PRIMARY_READER,
            witnesses=names,
            rejected=rejected,
            agreement_ratio=0.0,
            primary_text="",
            validation_findings=findings,
            uncertain=bool(flagged),
        )

    if not usable:
        return MachineCandidate(
            region_id=primary.region_id,
            region_type=primary.region_type,
            text=primary.text,
            abstained=False,
            reason=(
                "primary reading, uncorroborated: no local reader produced usable evidence"
                + (f"; primary flagged {flagged}" if flagged else "")
            )[:400],
            chosen_reader=PRIMARY_READER,
            witnesses=names,
            rejected=rejected,
            agreement_ratio=0.0,
            critical_conflict=bool(flagged),
            primary_text=primary.text,
            validation_findings=findings
            + ["no local reader corroborated this region"],
            uncertain=bool(flagged),
        )

    # Compare the primary reading against every usable witness on equal terms.
    # The primary participates in the alignment so its disagreements are
    # visible, but it is not competing for selection.
    disagreement = build_disagreement_map(
        {PRIMARY_READER: primary.text} | {w.reader: w.text for w in usable}
    )
    uncertain = [cell.to_json() for cell in disagreement.cells if cell.critical]
    # A flat contradiction need not contain a "critical token". If the readers
    # broadly do not recognise what the agent wrote down, that is exactly the
    # case a human has to look at, whether or not a digit happens to differ.
    substantive = disagreement.agreement_ratio < SUBSTANTIVE_DISAGREEMENT
    critical = disagreement.critical_conflict or bool(flagged) or substantive
    if substantive:
        findings.append("local readers broadly disagree with the primary reading")
    # The JICA rule. Where every witness lines up against the primary on a
    # critical token, that is the strongest signal the pipeline can produce -
    # and still not a licence to overwrite. The primary text stands and the
    # conflict is escalated, because two OCR models agreeing is not evidence
    # that the page says what they say. A human looks.
    unanimous_against = False
    for cell in disagreement.cells:
        opinions = {
            variant.value: set(variant.readers) for variant in cell.variants
        }
        primary_value = next(
            (value for value, readers in opinions.items() if PRIMARY_READER in readers),
            None,
        )
        against = {
            value: readers
            for value, readers in opinions.items()
            if PRIMARY_READER not in readers
        }
        if primary_value and len(against) == 1:
            other, readers = next(iter(against.items()))
            # Both sides must actually say something. An empty side is an
            # alignment gap - the readers segmented the line differently -
            # not a claim that the page reads otherwise.
            if other and len(readers) >= len(usable):
                unanimous_against = True
                findings.append(
                    f"every local reader reads {other!r} where the primary reading has "
                    f"{primary_value!r} ({cell.kind}); the primary reading is kept and "
                    "the conflict escalated rather than overwritten"
                )
    critical = critical or unanimous_against
    corroboration = (
        f"corroborated against {len(usable)} local reader(s)"
        if len(usable) > 1
        else "checked against 1 local reader, no second witness"
    )
    return MachineCandidate(
        region_id=primary.region_id,
        region_type=primary.region_type,
        text=primary.text,
        abstained=False,
        reason=(
            f"primary reading {corroboration}; "
            f"{len(disagreement.cells)} token conflicts, {len(uncertain)} critical"
            + ("; readers broadly disagree with the primary reading" if substantive else "")
            + (f"; primary flagged {flagged}" if flagged else "")
        )[:400],
        chosen_reader=PRIMARY_READER,
        witnesses=names,
        rejected=rejected,
        uncertain_tokens=uncertain,
        agreement_ratio=disagreement.agreement_ratio,
        critical_conflict=critical,
        disagreement=disagreement,
        primary_text=primary.text,
        supporting_readers=[
            witness.reader for witness in usable if witness.text == primary.text
        ],
        validation_findings=findings,
        uncertain=bool(flagged),
    )
