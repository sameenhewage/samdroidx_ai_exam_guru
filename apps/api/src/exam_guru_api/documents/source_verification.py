import hashlib
import unicodedata
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.documents.fidelity import ALGORITHM_VERSION
from exam_guru_api.documents.source_reading import SourceReadCandidate
from exam_guru_api.documents.understanding_contracts import (
    Key,
    ShortText,
    UnderstandingModel,
    _canonical_bytes,
)
from exam_guru_api.documents.understanding_verification import (
    Checksum,
    ObservationCandidate,
    PageArtifactIdentity,
    PageVerificationReport,
    verify_understanding,
)


class SourceVerificationDecision(UnderstandingModel):
    policy_version: Literal["source-fidelity-verification.v1"] = "source-fidelity-verification.v1"
    actor_id: UUID
    compared_with_original: Literal[True]
    reviewed_region_keys: tuple[Key, ...] = Field(min_length=1, max_length=128)
    resolved_uncertainty_keys: tuple[Key, ...] = Field(max_length=128)
    content_fingerprint: Checksum
    reason: ShortText

    @model_validator(mode="after")
    def explicit_decision(self) -> Self:
        if (
            self.reason != self.reason.strip()
            or not self.reason.strip()
            or any(unicodedata.category(character) in {"Cc", "Cs"} for character in self.reason)
        ):
            raise ValueError("source verification requires a clean reason")
        for keys in (self.reviewed_region_keys, self.resolved_uncertainty_keys):
            if len(keys) != len(set(keys)):
                raise ValueError("source verification keys must be unique")
        return self


class VerifiedSourceContent(UnderstandingModel):
    schema_version: Literal["verified-source-content.v1"] = "verified-source-content.v1"
    id: UUID
    source: PageArtifactIdentity
    candidate_id: UUID
    candidate_fingerprint: Checksum
    report_fingerprint: Checksum
    revision: int = Field(ge=1, le=2147483646)
    page_version: int = Field(ge=1, le=2147483646)
    content: SourceReadCandidate = Field(repr=False)
    decision: SourceVerificationDecision

    @model_validator(mode="after")
    def exact_verified_revision(self) -> Self:
        if self.content.fingerprint != self.decision.content_fingerprint:
            raise ValueError("verified source differs from its human decision")
        if set(self.decision.reviewed_region_keys) != {
            r.key for r in self.content.observation.regions
        }:
            raise ValueError("verified source regions differ from the human comparison")
        if set(self.decision.resolved_uncertainty_keys) != {
            u.key for u in self.content.uncertainties
        }:
            raise ValueError("source uncertainty must be explicitly resolved")
        require_readable_source(self.content)
        return self

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_bytes(self)).hexdigest()


def require_readable_source(content: SourceReadCandidate) -> None:
    for region in content.observation.regions:
        if region.table is not None and any(
            cell.state == "unreadable" for cell in region.table.cells
        ):
            raise ValueError("unreadable cells require a source correction")
        if region.kind in {
            "heading",
            "paragraph",
            "instruction",
            "question",
            "worked_example",
            "equation",
            "vertical_arithmetic",
            "label",
            "page_number",
            "footer",
        } and not (
            region.exact_text.strip()
            or region.equations
            or region.table is not None
            or region.visual_facts
        ):
            raise ValueError("empty text region requires a source correction")


def verify_source_reading(
    candidate: ObservationCandidate,
    report: PageVerificationReport,
    *,
    principal: Principal,
    identifier: UUID,
    revision: int,
    page_version: int,
    compared_with_original: bool,
    reviewed_region_keys: tuple[str, ...],
    resolved_uncertainty_keys: tuple[str, ...],
    reason: str,
) -> VerifiedSourceContent:
    authorize(principal, Permission.SOURCE_TRUST)
    candidate = ObservationCandidate.model_validate(candidate)
    report = PageVerificationReport.model_validate(report)
    if candidate.content.education.claims:
        raise ValueError("source-only reading is required before verification")
    if compared_with_original is not True:
        raise ValueError("explicit original comparison is required")
    if (
        report.candidate_id != candidate.id
        or report.candidate_fingerprint != candidate.fingerprint
        or report.source_checker_version != ALGORITHM_VERSION
    ):
        raise ValueError("source verification report is stale")
    if (
        report.state == "needs_reprocessing"
        or verify_understanding(candidate).state == "needs_reprocessing"
    ):
        raise ValueError("blocking source findings require a correction")
    content = SourceReadCandidate(
        schema_version="source-read-candidate.v1",
        observation=candidate.content.observation,
        uncertainties=candidate.content.uncertainties,
    )
    decision = SourceVerificationDecision(
        actor_id=principal.subject_id,
        compared_with_original=True,
        reviewed_region_keys=reviewed_region_keys,
        resolved_uncertainty_keys=resolved_uncertainty_keys,
        content_fingerprint=content.fingerprint,
        reason=reason,
    )
    return VerifiedSourceContent(
        id=identifier,
        source=candidate.source,
        candidate_id=candidate.id,
        candidate_fingerprint=candidate.fingerprint,
        report_fingerprint=report.fingerprint,
        revision=revision,
        page_version=page_version,
        content=content,
        decision=decision,
    )
