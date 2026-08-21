"""Aggregate root for the locked SI-02 Source Evidence contract."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from ._evidence_components import (
    ProviderTranscriptCandidate,
    SourceEvidenceMetadata,
    ValidatedAudioEvidence,
)
from ._evidence_provenance import SourceAcquisitionProvenance, SourceEvidenceDiagnostic
from ._evidence_validation import (
    SI02InvalidFieldError,
    SI02InvariantError,
    SI02MalformedDataError,
    SI02SizeLimitError,
    canonical_json_bytes,
    component_version,
    digest_sha256,
    logical_component_id,
    parse_canonical_json,
    schema_version,
)
from .source_evidence import (
    SourceAcquisitionMethod,
    SourceAcquisitionProvenanceRole,
    SourceComponentAvailability,
    SourceEvidenceDiagnosticCode,
    SourceEvidenceDiagnosticSeverity,
    SourceEvidenceDiagnosticSubject,
)
from .source_identity import CanonicalSourceIdentity, SourceObservationIdentity


_ARTIFACT_LIMIT = 512 * 1024
_COLLECTION_LIMIT = 256 * 1024


def _qualified_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _enum(value: Any, enum_type: type, field: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise SI02InvalidFieldError(f"Unsupported {field}: {value!r}.") from exc


def _component_summary(
    source_identity: CanonicalSourceIdentity,
    observation_identity: SourceObservationIdentity,
    metadata_availability: SourceComponentAvailability,
    transcript_availability: SourceComponentAvailability,
    audio_availability: SourceComponentAvailability,
    metadata: SourceEvidenceMetadata | None,
    candidates: tuple[ProviderTranscriptCandidate, ...],
    audio: ValidatedAudioEvidence | None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "audio_availability": audio_availability.value,
        "metadata_availability": metadata_availability.value,
        "observation_identity": observation_identity.to_dict(),
        "provider_transcript_candidates": [item.to_dict() for item in candidates],
        "source_identity": source_identity.to_dict(),
        "summary_schema_version": 1,
        "transcript_availability": transcript_availability.value,
    }
    if metadata is not None:
        value["metadata"] = metadata.to_dict()
    if audio is not None:
        value["audio_evidence"] = audio.to_dict()
    return value


def _semantic_preimage(
    *,
    schema_version: int,
    source_identity: CanonicalSourceIdentity,
    observation_identity: SourceObservationIdentity,
    producer_id: str,
    producer_version: str,
    metadata_availability: SourceComponentAvailability,
    transcript_availability: SourceComponentAvailability,
    audio_availability: SourceComponentAvailability,
    metadata: SourceEvidenceMetadata | None,
    candidates: tuple[ProviderTranscriptCandidate, ...],
    audio: ValidatedAudioEvidence | None,
    provenance: tuple[SourceAcquisitionProvenance, ...],
    aggregate_provenance_ref: str,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "aggregate_provenance_ref": aggregate_provenance_ref,
        "audio_availability": audio_availability.value,
        "metadata_availability": metadata_availability.value,
        "observation_identity": observation_identity.to_dict(),
        "producer_id": producer_id,
        "producer_version": producer_version,
        "provider_transcript_candidates": [item.to_dict() for item in candidates],
        "provenance": [item.to_dict() for item in provenance],
        "schema_version": schema_version,
        "source_identity": source_identity.to_dict(),
        "transcript_availability": transcript_availability.value,
    }
    if metadata is not None:
        value["metadata"] = metadata.to_dict()
    if audio is not None:
        value["audio_evidence"] = audio.to_dict()
    return value


@dataclass(frozen=True)
class AcquiredSourceEvidence:
    schema_version: int
    artifact_identity: str
    source_identity: CanonicalSourceIdentity
    observation_identity: SourceObservationIdentity
    producer_id: str
    producer_version: str
    metadata_availability: SourceComponentAvailability
    transcript_availability: SourceComponentAvailability
    audio_availability: SourceComponentAvailability
    metadata: SourceEvidenceMetadata | None
    provider_transcript_candidates: tuple[ProviderTranscriptCandidate, ...]
    audio_evidence: ValidatedAudioEvidence | None
    provenance: tuple[SourceAcquisitionProvenance, ...]
    aggregate_provenance_ref: str
    diagnostics: tuple[SourceEvidenceDiagnostic, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", schema_version(self.schema_version))
        object.__setattr__(
            self, "artifact_identity", digest_sha256(self.artifact_identity, "artifact_identity")
        )
        if not isinstance(self.source_identity, CanonicalSourceIdentity):
            raise SI02InvalidFieldError("source_identity must be a CanonicalSourceIdentity.")
        if not isinstance(self.observation_identity, SourceObservationIdentity):
            raise SI02InvalidFieldError(
                "observation_identity must be a SourceObservationIdentity."
            )
        if self.observation_identity.source_identity != self.source_identity:
            raise SI02InvariantError(
                "observation_identity.source_identity must equal source_identity."
            )
        object.__setattr__(self, "producer_id", logical_component_id(self.producer_id, "producer_id"))
        object.__setattr__(
            self, "producer_version", component_version(self.producer_version, "producer_version")
        )
        for field in (
            "metadata_availability", "transcript_availability", "audio_availability"
        ):
            object.__setattr__(
                self, field, _enum(getattr(self, field), SourceComponentAvailability, field)
            )
        self._validate_components()
        self._validate_provenance()
        expected_identity = _qualified_sha256(self.semantic_preimage())
        if self.artifact_identity != expected_identity:
            raise SI02InvariantError(
                "artifact_identity must equal SHA-256 of the canonical semantic preimage."
            )
        self._validate_diagnostics()
        self.canonical_bytes()

    def _validate_components(self) -> None:
        if self.metadata is not None and not isinstance(self.metadata, SourceEvidenceMetadata):
            raise SI02InvalidFieldError("metadata must be SourceEvidenceMetadata or absent.")
        if type(self.provider_transcript_candidates) is not tuple:
            raise SI02InvalidFieldError("provider_transcript_candidates must be an immutable tuple.")
        if type(self.provenance) is not tuple:
            raise SI02InvalidFieldError("provenance must be an immutable tuple.")
        if type(self.diagnostics) is not tuple:
            raise SI02InvalidFieldError("diagnostics must be an immutable tuple.")
        if self.audio_evidence is not None and not isinstance(
            self.audio_evidence, ValidatedAudioEvidence
        ):
            raise SI02InvalidFieldError("audio_evidence must be ValidatedAudioEvidence or absent.")

        candidates = self.provider_transcript_candidates
        if len(candidates) > 32 or any(
            not isinstance(item, ProviderTranscriptCandidate) for item in candidates
        ):
            raise SI02InvalidFieldError("provider_transcript_candidates must contain at most 32 valid candidates.")
        if candidates != tuple(sorted(candidates, key=ProviderTranscriptCandidate.canonical_order_key)):
            raise SI02InvariantError("provider_transcript_candidates must use canonical order.")
        candidate_keys = [
            (item.candidate_id, item.language_hint, item.candidate_kind) for item in candidates
        ]
        if len(set(candidate_keys)) != len(candidate_keys):
            raise SI02InvariantError("Provider transcript candidate identities must be unique.")
        digests = [item.evidence_digest for item in candidates]
        if len(set(digests)) != len(digests):
            raise SI02InvariantError("Provider transcript candidate evidence digests must be unique.")

        matrix = (
            (self.metadata_availability, self.metadata is not None, "metadata"),
            (self.transcript_availability, bool(candidates), "provider transcript candidates"),
            (self.audio_availability, self.audio_evidence is not None, "audio evidence"),
        )
        for availability, present, label in matrix:
            if (availability is SourceComponentAvailability.AVAILABLE) != present:
                raise SI02InvariantError(f"{label} availability does not match its value.")

        nested = tuple(candidates) + tuple(
            item for item in (self.audio_evidence,) if item is not None
        )
        for item in nested:
            if item.source_identity != self.source_identity or item.observation_identity != self.observation_identity:
                raise SI02InvariantError("Every component must use the aggregate identities.")

    def _validate_provenance(self) -> None:
        records = self.provenance
        if not 1 <= len(records) <= 64:
            raise SI02InvariantError("provenance must contain 1..64 records.")
        if any(not isinstance(item, SourceAcquisitionProvenance) for item in records):
            raise SI02InvalidFieldError("provenance must contain valid provenance records.")
        if records != tuple(sorted(records, key=lambda item: item.provenance_id.encode("ascii"))):
            raise SI02InvariantError("provenance records must be sorted by provenance_id.")
        ids = [item.provenance_id for item in records]
        if len(set(ids)) != len(ids):
            raise SI02InvariantError("provenance IDs must be unique.")
        if len({item.canonical_bytes() for item in records}) != len(records):
            raise SI02InvariantError("Duplicate canonical provenance records are invalid.")
        try:
            canonical_json_bytes(
                [item.to_dict() for item in records], maximum_bytes=_COLLECTION_LIMIT
            )
        except SI02SizeLimitError as exc:
            raise SI02InvariantError(
                "Aggregate canonical provenance bytes exceed 256 KiB."
            ) from exc
        by_id = {item.provenance_id: item for item in records}
        for record in records:
            if record.source_identity != self.source_identity or record.observation_identity != self.observation_identity:
                raise SI02InvariantError("Every provenance record must use the aggregate identities.")
            if any(parent not in by_id for parent in record.parent_refs):
                raise SI02InvariantError("Every provenance parent_ref must resolve exactly once.")

        aggregate_records = [
            item for item in records if item.role is SourceAcquisitionProvenanceRole.AGGREGATE
        ]
        if len(aggregate_records) != 1:
            raise SI02InvariantError("Exactly one AGGREGATE provenance record is required.")
        root = aggregate_records[0]
        if self.aggregate_provenance_ref != root.provenance_id:
            raise SI02InvariantError("aggregate_provenance_ref must resolve to the AGGREGATE record.")
        referenced = {parent for item in records for parent in item.parent_refs}
        roots = [item.provenance_id for item in records if item.provenance_id not in referenced]
        if roots != [root.provenance_id]:
            raise SI02InvariantError("The AGGREGATE record must be the unique provenance graph root.")

        state: dict[str, int] = {}
        reached: set[str] = set()

        def visit(record_id: str, depth: int) -> None:
            if depth > 32:
                raise SI02InvariantError("Provenance graph depth must not exceed 32 records.")
            if state.get(record_id) == 1:
                raise SI02InvariantError("Provenance graph must be acyclic.")
            if state.get(record_id) == 2:
                reached.add(record_id)
                return
            state[record_id] = 1
            reached.add(record_id)
            for parent in by_id[record_id].parent_refs:
                visit(parent, depth + 1)
            state[record_id] = 2

        visit(root.provenance_id, 1)
        if reached != set(ids):
            raise SI02InvariantError("Every provenance record must be reachable from the AGGREGATE root.")

        depths: dict[str, int] = {}

        def graph_depth(record_id: str) -> int:
            if record_id not in depths:
                parents = by_id[record_id].parent_refs
                depths[record_id] = 1 + max(
                    (graph_depth(parent) for parent in parents), default=0
                )
            return depths[record_id]

        if graph_depth(root.provenance_id) > 32:
            raise SI02InvariantError("Provenance graph depth must not exceed 32 records.")

        direct: list[tuple[str, SourceAcquisitionProvenanceRole, str]] = []
        if self.metadata is not None:
            direct.append((self.metadata.provenance_ref, SourceAcquisitionProvenanceRole.METADATA, self.metadata.evidence_digest))
        direct.extend(
            (item.provenance_ref, SourceAcquisitionProvenanceRole.PROVIDER_TRANSCRIPT_CANDIDATE, item.evidence_digest)
            for item in self.provider_transcript_candidates
        )
        if self.audio_evidence is not None:
            direct.append((self.audio_evidence.provenance_ref, SourceAcquisitionProvenanceRole.AUDIO, self.audio_evidence.content_digest))
        expected_parents = tuple(sorted({item[0] for item in direct}, key=str.encode))
        if root.parent_refs != expected_parents:
            raise SI02InvariantError("AGGREGATE parent_refs must equal the direct component provenance refs.")
        for reference, role, evidence_digest in direct:
            record = by_id.get(reference)
            if record is None or record.role is not role or record.evidence_digest != evidence_digest:
                raise SI02InvariantError("Component provenance role or evidence digest does not agree.")

        expected_summary = _qualified_sha256(
            _component_summary(
                self.source_identity,
                self.observation_identity,
                self.metadata_availability,
                self.transcript_availability,
                self.audio_availability,
                self.metadata,
                self.provider_transcript_candidates,
                self.audio_evidence,
            )
        )
        if root.evidence_digest != expected_summary:
            raise SI02InvariantError("AGGREGATE evidence_digest must equal the component summary digest.")

    def _validate_diagnostics(self) -> None:
        diagnostics = self.diagnostics
        if len(diagnostics) > 256 or any(
            not isinstance(item, SourceEvidenceDiagnostic) for item in diagnostics
        ):
            raise SI02InvalidFieldError("diagnostics must contain at most 256 valid diagnostics.")
        if diagnostics != tuple(sorted(diagnostics, key=SourceEvidenceDiagnostic.canonical_order_key)):
            raise SI02InvariantError("diagnostics must use canonical order.")
        keys = [item.uniqueness_key() for item in diagnostics]
        if len(set(keys)) != len(keys):
            raise SI02InvariantError("Diagnostic uniqueness keys must be unique.")
        try:
            canonical_json_bytes(
                [item.to_dict() for item in diagnostics], maximum_bytes=_COLLECTION_LIMIT
            )
        except SI02SizeLimitError as exc:
            raise SI02InvariantError(
                "Aggregate canonical diagnostic bytes exceed 256 KiB."
            ) from exc
        provenance_by_id = {item.provenance_id: item for item in self.provenance}
        candidate_digests = {item.evidence_digest for item in self.provider_transcript_candidates}
        availability = {
            SourceEvidenceDiagnosticSubject.METADATA: self.metadata_availability,
            SourceEvidenceDiagnosticSubject.PROVIDER_TRANSCRIPT_CANDIDATE: self.transcript_availability,
            SourceEvidenceDiagnosticSubject.AUDIO_EVIDENCE: self.audio_availability,
        }
        availability_codes = {
            SourceComponentAvailability.AVAILABLE: SourceEvidenceDiagnosticCode.COMPONENT_AVAILABLE,
            SourceComponentAvailability.UNAVAILABLE: SourceEvidenceDiagnosticCode.COMPONENT_UNAVAILABLE,
            SourceComponentAvailability.UNKNOWN: SourceEvidenceDiagnosticCode.COMPONENT_UNKNOWN,
            SourceComponentAvailability.NOT_REQUESTED: SourceEvidenceDiagnosticCode.COMPONENT_NOT_REQUESTED,
        }
        availability_code_set = set(availability_codes.values())
        for item in diagnostics:
            subject, code, reference = item.subject, item.code, item.component_ref
            if subject is SourceEvidenceDiagnosticSubject.EVIDENCE:
                valid_ref = reference == self.artifact_identity
            elif subject is SourceEvidenceDiagnosticSubject.METADATA:
                valid_ref = reference == (self.metadata.evidence_digest if self.metadata else None)
            elif subject is SourceEvidenceDiagnosticSubject.PROVIDER_TRANSCRIPT_CANDIDATE:
                valid_ref = reference in candidate_digests if candidate_digests else reference is None
            elif subject is SourceEvidenceDiagnosticSubject.AUDIO_EVIDENCE:
                valid_ref = reference == (self.audio_evidence.content_digest if self.audio_evidence else None)
            else:
                valid_ref = reference in provenance_by_id
            if not valid_ref:
                raise SI02InvariantError("Diagnostic component_ref is incompatible with its subject.")
            if reference is None and not (
                subject in availability
                and availability[subject] is not SourceComponentAvailability.AVAILABLE
                and code in {
                    SourceEvidenceDiagnosticCode.COMPONENT_UNAVAILABLE,
                    SourceEvidenceDiagnosticCode.COMPONENT_UNKNOWN,
                    SourceEvidenceDiagnosticCode.COMPONENT_NOT_REQUESTED,
                }
            ):
                raise SI02InvariantError("Diagnostic component_ref may be absent only for an absent component.")
            if code in availability_code_set:
                if subject not in availability or code is not availability_codes[availability[subject]]:
                    raise SI02InvariantError("Availability diagnostic does not match its component state.")
            elif code is SourceEvidenceDiagnosticCode.EVIDENCE_PARTIAL:
                if subject is not SourceEvidenceDiagnosticSubject.EVIDENCE or all(
                    value is SourceComponentAvailability.AVAILABLE
                    for value in availability.values()
                ):
                    raise SI02InvariantError("EVIDENCE_PARTIAL requires partial aggregate evidence.")
            elif code is SourceEvidenceDiagnosticCode.METADATA_INCOMPLETE:
                if subject is not SourceEvidenceDiagnosticSubject.METADATA or self.metadata is None:
                    raise SI02InvariantError("METADATA_INCOMPLETE requires metadata.")
            elif code is SourceEvidenceDiagnosticCode.TRANSCRIPT_CANDIDATE_INCOMPLETE:
                if subject is not SourceEvidenceDiagnosticSubject.PROVIDER_TRANSCRIPT_CANDIDATE or reference not in candidate_digests:
                    raise SI02InvariantError("TRANSCRIPT_CANDIDATE_INCOMPLETE requires a candidate.")
            elif code is SourceEvidenceDiagnosticCode.AUDIO_PROPERTIES_INCOMPLETE:
                audio = self.audio_evidence
                if subject is not SourceEvidenceDiagnosticSubject.AUDIO_EVIDENCE or audio is None or all(
                    getattr(audio, field) is not None
                    for field in ("duration_ms", "codec_label", "sample_rate_hz", "channel_count")
                ):
                    raise SI02InvariantError("AUDIO_PROPERTIES_INCOMPLETE requires incomplete audio properties.")
            elif code is SourceEvidenceDiagnosticCode.PROVENANCE_REPLAYED:
                record = provenance_by_id.get(reference or "")
                if (
                    subject is not SourceEvidenceDiagnosticSubject.PROVENANCE
                    or item.severity is not SourceEvidenceDiagnosticSeverity.INFORMATIONAL
                    or record is None
                    or record.acquisition_method is not SourceAcquisitionMethod.REPLAY
                ):
                    raise SI02InvariantError("PROVENANCE_REPLAYED requires informational replay provenance.")

    def semantic_preimage(self) -> dict[str, Any]:
        return _semantic_preimage(
            schema_version=self.schema_version,
            source_identity=self.source_identity,
            observation_identity=self.observation_identity,
            producer_id=self.producer_id,
            producer_version=self.producer_version,
            metadata_availability=self.metadata_availability,
            transcript_availability=self.transcript_availability,
            audio_availability=self.audio_availability,
            metadata=self.metadata,
            candidates=self.provider_transcript_candidates,
            audio=self.audio_evidence,
            provenance=self.provenance,
            aggregate_provenance_ref=self.aggregate_provenance_ref,
        )

    def to_dict(self) -> dict[str, Any]:
        value = self.semantic_preimage()
        value["artifact_identity"] = self.artifact_identity
        value["diagnostics"] = [item.to_dict() for item in self.diagnostics]
        return value

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict(), maximum_bytes=_ARTIFACT_LIMIT)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AcquiredSourceEvidence":
        if type(value) is not dict:
            raise SI02MalformedDataError("AcquiredSourceEvidence must be a JSON object.")
        required = {
            "schema_version", "artifact_identity", "source_identity", "observation_identity",
            "producer_id", "producer_version", "metadata_availability",
            "transcript_availability", "audio_availability",
            "provider_transcript_candidates", "provenance", "aggregate_provenance_ref",
            "diagnostics",
        }
        optional = {"metadata", "audio_evidence"}
        actual = set(value)
        if actual - required - optional:
            raise SI02MalformedDataError("Unknown AcquiredSourceEvidence fields: " + ", ".join(sorted(actual - required - optional)) + ".")
        if required - actual:
            raise SI02MalformedDataError("Missing AcquiredSourceEvidence fields: " + ", ".join(sorted(required - actual)) + ".")
        if any(value[field] is None for field in actual):
            raise SI02MalformedDataError("AcquiredSourceEvidence fields must be absent rather than null.")
        for field in ("provider_transcript_candidates", "provenance", "diagnostics"):
            if type(value[field]) is not list:
                raise SI02MalformedDataError(f"Serialized {field} must be an array.")
        return cls(
            schema_version=schema_version(value["schema_version"]),
            artifact_identity=value["artifact_identity"],
            source_identity=CanonicalSourceIdentity.from_dict(value["source_identity"]),
            observation_identity=SourceObservationIdentity.from_dict(value["observation_identity"]),
            producer_id=value["producer_id"],
            producer_version=value["producer_version"],
            metadata_availability=_enum(value["metadata_availability"], SourceComponentAvailability, "metadata_availability"),
            transcript_availability=_enum(value["transcript_availability"], SourceComponentAvailability, "transcript_availability"),
            audio_availability=_enum(value["audio_availability"], SourceComponentAvailability, "audio_availability"),
            metadata=SourceEvidenceMetadata.from_dict(value["metadata"]) if "metadata" in value else None,
            provider_transcript_candidates=tuple(ProviderTranscriptCandidate.from_dict(item) for item in value["provider_transcript_candidates"]),
            audio_evidence=ValidatedAudioEvidence.from_dict(value["audio_evidence"]) if "audio_evidence" in value else None,
            provenance=tuple(SourceAcquisitionProvenance.from_dict(item) for item in value["provenance"]),
            aggregate_provenance_ref=value["aggregate_provenance_ref"],
            diagnostics=tuple(SourceEvidenceDiagnostic.from_dict(item) for item in value["diagnostics"]),
        )

    @classmethod
    def from_json(cls, serialized: str | bytes) -> "AcquiredSourceEvidence":
        return cls.from_dict(parse_canonical_json(serialized, maximum_bytes=_ARTIFACT_LIMIT))
