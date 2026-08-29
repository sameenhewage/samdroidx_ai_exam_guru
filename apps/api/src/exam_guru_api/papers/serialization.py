import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID

from exam_guru_api.papers.domain import (
    _PUBLISH_CAPABILITY,
    CandidateInvariantError,
    CandidateRevision,
    CandidateState,
    GenerationLineage,
    PaperAssemblyError,
    PaperBlueprintReference,
    PublishedPaperSnapshot,
    PublishedQuestion,
    QuestionContent,
    QuestionOption,
    ReviewAction,
    ReviewDecision,
    ReviewRecord,
    SourceProvenance,
    ValidationEvidence,
    _published_content_payload,
)

MAX_PUBLISHED_SNAPSHOT_BYTES = 32 * 1024 * 1024


class PaperSnapshotIntegrityError(ValueError):
    def __init__(self, path: str, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"invalid published paper snapshot at {path}: {detail}")


def canonical_publication_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PaperSnapshotIntegrityError("snapshot", "must be canonical JSON data") from error


def publication_content_hash(value: object) -> str:
    return hashlib.sha256(canonical_publication_bytes(value)).hexdigest()


def serialize_published_snapshot(snapshot: PublishedPaperSnapshot) -> dict[str, object]:
    if not isinstance(snapshot, PublishedPaperSnapshot):
        raise TypeError("snapshot must be PublishedPaperSnapshot")
    payload = _published_content_payload(snapshot)
    serialized = canonical_publication_bytes(payload)
    if len(serialized) > MAX_PUBLISHED_SNAPSHOT_BYTES:
        raise PaperSnapshotIntegrityError(
            "snapshot",
            f"cannot exceed {MAX_PUBLISHED_SNAPSHOT_BYTES} UTF-8 bytes",
        )
    if not hmac.compare_digest(hashlib.sha256(serialized).hexdigest(), snapshot.content_hash):
        raise PaperSnapshotIntegrityError("content_hash", "does not match domain content")
    return payload


def reconstruct_published_snapshot(
    value: object,
    *,
    content_hash: str,
    published_by: UUID,
    previous_version: int | None,
    supersedes_content_hash: str | None,
) -> PublishedPaperSnapshot:
    serialized = canonical_publication_bytes(value)
    if len(serialized) > MAX_PUBLISHED_SNAPSHOT_BYTES:
        raise PaperSnapshotIntegrityError(
            "snapshot",
            f"cannot exceed {MAX_PUBLISHED_SNAPSHOT_BYTES} UTF-8 bytes",
        )
    if not isinstance(content_hash, str) or not hmac.compare_digest(
        hashlib.sha256(serialized).hexdigest(), content_hash
    ):
        raise PaperSnapshotIntegrityError("content_hash", "does not match canonical snapshot")
    try:
        root = _object(
            value,
            "snapshot",
            frozenset({"blueprint", "paper_id", "paper_version", "questions", "schema", "title"}),
        )
        if _string(root["schema"], "snapshot.schema") != "published-paper.v1":
            raise PaperSnapshotIntegrityError("snapshot.schema", "unsupported schema version")
        blueprint = _blueprint(root["blueprint"])
        questions = tuple(
            _question(item, f"snapshot.questions[{index}]")
            for index, item in enumerate(_array(root["questions"], "snapshot.questions"))
        )
        publication = PublishedPaperSnapshot(
            paper_id=_uuid(root["paper_id"], "snapshot.paper_id"),
            version=_integer(root["paper_version"], "snapshot.paper_version"),
            title=_string(root["title"], "snapshot.title"),
            blueprint=blueprint,
            questions=questions,
            published_by=published_by,
            previous_version=previous_version,
            supersedes_content_hash=supersedes_content_hash,
            _service_capability=_PUBLISH_CAPABILITY,
        )
    except PaperSnapshotIntegrityError:
        raise
    except (CandidateInvariantError, PaperAssemblyError, TypeError, ValueError) as error:
        raise PaperSnapshotIntegrityError("snapshot", str(error)) from error
    if _published_content_payload(publication) != root:
        raise PaperSnapshotIntegrityError("snapshot", "does not round-trip exactly")
    if not hmac.compare_digest(publication.content_hash, content_hash):
        raise PaperSnapshotIntegrityError("content_hash", "does not match reconstructed domain")
    return publication


def _object(value: object, path: str, keys: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise PaperSnapshotIntegrityError(path, "must be an object with string keys")
    actual = frozenset(cast(str, key) for key in value)
    if actual != keys:
        missing = sorted(keys - actual)
        unexpected = sorted(actual - keys)
        raise PaperSnapshotIntegrityError(
            path,
            f"object keys mismatch (missing={missing}, unexpected={unexpected})",
        )
    return cast(Mapping[str, object], value)


def _array(value: object, path: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise PaperSnapshotIntegrityError(path, "must be an array")
    return cast(Sequence[object], value)


def _nonempty_array(value: object, path: str) -> Sequence[object]:
    items = _array(value, path)
    if not items:
        raise PaperSnapshotIntegrityError(path, "must not be empty")
    return items


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise PaperSnapshotIntegrityError(path, "must be a string")
    return value


def _optional_string(value: object, path: str) -> str | None:
    return None if value is None else _string(value, path)


def _integer(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PaperSnapshotIntegrityError(path, "must be an integer")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise PaperSnapshotIntegrityError(path, "must be a boolean")
    return value


def _uuid(value: object, path: str) -> UUID:
    try:
        return UUID(_string(value, path))
    except ValueError as error:
        raise PaperSnapshotIntegrityError(path, "must be a UUID") from error


def _optional_uuid(value: object, path: str) -> UUID | None:
    return None if value is None else _uuid(value, path)


def _blueprint(value: object) -> PaperBlueprintReference:
    path = "snapshot.blueprint"
    root = _object(
        value,
        path,
        frozenset({"blueprint_id", "blueprint_version", "paper_blueprint_id", "slot_ids"}),
    )
    return PaperBlueprintReference(
        blueprint_id=_string(root["blueprint_id"], f"{path}.blueprint_id"),
        blueprint_version=_string(root["blueprint_version"], f"{path}.blueprint_version"),
        slot_ids=tuple(
            _string(item, f"{path}.slot_ids[{index}]")
            for index, item in enumerate(_nonempty_array(root["slot_ids"], f"{path}.slot_ids"))
        ),
        paper_blueprint_id=_optional_uuid(root["paper_blueprint_id"], f"{path}.paper_blueprint_id"),
    )


def _content(value: object, path: str) -> QuestionContent:
    keys = {
        "answer",
        "explanation",
        "marking_guide",
        "marks",
        "options",
        "question_type",
        "stem",
    }
    if isinstance(value, Mapping) and "marking_point_marks" in value:
        keys.add("marking_point_marks")
    root = _object(value, path, frozenset(keys))
    options = tuple(
        _option(item, f"{path}.options[{index}]")
        for index, item in enumerate(_array(root["options"], f"{path}.options"))
    )
    return QuestionContent(
        question_type=_string(root["question_type"], f"{path}.question_type"),
        stem=_string(root["stem"], f"{path}.stem"),
        options=options,
        answer=_string(root["answer"], f"{path}.answer"),
        explanation=_string(root["explanation"], f"{path}.explanation"),
        marks=_integer(root["marks"], f"{path}.marks"),
        marking_guide=tuple(
            _string(item, f"{path}.marking_guide[{index}]")
            for index, item in enumerate(
                _nonempty_array(root["marking_guide"], f"{path}.marking_guide")
            )
        ),
        marking_point_marks=tuple(
            _integer(item, f"{path}.marking_point_marks[{index}]")
            for index, item in enumerate(
                _nonempty_array(root["marking_point_marks"], f"{path}.marking_point_marks")
            )
        )
        if "marking_point_marks" in root
        else (),
    )


def _option(value: object, path: str) -> QuestionOption:
    root = _object(value, path, frozenset({"option_id", "text"}))
    return QuestionOption(
        option_id=_string(root["option_id"], f"{path}.option_id"),
        text=_string(root["text"], f"{path}.text"),
    )


def _lineage(value: object, path: str) -> GenerationLineage:
    root = _object(
        value,
        path,
        frozenset(
            {
                "blueprint_id",
                "blueprint_slot_id",
                "blueprint_version",
                "generation_attempt_id",
                "generation_id",
                "model_version",
                "prompt_version",
                "provenance",
                "provider",
                "retrieval_version",
                "schema_version",
            }
        ),
    )
    return GenerationLineage(
        generation_id=_uuid(root["generation_id"], f"{path}.generation_id"),
        generation_attempt_id=_uuid(root["generation_attempt_id"], f"{path}.generation_attempt_id"),
        blueprint_id=_string(root["blueprint_id"], f"{path}.blueprint_id"),
        blueprint_version=_string(root["blueprint_version"], f"{path}.blueprint_version"),
        blueprint_slot_id=_string(root["blueprint_slot_id"], f"{path}.blueprint_slot_id"),
        prompt_version=_string(root["prompt_version"], f"{path}.prompt_version"),
        provider=_string(root["provider"], f"{path}.provider"),
        model_version=_string(root["model_version"], f"{path}.model_version"),
        retrieval_version=_string(root["retrieval_version"], f"{path}.retrieval_version"),
        schema_version=_string(root["schema_version"], f"{path}.schema_version"),
        provenance=tuple(
            _provenance(item, f"{path}.provenance[{index}]")
            for index, item in enumerate(_nonempty_array(root["provenance"], f"{path}.provenance"))
        ),
    )


def _provenance(value: object, path: str) -> SourceProvenance:
    root = _object(
        value,
        path,
        frozenset({"chunk_id", "page_number", "source_document_id", "source_version"}),
    )
    return SourceProvenance(
        source_document_id=_string(root["source_document_id"], f"{path}.source_document_id"),
        source_version=_string(root["source_version"], f"{path}.source_version"),
        page_number=_integer(root["page_number"], f"{path}.page_number"),
        chunk_id=_string(root["chunk_id"], f"{path}.chunk_id"),
    )


def _validation(value: object, path: str) -> ValidationEvidence:
    root = _object(
        value,
        path,
        frozenset(
            {
                "finding_refs",
                "passed",
                "validated_revision",
                "validation_run_id",
                "validator_version",
            }
        ),
    )
    return ValidationEvidence(
        validation_run_id=_uuid(root["validation_run_id"], f"{path}.validation_run_id"),
        validator_version=_string(root["validator_version"], f"{path}.validator_version"),
        finding_refs=tuple(
            _string(item, f"{path}.finding_refs[{index}]")
            for index, item in enumerate(
                _nonempty_array(root["finding_refs"], f"{path}.finding_refs")
            )
        ),
        passed=_boolean(root["passed"], f"{path}.passed"),
        validated_revision=_integer(root["validated_revision"], f"{path}.validated_revision"),
    )


def _revision(value: object, path: str) -> CandidateRevision:
    root = _object(
        value,
        path,
        frozenset({"content", "reason", "reviewer_id", "revision"}),
    )
    return CandidateRevision(
        revision=_integer(root["revision"], f"{path}.revision"),
        content=_content(root["content"], f"{path}.content"),
        reviewer_id=_optional_uuid(root["reviewer_id"], f"{path}.reviewer_id"),
        reason=_optional_string(root["reason"], f"{path}.reason"),
    )


def _review_record(value: object, path: str) -> ReviewRecord:
    root = _object(
        value,
        path,
        frozenset({"action", "candidate_version", "reason", "reviewer_id"}),
    )
    try:
        action = ReviewAction(_string(root["action"], f"{path}.action"))
    except ValueError as error:
        raise PaperSnapshotIntegrityError(f"{path}.action", "invalid review action") from error
    return ReviewRecord(
        action=action,
        reviewer_id=_uuid(root["reviewer_id"], f"{path}.reviewer_id"),
        candidate_version=_integer(root["candidate_version"], f"{path}.candidate_version"),
        reason=_optional_string(root["reason"], f"{path}.reason"),
    )


def _decision(value: object, path: str) -> ReviewDecision:
    root = _object(
        value,
        path,
        frozenset({"candidate_version", "reason", "reviewer_id", "state"}),
    )
    try:
        state = CandidateState(_string(root["state"], f"{path}.state"))
    except ValueError as error:
        raise PaperSnapshotIntegrityError(f"{path}.state", "invalid candidate state") from error
    return ReviewDecision(
        state=state,
        reviewer_id=_uuid(root["reviewer_id"], f"{path}.reviewer_id"),
        candidate_version=_integer(root["candidate_version"], f"{path}.candidate_version"),
        reason=_optional_string(root["reason"], f"{path}.reason"),
    )


def _question(value: object, path: str) -> PublishedQuestion:
    root = _object(
        value,
        path,
        frozenset(
            {
                "candidate_id",
                "candidate_version",
                "content",
                "content_revision",
                "decision",
                "lineage",
                "review_history",
                "revisions",
                "slot_id",
                "validation",
            }
        ),
    )
    revisions = tuple(
        _revision(item, f"{path}.revisions[{index}]")
        for index, item in enumerate(_nonempty_array(root["revisions"], f"{path}.revisions"))
    )
    content_revision = _integer(root["content_revision"], f"{path}.content_revision")
    if revisions[-1].revision != content_revision:
        raise PaperSnapshotIntegrityError(
            f"{path}.content_revision", "must identify the final revision"
        )
    return PublishedQuestion(
        candidate_id=_uuid(root["candidate_id"], f"{path}.candidate_id"),
        candidate_version=_integer(root["candidate_version"], f"{path}.candidate_version"),
        slot_id=_string(root["slot_id"], f"{path}.slot_id"),
        content=_content(root["content"], f"{path}.content"),
        lineage=_lineage(root["lineage"], f"{path}.lineage"),
        validation=_validation(root["validation"], f"{path}.validation"),
        revisions=revisions,
        review_history=tuple(
            _review_record(item, f"{path}.review_history[{index}]")
            for index, item in enumerate(
                _nonempty_array(root["review_history"], f"{path}.review_history")
            )
        ),
        decision=_decision(root["decision"], f"{path}.decision"),
    )
