import hashlib
import json
import unicodedata
from typing import Any, Literal, Self
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from exam_guru_api.documents.understanding_contracts import (
    EducationalUnderstanding,
    PageObservation,
    PageRegionObservation,
    PageUnderstanding,
    UnderstandingModel,
    UnderstandingUncertainty,
    _canonical_bytes,
    _canonical_json,
)
from exam_guru_api.documents.understanding_verification import (
    Checksum,
    PageArtifactIdentity,
    TrustedPageKnowledge,
)

MAX_PROJECTION_CHARACTERS = 32_768
MAX_PROJECTION_BYTES = 65_536


class KnowledgeDerivationError(ValueError):
    pass


class KnowledgeScope(UnderstandingModel):
    schema_version: Literal["knowledge-scope.v1"] = "knowledge-scope.v1"
    document_id: UUID
    source_sha256: Checksum
    metadata_scope_version: int = Field(ge=0, le=2_147_483_646)
    curriculum_version_id: UUID
    grade: int = Field(ge=1, le=13)
    medium_id: UUID
    subject_id: UUID
    catalogue_decision_id: UUID
    catalogue_version: int = Field(ge=1, le=2_147_483_646)
    catalogue_scope_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    curriculum_unit_id: UUID | None
    lesson_id: UUID | None

    @model_validator(mode="after")
    def lesson_scope(self) -> Self:
        if self.lesson_id is not None and self.curriculum_unit_id is None:
            raise KnowledgeDerivationError("a knowledge lesson requires its curriculum unit")
        return self


def _unit_id(trusted_page_id: UUID, payload: dict[str, Any]) -> UUID:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return uuid5(trusted_page_id, "knowledge-unit.v1:" + digest)


class KnowledgeUnit(UnderstandingModel):
    schema_version: Literal["knowledge-unit.v1"] = "knowledge-unit.v1"
    derivation_version: Literal["page-region-components.v1"] = "page-region-components.v1"
    id: UUID
    trusted_page_id: UUID
    trusted_revision: int = Field(ge=1, le=2_147_483_646)
    trusted_fingerprint: Checksum
    candidate_id: UUID
    source: PageArtifactIdentity
    scope: KnowledgeScope
    sequence: int = Field(ge=0, le=127)
    region_ids: tuple[UUID, ...] = Field(min_length=1, max_length=128)
    observation: PageObservation = Field(repr=False)
    education: EducationalUnderstanding = Field(repr=False)
    resolved_uncertainties: tuple[UnderstandingUncertainty, ...] = Field(max_length=128, repr=False)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_bytes(self)).hexdigest()

    @model_validator(mode="after")
    def exact_component(self) -> Self:
        if (
            self.scope.document_id != self.source.document_id
            or self.scope.source_sha256 != self.source.source_sha256
        ):
            raise KnowledgeDerivationError("knowledge scope belongs to another source")
        PageUnderstanding(
            schema_version="page-understanding.v1",
            observation=self.observation,
            education=self.education,
            uncertainties=self.resolved_uncertainties,
        )
        expected_regions = tuple(
            uuid5(self.candidate_id, region.key) for region in self.observation.regions
        )
        if self.region_ids != expected_regions:
            raise KnowledgeDerivationError(
                "knowledge region identities do not match observed evidence"
            )
        payload = self.model_dump(mode="json", exclude={"id"})
        if self.id != _unit_id(self.trusted_page_id, payload):
            raise KnowledgeDerivationError(
                "knowledge unit identity does not match its versioned payload"
            )
        return self


class KnowledgeProjection(UnderstandingModel):
    schema_version: Literal["knowledge-projection.v1"] = "knowledge-projection.v1"
    transformation_version: Literal["source-observation-meaning.v1"] = (
        "source-observation-meaning.v1"
    )
    id: UUID
    unit_id: UUID
    unit_fingerprint: Checksum
    text: str = Field(min_length=1, max_length=MAX_PROJECTION_CHARACTERS, repr=False)
    text_sha256: Checksum

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_bytes(self)).hexdigest()

    @model_validator(mode="after")
    def bound_projection(self) -> Self:
        raw = self.text.encode("utf-8")
        if len(raw) > MAX_PROJECTION_BYTES or self.text != unicodedata.normalize("NFC", self.text):
            raise KnowledgeDerivationError(
                "projection exceeds its byte bound or is not an NFC view"
            )
        if self.text_sha256 != hashlib.sha256(raw).hexdigest():
            raise KnowledgeDerivationError("projection text fingerprint is invalid")
        expected = uuid5(
            self.unit_id,
            self.transformation_version + ":" + self.unit_fingerprint + ":" + self.text_sha256,
        )
        if self.id != expected:
            raise KnowledgeDerivationError(
                "projection identity does not match its source unit and text"
            )
        return self


def derive_knowledge_units(
    trusted: TrustedPageKnowledge, scope: KnowledgeScope
) -> tuple[KnowledgeUnit, ...]:
    trusted = TrustedPageKnowledge.model_validate(trusted)
    scope = KnowledgeScope.model_validate(scope)
    if (
        scope.document_id != trusted.source.document_id
        or scope.source_sha256 != trusted.source.source_sha256
    ):
        raise KnowledgeDerivationError("knowledge scope belongs to another source")
    regions = sorted(
        trusted.observation.regions, key=lambda region: (region.reading_order, region.key)
    )
    parents = {region.key: region.key for region in regions}

    def root(key: str) -> str:
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    def connect(keys: tuple[str, ...]) -> None:
        first = root(keys[0])
        for key in keys[1:]:
            parents[root(key)] = first

    for region in regions:
        if region.parent_key is not None:
            connect((region.parent_key, region.key))
    for relationship in trusted.observation.relationships:
        if relationship.kind != "reading_next":
            connect((relationship.source_key, relationship.target_key))
    for claim in trusted.education.claims:
        connect(claim.region_keys)
    for uncertainty in trusted.resolved_uncertainties:
        connect(uncertainty.region_keys)
    components: dict[str, list[PageRegionObservation]] = {}
    for region in regions:
        components.setdefault(root(region.key), []).append(region)
    units: list[KnowledgeUnit] = []
    trusted_fingerprint = hashlib.sha256(_canonical_bytes(trusted)).hexdigest()
    for sequence, component in enumerate(components.values()):
        keys = {region.key for region in component}
        observation = PageObservation(
            language=trusted.observation.language,
            regions=tuple(component),
            relationships=tuple(
                item
                for item in trusted.observation.relationships
                if item.source_key in keys and item.target_key in keys
            ),
        )
        education = EducationalUnderstanding(
            claims=tuple(
                claim for claim in trusted.education.claims if set(claim.region_keys) <= keys
            )
        )
        uncertainties = tuple(
            item for item in trusted.resolved_uncertainties if set(item.region_keys) <= keys
        )
        payload = {
            "schema_version": "knowledge-unit.v1",
            "derivation_version": "page-region-components.v1",
            "trusted_page_id": str(trusted.id),
            "trusted_revision": trusted.revision,
            "trusted_fingerprint": trusted_fingerprint,
            "candidate_id": str(trusted.decision.candidate_id),
            "source": trusted.source.model_dump(mode="json"),
            "scope": scope.model_dump(mode="json"),
            "sequence": sequence,
            "region_ids": [
                str(uuid5(trusted.decision.candidate_id, region.key)) for region in component
            ],
            "observation": observation.model_dump(mode="json"),
            "education": education.model_dump(mode="json"),
            "resolved_uncertainties": [item.model_dump(mode="json") for item in uncertainties],
        }
        identifier = _unit_id(trusted.id, payload)
        units.append(
            KnowledgeUnit.model_validate_json(
                json.dumps({"id": str(identifier), **payload}, ensure_ascii=False)
            )
        )
    return tuple(units)


def project_knowledge_unit(unit: KnowledgeUnit) -> KnowledgeProjection:
    unit = KnowledgeUnit.model_validate(unit)
    lines = ["Observed source"]
    meaningful = False
    for region in unit.observation.regions:
        lines.append("Source region: " + region.kind)
        if region.exact_text.strip():
            lines.append(region.exact_text)
            meaningful = True
        for equation in region.equations:
            lines.append("Printed equation: " + equation)
            meaningful = True
        if region.table is not None:
            for cell in sorted(region.table.cells, key=lambda cell: (cell.row, cell.column)):
                value = cell.exact_text if cell.state == "visible" else "[" + cell.state + "]"
                lines.append(
                    f"Cell {cell.row + 1},{cell.column + 1} "
                    f"({cell.row_span}x{cell.column_span}): {value}"
                )
                meaningful |= cell.state == "visible"
        for fact in region.visual_facts:
            lines.append(fact.description)
            meaningful = True
            if fact.group_count is not None:
                lines.append(f"Visible groups: {fact.group_count}")
            if fact.items_per_group is not None:
                lines.append(f"Items per group: {fact.items_per_group}")
            if fact.printed_total is not None:
                lines.append("Printed total: " + fact.printed_total)
    if unit.education.claims:
        lines.append("Accepted educational meaning")
        meaningful = True
        lines.extend(claim.kind + ": " + claim.description for claim in unit.education.claims)
    if not meaningful:
        raise KnowledgeDerivationError("projection has no retrievable observed or accepted content")
    text = unicodedata.normalize("NFC", "\n".join(lines))
    raw = text.encode("utf-8")
    if len(text) > MAX_PROJECTION_CHARACTERS or len(raw) > MAX_PROJECTION_BYTES:
        raise KnowledgeDerivationError("projection exceeds its character or byte bound")
    digest = hashlib.sha256(raw).hexdigest()
    version = "source-observation-meaning.v1"
    return KnowledgeProjection(
        id=uuid5(unit.id, version + ":" + unit.fingerprint + ":" + digest),
        unit_id=unit.id,
        unit_fingerprint=unit.fingerprint,
        text=text,
        text_sha256=digest,
    )
