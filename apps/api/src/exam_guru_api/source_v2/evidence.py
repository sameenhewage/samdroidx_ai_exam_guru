"""Reading one stored `reason` string as the diagnostics it actually contains.

A Machine Candidate's `reason` is a single line written by the offline
pipeline, and it mixes three things a reviewer reads very differently:

    primary reading by the executing agent from the canonical crop;
    3 deterministic finding(s); primary flagged ['spacing-doubt', 'no-text']

The first clause is provenance — true of every region, so it carries no
information about *this* one. The middle clauses are deterministic findings.
The bracketed codes are uncertainty the reader itself declared. Shown as one
run-on sentence at the top of a review card, they drown the thing the teacher
is there to check.

Splitting is deliberately conservative. Only clauses whose shape is known are
lifted out; anything unrecognised stays where it is, and the full `reason` is
always carried alongside, so nothing is hidden by a parser that guessed wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: `primary flagged ['spacing-doubt', 'no-text']` — the reader's own doubts.
FLAGGED = re.compile(r"primary flagged \[(?P<codes>[^\]]*)\]")
CODE = re.compile(r"['\"]\s*([^'\"]+?)\s*['\"]")
#: The provenance clause. True of every machine reading, so never a finding.
PROVENANCE = re.compile(r"^primary reading by the executing agent\b")
#: An abstention. The reader looked and found no printed text, which for a
#: figure is the correct answer rather than a failure (D4, D18).
NO_TEXT = re.compile(r"^primary reading found no text\b")
NO_TEXT_CODE = "no-text"


@dataclass(frozen=True)
class Diagnostics:
    """What a `reason` string says, separated so a card can hide it properly."""

    reason: str
    findings: tuple[str, ...]
    uncertainty: tuple[str, ...]


def _dedupe(values: list[str]) -> tuple[str, ...]:
    """Order-preserving. The same code is often flagged once per occurrence."""

    seen: dict[str, None] = {}
    for value in values:
        if value:
            seen.setdefault(value, None)
    return tuple(seen)


def diagnostics(reason: str) -> Diagnostics:
    """Split a stored reason into its findings and its uncertainty codes."""

    body = (reason or "").strip()
    if not body:
        return Diagnostics(reason="", findings=(), uncertainty=())

    uncertainty: list[str] = []
    flagged = FLAGGED.search(body)
    structured = body
    if flagged is not None:
        uncertainty.extend(CODE.findall(flagged.group("codes")))
        structured = body[: flagged.start()] + body[flagged.end() :]

    # Free prose starts at the first ": ". Everything before it is the
    # machine's structured head and can be split on clause boundaries; the
    # prose after it cannot, because it contains its own punctuation.
    head, separator, detail = structured.partition(": ")
    clauses = [clause.strip(" ;") for clause in head.split("; ")]
    findings = [clause for clause in clauses if clause and not PROVENANCE.match(clause)]

    if NO_TEXT.match(head.strip()):
        uncertainty.append(NO_TEXT_CODE)
        findings = [clause for clause in findings if not NO_TEXT.match(clause)]
    if separator and detail.strip():
        findings.append(detail.strip())

    # A single clause that *is* the whole reason is not a separate finding;
    # listing it twice would just make the technical section look busier.
    if len(findings) == 1 and findings[0] == body:
        findings = []

    return Diagnostics(reason=body, findings=tuple(findings), uncertainty=_dedupe(uncertainty))
