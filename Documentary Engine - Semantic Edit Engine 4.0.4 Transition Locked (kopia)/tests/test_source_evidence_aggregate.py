from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError, replace

import pytest

from engine.source_understanding import CanonicalSourceIdentity, SourceKind, SourceObservationIdentity
from engine.source_understanding._evidence_validation import canonical_json_bytes
from engine.source_understanding.source_evidence import (
    AcquiredSourceEvidence,
    AudioContainer,
    EvidenceLanguageTag,
    ProviderTranscriptCandidate,
    ProviderTranscriptEvidenceFormat,
    ProviderTranscriptKind,
    SI02InvalidFieldError,
    SI02InvariantError,
    SI02MalformedDataError,
    SI02SizeLimitError,
    SourceAcquisitionMethod,
    SourceAcquisitionProvenance,
    SourceAcquisitionProvenanceRole,
    SourceComponentAvailability,
    SourceEvidenceDiagnostic,
    SourceEvidenceDiagnosticCode,
    SourceEvidenceDiagnosticSeverity,
    SourceEvidenceDiagnosticSubject,
    SourceEvidenceMetadata,
    ValidatedAudioEvidence,
)
from engine.source_understanding._evidence_aggregate import _component_summary, _semantic_preimage


def qhash(value):
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def identity(value="AbCdEf_12-3"):
    return CanonicalSourceIdentity(1, SourceKind.YOUTUBE_VIDEO, "youtube-video-id", 1, value)


def observation(source=None):
    source = source or identity()
    return SourceObservationIdentity(1, source, "opaque", 1, "observation")


def record(*, source, observed, role, evidence_digest, parents=(), method=SourceAcquisitionMethod.REPLAY):
    values = {
        "schema_version": 1,
        "source_identity": source,
        "observation_identity": observed,
        "role": role,
        "adapter_id": "source.adapter",
        "adapter_version": "1.0.0",
        "acquisition_method": method,
        "evidence_digest": evidence_digest,
        "parent_refs": tuple(sorted(parents, key=str.encode)),
    }
    preimage = {
        "acquisition_method": values["acquisition_method"].value,
        "adapter_id": values["adapter_id"],
        "adapter_version": values["adapter_version"],
        "evidence_digest": values["evidence_digest"],
        "observation_identity": observed.to_dict(),
        "parent_refs": list(values["parent_refs"]),
        "role": role.value,
        "schema_version": 1,
        "source_identity": source.to_dict(),
    }
    return SourceAcquisitionProvenance(provenance_id=qhash(preimage), **values)


def artifact(*, states=None, diagnostics=(), mutate=None):
    source = identity()
    observed = observation(source)
    states = states or (
        SourceComponentAvailability.UNAVAILABLE,
        SourceComponentAvailability.UNKNOWN,
        SourceComponentAvailability.NOT_REQUESTED,
    )
    summary = _component_summary(source, observed, *states, None, (), None)
    root = record(
        source=source,
        observed=observed,
        role=SourceAcquisitionProvenanceRole.AGGREGATE,
        evidence_digest=qhash(summary),
    )
    provenance = (root,)
    values = {
        "schema_version": 1,
        "source_identity": source,
        "observation_identity": observed,
        "producer_id": "source.acquirer",
        "producer_version": "1.0.0",
        "metadata_availability": states[0],
        "transcript_availability": states[1],
        "audio_availability": states[2],
        "metadata": None,
        "provider_transcript_candidates": (),
        "audio_evidence": None,
        "provenance": provenance,
        "aggregate_provenance_ref": root.provenance_id,
        "diagnostics": tuple(diagnostics),
    }
    if mutate:
        mutate(values)
    preimage = _semantic_preimage(
        schema_version=values["schema_version"],
        source_identity=values["source_identity"],
        observation_identity=values["observation_identity"],
        producer_id=values["producer_id"],
        producer_version=values["producer_version"],
        metadata_availability=values["metadata_availability"],
        transcript_availability=values["transcript_availability"],
        audio_availability=values["audio_availability"],
        metadata=values["metadata"],
        candidates=values["provider_transcript_candidates"],
        audio=values["audio_evidence"],
        provenance=values["provenance"],
        aggregate_provenance_ref=values["aggregate_provenance_ref"],
    )
    return AcquiredSourceEvidence(artifact_identity=qhash(preimage), **values)


def metadata_artifact(graph_depth):
    """Build root -> direct metadata -> ancestors at the requested total depth."""
    source = identity()
    observed = observation(source)
    metadata_digest = "sha256:" + "a" * 64
    parent = None
    records = []
    for index in range(graph_depth - 2):
        ancestor = record(
            source=source,
            observed=observed,
            role=SourceAcquisitionProvenanceRole.METADATA,
            evidence_digest="sha256:" + f"{index + 1:064x}",
            parents=() if parent is None else (parent.provenance_id,),
        )
        records.append(ancestor)
        parent = ancestor
    direct = record(
        source=source,
        observed=observed,
        role=SourceAcquisitionProvenanceRole.METADATA,
        evidence_digest=metadata_digest,
        parents=() if parent is None else (parent.provenance_id,),
    )
    records.append(direct)
    metadata = SourceEvidenceMetadata(
        1, metadata_digest, direct.provenance_id, title="Evidence"
    )
    states = (
        SourceComponentAvailability.AVAILABLE,
        SourceComponentAvailability.UNAVAILABLE,
        SourceComponentAvailability.NOT_REQUESTED,
    )
    summary = _component_summary(source, observed, *states, metadata, (), None)
    root = record(
        source=source,
        observed=observed,
        role=SourceAcquisitionProvenanceRole.AGGREGATE,
        evidence_digest=qhash(summary),
        parents=(direct.provenance_id,),
    )
    provenance = tuple(sorted((*records, root), key=lambda item: item.provenance_id.encode("ascii")))
    values = {
        "schema_version": 1,
        "source_identity": source,
        "observation_identity": observed,
        "producer_id": "source.acquirer",
        "producer_version": "1.0.0",
        "metadata_availability": states[0],
        "transcript_availability": states[1],
        "audio_availability": states[2],
        "metadata": metadata,
        "provider_transcript_candidates": (),
        "audio_evidence": None,
        "provenance": provenance,
        "aggregate_provenance_ref": root.provenance_id,
        "diagnostics": (),
    }
    preimage = _semantic_preimage(
        schema_version=1, source_identity=source, observation_identity=observed,
        producer_id=values["producer_id"], producer_version=values["producer_version"],
        metadata_availability=states[0], transcript_availability=states[1],
        audio_availability=states[2], metadata=metadata, candidates=(), audio=None,
        provenance=provenance, aggregate_provenance_ref=root.provenance_id,
    )
    return AcquiredSourceEvidence(artifact_identity=qhash(preimage), **values)


def test_minimal_aggregate_round_trip_hashing_and_immutability():
    value = artifact()
    assert AcquiredSourceEvidence.from_json(value.canonical_bytes()) == value
    assert hash(value) == hash(replace(value))
    assert "diagnostics" not in value.semantic_preimage()
    assert "artifact_identity" not in value.semantic_preimage()
    with pytest.raises(FrozenInstanceError):
        value.producer_id = "changed"


@pytest.mark.parametrize("field", ["metadata", "provider_transcript_candidates", "audio_evidence"])
def test_available_requires_present_component(field):
    index = {"metadata": 0, "provider_transcript_candidates": 1, "audio_evidence": 2}[field]
    states = list((SourceComponentAvailability.UNAVAILABLE,) * 3)
    states[index] = SourceComponentAvailability.AVAILABLE
    with pytest.raises(SI02InvariantError, match="availability"):
        artifact(states=tuple(states))


@pytest.mark.parametrize("state", [
    SourceComponentAvailability.UNAVAILABLE,
    SourceComponentAvailability.UNKNOWN,
    SourceComponentAvailability.NOT_REQUESTED,
])
def test_absent_states_remain_distinct_and_valid(state):
    value = artifact(states=(state, state, state))
    assert value.metadata_availability is state


def test_provenance_must_be_sorted_closed_and_have_exact_aggregate_summary():
    def wrong_summary(values):
        root = record(
            source=values["source_identity"], observed=values["observation_identity"],
            role=SourceAcquisitionProvenanceRole.AGGREGATE,
            evidence_digest="sha256:" + "f" * 64,
        )
        values["provenance"] = (root,)
        values["aggregate_provenance_ref"] = root.provenance_id

    with pytest.raises(SI02InvariantError, match="summary"):
        artifact(mutate=wrong_summary)


def test_artifact_identity_is_exact_and_diagnostics_are_excluded():
    base = artifact()
    diagnostic = SourceEvidenceDiagnostic(
        1, SourceEvidenceDiagnosticSubject.METADATA,
        SourceEvidenceDiagnosticSeverity.NON_FATAL,
        SourceEvidenceDiagnosticCode.COMPONENT_UNAVAILABLE, 0,
    )
    enriched = artifact(diagnostics=(diagnostic,))
    assert enriched.artifact_identity == base.artifact_identity
    assert enriched != base and hash(enriched) != hash(base)
    with pytest.raises(SI02InvariantError, match="artifact_identity"):
        replace(base, artifact_identity="sha256:" + "f" * 64)


def test_diagnostic_availability_matrix_reference_and_uniqueness():
    good = SourceEvidenceDiagnostic(
        1, SourceEvidenceDiagnosticSubject.METADATA,
        SourceEvidenceDiagnosticSeverity.NON_FATAL,
        SourceEvidenceDiagnosticCode.COMPONENT_UNAVAILABLE, 0,
    )
    artifact(diagnostics=(good,))
    wrong = replace(good, code=SourceEvidenceDiagnosticCode.COMPONENT_UNKNOWN)
    with pytest.raises(SI02InvariantError, match="Availability"):
        artifact(diagnostics=(wrong,))
    duplicate_key = replace(good, ordinal=1)
    with pytest.raises(SI02InvariantError, match="uniqueness"):
        artifact(diagnostics=(good, duplicate_key))


def test_evidence_partial_requires_partial_state_and_artifact_reference():
    base = artifact()
    diagnostic = SourceEvidenceDiagnostic(
        1, SourceEvidenceDiagnosticSubject.EVIDENCE,
        SourceEvidenceDiagnosticSeverity.NON_FATAL,
        SourceEvidenceDiagnosticCode.EVIDENCE_PARTIAL, 0, base.artifact_identity,
    )
    artifact(diagnostics=(diagnostic,))
    with pytest.raises(SI02InvariantError, match="component_ref"):
        artifact(diagnostics=(replace(diagnostic, component_ref="sha256:" + "f" * 64),))


def test_replay_diagnostic_requires_informational_replay_record():
    base = artifact()
    diagnostic = SourceEvidenceDiagnostic(
        1, SourceEvidenceDiagnosticSubject.PROVENANCE,
        SourceEvidenceDiagnosticSeverity.INFORMATIONAL,
        SourceEvidenceDiagnosticCode.PROVENANCE_REPLAYED, 0,
        base.aggregate_provenance_ref,
    )
    artifact(diagnostics=(diagnostic,))
    with pytest.raises(SI02InvariantError, match="PROVENANCE_REPLAYED"):
        artifact(diagnostics=(replace(diagnostic, severity=SourceEvidenceDiagnosticSeverity.NON_FATAL),))


def test_serialized_exact_fields_arrays_null_and_preparse_gate():
    value = artifact().to_dict()
    with pytest.raises(SI02MalformedDataError):
        AcquiredSourceEvidence.from_dict({**value, "path": "/tmp/source"})
    with pytest.raises(SI02MalformedDataError):
        AcquiredSourceEvidence.from_dict({**value, "metadata": None})
    with pytest.raises(SI02MalformedDataError):
        AcquiredSourceEvidence.from_dict({**value, "diagnostics": ()})
    with pytest.raises(SI02MalformedDataError):
        AcquiredSourceEvidence.from_json(b"x" * (512 * 1024))
    with pytest.raises(SI02SizeLimitError):
        AcquiredSourceEvidence.from_json(b"x" * (512 * 1024 + 1))


def test_tuple_and_producer_validation():
    with pytest.raises(SI02InvalidFieldError):
        artifact(mutate=lambda values: values.__setitem__("diagnostics", []))
    with pytest.raises(SI02InvalidFieldError):
        artifact(mutate=lambda values: values.__setitem__("producer_id", "Upper"))


def test_provenance_graph_depth_32_is_accepted_and_33_rejected():
    assert len(metadata_artifact(32).provenance) == 32
    with pytest.raises(SI02InvariantError, match="depth"):
        metadata_artifact(33)


def test_unresolved_parent_and_noncanonical_provenance_order_are_rejected():
    def unresolved(values):
        root = values["provenance"][0]
        replacement = record(
            source=values["source_identity"], observed=values["observation_identity"],
            role=SourceAcquisitionProvenanceRole.AGGREGATE,
            evidence_digest=root.evidence_digest,
            parents=("sha256:" + "f" * 64,),
        )
        values["provenance"] = (replacement,)
        values["aggregate_provenance_ref"] = replacement.provenance_id

    with pytest.raises(SI02InvariantError, match="resolve"):
        artifact(mutate=unresolved)
    valid = metadata_artifact(3)
    with pytest.raises(SI02InvariantError, match="sorted"):
        replace(valid, provenance=tuple(reversed(valid.provenance)))


@pytest.mark.parametrize("serialized", [
    b"\xef\xbb\xbf{}",
    b"{}\n",
    b'{"schema_version":1,"schema_version":1}',
    b'{ "schema_version":1}',
])
def test_aggregate_parser_rejects_bom_trailing_duplicate_and_noncanonical_json(serialized):
    with pytest.raises(SI02MalformedDataError):
        AcquiredSourceEvidence.from_json(serialized)


def test_existing_package_level_api_remains_exactly_si01():
    import engine.source_understanding as package

    assert "AcquiredSourceEvidence" not in package.__all__
    assert len(package.__all__) == 11


def test_maximum_34_direct_component_fanout_is_valid_and_within_64_records():
    source = identity()
    observed = observation(source)
    metadata_digest = "sha256:" + f"{1000:064x}"
    audio_digest = "sha256:" + f"{1001:064x}"
    metadata_record = record(
        source=source, observed=observed,
        role=SourceAcquisitionProvenanceRole.METADATA,
        evidence_digest=metadata_digest,
    )
    audio_record = record(
        source=source, observed=observed,
        role=SourceAcquisitionProvenanceRole.AUDIO,
        evidence_digest=audio_digest,
    )
    candidate_records = []
    candidates = []
    for index in range(32):
        evidence_digest = "sha256:" + f"{index + 1:064x}"
        provenance_record = record(
            source=source, observed=observed,
            role=SourceAcquisitionProvenanceRole.PROVIDER_TRANSCRIPT_CANDIDATE,
            evidence_digest=evidence_digest,
        )
        candidate_records.append(provenance_record)
        candidates.append(ProviderTranscriptCandidate(
            1, source, observed, f"candidate-{index:02d}", ProviderTranscriptKind.MANUAL,
            EvidenceLanguageTag("en"), False, index + 1,
            ProviderTranscriptEvidenceFormat.PLAIN_TEXT, evidence_digest,
            provenance_record.provenance_id,
        ))
    candidates = tuple(sorted(candidates, key=ProviderTranscriptCandidate.canonical_order_key))
    metadata = SourceEvidenceMetadata(
        1, metadata_digest, metadata_record.provenance_id, title="Maximum fanout"
    )
    audio = ValidatedAudioEvidence(
        1, source, observed, audio_digest, 1, AudioContainer.WAV,
        "audio/wav", audio_record.provenance_id,
    )
    states = (SourceComponentAvailability.AVAILABLE,) * 3
    summary = _component_summary(source, observed, *states, metadata, candidates, audio)
    component_records = (metadata_record, *candidate_records, audio_record)
    root = record(
        source=source, observed=observed,
        role=SourceAcquisitionProvenanceRole.AGGREGATE,
        evidence_digest=qhash(summary),
        parents=tuple(item.provenance_id for item in component_records),
    )
    provenance = tuple(sorted((*component_records, root), key=lambda item: item.provenance_id.encode("ascii")))
    preimage = _semantic_preimage(
        schema_version=1, source_identity=source, observation_identity=observed,
        producer_id="source.acquirer", producer_version="1.0.0",
        metadata_availability=states[0], transcript_availability=states[1],
        audio_availability=states[2], metadata=metadata, candidates=candidates,
        audio=audio, provenance=provenance, aggregate_provenance_ref=root.provenance_id,
    )
    value = AcquiredSourceEvidence(
        1, qhash(preimage), source, observed, "source.acquirer", "1.0.0",
        *states, metadata, candidates, audio, provenance, root.provenance_id, (),
    )
    assert len(root.parent_refs) == 34
    assert len(value.provenance) == 35
    assert len(value.provider_transcript_candidates) == 32
