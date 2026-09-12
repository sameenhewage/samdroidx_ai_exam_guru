import hashlib
import unicodedata
from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.documents.fidelity import ALGORITHM_VERSION, _inspect_text, _latin_corruption
from exam_guru_api.documents.understanding_contracts import (
    MAX_PAGE_REGIONS,
    EducationalUnderstanding,
    Key,
    PageObservation,
    PageRegionObservation,
    PageUnderstanding,
    ShortText,
    UnderstandingModel,
    UnderstandingUncertainty,
    _canonical_bytes,
    understanding_fingerprint,
)

VERIFICATION_POLICY_VERSION: Final = "page-understanding-verification.v1"
Checksum = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class UnderstandingVerificationError(ValueError):
    pass


class PageArtifactIdentity(UnderstandingModel):
    document_id: UUID
    source_sha256: Checksum
    page_number: int = Field(ge=1, le=2147483646)
    image_sha256: Checksum


class ObservationCandidate(UnderstandingModel):
    id: UUID
    run_id: UUID
    revision: int = Field(ge=1, le=2147483646)
    method: Literal["native", "ocr", "visual_ai", "human"]
    source: PageArtifactIdentity
    content: PageUnderstanding = Field(repr=False)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_bytes(self)).hexdigest()


class SourceAnchor(UnderstandingModel):
    source: PageArtifactIdentity
    evidence_id: UUID
    region_key: Key
    kind: Literal["text", "equation", "cell", "blank_cell"]
    exact_text: str = Field(max_length=100_000, repr=False)
    row: int | None = Field(ge=0, le=63)
    column: int | None = Field(ge=0, le=63)

    @model_validator(mode="after")
    def position(self) -> Self:
        is_cell = self.kind in {"cell", "blank_cell"}
        if is_cell != (self.row is not None and self.column is not None):
            raise ValueError("source anchor cell position is invalid")
        if not is_cell and (self.row is not None or self.column is not None):
            raise ValueError("non-cell source anchor cannot carry a cell position")
        if self.kind == "blank_cell" and self.exact_text != "":
            raise ValueError("blank source anchor cannot supply an answer")
        return self


class UnderstandingFinding(UnderstandingModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,127}$")
    severity: Literal["block", "review"]
    region_keys: tuple[Key, ...] = Field(max_length=MAX_PAGE_REGIONS)
    summary: ShortText
    evidence_id: UUID | None = None


class PageVerificationReport(UnderstandingModel):
    candidate_id: UUID
    candidate_fingerprint: Checksum
    policy_version: Literal["page-understanding-verification.v1"] = VERIFICATION_POLICY_VERSION
    source_checker_version: str = Field(min_length=1, max_length=128)
    findings: tuple[UnderstandingFinding, ...] = Field(max_length=1024)
    anchor_evidence_ids: tuple[UUID, ...] = Field(default=(), max_length=512)
    anchor_fingerprint: Checksum = hashlib.sha256(b"[]").hexdigest()

    @property
    def state(self) -> Literal["needs_reprocessing", "needs_human_review"]:
        return (
            "needs_reprocessing"
            if any(item.severity == "block" for item in self.findings)
            else "needs_human_review"
        )

    @property
    def can_auto_verify(self) -> Literal[False]:
        return False

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_bytes(self)).hexdigest()


def _region_text(region: PageRegionObservation) -> tuple[str, ...]:
    cells = () if region.table is None else tuple(cell.exact_text for cell in region.table.cells)
    totals = tuple(
        fact.printed_total for fact in region.visual_facts if fact.printed_total is not None
    )
    return (region.exact_text, *region.equations, *cells, *totals)


def _anchor_matches(region: PageRegionObservation, anchor: SourceAnchor) -> bool:
    if anchor.kind == "text":
        return region.exact_text == anchor.exact_text
    if anchor.kind == "equation":
        return anchor.exact_text in region.equations
    if region.table is None:
        return False
    for cell in region.table.cells:
        if cell.row == anchor.row and cell.column == anchor.column:
            return cell.exact_text == anchor.exact_text and cell.state == (
                "blank" if anchor.kind == "blank_cell" else "visible"
            )
    return False


def verify_understanding(
    candidate: ObservationCandidate, *, anchors: tuple[SourceAnchor, ...] = ()
) -> PageVerificationReport:
    candidate = ObservationCandidate.model_validate(candidate)
    if not isinstance(anchors, tuple) or len(anchors) > 512:
        raise UnderstandingVerificationError("source anchors must be bounded")
    findings: list[UnderstandingFinding] = []
    regions = {region.key: region for region in candidate.content.observation.regions}
    observed_scripts = {"sinhala": 0, "tamil": 0, "latin": 0}
    for region in regions.values():
        unsafe = False
        for literal in _region_text(region):
            counts, risks, _, _ = _inspect_text(literal)
            for script in observed_scripts:
                observed_scripts[script] += counts[script]
            unsafe = unsafe or bool(risks) or _latin_corruption(literal)
        unsafe = unsafe or any(_inspect_text(fact.description)[1] for fact in region.visual_facts)
        if unsafe:
            findings.append(
                UnderstandingFinding(
                    code="observation.unsafe_text",
                    severity="block",
                    region_keys=(region.key,),
                    summary="Source text contains unresolved encoding or script corruption.",
                )
            )
    findings.extend(
        UnderstandingFinding(
            code="understanding.unsafe_text",
            severity="block",
            region_keys=claim.region_keys,
            summary="Interpretation contains invalid Unicode or script sequences.",
        )
        for claim in candidate.content.education.claims
        if _inspect_text(claim.description)[1]
    )
    required_script = {"si": "sinhala", "ta": "tamil"}.get(candidate.content.observation.language)
    if required_script and observed_scripts["latin"] and not observed_scripts[required_script]:
        findings.append(
            UnderstandingFinding(
                code="observation.source_script_missing",
                severity="block",
                region_keys=tuple(regions),
                summary="The declared source language is not represented in the observed text.",
            )
        )
    validated_anchors = tuple(SourceAnchor.model_validate(value) for value in anchors)
    for anchor in validated_anchors:
        if anchor.source != candidate.source:
            raise UnderstandingVerificationError(
                "source evidence belongs to another original or image"
            )
        target_region = regions.get(anchor.region_key)
        if target_region is None or not _anchor_matches(target_region, anchor):
            findings.append(
                UnderstandingFinding(
                    code="observation.anchor_mismatch",
                    severity="block",
                    region_keys=() if target_region is None else (target_region.key,),
                    summary="An observed value differs from independently bound source evidence.",
                    evidence_id=anchor.evidence_id,
                )
            )
    findings.append(
        UnderstandingFinding(
            code="understanding.original_comparison_required",
            severity="review",
            region_keys=tuple(regions),
            summary=(
                "Compare all observations and accepted educational meaning with the original. "
                "Provider agreement is not verification."
            ),
        )
    )
    return PageVerificationReport(
        candidate_id=candidate.id,
        candidate_fingerprint=candidate.fingerprint,
        source_checker_version=ALGORITHM_VERSION,
        findings=tuple(findings),
        anchor_evidence_ids=tuple(sorted({anchor.evidence_id for anchor in validated_anchors})),
        anchor_fingerprint=hashlib.sha256(
            b"[" + b",".join(_canonical_bytes(anchor) for anchor in validated_anchors) + b"]"
        ).hexdigest(),
    )


class PageVerificationDecision(UnderstandingModel):
    id: UUID
    actor_id: UUID
    source: PageArtifactIdentity
    candidate_id: UUID
    candidate_fingerprint: Checksum
    report_fingerprint: Checksum
    verified_content_fingerprint: Checksum
    policy_version: Literal["page-understanding-verification.v1"] = VERIFICATION_POLICY_VERSION
    source_checker_version: str = Field(min_length=1, max_length=128)
    compared_with_original: Literal[True]
    reviewed_region_keys: tuple[Key, ...] = Field(min_length=1, max_length=MAX_PAGE_REGIONS)
    accepted_claim_keys: tuple[Key, ...] = Field(max_length=128)
    resolved_uncertainty_keys: tuple[Key, ...] = Field(max_length=128)
    reason: ShortText

    @model_validator(mode="after")
    def safe_decision(self) -> Self:
        if (
            not self.reason.strip()
            or self.reason != self.reason.strip()
            or any(unicodedata.category(character) in {"Cc", "Cs"} for character in self.reason)
        ):
            raise ValueError("verification reason must be meaningful clean text")
        for keys in (
            self.reviewed_region_keys,
            self.accepted_claim_keys,
            self.resolved_uncertainty_keys,
        ):
            if len(keys) != len(set(keys)):
                raise ValueError("verification decision keys must be unique")
        return self


class TrustedPageKnowledge(UnderstandingModel):
    id: UUID
    source: PageArtifactIdentity
    revision: int = Field(ge=1, le=2147483646)
    observation: PageObservation = Field(repr=False)
    education: EducationalUnderstanding = Field(repr=False)
    resolved_uncertainties: tuple[UnderstandingUncertainty, ...] = Field(max_length=128, repr=False)
    decision: PageVerificationDecision

    @model_validator(mode="after")
    def verified_snapshot(self) -> Self:
        content = PageUnderstanding(
            schema_version="page-understanding.v1",
            observation=self.observation,
            education=self.education,
            uncertainties=self.resolved_uncertainties,
        )
        if (
            self.source != self.decision.source
            or self.id != self.decision.id
            or understanding_fingerprint(content) != self.decision.verified_content_fingerprint
        ):
            raise ValueError("trusted page snapshot must match its verification decision")
        if set(self.decision.reviewed_region_keys) != {
            region.key for region in self.observation.regions
        }:
            raise ValueError("trusted page regions differ from the reviewed regions")
        if set(self.decision.accepted_claim_keys) != {claim.key for claim in self.education.claims}:
            raise ValueError("trusted education differs from accepted claims")
        if set(self.decision.resolved_uncertainty_keys) != {
            item.key for item in self.resolved_uncertainties
        }:
            raise ValueError("trusted page uncertainty resolution differs")
        return self


def accept_trusted_page(
    candidate: ObservationCandidate,
    report: PageVerificationReport,
    *,
    principal: Principal,
    decision_id: UUID,
    revision: int,
    reason: str,
    compared_with_original: bool,
    reviewed_region_keys: tuple[str, ...],
    accepted_claim_keys: tuple[str, ...],
    resolved_uncertainty_keys: tuple[str, ...],
) -> TrustedPageKnowledge:
    authorize(principal, Permission.SOURCE_TRUST)
    candidate = ObservationCandidate.model_validate(candidate)
    report = PageVerificationReport.model_validate(report)
    if (
        report.candidate_id != candidate.id
        or report.candidate_fingerprint != candidate.fingerprint
        or report.source_checker_version != ALGORITHM_VERSION
    ):
        raise UnderstandingVerificationError("verification report is stale")
    if (
        report.state == "needs_reprocessing"
        or verify_understanding(candidate).state == "needs_reprocessing"
    ):
        raise UnderstandingVerificationError(
            "blocking source findings require a corrected candidate"
        )
    if compared_with_original is not True:
        raise UnderstandingVerificationError("explicit original comparison is required")
    region_keys = {region.key for region in candidate.content.observation.regions}
    claims = {claim.key: claim for claim in candidate.content.education.claims}
    uncertainties = {item.key for item in candidate.content.uncertainties}
    for name, selected, required, complete in (
        ("regions", reviewed_region_keys, region_keys, True),
        ("claims", accepted_claim_keys, set(claims), False),
        ("uncertainty", resolved_uncertainty_keys, uncertainties, True),
    ):
        if (
            not isinstance(selected, tuple)
            or len(set(selected)) != len(selected)
            or not set(selected) <= required
            or (complete and set(selected) != required)
        ):
            raise UnderstandingVerificationError(f"reviewed {name} do not match the candidate")
    accepted = EducationalUnderstanding(
        claims=tuple(
            claim
            for claim in candidate.content.education.claims
            if claim.key in accepted_claim_keys
        )
    )
    verified = PageUnderstanding(
        schema_version="page-understanding.v1",
        observation=candidate.content.observation,
        education=accepted,
        uncertainties=candidate.content.uncertainties,
    )
    decision = PageVerificationDecision(
        id=decision_id,
        actor_id=principal.subject_id,
        source=candidate.source,
        candidate_id=candidate.id,
        candidate_fingerprint=candidate.fingerprint,
        report_fingerprint=report.fingerprint,
        source_checker_version=report.source_checker_version,
        verified_content_fingerprint=understanding_fingerprint(verified),
        compared_with_original=True,
        reviewed_region_keys=reviewed_region_keys,
        accepted_claim_keys=accepted_claim_keys,
        resolved_uncertainty_keys=resolved_uncertainty_keys,
        reason=reason,
    )
    return TrustedPageKnowledge(
        id=decision.id,
        source=candidate.source,
        revision=revision,
        observation=verified.observation,
        education=verified.education,
        resolved_uncertainties=verified.uncertainties,
        decision=decision,
    )
